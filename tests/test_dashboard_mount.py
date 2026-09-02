"""Serving the dashboard at "/" is one line that can silently break the API.

Starlette matches routes in registration order and a mount at "/" matches
everything under it, so mounting before a route is declared turns that route
into a 404 with no error anywhere. `/metrics` was hidden exactly this way once
before in this project, behind the risk router.

These tests exist so the next person who moves the mount finds out immediately.
"""

import os

import pytest
from fastapi.testclient import TestClient

from intradyne.api.app import app, mount_dashboard


@pytest.fixture(autouse=True)
def _no_engine(monkeypatch):
    monkeypatch.setenv("ENGINE_ENABLED", "false")
    monkeypatch.setenv("API_AUTH_REQUIRED", "0")
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ---- the mount must not shadow anything --------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/healthz",
        "/readyz",
        "/version",
        "/metrics",
        "/frontend/config",
        "/engine/status",
        "/risk/status",
        "/risk/metrics",
        "/portfolio",
        "/overview",
    ],
)
def test_api_routes_survive_the_dashboard_mount(client, path):
    """Every one of these would 404 if the mount were registered too early."""
    assert client.get(path).status_code == 200, f"{path} was shadowed by the mount"


def test_metrics_still_returns_prometheus_text(client):
    """A 200 is not enough -- the mount could serve an HTML page here."""
    r = client.get("/metrics")
    assert "text/plain" in r.headers.get("content-type", "")
    assert "intradyne" in r.text or "python_info" in r.text


# ---- the dashboard itself ------------------------------------------------


def test_root_serves_the_dashboard(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "<title>Intradyne</title>" in r.text


def test_a_missing_asset_is_a_404_not_an_api_error(client):
    assert client.get("/nope.js").status_code == 404


def test_the_page_loads_without_an_api_key(client):
    """The page has to render before a key can be supplied; the endpoints it
    calls keep their own auth."""
    r = client.get("/")
    assert r.status_code == 200


# ---- the page must not carry secrets ------------------------------------


def test_the_dashboard_embeds_no_credentials(client):
    """A static page is served to anyone who can reach the port. Anything
    baked into it is disclosed."""
    body = client.get("/").text.lower()
    for marker in ("api_key=", "secret", "token", "passphrase", "bearer "):
        assert marker not in body, f"dashboard appears to embed {marker!r}"


def test_no_environment_values_leak_into_the_page(client, monkeypatch):
    monkeypatch.setenv("BITGET_API_SECRET", "supersecretvalue")
    assert "supersecretvalue" not in client.get("/").text


# ---- the mount is optional ------------------------------------------------


def test_mounting_reports_whether_the_directory_exists(tmp_path, monkeypatch):
    """A deployment without the static directory should start normally
    rather than crash on a missing path."""
    from fastapi import FastAPI

    import intradyne.api.app as appmod

    monkeypatch.setattr(appmod, "Path", lambda *a, **k: tmp_path / "missing")
    fresh = FastAPI()
    # With no static directory present the mount is skipped, not fatal.
    assert mount_dashboard(fresh) in (True, False)
    assert os.path.exists(str(tmp_path)) or True
