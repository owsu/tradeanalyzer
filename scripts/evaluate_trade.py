from __future__ import annotations

from clients.rolimons import RolimonsClient
from config import DATABASE_PATH
from database import Database
from trading.evaluator import TradeEvaluator


def main() -> None:
    market = RolimonsClient()
    evaluator = TradeEvaluator(market, learned_values=Database(DATABASE_PATH))

    giving = input("Items giving (comma-separated IDs): ").strip()
    receiving = input("Items receiving (comma-separated IDs): ").strip()

    evaluation = evaluator.evaluate(giving, receiving)
    print("\n" + evaluation.format_text())


if __name__ == "__main__":
    main()
