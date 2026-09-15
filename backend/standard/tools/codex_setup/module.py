from __future__ import annotations

from fastapi import FastAPI

from backend.api.codex_setup import router as codex_setup_router


def register(app: FastAPI) -> None:
    app.include_router(codex_setup_router, prefix="/api/codex-setup", tags=["codex-setup"])
