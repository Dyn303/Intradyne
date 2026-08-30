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
from typing import List, Optional

from loguru import logger

from intradyne.core.config import Settings
from .data_ws import DataFeed
from .execution import ExecutionManager
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
) -> StrategyRouter:
    router = StrategyRouter(
        symbols,
        build_risk_manager(settings),
        execution,
        execution.ctx.portfolio,
    )
    router._max_spread_bps = int(max(0, settings.max_spread_bps))
    router._entry_cooldown_s = int(max(0, settings.entry_cooldown_s))
    router._sentiment_enabled = bool(settings.sentiment_enabled)
    router._sentiment_long_min = float(settings.sentiment_long_min)
    router._sentiment_size_min = float(settings.sentiment_size_min)
    router._sentiment_size_max = float(settings.sentiment_size_max)
    return router


async def run_once(
    settings: Settings,
    execution: ExecutionManager,
    symbols: Optional[List[str]] = None,
) -> None:
    """Drive ticks into the router until the feed ends or the task is
    cancelled."""
    syms = symbols if symbols is not None else await resolve_symbols(settings)
    if not syms:
        logger.warning("engine: no tradable symbols resolved; loop not started")
        return
    router = build_router(settings, execution, syms)
    feed = DataFeed(settings.exchange, use_testnet=settings.use_testnet)
    logger.bind(event="engine_start").info(
        {"symbols": syms, "mode": settings.mode, "venue": settings.exchange}
    )
    async for l1 in feed.start(syms):
        await router.on_tick(l1)


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
    "build_risk_manager",
    "build_router",
    "resolve_symbols",
    "run_once",
    "supervise",
]
