from __future__ import annotations

import base64
import hashlib
import time
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlmodel import Session, select

from backend.core.attendance_access import can_manage_attendance_service, can_use_attendance_service
from backend.core.settings import get_settings
from backend.models import (
    AttendanceAccountAsset,
    AttendanceRun,
    AttendanceServiceConfig,
    AttendanceTemplateAsset,
    User,
    UserDevice,
)


class AttendanceServiceError(RuntimeError):
    """Raised when attendance service state cannot be used safely."""


SERVICE_CONFIG_ID = 1


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    secret_key = get_settings().secret_key.encode("utf-8")
    digest = hashlib.sha256(secret_key).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_attendance_secret(value: str) -> str:
    payload = (value or "").strip()
    if not payload:
        return ""
    return _get_fernet().encrypt(payload.encode("utf-8")).decode("utf-8")


def decrypt_attendance_secret(value: str) -> str:
    payload = (value or "").strip()
    if not payload:
        return ""
    try:
        return _get_fernet().decrypt(payload.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise AttendanceServiceError("已保存的问卷星密码无法解密，请重新保存") from exc


def ensure_can_manage_attendance_service(user: User | None) -> User:
    if not can_manage_attendance_service(user):
        raise HTTPException(status_code=403, detail="没有禅寺考勤管理权限")
    assert user is not None
    return user


def ensure_can_use_attendance_service(user: User | None, session: Session) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    config = get_or_create_attendance_service_config(session)
    if not can_use_attendance_service(user, granted_user_ids=config.granted_user_ids):
        raise HTTPException(status_code=403, detail="没有禅寺考勤使用权限")
    return user


def get_or_create_attendance_service_config(
    session: Session,
    *,
    actor: User | None = None,
) -> AttendanceServiceConfig:
    config = session.get(AttendanceServiceConfig, SERVICE_CONFIG_ID)
    if config is not None:
        return config

    now = time.time()
    actor_id = actor.id if actor else None
    config = AttendanceServiceConfig(
        id=SERVICE_CONFIG_ID,
        granted_user_ids=[],
        created_by_user_id=actor_id,
        updated_by_user_id=actor_id,
        created_at=now,
        updated_at=now,
    )
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def serialize_user_device(entry: UserDevice | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    return {
        "entry_id": entry.entry_id,
        "user_id": entry.user_id,
        "device_id": entry.device_id,
        "name": entry.name,
        "mode": entry.mode,
        "server_url": entry.server_url,
        "is_active": entry.is_active,
        "order_index": entry.order_index,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def serialize_attendance_account(account: AttendanceAccountAsset, *, include_password: bool = False) -> dict[str, Any]:
    data = {
        "id": account.id,
        "provider": account.provider,
        "name": account.name,
        "login_username": account.login_username,
        "created_by_user_id": account.created_by_user_id,
        "updated_by_user_id": account.updated_by_user_id,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }
    if include_password:
        data["password"] = decrypt_attendance_secret(account.password_encrypted)
    return data


def serialize_attendance_template(template: AttendanceTemplateAsset) -> dict[str, Any]:
    return {
        "id": template.id,
        "provider": template.provider,
        "name": template.name,
        "activity_id": template.activity_id,
        "is_active": template.is_active,
        "created_by_user_id": template.created_by_user_id,
        "updated_by_user_id": template.updated_by_user_id,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


def serialize_attendance_run(run: AttendanceRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "template_id": run.template_id,
        "account_id": run.account_id,
        "execution_device_entry_id": run.execution_device_entry_id,
        "requested_by_user_id": run.requested_by_user_id,
        "action": run.action,
        "status": run.status,
        "request": dict(run.request_json or {}),
        "result": dict(run.result_json or {}),
        "error_message": run.error_message,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "updated_at": run.updated_at,
    }


def get_attendance_account_or_404(session: Session, account_id: str) -> AttendanceAccountAsset:
    account = session.get(AttendanceAccountAsset, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="问卷星账号不存在")
    return account


def get_attendance_template_or_404(session: Session, template_id: str) -> AttendanceTemplateAsset:
    template = session.get(AttendanceTemplateAsset, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="问卷星模板不存在")
    return template


def get_attendance_run_or_404(session: Session, run_id: str) -> AttendanceRun:
    run = session.get(AttendanceRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return run


def get_user_device_or_404(session: Session, entry_id: str) -> UserDevice:
    entry = session.get(UserDevice, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="执行设备不存在")
    return entry


def get_current_account(session: Session, config: AttendanceServiceConfig) -> AttendanceAccountAsset | None:
    if not config.current_wjx_account_id:
        return None
    return session.get(AttendanceAccountAsset, config.current_wjx_account_id)


def get_current_execution_device(session: Session, config: AttendanceServiceConfig) -> UserDevice | None:
    if not config.execution_device_entry_id:
        return None
    return session.get(UserDevice, config.execution_device_entry_id)


def list_attendance_accounts(session: Session) -> list[AttendanceAccountAsset]:
    statement = (
        select(AttendanceAccountAsset)
        .order_by(AttendanceAccountAsset.updated_at.desc(), AttendanceAccountAsset.created_at.desc())
    )
    return list(session.exec(statement).all())


def list_attendance_templates(session: Session) -> list[AttendanceTemplateAsset]:
    statement = (
        select(AttendanceTemplateAsset)
        .order_by(AttendanceTemplateAsset.updated_at.desc(), AttendanceTemplateAsset.created_at.desc())
    )
    return list(session.exec(statement).all())
