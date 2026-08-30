#!/usr/bin/env python
"""Screen many entry signals against real ticks, with a selection-bias guard.

    python scripts/strategy_search.py --data DIR --train 2026-08-26,2026-08-27 \\
        --test 2026-08-28,2026-08-29

Testing 50 strategies and reporting the best 5 will always produce five
profitable-looking strategies, whether or not any edge exists: the maximum of
50 noisy estimates is biased upward. This script therefore does three things
that a naive screen does not.

1. **Shared exit mechanics.** The forward outcome of entering at every bar is
   computed once, so strategies differ only in *when they enter*. Nothing can
   win by accidentally getting a different exit policy.
2. **Held-out days.** Strategies are ranked on training days and re-measured
   on days never used for ranking. In-sample rank means nothing on its own.
3. **A null threshold.** Random entry signals with matched trade counts are
   drawn to build the distribution of "best of 50 under no edge". A strategy
   must beat that threshold, not merely beat zero.

Costs are charged on every round trip, so a signal with no predictive power
lands near minus the round-trip cost rather than near zero.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# --------------------------------------------------------------------------
# Data: aggregated trades -> 1s bars carrying order flow
# --------------------------------------------------------------------------


@dataclass
class Bars:
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    buy_volume: np.ndarray  # volume where a buyer lifted the offer
    sell_volume: np.ndarray  # volume where a seller hit the bid
    trades: np.ndarray

    def __len__(self) -> int:
        return len(self.close)


def load_bars(path: Path, seconds: int = 1) -> Bars:
    """Aggregate trades into bars, keeping the aggressor split.

    `was_buyer_maker` gives the side that crossed, which is the only way to
    recover order flow. Bar OHLCV alone cannot express it, and it is the one
    genuinely new signal family that tick data makes available.
    """
    import pandas as pd

    df = pd.read_csv(
        path,
        header=None,
        usecols=[1, 2, 5, 6],
        names=["price", "qty", "ts", "buyer_maker"],
        dtype={"price": "float64", "qty": "float64", "ts": "int64"},
    )
    # Binance publishes these in microseconds.
    df["ts"] = df["ts"] / 1e6
    df["bucket"] = (df["ts"] // seconds).astype("int64")
    maker = df["buyer_maker"].astype(str).str.lower().isin(("true", "1"))
    df["buy_qty"] = df["qty"].where(~maker, 0.0)  # a buyer lifted the offer
    df["sell_qty"] = df["qty"].where(maker, 0.0)  # a seller hit the bid

    g = df.groupby("bucket", sort=True)
    agg = g.agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        volume=("qty", "sum"),
        buy_volume=("buy_qty", "sum"),
        sell_volume=("sell_qty", "sum"),
        trades=("price", "size"),
    )
    return Bars(
        ts=agg.index.to_numpy(dtype=float) * seconds,
        open=agg["open"].to_numpy(dtype=float),
        high=agg["high"].to_numpy(dtype=float),
        low=agg["low"].to_numpy(dtype=float),
        close=agg["close"].to_numpy(dtype=float),
        volume=agg["volume"].to_numpy(dtype=float),
        buy_volume=agg["buy_volume"].to_numpy(dtype=float),
        sell_volume=agg["sell_volume"].to_numpy(dtype=float),
        trades=agg["trades"].to_numpy(dtype=float),
    )


# --------------------------------------------------------------------------
# Shared exit simulation
# --------------------------------------------------------------------------


def forward_outcomes(
    bars: Bars, tp_bps: float, sl_bps: float, horizon: int, cost_bps: float
) -> Tuple[np.ndarray, np.ndarray]:
    """Net return in bps of entering at each bar, and the bars held.

    Walks the horizon once per step rather than materialising a sliding
    window, so memory stays flat. When a bar's range spans both the target
    and the stop, the stop is assumed to hit first -- the pessimistic reading,
    since bar data cannot say which came first.
    """
    n = len(bars)
    entry = bars.close
    tp_px = entry * (1.0 + tp_bps / 1e4)
    sl_px = entry * (1.0 - sl_bps / 1e4)

    done = np.zeros(n, dtype=bool)
    gross = np.full(n, np.nan)
    held = np.zeros(n, dtype=int)

    for k in range(1, horizon + 1):
        idx = np.arange(n) + k
        valid = (idx < n) & ~done
        if not valid.any():
            break
        j = np.where(valid, idx, 0)

        hit_sl = valid & (bars.low[j] <= sl_px)
        hit_tp = valid & (bars.high[j] >= tp_px) & ~hit_sl

        gross[hit_sl] = -sl_bps
        gross[hit_tp] = tp_bps
        held[hit_sl | hit_tp] = k
        done |= hit_sl | hit_tp

    # Anything still open exits at the horizon, or at the last bar available.
    open_idx = np.where(~done)[0]
    if len(open_idx):
        exit_idx = np.minimum(open_idx + horizon, n - 1)
        gross[open_idx] = (bars.close[exit_idx] / entry[open_idx] - 1.0) * 1e4
        held[open_idx] = exit_idx - open_idx

    return gross - cost_bps, held


# --------------------------------------------------------------------------
# Signal library
# --------------------------------------------------------------------------


def _roll_mean(x: np.ndarray, n: int) -> np.ndarray:
    c = np.cumsum(np.insert(x, 0, 0.0))
    out = np.full_like(x, np.nan, dtype=float)
    out[n - 1 :] = (c[n:] - c[:-n]) / n
    return out


def _roll_std(x: np.ndarray, n: int) -> np.ndarray:
    m = _roll_mean(x, n)
    m2 = _roll_mean(x * x, n)
    return np.sqrt(np.maximum(m2 - m * m, 0.0))


def _roll_max(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=float)
    if len(x) >= n:
        w = np.lib.stride_tricks.sliding_window_view(x, n)
        out[n - 1 :] = w.max(axis=1)
    return out


def _roll_min(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=float)
    if len(x) >= n:
        w = np.lib.stride_tricks.sliding_window_view(x, n)
        out[n - 1 :] = w.min(axis=1)
    return out


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    a = 2.0 / (n + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


SignalFn = Callable[[Bars], np.ndarray]


def build_strategies() -> Dict[str, SignalFn]:
    """Fifty entry signals across eight families.

    Deliberately not fifty parameterisations of one idea -- that is a sweep,
    and this session already ran those. Order flow (the aggressor split) is
    the family that only tick data makes available.
    """
    S: Dict[str, SignalFn] = {}

    # --- momentum: recent return exceeds a threshold ----------------------
    for n in (5, 15, 30, 60, 120):
        for k in (1.0, 2.0):

            def f(b, n=n, k=k):
                r = np.full(len(b), np.nan)
                r[n:] = (b.close[n:] / b.close[:-n] - 1.0) * 1e4
                s = _roll_std(np.diff(b.close, prepend=b.close[0]) / b.close * 1e4, 60)
                return r > k * s * np.sqrt(n)

            S[f"mom_{n}s_k{k:g}"] = f

    # --- mean reversion: price stretched below its mean -------------------
    for n in (15, 30, 60, 120, 300):
        for k in (1.5, 2.5):

            def f(b, n=n, k=k):
                m = _roll_mean(b.close, n)
                sd = _roll_std(b.close, n)
                return b.close < m - k * sd

            S[f"revert_{n}s_k{k:g}"] = f

    # --- breakout: new high of the lookback -------------------------------
    for n in (30, 60, 120, 300):

        def f(b, n=n):
            return b.close >= _roll_max(b.high, n)

        S[f"breakout_{n}s"] = f

    # --- fade the extreme: new low of the lookback ------------------------
    for n in (30, 60, 120, 300):

        def f(b, n=n):
            return b.close <= _roll_min(b.low, n)

        S[f"fade_low_{n}s"] = f

    # --- order flow imbalance (only available from tick data) -------------
    for n in (10, 30, 60, 120):
        for thr in (0.2, 0.4):

            def f(b, n=n, thr=thr):
                buy = _roll_mean(b.buy_volume, n)
                sell = _roll_mean(b.sell_volume, n)
                tot = buy + sell
                imb = np.where(tot > 0, (buy - sell) / np.maximum(tot, 1e-12), 0.0)
                return imb > thr

            S[f"ofi_{n}s_>{thr:g}"] = f

    # --- contrarian order flow: fade heavy selling ------------------------
    for n in (10, 30, 60, 120):
        for thr in (0.2, 0.4):

            def f(b, n=n, thr=thr):
                buy = _roll_mean(b.buy_volume, n)
                sell = _roll_mean(b.sell_volume, n)
                tot = buy + sell
                imb = np.where(tot > 0, (buy - sell) / np.maximum(tot, 1e-12), 0.0)
                return imb < -thr

            S[f"ofi_fade_{n}s_<-{thr:g}"] = f

    # --- EMA cross --------------------------------------------------------
    for fast, slow in ((5, 30), (10, 60), (30, 120), (60, 300)):

        def f(b, fast=fast, slow=slow):
            ef, es = _ema(b.close, fast), _ema(b.close, slow)
            up = ef > es
            return up & ~np.roll(up, 1)

        S[f"ema_{fast}x{slow}"] = f

    # --- volatility regime ------------------------------------------------
    for n in (30, 60, 120):
        for hi in (True, False):

            def f(b, n=n, hi=hi):
                ret = np.diff(b.close, prepend=b.close[0]) / b.close
                v = _roll_std(ret, n)
                vm = _roll_mean(v, 300)
                return (v > 1.5 * vm) if hi else (v < 0.7 * vm)

            S[f"vol_{'high' if hi else 'low'}_{n}s"] = f

    # --- trade intensity spike -------------------------------------------
    for n in (10, 30, 60):

        def f(b, n=n):
            t = _roll_mean(b.trades, n)
            base = _roll_mean(b.trades, 300)
            return t > 2.0 * base

        S[f"intensity_{n}s"] = f

    # --- VWAP deviation ---------------------------------------------------
    for n in (60, 300):
        for k in (1.0, 2.0):

            def f(b, n=n, k=k):
                pv = _roll_mean(b.close * b.volume, n)
                v = _roll_mean(b.volume, n)
                vwap = np.where(v > 0, pv / np.maximum(v, 1e-12), b.close)
                sd = _roll_std(b.close, n)
                return b.close < vwap - k * sd

            S[f"vwap_dev_{n}s_k{k:g}"] = f

    return S


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def evaluate(
    mask: np.ndarray, net: np.ndarray, held: np.ndarray, min_gap: int
) -> Dict[str, float]:
    """Apply a signal, enforcing that trades cannot overlap."""
    idx = np.where(mask & np.isfinite(net))[0]
    picked: List[int] = []
    busy_until = -1
    for i in idx:
        if i <= busy_until:
            continue
        picked.append(i)
        busy_until = i + max(int(held[i]), min_gap)
    if not picked:
        return {"trades": 0, "mean_bps": 0.0, "win_rate": 0.0, "total_bps": 0.0}
    r = net[picked]
    return {
        "trades": len(picked),
        "mean_bps": float(r.mean()),
        "win_rate": float((r > 0).mean()),
        "total_bps": float(r.sum()),
    }


def null_threshold(
    net: np.ndarray,
    held: np.ndarray,
    trade_counts: List[int],
    n_strategies: int,
    draws: int,
    rng: np.random.Generator,
    min_gap: int,
) -> float:
    """The best-of-N mean return achievable by random entries.

    A strategy must clear this, not merely clear zero: the maximum of N noisy
    estimates drifts upward with N even when every one of them is worthless.
    """
    finite = np.where(np.isfinite(net))[0]
    best_of_each_draw = []
    for _ in range(draws):
        best = -np.inf
        for _ in range(n_strategies):
            k = trade_counts[rng.integers(len(trade_counts))]
            if k <= 0 or k > len(finite):
                continue
            # Sampled with replacement: at k of order 1e3 against 1e5
            # candidate bars, collisions are rare enough not to shift the
            # mean, and this is ~100x cheaper than a permutation.
            pick = finite[rng.integers(0, len(finite), size=k)]
            best = max(best, float(net[pick].mean()))
        if np.isfinite(best):
            best_of_each_draw.append(best)
    return float(np.percentile(best_of_each_draw, 95)) if best_of_each_draw else 0.0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True, help="Directory of aggTrades CSVs")
    p.add_argument("--train", required=True, help="Comma-separated dates")
    p.add_argument("--test", required=True, help="Comma-separated dates")
    p.add_argument("--symbol", default="ETHUSDT")
    p.add_argument("--tp-bps", type=float, default=40.0)
    p.add_argument("--sl-bps", type=float, default=20.0)
    p.add_argument("--horizon", type=int, default=300, help="Max bars held")
    p.add_argument("--cost-bps", type=float, default=14.0)
    p.add_argument("--bar-seconds", type=int, default=1)
    p.add_argument("--min-trades", type=int, default=100)
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--null-draws", type=int, default=200)
    args = p.parse_args(argv)

    root = Path(args.data)

    def load_days(days: str) -> Bars:
        parts = []
        for d in [x.strip() for x in days.split(",") if x.strip()]:
            f = root / f"{args.symbol}-aggTrades-{d}.csv"
            if not f.exists():
                print(f"  missing {f.name}")
                continue
            b = load_bars(f, args.bar_seconds)
            print(f"  {d}: {len(b):,} bars")
            parts.append(b)
        if not parts:
            raise SystemExit("no data loaded")
        return Bars(
            **{
                k: np.concatenate([getattr(b, k) for b in parts])
                for k in (
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
            }
        )

    print("loading training days")
    train = load_days(args.train)
    print("loading held-out days")
    test = load_days(args.test)

    strategies = build_strategies()
    print(
        f"\n{len(strategies)} strategies | tp={args.tp_bps:g} sl={args.sl_bps:g} "
        f"horizon={args.horizon}s cost={args.cost_bps:g}bps"
    )

    net_tr, held_tr = forward_outcomes(
        train, args.tp_bps, args.sl_bps, args.horizon, args.cost_bps
    )
    net_te, held_te = forward_outcomes(
        test, args.tp_bps, args.sl_bps, args.horizon, args.cost_bps
    )
    base_tr = float(np.nanmean(net_tr))
    print(
        f"entering at random on training data returns {base_tr:+.2f} bps "
        f"(cost is {args.cost_bps:g})"
    )

    rows = []
    for name, fn in strategies.items():
        try:
            mask = np.asarray(fn(train), dtype=bool)
        except Exception as exc:  # noqa: BLE001
            print(f"  {name}: failed ({exc})")
            continue
        r = evaluate(mask, net_tr, held_tr, args.bar_seconds)
        if r["trades"] < args.min_trades:
            continue
        rows.append((name, r))

    if not rows:
        print("no strategy produced enough trades")
        return 1

    rows.sort(key=lambda kv: -kv[1]["mean_bps"])
    counts = [r["trades"] for _, r in rows]
    rng = np.random.default_rng(7)
    thr = null_threshold(
        net_tr, held_tr, counts, len(rows), args.null_draws, rng, args.bar_seconds
    )
    print(
        f"\nbest-of-{len(rows)} under no edge (95th pct of random entries): "
        f"{thr:+.2f} bps"
    )
    print("a strategy must clear this to be worth a second look\n")

    header = (
        f"{'#':>2} {'strategy':24} {'trades':>7} {'win':>7} {'train bps':>10} "
        f"{'test bps':>9} {'test n':>7}  verdict"
    )
    print(header)
    print("-" * len(header))
    for i, (name, r) in enumerate(rows[: args.top], 1):
        te = evaluate(
            np.asarray(build_strategies()[name](test), dtype=bool),
            net_te,
            held_te,
            args.bar_seconds,
        )
        if r["mean_bps"] <= thr:
            verdict = "within noise"
        elif te["trades"] < args.min_trades:
            verdict = "test sample too small"
        elif te["mean_bps"] > 0:
            verdict = "HOLDS OUT"
        else:
            verdict = "fails out of sample"
        print(
            f"{i:>2} {name:24} {r['trades']:7d} {r['win_rate']:7.1%} "
            f"{r['mean_bps']:+10.2f} {te['mean_bps']:+9.2f} {te['trades']:7d}  {verdict}"
        )

    survivors = [
        name
        for name, r in rows[: args.top]
        if r["mean_bps"] > thr
        and evaluate(
            np.asarray(build_strategies()[name](test), dtype=bool),
            net_te,
            held_te,
            args.bar_seconds,
        )["mean_bps"]
        > 0
    ]
    print()
    if survivors:
        print(f"{len(survivors)} of the top {args.top} clear the null and hold out:")
        for s in survivors:
            print(f"  {s}")
    else:
        print(
            "None of the top strategies both clear the null threshold and hold "
            "up out of sample.\nThe ranking above is what selection bias looks "
            "like: the best of many noisy\nestimates, not an edge."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
