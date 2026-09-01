from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

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
from intradyne.core.idempotency import DuplicateOrder, OrderKeyStore, make_key
from intradyne.core.limits import NotionalTracker
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
    #: Durable traded-notional record, feeding the exposure caps.
    limits: Optional[NotionalTracker] = None
    #: Idempotency claims for live submission. Paper does not need it.
    order_keys: Optional[OrderKeyStore] = None
    #: "taker" crosses the spread; "maker" posts passively and may not fill.
    execution_mode: str = "taker"
    #: How far inside the touch to post when making, in bps.
    maker_offset_bps: float = 0.0


class ExecutionManager:
    """The single order path.

    Every order -- strategy-generated or API-submitted -- passes through
    ``submit``, so the Tier 1 gate is applied exactly once, in one place,
    before any broker is contacted.
    """

    def __init__(self, ctx: ExecContext) -> None:
        self.ctx = ctx

    def _gate(
        self,
        symbol: str,
        side: str,
        qty: float,
        params: Optional[Dict[str, Any]],
        price: Optional[float] = None,
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
                price=price,
            )
        )
        # Honour a VaR step-down: the gate may approve a smaller size than was
        # requested, and ignoring that would make the step-down decorative.
        return action, reasons, adjusted.qty

    def _record_mark(
        self, symbol: str, price: Optional[float], l1: Mapping[str, Any]
    ) -> None:
        if self.ctx.marks is None:
            return
        mark = l1.get("last") or l1.get("bid") or l1.get("ask") or price
        if mark:
            self.ctx.marks.record(symbol, float(mark), ts=l1.get("ts"))

    def _record_notional(self, symbol: str, qty: float, px: Optional[float]) -> None:
        if self.ctx.limits is None or not px:
            return
        self.ctx.limits.record(symbol, abs(float(qty)) * float(px))

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
        # A quote carries a string symbol alongside its numbers, and
        # `features`/`checks_passed` are strategy diagnostics that go straight
        # to the ledger as JSON -- never read numerically. Declaring all three
        # Dict[str, float] was simply untrue, and every caller contradicted it.
        l1: Mapping[str, Any],
        strategy_id: str,
        features: Mapping[str, Any],
        checks_passed: Mapping[str, Any],
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, object]:
        # Record the mark first: the flash-crash guardrail compares the
        # current price against an hour ago, so it must see this tick before
        # the gate runs on it.
        self._record_mark(symbol, price, l1)

        # The mark just recorded is what the exposure caps are measured
        # against, so resolve it before gating.
        mark = price or l1.get("last") or l1.get("bid") or l1.get("ask")
        action, reasons, qty = self._gate(
            symbol, side, qty, params, price=float(mark) if mark else None
        )
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
            # Claim an idempotency key before the venue is contacted, so a
            # crash mid-flight cannot become a second real order on restart.
            key = make_key(symbol, side, qty, strategy_id)
            if self.ctx.order_keys is not None:
                try:
                    self.ctx.order_keys.reserve(key, symbol, side, qty)
                except DuplicateOrder as exc:
                    self.ctx.ledger.append(
                        "order_duplicate_suppressed",
                        {"symbol": symbol, "side": side, "qty": qty, "key": key},
                    )
                    logger.bind(event="exec_duplicate").warning(str(exc))
                    return {
                        "status": "blocked",
                        "action": "duplicate",
                        "reasons": [str(exc)],
                    }
            try:
                res = await self.ctx.live_broker.place_order(
                    symbol, side, type_, qty, price, params, client_order_id=key
                )
            except Exception:
                # Keep the claim: the venue may have received it, so the key
                # must not be freed for silent reuse.
                if self.ctx.order_keys is not None:
                    self.ctx.order_keys.fail(key)
                raise
            if self.ctx.order_keys is not None:
                self.ctx.order_keys.complete(key, res.get("id"))
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
                        "idempotency_key": key,
                        "mode": "live",
                    }
                )
            # The live path returned before reaching the paper path's equity
            # snapshot, so live fills were invisible to the drawdown guardrail.
            self._record_notional(symbol, qty, px)
            self.record_equity()
            return res

        # In maker mode an *entry* becomes a passive limit at the touch. It
        # may not fill, which is the trade being made: the entry leg costs
        # 2bps instead of 7, in exchange for missed entries and adverse
        # selection (the book comes to you precisely when the move is against
        # you).
        #
        # Exits always cross. A passive stop-loss is not a stop: it rests
        # unfilled exactly when the market is running away from you, leaving
        # the position open and -- because the position still counts against
        # max_concurrent_pos -- blocking all further trading. Measured, that
        # took a 422-trade run down to one.
        #
        # Long-only makes this a clean split: entries buy, exits sell.
        if self.ctx.execution_mode == "maker" and type_ == "market" and side == "buy":
            # One resting entry per symbol at a time.
            #
            # The strategy decides to enter from position size, which stays
            # zero while an order rests unfilled, so it re-submits on every
            # subsequent tick. Those orders queue and then all fill together
            # on the first dip, producing a position many times the intended
            # size -- measured at roughly twelve times the notional of the
            # equivalent taker run.
            try:
                if self.ctx.paper.open_orders(symbol):
                    return {
                        "status": "pending",
                        "action": "resting_order_exists",
                        "reasons": [],
                    }
            except AttributeError:  # pragma: no cover - broker without a book
                pass

            touch = l1.get("bid") if side == "buy" else l1.get("ask")
            touch = touch or l1.get("last")
            if touch:
                offset = self.ctx.maker_offset_bps / 10_000.0
                post = (
                    float(touch) * (1.0 - offset)
                    if side == "buy"
                    else float(touch) * (1.0 + offset)
                )
                type_, price = "limit", post

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
        if order.status != "filled":
            # A resting limit that has not filled is not a trade. It stays in
            # the book and is swept by PaperBroker.on_tick on later quotes.
            return {"id": order.id, "status": order.status, "resting": True}

        px = order.price if order.filled_as_maker else px
        if order.status == "filled":
            self.ctx.trades += 1
            self._record_notional(symbol, qty, px)
        # Equity moved, so the drawdown guardrail needs the new point.
        self.record_equity()
        return {"id": order.id, "status": order.status}
