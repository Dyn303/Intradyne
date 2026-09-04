"""Measured touch spreads, per instrument.

A backtest has to assume a spread -- OHLCV carries none -- and the assumption
decides what the backtest concludes. One figure cannot serve a universe
spanning BTC at 0.00bps and DOT at 11.44, so `scripts/measure_spreads.py`
measures each instrument and commits the result to
`docs/spread_measurements.json`, dated and accumulating forward.

**These are today's spreads applied to historical bars.** That assumes the
cross-sectional ordering held -- that DOT has always been thinner than BTC --
which is far more defensible than assuming every instrument was equal, but it
is an assumption, not a measurement of the past. A symbol with no reading is
omitted rather than guessed at, and takes the configured fallback.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

#: Committed evidence, not runtime state: spreads drift, so the figures a cost
#: model was built against travel with it. See `scripts/build_universe.py`,
#: which puts its rulings in `docs/` for the same reason.
DEFAULT_PATH = Path("docs/spread_measurements.json")


def _find(path: Optional[Path]) -> Optional[Path]:
    if path is not None:
        return path if path.exists() else None
    if DEFAULT_PATH.exists():
        return DEFAULT_PATH
    # Walk up from this module so a backtest run from a subdirectory still
    # finds the repo's copy.
    for parent in Path(__file__).resolve().parents:
        candidate = parent / DEFAULT_PATH
        if candidate.exists():
            return candidate
    return None


def load_measured_spreads(
    path: Optional[Path] = None, exchange: Optional[str] = None
) -> Dict[str, float]:
    """Per-symbol median spread in bps, from the most recent measurement.

    Returns an empty map when no measurement is available, which leaves every
    symbol on the configured fallback -- absence of a reading is not evidence
    of a tight book, and must not be treated as one.
    """
    found = _find(path)
    if found is None:
        return {}
    try:
        doc = json.loads(found.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(doc, dict) or not doc:
        return {}

    for date in sorted(doc, reverse=True):
        entry = doc[date]
        if not isinstance(entry, dict):
            continue
        if exchange and entry.get("exchange") != exchange:
            continue
        symbols = entry.get("symbols")
        if not isinstance(symbols, dict):
            continue
        out: Dict[str, float] = {}
        for sym, row in symbols.items():
            if not isinstance(row, dict):
                continue
            v = row.get("spread_bps")
            if isinstance(v, (int, float)) and v >= 0:
                out[str(sym)] = float(v)
        if out:
            return out
    return {}
