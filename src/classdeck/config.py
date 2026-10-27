"""Runtime settings, read from environment variables and an optional ``.env`` file.

Every knob the CLI or the API needs lives here so that nothing else in the package
reads ``os.environ`` directly. See ``.env.example`` for the documented variables.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["api", "mock"]
_LEGACY_PROVIDER_NAMES = {"meta": "api"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Any OpenAI-compatible Chat Completions endpoint; see docs/llm-provider.md for examples.
    model_api_key: SecretStr | None = Field(default=None, validation_alias="MODEL_API_KEY")
    model_api_base_url: str = Field(
        default="https://api.meta.ai/v1", validation_alias="MODEL_API_BASE_URL"
    )

    model: str = Field(default="muse-spark-1.3", validation_alias="CLASSDECK_MODEL")
    # "api" calls the endpoint above; "mock" runs the deterministic template pipeline.
    provider: ProviderName = Field(default="api", validation_alias="CLASSDECK_PROVIDER")

    # Independent judge model for evaluation — any OpenAI-compatible endpoint. Should be a
    # different model family from the generator to avoid self-preference bias. When unset,
    # eval falls back to the generator and says so.
    judge_api_key: SecretStr | None = Field(default=None, validation_alias="JUDGE_API_KEY")
    judge_api_base_url: str | None = Field(default=None, validation_alias="JUDGE_API_BASE_URL")
    judge_model: str | None = Field(default=None, validation_alias="JUDGE_MODEL")

    # Set to an empty string to disable the on-disk response cache.
    cache_dir: Path | None = Field(
        default=Path(".cache/llm"), validation_alias="CLASSDECK_CACHE_DIR"
    )
    workdir: Path = Field(default=Path("out"), validation_alias="CLASSDECK_WORKDIR")
    curriculum_dir: Path = Field(
        default=Path("curriculum"), validation_alias="CLASSDECK_CURRICULUM_DIR"
    )

    @field_validator("cache_dir", mode="before")
    @classmethod
    def _empty_means_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator("provider", mode="before")
    @classmethod
    def _normalise_provider(cls, value: object) -> object:
        if isinstance(value, str):
            lowered = value.strip().lower()
            return _LEGACY_PROVIDER_NAMES.get(lowered, lowered)
        return value

    @property
    def model_api_host(self) -> str:
        """Host part of the model endpoint, used to name the provider (``api:<host>``)."""
        return urlparse(self.model_api_base_url).hostname or self.model_api_base_url

    @property
    def judge_configured(self) -> bool:
        return bool(self.judge_api_key and self.judge_api_base_url and self.judge_model)

    def redacted(self) -> dict[str, str]:
        """Settings as display strings with API keys masked — for ``classdeck config``."""
        return {
            "MODEL_API_KEY": _mask(self.model_api_key),
            "MODEL_API_BASE_URL": self.model_api_base_url,
            "CLASSDECK_MODEL": self.model,
            "CLASSDECK_PROVIDER": self.provider,
            "CLASSDECK_CACHE_DIR": str(self.cache_dir) if self.cache_dir else "(disabled)",
            "CLASSDECK_WORKDIR": str(self.workdir),
            "CLASSDECK_CURRICULUM_DIR": str(self.curriculum_dir),
            "JUDGE_API_KEY": _mask(self.judge_api_key),
            "JUDGE_API_BASE_URL": self.judge_api_base_url or "(not set — eval uses the generator)",
            "JUDGE_MODEL": self.judge_model or "(not set)",
        }


def _mask(secret: SecretStr | None) -> str:
    key = secret.get_secret_value() if secret else ""
    if not key:
        return "(not set)"
    return f"{key[:4]}…{key[-4:]}" if len(key) > 8 else "(set)"


@lru_cache
def get_settings() -> Settings:
    return Settings()
