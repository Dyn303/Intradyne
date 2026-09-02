#!/usr/bin/env python
"""Build a Shariah-screening worksheet for US equities from the daily movers.

    python scripts/screen_equities.py --out docs/EQUITY_SCREENING.md

**This script does not decide what is permissible.** It reports what each
ticker is and which categories raise a question. Which categories pass is a
scholarly ruling; this is an input to that, never a substitute. The posture is
copied deliberately from `scripts/build_universe.py`, which does the same job
for crypto.

Three screens feed it -- top gainers, top losers and most actively traded --
and the first thing measured about them was that **most of the movers are
derivatives**. In the response this was written against, 14 of 20 top gainers
and 16 of 20 top losers were warrants or rights; most-active carried no
warrants but did carry leveraged and inverse ETFs (TSLL 2x, NVD inverse, SOXL
3x, BITO bitcoin futures). Screening on price and volume alone produces a
majority-impermissible list, so instrument type is filtered first.

*How instrument type is decided.* Not by ticker suffix. A rule rejecting
tickers ending in W or R would throw away LOW, AMCR and BKR, which are ordinary
common stock. `LISTING_STATUS` returns `name` and `assetType` for every listed
US ticker in a single call, and the **name** is what carries the truth:
`assetType` is "Stock" even for `AACIW`, whose name reads "Armada Acquisition
Corp I - Warrants (13/08/2026)". So the reference list is fetched once, cached
for the day, and matched on name.

*Why the tier order.* Tiers 0-2 need no per-ticker request -- price and volume
arrive with the movers response, and instrument type comes from the cached
reference list. Only survivors cost an API call. On the observed data that is
roughly ten tickers rather than fifty-six, which is the main defence against
the rate limit, worth more than any amount of backoff tuning.

*What the flags mean.* A ticker carries **every** flag that applies rather than
one label, and a flag is a question raised, not a verdict. Matching is on whole
words from the sector and industry strings, never substrings: substring
matching is what once put Bitcoin in "DEX" via `dex` inside "Index", and the
equivalent here would put any "GAMING" industry name into gambling regardless
of whether it makes video games or runs a casino.

Thresholds and the excluded-activity list are **configuration, not code**.
AAOIFI, DJIM, S&P and MSCI screens differ in both the ratios and the
denominator, and choosing between them is a scholarly question. Every record
names the standard and the thresholds that produced it, so a worksheet can
never be read without knowing what it was screened against.

Requires ALPHAVANTAGE_API_KEY. See .env.example.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

AV = "https://www.alphavantage.co/query"

#: Words in a listing's *name* that mean the instrument is not common equity.
#: Matched as whole words against the upper-cased name. These are structural
#: exclusions rather than flags: `src/intradyne/risk/shariah.py` already
#: refuses derivatives outright, so there is no ruling left to make.
NOT_COMMON_EQUITY: List[Tuple[str, Set[str], str]] = [
    (
        "WARRANT",
        {"WARRANT", "WARRANTS", "WT", "WTS"},
        "a warrant is an option on the underlying",
    ),
    ("RIGHT", {"RIGHT", "RIGHTS", "RT", "RTS"}, "a subscription right is an option"),
    ("UNIT", {"UNIT", "UNITS"}, "a SPAC unit bundles shares with warrants"),
    (
        "PREFERRED",
        {"PREFERRED", "PFD", "DEPOSITARY"},
        "preferred lines carry a fixed coupon",
    ),
    ("NOTE", {"NOTE", "NOTES", "DEBENTURE", "BOND"}, "a debt instrument, not equity"),
    (
        "WHEN_ISSUED",
        {"ISSUED"},
        "a when-issued line is a forward on an unsettled share",
    ),
]

#: Markers in an ETF's name that mean it is leveraged, inverse or derivative
#: backed. Whole-word matched, same discipline.
DERIVATIVE_FUND: Set[str] = {
    "2X",
    "3X",
    "1.5X",
    "-1X",
    "-2X",
    "-3X",
    "BULL",
    "BEAR",
    "ULTRA",
    "ULTRASHORT",
    "INVERSE",
    "SHORT",
    "LEVERAGED",
    "DAILY",
    "FUTURES",
    "SWAP",
    "COVERED",
    "WEEKLYPAY",
    "BUFFER",
    "BUFFERED",
    "OPTION",
    "OPTIONS",
    "PREMIUM",
    "YIELDMAX",
}

#: Business activities that raise a screening question, with the reason. A
#: ticker may match several; all of them are reported. Matched as whole words
#: against the sector and industry strings.
FLAGS: List[Tuple[str, Set[str], str]] = [
    (
        "RIBA",
        {
            "BANK",
            "BANKS",
            "BANKING",
            "CREDIT",
            "LENDING",
            "LOAN",
            "LOANS",
            "MORTGAGE",
            "FINANCE",
            "FINANCIAL",
            "SAVINGS",
            "THRIFT",
        },
        "conventional lending or deposit-taking earns interest",
    ),
    (
        "INSURANCE",
        {"INSURANCE", "INSURERS", "REINSURANCE", "TITLE", "SURETY"},
        "conventional insurance carries gharar and invested float",
    ),
    (
        "BROKER",
        {
            "BROKER",
            "BROKERS",
            "BROKERAGE",
            "DEALERS",
            "EXCHANGES",
            "INVESTMENT",
            "ASSET",
            "SECURITY",
        },
        "revenue may include margin lending and derivatives",
    ),
    (
        "ALCOHOL",
        {
            "BEVERAGES",
            "BREWERS",
            "DISTILLERS",
            "WINERIES",
            "MALT",
            "LIQUOR",
            "WINE",
            "BEER",
        },
        "beverage producers may or may not be alcoholic -- resolve by hand",
    ),
    (
        "TOBACCO",
        {"TOBACCO", "CIGARETTES", "CIGAR", "VAPOR", "NICOTINE"},
        "tobacco is an excluded activity under every standard",
    ),
    (
        "GAMBLE",
        {"CASINO", "CASINOS", "GAMBLING", "BETTING", "LOTTERY", "WAGERING", "RACING"},
        "maysir",
    ),
    ("ADULT", {"ADULT", "EROTIC"}, "excluded activity"),
    (
        "PORK",
        {"PORK", "SWINE", "HOGS", "MEAT", "MEATS"},
        "meat producers may handle non-halal product -- resolve by hand",
    ),
    (
        "WEAPONS",
        {"ORDNANCE", "WEAPONS", "ARMS", "AMMUNITION", "DEFENSE"},
        "some standards exclude armaments",
    ),
    (
        "SHELL",
        {"BLANK", "CHECKS", "ACQUISITION"},
        "a blank-cheque company has no underlying activity yet",
    ),
    (
        "HOTEL",
        {"HOTELS", "MOTELS", "RESORTS", "CASINO-HOTELS", "CRUISE"},
        "hospitality revenue commonly includes alcohol",
    ),
    (
        "ENTERTAIN",
        {"ENTERTAINMENT", "MOTION", "PICTURE", "BROADCASTING", "MEDIA", "CABLE"},
        "some standards screen entertainment content",
    ),
]

#: Default ratio limits. These are the widely-cited AAOIFI-style thresholds
#: with market capitalisation as the denominator. They are defaults, not a
#: ruling -- override them to match whichever standard applies.
DEFAULT_STANDARD = "AAOIFI-style (market-cap denominator)"
DEFAULT_MAX_DEBT_RATIO = 0.30
DEFAULT_MAX_LIQUID_RATIO = 0.30


def _get(c: httpx.Client, params: Dict[str, str], tries: int = 5) -> Optional[Any]:
    """Alpha Vantage answers a quota breach with 200 and a Note, not a 429."""
    for i in range(tries):
        try:
            r = c.get(AV, params=params)
            if r.status_code == 429:
                time.sleep(20)
                continue
            if r.status_code != 200:
                return None
            text = r.text
            # A quota breach arrives as 200 with "Note"/"Information" JSON.
            if text.lstrip().startswith("{"):
                body = r.json()
                if "Note" in body or "Information" in body:
                    time.sleep(20)
                    continue
                return body
            return text
        except Exception:  # noqa: BLE001
            time.sleep(3 * (i + 1))
    return None


def words(*parts: str) -> Set[str]:
    """Whole words from free text, upper-cased, punctuation stripped.

    Whole-word matching is the point. Substrings put Bitcoin in "DEX" in the
    crypto worksheet, and here would put every "GAMING" industry into
    gambling regardless of whether it is a casino or a video-game studio.
    """
    out: Set[str] = set()
    for p in parts:
        if not p:
            continue
        token = ""
        for ch in p.upper():
            if ch.isalnum() or ch in ".-":
                token += ch
            else:
                if token:
                    out.add(token.strip(".-"))
                token = ""
        if token:
            out.add(token.strip(".-"))
    return {w for w in out if w}


def reference_list(c: httpx.Client, key: str, cache: Path) -> Dict[str, Dict[str, str]]:
    """Ticker -> {name, assetType, exchange}, for every listed US security.

    One request covers ~14k tickers, so this is cached for the day and is the
    reason instrument-type filtering costs nothing per candidate.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stamped = cache / f"listing_status_{today}.csv"
    if stamped.exists():
        text = stamped.read_text(encoding="utf-8")
    else:
        text = _get(c, {"function": "LISTING_STATUS", "apikey": key})
        if not isinstance(text, str):
            return {}
        stamped.parent.mkdir(parents=True, exist_ok=True)
        stamped.write_text(text, encoding="utf-8")

    out: Dict[str, Dict[str, str]] = {}
    # Names contain commas ("Company, Inc."), so this must be parsed as CSV.
    for row in csv.DictReader(io.StringIO(text)):
        sym = (row.get("symbol") or "").strip()
        if sym:
            out[sym] = {
                "name": (row.get("name") or "").strip(),
                "assetType": (row.get("assetType") or "").strip(),
                "exchange": (row.get("exchange") or "").strip(),
            }
    return out


