from __future__ import annotations

import time
from typing import Dict, Iterable

import requests

from src.core.utils import safe_log_key


def _map_symbol(sym: str) -> str:
    return sym.replace("/", "")


def get_prices(symbols: Iterable[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for s in symbols:
        if s == "USDT":
            out[s] = 1.0
            continue
        mapped = _map_symbol(s)
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": mapped},
                timeout=5,
            )
            r.raise_for_status()
            px = float(r.json()["price"])  # type: ignore[index]
            out[s] = px
        except Exception:
            # fallback to 0 to skip
            out[s] = out.get(s, 0.0)
        time.sleep(0.2)
    return out


def place_order(
    symbol: str, side: str, qty: float, mode: str = "paper"
) -> Dict[str, object]:
    # Stub: for live mode, require keys but do not expose
    import os

    if mode != "live":
        return {"status": "simulated", "symbol": symbol, "side": side, "qty": qty}
    key = os.getenv("BINANCE_API_KEY")
    secret = os.getenv("BINANCE_API_SECRET")
    if not key or not secret:
        return {"status": "error", "error": "missing_api_keys"}
    # Do not place real orders in this implementation; just acknowledge
    return {
        "status": "ack",
        "api_key": safe_log_key(key),
        "symbol": symbol,
        "side": side,
        "qty": qty,
    }
