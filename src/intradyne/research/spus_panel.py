"""Turn the SPUS filing timeline into a Panel the cross-sectional harness runs on.

`docs/spus_universe_timeline.json` records which CUSIPs SPUS held as of each
quarter end. `cross_sectional.Panel` wants a daily grid of prices and a boolean
membership mask. This bridges them, and the bridging is where the two mistakes
that matter live.

## Mistake one: dating membership to the as-of date

N-PORT is filed **54 to 60 days after** the period it describes. A backtest
that treats the 2020-05-31 holdings as known on 2020-06-01 is using a document
that did not exist until 2020-07-28. That is a two-month look-ahead applied to
every rebalance, and it flatters results in exactly the direction that makes a
strategy look tradeable.

So membership becomes effective on the **filing date**, following the
convention `scripts/fundamentals_asof.py` already established for earnings.
`FROM_AS_OF` is offered for the argument that an ETF publishes holdings daily
on its own website -- true in principle, and unverifiable from this data source,
which is why it is not the default.

## Mistake two: conflating "not held" with "not priced"

A name absent from a quarter's filing was not in the universe. A name in the
filing with no price row is a *data gap*, and the difference matters: the first
is information, the second is a hole that the 80% precondition in
`docs/SLOT_1_PREREGISTRATION.md` exists to catch. `build_panel` reports the
coverage separately rather than letting a gap masquerade as an exclusion.

## What this does not do

It does not map CUSIP to a tradeable symbol. N-PORT carries no ticker, and a
mapping that ignores renames silently drops names -- precondition P1. The price
source is asked for CUSIPs and must resolve them itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Protocol, Tuple

import numpy as np

from .cross_sectional import Panel

TIMELINE = Path("docs/spus_universe_timeline.json")

#: When a quarter's holdings become usable.
#:
#: FILED is the honest default: the filing date is when the document provably
#: existed. AS_OF assumes daily holdings disclosure, which the fund does publish
#: but which this data source cannot evidence historically.
FROM_FILED = "filed"
FROM_AS_OF = "as_of"


class PriceSource(Protocol):
    """Whatever supplies prices, asked for by CUSIP.

    Deliberately narrow. Slot 1 is blocked on a source that resolves CUSIPs
    through renames and delistings, and the harness should not care which one
    is eventually bought.
    """

    def close_series(
        self, cusip: str, start: date, end: date
    ) -> Mapping[date, float]:  # pragma: no cover - protocol
        ...


@dataclass(frozen=True)
class Coverage:
    """How much of the filed universe could actually be priced.

    Reported per quarter because an average hides the quarter that fails --
    and `SLOT_1_PREREGISTRATION.md` aborts on any quarter below 80%, not on
    the mean.
    """

    by_quarter: Dict[str, Tuple[int, int]]  # as_of -> (priced, filed)

    def worst(self) -> Tuple[str, float]:
        if not self.by_quarter:
            return "", 0.0
        q, (p, f) = min(
            self.by_quarter.items(),
            key=lambda kv: (kv[1][0] / kv[1][1]) if kv[1][1] else 0.0,
        )
        return q, (p / f if f else 0.0)

    def below(self, floor: float) -> List[str]:
        return sorted(
            q for q, (p, f) in self.by_quarter.items() if f and (p / f) < floor
        )


def _load_timeline(path: Path) -> Dict[str, Dict[str, object]]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Build it with scripts/spus_universe.py --contact ..."
        )
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not doc:
        raise ValueError(f"{path} is empty or malformed")
    return doc


def membership_windows(
    timeline: Mapping[str, Mapping[str, object]], effective: str = FROM_FILED
) -> List[Tuple[date, Optional[date], List[str]]]:
    """(start, end, cusips) windows, each holding until the next supersedes it.

    Windows are keyed by when the information became usable, not by the period
    it describes -- so ordering by as-of date and ordering by filing date can
    differ, and the sort is on the effective date for that reason.
    """
    rows: List[Tuple[date, List[str]]] = []
    for as_of, entry in timeline.items():
        holdings = entry.get("holdings")
        if not isinstance(holdings, list):
            continue
        cusips = [
            str(h["cusip"]) for h in holdings if isinstance(h, dict) and h.get("cusip")
        ]
        if not cusips:
            continue
        if effective == FROM_AS_OF:
            eff = date.fromisoformat(as_of)
        else:
            filed = entry.get("filed")
            if not isinstance(filed, str):
                continue
            eff = date.fromisoformat(filed)
        rows.append((eff, cusips))

    rows.sort(key=lambda r: r[0])
    out: List[Tuple[date, Optional[date], List[str]]] = []
    for i, (start, cusips) in enumerate(rows):
        end = rows[i + 1][0] if i + 1 < len(rows) else None
        out.append((start, end, cusips))
    return out


def _sessions(start: date, end: date) -> List[date]:
    """Weekday grid.

    Not an exchange calendar: holidays remain and simply have no price, which
    the harness already tolerates via its NaN handling. A real calendar is
    worth adding when a price source exists to check it against -- inventing
    one now would be a guess dressed as precision.
    """
    days: List[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def build_panel(
    prices: PriceSource,
    timeline_path: Path = TIMELINE,
    effective: str = FROM_FILED,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> Tuple[Panel, Coverage]:
    """A Panel over every CUSIP ever held, with quarterly membership."""
    if effective not in (FROM_FILED, FROM_AS_OF):
        raise ValueError(f"effective must be {FROM_FILED!r} or {FROM_AS_OF!r}")

    timeline = _load_timeline(timeline_path)
    windows = membership_windows(timeline, effective)
    if not windows:
        raise ValueError("no usable membership windows in the timeline")

    first = start or windows[0][0]
    last = end or date.today()
    sessions = _sessions(first, last)
    if not sessions:
        raise ValueError(f"no sessions between {first} and {last}")

    symbols = sorted({c for _, _, cs in windows for c in cs})
    idx = {c: j for j, c in enumerate(symbols)}
    t, n = len(sessions), len(symbols)

    membership = np.zeros((t, n), dtype=bool)
    for w_start, w_end, cusips in windows:
        lo = np.searchsorted([d.toordinal() for d in sessions], w_start.toordinal())
        hi = (
            np.searchsorted([d.toordinal() for d in sessions], w_end.toordinal())
            if w_end
            else t
        )
        if lo >= t:
            continue
        cols = [idx[c] for c in cusips if c in idx]
        membership[lo:hi, cols] = True

    close = np.full((t, n), np.nan)
    session_row = {d: i for i, d in enumerate(sessions)}
    priced_cusips = set()
    for c in symbols:
        try:
            series = prices.close_series(c, sessions[0], sessions[-1])
        except Exception:  # noqa: BLE001
            # A source that cannot resolve one CUSIP must not take the panel
            # down; the coverage report is where that shows up.
            continue
        if not series:
            continue
        priced_cusips.add(c)
        j = idx[c]
        for d, px in series.items():
            i = session_row.get(d)
            if i is not None and px and px > 0:
                close[i, j] = float(px)

    # Coverage is measured against what each quarter *filed*, not against the
    # union -- a name added late should not count against earlier quarters.
    by_quarter: Dict[str, Tuple[int, int]] = {}
    for as_of, entry in timeline.items():
        holdings = entry.get("holdings")
        if not isinstance(holdings, list):
            continue
        filed_cusips = {
            str(h["cusip"]) for h in holdings if isinstance(h, dict) and h.get("cusip")
        }
        if filed_cusips:
            by_quarter[as_of] = (
                len(filed_cusips & priced_cusips),
                len(filed_cusips),
            )

    dates = np.array(
        [
            datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()
            for d in sessions
        ]
    )
    return (
        Panel(dates=dates, symbols=symbols, close=close, membership=membership),
        Coverage(by_quarter=by_quarter),
    )


def describe(panel: Panel, coverage: Coverage, floor: float = 0.80) -> str:
    """A summary that states the precondition rather than implying it."""
    live = int(panel.membership.sum())
    priced_live = int((panel.membership & np.isfinite(panel.close)).sum())
    q, worst = coverage.worst()
    failing = coverage.below(floor)
    lines = [
        f"panel: {len(panel.dates)} sessions x {len(panel.symbols)} CUSIPs",
        f"  member-sessions        : {live:,}",
        f"  of those, priced       : {priced_live:,} ({100 * priced_live / live:.1f}%)"
        if live
        else "  no membership",
        f"  worst quarter          : {q} at {100 * worst:.1f}%",
    ]
    if failing:
        lines.append(
            f"  BELOW {100 * floor:.0f}% IN {len(failing)} QUARTER(S): {failing[:5]}"
        )
        lines.append(
            "  SLOT_1_PREREGISTRATION.md aborts here -- the panel is not "
            "survivorship-honest and the slot is not spent."
        )
    else:
        lines.append(f"  every quarter at or above {100 * floor:.0f}%")
    return "\n".join(lines)


__all__ = [
    "Coverage",
    "FROM_AS_OF",
    "FROM_FILED",
    "PriceSource",
    "build_panel",
    "describe",
    "membership_windows",
]
