from __future__ import annotations

import threading
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.access.auth import get_optional_current_user_from_token
from backend.core.codex.app_processes import start_codex_app, stop_codex_processes
from backend.core.codex.app_server import CodexAppServerError
from backend.core.codex.official_setup import (
    GPT_MODE,
    PROVIDER_ID,
    CodexSetupError,
    read_codex_status,
    read_existing_deepseek_token,
    resolve_codex_home,
)
from backend.core.codex.switch import (
    PROVIDERS as CODEX_SETUP_PROVIDERS,
    OPENAI_PROVIDER_ID,
    CodexSwitchError,
    detect_provider,
    restore_codeyun_backup,
    switch_codex,
)
from backend.core.codex.weekly_quota import (
    build_codex_general_quota_window,
    collect_codex_quota_snapshot,
    list_codex_weekly_quota_snapshots,
    load_codex_quota_snapshot,
)
from backend.core.opencode_usage import read_opencode_go_usage
from backend.core.settings import get_settings
from backend.core.system_ai_resources import SystemAiResourceError, resolve_system_ai_resource
from backend.db import get_session
from backend.models import User


router = APIRouter()

DEEPSEEK_PROVIDER = "deepseek"


class CodexProviderModel(BaseModel):
    id: str
    label: str


class CodexProviderInfo(BaseModel):
    id: str
    label: str
    models: list[CodexProviderModel] = Field(default_factory=list)


class CodexSetupStatusResponse(BaseModel):
    provider: str = OPENAI_PROVIDER_ID
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
    providers: list[CodexProviderInfo] = Field(default_factory=list)


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


class OpenCodeUsageWindow(BaseModel):
    label: str
    remaining_percent: int
    reset_at: str = ""
    status: str = ""


class OpenCodeUsageResponse(BaseModel):
    available: bool = False
    windows: list[OpenCodeUsageWindow] = Field(default_factory=list)
    error: str = ""


class CodexSetupSwitchRequest(BaseModel):
    provider: str
    model: Optional[str] = None
    api_key: Optional[str] = None


class CodexSetupSwitchResponse(BaseModel):
    ok: bool
    provider: str
    model: str = ""
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
    payload["provider"] = detect_provider(str(payload.get("model_provider") or ""))
    payload["deepseek_key_available"] = bool(_resolve_deepseek_key(session))
    payload["providers"] = [CodexProviderInfo(**item) for item in CODEX_SETUP_PROVIDERS]
    return CodexSetupStatusResponse(**payload)


def _switch_message(
    provider: str,
    model: str,
    *,
    closed_count: int = 0,
    app_restarted: bool = False,
    app_was_running: bool = False,
) -> str:
    if provider == OPENAI_PROVIDER_ID:
        base = "已还原为 OpenAI 默认配置"
    elif provider == DEEPSEEK_PROVIDER:
        base = f"已切换到 DeepSeek · {model}"
    else:
        base = f"已切换到 opencode Go · {model}"

    parts = [base]
    if closed_count:
        parts.append(f"已关闭 {closed_count} 个 Codex 进程")
    if app_was_running:
        parts.append("已重新打开 ChatGPT 客户端" if app_restarted else "请手动重新打开 ChatGPT 客户端")
    return "；".join(parts)


_PROVIDER_MAP = {item["id"]: item for item in CODEX_SETUP_PROVIDERS}


