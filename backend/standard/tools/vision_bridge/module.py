from __future__ import annotations

from fastapi import FastAPI

from backend.api.vision_bridge import router as vision_bridge_router


def register(app: FastAPI) -> None:
    app.include_router(vision_bridge_router, prefix="/api/vision-bridge", tags=["vision-bridge"])
