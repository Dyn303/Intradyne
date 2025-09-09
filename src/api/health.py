from __future__ import annotations

import os
from fastapi import APIRouter

router = APIRouter()


def _read_version() -> str:
    try:
        with open("VERSION", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "dev"


@router.get("/healthz")
def healthz():
    return {"status": "ok", "version": _read_version(), "build": os.getenv("BUILD_ID", "local")}


