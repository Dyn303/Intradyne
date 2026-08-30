"""The trading loop, assembled so the API service can own it.

``engine/main.py`` builds a portfolio, a paper broker, a ledger, an execution
manager *and* its own uvicorn server. That made the engine a second service
with a second copy of all the state. Here the loop is assembled around an
``ExecutionManager`` supplied by the caller, so when the API hosts it there is
one portfolio, one ledger and one pre-trade gate.

The loop is started from the API lifespan and is off by default
(``ENGINE_ENABLED=false``): phase 2 of MIGRATION.md puts the machinery in
place, and enabling it is a separate, deliberate step.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from intradyne.core.config import Settings
from .data_ws import DataFeed
from .execution import ExecutionManager
from .reconcile import reconcile_on_start
from .risk import RiskManager
from .router import StrategyRouter


async def resolve_symbols(settings: Settings) -> List[str]:
    """The Shariah whitelist, narrowed to what the venue actually lists.

    Falls back to the unfiltered whitelist when the venue cannot be reached,
    so a market-data outage degrades the universe rather than the process.
    Narrowing can only ever remove symbols, so the fallback is not a way in
    for a non-whitelisted instrument.
    """
    try:
        import ccxt.async_support as ccxt

        exchange = getattr(ccxt, settings.exchange)({"enableRateLimit": True})
        try:
            markets = await exchange.load_markets()
        finally:
            await exchange.close()
        return settings.load_symbols(list(markets.keys()))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"could not load {settings.exchange} markets ({exc}); "
            "using the unfiltered whitelist"
        )
        return settings.load_symbols()


def load_strategy_params(settings: Settings) -> Optional[Dict[str, Any]]:
    """Load tuned strategy parameters and apply any risk overrides.

    The hosted loop previously constructed the router with no params at all,
    so STRATEGY_PARAMS_FILE / artifacts/production_params.json were silently
    ignored and the engine ran strategy defaults while the documentation said
    otherwise. Mutates `settings.risk` in place, matching the standalone
    entrypoint's behaviour.
    """
    path = os.getenv(
        "STRATEGY_PARAMS_FILE",
        str(Path(settings.artifacts_dir) / "production_params.json"),
    )
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"could not read strategy params from {path}: {exc}")
        return None
    if not isinstance(raw, dict):
        logger.warning(f"strategy params in {path} are not an object; ignoring")
        return None

    params = {
        k: raw[k] for k in ("momentum", "meanrev") if isinstance(raw.get(k), dict)
    }

    overrides = raw.get("risk") or {}
    if isinstance(overrides, dict):
        for field in (
            "max_pos_pct",
            "per_trade_sl_pct",
            "tp_pct",
            "dd_soft",
            "dd_hard",
        ):
            if field in overrides:
                try:
                    setattr(settings.risk, field, float(overrides[field]))
                except (TypeError, ValueError):
                    logger.warning(f"ignoring non-numeric risk override {field}")
    logger.info(f"loaded strategy params from {path}")
    return params or None


def build_risk_manager(settings: Settings) -> RiskManager:
    risk = RiskManager(
        max_pos_pct=settings.risk.max_pos_pct,
        per_trade_sl_pct=settings.risk.per_trade_sl_pct,
        tp_pct=settings.risk.tp_pct,
        dd_soft=settings.risk.dd_soft,
        dd_hard=settings.risk.dd_hard,
        flash_crash_drop_1h=settings.risk.flash_crash_drop_1h,
        max_concurrent_pos=settings.risk.max_concurrent_pos,
        kill_switch_breaches=settings.risk.kill_switch_breaches,
    )
    if settings.risk.use_atr:
        risk.use_atr = True
        risk.atr_window = int(settings.risk.atr_window)
        risk.atr_k_sl = float(settings.risk.atr_k_sl)
        risk.atr_k_tp = float(settings.risk.atr_k_tp)
    return risk


def build_router(
    settings: Settings,
    execution: ExecutionManager,
    symbols: List[str],
    params: Optional[Dict[str, Any]] = None,
) -> StrategyRouter:
    router = StrategyRouter(
        symbols,
        build_risk_manager(settings),
        execution,
        execution.ctx.portfolio,
        params=params,
    )
    router._max_spread_bps = int(max(0, settings.max_spread_bps))
    router._entry_cooldown_s = int(max(0, settings.entry_cooldown_s))
    router._sentiment_enabled = bool(settings.sentiment_enabled)
    router._sentiment_long_min = float(settings.sentiment_long_min)
    router._sentiment_size_min = float(settings.sentiment_size_min)
    router._sentiment_size_max = float(settings.sentiment_size_max)
    return router


#: How often to snapshot equity while the loop runs.
EQUITY_SAMPLE_SECONDS = 60.0

#: The router currently driving ticks, or None when the loop is not running.
#: Exposed so the API can reconfigure the *live* engine, which previously
#: required the separate engine process and its own FastAPI app.
_ACTIVE_ROUTER: Optional[StrategyRouter] = None


def get_active_router() -> Optional[StrategyRouter]:
    return _ACTIVE_ROUTER


def apply_params(runtime: Dict[str, Any]) -> Dict[str, Any]:
    """Reconfigure the running router and risk manager in place.

    Returns what was applied. Raises RuntimeError when no loop is running,
    so a caller is told plainly rather than silently changing nothing.
    """
    router = _ACTIVE_ROUTER
    if router is None:
        raise RuntimeError("engine is not running")
    router.apply_params(runtime)
    overrides = runtime.get("risk") or {}
    applied: Dict[str, Any] = {}
    for field in ("max_pos_pct", "per_trade_sl_pct", "tp_pct"):
        if field in overrides:
            try:
                setattr(router.risk, field, float(overrides[field]))
                applied[field] = float(overrides[field])
            except (TypeError, ValueError):
                logger.warning(f"ignoring non-numeric risk override {field}")
    return {"strategies": sorted(k for k in runtime if k != "risk"), "risk": applied}


async def run_once(
    settings: Settings,
    execution: ExecutionManager,
    symbols: Optional[List[str]] = None,
    feed: Optional[Any] = None,
) -> None:
    """Drive ticks into the router until the feed ends or the task is
    cancelled.

    `feed` is injectable so the tick path can be driven without a venue
    connection.
    """
    global _ACTIVE_ROUTER

    syms = symbols if symbols is not None else await resolve_symbols(settings)
    if not syms:
        logger.warning("engine: no tradable symbols resolved; loop not started")
        return

    # Before any order can be raised, refuse to trade if a previous run left
    # live submissions unaccounted for.
    reconcile_on_start(
        getattr(execution.ctx, "order_keys", None),
        live=bool(settings.mode == "live" and settings.live_trading_enabled),
    )

    params = load_strategy_params(settings)
    router = build_router(settings, execution, syms, params=params)
    source = feed or DataFeed(settings.exchange, use_testnet=settings.use_testnet)
    logger.bind(event="engine_start").info(
        {
            "symbols": syms,
            "mode": settings.mode,
            "venue": settings.exchange,
            "tuned_params": sorted(params) if params else [],
        }
    )

    marks = execution.ctx.marks
    # Seed the series so a drawdown has a starting point even before the first
    # sampling interval elapses.
    execution.record_equity()
    last_sample = time.monotonic()

    _ACTIVE_ROUTER = router
    try:
        async for l1 in source.start(syms):
            # Every tick feeds the flash-crash window, not only ticks that
            # produce an order -- otherwise the hour-ago sample is missing on
            # a quiet market and the guardrail declines to fire.
            if marks is not None:
                price = l1.get("last") or l1.get("bid") or l1.get("ask")
                symbol = l1.get("symbol")
                if symbol and price:
                    marks.record(str(symbol), float(price), ts=l1.get("ts"))
            await router.on_tick(l1)

            # Sample equity on a timer, not only when a fill happens.
            # Recording solely on fills left the drawdown guardrail blind to
            # unrealised losses: a book that fell 30% without trading showed
            # no drawdown at all, which is precisely when the halt most needs
            # to engage.
            now = time.monotonic()
            if now - last_sample >= EQUITY_SAMPLE_SECONDS:
                execution.record_equity()
                last_sample = now
    finally:
        _ACTIVE_ROUTER = None


async def supervise(
    settings: Settings,
    execution: ExecutionManager,
    *,
    restart_delay: float = 5.0,
) -> None:
    """Restart the loop if it crashes; exit cleanly when cancelled.

    A dropped websocket or a transient venue error should not silently stop
    trading for the lifetime of the process, which is what an unsupervised
    task would do.
    """
    while True:
        try:
            await run_once(settings, execution)
            logger.info("engine: feed ended; restarting")
        except asyncio.CancelledError:
            logger.info("engine: shutting down")
            raise
        except Exception as exc:  # noqa: BLE001
            # The traceback is rendered by the handler, which is configured
            # with diagnose=False so local variable *values* are never
            # printed -- `settings` carries the broker credentials.
            logger.opt(exception=True).error(
                f"engine: loop crashed ({exc!r}); restarting in {restart_delay}s"
            )
        try:
            await asyncio.sleep(restart_delay)
        except asyncio.CancelledError:
            logger.info("engine: shutting down")
            raise


__all__ = [
    "apply_params",
    "build_risk_manager",
    "get_active_router",
    "load_strategy_params",
    "build_router",
    "resolve_symbols",
    "run_once",
    "supervise",
]
