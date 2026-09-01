#!/usr/bin/env python
"""Build a point-in-time tradeable universe, survivorship included.

    python scripts/point_in_time_universe.py --cache-dir DIR --out docs/UNIVERSE_TIMELINE.md

A cross-sectional backtest is only as honest as its universe, and the obvious
way to build one is wrong twice over.

**Survivorship.** Taking today's trading pairs and running them back through
history selects coins *because they survived*. Of 671 spot USDT pairs Binance
has ever listed, 200 are dead -- 30% mortality, including WAVES, SRM, OMG,
REN, OCEAN and MATIC. A momentum strategy looks wonderful when everything that
went to zero has been quietly removed from the sample.

**Look-ahead on liquidity.** The screening worksheet measures volume *today*.
Using today's liquidity to decide what was tradeable in 2022 leaks the future
just as surely: a coin that is liquid now may have been untradeable then, and
one that was liquid then may have died since.

Both are fixed the same way -- membership is recomputed at every rebalance
date from data available on that date and no later. A name enters when it
lists and has enough history, and leaves when it stops trading. Crucially, a
name that dies is not deleted from history: it stays in every earlier
snapshot, so a strategy holding it eats the loss it actually took.

The REST endpoint still serves delisted symbols, which is what makes this
practical -- otherwise dead pairs would have to be reassembled from ~6000
monthly archive files.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import numpy as np

BINANCE = "https://data-api.binance.vision/api/v3"
ARCHIVE_S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DAY_MS = 86_400_000

#: Leveraged tokens are derivatives wearing a spot wrapper.
LEVERAGED = ("UP", "DOWN", "BULL", "BEAR")


def _get(c: httpx.Client, url: str, params=None, tries: int = 5) -> Optional[Any]:
    for i in range(tries):
        try:
            r = c.get(url, params=params)
            if r.status_code == 429:
                time.sleep(10)
                continue
            if r.status_code == 200:
                return r.json()
            return None
        except Exception:  # noqa: BLE001
            time.sleep(2 * (i + 1))
    return None


def every_usdt_pair(c: httpx.Client) -> List[str]:
    """Every USDT spot pair the archive has ever published, alive or dead.

    Taken from the archive rather than exchangeInfo precisely because
    exchangeInfo only knows about survivors.
    """
    syms: List[str] = []
    marker = None
    while True:
        p = {"delimiter": "/", "prefix": "data/spot/monthly/klines/"}
        if marker:
            p["marker"] = marker
        try:
            r = c.get(ARCHIVE_S3, params=p, timeout=180)
        except Exception:  # noqa: BLE001
            break
        got = re.findall(r"<Prefix>data/spot/monthly/klines/([^/]+)/</Prefix>", r.text)
        if not got:
            break
        syms += got
        if "<IsTruncated>true</IsTruncated>" not in r.text:
            break
        marker = f"data/spot/monthly/klines/{got[-1]}/"
    return [
        s
        for s in syms
        if s.endswith("USDT") and not any(s[:-4].endswith(x) for x in LEVERAGED)
    ]


def fetch_daily(c: httpx.Client, symbol: str) -> Optional[Dict[str, np.ndarray]]:
    """Full daily history: open time, close, quote volume."""
    rows: List[List[Any]] = []
    start = 0
    while True:
        k = _get(
            c,
            f"{BINANCE}/klines",
            {"symbol": symbol, "interval": "1d", "startTime": start, "limit": 1000},
        )
        if not k:
            break
        rows += k
        if len(k) < 1000:
            break
        start = int(k[-1][0]) + DAY_MS
        time.sleep(0.15)
    if not rows:
        return None
    seen = set()
    ts, close, qvol = [], [], []
    for r in rows:
        t = int(r[0])
        if t in seen:  # pagination can overlap on the boundary bar
            continue
        seen.add(t)
        ts.append(t)
        close.append(float(r[4]))
        qvol.append(float(r[7]))
    order = np.argsort(ts)
    return {
        "ts": np.array(ts, dtype="int64")[order],
        "close": np.array(close, dtype="float64")[order],
        "qvol": np.array(qvol, dtype="float64")[order],
    }


def build_panel(
    c: httpx.Client, symbols: List[str], cache_dir: Path, progress_every: int = 25
) -> Dict[str, Dict[str, np.ndarray]]:
    """Fetch or load daily series for every symbol, checkpointed per symbol."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    panel: Dict[str, Dict[str, np.ndarray]] = {}
    for i, s in enumerate(symbols):
        f = cache_dir / f"{s}.npz"
        if f.exists():
            z = np.load(f)
            panel[s] = {k: z[k] for k in ("ts", "close", "qvol")}
            continue
        d = fetch_daily(c, s)
        if d is not None:
            np.savez_compressed(f, **d)
            panel[s] = d
        if i % progress_every == 0:
            print(f"  {i}/{len(symbols)} ({len(panel)} with data)", flush=True)
    return panel


def universe_at(
    panel,
    as_of_ms: int,
    min_history_days: int,
    min_quote_volume: float,
    lookback_days: int,
    stale_days: int,
) -> List[str]:
    """Names tradeable at ``as_of_ms``, judged only on data available by then.

    A name qualifies when it has enough history, is still trading (it has a
    bar within ``stale_days``), and cleared the liquidity floor on median
    volume over the trailing window -- all measured as of the date in
    question, never from today.
    """
    lo = as_of_ms - lookback_days * DAY_MS
    out = []
    for sym, d in panel.items():
        ts = d["ts"]
        past = ts <= as_of_ms
        if not past.any():
            continue
        n_hist = int(past.sum())
        if n_hist < min_history_days:
            continue
        # Still listed? A dead coin stops producing bars.
        if as_of_ms - int(ts[past][-1]) > stale_days * DAY_MS:
            continue
        window = past & (ts >= lo)
        if not window.any():
            continue
        if float(np.median(d["qvol"][window])) < min_quote_volume:
            continue
        out.append(sym)
    return sorted(out)


