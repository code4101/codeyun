from __future__ import annotations

from fastapi import FastAPI

from backend.api.device_entries import router as device_entries_router


def register(app: FastAPI) -> None:
    app.include_router(
        device_entries_router,
        prefix="/api/device-entries",
        tags=["device-entries"],
    )
