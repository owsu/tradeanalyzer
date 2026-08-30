from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date as Date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Market/trading models
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ItemSnapshot:
    asset_id: int
    name: str
    acronym: str
    rap: int
    roli_value: int | None
    default_value: int
    demand_name: str
    demand_score: int
    projected: bool
    rare: bool

    @property
    def base_value(self) -> int:
        if self.roli_value is not None and self.roli_value > 0:
            return self.roli_value
        if self.rap > 0:
            return self.rap
        return max(self.default_value, 0)


@dataclass(frozen=True)
class EvaluatedItem:
    asset_id: int
    name: str
    rap: int
    roli_value: int | None
    base_value: int
    effective_value: int
    effective_value_source: str
    demand_name: str
    demand_score: int
    projected: bool
    rare: bool


@dataclass(frozen=True)
class TradeSideSummary:
    items: list[EvaluatedItem]
    base_value: int
    effective_value: int
    weighted_demand: float
    demand_coverage: float
    biggest_item_value: int
    projected_count: int
    projected_value_share: float
    rare_count: int
    rare_value_share: float


@dataclass(frozen=True)
class TradeEvaluation:
    score: float
    recommendation: str
    trade_type: str
    giving: TradeSideSummary
    receiving: TradeSideSummary
    base_value_difference: int
    effective_value_difference: int
    demand_difference: float
    score_components: dict[str, float]
    reasons: list[str]

    def to_dict(self) -> dict:
        return asdict(self)

    def format_text(self) -> str:
        def side_lines(title: str, side: TradeSideSummary) -> list[str]:
            lines = [title]
            for item in side.items:
                assigned = (
                    f"{item.roli_value:,}"
                    if item.roli_value is not None
                    else "Not assigned"
                )
                lines.extend(
                    [
                        f"- {item.name} ({item.asset_id})",
                        f"  RAP: {item.rap:,} | Roli Value: {assigned}",
                        f"  Effective: {item.effective_value:,} ({item.effective_value_source})",
                        (
                            f"  Demand: {item.demand_name} | "
                            f"Projected: {item.projected} | Rare: {item.rare}"
                        ),
                    ]
                )
            lines.append(
                f"Totals -> base: {side.base_value:,} | effective: {side.effective_value:,}"
            )
            return lines

        lines: list[str] = []
        lines.extend(side_lines("GIVING", self.giving))
        lines.append("")
        lines.extend(side_lines("RECEIVING", self.receiving))
        lines.extend(
            [
                "",
                f"Trade type: {self.trade_type}",
                f"Base difference: {self.base_value_difference:+,}",
                f"Effective difference: {self.effective_value_difference:+,}",
                f"Demand difference: {self.demand_difference:+.2f}",
                f"Score: {self.score:.2f}/100",
                f"Recommendation: {self.recommendation.upper()}",
                "Reasons:",
            ]
        )
        lines.extend(f"- {reason}" for reason in self.reasons)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Proof models
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProofImage:
    data: bytes
    mime_type: str
    filename: str | None = None


@dataclass(frozen=True)
class RawProof:
    """Source-agnostic proof before the LLM parses it.

    A future Discord scraper, webhook, manual uploader, etc. can all create this
    same object, so the proof pipeline does not depend on one ingestion method.
    """

    source: str
    text: str | None = None
    images: tuple[ProofImage, ...] = ()
    image_urls: tuple[str, ...] = ()
    message_id: str | None = None
    channel_id: str | None = None
    author: str | None = None
    timestamp: datetime | None = None


class ProofItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    asset_id: int | None = Field(default=None, gt=0)
    rap: int | None = Field(default=None, ge=0)
    market_value: int | None = Field(default=None, ge=0)
    # Legacy parser field. It is intentionally not used for valuation because
    # old screenshots often caused RAP to be stored here as if it were value.
    stated_value: int | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()


class Proof(BaseModel):
    model_config = ConfigDict(extra="forbid")

    giving: list[ProofItem] = Field(default_factory=list)
    receiving: list[ProofItem] = Field(default_factory=list)
    giving_total: int | None = Field(default=None, ge=0)
    receiving_total: int | None = Field(default=None, ge=0)
    giving_rap_total: int | None = Field(default=None, ge=0)
    receiving_rap_total: int | None = Field(default=None, ge=0)
    overpay_amount: int | None = Field(default=None, ge=0)
    overpay_item: str | None = None
    deal_type: Literal["overpay", "underpay", "equal", "unknown"] = "unknown"
    deal_amount: int | None = Field(default=None, ge=0)
    deal_item: str | None = None
    sender: str | None = None
    receiver: str | None = None
    date: Date | None = None
    valid: bool

    # Python-owned fields: never trusted from the LLM.
    calculated_overpay_amount: int | None = Field(default=None, ge=0)
    validation_warnings: list[str] = Field(default_factory=list)

    @field_validator("overpay_item", "deal_item", "sender", "receiver")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None
