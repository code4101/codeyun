from __future__ import annotations

import threading
import time
import re
from datetime import datetime
from typing import Any, Literal, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlmodel import Session, select

from backend.core.attendance_service import (
    AttendanceServiceError,
    apply_attendance_order_operation_password_env,
    decrypt_attendance_secret,
    encrypt_attendance_secret,
    ensure_can_manage_attendance_service,
    ensure_can_use_attendance_service,
    get_attendance_account_or_404,
    get_attendance_wjx_data_entry_or_404,
    get_attendance_run_or_404,
    get_attendance_service_order_operation_password,
    get_attendance_template_or_404,
    get_current_account,
    get_current_execution_device,
    get_or_create_attendance_service_config,
    get_or_create_attendance_wjx_data_sync_state,
    get_attendance_service_extra_config,
    get_user_device_or_404,
    list_attendance_accounts,
    serialize_attendance_order_refund_history,
    list_attendance_templates,
    serialize_attendance_account,
    serialize_attendance_run,
    serialize_attendance_template,
    serialize_attendance_wjx_data_entry,
    serialize_attendance_wjx_data_sync_state,
    serialize_user_device,
    update_attendance_service_extra_config,
)
from backend.core.attendance_order import OrderAutomationError, execute_order_action
from backend.core.attendance_wjx_data import (
    WjxDataSyncError,
    execute_wjx_data_sync,
)
from backend.core.attendance_wjx import WjxAutomationError, execute_wjx_template_action
from backend.core.auth import get_current_user_from_token, get_optional_current_user_from_token
from backend.core.device import get_device_id
from backend.core.feature_access import is_feature_access_allowed
from backend.core.feature_access_guard import (
    require_any_feature_access_dependency,
    require_feature_access_dependency,
)
from backend.core.ui_automation import ensure_ui_automation_thread_context
from backend.db import get_session
from backend.models import (
    AttendanceAccountAsset,
    AttendanceOrderRefundHistory,
    AttendanceRun,
    AttendanceTemplateAsset,
    AttendanceWjxDataEntry,
    AttendanceWjxDataSyncState,
    User,
    UserDevice,
)

public_router = APIRouter()

router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("attendance-tools"))],
)

FIXED_WJX_TEMPLATE_ID = "wjx-course-catalog"
FIXED_WJX_TEMPLATE_NAME = "课程清单问卷"
FIXED_WJX_TEMPLATE_ACTIVITY_ID = "264266843"
FIXED_WJX_TEMPLATE_DESIGN_URL = (
    "https://www.wjx.cn/wjx/design/designstart.aspx?activity=264266843"
)
FIXED_WJX_TEMPLATE_VIEW_URL = "https://www.wjx.cn/vm/PbkKDaK.aspx"
FIXED_WJX_TEMPLATE_FILL_URL = "https://www.wjx.cn/vm/PbkKDaK.aspx"
LOCAL_FEEDBACK_ACTIVITY_ID = "codeyun-attendance-feedback"
LOCAL_FEEDBACK_TEMPLATE_ID = "wjx-feedback-local"
LOCAL_FEEDBACK_SOURCE = "采集系统"
LOCAL_FEEDBACK_SOURCE_DETAIL = "CodeYun反馈表"
LOCAL_FEEDBACK_SEQ_START = 645
ORDER_HISTORY_RESULT_TIMESTAMP_PATTERN = re.compile(
    r"(?P<year>\d{4})[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
)


class AttendanceConfigUpdateRequest(BaseModel):
    current_wjx_account_id: Optional[str] = None
    execution_device_entry_id: Optional[str] = None
    scan_reminder_users: Optional[list[str]] = None
    order_lookup_mode: Optional[Literal["hybrid", "db_only", "browser_only"]] = None
    order_operation_password: Optional[str] = None
    clear_order_operation_password: Optional[bool] = None


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


class AttendanceOrderExecuteRequest(BaseModel):
    action: Literal["inspect", "refund"]
    rows: list[dict[str, Any]] = Field(default_factory=list)
    execution_device_entry_id: Optional[str] = None
    login_users: list[str] = Field(default_factory=list)
    order_lookup_mode: Optional[Literal["hybrid", "db_only", "browser_only"]] = None
    persist_global_selection: bool = True
    operation_password: Optional[str] = None


class AttendanceOrderRefundHistoryItem(BaseModel):
    class ForegroundColors(BaseModel):
        created_day: Optional[str] = None
        operator: Optional[str] = None

    id: str
    requested_by_user_id: Optional[int] = None
    operator_username: str
    operator_nickname: str
    operator_name: str
    execution_device_entry_id: Optional[str] = None
    student_name: str
    wechat_order_id: str
    merchant_order_id: str
    order_amount: str
    refunded_amount: str
    remaining_amount: str
    refund_amount: str
    refund_reason: str
    result_text: str
    created_at: float
    foreground_colors: ForegroundColors = Field(default_factory=ForegroundColors)


