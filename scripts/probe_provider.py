#!/usr/bin/env python
"""Ask a market-data provider the four questions that decide a study.

    python scripts/probe_provider.py --provider twelvedata
    python scripts/probe_provider.py --provider alpaca

Approach 1 aborted because the data could not support a survivorship-honest
panel: 51% of a random draw was unreachable, and the missing half was delisted
names, renamed companies and misclassified preferreds. That was discovered
after building a fetcher, drawing a sample and running 120 requests. It should
have been discoverable in four.

So this is a **precondition check**, in the sense
`docs/EQUITY_PROGRAMME_STOP_RULE.md` uses: it establishes whether a question can
be asked, spends no slot, and produces a fact rather than a hope. Run it before
paying a provider, not after.

## The four questions

**Depth.** Does intraday history reach back far enough? The Sharpe bar of 0.80
needs 6.25 years (`t ~= Sharpe * sqrt(years)`), so a provider serving two is not
a cheaper version of one serving seven -- it cannot answer the question at all.

**Breadth.** Is the feed consolidated, or one venue? This is the trap worth the
whole script. Alpaca's free tier serves **IEX only, about 2.5% of US equity
volume**, and 2.5% of the tape still arrives shaped like bars: an OHLC row, a
volume column, nothing malformed. It would pass every quality check in
`equity_liquidity.py` and quietly answer a different question -- most sharply
for a first-half-hour signal, where venue fragmentation and auction flow
diverge most from the consolidated tape.

**Death.** Are delisted names served? 95% of them were unreachable in the
approach 1 draw, and they are by construction the names that did worst. A
provider that drops them cannot support any long-horizon study, and one that
returns a *placeholder* for them is worse than one that refuses.

**Identity.** Does a pre-rename ticker resolve? `FBHS` became `FBIN` in 2022.

Running this probe corrected a conclusion drawn during the approach 1
post-mortem. `FBIN` returned nothing for a 2019 window, and that was attributed
to the provider being unable to serve renamed companies. It cannot: Twelve Data
returns 65 bars for `FBHS` over the same window without complaint. The failure
was on this side -- `docs/equity_listings.csv` records one symbol per listing,
today's, so the fetch asked for a ticker that did not exist in 2019.

That makes renames a **symbology problem, not a coverage problem**, and it is
fixable with a point-in-time symbol map rather than by changing provider. The
probe stays because a provider that genuinely cannot serve historical tickers
would be disqualifying, and one has to look to know which case applies.

## Validating the probe against a known answer

Run it against `twelvedata` first. That provider's answers are known --
depth passes, death fails on a 404, identity passes -- so a probe that does not
reproduce them is broken, and any verdict it gives about a new provider is
worthless. Same falsification discipline
`test_random_selection_earns_no_excess` applies to the backtest harness.

One honest limit on the control: the breadth reference figure was itself
measured from Twelve Data, so that provider cannot fail its own breadth probe.
The comparison is a real test for **any other** provider and a tautology for
this one. Replacing the reference with an exchange-published figure would close
that, and until then a `twelvedata` breadth PASS should be read as "the probe
ran", not as evidence.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

#: A consolidated-tape figure this project measured directly: AAPL's opening
#: 30-minute bar on 2024-06-05. A single-venue feed reports a small fraction of
#: it, which is what makes the comparison a breadth test rather than a guess.
REFERENCE = {
    "symbol": "AAPL",
    "date": "2024-06-05",
    "bar": "09:30",
    "consolidated_volume": 7_833_410,
}

#: Below this share of the consolidated figure the feed is not the market.
#: IEX is ~2.5%; anything under a quarter of the tape cannot be treated as
#: consolidated, and the margin is wide because venue share moves day to day.
MIN_VOLUME_SHARE = 0.25

#: Delisted 2026-09-02. Alpha Vantage returns 100 sessions of 4.3600 at zero
#: volume for it -- a placeholder standing where the decline used to be.
DEAD = {"symbol": "ADVM", "start": "2026-06-02", "end": "2026-08-29"}

#: Fortune Brands traded as FBHS until the 2022 rename to FBIN.
RENAMED = {"symbol": "FBHS", "start": "2019-11-01", "end": "2019-11-08"}

#: The window approach 1 needs to reach. Earlier than this and the power
#: requirement cannot be met.
DEPTH = {"symbol": "AAPL", "start": "2019-11-01", "end": "2019-11-08"}


@dataclass
class Result:
    """One probe's answer, with the evidence that produced it."""

    name: str
    question: str
    passed: Optional[bool]
    detail: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def mark(self) -> str:
        return "PASS" if self.passed else ("FAIL" if self.passed is False else "??")


