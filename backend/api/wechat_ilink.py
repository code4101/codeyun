from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.core.auth import get_optional_current_user_from_token
from backend.core.feature_access_guard import require_feature_access_dependency
from backend.core.wechat_ilink import (
    WechatIlinkError,
    delete_account,
    get_runtime_status,
    get_updates,
    list_accounts,
    resolve_media_file,
    send_image_message,
    send_text_message,
    start_codex_bridge,
    start_login,
    stop_codex_bridge,
    wait_login,
)
from backend.models import User


router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("tools.ai-wechat"))],
)


class WechatIlinkAccountSummary(BaseModel):
    account_id: str
    user_id: str = ""
    base_url: str
    token_masked: str = ""
    created_at: Optional[float] = None
    updated_at: Optional[float] = None
    last_poll_at: Optional[float] = None
    last_message_at: Optional[float] = None
    has_cursor: bool = False
    context_user_count: int = 0
    codex_bridge: dict[str, Any] = Field(default_factory=dict)


class WechatIlinkStatusResponse(BaseModel):
    base_url: str
    bot_type: str
    channel_version: str
    accounts: list[WechatIlinkAccountSummary] = Field(default_factory=list)
    active_logins: list[dict[str, Any]] = Field(default_factory=list)


class WechatIlinkAccountsResponse(BaseModel):
    items: list[WechatIlinkAccountSummary] = Field(default_factory=list)


class WechatIlinkLoginStartRequest(BaseModel):
    account_id: Optional[str] = None
    force: bool = False


class WechatIlinkLoginStartResponse(BaseModel):
    session_key: str
    qrcode_url: str
    status: str
    message: str


class WechatIlinkLoginWaitRequest(BaseModel):
    session_key: str
    timeout_ms: int = Field(default=35_000, ge=1_000, le=60_000)


class WechatIlinkLoginWaitResponse(BaseModel):
    connected: bool
    status: str
    message: str
    account: Optional[WechatIlinkAccountSummary] = None


class WechatIlinkUpdatesRequest(BaseModel):
    timeout_ms: int = Field(default=35_000, ge=1_000, le=60_000)


class WechatIlinkImageSummary(BaseModel):
    id: str
    mime_type: str = ""
    size: int = 0
    download_url: str = ""
    data_url: str = ""
    download_error: str = ""


class WechatIlinkMessageSummary(BaseModel):
    seq: Optional[int | float | str] = None
    message_id: Optional[int | float | str] = None
    from_user_id: str = ""
    to_user_id: str = ""
    create_time_ms: Optional[int | float] = None
    session_id: str = ""
    message_type: Optional[int] = None
    message_state: Optional[int] = None
    context_token: str = ""
    text: str = ""
    images: list[WechatIlinkImageSummary] = Field(default_factory=list)
    item_types: list[int] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class WechatIlinkUpdatesResponse(BaseModel):
    ret: Optional[int] = 0
    errcode: Optional[int] = None
    errmsg: Optional[str] = None
    messages: list[WechatIlinkMessageSummary] = Field(default_factory=list)
    timed_out: bool = False
    longpolling_timeout_ms: Optional[int] = None


class WechatIlinkSendTextRequest(BaseModel):
    to_user_id: str
    text: str
    context_token: Optional[str] = None
    timeout_ms: int = Field(default=15_000, ge=1_000, le=60_000)


class WechatIlinkSendTextResponse(BaseModel):
    message_id: str
    to_user_id: str
    used_context_token: bool


class WechatIlinkSendImageResponse(BaseModel):
    message_id: str
    to_user_id: str
    used_context_token: bool
    image: WechatIlinkImageSummary


class WechatIlinkCodexBridgeStartRequest(BaseModel):
    model: Optional[str] = None
    command: Optional[str] = None
    system_prompt: Optional[str] = None


class WechatIlinkCodexBridgeResponse(BaseModel):
    account: WechatIlinkAccountSummary


def _map_wechat_error(exc: WechatIlinkError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    )


@router.get("/status", response_model=WechatIlinkStatusResponse)
def get_wechat_ilink_status() -> dict[str, Any]:
    try:
        return get_runtime_status()
    except WechatIlinkError as exc:
        raise _map_wechat_error(exc) from exc


