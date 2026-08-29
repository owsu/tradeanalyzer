from models import Proof
from proofs.validator import validate_proof


def test_validator_recalculates_bad_llm_math():
    proof = Proof.model_validate(
        {
            "giving": [{"name": "A", "stated_value": 13100}],
            "receiving": [{"name": "B", "stated_value": 19400}],
            "giving_total": 13000,
            "receiving_total": 19400,
            "overpay_amount": 6000,
            "overpay_item": "A",
            "sender": None,
            "receiver": None,
            "date": None,
            "valid": True,
        }
    )

    checked = validate_proof(proof)

    assert checked.giving_total == 13100
    assert checked.receiving_total == 19400
    assert checked.calculated_overpay_amount == 6300
    assert len(checked.validation_warnings) == 2
