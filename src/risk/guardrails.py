from __future__ import annotations

import json
import os
import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple


# Defaults (can be overridden via constructor/env)
DD_WARN_PCT = float(os.getenv("DD_WARN_PCT", 0.15))
DD_HALT_PCT = float(os.getenv("DD_HALT_PCT", 0.20))
FLASH_CRASH_PCT = float(os.getenv("FLASH_CRASH_PCT", 0.30))
KILL_SWITCH_BREACHES = int(os.getenv("KILL_SWITCH_BREACHES", 3))
VAR_1D_MAX = float(os.getenv("VAR_1D_MAX", 0.05))


@dataclass
class OrderReq:
    symbol: str
    side: str
    qty: float
    meta: Optional[Dict[str, Any]] = None

    def step_down(self, factor: float = 0.5) -> "OrderReq":
        return replace(self, qty=max(self.qty * factor, 0.0))


class Ledger:
    """Append-only JSON lines ledger with hash chaining.

    Each record includes: ts, event, details..., hash_prev, hash
    """

    def __init__(self, path: str = "guardrails_ledger.jsonl") -> None:
        self.path = path
        # ensure file exists
        if not os.path.exists(self.path):
            open(self.path, "a", encoding="utf-8").close()

    def _last_hash(self) -> Optional[str]:
        last = None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        last = json.loads(line)["hash"]
        except FileNotFoundError:
            return None
        return last

    def append(self, event: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        prev = self._last_hash()
        rec = {"ts": datetime.utcnow().isoformat() + "Z", "event": event}
        rec.update(payload)
        rec["hash_prev"] = prev
        rec["hash"] = self._hash_record(rec)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        return rec

    def iter_recent(self, since: datetime) -> Iterable[Dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    try:
                        ts = datetime.fromisoformat(rec["ts"].rstrip("Z"))
                    except Exception:
                        continue
                    if ts >= since:
                        yield rec
        except FileNotFoundError:
            return

    @staticmethod
    def _hash_record(rec: Dict[str, Any]) -> str:
        # Stable hash of selected fields to avoid order issues
        data = json.dumps(rec, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(data).hexdigest()


class PriceFeed:
    """Interface for price data. Implement get_price(symbol, at).
    Tests can provide a stub.
    """

    def get_price(self, symbol: str, at: Optional[datetime] = None) -> Optional[float]:
        raise NotImplementedError


class RiskData:
    """Interface for risk data.
    Provide equity series and daily returns for 30 days.
    """

    def equity_series_30d(self) -> List[Tuple[datetime, float]]:
        raise NotImplementedError

    def equity_daily_returns_30d(self) -> List[float]:
        raise NotImplementedError


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    vs = sorted(values)
    q = min(max(q, 0.0), 1.0)
    # nearest-rank method
    k = int(max(0, min(len(vs) - 1, round(q * (len(vs) - 1)))))
    return float(vs[k])


def historical_var(returns: List[float], alpha: float = 0.95) -> float:
    # VaR as positive number representing loss magnitude at (1-alpha)
    if not returns:
        return 0.0
    q = _percentile(returns, 1 - alpha)
    return max(0.0, -q)


def dd_30d(equity_series: List[Tuple[datetime, float]]) -> float:
    peak = float("-inf")
    dd = 0.0
    for _t, eq in equity_series:
        peak = max(peak, float(eq))
        if peak <= 0:
            continue
        dd = max(dd, (peak - float(eq)) / peak)
    return dd


def is_crypto_symbol(symbol: str) -> bool:
    return "/" in (symbol or "")


class ShariahPolicy:
    def __init__(self, allowed_crypto: Optional[Iterable[str]] = None, blocked_tags: Optional[Iterable[str]] = None):
        self.allowed_crypto = set(allowed_crypto or [])
        self.blocked_tags = set(blocked_tags or ["gambling", "riba", "porn"])  # extensible

    def check(self, symbol: str, meta: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        if is_crypto_symbol(symbol):
            if self.allowed_crypto and symbol not in self.allowed_crypto:
                return False, f"Crypto {symbol} not in allowed list"
            if meta and any(tag in self.blocked_tags for tag in meta.get("tags", [])):
                return False, "Crypto token has blocked tags"
            return True, "ok"
        # For non-crypto, allow by default unless whitelists are introduced here
        return True, "ok"


class Guardrails:
    def __init__(
        self,
        price_feed: PriceFeed,
        risk_data: RiskData,
        ledger: Optional[Ledger] = None,
        shariah: Optional[ShariahPolicy] = None,
        thresholds: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.price = price_feed
        self.risk = risk_data
        self.ledger = ledger or Ledger()
        self.shariah = shariah or ShariahPolicy()
        self.th = {
            "dd_warn": DD_WARN_PCT,
            "dd_halt": DD_HALT_PCT,
            "flash": FLASH_CRASH_PCT,
            "kill_switch": KILL_SWITCH_BREACHES,
            "var_max": VAR_1D_MAX,
        }
        if thresholds:
            self.th.update(thresholds)

    def _breach(self, btype: str, **fields: Any) -> None:
        payload = {"type": btype}
        payload.update(fields)
        self.ledger.append("guardrail_breach", payload)

    def _recent_breach_count(self, hours: int = 24) -> int:
        since = datetime.utcnow() - timedelta(hours=hours)
        return sum(1 for r in self.ledger.iter_recent(since) if r.get("event") == "guardrail_breach")

    def gate_trade(self, req: OrderReq) -> Tuple[str, List[str], OrderReq]:
        reasons: List[str] = []

        # 1) Shariah / whitelist
        ok, reason = self.shariah.check(req.symbol, req.meta or {})
        if not ok:
            self._breach("compliance", symbol=req.symbol, reason=reason, action="block")
            return "block", [reason], req

        # 2) Risk metrics
        eq = self.risk.equity_series_30d()
        dd = dd_30d(eq)
        if dd >= self.th["dd_halt"]:
            self._breach("dd_halt", metric=round(dd, 6), threshold=self.th["dd_halt"], action="halt")
            return "halt", [f"30d drawdown {dd:.3f} >= {self.th['dd_halt']:.3f}"], req
        if dd >= self.th["dd_warn"]:
            self._breach("dd_warn", metric=round(dd, 6), threshold=self.th["dd_warn"], action="warn")
            reasons.append(f"dd_warn {dd:.3f}")

        # 3) Flash crash check (1h drop > threshold)
        now = datetime.utcnow()
        p_now = self.price.get_price(req.symbol, now)
        p_1h = self.price.get_price(req.symbol, now - timedelta(hours=1))
        if p_now and p_1h and p_1h > 0:
            drop = (p_1h - p_now) / p_1h
            if drop > self.th["flash"]:
                self._breach("flash_crash", symbol=req.symbol, metric=round(drop, 6), threshold=self.th["flash"], action="pause")
                return "pause", [f"flash_crash {drop:.3f} > {self.th['flash']:.3f}"], req

        # 4) Kill switch (N breaches in last 24h)
        if self._recent_breach_count(24) >= int(self.th["kill_switch"]):
            self._breach("kill_switch", action="halt")
            return "halt", ["kill_switch"], req

        # 5) VaR step-down
        rets = self.risk.equity_daily_returns_30d()
        var = historical_var(rets, alpha=0.95)
        if var > self.th["var_max"]:
            self._breach("var_stepdown", metric=round(var, 6), threshold=self.th["var_max"], action="stepdown")
            req = req.step_down()
            reasons.append(f"var {var:.3f} > {self.th['var_max']:.3f}")

        return "allow", reasons, req


