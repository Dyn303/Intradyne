#!/usr/bin/env python
"""Fetch US equity intraday bars into the CSV cache the A1/A2 gates read.

    python scripts/fetch_equity_bars.py --symbols AAPL,CAT --interval 30min \\
        --from 2026-03 --to 2026-09

Closes the gap `docs/STRATEGY_RESEARCH_FRAMEWORK.md` Part 6 records: the CSVs
behind `scripts/equity_feasibility.py` and `scripts/equity_breadth.py` were
assembled by hand and did not reproduce on a fresh clone.

Output is `data/equities/{SYMBOL}_{interval}.csv` with a
`datetime,open,high,low,close,volume` header -- exactly what those two scripts
already parse, so they run unchanged against anything this writes.

*Why Alpha Vantage.* It answers all of this project's equity needs from one
key: intraday bars by calendar month, `LISTING_STATUS` for the survivorship-free
universe A3 will need, and the fundamentals `screen_equities.py` screens on.
Reachability was checked from here before committing to it, because
`scripts/fetch_ohlc.py` records that the main Binance API times out from
Malaysia and provider reachability is not something this project assumes.

*Month-at-a-time, cached per month.* `TIME_SERIES_INTRADAY` takes a `month`
parameter and returns that calendar month in full. Each month lands in its own
CSV under a per-symbol directory and is skipped if already present, so an
interrupted run resumes without re-downloading -- the same shape
`fetch_klines_archive.py` uses for its npz cache. The per-symbol CSV the gates
read is then concatenated from those parts.

*Extended hours are excluded.* `equity_feasibility.py` drops overnight returns
because a strategy flat at the close does not collect them; pre- and post-market
bars are the same argument one level down, and they carry spreads that would
flatter a volatility estimate.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

AV = "https://www.alphavantage.co/query"

INTERVALS = ("1min", "5min", "15min", "30min", "60min")

HEADER = "datetime,open,high,low,close,volume"


def months(start: str, end: str) -> List[str]:
    """Inclusive YYYY-MM enumeration, matching fetch_klines_archive.months."""
    a = datetime.strptime(start, "%Y-%m")
    b = datetime.strptime(end, "%Y-%m")
    out: List[str] = []
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append("%04d-%02d" % (y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _get(c: httpx.Client, params: Dict[str, str], tries: int = 5) -> Optional[str]:
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
            if text.lstrip().startswith("{"):
                # Either a quota note or an error; both mean no CSV this time.
                time.sleep(20)
                continue
            return text
        except Exception:  # noqa: BLE001
            time.sleep(3 * (i + 1))
    return None


def fetch_month(
    c: httpx.Client, key: str, symbol: str, interval: str, month: str
) -> Optional[List[Tuple[str, str, str, str, str, str]]]:
    text = _get(
        c,
        {
            "function": "TIME_SERIES_INTRADAY",
            "symbol": symbol,
            "interval": interval,
            "month": month,
            "outputsize": "full",
            "extended_hours": "false",
            "adjusted": "true",
            "datatype": "csv",
            "apikey": key,
        },
    )
    if not text:
        return None
    rows: List[Tuple[str, str, str, str, str, str]] = []
    for rec in csv.DictReader(io.StringIO(text)):
        ts = (rec.get("timestamp") or "").strip()
        if not ts:
            continue
        rows.append(
            (
                ts,
                rec.get("open", ""),
                rec.get("high", ""),
                rec.get("low", ""),
                rec.get("close", ""),
                rec.get("volume", ""),
            )
        )
    return rows or None


def write_symbol(parts_dir: Path, out: Path, symbol: str, interval: str) -> int:
    """Concatenate the cached months into the file the gate scripts read."""
    seen: Dict[str, Tuple[str, ...]] = {}
    for f in sorted(parts_dir.glob(f"{symbol}-{interval}-*.csv")):
        with f.open(encoding="utf-8") as fh:
            for rec in csv.DictReader(fh):
                # Month boundaries can repeat a bar; last write wins.
                seen[rec["datetime"]] = (
                    rec["datetime"],
                    rec["open"],
                    rec["high"],
                    rec["low"],
                    rec["close"],
                    rec["volume"],
                )
    if not seen:
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [HEADER]
    for stamp in sorted(seen):
        lines.append(",".join(seen[stamp]))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(seen)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="AAPL", help="Comma-separated tickers")
    ap.add_argument("--interval", default="30min", choices=INTERVALS)
    ap.add_argument("--from", dest="start", required=True, help="YYYY-MM")
    ap.add_argument("--to", dest="end", required=True, help="YYYY-MM")
    ap.add_argument("--data-dir", default="data/equities")
    ap.add_argument("--sleep", type=float, default=0.9, help="Seconds between requests")
    ap.add_argument("--limit", type=int, default=0, help="Cap symbols, for a smoke run")
    args = ap.parse_args(argv)

    key = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()
    if not key:
        print("ALPHAVANTAGE_API_KEY is not set; see .env.example", flush=True)
        return 1

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.limit:
        symbols = symbols[: args.limit]
    want = months(args.start, args.end)
    data_dir = Path(args.data_dir)
    parts_dir = data_dir / "months"
    parts_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    with httpx.Client(timeout=120, headers={"accept": "text/csv"}) as c:
        for symbol in symbols:
            got = 0
            for month in want:
                part = parts_dir / f"{symbol}-{args.interval}-{month}.csv"
                if part.exists():
                    got += 1
                    continue
                print(f"{symbol} {args.interval} {month} ...", end=" ", flush=True)
                try:
                    rows = fetch_month(c, key, symbol, args.interval, month)
                except Exception as exc:  # noqa: BLE001
                    print(f"FAILED ({type(exc).__name__})", flush=True)
                    continue
                if not rows:
                    print("no data", flush=True)
                    continue
                part.write_text(
                    "\n".join([HEADER] + [",".join(r) for r in rows]) + "\n",
                    encoding="utf-8",
                )
                got += 1
                print(f"{len(rows)} bars", flush=True)
                time.sleep(args.sleep)

            out = data_dir / f"{symbol}_{args.interval}.csv"
            n = write_symbol(parts_dir, out, symbol, args.interval)
            total += n
            print(f"{symbol}: {got}/{len(want)} months, {n} bars -> {out}", flush=True)

    if total == 0:
        print("no data fetched", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
