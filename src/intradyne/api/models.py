from __future__ import annotations

from pydantic import BaseModel


class FrontendConfig(BaseModel):
    api_base: str
    ws_ticks: str
    risk_status: str
    ledger_tail: str
    ai_summary: str
    enable_ai: bool
