from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from fastapi import APIRouter

from intradyne.api.deps import get_guardrails
from intradyne.core.config import load_settings
from intradyne.risk.guardrails import dd_30d


router = APIRouter()


@router.get("/risk/status")
def risk_status():
    gr = get_guardrails()
    settings = load_settings()
    since = datetime.utcnow() - timedelta(hours=24)
    breaches = sum(1 for _ in gr.ledger.iter_recent(since))
    try:
        dd = dd_30d(gr.risk.equity_series_30d())
    except Exception:
        dd = 0.0
    return {
        "breaches_24h": breaches,
        "dd_30d": dd,
        "thresholds": {
            "dd_warn": settings.DD_WARN_PCT,
            "dd_halt": settings.DD_HALT_PCT,
            "flash": settings.FLASH_CRASH_PCT,
            "var_max": settings.VAR_1D_MAX,
            "kill_switch": settings.KILL_SWITCH_BREACHES,
        },
    }


@router.get("/ledger/tail")
def ledger_tail(n: int = 100) -> List[Dict]:
    gr = get_guardrails()
    path = gr.ledger.path
    lines: List[str] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    lines.append(line)
    except FileNotFoundError:
        return []
    import json

    out: List[Dict] = []
    for line in lines[-max(0, int(n)) :]:
        try:
            rec = json.loads(line)
            rec.pop("SMTP_PASS", None)
            out.append(rec)
        except Exception:
            continue
    return out


@router.get("/metrics")
def metrics():
    from datetime import timedelta

    gr = get_guardrails()
    now = datetime.utcnow()
    counts = {
        "breaches_1h": sum(1 for r in gr.ledger.iter_recent(now - timedelta(hours=1)) if r.get("event") == "guardrail_breach"),
        "breaches_24h": sum(1 for r in gr.ledger.iter_recent(now - timedelta(hours=24)) if r.get("event") == "guardrail_breach"),
        "breaches_7d": sum(1 for r in gr.ledger.iter_recent(now - timedelta(days=7)) if r.get("event") == "guardrail_breach"),
    }
    try:
        dd = dd_30d(gr.risk.equity_series_30d())
    except Exception:
        dd = 0.0
    return {"counts": counts, "dd_30d": dd}
