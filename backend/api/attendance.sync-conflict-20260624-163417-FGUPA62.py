from __future__ import annotations

import threading
import time
from typing import Any, Literal, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.attendance_service import (
    AttendanceServiceError,
    decrypt_attendance_secret,
    encrypt_attendance_secret,
    ensure_can_manage_attendance_service,
    ensure_can_use_attendance_service,
    get_attendance_account_or_404,
    get_attendance_run_or_404,
    get_attendance_template_or_404,
    get_current_account,
    get_current_execution_device,
    get_or_create_attendance_service_config,
    get_user_device_or_404,
    list_attendance_accounts,
    list_attendance_templates,
    serialize_attendance_account,
    serialize_attendance_run,
    serialize_attendance_template,
    serialize_user_device,
)
from backend.core.attendance_wjx import WjxAutomationError, execute_wjx_template_action
from backend.core.auth import get_current_user_from_token
from backend.core.device import get_device_id
from backend.db import get_session
from backend.models import AttendanceAccountAsset, AttendanceRun, AttendanceTemplateAsset, User, UserDevice

router = APIRouter()

FIXED_WJX_TEMPLATE_ID = "wjx-course-catalog"
FIXED_WJX_TEMPLATE_NAME = "课程清单问卷"
FIXED_WJX_TEMPLATE_ACTIVITY_ID = "264266843"
FIXED_WJX_TEMPLATE_DESIGN_URL = (
    "https://www.wjx.cn/wjx/design/designstart.aspx?activity=264266843"
)


class AttendanceConfigUpdateRequest(BaseModel):
    current_wjx_account_id: Optional[str] = None
    execution_device_entry_id: Optional[str] = None


class AttendanceAccountCreateRequest(BaseModel):
    name: Optional[str] = None
    login_username: str
    password: str


class AttendanceAccountUpdateRequest(BaseModel):
    name: Optional[str] = None
    login_username: Optional[str] = None
    password: Optional[str] = None


class AttendanceTemplateCreateRequest(BaseModel):
    name: str
    activity_id: str
    is_active: bool = True


class AttendanceTemplateUpdateRequest(BaseModel):
    name: Optional[str] = None
    activity_id: Optional[str] = None
    is_active: Optional[bool] = None


class AttendanceRunCreateRequest(BaseModel):
    template_id: Optional[str] = None
    action: Literal["inspect", "apply"]
    account_id: Optional[str] = None
    execution_device_entry_id: Optional[str] = None
    hide: list[str] = Field(default_factory=list)
    add: list[str] = Field(default_factory=list)
    persist_global_selection: bool = True


def _normalize_optional_id(value: Optional[str]) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def _resolve_account_login_username(payload_login_username: Optional[str], payload_name: Optional[str]) -> str:
    login_username = (payload_login_username or "").strip()
    if login_username:
        return login_username
    fallback_name = (payload_name or "").strip()
    if fallback_name:
        return fallback_name
    return ""


def _get_fixed_wjx_template_payload() -> dict[str, str]:
    return {
        "id": FIXED_WJX_TEMPLATE_ID,
        "name": FIXED_WJX_TEMPLATE_NAME,
        "activity_id": FIXED_WJX_TEMPLATE_ACTIVITY_ID,
        "design_url": FIXED_WJX_TEMPLATE_DESIGN_URL,
    }


def _resolve_wjx_template_payload(template_id: Optional[str]) -> dict[str, str]:
    normalized = _normalize_optional_id(template_id)
    if normalized and normalized != FIXED_WJX_TEMPLATE_ID:
        raise HTTPException(status_code=400, detail="当前只支持固定的课程清单问卷")
    return _get_fixed_wjx_template_payload()


def _resolve_config_payload(session: Session) -> dict[str, Any]:
    config = get_or_create_attendance_service_config(session)
    current_account = get_current_account(session, config)
    current_device = get_current_execution_device(session, config)
    return {
        "service": {
            "current_wjx_account_id": config.current_wjx_account_id,
            "execution_device_entry_id": config.execution_device_entry_id,
            "granted_user_ids": list(config.granted_user_ids or []),
            "created_by_user_id": config.created_by_user_id,
            "updated_by_user_id": config.updated_by_user_id,
            "created_at": config.created_at,
            "updated_at": config.updated_at,
        },
        "current_account": serialize_attendance_account(current_account, include_password=True) if current_account else None,
        "current_execution_device": serialize_user_device(current_device),
        "fixed_wjx_template": _get_fixed_wjx_template_payload(),
    }


