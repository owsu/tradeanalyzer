from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import ValidationError

from clients.gemini import GeminiClient
from models import Proof
from proofs.validator import validate_proof


class ProofParseError(RuntimeError):
    """Raised when the LLM returns output that cannot be parsed/validated."""


def build_proof_prompt(raw_message: str | None, *, image_count: int = 0) -> str:
    proof_text = (raw_message or "").strip() or "(no text caption supplied)"
    return f"""
Parse this Roblox trading proof into JSON. The proof may consist of a text
caption, {image_count} attached screenshot(s), or both. Read every visible item,
username, side, value, and total from the screenshots. Use the caption only as
supporting evidence. Extract information only; do not judge whether the trade is
good or bad.

Caption:
{proof_text}

Return ONLY a single JSON object with exactly these keys:
{{
  "giving": [{{"name": "Item A", "asset_id": 123, "rap": 12100, "market_value": 13100}}],
  "receiving": [{{"name": "Item B", "asset_id": null, "rap": 18400, "market_value": 19400}}],
  "giving_total": 13100,
  "receiving_total": 19400,
  "giving_rap_total": 12100,
  "receiving_rap_total": 18400,
  "deal_type": "overpay",
  "deal_amount": 6300,
  "deal_item": "Item A",
  "sender": "username or null",
  "receiver": "username or null",
  "date": "YYYY-MM-DD or null",
  "valid": true
}}

Rules:
- No markdown and no backticks.
- Monetary/value fields are integer Robux amounts. Convert 6.3k -> 6300.
- asset_id is the Roblox catalog asset ID only when explicitly visible; otherwise null.
- RAP and trading/assigned value are different. Put recent average price only in
  rap and put an explicitly displayed trading/value figure only in market_value.
- Never copy RAP into market_value. Use null when the screenshot does not show
  an item's trading value.
- giving_total and receiving_total are trading-value totals, never RAP totals.
  Put displayed or calculated RAP totals in giving_rap_total/receiving_rap_total.
- Keep each side of the trade separate.
- If a value total is not stated but every market_value is known, calculate it.
- If an individual metric is unknown, use null for that metric.
- Do not invent cropped, obscured, or unreadable items or numbers.
- An explicitly labeled OP/overpay can be correct even when only RAP is visible.
  Preserve it as deal_type=overpay and deal_amount; do not derive it from RAP.
- Use deal_type=underpay for an explicitly stated underpay, equal for an
  explicitly equal-value deal, and unknown when the direction is unclear.
- deal_item is the item the proof says the overpay/underpay is "on". It must
  exactly match one extracted item name or be null.
- If screenshots and caption conflict, prefer clearly visible screenshot data.
  RAP differing from an explicitly stated overpay is not a conflict.
- If the message is empty, spam, unrelated, or too ambiguous to parse, set valid=false.
""".strip()


def parse_proof(
    raw_message: str | None,
    *,
    images: Sequence[tuple[bytes, str]] = (),
    client: GeminiClient | None = None,
) -> Proof:
    if not (raw_message or "").strip() and not images:
        return Proof(valid=False)

    client = client or GeminiClient()

    try:
        payload = client.generate_json(
            build_proof_prompt(raw_message, image_count=len(images)),
            images=images,
        )
        # These fields belong to Python, not the LLM.
        payload.pop("calculated_overpay_amount", None)
        payload.pop("validation_warnings", None)
        proof = Proof.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise ProofParseError(f"LLM returned invalid proof data: {exc}") from exc

    return validate_proof(proof)
