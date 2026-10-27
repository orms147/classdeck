"""On-disk response cache keyed by the full request."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from classdeck.llm.base import LLMProvider, LLMResponse, Message

logger = logging.getLogger("classdeck.llm")


def cache_key(
    name: str,
    model: str,
    messages: list[Message],
    temperature: float,
    max_tokens: int | None,
    json_schema: dict[str, Any] | None,
) -> str:
    payload = {
        "name": name,
        "model": model,
        "messages": [message.to_openai() for message in messages],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "json_schema": json_schema,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CachingProvider:
    """Wraps a provider; one JSON file per request under ``cache_dir``."""

    def __init__(self, inner: LLMProvider, cache_dir: Path) -> None:
        self.inner = inner
        self.cache_dir = cache_dir
        self.name = inner.name
        self.model = inner.model

    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        key = cache_key(self.name, self.model, messages, temperature, max_tokens, json_schema)
        path = self.cache_dir / f"{key}.json"

        hit = self._read(path)
        if hit is not None:
            return hit.model_copy(update={"cached": True, "latency_s": 0.0})

        response = self.inner.complete(
            messages, temperature=temperature, max_tokens=max_tokens, json_schema=json_schema
        )
        self._write(path, response)
        return response

    @staticmethod
    def _read(path: Path) -> LLMResponse | None:
        try:
            return LLMResponse.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            logger.warning("Ignoring unreadable cache file %s", path)
            return None

    def _write(self, path: Path, response: LLMResponse) -> None:
        stored = response.model_copy(update={"cached": False})
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(stored.model_dump(mode="json")), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            logger.warning("Could not write cache file %s", path, exc_info=True)
            tmp.unlink(missing_ok=True)
