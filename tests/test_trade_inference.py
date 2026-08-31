from datetime import UTC, datetime, timedelta

from database import Database
from market.correlator import TradeCorrelator
from market.inventory_client import AssetOwnerInstance, CollectibleInstance
from models import RawProof
from market.inventory_client import (
    AssetOwnersUnavailableError,
    InventoryRateLimitError,
    HybridRobloxInventoryClient,
    RobloxInventoryClient,
)
from market.owner_sweeper import OwnerSweeper
from market.collector import InventoryCollector


def collectible(uaid, asset_id, rap=1000):
    return CollectibleInstance(uaid=uaid, asset_id=asset_id, name="Item", rap=rap)


def test_watched_user_polling_is_budgeted_and_rotates(tmp_path):
    database = Database(tmp_path / "market.db")
    for user_id in (1, 2, 3):
        database.add_watched_user(user_id)

    class Client:
        calls = []

        def collectible_inventory(self, user_id):
            self.calls.append(user_id)
            return []

        def premium_status(self, user_id):
            return None

    client = Client()
    results = InventoryCollector(
        database, client, poll_budget=2, request_delay_seconds=0,
        user_poll_interval_seconds=1800,
    ).poll_watched_users()

    assert [result["user_id"] for result in results] == [1, 2]
    assert database.due_watched_user_ids(10) == [3]


def test_open_cloud_inventory_parses_instance_ids():
    class Response:
        status_code = 200
        ok = True
        headers = {}

        def json(self):
            return {
                "inventoryItems": [{
                    "assetDetails": {"assetId": "1001", "instanceId": "90001"}
                }],
                "nextPageToken": "",
            }

    class Session:
        def __init__(self):
            self.headers = {}
            self.cookies = type("Cookies", (), {
                "set": lambda *args: None, "get": lambda *args: None,
            })()

        def get(self, url, **kwargs):
            assert url.startswith("https://apis.roblox.com/cloud/v2/users/20/")
            assert kwargs["headers"]["x-api-key"] == "key"
            assert "onlyCollectibles=true" in kwargs["params"]["filter"]
            return Response()

    client = HybridRobloxInventoryClient(
        session=Session(), open_cloud_api_key="key"
    )
    items = client.collectible_inventory(20)
    assert [(item.uaid, item.asset_id, item.rap) for item in items] == [
        (90001, 1001, 0)
    ]
    assert client.last_inventory_source == "open_cloud"


def test_identical_inventory_snapshot_skips_ownership_work(tmp_path):
    database = Database(tmp_path / "market.db")
    database.add_watched_user(1)
    start = datetime.now(UTC)
    first = database.observe_inventory_snapshot(
        1, [collectible(101, 1001)], start
    )
    second = database.observe_inventory_snapshot(
        1, [collectible(101, 1001)], start + timedelta(minutes=5)
    )
    assert first == ([], False)
    assert second == ([], True)


def test_watched_user_polling_stops_on_first_rate_limit(tmp_path):
    database = Database(tmp_path / "market.db")
    for user_id in (1, 2, 3):
        database.add_watched_user(user_id)

    class Client:
        calls = []

        def collectible_inventory(self, user_id):
            self.calls.append(user_id)
            raise InventoryRateLimitError(5, operation="Inventory")

    client = Client()
    results = InventoryCollector(
        database, client, poll_budget=3, request_delay_seconds=0,
    ).poll_watched_users()

    assert client.calls == [1]
    assert len(results) == 1
    assert "429" in results[0]["error"]
    assert database.scheduler_cooldown() is not None


def test_one_way_ownership_change_is_not_inferred_as_trade(tmp_path):
    database = Database(tmp_path / "market.db")
    start = datetime.now(UTC)
    database.observe_inventory(1, [collectible(101, 1001)], start)
    database.observe_inventory(2, [], start)

    # UAID 101 appears under user 2, but nothing moves from user 2 to user 1.
    database.observe_inventory(2, [collectible(101, 1001)], start + timedelta(seconds=30))

    assert TradeCorrelator(database).correlate() == []
    assert database.inferred_trade_history() == []
    with database.connect() as connection:
        transfer = connection.execute("SELECT * FROM ownership_transfers").fetchone()
    assert transfer["status"] == "unmatched"


