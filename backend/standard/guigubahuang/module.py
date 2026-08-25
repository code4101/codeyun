from __future__ import annotations

from fastapi import FastAPI

from backend.api.guigubahuang import router as guigubahuang_router


def register(app: FastAPI) -> None:
    app.include_router(
        guigubahuang_router,
        prefix="/api/guigubahuang",
        tags=["guigubahuang"],
    )
