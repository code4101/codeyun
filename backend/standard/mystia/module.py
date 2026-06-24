from __future__ import annotations

from fastapi import FastAPI

from backend.api.mystia import router as mystia_router


def register(app: FastAPI) -> None:
    app.include_router(mystia_router, prefix="/api/mystia", tags=["mystia"])
