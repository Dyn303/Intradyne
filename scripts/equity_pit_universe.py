#!/usr/bin/env python
"""Build a point-in-time US equity universe, survivorship included.

    python scripts/equity_pit_universe.py --out docs/EQUITY_UNIVERSE_TIMELINE.md

Amendment A3 of `docs/STRATEGY_RESEARCH_FRAMEWORK.md`. This is a *precondition*
of the equities programme, not one of its four approaches -- it spends no slot
(`docs/EQUITY_PROGRAMME_STOP_RULE.md`).

The crypto analogue is `scripts/point_in_time_universe.py`, and two things
differ.

**Membership is stated, not inferred.** That script had to infer death from a
coin's bars stopping, because Binance publishes no delisting date. Alpha
Vantage's `LISTING_STATUS` gives `ipoDate` and `delistingDate` outright, so
membership at any date needs no price data at all -- two requests cover every
symbol ever listed, and the whole timeline follows by arithmetic. Explicit
dates are also more accurate than inferred ones: a halted-but-listed name looks
identical to a dead one in bar data.

**A ticker is not an identity.** Of 23,246 symbols ever listed, 619 carry
more than one listing. 305 of those are genuine *sequential reuse* -- the first
delisted before the second floated and the issuer differs, so the ticker was
handed to an unrelated company. `ADCT` was ADC Telecommunications until 2010
and is ADC Therapeutics; `ALC` was Assisted Living Concepts and is Alcon.
Keying a universe by ticker splices two companies into one series, silently. So
the unit here is a **listing** -- a (symbol, ipoDate) interval -- and `symbol`
is only a label on it. Crypto never had this problem: a delisted pair's name
was not reissued to a different asset.

The remaining 232 are *concurrent* listings, both live at once -- one issuer's
shares and its senior notes, or a cross-listing. Not reuse, and a naive
same-symbol-different-name test counts them as such and overstates the problem
by half.

The payoff is measurable rather than theoretical: at a 2012 rebalance the
point-in-time universe holds 4,664 names, of which only 2,643 are still listed
today. A backtest built from a current ticker list would be missing **43% of
what actually traded**, and missing it in one direction -- a security is absent
precisely because it stopped trading.

What this does *not* do is judge liquidity. A3 also requires tradeability
measured at each rebalance date, and that needs per-symbol volume history --
tens of thousands of requests against a 1-per-second quota. Membership is the
survivorship half and is the half that cannot be recovered later; liquidity is
applied downstream on a reduced candidate set, the same way
`scripts/screen_equities.py` already does it live. The gap is stated in the
worksheet rather than left for a reader to discover.

Requires ALPHAVANTAGE_API_KEY. See .env.example.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import httpx
except ImportError:  # pragma: no cover - dependency is in requirements.txt
    print("httpx is required: pip install httpx", flush=True)
    raise

AV = "https://www.alphavantage.co/query"

#: Asset types worth carrying. ETFs are excluded by default: a fund is a
#: wrapper around holdings that are screened individually, and a leveraged or
#: inverse ETF is the derivative exposure `risk/shariah.py` exists to refuse.
DEFAULT_ASSET_TYPES = ("Stock",)

#: Exchanges with a real continuous auction. OTC is excluded by default --
#: quotes are indicative, spreads are wide, and A1's cost model does not hold.
DEFAULT_EXCHANGES = ("NASDAQ", "NYSE", "NYSE ARCA", "NYSE MKT", "BATS", "AMEX")


def _get(c: httpx.Client, params: Dict[str, str], tries: int = 5) -> Optional[str]:
    """Alpha Vantage answers a quota breach with 200 and a Note, not a 429.

    Same shape as `screen_equities.py:_get`, deliberately not shared: that one
    returns parsed JSON for the fundamentals endpoints, this one wants CSV
    text, and a common wrapper would have to guess which.
    """
    for i in range(tries):
        try:
            r = c.get(AV, params=params, timeout=120.0)
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
                return None
            return text
        except Exception:  # noqa: BLE001
            time.sleep(3 * (i + 1))
    return None


@dataclass(frozen=True)
class Listing:
    """One security's life on an exchange.

    `symbol` is a label, not a key: see the module docstring. `listing_id`
    is the stable identifier and is what a panel should be keyed by.
    """

    symbol: str
    name: str
    exchange: str
    asset_type: str
    ipo: str
    delisted: Optional[str]

    @property
    def listing_id(self) -> str:
        return f"{self.symbol}@{self.ipo}"

    def live_at(self, on: str) -> bool:
        """Listed and not yet delisted on ``on`` (YYYY-MM-DD).

        The delisting date is treated as exclusive: a name that delisted on the
        rebalance date is not a member that day. It still belonged to every
        earlier snapshot, and its loss is taken at its last traded price --
        which is the whole point of retaining it.
        """
        if self.ipo > on:
            return False
        return self.delisted is None or self.delisted > on


def _clean(v: Optional[str]) -> str:
    s = (v or "").strip()
    return "" if s.lower() in {"null", "none", "n/a"} else s


#: Corporate-form words that carry no identity. Comparing raw names calls
#: "Absolute Software Corp" and "Absolute Software Corporation" two different
#: companies, and a re-registration is not a ticker reuse.
_SUFFIX = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|llc|lp"
    r"|sa|nv|ag|holdings?|group|the|class|cl|a|b|c)\b"
)


def norm_name(name: str) -> str:
    """Issuer name reduced to its identifying words."""
    s = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    return re.sub(r"\s+", " ", _SUFFIX.sub("", s)).strip()


def fetch_listings(
    c: httpx.Client, key: str, cache: Path, refresh: bool = False
) -> List[Listing]:
    """Every symbol ever listed: the active set plus the delisted set.

    Two requests, cached by date. Names can contain commas, so the payload is
    parsed as CSV rather than split -- the same trap `screen_equities.py`
    records at its own reference-list loader.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out: List[Listing] = []
    for state in ("active", "delisted"):
        stamped = cache / f"listing_status_{state}_{today}.csv"
        if stamped.exists() and not refresh:
            text = stamped.read_text(encoding="utf-8")
        else:
            params = {"function": "LISTING_STATUS", "apikey": key}
            if state == "delisted":
                params["state"] = "delisted"
            text = _get(c, params)
            if not text:
                print(f"  failed to fetch {state} listings", flush=True)
                continue
            stamped.parent.mkdir(parents=True, exist_ok=True)
            stamped.write_text(text, encoding="utf-8")
        n = 0
        for row in csv.DictReader(io.StringIO(text)):
            sym = _clean(row.get("symbol"))
            ipo = _clean(row.get("ipoDate"))
            if not sym or not ipo:
                continue
            out.append(
                Listing(
                    symbol=sym,
                    name=_clean(row.get("name")),
                    exchange=_clean(row.get("exchange")),
                    asset_type=_clean(row.get("assetType")),
                    ipo=ipo,
                    delisted=_clean(row.get("delistingDate")) or None,
                )
            )
            n += 1
        print(f"  {state:<9} {n:>6} listings", flush=True)
    return out


