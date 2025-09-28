from __future__ import annotations

import os
from typing import List, Optional


# Try pydantic v2 first, then v1; fall back to object
BaseSettings = None  # type: ignore
Field = None  # type: ignore
try:  # pydantic v2
    from pydantic_settings import BaseSettings as _BS  # type: ignore
    from pydantic import Field as _Field  # type: ignore

    class _Base(_BS):
        model_config = {  # type: ignore[attr-defined]
            "case_sensitive": False,
            "env_file": (".env", ".env.txt", ".env.example"),
            "extra": "ignore",
        }

    BaseSettings = _Base
    Field = _Field
except Exception:  # v1
    try:
        from pydantic import BaseSettings as _BS  # type: ignore
        from pydantic import Field as _Field  # type: ignore

        class _Base(_BS):
            class Config:  # type: ignore[override]
                case_sensitive = False
                env_file = (".env", ".env.txt", ".env.example")
                extra = "ignore"

        BaseSettings = _Base
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
    # Default Shariah-compliant crypto whitelist (spot-only)
    # Expand/override via config or env ALLOWED_SYMBOLS (comma-separated)
    ALLOWED_SYMBOLS: str = "BTC,ETH,SOL,XRP,ADA,LTC,AVAX,DOT,MATIC,USDT"

    # Infra
    DB_URL: str = "sqlite:///data/trades.sqlite"
    REDIS_URL: Optional[str] = None
    LOG_LEVEL: str = "INFO"
    EXPLAIN_LEDGER_PATH: str = "explainability_ledger.jsonl"

    # API and rate limits
    RATE_LIMIT_WINDOW: int = 60
    RATE_LIMIT_REQS: int = 120
    # AI endpoints can override rate limits; fallback to global if unset
    AI_RATE_LIMIT_WINDOW: int | None = None
    AI_RATE_LIMIT_REQS: int | None = None

    # Broker creds (env only; never commit secrets)
    BITGET_API_KEY: Optional[str] = None
    BITGET_API_SECRET: Optional[str] = None
    BITGET_API_PASSPHRASE: Optional[str] = None
    CCXT_EXCHANGE_ID: Optional[str] = None
    CCXT_API_KEY: Optional[str] = None
    CCXT_SECRET: Optional[str] = None

    def allowed_crypto_list(self) -> List[str]:
        raw = [s.strip() for s in (self.ALLOWED_SYMBOLS or "").split(",") if s.strip()]
        out: List[str] = []
        for s in raw:
            # Skip quote-only or self-pairs like USDT/USDT
            if "/" in s:
                try:
                    base, quote = s.split("/", 1)
                except ValueError:
                    continue
                if base.upper() == quote.upper():
                    continue
                out.append(f"{base}/{quote}")
            else:
                if s.upper() == "USDT":
                    continue
                out.append(f"{s}/USDT")
        return out

    def _map_compat(self) -> None:
        # Map CCXT_* to BITGET_* if exchange is bitget and bitget vars not set
        ccxt_exch = (
            self.CCXT_EXCHANGE_ID or os.getenv("CCXT_EXCHANGE_ID") or ""
        ).lower()
        if ccxt_exch == "bitget":
            ccxt_key = (
                self.CCXT_API_KEY
                or os.getenv("CCXT_API_KEY")
                or os.getenv("CCXT_APIKEY")
            )
            ccxt_secret = self.CCXT_SECRET or os.getenv("CCXT_SECRET")
            if not (self.BITGET_API_KEY or "").strip() and (ccxt_key or "").strip():
                object.__setattr__(self, "BITGET_API_KEY", ccxt_key)
            if (
                not (self.BITGET_API_SECRET or "").strip()
                and (ccxt_secret or "").strip()
            ):
                object.__setattr__(self, "BITGET_API_SECRET", ccxt_secret)

    def _validate_required_in_prod(self) -> None:
        env = (
            os.getenv("APP_ENV") or os.getenv("ENV") or os.getenv("ENVIRONMENT") or ""
        ).lower()
        is_prod = env in {"prod", "production"}
        if is_prod:
            missing: list[str] = []
            if not (self.BITGET_API_KEY or "").strip():
                missing.append("BITGET_API_KEY")
            if not (self.BITGET_API_SECRET or "").strip():
                missing.append("BITGET_API_SECRET")
            if not (self.BITGET_API_PASSPHRASE or "").strip():
                missing.append("BITGET_API_PASSPHRASE")
            if missing:
                raise RuntimeError(
                    f"Missing required credentials in production: {', '.join(missing)}"
                )


def load_settings() -> Settings:
    # Build from pydantic Settings; allow any RuntimeErrors to propagate
    # (e.g., missing creds in production). Only fall back if pydantic isn't available.
    try:
        s = Settings()  # type: ignore[call-arg]
    except Exception:
        # Minimal manual fallback (no .env parsing) if pydantic is unavailable
        class _Manual(Settings):  # type: ignore[misc]
            def __init__(self) -> None:  # type: ignore[no-untyped-def]
                import os

                env = os.environ
                self.DD_WARN_PCT = float(env.get("DD_WARN_PCT", "0.15"))
                self.DD_HALT_PCT = float(env.get("DD_HALT_PCT", "0.20"))
                self.FLASH_CRASH_PCT = float(env.get("FLASH_CRASH_PCT", "0.30"))
                self.VAR_1D_MAX = float(env.get("VAR_1D_MAX", "0.05"))
                self.KILL_SWITCH_BREACHES = int(env.get("KILL_SWITCH_BREACHES", "3"))
                self.ALLOWED_SYMBOLS = env.get(
                    "ALLOWED_SYMBOLS",
                    "BTC,ETH,SOL,XRP,ADA,LTC,AVAX,DOT,MATIC,USDT",
                )
                self.DB_URL = env.get("DB_URL", "sqlite:///data/trades.sqlite")
                self.REDIS_URL = env.get("REDIS_URL")
                self.LOG_LEVEL = env.get("LOG_LEVEL", "INFO")
                self.EXPLAIN_LEDGER_PATH = env.get(
                    "EXPLAIN_LEDGER_PATH", "explainability_ledger.jsonl"
                )
                self.RATE_LIMIT_WINDOW = int(env.get("RATE_LIMIT_WINDOW", "60"))
                self.RATE_LIMIT_REQS = int(env.get("RATE_LIMIT_REQS", "120"))
                self.AI_RATE_LIMIT_WINDOW = (
                    int(env.get("AI_RATE_LIMIT_WINDOW", "0")) or None
                )
                self.AI_RATE_LIMIT_REQS = (
                    int(env.get("AI_RATE_LIMIT_REQS", "0")) or None
                )
                self.BITGET_API_KEY = env.get("BITGET_API_KEY")
                self.BITGET_API_SECRET = env.get("BITGET_API_SECRET")
                self.BITGET_API_PASSPHRASE = env.get("BITGET_API_PASSPHRASE")
                self.CCXT_EXCHANGE_ID = env.get("CCXT_EXCHANGE_ID")
                self.CCXT_API_KEY = env.get("CCXT_API_KEY")
                self.CCXT_SECRET = env.get("CCXT_SECRET")

        s = _Manual()
        return s

    # Apply compatibility and validations
    s._map_compat()
    s._validate_required_in_prod()
    return s


__all__ = ["Settings", "load_settings"]
