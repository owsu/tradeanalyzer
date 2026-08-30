from __future__ import annotations

import hashlib

from config import PROOF_PARSER_VERSION
from models import RawProof


def proof_content_hash(raw: RawProof) -> str:
    """Hash normalized proof inputs, never temporary attachment URLs."""
    digest = hashlib.sha256()
    digest.update(f"parser:{PROOF_PARSER_VERSION}\n".encode())
    digest.update((raw.text or "").strip().encode("utf-8"))

    for image in raw.images:
        digest.update(b"\0image\0")
        digest.update(image.mime_type.lower().encode("ascii"))
        digest.update(hashlib.sha256(image.data).digest())

    return digest.hexdigest()
