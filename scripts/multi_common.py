#!/usr/bin/env python
"""Shared pieces for the intraday strategy searches.

Extracted so the single-instrument and pooled searches cannot drift apart.
The filter constants in particular must be identical: comparing a pooled run
against a single-instrument run is only meaningful if both were judged by the
same bar.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_klines_archive import FIELDS, months  # noqa: E402
from strategy_search import Bars, evaluate, forward_outcomes  # noqa: E402
from random_strategy_search import (  # noqa: E402
    COST_MAKER_BPS,
    COST_TAKER_BPS,
    MIN_TRADES,
    Strategy,
    _predicates,
    _regimes,
    sample_strategies,
)

__all__ = [
    "COST_MAKER_BPS",
    "COST_TAKER_BPS",
    "MIN_TRADES",
    "Strategy",
    "load_symbol",
    "outcomes_for",
    "sample_strategies",
    "strategy_trades",
]


def load_symbol(
    cache: Path, symbol: str, timeframe: str, start: str, end: str
) -> Dict[str, Any] | None:
    """Load one instrument and precompute its predicates once."""
    parts = []
    for m in months(start, end):
        f = cache / f"{symbol}-{timeframe}-{m}.npz"
        if f.exists():
            z = np.load(f)
            parts.append({k: z[k] for k in FIELDS})
    if not parts:
        return None
    bars = Bars(**{k: np.concatenate([p[k] for p in parts]) for k in FIELDS})
    if len(bars) < 1000:
        return None
    return {
        "bars": bars,
        "preds": _predicates(bars),
        "regs": _regimes(bars),
        # Outcomes depend only on (tp, sl, hold), so they are shared across
        # every strategy using that geometry on this instrument.
        "cache": {},
    }


def outcomes_for(
    s: Strategy, panel: Dict[str, Any], bar_minutes: int
) -> Tuple[np.ndarray, np.ndarray]:
    hold_bars = max(1, s.spec["hold_min"] // bar_minutes)
    key = (s.spec["tp"], s.spec["sl"], hold_bars)
    if key not in panel["cache"]:
        panel["cache"][key] = forward_outcomes(
            panel["bars"], s.spec["tp"], s.spec["sl"], hold_bars, 0.0
        )
    return panel["cache"][key]


def strategy_trades(
    s: Strategy, panel: Dict[str, Any], bar_minutes: int
) -> Tuple[np.ndarray, float]:
    """Per-trade gross returns on one instrument, and the median hold.

    Returns the individual trades rather than their mean: pooling across
    instruments has to be per-trade, or a strategy firing twice on one coin
    counts as much as one firing two hundred times on another.
    """
    gross, held = outcomes_for(s, panel, bar_minutes)
    mask = s.mask(panel["preds"], panel["regs"])
    idx = np.where(mask & np.isfinite(gross))[0]
    picked: List[int] = []
    busy = -1
    for i in idx:
        if i > busy:
            picked.append(i)
            busy = i + max(int(held[i]), 1)
    if not picked:
        return np.array([]), 0.0
    return gross[picked], float(np.median(held[picked])) * bar_minutes


def evaluate_single(
    s: Strategy, panel: Dict[str, Any], bar_minutes: int
) -> Dict[str, float]:
    """Single-instrument summary, kept here so both searches agree."""
    gross, held = outcomes_for(s, panel, bar_minutes)
    r = evaluate(s.mask(panel["preds"], panel["regs"]), gross, held, 1)
    return {
        "trades": r["trades"],
        "gross_bps": r["mean_bps"],
        "win_rate": r["win_rate"],
    }
