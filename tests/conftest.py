from __future__ import annotations

import pytest

from intradyne.core.config import reset_settings_cache


@pytest.fixture(autouse=True)
def _isolate_runtime_state(tmp_path, monkeypatch):
    """Keep tests off the repository's real database and ledger.

    Both default to paths inside the repo (data/trades.sqlite and
    explainability_ledger.jsonl). Without this, running the suite silently
    writes equity rows and ledger entries into tracked files.
    """
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'trades.sqlite'}")
    monkeypatch.setenv("EXPLAIN_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    reset_settings_cache()
    try:
        from intradyne.api.deps import reset_execution_manager

        reset_execution_manager()
    except Exception:  # pragma: no cover
        pass
    yield


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
