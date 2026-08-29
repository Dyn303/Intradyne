"""Phase 0 hardening: path traversal, CORS, and WebSocket auth.

Auth enforcement itself is covered in tests/test_api_auth.py.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pytest

from intradyne.api.app import create_app
from intradyne.api.deps import require_ws_api_key


_AUTH_VARS = (
    "APP_ENV",
    "ENV",
    "ENVIRONMENT",
    "API_AUTH_REQUIRED",
    "X_API_KEY",
    "API_KEY",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    for k in _AUTH_VARS + ("FRONTEND_ORIGINS",):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _get(
    app: Any, path: str, params: Optional[Dict[str, str]] = None
) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.get(path, params=params or {})

    return asyncio.run(go())


# --------------------------------------------------------------------------
# Item 2: path traversal on the OHLC dataset routes
# --------------------------------------------------------------------------

# `tf` was interpolated into the filename with no validation whatsoever.
TRAVERSAL_TFS = [
    "../../../etc/passwd",
    "..\..\..\windows\win.ini",
    "1d/../../../secret",
    "....//....//etc/passwd",
]

TRAVERSAL_SYMBOLS = [
    "../../etc/passwd",
    "..\..\secret",
    "BTC/../../../etc/passwd",
    "BTC/USDT/../../..",
]


@pytest.mark.parametrize("tf", TRAVERSAL_TFS)
def test_ohlc_rejects_timeframe_traversal(clean_env, tf):
    r = _get(create_app(), "/data/ohlc", {"symbol": "BTC/USDT", "tf": tf})
    assert r.status_code == 400, f"tf={tf!r} was not rejected"
    assert r.json()["detail"] == "invalid_timeframe"


@pytest.mark.parametrize("symbol", TRAVERSAL_SYMBOLS)
def test_ohlc_rejects_symbol_traversal(clean_env, symbol):
    r = _get(create_app(), "/data/ohlc", {"symbol": symbol, "tf": "1d"})
    assert r.status_code == 400, f"symbol={symbol!r} was not rejected"
    assert r.json()["detail"] == "invalid_symbol"


def test_ohlc_rejects_symbol_outside_whitelist(clean_env):
    """Well-formed but not Shariah-whitelisted."""
    r = _get(create_app(), "/data/ohlc", {"symbol": "DOGE/USDT", "tf": "1d"})
    assert r.status_code == 400
    assert r.json()["detail"].startswith("symbol_not_allowed")


def test_ohlc_still_serves_a_valid_dataset(clean_env):
    """Guard against the validator simply rejecting everything."""
    r = _get(create_app(), "/data/ohlc", {"symbol": "BTC/USDT", "tf": "1d"})
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "BTC/USDT" and body["tf"] == "1d"
    assert isinstance(body["data"], list) and body["data"]


def test_ohlc_unknown_but_valid_pair_is_404_not_400(clean_env):
    """A whitelisted pair with no stored dataset is 'not found', not 'invalid'."""
    r = _get(create_app(), "/data/ohlc", {"symbol": "LTC/USDT", "tf": "4h"})
    assert r.status_code == 404


# --------------------------------------------------------------------------
# Item 4: CORS
# --------------------------------------------------------------------------


def _cors_options(app: Any) -> Dict[str, Any]:
    for mw in app.user_middleware:
        if "CORS" in mw.cls.__name__:
            return dict(getattr(mw, "kwargs", {}) or {})
    raise AssertionError("CORS middleware not installed")


def test_wildcard_origin_disables_credentials(clean_env):
    """'*' + credentials is spec-invalid; Starlette would echo the caller's
    Origin back, turning the wildcard into 'trust every site'."""
    opts = _cors_options(create_app())
    assert opts["allow_origins"] == ["*"]
    assert opts["allow_credentials"] is False


def test_explicit_origins_keep_credentials(clean_env):
    clean_env.setenv(
        "FRONTEND_ORIGINS", "https://app.example.com,https://admin.example.com"
    )
    opts = _cors_options(create_app())
    assert opts["allow_origins"] == [
        "https://app.example.com",
        "https://admin.example.com",
    ]
    assert opts["allow_credentials"] is True


def test_wildcard_origin_refused_in_prod(clean_env):
    clean_env.setenv("APP_ENV", "production")
    clean_env.setenv("X_API_KEY", "k")
    for var in ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE"):
        clean_env.setenv(var, "dummy")
    with pytest.raises(RuntimeError, match="FRONTEND_ORIGINS"):
        create_app()


# --------------------------------------------------------------------------
# Item 3: WebSocket handshake auth
# --------------------------------------------------------------------------


class _FakeWebSocket:
    """Minimal stand-in. The installed starlette's TestClient needs httpx2, so
    the handshake check is exercised directly rather than over a real socket."""

    def __init__(
        self,
        headers: Optional[Dict[str, str]] = None,
        query: Optional[Dict[str, str]] = None,
    ):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.query_params = dict(query or {})
        self.closed: List[Tuple[int, Optional[str]]] = []

    async def close(self, code: int = 1000, reason: Optional[str] = None) -> None:
        self.closed.append((code, reason))


def _check(ws: _FakeWebSocket) -> bool:
    return asyncio.run(require_ws_api_key(ws))


def test_ws_open_when_auth_not_required(clean_env):
    ws = _FakeWebSocket()
    assert _check(ws) is True
    assert ws.closed == []


def test_ws_rejects_missing_key(clean_env):
    clean_env.setenv("API_AUTH_REQUIRED", "1")
    clean_env.setenv("X_API_KEY", "s3cret")
    ws = _FakeWebSocket()
    assert _check(ws) is False
    assert ws.closed and ws.closed[0][0] == 1008


def test_ws_rejects_wrong_key(clean_env):
    clean_env.setenv("API_AUTH_REQUIRED", "1")
    clean_env.setenv("X_API_KEY", "s3cret")
    ws = _FakeWebSocket(headers={"X-API-Key": "nope"})
    assert _check(ws) is False
    assert ws.closed[0][1] == "invalid_api_key"


def test_ws_accepts_header_key(clean_env):
    clean_env.setenv("API_AUTH_REQUIRED", "1")
    clean_env.setenv("X_API_KEY", "s3cret")
    ws = _FakeWebSocket(headers={"X-API-Key": "s3cret"})
    assert _check(ws) is True
    assert ws.closed == []


def test_ws_accepts_query_key_for_browsers(clean_env):
    """Browsers cannot set headers on a WebSocket, so a query token is allowed."""
    clean_env.setenv("API_AUTH_REQUIRED", "1")
    clean_env.setenv("X_API_KEY", "s3cret")
    ws = _FakeWebSocket(query={"api_key": "s3cret"})
    assert _check(ws) is True
    assert ws.closed == []


def test_ws_fails_closed_when_key_unconfigured(clean_env):
    clean_env.setenv("API_AUTH_REQUIRED", "1")
    ws = _FakeWebSocket(headers={"X-API-Key": "anything"})
    assert _check(ws) is False
    assert ws.closed[0][1] == "api_auth_misconfigured"
