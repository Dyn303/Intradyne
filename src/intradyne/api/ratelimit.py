from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

from fastapi import HTTPException, Request, status

from intradyne.core.config import load_settings


# In-memory sliding window counters keyed by (ip, route)
_WINDOWS: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
_REDIS = None  # lazy-initialized async client


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
