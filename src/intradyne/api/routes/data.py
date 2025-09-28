from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, HTTPException, Query


router = APIRouter()


def _map_symbol(sym: str) -> str:
    return sym.replace("/", "_")


def _ohlc_root() -> Path:
    # Prefer `deploy/data/ohlc`, then `data/ohlc` relative to repo root
    for p in (Path("deploy/data/ohlc"), Path("data/ohlc")):
        if p.exists():
            return p
    return Path("data/ohlc")


@router.get("/data/ohlc")
async def get_ohlc(symbol: str, tf: str = "1d") -> Dict[str, Any]:
    filename = f"{_map_symbol(symbol)}_{tf}.json"
    path = _ohlc_root() / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"dataset_not_found: {filename}")
    try:
        content = path.read_text(encoding="utf-8")
        # pass-through content as JSON
        import orjson

        data = orjson.loads(content)
        return {"symbol": symbol, "tf": tf, "data": data}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"dataset_read_error: {e}")


@router.get("/data/price")
async def get_price(symbols: str = Query("BTC/USDT,ETH/USDT")) -> Dict[str, float]:
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    out: Dict[str, float] = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for s in syms:
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
                # soft-fail with 0.0 when unreachable
                out[s] = out.get(s, 0.0)
            await asyncio.sleep(0)  # yield
    return out


@router.post("/data/fetch_ohlc")
async def fetch_ohlc(
    symbol: str,
    tf: str = "1d",
    limit: int = 90,
) -> Dict[str, Any]:
    """Fetch OHLC from Binance and store under data/ohlc. For demo use only.

    Timeframe map: supports 1d, 1h, 15m.
    """
    tf_map = {"1d": "1d", "1h": "1h", "15m": "15m"}
    if tf not in tf_map:
        raise HTTPException(status_code=400, detail="unsupported_timeframe")
    mapped = symbol.replace("/", "")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(
                "https://api.binance.com/api/v3/klines",
                params={
                    "symbol": mapped,
                    "interval": tf_map[tf],
                    "limit": max(1, min(1000, limit)),
                },
            )
            r.raise_for_status()
            rows = r.json()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"fetch_failed: {e}")
    root = _ohlc_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_map_symbol(symbol)}_{tf}.json"
    # Store minimal OHLCV
    ohlc = [
        [
            row[0],
            float(row[1]),
            float(row[2]),
            float(row[3]),
            float(row[4]),
            float(row[5]),
        ]
        for row in rows
    ]
    import orjson

    path.write_text(orjson.dumps(ohlc).decode("utf-8"), encoding="utf-8")
    return {"status": "ok", "file": str(path), "rows": len(ohlc)}


@router.get("/data/ohlc_list")
async def list_ohlc() -> List[Dict[str, Any]]:
    root = _ohlc_root()
    out: List[Dict[str, Any]] = []
    if not root.exists():
        return out
    for p in sorted(root.glob("*_*.json")):
        name = p.stem  # e.g., BTC_USDT_1d
        parts = name.split("_")
        if len(parts) >= 3:
            symbol = f"{parts[0]}/{parts[1]}"
            tf = parts[2]
        else:
            symbol, tf = name, ""
        try:
            stat = p.stat()
            out.append(
                {
                    "file": str(p),
                    "symbol": symbol,
                    "tf": tf,
                    "bytes": stat.st_size,
                    "mtime": int(stat.st_mtime),
                }
            )
        except Exception:
            continue
    return out
