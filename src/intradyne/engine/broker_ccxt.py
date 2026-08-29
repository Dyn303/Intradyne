from __future__ import annotations

from typing import Any, Dict, Optional

import ccxt.async_support as ccxt
from loguru import logger

from .compliance import ComplianceError, enforce_spot_only


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
        self.exchange: Optional[ccxt.Exchange] = None

    async def connect(self) -> None:
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
    ) -> Dict[str, Any]:
        if not self.live_enabled:
            raise ComplianceError(
                "Live trading disabled: set MODE=live and LIVE_TRADING_ENABLED=true"
            )
        enforce_spot_only(params)
        assert self.exchange is not None
        order = await self.exchange.create_order(
            symbol, type_, side, qty, price, params or {}
        )
        logger.bind(event="live_order").info(order)
        return order
