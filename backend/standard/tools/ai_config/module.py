from __future__ import annotations

from fastapi import FastAPI

from backend.api.ai_git_repos import router as ai_git_repos_router


def register(app: FastAPI) -> None:
    app.include_router(ai_git_repos_router, prefix="/api/ai-git-repos", tags=["ai-git-repos"])
