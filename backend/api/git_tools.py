from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.core.access.auth import verify_api_token
from backend.core.ai.git_tools import (
    GitToolError,
    collect_git_history_stats,
    collect_git_commit_context,
    collect_git_file_diff,
    collect_git_reduction_source_units,
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


class GitSuggestedSplitGroup(BaseModel):
    label: str
    file_count: int = 0
    sample_paths: list[str] = Field(default_factory=list)


class GitPrecheckContextLine(BaseModel):
    line_number: Optional[int] = None
    text: str = ""
    is_match: bool = False


class GitPrecheckIssue(BaseModel):
    issue_type: Literal["ignore_candidate", "sensitive_content", "local_artifact"]
    severity: Literal["warning", "error"]
    blocking: bool = False
    path: str
    line: Optional[int] = None
    message: str
    suggestion: str = ""
    context_lines: list[GitPrecheckContextLine] = Field(default_factory=list)


class GitPrecheckReport(BaseModel):
    checked_file_count: int = 0
    issue_count: int = 0
    warning_count: int = 0
    error_count: int = 0
    blocking_issue_count: int = 0
    has_blocking_issues: bool = False
    issues: list[GitPrecheckIssue] = Field(default_factory=list)


class GitReductionSourceUnit(BaseModel):
    unit_id: str
    path: str
    group: str
    content: str
    truncated: bool = False


class GitToolInspectRequest(BaseModel):
    cwd: str


class GitToolContextRequest(BaseModel):
    cwd: str
    max_files: int = Field(default=8, ge=1, le=20)


class GitToolHistoryStatsRequest(BaseModel):
    cwd: str
    days: int = Field(default=180, ge=0, le=1825)


class GitToolFileDiffRequest(BaseModel):
    cwd: str
    path: str


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
    changed_file_count: int = 0
    estimated_changed_line_count: int = 0
    added_line_count: int = 0
    deleted_line_count: int = 0
    split_recommended: bool = False
    split_reason: str = ""
    oversized: bool = False
    suggested_split_groups: list[GitSuggestedSplitGroup] = Field(default_factory=list)
    precheck: GitPrecheckReport = Field(default_factory=GitPrecheckReport)


class GitToolContextResponse(GitToolInspectResponse):
    prompt_context: str
    selected_paths: list[str] = Field(default_factory=list)
    omitted_path_count: int = 0
    context_truncated: bool = False
    context_mode: Literal["sampled", "overview_only"] = "sampled"


class GitToolHistoryStatsPoint(BaseModel):
    date: str
    added_line_count: int = 0
    deleted_line_count: int = 0
    commit_count: int = 0


class GitToolHistoryStatsResponse(BaseModel):
    cwd: str
    repo_root: str
    branch: str
    days: int
    start_date: str
    end_date: str
    total_added_line_count: int = 0
    total_deleted_line_count: int = 0
    total_commit_count: int = 0
    points: list[GitToolHistoryStatsPoint] = Field(default_factory=list)


class GitToolFileDiffSection(BaseModel):
    kind: Literal["unstaged", "staged", "untracked", "empty"]
    title: str
    content: str = ""
    truncated: bool = False


class GitToolFileDiffResponse(BaseModel):
    cwd: str
    repo_root: str
    branch: str
    path: str
    status: str = ""
    staged: bool = False
    unstaged: bool = False
    untracked: bool = False
    truncated: bool = False
    sections: list[GitToolFileDiffSection] = Field(default_factory=list)


class GitToolReductionInputRequest(BaseModel):
    cwd: str


class GitToolReductionInputResponse(GitToolInspectResponse):
    source_units: list[GitReductionSourceUnit] = Field(default_factory=list)
    source_unit_count: int = 0
    source_unit_truncated_count: int = 0


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


class GitToolGenerateAndCommitRequest(GitToolGenerateMessageRequest):
    add_all: bool = True


class GitToolReduceRequest(BaseModel):
    cwd: str
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    style: Literal["summary", "conventional"] = "summary"
    include_body: bool = True
    branch_factor: int = Field(default=10, ge=2, le=20)


class GitToolReduceAndCommitRequest(GitToolReduceRequest):
    add_all: bool = True


class GitToolReductionPreviewNode(BaseModel):
    node_id: str
    topic: str = ""
    summary: str = ""
    candidate_subject: str = ""
    source_ref_count: int = 0


class GitToolReductionLevel(BaseModel):
    level: int
    input_kind: Literal["source", "summary"]
    chunk_count: int
    node_count: int
    preview_nodes: list[GitToolReductionPreviewNode] = Field(default_factory=list)


class GitToolReductionMeta(BaseModel):
    run_id: str
    profile_id: str
    level_count: int
    source_unit_count: int
    source_unit_truncated_count: int = 0
    node_count: int = 0
    leaf_chunk_count: int = 0
    levels: list[GitToolReductionLevel] = Field(default_factory=list)


class GitToolReduceResponse(BaseModel):
    inspect: GitToolInspectResponse
    subject: str
    body: list[str] = Field(default_factory=list)
    full_message: str
    needs_split: bool = False
    reason: str = ""
    model: str = ""
    raw_content: str = ""
    topic: str = ""
    summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    risk_points: list[str] = Field(default_factory=list)
    reduction: GitToolReductionMeta


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


class GitToolGenerateAndCommitResponse(GitToolGenerateMessageResponse):
    commit: GitToolCommitResponse


class GitToolReduceAndCommitResponse(GitToolReduceResponse):
    commit: GitToolCommitResponse


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


@router.post("/history-stats", response_model=GitToolHistoryStatsResponse)
def collect_git_history_stats_endpoint(req: GitToolHistoryStatsRequest):
    try:
        return collect_git_history_stats(req.cwd, days=req.days)
    except GitToolError as exc:
        _raise_git_error(exc)


@router.post("/file-diff", response_model=GitToolFileDiffResponse)
def collect_git_file_diff_endpoint(req: GitToolFileDiffRequest):
    try:
        return collect_git_file_diff(req.cwd, req.path)
    except GitToolError as exc:
        _raise_git_error(exc)


@router.post("/reduction-input", response_model=GitToolReductionInputResponse)
def collect_git_reduction_input_endpoint(req: GitToolReductionInputRequest):
    try:
        return collect_git_reduction_source_units(req.cwd)
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
