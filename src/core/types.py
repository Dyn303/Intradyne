from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Order:
    symbol: str
    side: str
    qty: float


@dataclass
class VenueQuote:
    venue: str
    price: float
    available: float


@dataclass
class ChildOrder:
    venue: str
    symbol: str
    side: str
    qty: float
    price: Optional[float] = None


__all__ = ["Order", "VenueQuote", "ChildOrder"]

