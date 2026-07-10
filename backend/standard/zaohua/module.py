from __future__ import annotations

from fastapi import FastAPI

from backend.api.zaohua import router as zaohua_router


def register(app: FastAPI) -> None:
    app.include_router(zaohua_router, prefix="/api/zaohua", tags=["zaohua"])
