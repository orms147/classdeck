"""Runtime settings, read from environment variables and an optional ``.env`` file.

Every knob the CLI or the API needs lives here so that nothing else in the package
reads ``os.environ`` directly. See ``.env.example`` for the documented variables.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["api", "mock"]
_LEGACY_PROVIDER_NAMES = {"meta": "api"}


class Endpoint(BaseModel):
    """A known OpenAI-compatible endpoint: base URL, the env var holding its key, default model."""

    base_url: str
    key_env: str
    default_model: str
    label: str


ENDPOINTS: dict[str, Endpoint] = {
    "meta": Endpoint(
        base_url="https://api.meta.ai/v1",
        key_env="META_API_KEY",
        default_model="muse-spark-1.3",
        label="Meta Model API",
    ),
    "openrouter": Endpoint(
        base_url="https://openrouter.ai/api/v1",
        key_env="OPENROUTER_API_KEY",
        default_model="openai/gpt-6-luna",
        label="OpenRouter",
    ),
    "deepseek": Endpoint(
        base_url="https://api.deepseek.com",
        key_env="DEEPSEEK_API_KEY",
        default_model="deepseek-chat",
        label="DeepSeek",
    ),
}


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
    # Image model for `classdeck illustrate` (images endpoint of the same endpoint profile).
    image_model: str = Field(default="muse-image-1.0", validation_alias="CLASSDECK_IMAGE_MODEL")
    # "api" calls the endpoint above; "mock" runs the deterministic template pipeline.
    provider: ProviderName = Field(default="api", validation_alias="CLASSDECK_PROVIDER")

    # A named endpoint profile (see ENDPOINTS) overrides MODEL_API_BASE_URL / MODEL_API_KEY and
    # uses the profile's default model unless CLASSDECK_MODEL names one for it.
    endpoint: str | None = Field(default=None, validation_alias="CLASSDECK_ENDPOINT")
    meta_api_key: SecretStr | None = Field(default=None, validation_alias="META_API_KEY")
    openrouter_api_key: SecretStr | None = Field(
        default=None, validation_alias="OPENROUTER_API_KEY"
    )
    deepseek_api_key: SecretStr | None = Field(default=None, validation_alias="DEEPSEEK_API_KEY")

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

    @field_validator("endpoint", mode="before")
    @classmethod
    def _normalise_endpoint(cls, value: object) -> object:
        if isinstance(value, str):
            name = value.strip().lower()
            if name == "":
                return None
            if name not in ENDPOINTS:
                raise ValueError(f"unknown endpoint {value!r}; known: {', '.join(ENDPOINTS)}")
            return name
        return value

    def _profile_key(self, name: str) -> SecretStr | None:
        return {
            "meta": self.meta_api_key,
            "openrouter": self.openrouter_api_key,
            "deepseek": self.deepseek_api_key,
        }.get(name)

    def resolved(self, endpoint: str | None = None, model: str | None = None) -> Settings:
        """A copy with the model endpoint settled.

        With a named endpoint (argument, else ``CLASSDECK_ENDPOINT``) the profile's base URL and
        key env var win, and the model is ``model`` if given, else the profile default. Without
        one, ``MODEL_API_*`` apply; a missing ``MODEL_API_KEY`` is taken from the profile whose
        base URL matches, so ``META_API_KEY`` alone is enough for ``https://api.meta.ai/v1``.
        """
        name = (endpoint or self.endpoint or "").strip().lower() or None
        if name and name not in ENDPOINTS:
            raise ValueError(f"unknown endpoint {name!r}; known: {', '.join(ENDPOINTS)}")
        update: dict[str, object] = {}
        if name:
            profile = ENDPOINTS[name]
            update["model_api_base_url"] = profile.base_url
            update["model_api_key"] = self._profile_key(name) or self.model_api_key
            update["model"] = model or profile.default_model
            update["provider"] = "api"
        else:
            if model:
                update["model"] = model
            if not self.model_api_key:
                for key, profile in ENDPOINTS.items():
                    if urlparse(profile.base_url).hostname == self.model_api_host:
                        update["model_api_key"] = self._profile_key(key)
                        break
        return self.model_copy(update=update) if update else self

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
            "CLASSDECK_IMAGE_MODEL": self.image_model,
            "CLASSDECK_PROVIDER": self.provider,
            "CLASSDECK_ENDPOINT": self.endpoint or "(not set)",
            "META_API_KEY": _mask(self.meta_api_key),
            "OPENROUTER_API_KEY": _mask(self.openrouter_api_key),
            "DEEPSEEK_API_KEY": _mask(self.deepseek_api_key),
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
