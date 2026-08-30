#!/usr/bin/env python
"""Fetch real OHLCV bars into the layout the backtester reads.

    python scripts/fetch_ohlc.py --symbols BTC/USDT,ETH/USDT \
        --timeframe 1m --days 30

Writes ``{DATA_DIR}/{exchange}/{BASE-QUOTE}_{timeframe}.csv``.

Uses ``data-api.binance.vision``, Binance's public market-data host, rather
than ``api.binance.com``. The main API and Bitget both resolve to a single
ISP address from some networks (Malaysia among them) and time out; the data
host is served separately and stays reachable. No API key is involved -- this
endpoint is public and read-only.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List

import httpx

BASE_URL = "https://data-api.binance.vision/api/v3/klines"
MAX_LIMIT = 1000  # per request, imposed by the venue

TF_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def fetch(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> List[list]:
    """Page through klines until the window is covered."""
    step = TF_MS[timeframe]
    venue_symbol = symbol.replace("/", "")
    rows: List[list] = []
    cursor = start_ms
    with httpx.Client(timeout=30.0) as client:
        while cursor < end_ms:
            r = client.get(
                BASE_URL,
                params={
                    "symbol": venue_symbol,
                    "interval": timeframe,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": MAX_LIMIT,
                },
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            rows.extend(batch)
            last_open = batch[-1][0]
            # Advance past the last bar returned; a venue that returns fewer
            # rows than requested has reached the end of available history.
            cursor = last_open + step
            if len(batch) < MAX_LIMIT:
                break
            time.sleep(0.12)  # stay well inside the public rate limit
    return rows


def write_csv(rows: List[list], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    lines = ["timestamp,open,high,low,close,volume"]
    for k in rows:
        ts = int(k[0])
        if ts in seen:  # pagination can overlap on the boundary bar
            continue
        seen.add(ts)
        lines.append(
            f"{ts},{float(k[1])},{float(k[2])},{float(k[3])},{float(k[4])},{float(k[5])}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines) - 1


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", default="BTC/USDT")
    p.add_argument("--timeframe", default="1m", choices=sorted(TF_MS))
    p.add_argument("--days", type=float, default=30.0)
    p.add_argument("--data-dir", default="data")
    p.add_argument("--exchange", default="bitget", help="Subdirectory name only")
    args = p.parse_args(argv)

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(args.days * 86_400_000)
    root = Path(args.data_dir) / args.exchange

    total = 0
    for symbol in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        print(
            f"fetching {symbol} {args.timeframe} for {args.days:g} days ...",
            end=" ",
            flush=True,
        )
        try:
            rows = fetch(symbol, args.timeframe, start_ms, end_ms)
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED ({exc})")
            continue
        out = root / f"{symbol.replace('/', '-')}_{args.timeframe}.csv"
        n = write_csv(rows, out)
        total += n
        print(f"{n} bars -> {out}")

    if total == 0:
        print("no data fetched", file=sys.stderr)
        return 1
    print(f"\n{total} bars written under {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
