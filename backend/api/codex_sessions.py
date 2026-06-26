from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session

from backend.core.ai.chat import OllamaClientError
from backend.core.access.auth import verify_api_token
from backend.core.codex.sessions import (
    build_codex_daily_summary,
    build_codex_overview,
    build_codex_thread_detail,
    build_codex_thread_message_images,
    build_codex_workload,
    get_codex_daily_summary_latest_run,
    get_codex_daily_summary_run,
    start_codex_daily_summary_run,
)
from backend.core.devices.device import BaseDevice
from backend.db import get_session


router = APIRouter()


class CodexThreadSummary(BaseModel):
    id: str
    title: str
    preview: str | None = None
    cwd: str | None = None
    original_cwd: str | None = None
    rollout_path: str | None = None
    created_at: float | int | None = None
    updated_at: float | int | None = None
    archived: bool = False
    project_label: str
    project_secondary_label: str | None = None
    workspace_root: str | None = None


class CodexProjectGroup(BaseModel):
    key: str
    label: str
    secondary_label: str | None = None
    cwd: str | None = None
    workspace_root: str | None = None
    thread_count: int
    archived_thread_count: int = 0
    latest_updated_at: float | int | None = None
    threads: list[CodexThreadSummary]


class CodexOverviewResponse(BaseModel):
    root_dir: str
    default_root_dir: str
    state_db_path: str
    session_index_path: str
    global_state_path: str
    total_groups: int
    total_threads: int
    archived_threads: int
    groups: list[CodexProjectGroup]
    thread_offset: int = 0
    thread_limit: int | None = None
    returned_threads: int = 0
    has_more: bool = False


class CodexThreadMessage(BaseModel):
    seq: int
    timestamp: str | None = None
    role: Literal["user", "assistant"]
    phase: str | None = None
    text: str


class CodexThreadDetailSummary(CodexThreadSummary):
    group_key: str
    group_label: str
    group_secondary_label: str | None = None


class CodexThreadDetailResponse(BaseModel):
    root_dir: str
    thread: CodexThreadDetailSummary
    message_count: int
    user_message_count: int
    assistant_message_count: int
    messages: list[CodexThreadMessage]


class CodexThreadMessageImage(BaseModel):
    index: int
    type: str
    image_url: str


class CodexThreadMessageImagesResponse(BaseModel):
    root_dir: str
    thread_id: str
    message_seq: int
    images: list[CodexThreadMessageImage]


class CodexWorkloadTurn(BaseModel):
    id: str
    thread_id: str | None = None
    turn_index: int | None = None
    thread_title: str | None = None
    project_label: str | None = None
    project_secondary_label: str | None = None
    workspace_root: str | None = None
    group_key: str | None = None
    group_label: str | None = None
    user_seq: int | None = None
    assistant_seq: int | None = None
    start_at: float
    end_at: float
    duration_seconds: float
    completed: bool = False
    preview: str | None = None


class CodexWorkloadSegment(BaseModel):
    start_at: float
    end_at: float
    duration_seconds: float
    concurrency: int


class CodexWorkloadResponse(BaseModel):
    root_dir: str
    total_threads: int
    total_turns: int
    returned_turns: int = 0
    summarized_turns: int = 0
    skipped_threads: int = 0
    max_concurrency: int = 0
    time_range_start: float | None = None
    time_range_end: float | None = None
    day_seconds: dict[str, float] = {}
    turns: list[CodexWorkloadTurn]
    segments: list[CodexWorkloadSegment]


class CodexDailySummaryGenerateRequest(BaseModel):
    date: str
    root_dir: str | None = None
    model: str | None = None


class CodexDailySummaryThread(BaseModel):
    thread_id: str
    title: str
    project_label: str
    project_secondary_label: str | None = None
    workspace_root: str | None = None
    source_entry_id: str | None = None
    source_device_name: str | None = None
    source_root_dir: str | None = None
    start_at: float
    end_at: float
    turn_count: int
    user_message_count: int
    assistant_message_count: int
    preview: str | None = None


class CodexDailySummaryTypeItem(BaseModel):
    key: str
    label: str
    color: str | None = None
    order: int
    builtin: bool = False


class CodexDailySummaryResponse(BaseModel):
    root_dir: str
    date: str
    timezone: str
    generated_at: str | None = None
    generated_by: Literal["deepseek", "codex_cli", "empty"]
    model: str | None = None
    prompt_version: str
    summary_text: str
    thread_count: int
    turn_count: int
    user_message_count: int
    assistant_message_count: int
    threads: list[CodexDailySummaryThread]
    type_items: list[CodexDailySummaryTypeItem] = []


class CodexDailySummaryRunRequest(CodexDailySummaryGenerateRequest):
    force: bool = False


