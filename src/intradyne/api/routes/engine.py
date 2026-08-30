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
    return {
        "enabled": settings.engine_enabled,
        "running": active is not None,
        "mode": settings.mode,
        "live_trading_enabled": settings.live_trading_enabled,
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

    The previous copy is kept so it can be reverted.
    """
    path = _params_path("production_params.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="no_production_params")
    with open(path, "r", encoding="utf-8") as f:
        runtime = json.load(f)

    prev = _params_path("production_params.prev.json")
    try:
        if os.path.exists(path):
            with (
                open(path, "r", encoding="utf-8") as src,
                open(prev, "w", encoding="utf-8") as dst,
            ):
                dst.write(src.read())
    except OSError:
        # A missing backup only costs the ability to revert; do not fail the
        # apply over it.
        pass

    result = _apply(runtime, "production_params.json")
    result["applied_at"] = datetime.utcnow().isoformat() + "Z"
    return result


@router.post("/engine/params/revert")
def revert_profile() -> Dict[str, Any]:
    prev = _params_path("production_params.prev.json")
    if not os.path.exists(prev):
        return {"reverted": False, "reason": "no_backup"}
    with open(prev, "r", encoding="utf-8") as f:
        runtime = json.load(f)
    result = _apply(runtime, "production_params.prev.json")
    with open(_params_path("production_params.json"), "w", encoding="utf-8") as f:
        json.dump(runtime, f, indent=2)
    return {"reverted": True, "detail": result["detail"]}


__all__ = ["router"]
