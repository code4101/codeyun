from __future__ import annotations

from fastapi import FastAPI

from backend.api.web_outline import router as web_outline_router


def register(app: FastAPI) -> None:
    app.include_router(web_outline_router, prefix="/api/web-outline", tags=["web-outline"])

