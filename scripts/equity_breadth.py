#!/usr/bin/env python
"""A2: how many independent bets does an equity universe actually contain?

    python scripts/equity_breadth.py --data data/equities --interval 30min

Statistical power is bought with independent observations, and instrument
count is not the same thing. Crypto's mean pairwise correlation of 0.563 at
hourly horizons put twenty coins at **1.7 effective assets**, which is why a
pooled t of 3.91 across 25,946 trades was really nearer 1.2
(MIGRATION.md:1084-1139). Ten deliberately category-diverse names gave the
same 1.7. Adding instruments could not help, and this script exists so that
is measured for a new market rather than assumed.

Effective breadth is `N / (1 + (N-1) * rho_bar)`, which reproduces the 1.71
figure from the crypto inputs. It **saturates at 1/rho_bar**, so the ceiling
is reported alongside the point estimate: a universe cannot exceed it however
many names are added. Crypto's ceiling was 1.78 and twenty coins already
delivered 1.71 -- it was not merely inefficient to add more, it was
arithmetically incapable of helping.

**Every statistic here is clustered by day.** An earlier pass at this
measurement reported a correlation across 2,961 five-minute bars as though
they were 2,961 independent observations; they were 39 sessions, and the
result was inside noise once that was respected. Correlations are therefore
computed within each session and aggregated across sessions, and the standard
error comes from the between-session spread.

Overnight returns are excluded from the intraday horizons for the same reason
as in the A1 script: an intraday strategy is flat at the close.

The harness is falsified before its output is believed (framework D1):
independent noise must return N_eff ~ N, and a synthetic single common factor
at rho must return ~1/rho.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

INTERVAL_SECONDS = {"5min": 300, "15min": 900, "30min": 1800}

#: Crypto reference, measured in this project. See MIGRATION.md:1084-1139.
CRYPTO = {"rho": 0.563, "n": 20, "n_eff": 1.71, "ceiling": 1.78}


def load_csv(path: Path) -> List[Tuple[str, float]]:
    rows: List[Tuple[str, float]] = []
    with path.open(encoding="utf-8") as fh:
        for rec in csv.DictReader(fh):
            rows.append((rec["datetime"], float(rec["close"])))
    rows.sort()
    return rows


def effective_breadth(n: int, rho: float) -> float:
    return n / (1.0 + (n - 1) * rho)


def mean_pairwise(corr: np.ndarray) -> float:
    iu = np.triu_indices_from(corr, k=1)
    return float(np.mean(corr[iu]))


def falsify(n: int, obs: int, seed: int = 11) -> Dict[str, Dict[str, float]]:
    """Independent input must give N_eff ~ N; a common factor must give ~1/rho."""
    rng = np.random.default_rng(seed)
    checks: Dict[str, Dict[str, float]] = {}

    x = rng.standard_normal((n, obs))
    rho = mean_pairwise(np.corrcoef(x))
    checks["independent"] = {
        "rho": rho,
        "n_eff": effective_breadth(n, rho),
        "expected_n_eff": float(n),
    }

    common = rng.standard_normal((1, obs)).repeat(n, 0)
    idio = rng.standard_normal((n, obs))
    y = np.sqrt(0.5) * common + np.sqrt(0.5) * idio
    rho = mean_pairwise(np.corrcoef(y))
    checks["common_factor_0.5"] = {
        "rho": rho,
        "n_eff": effective_breadth(n, rho),
        "expected_n_eff": effective_breadth(n, 0.5),
    }
    return checks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/equities")
    ap.add_argument("--interval", default="30min", choices=sorted(INTERVAL_SECONDS))
    ap.add_argument("--min-bars-per-day", type=int, default=6)
    ap.add_argument("--out", default="artifacts/equity_breadth.json")
    args = ap.parse_args()

    data = Path(args.data)
    files = sorted(data.glob("*_%s.csv" % args.interval))
    if len(files) < 3:
        raise SystemExit("need at least 3 symbols at %s" % args.interval)

    series = {p.name.split("_")[0]: dict(load_csv(p)) for p in files}
    names = sorted(series)
    n = len(names)

    common = sorted(set.intersection(*[set(v) for v in series.values()]))
    by_day: Dict[str, List[str]] = {}
    for stamp in common:
        by_day.setdefault(stamp.split(" ")[0], []).append(stamp)
    days = sorted(d for d in by_day if len(by_day[d]) >= args.min_bars_per_day)
    if len(days) < 20:
        raise SystemExit("need at least 20 usable sessions, found %d" % len(days))

    print("A2 -- effective breadth, US equities")
    print("universe : %d names -- %s" % (n, ", ".join(names)))
    print(
        "sample   : %d aligned %s bars over %d sessions (%s to %s)"
        % (len(common), args.interval, len(days), days[0], days[-1])
    )
    print()

    def per_day_rho(step: int) -> np.ndarray:
        out = []
        for day in days:
            stamps = by_day[day][::step]
            if len(stamps) < 4:
                continue
            m = np.array([[series[s][t] for t in stamps] for s in names])
            r = np.diff(np.log(m), axis=1)
            if np.any(r.std(axis=1) == 0):
                continue
            out.append(mean_pairwise(np.corrcoef(r)))
        return np.array(out)

    bar_s = INTERVAL_SECONDS[args.interval]
    horizons = [(args.interval, 1), ("%dmin" % (2 * bar_s // 60), 2)]
    if bar_s * 4 <= 7200:
        horizons.append(("%dmin" % (4 * bar_s // 60), 4))

    print(
        "%-10s %6s %9s %9s %11s %10s"
        % ("horizon", "days", "rho_bar", "SE(day)", "N_eff of %d" % n, "ceiling")
    )
    rows = []
    for label, step in horizons:
        v = per_day_rho(step)
        if len(v) < 20:
            continue
        rho = float(v.mean())
        se = float(v.std(ddof=1) / np.sqrt(len(v)))
        row = {
            "horizon": label,
            "days": len(v),
            "rho_bar": rho,
            "se_day_clustered": se,
            "n_eff": effective_breadth(n, rho),
            "ceiling": 1.0 / rho if rho > 0 else float("inf"),
            "n_eff_ci95": [
                effective_breadth(n, rho + 1.96 * se),
                effective_breadth(n, rho - 1.96 * se),
            ],
        }
        rows.append(row)
        print(
            "%-10s %6d %+9.3f %9.3f %11.2f %10.2f"
            % (label, row["days"], rho, se, row["n_eff"], row["ceiling"])
        )

    # Close-to-close across sessions, using the last aligned bar of each day.
    last = np.array([[series[s][by_day[d][-1]] for d in days] for s in names])
    rho_d = mean_pairwise(np.corrcoef(np.diff(np.log(last), axis=1)))
    print(
        "%-10s %6d %+9.3f %9s %11.2f %10.2f"
        % (
            "daily c2c",
            len(days),
            rho_d,
            "n/a",
            effective_breadth(n, rho_d),
            1.0 / rho_d if rho_d > 0 else float("inf"),
        )
    )
    print()

    checks = falsify(n, len(days) * 13)
    print("--- harness falsification (D1) ---")
    for label, c in checks.items():
        print(
            "  %-20s rho=%+.3f  N_eff=%.2f  (expected %.2f)"
            % (label, c["rho"], c["n_eff"], c["expected_n_eff"])
        )
    ok = (
        abs(checks["independent"]["n_eff"] - n) < 0.25 * n
        and abs(
            checks["common_factor_0.5"]["n_eff"]
            - checks["common_factor_0.5"]["expected_n_eff"]
        )
        < 0.5
    )
    print("  harness: %s" % ("OK" if ok else "SUSPECT -- do not trust the numbers"))
    print()

    head = rows[0] if rows else None
    print("--- comparison ---")
    print(
        "  equities %s: rho %.3f, N_eff %.2f of %d, ceiling %.2f"
        % (args.interval, head["rho_bar"], head["n_eff"], n, head["ceiling"])
    )
    print(
        "  crypto  hourly: rho %.3f, N_eff %.2f of %d, ceiling %.2f"
        % (CRYPTO["rho"], CRYPTO["n_eff"], CRYPTO["n"], CRYPTO["ceiling"])
    )
    print("  ratio of ceilings: %.1fx" % (head["ceiling"] / CRYPTO["ceiling"]))
    print()
    print("  Caveats that belong with any use of this number:")
    print("   - a deliberately diverse handful of large caps, not a real")
    print("     universe; more same-sector pairs would raise rho and lower")
    print("     the ceiling")
    print("   - one regime, with no crisis in the window; equity correlations")
    print("     rise sharply in drawdowns, when breadth matters most")
    print("   - breadth is the power to detect an effect, never evidence of one")

    passed = ok and head is not None and head["n_eff"] > CRYPTO["n_eff"]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "gate": "A2_breadth",
                "interval": args.interval,
                "universe": names,
                "sessions": len(days),
                "first_session": days[0],
                "last_session": days[-1],
                "horizons": rows,
                "daily_c2c": {
                    "rho_bar": rho_d,
                    "n_eff": effective_breadth(n, rho_d),
                },
                "harness_checks": checks,
                "harness_ok": ok,
                "crypto_reference": CRYPTO,
                "passed": passed,
                "verdict": (
                    "pass -- breadth materially exceeds crypto's saturated 1.7; "
                    "measured on a diverse handful in one regime, and it is the "
                    "power to detect an effect, not evidence of one"
                    if passed
                    else "fail -- breadth is not better than crypto's"
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print()
    print("wrote %s" % out)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
