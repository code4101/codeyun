from __future__ import annotations

from fastapi import FastAPI

from backend.api.fanxiu_resources import router as fanxiu_resources_router


def register(app: FastAPI) -> None:
    app.include_router(fanxiu_resources_router, prefix="/api/fanxiu", tags=["fanxiu"])
