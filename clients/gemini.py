from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import Any

from config import GEMINI_MODEL


class GeminiClient:
    """Small wrapper around Gemini so proof parsing is provider-independent."""

    def __init__(self, *, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model or GEMINI_MODEL
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is missing from your environment/.env file")

        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError(
                "google-genai is not installed. Run: pip install google-genai"
            ) from exc

        self._client = genai.Client(api_key=self.api_key)

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        if cleaned.lstrip().startswith("json\n"):
            cleaned = cleaned.lstrip()[5:]
        return cleaned.strip()

    def generate_text(self, prompt: str) -> str:
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        return response.text or ""

    def generate_json(
        self,
        prompt: str,
        *,
        images: Sequence[tuple[bytes, str]] = (),
    ) -> dict[str, Any]:
        from google.genai import types

        contents: list[Any] = [
            types.Part.from_bytes(data=data, mime_type=mime_type)
            for data, mime_type in images
        ]
        contents.append(prompt)
        response = self._client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        text = self._strip_code_fences(response.text or "")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise TypeError("Gemini returned JSON, but it was not an object")
        return payload

    def list_model_names(self, contains: str = "") -> list[str]:
        needle = contains.lower().strip()
        names: list[str] = []
        for model in self._client.models.list():
            name = model.name
            if needle and needle not in name.lower():
                continue
            names.append(name)
        return names