@router.get("/accounts", response_model=WechatIlinkAccountsResponse)
def list_wechat_ilink_accounts() -> dict[str, Any]:
    try:
        return {"items": list_accounts()}
    except WechatIlinkError as exc:
        raise _map_wechat_error(exc) from exc


@router.post("/login/start", response_model=WechatIlinkLoginStartResponse)
def start_wechat_ilink_login(request: WechatIlinkLoginStartRequest) -> dict[str, Any]:
    try:
        return start_login(account_id=request.account_id, force=request.force)
    except WechatIlinkError as exc:
        raise _map_wechat_error(exc) from exc


@router.post("/login/wait", response_model=WechatIlinkLoginWaitResponse)
def wait_wechat_ilink_login(
    request: WechatIlinkLoginWaitRequest,
    current_user: User | None = Depends(get_optional_current_user_from_token),
) -> dict[str, Any]:
    try:
        return wait_login(
            session_key=request.session_key,
            timeout_seconds=request.timeout_ms / 1000,
            owner_user_id=current_user.id if current_user is not None else None,
        )
    except WechatIlinkError as exc:
        raise _map_wechat_error(exc) from exc


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_wechat_ilink_account(account_id: str) -> None:
    try:
        delete_account(account_id)
    except WechatIlinkError as exc:
        raise _map_wechat_error(exc) from exc


@router.post("/accounts/{account_id}/updates", response_model=WechatIlinkUpdatesResponse)
def pull_wechat_ilink_updates(account_id: str, request: WechatIlinkUpdatesRequest) -> dict[str, Any]:
    try:
        return get_updates(account_id, timeout_seconds=request.timeout_ms / 1000)
    except WechatIlinkError as exc:
        raise _map_wechat_error(exc) from exc


@router.post("/accounts/{account_id}/messages", response_model=WechatIlinkSendTextResponse)
def send_wechat_ilink_text(account_id: str, request: WechatIlinkSendTextRequest) -> dict[str, Any]:
    try:
        return send_text_message(
            account_id,
            to_user_id=request.to_user_id,
            text=request.text,
            context_token=request.context_token,
            timeout_seconds=request.timeout_ms / 1000,
        )
    except WechatIlinkError as exc:
        raise _map_wechat_error(exc) from exc


@router.post("/accounts/{account_id}/images", response_model=WechatIlinkSendImageResponse)
async def send_wechat_ilink_image(
    account_id: str,
    to_user_id: str = Form(...),
    image: UploadFile = File(...),
    text: str = Form(""),
    context_token: Optional[str] = Form(None),
    timeout_ms: int = Form(default=15_000, ge=1_000, le=60_000),
) -> dict[str, Any]:
    try:
        image_bytes = await image.read()
        return send_image_message(
            account_id,
            to_user_id=to_user_id,
            image_bytes=image_bytes,
            filename=image.filename or "",
            mime_type=image.content_type or "",
            text=text,
            context_token=context_token,
            timeout_seconds=timeout_ms / 1000,
        )
    except WechatIlinkError as exc:
        raise _map_wechat_error(exc) from exc
    finally:
        await image.close()


@router.get("/media/{media_id}")
def get_wechat_ilink_media(media_id: str) -> FileResponse:
    try:
        path, mime_type = resolve_media_file(media_id)
    except WechatIlinkError as exc:
        raise _map_wechat_error(exc) from exc
    return FileResponse(path, media_type=mime_type)


@router.post("/accounts/{account_id}/codex-bridge/start", response_model=WechatIlinkCodexBridgeResponse)
def start_wechat_ilink_codex_bridge(
    account_id: str,
    request: WechatIlinkCodexBridgeStartRequest,
    current_user: User | None = Depends(get_optional_current_user_from_token),
) -> dict[str, Any]:
    try:
        return {
            "account": start_codex_bridge(
                account_id,
                model=request.model,
                command=request.command,
                system_prompt=request.system_prompt,
                owner_user_id=current_user.id if current_user is not None else None,
            )
        }
    except WechatIlinkError as exc:
        raise _map_wechat_error(exc) from exc


@router.post("/accounts/{account_id}/codex-bridge/stop", response_model=WechatIlinkCodexBridgeResponse)
def stop_wechat_ilink_codex_bridge(account_id: str) -> dict[str, Any]:
    try:
        return {"account": stop_codex_bridge(account_id)}
    except WechatIlinkError as exc:
        raise _map_wechat_error(exc) from exc
