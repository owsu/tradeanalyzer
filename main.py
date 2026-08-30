from __future__ import annotations

import hmac
from functools import lru_cache

from flask import Flask, jsonify, request

from automation import AutomationController, AutomationPausedError
from clients.roblox import RobloxTradeClient, RobloxTradeError
from clients.rolimons import ItemNotFoundError, MarketDataError, RolimonsClient
from config import (
    AUTOMATION_CONTROL_TOKEN,
    DATABASE_PATH,
    LEARNED_VALUE_MAX_AGE_DAYS,
    LEARNED_VALUE_MIN_PROOFS,
    ROBLOX_SECURITY_COOKIE,
    ROBLOX_USER_ID,
)
from database import Database, RevisionConflictError
from trading.evaluator import TradeEvaluator


app = Flask(__name__)


@lru_cache(maxsize=1)
def get_database() -> Database:
    return Database(DATABASE_PATH)


@lru_cache(maxsize=1)
def get_automation() -> AutomationController:
    return AutomationController(get_database())


@lru_cache(maxsize=1)
def get_market() -> RolimonsClient:
    return RolimonsClient(automation=get_automation())


@lru_cache(maxsize=1)
def get_evaluator() -> TradeEvaluator:
    return TradeEvaluator(get_market(), learned_values=get_database())


@lru_cache(maxsize=1)
def get_roblox_trades() -> RobloxTradeClient:
    if not ROBLOX_SECURITY_COOKIE or not ROBLOX_USER_ID:
        raise RuntimeError(
            "ROBLOX_SECURITY_COOKIE and ROBLOX_USER_ID must be configured"
        )
    return RobloxTradeClient(
        ROBLOX_SECURITY_COOKIE,
        ROBLOX_USER_ID,
        automation=get_automation(),
    )


def _control_token_error():
    if not AUTOMATION_CONTROL_TOKEN:
        return None
    supplied_token = request.headers.get("X-Automation-Token", "")
    if not hmac.compare_digest(supplied_token, AUTOMATION_CONTROL_TOKEN):
        return jsonify({"error": "Invalid or missing automation control token"}), 401
    return None


@app.get("/health")
def health():
    market = get_market()
    return jsonify(
        {
            "ok": True,
            "catalog_items": len(market.items),
            "rolimons_endpoint": market.last_endpoint,
            "automation": get_automation().state(),
        }
    )


@app.get("/automation")
def automation_state():
    return jsonify(get_automation().state())


