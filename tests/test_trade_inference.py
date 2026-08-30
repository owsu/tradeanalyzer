from datetime import UTC, datetime, timedelta

from database import Database
from market.correlator import TradeCorrelator
from market.inventory_client import AssetOwnerInstance, CollectibleInstance
from models import RawProof
from market.inventory_client import (
    AssetOwnersUnavailableError,
    InventoryRateLimitError,
    RobloxInventoryClient,
)
from market.owner_sweeper import OwnerSweeper


def collectible(uaid, asset_id, rap=1000):
    return CollectibleInstance(uaid=uaid, asset_id=asset_id, name="Item", rap=rap)


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
