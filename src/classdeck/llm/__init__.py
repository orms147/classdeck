"""Model access: one provider interface, an OpenAI-compatible implementation, a mock, a cache."""

from __future__ import annotations

from classdeck.llm.base import (
    ImagePart,
    LLMError,
    LLMProvider,
    LLMResponse,
    Message,
    TextPart,
    Usage,
    UsageMeter,
)
from classdeck.llm.cache import CachingProvider
from classdeck.llm.factory import build_provider
from classdeck.llm.mock import MockProvider
from classdeck.llm.openai_compat import OpenAICompatibleProvider
from classdeck.llm.pricing import estimate_cost_usd
from classdeck.llm.prompts import load_prompt
from classdeck.llm.structured import StructuredOutputError, complete_json, extract_json

__all__ = [
    "CachingProvider",
    "ImagePart",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "MockProvider",
    "OpenAICompatibleProvider",
    "StructuredOutputError",
    "TextPart",
    "Usage",
    "UsageMeter",
    "build_provider",
    "complete_json",
    "estimate_cost_usd",
    "extract_json",
    "load_prompt",
]
