from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from intradyne.api.deps import get_guardrails
from intradyne.risk.kill_switch import is_halted, set_halt


router = APIRouter()


@router.post("/admin/kill-switch/toggle")
def kill_switch_toggle(enabled: bool):
    """Engage or release the operator halt. Same switch as POST /admin/halt.

    This used to append a ledger line and return {"ok": true} while changing
    nothing -- its comment called it a placeholder and deferred enforcement to
    "breach count", but that is the *automatic* kill switch inside Guardrails,
    a threshold of N breaches in 24h with no on/off control. So the endpoint
    named kill-switch was the one thing in the system that could not stop
    trading, and it reported success for doing so.

    That is the worst way for a control to fail: an operator hits it, sees
    {"ok": true}, believes the system is stopped and stops watching. It now
    moves the halt that Guardrails.gate_trade and the broker actually consult.
    """
    set_halt(bool(enabled), reason="admin_kill_switch" if enabled else "")
    gr = get_guardrails()
    gr.ledger.append("admin_toggle", {"kill_switch_enabled": bool(enabled)})
    return {"ok": True, "kill_switch_enabled": is_halted()}


@router.get("/admin/halt")
def halt_status():
    return {"enabled": is_halted()}


@router.post("/admin/halt")
def halt_set(
    payload: dict,
    x_admin_secret: str | None = Header(default=None, alias="X-Admin-Secret"),
):
    # Simple admin-only: if ADMIN_SECRET is set in env, require matching header; else allow (dev)
    import os

    req = os.getenv("ADMIN_SECRET")
    if req and (x_admin_secret or "") != req:
        raise HTTPException(status_code=401, detail="unauthorized")
    enabled = bool(payload.get("enabled"))
    set_halt(enabled, reason="admin_halt")
    gr = get_guardrails()
    gr.ledger.append("admin_halt", {"enabled": enabled})
    return {"enabled": enabled}