@app.put("/automation")
def update_automation_state():
    token_error = _control_token_error()
    if token_error:
        return token_error

    payload = request.get_json(silent=True) or {}
    fields = ("all_paused", "trade_ads_paused", "roblox_trades_paused")
    changes = {field: payload[field] for field in fields if field in payload}
    if any(type(value) is not bool for value in changes.values()):
        return jsonify({"error": "Pause fields must be JSON booleans"}), 400

    actor = str(payload.get("actor") or "api").strip()[:100] or "api"
    reason = payload.get("reason")
    if reason is not None:
        reason = str(reason).strip()[:500] or None
    expected_revision = payload.get("expected_revision")
    if expected_revision is not None and type(expected_revision) is not int:
        return jsonify({"error": "expected_revision must be an integer"}), 400

    try:
        state = get_automation().update(
            changes,
            actor=actor,
            reason=reason,
            expected_revision=expected_revision,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RevisionConflictError as exc:
        return jsonify({"error": str(exc), "current": get_automation().state()}), 409
    return jsonify(state)


@app.get("/automation/history")
def automation_history():
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    return jsonify({"events": get_database().automation_history(limit)})


@app.get("/value/<int:item_id>")
def get_value(item_id: int):
    try:
        item = get_market().get_item(item_id)
    except ItemNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except MarketDataError as exc:
        return jsonify({"error": str(exc)}), 503

    return jsonify(
        {
            "asset_id": item.asset_id,
            "name": item.name,
            "rap": item.rap,
            "rolimons_value": item.roli_value,
            "default_value": item.default_value,
            "base_value": item.base_value,
            "demand": {"name": item.demand_name, "score": item.demand_score},
            "projected": item.projected,
            "rare": item.rare,
        }
    )


@app.post("/evaluate")
def evaluate_trade():
    payload = request.get_json(silent=True) or {}
    giving = payload.get("giving")
    receiving = payload.get("receiving")

    if giving is None or receiving is None:
        return (
            jsonify(
                {
                    "error": "JSON body must contain both 'giving' and 'receiving'",
                    "example": {
                        "giving": [583721561, 10159622004],
                        "receiving": [19027209],
                    },
                }
            ),
            400,
        )

    try:
        evaluation = get_evaluator().evaluate(giving, receiving)
    except ItemNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    except MarketDataError as exc:
        return jsonify({"error": str(exc)}), 503

    result = evaluation.to_dict()
    evaluation_id = get_database().save_trade_evaluation(result, source="api")
    return jsonify({"evaluation_id": evaluation_id, **result})


@app.get("/evaluations")
def evaluation_history():
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    return jsonify({"evaluations": get_database().trade_evaluation_history(limit)})


@app.get("/proofs")
def proof_history():
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    return jsonify({"proofs": get_database().proof_message_history(limit)})


@app.get("/market-values")
def learned_market_values():
    return jsonify(
        {
            "minimum_distinct_proofs": LEARNED_VALUE_MIN_PROOFS,
            "maximum_age_days": LEARNED_VALUE_MAX_AGE_DAYS,
            "items": get_database().learned_market_values(
                min_proofs=LEARNED_VALUE_MIN_PROOFS,
                max_age_days=LEARNED_VALUE_MAX_AGE_DAYS,
            ),
        }
    )


@app.get("/market-status")
def market_status():
    return jsonify(
        get_database().market_status(
            min_proofs=LEARNED_VALUE_MIN_PROOFS,
            max_age_days=LEARNED_VALUE_MAX_AGE_DAYS,
        )
    )


def _roblox_call(operation):
    try:
        return jsonify(operation())
    except AutomationPausedError as exc:
        return jsonify({"error": str(exc)}), 409
    except RobloxTradeError as exc:
        status = exc.status_code or 502
        return jsonify({"error": str(exc), "roblox_status": exc.status_code}), (
            status if 400 <= status < 500 else 502
        )
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/roblox/trades")
def list_roblox_trades():
    return _roblox_call(
        lambda: get_roblox_trades().list_trades(
            request.args.get("status", "inbound"),
            limit=int(request.args.get("limit", "25")),
            cursor=request.args.get("cursor"),
            sort_order=request.args.get("sort_order", "Desc"),
        )
    )


@app.get("/roblox/trades/<int:trade_id>")
def get_roblox_trade(trade_id: int):
    return _roblox_call(lambda: get_roblox_trades().get_trade(trade_id))


def _trade_request_arguments(payload: dict) -> dict:
    required = (
        "partner_user_id",
        "giving_user_asset_ids",
        "receiving_user_asset_ids",
    )
    missing = [field for field in required if field not in payload]
    if missing:
        raise ValueError(f"Missing fields: {', '.join(missing)}")
    return {
        "partner_user_id": payload["partner_user_id"],
        "giving_user_asset_ids": payload["giving_user_asset_ids"],
        "receiving_user_asset_ids": payload["receiving_user_asset_ids"],
        "giving_robux": payload.get("giving_robux", 0),
        "receiving_robux": payload.get("receiving_robux", 0),
    }


@app.post("/roblox/trades/<int:trade_id>/accept")
def accept_roblox_trade(trade_id: int):
    token_error = _control_token_error()
    if token_error:
        return token_error
    return _roblox_call(lambda: get_roblox_trades().accept(trade_id))


@app.post("/roblox/trades/<int:trade_id>/decline")
def decline_roblox_trade(trade_id: int):
    token_error = _control_token_error()
    if token_error:
        return token_error
    return _roblox_call(lambda: get_roblox_trades().decline(trade_id))


@app.post("/roblox/trades/send")
def send_roblox_trade():
    token_error = _control_token_error()
    if token_error:
        return token_error
    payload = request.get_json(silent=True) or {}
    return _roblox_call(
        lambda: get_roblox_trades().send(**_trade_request_arguments(payload))
    )


@app.post("/roblox/trades/<int:trade_id>/counter")
def counter_roblox_trade(trade_id: int):
    token_error = _control_token_error()
    if token_error:
        return token_error
    payload = request.get_json(silent=True) or {}
    return _roblox_call(
        lambda: get_roblox_trades().counter(
            trade_id, **_trade_request_arguments(payload)
        )
    )


@app.get("/roblox/actions")
def roblox_action_history():
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    return jsonify({"actions": get_database().trade_action_history(limit)})


@app.post("/refresh")
def refresh_market_data():
    try:
        market = get_market()
        market.refresh()
        return jsonify(
            {
                "ok": True,
                "catalog_items": len(market.items),
                "rolimons_endpoint": market.last_endpoint,
            }
        )
    except MarketDataError as exc:
        return jsonify({"error": str(exc)}), 503


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
