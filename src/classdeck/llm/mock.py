"""Offline provider for tests and ``CLASSDECK_PROVIDER=mock``."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from typing import Any

from classdeck.llm.base import LLMError, LLMResponse, Message, TextPart, Usage

DEFAULT_REPLY = "mock response"


class MockProvider:
    """Replies from a queue of canned texts or from ``handler(messages) -> str``.

    With neither, every call returns ``DEFAULT_REPLY``. Every call is recorded in ``calls``.
    """

    name = "mock"

    def __init__(
        self,
        responses: Sequence[str] | None = None,
        *,
        handler: Callable[[list[Message]], str] | None = None,
        model: str = "mock",
    ) -> None:
        if responses is not None and handler is not None:
            raise ValueError("Pass either responses or handler, not both")
        self.model = model
        self.calls: list[dict[str, Any]] = []
        self._queue = deque(responses) if responses is not None else None
        self._handler = handler

    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": [message.to_openai() for message in messages],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "json_schema": json_schema,
            }
        )
        if self._handler is not None:
            text = self._handler(messages)
        elif self._queue is not None:
            if not self._queue:
                raise LLMError(
                    f"MockProvider has no canned responses left (call {len(self.calls)})"
                )
            text = self._queue.popleft()
        else:
            text = DEFAULT_REPLY
        usage = Usage(
            input_tokens=sum(_length(message) for message in messages) // 4,
            output_tokens=len(text) // 4,
        )
        return LLMResponse(text=text, model=self.model, usage=usage, latency_s=0.0)


def _length(message: Message) -> int:
    if isinstance(message.content, str):
        return len(message.content)
    return sum(len(part.text) for part in message.content if isinstance(part, TextPart))
