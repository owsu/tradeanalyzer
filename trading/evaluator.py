from __future__ import annotations

from typing import Iterable, Mapping, Protocol

from config import ESTIMATED_VALUES
from models import EvaluatedItem, ItemSnapshot, TradeEvaluation, TradeSideSummary
from trading.risk import build_reasons, recommendation_for
from trading.scoring import calculate_score_components, total_score


class MarketClient(Protocol):
    def get_item(self, asset_id: int) -> ItemSnapshot: ...


class TradeEvaluator:
    def __init__(
        self,
        market: MarketClient,
        *,
        estimated_values: Mapping[int, int] | None = None,
    ) -> None:
        self.market = market
        self.estimated_values = dict(
            ESTIMATED_VALUES if estimated_values is None else estimated_values
        )

    @staticmethod
    def _parse_ids(items: str | Iterable[int]) -> list[int]:
        if isinstance(items, str):
            parsed = [int(part.strip()) for part in items.split(",") if part.strip()]
        else:
            parsed = [int(item) for item in items]

        if not parsed:
            raise ValueError("A trade side must contain at least one item")
        if len(parsed) > 4:
            raise ValueError("A Roblox trade side can contain at most 4 items")
        return parsed

    def _evaluate_item(self, asset_id: int) -> EvaluatedItem:
        item = self.market.get_item(asset_id)
        if asset_id in self.estimated_values:
            effective_value = int(self.estimated_values[asset_id])
            source = "custom estimate"
        else:
            effective_value = item.base_value
            source = "Rolimons Value" if item.roli_value is not None else "RAP"

        return EvaluatedItem(
            asset_id=item.asset_id,
            name=item.name,
            rap=item.rap,
            roli_value=item.roli_value,
            base_value=item.base_value,
            effective_value=effective_value,
            effective_value_source=source,
            demand_name=item.demand_name,
            demand_score=item.demand_score,
            projected=item.projected,
            rare=item.rare,
        )

    @staticmethod
    def _summarize(items: list[EvaluatedItem]) -> TradeSideSummary:
        base_value = sum(item.base_value for item in items)
        effective_value = sum(item.effective_value for item in items)
        biggest_item_value = max(item.base_value for item in items)

        demand_items = [item for item in items if item.demand_score >= 0]
        demand_weight_total = sum(max(item.effective_value, 1) for item in demand_items)
        if demand_items and demand_weight_total:
            weighted_demand = sum(
                item.demand_score * max(item.effective_value, 1)
                for item in demand_items
            ) / demand_weight_total
        else:
            weighted_demand = -1.0

        return TradeSideSummary(
            items=items,
            base_value=base_value,
            effective_value=effective_value,
            weighted_demand=weighted_demand,
            biggest_item_value=biggest_item_value,
            projected_count=sum(item.projected for item in items),
            rare_count=sum(item.rare for item in items),
        )

    @staticmethod
    def _trade_type(giving: TradeSideSummary, receiving: TradeSideSummary) -> str:
        if receiving.biggest_item_value > giving.biggest_item_value:
            return "upgrade"
        if receiving.biggest_item_value < giving.biggest_item_value:
            return "downgrade"
        return "lateral"

    def evaluate(
        self,
        items_giving: str | Iterable[int],
        items_receiving: str | Iterable[int],
    ) -> TradeEvaluation:
        giving_ids = self._parse_ids(items_giving)
        receiving_ids = self._parse_ids(items_receiving)

        giving = self._summarize(
            [self._evaluate_item(asset_id) for asset_id in giving_ids]
        )
        receiving = self._summarize(
            [self._evaluate_item(asset_id) for asset_id in receiving_ids]
        )
        trade_type = self._trade_type(giving, receiving)

        (
            components,
            base_difference,
            effective_difference,
            demand_difference,
        ) = calculate_score_components(giving, receiving, trade_type)

        score = total_score(components)
        recommendation = recommendation_for(score, receiving)
        reasons = build_reasons(
            giving,
            receiving,
            trade_type,
            effective_difference,
            demand_difference,
        )

        return TradeEvaluation(
            score=round(score, 2),
            recommendation=recommendation,
            trade_type=trade_type,
            giving=giving,
            receiving=receiving,
            base_value_difference=base_difference,
            effective_value_difference=effective_difference,
            demand_difference=round(demand_difference, 3),
            score_components={
                key: round(value, 3) for key, value in components.items()
            },
            reasons=reasons,
        )


def calculate(
    items_giving: str | Iterable[int],
    items_receiving: str | Iterable[int],
    *,
    market: MarketClient | None = None,
) -> TradeEvaluation:
    if market is None:
        from clients.rolimons import RolimonsClient

        market = RolimonsClient()
    return TradeEvaluator(market).evaluate(items_giving, items_receiving)


# Backwards-compatible name from the original prototype.
Calculate = calculate
