#!/usr/bin/env python
"""A1: does an equity strategy's expected move clear its own round-trip cost?

    python scripts/equity_feasibility.py --data data/equities

This is the gate `docs/STRATEGY_RESEARCH_FRAMEWORK.md` puts before candidate
one, and it is the measurement that would have saved this project the most
time had it existed for crypto. There, the answer was decisive and negative:
ETH moved 3.5 bps over the intended two-minute hold against a 14 bps round
trip, so the fee was four times the entire expected move (MIGRATION.md:436).
No entry rule can survive that, and fifty of them were tested anyway.

The same computation is run here for US equities. Two things differ and both
help: the round trip is roughly a third of crypto's taker cost, and a session
compresses a day of movement into 6.5 hours rather than 24, which raises
volatility per unit of holding time.

**What a pass means.** Only that the arithmetic is not hopeless -- that an
edge, if one exists, would not be eaten by costs before it could be measured.
It is emphatically not evidence that an edge exists. Crypto's measured edge
was real at ~0.5 bps and still useless. This gate is necessary, never
sufficient.

Overnight returns are excluded: a strategy that is flat at the close does not
collect them, and including them would overstate the move available intraday.

The default interval is 30 minutes rather than 5, because realised volatility
measured on very short bars is inflated by bid-ask bounce -- the price
oscillates between the quotes without going anywhere, and that oscillation is
not a move a strategy can capture. Measuring AAPL on 5-minute bars puts the
two-minute ratio at 2.10x; the same name on 30-minute bars gives 1.85x. The
lower figure is the one to trust, so it is the one reported by default.

Data is a directory of `{SYMBOL}_{interval}.csv` with a `datetime,open,high,
low,close,volume` header. There is no equity fetcher in this repo yet -- the
files used for the committed result came from Twelve Data. That gap is
recorded in the framework's Part 6 rather than hidden.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from intradyne.backtester.costs import round_trip_cost_pct  # noqa: E402

#: Interval label -> seconds. Only intraday bars are used for the volatility
#: term, because overnight gaps have to be dropped and daily bars cannot.
INTERVAL_SECONDS = {"5min": 300, "15min": 900, "30min": 1800}

#: Holding periods reported, in seconds. The two-minute row exists so the
#: comparison with the crypto scalper is direct.
HOLDS: List[Tuple[str, int]] = [
    ("2 min", 120),
    ("5 min", 300),
    ("15 min", 900),
    ("30 min", 1800),
    ("1 hour", 3600),
    ("2 hours", 7200),
    ("1 session", 23400),
]

#: Fees on the sell leg only (SEC + TAF), in basis points, approximate.
SELL_SIDE_FEES_BPS = 0.3

#: US equities quote in one-cent increments, so the *full* spread cannot be
#: narrower than a cent: `100 / price` bps. `--spread-bps` is the half-spread
#: paid per side, hence half of that.
#:
#: This matters because the flat default is a large-cap number. It is
#: conservative above $50, where the floor falls below 1 bp -- and understates
#: below it, badly: a $5 name carries a 10 bp half-spread, ten times assumed.
TICK_CENTS = 0.01


def half_spread_floor_bps(price: float) -> float:
    """The narrowest half-spread a one-cent tick permits at this price."""
    if price <= 0:
        return 0.0
    return (TICK_CENTS / price) * 1e4 / 2.0


def price_band(price: float) -> str:
    for hi, label in (
        (10, "under $10"),
        (25, "$10-25"),
        (50, "$25-50"),
        (100, "$50-100"),
    ):
        if price < hi:
            return label
    return "over $100"


def load_csv(path: Path) -> List[Tuple[str, float]]:
    rows: List[Tuple[str, float]] = []
    with path.open(encoding="utf-8") as fh:
        for rec in csv.DictReader(fh):
            rows.append((rec["datetime"], float(rec["close"])))
    rows.sort()
    return rows


def intraday_sigma(rows: List[Tuple[str, float]]) -> Tuple[float, int, int]:
    """Return (bps per sqrt(second), bar count, session count).

    Returns are computed within each session and concatenated, so the
    overnight gap never enters.
    """
    by_day: Dict[str, List[float]] = {}
    for stamp, px in rows:
        by_day.setdefault(stamp.split(" ")[0], []).append(px)

    squares: List[float] = []
    n = 0
    for day in sorted(by_day):
        prices = by_day[day]
        if len(prices) < 3:
            continue
        for a, b in zip(prices, prices[1:]):
            if a > 0 and b > 0:
                squares.append(math.log(b / a) ** 2)
                n += 1
    if n < 2:
        raise SystemExit("not enough intraday bars to measure volatility")
    # Mean square rather than variance about the mean: intraday drift is
    # indistinguishable from zero at these horizons and estimating it would
    # only add noise.
    sigma_bar = math.sqrt(sum(squares) / len(squares)) * 1e4
    return sigma_bar, n, len(by_day)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/equities")
    ap.add_argument("--interval", default="30min", choices=sorted(INTERVAL_SECONDS))
    ap.add_argument(
        "--spread-bps",
        type=float,
        default=1.0,
        help="half-spread paid per side; 1.0 is a central liquid large-cap value",
    )
    ap.add_argument("--slippage-bps", type=float, default=1.0)
    ap.add_argument(
        "--flat-spread",
        action="store_true",
        help="use --spread-bps for every name, ignoring the tick floor. The "
        "old behaviour, kept for comparison against the committed result.",
    )
    ap.add_argument("--out", default="artifacts/equity_feasibility.json")
    args = ap.parse_args()

    bar_s = INTERVAL_SECONDS[args.interval]
    data = Path(args.data)
    files = sorted(data.glob("*_%s.csv" % args.interval))
    if not files:
        raise SystemExit("no %s files under %s" % (args.interval, data))

    def cost_bps(spread_bps: float) -> float:
        return (
            round_trip_cost_pct(taker_bps=spread_bps, slippage_bps=args.slippage_bps)
            * 1e4
            + SELL_SIDE_FEES_BPS
        )

    flat_round_trip = cost_bps(args.spread_bps)

    print("A1 -- feasibility gate, US equities")
    if args.flat_spread:
        print(
            "round trip assumed : %.2f bps flat (%s bars)"
            % (flat_round_trip, args.interval)
        )
    else:
        print(
            "round trip         : per name, max(%.2f bps assumed, tick floor) "
            "(%s bars)" % (args.spread_bps, args.interval)
        )
    print("crypto comparison  : 4 bps all-maker, 14 bps taker")
    print()

    results = []
    for path in files:
        symbol = path.name.split("_")[0]
        rows = load_csv(path)
        per_sqrt_s, bars, sessions = intraday_sigma(rows)
        per_sqrt_s /= math.sqrt(bar_s)

        # The cost is a property of the name, not of the study. A flat spread
        # asks whether the *average large cap* clears its costs; the gate is
        # supposed to ask whether the names being traded do.
        price = statistics.median([px for _, px in rows]) if rows else 0.0
        floor = half_spread_floor_bps(price)
        spread = args.spread_bps if args.flat_spread else max(args.spread_bps, floor)
        round_trip_bps = cost_bps(spread)

        moves = {}
        for label, secs in HOLDS:
            move = per_sqrt_s * math.sqrt(secs)
            moves[label] = {"move_bps": move, "ratio": move / round_trip_bps}
        breakeven_s = (round_trip_bps / per_sqrt_s) ** 2

        print("=== %s ===  %d bars, %d sessions" % (symbol, bars, sessions))
        print(
            "  median price $%.2f -> tick floor %.2f bps half-spread, "
            "round trip %.2f bps" % (price, floor, round_trip_bps)
        )
        print("  realised vol %.4f bps per sqrt(second)" % per_sqrt_s)
        for label, _ in HOLDS:
            m = moves[label]
            print(
                "    %-10s %8.2f bps   %6.2fx cost" % (label, m["move_bps"], m["ratio"])
            )
        print("  breakeven hold: %.1f s (crypto: ~1860 s taker)" % breakeven_s)
        print()

        results.append(
            {
                "symbol": symbol,
                "bars": bars,
                "sessions": sessions,
                "median_price": price,
                "half_spread_floor_bps": floor,
                "half_spread_used_bps": spread,
                "round_trip_bps": round_trip_bps,
                "price_band": price_band(price),
                "bps_per_sqrt_second": per_sqrt_s,
                "breakeven_hold_seconds": breakeven_s,
                "holds": moves,
            }
        )

    # The gate: at the shortest hold reported, does the move clear the cost?
    ratios = [r["holds"]["2 min"]["ratio"] for r in results]
    worst = min(ratios)
    passed = worst > 1.0

    # The point of the re-run: the gate is not one number when the cost is a
    # function of price. A universe whose cheap names fail is a smaller
    # universe, which is A2's breadth input, not a footnote here.
    print("--- by price band ---")
    bands: Dict[str, List[Dict[str, Any]]] = {}
    for r in results:
        bands.setdefault(r["price_band"], []).append(r)
    order = ["under $10", "$10-25", "$25-50", "$50-100", "over $100"]
    print(
        "  %-10s %6s %12s %14s %10s"
        % ("band", "names", "round trip", "2-min move", "ratio")
    )
    for label in order:
        group = bands.get(label)
        if not group:
            continue
        rt = statistics.median([g["round_trip_bps"] for g in group])
        mv = statistics.median([g["holds"]["2 min"]["move_bps"] for g in group])
        rs = [g["holds"]["2 min"]["ratio"] for g in group]
        flag = "" if min(rs) > 1.0 else "   <-- fails"
        print(
            "  %-10s %6d %9.2f bps %11.2f bps %9.2fx%s"
            % (label, len(group), rt, mv, statistics.median(rs), flag)
        )
    print()

    print("--- verdict ---")
    print(
        "  worst 2-minute move/cost ratio across %d names: %.2fx"
        % (len(results), worst)
    )
    print("  crypto at the same hold: 0.25x (3.5 bps against 14 bps)")
    print("  A1: %s" % ("PASS" if passed else "FAIL"))

    # A verdict driven by the worst name was the right shape when the cost was
    # one number. Once it varies with price, a global FAIL can mean "one $6
    # stock" rather than "equities are infeasible", and the second reading is
    # the one someone takes from a single word. So the gate says which.
    if not passed:
        failing = [r for r in results if r["holds"]["2 min"]["ratio"] <= 1.0]
        clears = [r for r in results if r["holds"]["2 min"]["ratio"] > 1.0]
        print()
        print(
            "  %d of %d names fail, all of them cheap: %s"
            % (
                len(failing),
                len(results),
                ", ".join(
                    "%s ($%.2f, %.2fx)"
                    % (r["symbol"], r["median_price"], r["holds"]["2 min"]["ratio"])
                    for r in sorted(failing, key=lambda r: r["median_price"])
                ),
            )
        )
        if clears:
            floor = max(r["median_price"] for r in failing)
            print(
                "  Every name above $%.2f clears it, worst %.2fx. So this reads as a"
                % (floor, min(r["holds"]["2 min"]["ratio"] for r in clears))
            )
            print("  price floor on the tradeable universe, not as infeasibility --")
            print("  which is a universe-size question, and therefore A2's input.")
    print()
    print("  A pass means costs do not make the search hopeless. It is not")
    print("  evidence of an edge, and does not authorise a search on its own --")
    print("  see A2 (scripts/equity_breadth.py) and the pre-registration rule.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "gate": "A1_feasibility",
                "interval": args.interval,
                "flat_spread": args.flat_spread,
                "flat_round_trip_bps": flat_round_trip,
                "spread_bps": args.spread_bps,
                "slippage_bps": args.slippage_bps,
                "sell_side_fees_bps": SELL_SIDE_FEES_BPS,
                "worst_2min_ratio": worst,
                "passed": passed,
                "crypto_reference": {
                    "move_bps_2min": 3.5,
                    "round_trip_bps_taker": 14.0,
                    "ratio": 0.25,
                },
                "per_symbol": results,
                "verdict": (
                    "pass -- costs do not preclude a search; this is not evidence "
                    "of an edge"
                    if passed
                    else "fail -- cost exceeds the move at the shortest hold"
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("wrote %s" % out)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
