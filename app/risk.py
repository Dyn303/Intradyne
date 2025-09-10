from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional


@dataclass
class RiskState:
    breaches_24h: Deque[float] = field(default_factory=deque)  # timestamps
    dd_soft_triggered: bool = False
    dd_hard_triggered: bool = False
    kill_switch: bool = False
    symbol_windows: Dict[str, Deque[tuple[float, float]]] = field(default_factory=dict)  # ts, price for 60m


@dataclass
class RiskManager:
    max_pos_pct: float
    per_trade_sl_pct: float
    tp_pct: float
    dd_soft: float
    dd_hard: float
    flash_crash_drop_1h: float
    max_concurrent_pos: int
    kill_switch_breaches: int
    state: RiskState = field(default_factory=RiskState)

    def sizer(self, equity: float, price: float) -> float:
        max_notional = equity * self.max_pos_pct
        qty = max_notional / price if price > 0 else 0.0
        return max(qty, 0.0)

    def sl_tp_levels(self, entry_price: float) -> tuple[float, float]:
        sl = entry_price * (1.0 - self.per_trade_sl_pct)
        tp = entry_price * (1.0 + self.tp_pct)
        return sl, tp

    def update_drawdown(self, start_equity: float, current_equity: float) -> None:
        if start_equity <= 0:
            return
        dd = 1.0 - current_equity / start_equity
        now = time.time()
        if dd >= self.dd_soft:
            self.state.dd_soft_triggered = True
            self._register_breach(now)
        if dd >= self.dd_hard:
            self.state.dd_hard_triggered = True
            self._register_breach(now)
        self._update_kill_switch(now)

    def flash_crash_check(self, symbol: str, ts: float, price: float) -> bool:
        win = self.state.symbol_windows.setdefault(symbol, deque())
        cutoff = ts - 3600.0
        win.append((ts, price))
        while win and win[0][0] < cutoff:
            win.popleft()
        if not win:
            return False
        max_px = max(p for _, p in win)
        if max_px <= 0:
            return False
        drop = (max_px - price) / max_px
        if drop >= self.flash_crash_drop_1h:
            self._register_breach(ts)
            return True
        return False

    def can_open_new_position(self, open_positions: int) -> bool:
        if self.state.kill_switch or self.state.dd_hard_triggered:
            return False
        return open_positions < self.max_concurrent_pos and not self.state.dd_soft_triggered

    def _register_breach(self, ts: float) -> None:
        self.state.breaches_24h.append(ts)

    def _update_kill_switch(self, ts: float) -> None:
        cutoff = ts - 86400.0
        q = self.state.breaches_24h
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.kill_switch_breaches:
            self.state.kill_switch = True

