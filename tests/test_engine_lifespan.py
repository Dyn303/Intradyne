"""The API lifespan owns the trading loop.

The loop is off by default; when on, it runs as a supervised task that
restarts on failure and shuts down cleanly.
"""

from __future__ import annotations

import asyncio

import pytest

from intradyne.core.config import load_settings, reset_settings_cache


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    for k in (
        "APP_ENV",
        "API_AUTH_REQUIRED",
        "X_API_KEY",
        "ENGINE_ENABLED",
        "ACKNOWLEDGE_NO_EDGE",
    ):
        monkeypatch.delenv(k, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


def _run_lifespan(app):
    """Enter and exit the app lifespan, returning the engine task if any."""
    from intradyne.api import app as app_module

    seen = {}

    async def go():
        async with app_module._lifespan(app):
            seen["tasks"] = [
                t for t in asyncio.all_tasks() if t.get_name() == "intradyne-engine"
            ]
        seen["after"] = [
            t
            for t in asyncio.all_tasks()
            if t.get_name() == "intradyne-engine" and not t.done()
        ]

    asyncio.run(go())
    return seen


def test_engine_is_off_by_default():
    assert load_settings().engine_enabled is False


def test_lifespan_starts_no_task_when_disabled():
    from intradyne.api.app import create_app

    seen = _run_lifespan(create_app())
    assert seen["tasks"] == []


def test_lifespan_starts_and_stops_the_engine_task(monkeypatch):
    """With the engine on, a supervised task runs and is cancelled on exit."""
    monkeypatch.setenv("ENGINE_ENABLED", "true")
    # The strategy has no demonstrated edge, so the boot gate blocks the
    # loop by default. This test is about lifespan mechanics, not about
    # whether the strategy is worth running, so it acknowledges and moves
    # on -- see test_strategy_edge_gate.py for the gate itself.
    monkeypatch.setenv("ACKNOWLEDGE_NO_EDGE", "true")
    reset_settings_cache()

    started = asyncio.Event()

    async def _fake_supervise(settings, execution, **kwargs):
        started.set()
        await asyncio.sleep(3600)  # never finishes on its own

    import intradyne.engine.loop as loop_mod

    monkeypatch.setattr(loop_mod, "supervise", _fake_supervise)

    from intradyne.api.app import create_app

    seen = _run_lifespan(create_app())
    assert len(seen["tasks"]) == 1, "engine task should be running inside lifespan"
    assert seen["after"] == [], "engine task should be cancelled on shutdown"


def test_supervisor_restarts_the_loop_after_a_crash(monkeypatch):
    """A dropped feed must not silently stop trading for the process
    lifetime, which is what an unsupervised task would do.

    Waits on an event rather than sleeping a fixed interval: the previous
    version raced against however long loguru took to render a traceback and
    failed intermittently under load.
    """
    from intradyne.engine import loop as loop_mod

    target_restarts = 3
    calls = {"n": 0}
    reached = asyncio.Event()

    async def _boom(settings, execution, symbols=None):
        calls["n"] += 1
        if calls["n"] >= target_restarts:
            reached.set()
        raise RuntimeError("feed dropped")

    monkeypatch.setattr(loop_mod, "run_once", _boom)

    async def go():
        task = asyncio.create_task(
            loop_mod.supervise(load_settings(), object(), restart_delay=0.001)
        )
        try:
            await asyncio.wait_for(reached.wait(), timeout=10)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(go())
    assert calls["n"] >= target_restarts


def test_supervisor_exits_cleanly_on_cancel(monkeypatch):
    from intradyne.engine import loop as loop_mod

    async def _hang(settings, execution, symbols=None):
        await asyncio.sleep(3600)

    monkeypatch.setattr(loop_mod, "run_once", _hang)

    async def go():
        task = asyncio.create_task(
            loop_mod.supervise(load_settings(), object(), restart_delay=0.001)
        )
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
