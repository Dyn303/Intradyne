from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env(*names: str, default: str = "") -> str:
    """First non-empty value among `names`.

    Several settings are reachable under two names because the API and the
    engine were configured independently before they were merged. The first
    name listed is canonical.
    """
    for n in names:
        v = os.getenv(n)
        if v is not None and v.strip() != "":
            return v
    return default


def _f(*names: str, default: float) -> float:
    try:
        return float(_env(*names) or default)
    except ValueError:
        return default


def _i(*names: str, default: int) -> int:
    try:
        return int(_env(*names) or default)
    except ValueError:
        return default


def _b(*names: str, default: bool = False) -> bool:
    raw = _env(*names)
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class RiskConfig(BaseModel):
    """Tier 2: position sizing and per-trade exits, applied inside the engine
    loop by RiskManager."""

    max_pos_pct: float = 0.015
    per_trade_sl_pct: float = 0.003
    tp_pct: float = 0.002
    max_concurrent_pos: int = 5
    # Session drawdown. Distinct from the 30-day peak-to-trough measure in
    # GuardrailConfig -- see MIGRATION.md phase 3.
    dd_soft: float = 0.03
    dd_hard: float = 0.05
    flash_crash_drop_1h: float = 0.30
    kill_switch_breaches: int = 3
    use_atr: bool = False
    atr_window: int = 14
    atr_k_sl: float = 1.5
    atr_k_tp: float = 2.0


class GuardrailConfig(BaseModel):
    """Tier 1: the pre-trade veto thresholds used by Guardrails.gate_trade."""

    # 30-day peak-to-trough drawdown, not the session drawdown above.
    dd_warn_pct: float = 0.15
    dd_halt_pct: float = 0.20
    flash_crash_pct: float = 0.30
    var_1d_max: float = 0.05
    kill_switch_breaches: int = 3


class FeesConfig(BaseModel):
    maker_bps: int = 2
    taker_bps: int = 5
    slippage_bps: int = 2


