from __future__ import annotations

import pytest

from intradyne.core.config import reset_settings_cache


@pytest.fixture(autouse=True)
def _isolate_settings_cache():
    """Keep the cached Settings from leaking across tests.

    load_settings() is memoised so that request handlers do not re-parse .env
    on every call. Tests routinely monkeypatch the environment and expect the
    next load to observe it, so the cache is dropped either side of each test.
    """
    reset_settings_cache()
    yield
    reset_settings_cache()
