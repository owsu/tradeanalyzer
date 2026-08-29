from __future__ import annotations

from config import (
    BASE_SCORE,
    DEMAND_POINT_WEIGHT,
    DOWNGRADE_EXPECTED_OP_PENALTY,
    ITEM_SIZE_WEIGHT,
    PROJECTED_GIVE_BONUS,
    PROJECTED_RECEIVE_PENALTY,
    RARE_RECEIVE_UNCERTAINTY_PENALTY,
    UPGRADE_BONUS,
    VALUE_SCORE_WEIGHT,
)
from models import TradeSideSummary


def calculate_demand_difference(
    giving: TradeSideSummary,
    receiving: TradeSideSummary,
) -> float:
    if giving.weighted_demand < 0 or receiving.weighted_demand < 0:
        return 0.0
    return receiving.weighted_demand - giving.weighted_demand


def calculate_score_components(
    giving: TradeSideSummary,
    receiving: TradeSideSummary,
    trade_type: str,
) -> tuple[dict[str, float], int, int, float]:
    if giving.effective_value <= 0:
        raise ValueError("Giving-side effective value must be greater than zero")

    effective_difference = receiving.effective_value - giving.effective_value
    base_difference = receiving.base_value - giving.base_value
    effective_difference_pct = effective_difference / giving.effective_value * 100
    demand_difference = calculate_demand_difference(giving, receiving)

    value_component = effective_difference_pct * VALUE_SCORE_WEIGHT
    demand_component = demand_difference * DEMAND_POINT_WEIGHT

    size_ratio = (
        receiving.biggest_item_value / max(giving.biggest_item_value, 1)
    ) - 1
    item_size_component = max(-10.0, min(10.0, size_ratio * ITEM_SIZE_WEIGHT))

    direction_component = 0.0
    if trade_type == "upgrade":
        direction_component = UPGRADE_BONUS
    elif trade_type == "downgrade":
        direction_component = -DOWNGRADE_EXPECTED_OP_PENALTY

    projected_component = (
        giving.projected_count * PROJECTED_GIVE_BONUS
        - receiving.projected_count * PROJECTED_RECEIVE_PENALTY
    )
    rare_component = -receiving.rare_count * RARE_RECEIVE_UNCERTAINTY_PENALTY

    components = {
        "base": BASE_SCORE,
        "effective_value": value_component,
        "demand": demand_component,
        "item_size": item_size_component,
        "trade_direction": direction_component,
        "projected_risk": projected_component,
        "rare_uncertainty": rare_component,
    }

    return components, base_difference, effective_difference, demand_difference


def total_score(components: dict[str, float]) -> float:
    return max(0.0, min(100.0, sum(components.values())))