class CodexDailySummaryRunRead(BaseModel):
    id: str
    root_dir: str
    date: str
    timezone: str
    provider: str
    generated_by: str
    model: str | None = None
    prompt_version: str
    force_requested: bool = False
    reused_existing_run: bool = False
    status: str
    stage: str
    stage_label: str
    thread_count: int = 0
    turn_count: int = 0
    user_message_count: int = 0
    assistant_message_count: int = 0
    summary_text: str = ""
    error_message: str | None = None
    heartbeat_at: float | None = None
    result: CodexDailySummaryResponse | None = None
    created_at: float
    finished_at: float | None = None
    updated_at: float


def _translate_codex_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, NotADirectoryError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (ValueError, OllamaClientError)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=f"读取 Codex 会话失败：{exc}")


@router.get("/overview", response_model=CodexOverviewResponse)
def get_codex_overview(
    root_dir: str | None = Query(default=None),
    thread_offset: int = Query(default=0, ge=0),
    thread_limit: int | None = Query(default=None, ge=1, le=2000),
    session: Session = Depends(get_session),
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        return build_codex_overview(
            root_dir,
            session=session,
            thread_offset=thread_offset,
            thread_limit=thread_limit,
        )
    except Exception as exc:  # pragma: no cover - translated for HTTP callers
        raise _translate_codex_error(exc) from exc


@router.get("/threads/{thread_id}", response_model=CodexThreadDetailResponse)
def get_codex_thread_detail(
    thread_id: str,
    root_dir: str | None = Query(default=None),
    session: Session = Depends(get_session),
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        return build_codex_thread_detail(root_dir, thread_id, session=session)
    except Exception as exc:  # pragma: no cover - translated for HTTP callers
        raise _translate_codex_error(exc) from exc


@router.get("/threads/{thread_id}/messages/{message_seq}/images", response_model=CodexThreadMessageImagesResponse)
def get_codex_thread_message_images(
    thread_id: str,
    message_seq: int,
    root_dir: str | None = Query(default=None),
    session: Session = Depends(get_session),
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        return build_codex_thread_message_images(root_dir, thread_id, message_seq, session=session)
    except Exception as exc:  # pragma: no cover - translated for HTTP callers
        raise _translate_codex_error(exc) from exc


@router.get("/workload", response_model=CodexWorkloadResponse)
def get_codex_workload(
    root_dir: str | None = Query(default=None),
    start_at: float | None = Query(default=None),
    end_at: float | None = Query(default=None),
    compact: bool = Query(default=False),
    include_segments: bool = Query(default=True),
    historical_day_summary_before: float | None = Query(default=None),
    session: Session = Depends(get_session),
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        return build_codex_workload(
            root_dir,
            session=session,
            start_at=start_at,
            end_at=end_at,
            compact=compact,
            include_segments=include_segments,
            historical_day_summary_before=historical_day_summary_before,
        )
    except Exception as exc:  # pragma: no cover - translated for HTTP callers
        raise _translate_codex_error(exc) from exc


@router.post("/daily-summary/generate", response_model=CodexDailySummaryResponse)
def generate_codex_daily_summary(
    payload: CodexDailySummaryGenerateRequest,
    session: Session = Depends(get_session),
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        return build_codex_daily_summary(
            payload.root_dir,
            payload.date,
            model=payload.model,
            session=session,
        )
    except Exception as exc:  # pragma: no cover - translated for HTTP callers
        raise _translate_codex_error(exc) from exc


@router.get("/daily-summary/latest", response_model=CodexDailySummaryRunRead)
def get_latest_codex_daily_summary(
    date: str,
    root_dir: str | None = Query(default=None),
    session: Session = Depends(get_session),
    device: BaseDevice = Depends(verify_api_token),
):
    try:
        return get_codex_daily_summary_latest_run(
            f"device:{device.device_id}",
            root_dir,
            date,
            session=session,
        )
    except Exception as exc:  # pragma: no cover - translated for HTTP callers
        raise _translate_codex_error(exc) from exc


@router.post("/daily-summary/runs", response_model=CodexDailySummaryRunRead)
def create_codex_daily_summary_run(
    payload: CodexDailySummaryRunRequest,
    session: Session = Depends(get_session),
    device: BaseDevice = Depends(verify_api_token),
):
    try:
        return start_codex_daily_summary_run(
            f"device:{device.device_id}",
            payload.root_dir,
            payload.date,
            model=payload.model,
            force=payload.force,
            session=session,
        )
    except Exception as exc:  # pragma: no cover - translated for HTTP callers
        raise _translate_codex_error(exc) from exc


@router.get("/daily-summary/runs/{run_id}", response_model=CodexDailySummaryRunRead)
def get_codex_daily_summary_run_state(
    run_id: str,
    session: Session = Depends(get_session),
    device: BaseDevice = Depends(verify_api_token),
):
    try:
        return get_codex_daily_summary_run(
            f"device:{device.device_id}",
            run_id,
            session=session,
        )
    except Exception as exc:  # pragma: no cover - translated for HTTP callers
        raise _translate_codex_error(exc) from exc
