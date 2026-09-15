from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.access.auth import get_optional_current_user_from_token
from backend.core.codex.app_processes import start_codex_app, stop_codex_processes
from backend.core.codex.app_server import CodexAppServerError
from backend.core.codex.weekly_quota import (
    build_codex_general_quota_window,
    collect_codex_quota_snapshot,
    list_codex_weekly_quota_snapshots,
    load_codex_quota_snapshot,
)
from backend.core.codex.official_setup import (
    FLASH_MODE,
    GPT_MODE,
    PRO_MODE,
    PROVIDER_ID,
    SUPPORTED_MODES,
    CodexSetupError,
    read_codex_status,
    read_existing_deepseek_token,
    switch_codex_mode,
)
from backend.core.settings import get_settings
from backend.core.system_ai_resources import SystemAiResourceError, resolve_system_ai_resource
from backend.db import get_session
from backend.models import User


router = APIRouter()

CODEX_SETUP_MODES: tuple[dict[str, str], ...] = (
    {
        "id": FLASH_MODE,
        "label": "DeepSeek Flash",
        "description": "响应更快，支持图片输入。",
    },
    {
        "id": PRO_MODE,
        "label": "DeepSeek V4 Pro",
        "description": "能力最强，只处理文本。",
    },
    {
        "id": GPT_MODE,
        "label": "GPT（Codex 默认）",
        "description": "撤销 DeepSeek 配置，恢复 Codex 官方默认账号。",
    },
)


class CodexSetupModeInfo(BaseModel):
    id: str
    label: str
    description: str


class CodexSetupStatusResponse(BaseModel):
    mode: str
    model: str = ""
    model_provider: str = ""
    codex_home: str
    config_path: str
    config_exists: bool
    models_json_exists: bool
    backup_exists: bool
    deepseek_configured: bool
    deepseek_api_key_present: bool
    deepseek_key_available: bool
    modes: list[CodexSetupModeInfo] = Field(default_factory=list)


class CodexQuotaWindow(BaseModel):
    label: str = ""
    remaining_percent: int
    reset_at: str = ""


class CodexQuotaGroup(BaseModel):
    id: str
    name: str
    windows: list[CodexQuotaWindow] = Field(default_factory=list)


class CodexQuotaPoint(BaseModel):
    at: str
    remaining_percent: int


class CodexQuotaWindowHistory(BaseModel):
    name: str = ""
    window_start: str = ""
    window_end: str = ""
    reset_at: str = ""
    remaining_percent: Optional[int] = None
    points: list[CodexQuotaPoint] = Field(default_factory=list)


class CodexQuotaResponse(BaseModel):
    groups: list[CodexQuotaGroup] = Field(default_factory=list)
    general_window: Optional[CodexQuotaWindowHistory] = None
    observed_at: str = ""
    error: str = ""


class CodexSetupSwitchRequest(BaseModel):
    mode: Literal["deepseek-flash", "deepseek-v4-pro", "gpt"]
    api_key: Optional[str] = None


class CodexSetupSwitchResponse(BaseModel):
    ok: bool
    mode: str
    changed: bool
    message: str
    output: str = ""
    closed_process_count: int = 0
    restarted_app: bool = False
    status: CodexSetupStatusResponse


def _ensure_codex_setup_access(current_user: Optional[User]) -> None:
    settings = get_settings()
    if not settings.is_production:
        return
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="重置 Codex 仅对登录用户开放",
        )
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="重置 Codex 仅对管理员开放",
        )


def _resolve_deepseek_key(session: Session) -> str:
    try:
        runtime = resolve_system_ai_resource(session=session, provider_id=PROVIDER_ID)
        key = str(runtime.get("api_key") or "").strip()
    except SystemAiResourceError:
        key = ""
    if key:
        return key
    return read_existing_deepseek_token()


def _build_status(session: Session, raw: dict[str, Any] | None = None) -> CodexSetupStatusResponse:
    payload = dict(raw if raw is not None else read_codex_status())
    payload["deepseek_key_available"] = bool(_resolve_deepseek_key(session))
    payload["modes"] = [CodexSetupModeInfo(**item) for item in CODEX_SETUP_MODES]
    return CodexSetupStatusResponse(**payload)


