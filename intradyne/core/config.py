from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    APP_ENV: str
    BITGET_API_KEY: str
    BITGET_API_SECRET: str
    BITGET_API_PASSPHRASE: str
    CCXT_EXCHANGE_ID: str


def load_settings() -> Settings:
    env = os.getenv("APP_ENV", "dev").lower()
    bitget_key = os.getenv("BITGET_API_KEY", "")
    bitget_secret = os.getenv("BITGET_API_SECRET", "")
    bitget_passphrase = os.getenv("BITGET_API_PASSPHRASE", "")
    ex_id = os.getenv("CCXT_EXCHANGE_ID", "bitget").lower()

    # Map CCXT_* to BITGET_* when exchange is bitget and missing
    if ex_id == "bitget":
        if not bitget_key:
            bitget_key = os.getenv("CCXT_API_KEY", bitget_key)
        if not bitget_secret:
            bitget_secret = os.getenv("CCXT_SECRET", bitget_secret)

    if env == "production":
        if not (bitget_key and bitget_secret and bitget_passphrase):
            raise RuntimeError("Missing Bitget credentials in production")

    return Settings(
        APP_ENV=env,
        BITGET_API_KEY=bitget_key,
        BITGET_API_SECRET=bitget_secret,
        BITGET_API_PASSPHRASE=bitget_passphrase,
        CCXT_EXCHANGE_ID=ex_id,
    )
