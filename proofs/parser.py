from __future__ import annotations

import json

from pydantic import ValidationError

from clients.gemini import GeminiClient
from models import Proof
from proofs.validator import validate_proof


class ProofParseError(RuntimeError):
    """Raised when the LLM returns output that cannot be parsed/validated."""


def build_proof_prompt(raw_message: str) -> str:
    return f"""
Parse this Roblox trading proof into JSON. Extract information only; do not judge
whether the trade is good or bad.

Proof:
{raw_message}

Return ONLY a single JSON object with exactly these keys:
{{
  "giving": [{{"name": "Item A", "stated_value": 13100}}],
  "receiving": [{{"name": "Item B", "stated_value": 19400}}],
  "giving_total": 13100,
  "receiving_total": 19400,
  "overpay_amount": 6300,
  "overpay_item": "Item A",
  "sender": "username or null",
  "receiver": "username or null",
  "date": "YYYY-MM-DD or null",
  "valid": true
}}

Rules:
- No markdown and no backticks.
- Monetary/value fields are integer Robux amounts. Convert 6.3k -> 6300.
- Keep each side of the trade separate.
- If a total is not explicitly stated but all item values are stated, calculate it.
- If an individual item's value is unknown, use null for stated_value.
- overpay_item is the item the proof text says the OP is "on"; use null if unclear.
- If the message is empty, spam, unrelated, or too ambiguous to parse, set valid=false.
""".strip()


def parse_proof(raw_message: str, *, client: GeminiClient | None = None) -> Proof:
    if not raw_message or not raw_message.strip():
        return Proof(valid=False)

    client = client or GeminiClient()

    try:
        payload = client.generate_json(build_proof_prompt(raw_message))
        # These fields belong to Python, not the LLM.
        payload.pop("calculated_overpay_amount", None)
        payload.pop("validation_warnings", None)
        proof = Proof.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise ProofParseError(f"LLM returned invalid proof data: {exc}") from exc

    return validate_proof(proof)
