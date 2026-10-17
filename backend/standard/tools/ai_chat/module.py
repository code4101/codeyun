from __future__ import annotations

from fastapi import FastAPI

from backend.api.ai_chat import router as ai_chat_router
from backend.api.ai_privacy import router as ai_privacy_router


def register(app: FastAPI) -> None:
    app.include_router(ai_chat_router, prefix="/api/ai-chat", tags=["ai-chat"])
    app.include_router(ai_privacy_router, prefix="/api/ai-chat/privacy", tags=["ai-privacy"])
