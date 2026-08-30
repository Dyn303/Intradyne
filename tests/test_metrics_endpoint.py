from __future__ import annotations

from fastapi.testclient import TestClient

from intradyne.api.app import app


client = TestClient(app)


def test_metrics_endpoint_exposes_prometheus_format():
    r = client.get("/metrics")
    assert r.status_code == 200
    # Prometheus text exposition starts with comment HELP/TYPE lines
    assert r.text.startswith("#")


def test_guardrail_breach_counter_is_exposed():
    """The Grafana alert rules key off this series, so it must be scrapable."""
    r = client.get("/metrics")
    assert "intradyne_guardrail_breaches_total" in r.text


def test_risk_metrics_json_moved_off_the_scrape_path():
    """Regression: this handler used to sit on bare /metrics and shadow the
    Prometheus endpoint, because routers are registered before it."""
    r = client.get("/risk/metrics")
    assert r.status_code == 200
    body = r.json()
    assert "counts" in body and "breaches_24h" in body["counts"]
