from __future__ import annotations

import secrets
from datetime import datetime
from typing import Optional, List, Tuple

from fastapi import Header, HTTPException

from intradyne.core.config import load_settings
from intradyne.core.ledger import Ledger
from intradyne.risk.guardrails import Guardrails, ShariahPolicy, PriceFeed, RiskData


class _DefaultPriceFeed(PriceFeed):
    def get_price(self, symbol: str, at: Optional[datetime] = None) -> Optional[float]:
        return None


class _DefaultRiskData(RiskData):
    def equity_series_30d(self) -> List[Tuple[datetime, float]]:
        return []

    def equity_daily_returns_30d(self) -> List[float]:
        return []


_ENGINE: Optional[Guardrails] = None


def get_guardrails() -> Guardrails:
    global _ENGINE
    if _ENGINE is None:
        settings = load_settings()
        sh = ShariahPolicy(allowed_crypto=settings.allowed_crypto_list())
        _ENGINE = Guardrails(
            price_feed=_DefaultPriceFeed(),
            risk_data=_DefaultRiskData(),
            ledger=Ledger(path=settings.explain_ledger_path),
            shariah=sh,
            thresholds={
                "dd_warn": settings.guardrails.dd_warn_pct,
                "dd_halt": settings.guardrails.dd_halt_pct,
                "flash": settings.guardrails.flash_crash_pct,
                "kill_switch": settings.guardrails.kill_switch_breaches,
                "var_max": settings.guardrails.var_1d_max,
            },
        )
    return _ENGINE


def get_settings():
    return load_settings()


def get_ledger():
    return get_guardrails().ledger


def is_prod() -> bool:
    """True when the app is running in a production environment."""
    import os

    env = (
        os.getenv("APP_ENV") or os.getenv("ENV") or os.getenv("ENVIRONMENT") or ""
    ).lower()
    return env in {"prod", "production"}


def api_auth_required() -> bool:
    """Whether API authentication is enforced.

    Default-on in production; opt-in elsewhere. Single source of truth -- the
    HTTP dependency wiring in api/app.py and the WebSocket handshake check both
    read this, so they cannot drift apart.
    """
    import os

    cfg = (os.getenv("API_AUTH_REQUIRED") or "").strip().lower()
    if is_prod() and not cfg:
        return True
    return cfg in {"1", "true", "yes"}


def configured_api_key() -> str:
    """The expected value of the X-API-Key header.

    Deliberately does NOT fall back to ``API_KEY``: that name is the *broker*
    credential in ``.env.example``, and reusing it here would silently promote
    an exchange secret into an HTTP auth token.
    """
    import os

    return (os.getenv("X_API_KEY") or "").strip()


# API key requirement for frontend/backend requests.
#
# Whether auth is enforced is decided once, at app construction, by which
# routers receive this dependency. This function does not second-guess that
# decision -- if it runs at all, a valid key is required. (It previously
# re-read API_AUTH_REQUIRED and returned early, which silently disabled the
# production default-on behaviour configured in api/app.py.)
async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    expected = configured_api_key()
    if not expected:
        # Fail closed: auth was requested but no key is configured.
        raise HTTPException(
            status_code=503,
            detail="api_auth_misconfigured: X_API_KEY is not set",
        )
    if not secrets.compare_digest(x_api_key or "", expected):
        raise HTTPException(status_code=401, detail="invalid_api_key")


async def require_ws_api_key(websocket) -> bool:
    """Authenticate a WebSocket handshake. Returns False once rejected.

    WebSocket routes cannot use the HTTP `Depends` chain, so this is called
    explicitly at the top of each handler *before* `accept()`; closing an
    un-accepted socket rejects the handshake outright.

    Browsers cannot set headers on a WebSocket connection, so an `api_key`
    query parameter is accepted as well as the `X-API-Key` header. That does
    put the key in the URL, hence it is only consulted when a header was not
    supplied -- non-browser clients should send the header.
    """
    if not api_auth_required():
        return True
    expected = configured_api_key()
    if not expected:
        await websocket.close(code=1008, reason="api_auth_misconfigured")
        return False
    try:
        supplied = websocket.headers.get("x-api-key") or ""
        if not supplied:
            supplied = websocket.query_params.get("api_key") or ""
    except Exception:
        supplied = ""
    if not secrets.compare_digest(supplied, expected):
        await websocket.close(code=1008, reason="invalid_api_key")
        return False
    return True
