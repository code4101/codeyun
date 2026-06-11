from __future__ import annotations

from fastapi import FastAPI

from backend.api.music_tools import router as music_tools_router


def register(app: FastAPI) -> None:
    app.include_router(music_tools_router, prefix="/api/music-tools", tags=["music-tools"])
