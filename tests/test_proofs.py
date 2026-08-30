import asyncio

from models import Proof, ProofImage, RawProof
from database import Database
from proofs.ingestion import ProofIngestionService
from proofs.parser import parse_proof
from proofs.validator import validate_proof


def test_validator_recalculates_bad_llm_math():
    proof = Proof.model_validate(
        {
            "giving": [{"name": "A", "market_value": 13100}],
            "receiving": [{"name": "B", "market_value": 19400}],
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


def test_validator_never_calculates_overpay_from_rap():
    proof = Proof.model_validate(
        {
            "giving": [{"name": "A", "rap": 361398}],
            "receiving": [{"name": "B", "rap": 311729}],
            "overpay_amount": 28000,
            "overpay_item": "B",
            "valid": True,
        }
    )

    checked = validate_proof(proof)

    assert checked.valid is True
    assert checked.overpay_amount == 28000
    assert checked.calculated_overpay_amount is None
    assert checked.giving_total is None
    assert checked.receiving_total is None
    assert "cannot be independently verified" in checked.validation_warnings[0]


class FakeGemini:
    def __init__(self):
        self.prompt = None
        self.images = None

    def generate_json(self, prompt, *, images=()):
        self.prompt = prompt
        self.images = images
        return {
            "giving": [{"name": "A", "market_value": 1000}],
            "receiving": [{"name": "B", "market_value": 1200}],
            "giving_total": 1000,
            "receiving_total": 1200,
            "overpay_amount": 200,
            "overpay_item": "A",
            "sender": None,
            "receiver": None,
            "date": None,
            "valid": True,
        }


def test_image_only_proof_is_sent_to_multimodal_client():
    client = FakeGemini()
    proof = parse_proof(
        None,
        images=[(b"fake png bytes", "image/png")],
        client=client,
    )

    assert proof.valid is True
    assert client.images == [(b"fake png bytes", "image/png")]
    assert "1 attached screenshot" in client.prompt


def test_ingestion_forwards_image_bytes_without_text():
    received = {}

    def parser(text, *, images):
        received["text"] = text
        received["images"] = images
        return Proof(valid=False)

    raw = RawProof(
        source="test",
        images=(ProofImage(b"pixels", "image/webp", "proof.webp"),),
    )
    result = asyncio.run(ProofIngestionService(parser).process(raw))

    assert result.valid is False
    assert received == {
        "text": None,
        "images": ((b"pixels", "image/webp"),),
    }


def parsed_proof():
    return Proof.model_validate(
        {
            "giving": [{"name": "A", "market_value": 1000}],
            "receiving": [{"name": "B", "market_value": 1200}],
            "giving_total": 1000,
            "receiving_total": 1200,
            "overpay_amount": 200,
            "overpay_item": "A",
            "valid": True,
        }
    )


def persistent_raw(message_id, text="proof"):
    return RawProof(
        source="discord_bot",
        channel_id="channel-1",
        message_id=str(message_id),
        text=text,
        author="tester",
    )


def test_same_message_id_and_content_is_parsed_once(tmp_path):
    calls = []

    def parser(text, *, images):
        calls.append(text)
        return parsed_proof()

    service = ProofIngestionService(
        parser, database=Database(tmp_path / "proofs.db")
    )
    raw = persistent_raw(1)

    first = asyncio.run(service.process(raw))
    second = asyncio.run(service.process(raw))

    assert first is not None
    assert second is None
    assert calls == ["proof"]


def test_same_content_new_message_reuses_parse_but_keeps_occurrence(tmp_path):
    calls = []

    def parser(text, *, images):
        calls.append(text)
        return parsed_proof()

    database = Database(tmp_path / "proofs.db")
    service = ProofIngestionService(parser, database=database)

    asyncio.run(service.process(persistent_raw(1)))
    reused = asyncio.run(service.process(persistent_raw(2)))

    assert reused is not None
    assert reused.valid is True
    assert calls == ["proof"]
    history = database.proof_message_history()
    assert {row["message_id"] for row in history} == {"1", "2"}
    assert all(row["status"] == "succeeded" for row in history)


def test_edited_message_id_with_new_content_is_reparsed(tmp_path):
    calls = []

    def parser(text, *, images):
        calls.append(text)
        return parsed_proof()

    service = ProofIngestionService(
        parser, database=Database(tmp_path / "proofs.db")
    )

    asyncio.run(service.process(persistent_raw(1, "original")))
    asyncio.run(service.process(persistent_raw(1, "edited")))

    assert calls == ["original", "edited"]
