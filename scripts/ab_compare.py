"""Compare two concurrently-running execution arms.

The first attempt at Stage 1 of `docs/PERFORMANCE_IMPROVEMENT_PLAN.md` ran
maker execution *after* the taker baseline rather than beside it, and the
result looked excellent: +3.20 bps gross against -0.57, t = +2.31, profit
factor 5.27. It was an artefact. The maker run covered 23:01-10:04 while the
control ran through the trading day, and every dispersion measure in the maker
sample was about half the control's -- gross sd 6.19 against 13.18, median
absolute move 2.34 against 4.37. A quieter market, not better execution.

So the arms now run concurrently on the same symbols against the same clock,
with separate portfolios and ledgers, and this reads both.

## The metric that decides it is not per-trade

Maker execution takes *fewer* trades: one resting order per symbol at a time
means a signal arriving while an order rests is dropped, and in an 11-hour run
that was 187 suppressed against 20 filled. A per-trade average silently
rewards that selectivity -- discard the worst 90% of trades by any rule and
per-trade P&L improves, having earned nothing.

So the headline here is **per signal**, counting a suppressed signal as a
trade that returned zero, and **per hour**, which is what an account actually
experiences. Per-trade figures are printed as secondary and labelled.

Usage:
    python scripts/ab_compare.py
    python scripts/ab_compare.py --a data/ab_taker_ledger.jsonl --b ...
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _read(path: Path) -> Tuple[List[float], int, float]:
    """Returns (gross bps per closed trade, suppressed signals, span hours)."""
    gross: List[float] = []
    suppressed = 0
    stamps: List[float] = []
    if not path.exists():
        return gross, suppressed, 0.0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload: Dict[str, Any] = d if isinstance(d, dict) else {}
        ts = payload.get("ts")
        if isinstance(ts, (int, float)) and ts > 0:
            stamps.append(float(ts))
        if payload.get("event") == "trade_mfe_mae":
            e, x = payload.get("entry"), payload.get("exit")
            if isinstance(e, (int, float)) and isinstance(x, (int, float)) and e > 0:
                gross.append((x / e - 1) * 10_000.0)
        elif payload.get("action") == "resting_order_exists":
            suppressed += 1
    span = (max(stamps) - min(stamps)) / 3600.0 if len(stamps) > 1 else 0.0
    return gross, suppressed, span


def _summarise(name: str, gross: List[float], suppressed: int, hours: float) -> None:
    n = len(gross)
    print(f"\n{name}")
    if n == 0:
        print("  no closed trades yet")
        return
    m = st.mean(gross)
    sd = st.stdev(gross) if n > 1 else 0.0
    wins = [g for g in gross if g > 0]
    losses = [g for g in gross if g <= 0]
    pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else float("inf")
    signals = n + suppressed

    print(f"  closed trades     : {n}")
    print(f"  suppressed signals: {suppressed}")
    print(f"  runtime           : {hours:.2f} h")
    print("  -- headline --")
    # A suppressed signal is a trade the strategy asked for and did not get.
    # Scoring it zero is what stops selectivity from looking like skill.
    print(f"  gross per signal  : {m * n / signals if signals else 0:+7.3f} bps")
    print(f"  gross per hour    : {m * n / hours if hours else 0:+7.2f} bps")
    print("  -- per trade (secondary; favours the more selective arm) --")
    print(f"  gross mean        : {m:+7.3f} bps   sd {sd:.2f}")
    if n > 1 and sd > 0:
        print(f"  t vs zero         : {m / (sd / math.sqrt(n)):+7.2f}")
    print(f"  win rate          : {100 * len(wins) / n:6.1f}%")
    print(f"  profit factor     : {pf:7.2f}")


def _welch(a: List[float], b: List[float]) -> None:
    if len(a) < 2 or len(b) < 2:
        print("\nnot enough trades in both arms to compare")
        return
    ma, mb = st.mean(a), st.mean(b)
    va, vb = st.variance(a), st.variance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    print("\n== difference, per trade ==")
    print(f"  B - A: {mb - ma:+.3f} bps")
    if se == 0:
        return
    t = (mb - ma) / se
    print(f"  Welch t: {t:+.2f}  (unequal variances, which the first attempt had)")
    # Resolvable effect at this sample size, so a null reads as "too early"
    # rather than "no difference" -- they are not the same finding.
    print(f"  resolvable at 2 sigma: {2 * se:.2f} bps")
    if abs(t) < 2:
        print("  -> indistinguishable. Not evidence of equality; evidence of")
        print("     insufficient data, unless the resolvable effect above is")
        print("     already smaller than a difference worth acting on.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="data/ab_taker_ledger.jsonl")
    ap.add_argument("--b", default="data/ab_maker_ledger.jsonl")
    # The arms were hardcoded as "taker" and "maker entries" for the Stage 1
    # execution test. Stage 2 reuses this script for strategy-vs-random, where
    # those labels name the wrong thing entirely -- and a mislabelled arm is
    # how a control gets read as a treatment.
    ap.add_argument("--label-a", default="A  (arm A)")
    ap.add_argument("--label-b", default="B  (arm B)")
    args = ap.parse_args()

    ga, sa, ha = _read(Path(args.a))
    gb, sb, hb = _read(Path(args.b))

    print("== concurrent A/B ==")
    print("same symbols, same clock, separate portfolios and ledgers")
    print(f"  A: {Path(args.a).name}")
    print(f"  B: {Path(args.b).name}")
    _summarise(args.label_a, ga, sa, ha)
    _summarise(args.label_b, gb, sb, hb)
    _welch(ga, gb)

    print("\n== what this design still does not control ==")
    print("  Maker fills are a selected subsample even within one period: an")
    print("  entry lands only when the market comes down to a resting bid, so")
    print("  the arms trade at different moments within the same hour. Scoring")
    print("  suppressed signals at zero bounds that effect; it does not remove")
    print("  it. A difference here is a difference between two whole execution")
    print("  policies, not between two fill prices for the same trade.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
