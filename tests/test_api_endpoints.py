from __future__ import annotations

from fastapi.testclient import TestClient

from pathlib import Path

from intradyne.api.app import app


client = TestClient(app)

# The repo keeps a VERSION file and health.py keeps a constant. Read the file
# so this test fails on drift instead of pinning a third copy of the string.
VERSION_FILE = (
    (Path(__file__).resolve().parent.parent / "VERSION")
    .read_text(encoding="utf-8")
    .strip()
)


def test_version_endpoint():
    r = client.get("/version")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == VERSION_FILE
    assert isinstance(body.get("build_time"), str)
    assert body["build_time"].endswith("Z")


def test_healthz_includes_version():
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == VERSION_FILE