def _switch_message(
    mode: str,
    *,
    closed_count: int = 0,
    app_restarted: bool = False,
    app_was_running: bool = False,
) -> str:
    if mode == FLASH_MODE:
        base = "已切换到 DeepSeek Flash"
    elif mode == PRO_MODE:
        base = "已切换到 DeepSeek V4 Pro"
    else:
        base = "已还原为 GPT 默认配置"

    parts = [base]
    if closed_count:
        parts.append(f"已关闭 {closed_count} 个 Codex 进程")
    if app_was_running:
        parts.append("已重新打开 ChatGPT 客户端" if app_restarted else "请手动重新打开 ChatGPT 客户端")
    return "；".join(parts)


@router.get("/status", response_model=CodexSetupStatusResponse)
def get_codex_setup_status(
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_codex_setup_access(current_user)
    return _build_status(session)


def _quota_response(groups: list[dict[str, Any]], observed_at: str) -> CodexQuotaResponse:
    window = build_codex_general_quota_window(groups, list_codex_weekly_quota_snapshots())
    return CodexQuotaResponse(
        groups=[CodexQuotaGroup(**item) for item in groups],
        general_window=CodexQuotaWindowHistory(**window),
        observed_at=observed_at,
    )


@router.get("/quota", response_model=CodexQuotaResponse)
def get_codex_quota(
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
):
    _ensure_codex_setup_access(current_user)
    snapshot = load_codex_quota_snapshot()
    groups = snapshot["groups"]
    if not groups:
        return CodexQuotaResponse(error="尚未采集额度，点击刷新")
    return _quota_response(groups, snapshot["observed_at"])


@router.post("/quota/refresh", response_model=CodexQuotaResponse)
def refresh_codex_quota(
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
):
    _ensure_codex_setup_access(current_user)
    try:
        snapshot = collect_codex_quota_snapshot()
    except (CodexAppServerError, OSError) as exc:
        return CodexQuotaResponse(error=str(exc))
    return _quota_response(snapshot["groups"], snapshot["observed_at"])


@router.post("/switch", response_model=CodexSetupSwitchResponse)
def switch_codex_setup(
    payload: CodexSetupSwitchRequest,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_codex_setup_access(current_user)

    mode = str(payload.mode or "").strip().lower()
    if mode not in SUPPORTED_MODES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的切换目标")

    before = read_codex_status()
    if mode == GPT_MODE:
        if not before["backup_exists"] and not before["deepseek_configured"]:
            return CodexSetupSwitchResponse(
                ok=True,
                mode=mode,
                changed=False,
                message="当前已经是 GPT 默认配置",
                output="",
                status=_build_status(session),
            )
        if not before["backup_exists"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="未找到官方脚本备份（backup-deepseek），无法还原默认配置",
            )

    api_key = str(payload.api_key or "").strip() or _resolve_deepseek_key(session)
    if mode != GPT_MODE and not api_key and not before["deepseek_api_key_present"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未找到可用的 DeepSeek API Key，无法写入 Codex 配置",
        )

    snapshot: dict[str, Any] = {"was_app_running": False, "app_exe": "", "stopped": []}
    try:
        snapshot = stop_codex_processes()
        result = switch_codex_mode(mode, api_key=api_key)
    except CodexSetupError as exc:
        start_codex_app(snapshot)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception:
        start_codex_app(snapshot)
        raise

    restarted = start_codex_app(snapshot)
    closed_count = len(snapshot.get("stopped") or [])
    after = result.get("status") if isinstance(result, dict) else None
    new_status = _build_status(session, after if isinstance(after, dict) else None)
    return CodexSetupSwitchResponse(
        ok=True,
        mode=mode,
        changed=new_status.mode != before["mode"],
        message=_switch_message(mode, closed_count=closed_count, app_restarted=restarted, app_was_running=bool(snapshot.get("was_app_running"))),
        output=str(result.get("output") or ""),
        closed_process_count=closed_count,
        restarted_app=restarted,
        status=new_status,
    )
