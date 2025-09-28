from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from intradyne.api.deps import get_guardrails, set_halt, is_halted


router = APIRouter()


@router.post("/admin/kill-switch/toggle")
def kill_switch_toggle(enabled: bool):
    gr = get_guardrails()
    # Placeholder: record intent in ledger; actual enforcement handled via breach count
    gr.ledger.append("admin_toggle", {"kill_switch_enabled": bool(enabled)})
    return {"ok": True, "kill_switch_enabled": bool(enabled)}


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
    set_halt(enabled)
    gr = get_guardrails()
    gr.ledger.append("admin_halt", {"enabled": enabled})
    return {"enabled": enabled}