def test_reciprocal_uaid_swap_creates_inferred_trade(tmp_path):
    database = Database(tmp_path / "market.db")
    start = datetime.now(UTC)
    database.observe_inventory(1, [collectible(101, 1001, 5000)], start)
    database.observe_inventory(2, [collectible(202, 2002, 6000)], start)

    database.observe_inventory(
        2, [collectible(101, 1001, 5000)], start + timedelta(seconds=30)
    )
    database.observe_inventory(
        1, [collectible(202, 2002, 6000)], start + timedelta(seconds=45)
    )

    inferred_ids = TradeCorrelator(database).correlate()
    assert len(inferred_ids) == 1
    trade = database.inferred_trade_history()[0]
    assert trade["confidence"] == 0.95
    assert trade["possible_robux"] is True
    assert {(item["from_user_id"], item["to_user_id"]) for item in trade["items"]} == {
        (1, 2),
        (2, 1),
    }


def test_private_or_failed_poll_does_not_create_transfers(tmp_path):
    database = Database(tmp_path / "market.db")
    start = datetime.now(UTC)
    database.observe_inventory(1, [collectible(101, 1001)], start)
    database.record_inventory_failure(1, "private", start + timedelta(minutes=1))

    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS count FROM ownership_transfers"
        ).fetchone()["count"]
    assert count == 0


def test_owner_sweep_baseline_is_dormant_then_reciprocal_changes_activate_users(tmp_path):
    database = Database(tmp_path / "market.db")
    start = datetime.now(UTC)
    database.upsert_tracked_asset(
        1001, name="A", market_value=100_000, interval_seconds=600
    )
    database.upsert_tracked_asset(
        2002, name="B", market_value=100_000, interval_seconds=600
    )

    database.observe_owner_page(
        1001, [AssetOwnerInstance(101, 1001, 1)], start
    )
    database.observe_owner_page(
        2002, [AssetOwnerInstance(202, 2002, 2)], start
    )
    assert database.watched_user_ids() == []

    database.observe_owner_page(
        1001, [AssetOwnerInstance(101, 1001, 2)], start + timedelta(seconds=30)
    )
    database.observe_owner_page(
        2002, [AssetOwnerInstance(202, 2002, 1)], start + timedelta(seconds=45)
    )

    assert database.watched_user_ids() == [1, 2]
    assert len(TradeCorrelator(database).correlate()) == 1


def test_hidden_owner_from_sweep_is_never_recorded(tmp_path):
    database = Database(tmp_path / "market.db")
    database.observe_owner_page(
        1001,
        [AssetOwnerInstance(101, 1001, None)],
        datetime.now(UTC),
    )

    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS count FROM uaid_ownership"
        ).fetchone()["count"]
    assert count == 0


def test_owner_sweep_cursor_is_checkpointed(tmp_path):
    database = Database(tmp_path / "market.db")
    database.upsert_tracked_asset(
        1001, name="A", market_value=100_000, interval_seconds=600
    )
    target = database.next_owner_sweep_target()
    assert target == {"asset_id": 1001, "cursor": None}

    database.complete_owner_sweep_page(
        1001,
        next_cursor="next-page",
        observed_at=datetime.now(UTC),
    )
    assert database.next_owner_sweep_target() == {
        "asset_id": 1001,
        "cursor": "next-page",
    }


def test_priority_and_background_owner_lanes_do_not_starve_each_other(tmp_path):
    database = Database(tmp_path / "market.db")
    for asset_id in (1001, 2002, 3003):
        database.upsert_tracked_asset(
            asset_id, name="Item", market_value=asset_id, interval_seconds=600
        )
    database.promote_tracked_asset(3003, reason="proof")

    assert database.next_owner_sweep_target("priority")["asset_id"] == 3003
    assert database.next_owner_sweep_target("background")["asset_id"] == 1001

    database.complete_owner_sweep_page(
        1001, next_cursor="page-2", observed_at=datetime.now(UTC),
        page_rotation_seconds=60,
    )
    assert database.next_owner_sweep_target("background")["asset_id"] == 2002


