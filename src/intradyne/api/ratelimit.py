from __future__ import annotations

import time
from collections import defaultdict, deque
import os
from typing import Deque, Dict, Optional, Tuple

from fastapi import HTTPException, Request, status

from intradyne.core.config import load_settings


# In-memory sliding window counters keyed by (ip, route)
_WINDOWS: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
_REDIS = None  # lazy-initialized async client

# Token-bucket state for WebSocket streaming (in-memory)
_WS_BUCKETS: Dict[Tuple[str, str], Tuple[float, float]] = {}  # (tokens, last_ts)


async def _get_redis(url: Optional[str]):
    global _REDIS
    if _REDIS is not None:
        return _REDIS
    if not url:
        return None
    try:
        import redis.asyncio as redis

        _REDIS = redis.from_url(url)
        return _REDIS
    except Exception:
        return None


async def ai_rate_limit(request: Request) -> None:
    s = load_settings()
    # Prefer AI-specific limits if present, else fall back to global
    try:
        ai_reqs = int(getattr(s, "AI_RATE_LIMIT_REQS", 0) or 0)
        ai_win = int(getattr(s, "AI_RATE_LIMIT_WINDOW", 0) or 0)
    except Exception:  # pragma: no cover - defensive
        ai_reqs, ai_win = 0, 0
    max_reqs = ai_reqs or getattr(s, "RATE_LIMIT_REQS", 60)
    win_s = ai_win or getattr(s, "RATE_LIMIT_WINDOW", 60)

    now = time.time()
    route = request.url.path.split("?")[0]
    # Best-effort client IP detection
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    client_host = request.client.host if request.client else ""
    ip = fwd or client_host or "unknown"
    # Prefer Redis fixed-window counter when REDIS_URL is set; else fallback to in-memory sliding window.
    red = await _get_redis(getattr(s, "REDIS_URL", None))
    if red is not None:
        # Use fixed window key with TTL
        window_key = int(now // float(win_s))
        rkey = f"rl:{route}:{ip}:{window_key}"
        try:
            cur = await red.incr(rkey)
            if cur == 1:
                await red.expire(rkey, int(win_s))
            if int(cur) > int(max_reqs):
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "rate_limited",
                        "window_seconds": int(win_s),
                        "max_requests": int(max_reqs),
                    },
                )
            return
        except HTTPException:
            raise
        except Exception:
            # Fallback on errors
            pass

    key_t = (ip, route)
    q = _WINDOWS[key_t]
    cutoff = now - float(win_s)
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= int(max_reqs):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "rate_limited",
                "window_seconds": int(win_s),
                "max_requests": int(max_reqs),
            },
        )
    q.append(now)


async def ws_rate_limit(websocket, route: str) -> bool:
    """Token-bucket limiter for WebSocket streaming sends.

    Env:
      - WS_BUCKET_RATE: tokens per second (default 50)
      - WS_BUCKET_BURST: max bucket size (default 100)
      - REDIS_URL (optional): when set, use fixed-window counts per send instead.
    Returns True if allowed, False if limited.
    """
    s = load_settings()
    now = time.time()
    try:
        rate = float(os.getenv("WS_BUCKET_RATE", "50"))
        burst = float(os.getenv("WS_BUCKET_BURST", "100"))
    except Exception:
        rate, burst = 50.0, 100.0

    # Identify client
    try:
        ip = websocket.client.host if websocket.client else "unknown"
    except Exception:
        ip = "unknown"

    # Prefer Redis fixed-window when configured (best-effort)
    red = await _get_redis(getattr(s, "REDIS_URL", None))
    if red is not None:
        window = 1.0  # 1-second windows for streaming
        window_key = int(now // window)
        key = f"rlws:{route}:{ip}:{window_key}"
        try:
            cur = await red.incr(key)
            if cur == 1:
                await red.expire(key, 2)
            # Allow up to burst per second under Redis path
            return int(cur) <= int(max(1, burst))
        except Exception:
            pass  # fall back to in-memory bucket

    k = (ip, route)
    tokens, last = _WS_BUCKETS.get(k, (burst, now))
    # Refill
    elapsed = max(0.0, now - last)
    tokens = min(burst, tokens + rate * elapsed)
    if tokens < 1.0:
        _WS_BUCKETS[k] = (tokens, now)
        return False
    _WS_BUCKETS[k] = (tokens - 1.0, now)
    return True


async def general_rate_limit(request: Request) -> None:
    """General-purpose rate limiter for all non-AI routes.

    Uses global RATE_LIMIT_REQS and RATE_LIMIT_WINDOW. Shares Redis/in-memory
    implementation with the AI limiter, but does not consider AI-specific
    overrides.
    """
    s = load_settings()
    try:
        max_reqs = int(getattr(s, "RATE_LIMIT_REQS", 60))
        win_s = int(getattr(s, "RATE_LIMIT_WINDOW", 60))
    except Exception:  # pragma: no cover
        max_reqs, win_s = 60, 60

    now = time.time()
    route = request.url.path.split("?")[0]
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    client_host = request.client.host if request.client else ""
    ip = fwd or client_host or "unknown"

    red = await _get_redis(getattr(s, "REDIS_URL", None))
    if red is not None:
        window_key = int(now // float(win_s))
        rkey = f"rl:{route}:{ip}:{window_key}"
        try:
            cur = await red.incr(rkey)
            if cur == 1:
                await red.expire(rkey, int(win_s))
            if int(cur) > int(max_reqs):
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "rate_limited",
                        "window_seconds": int(win_s),
                        "max_requests": int(max_reqs),
                    },
                )
            return
        except HTTPException:
            raise
        except Exception:
            pass

    key_t = (ip, route)
    q = _WINDOWS[key_t]
    cutoff = now - float(win_s)
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= int(max_reqs):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "rate_limited",
                "window_seconds": int(win_s),
                "max_requests": int(max_reqs),
            },
        )
    q.append(now)
