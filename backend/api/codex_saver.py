from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.codex_saver import (
    CodexSaverError,
    doctor_codex_saver,
    execute_codex_saver_task,
    get_codex_saver_config,
    get_codex_saver_logs,
    get_codex_saver_mcp_bearer_config,
    get_codex_saver_runtime_status,
    preview_codex_saver_route,
    save_codex_saver_config,
)
from backend.core.auth import (
    extract_api_token,
    get_optional_current_user_from_token,
    validate_api_token_value,
)
from backend.core.feature_access_guard import ensure_feature_access, require_feature_access_dependency
from backend.db import get_session
from backend.models import User


router = APIRouter()
require_codex_saver_admin_access = require_feature_access_dependency("tools.ai-codex-saver")


class CodexSaverConfigRequest(BaseModel):
    provider_id: str = "deepseek"
    model: str = ""
    flash_model: str = "deepseek-v4-flash"
    pro_model: str = "deepseek-v4-pro"
    use_flash_gate: bool = True
    default_decision: str = "deepseek"
    multimodal_decision: str = "deny"
    auto_apply: bool = True
    write_boundary_mode: str = "none"
    allowed_write_roots: list[str] = Field(default_factory=list)
    log_file_name: str = ".codexsaver.log"
    log_backup_file_name: str = ".codexsaver.log.backup"
    log_max_bytes: int = 1024 * 1024
    require_verification_success: bool = False
    rules: list[dict[str, Any]] = Field(default_factory=list)


class CodexSaverRoutePreviewRequest(BaseModel):
    task: str
    cwd: str = ""
    context: str = ""
    files: list[str] = Field(default_factory=list)
    input_kinds: list[str] = Field(default_factory=lambda: ["text"])
    verification_commands: list[str] = Field(default_factory=list)
    allow_auto_apply: bool | None = None


class CodexSaverLogsRequest(BaseModel):
    cwd: str = ""
    max_bytes: int = 200_000


class CodexSaverMcpBearerRequest(BaseModel):
    reveal: bool = False


def _raise_bad_request(exc: Exception) -> None:
    raise HTTPException(status_code=400, detail=str(exc)) from exc


async def require_codex_saver_execute_access(
    authorization: str | None = Header(None),
    x_device_token: str | None = Header(None),
    token: str | None = Query(None),
    sec_websocket_protocol: str | None = Header(None),
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
) -> User | object | None:
    try:
        return ensure_feature_access(
            session,
            feature_key="tools.ai-codex-saver",
            current_user=current_user,
        )
    except HTTPException as feature_exc:
        final_token = extract_api_token(
            authorization=authorization,
            x_device_token=x_device_token,
            token=token,
            sec_websocket_protocol=sec_websocket_protocol,
        )
        if final_token:
            return validate_api_token_value(final_token)
        if feature_exc.status_code == status.HTTP_403_FORBIDDEN:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authentication token",
            ) from feature_exc
        raise


@router.get("/config")
def read_codex_saver_config(
    session: Session = Depends(get_session),
    _: User | None = Depends(require_codex_saver_admin_access),
) -> dict[str, Any]:
    try:
        return get_codex_saver_config(session)
    except CodexSaverError as exc:
        _raise_bad_request(exc)


@router.put("/config")
def update_codex_saver_config(
    payload: CodexSaverConfigRequest,
    session: Session = Depends(get_session),
    _: User | None = Depends(require_codex_saver_admin_access),
) -> dict[str, Any]:
    try:
        return save_codex_saver_config(session, payload.model_dump())
    except CodexSaverError as exc:
        _raise_bad_request(exc)


@router.post("/mcp-bearer")
def read_mcp_bearer_config(
    payload: CodexSaverMcpBearerRequest | None = None,
    _: User | None = Depends(require_codex_saver_admin_access),
) -> dict[str, Any]:
    body = payload or CodexSaverMcpBearerRequest()
    return get_codex_saver_mcp_bearer_config(reveal=body.reveal)


@router.post("/route-preview")
def preview_route(
    payload: CodexSaverRoutePreviewRequest,
    session: Session = Depends(get_session),
    _: object = Depends(require_codex_saver_execute_access),
) -> dict[str, Any]:
    try:
        return preview_codex_saver_route(session, payload.model_dump())
    except CodexSaverError as exc:
        _raise_bad_request(exc)


@router.post("/execute")
def execute_task(
    payload: CodexSaverRoutePreviewRequest,
    session: Session = Depends(get_session),
    _: object = Depends(require_codex_saver_execute_access),
) -> dict[str, Any]:
    try:
        return execute_codex_saver_task(session, payload.model_dump())
    except CodexSaverError as exc:
        _raise_bad_request(exc)


@router.get("/logs")
def read_logs(
    cwd: str = "",
    max_bytes: int = 200_000,
    session: Session = Depends(get_session),
    _: User | None = Depends(require_codex_saver_admin_access),
) -> dict[str, Any]:
    try:
        return get_codex_saver_logs(session, cwd=cwd, max_bytes=max_bytes)
    except CodexSaverError as exc:
        _raise_bad_request(exc)


@router.get("/runtime")
def read_runtime_status(
    _: User | None = Depends(require_codex_saver_admin_access),
) -> dict[str, Any]:
    return get_codex_saver_runtime_status()


@router.post("/doctor")
def run_doctor(
    payload: CodexSaverLogsRequest | None = None,
    session: Session = Depends(get_session),
    _: User | None = Depends(require_codex_saver_admin_access),
) -> dict[str, Any]:
    try:
        body = payload or CodexSaverLogsRequest()
        return doctor_codex_saver(session, cwd=body.cwd)
    except CodexSaverError as exc:
        _raise_bad_request(exc)
