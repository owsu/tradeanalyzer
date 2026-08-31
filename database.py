from __future__ import annotations

import json
import hashlib
import math
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator


class RevisionConflictError(RuntimeError):
    """Raised when a caller attempts to overwrite newer automation state."""


class Database:
    """Small SQLite repository with explicit transactions and migrations."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS automation_state (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    all_paused INTEGER NOT NULL DEFAULT 1 CHECK (all_paused IN (0, 1)),
                    trade_ads_paused INTEGER NOT NULL DEFAULT 1 CHECK (trade_ads_paused IN (0, 1)),
                    roblox_trades_paused INTEGER NOT NULL DEFAULT 1 CHECK (roblox_trades_paused IN (0, 1)),
                    revision INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS automation_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    previous_state TEXT NOT NULL,
                    new_state TEXT NOT NULL,
                    reason TEXT
                );
                CREATE TABLE IF NOT EXISTS trade_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    evaluated_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    evaluation TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trade_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_trade_id INTEGER,
                    success INTEGER NOT NULL CHECK (success IN (0, 1)),
                    detail TEXT
                );
                CREATE TABLE IF NOT EXISTS proof_messages (
                    source TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('processing', 'succeeded', 'failed')
                    ),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    raw_text TEXT,
                    image_urls TEXT NOT NULL DEFAULT '[]',
                    parsed_proof TEXT,
                    error TEXT,
                    author TEXT,
                    message_timestamp TEXT,
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source, channel_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS proof_messages_content_hash
                    ON proof_messages(content_hash, status);
                CREATE TABLE IF NOT EXISTS watched_users (
                    user_id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    added_at TEXT NOT NULL,
                    last_polled_at TEXT,
                    inventory_public INTEGER,
                    next_poll_at TEXT,
                    poll_priority INTEGER NOT NULL DEFAULT 0,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    username TEXT,
                    last_trade_ad_at TEXT,
                    trade_ad_admitted INTEGER NOT NULL DEFAULT 0,
                    trade_ad_score REAL NOT NULL DEFAULT 0,
                    trade_ad_archived_at TEXT
                );
                CREATE TABLE IF NOT EXISTS rolimons_trade_ads (
                    ad_id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    offer_robux INTEGER NOT NULL DEFAULT 0,
                    request_robux INTEGER NOT NULL DEFAULT 0,
                    request_tags TEXT NOT NULL DEFAULT '[]',
                    first_seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS rolimons_trade_ads_user_time
                    ON rolimons_trade_ads(user_id, created_at);
                CREATE INDEX IF NOT EXISTS rolimons_trade_ads_created
                    ON rolimons_trade_ads(created_at);
                CREATE TABLE IF NOT EXISTS rolimons_trade_ad_items (
                    ad_id INTEGER NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('offer', 'request')),
                    position INTEGER NOT NULL,
                    asset_id INTEGER NOT NULL,
                    PRIMARY KEY(ad_id, side, position),
                    FOREIGN KEY(ad_id) REFERENCES rolimons_trade_ads(ad_id)
                );
                CREATE INDEX IF NOT EXISTS rolimons_trade_ad_items_asset
                    ON rolimons_trade_ad_items(asset_id, side);
                CREATE TABLE IF NOT EXISTS tracked_assets (
                    asset_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    market_value INTEGER NOT NULL DEFAULT 0,
                    demand_score INTEGER NOT NULL DEFAULT -1,
                    trend_score INTEGER NOT NULL DEFAULT -1,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    interval_seconds INTEGER NOT NULL,
                    cursor TEXT,
                    sweep_started_at TEXT,
                    last_completed_at TEXT,
                    next_sweep_at TEXT NOT NULL,
                    last_error TEXT,
                    priority_score INTEGER NOT NULL DEFAULT 0,
                    priority_reason TEXT,
                    priority_until TEXT,
                    consecutive_hidden_pages INTEGER NOT NULL DEFAULT 0,
                    last_visible_owner_at TEXT
                );
                CREATE TABLE IF NOT EXISTS market_scheduler_state (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    cooldown_until TEXT,
                    consecutive_rate_limits INTEGER NOT NULL DEFAULT 0,
                    last_rate_limit_at TEXT
                );
                CREATE TABLE IF NOT EXISTS inventory_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    successful INTEGER NOT NULL CHECK (successful IN (0, 1)),
                    item_count INTEGER,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS uaid_ownership (
                    uaid INTEGER PRIMARY KEY,
                    asset_id INTEGER NOT NULL,
                    current_owner_id INTEGER NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ownership_transfers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uaid INTEGER NOT NULL,
                    asset_id INTEGER NOT NULL,
                    previous_owner_id INTEGER NOT NULL,
                    new_owner_id INTEGER NOT NULL,
                    detected_at TEXT NOT NULL,
                    observed_rap INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'unmatched' CHECK (
                        status IN ('unmatched', 'matched')
                    ),
                    inferred_trade_id INTEGER
                );
                CREATE INDEX IF NOT EXISTS ownership_transfers_pair
                    ON ownership_transfers(
                        previous_owner_id, new_owner_id, status, detected_at
                    );
                CREATE TABLE IF NOT EXISTS inferred_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    correlation_key TEXT NOT NULL UNIQUE,
                    user_a_id INTEGER NOT NULL,
                    user_b_id INTEGER NOT NULL,
                    window_started_at TEXT NOT NULL,
                    window_ended_at TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    possible_robux INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inferred_trade_items (
                    inferred_trade_id INTEGER NOT NULL,
                    transfer_id INTEGER NOT NULL UNIQUE,
                    from_user_id INTEGER NOT NULL,
                    to_user_id INTEGER NOT NULL,
                    uaid INTEGER NOT NULL,
                    asset_id INTEGER NOT NULL,
                    observed_rap INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (inferred_trade_id) REFERENCES inferred_trades(id)
                );
                CREATE TABLE IF NOT EXISTS proof_trade_links (
                    source TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    inferred_trade_id INTEGER NOT NULL UNIQUE,
                    confidence REAL NOT NULL,
                    linked_at TEXT NOT NULL,
                    PRIMARY KEY (source, channel_id, message_id),
                    FOREIGN KEY (inferred_trade_id) REFERENCES inferred_trades(id)
                );
                CREATE TABLE IF NOT EXISTS item_value_observations (
                    observation_key TEXT PRIMARY KEY,
                    asset_id INTEGER NOT NULL,
                    observed_value INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    observation_kind TEXT NOT NULL DEFAULT 'displayed',
                    baseline_value INTEGER,
                    raw_adjustment INTEGER,
                    structural_compensation INTEGER,
                    largest_payment_ratio REAL,
                    confidence REAL NOT NULL,
                    proof_source TEXT,
                    proof_channel_id TEXT,
                    proof_message_id TEXT,
                    inferred_trade_id INTEGER,
                    FOREIGN KEY (inferred_trade_id) REFERENCES inferred_trades(id)
                );
                CREATE INDEX IF NOT EXISTS item_value_observations_asset_time
                    ON item_value_observations(asset_id, observed_at);
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column(connection, "watched_users", "premium_status", "INTEGER")
            self._ensure_column(
                connection, "watched_users", "last_premium_checked_at", "TEXT"
            )
            self._ensure_column(connection, "watched_users", "next_poll_at", "TEXT")
            self._ensure_column(connection, "watched_users", "poll_priority", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "watched_users", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "watched_users", "username", "TEXT")
            self._ensure_column(connection, "watched_users", "last_trade_ad_at", "TEXT")
            self._ensure_column(connection, "watched_users", "trade_ad_admitted", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "watched_users", "trade_ad_score", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(connection, "watched_users", "trade_ad_archived_at", "TEXT")
            self._ensure_column(
                connection,
                "item_value_observations",
                "observation_kind",
                "TEXT NOT NULL DEFAULT 'displayed'",
            )
            self._ensure_column(
                connection, "item_value_observations", "baseline_value", "INTEGER"
            )
            self._ensure_column(
                connection, "item_value_observations", "raw_adjustment", "INTEGER"
            )
            self._ensure_column(
                connection,
                "item_value_observations",
                "structural_compensation",
                "INTEGER",
            )
            self._ensure_column(
                connection,
                "item_value_observations",
                "largest_payment_ratio",
                "REAL",
            )
            self._ensure_column(
                connection,
                "tracked_assets",
                "demand_score",
                "INTEGER NOT NULL DEFAULT -1",
            )
            self._ensure_column(
                connection,
                "tracked_assets",
                "trend_score",
                "INTEGER NOT NULL DEFAULT -1",
            )
            self._ensure_column(connection, "tracked_assets", "priority_score", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "tracked_assets", "priority_reason", "TEXT")
            self._ensure_column(connection, "tracked_assets", "priority_until", "TEXT")
            self._ensure_column(connection, "tracked_assets", "consecutive_hidden_pages", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "tracked_assets", "last_visible_owner_at", "TEXT")
            now = datetime.now(UTC).isoformat()
            migration = connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(name, applied_at)
                VALUES ('purge_ambiguous_proof_values_v4', ?)
                """,
                (now,),
            )
            if migration.rowcount:
                connection.execute(
                    "DELETE FROM item_value_observations WHERE source='proof'"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO automation_state
                    (singleton_id, updated_at, updated_by)
                VALUES (1, ?, 'system')
                """,
                (now,),
            )
            connection.execute(
                "INSERT OR IGNORE INTO market_scheduler_state(singleton_id) VALUES (1)"
            )
            connection.execute(
                "UPDATE watched_users SET next_poll_at=COALESCE(next_poll_at, added_at)"
            )
            connection.commit()

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _proof_key(raw) -> tuple[str, str, str]:
        if not raw.message_id or not raw.channel_id:
            raise ValueError(
                "Persistent proof ingestion requires message_id and channel_id"
            )
        return raw.source, raw.channel_id, raw.message_id

    def proof_message_succeeded(
        self, source: str, channel_id: str, message_id: str
    ) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT status FROM proof_messages
                WHERE source = ? AND channel_id = ? AND message_id = ?
                """,
                (source, channel_id, message_id),
            ).fetchone()
        return row is not None and row["status"] == "succeeded"

    def claim_proof_message(
        self,
        raw,
        content_hash: str,
        *,
        max_attempts: int,
        processing_timeout_seconds: int,
    ) -> tuple[str, dict | None]:
        """Atomically return process, skip, or reused for a proof message."""
        source, channel_id, message_id = self._proof_key(raw)
        now = datetime.now(UTC)
        now_text = now.isoformat()
        stale_before = now - timedelta(seconds=processing_timeout_seconds)

        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM proof_messages
                WHERE source = ? AND channel_id = ? AND message_id = ?
                """,
                (source, channel_id, message_id),
            ).fetchone()

            if row is not None and row["content_hash"] == content_hash:
                if row["status"] == "succeeded":
                    connection.commit()
                    return "skip", json.loads(row["parsed_proof"])
                if row["status"] == "processing":
                    updated_at = datetime.fromisoformat(row["updated_at"])
                    if updated_at >= stale_before:
                        connection.commit()
                        return "skip", None
                if row["attempts"] >= max_attempts:
                    connection.commit()
                    return "skip", None

            reusable = connection.execute(
                """
                SELECT parsed_proof FROM proof_messages
                WHERE content_hash = ? AND status = 'succeeded'
                  AND parsed_proof IS NOT NULL
                ORDER BY updated_at DESC LIMIT 1
                """,
                (content_hash,),
            ).fetchone()
            if reusable is not None:
                parsed_json = reusable["parsed_proof"]
                connection.execute(
                    """
                    INSERT INTO proof_messages (
                        source, channel_id, message_id, content_hash, status,
                        attempts, raw_text, image_urls, parsed_proof, error,
                        author, message_timestamp, first_seen_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'succeeded', 0, ?, ?, ?, NULL, ?, ?, ?, ?)
                    ON CONFLICT(source, channel_id, message_id) DO UPDATE SET
                        content_hash = excluded.content_hash,
                        status = 'succeeded', parsed_proof = excluded.parsed_proof,
                        error = NULL, raw_text = excluded.raw_text,
                        image_urls = excluded.image_urls, author = excluded.author,
                        message_timestamp = excluded.message_timestamp,
                        updated_at = excluded.updated_at
                    """,
                    (
                        source,
                        channel_id,
                        message_id,
                        content_hash,
                        raw.text,
                        json.dumps(list(raw.image_urls)),
                        parsed_json,
                        raw.author,
                        raw.timestamp.isoformat() if raw.timestamp else None,
                        now_text,
                        now_text,
                    ),
                )
                connection.commit()
                return "reused", json.loads(parsed_json)

            attempts = 1 if row is None or row["content_hash"] != content_hash else row["attempts"] + 1
            connection.execute(
                """
                INSERT INTO proof_messages (
                    source, channel_id, message_id, content_hash, status,
                    attempts, raw_text, image_urls, parsed_proof, error,
                    author, message_timestamp, first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, 'processing', ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
                ON CONFLICT(source, channel_id, message_id) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    status = 'processing', attempts = excluded.attempts,
                    raw_text = excluded.raw_text, image_urls = excluded.image_urls,
                    parsed_proof = NULL, error = NULL, author = excluded.author,
                    message_timestamp = excluded.message_timestamp,
                    updated_at = excluded.updated_at
                """,
                (
                    source,
                    channel_id,
                    message_id,
                    content_hash,
                    attempts,
                    raw.text,
                    json.dumps(list(raw.image_urls)),
                    raw.author,
                    raw.timestamp.isoformat() if raw.timestamp else None,
                    now_text,
                    now_text,
                ),
            )
            connection.commit()
            return "process", None

    def complete_proof_message(self, raw, proof: dict) -> dict:
        source, channel_id, message_id = self._proof_key(raw)
        proof = json.loads(json.dumps(proof))
        with self.connect() as connection:
            # Resolve missing IDs only on an exact, unique catalog-name match.
            for side in ("giving", "receiving"):
                for item in proof.get(side, []):
                    if item.get("asset_id") or not item.get("name"):
                        continue
                    rows = connection.execute(
                        """
                        SELECT asset_id FROM tracked_assets
                        WHERE lower(trim(name)) = lower(trim(?))
                        LIMIT 2
                        """,
                        (str(item["name"]),),
                    ).fetchall()
                    if len(rows) == 1:
                        item["asset_id"] = int(rows[0]["asset_id"])

            # Proof activity makes these assets immediately relevant to the
            # market collector, independent of whether this proof contributes
            # an implied-value observation.
            priority_until = (datetime.now(UTC) + timedelta(days=7)).isoformat()
            for side in ("giving", "receiving"):
                for item in proof.get(side, []):
                    if item.get("asset_id"):
                        connection.execute(
                            """
                            UPDATE tracked_assets SET priority_score=MAX(priority_score, 100),
                                priority_reason='proof', priority_until=?,
                                next_sweep_at=MIN(next_sweep_at, ?)
                            WHERE asset_id=?
                            """,
                            (priority_until, datetime.now(UTC).isoformat(), int(item["asset_id"])),
                        )

            now_text = datetime.now(UTC).isoformat()
            connection.execute(
                """
                UPDATE proof_messages
                SET status = 'succeeded', parsed_proof = ?, error = NULL,
                    updated_at = ?
                WHERE source = ? AND channel_id = ? AND message_id = ?
                """,
                (
                    json.dumps(proof, sort_keys=True),
                    now_text,
                    source,
                    channel_id,
                    message_id,
                ),
            )
            # Reprocessing must remove legacy observations where RAP was
            # previously mistaken for assigned/trading value, even if the new
            # parse is invalid or contains no visible market values.
            connection.execute(
                """
                DELETE FROM item_value_observations
                WHERE proof_source=? AND proof_channel_id=?
                  AND proof_message_id=?
                """,
                (source, channel_id, message_id),
            )
            if proof.get("valid"):
                observed_at = (
                    raw.timestamp.isoformat() if raw.timestamp else now_text
                )
                for side in ("giving", "receiving"):
                    for index, item in enumerate(proof.get(side, [])):
                        asset_id = item.get("asset_id")
                        value = item.get("market_value")
                        if not asset_id or value is None or int(value) <= 0:
                            continue
                        observation_key = ":".join(
                            (source, channel_id, message_id, side, str(index))
                        )
                        connection.execute(
                            """
                            INSERT INTO item_value_observations (
                                observation_key, asset_id, observed_value,
                                observed_at, source, observation_kind,
                                baseline_value, confidence, proof_source,
                                proof_channel_id, proof_message_id
                            ) VALUES (?, ?, ?, ?, 'proof_displayed', 'displayed',
                                      ?, 0.35, ?, ?, ?)
                            ON CONFLICT(observation_key) DO UPDATE SET
                                asset_id = excluded.asset_id,
                                observed_value = excluded.observed_value,
                                observed_at = excluded.observed_at,
                                source = excluded.source,
                                observation_kind = excluded.observation_kind,
                                baseline_value = excluded.baseline_value,
                                confidence = excluded.confidence,
                                inferred_trade_id = NULL
                            """,
                            (
                                observation_key,
                                int(asset_id),
                                int(value),
                                observed_at,
                                int(value),
                                source,
                                channel_id,
                                message_id,
                            ),
                        )
                deal_type = proof.get("deal_type", "unknown")
                deal_amount = proof.get("deal_amount")
                deal_item = str(proof.get("deal_item") or "").strip().casefold()
                all_items = [
                    (side, item)
                    for side in ("giving", "receiving")
                    for item in proof.get(side, [])
                ]
                targets = [
                    (side, item)
                    for side, item in all_items
                    if str(item.get("name") or "").strip().casefold() == deal_item
                ]
                if (
                    len(targets) == 1
                    and deal_type in {"overpay", "underpay", "equal"}
                    and (deal_amount is not None or deal_type == "equal")
                ):
                    _target_side, target = targets[0]
                    target_side = targets[0][0]
                    target_asset_id = target.get("asset_id")
                    baseline = int(target.get("market_value") or 0)
                    payment_side = "receiving" if target_side == "giving" else "giving"
                    payment_items = proof.get(payment_side, [])
                    payment_values_complete = bool(payment_items) and all(
                        item.get("market_value") is not None for item in payment_items
                    )
                    adjustment = int(deal_amount or 0)
                    signed_adjustment = (
                        adjustment if deal_type == "overpay"
                        else -adjustment if deal_type == "underpay"
                        else 0
                    )
                    structural_compensation = 0
                    largest_payment_ratio = 0.0
                    if baseline > 0 and payment_values_complete:
                        payment_values = [
                            int(item["market_value"]) for item in payment_items
                        ]
                        largest_payment_ratio = max(payment_values) / baseline
                        if largest_payment_ratio >= 0.85:
                            fragmentation_pct = 0.0
                        elif largest_payment_ratio >= 0.65:
                            fragmentation_pct = 0.02
                        elif largest_payment_ratio >= 0.40:
                            fragmentation_pct = 0.05
                        else:
                            fragmentation_pct = 0.08
                        fragmentation_pct += max(len(payment_items) - 2, 0) * 0.005

                        target_demand_row = connection.execute(
                            "SELECT demand_score FROM tracked_assets WHERE asset_id=?",
                            (int(target_asset_id),),
                        ).fetchone() if target_asset_id else None
                        target_demand = (
                            int(target_demand_row["demand_score"])
                            if target_demand_row is not None else -1
                        )
                        known_payment_demand: list[tuple[int, int]] = []
                        for item, value in zip(payment_items, payment_values):
                            if not item.get("asset_id"):
                                continue
                            demand_row = connection.execute(
                                "SELECT demand_score FROM tracked_assets WHERE asset_id=?",
                                (int(item["asset_id"]),),
                            ).fetchone()
                            if demand_row is not None and int(demand_row["demand_score"]) >= 0:
                                known_payment_demand.append(
                                    (int(demand_row["demand_score"]), value)
                                )
                        demand_pct = 0.0
                        if target_demand >= 0 and known_payment_demand:
                            demand_weight = sum(value for _, value in known_payment_demand)
                            payment_demand = sum(
                                score * value for score, value in known_payment_demand
                            ) / demand_weight
                            demand_pct = max(target_demand - payment_demand, 0) * 0.02
                        compensation_pct = min(fragmentation_pct + demand_pct, 0.15)
                        structural_compensation = round(baseline * compensation_pct)

                    implied = baseline + signed_adjustment - structural_compensation
                    # Reject malformed direction/amount extraction rather than
                    # letting one proof create an extreme market multiplier.
                    if (
                        target_asset_id
                        and baseline > 0
                        and payment_values_complete
                        and 0.5 * baseline <= implied <= 1.5 * baseline
                    ):
                        observation_key = ":".join(
                            (source, channel_id, message_id, "implied")
                        )
                        connection.execute(
                            """
                            INSERT INTO item_value_observations (
                                observation_key, asset_id, observed_value,
                                observed_at, source, observation_kind,
                                baseline_value, raw_adjustment,
                                structural_compensation, largest_payment_ratio,
                                confidence, proof_source,
                                proof_channel_id, proof_message_id
                            ) VALUES (?, ?, ?, ?, 'proof_implied', 'implied',
                                      ?, ?, ?, ?, 0.75, ?, ?, ?)
                            ON CONFLICT(observation_key) DO UPDATE SET
                                asset_id=excluded.asset_id,
                                observed_value=excluded.observed_value,
                                observed_at=excluded.observed_at,
                                baseline_value=excluded.baseline_value,
                                raw_adjustment=excluded.raw_adjustment,
                                structural_compensation=excluded.structural_compensation,
                                largest_payment_ratio=excluded.largest_payment_ratio,
                                confidence=excluded.confidence,
                                inferred_trade_id=NULL
                            """,
                            (
                                observation_key, int(target_asset_id), implied,
                                observed_at, baseline, signed_adjustment,
                                structural_compensation, largest_payment_ratio,
                                source, channel_id, message_id,
                            ),
                        )
            connection.commit()
        return proof

    def reconcile_proofs_with_inferred_trades(self, window_days: int = 7) -> list[dict]:
        """Link only unique, exact reciprocal item-set matches near proof time."""
        from collections import Counter

        linked: list[dict] = []
        with self.connect() as connection:
            proofs = connection.execute(
                """
                SELECT p.* FROM proof_messages p
                LEFT JOIN proof_trade_links l
                  ON l.source=p.source AND l.channel_id=p.channel_id
                 AND l.message_id=p.message_id
                WHERE p.status='succeeded' AND p.parsed_proof IS NOT NULL
                  AND l.message_id IS NULL
                """
            ).fetchall()
            for row in proofs:
                proof = json.loads(row["parsed_proof"])
                if not proof.get("valid"):
                    continue
                try:
                    giving = Counter(int(i["asset_id"]) for i in proof["giving"])
                    receiving = Counter(
                        int(i["asset_id"]) for i in proof["receiving"]
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                if not giving or not receiving:
                    continue
                proof_time = datetime.fromisoformat(
                    row["message_timestamp"] or row["first_seen_at"]
                )
                earliest = (proof_time - timedelta(days=window_days)).isoformat()
                latest = (proof_time + timedelta(days=window_days)).isoformat()
                candidates = connection.execute(
                    """
                    SELECT t.id, t.user_a_id, t.user_b_id
                    FROM inferred_trades t
                    LEFT JOIN proof_trade_links l ON l.inferred_trade_id=t.id
                    WHERE l.inferred_trade_id IS NULL
                      AND t.window_ended_at BETWEEN ? AND ?
                    """,
                    (earliest, latest),
                ).fetchall()
                matches: list[int] = []
                for trade in candidates:
                    items = connection.execute(
                        """
                        SELECT from_user_id, to_user_id, asset_id
                        FROM inferred_trade_items WHERE inferred_trade_id=?
                        """,
                        (trade["id"],),
                    ).fetchall()
                    a_to_b = Counter(
                        int(i["asset_id"])
                        for i in items
                        if i["from_user_id"] == trade["user_a_id"]
                    )
                    b_to_a = Counter(
                        int(i["asset_id"])
                        for i in items
                        if i["from_user_id"] == trade["user_b_id"]
                    )
                    if (giving == a_to_b and receiving == b_to_a) or (
                        giving == b_to_a and receiving == a_to_b
                    ):
                        matches.append(int(trade["id"]))
                if len(matches) != 1:
                    continue
                trade_id = matches[0]
                connection.execute(
                    """
                    INSERT INTO proof_trade_links (
                        source, channel_id, message_id, inferred_trade_id,
                        confidence, linked_at
                    ) VALUES (?, ?, ?, ?, 0.95, ?)
                    """,
                    (
                        row["source"], row["channel_id"], row["message_id"],
                        trade_id, datetime.now(UTC).isoformat(),
                    ),
                )
                connection.execute(
                    """
                    UPDATE item_value_observations
                    SET confidence=0.95, inferred_trade_id=?
                    WHERE proof_source=? AND proof_channel_id=?
                      AND proof_message_id=?
                    """,
                    (trade_id, row["source"], row["channel_id"], row["message_id"]),
                )
                linked.append(
                    {"inferred_trade_id": trade_id, "message_id": row["message_id"]}
                )
            connection.commit()
        return linked

    def learned_item_value(
        self, asset_id: int, *, min_proofs: int = 3, max_age_days: int = 90,
        recency_half_life_days: float = 14,
        proof_executability_weight: float = 0.50,
        verified_executability_weight: float = 0.85,
    ) -> dict | None:
        """Estimate latent value from direct concessions and similar-value peers."""
        cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
        with self.connect() as connection:
            baseline_row = connection.execute(
                """
                SELECT market_value, demand_score, trend_score
                FROM tracked_assets WHERE asset_id=?
                """,
                (int(asset_id),),
            ).fetchone()
            if baseline_row is None or int(baseline_row["market_value"]) <= 0:
                return None
            baseline = int(baseline_row["market_value"])
            target_demand = int(baseline_row["demand_score"])
            target_trend = int(baseline_row["trend_score"])
            direct_rows = connection.execute(
                """
                SELECT o.observed_value, o.baseline_value, o.raw_adjustment,
                       o.structural_compensation, o.largest_payment_ratio,
                       o.confidence, o.observed_at, o.inferred_trade_id,
                       p.content_hash
                FROM item_value_observations o
                JOIN proof_messages p
                  ON p.source=o.proof_source
                 AND p.channel_id=o.proof_channel_id
                 AND p.message_id=o.proof_message_id
                WHERE o.asset_id=? AND o.observation_kind='implied'
                  AND o.observed_at>=?
                ORDER BY o.confidence DESC, o.observed_at DESC
                """,
                (int(asset_id), cutoff.isoformat()),
            ).fetchall()
            peer_rows = connection.execute(
                """
                SELECT o.asset_id, o.observed_value, o.baseline_value,
                       o.confidence, o.observed_at, p.content_hash,
                       o.inferred_trade_id, a.demand_score, a.trend_score
                FROM item_value_observations o
                JOIN tracked_assets a ON a.asset_id=o.asset_id
                JOIN proof_messages p
                  ON p.source=o.proof_source
                 AND p.channel_id=o.proof_channel_id
                 AND p.message_id=o.proof_message_id
                WHERE o.asset_id<>? AND o.observation_kind='implied'
                  AND o.baseline_value BETWEEN ? AND ?
                  AND o.observed_at>=?
                ORDER BY o.confidence DESC, o.observed_at DESC
                """,
                (
                    int(asset_id), max(int(baseline * 0.5), 1),
                    int(baseline * 2.0), cutoff.isoformat(),
                ),
            ).fetchall()

        direct: dict[str, sqlite3.Row] = {}
        for row in direct_rows:
            direct.setdefault(row["content_hash"], row)
        peers: dict[str, sqlite3.Row] = {}
        for row in peer_rows:
            peer_demand = int(row["demand_score"])
            peer_trend = int(row["trend_score"])
            if target_demand >= 0 and peer_demand >= 0:
                if abs(target_demand - peer_demand) > 1:
                    continue
            if target_trend >= 0 and peer_trend >= 0:
                if abs(target_trend - peer_trend) > 1:
                    continue
            peers.setdefault(row["content_hash"], row)

        required = max(int(min_proofs), 1)
        use_direct_only = len(direct) >= required
        if not use_direct_only and len(direct) + len(peers) < required:
            return None

        weighted: list[tuple[int, float]] = []
        showcase_weighted: list[tuple[int, float]] = []
        now = datetime.now(UTC)
        half_life = max(float(recency_half_life_days), 0.1)
        proof_factor = max(0.0, min(float(proof_executability_weight), 1.0))
        verified_factor = max(
            proof_factor, min(float(verified_executability_weight), 1.0)
        )
        for row in direct.values():
            age_days = max(
                (now - datetime.fromisoformat(row["observed_at"])).total_seconds()
                / 86400,
                0,
            )
            weight = float(row["confidence"]) * (0.5 ** (age_days / half_life)) * 2.0
            observed_value = int(row["observed_value"])
            factor = verified_factor if row["inferred_trade_id"] else proof_factor
            executable_value = round(baseline + (observed_value - baseline) * factor)
            weighted.append((executable_value, weight))
            showcase_weighted.append((observed_value, weight))
        if not use_direct_only:
            for row in peers.values():
                peer_baseline = int(row["baseline_value"] or 0)
                if peer_baseline <= 0:
                    continue
                ratio = int(row["observed_value"]) / peer_baseline
                ratio = max(0.5, min(1.5, ratio))
                peer_value = round(baseline * ratio)
                age_days = max(
                    (now - datetime.fromisoformat(row["observed_at"])).total_seconds()
                    / 86400,
                    0,
                )
                distance = abs(math.log(peer_baseline / baseline))
                similarity = 1 / (1 + 2 * distance)
                if target_demand >= 0 and int(row["demand_score"]) >= 0:
                    similarity *= 1 / (
                        1 + abs(target_demand - int(row["demand_score"]))
                    )
                if target_trend >= 0 and int(row["trend_score"]) >= 0:
                    similarity *= 1 / (
                        1 + abs(target_trend - int(row["trend_score"]))
                    )
                weight = (
                    float(row["confidence"])
                    * (0.5 ** (age_days / half_life))
                    * similarity
                    * 0.5
                )
                factor = verified_factor if row["inferred_trade_id"] else proof_factor
                executable_peer_value = round(
                    baseline + (peer_value - baseline) * factor
                )
                weighted.append((executable_peer_value, weight))
                showcase_weighted.append((peer_value, weight))
        if not weighted:
            return None
        weighted.sort()
        showcase_weighted.sort()

        def weighted_quantile(values: list[tuple[int, float]], fraction: float) -> int:
            threshold = sum(weight for _, weight in values) * fraction
            running = 0.0
            for value, weight in values:
                running += weight
                if running >= threshold:
                    return value
            return values[-1][0]

        estimate = weighted_quantile(weighted, 0.5)
        lower_value = weighted_quantile(weighted, 0.2)
        upper_value = weighted_quantile(weighted, 0.8)
        showcase_value = weighted_quantile(showcase_weighted, 0.5)
        showcase_lower_value = weighted_quantile(showcase_weighted, 0.2)
        showcase_upper_value = weighted_quantile(showcase_weighted, 0.8)
        direct_count = len(direct)
        peer_count = 0 if use_direct_only else len(peers)
        uncertainty_pct = round((upper_value - lower_value) / baseline * 100, 2)
        showcase_uncertainty_pct = round(
            (showcase_upper_value - showcase_lower_value) / baseline * 100, 2
        )
        # Sparse peer estimates with a wide range are useful diagnostics, but
        # are not safe effective-value overrides yet. Test the uncompressed
        # proof distribution so selection-bias shrinkage cannot manufacture
        # eligibility by making a genuinely wide range appear certain.
        if not use_direct_only and showcase_uncertainty_pct > 25:
            return None
        source = "direct implied trades" if use_direct_only else (
            "direct + similar-value peers" if direct_count else "similar-value peers"
        )
        confidence = (
            "high" if use_direct_only and showcase_uncertainty_pct <= 15
            else "medium" if direct_count or showcase_uncertainty_pct <= 10
            else "low"
        )
        total_weight = sum(weight for _, weight in weighted)
        effective_proof_count = round(
            (total_weight * total_weight)
            / sum(weight * weight for _, weight in weighted),
            2,
        )
        average_raw_adjustment = (
            round(
                sum(int(row["raw_adjustment"] or 0) for row in direct.values())
                / direct_count
            )
            if direct_count else None
        )
        average_structural_compensation = (
            round(
                sum(
                    int(row["structural_compensation"] or 0)
                    for row in direct.values()
                ) / direct_count
            )
            if direct_count else None
        )
        average_largest_payment_ratio = (
            round(
                sum(float(row["largest_payment_ratio"] or 0) for row in direct.values())
                / direct_count,
                3,
            )
            if direct_count else None
        )
        return {
            "value": estimate,
            "showcase_value": showcase_value,
            "selection_bias_adjustment": estimate - showcase_value,
            "baseline_value": baseline,
            "adjustment": estimate - baseline,
            "adjustment_pct": round((estimate / baseline - 1) * 100, 2),
            "lower_value": lower_value,
            "upper_value": upper_value,
            "uncertainty_pct": uncertainty_pct,
            "showcase_lower_value": showcase_lower_value,
            "showcase_upper_value": showcase_upper_value,
            "showcase_uncertainty_pct": showcase_uncertainty_pct,
            "confidence": confidence,
            "average_raw_adjustment": average_raw_adjustment,
            "average_structural_compensation": average_structural_compensation,
            "average_largest_payment_ratio": average_largest_payment_ratio,
            "proof_count": direct_count + peer_count,
            "direct_proof_count": direct_count,
            "peer_proof_count": peer_count,
            "source": source,
            "recency_half_life_days": half_life,
            "proof_executability_weight": proof_factor,
            "verified_executability_weight": verified_factor,
            "effective_proof_count": effective_proof_count,
            "estimation_model": "recency-weighted, showcase-bias-adjusted",
        }

    def learned_market_values(
        self, *, min_proofs: int = 3, max_age_days: int = 90,
        recency_half_life_days: float = 14,
        proof_executability_weight: float = 0.50,
        verified_executability_weight: float = 0.85,
    ) -> list[dict]:
        """List eligible learned values and their distinct-proof support."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT o.asset_id, COALESCE(a.name, '') AS name
                FROM item_value_observations o
                LEFT JOIN tracked_assets a ON a.asset_id=o.asset_id
                ORDER BY o.asset_id
                """
            ).fetchall()
        values: list[dict] = []
        for row in rows:
            estimate = self.learned_item_value(
                row["asset_id"],
                min_proofs=min_proofs,
                max_age_days=max_age_days,
                recency_half_life_days=recency_half_life_days,
                proof_executability_weight=proof_executability_weight,
                verified_executability_weight=verified_executability_weight,
            )
            if estimate is not None:
                values.append(
                    {
                        "asset_id": int(row["asset_id"]),
                        "name": row["name"] or None,
                        **estimate,
                    }
                )
        return sorted(values, key=lambda item: (-item["proof_count"], item["asset_id"]))

    def market_status(
        self, *, min_proofs: int = 3, max_age_days: int = 90
    ) -> dict:
        """Return operational counts without exposing proof or account contents."""
        with self.connect() as connection:
            def count(query: str, parameters: tuple = ()) -> int:
                return int(connection.execute(query, parameters).fetchone()[0])

            status = {
                "catalog_assets": count("SELECT COUNT(asset_id) FROM tracked_assets"),
                "enabled_catalog_assets": count(
                    "SELECT COUNT(asset_id) FROM tracked_assets WHERE enabled=1"
                ),
                "priority_sweep_assets": count(
                    "SELECT COUNT(asset_id) FROM tracked_assets WHERE enabled=1 AND priority_score>0 AND priority_until>?",
                    (datetime.now(UTC).isoformat(),),
                ),
                "baseline_assets_completed": count(
                    "SELECT COUNT(asset_id) FROM tracked_assets WHERE last_completed_at IS NOT NULL"
                ),
                "baseline_assets_in_progress": count(
                    "SELECT COUNT(asset_id) FROM tracked_assets WHERE last_completed_at IS NULL AND cursor IS NOT NULL"
                ),
                "baseline_assets_untouched": count(
                    "SELECT COUNT(asset_id) FROM tracked_assets WHERE last_completed_at IS NULL AND cursor IS NULL"
                ),
                "hidden_owner_backoff_assets": count(
                    "SELECT COUNT(asset_id) FROM tracked_assets WHERE consecutive_hidden_pages>=20"
                ),
                "known_collectible_owners": count(
                    "SELECT COUNT(uaid) FROM uaid_ownership"
                ),
                "watched_users": count(
                    "SELECT COUNT(user_id) FROM watched_users WHERE enabled=1"
                ),
                "watched_users_due": count(
                    """SELECT COUNT(user_id) FROM watched_users
                       WHERE enabled=1 AND COALESCE(next_poll_at, added_at)<=?""",
                    (datetime.now(UTC).isoformat(),),
                ),
                "rolimons_trade_ads": count(
                    "SELECT COUNT(ad_id) FROM rolimons_trade_ads"
                ),
                "rolimons_trade_advertisers": count(
                    "SELECT COUNT(DISTINCT user_id) FROM rolimons_trade_ads"
                ),
                "active_trade_ad_users": count(
                    "SELECT COUNT(user_id) FROM watched_users WHERE enabled=1 AND last_trade_ad_at IS NOT NULL"
                ),
                "admitted_trade_ad_users": count(
                    "SELECT COUNT(user_id) FROM watched_users WHERE trade_ad_admitted=1"
                ),
                "unadmitted_trade_ad_users": count(
                    "SELECT COUNT(user_id) FROM watched_users WHERE last_trade_ad_at IS NOT NULL AND trade_ad_admitted=0"
                ),
                "archived_trade_ad_users": count(
                    "SELECT COUNT(user_id) FROM watched_users WHERE trade_ad_archived_at IS NOT NULL"
                ),
                "rolimons_trade_ads_24h": count(
                    "SELECT COUNT(ad_id) FROM rolimons_trade_ads WHERE created_at>=?",
                    ((datetime.now(UTC) - timedelta(days=1)).isoformat(),),
                ),
                "rolimons_advertised_assets": count(
                    "SELECT COUNT(DISTINCT asset_id) FROM rolimons_trade_ad_items"
                ),
                "ownership_transfers": count(
                    "SELECT COUNT(id) FROM ownership_transfers"
                ),
                "unmatched_transfers": count(
                    "SELECT COUNT(id) FROM ownership_transfers WHERE status='unmatched'"
                ),
                "inferred_trades": count("SELECT COUNT(id) FROM inferred_trades"),
                "successful_proofs": count(
                    "SELECT COUNT(message_id) FROM proof_messages WHERE status='succeeded'"
                ),
                "failed_proofs": count(
                    "SELECT COUNT(message_id) FROM proof_messages WHERE status='failed'"
                ),
                "proof_trade_links": count(
                    "SELECT COUNT(message_id) FROM proof_trade_links"
                ),
                "displayed_value_observations": count(
                    """
                    SELECT COUNT(observation_key) FROM item_value_observations
                    WHERE observation_kind='displayed'
                    """
                ),
                "implied_value_observations": count(
                    """
                    SELECT COUNT(observation_key) FROM item_value_observations
                    WHERE observation_kind='implied'
                    """
                ),
            }
        status["eligible_market_estimates"] = len(
            self.learned_market_values(
                min_proofs=min_proofs, max_age_days=max_age_days
            )
        )
        status["scheduler_cooldown"] = self.scheduler_cooldown()
        return status

    def fail_proof_message(self, raw, error: str) -> None:
        source, channel_id, message_id = self._proof_key(raw)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE proof_messages
                SET status = 'failed', error = ?, updated_at = ?
                WHERE source = ? AND channel_id = ? AND message_id = ?
                """,
                (
                    error[:1000],
                    datetime.now(UTC).isoformat(),
                    source,
                    channel_id,
                    message_id,
                ),
            )
            connection.commit()

    def proof_message_history(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT source, channel_id, message_id, content_hash, status,
                       attempts, raw_text, image_urls, parsed_proof, error,
                       author, message_timestamp, first_seen_at, updated_at
                FROM proof_messages ORDER BY first_seen_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "source": row["source"],
                "channel_id": row["channel_id"],
                "message_id": row["message_id"],
                "content_hash": row["content_hash"],
                "status": row["status"],
                "attempts": row["attempts"],
                "raw_text": row["raw_text"],
                "image_urls": json.loads(row["image_urls"]),
                "parsed_proof": (
                    json.loads(row["parsed_proof"])
                    if row["parsed_proof"]
                    else None
                ),
                "error": row["error"],
                "author": row["author"],
                "message_timestamp": row["message_timestamp"],
                "first_seen_at": row["first_seen_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def add_watched_user(self, user_id: int, *, source: str = "manual") -> None:
        user_id = int(user_id)
        if user_id <= 0:
            raise ValueError("user_id must be a positive integer")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO watched_users
                    (user_id, source, enabled, added_at, next_poll_at, poll_priority)
                VALUES (?, ?, 1, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    source = excluded.source, enabled = 1,
                    poll_priority=MAX(watched_users.poll_priority, excluded.poll_priority),
                    next_poll_at=MIN(COALESCE(watched_users.next_poll_at, excluded.next_poll_at), excluded.next_poll_at)
                """,
                (user_id, source[:100], datetime.now(UTC).isoformat(),
                 datetime.now(UTC).isoformat(), self._watched_user_priority(source)),
            )
            connection.commit()

    @staticmethod
    def _watched_user_priority(source: str) -> int:
        if source == "active_transfer":
            return 200
        if source == "trade_ad":
            return 150
        if source == "proof":
            return 100
        if source == "manual" or source == "cli":
            return 75
        return 0

    def ingest_rolimons_trade_ads(
        self, ads, *, repromote_seconds: int = 1800,
        asset_priority_seconds: int = 21600,
        admission_window_seconds: int = 86400,
        admission_min_ads: int = 3,
        admission_min_offer_value: int = 100000,
        admission_max_users: int = 500,
        asset_min_offers: int = 3,
        asset_max_priority: int = 200,
    ) -> dict:
        now = datetime.now(UTC)
        now_text = now.isoformat()
        priority_until = (now + timedelta(seconds=max(asset_priority_seconds, 1))).isoformat()
        activity_cutoff = (now - timedelta(seconds=max(admission_window_seconds, 1))).isoformat()
        new_ads = 0
        advertisers: set[int] = set()
        offered_assets: set[int] = set()
        requested_assets: set[int] = set()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for ad in ads:
                created_at = datetime.fromtimestamp(int(ad.created_at), UTC)
                created_text = created_at.isoformat()
                inserted = connection.execute(
                    """
                    INSERT OR IGNORE INTO rolimons_trade_ads (
                        ad_id, created_at, user_id, username, offer_robux,
                        request_robux, request_tags, first_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (int(ad.ad_id), created_text, int(ad.user_id), ad.username[:100],
                     int(ad.offer_robux), int(ad.request_robux),
                     json.dumps(list(ad.request_tags)), now_text),
                )
                if not inserted.rowcount:
                    continue
                new_ads += 1
                advertisers.add(int(ad.user_id))
                connection.execute(
                    """
                    INSERT INTO watched_users (
                        user_id, source, enabled, added_at, next_poll_at,
                        poll_priority, username, last_trade_ad_at
                    ) VALUES (?, 'trade_ad', 0, ?, ?, 0, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username=excluded.username,
                        last_trade_ad_at=MAX(COALESCE(watched_users.last_trade_ad_at, excluded.last_trade_ad_at), excluded.last_trade_ad_at),
                        trade_ad_archived_at=NULL,
                        source=CASE WHEN watched_users.source IN
                            ('active_transfer', 'proof', 'manual', 'cli')
                            THEN watched_users.source ELSE 'trade_ad' END
                    """,
                    (int(ad.user_id), now_text, now_text, ad.username[:100], created_text),
                )
                for side, items in (("offer", ad.offer_items), ("request", ad.request_items)):
                    for position, asset_id in enumerate(items):
                        asset_id = int(asset_id)
                        connection.execute(
                            """INSERT INTO rolimons_trade_ad_items
                               (ad_id, side, position, asset_id) VALUES (?, ?, ?, ?)""",
                            (int(ad.ad_id), side, position, asset_id),
                        )
                        (offered_assets if side == "offer" else requested_assets).add(asset_id)

            # Recompute a bounded cohort. All ads remain stored, but a one-off
            # advertiser only enters polling when offering a high-value item.
            connection.execute(
                "UPDATE watched_users SET trade_ad_admitted=0, trade_ad_score=0 WHERE last_trade_ad_at IS NOT NULL"
            )
            connection.execute(
                "UPDATE watched_users SET enabled=0, poll_priority=0 WHERE source='trade_ad'"
            )
            candidates = connection.execute(
                """
                SELECT a.user_id, MAX(a.username) AS username,
                       MAX(a.created_at) AS last_ad_at,
                       COUNT(DISTINCT a.ad_id) AS ad_count,
                       COALESCE(MAX(t.market_value), 0) AS max_offer_value
                FROM rolimons_trade_ads a
                LEFT JOIN rolimons_trade_ad_items i
                  ON i.ad_id=a.ad_id AND i.side='offer'
                LEFT JOIN tracked_assets t ON t.asset_id=i.asset_id
                WHERE a.created_at>=?
                GROUP BY a.user_id
                HAVING COUNT(DISTINCT a.ad_id)>=? OR COALESCE(MAX(t.market_value), 0)>=?
                ORDER BY COUNT(DISTINCT a.ad_id) DESC,
                         COALESCE(MAX(t.market_value), 0) DESC,
                         MAX(a.created_at) DESC
                LIMIT ?
                """,
                (activity_cutoff, max(int(admission_min_ads), 1),
                 max(int(admission_min_offer_value), 0), max(int(admission_max_users), 1)),
            ).fetchall()
            admitted_users: set[int] = set()
            for candidate in candidates:
                user_id = int(candidate["user_id"])
                admitted_users.add(user_id)
                score = int(candidate["ad_count"]) * 10 + min(
                    int(candidate["max_offer_value"]) / 10000, 100
                )
                connection.execute(
                    """
                    UPDATE watched_users SET enabled=1, trade_ad_admitted=1,
                        trade_ad_score=?, poll_priority=MAX(poll_priority, 150),
                        next_poll_at=CASE
                            WHEN last_polled_at IS NULL OR last_polled_at<=? THEN
                                MIN(COALESCE(next_poll_at, ?), ?)
                            ELSE next_poll_at END
                    WHERE user_id=?
                    """,
                    (score, (now - timedelta(seconds=max(repromote_seconds, 1))).isoformat(),
                     now_text, now_text, user_id),
                )

            # Requested items are not ownership evidence. Promote only the top
            # repeatedly offered assets, keeping the priority lane selective.
            connection.execute(
                """UPDATE tracked_assets SET priority_score=0,
                       priority_reason=NULL, priority_until=NULL
                   WHERE priority_reason='trade_ad'"""
            )
            active_assets = connection.execute(
                """
                SELECT i.asset_id, COUNT(DISTINCT i.ad_id) AS offer_count
                FROM rolimons_trade_ad_items i
                JOIN rolimons_trade_ads a ON a.ad_id=i.ad_id
                WHERE i.side='offer' AND a.created_at>=?
                GROUP BY i.asset_id
                HAVING COUNT(DISTINCT i.ad_id)>=?
                ORDER BY COUNT(DISTINCT i.ad_id) DESC
                LIMIT ?
                """,
                (activity_cutoff, max(int(asset_min_offers), 1),
                 max(int(asset_max_priority), 1)),
            ).fetchall()
            for asset in active_assets:
                score = min(80 + int(asset["offer_count"]) * 5, 150)
                connection.execute(
                    """UPDATE tracked_assets SET priority_score=MAX(priority_score, ?),
                       priority_reason='trade_ad', priority_until=?,
                       next_sweep_at=MIN(next_sweep_at, ?) WHERE asset_id=?""",
                    (score, priority_until, now_text, int(asset["asset_id"])),
                )
            connection.commit()
        return {
            "received_ads": len(ads), "new_ads": new_ads,
            "new_advertisers": len(advertisers),
            "offered_assets": len(offered_assets),
            "requested_assets": len(requested_assets),
            "admitted_users": len(admitted_users),
            "priority_assets": len(active_assets),
        }

    def watched_user_ids(self) -> list[int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT user_id FROM watched_users WHERE enabled = 1 ORDER BY user_id"
            ).fetchall()
        return [int(row["user_id"]) for row in rows]

    def due_watched_user_ids(self, limit: int) -> list[int]:
        if int(limit) <= 0:
            return []
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id FROM watched_users
                WHERE enabled=1 AND COALESCE(next_poll_at, added_at) <= ?
                ORDER BY poll_priority DESC, COALESCE(next_poll_at, added_at) ASC,
                         user_id ASC
                LIMIT ?
                """,
                (now, int(limit)),
            ).fetchall()
        return [int(row["user_id"]) for row in rows]

    def archive_inactive_trade_ad_users(self, inactive_days: int = 365) -> int:
        """Disable polling only; ad, inventory, and ownership history is retained."""
        cutoff = (datetime.now(UTC) - timedelta(days=max(int(inactive_days), 1))).isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE watched_users SET enabled=0, poll_priority=0,
                    trade_ad_archived_at=?
                WHERE enabled=1 AND source='trade_ad'
                  AND last_trade_ad_at IS NOT NULL AND last_trade_ad_at < ?
                """,
                (datetime.now(UTC).isoformat(), cutoff),
            )
            connection.commit()
        return int(cursor.rowcount)

    def recommended_watched_user_delay(
        self, user_id: int, *, default_seconds: int,
        hot_window_seconds: int, hot_interval_seconds: int,
        warm_window_seconds: int, warm_interval_seconds: int,
        active_window_seconds: int, active_interval_seconds: int,
        cold_interval_seconds: int,
    ) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT source, last_trade_ad_at FROM watched_users WHERE user_id=?",
                (int(user_id),),
            ).fetchone()
        if row is None or not row["last_trade_ad_at"]:
            return max(int(default_seconds), 1)
        # A positively observed transfer is a stronger and more recent signal
        # than an older ad, so keep it on the normal active-user cadence.
        if row["source"] == "active_transfer":
            return max(int(default_seconds), 1)
        age = max(
            (datetime.now(UTC) - datetime.fromisoformat(row["last_trade_ad_at"])).total_seconds(),
            0,
        )
        if age <= hot_window_seconds:
            return max(int(hot_interval_seconds), 1)
        if age <= warm_window_seconds:
            return max(int(warm_interval_seconds), 1)
        if age <= active_window_seconds:
            return max(int(active_interval_seconds), 1)
        return max(int(cold_interval_seconds), 1)

    def schedule_next_user_poll(
        self, user_id: int, observed_at: datetime, *, delay_seconds: float,
        succeeded: bool,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE watched_users SET next_poll_at=?,
                    consecutive_failures=CASE WHEN ? THEN 0 ELSE consecutive_failures+1 END,
                    poll_priority=0
                WHERE user_id=?
                """,
                ((observed_at + timedelta(seconds=max(float(delay_seconds), 1))).isoformat(),
                 int(succeeded), int(user_id)),
            )
            connection.commit()

    def update_premium_status(self, user_id: int, premium: bool) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE watched_users
                SET premium_status = ?, last_premium_checked_at = ?
                WHERE user_id = ?
                """,
                (int(premium), datetime.now(UTC).isoformat(), int(user_id)),
            )
            connection.commit()

    def upsert_tracked_asset(
        self,
        asset_id: int,
        *,
        name: str,
        market_value: int,
        interval_seconds: int,
        demand_score: int = -1,
        trend_score: int = -1,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO tracked_assets (
                    asset_id, name, market_value, demand_score, trend_score,
                    interval_seconds, next_sweep_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                    name = excluded.name,
                    market_value = excluded.market_value,
                    demand_score = excluded.demand_score,
                    trend_score = excluded.trend_score,
                    interval_seconds = excluded.interval_seconds
                """,
                (
                    int(asset_id), name, max(int(market_value), 0),
                    int(demand_score), int(trend_score), interval_seconds, now,
                ),
            )
            connection.commit()

    def promote_tracked_asset(
        self, asset_id: int, *, reason: str, score: int = 100,
        duration_seconds: int = 604800,
    ) -> None:
        now = datetime.now(UTC)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE tracked_assets SET priority_score=MAX(priority_score, ?),
                    priority_reason=?, priority_until=?,
                    next_sweep_at=MIN(next_sweep_at, ?)
                WHERE asset_id=?
                """,
                (max(int(score), 1), reason[:200],
                 (now + timedelta(seconds=max(duration_seconds, 1))).isoformat(),
                 now.isoformat(), int(asset_id)),
            )
            connection.commit()

    def next_owner_sweep_target(self, lane: str = "any") -> dict | None:
        if lane not in {"any", "priority", "background"}:
            raise ValueError("lane must be any, priority, or background")
        now = datetime.now(UTC).isoformat()
        lane_sql = ""
        lane_params: tuple[str, ...] = ()
        if lane == "priority":
            lane_sql = "AND priority_score > 0 AND priority_until > ?"
            lane_params = (now,)
        elif lane == "background":
            lane_sql = "AND NOT (priority_score > 0 AND priority_until > ?)"
            lane_params = (now,)
        with self.connect() as connection:
            row = connection.execute(
                f"""
                SELECT asset_id, cursor FROM tracked_assets
                WHERE enabled = 1 AND next_sweep_at <= ?
                {lane_sql}
                ORDER BY CASE WHEN priority_score > 0 AND priority_until > ?
                         THEN priority_score ELSE 0 END DESC,
                    (last_completed_at IS NULL) DESC,
                    next_sweep_at ASC, asset_id ASC
                LIMIT 1
                """,
                (now, *lane_params, now),
            ).fetchone()
        return dict(row) if row else None

    def complete_owner_sweep_page(
        self,
        asset_id: int,
        *,
        next_cursor: str | None,
        observed_at: datetime,
        visible_owner_count: int | None = None,
        page_rotation_seconds: float = 0,
        hidden_retry_seconds: float = 86400,
        hidden_page_threshold: int = 20,
    ) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT interval_seconds, sweep_started_at, consecutive_hidden_pages FROM tracked_assets WHERE asset_id = ?",
                (int(asset_id),),
            ).fetchone()
            if row is None:
                raise ValueError(f"Asset {asset_id} is not tracked")
            if next_cursor:
                hidden_count = int(row["consecutive_hidden_pages"] or 0) + 1 if visible_owner_count == 0 else 0
                delay = max(float(page_rotation_seconds), 0.0)
                if hidden_count >= max(int(hidden_page_threshold), 1):
                    delay = max(delay, float(hidden_retry_seconds))
                connection.execute(
                    """
                    UPDATE tracked_assets
                    SET cursor = ?, sweep_started_at = COALESCE(sweep_started_at, ?),
                        next_sweep_at = ?, last_error = NULL,
                        consecutive_hidden_pages = ?,
                        last_visible_owner_at = CASE WHEN ? > 0 THEN ? ELSE last_visible_owner_at END
                    WHERE asset_id = ?
                    """,
                    (next_cursor, observed_at.isoformat(),
                     (observed_at + timedelta(seconds=delay)).isoformat(), hidden_count,
                     int(visible_owner_count or 0), observed_at.isoformat(), int(asset_id)),
                )
            else:
                next_sweep = observed_at + timedelta(seconds=row["interval_seconds"])
                connection.execute(
                    """
                    UPDATE tracked_assets
                    SET cursor = NULL, sweep_started_at = NULL,
                        last_completed_at = ?, next_sweep_at = ?, last_error = NULL
                    WHERE asset_id = ?
                    """,
                    (
                        observed_at.isoformat(),
                        next_sweep.isoformat(),
                        int(asset_id),
                    ),
                )
            connection.commit()

    def scheduler_cooldown(self) -> dict | None:
        now = datetime.now(UTC)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT cooldown_until FROM market_scheduler_state WHERE singleton_id=1"
            ).fetchone()
        if not row or not row[0]:
            return None
        until = datetime.fromisoformat(row[0])
        if until <= now:
            return None
        return {"until": until.isoformat(), "remaining_seconds": round((until-now).total_seconds(), 1)}

    def record_scheduler_rate_limit(self, retry_after: float | None) -> dict:
        now = datetime.now(UTC)
        with self.connect() as connection:
            count = int(connection.execute(
                "SELECT consecutive_rate_limits FROM market_scheduler_state WHERE singleton_id=1"
            ).fetchone()[0] or 0) + 1
            seconds = max(float(retry_after or 60), min(60 * (2 ** (count - 1)), 3600))
            until = now + timedelta(seconds=seconds)
            connection.execute(
                """UPDATE market_scheduler_state SET cooldown_until=?,
                   consecutive_rate_limits=?, last_rate_limit_at=? WHERE singleton_id=1""",
                (until.isoformat(), count, now.isoformat()),
            )
            connection.commit()
        return {"until": until.isoformat(), "remaining_seconds": seconds}

    def record_scheduler_success(self) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE market_scheduler_state SET consecutive_rate_limits=0, cooldown_until=NULL WHERE singleton_id=1"
            )
            connection.commit()

    def fail_owner_sweep_page(
        self, asset_id: int, error: str, *, retry_seconds: float = 300
    ) -> None:
        retry_at = datetime.now(UTC) + timedelta(
            seconds=max(float(retry_seconds), 1.0)
        )
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE tracked_assets SET next_sweep_at = ?, last_error = ?
                WHERE asset_id = ?
                """,
                (retry_at.isoformat(), error[:1000], int(asset_id)),
            )
            connection.commit()

    def disable_owner_sweep_asset(self, asset_id: int, error: str) -> None:
        """Stop scheduling an asset whose owner list Roblox will not expose."""
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE tracked_assets
                SET enabled = 0, cursor = NULL, sweep_started_at = NULL,
                    last_error = ?
                WHERE asset_id = ?
                """,
                (error[:1000], int(asset_id)),
            )
            connection.commit()

    def observe_owner_page(self, asset_id: int, owners, observed_at: datetime) -> list[int]:
        """Record only public owners positively returned by Roblox."""
        observed_text = observed_at.isoformat()
        transfer_ids: list[int] = []
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for owner in owners:
                if owner.owner_id is None:
                    continue
                existing = connection.execute(
                    "SELECT * FROM uaid_ownership WHERE uaid = ?", (int(owner.uaid),)
                ).fetchone()
                is_transfer = False
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO uaid_ownership
                            (uaid, asset_id, current_owner_id, first_seen_at, last_seen_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            owner.uaid,
                            int(asset_id),
                            owner.owner_id,
                            observed_text,
                            observed_text,
                        ),
                    )
                elif int(existing["current_owner_id"]) != owner.owner_id:
                    is_transfer = True
                    event_at = owner.updated_at
                    if event_at is None or event_at > observed_at + timedelta(minutes=5):
                        event_at = observed_at
                    cursor = connection.execute(
                        """
                        INSERT INTO ownership_transfers (
                            uaid, asset_id, previous_owner_id, new_owner_id,
                            detected_at, observed_rap
                        ) VALUES (?, ?, ?, ?, ?, 0)
                        """,
                        (
                            owner.uaid,
                            int(asset_id),
                            existing["current_owner_id"],
                            owner.owner_id,
                            event_at.isoformat(),
                        ),
                    )
                    transfer_ids.append(int(cursor.lastrowid))
                    connection.execute(
                        """UPDATE tracked_assets SET priority_score=MAX(priority_score, 200),
                           priority_reason='transfer', priority_until=?, next_sweep_at=MIN(next_sweep_at, ?)
                           WHERE asset_id=?""",
                        ((observed_at + timedelta(days=7)).isoformat(), observed_text, int(asset_id)),
                    )
                    connection.execute(
                        """
                        INSERT INTO watched_users
                            (user_id, source, enabled, added_at, next_poll_at, poll_priority)
                        VALUES (?, 'active_transfer', 1, ?, ?, 200)
                        ON CONFLICT(user_id) DO UPDATE SET
                            source = 'active_transfer', enabled = 1,
                            poll_priority=200, next_poll_at=MIN(
                                COALESCE(watched_users.next_poll_at, excluded.next_poll_at),
                                excluded.next_poll_at
                            )
                        """,
                        (existing["current_owner_id"], observed_text, observed_text),
                    )
                    connection.execute(
                        """
                        UPDATE uaid_ownership
                        SET current_owner_id = ?, asset_id = ?, last_seen_at = ?
                        WHERE uaid = ?
                        """,
                        (owner.owner_id, int(asset_id), observed_text, owner.uaid),
                    )
                else:
                    connection.execute(
                        "UPDATE uaid_ownership SET last_seen_at = ? WHERE uaid = ?",
                        (observed_text, owner.uaid),
                    )
                connection.execute(
                    """
                    INSERT INTO watched_users (user_id, source, enabled, added_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        enabled = CASE
                            WHEN excluded.enabled = 1 THEN 1
                            ELSE watched_users.enabled
                        END,
                        source = CASE
                            WHEN excluded.enabled = 1 THEN excluded.source
                            ELSE watched_users.source
                        END,
                        poll_priority = CASE WHEN excluded.enabled=1 THEN 200
                                             ELSE watched_users.poll_priority END,
                        next_poll_at = CASE WHEN excluded.enabled=1 THEN
                            MIN(COALESCE(watched_users.next_poll_at, excluded.added_at), excluded.added_at)
                            ELSE watched_users.next_poll_at END
                    """,
                    (
                        owner.owner_id,
                        "active_transfer" if is_transfer else "owner_sweep",
                        int(is_transfer),
                        observed_text,
                    ),
                )
            connection.commit()
        return transfer_ids

    def record_inventory_failure(
        self, user_id: int, error: str, observed_at: datetime
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO inventory_observations
                    (user_id, observed_at, successful, error)
                VALUES (?, ?, 0, ?)
                """,
                (int(user_id), observed_at.isoformat(), error[:1000]),
            )
            connection.execute(
                """
                UPDATE watched_users SET last_polled_at = ?, inventory_public = 0
                WHERE user_id = ?
                """,
                (observed_at.isoformat(), int(user_id)),
            )
            connection.commit()

    def observe_inventory(self, user_id: int, items, observed_at: datetime) -> list[int]:
        """Observe positive ownership only; missing items never imply transfers."""
        user_id = int(user_id)
        observed_text = observed_at.isoformat()
        transfer_ids: list[int] = []
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO inventory_observations
                    (user_id, observed_at, successful, item_count)
                VALUES (?, ?, 1, ?)
                """,
                (user_id, observed_text, len(items)),
            )
            connection.execute(
                """
                UPDATE watched_users SET last_polled_at = ?, inventory_public = 1
                WHERE user_id = ?
                """,
                (observed_text, user_id),
            )
            for item in items:
                existing = connection.execute(
                    "SELECT * FROM uaid_ownership WHERE uaid = ?", (int(item.uaid),)
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO uaid_ownership
                            (uaid, asset_id, current_owner_id, first_seen_at, last_seen_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (item.uaid, item.asset_id, user_id, observed_text, observed_text),
                    )
                    continue
                if int(existing["current_owner_id"]) != user_id:
                    cursor = connection.execute(
                        """
                        INSERT INTO ownership_transfers (
                            uaid, asset_id, previous_owner_id, new_owner_id,
                            detected_at, observed_rap
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item.uaid,
                            item.asset_id,
                            existing["current_owner_id"],
                            user_id,
                            observed_text,
                            item.rap,
                        ),
                    )
                    transfer_ids.append(int(cursor.lastrowid))
                    connection.execute(
                        """UPDATE tracked_assets SET priority_score=MAX(priority_score, 200),
                           priority_reason='transfer', priority_until=?, next_sweep_at=MIN(next_sweep_at, ?)
                           WHERE asset_id=?""",
                        ((observed_at + timedelta(days=7)).isoformat(), observed_text, int(item.asset_id)),
                    )
                connection.execute(
                    """
                    UPDATE uaid_ownership
                    SET asset_id = ?, current_owner_id = ?, last_seen_at = ?
                    WHERE uaid = ?
                    """,
                    (item.asset_id, user_id, observed_text, item.uaid),
                )
            connection.commit()
        return transfer_ids

    def correlate_reciprocal_transfers(self, window_seconds: int) -> list[int]:
        """Create inferred trades only when both directions of a swap exist."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM ownership_transfers
                WHERE status = 'unmatched' ORDER BY detected_at, id
                """
            ).fetchall()

        groups: dict[tuple[int, int], list[sqlite3.Row]] = {}
        for row in rows:
            pair = tuple(sorted((row["previous_owner_id"], row["new_owner_id"])))
            groups.setdefault(pair, []).append(row)

        created: list[int] = []
        for (user_a, user_b), transfers in groups.items():
            remaining = list(transfers)
            while remaining:
                seed = remaining.pop(0)
                seed_time = datetime.fromisoformat(seed["detected_at"])
                cluster = [seed]
                still_remaining = []
                for candidate in remaining:
                    candidate_time = datetime.fromisoformat(candidate["detected_at"])
                    if abs((candidate_time - seed_time).total_seconds()) <= window_seconds:
                        cluster.append(candidate)
                    else:
                        still_remaining.append(candidate)
                remaining = still_remaining

                a_to_b = [
                    row
                    for row in cluster
                    if row["previous_owner_id"] == user_a
                    and row["new_owner_id"] == user_b
                ]
                b_to_a = [
                    row
                    for row in cluster
                    if row["previous_owner_id"] == user_b
                    and row["new_owner_id"] == user_a
                ]
                # This is the critical sale filter: one-way movement is never a trade.
                if not a_to_b or not b_to_a:
                    continue
                if len(a_to_b) > 4 or len(b_to_a) > 4:
                    continue

                transfer_ids = sorted(row["id"] for row in cluster)
                correlation_key = hashlib.sha256(
                    ",".join(map(str, transfer_ids)).encode()
                ).hexdigest()
                timestamps = [datetime.fromisoformat(row["detected_at"]) for row in cluster]
                span_seconds = (max(timestamps) - min(timestamps)).total_seconds()
                confidence = 0.75
                if span_seconds <= 120:
                    confidence += 0.15
                if len(a_to_b) <= 4 and len(b_to_a) <= 4:
                    confidence += 0.05
                confidence = min(confidence, 0.95)

                with self.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO inferred_trades (
                            correlation_key, user_a_id, user_b_id,
                            window_started_at, window_ended_at, confidence,
                            possible_robux, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                        """,
                        (
                            correlation_key,
                            user_a,
                            user_b,
                            min(timestamps).isoformat(),
                            max(timestamps).isoformat(),
                            confidence,
                            datetime.now(UTC).isoformat(),
                        ),
                    )
                    if cursor.rowcount == 0:
                        connection.commit()
                        continue
                    trade_id = int(cursor.lastrowid)
                    for row in cluster:
                        connection.execute(
                            """
                            INSERT INTO inferred_trade_items (
                                inferred_trade_id, transfer_id, from_user_id,
                                to_user_id, uaid, asset_id, observed_rap
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                trade_id,
                                row["id"],
                                row["previous_owner_id"],
                                row["new_owner_id"],
                                row["uaid"],
                                row["asset_id"],
                                row["observed_rap"],
                            ),
                        )
                    placeholders = ",".join("?" for _ in transfer_ids)
                    connection.execute(
                        f"""
                        UPDATE ownership_transfers
                        SET status = 'matched', inferred_trade_id = ?
                        WHERE id IN ({placeholders})
                        """,
                        (trade_id, *transfer_ids),
                    )
                    connection.commit()
                    created.append(trade_id)
        return created

    def inferred_trade_history(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self.connect() as connection:
            trades = connection.execute(
                "SELECT * FROM inferred_trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            results = []
            for trade in trades:
                items = connection.execute(
                    """
                    SELECT from_user_id, to_user_id, uaid, asset_id, observed_rap
                    FROM inferred_trade_items WHERE inferred_trade_id = ?
                    ORDER BY transfer_id
                    """,
                    (trade["id"],),
                ).fetchall()
                results.append(
                    {
                        "id": trade["id"],
                        "user_a_id": trade["user_a_id"],
                        "user_b_id": trade["user_b_id"],
                        "window_started_at": trade["window_started_at"],
                        "window_ended_at": trade["window_ended_at"],
                        "confidence": trade["confidence"],
                        "possible_robux": bool(trade["possible_robux"]),
                        "items": [dict(item) for item in items],
                    }
                )
        return results

    @staticmethod
    def _state_from_row(row: sqlite3.Row) -> dict:
        all_paused = bool(row["all_paused"])
        trade_ads_paused = bool(row["trade_ads_paused"])
        roblox_trades_paused = bool(row["roblox_trades_paused"])
        return {
            "all_paused": all_paused,
            "trade_ads_paused": trade_ads_paused,
            "roblox_trades_paused": roblox_trades_paused,
            "trade_ads_enabled": not (all_paused or trade_ads_paused),
            "roblox_trades_enabled": not (all_paused or roblox_trades_paused),
            "revision": row["revision"],
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        }

    def get_automation_state(self) -> dict:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM automation_state WHERE singleton_id = 1"
            ).fetchone()
        if row is None:  # Defensive: migrate always creates this row.
            raise RuntimeError("Automation state was not initialized")
        return self._state_from_row(row)

    def update_automation_state(
        self,
        changes: dict[str, bool],
        *,
        actor: str,
        reason: str | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        allowed = {"all_paused", "trade_ads_paused", "roblox_trades_paused"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unknown automation fields: {', '.join(sorted(unknown))}")
        if not changes:
            raise ValueError("At least one automation field must be supplied")

        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM automation_state WHERE singleton_id = 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("Automation state was not initialized")
            previous = self._state_from_row(row)
            if expected_revision is not None and previous["revision"] != expected_revision:
                raise RevisionConflictError(
                    f"Expected revision {expected_revision}, current revision is "
                    f"{previous['revision']}"
                )

            stored = {
                "all_paused": previous["all_paused"],
                "trade_ads_paused": previous["trade_ads_paused"],
                "roblox_trades_paused": previous["roblox_trades_paused"],
            }
            stored.update(changes)
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                UPDATE automation_state
                SET all_paused = ?, trade_ads_paused = ?, roblox_trades_paused = ?,
                    revision = revision + 1, updated_at = ?, updated_by = ?
                WHERE singleton_id = 1
                """,
                (
                    int(stored["all_paused"]),
                    int(stored["trade_ads_paused"]),
                    int(stored["roblox_trades_paused"]),
                    now,
                    actor,
                ),
            )
            new_row = connection.execute(
                "SELECT * FROM automation_state WHERE singleton_id = 1"
            ).fetchone()
            current = self._state_from_row(new_row)
            connection.execute(
                """
                INSERT INTO automation_audit
                    (occurred_at, actor, previous_state, new_state, reason)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    now,
                    actor,
                    json.dumps(previous, sort_keys=True),
                    json.dumps(current, sort_keys=True),
                    reason,
                ),
            )
            connection.commit()
        return current

    def automation_history(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, occurred_at, actor, previous_state, new_state, reason
                FROM automation_audit ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "occurred_at": row["occurred_at"],
                "actor": row["actor"],
                "previous_state": json.loads(row["previous_state"]),
                "new_state": json.loads(row["new_state"]),
                "reason": row["reason"],
            }
            for row in rows
        ]

    def save_trade_evaluation(self, evaluation: dict, *, source: str = "api") -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO trade_evaluations (evaluated_at, source, evaluation)
                VALUES (?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    source[:100],
                    json.dumps(evaluation, sort_keys=True),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def trade_evaluation_history(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, evaluated_at, source, evaluation
                FROM trade_evaluations ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "evaluated_at": row["evaluated_at"],
                "source": row["source"],
                "evaluation": json.loads(row["evaluation"]),
            }
            for row in rows
        ]

    def record_trade_action(
        self,
        action: str,
        *,
        target_trade_id: int | None,
        success: bool,
        detail: str | None = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO trade_actions
                    (occurred_at, action, target_trade_id, success, detail)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    action[:50],
                    target_trade_id,
                    int(success),
                    detail[:500] if detail else None,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def trade_action_history(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, occurred_at, action, target_trade_id, success, detail
                FROM trade_actions ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "occurred_at": row["occurred_at"],
                "action": row["action"],
                "target_trade_id": row["target_trade_id"],
                "success": bool(row["success"]),
                "detail": row["detail"],
            }
            for row in rows
        ]