Bars = List[Tuple[str, float, float]]  # (timestamp, close, volume)


def _flat(bars: Bars) -> bool:
    """Every close identical -- the placeholder shape, not a price series."""
    return len(bars) > 5 and len({round(c, 6) for _, c, _ in bars}) == 1


def _dead_volume(bars: Bars) -> bool:
    return len(bars) > 5 and all(v == 0 for _, _, v in bars)


# ---- providers ------------------------------------------------------------


def fetch_twelvedata(
    c: httpx.Client, key: str, symbol: str, start: str, end: str
) -> Tuple[Optional[Bars], str]:
    r = c.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol": symbol,
            "interval": "30min",
            "start_date": start,
            "end_date": end,
            "outputsize": "5000",
            "format": "JSON",
            "apikey": key,
        },
        timeout=60.0,
    )
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    body = r.json()
    if body.get("status") == "error":
        return None, str(body.get("message", ""))[:70]
    vals = body.get("values") or []
    bars = [
        (v["datetime"], float(v["close"]), float(v.get("volume") or 0)) for v in vals
    ]
    bars.sort()
    return (bars or None), ("ok" if bars else "empty payload")


def fetch_alpaca(
    c: httpx.Client, key: str, symbol: str, start: str, end: str
) -> Tuple[Optional[Bars], str]:
    """Alpaca splits the key across two headers, and `feed` decides everything.

    `feed=sip` is requested explicitly rather than left to the default: the
    point of the breadth probe is to learn whether this account is served the
    consolidated tape, and a silent fallback to IEX would answer the question
    wrongly and look like a pass.
    """
    secret = os.getenv("ALPACA_SECRET_KEY", "").strip()
    r = c.get(
        "https://data.alpaca.markets/v2/stocks/bars",
        params={
            "symbols": symbol,
            "timeframe": "30Min",
            "start": f"{start}T00:00:00Z",
            "end": f"{end}T23:59:59Z",
            "limit": "5000",
            "feed": os.getenv("ALPACA_FEED", "sip"),
            "adjustment": "split",
        },
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        timeout=60.0,
    )
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:60]}"
    body = r.json()
    rows = (body.get("bars") or {}).get(symbol) or []
    bars = [(b["t"], float(b["c"]), float(b.get("v") or 0)) for b in rows]
    bars.sort()
    return (bars or None), ("ok" if bars else "empty payload")


# ---- webull ---------------------------------------------------------------

#: Webull signs a canonical string rather than a bearer token, and the exact
#: construction is theirs, not a convention -- see
#: developer.webull.com.au/apis/docs/authentication/signature.
WEBULL_SIGNED_HEADERS = (
    "x-app-key",
    "x-signature-algorithm",
    "x-signature-nonce",
    "x-signature-version",
    "x-timestamp",
)

#: The bars path is the one piece of this adapter not confirmed from the
#: published docs. Their reference lists "Historical Bars (Single Symbol)" and
#: the snapshot example is `/openapi/market-data/stock/snapshot`, so this
#: follows that shape -- but a guessed path 404s, and a 404 would be reported
#: as "provider lacks history" when the truth is "we asked the wrong URL".
#: Override with WEBULL_BARS_PATH once the API Reference confirms it.
WEBULL_BARS_PATH = "/openapi/market-data/stock/bars"