def _ensure_owned_device_for_selection(entry: UserDevice, current_user: User) -> None:
    if entry.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="只能从你自己的设备资产中选择执行设备")
    if not entry.is_active:
        raise HTTPException(status_code=400, detail="当前执行设备已停用")


def _resolve_run_account(
    session: Session,
    config,
    *,
    account_id: Optional[str],
) -> AttendanceAccountAsset:
    selected_id = _normalize_optional_id(account_id) or _normalize_optional_id(config.current_wjx_account_id)
    if not selected_id:
        accounts = list_attendance_accounts(session)
        if len(accounts) == 1:
            selected_id = accounts[0].id
    if not selected_id:
        raise HTTPException(status_code=400, detail="请先配置或选择当前问卷星账号")
    account = get_attendance_account_or_404(session, selected_id)
    return account


def _resolve_run_device(
    session: Session,
    config,
    *,
    execution_device_entry_id: Optional[str],
    current_user: User,
) -> UserDevice:
    selected_id = _normalize_optional_id(execution_device_entry_id) or _normalize_optional_id(config.execution_device_entry_id)
    if not selected_id:
        raise HTTPException(status_code=400, detail="请先配置或选择执行设备")
    entry = get_user_device_or_404(session, selected_id)
    if _normalize_optional_id(execution_device_entry_id) and selected_id != _normalize_optional_id(config.execution_device_entry_id):
        _ensure_owned_device_for_selection(entry, current_user)
    if not entry.is_active:
        raise HTTPException(status_code=400, detail="当前执行设备已停用")
    return entry


def _build_remote_headers(entry: UserDevice) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {entry.token}",
        "X-Device-Token": entry.token,
    }


