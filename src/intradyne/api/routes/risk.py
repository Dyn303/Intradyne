from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from fastapi import APIRouter

import orjson
from intradyne.api.deps import get_guardrails
from intradyne.core.config import load_settings
from intradyne.risk.guardrails import dd_30d, historical_var
from intradyne.risk.kill_switch import halt_reason, is_halted


router = APIRouter()


@router.get("/risk/status")
async def risk_status():
    gr = get_guardrails()
    settings = load_settings()
    since = datetime.utcnow() - timedelta(hours=24)

    # Count guardrail breaches, not every ledger record. This previously
    # counted fills, admin actions and everything else, so the figure shown
    # here disagreed with the count the kill-switch actually acts on.
    breaches = sum(
        1 for r in gr.ledger.iter_recent(since) if r.get("event") == "guardrail_breach"
    )

    series = gr.risk.equity_series_30d()
    returns = gr.risk.equity_daily_returns_30d()
    dd = dd_30d(series)
    var = historical_var(returns, alpha=0.95)

    return {
        "breaches_24h": breaches,
        # Peak-to-trough over 30 days. NOT the session drawdown the engine's
        # RiskManager tracks against starting equity -- they are different
        # measurements with different thresholds, and reporting either as
        # "the" drawdown has caused confusion before.
        "dd_30d": dd,
        "var_1d": var,
        "equity": {
            "latest": series[-1][1] if series else None,
            "points_30d": len(series),
            "daily_returns_30d": len(returns),
        },
        "halted": is_halted(),
        "halt_reason": halt_reason() or None,
        "thresholds": {
            "dd_warn": settings.guardrails.dd_warn_pct,
            "dd_halt": settings.guardrails.dd_halt_pct,
            "flash": settings.guardrails.flash_crash_pct,
            "var_max": settings.guardrails.var_1d_max,
            "kill_switch": settings.guardrails.kill_switch_breaches,
        },
        "session_drawdown_thresholds": {
            "dd_soft": settings.risk.dd_soft,
            "dd_hard": settings.risk.dd_hard,
        },
    }


@router.get("/ledger/tail")
async def ledger_tail(n: int = 100) -> List[Dict]:
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

    out: List[Dict] = []
    for line in lines[-max(0, int(n)) :]:
        try:
            rec = orjson.loads(line)
            rec.pop("SMTP_PASS", None)
            out.append(rec)
        except Exception:
            continue
    return out


# Namespaced under /risk to match /risk/status. It previously sat on bare
# "/metrics", where -- being registered with the routers before the
# app-level Prometheus handler -- it shadowed the scrape endpoint, so
# Prometheus received this JSON instead of the exposition format.
@router.get("/risk/metrics")
async def risk_metrics():
    gr = get_guardrails()
    now = datetime.utcnow()
    counts = {
        "breaches_1h": sum(
            1
            for r in gr.ledger.iter_recent(now - timedelta(hours=1))
            if r.get("event") == "guardrail_breach"
        ),
        "breaches_24h": sum(
            1
            for r in gr.ledger.iter_recent(now - timedelta(hours=24))
            if r.get("event") == "guardrail_breach"
        ),
        "breaches_7d": sum(
            1
            for r in gr.ledger.iter_recent(now - timedelta(days=7))
            if r.get("event") == "guardrail_breach"
        ),
    }
    try:
        dd = dd_30d(gr.risk.equity_series_30d())
    except Exception:
        dd = 0.0
    return {"counts": counts, "dd_30d": dd}


@router.get("/overview")
async def overview():
    st = await risk_status()
    # Minimal version
    from intradyne.api.health import VERSION as _V

    # Ledger last 5
    tail = await ledger_tail(5)
    return {
        "version": _V,
        "risk": st,
        "ledger_tail": tail,
    }
