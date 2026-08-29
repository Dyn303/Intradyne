"""Auth enforcement on the shipped API.

These import ``src.intradyne.api.app`` rather than ``intradyne.api.app``: under
``pytest.ini``'s path order the latter resolves to the root ``intradyne/``
stub app, which is *not* what the container runs. MIGRATION.md phase 1 deletes
that stub, after which the plain import is correct here.

Requests go through ``httpx.ASGITransport`` instead of ``TestClient`` so the
suite does not depend on which starlette/httpx pairing is installed.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

import httpx
import pytest

from src.intradyne.api.app import create_app


_PROD = {
    "APP_ENV": "production",
    # src/core/config.py refuses to load in prod without these, and the rate
    # limiter calls load_settings() per request. Unrelated to auth.
    "BITGET_API_KEY": "dummy",
    "BITGET_API_SECRET": "dummy",
    "BITGET_API_PASSPHRASE": "dummy",
    # A '*' origin list is refused in prod (see test_api_hardening.py). Pin an
    # explicit origin so these tests exercise auth and nothing else.
    "FRONTEND_ORIGINS": "https://app.example.com",
}

_AUTH_VARS = (
    "APP_ENV",
    "ENV",
    "ENVIRONMENT",
    "API_AUTH_REQUIRED",
    "X_API_KEY",
    "API_KEY",
    "FRONTEND_ORIGINS",
)


@pytest.fixture
def build(monkeypatch: pytest.MonkeyPatch):
    """Build an app under a specific environment."""

    def _build(env: Dict[str, str]):
        for k in _AUTH_VARS:
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        # NB: create_app() is safe to call repeatedly, but only because the
        # Prometheus collectors in routes/research.py, routes/data.py and
        # risk/guardrails.py register at *module import* rather than per app.
        # Re-importing those modules in one process raises DuplicateTimeseries;
        # phase 1 moves registration into a factory. Do not "fix" that here by
        # clearing the default REGISTRY -- it is global, and wiping it breaks
        # tests/test_metrics_endpoint.py further down the run.
        return create_app()

    return _build


def _get(
    app: Any,
    path: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, str]] = None,
) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.get(path, headers=headers or {}, params=params or {})

    return asyncio.run(go())


def test_prod_without_key_refuses_to_boot(build):
    """Fail closed. Previously this served every route unauthenticated."""
    with pytest.raises(RuntimeError, match="X_API_KEY"):
        build(_PROD)


def test_prod_requires_the_header(build):
    app = build({**_PROD, "X_API_KEY": "s3cret"})
    assert _get(app, "/healthz").status_code == 401
    assert _get(app, "/healthz", headers={"X-API-Key": "wrong"}).status_code == 401
    assert _get(app, "/healthz", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_key_in_query_string_is_rejected(build):
    """Regression: the dependency took no Header() marker, so FastAPI bound it
    as a query parameter -- the header was ignored and the secret belonged in
    the URL, where it lands in access logs."""
    app = build({**_PROD, "X_API_KEY": "s3cret"})
    r = _get(app, "/healthz", params={"x_api_key": "s3cret"})
    assert r.status_code == 401


def test_broker_credential_does_not_satisfy_api_auth(build):
    """API_KEY is the *broker* credential in .env.example. It must never be
    accepted as the HTTP auth token."""
    with pytest.raises(RuntimeError, match="X_API_KEY"):
        build({**_PROD, "API_KEY": "broker-secret"})


def test_non_prod_default_is_open(build):
    app = build({})
    assert _get(app, "/healthz").status_code == 200


def test_explicit_opt_in_outside_prod(build):
    app = build({"API_AUTH_REQUIRED": "1", "X_API_KEY": "k"})
    assert _get(app, "/healthz").status_code == 401
    assert _get(app, "/healthz", headers={"X-API-Key": "k"}).status_code == 200


def test_env_is_restored_between_tests():
    """Guard against monkeypatch leakage into other modules' tests."""
    assert os.getenv("APP_ENV") not in {"prod", "production"}
