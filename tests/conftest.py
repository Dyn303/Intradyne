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


@pytest.fixture(autouse=True, scope="session")
def _quiet_loguru_diagnostics():
    """Match production logging: no local-variable rendering in tracebacks.

    loguru defaults to diagnose=True, which prints the *values* of locals.
    Settings objects carrying broker credentials are in scope across the
    engine, so a crash would put them in the log -- and the rendering is slow
    enough to distort timing-sensitive tests.
    """
    try:
        import sys

        from loguru import logger
    except Exception:  # pragma: no cover - loguru always present in CI
        yield
        return
    logger.remove()
    handler = logger.add(sys.stderr, backtrace=True, diagnose=False, level="WARNING")
    yield
    logger.remove(handler)
