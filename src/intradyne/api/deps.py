from __future__ import annotations

import secrets
from datetime import datetime
from typing import TYPE_CHECKING, Optional, List, Tuple

from fastapi import Header, HTTPException

from loguru import logger

from intradyne.api import telegram_auth
from intradyne.core.config import load_settings
from intradyne.core.equity import EquityHistory
from intradyne.core.ledger import Ledger
from intradyne.core.idempotency import OrderKeyStore
from intradyne.core.limits import NotionalTracker
from intradyne.core.marks import MarkStore
from intradyne.risk.guardrails import Guardrails, ShariahPolicy, PriceFeed, RiskData

if TYPE_CHECKING:  # pragma: no cover
    from intradyne.engine.execution import ExecutionManager


class _MarkStorePriceFeed(PriceFeed):
    """Prices from the in-process mark store.

    This replaced a stub that returned None for every symbol, which meant the
    flash-crash guardrail compared None against None and never fired.
    """

    def __init__(self, marks: MarkStore) -> None:
        self._marks = marks

    def get_price(self, symbol: str, at: Optional[datetime] = None) -> Optional[float]:
        return self._marks.get(symbol, at)


class _SqliteRiskData(RiskData):
    """Equity history from disk.

    This replaced a stub returning []; dd_30d([]) is 0.0, so the drawdown halt
    could never trigger however far equity fell.
    """

    def __init__(self, history: EquityHistory) -> None:
        self._history = history

    def equity_series_30d(self) -> List[Tuple[datetime, float]]:
        return self._history.series_30d()

    def equity_daily_returns_30d(self) -> List[float]:
        return self._history.daily_returns(30)


_ENGINE: Optional[Guardrails] = None
_EXECUTION: Optional["ExecutionManager"] = None
_MARKS: Optional[MarkStore] = None
_EQUITY: Optional[EquityHistory] = None
_LIMITS: Optional[NotionalTracker] = None


def get_mark_store() -> MarkStore:
    """Recent prices, shared by the order path and the flash-crash guardrail."""
    global _MARKS
    if _MARKS is None:
        _MARKS = MarkStore()
    return _MARKS


def get_equity_history() -> EquityHistory:
    """Durable equity series backing the drawdown and VaR guardrails."""
    global _EQUITY
    if _EQUITY is None:
        _EQUITY = EquityHistory(load_settings().db_url)
    return _EQUITY


def get_notional_tracker() -> NotionalTracker:
    """Durable traded-notional record backing the exposure caps."""
    global _LIMITS
    if _LIMITS is None:
        _LIMITS = NotionalTracker(load_settings().db_url)
    return _LIMITS


def get_guardrails() -> Guardrails:
    global _ENGINE
    if _ENGINE is None:
        settings = load_settings()
        sh = ShariahPolicy(allowed_crypto=settings.allowed_crypto_list())
        _ENGINE = Guardrails(
            price_feed=_MarkStorePriceFeed(get_mark_store()),
            risk_data=_SqliteRiskData(get_equity_history()),
            ledger=Ledger(path=settings.explain_ledger_path),
            shariah=sh,
            thresholds={
                "dd_warn": settings.guardrails.dd_warn_pct,
                "dd_halt": settings.guardrails.dd_halt_pct,
                "flash": settings.guardrails.flash_crash_pct,
                "kill_switch": settings.guardrails.kill_switch_breaches,
                "var_max": settings.guardrails.var_1d_max,
                "max_order_notional": settings.guardrails.max_order_notional,
                "max_symbol_notional_24h": settings.guardrails.max_symbol_notional_24h,
                "max_daily_notional": settings.guardrails.max_daily_notional,
            },
            limits=get_notional_tracker(),
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


async def require_api_key_or_telegram(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_telegram_init_data: str | None = Header(
        default=None, alias="X-Telegram-Init-Data"
    ),
) -> None:
    """Accept either the API key or a signed Telegram Mini App `initData`.

    Two credentials for one door, because they serve different clients. A
    script or a curl call sends the header key. The Mini App cannot: a page
    running inside Telegram would have to embed the key in JavaScript that
    anyone opening the app can read, which is precisely the objection recorded
    against exposing this API in `docs/FULLSTACK_PLAN.md`. Telegram signs
    `initData` with the bot token instead, so the browser carries a signature
    and never a secret.

    initData is tried first and, when present, decides the request outright
    rather than falling through to the key. Falling through would turn every
    rejected signature -- expired, wrong user, forged -- into an
    indistinguishable "no key supplied" 401, which hides exactly the failures
    worth seeing in a log.
    """
    if x_telegram_init_data:
        try:
            user = telegram_auth.verify_init_data(x_telegram_init_data)
        except telegram_auth.InitDataError as exc:
            # The reason goes to the log, never to the response: distinguishing
            # "bad signature" from "valid signature, wrong user" tells an
            # attacker whether they are holding something real.
            logger.bind(event="miniapp_auth_denied").warning({"reason": exc.reason})
            raise HTTPException(status_code=401, detail="unauthorized") from None
        logger.bind(event="miniapp_auth_ok").debug({"user": user.label})
        return

    if not configured_api_key() and telegram_auth.enabled():
        # Mini App auth is the only credential this deployment has, and this
        # request did not use it. Delegating would hit require_api_key's
        # fail-closed branch and answer 503 "misconfigured", which is both the
        # wrong status -- the caller is unauthorized, the server is fine -- and
        # a misleading one to debug, since it reports a missing X_API_KEY that
        # was never meant to exist.
        raise HTTPException(status_code=401, detail="unauthorized")

    await require_api_key(x_api_key)


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


def get_execution_manager() -> "ExecutionManager":
    """The process-wide order path.

    Both POST /orders and the engine loop submit through this one instance, so
    there is a single portfolio, a single ledger, and exactly one place where
    the Tier 1 gate runs. The live broker is deliberately absent: live trading
    is barred until MIGRATION.md phase 5.
    """
    global _EXECUTION
    if _EXECUTION is None:
        from intradyne.engine.broker_paper import PaperBroker
        from intradyne.engine.execution import ExecContext, ExecutionManager
        from intradyne.engine.portfolio import Portfolio

        settings = load_settings()
        guardrails = get_guardrails()
        portfolio = Portfolio(
            maker_bps=settings.fees.maker_bps,
            taker_bps=settings.fees.taker_bps,
        )
        _EXECUTION = ExecutionManager(
            ExecContext(
                portfolio=portfolio,
                paper=PaperBroker(
                    portfolio,
                    slippage_bps=settings.fees.slippage_bps,
                    limit_ttl_s=settings.limit_ttl_s,
                ),
                # Same ledger the guardrails write to, so refusals and fills
                # land in one chain.
                ledger=guardrails.ledger,
                whitelist=settings.allowed_crypto_list(),
                live_broker=None,
                live_enabled=False,
                guardrails=guardrails,
                marks=get_mark_store(),
                equity=get_equity_history(),
                limits=get_notional_tracker(),
                order_keys=OrderKeyStore(settings.db_url),
                execution_mode=settings.execution_mode,
                maker_offset_bps=settings.maker_offset_bps,
            )
        )
    return _EXECUTION


def get_portfolio():
    return get_execution_manager().ctx.portfolio


def reset_execution_manager() -> None:
    """Drop the cached order path and its risk inputs. For tests."""
    global _EXECUTION, _ENGINE, _MARKS, _EQUITY, _LIMITS
    _EXECUTION = None
    _ENGINE = None
    _MARKS = None
    _EQUITY = None
    _LIMITS = None
