from __future__ import annotations

import asyncio
from collections.abc import Callable

from models import Proof, RawProof
from proofs.parser import parse_proof


class ProofIngestionService:
    """Source-agnostic entry point for raw proofs.

    Discord is only one possible producer. A future webhook, manual importer, or
    authorized scraper can produce RawProof objects and use this exact service.
    """

    def __init__(self, parser: Callable[[str], Proof] = parse_proof) -> None:
        self.parser = parser

    async def process(self, raw: RawProof) -> Proof:
        text = (raw.text or "").strip()
        if not text:
            return Proof(
                valid=False,
                validation_warnings=[
                    "No text was supplied to the text proof parser. "
                    "Image parsing can be added as a separate parser later."
                ],
            )

        # Gemini's SDK call is synchronous, so run it off Discord's event loop.
        return await asyncio.to_thread(self.parser, text)
