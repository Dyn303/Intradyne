from __future__ import annotations

from typing import Dict, List, Optional

from src.adapters.base import ExchangeAdapter
from src.config import load_settings


class BitgetAdapter(ExchangeAdapter):
    name = "bitget"

    def __init__(self) -> None:
        s = load_settings()
        self.api_key = s.BITGET_API_KEY
        self.api_secret = s.BITGET_API_SECRET
        self.passphrase = s.BITGET_API_PASSPHRASE
        # Do not log secrets; validate presence only
        if not (self.api_key and self.api_secret and self.passphrase):
            raise RuntimeError("Missing BITGET credentials in environment")

    async def get_balances(self) -> Dict[str, float]:
        return {}

    async def get_symbols(self) -> List[str]:
        return []

    async def get_ticker(self, symbol: str) -> Optional[float]:
        return None

    async def place_order(self, symbol: str, side: str, qty: float) -> Dict:
        return {"order_id": "stub", "status": "accepted"}

    async def cancel_order(self, order_id: str) -> Dict:
        return {"order_id": order_id, "status": "cancelled"}

    async def get_open_orders(self) -> List[Dict]:
        return []


__all__ = ["BitgetAdapter"]
