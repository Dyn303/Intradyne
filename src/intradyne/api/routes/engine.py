"""Engine status and runtime reconfiguration.

These replace the second FastAPI app that ``engine/server.py`` used to serve
from the standalone engine process. That process built its own portfolio,
paper broker, ledger and execution manager, so its ``/state`` and
``/profile/apply`` operated on a completely separate copy of the system from
the one the API reported on. There is now one process, one set of state, and
these endpoints act on the loop actually running in it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from intradyne.api.deps import get_execution_manager, get_ledger
from intradyne.core.config import load_settings
from intradyne.engine import loop as engine_loop


router = APIRouter()


def _params_path(name: str) -> str:
    return os.path.join(load_settings().artifacts_dir, name)


@router.get("/engine/status")
def engine_status() -> Dict[str, Any]:
    settings = load_settings()
    active = engine_loop.get_active_router()
    feed = engine_loop.get_active_feed()
    return {
        "enabled": settings.engine_enabled,
        "running": active is not None,
        "mode": settings.mode,
        "live_trading_enabled": settings.live_trading_enabled,
        # How prices arrive, and how often. The strategies size their windows
        # in ticks, so `interval_s` is what converts a 60-tick lookback into a
        # span of time -- 60s on the socket, 170s on a slow REST pass against
        # a 120s time stop. It was previously only inferable from outside the
        # process by the absence of a log warning.
        "transport": feed.transport if feed is not None else None,
        "interval_s": (
            round(feed.interval_s, 3)
            if feed is not None and feed.interval_s is not None
            else None
        ),
        "symbols": list(active.symbols) if active is not None else [],
        "open_positions": (
            {s: p.base for s, p in active.portfolio.positions.items() if p.base > 0}
            if active is not None
            else {}
        ),
    }


@router.get("/engine/state")
def engine_state() -> Dict[str, Any]:
    """Balances and positions. Replaces the engine process's /state."""
    settings = load_settings()
    portfolio = get_execution_manager().ctx.portfolio
    return {
        "mode": settings.mode,
        "balances": dict(portfolio.balances),
        "positions": {
            symbol: {
                "base": pos.base,
                "avg_price": pos.avg_price,
                "realized_pnl": pos.realized_pnl,
            }
            for symbol, pos in portfolio.positions.items()
        },
    }


def _record_applied(runtime: Dict[str, Any]) -> None:
    """Remember what is running, so the next apply can back it up."""
    try:
        with open(
            _params_path("production_params.applied.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(runtime, f, indent=2)
    except OSError:
        # Losing this only costs revertibility of the *next* apply.
        pass


def _apply(runtime: Dict[str, Any], source: str) -> Dict[str, Any]:
    try:
        applied = engine_loop.apply_params(runtime)
    except RuntimeError as exc:
        # No loop running: say so rather than reporting success having changed
        # nothing, which the old endpoint could do.
        raise HTTPException(status_code=409, detail=str(exc))
    get_ledger().append(
        "profile_apply_runtime",
        {"params": runtime, "source": source, "applied": applied},
    )
    return {"applied": True, "detail": applied, "source": source}


@router.post("/engine/params/apply")
def apply_profile() -> Dict[str, Any]:
    """Apply artifacts/production_params.json to the running engine.

    Snapshots the previously applied configuration first, so /revert has
    something real to return to.
    """
    path = _params_path("production_params.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="no_production_params")
    with open(path, "r", encoding="utf-8") as f:
        runtime = json.load(f)

    # What is running right now, recorded by the previous apply.
    #
    # This used to copy production_params.json into the backup -- but that file
    # already holds the *new* values, so the backup was a copy of what was
    # being applied rather than of what it replaced, and revert re-applied the
    # current configuration while reporting success. The engine cannot be asked
    # what it is running (apply_params mutates the router in place and returns
    # no snapshot), so the applied configuration is tracked here instead.
    applied_path = _params_path("production_params.applied.json")
    prev = _params_path("production_params.prev.json")
    try:
        if os.path.exists(applied_path):
            with (
                open(applied_path, "r", encoding="utf-8") as src,
                open(prev, "w", encoding="utf-8") as dst,
            ):
                dst.write(src.read())
        else:
            # First apply of this process: there is no earlier configuration,
            # so leave no backup rather than one that points at the present.
            if os.path.exists(prev):
                os.remove(prev)
    except OSError:
        # A missing backup only costs the ability to revert; do not fail the
        # apply over it.
        pass

    result = _apply(runtime, "production_params.json")
    _record_applied(runtime)
    result["applied_at"] = datetime.utcnow().isoformat() + "Z"
    result["revertible"] = os.path.exists(prev)
    return result


@router.post("/engine/params/revert")
def revert_profile() -> Dict[str, Any]:
    """Restore the configuration that was running before the last apply.

    One level of undo. The backup is consumed, so a second revert reports
    nothing to do instead of flip-flopping between two configurations.
    """
    prev = _params_path("production_params.prev.json")
    if not os.path.exists(prev):
        return {"reverted": False, "reason": "no_backup"}
    with open(prev, "r", encoding="utf-8") as f:
        runtime = json.load(f)

    result = _apply(runtime, "production_params.prev.json")

    # The file has to move back too. Reverting the engine while leaving
    # production_params.json holding the rejected values puts the next apply
    # straight back onto them.
    with open(_params_path("production_params.json"), "w", encoding="utf-8") as f:
        json.dump(runtime, f, indent=2)
    _record_applied(runtime)
    try:
        os.remove(prev)
    except OSError:
        pass
    return {"reverted": True, "detail": result["detail"]}


__all__ = ["router"]
