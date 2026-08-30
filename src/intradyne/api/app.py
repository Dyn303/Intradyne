from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
import os as _os
from fastapi.middleware.cors import CORSMiddleware
from intradyne.api.health import router as health_router
from intradyne.api.routes.orders import router as orders_router
from intradyne.api.routes.risk import router as risk_router
from intradyne.api.routes.admin import router as admin_router
from intradyne.api.routes.ai import router as ai_router
from intradyne.api.routes.data import router as data_router
from intradyne.api.routes.ws import router as ws_router
from intradyne.api.routes.research import router as research_router
from fastapi import Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from intradyne.api.deps import (
    api_auth_required,
    configured_api_key,
    is_prod,
    require_api_key,
)
from intradyne.api.deps import get_execution_manager
from intradyne.api.models import FrontendConfig
from intradyne.api.ratelimit import general_rate_limit
from intradyne.core.config import assert_live_trading_gate, load_settings
from intradyne.core.logging import setup_logging


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Own the trading loop for the lifetime of the service.

    Replaces the deprecated on_event("startup") hook, and gives the engine a
    supervised task with a clean shutdown path. The loop shares the process
    ExecutionManager, so orders it raises pass the same Tier 1 gate, move the
    same portfolio and land in the same ledger as API-submitted ones.
    """
    setup_logging(_os.getenv("LOG_LEVEL"))
    settings = load_settings()

    task: "asyncio.Task | None" = None
    if settings.engine_enabled:
        from intradyne.engine.loop import supervise

        task = asyncio.create_task(
            supervise(settings, get_execution_manager()), name="intradyne-engine"
        )
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def create_app() -> FastAPI:
    # Refuse to start with live trading armed before phase 5 (MIGRATION.md).
    assert_live_trading_gate(load_settings())

    app = FastAPI(title="IntraDyne Lite API", lifespan=_lifespan)
    # CORS for frontend readiness.
    #
    # `allow_origins=["*"]` together with `allow_credentials=True` is not a
    # valid combination: the CORS spec forbids a wildcard on a credentialed
    # request, so browsers reject every such response. Starlette resolves it by
    # silently echoing the caller's Origin back, which turns the wildcard into
    # "trust every site" rather than the intended "public read-only API".
    origins = [o.strip() for o in (_os.getenv("FRONTEND_ORIGINS") or "*").split(",")]
    origins = [o for o in origins if o]
    _wildcard = "*" in origins
    if _wildcard and is_prod():
        raise RuntimeError(
            "FRONTEND_ORIGINS is '*' in production. Set it to an explicit "
            "comma-separated list of allowed origins."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        # Credentials are only meaningful against an explicit origin list.
        allow_credentials=not _wildcard,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # API auth: default-on in production, else env-driven (see deps.py).
    _auth_required = api_auth_required()
    if _auth_required and not configured_api_key():
        # Fail closed at boot rather than serving unauthenticated traffic or
        # 503-ing every request once deployed.
        raise RuntimeError(
            "API auth is required (APP_ENV=prod or API_AUTH_REQUIRED=1) but "
            "X_API_KEY is not set. Set X_API_KEY, or explicitly set "
            "API_AUTH_REQUIRED=0 to run without authentication."
        )
    deps_auth = [Depends(require_api_key)] if _auth_required else []
    deps_common = deps_auth + [Depends(general_rate_limit)]

    # Apply general rate limit to HTTP routers; WS router excluded
    app.include_router(health_router, dependencies=deps_common, tags=["Health"])
    app.include_router(orders_router, dependencies=deps_common, tags=["Orders"])
    app.include_router(risk_router, dependencies=deps_common, tags=["Risk"])
    app.include_router(admin_router, dependencies=deps_common, tags=["Admin"])
    app.include_router(ai_router, dependencies=deps_common, tags=["AI"])
    app.include_router(data_router, dependencies=deps_common, tags=["Data"])
    app.include_router(ws_router, tags=["WebSocket"])
    app.include_router(research_router, dependencies=deps_common, tags=["Research"])

    return app


app = create_app()


@app.get("/metrics")
def metrics() -> Response:
    data = generate_latest(REGISTRY)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@app.get("/frontend/config", response_model=FrontendConfig, tags=["Frontend"])
def frontend_config() -> FrontendConfig:
    return FrontendConfig(
        api_base=_os.getenv("API_BASE_URL", ""),
        ws_ticks="/ws/ticks",
        risk_status="/risk/status",
        ledger_tail="/ledger/tail",
        ai_summary="/ai/summarize",
        enable_ai=bool(_os.getenv("OPENAI_API_KEY")),
    )
