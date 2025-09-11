from __future__ import annotations

import datetime as dt
from typing import Any, Dict

from fastapi import FastAPI, Body

from .. import __version__


app = FastAPI(title="Intradyne API")

_halt_enabled = False


@app.get("/version")
def version() -> Dict[str, Any]:
    return {"version": __version__, "build_time": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"}


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    return {"status": "ok", "version": __version__}


@app.get("/readyz")
def readyz() -> Dict[str, Any]:
    # Always return ready in this lightweight stub
    return {"ready": True, "components": {"db": True, "redis": True}}


@app.get("/admin/halt")
def get_halt() -> Dict[str, Any]:
    return {"enabled": _halt_enabled}


@app.post("/admin/halt")
def set_halt(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    global _halt_enabled
    _halt_enabled = bool(payload.get("enabled", False))
    return {"enabled": _halt_enabled}