def test_rate_limit_cooldown_is_shared_and_persistent(tmp_path):
    database = Database(tmp_path / "market.db")
    cooldown = database.record_scheduler_rate_limit(120)
    assert cooldown["remaining_seconds"] == 120
    reopened = Database(tmp_path / "market.db")
    assert reopened.scheduler_cooldown()["remaining_seconds"] > 100


def test_owner_client_exposes_retry_after_on_429():
    class Response:
        status_code = 429
        ok = False
        headers = {"Retry-After": "90"}

    class Session:
        def __init__(self):
            self.headers = {}
            self.cookies = type("Cookies", (), {"set": lambda *args: None})()

        def get(self, *args, **kwargs):
            return Response()

    client = RobloxInventoryClient(session=Session())
    try:
        client.asset_owners_page(1001)
    except InventoryRateLimitError as exc:
        assert exc.retry_after == 90
    else:
        raise AssertionError("expected a rate-limit error")


def test_owner_sweep_stops_after_first_rate_limit(tmp_path):
    database = Database(tmp_path / "market.db")
    for asset_id in (1001, 2002):
        database.upsert_tracked_asset(
            asset_id, name="Item", market_value=100_000, interval_seconds=600
        )

    class LimitedClient:
        calls = 0

        def asset_owners_page(self, asset_id, *, cursor=None):
            self.calls += 1
            raise InventoryRateLimitError(120)

    client = LimitedClient()
    result = OwnerSweeper(database, client).sweep_due_pages(25)

    assert client.calls == 1
    assert result["pages"] == 1
    assert "429" in result["rate_limited"]["message"]
    assert result["errors"] == []


def test_owner_sweep_disables_forbidden_asset_and_continues(tmp_path):
    database = Database(tmp_path / "market.db")
    database.upsert_tracked_asset(
        1001, name="Migrated", market_value=200_000, interval_seconds=600
    )
    database.upsert_tracked_asset(
        2002, name="Accessible", market_value=100_000, interval_seconds=600
    )

    class Client:
        def asset_owners_page(self, asset_id, *, cursor=None):
            if asset_id == 1001:
                raise AssetOwnersUnavailableError("HTTP 403")
            return type("Page", (), {"owners": (), "next_cursor": None})()

    result = OwnerSweeper(database, Client()).sweep_due_pages(2)

    assert result["pages"] == 2
    with database.connect() as connection:
        forbidden = connection.execute(
            "SELECT enabled, last_error FROM tracked_assets WHERE asset_id = 1001"
        ).fetchone()
    assert forbidden["enabled"] == 0
    assert "403" in forbidden["last_error"]
    assert result["skipped_assets"][0]["asset_id"] == 1001
    assert result["errors"] == []


def test_exact_proof_items_link_to_inferred_swap_and_raise_value_confidence(tmp_path):
    database = Database(tmp_path / "market.db")
    start = datetime.now(UTC)
    database.observe_inventory(1, [collectible(101, 1001)], start)
    database.observe_inventory(2, [collectible(202, 2002)], start)
    database.observe_inventory(
        2, [collectible(101, 1001)], start + timedelta(seconds=30)
    )
    database.observe_inventory(
        1, [collectible(202, 2002)], start + timedelta(seconds=45)
    )
    trade_id = TradeCorrelator(database).correlate()[0]

    raw = RawProof(
        source="discord_bot",
        channel_id="proofs",
        message_id="123",
        timestamp=start + timedelta(minutes=1),
    )
    database.claim_proof_message(
        raw, "unique-content", max_attempts=3, processing_timeout_seconds=600
    )
    database.complete_proof_message(
        raw,
        {
            "giving": [{"name": "A", "asset_id": 1001, "market_value": 5000}],
            "receiving": [{"name": "B", "asset_id": 2002, "market_value": 6000}],
            "valid": True,
        },
    )

    links = database.reconcile_proofs_with_inferred_trades(7)
    assert links == [{"inferred_trade_id": trade_id, "message_id": "123"}]
    with database.connect() as connection:
        observations = connection.execute(
            "SELECT confidence, inferred_trade_id FROM item_value_observations"
        ).fetchall()
    assert all(row["confidence"] == 0.95 for row in observations)
    assert all(row["inferred_trade_id"] == trade_id for row in observations)


