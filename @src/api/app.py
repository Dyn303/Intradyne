from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Dict

from fastapi import FastAPI, HTTPException
from src.api.health import router as health_router
from src.api.orders import submit_order, get_engine, OrderIn
from src.config import load_settings
from src.risk.guardrails import OrderReq, dd_30d


app = FastAPI(title="IntraDyne Lite API")
app.include_router(health_router)


@app.post("/orders")
def create_order(inp: OrderIn):
    gr = get_engine()

    def _exec(o: OrderReq) -> Dict:
        # Placeholder execution; integrate real routing as needed
        from uuid import uuid4

        return {"trade_id": str(uuid4()), "order_id": str(uuid4()), "status": "accepted"}

    ok, payload = submit_order(gr, OrderReq(symbol=inp.symbol, side=inp.side, qty=inp.qty), _exec)
    if not ok:
        raise HTTPException(status_code=400, detail=payload)
    return payload


@app.get("/risk/status")
def risk_status():
    gr = get_engine()
    settings = load_settings()
    # recent breaches in 24h
    since = datetime.utcnow() - timedelta(hours=24)
    breaches = sum(1 for _ in gr.ledger.iter_recent(since))
    # dd estimate from risk data if available
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


@app.get("/ledger/tail")
def ledger_tail(n: int = 100) -> List[Dict]:
    gr = get_engine()
    # Read last N lines by scanning; safe for small N
    path = gr.ledger.path
    lines: List[str] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    lines.append(line)
    except FileNotFoundError:
        return []
    # return last N parsed dicts
    import json

    out: List[Dict] = []
    for line in lines[-max(0, int(n)) :]:
        try:
            rec = json.loads(line)
            # redact secrets if accidentally present
            rec.pop("SMTP_PASS", None)
            out.append(rec)
        except Exception:
            continue
    return out

