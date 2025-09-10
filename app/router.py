from __future__ import annotations

import asyncio
from typing import Dict, List

from loguru import logger

from .execution import ExecutionManager
from .risk import RiskManager
from .portfolio import Portfolio
from .strategies.momentum import MomentumStrategy
from .strategies.meanrev import MeanRevStrategy


class StrategyRouter:
    def __init__(self, symbols: List[str], risk: RiskManager, execman: ExecutionManager, portfolio: Portfolio, params: Dict[str, Dict[str, float]] | None = None) -> None:
        self.symbols = symbols
        self.risk = risk
        self.execman = execman
        self.portfolio = portfolio
        self.momo = {s: MomentumStrategy(symbol=s) for s in symbols}
        self.meanrev = {s: MeanRevStrategy(symbol=s) for s in symbols}
        self.open_symbols: set[str] = set()
        self.stops: dict[str, tuple[float, float]] = {}  # symbol -> (sl,tp)
        # Apply parameter overrides if provided
        params = params or {}
        for s in symbols:
            m = self.momo[s]
            for k, v in params.get("momentum", {}).items():
                if hasattr(m, k):
                    setattr(m, k, v)  # type: ignore
            r = self.meanrev[s]
            for k, v in params.get("meanrev", {}).items():
                if hasattr(r, k):
                    setattr(r, k, v)  # type: ignore

    async def on_tick(self, l1: Dict[str, object]) -> None:
        sym = l1["symbol"]
        last = l1.get("last") or l1.get("bid") or l1.get("ask")
        if last is None:
            return
        last_f = float(last)
        # Risk flash crash check
        halted = self.risk.flash_crash_check(sym, l1["ts"], last_f)
        if halted:
            logger.warning(f"Flash-crash shield halted {sym}")
            return

        pos = self.portfolio.get_position(sym)
        # Check exits (SL/TP)
        if pos.base > 0 and sym in self.stops:
            sl, tp = self.stops[sym]
            if last_f <= sl or last_f >= tp:
                qty = pos.base
                features = {"exit_reason": "sl" if last_f <= sl else "tp"}
                checks = {"whitelist": True, "spot_only": True, "long_only": True}
                await self.execman.submit(sym, "sell", "market", qty, None, l1, "stop_exit", features, checks)
                self.stops.pop(sym, None)
                return

        open_positions = sum(1 for p in self.portfolio.positions.values() if p.base > 0)
        if not self.risk.can_open_new_position(open_positions):
            return

        # Run strategies in priority order
        for strat in (self.momo[sym], self.meanrev[sym]):
            sig = strat.on_tick(l1)
            if not sig:
                continue
            if sig["action"] == "buy":
                # Do not pyramid: skip if already long
                if pos.base > 0:
                    continue
                qty = self.risk.sizer(self.portfolio.equity({sym: last_f}), last_f)
                if qty <= 0:
                    continue
                sl, tp = self.risk.sl_tp_levels(last_f)
                features = sig.get("features", {})
                features.update({"sl": sl, "tp": tp})
                checks = {
                    "whitelist": True,
                    "spot_only": True,
                    "long_only": True,
                }
                await self.execman.submit(sym, "buy", "market", qty, None, l1, strat.id, features, checks)
                self.stops[sym] = (sl, tp)
                break
