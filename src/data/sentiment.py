from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

import httpx

_CACHE: Dict[str, Any] = {"ts": 0.0, "score": 0.0}


def _normalize_fng(value_0_100: float) -> float:
    v = max(0.0, min(100.0, float(value_0_100)))
    # Map 0..100 -> -1..1 (50 -> 0)
    return (v - 50.0) / 50.0


async def fetch_fear_greed_async(timeout: float = 4.0) -> Optional[float]:
    url = "https://api.alternative.me/fng/"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
            # Expect { data: [ { value: "56", ... } ] }
            arr = data.get("data", []) if isinstance(data, dict) else []
            if arr:
                raw = float(arr[0].get("value", 50))
                return _normalize_fng(raw)
    except Exception:
        return None
    return None


def get_sentiment_score_cached(ttl: int = 300) -> float:
    """Return cached sentiment score in [-1, 1].

    Fetches Fear & Greed only when SENTIMENT_FETCH=1 and cache is stale;
    otherwise returns the last score (default 0.0 at startup).
    """
    now = time.time()
    if (_CACHE.get("ts", 0.0) or 0.0) + float(ttl) > now:
        return float(_CACHE.get("score", 0.0) or 0.0)
    # Only fetch if explicitly enabled; otherwise keep neutral 0.0
    if (os.getenv("SENTIMENT_FETCH") or "").strip().lower() not in {"1", "true", "yes"}:
        _CACHE["ts"] = now
        _CACHE["score"] = float(_CACHE.get("score", 0.0) or 0.0)
        return float(_CACHE["score"])  # neutral / last known
    # Try a quick async fetch using httpx + anyio
    score = None
    try:
        import anyio

        score = anyio.run(fetch_fear_greed_async)
    except Exception:
        score = None
    if score is None:
        score = 0.0
    _CACHE["ts"] = now
    _CACHE["score"] = float(score)
    return float(score)


__all__ = ["get_sentiment_score_cached", "fetch_fear_greed_async"]