def test_learned_value_requires_distinct_proof_content(tmp_path):
    database = Database(tmp_path / "market.db")
    database.upsert_tracked_asset(
        1001, name="A", market_value=10_000, interval_seconds=600
    )
    database.upsert_tracked_asset(
        2002, name="B", market_value=12_000, interval_seconds=600
    )
    now = datetime.now(UTC)
    for message_id, content_hash, value in (
        ("1", "same", 10_000),
        ("2", "same", 10_000),
        ("3", "different", 12_000),
    ):
        raw = RawProof(
            source="discord_bot", channel_id="proofs",
            message_id=message_id, timestamp=now,
        )
        database.claim_proof_message(
            raw, content_hash, max_attempts=3, processing_timeout_seconds=600
        )
        database.complete_proof_message(
            raw,
            {
                "giving": [
                    {"name": "A", "asset_id": 1001, "market_value": 10_000}
                ],
                "receiving": [
                    {"name": "B", "asset_id": 2002, "market_value": value}
                ],
                "deal_type": "overpay",
                "deal_amount": max(value - 10_000, 0),
                "deal_item": "A",
                "valid": True,
            },
        )

    assert database.learned_item_value(1001, min_proofs=3) is None
    estimate = database.learned_item_value(1001, min_proofs=2)
    assert estimate is not None
    assert estimate["proof_count"] == 2
    assert estimate["value"] == 10_000
    assert estimate["source"] == "direct implied trades"


def test_proof_showcase_value_is_recency_weighted_and_bias_adjusted(tmp_path):
    database = Database(tmp_path / "market.db")
    database.upsert_tracked_asset(
        1001, name="Target", market_value=10_000, interval_seconds=600
    )
    now = datetime.now(UTC)
    for message_id, age_days, amount in (
        ("old-1", 60, 4_000),
        ("old-2", 55, 4_000),
        ("recent", 0, 2_000),
    ):
        raw = RawProof(
            source="discord_bot", channel_id="proofs", message_id=message_id,
            timestamp=now - timedelta(days=age_days),
        )
        database.claim_proof_message(
            raw, message_id, max_attempts=3, processing_timeout_seconds=600
        )
        database.complete_proof_message(raw, {
            "giving": [{"name": "Target", "asset_id": 1001, "market_value": 10_000}],
            "receiving": [{"name": "Payment", "asset_id": 2001, "market_value": 10_000}],
            "deal_type": "overpay", "deal_amount": amount,
            "deal_item": "Target", "valid": True,
        })

    estimate = database.learned_item_value(
        1001, min_proofs=3, max_age_days=90, recency_half_life_days=14,
        proof_executability_weight=0.5,
    )
    assert estimate["showcase_value"] == 12_000
    assert estimate["value"] == 11_000
    assert estimate["selection_bias_adjustment"] == -1_000
    assert estimate["showcase_uncertainty_pct"] >= estimate["uncertainty_pct"]


