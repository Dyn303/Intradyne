from __future__ import annotations

from fastapi.testclient import TestClient

from intradyne.api.app import app


client = TestClient(app)


def test_version_endpoint():
    r = client.get("/version")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == "v1.9.0-final"
    assert isinstance(body.get("build_time"), str)
    assert body["build_time"].endswith("Z")


def test_healthz_includes_version():
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == "v1.9.0-final"