def movers(c: httpx.Client, key: str) -> List[Dict[str, Any]]:
    """The three screens, in one request. Duplicates across lists are merged."""
    body = _get(c, {"function": "TOP_GAINERS_LOSERS", "apikey": key})
    if not isinstance(body, dict):
        return []
    seen: Dict[str, Dict[str, Any]] = {}
    for screen, field in (
        ("gainer", "top_gainers"),
        ("loser", "top_losers"),
        ("active", "most_actively_traded"),
    ):
        for r in body.get(field, []) or []:
            sym = (r.get("ticker") or "").strip()
            if not sym:
                continue
            rec = seen.setdefault(
                sym,
                {
                    "symbol": sym,
                    "screens": [],
                    "price": _f(r.get("price")),
                    "change_pct": _f((r.get("change_percentage") or "").rstrip("%")),
                    "volume": _f(r.get("volume")),
                },
            )
            rec["screens"].append(screen)
    return sorted(seen.values(), key=lambda r: r["symbol"])


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _norm(s: str) -> str:
    return "".join(ch for ch in s.upper() if ch.isalnum())


def instrument_type(sym: str, ref: Dict[str, Dict[str, str]]) -> Tuple[str, str]:
    """(verdict, reason). Structural, and decided from the listing *name*.

    `assetType` is "Stock" even for warrants -- AACIW is typed "Stock" and
    named "Armada Acquisition Corp I - Warrants". So the name decides, which
    also means LOW and BKR are not thrown away by a suffix rule.

    Some secondary lines carry no marker at all: RDACR is a rights line named
    plainly "Rising Dragon Acquisition Corp". Those are caught by comparing
    against the root ticker -- RDACR against RDAC, same issuer name, so the
    longer ticker is the derivative line. The comparison is what makes this
    safe: AMCR's root AMC is "AMC Entertainment", CR's root C is "Citigroup",
    NVDA's root NVD is a 2x short fund, and none of them match.
    """
    meta = ref.get(sym)
    if meta is None:
        return "unknown", "not in the listing reference"
    name_words = words(meta["name"])
    for code, keys, why in NOT_COMMON_EQUITY:
        if name_words & keys:
            return "excluded", f"{code}: {why}"

    if len(sym) > 1 and sym[-1] in "WRUP":
        root = ref.get(sym[:-1])
        if root and _norm(root["name"]) == _norm(meta["name"]):
            return (
                "excluded",
                f"SECONDARY: same issuer name as {sym[:-1]}, so this is a "
                "warrant, right or unit line",
            )

    # Leverage markers are checked whatever the listing calls the instrument.
    # SNXX is "Tradr 2X Long SNDK Daily" and is typed "Stock", not "ETF", so
    # gating this on assetType let a 2x product through as common equity.
    hit = name_words & DERIVATIVE_FUND
    if hit:
        return (
            "excluded",
            f"DERIVATIVE: leveraged or derivative-backed ({'/'.join(sorted(hit))})",
        )
    if meta["assetType"].upper() == "ETF":
        return "fund", "a plain fund -- holdings need screening, not just the wrapper"
    return "common", "common equity"


