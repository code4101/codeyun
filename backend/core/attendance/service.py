from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pyxllib.cv.rgbfmt import hash_text_to_hex_color
from sqlmodel import Session, select

from backend.core.attendance.access import can_manage_attendance_service, can_use_attendance_service
from backend.core.settings import get_settings
from backend.models import (
    AttendanceAccountAsset,
    AttendanceOrderRefundHistory,
    AttendanceServiceConfig,
    AttendanceWjxDataEntry,
    AttendanceWjxDataSyncState,
    AppSetting,
    User,
    UserDevice,
)


class AttendanceServiceError(RuntimeError):
    """Raised when attendance service state cannot be used safely."""


SERVICE_CONFIG_ID = 1
ATTENDANCE_SERVICE_EXTRA_SETTING_KEY = "attendance.service.extra"
ATTENDANCE_COURSE_DATA_FLOW_SETTING_KEY = "attendance.course.data_flow"
ATTENDANCE_LEGACY_COURSE_WORKFLOW_SETTING_KEY = "attendance.course.workflow"
DEFAULT_ORDER_LOOKUP_MODE = "browser_only"
ORDER_LOOKUP_MODES = {"hybrid", "db_only", "browser_only"}
ATTENDANCE_ORDER_OPERATION_PASSWORD_KEY = "order_operation_password_encrypted"
ATTENDANCE_ORDER_OPERATION_PASSWORD_ENV = "XL_KQ_PAY_PASSWORD"
ATTENDANCE_COURSE_DATA_BROWSER_DEVICE_ENTRY_ID_KEY = "browser_device_entry_id"
ATTENDANCE_COURSE_DATA_DEVICE_ENTRY_ID_KEY = "data_device_entry_id"
ATTENDANCE_COURSE_DATA_STEP_DEVICE_ENTRY_IDS_KEY = "step_device_entry_ids"
ATTENDANCE_STEP_NUMBERS = (0, 1, 2, 3, 4, 5, 6)
ATTENDANCE_BROWSER_STEP_NUMBERS = {0, 1, 4, 6}
ATTENDANCE_STEP_TITLES = {
    0: "step0 浏览器同步动态课程目录与表格模板",
    1: "step1 课程数据浏览器导入原始数据",
    2: "step2 课程数据聚合与写回",
    3: "step3 课程数据返款计算与高亮",
    4: "step4 课程数据上午浏览器检查",
    5: "step5 课程数据上午处理",
    6: "step6 课程数据上午收尾",
}
SUBMITTED_DAY_PATTERN = re.compile(r"^\s*(\d{4}[/-]\d{1,2}[/-]\d{1,2})")


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


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _normalize_order_lookup_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in ORDER_LOOKUP_MODES:
        return normalized
    return DEFAULT_ORDER_LOOKUP_MODE


def normalize_attendance_step_number(value: Any) -> int:
    try:
        step_number = int(value)
    except (TypeError, ValueError) as exc:
        raise AttendanceServiceError("课程数据步骤编号必须是 0-6") from exc
    if step_number not in ATTENDANCE_STEP_NUMBERS:
        raise AttendanceServiceError("课程数据步骤编号必须是 0-6")
    return step_number


def normalize_attendance_device_entry_id(value: Any) -> str:
    return str(value or "").strip()


