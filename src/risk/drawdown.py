from __future__ import annotations

from typing import List, Tuple
from datetime import datetime


def dd_30d(equity_series: List[Tuple[datetime, float]]) -> float:
    from src.risk.guardrails import dd_30d as _dd

    return _dd(equity_series)


__all__ = ["dd_30d"]
