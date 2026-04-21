from __future__ import annotations

from fastapi import FastAPI

from backend.api.device_control import router as device_control_router


def register(app: FastAPI) -> None:
    app.include_router(
        device_control_router,
        prefix="/api/device-control",
        tags=["device-control"],
    )
