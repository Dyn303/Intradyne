from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from loguru import logger

from .broker_paper import PaperBroker
from .broker_ccxt import CCXTBroker
from .compliance import assert_whitelisted, enforce_spot_only, forbid_shorting
from .ledger import ExplainabilityLedger
from .portfolio import Portfolio


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


class ExecutionManager:
    def __init__(self, ctx: ExecContext) -> None:
        self.ctx = ctx

    async def submit(self, symbol: str, side: str, type_: str, qty: float, price: Optional[float], l1: Dict[str, float], strategy_id: str, features: Dict[str, float], checks_passed: Dict[str, bool]) -> Dict[str, object]:
        assert_whitelisted(symbol, self.ctx.whitelist)
        base_inv = self.ctx.portfolio.get_position(symbol).base
        forbid_shorting(side, base_inv)
        enforce_spot_only({})

        if self.ctx.live_enabled and self.ctx.live_broker is not None:
            res = await self.ctx.live_broker.place_order(symbol, side, type_, qty, price)
            px = res.get('price') or price
            if not self.ctx.fast_mode:
                self.ctx.ledger.append({
                    "ts": res.get('timestamp'),
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "px": px,
                    "fees": None,
                    "pnl": None,
                    "strategy_id": strategy_id,
                    "features": features,
                    "checks_passed": checks_passed,
                    "mode": "live",
                })
            return res
        else:
            order = self.ctx.paper.place_order(symbol, side, type_, qty, price, l1)
            px = price
            if order.type == 'market':
                px = (l1.get('ask') if side == 'buy' else l1.get('bid')) or l1.get('last')
            if not self.ctx.fast_mode:
                self.ctx.ledger.append({
                    "ts": l1.get('ts'),
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "px": px,
                    "fees": "included",  # fees applied in portfolio
                    "pnl": self.ctx.portfolio.get_position(symbol).realized_pnl,
                    "strategy_id": strategy_id,
                    "features": features,
                    "checks_passed": checks_passed,
                    "mode": "paper",
                })
            logger.bind(event="exec_submit").info({"order_id": order.id, "symbol": symbol, "side": side, "qty": qty, "px": px, "type": type_})
            if order.status == 'filled':
                self.ctx.trades += 1
            return {"id": order.id, "status": order.status}
