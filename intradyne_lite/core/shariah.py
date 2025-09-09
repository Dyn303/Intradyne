
from __future__ import annotations
from typing import Dict, Any, Optional

DEFAULT_FORBIDDEN = {
    "sectors": {"conventional_finance","insurance","alcohol","gambling","pork","tobacco","weapons","adult"},
}

def normalize(cfg: Dict[str, Any]) -> Dict[str, Any]:
    s = (cfg.get("shariah") or {})
    return {
        "stocks": {
            "forbidden_sectors": set((s.get("stocks") or {}).get("forbidden_sectors", [])) or DEFAULT_FORBIDDEN["sectors"],
            "max_debt_to_assets": float((s.get("stocks") or {}).get("max_debt_to_assets", 0.33)),
            "max_non_compliant_rev": float((s.get("stocks") or {}).get("max_non_compliant_rev", 0.05)),
            "whitelist": set((s.get("stocks") or {}).get("whitelist", [])),
            "blacklist": set((s.get("stocks") or {}).get("blacklist", [])),
        },
        "crypto": {
            "allowed": set((s.get("crypto") or {}).get("allowed", [])),
            "blocked_tags": set((s.get("crypto") or {}).get("blocked_tags", ["gambling","riba","porn"])),
        },
        "options": {
            "allow": set((s.get("options") or {}).get("allow", ["covered_call","protective_put"])),
        }
    }

def _symbol_kind(symbol: str) -> str:
    return "crypto" if "/" in (symbol or "") else "stock"

def check_symbol(cfg: Dict[str, Any], symbol: str, meta: Optional[Dict[str,Any]] = None) -> (bool, str):
    s = normalize(cfg)
    kind = _symbol_kind(symbol)
    if kind=="crypto":
        allowed = s["crypto"]["allowed"]
        if allowed and symbol not in allowed:
            return False, f"Crypto {symbol} not in allowed list."
        if meta and any(tag in s['crypto']['blocked_tags'] for tag in meta.get("tags",[])):
            return False, "Crypto token has blocked tags."
        return True, "ok"
    st = s["stocks"]
    if symbol in st["blacklist"]:
        return False, "Stock symbol blacklisted."
    if st["whitelist"] and symbol in st["whitelist"]:
        return True, "whitelisted"
    if not meta:
        return True, "no_meta"
    if any(sec in st["forbidden_sectors"] for sec in meta.get("sectors", [])):
        return False, "Forbidden sector"
    dta = meta.get("debt_to_assets")
    if dta is not None and float(dta) > st["max_debt_to_assets"]:
        return False, f"Debt/Assets {dta} exceeds {st['max_debt_to_assets']}"
    ncr = meta.get("non_compliant_revenue")
    if ncr is not None and float(ncr) > st["max_non_compliant_rev"]:
        return False, f"Non-compliant revenue {ncr} exceeds {st['max_non_compliant_rev']}"
    return True, "ok"
