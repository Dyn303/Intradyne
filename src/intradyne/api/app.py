from __future__ import annotations

from fastapi import FastAPI
from intradyne.api.health import router as health_router
from intradyne.api.routes.orders import router as orders_router
from intradyne.api.routes.risk import router as risk_router
from intradyne.api.routes.admin import router as admin_router


def create_app() -> FastAPI:
    app = FastAPI(title="IntraDyne Lite API")
    app.include_router(health_router)
    app.include_router(orders_router)
    app.include_router(risk_router)
    app.include_router(admin_router)
    return app


app = create_app()


