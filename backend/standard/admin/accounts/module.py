from __future__ import annotations

from fastapi import FastAPI

from backend.api.admin import accounts_router as admin_accounts_router


def register(app: FastAPI) -> None:
    app.include_router(admin_accounts_router, prefix="/api/admin", tags=["admin"])