def _webull_sign(secret: str, path: str, params: Dict[str, str]) -> str:
    """Canonical string, then base64(HMAC-SHA256).

    Query parameters and the signing headers go into one list, sorted by name,
    joined `name=value&...`. With no body the canonical string is
    `path + "&" + that`. The key is the app secret with `&` appended -- an easy
    detail to miss, and the signature silently fails without it.
    """
    merged = "&".join(f"{k}={params[k]}" for k in sorted(params))
    canonical = quote(f"{path}&{merged}", safe="")
    mac = hmac.new(
        (secret + "&").encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    )
    return base64.b64encode(mac.digest()).decode("ascii")


def fetch_webull(
    c: httpx.Client, key: str, symbol: str, start: str, end: str
) -> Tuple[Optional[Bars], str]:
    """Historical bars from the venue this project would execute against.

    Worth probing even if another provider wins on history: research data and
    execution data coming from different vendors makes the backtest/paper/live
    comparison in the framework a comparison of two markets.
    """
    secret = os.getenv("WEBULL_APP_SECRET", "").strip()
    host = os.getenv("WEBULL_HOST", "api.webull.com.my").strip()
    path = os.getenv("WEBULL_BARS_PATH", WEBULL_BARS_PATH).strip()
    query = {
        "symbol": symbol,
        "category": "US_STOCK",
        "timespan": "M30",
        "count": "1000",
    }
    headers = {
        "x-app-key": key,
        "x-signature-algorithm": "HMAC-SHA256",
        "x-signature-version": "1.0",
        "x-signature-nonce": uuid.uuid4().hex,
        "x-timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "x-version": "1.0",
    }
    signed = {k: headers[k] for k in WEBULL_SIGNED_HEADERS}
    signed["host"] = host
    signed.update(query)
    headers["x-signature"] = _webull_sign(secret, path, signed)

    r = c.get(f"https://{host}{path}", params=query, headers=headers, timeout=60.0)
    if r.status_code == 404:
        return None, (
            f"HTTP 404 on {path} -- confirm the bars path in the API Reference "
            "and set WEBULL_BARS_PATH; this is not evidence about coverage"
        )
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:60]}"
    body = r.json()
    rows = body.get("data") or body.get("bars") or []
    bars: Bars = []
    for b in rows:
        ts = b.get("tradeTime") or b.get("t") or b.get("timestamp")
        close = b.get("close") or b.get("c")
        if ts is None or close is None:
            continue
        bars.append((str(ts), float(close), float(b.get("volume") or b.get("v") or 0)))
    bars.sort()
    return (bars or None), ("ok" if bars else "empty payload")


PROVIDERS = {
    "twelvedata": (fetch_twelvedata, "TWELVEDATA_API_KEY"),
    "alpaca": (fetch_alpaca, "ALPACA_API_KEY"),
    "webull": (fetch_webull, "WEBULL_APP_KEY"),
}


# ---- probes ---------------------------------------------------------------


def probe_depth(fetch, key, c) -> Result:
    bars, why = fetch(c, key, DEPTH["symbol"], DEPTH["start"], DEPTH["end"])
    if not bars:
        return Result(
            "depth",
            "Does intraday history reach 2019-11?",
            False,
            f"{DEPTH['symbol']} returned nothing ({why}). The 6.25 years the "
            "power requirement needs are not available.",
        )
    return Result(
        "depth",
        "Does intraday history reach 2019-11?",
        True,
        f"{len(bars)} bars from {bars[0][0]}.",
        {"bars": len(bars), "first": bars[0][0]},
    )


