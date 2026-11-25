"""Admin halt and readiness, against the shipped app.

Before phase 1 these ran against a root stub whose /readyz was a hardcoded
`{"ready": True, "components": {"db": True, "redis": True}}` -- it reported
the database healthy without opening it. They now hit the real handler.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from intradyne.api.app import app
from intradyne.risk.kill_switch import is_halted, set_halt


client = TestClient(app)


def test_admin_halt_toggle_sequence():
    assert client.get("/admin/halt").json()["enabled"] is False

    r = client.post("/admin/halt", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert client.get("/admin/halt").json()["enabled"] is True

    r = client.post("/admin/halt", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False


def test_admin_halt_actually_engages_the_gate():
    """The endpoint returning {"enabled": true} proves nothing on its own --
    what matters is that the shared kill-switch the gate reads has moved."""
    client.post("/admin/halt", json={"enabled": True})
    assert is_halted() is True
    client.post("/admin/halt", json={"enabled": False})
    assert is_halted() is False


def test_admin_halt_requires_the_secret_when_configured(monkeypatch):
    monkeypatch.setenv("ADMIN_SECRET", "s3cret")
    assert client.post("/admin/halt", json={"enabled": True}).status_code == 401
    assert is_halted() is False, "a rejected request must not change the halt"

    r = client.post(
        "/admin/halt", json={"enabled": True}, headers={"X-Admin-Secret": "s3cret"}
    )
    assert r.status_code == 200 and is_halted() is True


def test_readyz_reports_a_real_database_check():
    """conftest points DB_URL at a temporary file; this must actually open it
    rather than answering from a constant."""
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["components"]["db"] is True


def test_operator_release_requires_secret_on_both_routes(monkeypatch):
    monkeypatch.setenv("ADMIN_SECRET", "operator-test-secret")
    set_halt(True, reason="test rehearsal")

    for path, request in (
        ("/admin/halt", {"json": {"enabled": False}}),
        ("/admin/kill-switch/toggle", {"params": {"enabled": False}}),
    ):
        assert client.post(path, **request).status_code == 401
        assert is_halted()

    result = client.post(
        "/admin/kill-switch/toggle",
        params={"enabled": False},
        headers={"X-Admin-Secret": "operator-test-secret"},
    )
    assert result.status_code == 200
    assert not is_halted()


def test_operator_halt_survives_process_restart():
    set_halt(True, reason="test rehearsal")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from intradyne.risk.kill_switch import read_halt; "
            "assert read_halt() == (True, 'test rehearsal')",
        ],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_invalid_halt_payload_does_not_release():
    set_halt(True)
    assert client.post("/admin/halt", json={}).status_code == 422
    assert is_halted()


def test_corrupt_ledger_fails_readiness():
    Path(os.environ["EXPLAIN_LEDGER_PATH"]).write_bytes(b'{"event":')
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["components"]["ledger"] is False


def test_readyz_fails_when_the_database_is_unusable(monkeypatch, tmp_path):
    """The stub could never fail. The real handler must."""
    blocked_parent = tmp_path / "file-not-directory"
    blocked_parent.touch()
    monkeypatch.setenv("DB_URL", f"sqlite:///{blocked_parent / 'trades.sqlite'}")
    from intradyne.core.config import reset_settings_cache

    reset_settings_cache()
    r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["components"]["db"] is False
    assert is_halted()
    assert client.post("/admin/halt", json={"enabled": False}).status_code == 503


def test_kill_switch_toggle_actually_stops_trading():
    """It used to append a ledger line and return {"ok": true} having changed
    nothing -- the one endpoint named kill-switch was the one that could not
    stop trading, and it reported success for doing so."""
    try:
        r = client.post("/admin/kill-switch/toggle", params={"enabled": True})
        assert r.status_code == 200
        assert is_halted() is True, "the toggle reported success without halting"
        assert r.json()["kill_switch_enabled"] is True

        r = client.post("/admin/kill-switch/toggle", params={"enabled": False})
        assert is_halted() is False
        assert r.json()["kill_switch_enabled"] is False
    finally:
        from intradyne.risk.kill_switch import set_halt

        set_halt(False)


def test_kill_switch_toggle_and_halt_are_the_same_switch():
    """Two endpoints, one piece of state. If they ever diverge, an operator
    can release a halt from one and believe the other still holds."""
    from intradyne.risk.kill_switch import set_halt

    try:
        client.post("/admin/kill-switch/toggle", params={"enabled": True})
        assert client.get("/admin/halt").json()["enabled"] is True

        client.post("/admin/halt", json={"enabled": False})
        assert client.post("/admin/kill-switch/toggle", params={"enabled": False})
        assert is_halted() is False
    finally:
        set_halt(False)
