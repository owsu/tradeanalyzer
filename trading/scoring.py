from __future__ import annotations

from config import (
    BASE_SCORE,
    DEMAND_POINT_WEIGHT,
    DOWNGRADE_FRAGMENTED_MIN_RATIO,
    DOWNGRADE_FRAGMENTED_PENALTY,
    DOWNGRADE_HEAVY_FRAGMENTATION_PENALTY,
    DOWNGRADE_NEAR_SIZE_BONUS,
    DOWNGRADE_NEAR_SIZE_MIN_RATIO,
    DOWNGRADE_SOLID_SIZE_BONUS,
    DOWNGRADE_SOLID_SIZE_MIN_RATIO,
    ITEM_SIZE_WEIGHT,
    PROJECTED_GIVE_BONUS,
    PROJECTED_RECEIVE_PENALTY,
    RARE_RECEIVE_UNCERTAINTY_PENALTY,
    UPGRADE_EXCESS_OP_MAX_PENALTY,
    UPGRADE_EXCESS_OP_PENALTY_PER_PCT,
    UPGRADE_HIGH_OP_BONUS,
    UPGRADE_HIGH_OP_MAX_PCT,
    UPGRADE_LOW_OP_BONUS,
    UPGRADE_LOW_OP_MAX_PCT,
    UPGRADE_MODERATE_OP_BONUS,
    UPGRADE_MODERATE_OP_MAX_PCT,
    UPGRADE_UNDERPAY_BONUS,
    VALUE_SCORE_WEIGHT,
)
from models import TradeSideSummary


def calculate_demand_difference(
    giving: TradeSideSummary,
    receiving: TradeSideSummary,
) -> float:
    return receiving.weighted_demand - giving.weighted_demand


def calculate_upgrade_incentive(
    giving_value: int,
    receiving_value: int,
) -> float:
    """Reward efficient upgrades and penalize overpay that burns the benefit."""
    overpay = giving_value - receiving_value
    if overpay <= 0:
        return UPGRADE_UNDERPAY_BONUS

    overpay_pct = overpay / giving_value * 100
    if overpay_pct <= UPGRADE_LOW_OP_MAX_PCT:
        return UPGRADE_LOW_OP_BONUS
    if overpay_pct <= UPGRADE_MODERATE_OP_MAX_PCT:
        return UPGRADE_MODERATE_OP_BONUS
    if overpay_pct <= UPGRADE_HIGH_OP_MAX_PCT:
        return UPGRADE_HIGH_OP_BONUS

    excess_pct = overpay_pct - UPGRADE_HIGH_OP_MAX_PCT
    return -min(
        UPGRADE_EXCESS_OP_MAX_PENALTY,
        excess_pct * UPGRADE_EXCESS_OP_PENALTY_PER_PCT,
    )


def calculate_downgrade_incentive(
    giving_biggest_value: int,
    receiving_biggest_value: int,
) -> float:
    """Score how much of a downgrade remains concentrated in its main item."""
    retention_ratio = receiving_biggest_value / max(giving_biggest_value, 1)
    if retention_ratio >= DOWNGRADE_NEAR_SIZE_MIN_RATIO:
        return DOWNGRADE_NEAR_SIZE_BONUS
    if retention_ratio >= DOWNGRADE_SOLID_SIZE_MIN_RATIO:
        return DOWNGRADE_SOLID_SIZE_BONUS
    if retention_ratio >= DOWNGRADE_FRAGMENTED_MIN_RATIO:
        return -DOWNGRADE_FRAGMENTED_PENALTY
    return -DOWNGRADE_HEAVY_FRAGMENTATION_PENALTY


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
        direction_component = calculate_upgrade_incentive(
            giving.effective_value,
            receiving.effective_value,
        )
    elif trade_type == "downgrade":
        direction_component = calculate_downgrade_incentive(
            giving.biggest_item_value,
            receiving.biggest_item_value,
        )

    projected_component = (
        giving.projected_value_share * PROJECTED_GIVE_BONUS
        - receiving.projected_value_share * PROJECTED_RECEIVE_PENALTY
    )
    rare_component = (
        -receiving.rare_value_share * RARE_RECEIVE_UNCERTAINTY_PENALTY
    )

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
