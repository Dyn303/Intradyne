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

*Two sources, because one of them cannot serve the history.* Alpha Vantage
answers most of this project's equity needs from one key -- `LISTING_STATUS`
for the survivorship-free universe, and the fundamentals `screen_equities.py`
screens on. But `TIME_SERIES_INTRADAY` with a `month` parameter is a **premium
endpoint**, so historical intraday is unavailable on the free key and every
request for a past month returns a quota notice rather than bars.

Twelve Data serves it on its free tier. Its 30-minute history begins
2019-09-16, and the earliest date actually retrievable is a little later than
the metadata advertises -- October 2019 returns nothing while November works,
so a window should start from what a fetch proves rather than from what the
provider claims. Prices are split-adjusted: AAPL reads ~$80 in June 2020, its
post-4:1 equivalent, so no separate adjustment step is needed.

Choose with `--source`. Alpha Vantage remains the default because it is the key
already in `.env.example`, and it is the right source for anything a premium
plan covers.

*Chunked, and cached per chunk.* Alpha Vantage is addressed one calendar month
at a time; Twelve Data by date range, up to 5,000 bars, which is about eighteen
months of 30-minute bars -- so months are grouped into years and a symbol's
seven years cost seven requests rather than eighty-two. Either way each chunk
lands in its own CSV and is skipped if present, so an interrupted run resumes
without re-downloading, the same shape `fetch_klines_archive.py` uses for its
npz cache. The per-symbol CSV the gates read is concatenated from those parts.

*The rate limit is enforced, not documented.* Twelve Data's free tier allows
eight requests a minute; `--sleep` is raised to that floor automatically,
because running faster earns a stream of 429s and a half-built panel that looks
like a fetched one.

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
TD = "https://api.twelvedata.com/time_series"

#: Twelve Data's free tier allows 8 requests a minute. One month of 30-minute
#: bars is ~273 rows, so months are batched into year-long chunks: 7 requests
#: per symbol for the ~6.8 years of history the provider serves, rather than 82.
TD_CHUNK_MONTHS = 12
TD_SLEEP_S = 7.6  # 60/8, plus a margin

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


def month_span(chunk: List[str]) -> Tuple[str, str]:
    """First day of the first month, last day of the last, as YYYY-MM-DD."""
    a = datetime.strptime(chunk[0], "%Y-%m")
    b = datetime.strptime(chunk[-1], "%Y-%m")
    if b.month == 12:
        end = datetime(b.year + 1, 1, 1)
    else:
        end = datetime(b.year, b.month + 1, 1)
    return a.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def fetch_twelvedata(
    c: httpx.Client, key: str, symbol: str, interval: str, chunk: List[str]
) -> Optional[List[Tuple[str, str, str, str, str, str]]]:
    """One date range from Twelve Data, newest-first, returned oldest-first.

    Alpha Vantage serves historical intraday only on a premium plan, which is
    why this source exists. Twelve Data's own history starts 2019-09-16 and its
    prices are split-adjusted -- AAPL reads ~$80 in June 2020, its post-4:1
    equivalent -- so the series is usable without a separate adjustment step.

    JSON rather than CSV: the CSV form is semicolon-delimited and a company
    name containing one would shift every field, which is the trap
    `screen_equities.py` already records for the listing endpoint.
    """
    start, end = month_span(chunk)
    interval_td = interval.replace("60min", "1h")
    try:
        r = c.get(
            TD,
            params={
                "symbol": symbol,
                "interval": interval_td,
                "start_date": start,
                "end_date": end,
                "outputsize": "5000",
                "format": "JSON",
                "apikey": key,
            },
            timeout=120.0,
        )
    except Exception:  # noqa: BLE001
        return None
    if r.status_code != 200:
        return None
    body = r.json()
    if not isinstance(body, dict) or body.get("status") == "error":
        return None
    values = body.get("values") or []
    rows: List[Tuple[str, str, str, str, str, str]] = []
    for rec in values:
        ts = str(rec.get("datetime") or "").strip()
        if not ts:
            continue
        rows.append(
            (
                ts,
                str(rec.get("open", "")),
                str(rec.get("high", "")),
                str(rec.get("low", "")),
                str(rec.get("close", "")),
                str(rec.get("volume", "") or "0"),
            )
        )
    rows.sort()
    return rows or None


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
    ap.add_argument(
        "--source",
        default="alphavantage",
        choices=("alphavantage", "twelvedata"),
        help="twelvedata serves historical intraday on its free tier; "
        "alphavantage requires a premium plan for it",
    )
    args = ap.parse_args(argv)

    td = args.source == "twelvedata"
    env = "TWELVEDATA_API_KEY" if td else "ALPHAVANTAGE_API_KEY"
    key = os.getenv(env, "").strip()
    if not key:
        print(f"{env} is not set; see .env.example", flush=True)
        return 1
    if td and args.sleep < TD_SLEEP_S:
        # Eight requests a minute on the free tier. Running faster earns a
        # stream of 429s and a half-built panel, so the floor is enforced
        # rather than left to whoever types the command.
        args.sleep = TD_SLEEP_S

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.limit:
        symbols = symbols[: args.limit]
    want = months(args.start, args.end)
    data_dir = Path(args.data_dir)
    parts_dir = data_dir / "months"
    parts_dir.mkdir(parents=True, exist_ok=True)

    # Alpha Vantage is addressed one month at a time; Twelve Data by date
    # range, so months are grouped. Either way a chunk is cached under its own
    # name, so an interrupted run resumes rather than restarting -- the same
    # checkpoint discipline `build_universe.py` uses per ticker.
    if td:
        chunks = [
            want[i : i + TD_CHUNK_MONTHS] for i in range(0, len(want), TD_CHUNK_MONTHS)
        ]
    else:
        chunks = [[m] for m in want]

    total = 0
    with httpx.Client(timeout=120, headers={"accept": "application/json"}) as c:
        for symbol in symbols:
            got = 0
            for chunk in chunks:
                tag = chunk[0] if len(chunk) == 1 else f"{chunk[0]}_{chunk[-1]}"
                part = parts_dir / f"{symbol}-{args.interval}-{tag}.csv"
                if part.exists():
                    got += 1
                    continue
                print(f"{symbol} {args.interval} {tag} ...", end=" ", flush=True)
                try:
                    if td:
                        rows = fetch_twelvedata(c, key, symbol, args.interval, chunk)
                    else:
                        rows = fetch_month(c, key, symbol, args.interval, chunk[0])
                except Exception as exc:  # noqa: BLE001
                    print(f"FAILED ({type(exc).__name__})", flush=True)
                    continue
                if not rows:
                    print("no data", flush=True)
                    time.sleep(args.sleep)
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
            print(
                f"{symbol}: {got}/{len(chunks)} chunks, {n} bars -> {out}",
                flush=True,
            )

    if total == 0:
        print("no data fetched", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