class Settings(BaseSettings):
    """One configuration for the whole system.

    The API and the engine previously had separate Settings classes reading
    overlapping but differently-named environment variables, so setting
    FLASH_CRASH_PCT armed the API guardrail while leaving the engine's
    identical threshold at its default. Shared values are now read once and
    handed to both tiers.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.txt"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Mode and venue
    mode: str = "paper"  # paper | live
    exchange: str = "bitget"
    use_testnet: bool = True
    live_trading_enabled: bool = False

    # Broker credentials. API_KEY / API_SECRET / API_PASSPHRASE are accepted
    # as legacy aliases. These are the *broker* credentials; the HTTP API key
    # is X_API_KEY and is deliberately unrelated.
    bitget_api_key: Optional[str] = None
    bitget_api_secret: Optional[str] = None
    bitget_api_passphrase: Optional[str] = None
    ccxt_exchange_id: Optional[str] = None
    ccxt_api_key: Optional[str] = None
    ccxt_secret: Optional[str] = None

    # Paths and infrastructure
    port: int = 8000
    log_dir: str = "logs"
    log_level: str = "INFO"
    data_dir: str = "data"
    artifacts_dir: str = "artifacts"
    optuna_db_url: str = "sqlite:///optuna.db"
    db_url: str = "sqlite:///data/trades.sqlite"
    redis_url: Optional[str] = None
    explain_ledger_path: str = "explainability_ledger.jsonl"

    # Universe. Accepts either BASE or BASE/QUOTE.
    allowed_symbols: str = "BTC,ETH,SOL,XRP,ADA,LTC,AVAX,DOT,MATIC,USDT"
    symbols: List[str] = []

    # HTTP rate limits
    rate_limit_window: int = 60
    rate_limit_reqs: int = 120
    ai_rate_limit_window: Optional[int] = None
    ai_rate_limit_reqs: Optional[int] = None

    # Trading loop. Off by default: phase 2 puts the machinery in place and
    # switching it on is a separate, deliberate step.
    engine_enabled: bool = False

    # Execution filters
    max_spread_bps: int = 0  # 0 disables
    entry_cooldown_s: int = 0

    # Sentiment
    sentiment_enabled: bool = False
    sentiment_long_min: float = 0.0
    sentiment_size_min: float = 0.8
    sentiment_size_max: float = 1.2
    sentiment_smooth_n: int = 12

    risk: RiskConfig = RiskConfig()
    guardrails: GuardrailConfig = GuardrailConfig()
    fees: FeesConfig = FeesConfig()

    # ---- derived -------------------------------------------------------

    def allowed_crypto_list(self) -> List[str]:
        raw = [s.strip() for s in (self.allowed_symbols or "").split(",") if s.strip()]
        out: List[str] = []
        for s in raw:
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

    def load_symbols(self, markets: Optional[List[str]] = None) -> List[str]:
        """Load the Shariah whitelist, optionally intersected with the venue's
        tradable markets."""
        whitelist_path = (
            Path(__file__).resolve().parent.parent / "engine" / "whitelist.json"
        )
        with open(whitelist_path, "r", encoding="utf-8") as f:
            wl = json.load(f)
        syms = wl.get("symbols", [])
        if markets:
            syms = [s for s in syms if s in markets]
        self.symbols = syms
        return self.symbols

    # ---- validation ----------------------------------------------------

    def _map_compat(self) -> None:
        """Fill BITGET_* from CCXT_* when the venue is bitget."""
        if (self.ccxt_exchange_id or "").lower() != "bitget":
            return
        if (
            not (self.bitget_api_key or "").strip()
            and (self.ccxt_api_key or "").strip()
        ):
            self.bitget_api_key = self.ccxt_api_key
        if (
            not (self.bitget_api_secret or "").strip()
            and (self.ccxt_secret or "").strip()
        ):
            self.bitget_api_secret = self.ccxt_secret

    def _validate_required_in_prod(self) -> None:
        env = _env("APP_ENV", "ENV", "ENVIRONMENT").lower()
        if env not in {"prod", "production"}:
            return
        missing = [
            name
            for name, val in (
                ("BITGET_API_KEY", self.bitget_api_key),
                ("BITGET_API_SECRET", self.bitget_api_secret),
                ("BITGET_API_PASSPHRASE", self.bitget_api_passphrase),
            )
            if not (val or "").strip()
        ]
        if missing:
            raise RuntimeError(
                f"Missing required credentials in production: {', '.join(missing)}"
            )


# Phase 5 of MIGRATION.md opens live trading. Until the controls listed in
# assert_live_trading_gate() exist, the system refuses to start in live mode.
LIVE_TRADING_GATE_OPEN = False


def assert_live_trading_gate(settings: "Settings") -> None:
    """Refuse to start with live trading armed before phase 5 is done.

    Deliberately not overridable by an environment variable: an env override
    is exactly how this would get flipped by accident. Opening it is a code
    change to LIVE_TRADING_GATE_OPEN, which leaves a reviewable commit.
    """
    if LIVE_TRADING_GATE_OPEN:
        return
    if settings.mode == "live" and settings.live_trading_enabled:
        raise RuntimeError(
            "Live trading is armed (MODE=live and LIVE_TRADING_ENABLED=true) but "
            "the live-readiness work is not done. Still missing: idempotency keys "
            "on order submission, reconciliation against exchange state on "
            "restart, per-symbol and daily notional caps, and alerting on "
            "halt/kill-switch. See MIGRATION.md phase 5. Run with MODE=paper."
        )


def _build_settings() -> Settings:
    # Shared thresholds are read once here and handed to both tiers, so one
    # environment variable cannot arm one tier and leave the other at default.
    flash = _f("FLASH_CRASH_PCT", "FLASH_CRASH_DROP_1H", default=0.30)
    kill = _i("KILL_SWITCH_BREACHES", default=3)

    s = Settings(
        bitget_api_key=_env("BITGET_API_KEY", "API_KEY") or None,
        bitget_api_secret=_env("BITGET_API_SECRET", "API_SECRET") or None,
        bitget_api_passphrase=_env("BITGET_API_PASSPHRASE", "API_PASSPHRASE") or None,
        sentiment_enabled=_b("SENTIMENT_ENABLED", "SENTIMENT_ENABLE"),
        risk=RiskConfig(
            max_pos_pct=_f("MAX_POS_PCT", default=0.015),
            per_trade_sl_pct=_f("PER_TRADE_SL_PCT", default=0.003),
            tp_pct=_f("TP_PCT", default=0.002),
            max_concurrent_pos=_i("MAX_CONCURRENT_POS", default=5),
            dd_soft=_f("DD_SOFT", default=0.03),
            dd_hard=_f("DD_HARD", default=0.05),
            flash_crash_drop_1h=flash,
            kill_switch_breaches=kill,
            use_atr=_b("USE_ATR"),
            atr_window=_i("ATR_WINDOW", default=14),
            atr_k_sl=_f("ATR_K_SL", default=1.5),
            atr_k_tp=_f("ATR_K_TP", default=2.0),
        ),
        guardrails=GuardrailConfig(
            dd_warn_pct=_f("DD_WARN_PCT", default=0.15),
            dd_halt_pct=_f("DD_HALT_PCT", default=0.20),
            flash_crash_pct=flash,
            var_1d_max=_f("VAR_1D_MAX", default=0.05),
            kill_switch_breaches=kill,
        ),
        fees=FeesConfig(
            maker_bps=_i("MAKER_BPS", default=2),
            taker_bps=_i("TAKER_BPS", default=5),
            slippage_bps=_i("SLIPPAGE_BPS", default=2),
        ),
    )
    s._map_compat()
    s._validate_required_in_prod()
    return s


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Return the process-wide Settings, built once.

    Called on every request by the rate limiters and by several routes.
    Uncached, each call re-parsed the .env files and re-ran the production
    credential check, so a prod deployment without broker credentials raised
    on every request -- /healthz included -- even though the API places no
    orders itself.

    Tests that manipulate the environment must call reset_settings_cache();
    tests/conftest.py does so automatically between tests.
    """
    return _build_settings()


def reset_settings_cache() -> None:
    """Drop the cached Settings so the next load re-reads the environment."""
    load_settings.cache_clear()


__all__ = [
    "Settings",
    "assert_live_trading_gate",
    "LIVE_TRADING_GATE_OPEN",
    "RiskConfig",
    "GuardrailConfig",
    "FeesConfig",
    "load_settings",
    "reset_settings_cache",
]
