#!/usr/bin/env python
"""Build a Shariah-screening worksheet for the tradeable universe.

    python scripts/build_universe.py --out docs/UNIVERSE_SCREENING.md

The shipped whitelist carries 15 names, which is thin for a cross-sectional
strategy: information ratio scales as ``IC * sqrt(breadth)``, so the number of
names ranked over matters more than refinement of the entry signal. This
assembles the candidate universe and groups it so the compliance decision can
be made against a prepared list.

**This script does not decide what is permissible.** It reports what each
token is and which categories raise a question. Which categories pass is a
scholarly ruling; this is an input to that, never a substitute.

Two traps, both hit while writing it, both worth knowing if the numbers are
ever regenerated:

*Ticker collisions.* Matching Binance tickers against CoinGecko's ~19k-coin
list resolves ETH to a Binance-peg wrapper, UNI to a Sui memecoin and USDC to
a bridged copy, because dozens of tokens share a symbol. Identities therefore
come from the market-cap ranking, where the highest-ranked holder of a ticker
is the real one.

*Substring matching.* Classifying on substrings puts Bitcoin in "DEX" (``dex``
matches "Index") and Chainlink in "lending". Tags are matched whole.

And one modelling choice: a token carries **every** flag that applies rather
than one label. Aave is a lending protocol that also issues a stablecoin;
Chainlink is an oracle that also carries an RWA tag. Collapsing that to a
single category discards what the ruling needs to see.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

BINANCE = "https://data-api.binance.vision/api/v3"
GECKO = "https://api.coingecko.com/api/v3"

#: Categories that raise a screening question, with the reason. A token may
#: match several; all of them are reported.
FLAGS: List[Tuple[str, set, str]] = [
    ("LEND", {"Lending/Borrowing Protocols"}, "interest-bearing by design (riba)"),
    (
        "YIELD",
        {
            "Yield Farming",
            "Liquid Staking Tokens",
            "Restaking",
            "Liquid Staking Governance Tokens",
        },
        "return is yield on deposits",
    ),
    (
        "DERIV",
        {
            "Perpetuals",
            "Derivatives",
            "Options",
            "Prediction Markets",
            "Synthetic Issuer",
        },
        "gharar / maysir",
    ),
    ("GAMBLE", {"Gambling (GambleFi)", "Casino"}, "maysir"),
    (
        "MEME",
        {"Meme", "Dog-Themed", "4chan-Themed", "Elon Musk-Inspired"},
        "no underlying activity; pure speculation",
    ),
    (
        "CEX",
        {"Exchange-based Tokens", "Centralized Exchange (CEX)"},
        "issuer revenue includes margin, futures and lending",
    ),
    (
        "STABLE",
        {
            "Stablecoins",
            "USD Stablecoin",
            "Fiat-backed Stablecoin",
            "Stablecoin Issuer",
        },
        "reserves earn interest; also carries no price signal to rank on",
    ),
    (
        "DEX",
        {"Decentralized Exchange (DEX)", "Automated Market Maker (AMM)"},
        "a spot AMM may be judged differently from a perp venue",
    ),
    (
        "RWA",
        {"Real World Assets (RWA)", "Tokenized Gold", "Tokenized Treasury Bills"},
        "depends entirely on the underlying",
    ),
    (
        "GAME",
        {"Gaming (GameFi)", "Metaverse", "NFT", "Play To Earn", "Gaming Utility Token"},
        "some titles are gambling-adjacent",
    ),
    (
        "PRIV",
        {"Privacy", "Privacy Infrastructure", "Privacy Coins"},
        "delisting/regulatory risk rather than a fiqh objection",
    ),
    (
        "DEFI",
        {"Decentralized Finance (DeFi)"},
        "too broad to judge alone; check the protocol's actual business",
    ),
]

#: Descriptive sector tags. Never a verdict -- they say what a thing is.
WHAT: List[Tuple[str, set]] = [
    ("L1", {"Layer 1 (L1)", "Smart Contract Platform", "Layer 0 (L0)"}),
    ("L2", {"Layer 2 (L2)", "Zero Knowledge (ZK)", "Rollup"}),
    ("oracle", {"Oracle"}),
    ("AI", {"Artificial Intelligence (AI)", "AI Agents", "AI Agent Launchpad"}),
    ("depin", {"DePIN", "Storage", "Distributed Computing", "Filesharing"}),
    (
        "infra",
        {
            "Infrastructure",
            "Interoperability",
            "Bridge",
            "Identity",
            "Account Abstraction",
        },
    ),
    ("pay", {"Payment Solutions"}),
]

#: Leveraged tokens are derivatives wearing a spot wrapper.
LEVERAGED = ("UP", "DOWN", "BULL", "BEAR")


def _get(c: httpx.Client, url: str, params=None, tries: int = 6) -> Optional[Any]:
    """CoinGecko drops roughly one request in three from here; retry all of it."""
    for i in range(tries):
        try:
            r = c.get(url, params=params)
            if r.status_code == 429:
                time.sleep(20)
                continue
            return r.json() if r.status_code == 200 else None
        except Exception:  # noqa: BLE001
            time.sleep(4 * (i + 1))
    return None


def spot_pairs(c: httpx.Client, min_volume: float) -> List[Dict[str, Any]]:
    info = _get(c, f"{BINANCE}/exchangeInfo") or {}
    vol = {
        t["symbol"]: float(t["quoteVolume"])
        for t in (_get(c, f"{BINANCE}/ticker/24hr") or [])
    }
    out = []
    for s in info.get("symbols", []):
        if (
            s["quoteAsset"] != "USDT"
            or s["status"] != "TRADING"
            or not s.get("isSpotTradingAllowed")
        ):
            continue
        base = s["baseAsset"]
        if any(base.endswith(x) for x in LEVERAGED):
            continue
        v = vol.get(s["symbol"], 0.0)
        if v >= min_volume:
            out.append({"base": base, "vol": v})
    return sorted(out, key=lambda r: -r["vol"])


def add_history(c: httpx.Client, rows, min_years: float):
    """Keep only names with enough history for a multi-year backtest."""
    now = datetime.now(timezone.utc)
    kept = []
    for r in rows:
        k = _get(
            c,
            f"{BINANCE}/klines",
            {
                "symbol": f"{r['base']}USDT",
                "interval": "1d",
                "startTime": 0,
                "limit": 1,
            },
        )
        if not k:
            continue
        first = datetime.fromtimestamp(k[0][0] / 1000, timezone.utc)
        years = (now - first).days / 365.25
        if years >= min_years:
            kept.append(
                {**r, "listed": first.strftime("%Y-%m-%d"), "years": round(years, 2)}
            )
    return kept


def identities(c: httpx.Client, pages: int = 8) -> Dict[str, Dict[str, Any]]:
    """Map a ticker to the highest-market-cap coin holding it.

    Ranked pages rather than the full ~19k coin list, because dozens of
    tokens share a ticker and the ranking says which one is meant. Eight
    pages (2000 coins) rather than four: at 1000 the tail included WBTC,
    PYTH and VIRTUAL, which are not obscure -- they were simply below the
    cut, and landed in the unidentified bucket as a result.
    """
    sym2id: Dict[str, Dict[str, Any]] = {}
    rank = 0
    for page in range(1, pages + 1):
        j = _get(
            c,
            f"{GECKO}/coins/markets",
            {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 250,
                "page": page,
            },
        )
        if not j:
            break
        for co in j:
            rank += 1
            sym2id.setdefault(
                co["symbol"].upper(),
                {
                    "id": co["id"],
                    "name": co["name"],
                    "mcap": co.get("market_cap"),
                    "rank": rank,
                },
            )
        time.sleep(3)
    return sym2id


def categorise(c: httpx.Client, rows, sym2id, cache: Optional[Path]):
    """Fetch categories, checkpointing so a dropped connection costs one coin."""
    done: Dict[str, Any] = {}
    if cache and cache.exists():
        done = {d["base"]: d for d in json.loads(cache.read_text())}
    for r in rows:
        if r["base"] in done:
            continue
        m = sym2id.get(r["base"].upper())
        rec = {**r, "cg_id": None, "name": None, "mcap": None, "categories": []}
        if m:
            j = _get(
                c,
                f"{GECKO}/coins/{m['id']}",
                {
                    "localization": "false",
                    "tickers": "false",
                    "market_data": "false",
                    "community_data": "false",
                    "developer_data": "false",
                },
            )
            time.sleep(2.5)
            if j:
                rec.update(
                    cg_id=m["id"],
                    name=m["name"],
                    mcap=m["mcap"],
                    categories=[x for x in (j.get("categories") or []) if x],
                )
        done[r["base"]] = rec
        if cache:
            cache.write_text(json.dumps(list(done.values()), indent=1))
    # Return only the current candidates. The cache accumulates across runs
    # with different thresholds, so returning all of it silently readmits
    # names that no longer meet the liquidity or history floor.
    return [done[r["base"]] for r in rows if r["base"] in done]


def classify(rows):
    for r in rows:
        tags = set(r["categories"])
        r["flags"] = [f for f, keys, _ in FLAGS if tags & keys]
        r["what"] = [w for w, keys in WHAT if tags & keys]
        # No tags at all means no information, which is not the same as clean.
        r["known"] = bool(tags)
    return rows


def _mcap(r) -> str:
    m = r.get("mcap")
    if not m:
        return "?"
    return f"${m / 1e9:.1f}B" if m >= 1e9 else f"${m / 1e6:.0f}M"


def worksheet(rows, min_years: float, min_volume: float) -> str:
    rows = sorted(rows, key=lambda r: (len(r["flags"]), -(r.get("mcap") or 0)))
    clean = [r for r in rows if r["known"] and not r["flags"]]
    unknown = [r for r in rows if not r["known"]]
    flagged = [r for r in rows if r["known"] and r["flags"]]

    L: List[str] = []
    L.append("# Universe screening worksheet\n")
    L.append(
        f"Generated by `scripts/build_universe.py`. Binance spot USDT pairs "
        f"with at least {min_years:g} years of history and "
        f"${min_volume / 1e3:.0f}k+ daily volume.\n"
    )
    L.append(
        "**This is not a compliance ruling.** It reports what each token is "
        "and which categories raise a question. Deciding which categories "
        "pass is a scholarly judgement; this is an input to it.\n"
    )
    L.append("| | count |\n|---|---|")
    L.append(f"| total universe | {len(rows)} |")
    L.append(f"| no flag raised | {len(clean)} |")
    L.append(f"| unknown, needs manual identification | {len(unknown)} |")
    L.append(f"| flagged, needs a ruling | {len(flagged)} |\n")

    L.append("\n## No flag raised\n")
    L.append(
        "Infrastructure and utility tokens: nothing in their categories "
        "raises a screening question, which is not the same as approval.\n"
    )
    L.append("| symbol | market cap | is | name |\n|---|---|---|---|")
    for r in clean:
        L.append(
            f"| {r['base']} | {_mcap(r)} | {','.join(r['what']) or 'other'} "
            f"| {r['name'] or ''} |"
        )

    L.append("\n## Unknown -- resolve by hand\n")
    L.append(
        "Outside CoinGecko's top 1000, so no sector data was available. "
        "**These are not clean, they are unidentified.** Their absence from "
        "the flagged list is not a pass.\n"
    )
    L.append("| symbol | 24h volume | listed | years |\n|---|---|---|---|")
    for r in unknown:
        L.append(
            f"| {r['base']} | ${r['vol'] / 1e6:.1f}M | {r['listed']} | {r['years']} |"
        )

    L.append("\n## Flagged -- needs a ruling\n")
    L.append("| symbol | market cap | flags | is | name |\n|---|---|---|---|---|")
    for r in flagged:
        L.append(
            f"| {r['base']} | {_mcap(r)} | `{','.join(r['flags'])}` "
            f"| {','.join(r['what']) or 'other'} | {r['name'] or ''} |"
        )

    L.append("\n## Flag key\n")
    L.append("| flag | question it raises |\n|---|---|")
    for f, _, why in FLAGS:
        L.append(f"| `{f}` | {why} |")

    L.append("\n## Why breadth matters here\n")
    ratio = (len(clean) / 15) ** 0.5 if clean else 1.0
    L.append(
        "Information ratio scales as `IC * sqrt(breadth)`, so the number of "
        "names a cross-sectional strategy ranks over moves results more than "
        "signal refinement does. Going from the shipped 15-name whitelist to "
        f"{len(clean)} names is roughly a {ratio:.1f}x improvement for "
        "identical skill.\n"
    )
    L.append(
        "One caveat the count hides: the unflagged names are almost entirely "
        "L1/L2 infrastructure, the most mutually correlated group in crypto. "
        "Cross-sectional selection feeds on dispersion *between* names, so "
        "effective breadth is lower than the headline number. The categories "
        "that would add real dispersion -- gaming, DeFi, memecoins -- are the "
        "flagged ones.\n"
    )
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="docs/UNIVERSE_SCREENING.md")
    # Tracked alongside the worksheet, not under artifacts/ which is
    # gitignored: market caps and categories drift, so the evidence a
    # ruling was made against needs to be committed with it.
    p.add_argument("--json-out", default="docs/universe_candidates.json")
    p.add_argument("--min-years", type=float, default=3.0)
    p.add_argument("--min-volume", type=float, default=300_000)
    p.add_argument("--cache", default=None, help="Resume file for the slow step")
    args = p.parse_args(argv)

    with httpx.Client(timeout=90, headers={"accept": "application/json"}) as c:
        print("listing spot pairs ...", flush=True)
        rows = spot_pairs(c, args.min_volume)
        print(f"  {len(rows)} pairs above ${args.min_volume / 1e3:.0f}k/day")
        print("checking history depth ...", flush=True)
        rows = add_history(c, rows, args.min_years)
        print(f"  {len(rows)} with >= {args.min_years:g}y history")
        print("resolving identities ...", flush=True)
        sym2id = identities(c)
        print(f"  {len(sym2id)} tickers mapped")
        print("fetching categories ...", flush=True)
        rows = categorise(c, rows, sym2id, Path(args.cache) if args.cache else None)

    rows = classify(rows)
    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        worksheet(rows, args.min_years, args.min_volume), encoding="utf-8"
    )
    print(f"\nwrote {args.out} and {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
