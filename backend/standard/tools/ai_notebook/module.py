from __future__ import annotations

from fastapi import FastAPI

from backend.api.ai_notebook_lab import router as ai_notebook_router


def register(app: FastAPI) -> None:
    app.include_router(ai_notebook_router, prefix="/api/ai-notebook", tags=["ai-notebook"])
