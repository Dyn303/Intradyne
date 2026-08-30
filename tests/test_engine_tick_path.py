"""The real tick path: feed -> router -> gate -> broker.

Every earlier engine test monkeypatched `run_once`, so this path had never
executed -- not in tests and not in the container. These drive it with a
scripted feed instead of a venue connection.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import pytest

from intradyne.api import deps
from intradyne.core.config import load_settings, reset_settings_cache
from intradyne.engine import loop as engine_loop
from intradyne.risk.kill_switch import set_halt


class ScriptedFeed:
    """Yields a fixed sequence of L1 ticks, then ends."""

    def __init__(self, ticks: List[Dict[str, Any]]):
        self.ticks = ticks
        self.started_with: List[str] | None = None

    async def start(self, symbols):
        self.started_with = list(symbols)
        for tick in self.ticks:
            yield tick
            await asyncio.sleep(0)


def _ticks(symbol: str, prices: List[float], start_ts: float = 1_000_000.0):
    return [
        {
            "ts": start_ts + i,
            "symbol": symbol,
            "bid": p * 0.999,
            "ask": p * 1.001,
            "last": p,
            "volume": 10.0,
        }
        for i, p in enumerate(prices)
    ]


@pytest.fixture
def engine(tmp_path, monkeypatch):
    for k in ("APP_ENV", "API_AUTH_REQUIRED", "X_API_KEY", "STRATEGY_PARAMS_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'eq.sqlite'}")
    monkeypatch.setenv("EXPLAIN_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("ALLOWED_SYMBOLS", "BTC,ETH")
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    reset_settings_cache()
    deps.reset_execution_manager()
    set_halt(False)
    yield tmp_path
    set_halt(False)
    deps.reset_execution_manager()
    reset_settings_cache()


def _run(feed, symbols=("BTC/USDT",)):
    execution = deps.get_execution_manager()
    asyncio.run(
        engine_loop.run_once(
            load_settings(), execution, symbols=list(symbols), feed=feed
        )
    )
    return execution


# ---- the loop actually runs ---------------------------------------------


def test_ticks_reach_the_router_and_the_feed_is_started_with_the_symbols(engine):
    feed = ScriptedFeed(_ticks("BTC/USDT", [100.0, 101.0, 102.0]))
    _run(feed)
    assert feed.started_with == ["BTC/USDT"]


def test_every_tick_feeds_the_flash_crash_window(engine):
    """Marks must be recorded from ticks, not only from orders -- otherwise
    the hour-ago sample is missing on a quiet market."""
    feed = ScriptedFeed(_ticks("BTC/USDT", [100.0, 101.0, 102.0]))
    execution = _run(feed)
    marks = execution.ctx.marks
    assert marks is not None
    assert marks.latest("BTC/USDT") == pytest.approx(102.0)


def test_equity_is_seeded_even_with_no_fills(engine):
    """Recording equity only on fills left the drawdown guardrail with an
    empty series until something traded."""
    _run(ScriptedFeed(_ticks("BTC/USDT", [100.0])))
    history = deps.get_equity_history()
    assert history.count() >= 1
    assert history.latest() == pytest.approx(10_000.0)


def test_active_router_is_exposed_while_running_and_cleared_after(engine):
    seen = {}

    class _Watching(ScriptedFeed):
        async def start(self, symbols):
            self.started_with = list(symbols)
            seen["during"] = engine_loop.get_active_router()
            for t in self.ticks:
                yield t

    _run(_Watching(_ticks("BTC/USDT", [100.0])))
    assert seen["during"] is not None, "router should be reachable while running"
    assert engine_loop.get_active_router() is None, "handle must clear on exit"


# ---- tuned parameters are honoured (regression) --------------------------


def test_tuned_parameters_are_applied_to_the_strategies(engine):
    """build_router used to be called with no params at all, so
    STRATEGY_PARAMS_FILE and production_params.json were silently ignored and
    the engine always ran strategy defaults."""
    params_file = engine / "production_params.json"
    params_file.write_text(
        json.dumps(
            {
                "momentum": {"breakout_window": 7, "min_range_bps": 42},
                "risk": {"max_pos_pct": 0.001, "tp_pct": 0.009},
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings()
    loaded = engine_loop.load_strategy_params(settings)
    assert loaded is not None and "momentum" in loaded
    # risk overrides are applied to settings in place
    assert settings.risk.max_pos_pct == pytest.approx(0.001)
    assert settings.risk.tp_pct == pytest.approx(0.009)

    router = engine_loop.build_router(
        settings, deps.get_execution_manager(), ["BTC/USDT"], params=loaded
    )
    assert router.momo["BTC/USDT"].breakout_window == 7
    assert router.momo["BTC/USDT"].min_range_bps == 42
    assert router.risk.max_pos_pct == pytest.approx(0.001)


def test_missing_params_file_is_not_an_error(engine):
    assert engine_loop.load_strategy_params(load_settings()) is None


def test_malformed_params_file_is_ignored_rather_than_crashing(engine):
    (engine / "production_params.json").write_text("{not json", encoding="utf-8")
    assert engine_loop.load_strategy_params(load_settings()) is None


# ---- the gate still governs orders raised by the loop --------------------


def test_a_halt_stops_orders_raised_from_the_tick_path(engine):
    """Orders the strategies raise go through the same Tier 1 gate as
    API-submitted ones."""
    set_halt(True, reason="manual stop")
    execution = _run(ScriptedFeed(_ticks("BTC/USDT", [100.0] * 5)))
    # Nothing can have filled while halted.
    assert execution.ctx.portfolio.get_position("BTC/USDT").base == 0.0
    assert execution.ctx.portfolio.balances["USDT"] == pytest.approx(10_000.0)


def test_loop_exits_cleanly_when_no_symbols_resolve(engine):
    execution = deps.get_execution_manager()
    asyncio.run(
        engine_loop.run_once(
            load_settings(), execution, symbols=[], feed=ScriptedFeed([])
        )
    )
    assert engine_loop.get_active_router() is None
