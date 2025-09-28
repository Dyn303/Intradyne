from __future__ import annotations
from fastapi.testclient import TestClient

from intradyne.api.app import app


client = TestClient(app)


def test_ai_status_reports_configured_false(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = client.get("/ai/status")
    assert r.status_code == 200
    assert r.json().get("configured") is False


def test_ai_summarize_returns_503_when_not_configured(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = client.post("/ai/summarize")
    assert r.status_code == 503
    assert "not configured" in r.json().get("detail", "").lower()


def test_ai_rate_limit(monkeypatch):
    # Tighten AI rate limits and verify 429 after threshold
    monkeypatch.setenv("AI_RATE_LIMIT_REQS", "2")
    monkeypatch.setenv("AI_RATE_LIMIT_WINDOW", "60")
    rs = [client.get("/ai/status") for _ in range(4)]
    # At least one request should be rate-limited under tight limits
    assert any(r.status_code == 429 for r in rs)
    body = next((r.json() for r in rs if r.status_code == 429), {})
    assert body.get("detail", {}).get("error") == "rate_limited"


def test_ai_explain_works_without_ai(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    payload = {"symbol": "BTC/USDT", "side": "buy", "qty": 1.0}
    r = client.post("/ai/explain", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] in {"allow", "halt", "pause", "block"}
    assert isinstance(body.get("reasons"), list)
