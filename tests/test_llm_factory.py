from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from classdeck.config import Settings
from classdeck.llm import (
    CachingProvider,
    LLMError,
    Message,
    MockProvider,
    OpenAICompatibleProvider,
    build_provider,
    load_prompt,
)

ENV_VARS = [
    "MODEL_API_KEY",
    "MODEL_API_BASE_URL",
    "CLASSDECK_MODEL",
    "CLASSDECK_PROVIDER",
    "CLASSDECK_CACHE_DIR",
    "CLASSDECK_WORKDIR",
    "CLASSDECK_CURRICULUM_DIR",
    "JUDGE_API_KEY",
    "JUDGE_API_BASE_URL",
    "JUDGE_MODEL",
]

JUDGE = {
    "judge_api_key": "judge-key",
    "judge_api_base_url": "https://judge.example.test/v1",
    "judge_model": "judge-model",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _settings(**values: Any) -> Settings:
    values.setdefault("cache_dir", None)
    return Settings(_env_file=None, **values)


def test_mock_generator() -> None:
    provider = build_provider(_settings(provider="mock"))
    assert isinstance(provider, MockProvider)
    assert provider.complete([Message(role="user", content="hi")]).text


def test_api_generator_uses_the_configured_endpoint() -> None:
    settings = _settings(model_api_key="secret-key", model="muse-test")
    provider = build_provider(settings)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert (provider.name, provider.model) == ("api:openrouter.ai", "muse-test")


def test_api_generator_is_named_after_the_endpoint_host() -> None:
    settings = _settings(
        model_api_key="k", model_api_base_url="https://api.example.test/v1", model="m"
    )
    assert build_provider(settings).name == "api:api.example.test"


def test_legacy_provider_name_is_accepted() -> None:
    assert _settings(provider="meta").provider == "api"
    assert _settings(provider="API").provider == "api"


def test_api_without_a_key_raises() -> None:
    with pytest.raises(LLMError, match="MODEL_API_KEY is not set"):
        build_provider(_settings())
    with pytest.raises(LLMError, match="MODEL_API_KEY is not set"):
        build_provider(_settings(model_api_key=""))


def test_judge_falls_back_to_the_generator_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        mock_judge = build_provider(_settings(provider="mock"), "judge")
        api_judge = build_provider(_settings(model_api_key="k"), "judge")
    assert mock_judge.name == "mock"
    assert api_judge.name == "api:openrouter.ai"
    assert sum("judge" in r.getMessage().lower() for r in caplog.records) == 2


def test_partially_configured_judge_falls_back() -> None:
    settings = _settings(provider="mock", judge_api_key="k", judge_model="m")
    assert build_provider(settings, "judge").name == "mock"


def test_configured_judge_is_independent_of_the_generator_key() -> None:
    provider = build_provider(_settings(**JUDGE), "judge")
    assert isinstance(provider, OpenAICompatibleProvider)
    assert (provider.name, provider.model) == ("judge", "judge-model")


def test_generator_ignores_judge_settings() -> None:
    provider = build_provider(_settings(provider="mock", **JUDGE))
    assert provider.name == "mock"


def test_cache_wraps_each_role_in_its_own_subdirectory(tmp_path: Path) -> None:
    settings = _settings(provider="mock", cache_dir=tmp_path, **JUDGE)
    generator = build_provider(settings)
    judge = build_provider(settings, "judge")
    assert isinstance(generator, CachingProvider)
    assert isinstance(judge, CachingProvider)
    assert generator.cache_dir == tmp_path / "generator"
    assert judge.cache_dir == tmp_path / "judge"
    assert isinstance(generator.inner, MockProvider)
    assert judge.name == "judge"


def test_fallback_judge_is_cached_under_the_judge_directory(tmp_path: Path) -> None:
    provider = build_provider(_settings(provider="mock", cache_dir=tmp_path), "judge")
    assert isinstance(provider, CachingProvider)
    assert provider.cache_dir == tmp_path / "judge"


def test_cache_can_be_bypassed_or_disabled(tmp_path: Path) -> None:
    with_dir = _settings(provider="mock", cache_dir=tmp_path)
    assert isinstance(build_provider(with_dir, use_cache=False), MockProvider)
    assert isinstance(build_provider(_settings(provider="mock", cache_dir=None)), MockProvider)


def test_load_prompt_substitutes_variables() -> None:
    assert load_prompt("_example", name="World").strip() == "Hello World"


def test_load_prompt_leaves_substituted_text_alone() -> None:
    assert load_prompt("_example", name="{{other}}").strip() == "Hello {{other}}"


def test_load_prompt_names_unfilled_placeholders() -> None:
    with pytest.raises(KeyError, match="name"):
        load_prompt("_example")


def test_load_prompt_unknown_name() -> None:
    with pytest.raises(FileNotFoundError, match="no_such_prompt"):
        load_prompt("no_such_prompt")
