from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI

from .metrics import METRICS


def create_app(state_provider) -> FastAPI:
    app = FastAPI(title="Intradyne-Lite")

    @app.get("/readyz")
    def readyz() -> Dict[str, Any]:
        return {"ready": True}

    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        return {"ok": True}

    @app.get("/metrics")
    def metrics() -> str:
        return METRICS.as_prometheus()

    @app.get("/state")
    def state() -> Dict[str, Any]:
        st = state_provider()
        return st

    return app

