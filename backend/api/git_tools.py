from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.core.auth import verify_api_token
from backend.core.git_tools import (
    GitToolError,
    collect_git_commit_context,
    create_git_commit,
    inspect_git_repository,
)


router = APIRouter(dependencies=[Depends(verify_api_token)])


class GitChangedFile(BaseModel):
    path: str
    status: str
    staged: bool = False
    unstaged: bool = False
    untracked: bool = False


class GitToolInspectRequest(BaseModel):
    cwd: str


class GitToolContextRequest(BaseModel):
    cwd: str
    max_files: int = Field(default=8, ge=1, le=20)


class GitToolInspectResponse(BaseModel):
    cwd: str
    repo_root: str
    branch: str
    branch_status: str = ""
    clean: bool
    status_lines: list[str] = Field(default_factory=list)
    diff_stat: str = ""
    staged_diff_stat: str = ""
    changed_files: list[GitChangedFile] = Field(default_factory=list)


class GitToolContextResponse(GitToolInspectResponse):
    prompt_context: str
    selected_paths: list[str] = Field(default_factory=list)
    omitted_path_count: int = 0
    context_truncated: bool = False


class GitToolGenerateMessageRequest(BaseModel):
    cwd: str
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    style: Literal["summary", "conventional"] = "summary"
    include_body: bool = True
    max_files: int = Field(default=8, ge=1, le=20)


class GitToolGenerateMessageResponse(BaseModel):
    inspect: GitToolInspectResponse
    subject: str
    body: list[str] = Field(default_factory=list)
    full_message: str
    needs_split: bool = False
    reason: str = ""
    model: str = ""
    raw_content: str = ""


class GitToolCommitRequest(BaseModel):
    cwd: str
    subject: str
    body: list[str] = Field(default_factory=list)
    add_all: bool = True


class GitToolCommitResponse(BaseModel):
    cwd: str
    repo_root: str
    branch: str
    commit_hash: str
    short_hash: str
    summary: str
    full_message: str
    clean: bool
    status_lines: list[str] = Field(default_factory=list)


def _raise_git_error(exc: GitToolError) -> None:
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/inspect", response_model=GitToolInspectResponse)
def inspect_git_repository_endpoint(req: GitToolInspectRequest):
    try:
        return inspect_git_repository(req.cwd)
    except GitToolError as exc:
        _raise_git_error(exc)


@router.post("/context", response_model=GitToolContextResponse)
def collect_git_commit_context_endpoint(req: GitToolContextRequest):
    try:
        return collect_git_commit_context(req.cwd, max_files=req.max_files)
    except GitToolError as exc:
        _raise_git_error(exc)


@router.post("/commit", response_model=GitToolCommitResponse)
def create_git_commit_endpoint(req: GitToolCommitRequest):
    try:
        return create_git_commit(
            req.cwd,
            subject=req.subject,
            body=req.body,
            add_all=req.add_all,
        )
    except GitToolError as exc:
        _raise_git_error(exc)
