from __future__ import annotations

from fastapi import FastAPI

from backend.api.reduction_documents import router as reduction_documents_router


def register(app: FastAPI) -> None:
    app.include_router(
        reduction_documents_router,
        prefix="/api/reduction-documents",
        tags=["reduction-documents"],
    )
