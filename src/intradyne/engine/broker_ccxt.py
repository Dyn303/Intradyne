from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

from intradyne.risk.kill_switch import halt_reason, is_halted
from intradyne.risk.shariah import ComplianceError, enforce_spot_only


class CCXTBroker:
    def __init__(
        self,
        exchange_id: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        live_enabled: bool,
    ) -> None:
        self.exchange_id = exchange_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.live_enabled = live_enabled
        self.exchange: Optional[Any] = None

    async def connect(self) -> None:
        # Imported lazily: ccxt is only needed to trade live, and the API
        # image runs paper-only, so it must not be an import-time dependency.
        import ccxt.async_support as ccxt

        ex_class = getattr(ccxt, self.exchange_id)
        self.exchange = ex_class(
            {
                "apiKey": self.api_key,
                "secret": self.api_secret,
                "password": self.api_passphrase,
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            }
        )
        await self.exchange.load_markets()

    async def close(self) -> None:
        if self.exchange:
            try:
                await self.exchange.close()
            except Exception:
                pass

    async def place_order(
        self,
        symbol: str,
        side: str,
        type_: str,
        qty: float,
        price: Optional[float],
        params: Optional[Dict[str, Any]] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        # The third leg of the live gate. gate_trade already refuses while
        # halted, but the halt must also hold at the boundary that actually
        # spends money -- any future caller reaching the broker directly is
        # then still covered.
        if is_halted():
            raise ComplianceError(f"trading halted: {halt_reason() or 'admin_halt'}")
        if not self.live_enabled:
            raise ComplianceError(
                "Live trading disabled: set MODE=live and LIVE_TRADING_ENABLED=true"
            )
        enforce_spot_only(params)
        assert self.exchange is not None

        venue_params = dict(params or {})
        if client_order_id:
            # Let the venue reject a duplicate as well as our local claim.
            venue_params.setdefault("clientOrderId", client_order_id)

        order = await self.exchange.create_order(
            symbol, type_, side, qty, price, venue_params
        )
        logger.bind(event="live_order").info(order)
        return order
