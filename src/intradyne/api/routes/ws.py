from __future__ import annotations

import asyncio
import math
import time
from typing import Dict, List

import httpx
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from intradyne.api.deps import get_ledger


router = APIRouter()


async def _fetch_prices(
    client: httpx.AsyncClient, symbols: List[str]
) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for s in symbols:
        if s.upper() == "USDT":
            out[s] = 1.0
            continue
        mapped = s.replace("/", "")
        try:
            r = await client.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": mapped},
            )
            r.raise_for_status()
            data = r.json()
            out[s] = float(data["price"])
        except Exception:
            out[s] = out.get(s, 0.0)
        await asyncio.sleep(0)
    return out


@router.websocket("/ws/ticks")
async def ws_ticks(
    websocket: WebSocket,
    symbols: str = Query("BTC/USDT,ETH/USDT"),
    interval: float = Query(1.0, ge=0.1, le=10.0),
    mock: int = Query(0, description="Use synthetic prices when 1 (for tests)"),
) -> None:
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    await websocket.accept()
    try:
        if mock:
            # Synthetic sine-wave ticks for testing
            i = 0
            while True:
                now = time.time()
                payload = []
                for idx, s in enumerate(syms):
                    base = 100.0 + 10.0 * idx
                    px = base + 2.0 * math.sin((i + idx) / 5.0)
                    payload.append({"ts": now, "symbol": s, "last": round(px, 4)})
                await websocket.send_json({"ticks": payload})
                i += 1
                await asyncio.sleep(max(0.05, float(interval)))
        else:
            backoff = 1.0
            async with httpx.AsyncClient(timeout=5.0) as client:
                while True:
                    try:
                        now = time.time()
                        prices = await _fetch_prices(client, syms)
                        payload = [
                            {"ts": now, "symbol": s, "last": prices.get(s, 0.0)}
                            for s in syms
                        ]
                        await websocket.send_json({"ticks": payload})
                        backoff = 1.0  # reset after success
                    except Exception as e:
                        try:
                            await websocket.send_json({"error": str(e)})
                        except Exception:
                            pass
                        # exponential backoff, capped at 30s
                        backoff = min(backoff * 2.0, 30.0)
                    await asyncio.sleep(max(float(interval), backoff))
    except WebSocketDisconnect:
        return
    except Exception as e:  # noqa: BLE001
        try:
            await websocket.send_json({"error": str(e)})
        except Exception:
            pass
        await websocket.close()


@router.websocket("/ws/ledger")
async def ws_ledger(
    websocket: WebSocket,
    tail: int = Query(10, ge=0, le=1000),
    follow: int = Query(1, ge=0, le=1),
    mock: int = Query(0, ge=0, le=1),
    interval: float = Query(1.0, ge=0.1, le=10.0),
) -> None:
    """Stream explainability ledger records as they arrive.

    - tail: number of recent lines to emit initially
    - follow: when 1, continue streaming new lines
    - mock: when 1, stream synthetic events for testing
    - interval: poll interval when following a file
    """
    await websocket.accept()
    try:
        if mock:
            i = 0
            while True:
                now = time.time()
                payload = {
                    "ts": now,
                    "event": "guardrail_breach" if i % 2 == 0 else "info",
                    "type": "dd_warn" if i % 2 == 0 else "heartbeat",
                }
                await websocket.send_json({"record": payload})
                i += 1
                await asyncio.sleep(float(interval))
        else:
            # Tail + follow file
            led = get_ledger()
            path = led.path
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except FileNotFoundError:
                lines = []
            import orjson

            # Send tail records
            for line in lines[-int(tail) :]:
                line = line.strip()
                if not line:
                    continue
                try:
                    await websocket.send_json({"record": orjson.loads(line)})
                except Exception:
                    continue

            if not follow:
                await websocket.close()
                return

            # Follow by polling size and reading new lines
            pos = 0
            try:
                with open(path, "r", encoding="utf-8") as f:
                    f.seek(0, 2)
                    pos = f.tell()
            except FileNotFoundError:
                pos = 0

            while True:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        f.seek(pos)
                        for line in f:
                            if not line.strip():
                                continue
                            try:
                                await websocket.send_json(
                                    {"record": orjson.loads(line)}
                                )
                            except Exception:
                                continue
                        pos = f.tell()
                except FileNotFoundError:
                    # File might be rotated; wait and retry
                    pos = 0
                await asyncio.sleep(float(interval))
    except WebSocketDisconnect:
        return
    except Exception as e:  # noqa: BLE001
        try:
            await websocket.send_json({"error": str(e)})
        except Exception:
            pass
        await websocket.close()