def test_peer_eligibility_uses_uncompressed_proof_uncertainty(tmp_path):
    database = Database(tmp_path / "market.db")
    for asset_id in (1001, 2001, 2002):
        database.upsert_tracked_asset(
            asset_id, name="Item", market_value=10_000, interval_seconds=600
        )
    now = datetime.now(UTC).isoformat()
    with database.connect() as connection:
        for message_id, asset_id, observed_value in (
            ("low", 2001, 8_000),
            ("high", 2002, 12_000),
        ):
            connection.execute(
                """
                INSERT INTO proof_messages (
                    source, channel_id, message_id, content_hash, status,
                    first_seen_at, updated_at
                ) VALUES ('test', 'proofs', ?, ?, 'succeeded', ?, ?)
                """,
                (message_id, message_id, now, now),
            )
            connection.execute(
                """
                INSERT INTO item_value_observations (
                    observation_key, asset_id, observed_value, observed_at,
                    source, observation_kind, baseline_value, confidence,
                    proof_source, proof_channel_id, proof_message_id
                ) VALUES (?, ?, ?, ?, 'proof', 'implied', 10000, 1.0,
                          'test', 'proofs', ?)
                """,
                (message_id, asset_id, observed_value, now, message_id),
            )
        connection.commit()

    # The 50% executability shrink would compress this 40% raw range to 20%.
    # It must remain ineligible because the underlying proof evidence is wide.
    assert database.learned_item_value(1001, min_proofs=2) is None


def test_overpay_and_underpay_create_signed_implied_values(tmp_path):
    database = Database(tmp_path / "market.db")
    database.upsert_tracked_asset(
        1001, name="Target", market_value=100_000, interval_seconds=600
    )
    now = datetime.now(UTC)
    for message_id, deal_type, amount in (
        ("op", "overpay", 20_000),
        ("up", "underpay", 10_000),
    ):
        raw = RawProof(
            source="discord_bot", channel_id="proofs",
            message_id=message_id, timestamp=now,
        )
        database.claim_proof_message(
            raw, message_id, max_attempts=3, processing_timeout_seconds=600
        )
        database.complete_proof_message(
            raw,
            {
                "giving": [
                    {"name": "Target", "asset_id": 1001, "market_value": 100_000}
                ],
                "receiving": [
                    {"name": "Other", "asset_id": 2002, "market_value": 100_000}
                ],
                "deal_type": deal_type,
                "deal_amount": amount,
                "deal_item": "Target",
                "valid": True,
            },
        )

    with database.connect() as connection:
        values = connection.execute(
            """
            SELECT observed_value FROM item_value_observations
            WHERE observation_kind='implied' ORDER BY observed_value
            """
        ).fetchall()
    assert [row["observed_value"] for row in values] == [90_000, 120_000]


def test_concentrated_payment_is_stronger_than_larger_fragmented_overpay(tmp_path):
    database = Database(tmp_path / "market.db")
    database.upsert_tracked_asset(
        1001, name="Target", market_value=100_000, interval_seconds=600
    )
    now = datetime.now(UTC)
    cases = (
        (
            "fragmented", 12_000,
            [
                {"name": f"Small {index}", "asset_id": 2000 + index,
                 "market_value": 28_000}
                for index in range(4)
            ],
        ),
        (
            "concentrated", 8_000,
            [
                {"name": "Main", "asset_id": 3001, "market_value": 90_000},
                {"name": "Add", "asset_id": 3002, "market_value": 18_000},
            ],
        ),
    )
    for message_id, overpay, payment in cases:
        raw = RawProof(
            source="discord_bot", channel_id="proofs",
            message_id=message_id, timestamp=now,
        )
        database.claim_proof_message(
            raw, message_id, max_attempts=3, processing_timeout_seconds=600
        )
        database.complete_proof_message(
            raw,
            {
                "giving": [
                    {"name": "Target", "asset_id": 1001, "market_value": 100_000}
                ],
                "receiving": payment,
                "deal_type": "overpay",
                "deal_amount": overpay,
                "deal_item": "Target",
                "valid": True,
            },
        )

    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT proof_message_id, observed_value, structural_compensation
            FROM item_value_observations WHERE observation_kind='implied'
            """
        ).fetchall()
    values = {row["proof_message_id"]: dict(row) for row in rows}
    assert values["fragmented"]["structural_compensation"] == 9_000
    assert values["fragmented"]["observed_value"] == 103_000
    assert values["concentrated"]["structural_compensation"] == 0
    assert values["concentrated"]["observed_value"] == 108_000
