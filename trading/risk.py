from __future__ import annotations

from config import ACCEPT_THRESHOLD, DECLINE_THRESHOLD
from models import TradeSideSummary


def recommendation_for(score: float, receiving: TradeSideSummary) -> str:
    if score >= ACCEPT_THRESHOLD:
        recommendation = "accept"
    elif score <= DECLINE_THRESHOLD:
        recommendation = "decline"
    else:
        recommendation = "review"

    # Hard risk gate: projected receives never auto-accept.
    if receiving.projected_count and recommendation == "accept":
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

    if trade_type == "upgrade":
        reasons.append("You are upgrading into a larger item")
    elif trade_type == "downgrade":
        reasons.append("You are downgrading, so overpay is expected")
    else:
        reasons.append("Trade is roughly lateral by largest-item size")

    if receiving.projected_count:
        reasons.append(
            f"Receiving {receiving.projected_count} projected item(s) adds major risk"
        )
    if giving.projected_count:
        reasons.append(
            f"Giving away {giving.projected_count} projected item(s) reduces risk"
        )
    if receiving.rare_count:
        reasons.append(
            f"Receiving {receiving.rare_count} rare item(s); market confidence may be lower"
        )

    return reasons
