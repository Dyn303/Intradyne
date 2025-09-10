from __future__ import annotations

from typing import List


def compute_daily_returns(equity: List[float]) -> List[float]:
    if not equity:
        return []
    rets: List[float] = [0.0]
    for i in range(1, len(equity)):
        prev = equity[i - 1]
        cur = equity[i]
        if prev == 0:
            rets.append(0.0)
        else:
            rets.append((cur / prev - 1.0) * 100.0)
    return rets


def compute_max_drawdown(equity: List[float]) -> float:
    max_peak = 0.0
    max_dd_pct = 0.0
    for v in equity:
        if v > max_peak:
            max_peak = v
        if max_peak > 0:
            dd = (max_peak - v) / max_peak * 100.0
            if dd > max_dd_pct:
                max_dd_pct = dd
    return max_dd_pct