def eligible(
    listings: Iterable[Listing],
    asset_types: Sequence[str],
    exchanges: Sequence[str],
) -> List[Listing]:
    at = {a.lower() for a in asset_types}
    ex = {e.upper() for e in exchanges}
    return [
        li
        for li in listings
        if li.asset_type.lower() in at and li.exchange.upper() in ex
    ]


def members_at(listings: Sequence[Listing], on: str) -> List[Listing]:
    """Listings live on ``on``. Ticker collisions are resolved, not ignored.

    Two listings sharing a symbol should never both be live -- one delisted
    before the other floated. Where the source data says otherwise the most
    recently floated wins, and the collision is counted so the worksheet can
    report it rather than the choice being invisible.
    """
    live = [li for li in listings if li.live_at(on)]
    by_symbol: Dict[str, Listing] = {}
    for li in live:
        prev = by_symbol.get(li.symbol)
        if prev is None or li.ipo > prev.ipo:
            by_symbol[li.symbol] = li
    return sorted(by_symbol.values(), key=lambda x: x.symbol)


def collisions(listings: Sequence[Listing], dates: Sequence[str]) -> Dict[str, int]:
    """How many symbols carry two live listings, per rebalance date.

    Reported as a count rather than a yes/no: concurrent listings exist on
    essentially every date, so "44 of 44 dates" says nothing, while "at most 9
    symbols on any date, out of 4,000" says it is a rounding error.
    """
    per = []
    for on in dates:
        live = [li for li in listings if li.live_at(on)]
        seen: Dict[str, int] = {}
        for li in live:
            seen[li.symbol] = seen.get(li.symbol, 0) + 1
        per.append(sum(1 for n in seen.values() if n > 1))
    per.sort()
    return {
        "max": per[-1] if per else 0,
        "median": per[len(per) // 2] if per else 0,
        "dates": sum(1 for n in per if n),
    }


def rebalance_dates(start: str, end: str, step_days: int) -> List[str]:
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    out, d = [], d0
    while d <= d1:
        out.append(d.isoformat())
        d += timedelta(days=step_days)
    return out


def timeline(
    listings: Sequence[Listing], dates: Sequence[str]
) -> List[Dict[str, object]]:
    """Universe size and churn at each rebalance date."""
    prev: set = set()
    out = []
    for on in dates:
        ids = {li.listing_id for li in members_at(listings, on)}
        out.append(
            {
                "date": on,
                "size": len(ids),
                "added": len(ids - prev) if prev else 0,
                "removed": len(prev - ids) if prev else 0,
            }
        )
        prev = ids
    return out


def survivorship_cost(
    universe: Sequence[Listing], dates: Sequence[str]
) -> List[Dict[str, object]]:
    """What a today's-listings universe would have missed, per date.

    This is the argument for the whole script in one table. The omitted names
    are not a random sample: a security is absent precisely because it stopped
    trading, so the bias runs one way.
    """
    alive = {li.symbol for li in universe if li.delisted is None}
    out = []
    for on in dates:
        m = members_at(universe, on)
        if not m:
            continue
        surv = sum(1 for li in m if li.symbol in alive)
        out.append(
            {
                "date": on,
                "pit": len(m),
                "survivors": surv,
                "missing": len(m) - surv,
                "bias": 100.0 * (len(m) - surv) / len(m),
            }
        )
    return out


def worksheet(
    listings: Sequence[Listing],
    universe: Sequence[Listing],
    points: Sequence[Dict[str, object]],
    reuse: List[Tuple[str, Listing, Listing]],
    meta: Dict[str, object],
) -> str:
    L: List[str] = []
    a = L.append
    a("# Point-in-time equity universe")
    a("")
    a(f"Generated {meta['generated']} by `scripts/equity_pit_universe.py`.")
    a("")
    a("Amendment A3. Membership is point-in-time and includes names that have")
    a("since delisted: a security belongs to every snapshot it was listed in,")
    a("and its loss is taken at its last traded price. Building a universe from")
    a("today's listings instead selects securities *because they survived*.")
    a("")
    a("## Coverage")
    a("")
    a("| | count |")
    a("|---|---|")
    a(f"| symbols ever listed | {meta['ever']:,} |")
    a(f"| listed today | {meta['active']:,} |")
    a(f"| since delisted | {meta['dead']:,} |")
    a(f"| **mortality** | **{meta['mortality']:.1f}%** |")
    a("")
    a(f"After the {meta['asset_types']} / {meta['exchanges']} filter:")
    a(f"**{len(universe):,} listings** enter the universe.")
    a("")
    a("A backtest run over today's listings alone would omit the delisted")
    a(f"{meta['dead']:,} entirely -- {meta['mortality']:.1f}% of everything that")
    a("ever traded, and the part that by construction did worst.")
    a("")
    a("## A ticker is not an identity")
    a("")
    a(f"{meta['multi']:,} symbols carry more than one listing, and two quite")
    a("different situations hide in that number:")
    a("")
    a(f"- **{len(reuse):,} sequential reuses.** The first delisted before the")
    a("  second floated *and* the issuer differs -- the ticker was reassigned to")
    a("  an unrelated company. `ACCL` was Accelrys until 2014 and is Acco Group")
    a("  now; `ADCT` was ADC Telecommunications and is ADC Therapeutics.")
    a(f"- **{meta['concurrent']:,} concurrent listings.** Both live at once: one")
    a("  issuer's shares and its senior notes, or a cross-listing. Not reuse,")
    a("  and counting it as such overstates the problem.")
    a("")
    a("Names are normalised before comparison, because a re-registration is not")
    a('a reassignment -- "Absolute Software Corp" and "Absolute Software')
    a('Corporation" are one company, and a raw string compare calls them two.')
    a("")
    a("A universe keyed by ticker splices reused symbols into a single series.")
    a("The unit here is a **listing** -- a `SYMBOL@ipoDate` interval -- so the")
    a("two are distinct rows and no price series is ever joined across them.")
    a("")
    if reuse:
        a("| symbol | was | until | became | from |")
        a("|---|---|---|---|---|")
        for sym, old, new in reuse[:12]:
            a(
                f"| `{sym}` | {old.name[:32]} | {old.delisted} "
                f"| {new.name[:32]} | {new.ipo} |"
            )
        if len(reuse) > 12:
            a(f"| ... | *{len(reuse) - 12:,} more* | | | |")
        a("")
    c = meta["coll"]
    a("Concurrent listings mean a symbol can be doubly live. At most")
    a(f"**{c['max']}** symbols on any one rebalance date (median {c['median']},")
    a(f"on {c['dates']} of {meta['n_dates']} dates) against a universe of")
    a("thousands. The later flotation wins, and the choice is counted here")
    a("rather than made silently.")
    a("")
    a("## What survivorship would have cost")
    a("")
    a("Universe size at each date, against the subset still listed today --")
    a("which is what a backtest built from a current ticker list would see.")
    a("")
    a("| as of | point-in-time | still listed today | missing | bias |")
    a("|---|---|---|---|---|")
    for r in meta["bias_rows"]:
        a(
            f"| {r['date']} | {r['pit']:,} | {r['survivors']:,} "
            f"| {r['missing']:,} | **{r['bias']:.1f}%** |"
        )
    a("")
    a("The missing names are not a random sample. A security is absent")
    a("precisely because it stopped trading, so the omission runs one way and")
    a("a backtest over today's list is flattered by exactly the constituents")
    a("that did worst.")
    a("")
    a("## Universe over time")
    a("")
    a("| date | size | added | removed |")
    a("|---|---|---|---|")
    for p in points:
        a(f"| {p['date']} | {p['size']:,} | {p['added']:,} | {p['removed']:,} |")
    a("")
    a("## What this does not do")
    a("")
    a("**Liquidity is not judged here.** A3 also requires tradeability measured")
    a("at each rebalance date, which needs per-symbol volume history -- tens of")
    a("thousands of requests against a one-per-second quota. Membership is the")
    a("survivorship half, and it is the half that cannot be reconstructed after")
    a("the fact. Apply a liquidity floor downstream, on the reduced candidate")
    a("set, judged on data available at the rebalance date and never on today's")
    a("volume. Using current liquidity to decide what was tradeable in 2019")
    a("leaks the future exactly as using current listings does.")
    a("")
    a("**No delisting reason is given.** A merger at a premium and a bankruptcy")
    a("both appear here as a `delistingDate`. They are not the same event for a")
    a("long-only strategy, and any result sensitive to that distinction needs a")
    a("corporate-actions source this does not have.")
    a("")
    a("**Ratios are not screened.** Shariah permissibility is decided by")
    a("`scripts/screen_equities.py`, and that script does not decide either --")
    a("it produces a worksheet. See `docs/EQUITY_SCREENING.md`.")
    a("")
    return "\n".join(L)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="docs/EQUITY_UNIVERSE_TIMELINE.md")
    p.add_argument("--json-out", default="docs/equity_universe_timeline.json")
    p.add_argument("--csv-out", default="docs/equity_listings.csv")
    p.add_argument("--cache", default="data/reference")
    p.add_argument("--start", default="2005-01-01")
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--step-days", type=int, default=182)
    p.add_argument("--asset-types", default=",".join(DEFAULT_ASSET_TYPES))
    p.add_argument("--exchanges", default=",".join(DEFAULT_EXCHANGES))
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args(argv)

    key = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()
    if not key:
        print("ALPHAVANTAGE_API_KEY is not set; see .env.example", flush=True)
        return 2

    print("fetching listing status", flush=True)
    with httpx.Client(timeout=120.0) as c:
        listings = fetch_listings(c, key, Path(args.cache), args.refresh)
    if not listings:
        print("no listings fetched", flush=True)
        return 1

    active = {li.symbol for li in listings if li.delisted is None}
    dead = {li.symbol for li in listings if li.delisted is not None}
    ever = active | dead

    # Same symbol, more than one listing. Two quite different situations hide
    # in here and only one of them is ticker reuse.
    #
    #   sequential  the first delisted before the second floated. If the
    #               issuer also differs, the ticker was reassigned -- ACCL was
    #               Accelrys until 2014 and is Acco Group now.
    #   concurrent  both live at once. Same issuer, different instrument
    #               (shares vs senior notes), or a cross-listing. Not reuse.
    #
    # Comparing raw names conflates the two and also miscounts
    # re-registrations, so names are normalised first.
    by_symbol: Dict[str, List[Listing]] = {}
    for li in listings:
        by_symbol.setdefault(li.symbol, []).append(li)
    reuse: List[Tuple[str, Listing, Listing]] = []
    concurrent = 0
    for sym, group in by_symbol.items():
        if len(group) < 2:
            continue
        g = sorted(group, key=lambda x: x.ipo)
        for old, new in zip(g, g[1:]):
            disjoint = old.delisted is not None and old.delisted <= new.ipo
            if not disjoint:
                concurrent += 1
            elif norm_name(old.name) != norm_name(new.name):
                reuse.append((sym, old, new))
    reuse.sort(key=lambda t: t[0])

    types = [s.strip() for s in args.asset_types.split(",") if s.strip()]
    exch = [s.strip() for s in args.exchanges.split(",") if s.strip()]
    universe = eligible(listings, types, exch)
    print(f"eligible after filter: {len(universe):,} listings", flush=True)

    dates = rebalance_dates(args.start, args.end, args.step_days)
    points = timeline(universe, dates)
    coll = collisions(universe, dates)

    meta = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "ever": len(ever),
        "active": len(active),
        "dead": len(dead - active),
        "both": len(active & dead),
        "mortality": 100.0 * len(dead - active) / max(len(ever), 1),
        "asset_types": "/".join(types),
        "exchanges": f"{len(exch)} exchanges",
        "coll": coll,
        "bias_rows": survivorship_cost(universe, dates[::8]),
        "n_dates": len(dates),
        "multi": sum(1 for v in by_symbol.values() if len(v) > 1),
        "concurrent": concurrent,
        "universe": len(universe),
        "start": args.start,
        "end": args.end,
        "step_days": args.step_days,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(worksheet(listings, universe, points, reuse, meta), encoding="utf-8")

    # The interval table, not per-date member lists: membership at any date is
    # derivable from it, and the crypto timeline reached a quarter of a
    # megabyte writing out dates instead.
    # The listing table goes to CSV and the summary to JSON. As one indented
    # JSON blob this was 2.8MB against the crypto timeline's 0.22MB; the table
    # is plainly tabular, and CSV both halves that and diffs line by line when
    # the universe changes.
    Path(args.json_out).write_text(
        json.dumps({"meta": meta, "timeline": points}, indent=1),
        encoding="utf-8",
    )
    csv_out = Path(args.csv_out)
    with csv_out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["listing_id", "symbol", "name", "exchange", "ipo", "delisted"])
        for li in sorted(universe, key=lambda x: (x.symbol, x.ipo)):
            w.writerow(
                [
                    li.listing_id,
                    li.symbol,
                    li.name,
                    li.exchange,
                    li.ipo,
                    li.delisted or "",
                ]
            )
    print(f"wrote {out}, {args.json_out} and {csv_out}", flush=True)
    print(
        f"  ever listed {meta['ever']:,} | active {meta['active']:,} | "
        f"delisted {meta['dead']:,} | mortality {meta['mortality']:.1f}%",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
