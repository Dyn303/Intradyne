from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger

from .broker_paper import PaperBroker
from .broker_ccxt import CCXTBroker
from intradyne.risk.guardrails import Guardrails, OrderReq
from intradyne.risk.shariah import (
    assert_whitelisted,
    enforce_spot_only,
    forbid_shorting,
)
from intradyne.core.equity import EquityHistory
from intradyne.core.ledger import ExplainabilityLedger
from intradyne.core.marks import MarkStore
from .portfolio import Portfolio
from .metrics_ml import ML_EXEC_BUYS


@dataclass
class ExecContext:
    portfolio: Portfolio
    paper: PaperBroker
    ledger: ExplainabilityLedger
    whitelist: list[str]
    live_broker: Optional[CCXTBroker] = None
    live_enabled: bool = False
    trades: int = 0
    fast_mode: bool = False
    #: The Tier 1 pre-trade veto. When absent, submit falls back to the
    #: imperative compliance helpers, which cover the Shariah rules but not
    #: drawdown, flash-crash, VaR, the kill-switch or the operator halt.
    guardrails: Optional[Guardrails] = None
    #: Recent prices, feeding the flash-crash guardrail.
    marks: Optional[MarkStore] = None
    #: Durable equity series, feeding the drawdown and VaR guardrails.
    equity: Optional[EquityHistory] = None


class ExecutionManager:
    """The single order path.

    Every order -- strategy-generated or API-submitted -- passes through
    ``submit``, so the Tier 1 gate is applied exactly once, in one place,
    before any broker is contacted.
    """

    def __init__(self, ctx: ExecContext) -> None:
        self.ctx = ctx

    def _gate(
        self, symbol: str, side: str, qty: float, params: Optional[Dict[str, Any]]
    ) -> tuple[str, list[str], float]:
        """Run the pre-trade veto. Returns (action, reasons, approved_qty)."""
        base_inv = self.ctx.portfolio.get_position(symbol).base

        if self.ctx.guardrails is None:
            # No gate wired in (e.g. a bare backtest). Enforce at least the
            # Shariah rules, which is what this path did historically.
            assert_whitelisted(symbol, self.ctx.whitelist)
            forbid_shorting(side, base_inv, qty)
            enforce_spot_only(params)
            return "allow", [], qty

        action, reasons, adjusted = self.ctx.guardrails.gate_trade(
            OrderReq(
                symbol=symbol,
                side=side,
                qty=qty,
                params=params,
                base_inventory=base_inv,
            )
        )
        # Honour a VaR step-down: the gate may approve a smaller size than was
        # requested, and ignoring that would make the step-down decorative.
        return action, reasons, adjusted.qty

    def _record_mark(
        self, symbol: str, price: Optional[float], l1: Dict[str, float]
    ) -> None:
        if self.ctx.marks is None:
            return
        mark = l1.get("last") or l1.get("bid") or l1.get("ask") or price
        if mark:
            self.ctx.marks.record(symbol, float(mark), ts=l1.get("ts"))

    def record_equity(self) -> Optional[float]:
        """Snapshot portfolio equity into the durable history.

        Without this the drawdown guardrail has nothing to measure, and
        without it being durable the measurement resets on every restart.
        """
        if self.ctx.equity is None:
            return None
        marks = self.ctx.marks.marks() if self.ctx.marks is not None else {}
        value = self.ctx.portfolio.equity(marks)
        self.ctx.equity.record(value)
        return value

    async def submit(
        self,
        symbol: str,
        side: str,
        type_: str,
        qty: float,
        price: Optional[float],
        l1: Dict[str, float],
        strategy_id: str,
        features: Dict[str, float],
        checks_passed: Dict[str, bool],
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, object]:
        # Record the mark first: the flash-crash guardrail compares the
        # current price against an hour ago, so it must see this tick before
        # the gate runs on it.
        self._record_mark(symbol, price, l1)

        action, reasons, qty = self._gate(symbol, side, qty, params)
        if action != "allow":
            # Record the refusal. A blocked order previously left no trace at
            # all on this path, which defeats the point of an audit ledger.
            self.ctx.ledger.append(
                "order_blocked",
                {
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "action": action,
                    "reasons": reasons,
                    "strategy_id": strategy_id,
                },
            )
            logger.bind(event="exec_blocked").info(
                {"symbol": symbol, "side": side, "action": action, "reasons": reasons}
            )
            # Returned rather than raised: one refused order must not tear
            # down the strategy loop.
            return {"status": "blocked", "action": action, "reasons": reasons}

        if qty <= 0:
            return {"status": "blocked", "action": "zero_qty", "reasons": reasons}

        # `checks_passed` is strategy-supplied diagnostics. It is recorded as
        # such and never as compliance evidence -- callers pass a hardcoded
        # {"whitelist": True, ...}, so presenting it as the outcome of the
        # compliance checks would put unverified claims in the audit trail.
        gate_record = {"action": action, "reasons": reasons}

        if self.ctx.live_enabled and self.ctx.live_broker is not None:
            res = await self.ctx.live_broker.place_order(
                symbol, side, type_, qty, price, params
            )
            px = res.get("price") or price
            if not self.ctx.fast_mode:
                self.ctx.ledger.append(
                    {
                        "ts": res.get("timestamp"),
                        "event": "order_filled",
                        "symbol": symbol,
                        "side": side,
                        "qty": qty,
                        "px": px,
                        "fees": None,
                        "pnl": None,
                        "strategy_id": strategy_id,
                        "features": features,
                        "strategy_checks": checks_passed,
                        "gate": gate_record,
                        "mode": "live",
                    }
                )
            return res

        order = self.ctx.paper.place_order(symbol, side, type_, qty, price, l1)
        px = price
        if order.type == "market":
            px = (l1.get("ask") if side == "buy" else l1.get("bid")) or l1.get("last")
        if not self.ctx.fast_mode:
            self.ctx.ledger.append(
                {
                    "ts": l1.get("ts"),
                    "event": "order_filled",
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "px": px,
                    "fees": "included",  # fees applied in portfolio
                    "pnl": self.ctx.portfolio.get_position(symbol).realized_pnl,
                    "strategy_id": strategy_id,
                    "features": features,
                    "strategy_checks": checks_passed,
                    "gate": gate_record,
                    "mode": "paper",
                }
            )
            if strategy_id == "ml" and side == "buy":
                try:
                    ML_EXEC_BUYS.labels(symbol).inc()
                except Exception:
                    pass
        logger.bind(event="exec_submit").info(
            {
                "order_id": order.id,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "px": px,
                "type": type_,
            }
        )
        if order.status == "filled":
            self.ctx.trades += 1
        # Equity moved, so the drawdown guardrail needs the new point.
        self.record_equity()
        return {"id": order.id, "status": order.status}
