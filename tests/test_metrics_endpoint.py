from __future__ import annotations

from fastapi.testclient import TestClient

from intradyne.api.app import app


def test_metrics_endpoint_exposes_prometheus_format():
    client = TestClient(app)
    r = client.get("/metrics")
    assert r.status_code == 200
    # Prometheus text exposition starts with comment HELP/TYPE lines
    assert r.text.startswith("#")

