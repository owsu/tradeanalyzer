import requests

from automation import AutomationController, AutomationPausedError
from clients.roblox import RobloxTradeClient
from database import Database


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.content = b"" if payload is None else b"json"

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.cookies = requests.cookies.RequestsCookieJar()
        self.headers = {}

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)


def enabled_controller(tmp_path):
    controller = AutomationController(Database(tmp_path / "trader.db"))
    controller.update(
        {"all_paused": False, "roblox_trades_paused": False}, actor="test"
    )
    return controller


def test_paused_client_cannot_accept_or_make_http_request(tmp_path):
    controller = AutomationController(Database(tmp_path / "trader.db"))
    session = FakeSession([])
    client = RobloxTradeClient("secret", 1, automation=controller, session=session)

    try:
        client.accept(123)
    except AutomationPausedError:
        pass
    else:
        raise AssertionError("paused Roblox action should be rejected")

    assert session.calls == []
    assert controller.database.trade_action_history()[0]["success"] is False


def test_csrf_token_is_retried_and_action_is_audited(tmp_path):
    controller = enabled_controller(tmp_path)
    session = FakeSession(
        [
            FakeResponse(403, {"errors": []}, {"x-csrf-token": "csrf"}),
            FakeResponse(200),
        ]
    )
    client = RobloxTradeClient("secret", 1, automation=controller, session=session)

    assert client.accept(123) == {}
    assert len(session.calls) == 2
    assert session.calls[1][2]["headers"]["X-CSRF-TOKEN"] == "csrf"
    action = controller.database.trade_action_history()[0]
    assert action["action"] == "accept"
    assert action["target_trade_id"] == 123
    assert action["success"] is True


def test_send_builds_offers_with_user_asset_ids(tmp_path):
    controller = enabled_controller(tmp_path)
    session = FakeSession([FakeResponse(200, {"id": 456})])
    client = RobloxTradeClient("secret", 10, automation=controller, session=session)

    result = client.send(20, [101, 102], [201], giving_robux=50)

    assert result == {"id": 456}
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url.endswith("/v2/trades/send")
    assert kwargs["json"]["offers"] == [
        {"userId": 10, "userAssetIds": [101, 102], "robux": 50},
        {"userId": 20, "userAssetIds": [201], "robux": 0},
    ]


def test_read_only_trade_listing_works_while_paused(tmp_path):
    controller = AutomationController(Database(tmp_path / "trader.db"))
    session = FakeSession([FakeResponse(200, {"data": [{"id": 1}]})])
    client = RobloxTradeClient("secret", 10, automation=controller, session=session)

    result = client.list_trades("inbound", limit=25)

    assert result["data"][0]["id"] == 1
    assert session.calls[0][2]["params"]["sortOrder"] == "Desc"
