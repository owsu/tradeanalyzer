from __future__ import annotations

from clients.rolimons import RolimonsClient
from trading.evaluator import TradeEvaluator


def main() -> None:
    market = RolimonsClient()
    evaluator = TradeEvaluator(market)

    giving = input("Items giving (comma-separated IDs): ").strip()
    receiving = input("Items receiving (comma-separated IDs): ").strip()

    evaluation = evaluator.evaluate(giving, receiving)
    print("\n" + evaluation.format_text())


if __name__ == "__main__":
    main()
