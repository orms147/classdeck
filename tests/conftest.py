"""Shared test configuration.

Tests must never read the developer's ``.env`` or the user settings file (either may hold real
API keys): settings come from explicit environment variables only, ``CLASSDECK_HOME`` points at
an empty folder, and the cached settings are reset around each test.
"""

from __future__ import annotations

import pytest

from classdeck import config as config_module
from classdeck.config import Settings


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("CLASSDECK_HOME", str(tmp_path_factory.mktemp("classdeck-home")))
    config_module.get_settings.cache_clear()
    yield
    config_module.get_settings.cache_clear()
