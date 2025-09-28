from __future__ import annotations

import os
from datetime import datetime
import sqlite3
from urllib.parse import urlparse
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

router = APIRouter()

# Source of truth for runtime version
VERSION = "v1.9.0-final"
BUILD_TIME = os.getenv("BUILD_TIME") or datetime.utcnow().isoformat() + "Z"


@router.get("/version")
def version():
    return {"version": VERSION, "build_time": BUILD_TIME}


@router.get("/healthz")
def healthz():
    return {"status": "ok", "version": VERSION, "build": os.getenv("BUILD_ID", "local")}


@router.get("/readyz")
def readyz():
    from intradyne.core.config import load_settings

    s = load_settings()
    db_ok = False
    redis_ok = False
    # DB check (sqlite only)
    try:
        if s.DB_URL.startswith("sqlite"):
            # parse path
            path = s.DB_URL.split("sqlite:///")[-1]
            import os as _os

            _os.makedirs(_os.path.dirname(path) or ".", exist_ok=True)
            conn = sqlite3.connect(path)
            conn.execute("SELECT 1")
            conn.close()
            db_ok = True
        else:
            db_ok = True  # skip for non-sqlite in this minimal build
    except Exception:
        db_ok = False
    # Redis check (TCP ping if URL given)
    try:
        if s.REDIS_URL:
            u = urlparse(s.REDIS_URL)
            import socket

            with socket.create_connection(
                (u.hostname or "localhost", int(u.port or 6379)), timeout=0.2
            ):
                pass
            redis_ok = True
        else:
            redis_ok = True
    except Exception:
        redis_ok = False
    ready = db_ok and redis_ok
    return JSONResponse(
        status_code=status.HTTP_200_OK
        if ready
        else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"ready": ready, "components": {"db": db_ok, "redis": redis_ok}},
    )


@router.get("/ops/test_connectivity")
def test_connectivity(hosts: str | None = None, timeout: float = 3.0):
    """
    Lightweight connectivity probe. Checks internal services (postgres, redis)
    and external HTTPS endpoints. `hosts` is a comma-separated list of hostnames
    to test over TLS:443; defaults to common exchange APIs.
    """
    import socket
    import ssl
    import time

    def _dns(h: str):
        try:
            return {"ok": True, "ip": socket.gethostbyname(h)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def _tcp(h: str, p: int):
        t0 = time.time()
        try:
            with socket.create_connection((h, p), timeout=timeout):
                pass
            return {"ok": True, "ms": int((time.time() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def _tls(h: str, p: int = 443):
        t0 = time.time()
        try:
            s = socket.create_connection((h, p), timeout=timeout)
            ctx = ssl.create_default_context()
            ss = ctx.wrap_socket(s, server_hostname=h)
            # Minimal request to elicit a response
            ss.send(
                b"HEAD / HTTP/1.1\r\nHost: "
                + h.encode()
                + b"\r\nConnection: close\r\n\r\n"
            )
            line = (ss.recv(120) or b"").decode("latin1", "ignore").splitlines()[:1]
            ss.close()
            return {
                "ok": True,
                "ms": int((time.time() - t0) * 1000),
                "status": (line[0] if line else ""),
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # Internal service checks (Docker network names)
    internal = {
        "postgres": {
            "dns": _dns("postgres"),
            "tcp": _tcp("postgres", 5432),
        },
        "redis": {
            "dns": _dns("redis"),
            "tcp": _tcp("redis", 6379),
        },
    }

    # External hosts list
    default_hosts = [
        "api.kraken.com",
        "api.coinbase.com",
        "api.binance.com",
        "example.com",
    ]
    host_list = [
        h.strip() for h in (hosts.split(",") if hosts else default_hosts) if h.strip()
    ]

    external = {}
    for h in host_list:
        external[h] = {"dns": _dns(h)}
        if external[h]["dns"]["ok"]:
            external[h]["tls_443"] = _tls(h, 443)

    return {"internal": internal, "external": external}
