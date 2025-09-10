from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class RiskConfig(BaseModel):
    max_pos_pct: float = 0.015
    per_trade_sl_pct: float = 0.003
    tp_pct: float = 0.002
    dd_soft: float = 0.03
    dd_hard: float = 0.05
    flash_crash_drop_1h: float = 0.30
    max_concurrent_pos: int = 5
    kill_switch_breaches: int = 3


class FeesConfig(BaseModel):
    maker_bps: int = 2
    taker_bps: int = 5
    slippage_bps: int = 2


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra='ignore')

    mode: str = "paper"  # paper | live
    exchange: str = "bitget"
    use_testnet: bool = True
    live_trading_enabled: bool = False

    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""

    port: int = 8000
    log_dir: str = "logs"
    log_level: str = "INFO"
    data_dir: str = "data"
    artifacts_dir: str = "artifacts"
    optuna_db_url: str = "sqlite:///optuna.db"

    risk: RiskConfig = RiskConfig()
    fees: FeesConfig = FeesConfig()

    symbols: List[str] = []

    def load_symbols(self, markets: Optional[List[str]] = None) -> List[str]:
        here = Path(__file__).parent
        whitelist_path = here / "whitelist.json"
        with open(whitelist_path, "r", encoding="utf-8") as f:
            wl = json.load(f)
        syms = wl.get("symbols", [])
        if markets:
            syms = [s for s in syms if s in markets]
        self.symbols = syms
        return self.symbols


def load_settings() -> Settings:
    # Pydantic v2 settings class needs explicit build from env.
    # Using environment variables aligned with .env.example
    s = Settings(
        mode=os.getenv("MODE", "paper"),
        exchange=os.getenv("EXCHANGE", "bitget"),
        use_testnet=os.getenv("USE_TESTNET", "true").lower() == "true",
        live_trading_enabled=os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true",
        api_key=os.getenv("API_KEY", ""),
        api_secret=os.getenv("API_SECRET", ""),
        api_passphrase=os.getenv("API_PASSPHRASE", ""),
        port=int(os.getenv("PORT", "8000")),
        log_dir=os.getenv("LOG_DIR", "logs"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        data_dir=os.getenv("DATA_DIR", "data"),
        artifacts_dir=os.getenv("ARTIFACTS_DIR", "artifacts"),
        optuna_db_url=os.getenv("OPTUNA_DB_URL", "sqlite:///optuna.db"),
        risk=RiskConfig(
            max_pos_pct=float(os.getenv("MAX_POS_PCT", "0.015")),
            per_trade_sl_pct=float(os.getenv("PER_TRADE_SL_PCT", "0.003")),
            tp_pct=float(os.getenv("TP_PCT", "0.002")),
            dd_soft=float(os.getenv("DD_SOFT", "0.03")),
            dd_hard=float(os.getenv("DD_HARD", "0.05")),
            flash_crash_drop_1h=float(os.getenv("FLASH_CRASH_DROP_1H", "0.30")),
            max_concurrent_pos=int(os.getenv("MAX_CONCURRENT_POS", "5")),
            kill_switch_breaches=int(os.getenv("KILL_SWITCH_BREACHES", "3")),
        ),
        fees=FeesConfig(
            maker_bps=int(os.getenv("MAKER_BPS", "2")),
            taker_bps=int(os.getenv("TAKER_BPS", "5")),
            slippage_bps=int(os.getenv("SLIPPAGE_BPS", "2")),
        ),
    )
    return s
