"""Builds the configured provider for the ``generator`` and ``judge`` roles."""

from __future__ import annotations

import logging
from typing import Literal

from classdeck.config import Settings
from classdeck.llm.base import LLMError, LLMProvider
from classdeck.llm.cache import CachingProvider
from classdeck.llm.mock import MockProvider
from classdeck.llm.openai_compat import OpenAICompatibleProvider

Role = Literal["generator", "judge"]


def build_provider(
    settings: Settings, role: Role = "generator", *, use_cache: bool = True
) -> LLMProvider:
    if role == "judge" and settings.judge_configured:
        provider = _build_judge(settings)
    else:
        if role == "judge":
            logging.warning(
                "JUDGE_API_KEY, JUDGE_API_BASE_URL and JUDGE_MODEL are not all set; "
                "the judge falls back to the generator model"
            )
        provider = _build_generator(settings)
    if settings.cache_dir is not None and use_cache:
        return CachingProvider(provider, settings.cache_dir / role)
    return provider


def _build_generator(settings: Settings) -> LLMProvider:
    if settings.provider == "mock":
        return MockProvider()
    api_key = settings.model_api_key.get_secret_value() if settings.model_api_key else ""
    if not api_key:
        raise LLMError("MODEL_API_KEY is not set")
    return OpenAICompatibleProvider(
        name=f"api:{settings.model_api_host}",
        base_url=settings.model_api_base_url,
        api_key=api_key,
        model=settings.model,
    )


def _build_judge(settings: Settings) -> LLMProvider:
    assert settings.judge_api_key and settings.judge_api_base_url and settings.judge_model
    return OpenAICompatibleProvider(
        name="judge",
        base_url=settings.judge_api_base_url,
        api_key=settings.judge_api_key.get_secret_value(),
        model=settings.judge_model,
    )
