from __future__ import annotations

from typing import Iterable, Mapping, Protocol

from config import (
    ESTIMATED_VALUES,
    LEARNED_VALUE_MAX_AGE_DAYS,
    LEARNED_VALUE_MIN_PROOFS,
    LEARNED_VALUE_RECENCY_HALF_LIFE_DAYS,
    LEARNED_VALUE_PROOF_EXECUTABILITY_WEIGHT,
    LEARNED_VALUE_VERIFIED_EXECUTABILITY_WEIGHT,
    UNRATED_DEMAND_BASELINE,
    RAP_VALUE_BAD_RATIO,
    RAP_VALUE_DECENT_RATIO,
    RAP_VALUE_GOOD_RATIO,
    RAP_VALUE_RAISING_RATIO,
    RAP_VALUE_BAD_MULTIPLIER,
    RAP_VALUE_DECENT_MULTIPLIER,
    RAP_VALUE_GOOD_MULTIPLIER,
    RAP_VALUE_LOWERING_EXTRA_PENALTY,
    RAP_VALUE_RAISING_MAX_BONUS,
)
from models import EvaluatedItem, ItemSnapshot, TradeEvaluation, TradeSideSummary
from trading.risk import build_reasons, recommendation_for
from trading.scoring import calculate_score_components, total_score


class MarketClient(Protocol):
    def get_item(self, asset_id: int) -> ItemSnapshot: ...


class LearnedValueProvider(Protocol):
    def learned_item_value(
        self, asset_id: int, *, min_proofs: int, max_age_days: int,
        recency_half_life_days: float, proof_executability_weight: float,
        verified_executability_weight: float,
    ) -> dict | None: ...


class TradeEvaluator:
    def __init__(
        self,
        market: MarketClient,
        *,
        estimated_values: Mapping[int, int] | None = None,
        learned_values: LearnedValueProvider | None = None,
    ) -> None:
        self.market = market
        self.estimated_values = dict(
            ESTIMATED_VALUES if estimated_values is None else estimated_values
        )
        self.learned_values = learned_values

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
        elif self.learned_values is not None and (
            learned := self.learned_values.learned_item_value(
                asset_id,
                min_proofs=LEARNED_VALUE_MIN_PROOFS,
                max_age_days=LEARNED_VALUE_MAX_AGE_DAYS,
                recency_half_life_days=LEARNED_VALUE_RECENCY_HALF_LIFE_DAYS,
                proof_executability_weight=LEARNED_VALUE_PROOF_EXECUTABILITY_WEIGHT,
                verified_executability_weight=LEARNED_VALUE_VERIFIED_EXECUTABILITY_WEIGHT,
            )
        ) is not None:
            effective_value = int(learned["value"])
            source = (
                f"implied market ({learned['source']}, "
                f"{learned['proof_count']} proofs)"
            )
        else:
            effective_value = item.base_value
            source = "Rolimons Value" if item.roli_value is not None else "RAP"

        rap_value_ratio: float | None = None
        rap_value_modifier = 1.0
        eligible_for_guard = (
            source != "custom estimate"
            and item.roli_value is not None and item.roli_value > 0
            and item.demand_score >= 2
            and item.trend_score in {0, 2, 3}
        )
        if eligible_for_guard:
            rap_value_ratio = item.rap / item.roli_value
            candidate_modifier = 1.0
            if rap_value_ratio <= RAP_VALUE_BAD_RATIO:
                candidate_modifier = RAP_VALUE_BAD_MULTIPLIER
            elif rap_value_ratio < RAP_VALUE_DECENT_RATIO:
                candidate_modifier = RAP_VALUE_DECENT_MULTIPLIER
            elif rap_value_ratio < RAP_VALUE_GOOD_RATIO:
                candidate_modifier = RAP_VALUE_GOOD_MULTIPLIER
            if item.trend_score == 0 and candidate_modifier < 1:
                candidate_modifier = max(
                    candidate_modifier - RAP_VALUE_LOWERING_EXTRA_PENALTY,
                    0.85,
                )
            elif (
                item.trend_score == 3
                and rap_value_ratio >= RAP_VALUE_RAISING_RATIO
                and effective_value <= item.roli_value
            ):
                candidate_modifier = 1 + RAP_VALUE_RAISING_MAX_BONUS

            guarded_value = round(item.roli_value * candidate_modifier)
            guarded_effective = effective_value
            if candidate_modifier < 1:
                guarded_effective = min(effective_value, guarded_value)
            elif candidate_modifier > 1:
                guarded_effective = max(effective_value, guarded_value)
            if guarded_effective != effective_value:
                effective_value = guarded_effective
                rap_value_modifier = candidate_modifier
                source += (
                    f"; RAP/value guard {rap_value_modifier:.0%} "
                    f"({item.trend_name})"
                )

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
            trend_name=item.trend_name,
            trend_score=item.trend_score,
            rap_value_ratio=rap_value_ratio,
            rap_value_modifier=rap_value_modifier,
        )

    @staticmethod
    def _summarize(items: list[EvaluatedItem]) -> TradeSideSummary:
        base_value = sum(item.base_value for item in items)
        effective_value = sum(item.effective_value for item in items)
        biggest_item_value = max(item.base_value for item in items)

        demand_weight_total = sum(max(item.effective_value, 1) for item in items)
        known_demand_weight = sum(
            max(item.effective_value, 1) for item in items if item.demand_score >= 0
        )
        weighted_demand = sum(
            (
                item.demand_score
                if item.demand_score >= 0
                else UNRATED_DEMAND_BASELINE
            )
            * max(item.effective_value, 1)
            for item in items
        ) / demand_weight_total
        demand_coverage = known_demand_weight / demand_weight_total

        return TradeSideSummary(
            items=items,
            base_value=base_value,
            effective_value=effective_value,
            weighted_demand=weighted_demand,
            demand_coverage=demand_coverage,
            biggest_item_value=biggest_item_value,
            projected_count=sum(item.projected for item in items),
            projected_value_share=sum(
                item.effective_value for item in items if item.projected
            )
            / max(effective_value, 1),
            rare_count=sum(item.rare for item in items),
            rare_value_share=sum(item.effective_value for item in items if item.rare)
            / max(effective_value, 1),
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
        recommendation = recommendation_for(score, receiving, trade_type)
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
