from __future__ import annotations

from fastapi import FastAPI

from backend.api.admin import tasks_router as admin_tasks_router


def register(app: FastAPI) -> None:
    app.include_router(admin_tasks_router, prefix="/api/admin", tags=["admin"])