def normalize_attendance_step_device_entry_ids(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for raw_step, raw_entry_id in value.items():
        try:
            step_number = normalize_attendance_step_number(raw_step)
        except AttendanceServiceError:
            continue
        entry_id = normalize_attendance_device_entry_id(raw_entry_id)
        if entry_id:
            result[str(step_number)] = entry_id
    return result


def get_attendance_course_data_step_default_role(step_number: int) -> str:
    normalized_step = normalize_attendance_step_number(step_number)
    if normalized_step in ATTENDANCE_BROWSER_STEP_NUMBERS:
        return "browser_device"
    return "data_host"


def get_attendance_service_extra_config(session: Session) -> dict[str, Any]:
    row = session.get(AppSetting, ATTENDANCE_SERVICE_EXTRA_SETTING_KEY)
    payload = row.value if row and isinstance(row.value, dict) else {}
    return {
        "scan_reminder_users": _normalize_string_list(payload.get("scan_reminder_users")),
        "order_lookup_mode": _normalize_order_lookup_mode(payload.get("order_lookup_mode")),
        "order_operation_password_configured": bool(str(payload.get(ATTENDANCE_ORDER_OPERATION_PASSWORD_KEY) or "").strip()),
    }


def get_attendance_service_order_operation_password(session: Session) -> str:
    row = session.get(AppSetting, ATTENDANCE_SERVICE_EXTRA_SETTING_KEY)
    payload = row.value if row and isinstance(row.value, dict) else {}
    encrypted = str(payload.get(ATTENDANCE_ORDER_OPERATION_PASSWORD_KEY) or "").strip()
    if not encrypted:
        return ""
    try:
        return decrypt_attendance_secret(encrypted)
    except AttendanceServiceError as exc:
        raise AttendanceServiceError("已保存的退款操作密码无法解密，请重新保存") from exc


def update_attendance_service_extra_config(
    session: Session,
    *,
    scan_reminder_users: list[str] | None = None,
    order_lookup_mode: str | None = None,
    order_operation_password: str | None = None,
    clear_order_operation_password: bool = False,
) -> dict[str, Any]:
    row = session.get(AppSetting, ATTENDANCE_SERVICE_EXTRA_SETTING_KEY)
    payload = row.value.copy() if row and isinstance(row.value, dict) else {}

    if scan_reminder_users is not None:
        payload["scan_reminder_users"] = _normalize_string_list(scan_reminder_users)
    if order_lookup_mode is not None:
        payload["order_lookup_mode"] = _normalize_order_lookup_mode(order_lookup_mode)
    if clear_order_operation_password:
        payload[ATTENDANCE_ORDER_OPERATION_PASSWORD_KEY] = ""
    elif order_operation_password is not None:
        payload[ATTENDANCE_ORDER_OPERATION_PASSWORD_KEY] = encrypt_attendance_secret(order_operation_password)

    now = time.time()
    if row is None:
        row = AppSetting(key=ATTENDANCE_SERVICE_EXTRA_SETTING_KEY, value=payload, updated_at=now)
    else:
        row.value = payload
        row.updated_at = now

    session.add(row)
    session.commit()
    session.refresh(row)
    return get_attendance_service_extra_config(session)


def get_attendance_course_data_flow_config(session: Session) -> dict[str, Any]:
    row = session.get(AppSetting, ATTENDANCE_COURSE_DATA_FLOW_SETTING_KEY)
    if row is None:
        row = session.get(AppSetting, ATTENDANCE_LEGACY_COURSE_WORKFLOW_SETTING_KEY)
    payload = row.value if row and isinstance(row.value, dict) else {}
    return {
        ATTENDANCE_COURSE_DATA_BROWSER_DEVICE_ENTRY_ID_KEY: normalize_attendance_device_entry_id(
            payload.get(ATTENDANCE_COURSE_DATA_BROWSER_DEVICE_ENTRY_ID_KEY)
        ) or None,
        ATTENDANCE_COURSE_DATA_DEVICE_ENTRY_ID_KEY: normalize_attendance_device_entry_id(
            payload.get(ATTENDANCE_COURSE_DATA_DEVICE_ENTRY_ID_KEY)
        ) or None,
        ATTENDANCE_COURSE_DATA_STEP_DEVICE_ENTRY_IDS_KEY: normalize_attendance_step_device_entry_ids(
            payload.get(ATTENDANCE_COURSE_DATA_STEP_DEVICE_ENTRY_IDS_KEY)
        ),
    }


def update_attendance_course_data_flow_config(
    session: Session,
    *,
    browser_device_entry_id: str | None = None,
    data_device_entry_id: str | None = None,
    step_device_entry_ids: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = session.get(AppSetting, ATTENDANCE_COURSE_DATA_FLOW_SETTING_KEY)
    payload = row.value.copy() if row and isinstance(row.value, dict) else {}

    if browser_device_entry_id is not None:
        payload[ATTENDANCE_COURSE_DATA_BROWSER_DEVICE_ENTRY_ID_KEY] = normalize_attendance_device_entry_id(
            browser_device_entry_id
        )
    if data_device_entry_id is not None:
        payload[ATTENDANCE_COURSE_DATA_DEVICE_ENTRY_ID_KEY] = normalize_attendance_device_entry_id(data_device_entry_id)
    if step_device_entry_ids is not None:
        payload[ATTENDANCE_COURSE_DATA_STEP_DEVICE_ENTRY_IDS_KEY] = normalize_attendance_step_device_entry_ids(
            step_device_entry_ids
        )

    now = time.time()
    if row is None:
        row = AppSetting(key=ATTENDANCE_COURSE_DATA_FLOW_SETTING_KEY, value=payload, updated_at=now)
    else:
        row.value = payload
        row.updated_at = now

    session.add(row)
    session.commit()
    session.refresh(row)
    return get_attendance_course_data_flow_config(session)


def get_attendance_course_data_step_effective_device_entry_id(
    config: AttendanceServiceConfig,
    data_flow_config: dict[str, Any],
    step_number: int,
) -> str | None:
    normalized_step = normalize_attendance_step_number(step_number)
    step_device_entry_ids = normalize_attendance_step_device_entry_ids(
        data_flow_config.get(ATTENDANCE_COURSE_DATA_STEP_DEVICE_ENTRY_IDS_KEY)
    )
    explicit_entry_id = step_device_entry_ids.get(str(normalized_step))
    if explicit_entry_id:
        return explicit_entry_id

    default_role = get_attendance_course_data_step_default_role(normalized_step)
    if default_role == "browser_device":
        return (
            normalize_attendance_device_entry_id(
                data_flow_config.get(ATTENDANCE_COURSE_DATA_BROWSER_DEVICE_ENTRY_ID_KEY)
            )
            or normalize_attendance_device_entry_id(config.execution_device_entry_id)
            or None
        )
    return normalize_attendance_device_entry_id(data_flow_config.get(ATTENDANCE_COURSE_DATA_DEVICE_ENTRY_ID_KEY)) or None


def build_attendance_course_data_step_runner_configs(
    session: Session,
    config: AttendanceServiceConfig,
    data_flow_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    data_flow = data_flow_config or get_attendance_course_data_flow_config(session)
    step_device_entry_ids = normalize_attendance_step_device_entry_ids(
        data_flow.get(ATTENDANCE_COURSE_DATA_STEP_DEVICE_ENTRY_IDS_KEY)
    )
    browser_device_entry_id = normalize_attendance_device_entry_id(
        data_flow.get(ATTENDANCE_COURSE_DATA_BROWSER_DEVICE_ENTRY_ID_KEY)
    )
    data_device_entry_id = normalize_attendance_device_entry_id(
        data_flow.get(ATTENDANCE_COURSE_DATA_DEVICE_ENTRY_ID_KEY)
    )

    items: list[dict[str, Any]] = []
    for step_number in ATTENDANCE_STEP_NUMBERS:
        step_key = str(step_number)
        configured_entry_id = step_device_entry_ids.get(step_key)
        default_role = get_attendance_course_data_step_default_role(step_number)
        effective_role = "custom_device" if configured_entry_id else default_role
        if configured_entry_id:
            effective_entry_id = configured_entry_id
        elif default_role == "browser_device":
            effective_entry_id = browser_device_entry_id or normalize_attendance_device_entry_id(
                config.execution_device_entry_id
            )
        else:
            effective_entry_id = data_device_entry_id

        device = session.get(UserDevice, effective_entry_id) if effective_entry_id else None
        items.append({
            "step": step_number,
            "title": ATTENDANCE_STEP_TITLES[step_number],
            "default_role": default_role,
            "effective_role": effective_role,
            "configured_device_entry_id": configured_entry_id or None,
            "effective_device_entry_id": effective_entry_id or None,
            "device": serialize_user_device(device),
            "device_missing": bool(effective_entry_id and device is None),
            "device_inactive": bool(device is not None and not device.is_active),
        })
    return items


def get_attendance_course_data_step_runner_device(
    session: Session,
    config: AttendanceServiceConfig,
    *,
    step_number: int,
    data_flow_config: dict[str, Any] | None = None,
) -> UserDevice | None:
    data_flow = data_flow_config or get_attendance_course_data_flow_config(session)
    entry_id = get_attendance_course_data_step_effective_device_entry_id(config, data_flow, step_number)
    if not entry_id:
        return None
    entry = session.get(UserDevice, entry_id)
    if entry is None:
        raise AttendanceServiceError(f"课程数据 step{step_number} 运行设备不存在，请在考勤配置页重新选择")
    if not entry.is_active:
        raise AttendanceServiceError(f"课程数据 step{step_number} 运行设备已停用，请在考勤配置页重新选择")
    return entry


def _encode_temp_xlenv_string(value: str) -> str:
    return base64.b64encode(json.dumps(str(value)).encode("utf-8")).decode("utf-8")


@contextmanager
def apply_attendance_order_operation_password_env(password: str | None):
    normalized = (password or "").strip()
    if not normalized:
        yield
        return

    previous = os.environ.get(ATTENDANCE_ORDER_OPERATION_PASSWORD_ENV)
    os.environ[ATTENDANCE_ORDER_OPERATION_PASSWORD_ENV] = _encode_temp_xlenv_string(normalized)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(ATTENDANCE_ORDER_OPERATION_PASSWORD_ENV, None)
        else:
            os.environ[ATTENDANCE_ORDER_OPERATION_PASSWORD_ENV] = previous


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


def _normalize_attendance_order_id(value: Any) -> str:
    return str(value or "").lstrip("`'").strip()


def _build_attendance_order_refund_history_foreground_colors(
    *,
    created_at: float | None,
    operator_name: str | None,
) -> dict[str, str | None]:
    def resolve(value: str | None) -> str | None:
        normalized = _normalize_attendance_color_key(value)
        if not normalized:
            return None
        return hash_text_to_hex_color(normalized, tone="dark")

    created_day_key = ""
    if isinstance(created_at, (int, float)) and float(created_at) > 0:
        created_day_key = datetime.fromtimestamp(float(created_at)).strftime("%Y/%m/%d")

    return {
        "created_day": resolve(created_day_key),
        "operator": resolve(operator_name),
    }


def serialize_attendance_order_refund_history(
    record: AttendanceOrderRefundHistory,
    *,
    created_at: float | None = None,
) -> dict[str, Any]:
    operator_name = (record.operator_nickname or "").strip() or (record.operator_username or "").strip()
    effective_created_at = created_at if isinstance(created_at, (int, float)) else record.created_at
    return {
        "id": record.id,
        "requested_by_user_id": record.requested_by_user_id,
        "operator_username": record.operator_username,
        "operator_nickname": record.operator_nickname,
        "operator_name": operator_name,
        "execution_device_entry_id": record.execution_device_entry_id,
        "student_name": record.student_name,
        "wechat_order_id": _normalize_attendance_order_id(record.wechat_order_id),
        "merchant_order_id": _normalize_attendance_order_id(record.merchant_order_id),
        "order_amount": record.order_amount,
        "refunded_amount": record.refunded_amount,
        "remaining_amount": record.remaining_amount,
        "refund_amount": record.refund_amount,
        "refund_reason": record.refund_reason,
        "result_text": record.result_text,
        "created_at": effective_created_at,
        "foreground_colors": _build_attendance_order_refund_history_foreground_colors(
            created_at=effective_created_at,
            operator_name=operator_name,
        ),
    }


def serialize_attendance_wjx_data_sync_state(state: AttendanceWjxDataSyncState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "activity_id": state.activity_id,
        "template_id": state.template_id,
        "last_max_seq": state.last_max_seq,
        "last_incremental_count": state.last_incremental_count,
        "stored_count": state.stored_count,
        "last_used_all_pages": state.last_used_all_pages,
        "last_sync_at": state.last_sync_at,
        "last_success_at": state.last_success_at,
        "last_error": state.last_error,
        "execution_device_entry_id": state.execution_device_entry_id,
        "created_by_user_id": state.created_by_user_id,
        "updated_by_user_id": state.updated_by_user_id,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


def _normalize_attendance_color_key(value: str | None) -> str:
    return (value or "").strip()


def _extract_submitted_day_key(submitted_at_text: str | None) -> str:
    value = _normalize_attendance_color_key(submitted_at_text)
    if not value:
        return ""
    match = SUBMITTED_DAY_PATTERN.match(value)
    if match:
        return match.group(1).replace("-", "/")
    return value.split()[0]


def _build_attendance_wjx_data_foreground_colors(entry: AttendanceWjxDataEntry) -> dict[str, str | None]:
    def resolve(value: str) -> str | None:
        normalized = _normalize_attendance_color_key(value)
        if not normalized:
            return None
        return hash_text_to_hex_color(normalized, tone="dark")

    return {
        "submitted": resolve(_extract_submitted_day_key(entry.submitted_at_text)),
        "course": resolve(entry.course_name),
        "student": resolve(entry.student_name),
    }


def serialize_attendance_wjx_data_entry(entry: AttendanceWjxDataEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "activity_id": entry.activity_id,
        "seq": entry.seq,
        "submitted_at_text": entry.submitted_at_text,
        "duration_text": entry.duration_text,
        "source": entry.source,
        "source_detail": entry.source_detail,
        "source_ip": entry.source_ip,
        "course_name": entry.course_name,
        "student_id_text": entry.student_id_text,
        "student_name": entry.student_name,
        "foreground_colors": _build_attendance_wjx_data_foreground_colors(entry),
        "correction_request": entry.correction_request,
        "extra_note": entry.extra_note,
        "process_status": entry.process_status,
        "process_note": entry.process_note,
        "match_result": dict(entry.match_result_json or {}),
        "revision_result": dict(entry.revision_result_json or {}),
        "raw_row": dict(entry.raw_row_json or {}),
        "synced_at": entry.synced_at,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def get_or_create_attendance_wjx_data_sync_state(
    session: Session,
    *,
    activity_id: str,
    template_id: str = "wjx-course-catalog",
    actor: User | None = None,
) -> AttendanceWjxDataSyncState:
    state = session.get(AttendanceWjxDataSyncState, activity_id)
    if state is not None:
        return state

    now = time.time()
    actor_id = actor.id if actor else None
    state = AttendanceWjxDataSyncState(
        activity_id=activity_id,
        template_id=template_id,
        created_by_user_id=actor_id,
        updated_by_user_id=actor_id,
        created_at=now,
        updated_at=now,
    )
    session.add(state)
    session.commit()
    session.refresh(state)
    return state


def get_attendance_wjx_data_entry_or_404(session: Session, entry_id: int) -> AttendanceWjxDataEntry:
    entry = session.get(AttendanceWjxDataEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="问卷星数据不存在")
    return entry


def get_attendance_account_or_404(session: Session, account_id: str) -> AttendanceAccountAsset:
    account = session.get(AttendanceAccountAsset, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="问卷星账号不存在")
    return account


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
