from __future__ import annotations

from fastapi import FastAPI

from backend.api.notes import router as notes_router


def register(app: FastAPI) -> None:
    app.include_router(notes_router, prefix="/api/notes", tags=["notes"])
