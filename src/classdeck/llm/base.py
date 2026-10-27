"""Provider-neutral types for model calls: messages, responses, usage and the provider protocol.

The rest of the package depends on this module only, never on an SDK. See
``docs/llm-provider.md``.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict


class TextPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = "text"
    text: str

    def to_openai(self) -> dict[str, Any]:
        return {"type": "text", "text": self.text}


class ImagePart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["image_url"] = "image_url"
    url: str
    detail: str | None = None

    def to_openai(self) -> dict[str, Any]:
        image: dict[str, Any] = {"url": self.url}
        if self.detail is not None:
            image["detail"] = self.detail
        return {"type": "image_url", "image_url": image}


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str | list[TextPart | ImagePart]

    def to_openai(self) -> dict[str, Any]:
        if isinstance(self.content, str):
            return {"role": self.role, "content": self.content}
        return {"role": self.role, "content": [part.to_openai() for part in self.content]}


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    model: str
    usage: Usage
    cached: bool = False
    latency_s: float | None = None


class LLMError(Exception):
    """A model or network failure. ``status`` and ``body`` carry the HTTP details when known."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse: ...


class UsageMeter:
    """Sums token usage over a run. Cached responses count as calls but add no tokens."""

    def __init__(self) -> None:
        self._calls = 0
        self._cached_calls = 0
        self._input_tokens = 0
        self._output_tokens = 0

    def add(self, response: LLMResponse) -> None:
        self._calls += 1
        if response.cached:
            self._cached_calls += 1
            return
        self._input_tokens += response.usage.input_tokens or 0
        self._output_tokens += response.usage.output_tokens or 0

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def cached_calls(self) -> int:
        return self._cached_calls

    @property
    def input_tokens(self) -> int:
        return self._input_tokens

    @property
    def output_tokens(self) -> int:
        return self._output_tokens

    def summary(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "cached_calls": self.cached_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }
