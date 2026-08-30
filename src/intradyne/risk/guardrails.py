from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from intradyne.core.ledger import Ledger
from intradyne.risk.kill_switch import halt_reason, is_halted
from intradyne.risk.shariah import ShariahPolicy
from prometheus_client import Counter


# Defaults (can be overridden via constructor/env)
DD_WARN_PCT = float(os.getenv("DD_WARN_PCT", 0.15))
DD_HALT_PCT = float(os.getenv("DD_HALT_PCT", 0.20))
FLASH_CRASH_PCT = float(os.getenv("FLASH_CRASH_PCT", 0.30))
KILL_SWITCH_BREACHES = int(os.getenv("KILL_SWITCH_BREACHES", 3))
VAR_1D_MAX = float(os.getenv("VAR_1D_MAX", 0.05))

_BREACH_COUNTER = Counter(
    "intradyne_guardrail_breaches_total",
    "Total guardrail breaches",
    labelnames=("type", "action"),
)


@dataclass
class OrderReq:
    symbol: str
    side: str
    qty: float
    meta: Optional[Dict[str, Any]] = None
    #: Venue parameters, screened for margin/derivative keys (spot-only).
    params: Optional[Dict[str, Any]] = None
    #: Base-asset inventory held. Required for a sell: without it the order
    #: cannot be shown to be covered, and the gate fails closed.
    base_inventory: Optional[float] = None

    def step_down(self, factor: float = 0.5) -> "OrderReq":
        return replace(self, qty=max(self.qty * factor, 0.0))

    # Ledger is provided by intradyne.core.ledger


class PriceFeed:
    """Interface for price data. Implement get_price(symbol, at).
    Tests can provide a stub.
    """

    def get_price(self, symbol: str, at: Optional[datetime] = None) -> Optional[float]:
        raise NotImplementedError


class RiskData:
    """Interface for risk data.
    Provide equity series and daily returns for 30 days.
    """

    def equity_series_30d(self) -> List[Tuple[datetime, float]]:
        raise NotImplementedError

    def equity_daily_returns_30d(self) -> List[float]:
        raise NotImplementedError


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    vs = sorted(values)
    q = min(max(q, 0.0), 1.0)
    # nearest-rank method
    k = int(max(0, min(len(vs) - 1, round(q * (len(vs) - 1)))))
    return float(vs[k])


def historical_var(returns: List[float], alpha: float = 0.95) -> float:
    # VaR as positive number representing loss magnitude at (1-alpha)
    if not returns:
        return 0.0
    q = _percentile(returns, 1 - alpha)
    return max(0.0, -q)


def dd_30d(equity_series: List[Tuple[datetime, float]]) -> float:
    peak = float("-inf")
    dd = 0.0
    for _t, eq in equity_series:
        peak = max(peak, float(eq))
        if peak <= 0:
            continue
        dd = max(dd, (peak - float(eq)) / peak)
    return dd


class Guardrails:
    def __init__(
        self,
        price_feed: PriceFeed,
        risk_data: RiskData,
        ledger: Optional[Ledger] = None,
        shariah: Optional[ShariahPolicy] = None,
        thresholds: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.price = price_feed
        self.risk = risk_data
        self.ledger = ledger or Ledger()
        self.shariah = shariah or ShariahPolicy()
        self.th = {
            "dd_warn": DD_WARN_PCT,
            "dd_halt": DD_HALT_PCT,
            "flash": FLASH_CRASH_PCT,
            "kill_switch": KILL_SWITCH_BREACHES,
            "var_max": VAR_1D_MAX,
        }
        if thresholds:
            self.th.update(thresholds)

    def _breach(self, btype: str, **fields: Any) -> None:
        payload = {"type": btype}
        payload.update(fields)
        self.ledger.append("guardrail_breach", payload)
        try:
            _BREACH_COUNTER.labels(
                type=btype, action=str(fields.get("action", ""))
            ).inc()
        except Exception:
            pass

    def _recent_breach_count(self, hours: int = 24) -> int:
        since = datetime.utcnow() - timedelta(hours=hours)
        return sum(
            1
            for r in self.ledger.iter_recent(since)
            if r.get("event") == "guardrail_breach"
        )

    def gate_trade(self, req: OrderReq) -> Tuple[str, List[str], OrderReq]:
        reasons: List[str] = []

        # 0) Operator halt. Checked here rather than in the route so that it
        # also stops strategy-generated orders, not just API-submitted ones.
        if is_halted():
            reason = halt_reason() or "admin_halt"
            self._breach("admin_halt", symbol=req.symbol, reason=reason, action="halt")
            return "halt", [reason], req

        # 1) Shariah: whitelist, blocked tags, spot-only, long-only
        ok, reason = self.shariah.check(
            req.symbol,
            side=req.side,
            meta=req.meta,
            params=req.params,
            base_inventory=req.base_inventory,
            qty=req.qty,
        )
        if not ok:
            self._breach("compliance", symbol=req.symbol, reason=reason, action="block")
            return "block", [reason], req

        # 2) Kill switch: N breaches in the last 24h.
        #
        # Checked before the metric guardrails, not after. The flash-crash
        # branch returns "pause" as soon as it trips, so while it kept firing
        # this check was never reached and repeated breaches could never
        # escalate to a halt -- which is the entire purpose of a kill switch.
        if self._recent_breach_count(24) >= int(self.th["kill_switch"]):
            self._breach("kill_switch", action="halt")
            return "halt", ["kill_switch"], req

        # 3) Risk metrics
        eq = self.risk.equity_series_30d()
        dd = dd_30d(eq)
        if dd >= self.th["dd_halt"]:
            self._breach(
                "dd_halt",
                metric=round(dd, 6),
                threshold=self.th["dd_halt"],
                action="halt",
            )
            return "halt", [f"30d drawdown {dd:.3f} >= {self.th['dd_halt']:.3f}"], req
        if dd >= self.th["dd_warn"]:
            self._breach(
                "dd_warn",
                metric=round(dd, 6),
                threshold=self.th["dd_warn"],
                action="warn",
            )
            reasons.append(f"dd_warn {dd:.3f}")

        # 4) Flash crash check (1h drop > threshold)
        now = datetime.utcnow()
        p_now = self.price.get_price(req.symbol, now)
        p_1h = self.price.get_price(req.symbol, now - timedelta(hours=1))
        if p_now and p_1h and p_1h > 0:
            drop = (p_1h - p_now) / p_1h
            if drop > self.th["flash"]:
                self._breach(
                    "flash_crash",
                    symbol=req.symbol,
                    metric=round(drop, 6),
                    threshold=self.th["flash"],
                    action="pause",
                )
                return (
                    "pause",
                    [f"flash_crash {drop:.3f} > {self.th['flash']:.3f}"],
                    req,
                )

        # 5) VaR step-down
        rets = self.risk.equity_daily_returns_30d()
        var = historical_var(rets, alpha=0.95)
        if var > self.th["var_max"]:
            self._breach(
                "var_stepdown",
                metric=round(var, 6),
                threshold=self.th["var_max"],
                action="stepdown",
            )
            req = req.step_down()
            reasons.append(f"var {var:.3f} > {self.th['var_max']:.3f}")

        return "allow", reasons, req


# ShariahPolicy is re-exported: it moved to risk.shariah but is widely
# imported from here.
__all__ = [
    "Guardrails",
    "OrderReq",
    "PriceFeed",
    "RiskData",
    "ShariahPolicy",
    "dd_30d",
    "historical_var",
]
