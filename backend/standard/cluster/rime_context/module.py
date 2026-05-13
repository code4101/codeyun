from __future__ import annotations

from fastapi import FastAPI

from backend.api.rime_context_prediction import router as rime_context_prediction_router


def register(app: FastAPI) -> None:
    app.include_router(
        rime_context_prediction_router,
        prefix="/api/rime",
        tags=["rime"],
    )
