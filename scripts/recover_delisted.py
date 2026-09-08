#!/usr/bin/env python
"""Resolve the SPUS universe through three sources, and re-measure P3.

    python scripts/recover_delisted.py                 # resolution only, offline
    python scripts/recover_delisted.py --prices        # also fetch and price

P3 -- coverage of names that *left* the universe -- stood at 75.8% against an
80% floor, and the conclusion recorded at the time was that closing it needed a
paid security master. That was wrong, and this script is the correction
amendment D4 requires, reported with the before and after figures rather than
as a silent re-run.

The three sources are complementary rather than redundant, and each recovers a
population the others structurally cannot:

    OpenFIGI     CUSIP -> ticker, but with holes for live names
    SEC          current registrants, so never the acquired ones
    delisted     dead listings, so never the live ones

`docs/equity_listings.csv` -- the delisted half -- was already committed by
`scripts/equity_pit_universe.py` for a different purpose. No new data was
bought and, for the resolution step, no request is made: the cascade runs from
files already in the repository.

*The pricing step is where the quota goes.* Live names come from yfinance,
which is free and unmetered; dead ones from Alpha Vantage, whose free tier
allows 25 requests a day against roughly two per delisted name (a series and
its split history). The cache is per ticker, so an interrupted run resumes the
next day rather than starting over -- run this repeatedly until it stops
reporting failures.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, "src")

try:  # the entrypoint convention in `api/app.py` and `engine/main.py`
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dependency is in requirements.txt
    pass

from intradyne.research.delisted_names import (  # noqa: E402
    LISTINGS,
    by_symbol,
    load_delisted,
    recover as recover_delisted,
)
from intradyne.research.price_source import (  # noqa: E402
    CachedPrices,
    Resolution,
)
from intradyne.research.sec_names import (  # noqa: E402
    load_registry,
    recover as recover_sec,
)

TIMELINE = Path("docs/spus_universe_timeline.json")
FIGI_MAP = Path("docs/cusip_ticker_map.json")
OUT = Path("docs/resolved_ticker_map.json")
#: P3's floor, from `docs/SLOT_1_PREREGISTRATION.md` Amendment 1.
FLOOR = 0.80
#: How far back a reported position extends. An N-PORT as-of date states a
#: holding *on* that date, and 17 CUSIPs appear in exactly one quarter, so
#: their reported range is a single day.
#:
#: The fund's quarter ends land on Sundays and market holidays -- 2020-05-31
#: is a Sunday, 2021-05-31 is Memorial Day -- so a zero-width window asks for
#: prices on a day the market was shut and gets none. Before this padding
#: existed, ALK, BALL, BWA, LUV and Q were all scored unpriceable that way:
#: five live, liquid names that yfinance serves in full. Reading that as
#: absent data would have understated P3 by four points.
QUARTER = timedelta(days=91)


def holdings(
    timeline_path: Path = TIMELINE,
) -> Tuple[Dict[str, str], Dict[str, Tuple[str, str]], set, set]:
    """Names, holding windows, the full universe and the surviving subset.

    The window is (first, last) as-of date the fund reported the CUSIP. It is
    what makes a name match checkable against the calendar -- see the ticker
    reuse argument in `research/delisted_names.py`.
    """
    doc = json.loads(timeline_path.read_text(encoding="utf-8"))
    names: Dict[str, str] = {}
    held: Dict[str, Tuple[str, str]] = {}
    for as_of in sorted(doc):
        for h in doc[as_of].get("holdings", []):
            cusip = h.get("cusip")
            if not cusip:
                continue
            names.setdefault(cusip, h.get("name") or "")
            first, last = held.get(cusip, (as_of, as_of))
            held[cusip] = (min(first, as_of), max(last, as_of))
    final = sorted(doc)[-1]
    still = {h["cusip"] for h in doc[final].get("holdings", []) if h.get("cusip")}
    return names, held, set(names), still


def cascade(
    names: Dict[str, str], held: Dict[str, Tuple[str, str]]
) -> Tuple[Dict[str, Resolution], List[Tuple[str, Dict[str, Resolution]]]]:
    """Resolve every CUSIP, recording which source answered.

    Order matters and is not arbitrary: OpenFIGI carries the exchange-verified
    US composite line, SEC is authoritative for anything still registered, and
    the delisted record is consulted last because it is the only one whose
    matches need a time guard to be safe at all.
    """
    stages: List[Tuple[str, Dict[str, Resolution]]] = []

    figi_doc = json.loads(FIGI_MAP.read_text(encoding="utf-8"))
    figi = {
        c: Resolution(ticker=v["ticker"])
        for c, v in figi_doc.items()
        if isinstance(v, dict) and v.get("ticker")
    }
    stages.append(("openfigi", figi))
    resolved: Dict[str, Resolution] = dict(figi)

    rest = {c: names[c] for c in names if c not in resolved}
    sec = {
        c: Resolution(ticker=t) for c, t in recover_sec(rest, load_registry()).items()
    }
    stages.append(("+sec", sec))
    resolved.update(sec)

    rest = {c: names[c] for c in names if c not in resolved}
    dead = {
        c: Resolution(ticker=li.symbol, delisted=li.delisted)
        for c, li in recover_delisted(rest, load_delisted(LISTINGS), held).items()
    }
    stages.append(("+delisted", dead))
    resolved.update(dead)

    return annotate_liveness(resolved, held), stages


def annotate_liveness(
    resolved: Dict[str, Resolution], held: Dict[str, Tuple[str, str]]
) -> Dict[str, Resolution]:
    """Mark every resolution dead or alive, however its ticker was resolved.

    Without this the routing is wrong for the names that matter. OpenFIGI and
    SEC say what a CUSIP's ticker is and nothing about whether it still
    trades, so a name they resolve is sent to yfinance even when it delisted
    in 2021 -- and yfinance answers a dead ticker with an empty frame that
    reads as a network failure rather than as a wrong provider. That is a
    large part of how the tail measured 53.1%.

    Liveness is therefore looked up by symbol against the listing record,
    independently of the resolver, and still guarded by the holding window so
    a reused ticker's earlier death is not attributed to the later company.
    """
    index = by_symbol(LISTINGS)
    out: Dict[str, Resolution] = {}
    for cusip, res in resolved.items():
        if res.delisted is not None:
            out[cusip] = res
            continue
        window = held.get(cusip)
        dead = [
            li for li in index.get(res.ticker, ()) if window and li.overlaps(*window)
        ]
        out[cusip] = Resolution(res.ticker, delisted=dead[0].delisted) if dead else res
    return out


def price_tail(
    resolved: Dict[str, Resolution],
    universe: set,
    dropped: set,
    held: Dict[str, Tuple[str, str]],
    key: str,
) -> Tuple[set, CachedPrices]:
    """Fetch each resolved name and report which produced a usable series.

    A name counts as priced only if the provider returns closes *inside the
    window the fund held it*. A series that exists but stops before the fund
    bought the name prices nothing, and counting it would restate the same
    survivorship error P3 exists to catch, one level down.
    """
    prices = CachedPrices(resolved, api_key=key)
    priced: set = set()
    order = sorted(dropped & universe) + sorted(universe - dropped)
    for i, cusip in enumerate(order, 1):
        res = resolved.get(cusip)
        if res is None:
            continue
        first, last = held[cusip]
        got = prices.close_series(
            cusip,
            date.fromisoformat(first) - QUARTER,
            date.fromisoformat(last),
        )
        if got:
            priced.add(cusip)
        mark = "ok " if got else "-- "
        tag = "dead" if res.delisted else "live"
        print(
            f"  [{i:>3}/{len(order)}] {mark}{res.ticker:<8} {tag}  {len(got):>5} closes",
            flush=True,
        )
    return priced, prices


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices", action="store_true", help="fetch prices and score P3")
    ap.add_argument(
        "--full",
        action="store_true",
        help="price the whole universe, not only the dropped tail P3 scores",
    )
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    names, held, universe, still = holdings()
    dropped = universe - still
    print(
        f"universe {len(universe)} | still held {len(still)} | dropped {len(dropped)}"
    )
    print(f"P3 floor {FLOOR:.0%} on the dropped tail\n")

    resolved, stages = cascade(names, held)

    print("-- resolution --")
    run: Dict[str, Resolution] = {}
    for label, stage in stages:
        run.update(stage)
        tail = len(dropped & set(run))
        print(
            f"  {label:<11} universe {len(run):>3}/{len(universe)}"
            f" ({100 * len(run) / len(universe):5.1f}%)"
            f"   dropped tail {tail:>3}/{len(dropped)}"
            f" ({100 * tail / len(dropped):5.1f}%)"
        )

    newly = stages[-1][1]
    print(f"\n  recovered by the delisted record: {len(newly)}")
    for cusip, res in sorted(newly.items(), key=lambda kv: kv[1].ticker):
        print(f"    {res.ticker:<7} {res.delisted}  {names[cusip][:38]}")

    unresolved = sorted(dropped - set(resolved))
    print(f"\n  dropped names still unresolved: {len(unresolved)}")
    for cusip in unresolved:
        print(f"    {cusip}  {names[cusip]}")

    out = Path(args.out)
    out.write_text(
        json.dumps(
            {
                c: {"ticker": r.ticker, "delisted": r.delisted}
                for c, r in sorted(resolved.items())
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\n  wrote {out}")

    if not args.prices:
        print("\n-- P3 --")
        print("  resolution is a ceiling on coverage, not coverage itself.")
        print("  Re-run with --prices to score P3 on series actually returned.")
        return 0

    key = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()
    scope = universe if args.full else dropped
    # Names the delisted record routes to Alpha Vantage. Without a key they
    # cannot be fetched, and they are precisely the population P3 scores -- so
    # the run still prices the live half, and the verdict is withheld rather
    # than computed against a set that was never asked for.
    unfetchable = (
        {c for c in scope if (r := resolved.get(c)) and r.delisted} if not key else set()
    )
    if unfetchable:
        print("\nALPHAVANTAGE_API_KEY is not set; see .env.example")
        print(f"  {len(unfetchable)} delisted names cannot be fetched without it.")
        print("  Pricing the live half only; P3 will not be scored.")

    print("\n-- prices --")
    priced, prices = price_tail(resolved, scope - unfetchable, dropped, held, key)

    tail_priced = len(dropped & priced)
    print("\n-- P3 --")
    print(f"  dropped tail priced : {tail_priced}/{len(dropped)}")
    print(f"  requests spent      : {prices.requests_made}")
    if prices.splits_applied:
        for t, n in sorted(prices.splits_applied.items()):
            print(f"  split-adjusted      : {t} ({n} event(s))")
    else:
        print("  split-adjusted      : none needed inside any window")
    if prices.failures:
        print(f"  failures            : {len(prices.failures)}")
        for t, why in sorted(prices.failures.items())[:20]:
            print(f"    {t:<8} {why}")

    # A coverage figure computed over names that were never requested is not a
    # low score, it is a meaningless one -- the same refusal the horizon work
    # adopted after an empty table was reported as "0 of 8 passed".
    if unfetchable:
        print(
            f"\n  P3 NOT SCORED: {len(unfetchable)} of the {len(dropped)} dropped"
            " names were never requested."
        )
        print("  Set ALPHAVANTAGE_API_KEY and re-run; the cache keeps this run.")
        return 3

    coverage = tail_priced / len(dropped) if dropped else 0.0
    verdict = "PASSES" if coverage >= FLOOR else "FAILS"
    print(f"\n  P3 {verdict} at {100 * coverage:.1f}% against a {FLOOR:.0%} floor")
    if coverage < FLOOR and prices.failures:
        print("  Some failures are quota, not absence. Re-run tomorrow before")
        print("  reading this as a verdict -- the cache makes the re-run cheap.")
    return 0 if coverage >= FLOOR else 2


if __name__ == "__main__":
    raise SystemExit(main())
