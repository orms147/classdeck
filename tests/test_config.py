from __future__ import annotations

import json
from pathlib import Path

import pytest

from classdeck.config import (
    Settings,
    key_hint,
    load_user_settings,
    save_user_settings,
    user_settings_path,
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


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.model == "openai/gpt-6-luna"
    assert settings.provider == "api"
    assert settings.model_api_base_url == "https://openrouter.ai/api/v1"
    assert settings.model_api_host == "openrouter.ai"
    assert settings.default_theme == "sunny"
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


# --- user settings file ------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    monkeypatch.setenv("CLASSDECK_HOME", str(folder))
    for name in ("OPENROUTER_API_KEY", "CLASSDECK_THEME", "CLASSDECK_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    return folder


def test_user_settings_path_follows_classdeck_home(home: Path) -> None:
    assert user_settings_path() == home / "settings.json"


def test_user_settings_path_defaults_per_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLASSDECK_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(Path("appdata")))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(Path("xdg")))
    path = user_settings_path()
    assert path.name == "settings.json"
    assert path.parent.name in {"ClassDeck", "classdeck"}


def test_saved_settings_are_read_back_and_feed_the_defaults(home: Path) -> None:
    saved = save_user_settings(
        {"openrouter_api_key": "sk-or-abcdefgh1234", "model": "vendor/some-model", "other": "x"}
    )
    assert saved == {"openrouter_api_key": "sk-or-abcdefgh1234", "model": "vendor/some-model"}
    assert json.loads((home / "settings.json").read_text(encoding="utf-8")) == saved

    settings = Settings(_env_file=None)
    assert settings.model == "vendor/some-model"
    resolved = settings.resolved()
    assert resolved.model_api_host == "openrouter.ai"
    assert resolved.model_api_key is not None
    assert resolved.model_api_key.get_secret_value() == "sk-or-abcdefgh1234"
    assert resolved.model == "vendor/some-model"


def test_environment_wins_over_the_user_settings_file(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_user_settings({"openrouter_api_key": "from-file", "model": "file/model"})
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    monkeypatch.setenv("CLASSDECK_MODEL", "env/model")
    settings = Settings(_env_file=None)
    assert settings.model == "env/model"
    assert settings.openrouter_api_key is not None
    assert settings.openrouter_api_key.get_secret_value() == "from-env"


def test_init_arguments_win_over_the_file_and_empty_values_remove_keys(home: Path) -> None:
    save_user_settings({"model": "file/model", "default_theme": "kids"})
    assert Settings(_env_file=None, model="init/model").model == "init/model"
    assert Settings(_env_file=None).default_theme == "kids"
    assert save_user_settings({"default_theme": ""}) == {"model": "file/model"}
    assert Settings(_env_file=None).default_theme == "sunny"


def test_a_missing_or_broken_file_means_the_defaults(home: Path) -> None:
    assert load_user_settings() == {}
    home.mkdir(parents=True)
    (home / "settings.json").write_text("{not json", encoding="utf-8")
    assert load_user_settings() == {}
    (home / "settings.json").write_text('["list"]', encoding="utf-8")
    assert Settings(_env_file=None).model == "openai/gpt-6-luna"


def test_key_hint_never_shows_the_middle_of_a_key() -> None:
    hint = key_hint("sk-or-v1-0123456789abcdef")
    assert hint == "sk-…cdef"
    assert "0123" not in hint
    assert key_hint("") is None and key_hint(None) is None
    assert key_hint("short") == "…rt"