def overview(
    c: httpx.Client, key: str, sym: str, cache: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    hit = cache.get(sym, {}).get("overview")
    if hit:
        return hit
    body = _get(c, {"function": "COMPANY_OVERVIEW", "symbol": sym, "apikey": key})
    if not isinstance(body, dict) or not body.get("Symbol"):
        return None
    cache.setdefault(sym, {})["overview"] = body
    return body


def balance_sheet(
    c: httpx.Client, key: str, sym: str, cache: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    hit = cache.get(sym, {}).get("balance")
    if hit:
        return hit
    body = _get(c, {"function": "BALANCE_SHEET", "symbol": sym, "apikey": key})
    if not isinstance(body, dict):
        return None
    reports = body.get("quarterlyReports") or body.get("annualReports") or []
    if not reports:
        return None
    cache.setdefault(sym, {})["balance"] = reports[0]
    return reports[0]


def _n(v: Any) -> Optional[float]:
    if v in (None, "None", "", "-"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def screen_one(
    c: httpx.Client,
    key: str,
    rec: Dict[str, Any],
    cache: Dict[str, Any],
    max_debt: float,
    max_liquid: float,
) -> Dict[str, Any]:
    """Tiers 3 and 4 for one surviving candidate."""
    sym = rec["symbol"]
    ov = overview(c, key, sym, cache)
    if ov is None:
        rec["known"] = False
        rec["flags"] = []
        rec["reason"] = "no company data returned"
        return rec

    rec["name"] = ov.get("Name", "")
    rec["sector"] = ov.get("Sector", "")
    rec["industry"] = ov.get("Industry", "")
    rec["as_of"] = ov.get("LatestQuarter", "")
    mcap = _n(ov.get("MarketCapitalization"))
    rec["market_cap"] = mcap

    tags = words(rec["sector"], rec["industry"])
    rec["flags"] = [code for code, keys, _ in FLAGS if tags & keys]
    # No sector or industry at all is not the same as clean.
    rec["known"] = bool(tags)

    bs = balance_sheet(c, key, sym, cache)
    if bs is None or not mcap:
        rec["ratios"] = None
        rec["ratio_flags"] = ["NO_DATA"]
        return rec

    debt = _n(bs.get("shortLongTermDebtTotal")) or 0.0
    liquid = _n(bs.get("cashAndShortTermInvestments")) or 0.0
    ratios = {"debt_over_mcap": debt / mcap, "liquid_over_mcap": liquid / mcap}
    rec["ratios"] = ratios
    rec["ratio_as_of"] = bs.get("fiscalDateEnding", "")

    breaches = []
    if ratios["debt_over_mcap"] > max_debt:
        breaches.append("DEBT")
    if ratios["liquid_over_mcap"] > max_liquid:
        breaches.append("LIQUID")
    rec["ratio_flags"] = breaches
    return rec


def worksheet(rows: List[Dict[str, Any]], meta: Dict[str, Any]) -> str:
    """Markdown, grouped so the compliance decision reads off a prepared list."""
    clean = [
        r
        for r in rows
        if r.get("known") and not r.get("flags") and not r.get("ratio_flags")
    ]
    flagged = [
        r for r in rows if r.get("known") and (r.get("flags") or r.get("ratio_flags"))
    ]
    unknown = [r for r in rows if not r.get("known")]

    L: List[str] = []
    L.append("# US equity screening worksheet")
    L.append("")
    L.append(
        f"Screened {meta['screened']} of {meta['candidates']} movers on "
        f"{meta['date']}, against **{meta['standard']}** "
        f"(debt/mcap <= {meta['max_debt']:.0%}, liquid/mcap <= {meta['max_liquid']:.0%})."
    )
    L.append("")
    L.append(
        "**This worksheet does not decide what is permissible.** It reports what "
        "each ticker is and which categories raise a question. Which categories "
        "pass is a scholarly ruling."
    )
    L.append("")
    L.append(
        "It is also a **live screen, not a research universe**. These names were "
        "selected by what already moved today. Using them to define a backtest "
        "universe is selection on the outcome -- see Amendment A3 in "
        "`docs/STRATEGY_RESEARCH_FRAMEWORK.md`."
    )
    L.append("")
    L.append("| bucket | count |")
    L.append("|---|---|")
    L.append(f"| no flag raised | {len(clean)} |")
    L.append(f"| unknown -- resolve by hand | {len(unknown)} |")
    L.append(f"| flagged -- needs a ruling | {len(flagged)} |")
    L.append(f"| excluded before screening | {meta['excluded']} |")
    L.append("")

    if meta["excluded_rows"]:
        L.append("## Excluded before screening")
        L.append("")
        L.append(
            "Structural, not a ruling: these are not common equity, and "
            "`src/intradyne/risk/shariah.py` already refuses derivatives."
        )
        L.append("")
        L.append("| symbol | why |")
        L.append("|---|---|")
        for r in meta["excluded_rows"][:200]:
            L.append(f"| {r['symbol']} | {r['reason']} |")
        L.append("")

    def table(title: str, rs: List[Dict[str, Any]], note: str = "") -> None:
        L.append(f"## {title}")
        L.append("")
        if note:
            L.append(note)
            L.append("")
        if not rs:
            L.append("_none_")
            L.append("")
            return
        L.append(
            "| symbol | price | screens | flags | debt/mcap | liquid/mcap | name |"
        )
        L.append("|---|---|---|---|---|---|---|")
        for r in rs:
            ra = r.get("ratios") or {}
            d = f"{ra['debt_over_mcap']:.2f}" if ra else "--"
            q = f"{ra['liquid_over_mcap']:.2f}" if ra else "--"
            fl = ",".join(r.get("flags", []) + r.get("ratio_flags", [])) or "--"
            L.append(
                f"| {r['symbol']} | {r['price']:.2f} | {'/'.join(r['screens'])} | "
                f"{fl} | {d} | {q} | {r.get('name', '')} |"
            )
        L.append("")

    table("No flag raised", clean)
    table(
        "Unknown -- resolve by hand",
        unknown,
        "No sector or industry was returned. That is missing information, not a "
        "clean result.",
    )
    table("Flagged -- needs a ruling", flagged)

    L.append("## Flag key")
    L.append("")
    L.append("| flag | question it raises |")
    L.append("|---|---|")
    for code, _, why in FLAGS:
        L.append(f"| {code} | {why} |")
    L.append("| DEBT | debt over the configured share of market cap |")
    L.append("| LIQUID | cash and short-term investments over that share |")
    L.append("| NO_DATA | no balance sheet returned; ratios not computed |")
    L.append("")
    return "\n".join(L)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/EQUITY_SCREENING.md")
    ap.add_argument("--json-out", default="docs/equity_screen.json")
    ap.add_argument("--timeline", default="docs/equity_screen_timeline.json")
    ap.add_argument("--cache-dir", default="data/equities/cache")
    ap.add_argument("--min-price", type=float, default=5.0)
    ap.add_argument("--max-price", type=float, default=20.0)
    ap.add_argument("--min-dollar-volume", type=float, default=1_000_000.0)
    ap.add_argument("--standard", default=DEFAULT_STANDARD)
    ap.add_argument("--max-debt-ratio", type=float, default=DEFAULT_MAX_DEBT_RATIO)
    ap.add_argument("--max-liquid-ratio", type=float, default=DEFAULT_MAX_LIQUID_RATIO)
    ap.add_argument(
        "--limit", type=int, default=0, help="Cap screened names, for a smoke run"
    )
    args = ap.parse_args(argv)

    key = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()
    if not key:
        print("ALPHAVANTAGE_API_KEY is not set; see .env.example", flush=True)
        return 1

    cache_dir = Path(args.cache_dir)
    cache_file = cache_dir / "fundamentals.json"
    cache: Dict[str, Any] = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cache = {}

    with httpx.Client(timeout=90, headers={"accept": "application/json"}) as c:
        print("fetching the listing reference ...", flush=True)
        ref = reference_list(c, key, cache_dir)
        print(f"  {len(ref)} listed tickers", flush=True)
        if not ref:
            print("no listing reference; cannot classify instruments", flush=True)
            return 1

        print("fetching movers ...", flush=True)
        cands = movers(c, key)
        print(f"  {len(cands)} unique tickers across three screens", flush=True)
        if not cands:
            print("no movers returned", flush=True)
            return 1

        print("tier 0-2: instrument type, price band, dollar volume ...", flush=True)
        survivors: List[Dict[str, Any]] = []
        excluded: List[Dict[str, Any]] = []
        for r in cands:
            verdict, reason = instrument_type(r["symbol"], ref)
            r["instrument"] = verdict
            if verdict != "common":
                r["reason"] = reason
                excluded.append(r)
                continue
            if not (args.min_price <= r["price"] <= args.max_price):
                r["reason"] = (
                    f"price {r['price']:.2f} outside {args.min_price:g}-{args.max_price:g}"
                )
                excluded.append(r)
                continue
            if r["price"] * r["volume"] < args.min_dollar_volume:
                r["reason"] = "below the dollar-volume floor"
                excluded.append(r)
                continue
            survivors.append(r)
        print(f"  {len(survivors)} survive, {len(excluded)} excluded", flush=True)

        if args.limit:
            survivors = survivors[: args.limit]

        print(f"tier 3-4: screening {len(survivors)} names ...", flush=True)
        screened: List[Dict[str, Any]] = []
        for i, r in enumerate(survivors, 1):
            try:
                screened.append(
                    screen_one(
                        c, key, r, cache, args.max_debt_ratio, args.max_liquid_ratio
                    )
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  {r['symbol']} FAILED ({type(exc).__name__})", flush=True)
            # Checkpoint after every name, so a dropped connection costs one.
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache), encoding="utf-8")
            if i % 5 == 0:
                print(f"  {i}/{len(survivors)}", flush=True)

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for r in screened:
        r["standard"] = args.standard
        r["thresholds"] = {
            "max_debt_ratio": args.max_debt_ratio,
            "max_liquid_ratio": args.max_liquid_ratio,
        }
        r["screened_on"] = date

    meta = {
        "date": date,
        "standard": args.standard,
        "max_debt": args.max_debt_ratio,
        "max_liquid": args.max_liquid_ratio,
        "candidates": len(cands),
        "screened": len(screened),
        "excluded": len(excluded),
        "excluded_rows": excluded,
    }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(worksheet(screened, meta), encoding="utf-8")
    Path(args.json_out).write_text(json.dumps(screened, indent=1), encoding="utf-8")

    # Forward-accumulating record, one file keyed by date -- the shape
    # docs/universe_timeline.json already uses. Not a research universe until
    # enough history exists, and it says so.
    tl_path = Path(args.timeline)
    timeline: Dict[str, Any] = {}
    if tl_path.exists():
        try:
            timeline = json.loads(tl_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            timeline = {}
    timeline.setdefault(
        "_note", "Live screens accumulated forward. Not a backtest universe."
    )
    timeline[date] = [
        r["symbol"]
        for r in screened
        if r.get("known") and not r.get("flags") and not r.get("ratio_flags")
    ]
    tl_path.write_text(json.dumps(timeline, indent=1), encoding="utf-8")

    print(f"\nwrote {args.out}, {args.json_out} and {args.timeline}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