def month_ends(panel, step_days: int) -> List[int]:
    """Rebalance dates spanning the data, in epoch ms."""
    if not panel:
        return []
    lo = min(int(d["ts"][0]) for d in panel.values())
    hi = max(int(d["ts"][-1]) for d in panel.values())
    t, out = lo, []
    while t <= hi:
        out.append(t)
        t += step_days * DAY_MS
    return out


def summarise(panel, dates, members: Dict[int, List[str]]) -> str:
    live_now = {
        s
        for s, d in panel.items()
        if dates and int(d["ts"][-1]) >= dates[-1] - 30 * DAY_MS
    }
    ever = set()
    for v in members.values():
        ever |= set(v)
    died = sorted(s for s in ever if s not in live_now)

    L = ["# Point-in-time universe\n"]
    L.append(
        "Generated by `scripts/point_in_time_universe.py`. Membership is "
        "recomputed at each rebalance date from data available on that "
        "date only.\n"
    )
    L.append("Two biases this exists to remove:\n")
    L.append(
        "- **Survivorship.** Building a universe from today's trading "
        "pairs selects coins because they survived. Dead names stay in "
        "every snapshot they belonged to, so a strategy holding one takes "
        "the loss it actually took."
    )
    L.append(
        "- **Look-ahead on liquidity.** Volume is measured in a window "
        "ending at the rebalance date, never from today, so a coin that is "
        "liquid now is not retroactively tradeable in 2022.\n"
    )
    sizes = [len(members[d]) for d in dates if d in members]
    if sizes:
        L.append("| | |\n|---|---|")
        L.append(f"| symbols with data | {len(panel)} |")
        L.append(f"| rebalance dates | {len(dates)} |")
        L.append(f"| universe size, first date | {sizes[0]} |")
        L.append(f"| universe size, last date | {sizes[-1]} |")
        L.append(f"| universe size, median | {int(np.median(sizes))} |")
        L.append(f"| names ever included | {len(ever)} |")
        L.append(
            f"| of those, no longer trading | {len(died)} "
            f"({len(died) / max(1, len(ever)):.0%}) |\n"
        )
    L.append("\n## Universe size over time\n")
    L.append("| date | names |\n|---|---|")
    for d in dates:
        if d in members:
            L.append(
                f"| {datetime.fromtimestamp(d / 1000, timezone.utc):%Y-%m-%d} "
                f"| {len(members[d])} |"
            )
    L.append("\n## Names that entered the universe and later stopped trading\n")
    L.append(
        "These are the reason the exercise matters: a survivorship-biased "
        "universe contains none of them.\n"
    )
    L.append("| symbol | last bar |\n|---|---|")
    for s in died:
        last = datetime.fromtimestamp(int(panel[s]["ts"][-1]) / 1000, timezone.utc)
        L.append(f"| {s[:-4]} | {last:%Y-%m-%d} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--cache-dir",
        required=True,
        help="Where daily series are cached (large; keep outside the repo)",
    )
    p.add_argument("--out", default="docs/UNIVERSE_TIMELINE.md")
    p.add_argument("--json-out", default="docs/universe_timeline.json")
    p.add_argument("--min-history-days", type=int, default=180)
    p.add_argument("--min-quote-volume", type=float, default=300_000)
    p.add_argument("--lookback-days", type=int, default=30)
    p.add_argument(
        "--stale-days",
        type=int,
        default=7,
        help="No bar within this many days means it stopped trading",
    )
    p.add_argument("--step-days", type=int, default=30)
    p.add_argument("--limit", type=int, default=0, help="Cap symbols, for a smoke run")
    args = p.parse_args(argv)

    with httpx.Client(timeout=90, headers={"accept": "application/json"}) as c:
        print("enumerating every USDT pair ever listed ...", flush=True)
        syms = every_usdt_pair(c)
        if args.limit:
            syms = syms[: args.limit]
        print(f"  {len(syms)} pairs (alive and dead)")
        print("fetching daily history ...", flush=True)
        panel = build_panel(c, syms, Path(args.cache_dir))
        print(f"  {len(panel)} symbols with data")

    dates = month_ends(panel, args.step_days)
    members = {
        d: universe_at(
            panel,
            d,
            args.min_history_days,
            args.min_quote_volume,
            args.lookback_days,
            args.stale_days,
        )
        for d in dates
    }
    members = {d: v for d, v in members.items() if v}
    dates = [d for d in dates if d in members]

    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(
        json.dumps(
            {
                datetime.fromtimestamp(d / 1000, timezone.utc).strftime("%Y-%m-%d"): [
                    s[:-4] for s in members[d]
                ]
                for d in dates
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(summarise(panel, dates, members), encoding="utf-8")
    print(f"\nwrote {args.out} and {args.json_out}")
    if dates:
        print(
            f"universe: {len(members[dates[0]])} names at "
            f"{datetime.fromtimestamp(dates[0] / 1000, timezone.utc):%Y-%m}, "
            f"{len(members[dates[-1]])} at "
            f"{datetime.fromtimestamp(dates[-1] / 1000, timezone.utc):%Y-%m}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
