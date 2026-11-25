from __future__ import annotations

import os
from hmac import compare_digest

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, StrictBool

from intradyne.api.deps import get_guardrails, is_prod
from intradyne.risk.kill_switch import HaltStorageUnavailable, is_halted, set_halt


router = APIRouter()


def require_operator_secret(
    x_admin_secret: str | None = Header(default=None, alias="X-Admin-Secret"),
) -> None:
    expected = os.getenv("ADMIN_SECRET")
    if expected and not compare_digest(
        (x_admin_secret or "").encode(), expected.encode()
    ):
        raise HTTPException(status_code=401, detail="unauthorized")


class HaltRequest(BaseModel):
    enabled: StrictBool


def write_operator_halt(enabled: bool, reason: str) -> None:
    if not enabled and is_prod() and not os.getenv("ADMIN_SECRET"):
        raise HTTPException(status_code=503, detail="operator secret is not configured")
    try:
        set_halt(enabled, reason=reason)
    except HaltStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/admin/kill-switch/toggle", dependencies=[Depends(require_operator_secret)]
)
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
    write_operator_halt(enabled, "admin_kill_switch")
    gr = get_guardrails()
    gr.ledger.append("admin_toggle", {"kill_switch_enabled": bool(enabled)})
    return {"ok": True, "kill_switch_enabled": is_halted()}


@router.get("/admin/halt")
def halt_status():
    return {"enabled": is_halted()}


@router.post("/admin/halt", dependencies=[Depends(require_operator_secret)])
def halt_set(payload: HaltRequest):
    enabled = payload.enabled
    write_operator_halt(enabled, "admin_halt")
    gr = get_guardrails()
    gr.ledger.append("admin_halt", {"enabled": enabled})
    return {"enabled": enabled}
