from __future__ import annotations

from fastapi import FastAPI

from backend.api.admin import images_router as admin_images_router


def register(app: FastAPI) -> None:
    app.include_router(admin_images_router, prefix="/api/admin", tags=["admin"])
