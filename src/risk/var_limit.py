from __future__ import annotations

from typing import List


def historical_var(returns: List[float], alpha: float = 0.95) -> float:
    from src.risk.guardrails import historical_var as _var

    return _var(returns, alpha)


__all__ = ["historical_var"]
