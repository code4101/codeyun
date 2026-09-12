from __future__ import annotations

from fastapi import FastAPI

from backend.api.fanxiu import service_router as fanxiu_service_router
from backend.api.fanxiu import status_router as fanxiu_status_router
from backend.api.fanxiu_remote import router as fanxiu_remote_router


def register(app: FastAPI) -> None:
    app.include_router(fanxiu_remote_router, prefix="/api/fanxiu/remote")
    app.include_router(fanxiu_status_router, prefix="/api/fanxiu", tags=["fanxiu"])
    app.include_router(fanxiu_service_router, prefix="/api/fanxiu", tags=["fanxiu"])
