from __future__ import annotations

from fastapi.testclient import TestClient

from intradyne.api.app import app


client = TestClient(app)


def test_data_ohlc_local_file():
    r = client.get("/data/ohlc", params={"symbol": "ETH/USDT", "tf": "1d"})
    assert r.status_code in (200, 404)
    # If sample dataset exists, basic structure must match
    if r.status_code == 200:
        body = r.json()
        assert body["symbol"] == "ETH/USDT"
        assert body["tf"] == "1d"
        assert isinstance(body.get("data"), list)


def test_data_price_usdt_shortcut():
    r = client.get("/data/price", params={"symbols": "USDT"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("USDT") == 1.0


def test_ws_ticks_mock_stream():
    with client.websocket_connect(
        "/ws/ticks?symbols=BTC/USDT,ETH/USDT&mock=1&interval=0.1"
    ) as ws:
        msg = ws.receive_json()
        assert "ticks" in msg
        assert isinstance(msg["ticks"], list)
        assert len(msg["ticks"]) >= 1
