from __future__ import annotations

from fastapi.testclient import TestClient

from intradyne.api.app import app


client = TestClient(app)


def test_admin_halt_toggle_sequence(monkeypatch):
    # In dev (no ADMIN_SECRET), should allow
    r = client.get("/admin/halt")
    assert r.status_code == 200
    assert r.json()["enabled"] in (False, True)

    r = client.post("/admin/halt", json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["enabled"] is True

    r = client.get("/admin/halt")
    assert r.status_code == 200
    assert r.json()["enabled"] is True

    r = client.post("/admin/halt", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_readyz_sqlite_ok(monkeypatch):
    # Ensure DB_URL is sqlite and REDIS_URL is unset
    monkeypatch.setenv("DB_URL", "sqlite:///data/trades.sqlite")
    monkeypatch.delenv("REDIS_URL", raising=False)
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["components"]["db"] is True
    assert body["components"]["redis"] is True
