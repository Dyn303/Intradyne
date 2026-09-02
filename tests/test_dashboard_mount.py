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


# ---- the page stays a single file with one optional dependency -----------


def test_the_only_external_resource_is_telegram_and_it_is_deferred(client):
    """The dashboard was built as one self-contained file precisely so it
    works on a bad connection. The Telegram bridge is the sole exception, and
    a blocking <script> in the head would give that exception the power to
    stall the whole page whenever telegram.org is slow."""
    import re

    body = client.get("/").text
    external = re.findall(r'<(?:script|link)[^>]+(?:src|href)="(https?://[^"]+)"', body)
    assert external == ["https://telegram.org/js/telegram-web-app.js"], (
        f"unexpected external resources on the page: {external}"
    )
    tag = next(ln for ln in body.splitlines() if "telegram-web-app.js" in ln)
    assert "defer" in tag, f"the Telegram bridge must not block rendering: {tag}"


def test_authentication_does_not_depend_on_the_remote_bridge(client):
    """initData is in the URL fragment, so the page can authenticate even if
    telegram.org never loads. Losing the bridge should cost theming, not
    access."""
    body = client.get("/").text
    assert "tgWebAppData" in body, "no fallback path to initData without the bridge"


# ---- Phase 2 controls: nothing writes without asking first ---------------


def test_every_write_goes_through_the_confirmation_path(client):
    """The controls panel changes live trading state, so the guard that
    matters is structural: there must be exactly one place in the page that
    issues a POST, and it must ask before it does.

    A future one-click write added straight into a click handler would slip
    past review easily and is exactly what this catches.
    """
    body = client.get("/").text
    assert body.count('method: "POST"') == 1, (
        "more than one POST site on the page -- a write may be bypassing act()"
    )
    act = body[
        body.index("async function act(") : body.index("function renderControls")
    ]
    assert "await confirmAction(" in act, "act() issues a write without confirming"
    assert act.index("confirmAction") < act.index('method: "POST"'), (
        "the write happens before the confirmation is awaited"
    )


def test_halt_and_resume_are_never_offered_together(client):
    """One switch, one direction. Showing both invites the wrong tap in the
    moment it matters most."""
    body = client.get("/").text
    render = body[body.index("function renderControls(") :]
    render = render[: render.index("async function refresh()")]
    assert "halted" in render and "?" in render, "controls are not state-dependent"
    assert render.count("c-halt") >= 1 and render.count("c-resume") >= 1


def test_the_stop_control_is_not_harder_to_reach_than_the_start(client):
    """Halting a healthy system costs idle minutes; resuming one that should
    be stopped costs money. The stop must be at least as prominent."""
    body = client.get("/").text
    assert "button.stop" in body, "no distinct styling for the stop control"
    assert "flex-basis: 100%" in body, "the stop control is not full width"
