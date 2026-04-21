from __future__ import annotations

from fastapi import FastAPI

from backend.api.fanxiu import status_router as fanxiu_status_router


def register(app: FastAPI) -> None:
    app.include_router(fanxiu_status_router, prefix="/api/fanxiu", tags=["fanxiu"])
