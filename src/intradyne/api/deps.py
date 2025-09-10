from __future__ import annotations

from typing import Optional

from intradyne.core.config import load_settings
from intradyne.core.ledger import Ledger
from intradyne.risk.guardrails import Guardrails, ShariahPolicy, PriceFeed, RiskData


class _DefaultPriceFeed(PriceFeed):
    def get_price(self, symbol: str, at=None):  # type: ignore[override]
        return None


class _DefaultRiskData(RiskData):
    def equity_series_30d(self):  # type: ignore[override]
        return []

    def equity_daily_returns_30d(self):  # type: ignore[override]
        return []


_ENGINE: Optional[Guardrails] = None
_HALT_ENABLED: bool = False


def get_guardrails() -> Guardrails:
    global _ENGINE
    if _ENGINE is None:
        settings = load_settings()
        sh = ShariahPolicy(allowed_crypto=settings.allowed_crypto_list())
        _ENGINE = Guardrails(
            price_feed=_DefaultPriceFeed(),
            risk_data=_DefaultRiskData(),
            ledger=Ledger(path=settings.EXPLAIN_LEDGER_PATH),
            shariah=sh,
            thresholds={
                "dd_warn": settings.DD_WARN_PCT,
                "dd_halt": settings.DD_HALT_PCT,
                "flash": settings.FLASH_CRASH_PCT,
                "kill_switch": settings.KILL_SWITCH_BREACHES,
                "var_max": settings.VAR_1D_MAX,
            },
        )
    return _ENGINE


def get_settings():
    return load_settings()


def get_ledger():
    return get_guardrails().ledger


def set_halt(enabled: bool) -> None:
    global _HALT_ENABLED
    _HALT_ENABLED = bool(enabled)


def is_halted() -> bool:
    return _HALT_ENABLED
