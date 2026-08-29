from __future__ import annotations

from functools import lru_cache

from flask import Flask, jsonify, request

from clients.rolimons import ItemNotFoundError, MarketDataError, RolimonsClient
from trading.evaluator import TradeEvaluator


app = Flask(__name__)


@lru_cache(maxsize=1)
def get_market() -> RolimonsClient:
    return RolimonsClient()


@lru_cache(maxsize=1)
def get_evaluator() -> TradeEvaluator:
    return TradeEvaluator(get_market())


@app.get("/health")
def health():
    market = get_market()
    return jsonify(
        {
            "ok": True,
            "catalog_items": len(market.items),
            "rolimons_endpoint": market.last_endpoint,
        }
    )


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

    return jsonify(evaluation.to_dict())


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
