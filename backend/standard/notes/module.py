from __future__ import annotations

from fastapi import FastAPI

from backend.api.notes import router as notes_router
from backend.api.note_docs import router as note_docs_router
from backend.api.note_sheets import router as note_sheets_router
from backend.api.pdf_documents import router as pdf_documents_router
from backend.api.eastmoney import router as eastmoney_router
from backend.api.freebill import router as freebill_router
from backend.api.wechat_archive import router as wechat_archive_router


def register(app: FastAPI) -> None:
    app.include_router(notes_router, prefix="/api/notes", tags=["notes"])
    app.include_router(note_docs_router, prefix="/api/note-docs", tags=["note-docs"])
    app.include_router(note_sheets_router, prefix="/api/note-sheets", tags=["note-sheets"])
    app.include_router(pdf_documents_router, prefix="/api/pdf-documents", tags=["pdf-documents"])
    app.include_router(eastmoney_router, prefix="/api/eastmoney", tags=["eastmoney"])
    app.include_router(freebill_router, prefix="/api/freebill", tags=["freebill"])
    app.include_router(wechat_archive_router, prefix="/api/wechat-archive", tags=["wechat-archive"])
