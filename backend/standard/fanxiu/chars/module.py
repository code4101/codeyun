from __future__ import annotations

from fastapi import FastAPI

from backend.api.fanxiu import chars_router as fanxiu_chars_router


def register(app: FastAPI) -> None:
    app.include_router(fanxiu_chars_router, prefix="/api/fanxiu", tags=["fanxiu"])
