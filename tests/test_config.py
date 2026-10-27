from __future__ import annotations

from pathlib import Path

import pytest

from classdeck.config import Settings

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


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.model == "muse-spark-1.3"
    assert settings.provider == "api"
    assert settings.model_api_base_url == "https://api.meta.ai/v1"
    assert settings.model_api_host == "api.meta.ai"
    assert settings.cache_dir == Path(".cache/llm")
    assert settings.model_api_key is None
    assert settings.judge_configured is False


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSDECK_MODEL", "muse-spark-1.2")
    monkeypatch.setenv("CLASSDECK_PROVIDER", "mock")
    monkeypatch.setenv("CLASSDECK_CACHE_DIR", "")
    settings = Settings(_env_file=None)
    assert settings.model == "muse-spark-1.2"
    assert settings.provider == "mock"
    assert settings.cache_dir is None


def test_judge_configured_requires_all_three(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDGE_API_KEY", "k")
    monkeypatch.setenv("JUDGE_API_BASE_URL", "https://example.test/v1")
    assert Settings(_env_file=None).judge_configured is False
    monkeypatch.setenv("JUDGE_MODEL", "some-model")
    assert Settings(_env_file=None).judge_configured is True


def test_redacted_masks_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "abcd1234WXYZ")
    monkeypatch.setenv("JUDGE_API_KEY", "short")
    shown = Settings(_env_file=None).redacted()
    assert shown["MODEL_API_KEY"] == "abcd…WXYZ"
    assert "1234" not in shown["MODEL_API_KEY"]
    assert shown["JUDGE_API_KEY"] == "(set)"
    assert shown["JUDGE_MODEL"] == "(not set)"


def test_resolved_named_endpoint_sets_url_key_and_default_model(monkeypatch) -> None:
    monkeypatch.setenv("META_API_KEY", "meta-key-value")
    monkeypatch.setenv("CLASSDECK_MODEL", "openai/gpt-6-luna")
    settings = Settings(_env_file=None).resolved("meta")
    assert settings.model_api_base_url == "https://api.meta.ai/v1"
    assert settings.model == "muse-spark-1.3"
    assert settings.model_api_key is not None
    assert settings.model_api_key.get_secret_value() == "meta-key-value"
    assert settings.provider == "api"


def test_resolved_explicit_model_wins_over_profile_default(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    settings = Settings(_env_file=None).resolved("openrouter", "deepseek/deepseek-v4.1-flash-1017")
    assert settings.model == "deepseek/deepseek-v4.1-flash-1017"
    assert settings.model_api_host == "openrouter.ai"


def test_resolved_without_profile_borrows_the_matching_profile_key(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_API_BASE_URL", "https://api.meta.ai/v1")
    monkeypatch.setenv("META_API_KEY", "meta-key-value")
    settings = Settings(_env_file=None).resolved()
    assert settings.model_api_key is not None
    assert settings.model_api_key.get_secret_value() == "meta-key-value"


def test_unknown_endpoint_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("CLASSDECK_ENDPOINT", "nope")
    with pytest.raises(ValueError, match="unknown endpoint"):
        Settings(_env_file=None)


def test_endpoint_env_is_used_by_resolved(monkeypatch) -> None:
    monkeypatch.setenv("CLASSDECK_ENDPOINT", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d")
    settings = Settings(_env_file=None).resolved()
    assert settings.model_api_host == "api.deepseek.com"
    assert settings.model == "deepseek-chat"