class AttendanceOrderRefundHistoryPage(BaseModel):
    items: list[AttendanceOrderRefundHistoryItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class AttendanceWjxDataSyncRequest(BaseModel):
    template_id: Optional[str] = None
    account_id: Optional[str] = None
    execution_device_entry_id: Optional[str] = None
    persist_global_selection: bool = True


class AttendanceFeedbackSubmitRequest(BaseModel):
    course_name: str
    student_id_text: str
    student_name: str
    correction_request: str
    extra_note: str = ""


class AttendanceWjxDataUpdateRequest(BaseModel):
    process_status: Optional[str] = None
    process_note: Optional[str] = None
    match_result: Optional[dict[str, Any]] = None
    revision_result: Optional[dict[str, Any]] = None


class AttendanceWjxDataForegroundColors(BaseModel):
    submitted: Optional[str] = None
    course: Optional[str] = None
    student: Optional[str] = None


class AttendanceWjxDataItem(BaseModel):
    id: int
    activity_id: str
    seq: int
    submitted_at_text: str
    duration_text: str
    source: str
    source_detail: str
    source_ip: str
    course_name: str
    student_id_text: str
    student_name: str
    foreground_colors: AttendanceWjxDataForegroundColors = Field(default_factory=AttendanceWjxDataForegroundColors)
    correction_request: str
    extra_note: str
    process_status: str
    process_note: str
    match_result: dict[str, Any] = Field(default_factory=dict)
    revision_result: dict[str, Any] = Field(default_factory=dict)
    raw_row: dict[str, Any] = Field(default_factory=dict)
    synced_at: float
    created_at: float
    updated_at: float


class AttendanceWjxDataSyncStateItem(BaseModel):
    activity_id: str
    template_id: str
    last_max_seq: int
    last_incremental_count: int
    stored_count: int
    last_used_all_pages: bool
    last_sync_at: Optional[float] = None
    last_success_at: Optional[float] = None
    last_error: Optional[str] = None
    execution_device_entry_id: Optional[str] = None
    created_by_user_id: Optional[int] = None
    updated_by_user_id: Optional[int] = None
    created_at: float
    updated_at: float


class AttendanceWjxDataPage(BaseModel):
    items: list[AttendanceWjxDataItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
    sync_state: Optional[AttendanceWjxDataSyncStateItem] = None
    template: dict[str, str]


class AttendanceFeedbackFormMeta(BaseModel):
    course_names: list[str] = Field(default_factory=list)
    course_names_updated_at: Optional[float] = None
    template: dict[str, str]


class AttendanceFeedbackFormMetaUpdateRequest(BaseModel):
    course_names: list[str] = Field(default_factory=list)


def _normalize_optional_id(value: Optional[str]) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def _normalize_order_history_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not value.is_integer():
            return f"{value:g}"
        return str(int(value))
    return str(value).strip()


def _normalize_course_name_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    seen: set[str] = set()
    result: list[str] = []
    for item in value:
        text = _normalize_order_history_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _coerce_order_history_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _compute_order_history_remaining_amount_text(order_amount: Any, refunded_amount: Any) -> str:
    amount = _coerce_order_history_number(order_amount)
    refunded = _coerce_order_history_number(refunded_amount)
    if amount is None or refunded is None:
        return ""
    remaining = max(amount - refunded, 0)
    if remaining.is_integer():
        return str(int(remaining))
    return f"{remaining:g}"


def _normalize_attendance_order_id(value: Any) -> str:
    return str(value or "").lstrip("`'").strip()


def _normalize_attendance_order_result_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized["微信支付订单号"] = _normalize_attendance_order_id(normalized.get("微信支付订单号"))
    normalized["商户订单号"] = _normalize_attendance_order_id(normalized.get("商户订单号"))
    return normalized


def _normalize_attendance_order_execution_result(result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(result or {})
    rows = normalized.get("rows")
    if isinstance(rows, list):
        normalized["rows"] = [
            _normalize_attendance_order_result_row(row)
            for row in rows
            if isinstance(row, dict)
        ]
    else:
        normalized["rows"] = []
    return normalized


def _parse_order_history_result_timestamp(value: Any) -> float | None:
    text = _normalize_order_history_text(value)
    if not text:
        return None

    match = ORDER_HISTORY_RESULT_TIMESTAMP_PATTERN.search(text)
    if match is None:
        return None

    try:
        dt = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second") or 0),
        )
    except ValueError:
        return None

    return dt.timestamp()


def _strip_order_history_result_timestamps(value: Any) -> str:
    text = _normalize_order_history_text(value)
    if not text:
        return ""

    timestamp_removed = False
    cleaned_lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        stripped_line = ORDER_HISTORY_RESULT_TIMESTAMP_PATTERN.sub("", line)
        if stripped_line != line:
            timestamp_removed = True
            line = re.sub(r"[ \t]{2,}", " ", stripped_line).strip().strip("：: -")

        if line:
            cleaned_lines.append(line)

    cleaned_text = "\n".join(cleaned_lines).strip()
    if timestamp_removed:
        cleaned_text = cleaned_text.rstrip("：: ").strip()
    return cleaned_text


def _resolve_order_history_raw_result_text(row: dict[str, Any]) -> str:
    result_text = _normalize_order_history_text(row.get("执行退款"))
    if result_text:
        return result_text

    fallback = row.get("订单金额")
    if isinstance(fallback, str) and _coerce_order_history_number(fallback) is None:
        return _normalize_order_history_text(fallback)
    return ""


def _resolve_order_history_result_text(row: dict[str, Any]) -> str:
    return _strip_order_history_result_timestamps(_resolve_order_history_raw_result_text(row))


def _resolve_order_history_record_timestamp(record: AttendanceOrderRefundHistory) -> float | None:
    parsed = _parse_order_history_result_timestamp(record.result_text)
    if parsed is not None:
        return parsed

    raw_row = record.raw_row_json if isinstance(record.raw_row_json, dict) else {}
    return _parse_order_history_result_timestamp(raw_row.get("执行退款"))


def _interpolate_order_history_timestamps(
    parsed_timestamps: list[float | None],
    fallback_timestamps: list[float],
) -> list[float]:
    resolved: list[float | None] = list(parsed_timestamps)
    total = len(resolved)
    index = 0

    while index < total:
        if resolved[index] is not None:
            index += 1
            continue

        start = index
        while index < total and resolved[index] is None:
            index += 1

        end = index
        prev_timestamp = resolved[start - 1] if start > 0 else None
        next_timestamp = resolved[end] if end < total else None
        gap_size = end - start

        if prev_timestamp is not None and next_timestamp is not None:
            step = (next_timestamp - prev_timestamp) / (gap_size + 1)
            for offset in range(gap_size):
                resolved[start + offset] = prev_timestamp + (step * (offset + 1))
            continue

        for offset in range(gap_size):
            resolved[start + offset] = fallback_timestamps[start + offset]

    return [
        fallback_timestamps[index] if timestamp is None else float(timestamp)
        for index, timestamp in enumerate(resolved)
    ]


def _resolve_order_history_row_timestamps(rows: list[dict[str, Any]], *, fallback_base: float) -> list[float]:
    fallback_timestamps = [fallback_base + (index * 0.001) for index in range(len(rows))]
    parsed_timestamps = [_parse_order_history_result_timestamp(_resolve_order_history_raw_result_text(row)) for row in rows]
    return _interpolate_order_history_timestamps(parsed_timestamps, fallback_timestamps)


def _serialize_order_refund_history_items(
    records: list[AttendanceOrderRefundHistory],
) -> list[AttendanceOrderRefundHistoryItem]:
    ordered_records = sorted(records, key=lambda record: (float(record.created_at or 0.0), record.id))
    resolved_timestamps = _interpolate_order_history_timestamps(
        [_resolve_order_history_record_timestamp(record) for record in ordered_records],
        [float(record.created_at or 0.0) for record in ordered_records],
    )

    serialized_items: list[dict[str, Any]] = []
    for record, created_at in zip(ordered_records, resolved_timestamps):
        item = serialize_attendance_order_refund_history(record, created_at=created_at)
        item["result_text"] = _strip_order_history_result_timestamps(item.get("result_text"))
        serialized_items.append(item)

    serialized_items.sort(key=lambda item: (float(item["created_at"] or 0.0), item["id"]), reverse=True)
    return [AttendanceOrderRefundHistoryItem.model_validate(item) for item in serialized_items]


def _is_meaningful_order_history_row(row: dict[str, Any]) -> bool:
    for key in ("学员名称", "微信支付订单号", "商户订单号", "订单金额", "已返款", "退款额度", "退款原因", "执行退款"):
        if _normalize_order_history_text(row.get(key)):
            return True
    return False


def _persist_refund_history(
    session: Session,
    *,
    current_user: User,
    execution_device_entry_id: str,
    rows: list[dict[str, Any]],
) -> None:
    meaningful_rows = [row for row in rows if isinstance(row, dict) and _is_meaningful_order_history_row(row)]
    if not meaningful_rows:
        return

    now = time.time()
    created_at_values = _resolve_order_history_row_timestamps(meaningful_rows, fallback_base=now)
    records: list[AttendanceOrderRefundHistory] = []
    for index, row in enumerate(meaningful_rows):
        order_amount = _normalize_order_history_text(row.get("订单金额"))
        refunded_amount = _normalize_order_history_text(row.get("已返款"))
        remaining_amount = _normalize_order_history_text(row.get("剩余金额"))
        if not remaining_amount:
            remaining_amount = _compute_order_history_remaining_amount_text(row.get("订单金额"), row.get("已返款"))

        records.append(
            AttendanceOrderRefundHistory(
                requested_by_user_id=current_user.id,
                operator_username=current_user.username,
                operator_nickname=current_user.nickname,
                execution_device_entry_id=execution_device_entry_id,
                student_name=_normalize_order_history_text(row.get("学员名称")),
                wechat_order_id=_normalize_attendance_order_id(row.get("微信支付订单号")),
                merchant_order_id=_normalize_attendance_order_id(row.get("商户订单号")),
                order_amount=order_amount,
                refunded_amount=refunded_amount,
                remaining_amount=remaining_amount,
                refund_amount=_normalize_order_history_text(row.get("退款额度")),
                refund_reason=_normalize_order_history_text(row.get("退款原因")),
                result_text=_resolve_order_history_result_text(row),
                raw_row_json=dict(row),
                created_at=created_at_values[index],
            )
        )

    session.add_all(records)
    session.commit()


def _build_order_refund_history_page(session: Session, *, page: int, page_size: int) -> AttendanceOrderRefundHistoryPage:
    normalized_page = max(1, int(page or 1))
    normalized_page_size = min(max(1, int(page_size or 20)), 100)
    offset = (normalized_page - 1) * normalized_page_size

    statement = select(AttendanceOrderRefundHistory)
    all_records = list(session.exec(statement).all())
    serialized_items = _serialize_order_refund_history_items(all_records)
    total = len(serialized_items)
    items = serialized_items[offset : offset + normalized_page_size]
    return AttendanceOrderRefundHistoryPage(
        items=items,
        total=total,
        page=normalized_page,
        page_size=normalized_page_size,
    )


def _normalize_wjx_data_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:g}"
    return str(value).strip()


def _normalize_required_feedback_text(value: Any, *, field_name: str) -> str:
    text = _normalize_wjx_data_text(value)
    if not text:
        raise HTTPException(status_code=400, detail=f"{field_name}不能为空")
    return text


def _resolve_feedback_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip

    client = request.client
    if client and client.host:
        return client.host
    return ""


def _format_feedback_submitted_at(now: float) -> str:
    return datetime.fromtimestamp(now).strftime("%Y/%m/%d %H:%M:%S")


def _list_attendance_wjx_data_activity_ids(template: dict[str, str]) -> list[str]:
    activity_ids = [str(template["activity_id"])]
    if LOCAL_FEEDBACK_ACTIVITY_ID not in activity_ids:
        activity_ids.append(LOCAL_FEEDBACK_ACTIVITY_ID)
    return activity_ids


def _resolve_wjx_data_state_template_id(activity_id: str) -> str:
    if activity_id == LOCAL_FEEDBACK_ACTIVITY_ID:
        return LOCAL_FEEDBACK_TEMPLATE_ID
    if activity_id == FIXED_WJX_TEMPLATE_ACTIVITY_ID:
        return FIXED_WJX_TEMPLATE_ID
    return activity_id


def _count_attendance_wjx_data_entries(session: Session, *, activity_id: str) -> int:
    return int(
        session.exec(
            select(func.count())
            .select_from(AttendanceWjxDataEntry)
            .where(AttendanceWjxDataEntry.activity_id == activity_id)
        ).one()
        or 0
    )


def _remember_wjx_data_seq_history(
    session: Session,
    *,
    activity_id: str,
    seq: int,
    actor: User | None = None,
) -> AttendanceWjxDataSyncState:
    now = time.time()
    state = get_or_create_attendance_wjx_data_sync_state(
        session,
        activity_id=activity_id,
        template_id=_resolve_wjx_data_state_template_id(activity_id),
        actor=actor,
    )
    state.last_max_seq = max(int(state.last_max_seq or 0), int(seq or 0))
    state.updated_at = now
    if actor is not None:
        state.updated_by_user_id = actor.id
    session.add(state)
    return state


def _resolve_wjx_data_history_max(session: Session) -> int:
    activity_ids = [FIXED_WJX_TEMPLATE_ACTIVITY_ID, LOCAL_FEEDBACK_ACTIVITY_ID]
    max_seq = session.exec(
        select(func.max(AttendanceWjxDataEntry.seq)).where(
            AttendanceWjxDataEntry.activity_id.in_(activity_ids)
        )
    ).one()
    states = session.exec(
        select(AttendanceWjxDataSyncState).where(
            AttendanceWjxDataSyncState.activity_id.in_(activity_ids)
        )
    ).all()
    state_max = max((int(item.last_max_seq or 0) for item in states), default=0)
    return max(int(max_seq or 0), state_max, LOCAL_FEEDBACK_SEQ_START - 1)


def _get_next_local_feedback_seq(session: Session) -> int:
    return _resolve_wjx_data_history_max(session) + 1


def _persist_attendance_feedback_submission(
    session: Session,
    *,
    payload: AttendanceFeedbackSubmitRequest,
    request: Request,
) -> AttendanceWjxDataEntry:
    course_name = _normalize_required_feedback_text(payload.course_name, field_name="所属课程")
    student_id_text = _normalize_required_feedback_text(payload.student_id_text, field_name="学号")
    student_name = _normalize_required_feedback_text(payload.student_name, field_name="姓名")
    correction_request = _normalize_required_feedback_text(payload.correction_request, field_name="修正需求")
    extra_note = _normalize_wjx_data_text(payload.extra_note)

    now = time.time()
    seq = _get_next_local_feedback_seq(session)
    state = _remember_wjx_data_seq_history(
        session,
        activity_id=LOCAL_FEEDBACK_ACTIVITY_ID,
        seq=seq,
    )
    submitted_at_text = _format_feedback_submitted_at(now)
    source_ip = _resolve_feedback_client_ip(request)
    raw_row = {
        "序号": seq,
        "提交答卷时间": submitted_at_text,
        "所用时间": "",
        "来源": LOCAL_FEEDBACK_SOURCE,
        "来源详情": LOCAL_FEEDBACK_SOURCE_DETAIL,
        "来自IP": source_ip,
        "1、所属课程": course_name,
        "2、学号": student_id_text,
        "3、姓名": student_name,
        "4、修正需求": correction_request,
        "5、其他补充说明": extra_note,
    }

    entry = AttendanceWjxDataEntry(
        activity_id=LOCAL_FEEDBACK_ACTIVITY_ID,
        seq=seq,
        submitted_at_text=submitted_at_text,
        duration_text="",
        source=LOCAL_FEEDBACK_SOURCE,
        source_detail=LOCAL_FEEDBACK_SOURCE_DETAIL,
        source_ip=source_ip,
        course_name=course_name,
        student_id_text=student_id_text,
        student_name=student_name,
        correction_request=correction_request,
        extra_note=extra_note,
        raw_row_json=raw_row,
        synced_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(entry)
    state.stored_count = _count_attendance_wjx_data_entries(
        session,
        activity_id=LOCAL_FEEDBACK_ACTIVITY_ID,
    ) + 1
    state.updated_at = now
    session.add(state)
    session.commit()
    session.refresh(entry)
    return entry


def _get_attendance_wjx_data_state(
    session: Session,
    *,
    template: dict[str, str],
    actor: User | None = None,
) -> AttendanceWjxDataSyncState:
    return get_or_create_attendance_wjx_data_sync_state(
        session,
        activity_id=str(template["activity_id"]),
        template_id=str(template["id"]),
        actor=actor,
    )


def _persist_wjx_data_sync_result(
    session: Session,
    *,
    current_user: User,
    template: dict[str, str],
    execution_device_entry_id: str,
    sync_result: dict[str, Any],
) -> tuple[AttendanceWjxDataSyncState, int, int]:
    activity_id = str(template["activity_id"])
    state = _get_attendance_wjx_data_state(session, template=template, actor=current_user)
    now = time.time()
    rows = list(sync_result.get("rows") or [])

    seqs: list[int] = []
    for row in rows:
        try:
            seqs.append(int(row.get("序号")))
        except (TypeError, ValueError):
            continue

    existing_map: dict[int, AttendanceWjxDataEntry] = {}
    if seqs:
        statement = select(AttendanceWjxDataEntry).where(
            AttendanceWjxDataEntry.activity_id == activity_id,
            AttendanceWjxDataEntry.seq.in_(seqs),
        )
        existing_map = {item.seq: item for item in session.exec(statement).all()}

    inserted_count = 0
    updated_count = 0
    for row in rows:
        try:
            seq = int(row.get("序号"))
        except (TypeError, ValueError):
            continue

        entry = existing_map.get(seq)
        if entry is None:
            entry = AttendanceWjxDataEntry(
                activity_id=activity_id,
                seq=seq,
                created_at=now,
            )
            inserted_count += 1
        else:
            updated_count += 1

        entry.submitted_at_text = _normalize_wjx_data_text(row.get("提交答卷时间"))
        entry.duration_text = _normalize_wjx_data_text(row.get("所用时间"))
        entry.source = _normalize_wjx_data_text(row.get("来源"))
        entry.source_detail = _normalize_wjx_data_text(row.get("来源详情"))
        entry.source_ip = _normalize_wjx_data_text(row.get("来自IP"))
        entry.course_name = _normalize_wjx_data_text(row.get("1、所属课程"))
        entry.student_id_text = _normalize_wjx_data_text(row.get("2、学号"))
        entry.student_name = _normalize_wjx_data_text(row.get("3、姓名"))
        entry.correction_request = _normalize_wjx_data_text(row.get("4、修正需求"))
        entry.extra_note = _normalize_wjx_data_text(row.get("5、其他补充说明"))
        entry.raw_row_json = dict(row)
        entry.synced_at = now
        entry.updated_at = now
        session.add(entry)

    session.commit()

    stored_count = int(
        session.exec(
            select(func.count())
            .select_from(AttendanceWjxDataEntry)
            .where(AttendanceWjxDataEntry.activity_id == activity_id)
        ).one()
        or 0
    )

    state.last_max_seq = max(
        int(state.last_max_seq or 0),
        int(sync_result.get("latest_max_id") or 0),
    )
    state.last_incremental_count = int(sync_result.get("incremental_count") or 0)
    state.stored_count = stored_count
    state.last_used_all_pages = bool(sync_result.get("used_all_pages"))
    state.last_sync_at = now
    state.last_success_at = now
    state.last_error = None
    state.execution_device_entry_id = execution_device_entry_id
    state.updated_by_user_id = current_user.id
    state.updated_at = now
    session.add(state)
    session.commit()
    session.refresh(state)

    return state, inserted_count, updated_count

def _build_attendance_wjx_data_page(
    session: Session,
    *,
    template: dict[str, str],
    page: int,
    page_size: int,
    process_status: str | None = None,
    keyword: str | None = None,
    public_view: bool = False,
) -> AttendanceWjxDataPage:
    activity_id = str(template["activity_id"])
    activity_ids = _list_attendance_wjx_data_activity_ids(template)
    normalized_page = max(1, int(page or 1))
    normalized_page_size = min(max(1, int(page_size or 20)), 100)
    offset = (normalized_page - 1) * normalized_page_size

    conditions = [AttendanceWjxDataEntry.activity_id.in_(activity_ids)]

    normalized_status = (process_status or "").strip()
    if normalized_status == "__empty__":
        conditions.append(AttendanceWjxDataEntry.process_status == "")
    elif normalized_status:
        conditions.append(AttendanceWjxDataEntry.process_status == normalized_status)

    normalized_keyword = (keyword or "").strip()
    if normalized_keyword:
        conditions.append(
            or_(
                AttendanceWjxDataEntry.course_name.contains(normalized_keyword),
                AttendanceWjxDataEntry.student_id_text.contains(normalized_keyword),
                AttendanceWjxDataEntry.student_name.contains(normalized_keyword),
                AttendanceWjxDataEntry.correction_request.contains(normalized_keyword),
                AttendanceWjxDataEntry.extra_note.contains(normalized_keyword),
                AttendanceWjxDataEntry.process_note.contains(normalized_keyword),
            )
        )

    total = int(
        session.exec(
            select(func.count())
            .select_from(AttendanceWjxDataEntry)
            .where(*conditions)
        ).one()
        or 0
    )
    statement = (
        select(AttendanceWjxDataEntry)
        .where(*conditions)
        .order_by(
            AttendanceWjxDataEntry.seq.desc(),
            AttendanceWjxDataEntry.synced_at.desc(),
            AttendanceWjxDataEntry.id.desc(),
        )
        .offset(offset)
        .limit(normalized_page_size)
    )
    items = [serialize_attendance_wjx_data_entry(row) for row in session.exec(statement).all()]
    if public_view:
        sanitized_items: list[dict[str, Any]] = []
        for item in items:
            colors = dict(item.get("foreground_colors") or {})
            sanitized_items.append(
                {
                    **item,
                    "source": "",
                    "source_detail": "",
                    "source_ip": "",
                    "student_id_text": "",
                    "student_name": "",
                    "foreground_colors": {
                        "submitted": colors.get("submitted"),
                        "course": colors.get("course"),
                        "student": None,
                    },
                    "correction_request": "",
                    "extra_note": "",
                    "process_status": "",
                    "process_note": "",
                    "match_result": {},
                    "revision_result": {},
                    "raw_row": {},
                }
            )
        items = sanitized_items

    state = None if public_view else session.get(AttendanceWjxDataSyncState, activity_id)
    return AttendanceWjxDataPage(
        items=[AttendanceWjxDataItem.model_validate(item) for item in items],
        total=total,
        page=normalized_page,
        page_size=normalized_page_size,
        sync_state=(
            AttendanceWjxDataSyncStateItem.model_validate(serialize_attendance_wjx_data_sync_state(state))
            if state is not None
            else None
        ),
        template=template,
    )


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
        "view_url": FIXED_WJX_TEMPLATE_VIEW_URL,
        "fill_url": FIXED_WJX_TEMPLATE_FILL_URL,
    }


def _resolve_wjx_template_payload(template_id: Optional[str]) -> dict[str, str]:
    normalized = _normalize_optional_id(template_id)
    if normalized and normalized != FIXED_WJX_TEMPLATE_ID:
        raise HTTPException(status_code=400, detail="当前只支持固定的课程清单问卷")
    return _get_fixed_wjx_template_payload()


def _extract_feedback_course_names(run_result: Any) -> list[str]:
    if not isinstance(run_result, dict):
        return []

    direct_names = _normalize_course_name_list(run_result.get("visible_names"))
    if direct_names:
        return direct_names

    after = run_result.get("after")
    if isinstance(after, dict):
        return _normalize_course_name_list(after.get("visible_names"))
    return []


def _build_attendance_feedback_form_meta(session: Session) -> AttendanceFeedbackFormMeta:
    extra_config = get_attendance_service_extra_config(session)
    return AttendanceFeedbackFormMeta(
        course_names=list(extra_config.get("feedback_course_names") or []),
        course_names_updated_at=extra_config.get("feedback_course_names_updated_at"),
        template=_get_fixed_wjx_template_payload(),
    )


def _resolve_feedback_course_names_or_400(course_names: list[str]) -> list[str]:
    normalized = _normalize_course_name_list(course_names)
    if not normalized:
        raise HTTPException(status_code=400, detail="至少保留一个所属课程")
    return normalized


def _resolve_config_payload(session: Session) -> dict[str, Any]:
    config = get_or_create_attendance_service_config(session)
    extra_config = get_attendance_service_extra_config(session)
    current_account = get_current_account(session, config)
    current_device = get_current_execution_device(session, config)
    return {
        "service": {
            "current_wjx_account_id": config.current_wjx_account_id,
            "execution_device_entry_id": config.execution_device_entry_id,
            "scan_reminder_users": list(extra_config.get("scan_reminder_users") or []),
            "order_lookup_mode": str(extra_config.get("order_lookup_mode") or "browser_only"),
            "order_operation_password_configured": bool(extra_config.get("order_operation_password_configured")),
            "granted_user_ids": list(config.granted_user_ids or []),
            "created_by_user_id": config.created_by_user_id,
            "updated_by_user_id": config.updated_by_user_id,
            "created_at": config.created_at,
            "updated_at": config.updated_at,
        },
        "current_account": serialize_attendance_account(current_account, include_password=False) if current_account else None,
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
        with ensure_ui_automation_thread_context():
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


def _execute_wjx_data_sync_on_entry(entry_snapshot: dict[str, Any], execution_payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(entry_snapshot.get("mode") or "")
    if mode == "local":
        local_device_id = get_device_id()
        if str(entry_snapshot.get("device_id") or "") != local_device_id:
            raise RuntimeError("所选本地执行设备不属于当前节点")
        with ensure_ui_automation_thread_context():
            return execute_wjx_data_sync(**execution_payload)

    server_url = (entry_snapshot.get("server_url") or "").rstrip("/")
    token = str(entry_snapshot.get("token") or "")
    if not server_url or not token:
        raise RuntimeError("远程执行设备缺少后端地址或访问令牌")

    response = requests.post(
        f"{server_url}/api/device-control/attendance/wjx-data/execute",
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


def _execute_order_on_entry(entry_snapshot: dict[str, Any], execution_payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(entry_snapshot.get("mode") or "")
    operation_password = str(execution_payload.get("operation_password") or "")
    order_action_payload = {
        key: value
        for key, value in execution_payload.items()
        if key != "operation_password"
    }
    if mode == "local":
        local_device_id = get_device_id()
        if str(entry_snapshot.get("device_id") or "") != local_device_id:
            raise RuntimeError("所选本地执行设备不属于当前节点")
        with ensure_ui_automation_thread_context():
            with apply_attendance_order_operation_password_env(operation_password):
                return execute_order_action(**order_action_payload)

    server_url = (entry_snapshot.get("server_url") or "").rstrip("/")
    token = str(entry_snapshot.get("token") or "")
    if not server_url or not token:
        raise RuntimeError("远程执行设备缺少后端地址或访问令牌")

    response = requests.post(
        f"{server_url}/api/device-control/attendance/order/execute",
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
            feedback_course_names = _extract_feedback_course_names(result)
            if feedback_course_names:
                try:
                    update_attendance_service_extra_config(
                        session,
                        feedback_course_names=feedback_course_names,
                        feedback_course_names_updated_at=now,
                    )
                except Exception:
                    pass
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
    _: User | None = Depends(
        require_any_feature_access_dependency(
            "attendance.configs",
            "attendance.wjx-templates",
            "attendance.orders",
        )
    ),
):
    if not current_user.is_superuser:
        ensure_can_use_attendance_service(current_user, session)
    return _resolve_config_payload(session)


@public_router.get("/wjx-feedback-form", response_model=AttendanceFeedbackFormMeta)
def get_attendance_feedback_form_meta(
    session: Session = Depends(get_session),
):
    return _build_attendance_feedback_form_meta(session)


@public_router.put("/wjx-feedback-form", response_model=AttendanceFeedbackFormMeta)
def update_attendance_feedback_form_meta(
    payload: AttendanceFeedbackFormMetaUpdateRequest,
    session: Session = Depends(get_session),
):
    update_attendance_service_extra_config(
        session,
        feedback_course_names=_resolve_feedback_course_names_or_400(payload.course_names),
        feedback_course_names_updated_at=time.time(),
    )
    return _build_attendance_feedback_form_meta(session)


@router.put("/config")
def update_attendance_config(
    payload: AttendanceConfigUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.configs")),
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

    if (
        payload.scan_reminder_users is not None
        or payload.order_lookup_mode is not None
        or payload.order_operation_password is not None
        or payload.clear_order_operation_password
    ):
        update_attendance_service_extra_config(
            session,
            scan_reminder_users=list(payload.scan_reminder_users or []) if payload.scan_reminder_users is not None else None,
            order_lookup_mode=payload.order_lookup_mode,
            order_operation_password=payload.order_operation_password,
            clear_order_operation_password=bool(payload.clear_order_operation_password),
        )

    config.updated_by_user_id = current_user.id
    config.updated_at = time.time()
    session.add(config)
    session.commit()
    return _resolve_config_payload(session)


@router.get("/accounts")
def get_attendance_accounts(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.configs")),
):
    ensure_can_manage_attendance_service(current_user)
    return {"items": [serialize_attendance_account(item, include_password=True) for item in list_attendance_accounts(session)]}


@router.post("/accounts")
def create_attendance_account(
    payload: AttendanceAccountCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.configs")),
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
    _: User | None = Depends(require_feature_access_dependency("attendance.configs")),
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
    _: User | None = Depends(require_feature_access_dependency("attendance.configs")),
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
    _: User | None = Depends(require_feature_access_dependency("attendance.configs")),
):
    ensure_can_manage_attendance_service(current_user)
    return {"items": [serialize_attendance_template(item) for item in list_attendance_templates(session)]}


@router.post("/templates")
def create_attendance_template(
    payload: AttendanceTemplateCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.configs")),
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
    _: User | None = Depends(require_feature_access_dependency("attendance.configs")),
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
    _: User | None = Depends(require_feature_access_dependency("attendance.configs")),
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
    _: User | None = Depends(require_feature_access_dependency("attendance.wjx-templates")),
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
    _: User | None = Depends(require_feature_access_dependency("attendance.wjx-templates")),
):
    ensure_can_use_attendance_service(current_user, session)
    run = get_attendance_run_or_404(session, run_id)
    return serialize_attendance_run(run)


@public_router.post("/wjx-feedback/submissions", response_model=AttendanceWjxDataItem)
def submit_attendance_feedback(
    payload: AttendanceFeedbackSubmitRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    entry = _persist_attendance_feedback_submission(
        session,
        payload=payload,
        request=request,
    )
    return AttendanceWjxDataItem.model_validate(serialize_attendance_wjx_data_entry(entry))

@router.post("/wjx-data/sync")
def sync_attendance_wjx_data(
    payload: AttendanceWjxDataSyncRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.wjx-data")),
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

    state = _get_attendance_wjx_data_state(session, template=template, actor=current_user)
    execution_payload = {
        "login_username": account.login_username,
        "password": password_plain,
        "activity_id": template["activity_id"],
        "exist_max_id": state.last_max_seq,
    }

    try:
        sync_result = _execute_wjx_data_sync_on_entry(
            {
                **serialize_user_device(entry),
                "token": entry.token,
            },
            execution_payload,
        )
    except WjxDataSyncError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        now = time.time()
        state.last_sync_at = now
        state.last_error = str(exc)
        state.execution_device_entry_id = entry.entry_id
        state.updated_by_user_id = current_user.id
        state.updated_at = now
        session.add(state)
        session.commit()
        raise HTTPException(
            status_code=400 if isinstance(exc, RuntimeError) else 500,
            detail=str(exc),
        ) from exc

    state, inserted_count, updated_count = _persist_wjx_data_sync_result(
        session,
        current_user=current_user,
        template=template,
        execution_device_entry_id=entry.entry_id,
        sync_result=sync_result,
    )

    if payload.persist_global_selection:
        config.current_wjx_account_id = account.id
        config.execution_device_entry_id = entry.entry_id
        config.updated_by_user_id = current_user.id
        config.updated_at = time.time()
        session.add(config)
        session.commit()

    return {
        "template": template,
        "execution_device_entry_id": entry.entry_id,
        "inserted_count": inserted_count,
        "updated_count": updated_count,
        "latest_max_seq": int(sync_result.get("latest_max_id") or 0),
        "recent_count": int(sync_result.get("recent_count") or 0),
        "fetched_count": int(sync_result.get("fetched_count") or 0),
        "incremental_count": int(sync_result.get("incremental_count") or 0),
        "used_all_pages": bool(sync_result.get("used_all_pages")),
        "sync_state": serialize_attendance_wjx_data_sync_state(state),
    }


@router.get("/wjx-data", response_model=AttendanceWjxDataPage)
def list_attendance_wjx_data(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    process_status: Optional[str] = None,
    keyword: Optional[str] = None,
    template_id: Optional[str] = None,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    template = _resolve_wjx_template_payload(template_id)
    can_use_full_view = bool(
        current_user
        and is_feature_access_allowed(
            session,
            feature_key="attendance.wjx-data",
            current_user=current_user,
        )
    )

    if can_use_full_view:
        ensure_can_use_attendance_service(current_user, session)
        return _build_attendance_wjx_data_page(
            session,
            template=template,
            page=page,
            page_size=page_size,
            process_status=process_status,
            keyword=keyword,
        )

    return _build_attendance_wjx_data_page(
        session,
        template=template,
        page=page,
        page_size=page_size,
        process_status="__empty__",
        keyword=None,
        public_view=True,
    )


@router.patch("/wjx-data/{entry_id}", response_model=AttendanceWjxDataItem)
def update_attendance_wjx_data(
    entry_id: int,
    payload: AttendanceWjxDataUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.wjx-data")),
):
    ensure_can_use_attendance_service(current_user, session)
    entry = get_attendance_wjx_data_entry_or_404(session, entry_id)

    if payload.process_status is not None:
        entry.process_status = (payload.process_status or "").strip()
    if payload.process_note is not None:
        entry.process_note = (payload.process_note or "").strip()
    if payload.match_result is not None:
        entry.match_result_json = dict(payload.match_result)
    if payload.revision_result is not None:
        entry.revision_result_json = dict(payload.revision_result)

    entry.updated_at = time.time()
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return AttendanceWjxDataItem.model_validate(serialize_attendance_wjx_data_entry(entry))


@router.delete("/wjx-data/{entry_id}")
def delete_attendance_wjx_data(
    entry_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.wjx-data")),
):
    ensure_can_use_attendance_service(current_user, session)
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="只有超级管理员可以删除问卷数据")
    entry = get_attendance_wjx_data_entry_or_404(session, entry_id)
    activity_id = str(entry.activity_id)
    seq = int(entry.seq)
    now = time.time()

    state = _remember_wjx_data_seq_history(
        session,
        activity_id=activity_id,
        seq=seq,
        actor=current_user,
    )
    session.delete(entry)
    session.flush()
    state.stored_count = _count_attendance_wjx_data_entries(session, activity_id=activity_id)
    state.updated_at = now
    state.updated_by_user_id = current_user.id
    session.add(state)
    session.commit()

    return {
        "deleted": True,
        "entry_id": entry_id,
        "activity_id": activity_id,
        "seq": seq,
    }


@router.post("/order-execute")
def execute_attendance_order(
    payload: AttendanceOrderExecuteRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.orders")),
):
    current_user = ensure_can_use_attendance_service(current_user, session)
    config = get_or_create_attendance_service_config(session)
    extra_config = get_attendance_service_extra_config(session)
    order_operation_password = ""
    if payload.action == "refund":
        try:
            order_operation_password = str(
                payload.operation_password or get_attendance_service_order_operation_password(session) or ""
            )
        except AttendanceServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    entry = _resolve_run_device(
        session,
        config,
        execution_device_entry_id=payload.execution_device_entry_id,
        current_user=current_user,
    )

    execution_payload = {
        "action": payload.action,
        "rows": list(payload.rows or []),
        "login_users": list(payload.login_users or extra_config.get("scan_reminder_users") or []),
        "lookup_mode": str(payload.order_lookup_mode or extra_config.get("order_lookup_mode") or "browser_only"),
    }
    if order_operation_password:
        execution_payload["operation_password"] = order_operation_password

    try:
        result = _execute_order_on_entry(
            {
                **serialize_user_device(entry),
                "token": entry.token,
            },
            execution_payload,
        )
        result = _normalize_attendance_order_execution_result(result)
    except OrderAutomationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if payload.persist_global_selection:
        config.execution_device_entry_id = entry.entry_id
        config.updated_by_user_id = current_user.id
        config.updated_at = time.time()
        session.add(config)
        session.commit()

    if payload.action == "refund":
        try:
            _persist_refund_history(
                session,
                current_user=current_user,
                execution_device_entry_id=entry.entry_id,
                rows=list(result.get("rows") or []),
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"退款已执行，但写入历史失败：{exc}") from exc

    return {
        "execution_device_entry_id": entry.entry_id,
        **result,
    }


@router.get("/order-refund-history", response_model=AttendanceOrderRefundHistoryPage)
def list_attendance_order_refund_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.orders")),
):
    ensure_can_use_attendance_service(current_user, session)
    return _build_order_refund_history_page(session, page=page, page_size=page_size)
