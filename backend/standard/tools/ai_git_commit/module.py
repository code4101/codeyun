from __future__ import annotations

from fastapi import FastAPI

from backend.api.git_tools import router as git_tools_router


def register(app: FastAPI) -> None:
    app.include_router(
        git_tools_router,
        prefix="/api/git-tools",
        tags=["git-tools"],
    )
