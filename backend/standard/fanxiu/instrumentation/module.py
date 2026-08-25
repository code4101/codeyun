from __future__ import annotations

from fastapi import FastAPI

from backend.api.fanxiu_instrumentation import router


def register(app: FastAPI) -> None:
    app.include_router(
        router,
        prefix="/api/fanxiu",
        tags=["fanxiu-instrumentation"],
    )
