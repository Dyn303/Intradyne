#!/usr/bin/env python
"""Verify the Alpha Vantage free-tier assumptions the delisted path rests on.

    python scripts/av_tier_probe.py
    python scripts/av_tier_probe.py --dead ABMD --live AAPL --sleep 2

`price_source` encodes three beliefs about what the free tier serves, and the
whole question of whether the dropped tail is priceable turns on them:

* `TIME_SERIES_DAILY` with `outputsize=full` is **premium** and returns nothing
* `outputsize=compact` returns `COMPACT_SESSIONS` daily bars
* weekly and monthly take no `outputsize` and return a delisted name's full life

Those are comments. Comments do not fail when a provider changes its plans, and
this project has been bitten by a claim that looked settled: #76 asserted the
free tier served delisted history, true only because the evidence came from MCP
calls that had quietly used `compact` where the fetcher used `full`. The
correction cost a run to find.

So this probe reads the constants **out of the module** rather than restating
them, and checks them against the live API. If someone changes
`COMPACT_SESSIONS` or adds a frequency, the probe checks the new value. If
Alpha Vantage moves `TIME_SERIES_WEEKLY` behind the paywall, this is what says
so, rather than a research run failing weeks later for reasons nobody can place.

*Throttling and paywalling are not the same answer, and look identical.* Both
arrive as HTTP 200 carrying an `Information` notice. A probe that cannot tell
them apart reports "premium, as expected" for a request that was merely too
fast -- a false pass on the exact question it exists to answer. So notices are
classified, and a throttled check is reported **inconclusive** rather than
scored either way: the run is worth repeating, not believing.

The first draft of this script had that bug, and also fired requests
back-to-back. `--sleep` defaults above the documented one-per-second.

*A delisted symbol is the case that matters.* A live ticker answers on any
plan; the population at issue is names that stopped trading, and those are what
a tier change would silently strand. The live control is what separates "dead
names are now unreachable" from "this key is exhausted".

Costs six requests of the free tier's 25 per day. Exits non-zero when an
assumption is contradicted; exits 2 when the answer could not be established.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
from dotenv import find_dotenv, load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from intradyne.research.price_source import (  # noqa: E402
    AV,
    AV_FUNCTION,
    COMPACT_SESSIONS,
)

#: Phrases Alpha Vantage uses when it is throttling rather than upselling.
#: Checked before the premium markers because the daily-limit notice mentions
#: premium plans too, and reading that as "this endpoint is paid" is precisely
#: the false pass this classification exists to prevent.
THROTTLE_MARKERS = (
    "spreading out",
    "per second",
    "requests per day",
    "requests/day",
    "rate limit",
    "call frequency",
)

#: Phrases that mean the endpoint or parameter itself is paid.
PREMIUM_MARKERS = ("premium endpoint", "subscribe to any of the premium")

SERIES = "series"
THROTTLED = "throttled"
PREMIUM = "premium"
OTHER = "other"


def classify(body: str) -> str:
    """What the payload is. Status codes are never the signal here.

    A series starts with its header row -- the same test `price_source._av`
    applies, kept identical so the probe cannot pass while production fails.
    """
    if body[:80].lower().startswith(("timestamp,", "effective_date,")):
        return SERIES
    low = body.lower()
    if any(m in low for m in THROTTLE_MARKERS):
        return THROTTLED
    if any(m in low for m in PREMIUM_MARKERS):
        return PREMIUM
    return OTHER


def fetch(key: str, params: Dict[str, str]) -> Tuple[str, str, int]:
    """(kind, detail, rows). Never raises, never prints or logs the key."""
    query = dict(params)
    query["apikey"] = key
    query.setdefault("datatype", "csv")
    try:
        r = httpx.get(AV, params=query, timeout=60.0)
    except Exception as exc:  # noqa: BLE001
        return OTHER, f"request failed: {type(exc).__name__}", 0
    if r.status_code != 200:
        return OTHER, f"HTTP {r.status_code}", 0
    body = r.text.strip()
    kind = classify(body)
    if kind == SERIES:
        return SERIES, "series", sum(1 for _ in csv.DictReader(io.StringIO(body)))
    # The notice text is the only useful diagnostic and is safe to show; the
    # key travels in the URL, so the URL is never printed.
    return kind, " ".join(body.split())[:130], 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", default="AAPL", help="a currently listed control")
    ap.add_argument("--dead", default="ABMD", help="a delisted name (the real case)")
    ap.add_argument(
        "--sleep",
        type=float,
        default=1.5,
        help="seconds between requests; the documented ceiling is one per second",
    )
    args = ap.parse_args(argv)

    load_dotenv(find_dotenv(usecwd=True))
    key = (os.getenv("ALPHAVANTAGE_API_KEY") or "").strip()
    if not key:
        print("ALPHAVANTAGE_API_KEY is not set; see .env.example")
        return 2

    broken: List[str] = []
    unknown: List[str] = []
    spent = 0

    def probe(label: str, params: Dict[str, str], want: str, ok_rows) -> None:
        nonlocal spent
        time.sleep(args.sleep)
        kind, detail, rows = fetch(key, params)
        spent += 1

        if kind == THROTTLED:
            print(f"  ??   {label:<44} throttled -- inconclusive")
            unknown.append(label)
            return
        if want == SERIES:
            passed = kind == SERIES and ok_rows(rows)
            got = f"{rows} rows" if kind == SERIES else f"{kind}: {detail}"
        else:  # we expect a refusal
            passed = kind == PREMIUM
            got = "refused as premium" if passed else f"{kind}: {detail[:60]}"
        print(f"  {'ok  ' if passed else 'FAIL'} {label:<44} {got}")
        if not passed:
            broken.append(f"{label}: {got}")

    print(f"probing with the configured key ({len(key)} chars)")
    print(f"{args.sleep}s between requests\n")

    print("daily -- the capped endpoint")
    probe(
        "outputsize=full is premium",
        {"function": "TIME_SERIES_DAILY", "symbol": args.dead, "outputsize": "full"},
        PREMIUM,
        None,
    )
    probe(
        f"outputsize=compact gives ~{COMPACT_SESSIONS} sessions",
        {"function": "TIME_SERIES_DAILY", "symbol": args.dead, "outputsize": "compact"},
        SERIES,
        lambda n: abs(n - COMPACT_SESSIONS) <= 5,
    )

    print("\nweekly and monthly -- no outputsize, full history")
    for freq in ("weekly", "monthly"):
        probe(
            f"{AV_FUNCTION[freq]} serves delisted {args.dead}",
            {"function": AV_FUNCTION[freq], "symbol": args.dead},
            SERIES,
            lambda n: n > COMPACT_SESSIONS,
        )

    print("\nlive control -- a tier change, or just an exhausted key?")
    for freq in ("weekly", "monthly"):
        probe(
            f"{AV_FUNCTION[freq]} serves listed {args.live}",
            {"function": AV_FUNCTION[freq], "symbol": args.live},
            SERIES,
            lambda n: n > 0,
        )

    print(f"\n{spent} requests spent of the free tier's daily allowance")

    if broken:
        print(f"\n{len(broken)} assumption(s) contradicted:")
        for b in broken:
            print(f"  - {b}")
        print(
            "\nprice_source documents the delisted-pricing path against these;\n"
            "update the code and its comments together."
        )
        return 1
    if unknown:
        print(f"\n{len(unknown)} check(s) inconclusive -- throttled, not answered:")
        for u in unknown:
            print(f"  - {u}")
        print("\nRe-run with a larger --sleep, or tomorrow if the daily cap is spent.")
        return 2
    print("all assumptions hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
