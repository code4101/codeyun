from __future__ import annotations

from fastapi import FastAPI

from backend.api.hardware_temperature import router as hardware_temperature_router


def register(app: FastAPI) -> None:
    app.include_router(
        hardware_temperature_router,
        prefix="/api/hardware-temperatures",
        tags=["hardware-temperatures"],
    )
