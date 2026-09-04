"""Measure the touch spread of each whitelist symbol, and commit the result.

The backtester has to assume a spread -- OHLCV carries none -- and that
assumption decides what a backtest concludes. A single figure cannot be right
for a universe spanning BTC at 0.00bps and DOT at 11.38, so this measures one
per instrument.

The output is committed to `docs/` rather than `artifacts/` for the reason
`build_universe.py` gives: spreads drift, so the evidence a cost model was
built against needs to travel with it. Keyed by date, accumulating forward,
following `docs/universe_timeline.json`.

**What this is not.** These are spreads measured *today*, and a backtest runs
over history. Applying them to 2023 bars assumes the cross-sectional ordering
was stable -- that DOT has always been thinner than BTC -- which is far more
defensible than assuming they were equal, but it is still an assumption and
not a measurement of the past. The alternative is a single flat figure that
is wrong for every instrument at once.

Usage:
    python scripts/measure_spreads.py [--samples 20] [--interval 3] [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import statistics as st
from pathlib import Path
from typing import Any, Dict, List

OUT = Path("docs/spread_measurements.json")
WHITELIST = Path("src/intradyne/engine/whitelist.json")
#: Depth is reported within this distance of the touch, as context for whether
#: a quoted spread is real or a single lot sitting alone at the front.
DEPTH_BAND_BPS = 5.0


def _load_symbols(limit: int | None) -> List[str]:
    syms = list(json.loads(WHITELIST.read_text(encoding="utf-8"))["symbols"])
    return syms[:limit] if limit else syms


async def _sample(ex: Any, sym: str) -> Dict[str, float] | None:
    """One observation: spread at the touch, and depth behind it."""
    ob = await ex.fetch_order_book(sym, limit=50)
    if not ob.get("bids") or not ob.get("asks"):
        return None
    bid, ask = ob["bids"][0][0], ob["asks"][0][0]
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    band = mid * (1 + DEPTH_BAND_BPS / 10_000.0)
    return {
        "spread_bps": (ask - bid) / mid * 10_000.0,
        "depth_usd": sum(p * q for p, q in ob["asks"] if p <= band),
    }


async def measure(
    exchange_id: str, symbols: List[str], n: int, gap: float
) -> Dict[str, Any]:
    import ccxt.async_support as ccxt

    ex = getattr(ccxt, exchange_id)()
    out: Dict[str, Any] = {}
    try:
        await ex.load_markets()
        acc: Dict[str, List[Dict[str, float]]] = {s: [] for s in symbols}
        for i in range(n):
            print(f"  sample {i + 1}/{n}", flush=True)
            for sym in symbols:
                try:
                    s = await _sample(ex, sym)
                    if s:
                        acc[sym].append(s)
                except Exception as e:  # noqa: BLE001
                    print(f"    {sym}: {e}", flush=True)
            if i < n - 1:
                await asyncio.sleep(gap)
        for sym, rows in acc.items():
            if not rows:
                # No reading is not the same as a tight spread, so the symbol
                # is omitted and takes the configured fallback.
                print(f"  {sym}: no readings, omitted", flush=True)
                continue
            sp = [r["spread_bps"] for r in rows]
            dp = [r["depth_usd"] for r in rows]
            out[sym] = {
                "spread_bps": round(st.median(sp), 4),
                "spread_bps_min": round(min(sp), 4),
                "spread_bps_max": round(max(sp), 4),
                "depth_usd_5bps": round(st.median(dp), 2),
                "samples": len(rows),
            }
    finally:
        await ex.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exchange", default="bitget")
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    symbols = _load_symbols(args.limit)
    print(f"== measuring {len(symbols)} symbols on {args.exchange} ==", flush=True)
    rows = asyncio.run(measure(args.exchange, symbols, args.samples, args.interval))
    if not rows:
        print("no measurements taken", flush=True)
        return 1

    today = dt.date.today().isoformat()
    doc: Dict[str, Any] = {}
    if OUT.exists():
        doc = json.loads(OUT.read_text(encoding="utf-8"))
    doc[today] = {"exchange": args.exchange, "symbols": rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print(f"\n== {today} ==", flush=True)
    print(f"{'symbol':12} {'median':>8} {'min':>8} {'max':>8} {'depth 5bps':>13}")
    for sym, r in sorted(rows.items(), key=lambda kv: kv[1]["spread_bps"]):
        print(
            f"{sym:12} {r['spread_bps']:8.2f} {r['spread_bps_min']:8.2f} "
            f"{r['spread_bps_max']:8.2f} {r['depth_usd_5bps']:13,.0f}"
        )
    print(f"\nwrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