@router.get("/status", response_model=CodexSetupStatusResponse)
def get_codex_setup_status(
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_codex_setup_access(current_user)
    return _build_status(session)


_quota_bootstrap_lock = threading.Lock()
_quota_bootstrap_last_attempt = 0.0
_QUOTA_BOOTSTRAP_COOLDOWN_SECONDS = 300.0


def _bootstrap_quota_snapshot() -> dict[str, Any] | None:
    """Collect once when nothing has ever been persisted, then back off on failures."""

    global _quota_bootstrap_last_attempt
    now = time.monotonic()
    with _quota_bootstrap_lock:
        if now - _quota_bootstrap_last_attempt < _QUOTA_BOOTSTRAP_COOLDOWN_SECONDS:
            return None
        _quota_bootstrap_last_attempt = now
    try:
        return collect_codex_quota_snapshot()
    except (CodexAppServerError, OSError):
        return None


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
    if not snapshot["groups"]:
        bootstrapped = _bootstrap_quota_snapshot()
        if bootstrapped:
            snapshot = bootstrapped
    groups = snapshot["groups"]
    window = build_codex_general_quota_window(groups, list_codex_weekly_quota_snapshots())
    points = window.get("points") or []
    observed_at = snapshot["observed_at"] or (points[-1]["at"] if points else "")
    if not groups and not points:
        return CodexQuotaResponse(error="尚未采集额度，点击刷新")
    return CodexQuotaResponse(
        groups=[CodexQuotaGroup(**item) for item in groups],
        general_window=CodexQuotaWindowHistory(**window),
        observed_at=observed_at,
    )


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


@router.get("/opencode-usage", response_model=OpenCodeUsageResponse)
def get_opencode_usage(
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
):
    _ensure_codex_setup_access(current_user)
    return OpenCodeUsageResponse(**read_opencode_go_usage())


@router.post("/switch", response_model=CodexSetupSwitchResponse)
def switch_codex_setup(
    payload: CodexSetupSwitchRequest,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_codex_setup_access(current_user)

    provider = str(payload.provider or "").strip().lower()
    provider_info = _PROVIDER_MAP.get(provider)
    if provider_info is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="未知的供应商")

    model = str(payload.model or "").strip()
    allowed_models = {item["id"] for item in provider_info.get("models", [])}
    if provider != OPENAI_PROVIDER_ID:
        if not model:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请选择模型")
        if model not in allowed_models:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{provider_info['label']} 不支持该模型",
            )

    before = read_codex_status()
    before_provider = detect_provider(str(before.get("model_provider") or ""))
    before_model = str(before.get("model") or "")

    if provider == OPENAI_PROVIDER_ID and before_provider == OPENAI_PROVIDER_ID and not before["deepseek_configured"]:
        return CodexSetupSwitchResponse(
            ok=True,
            provider=provider,
            model=before_model,
            changed=False,
            message="当前已经是 OpenAI 默认配置",
            status=_build_status(session),
        )

    api_key = ""
    if provider == DEEPSEEK_PROVIDER:
        api_key = str(payload.api_key or "").strip() or _resolve_deepseek_key(session)
        if not api_key and not before["deepseek_api_key_present"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="未找到可用的 DeepSeek API Key，无法写入 Codex 配置",
            )

    codex_home = resolve_codex_home()
    snapshot: dict[str, Any] = {"was_app_running": False, "app_exe": "", "stopped": []}
    try:
        snapshot = stop_codex_processes()
        if provider == OPENAI_PROVIDER_ID:
            if before["backup_exists"]:
                result = switch_codex(OPENAI_PROVIDER_ID)
            elif restore_codeyun_backup(codex_home):
                result = {"mode": GPT_MODE, "output": "", "status": read_codex_status()}
            else:
                raise CodexSwitchError("未找到备份，无法还原 OpenAI 默认配置")
        elif provider == DEEPSEEK_PROVIDER:
            result = switch_codex(DEEPSEEK_PROVIDER, model, api_key=api_key)
        else:
            result = switch_codex(provider, model)
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
        provider=provider,
        model=model or new_status.model,
        changed=new_status.provider != before_provider or new_status.model != before_model,
        message=_switch_message(
            provider,
            model or new_status.model,
            closed_count=closed_count,
            app_restarted=restarted,
            app_was_running=bool(snapshot.get("was_app_running")),
        ),
        output=str(result.get("output") or ""),
        closed_process_count=closed_count,
        restarted_app=restarted,
        status=new_status,
    )
