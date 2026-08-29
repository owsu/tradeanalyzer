from __future__ import annotations

from models import Proof


def validate_proof(proof: Proof) -> Proof:
    """Verify LLM-extracted proof arithmetic using normal Python."""
    warnings = list(proof.validation_warnings)

    if not proof.valid:
        return proof.model_copy(
            update={"calculated_overpay_amount": None, "validation_warnings": warnings}
        )

    if not proof.giving or not proof.receiving:
        warnings.append("Valid proof is missing one or both trade sides")
        return proof.model_copy(
            update={
                "valid": False,
                "calculated_overpay_amount": None,
                "validation_warnings": warnings,
            }
        )

    giving_item_values = [item.stated_value for item in proof.giving]
    receiving_item_values = [item.stated_value for item in proof.receiving]

    calculated_giving_total: int | None = None
    calculated_receiving_total: int | None = None

    if giving_item_values and all(value is not None for value in giving_item_values):
        calculated_giving_total = sum(
            value for value in giving_item_values if value is not None
        )
    if receiving_item_values and all(value is not None for value in receiving_item_values):
        calculated_receiving_total = sum(
            value for value in receiving_item_values if value is not None
        )

    giving_total = (
        calculated_giving_total
        if calculated_giving_total is not None
        else proof.giving_total
    )
    receiving_total = (
        calculated_receiving_total
        if calculated_receiving_total is not None
        else proof.receiving_total
    )

    if calculated_giving_total is not None and proof.giving_total not in (
        None,
        calculated_giving_total,
    ):
        warnings.append(
            f"LLM giving_total={proof.giving_total} but item values sum to "
            f"{calculated_giving_total}"
        )
    if calculated_receiving_total is not None and proof.receiving_total not in (
        None,
        calculated_receiving_total,
    ):
        warnings.append(
            f"LLM receiving_total={proof.receiving_total} but item values sum to "
            f"{calculated_receiving_total}"
        )

    calculated_overpay: int | None = None
    if giving_total is not None and receiving_total is not None:
        calculated_overpay = abs(receiving_total - giving_total)
        if proof.overpay_amount not in (None, calculated_overpay):
            warnings.append(
                f"LLM overpay_amount={proof.overpay_amount} but Python calculates "
                f"{calculated_overpay}"
            )

    return proof.model_copy(
        update={
            "giving_total": giving_total,
            "receiving_total": receiving_total,
            "calculated_overpay_amount": calculated_overpay,
            "validation_warnings": warnings,
        }
    )
