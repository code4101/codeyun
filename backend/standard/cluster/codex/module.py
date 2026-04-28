from __future__ import annotations

from fastapi import FastAPI

from backend.api.codex_sessions import router as codex_router


def register(app: FastAPI) -> None:
    app.include_router(codex_router, prefix="/api/codex", tags=["codex"])
