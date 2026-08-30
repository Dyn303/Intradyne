#!/usr/bin/env python
"""Pull months of bars from the Binance public archive into the bar cache.

    python scripts/fetch_klines_archive.py --symbols ETHUSDT,BTCUSDT \
        --timeframe 1m --from 2024-01 --to 2026-07 --out DIR

The REST endpoint in `fetch_ohlc.py` paginates 1000 bars at a time, which is
fine for days and hopeless for years. The archive publishes whole months as
zipped CSV instead: 1m bars are ~2MB a month, so multiple years cost less
bandwidth than a single day of aggTrades.

Klines also carry `taker_buy_base_volume`, the share of volume where a buyer
crossed the spread. That is the same aggressor split the tick loader
reconstructs from `was_buyer_maker`, so the order-flow signals survive the
move from ticks to bars:

    buy_volume  = taker_buy_base_volume
    sell_volume = volume - taker_buy_base_volume

Bars are placed on a complete grid with gaps forward-filled at last price and
zero volume. Without that a "3600 bar" horizon silently means something longer
than 3600 seconds, because quiet intervals are missing from the file.
"""

from __future__ import annotations

import argparse
import io
import zipfile
from pathlib import Path
from typing import List, Optional

import httpx
import numpy as np

BASE = "https://data.binance.vision/data/spot/monthly/klines"
TF_SECONDS = {"1s": 1, "1m": 60, "5m": 300, "15m": 900, "1h": 3600}
FIELDS = (
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "buy_volume",
    "sell_volume",
    "trades",
)


def months(start: str, end: str) -> List[str]:
    y0, m0 = (int(x) for x in start.split("-"))
    y1, m1 = (int(x) for x in end.split("-"))
    out = []
    while (y0, m0) <= (y1, m1):
        out.append(f"{y0:04d}-{m0:02d}")
        y0, m0 = (y0 + 1, 1) if m0 == 12 else (y0, m0 + 1)
    return out


def fetch_month(client: httpx.Client, symbol: str, tf: str, month: str):
    """Return the month's raw kline table, or None when not published."""
    import pandas as pd

    url = f"{BASE}/{symbol}/{tf}/{symbol}-{tf}-{month}.zip"
    r = client.get(url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    with z.open(z.namelist()[0]) as fh:
        head = fh.read(64)
    # Recent archives ship a header row; older ones do not.
    has_header = head[:1].isalpha()
    with z.open(z.namelist()[0]) as fh:
        return pd.read_csv(
            fh,
            header=0 if has_header else None,
            usecols=[0, 1, 2, 3, 4, 5, 8, 9],
            names=None
            if has_header
            else [
                "open_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_volume",
                "trades",
                "taker_buy_base",
                "taker_buy_quote",
                "ignore",
            ],
        )


def to_grid(df, tf: str) -> dict:
    """Normalise a month onto a gap-free bar grid."""
    import pandas as pd

    df = df.copy()
    df.columns = [
        "ts",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "trades",
        "taker_buy_base",
    ]
    ts = pd.to_numeric(df["ts"], errors="coerce").to_numpy(dtype="float64")
    ts = ts[np.isfinite(ts)]
    if len(ts) == 0:
        raise ValueError("no usable timestamps")
    # Binance switched these from milliseconds to microseconds partway
    # through; both appear in the archive, so scale by magnitude.
    scale = 1e6 if np.nanmedian(ts) > 1e14 else 1e3
    step = TF_SECONDS[tf]

    df = df[np.isfinite(pd.to_numeric(df["ts"], errors="coerce"))]
    sec = (pd.to_numeric(df["ts"]).to_numpy(dtype="float64") / scale).astype("int64")
    sec = (sec // step) * step

    lo, hi = int(sec.min()), int(sec.max())
    grid = np.arange(lo, hi + step, step, dtype="int64")
    pos = np.searchsorted(grid, sec)

    n = len(grid)
    out = {k: np.zeros(n) for k in FIELDS}
    out["ts"] = grid.astype(float)
    for name, col in (
        ("open", "open"),
        ("high", "high"),
        ("low", "low"),
        ("close", "close"),
        ("volume", "volume"),
        ("trades", "trades"),
        ("buy_volume", "taker_buy_base"),
    ):
        vals = pd.to_numeric(df[col], errors="coerce").to_numpy(dtype="float64")
        out[name][pos] = np.nan_to_num(vals)

    # Forward-fill price through empty intervals; volume stays zero, so a
    # quiet stretch cannot trigger a barrier or look like activity.
    present = np.zeros(n, dtype=bool)
    present[pos] = True
    idx = np.maximum.accumulate(np.where(present, np.arange(n), 0))
    for name in ("open", "high", "low", "close"):
        out[name] = out[name][idx]
    out["sell_volume"] = np.maximum(out["volume"] - out["buy_volume"], 0.0)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", default="ETHUSDT")
    p.add_argument("--timeframe", default="1m", choices=sorted(TF_SECONDS))
    p.add_argument("--from", dest="start", required=True, help="YYYY-MM")
    p.add_argument("--to", dest="end", required=True, help="YYYY-MM")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    cache = Path(args.out) / "bars"
    cache.mkdir(parents=True, exist_ok=True)
    want = months(args.start, args.end)

    with httpx.Client(timeout=300, follow_redirects=True) as client:
        for symbol in [s.strip() for s in args.symbols.split(",") if s.strip()]:
            got = 0
            for m in want:
                npz = cache / f"{symbol}-{args.timeframe}-{m}.npz"
                if npz.exists():
                    got += 1
                    continue
                try:
                    df = fetch_month(client, symbol, args.timeframe, m)
                except Exception as exc:  # noqa: BLE001
                    print(f"  {symbol} {m}: FAILED ({type(exc).__name__})")
                    continue
                if df is None:
                    print(f"  {symbol} {m}: not published")
                    continue
                try:
                    grid = to_grid(df, args.timeframe)
                except Exception as exc:  # noqa: BLE001
                    print(f"  {symbol} {m}: unusable ({exc})")
                    continue
                np.savez_compressed(npz, **grid)
                got += 1
                print(f"  {symbol} {m}: {len(grid['close']):,} bars", flush=True)
            print(f"{symbol} {args.timeframe}: {got}/{len(want)} months cached")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
