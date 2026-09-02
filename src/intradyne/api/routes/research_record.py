"""The research record, read-only.

Deliberately separate from ``routes/research.py`` next door, which *computes* --
`/research/optimize_ma`, `/research/optimize_params` and friends all kick off
work. `docs/FULLSTACK_PLAN.md` names scope creep back into strategy search as
this project's most likely failure mode, and a browsable results view is
exactly the thing that invites "let me just re-run that one". Nothing in this
module runs a search. It reads files that already exist and returns them.

Every path is fixed in `REGISTRY` and the client selects one by key. There is
no filesystem path anywhere in a request, so directory traversal is not
defended against so much as unrepresentable.

The universe timeline is summarised server-side rather than shipped whole:
the file is a quarter of a megabyte of per-date symbol lists, the view wants
size and churn, and the Mini App reading it is on mobile data.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass(frozen=True)
class Record:
    """One result file, with the context needed to read it honestly.

    `verdict` matters as much as the numbers. Every search in this record
    returned negative, and several of them look encouraging in one cell while
    failing the pre-registered criteria overall -- cross_sectional_v2 has a
    cell with Sharpe 0.94 that the pre-registration had already ruled out. A
    viewer that shows the numbers without the conclusion invites exactly the
    re-litigation this project keeps having.
    """

    path: str
    title: str
    summary: str
    verdict: str = ""


REGISTRY: Dict[str, Record] = {
    "random_strategy_search": Record(
        "artifacts/random_strategy_search.json",
        "100 random strategies, tiered filter",
        "Randomly generated intraday strategies screened through a tiered filter.",
        "negative",
    ),
    "multi_instrument_search": Record(
        "artifacts/multi_instrument_search.json",
        "Pooled multi-instrument search",
        "The same search pooled across instruments, with day-clustered t-statistics.",
        "negative -- the uncorrected t of 3.91 became ~1.2 once trades were "
        "clustered by day, because the instruments are not independent.",
    ),
    "literature_signals": Record(
        "artifacts/literature_signals.json",
        "Pre-specified signals from the literature",
        "Published intraday and cross-sectional signals, tested as specified.",
        "negative after costs -- intraday momentum nets -1.1bps as maker and "
        "-11.1bps as taker.",
    ),
    "hierarchy_gates": Record(
        "artifacts/hierarchy_gates.json",
        "The 8-component hierarchy as cumulative gates",
        "Market structure, liquidity, volume profile, order flow and the rest, "
        "applied as successive AND-gates.",
        "negative -- no gate combination beat no gate at all.",
    ),
    "cross_sectional_v2": Record(
        "artifacts/cross_sectional_v2.json",
        "Cross-sectional test v2 (pre-registered)",
        "Four cells, four pre-registered criteria each.",
        "negative -- one cell passes all four, and the pre-registration written "
        "beforehand had already ruled it out: the secondary cannot rescue the "
        "primary. Its sign flips in the full universe.",
    ),
    "ctrend_test": Record(
        "artifacts/ctrend_test.json",
        "CTREND (Han-Zhou-Zhu trend factor)",
        "A learned trend factor with expanding-window regression weights.",
        "negative -- and weakest in the cells where the paper claims it works.",
    ),
    "best_params_mtf": Record(
        "artifacts/best_params_mtf.json",
        "Best multi-timeframe parameters",
        "The parameter set that scored best in the multi-timeframe sweep.",
    ),
    "production_params": Record(
        "artifacts/production_params.json",
        "Production parameters",
        "What POST /engine/params/apply would load into the running engine.",
    ),
    "universe_candidates": Record(
        "docs/universe_candidates.json",
        "Shariah screening worksheet",
        "Every candidate considered, with liquidity, listing date and the "
        "compliance flags that applied.",
    ),
}


def _root() -> str:
    return os.getcwd()


def _read(rec: Record) -> Any:
    path = os.path.join(_root(), rec.path)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="not_generated")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        # A truncated or half-written artifact should say so rather than
        # surfacing as an opaque 500.
        raise HTTPException(status_code=422, detail=f"unreadable: {type(exc).__name__}")


@router.get("/research/record")
def list_records() -> Dict[str, Any]:
    """What results exist, without their contents.

    Reports availability per entry instead of hiding missing files: an
    artifact that was never generated is a fact about the record, and a list
    that silently omits it looks like the test was never run.
    """
    items: List[Dict[str, Any]] = []
    for key, rec in REGISTRY.items():
        path = os.path.join(_root(), rec.path)
        exists = os.path.exists(path)
        items.append(
            {
                "key": key,
                "title": rec.title,
                "summary": rec.summary,
                "verdict": rec.verdict,
                "source": rec.path,
                "available": exists,
                "bytes": os.path.getsize(path) if exists else 0,
            }
        )
    return {"records": items, "count": len(items)}


@router.get("/research/record/{key}")
def get_record(key: str) -> Dict[str, Any]:
    """One result file. `key` indexes REGISTRY; it never becomes a path."""
    rec = REGISTRY.get(key)
    if rec is None:
        raise HTTPException(status_code=404, detail="unknown_record")
    return {
        "key": key,
        "title": rec.title,
        "summary": rec.summary,
        "verdict": rec.verdict,
        "source": rec.path,
        "data": _read(rec),
    }


def summarise_timeline(raw: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    """Turn {date: [symbols]} into size and churn per date.

    The point of the timeline is that the universe is not fixed -- names enter
    and leave, and a backtest over today's survivors is a different experiment
    from one run point-in-time. Size alone hides that, so entries and exits are
    carried too.
    """
    out: List[Dict[str, Any]] = []
    prev: Optional[set] = None
    for date in sorted(raw):
        members = set(raw[date] or [])
        out.append(
            {
                "date": date,
                "size": len(members),
                "added": sorted(members - prev) if prev is not None else [],
                "removed": sorted(prev - members) if prev is not None else [],
            }
        )
        prev = members
    return out


@router.get("/research/universe/timeline")
def universe_timeline() -> Dict[str, Any]:
    """Universe size and churn over time.

    Summarised here rather than shipped whole: the file is a quarter of a
    megabyte of per-date symbol lists and the view needs counts and names that
    changed.
    """
    path = os.path.join(_root(), "docs/universe_timeline.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="not_generated")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"unreadable: {type(exc).__name__}")

    points = summarise_timeline(raw)
    ever = {s for members in raw.values() for s in (members or [])}
    current = set(raw[points[-1]["date"]] or []) if points else set()
    return {
        "points": points,
        "dates": len(points),
        "ever_listed": len(ever),
        "current": len(current),
        # The gap between these two is the survivorship bias a
        # today's-symbols backtest would have silently absorbed.
        "delisted": sorted(ever - current),
    }


__all__ = ["router", "REGISTRY", "Record", "summarise_timeline"]