def _execute_run_on_entry(entry_snapshot: dict[str, Any], execution_payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(entry_snapshot.get("mode") or "")
    if mode == "local":
        local_device_id = get_device_id()
        if str(entry_snapshot.get("device_id") or "") != local_device_id:
            raise RuntimeError("所选本地执行设备不属于当前节点")
        return execute_wjx_template_action(**execution_payload)

    server_url = (entry_snapshot.get("server_url") or "").rstrip("/")
    token = str(entry_snapshot.get("token") or "")
    if not server_url or not token:
        raise RuntimeError("远程执行设备缺少后端地址或访问令牌")

    response = requests.post(
        f"{server_url}/api/device-control/attendance/wjx/execute",
        json=execution_payload,
        headers=_build_remote_headers(
            UserDevice(
                entry_id=str(entry_snapshot.get("entry_id") or ""),
                user_id=int(entry_snapshot.get("user_id") or 0),
                device_id=str(entry_snapshot.get("device_id") or ""),
                name=str(entry_snapshot.get("name") or ""),
                mode=str(entry_snapshot.get("mode") or "remote"),
                server_url=server_url,
                token=token,
                is_active=bool(entry_snapshot.get("is_active", True)),
                order_index=int(entry_snapshot.get("order_index") or 0),
                created_at=float(entry_snapshot.get("created_at") or 0.0),
                updated_at=float(entry_snapshot.get("updated_at") or 0.0),
            )
        ),
        timeout=600,
    )
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = response.text.strip()
        raise RuntimeError(detail or f"远程执行失败，HTTP {response.status_code}")
    return response.json()


def _attendance_run_worker(
    *,
    db_bind,
    run_id: str,
    requested_by_user_id: int,
    account_id: str,
    execution_device_entry_id: str,
    entry_snapshot: dict[str, Any],
    execution_payload: dict[str, Any],
    persist_global_selection: bool,
) -> None:
    with Session(db_bind) as session:
        run = session.get(AttendanceRun, run_id)
        if run is None:
            return

        try:
            result = _execute_run_on_entry(entry_snapshot, execution_payload)
            now = time.time()
            run.status = "completed"
            run.result_json = result
            run.error_message = None
            run.finished_at = now
            run.updated_at = now
            session.add(run)

            if persist_global_selection:
                config = get_or_create_attendance_service_config(session)
                config.current_wjx_account_id = account_id
                config.execution_device_entry_id = execution_device_entry_id
                config.updated_by_user_id = requested_by_user_id
                config.updated_at = now
                session.add(config)

            session.commit()
        except Exception as exc:
            now = time.time()
            run.status = "failed"
            run.error_message = str(exc)
            run.finished_at = now
            run.updated_at = now
            session.add(run)
            session.commit()


@router.get("/config")
def get_attendance_config(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    ensure_can_manage_attendance_service(current_user)
    return _resolve_config_payload(session)


@router.put("/config")
def update_attendance_config(
    payload: AttendanceConfigUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    current_user = ensure_can_manage_attendance_service(current_user)
    config = get_or_create_attendance_service_config(session, actor=current_user)

    account_id = _normalize_optional_id(payload.current_wjx_account_id)
    device_entry_id = _normalize_optional_id(payload.execution_device_entry_id)

    if payload.current_wjx_account_id is not None:
        if account_id is None:
            config.current_wjx_account_id = None
        else:
            account = get_attendance_account_or_404(session, account_id)
            config.current_wjx_account_id = account.id

    if payload.execution_device_entry_id is not None:
        if device_entry_id is None:
            config.execution_device_entry_id = None
        else:
            entry = get_user_device_or_404(session, device_entry_id)
            if device_entry_id != _normalize_optional_id(config.execution_device_entry_id):
                _ensure_owned_device_for_selection(entry, current_user)
            if not entry.is_active:
                raise HTTPException(status_code=400, detail="当前执行设备已停用")
            config.execution_device_entry_id = entry.entry_id

    config.updated_by_user_id = current_user.id
    config.updated_at = time.time()
    session.add(config)
    session.commit()
    return _resolve_config_payload(session)


@router.get("/accounts")
def get_attendance_accounts(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    ensure_can_manage_attendance_service(current_user)
    return {"items": [serialize_attendance_account(item, include_password=True) for item in list_attendance_accounts(session)]}


@router.post("/accounts")
def create_attendance_account(
    payload: AttendanceAccountCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    current_user = ensure_can_manage_attendance_service(current_user)
    existing_accounts = list_attendance_accounts(session)
    if existing_accounts:
        raise HTTPException(status_code=400, detail="问卷星账号只支持一个，请直接编辑现有账号")

    login_username = _resolve_account_login_username(payload.login_username, payload.name)
    password = payload.password
    if not login_username:
        raise HTTPException(status_code=400, detail="登录账号不能为空")
    if not password:
        raise HTTPException(status_code=400, detail="登录密码不能为空")

    now = time.time()
    account = AttendanceAccountAsset(
        name=login_username,
        login_username=login_username,
        password_encrypted=encrypt_attendance_secret(password),
        is_active=True,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(account)
    session.commit()
    session.refresh(account)

    config = get_or_create_attendance_service_config(session, actor=current_user)
    config.current_wjx_account_id = account.id
    config.updated_by_user_id = current_user.id
    config.updated_at = time.time()
    session.add(config)
    session.commit()
    session.refresh(account)
    return serialize_attendance_account(account, include_password=True)


@router.put("/accounts/{account_id}")
def update_attendance_account(
    account_id: str,
    payload: AttendanceAccountUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    current_user = ensure_can_manage_attendance_service(current_user)
    account = get_attendance_account_or_404(session, account_id)

    if payload.login_username is not None or payload.name is not None:
        login_username = _resolve_account_login_username(payload.login_username, payload.name)
        if not login_username:
            raise HTTPException(status_code=400, detail="登录账号不能为空")
        account.login_username = login_username
        account.name = login_username
    if payload.password is not None:
        if not payload.password:
            raise HTTPException(status_code=400, detail="登录密码不能为空")
        account.password_encrypted = encrypt_attendance_secret(payload.password)

    account.updated_by_user_id = current_user.id
    account.updated_at = time.time()
    session.add(account)
    session.commit()
    session.refresh(account)
    return serialize_attendance_account(account, include_password=True)


@router.delete("/accounts/{account_id}")
def delete_attendance_account(
    account_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    current_user = ensure_can_manage_attendance_service(current_user)
    account = get_attendance_account_or_404(session, account_id)
    config = get_or_create_attendance_service_config(session, actor=current_user)
    if config.current_wjx_account_id == account.id:
        config.current_wjx_account_id = None
        config.updated_by_user_id = current_user.id
        config.updated_at = time.time()
        session.add(config)
    session.delete(account)
    session.commit()
    return {"ok": True}


@router.get("/templates")
def get_attendance_templates(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    ensure_can_manage_attendance_service(current_user)
    return {"items": [serialize_attendance_template(item) for item in list_attendance_templates(session)]}


@router.post("/templates")
def create_attendance_template(
    payload: AttendanceTemplateCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    current_user = ensure_can_manage_attendance_service(current_user)
    name = payload.name.strip()
    activity_id = payload.activity_id.strip()
    if not name:
        raise HTTPException(status_code=400, detail="模板名称不能为空")
    if not activity_id:
        raise HTTPException(status_code=400, detail="问卷 activity_id 不能为空")

    now = time.time()
    template = AttendanceTemplateAsset(
        name=name,
        activity_id=activity_id,
        is_active=payload.is_active,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(template)
    session.commit()
    session.refresh(template)
    return serialize_attendance_template(template)


@router.put("/templates/{template_id}")
def update_attendance_template(
    template_id: str,
    payload: AttendanceTemplateUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    current_user = ensure_can_manage_attendance_service(current_user)
    template = get_attendance_template_or_404(session, template_id)

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="模板名称不能为空")
        template.name = name
    if payload.activity_id is not None:
        activity_id = payload.activity_id.strip()
        if not activity_id:
            raise HTTPException(status_code=400, detail="问卷 activity_id 不能为空")
        template.activity_id = activity_id
    if payload.is_active is not None:
        template.is_active = payload.is_active

    template.updated_by_user_id = current_user.id
    template.updated_at = time.time()
    session.add(template)
    session.commit()
    session.refresh(template)
    return serialize_attendance_template(template)


@router.delete("/templates/{template_id}")
def delete_attendance_template(
    template_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    ensure_can_manage_attendance_service(current_user)
    template = get_attendance_template_or_404(session, template_id)
    session.delete(template)
    session.commit()
    return {"ok": True}


@router.post("/wjx-runs")
def create_attendance_run(
    payload: AttendanceRunCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    current_user = ensure_can_use_attendance_service(current_user, session)
    template = _resolve_wjx_template_payload(payload.template_id)

    config = get_or_create_attendance_service_config(session)
    account = _resolve_run_account(session, config, account_id=payload.account_id)
    entry = _resolve_run_device(
        session,
        config,
        execution_device_entry_id=payload.execution_device_entry_id,
        current_user=current_user,
    )

    try:
        password_plain = decrypt_attendance_secret(account.password_encrypted)
    except AttendanceServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    execution_payload = {
        "login_username": account.login_username,
        "password": password_plain,
        "activity_id": template["activity_id"],
        "action": payload.action,
        "hide_names": list(payload.hide or []),
        "add_names": list(payload.add or []),
    }
    run_request_payload = {
        "template": template,
        "account_id": account.id,
        "execution_device_entry_id": entry.entry_id,
        "action": payload.action,
        "hide": list(payload.hide or []),
        "add": list(payload.add or []),
        "persist_global_selection": payload.persist_global_selection,
    }

    now = time.time()
    run = AttendanceRun(
        template_id=template["id"],
        account_id=account.id,
        execution_device_entry_id=entry.entry_id,
        requested_by_user_id=current_user.id,
        action=payload.action,
        status="running",
        request_json=run_request_payload,
        result_json={},
        error_message=None,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    db_bind = session.get_bind()

    worker = threading.Thread(
        target=_attendance_run_worker,
        kwargs={
            "db_bind": db_bind,
            "run_id": run.id,
            "requested_by_user_id": current_user.id,
            "account_id": account.id,
            "execution_device_entry_id": entry.entry_id,
            "entry_snapshot": {
                **serialize_user_device(entry),
                "token": entry.token,
            },
            "execution_payload": execution_payload,
            "persist_global_selection": payload.persist_global_selection,
        },
        daemon=True,
    )
    worker.start()

    return serialize_attendance_run(run)


@router.get("/wjx-runs/{run_id}")
def get_attendance_run(
    run_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    ensure_can_use_attendance_service(current_user, session)
    run = get_attendance_run_or_404(session, run_id)
    return serialize_attendance_run(run)
