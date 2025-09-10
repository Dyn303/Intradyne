from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class ExchangeAdapter(ABC):
    name: str

    @abstractmethod
    async def get_balances(self) -> Dict[str, float]:
        ...

    @abstractmethod
    async def get_symbols(self) -> List[str]:
        ...

    @abstractmethod
    async def get_ticker(self, symbol: str) -> Optional[float]:
        ...

    @abstractmethod
    async def place_order(self, symbol: str, side: str, qty: float) -> Dict:
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> Dict:
        ...

    @abstractmethod
    async def get_open_orders(self) -> List[Dict]:
        ...


__all__ = ["ExchangeAdapter"]

