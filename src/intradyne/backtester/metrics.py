from __future__ import annotations

import math
from typing import Iterable


def sharpe(returns: Iterable[float], risk_free: float = 0.0) -> float:
    rs = list(returns)
    if not rs:
        return 0.0
    excess = [r - risk_free for r in rs]
    mean = sum(excess) / len(excess)
    var = sum((x - mean) ** 2 for x in excess) / max(1, len(excess) - 1)
    std = math.sqrt(var) if var > 0 else 0.0
    if std == 0:
        return 0.0
    # assume returns are per-period; return unit Sharpe
    return mean / std


def winrate(outcomes: Iterable[float]) -> float:
    xs = list(outcomes)
    if not xs:
        return 0.0
    wins = sum(1 for x in xs if x > 0)
    return wins / len(xs)


__all__ = ["sharpe", "winrate"]
