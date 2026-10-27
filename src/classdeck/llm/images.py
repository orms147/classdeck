"""Image generation: one provider interface, an OpenAI-compatible implementation, a mock.

Used by ``classdeck illustrate`` for flashcard pictures. The OpenAI-compatible provider calls
``images.generate`` and decodes the base64 PNG; the mock draws a flat placeholder card offline.
See ``docs/output-formats.md``.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any, Protocol

import openai
import pymupdf
from pydantic import BaseModel, ConfigDict

from classdeck.config import Settings
from classdeck.llm.base import LLMError, Usage
from classdeck.llm.openai_compat import _error_body


class ImageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    png: bytes
    usage: Usage | None = None
    model: str


class ImageProvider(Protocol):
    name: str
    model: str

    def generate(self, prompt: str, *, output_format: str = "png") -> ImageResult: ...


class OpenAICompatibleImageProvider:
    def __init__(
        self,
        name: str,
        *,
        api_key: str,
        base_url: str,
        model: str,
        client: Any | None = None,
        timeout: float = 180.0,
    ) -> None:
        self.name = name
        self.model = model
        self._client = client or openai.OpenAI(
            api_key=api_key, base_url=base_url, max_retries=0, timeout=timeout
        )

    def generate(self, prompt: str, *, output_format: str = "png") -> ImageResult:
        try:
            response = self._client.images.generate(
                model=self.model, prompt=prompt, n=1, output_format=output_format
            )
        except openai.OpenAIError as exc:
            raise LLMError(
                str(exc), status=getattr(exc, "status_code", None), body=_error_body(exc)
            ) from exc
        data = getattr(response, "data", None) or []
        encoded = getattr(data[0], "b64_json", None) if data else None
        if not encoded:
            raise LLMError(f"{self.name} returned no image data (b64_json missing)")
        try:
            png = base64.b64decode(encoded, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise LLMError(f"{self.name} returned undecodable image data") from exc
        raw_usage = getattr(response, "usage", None)
        usage = None
        if raw_usage is not None:
            usage = Usage(
                input_tokens=_int(getattr(raw_usage, "input_tokens", None)),
                output_tokens=_int(getattr(raw_usage, "output_tokens", None)),
            )
        return ImageResult(png=png, usage=usage, model=self.model)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) else None


_PALETTE = [
    (1.0, 0.93, 0.70),
    (0.76, 0.87, 1.0),
    (0.77, 0.94, 0.78),
    (1.0, 0.80, 0.80),
    (0.87, 0.80, 0.96),
]


class MockImageProvider:
    """Deterministic placeholder pictures: a coloured card with the word on it."""

    def __init__(self, model: str = "mock-image") -> None:
        self.name = "mock-image"
        self.model = model
        self.calls: list[str] = []

    def generate(self, prompt: str, *, output_format: str = "png") -> ImageResult:
        self.calls.append(prompt)
        match = re.search(r"^word:\s*(\S+)", prompt, re.IGNORECASE | re.MULTILINE)
        word = match.group(1) if match else (prompt.split() or ["?"])[0]
        color = _PALETTE[sum(map(ord, word)) % len(_PALETTE)]
        size = 512
        doc = pymupdf.open()
        page = doc.new_page(width=size, height=size)
        page.draw_rect(page.rect, color=None, fill=color)
        page.insert_textbox(
            pymupdf.Rect(16, size / 2 - 40, size - 16, size / 2 + 40),
            word,
            fontsize=56,
            align=pymupdf.TEXT_ALIGN_CENTER,
            color=(0.12, 0.16, 0.22),
        )
        png = page.get_pixmap(dpi=72).tobytes("png")
        doc.close()
        return ImageResult(png=png, usage=None, model=self.model)


def build_image_provider(settings: Settings) -> ImageProvider:
    if settings.provider == "mock":
        return MockImageProvider()
    api_key = settings.model_api_key.get_secret_value() if settings.model_api_key else ""
    if not api_key:
        raise LLMError("MODEL_API_KEY is not set")
    return OpenAICompatibleImageProvider(
        name=f"api:{settings.model_api_host}",
        api_key=api_key,
        base_url=settings.model_api_base_url,
        model=settings.image_model,
    )
