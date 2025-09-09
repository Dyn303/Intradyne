

from __future__ import annotations

import os
from typing import List, Optional


def _to_bool(v: Optional[str], default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


# Try pydantic v2 (pydantic-settings), then pydantic v1, then fallback
BaseSettings = None  # type: ignore
Field = None  # type: ignore
try:  # pydantic v2
    from pydantic_settings import BaseSettings as _BS  # type: ignore
    from pydantic import Field as _Field  # type: ignore

    BaseSettings = _BS
    Field = _Field
except Exception:
    try:  # pydantic v1
        from pydantic import BaseSettings as _BS  # type: ignore
        from pydantic import Field as _Field  # type: ignore

        BaseSettings = _BS
        Field = _Field
    except Exception:
        BaseSettings = object  # type: ignore


class Settings(BaseSettings):  # type: ignore[misc]
    # Risk thresholds
    DD_WARN_PCT: float = 0.15
    DD_HALT_PCT: float = 0.20
    FLASH_CRASH_PCT: float = 0.30
    VAR_1D_MAX: float = 0.05
    KILL_SWITCH_BREACHES: int = 3

    # Allowed symbols (comma-separated). Accepts either BASE or BASE/QUOTE.
    ALLOWED_SYMBOLS: str = "BTC,ETH,USDT"

    # Infra
    DB_URL: str = "sqlite:///data/trades.sqlite"
    REDIS_URL: Optional[str] = None
    LOG_LEVEL: str = "INFO"
    EXPLAIN_LEDGER_PATH: str = "explainability_ledger.jsonl"

    # API and rate limits
    RATE_LIMIT_WINDOW: int = 60
    RATE_LIMIT_REQS: int = 120

    class Config:  # type: ignore[override]
        case_sensitive = False

    def allowed_crypto_list(self) -> List[str]:
        raw = [s.strip() for s in (self.ALLOWED_SYMBOLS or "").split(",") if s.strip()]
        out: List[str] = []
        for s in raw:
            if "/" in s:
                out.append(s)
            else:
                out.append(f"{s}/USDT")
        return out


def load_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except Exception:
        # Fallback manual env loader if pydantic settings is unavailable
        class _Manual(Settings):  # type: ignore[misc]
            def __init__(self) -> None:  # type: ignore[no-untyped-def]
                env = os.environ
                self.DD_WARN_PCT = float(env.get("DD_WARN_PCT", "0.15"))
                self.DD_HALT_PCT = float(env.get("DD_HALT_PCT", "0.20"))
                self.FLASH_CRASH_PCT = float(env.get("FLASH_CRASH_PCT", "0.30"))
                self.VAR_1D_MAX = float(env.get("VAR_1D_MAX", "0.05"))
                self.KILL_SWITCH_BREACHES = int(env.get("KILL_SWITCH_BREACHES", "3"))
                self.ALLOWED_SYMBOLS = env.get("ALLOWED_SYMBOLS", "BTC,ETH,USDT")
                self.DB_URL = env.get("DB_URL", "sqlite:///data/trades.sqlite")
                self.REDIS_URL = env.get("REDIS_URL")
                self.LOG_LEVEL = env.get("LOG_LEVEL", "INFO")
                self.EXPLAIN_LEDGER_PATH = env.get("EXPLAIN_LEDGER_PATH", "explainability_ledger.jsonl")
                self.RATE_LIMIT_WINDOW = int(env.get("RATE_LIMIT_WINDOW", "60"))
                self.RATE_LIMIT_REQS = int(env.get("RATE_LIMIT_REQS", "120"))

        return _Manual()


__all__ = ["Settings", "load_settings"]

