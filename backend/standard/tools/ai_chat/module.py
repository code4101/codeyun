from __future__ import annotations

from fastapi import FastAPI

from backend.api.ai_chat import router as ai_chat_router


def register(app: FastAPI) -> None:
    app.include_router(ai_chat_router, prefix="/api/ai-chat", tags=["ai-chat"])
