from automation import AutomationController, AutomationPausedError
from database import Database, RevisionConflictError
from clients.rolimons import RolimonsClient


def test_automation_starts_fail_closed_and_persists(tmp_path):
    path = tmp_path / "trader.db"
    controller = AutomationController(Database(path))

    state = controller.state()
    assert state["all_paused"] is True
    assert state["trade_ads_enabled"] is False
    assert state["roblox_trades_enabled"] is False

    updated = controller.update(
        {
            "all_paused": False,
            "trade_ads_paused": False,
            "roblox_trades_paused": True,
        },
        actor="test",
        reason="paper trade ads only",
        expected_revision=state["revision"],
    )
    restarted = AutomationController(Database(path))
    assert restarted.state() == updated
    assert updated["trade_ads_enabled"] is True
    assert updated["roblox_trades_enabled"] is False


def test_global_pause_overrides_individual_switches(tmp_path):
    controller = AutomationController(Database(tmp_path / "trader.db"))
    controller.update(
        {"trade_ads_paused": False, "roblox_trades_paused": False}, actor="test"
    )

    try:
        controller.require_trade_ads_enabled()
    except AutomationPausedError as exc:
        assert "paused" in str(exc)
    else:
        raise AssertionError("global pause should block trade ads")


def test_updates_are_audited_and_revision_checked(tmp_path):
    database = Database(tmp_path / "trader.db")
    controller = AutomationController(database)
    initial = controller.state()
    controller.update(
        {"all_paused": False},
        actor="operator",
        reason="start paper trading",
        expected_revision=initial["revision"],
    )

    events = database.automation_history()
    assert events[0]["actor"] == "operator"
    assert events[0]["reason"] == "start paper trading"
    assert events[0]["previous_state"]["all_paused"] is True
    assert events[0]["new_state"]["all_paused"] is False

    try:
        controller.update(
            {"all_paused": True},
            actor="stale-client",
            expected_revision=initial["revision"],
        )
    except RevisionConflictError:
        pass
    else:
        raise AssertionError("stale revision should be rejected")


def test_trade_evaluations_are_persisted(tmp_path):
    database = Database(tmp_path / "trader.db")
    evaluation_id = database.save_trade_evaluation(
        {"score": 72.5, "recommendation": "accept"}, source="test"
    )

    records = database.trade_evaluation_history()
    assert records[0]["id"] == evaluation_id
    assert records[0]["source"] == "test"
    assert records[0]["evaluation"]["score"] == 72.5


class RecordingSession:
    def __init__(self):
        self.post_calls = []

    def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))
        raise AssertionError("HTTP request should not occur while paused")


def test_trade_ad_pause_is_enforced_before_http_request(tmp_path):
    controller = AutomationController(Database(tmp_path / "trader.db"))
    session = RecordingSession()
    client = RolimonsClient(
        session=session,
        auto_refresh=False,
        automation=controller,
    )

    try:
        client.post_trade_ad("cookie", [1], [2], [], 123)
    except AutomationPausedError:
        pass
    else:
        raise AssertionError("paused trade ad should have been rejected")

    assert session.post_calls == []
