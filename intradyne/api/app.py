from __future__ import annotations

import datetime as dt
from typing import Any, Dict
import os as _os

from fastapi import Body, Depends, FastAPI, Response
from fastapi.responses import ORJSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from intradyne.api.deps import require_api_key
from intradyne.api.ratelimit import general_rate_limit
from intradyne.core.logging import setup_logging

from .. import __version__

# Routers
from .routes import orders as orders_routes  # type: ignore
from .routes import risk as risk_routes  # type: ignore
from .routes import admin as admin_routes  # type: ignore
from .routes import ai as ai_routes  # type: ignore
from .routes import data as data_routes  # type: ignore
from .routes import ws as ws_routes  # type: ignore
from .routes import research as research_routes  # type: ignore


app = FastAPI(title="Intradyne API", default_response_class=ORJSONResponse)

_halt_enabled = False


@app.get("/version")
async def version() -> Dict[str, Any]:
    return {
        "version": __version__,
        "build_time": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {"status": "ok", "version": __version__}


@app.get("/readyz")
async def readyz() -> Dict[str, Any]:
    # Always return ready in this lightweight stub
    return {"ready": True, "components": {"db": True, "redis": True}}


@app.get("/admin/halt")
async def get_halt() -> Dict[str, Any]:
    return {"enabled": _halt_enabled}


@app.post("/admin/halt")
async def set_halt(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    global _halt_enabled
    _halt_enabled = bool(payload.get("enabled", False))
    return {"enabled": _halt_enabled}


@app.get("/metrics")
async def metrics() -> Response:
    data = generate_latest(REGISTRY)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


# Logging setup and dependencies
@app.on_event("startup")
def _startup_logging() -> None:
    setup_logging(_os.getenv("LOG_LEVEL"))


# API auth: default-on in production, else env-driven
_env = (
    _os.getenv("APP_ENV") or _os.getenv("ENV") or _os.getenv("ENVIRONMENT") or ""
).lower()
_auth_cfg = (_os.getenv("API_AUTH_REQUIRED") or "").strip().lower()
_auth_required = (
    True
    if _env in {"prod", "production"} and not _auth_cfg
    else _auth_cfg in {"1", "true", "yes"}
)
deps_auth = [Depends(require_api_key)] if _auth_required else []
deps_common = deps_auth + [Depends(general_rate_limit)]


# Mount sub-routers with dependencies
app.include_router(orders_routes.router, dependencies=deps_common)
app.include_router(risk_routes.router, dependencies=deps_common)
app.include_router(admin_routes.router, dependencies=deps_common)
app.include_router(ai_routes.router, dependencies=deps_common)
app.include_router(data_routes.router, dependencies=deps_common)
# WebSocket routes excluded from HTTP rate limiter dependencies
app.include_router(ws_routes.router)
app.include_router(research_routes.router, dependencies=deps_common)
