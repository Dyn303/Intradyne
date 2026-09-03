#!/usr/bin/env python
"""A3's liquidity floor: was this name tradeable *at* the rebalance date.

    python scripts/equity_liquidity.py --symbols AAPL,MSFT,F --as-of 2026-06-30

Amendment A3 has two halves. `scripts/equity_pit_universe.py` built membership
-- who was listed when, with the dead retained. This is the other half: of
those members, which could actually be traded in size, judged on data available
at that date and never on today's volume. Using current liquidity to decide what
was tradeable in 2019 leaks the future exactly as using current listings does.

The measure is median daily dollar volume over a trailing window ending at the
rebalance date, following `point_in_time_universe.py:155` which does the same
thing for crypto. Median rather than mean because a single halt-and-reopen day
can carry a mean, and a name that trades in size once a month is not liquid.

## The finding that shapes this script

**Delisted names have no usable history from this provider, and one of the two
ways it fails is silent.**

    FXEN  delisted 2015   ->  "Invalid API call"          -- a clean refusal
    ADVM  delisted 2026   ->  100 sessions of 4.3600      -- flat line, volume 0

The second is the dangerous one. A series of identical prices at zero volume is
not data; it is a placeholder standing where the delisting decline used to be.
Scored naively it reports zero volatility and would sail through any check that
only asks "did I get rows back". So quality is asserted *before* liquidity is
computed, and a series that fails is recorded as `no_data` -- never as a pass,
and never as a fail on the merits.

## What that costs, stated rather than hidden

A liquidity floor applied with this provider can only judge survivors. Since
38% of every symbol ever listed is now delisted, screening on it reintroduces
a slice of the survivorship bias the membership half exists to remove. The
worksheet reports coverage per rebalance date so the size of that hole is
visible, and `--require-coverage` makes the script exit non-zero when the hole
is larger than a caller is willing to accept.

Fixing it properly needs a provider that serves delisted history -- a paid
survivorship-free dataset. That is a purchase, not a code change, and this
script's job is to say so precisely rather than to paper over it.

Requires ALPHAVANTAGE_API_KEY. See .env.example.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import httpx
except ImportError:  # pragma: no cover - dependency is in requirements.txt
    print("httpx is required: pip install httpx", flush=True)
    raise

AV = "https://www.alphavantage.co/query"

#: Trailing window the median is taken over, in calendar days.
LOOKBACK_DAYS = 90

#: Minimum observations inside the window. Below this the median is a
#: statement about a handful of days.
MIN_OBSERVATIONS = 30

#: A name must have traded within this many days of the rebalance date. A
#: halted or dead name still has old bars.
STALE_DAYS = 10

#: Default floor. $1M/day is roughly where a retail-sized order stops being a
#: meaningful fraction of the day's turnover; it is a starting value, not a
#: finding, and belongs in the pre-registration of whatever test uses it.
MIN_DOLLAR_VOLUME = 1_000_000.0

#: A window this proportion zero-volume is a placeholder, not a quiet stock.
MAX_ZERO_VOLUME_FRAC = 0.20

#: A window whose closes are this proportion identical is a flat line.
MAX_FLAT_FRAC = 0.50


def _get(c: httpx.Client, params: Dict[str, str], tries: int = 5) -> Optional[str]:
    """Alpha Vantage answers a quota breach with 200 and a Note, not a 429."""
    for i in range(tries):
        try:
            r = c.get(AV, params=params, timeout=90.0)
            if r.status_code == 429:
                time.sleep(20)
                continue
            if r.status_code != 200:
                return None
            text = r.text
            if text.lstrip().startswith("{"):
                body = r.json()
                if "Note" in body or "Information" in body:
                    time.sleep(20)
                    continue
                return None  # an Error Message: the symbol has no series
            return text
        except Exception:  # noqa: BLE001
            time.sleep(3 * (i + 1))
    return None


@dataclass
class Series:
    symbol: str
    dates: List[str] = field(default_factory=list)
    close: List[float] = field(default_factory=list)
    volume: List[float] = field(default_factory=list)

    def window(self, as_of: str, lookback: int) -> Tuple[np.ndarray, np.ndarray]:
        """Closes and volumes in (as_of - lookback, as_of]. Never beyond."""
        lo = (
            datetime.strptime(as_of, "%Y-%m-%d").date() - timedelta(days=lookback)
        ).isoformat()
        idx = [i for i, d in enumerate(self.dates) if lo < d <= as_of]
        return (
            np.array([self.close[i] for i in idx]),
            np.array([self.volume[i] for i in idx]),
        )

    def last_trade_on_or_before(self, as_of: str) -> Optional[str]:
        prior = [d for d in self.dates if d <= as_of]
        return max(prior) if prior else None


@dataclass(frozen=True)
class Verdict:
    """One name's outcome. `usable` separates 'illiquid' from 'unknowable'.

    A screen that collapses those two is how a survivorship hole becomes a
    liquidity finding.
    """

    symbol: str
    usable: bool
    passed: bool
    reason: str
    dollar_volume: Optional[float] = None
    observations: int = 0


def parse_daily(text: str, symbol: str) -> Series:
    s = Series(symbol=symbol)
    for row in csv.DictReader(io.StringIO(text)):
        d = (row.get("timestamp") or "").strip()
        if not d:
            continue
        # Parse before appending. Appending the date first and letting a bad
        # close raise leaves the three lists at different lengths, and the
        # reorder below then reads past the end of two of them.
        try:
            close = float(row["close"])
            volume = float(row["volume"])
        except (KeyError, TypeError, ValueError):
            continue
        s.dates.append(d)
        s.close.append(close)
        s.volume.append(volume)
    order = sorted(range(len(s.dates)), key=lambda i: s.dates[i])
    s.dates = [s.dates[i] for i in order]
    s.close = [s.close[i] for i in order]
    s.volume = [s.volume[i] for i in order]
    return s


def fetch(
    c: httpx.Client, key: str, symbol: str, cache: Path, refresh: bool = False
) -> Optional[Series]:
    """Full daily history for one symbol, cached. One request per symbol.

    `outputsize=full` is deliberate: the whole history arrives in a single
    request, so liquidity at *every* rebalance date costs one call rather than
    one call per date.
    """
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"{symbol}_daily.csv"
    if f.exists() and not refresh:
        return parse_daily(f.read_text(encoding="utf-8"), symbol)
    text = _get(
        c,
        {
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": "full",
            "datatype": "csv",
            "apikey": key,
        },
    )
    if not text or "timestamp" not in text[:200]:
        return None
    f.write_text(text, encoding="utf-8")
    return parse_daily(text, symbol)


def judge(
    series: Optional[Series],
    symbol: str,
    as_of: str,
    min_dollar_volume: float = MIN_DOLLAR_VOLUME,
    lookback: int = LOOKBACK_DAYS,
) -> Verdict:
    """Tradeable at ``as_of``? Quality first, then the floor.

    The ordering matters. Every quality check below returns ``usable=False``,
    which is a different outcome from failing the floor: one says the name was
    too illiquid to trade, the other says this provider cannot tell us. Only
    the first is evidence about the name.
    """
    if series is None or not series.dates:
        return Verdict(symbol, False, False, "no_data: provider returned no series")

    last = series.last_trade_on_or_before(as_of)
    if last is None:
        return Verdict(symbol, False, False, "no_data: no bars at or before as_of")
    stale = (
        datetime.strptime(as_of, "%Y-%m-%d").date()
        - datetime.strptime(last, "%Y-%m-%d").date()
    ).days
    if stale > STALE_DAYS:
        return Verdict(symbol, False, False, f"no_data: last bar {stale}d before as_of")

    close, vol = series.window(as_of, lookback)
    if close.size < MIN_OBSERVATIONS:
        return Verdict(
            symbol,
            False,
            False,
            f"no_data: {close.size} bars in window",
            None,
            close.size,
        )

    # The ADVM shape: a placeholder standing in for a delisted name. Neither
    # check can be omitted -- a halted name has volume and no price movement,
    # and a thin name has price movement and almost no volume.
    zero_frac = float(np.mean(vol <= 0))
    if zero_frac > MAX_ZERO_VOLUME_FRAC:
        return Verdict(
            symbol,
            False,
            False,
            f"no_data: {zero_frac:.0%} of window is zero volume",
            None,
            int(close.size),
        )
    flat_frac = float(np.mean(close == close[-1]))
    if flat_frac > MAX_FLAT_FRAC:
        return Verdict(
            symbol,
            False,
            False,
            f"no_data: {flat_frac:.0%} of window is one price",
            None,
            int(close.size),
        )

    dv = float(np.median(close * vol))
    ok = dv >= min_dollar_volume
    return Verdict(
        symbol,
        True,
        ok,
        ("clears" if ok else "below floor") + f": median ${dv:,.0f}/day",
        dv,
        int(close.size),
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="", help="comma-separated tickers")
    ap.add_argument("--symbols-file", default="", help="one ticker per line")
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--min-dollar-volume", type=float, default=MIN_DOLLAR_VOLUME)
    ap.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    ap.add_argument("--cache", default="data/equities/daily")
    ap.add_argument("--out", default="artifacts/equity_liquidity.json")
    ap.add_argument(
        "--require-coverage",
        type=float,
        default=0.0,
        help="exit non-zero if the judgeable fraction falls below this (0-1)",
    )
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.symbols_file:
        syms += [
            line.strip().upper()
            for line in Path(args.symbols_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    syms = sorted(set(syms))
    if not syms:
        print("no symbols given; use --symbols or --symbols-file", flush=True)
        return 2

    key = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()
    cache = Path(args.cache)
    verdicts: List[Verdict] = []
    with httpx.Client(timeout=90.0) as c:
        for i, sym in enumerate(syms, 1):
            f = cache / f"{sym}_daily.csv"
            if not f.exists() and not key:
                verdicts.append(
                    Verdict(sym, False, False, "no_data: not cached and no API key")
                )
                continue
            s = fetch(c, key, sym, cache, args.refresh)
            verdicts.append(
                judge(s, sym, args.as_of, args.min_dollar_volume, args.lookback_days)
            )
            if i % 25 == 0:
                print(f"  {i}/{len(syms)}", flush=True)

    usable = [v for v in verdicts if v.usable]
    passed = [v for v in usable if v.passed]
    coverage = len(usable) / len(verdicts) if verdicts else 0.0

    print(f"liquidity floor at {args.as_of}")
    print(
        f"  window {args.lookback_days}d, floor ${args.min_dollar_volume:,.0f}/day "
        f"median dollar volume"
    )
    print()
    print(f"{'symbol':<8}{'judgeable':>11}{'verdict':>10}  reason")
    print("-" * 72)
    for v in sorted(verdicts, key=lambda x: (not x.usable, not x.passed, x.symbol)):
        # An unjudgeable name gets "--", never "fail". Printing "fail" beside
        # a name the provider has no data for is the exact conflation this
        # script exists to prevent, and a reader scanning the column would
        # take it as a statement about the stock.
        verdict = ("pass" if v.passed else "fail") if v.usable else "--"
        print(
            f"{v.symbol:<8}{('yes' if v.usable else 'NO'):>11}{verdict:>10}  {v.reason}"
        )
    print()
    print(
        f"judgeable   {len(usable):>4} of {len(verdicts)}   ({coverage:.0%} coverage)"
    )
    print(f"cleared     {len(passed):>4} of {len(usable)} judgeable")
    print()

    if coverage < 1.0:
        print("The unjudgeable names are not a random sample: this provider does not")
        print("serve delisted history, so the hole is concentrated in exactly the")
        print("names that stopped trading. Screening on liquidity alone therefore")
        print("reintroduces survivorship unless the gap is closed by a provider that")
        print("carries dead names.")
        print()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "generated": datetime.now(timezone.utc).isoformat(),
                "as_of": args.as_of,
                "lookback_days": args.lookback_days,
                "min_dollar_volume": args.min_dollar_volume,
                "coverage": coverage,
                "judgeable": len(usable),
                "cleared": len(passed),
                "verdicts": [
                    {
                        "symbol": v.symbol,
                        "usable": v.usable,
                        "passed": v.passed,
                        "reason": v.reason,
                        "dollar_volume": v.dollar_volume,
                        "observations": v.observations,
                    }
                    for v in verdicts
                ],
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out}")

    if args.require_coverage and coverage < args.require_coverage:
        print(
            f"coverage {coverage:.0%} below required {args.require_coverage:.0%}",
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
