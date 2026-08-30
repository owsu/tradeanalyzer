from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial

from config import (
    INVENTORY_REQUEST_TIMEOUT,
    PROOF_MAX_ATTEMPTS,
    PROOF_PROCESSING_TIMEOUT_SECONDS,
    PROOF_TRADE_LINK_WINDOW_DAYS,
)
from database import Database
from models import Proof, RawProof
from proofs.deduplication import proof_content_hash
from proofs.parser import parse_proof
from market.inventory_client import InventoryError, RobloxInventoryClient


class ProofIngestionService:
    """Source-agnostic entry point for raw proofs.

    Discord is only one possible producer. A future webhook, manual importer, or
    authorized scraper can produce RawProof objects and use this exact service.
    """

    def __init__(
        self,
        parser: Callable[..., Proof] = parse_proof,
        *,
        database: Database | None = None,
    ) -> None:
        self.parser = parser
        self.database = database

    async def process(self, raw: RawProof) -> Proof | None:
        text = (raw.text or "").strip()
        images = tuple((image.data, image.mime_type) for image in raw.images)
        if not text and not images:
            return Proof(
                valid=False,
                validation_warnings=[
                    "No text or supported image was supplied to the proof parser."
                ],
            )

        if self.database is not None:
            disposition, stored = await asyncio.to_thread(
                self.database.claim_proof_message,
                raw,
                proof_content_hash(raw),
                max_attempts=PROOF_MAX_ATTEMPTS,
                processing_timeout_seconds=PROOF_PROCESSING_TIMEOUT_SECONDS,
            )
            if disposition == "skip":
                return None
            if disposition == "reused":
                resolved = await asyncio.to_thread(
                    self.database.complete_proof_message, raw, stored
                )
                await asyncio.to_thread(self._watch_proof_participants, resolved)
                await asyncio.to_thread(
                    self.database.reconcile_proofs_with_inferred_trades,
                    PROOF_TRADE_LINK_WINDOW_DAYS,
                )
                return Proof.model_validate(resolved)

        try:
            # Gemini's SDK call is synchronous, so run it off Discord's event loop.
            proof = await asyncio.to_thread(
                partial(self.parser, text or None, images=images)
            )
        except Exception as exc:
            if self.database is not None:
                await asyncio.to_thread(
                    self.database.fail_proof_message,
                    raw,
                    f"{type(exc).__name__}: {exc}",
                )
            raise

        if self.database is not None:
            resolved = await asyncio.to_thread(
                self.database.complete_proof_message,
                raw,
                proof.model_dump(mode="json"),
            )
            proof = Proof.model_validate(resolved)
            await asyncio.to_thread(self._watch_proof_participants, resolved)
            await asyncio.to_thread(
                self.database.reconcile_proofs_with_inferred_trades,
                PROOF_TRADE_LINK_WINDOW_DAYS,
            )
        return proof

    def _watch_proof_participants(self, proof: dict) -> None:
        if self.database is None or not proof.get("valid"):
            return
        names = [str(proof.get(key) or "").strip() for key in ("sender", "receiver")]
        names = [name for name in names if name]
        if not names:
            return
        try:
            resolved = RobloxInventoryClient(
                timeout=INVENTORY_REQUEST_TIMEOUT
            ).resolve_usernames(names)
        except InventoryError:
            # Proof processing remains durable even when Roblox is unavailable;
            # a future resubmission/backfill can resolve the participant.
            return
        for name in names:
            user_id = resolved.get(name.casefold())
            if user_id:
                self.database.add_watched_user(user_id, source="proof")
