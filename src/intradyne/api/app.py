from __future__ import annotations

from fastapi import FastAPI, Depends
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
from intradyne.api.deps import require_api_key
from intradyne.api.models import FrontendConfig


def create_app() -> FastAPI:
    app = FastAPI(title="IntraDyne Lite API")
    # CORS for frontend readiness
    import os as _os

    origins = [o for o in (_os.getenv("FRONTEND_ORIGINS") or "*").split(",") if o]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Optional API auth
    import os as _os

    _auth_required = (_os.getenv("API_AUTH_REQUIRED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    deps = [Depends(require_api_key)] if _auth_required else []

    app.include_router(health_router, dependencies=deps, tags=["Health"])
    app.include_router(orders_router, dependencies=deps, tags=["Orders"])
    app.include_router(risk_router, dependencies=deps, tags=["Risk"])
    app.include_router(admin_router, dependencies=deps, tags=["Admin"])
    app.include_router(ai_router, dependencies=deps, tags=["AI"])
    app.include_router(data_router, dependencies=deps, tags=["Data"])
    app.include_router(ws_router, dependencies=deps, tags=["WebSocket"])
    app.include_router(research_router, dependencies=deps, tags=["Research"])
    return app


app = create_app()


@app.get("/metrics")
def metrics() -> Response:
    data = generate_latest(REGISTRY)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@app.get("/frontend/config", response_model=FrontendConfig, tags=["Frontend"])
def frontend_config() -> FrontendConfig:
    import os as _os

    return FrontendConfig(
        api_base=_os.getenv("API_BASE_URL", ""),
        ws_ticks="/ws/ticks",
        risk_status="/risk/status",
        ledger_tail="/ledger/tail",
        ai_summary="/ai/summarize",
        enable_ai=bool(_os.getenv("OPENAI_API_KEY")),
    )
