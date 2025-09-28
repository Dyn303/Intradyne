from __future__ import annotations

from typing import Any, Dict, Callable, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .metrics import METRICS


def create_app(
    state_provider: Callable[[], Dict[str, Any]],
    apply_profile: Optional[Callable[[], Dict[str, Any]]] = None,
    revert_profile: Optional[Callable[[], Dict[str, Any]]] = None,
) -> FastAPI:
    app = FastAPI(title="Intradyne-Lite")
    # CORS for frontend consumption
    import os

    origins = [o for o in (os.getenv("FRONTEND_ORIGINS") or "*").split(",") if o]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

    @app.get("/frontend/config")
    def frontend_config() -> Dict[str, Any]:
        return {
            "state": "/state",
            "apply_profile": "/profile/apply",
            "revert_profile": "/profile/revert",
        }

    if apply_profile is not None:

        @app.post("/profile/apply")
        def profile_apply() -> Dict[str, Any]:
            try:
                return apply_profile()
            except Exception as e:  # noqa: BLE001
                return {"error": str(e)}

    if revert_profile is not None:

        @app.post("/profile/revert")
        def profile_revert() -> Dict[str, Any]:
            try:
                return revert_profile()
            except Exception as e:  # noqa: BLE001
                return {"error": str(e)}

    return app
