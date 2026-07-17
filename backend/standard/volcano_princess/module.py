from __future__ import annotations

from fastapi import FastAPI

from backend.api.volcano_princess import router as volcano_princess_router


def register(app: FastAPI) -> None:
    app.include_router(
        volcano_princess_router,
        prefix="/api/volcano-princess",
        tags=["volcano-princess"],
    )

