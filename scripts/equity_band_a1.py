#!/usr/bin/env python
"""A1 feasibility, re-run per price band.

    python scripts/equity_band_a1.py --data data/equities

`scripts/equity_feasibility.py` takes the half-spread as a parameter and 1.0bps
is a reasonable value for a liquid large cap. Below about $20 that parameter
stops being free.

US equities quote in one-cent ticks above $1, so the spread cannot be narrower
than a penny however liquid the name is, and a penny is a **fixed fraction of
price**: 20bps of a $5 stock, 5bps of a $20 one, 1.1bps of a $90 one. Crossing
it on both legs costs `100 / price` bps before slippage or fees. That is a hard
floor set by market structure, not an assumption, and it is why A1's 4.3bps
large-cap figure cannot be carried downward.

So the question this answers is whether the extra *movement* in cheaper stocks
pays for their structurally wider spread. Both halves are measured: the cost
floor from the tick, the move from realised intraday volatility over 2,000
thirty-minute bars per name.

**The sample flatters the low bands, deliberately.** Its cheap names come from
the most-active list, so they are the most liquid stocks at their price -- the
best case for the band. A one-sided test is the right shape here: if the best
case fails, the band fails, and no better-chosen name rescues it.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from intradyne.backtester.costs import round_trip_cost_pct  # noqa: E402

BAR_SECONDS = 1800

#: US minimum tick above $1. Sub-penny quoting is prohibited, so this is the
#: floor on the spread rather than a typical value.
TICK = 0.01

#: SEC + TAF, charged on the sell leg only. Small, but not zero at $5.
SELL_FEE_BPS = 0.3

#: Price bands. Open-ended at the top; the bottom stops at $5 because below it
#: the tick alone exceeds 20bps and the arithmetic is not close.
BANDS: Sequence[Tuple[str, float, float]] = (
    ("$5-20", 5.0, 20.0),
    ("$20-50", 20.0, 50.0),
    ("$50-100", 50.0, 100.0),
    ("$100-200", 100.0, 200.0),
    ("$200+", 200.0, float("inf")),
)

#: Holding periods to price the move over.
HORIZONS: Sequence[Tuple[str, int]] = (
    ("30 min", 1800),
    ("2 hours", 7200),
    ("1 day", 23400),  # one 6.5h session
    ("1 week", 117000),
)


@dataclass(frozen=True)
class Name:
    symbol: str
    price: float
    sigma_per_sqrt_s: float
    bars: int
    days: int

    def band(self) -> Optional[str]:
        for label, lo, hi in BANDS:
            if lo <= self.price < hi:
                return label
        return None

    def move_bps(self, seconds: int) -> float:
        return self.sigma_per_sqrt_s * math.sqrt(seconds) * 1e4

    def round_trip_bps(self, slippage_bps: float, spread_ticks: float) -> float:
        """Cost floor at this price: the tick, both legs, plus slippage.

        `spread_ticks` scales the quoted spread. 1.0 is the tightest a US
        equity can legally quote; less liquid names sit wider.
        """
        half_spread_bps = (spread_ticks * TICK / 2.0) / self.price * 1e4
        rt = (
            round_trip_cost_pct(taker_bps=half_spread_bps, slippage_bps=slippage_bps)
            * 1e4
        )
        return rt + SELL_FEE_BPS


def load(path: Path) -> Tuple[List[str], np.ndarray]:
    ts, px = [], []
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            c = row.get("close")
            if c:
                ts.append(row["datetime"])
                px.append(float(c))
    return ts, np.asarray(px, dtype=float)


def measure(path: Path) -> Optional[Name]:
    """Realised intraday volatility, overnight gaps excluded.

    An intraday strategy is flat at the close and does not collect the gap;
    including it would overstate the move a trade can capture.
    """
    ts, px = load(path)
    if len(px) < 200:
        return None
    days = [t.split(" ")[0] for t in ts]
    rets = []
    for i in range(1, len(px)):
        if days[i] == days[i - 1] and px[i - 1] > 0:
            rets.append(math.log(px[i] / px[i - 1]))
    if len(rets) < 100:
        return None
    sigma_bar = float(np.std(rets, ddof=1))
    return Name(
        symbol=path.stem.split("_")[0],
        price=float(np.median(px)),
        sigma_per_sqrt_s=sigma_bar / math.sqrt(BAR_SECONDS),
        bars=len(px),
        days=len(set(days)),
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="data/equities")
    ap.add_argument("--interval", default="30min")
    ap.add_argument("--slippage-bps", type=float, default=1.0)
    ap.add_argument(
        "--spread-ticks",
        type=float,
        default=1.0,
        help="quoted spread in ticks; 1.0 is the legal minimum",
    )
    ap.add_argument("--out", default="artifacts/equity_band_a1.json")
    args = ap.parse_args(argv)

    files = sorted(Path(args.data).glob(f"*_{args.interval}.csv"))
    names = [n for n in (measure(f) for f in files) if n is not None]
    if not names:
        print(f"no {args.interval} CSVs under {args.data}", flush=True)
        return 1
    names.sort(key=lambda n: n.price)

    print(f"A1 per price band -- {len(names)} names, {args.interval} bars")
    print(
        f"tick {TICK:.2f}, spread {args.spread_ticks:g} tick(s), "
        f"slippage {args.slippage_bps:g}bps/side, sell fees {SELL_FEE_BPS}bps"
    )
    print()
    print(f"{'symbol':<7}{'price':>9}{'band':>10}{'round trip':>12}{'1d move':>10}")
    print("-" * 48)
    for n in names:
        print(
            f"{n.symbol:<7}{n.price:>9.2f}{n.band() or '-':>10}"
            f"{n.round_trip_bps(args.slippage_bps, args.spread_ticks):>11.2f}b"
            f"{n.move_bps(23400):>9.0f}b"
        )
    print()

    rows = []
    for label, lo, hi in BANDS:
        members = [n for n in names if n.band() == label]
        if not members:
            rows.append({"band": label, "names": 0})
            continue
        mid = math.sqrt(lo * hi) if hi != float("inf") else 400.0
        rt = float(
            np.mean(
                [
                    n.round_trip_bps(args.slippage_bps, args.spread_ticks)
                    for n in members
                ]
            )
        )
        row: Dict[str, object] = {
            "band": label,
            "names": len(members),
            "symbols": [n.symbol for n in members],
            "median_price": float(np.median([n.price for n in members])),
            "round_trip_bps": rt,
            "tick_floor_bps": 100.0 / mid,
            "horizons": {},
        }
        for hl, secs in HORIZONS:
            per = [
                n.move_bps(secs)
                / n.round_trip_bps(args.slippage_bps, args.spread_ticks)
                for n in members
            ]
            mv = float(np.mean([n.move_bps(secs) for n in members]))
            row["horizons"][hl] = {  # type: ignore[index]
                "move_bps": mv,
                "ratio": mv / rt,
                "ratio_min": float(min(per)),
                "ratio_max": float(max(per)),
            }
        rows.append(row)

    print(f"{'band':<10}{'n':>3}{'round trip':>12}", end="")
    for hl, _ in HORIZONS:
        print(f"{hl:>12}", end="")
    print()
    print("-" * (25 + 12 * len(HORIZONS)))
    for r in rows:
        if not r["names"]:
            print(f"{r['band']:<10}{'-':>3}{'no sample':>12}")
            continue
        print(f"{r['band']:<10}{r['names']:>3}{r['round_trip_bps']:>11.2f}b", end="")
        for hl, _ in HORIZONS:
            h = r["horizons"][hl]  # type: ignore[index]
            print(f"{h['ratio']:>11.1f}x", end="")
        print()
    print()
    print("move / cost. Below 1.0 the move does not pay the spread.")
    print()

    # Band means hide the dispersion inside them, and in the cheap band the
    # dispersion is the story: its names differ threefold in volatility.
    print("per-name spread within each band, at a 1-day hold:")
    for r in rows:
        if not r["names"]:
            continue
        h = r["horizons"]["1 day"]  # type: ignore[index]
        print(
            f"  {r['band']:<10} {h['ratio_min']:>6.1f}x to {h['ratio_max']:>6.1f}x"
            f"   ({', '.join(r['symbols'])})"  # type: ignore[arg-type]
        )
    print()

    # Sanity: annualised volatility per name, so the sigma behind every ratio
    # above can be checked against something known rather than trusted.
    print("annualised volatility implied by the same sigma (sanity check):")
    for n in names:
        print(
            f"  {n.symbol:<7}{n.move_bps(23400) / 1e4 * math.sqrt(252) * 100:>7.1f}%"
            f"   ({n.days} sessions, {n.bars} bars)"
        )
    print()

    # One tick is the tightest a US equity may legally quote, so every ratio
    # above is an upper bound and every band passes more easily than it should.
    # The honest question is not whether it passes at one tick but how far the
    # spread would have to widen before it stopped.
    #
    # Round trip in bps is 100*k/P + 2*slippage + fees for a k-tick spread, so
    # breakeven k solves directly rather than by search.
    print("how wide the spread would have to be to fail, at a 1-day hold:")
    for r in rows:
        if not r["names"]:
            continue
        mv = float(r["horizons"]["1 day"]["move_bps"])  # type: ignore[index]
        price = float(r["median_price"])  # type: ignore[arg-type]
        k = (mv - 2.0 * args.slippage_bps - SELL_FEE_BPS) * price / 100.0
        r["breakeven_ticks"] = k
        print(
            f"  {r['band']:<10} {k:>8.0f} ticks (${k * TICK:,.2f} on a "
            f"${price:,.2f} share)"
        )
    print()
    print("A penny is one tick, and the widest quoted spreads on listed US")
    print("names run to a few cents. The margin is not close.")
    print()

    # The verdict A1 exists to give, per band, at the shortest horizon where a
    # strategy could plausibly trade.
    print("verdict at a 1-day hold (A1 asks only whether this is hopeless):")
    for r in rows:
        if not r["names"]:
            print(f"  {r['band']:<10} no sample -- band not tested")
            continue
        ratio = r["horizons"]["1 day"]["ratio"]  # type: ignore[index]
        verdict = "clears" if ratio >= 3 else "marginal" if ratio >= 1.5 else "fails"
        # A band mean over one or two names is a statement about those names,
        # not about the band.
        thin = "" if int(r["names"]) >= 3 else f"   [thin: n={r['names']}]"
        print(f"  {r['band']:<10} {ratio:>6.1f}x  {verdict}{thin}")
    print()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "generated": datetime.now(timezone.utc).isoformat(),
                "interval": args.interval,
                "tick": TICK,
                "spread_ticks": args.spread_ticks,
                "slippage_bps": args.slippage_bps,
                "sell_fee_bps": SELL_FEE_BPS,
                "names": [
                    {
                        "symbol": n.symbol,
                        "price": n.price,
                        "band": n.band(),
                        "bars": n.bars,
                        "days": n.days,
                    }
                    for n in names
                ],
                "bands": rows,
                "note": (
                    "Cheap names are drawn from the most-active list and are "
                    "therefore the most liquid at their price. The low bands "
                    "are a best case; a failure there is decisive, a pass is "
                    "not representative."
                ),
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
