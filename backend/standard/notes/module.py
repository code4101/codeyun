from __future__ import annotations

from fastapi import FastAPI

from backend.api.notes import router as notes_router
from backend.api.note_sheets import router as note_sheets_router
from backend.api.pdf_documents import router as pdf_documents_router


def register(app: FastAPI) -> None:
    app.include_router(notes_router, prefix="/api/notes", tags=["notes"])
    app.include_router(note_sheets_router, prefix="/api/note-sheets", tags=["note-sheets"])
    app.include_router(pdf_documents_router, prefix="/api/pdf-documents", tags=["pdf-documents"])
