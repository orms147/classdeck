"""Provider for any Chat-Completions-compatible endpoint, via the ``openai`` SDK.

Retries and the structured-output fallback are handled here rather than in the SDK
(``max_retries=0``) so that behaviour is identical for every endpoint and testable offline.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

import openai

from classdeck.llm.base import LLMError, LLMResponse, Message, Usage

logger = logging.getLogger("classdeck.llm")

_BACKOFF_BASE_S = 1.0


class OpenAICompatibleProvider:
    def __init__(
        self,
        name: str,
        *,
        api_key: str,
        base_url: str,
        model: str,
        client: Any | None = None,
        max_retries: int = 2,
        timeout: float = 120.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = name
        self.model = model
        self._max_retries = max_retries
        self._sleep = sleep
        self._client = client or openai.OpenAI(
            api_key=api_key, base_url=base_url, max_retries=0, timeout=timeout
        )

    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [message.to_openai() for message in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": json_schema, "strict": False},
            }

        retries = 0
        while True:
            started = time.perf_counter()
            try:
                completion = self._client.chat.completions.create(**kwargs)
            except openai.OpenAIError as exc:
                if isinstance(exc, openai.BadRequestError) and "response_format" in kwargs:
                    logger.warning(
                        "%s rejected response_format (%s); retrying without it", self.name, exc
                    )
                    del kwargs["response_format"]
                    continue
                if _is_retryable(exc) and retries < self._max_retries:
                    delay = _BACKOFF_BASE_S * 2**retries
                    retries += 1
                    logger.warning(
                        "%s call failed (%s); retry %d/%d in %.1fs",
                        self.name,
                        exc,
                        retries,
                        self._max_retries,
                        delay,
                    )
                    self._sleep(delay)
                    continue
                raise LLMError(
                    str(exc), status=getattr(exc, "status_code", None), body=_error_body(exc)
                ) from exc
            latency = time.perf_counter() - started
            break

        choices = getattr(completion, "choices", None) or []
        text = (choices[0].message.content if choices else None) or ""
        raw_usage = getattr(completion, "usage", None)
        usage = Usage(
            input_tokens=getattr(raw_usage, "prompt_tokens", None),
            output_tokens=getattr(raw_usage, "completion_tokens", None),
        )
        model = getattr(completion, "model", None) or self.model
        logger.info(
            "llm call provider=%s model=%s in=%s out=%s latency=%.2fs",
            self.name,
            model,
            usage.input_tokens,
            usage.output_tokens,
            latency,
        )
        return LLMResponse(text=text, model=model, usage=usage, latency_s=latency)


def _is_retryable(exc: openai.OpenAIError) -> bool:
    if isinstance(exc, openai.RateLimitError | openai.APIConnectionError):
        return True
    return isinstance(exc, openai.APIStatusError) and exc.status_code >= 500


def _error_body(exc: openai.OpenAIError) -> str | None:
    response = getattr(exc, "response", None)
    if response is not None:
        with contextlib.suppress(Exception):  # a streamed body may be unreadable
            return str(response.text)
    body = getattr(exc, "body", None)
    return None if body is None else str(body)
