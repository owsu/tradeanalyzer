from __future__ import annotations

from config import (
    ACCEPT_THRESHOLD,
    DECLINE_THRESHOLD,
    PROJECTED_REVIEW_SHARE_THRESHOLD,
)
from models import TradeSideSummary


def recommendation_for(
    score: float,
    receiving: TradeSideSummary,
    trade_type: str,
) -> str:
    if score >= ACCEPT_THRESHOLD:
        recommendation = "accept"
    elif score <= DECLINE_THRESHOLD:
        recommendation = "decline"
    else:
        recommendation = "review"

    # A projected item that is a material share of the receiving side never
    # auto-accepts. Tiny adds are still penalized proportionally and disclosed.
    if (
        receiving.projected_value_share >= PROJECTED_REVIEW_SHARE_THRESHOLD
        and recommendation == "accept"
    ):
        recommendation = "review"

    # A strong upgrade bonus must not auto-accept an item whose rated demand is
    # genuinely terrible. Low coverage alone does not trigger this gate.
    if (
        trade_type == "upgrade"
        and receiving.demand_coverage >= 0.75
        and receiving.weighted_demand <= 0.5
        and recommendation == "accept"
    ):
        recommendation = "review"

    return recommendation


def build_reasons(
    giving: TradeSideSummary,
    receiving: TradeSideSummary,
    trade_type: str,
    effective_difference: int,
    demand_difference: float,
) -> list[str]:
    reasons: list[str] = []

    if effective_difference > 0:
        reasons.append(f"Effective value gain of {effective_difference:,}")
    elif effective_difference < 0:
        reasons.append(f"Effective value loss of {abs(effective_difference):,}")
    else:
        reasons.append("No effective-value difference")

    if demand_difference >= 0.35:
        reasons.append("Receiving side has meaningfully better weighted demand")
    elif demand_difference <= -0.35:
        reasons.append("Receiving side has meaningfully worse weighted demand")

    if giving.demand_coverage < 1 or receiving.demand_coverage < 1:
        reasons.append(
            "Some items have no Rolimons demand rating; unrated demand was "
            "estimated at the normal baseline "
            f"(giving coverage {giving.demand_coverage:.0%}, "
            f"receiving coverage {receiving.demand_coverage:.0%})"
        )

    if trade_type == "upgrade":
        upgrade_cost = giving.effective_value - receiving.effective_value
        if upgrade_cost < 0:
            reasons.append(
                f"You are upgrading with an underpay of {abs(upgrade_cost):,}; "
                "this receives the strongest upgrade incentive"
            )
        elif upgrade_cost == 0:
            reasons.append(
                "You are upgrading at equal value; this receives the strongest "
                "upgrade incentive"
            )
        else:
            upgrade_cost_pct = upgrade_cost / max(giving.effective_value, 1) * 100
            reasons.append(
                f"You are upgrading with {upgrade_cost:,} overpay "
                f"({upgrade_cost_pct:.1f}% of giving value)"
            )
    elif trade_type == "downgrade":
        size_retention = (
            receiving.biggest_item_value / max(giving.biggest_item_value, 1)
        )
        reasons.append(
            "You are downgrading; the largest received item retains "
            f"{size_retention:.0%} of the largest given item's value"
        )
        if size_retention >= 0.85:
            reasons.append(
                "This is a near-size downgrade, so it receives a strong "
                "concentration incentive"
            )
        elif size_retention < 0.40:
            reasons.append(
                "The downgrade is heavily fragmented into smaller items"
            )
    else:
        reasons.append("Trade is roughly lateral by largest-item size")

    if receiving.projected_count:
        reasons.append(
            f"Receiving {receiving.projected_count} projected item(s), representing "
            f"{receiving.projected_value_share:.0%} of receiving effective value"
        )
    if giving.projected_count:
        reasons.append(
            f"Giving away {giving.projected_count} projected item(s), representing "
            f"{giving.projected_value_share:.0%} of giving effective value"
        )
    if receiving.rare_count:
        reasons.append(
            f"Receiving {receiving.rare_count} rare item(s), representing "
            f"{receiving.rare_value_share:.0%} of receiving effective value; "
            "market confidence may be lower"
        )
    if (
        trade_type == "upgrade"
        and receiving.demand_coverage >= 0.75
        and receiving.weighted_demand <= 0.5
    ):
        reasons.append(
            "Receiving side has rated terrible demand, so the upgrade requires review"
        )

    return reasons