def probe_breadth(fetch, key, c) -> Result:
    # A same-day range is rejected by at least one provider, so the window is
    # opened to the following day and the reference bar selected from it.
    nxt = (
        datetime.strptime(REFERENCE["date"], "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")
    bars, why = fetch(c, key, REFERENCE["symbol"], REFERENCE["date"], nxt)
    bars = [b for b in (bars or []) if b[0].startswith(REFERENCE["date"])] or None
    if not bars:
        return Result(
            "breadth", "Is the feed the whole tape?", None, f"no data ({why})."
        )
    opening = [b for b in bars if REFERENCE["bar"] in b[0]]
    if not opening:
        return Result(
            "breadth", "Is the feed the whole tape?", None, "opening bar absent."
        )
    vol = opening[0][2]
    share = vol / REFERENCE["consolidated_volume"]
    ok = share >= MIN_VOLUME_SHARE
    return Result(
        "breadth",
        "Is the feed the whole tape?",
        ok,
        f"opening bar {vol:,.0f} vs {REFERENCE['consolidated_volume']:,} "
        f"consolidated = {share:.0%} of the tape."
        + ("" if ok else "  Single-venue feed; bars are not the market."),
        {"volume": vol, "share": round(share, 4)},
    )


def probe_death(fetch, key, c) -> Result:
    bars, why = fetch(c, key, DEAD["symbol"], DEAD["start"], DEAD["end"])
    if not bars:
        return Result(
            "death",
            "Are delisted names served?",
            False,
            f"{DEAD['symbol']} returned nothing ({why}). An honest refusal, "
            "but the name is unusable.",
        )
    if _flat(bars) or _dead_volume(bars):
        return Result(
            "death",
            "Are delisted names served?",
            False,
            f"{DEAD['symbol']} returned {len(bars)} bars that are a "
            "placeholder, not a price series -- identical closes or zero "
            "volume. Worse than a refusal: it scores a dead company as a "
            "zero-volatility asset.",
            {"bars": len(bars), "placeholder": True},
        )
    return Result(
        "death",
        "Are delisted names served?",
        True,
        f"{DEAD['symbol']} returned {len(bars)} bars with real variation.",
        {"bars": len(bars)},
    )


def probe_identity(fetch, key, c) -> Result:
    bars, why = fetch(c, key, RENAMED["symbol"], RENAMED["start"], RENAMED["end"])
    if not bars:
        return Result(
            "identity",
            "Does a pre-rename ticker resolve?",
            False,
            f"{RENAMED['symbol']} returned nothing ({why}). A live company's "
            "history is unreachable under the name it traded by.",
        )
    return Result(
        "identity",
        "Does a pre-rename ticker resolve?",
        True,
        f"{RENAMED['symbol']} returned {len(bars)} bars for 2019.",
        {"bars": len(bars)},
    )


PROBES = (probe_depth, probe_breadth, probe_death, probe_identity)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    fetch, env = PROVIDERS[args.provider]
    key = os.getenv(env, "").strip()
    if not key:
        print(f"{env} is not set; see .env.example", flush=True)
        return 2

    print(f"probing {args.provider}")
    print()
    results: List[Result] = []
    with httpx.Client() as c:
        for p in PROBES:
            try:
                r = p(fetch, key, c)
            except Exception as exc:  # noqa: BLE001
                r = Result(
                    p.__name__.replace("probe_", ""),
                    "",
                    None,
                    f"probe raised {type(exc).__name__}: {exc}",
                )
            results.append(r)
            print(f"  {r.mark:<5} {r.name:<9} {r.detail}")

    print()
    failed = [r for r in results if r.passed is False]
    unknown = [r for r in results if r.passed is None]
    if failed:
        print(f"VERDICT: unusable -- {len(failed)} of {len(results)} probes failed.")
        print("A provider failing any one of these cannot support a")
        print("survivorship-honest study. Do not pay for it, and do not spend a")
        print("slot against it.")
    elif unknown:
        print("VERDICT: inconclusive -- rerun; some probes could not answer.")
    else:
        print("VERDICT: usable -- all four questions answered affirmatively.")
        print("This clears the data precondition. It says nothing about whether")
        print("an edge exists.")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(
                {
                    "provider": args.provider,
                    "generated": datetime.now(timezone.utc).isoformat(),
                    "results": [
                        {
                            "name": r.name,
                            "question": r.question,
                            "passed": r.passed,
                            "detail": r.detail,
                            "evidence": r.evidence,
                        }
                        for r in results
                    ],
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
