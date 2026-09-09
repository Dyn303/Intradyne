#!/usr/bin/env python
"""Does the frozen-price failure still hold, and does weekly avoid it?

`docs/APPROACH_1_PREREGISTRATION.md` chose the weaker hypothesis for slot 1
because delisted prices failed two ways:

    FXEN  delisted 2015  ->  "Invalid API call"      a clean refusal
    ADVM  delisted 2026  ->  100 sessions of 4.3600  flat line, volume 0

The second is the dangerous one: a placeholder standing where the delisting
decline used to be, which a cross-sectional backtest would score as a
zero-volatility asset rather than as a loss.

Both observations were made against TIME_SERIES_DAILY. The question here is
whether the weekly endpoint -- free, and unconstrained by `outputsize` --
carries the real decline instead.

A series is "frozen" when its closes barely vary and its volume is zero: that
is the signature of a padded tail, not of a traded security.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Optional

import httpx
from dotenv import find_dotenv, load_dotenv

AV = "https://www.alphavantage.co/query"
CASES = [("ADVM", "the silent placeholder"), ("FXEN", "the clean refusal")]


def describe(body: str) -> Optional[Dict[str, object]]:
    if not body[:80].lower().startswith("timestamp,"):
        return None
    rows = list(csv.DictReader(io.StringIO(body)))
    if not rows:
        return None
    closes = [float(r["close"]) for r in rows]
    vols = [float(r["volume"]) for r in rows]
    return {
        "bars": len(rows),
        "first": rows[-1]["timestamp"],
        "last": rows[0]["timestamp"],
        "distinct_closes": len(set(closes)),
        "min_close": min(closes),
        "max_close": max(closes),
        "zero_volume_bars": sum(1 for v in vols if v == 0),
        # A real series moves. A placeholder does not.
        "frozen": len(set(closes)) <= 2
        and sum(1 for v in vols if v == 0) > len(rows) / 2,
    }


def main() -> int:
    load_dotenv(find_dotenv(usecwd=True))
    key = (os.getenv("ALPHAVANTAGE_API_KEY") or "").strip()
    if not key:
        print("ALPHAVANTAGE_API_KEY is not set")
        return 2

    out = []
    for sym, why in CASES:
        print(f"\n{sym} -- {why}")
        for func, extra in (
            ("TIME_SERIES_DAILY", {"outputsize": "compact"}),
            ("TIME_SERIES_WEEKLY", {}),
        ):
            time.sleep(3.0)
            q = {
                "function": func,
                "symbol": sym,
                "apikey": key,
                "datatype": "csv",
                **extra,
            }
            try:
                body = httpx.get(AV, params=q, timeout=60.0).text.strip()
            except Exception as exc:  # noqa: BLE001
                print(f"  {func:<20} request failed: {type(exc).__name__}")
                continue
            d = describe(body)
            rec = {"symbol": sym, "function": func, **(d or {})}
            if d is None:
                note = " ".join(body.split())[:90]
                rec["refused"] = note
                print(f"  {func:<20} no series -- {note[:70]}")
            else:
                flag = "  <-- FROZEN" if d["frozen"] else ""
                print(
                    f"  {func:<20} {d['bars']:>5} bars  {d['first']} -> {d['last']}  "
                    f"{d['distinct_closes']} distinct closes  "
                    f"{d['zero_volume_bars']} zero-vol{flag}"
                )
                print(
                    f"  {'':<20} close range {d['min_close']:.4f} .. {d['max_close']:.4f}"
                )
            out.append(rec)

    # Optional: an output directory. Defaults to the working directory so the
    # script runs with no arguments, which is how a reproduction gets run.
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    p = out_dir / "delisted_series_check.json"
    p.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
