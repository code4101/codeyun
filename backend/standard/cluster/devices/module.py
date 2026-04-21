from __future__ import annotations

from fastapi import FastAPI

from backend.api.device import router as device_router


def register(app: FastAPI) -> None:
    app.include_router(device_router, prefix="/api/devices", tags=["devices"])
