from __future__ import annotations

from fastapi import FastAPI

from backend.api.device_agent import router as device_agent_router


def register(app: FastAPI) -> None:
    app.include_router(
        device_agent_router,
        prefix="/api/device-agent",
        tags=["device-agent"],
    )
