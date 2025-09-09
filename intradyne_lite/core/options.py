
from __future__ import annotations
from typing import Dict, Any

def covered_call(symbol: str, qty: float, strike: float, expiry: str) -> Dict[str, Any]:
    # Structure only: assume long shares + sell call
    return {
        "strategy": "covered_call",
        "underlying": {"symbol": symbol, "qty": qty, "action":"BUY"},
        "option_short_call": {"symbol": symbol, "type":"CALL", "strike": float(strike), "expiry": expiry, "action":"SELL", "qty": qty},
        "notes": "Structure only. Placement depends on broker's options API."
    }

def protective_put(symbol: str, qty: float, strike: float, expiry: str) -> Dict[str, Any]:
    # Structure only: long shares + buy put
    return {
        "strategy": "protective_put",
        "underlying": {"symbol": symbol, "qty": qty, "action":"BUY"},
        "option_long_put": {"symbol": symbol, "type":"PUT", "strike": float(strike), "expiry": expiry, "action":"BUY", "qty": qty},
        "notes": "Structure only. Placement depends on broker's options API."
    }
