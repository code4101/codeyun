from __future__ import annotations

from fastapi import FastAPI

from backend.api.attendance import public_router as attendance_public_router, router as attendance_router


def register(app: FastAPI) -> None:
    app.include_router(attendance_public_router, prefix="/api/attendance", tags=["attendance"])
    app.include_router(attendance_router, prefix="/api/attendance", tags=["attendance"])
