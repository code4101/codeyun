from __future__ import annotations

from fastapi import FastAPI

from backend.api.evomind import router as evomind_router


def register(app: FastAPI) -> None:
    app.include_router(evomind_router, prefix="/api/evomind", tags=["evomind"])
