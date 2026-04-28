from __future__ import annotations

from fastapi import FastAPI

from backend.api.notes import router as notes_router
from backend.api.note_sheets import router as note_sheets_router


def register(app: FastAPI) -> None:
    app.include_router(notes_router, prefix="/api/notes", tags=["notes"])
    app.include_router(note_sheets_router, prefix="/api/note-sheets", tags=["note-sheets"])
