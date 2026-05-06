from __future__ import annotations

import time
import re
from datetime import datetime
from typing import Any, Literal, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from pyxllib.cv.rgbfmt import hash_text_to_hex_color
from sqlalchemy import func, or_
from sqlmodel import Session, select

from backend.core.attendance_service import (
    AttendanceServiceError,
    apply_attendance_order_operation_password_env,
    encrypt_attendance_secret,
    ensure_can_manage_attendance_service,
    ensure_can_use_attendance_service,
    get_attendance_account_or_404,
    get_attendance_wjx_data_entry_or_404,
    get_attendance_service_order_operation_password,
    get_current_account,
    get_current_execution_device,
    get_or_create_attendance_service_config,
    get_or_create_attendance_wjx_data_sync_state,
    get_attendance_service_extra_config,
    get_user_device_or_404,
    list_attendance_accounts,
    serialize_attendance_order_refund_history,
    serialize_attendance_account,
    serialize_attendance_wjx_data_entry,
    serialize_attendance_wjx_data_sync_state,
    serialize_user_device,
    update_attendance_service_extra_config,
)
from backend.core.attendance_order import (
    OrderAutomationError,
    execute_order_action,
    query_order_refund_details,
)
from backend.core.auth import get_current_user_from_token
from backend.core.device import get_device_id
from backend.core.feature_access_guard import (
    require_any_feature_access_dependency,
    require_feature_access_dependency,
)
from backend.core.ui_automation import ensure_ui_automation_thread_context
from backend.db import get_session
from backend.models import (
    AttendanceAccountAsset,
    AttendanceOrderRefundHistory,
    AttendanceWjxDataEntry,
    AttendanceWjxDataSyncState,
    ResourceAccessGrant,
    SheetDocument,
    User,
    UserDevice,
    WorkbookDocument,
    WorkbookSheetLink,
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
ATTENDANCE_WJX_SUBMITTED_AT_DISPLAY_FORMAT = 'case(is_current_year, "mm/dd hh:mm", "yyyy/mm/dd hh:mm")'
ATTENDANCE_WJX_DATA_WORKBOOK_ID = 2
ATTENDANCE_WJX_DATA_SHEET_TITLE = "问卷数据"
ATTENDANCE_WJX_DATA_OWNER_TYPE = "attendance_questionnaire"
ATTENDANCE_WJX_DATA_OWNER_KEY = "wjx-data"
ATTENDANCE_WJX_DATA_SHEET_KEY = "data"
ATTENDANCE_WJX_DATA_COLUMNS = [
    "序号",
    "提交时间",
    "来源",
    "课程",
    "学号",
    "姓名",
    "修正需求",
    "补充说明",
    "处理状态",
]
FEEDBACK_COURSE_SOURCE_SHEET_ID = 4
NOTE_SHEET_PUBLIC_RESOURCE_TYPE = "sheet"
NOTE_SHEET_PUBLIC_SUBJECT_TYPE = "anonymous"
NOTE_SHEET_PUBLIC_SUBJECT_KEY = "anonymous"
NOTE_SHEET_PUBLIC_VIEWER_ROLE = "viewer"
FEEDBACK_COURSE_FIELD_BINDINGS: dict[str, tuple[str, int]] = {
    "course_name": ("课程名称", 1),
    "online_sheet": ("在线考勤表", 2),
    "completed_date": ("考勤实际完成结点", 10),
}
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


class AttendanceOrderExecuteRequest(BaseModel):
    action: Literal["inspect", "refund"]
    rows: list[dict[str, Any]] = Field(default_factory=list)
    execution_device_entry_id: Optional[str] = None
    login_users: list[str] = Field(default_factory=list)
    order_lookup_mode: Optional[Literal["hybrid", "db_only", "browser_only"]] = None
    persist_global_selection: bool = True
    operation_password: Optional[str] = None


class AttendanceOrderRefundDetailRequest(BaseModel):
    order_id: str
    query_type: Literal["auto", "pay_order", "merchant_order", "refund_id"] = "auto"
    execution_device_entry_id: Optional[str] = None
    login_users: list[str] = Field(default_factory=list)
    persist_global_selection: bool = True


class AttendanceOrderRefundDetailItem(BaseModel):
    wechat_order_id: str = ""
    merchant_order_id: str = ""
    refund_id: str = ""
    refund_amount: float = 0.0
    refund_status: str = ""
    applicant: str = ""
    submitted_at: str = ""
    completed_at: str = ""


class AttendanceOrderRefundDetailSummary(BaseModel):
    order_id: str
    matched_order_id: str
    query_type: Literal["auto", "pay_order", "merchant_order", "refund_id"]
    row_count: int = 0
    refund_amount_total: float = 0.0
    wechat_order_id: str = ""
    merchant_order_id: str = ""
    refund_statuses: list[str] = Field(default_factory=list)


class AttendanceOrderRefundDetailResponse(BaseModel):
    execution_device_entry_id: str
    summary: AttendanceOrderRefundDetailSummary
    rows: list[AttendanceOrderRefundDetailItem] = Field(default_factory=list)


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


class AttendanceWjxDataSheetLocation(BaseModel):
    workbook_id: int
    sheet_id: int
    path: str


class AttendanceFeedbackCourseOption(BaseModel):
    name: str
    attendance_sheet_url: str = ""


class AttendanceFeedbackFormMeta(BaseModel):
    course_names: list[str] = Field(default_factory=list)
    course_options: list[AttendanceFeedbackCourseOption] = Field(default_factory=list)
    course_names_updated_at: Optional[float] = None
    data_sheet_url: str = ""


class AttendanceSheetDocumentResponse(BaseModel):
    id: int
    scope: str
    owner_type: str
    owner_key: str
    sheet_key: str
    title: str
    engine: str
    document_json: dict[str, Any] = Field(default_factory=dict)
    version: int
    created_by_user_id: Optional[int] = None
    updated_by_user_id: Optional[int] = None
    created_at: float
    updated_at: float


class AttendanceSheetDocumentUpsertRequest(BaseModel):
    owner_type: str
    owner_key: str
    sheet_key: str
    title: str = ""
    engine: Literal["handsontable"] = "handsontable"
    document_json: dict[str, Any] = Field(default_factory=dict)


def _normalize_optional_id(value: Optional[str]) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def _normalize_sheet_locator_part(value: str, *, field_name: str, lower: bool = False) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} 不能为空")
    return normalized.lower() if lower else normalized


def _normalize_sheet_numeric_id(value: str) -> int:
    try:
        numeric_id = int(str(value or "").strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="sheet_id 非法") from exc
    if numeric_id <= 0:
        raise HTTPException(status_code=400, detail="sheet_id 非法")
    return numeric_id


def _get_next_sheet_numeric_id(session: Session) -> int:
    current_max = session.exec(
        select(SheetDocument.numeric_id)
        .where(SheetDocument.numeric_id.is_not(None))
        .order_by(SheetDocument.numeric_id.desc())
    ).first()
    return max(int(current_max or 0), 0) + 1


def _get_next_workbook_link_order(session: Session, workbook_id: str) -> int:
    current_max = session.exec(
        select(func.max(WorkbookSheetLink.order_index))
        .where(WorkbookSheetLink.workbook_id == workbook_id)
    ).one()
    return max(int(current_max or 0), 0) + 10


def _build_public_note_sheet_url(document: SheetDocument | None) -> str:
    if document is None or document.numeric_id is None:
        return ""
    return f"/sheet/{int(document.numeric_id)}"


def _ensure_note_sheet_anonymous_viewer(session: Session, document: SheetDocument | None) -> str:
    if document is None:
        return ""

    grant = session.exec(
        select(ResourceAccessGrant)
        .where(ResourceAccessGrant.resource_type == NOTE_SHEET_PUBLIC_RESOURCE_TYPE)
        .where(ResourceAccessGrant.resource_id == document.id)
        .where(ResourceAccessGrant.subject_key == NOTE_SHEET_PUBLIC_SUBJECT_KEY)
    ).first()
    now = time.time()
    mutated = False
    if grant is None:
        grant = ResourceAccessGrant(
            resource_type=NOTE_SHEET_PUBLIC_RESOURCE_TYPE,
            resource_id=document.id,
            subject_key=NOTE_SHEET_PUBLIC_SUBJECT_KEY,
            subject_type=NOTE_SHEET_PUBLIC_SUBJECT_TYPE,
            role=NOTE_SHEET_PUBLIC_VIEWER_ROLE,
            created_at=now,
            updated_at=now,
        )
        mutated = True
    elif (
        grant.role != NOTE_SHEET_PUBLIC_VIEWER_ROLE
        or grant.subject_type != NOTE_SHEET_PUBLIC_SUBJECT_TYPE
        or grant.subject_user_id is not None
    ):
        grant.role = NOTE_SHEET_PUBLIC_VIEWER_ROLE
        grant.subject_type = NOTE_SHEET_PUBLIC_SUBJECT_TYPE
        grant.subject_user_id = None
        grant.updated_at = now
        mutated = True

    if mutated:
        session.add(grant)
        session.commit()
    return _build_public_note_sheet_url(document)


def _create_default_attendance_wjx_sheet_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "columns": list(ATTENDANCE_WJX_DATA_COLUMNS),
        "rows": [],
        "column_configs": {
            "序号": {"value_type": "number", "allow_empty": False, "display_mode": "single_line"},
            "提交时间": {
                "value_type": "date",
                "display_format": ATTENDANCE_WJX_SUBMITTED_AT_DISPLAY_FORMAT,
                "display_mode": "single_line",
            },
            "来源": {"display_mode": "single_line"},
            "学号": {"display_mode": "single_line"},
            "姓名": {"display_mode": "single_line"},
        },
        "view_settings": {
            "show_row_numbers": True,
            "row_marker_numbering": "global",
            "show_column_markers": True,
            "column_marker_style": "letters",
            "pagination": {
                "enabled": True,
                "page_size": 100,
            },
        },
    }


def _normalize_attendance_wjx_sheet_cell(value: Any) -> str:
    return _normalize_wjx_data_text(value)


def _normalize_attendance_wjx_sheet_columns(value: Any) -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()
    if isinstance(value, list):
        for item in value:
            header = str(item or "").strip()
            if header and header not in seen:
                columns.append(header)
                seen.add(header)
    for header in ATTENDANCE_WJX_DATA_COLUMNS:
        if header not in seen:
            columns.append(header)
            seen.add(header)
    return columns


def _normalize_attendance_wjx_sheet_row(row: Any, columns: list[str]) -> list[str]:
    if isinstance(row, dict):
        values = [_normalize_attendance_wjx_sheet_cell(row.get(column, "")) for column in columns]
    elif isinstance(row, list):
        values = [_normalize_attendance_wjx_sheet_cell(cell) for cell in row]
    else:
        values = []
    if len(values) < len(columns):
        values.extend([""] * (len(columns) - len(values)))
    return values[:len(columns)]


def _normalize_attendance_wjx_sheet_document(value: Any) -> dict[str, Any]:
    source = dict(value) if isinstance(value, dict) else {}
    columns = _normalize_attendance_wjx_sheet_columns(source.get("columns"))
    rows_source = source.get("rows")
    rows = [
        _normalize_attendance_wjx_sheet_row(row, columns)
        for row in (rows_source if isinstance(rows_source, list) else [])
    ]

    default_document = _create_default_attendance_wjx_sheet_document()
    column_configs = dict(source.get("column_configs") if isinstance(source.get("column_configs"), dict) else {})
    for header, config in default_document["column_configs"].items():
        if not isinstance(column_configs.get(header), dict):
            column_configs[header] = dict(config)
            continue
        merged_config = dict(config)
        merged_config.update(column_configs[header])
        column_configs[header] = merged_config

    view_settings = dict(source.get("view_settings") if isinstance(source.get("view_settings"), dict) else {})
    view_settings.setdefault("show_row_numbers", True)
    view_settings.setdefault("row_marker_numbering", "global")
    view_settings.setdefault("show_column_markers", True)
    view_settings.setdefault("column_marker_style", "letters")
    pagination = dict(view_settings.get("pagination") if isinstance(view_settings.get("pagination"), dict) else {})
    pagination.setdefault("enabled", True)
    pagination.setdefault("page_size", 100)
    view_settings["pagination"] = pagination

    return {
        **source,
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "column_configs": column_configs,
        "view_settings": view_settings,
    }


def _get_attendance_wjx_sheet_column_index(columns: list[str], header: str) -> int:
    try:
        return columns.index(header)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"问卷数据表缺少字段：{header}") from exc


def _get_attendance_wjx_sheet_cell(row: list[str], columns: list[str], header: str) -> str:
    index = _get_attendance_wjx_sheet_column_index(columns, header)
    return row[index] if index < len(row) else ""


def _set_attendance_wjx_sheet_cell_link(
    document_json: dict[str, Any],
    *,
    row_index: int,
    column_index: int,
    url: str,
) -> bool:
    cell_meta = dict(document_json.get("cell_meta") if isinstance(document_json.get("cell_meta"), dict) else {})
    key = f"{row_index}:{column_index}"
    entry = dict(cell_meta.get(key) if isinstance(cell_meta.get(key), dict) else {})
    normalized_url = _normalize_attendance_wjx_sheet_cell(url)

    if normalized_url:
        current_link = entry.get("link") if isinstance(entry.get("link"), dict) else {}
        if _normalize_attendance_wjx_sheet_cell(current_link.get("url")) == normalized_url:
            return False
        entry["link"] = {"url": normalized_url}
        cell_meta[key] = entry
    else:
        if "link" not in entry:
            return False
        entry.pop("link", None)
        if entry:
            cell_meta[key] = entry
        else:
            cell_meta.pop(key, None)

    document_json["cell_meta"] = cell_meta
    return True


def _parse_attendance_wjx_sheet_cell_meta_key(key: Any) -> tuple[int, int] | None:
    if not isinstance(key, str):
        return None
    row_text, separator, column_text = key.partition(":")
    if separator != ":" or not row_text.isdigit() or not column_text.isdigit():
        return None
    return int(row_text), int(column_text)


def _shift_attendance_wjx_sheet_cell_meta_rows_for_insert(
    document_json: dict[str, Any],
    *,
    insert_index: int,
    amount: int,
) -> None:
    cell_meta = document_json.get("cell_meta")
    if not isinstance(cell_meta, dict) or amount <= 0:
        return

    shifted: dict[str, Any] = {}
    for key, value in cell_meta.items():
        position = _parse_attendance_wjx_sheet_cell_meta_key(key)
        if position is None:
            shifted[str(key)] = value
            continue

        row_index, column_index = position
        next_row_index = row_index + amount if row_index >= insert_index else row_index
        shifted[f"{next_row_index}:{column_index}"] = value
    document_json["cell_meta"] = shifted


def _sync_attendance_wjx_sheet_course_link_for_row(
    document_json: dict[str, Any],
    *,
    row_index: int,
    row: list[str],
    columns: list[str],
    course_link_map: dict[str, str] | None,
) -> bool:
    if course_link_map is None:
        return False

    course_column_index = _get_attendance_wjx_sheet_column_index(columns, "课程")
    course_name = _normalize_attendance_wjx_sheet_cell(row[course_column_index] if course_column_index < len(row) else "")
    return _set_attendance_wjx_sheet_cell_link(
        document_json,
        row_index=row_index,
        column_index=course_column_index,
        url=course_link_map.get(course_name, ""),
    )


def _apply_attendance_wjx_sheet_course_links(
    document_json: dict[str, Any],
    course_link_map: dict[str, str] | None,
) -> tuple[dict[str, Any], bool]:
    document = _normalize_attendance_wjx_sheet_document(document_json)
    if course_link_map is None:
        return document, False

    columns = list(document["columns"])
    changed = False
    for row_index, row in enumerate(document["rows"]):
        changed = _sync_attendance_wjx_sheet_course_link_for_row(
            document,
            row_index=row_index,
            row=row,
            columns=columns,
            course_link_map=course_link_map,
        ) or changed
    return document, changed


def _set_attendance_wjx_sheet_cell(row: list[str], columns: list[str], header: str, value: Any) -> None:
    index = _get_attendance_wjx_sheet_column_index(columns, header)
    if len(row) < len(columns):
        row.extend([""] * (len(columns) - len(row)))
    row[index] = _normalize_attendance_wjx_sheet_cell(value)


def _parse_attendance_wjx_sheet_seq(value: Any) -> int | None:
    text = _normalize_attendance_wjx_sheet_cell(value)
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        return int(float(text))
    return None


def _find_attendance_wjx_sheet_row_index(document_json: dict[str, Any], seq: int) -> int | None:
    document = _normalize_attendance_wjx_sheet_document(document_json)
    columns = list(document["columns"])
    rows = list(document["rows"])
    for index, row in enumerate(rows):
        current_seq = _parse_attendance_wjx_sheet_seq(
            _get_attendance_wjx_sheet_cell(row, columns, "序号")
        )
        if current_seq == seq:
            return index
    return None


def _get_attendance_wjx_sheet_insert_index(rows: list[list[str]], columns: list[str], seq: int) -> int:
    first_non_seq_index: int | None = None
    for index, row in enumerate(rows):
        current_seq = _parse_attendance_wjx_sheet_seq(
            _get_attendance_wjx_sheet_cell(row, columns, "序号")
        )
        if current_seq is None:
            if first_non_seq_index is None:
                first_non_seq_index = index
            continue
        if seq > current_seq:
            return index
    return first_non_seq_index if first_non_seq_index is not None else len(rows)


def _get_next_attendance_wjx_sheet_seq(document_json: dict[str, Any]) -> int:
    document = _normalize_attendance_wjx_sheet_document(document_json)
    columns = list(document["columns"])
    max_seq = LOCAL_FEEDBACK_SEQ_START - 1
    for row in document["rows"]:
        seq = _parse_attendance_wjx_sheet_seq(_get_attendance_wjx_sheet_cell(row, columns, "序号"))
        if seq is not None:
            max_seq = max(max_seq, seq)
    return max_seq + 1


def _entry_to_attendance_wjx_sheet_values(
    entry: AttendanceWjxDataEntry,
    *,
    process_status: str | None = None,
) -> dict[str, Any]:
    return {
        "序号": entry.seq,
        "提交时间": entry.submitted_at_text,
        "来源": entry.source,
        "课程": entry.course_name,
        "学号": entry.student_id_text,
        "姓名": entry.student_name,
        "修正需求": entry.correction_request,
        "补充说明": entry.extra_note,
        "处理状态": process_status if process_status is not None else (entry.process_note or entry.process_status),
    }


def _wjx_raw_row_to_attendance_wjx_sheet_values(row: dict[str, Any]) -> dict[str, Any] | None:
    seq = _parse_attendance_wjx_sheet_seq(row.get("序号"))
    if seq is None:
        return None
    return {
        "序号": seq,
        "提交时间": _normalize_wjx_data_text(row.get("提交答卷时间")),
        "来源": _normalize_wjx_data_text(row.get("来源")),
        "课程": _normalize_wjx_data_text(row.get("1、所属课程")),
        "学号": _normalize_wjx_data_text(row.get("2、学号")),
        "姓名": _normalize_wjx_data_text(row.get("3、姓名")),
        "修正需求": _normalize_wjx_data_text(row.get("4、修正需求")),
        "补充说明": _normalize_wjx_data_text(row.get("5、其他补充说明")),
        "处理状态": "",
    }


def _upsert_attendance_wjx_sheet_values(
    document_json: dict[str, Any],
    values: dict[str, Any],
    *,
    preserve_process_status: bool = True,
    course_link_map: dict[str, str] | None = None,
) -> tuple[dict[str, Any], bool, bool]:
    seq = _parse_attendance_wjx_sheet_seq(values.get("序号"))
    if seq is None:
        return _normalize_attendance_wjx_sheet_document(document_json), False, False

    document = _normalize_attendance_wjx_sheet_document(document_json)
    columns = list(document["columns"])
    rows = list(document["rows"])
    row_index = _find_attendance_wjx_sheet_row_index(document, seq)
    inserted = row_index is None
    if inserted:
        row = [""] * len(columns)
        row_index = _get_attendance_wjx_sheet_insert_index(rows, columns, seq)
        rows.insert(row_index, row)
        _shift_attendance_wjx_sheet_cell_meta_rows_for_insert(
            document,
            insert_index=row_index,
            amount=1,
        )
    else:
        row = list(rows[row_index])

    original_row = list(row)
    existing_process_status = _get_attendance_wjx_sheet_cell(row, columns, "处理状态")
    for header in ATTENDANCE_WJX_DATA_COLUMNS:
        if header == "处理状态" and preserve_process_status and existing_process_status:
            continue
        if header in values:
            _set_attendance_wjx_sheet_cell(row, columns, header, values.get(header))

    rows[row_index] = row
    document["rows"] = rows
    link_changed = _sync_attendance_wjx_sheet_course_link_for_row(
        document,
        row_index=row_index,
        row=row,
        columns=columns,
        course_link_map=course_link_map,
    )
    return document, inserted, inserted or row != original_row or link_changed


def _remove_attendance_wjx_sheet_row(
    document_json: dict[str, Any],
    *,
    seq: int,
) -> tuple[dict[str, Any], bool]:
    document = _normalize_attendance_wjx_sheet_document(document_json)
    row_index = _find_attendance_wjx_sheet_row_index(document, seq)
    if row_index is None:
        return document, False
    rows = list(document["rows"])
    rows.pop(row_index)
    document["rows"] = rows
    return document, True


def _get_attendance_wjx_data_workbook(session: Session) -> WorkbookDocument | None:
    return session.exec(
        select(WorkbookDocument).where(WorkbookDocument.numeric_id == ATTENDANCE_WJX_DATA_WORKBOOK_ID)
    ).first()


def _find_attendance_wjx_sheet_document(
    session: Session,
    workbook: WorkbookDocument,
) -> SheetDocument | None:
    document = session.exec(
        select(SheetDocument)
        .where(SheetDocument.scope == "notes")
        .where(SheetDocument.owner_type == ATTENDANCE_WJX_DATA_OWNER_TYPE)
        .where(SheetDocument.owner_key == ATTENDANCE_WJX_DATA_OWNER_KEY)
        .where(SheetDocument.sheet_key == ATTENDANCE_WJX_DATA_SHEET_KEY)
    ).first()
    if document is not None:
        return document

    links = session.exec(
        select(WorkbookSheetLink).where(WorkbookSheetLink.workbook_id == workbook.id)
    ).all()
    if not links:
        return None

    linked_sheet_ids = [link.sheet_id for link in links]
    return session.exec(
        select(SheetDocument)
        .where(SheetDocument.id.in_(linked_sheet_ids))
        .where(SheetDocument.title == ATTENDANCE_WJX_DATA_SHEET_TITLE)
    ).first()


def _ensure_attendance_wjx_sheet_document(
    session: Session,
    *,
    create: bool,
) -> SheetDocument | None:
    workbook = _get_attendance_wjx_data_workbook(session)
    if workbook is None:
        return None

    document = _find_attendance_wjx_sheet_document(session, workbook)
    now = time.time()
    mutated = False
    created_document = False
    owner_user_id = workbook.owner_user_id or workbook.created_by_user_id or workbook.updated_by_user_id

    if document is None:
        if not create:
            return None
        document = SheetDocument(
            numeric_id=_get_next_sheet_numeric_id(session),
            scope="notes",
            owner_type=ATTENDANCE_WJX_DATA_OWNER_TYPE,
            owner_key=ATTENDANCE_WJX_DATA_OWNER_KEY,
            sheet_key=ATTENDANCE_WJX_DATA_SHEET_KEY,
            title=ATTENDANCE_WJX_DATA_SHEET_TITLE,
            engine="handsontable",
            document_json=_create_default_attendance_wjx_sheet_document(),
            version=1,
            owner_user_id=owner_user_id,
            created_by_user_id=owner_user_id,
            updated_by_user_id=owner_user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(document)
        session.flush()
        mutated = True
        created_document = True
    else:
        if document.numeric_id is None:
            document.numeric_id = _get_next_sheet_numeric_id(session)
            mutated = True
        if document.owner_type != ATTENDANCE_WJX_DATA_OWNER_TYPE:
            document.owner_type = ATTENDANCE_WJX_DATA_OWNER_TYPE
            mutated = True
        if document.owner_key != ATTENDANCE_WJX_DATA_OWNER_KEY:
            document.owner_key = ATTENDANCE_WJX_DATA_OWNER_KEY
            mutated = True
        if document.sheet_key != ATTENDANCE_WJX_DATA_SHEET_KEY:
            document.sheet_key = ATTENDANCE_WJX_DATA_SHEET_KEY
            mutated = True
        if document.owner_user_id is None and owner_user_id is not None:
            document.owner_user_id = owner_user_id
            mutated = True

    link = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == workbook.id)
        .where(WorkbookSheetLink.sheet_id == document.id)
    ).first()
    if link is None:
        session.add(
            WorkbookSheetLink(
                workbook_id=workbook.id,
                sheet_id=document.id,
                order_index=_get_next_workbook_link_order(session, workbook.id),
                created_at=now,
            )
        )
        workbook.updated_by_user_id = owner_user_id
        workbook.updated_at = now
        session.add(workbook)
        mutated = True

    normalized_document = _normalize_attendance_wjx_sheet_document(dict(document.document_json or {}))
    if created_document and not normalized_document["rows"]:
        normalized_document = _seed_attendance_wjx_sheet_from_entries(session, normalized_document)
    normalized_document, _links_changed = _sync_attendance_wjx_sheet_course_links(session, normalized_document)
    if dict(document.document_json or {}) != normalized_document:
        document.document_json = normalized_document
        document.version = max(int(document.version or 1), 1) + 1 if not mutated else int(document.version or 1)
        document.updated_by_user_id = owner_user_id
        document.updated_at = now
        mutated = True

    if mutated:
        session.add(document)
        session.commit()
        session.refresh(document)
    _ensure_note_sheet_anonymous_viewer(session, document)
    return document


def _seed_attendance_wjx_sheet_from_entries(
    session: Session,
    document_json: dict[str, Any],
) -> dict[str, Any]:
    activity_ids = [FIXED_WJX_TEMPLATE_ACTIVITY_ID, LOCAL_FEEDBACK_ACTIVITY_ID]
    entries = session.exec(
        select(AttendanceWjxDataEntry)
        .where(AttendanceWjxDataEntry.activity_id.in_(activity_ids))
        .order_by(AttendanceWjxDataEntry.seq.asc(), AttendanceWjxDataEntry.id.asc())
    ).all()
    next_document = _normalize_attendance_wjx_sheet_document(document_json)
    course_link_map = _get_feedback_course_link_map_from_summary_sheet(session)
    for entry in entries:
        next_document, _inserted, _changed = _upsert_attendance_wjx_sheet_values(
            next_document,
            _entry_to_attendance_wjx_sheet_values(entry),
            preserve_process_status=False,
            course_link_map=course_link_map,
        )
    return next_document


def _persist_attendance_wjx_sheet_document(
    session: Session,
    document: SheetDocument,
    next_document: dict[str, Any],
    *,
    actor: User | None = None,
) -> None:
    normalized = _normalize_attendance_wjx_sheet_document(next_document)
    if dict(document.document_json or {}) == normalized:
        return
    document.document_json = normalized
    document.version = max(int(document.version or 1), 1) + 1
    document.updated_by_user_id = actor.id if actor is not None else document.owner_user_id
    document.updated_at = time.time()
    session.add(document)
    session.commit()
    session.refresh(document)


def _upsert_attendance_wjx_sheet_entry(
    session: Session,
    entry: AttendanceWjxDataEntry,
    *,
    actor: User | None = None,
    preserve_process_status: bool = True,
) -> None:
    document = _ensure_attendance_wjx_sheet_document(session, create=True)
    if document is None:
        return
    course_link_map = _get_feedback_course_link_map_from_summary_sheet(session)
    next_document, _inserted, changed = _upsert_attendance_wjx_sheet_values(
        dict(document.document_json or {}),
        _entry_to_attendance_wjx_sheet_values(entry),
        preserve_process_status=preserve_process_status,
        course_link_map=course_link_map,
    )
    if changed:
        _persist_attendance_wjx_sheet_document(session, document, next_document, actor=actor)


def _upsert_attendance_wjx_sheet_raw_rows(
    session: Session,
    *,
    rows: list[dict[str, Any]],
    actor: User | None = None,
) -> None:
    document = _ensure_attendance_wjx_sheet_document(session, create=True)
    if document is None:
        return

    next_document = dict(document.document_json or {})
    changed = False
    course_link_map = _get_feedback_course_link_map_from_summary_sheet(session)
    for row in rows:
        values = _wjx_raw_row_to_attendance_wjx_sheet_values(row)
        if values is None:
            continue
        next_document, _inserted, row_changed = _upsert_attendance_wjx_sheet_values(
            next_document,
            values,
            preserve_process_status=True,
            course_link_map=course_link_map,
        )
        changed = changed or row_changed
    if changed:
        _persist_attendance_wjx_sheet_document(session, document, next_document, actor=actor)


def _build_attendance_wjx_sheet_foreground_colors(item: dict[str, Any]) -> dict[str, str | None]:
    def resolve(value: str) -> str | None:
        normalized = _normalize_attendance_wjx_sheet_cell(value)
        if not normalized:
            return None
        return hash_text_to_hex_color(normalized, tone="dark")

    submitted = _normalize_attendance_wjx_sheet_cell(item.get("submitted_at_text"))
    submitted_day = submitted.split()[0] if submitted else ""
    return {
        "submitted": resolve(submitted_day),
        "course": resolve(_normalize_attendance_wjx_sheet_cell(item.get("course_name"))),
        "student": resolve(_normalize_attendance_wjx_sheet_cell(item.get("student_name"))),
    }


def _serialize_attendance_wjx_sheet_row(
    row: list[str],
    columns: list[str],
    *,
    updated_at: float,
) -> dict[str, Any] | None:
    seq = _parse_attendance_wjx_sheet_seq(_get_attendance_wjx_sheet_cell(row, columns, "序号"))
    if seq is None:
        return None

    source = _get_attendance_wjx_sheet_cell(row, columns, "来源")
    process_status = _get_attendance_wjx_sheet_cell(row, columns, "处理状态")
    item = {
        "id": seq,
        "activity_id": LOCAL_FEEDBACK_ACTIVITY_ID if source == LOCAL_FEEDBACK_SOURCE else FIXED_WJX_TEMPLATE_ACTIVITY_ID,
        "seq": seq,
        "submitted_at_text": _get_attendance_wjx_sheet_cell(row, columns, "提交时间"),
        "duration_text": "",
        "source": source,
        "source_detail": "",
        "source_ip": "",
        "course_name": _get_attendance_wjx_sheet_cell(row, columns, "课程"),
        "student_id_text": _get_attendance_wjx_sheet_cell(row, columns, "学号"),
        "student_name": _get_attendance_wjx_sheet_cell(row, columns, "姓名"),
        "correction_request": _get_attendance_wjx_sheet_cell(row, columns, "修正需求"),
        "extra_note": _get_attendance_wjx_sheet_cell(row, columns, "补充说明"),
        "process_status": process_status,
        "process_note": process_status,
        "match_result": {},
        "revision_result": {},
        "raw_row": {},
        "synced_at": updated_at,
        "created_at": updated_at,
        "updated_at": updated_at,
    }
    item["foreground_colors"] = _build_attendance_wjx_sheet_foreground_colors(item)
    return item


def _build_attendance_wjx_sheet_data_page(
    session: Session,
    *,
    document: SheetDocument,
    template: dict[str, str],
    page: int,
    page_size: int,
    process_status: str | None = None,
    keyword: str | None = None,
) -> AttendanceWjxDataPage:
    normalized_page = max(1, int(page or 1))
    normalized_page_size = min(max(1, int(page_size or 20)), 100)
    offset = (normalized_page - 1) * normalized_page_size
    document_json = _normalize_attendance_wjx_sheet_document(dict(document.document_json or {}))
    columns = list(document_json["columns"])
    normalized_status = (process_status or "").strip()
    normalized_keyword = (keyword or "").strip()

    items: list[dict[str, Any]] = []
    for row in document_json["rows"]:
        item = _serialize_attendance_wjx_sheet_row(
            row,
            columns,
            updated_at=float(document.updated_at or 0.0),
        )
        if item is None:
            continue
        item_status = _normalize_attendance_wjx_sheet_cell(item.get("process_status"))
        if normalized_status == "__empty__" and item_status:
            continue
        if normalized_status and normalized_status != "__empty__" and item_status != normalized_status:
            continue
        if normalized_keyword:
            haystack = " ".join(
                _normalize_attendance_wjx_sheet_cell(item.get(key))
                for key in (
                    "submitted_at_text",
                    "source",
                    "course_name",
                    "student_id_text",
                    "student_name",
                    "correction_request",
                    "extra_note",
                    "process_status",
                )
            )
            if normalized_keyword not in haystack:
                continue
        items.append(item)

    items.sort(key=lambda item: int(item["seq"]), reverse=True)
    total = len(items)
    paged_items = items[offset : offset + normalized_page_size]
    state = session.get(AttendanceWjxDataSyncState, str(template["activity_id"]))
    return AttendanceWjxDataPage(
        items=[AttendanceWjxDataItem.model_validate(item) for item in paged_items],
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


def _serialize_sheet_document(document: SheetDocument) -> dict[str, Any]:
    return {
        "id": int(document.numeric_id or 0),
        "scope": document.scope,
        "owner_type": document.owner_type,
        "owner_key": document.owner_key,
        "sheet_key": document.sheet_key,
        "title": document.title,
        "engine": document.engine,
        "document_json": dict(document.document_json or {}),
        "version": int(document.version or 1),
        "created_by_user_id": document.created_by_user_id,
        "updated_by_user_id": document.updated_by_user_id,
        "created_at": float(document.created_at or 0.0),
        "updated_at": float(document.updated_at or 0.0),
    }


def _get_attendance_sheet_document_by_owner(
    session: Session,
    *,
    owner_type: str,
    owner_key: str,
    sheet_key: str,
) -> SheetDocument | None:
    statement = (
        select(SheetDocument)
        .where(SheetDocument.scope == "attendance")
        .where(SheetDocument.owner_type == owner_type)
        .where(SheetDocument.owner_key == owner_key)
        .where(SheetDocument.sheet_key == sheet_key)
    )
    return session.exec(statement).first()


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


def _normalize_feedback_sheet_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalize_feedback_sheet_columns(document_json: dict[str, Any]) -> list[str]:
    columns = document_json.get("columns")
    if not isinstance(columns, list):
        return []
    return [str(column or "").strip() for column in columns]


def _extract_feedback_sheet_rows(document_json: dict[str, Any]) -> list[Any]:
    rows = document_json.get("rows")
    return list(rows) if isinstance(rows, list) else []


def _find_feedback_course_column_index(columns: list[str], field_key: str) -> int | None:
    binding = FEEDBACK_COURSE_FIELD_BINDINGS.get(field_key)
    if binding is None:
        return None
    header, fallback_index = binding
    for index, column in enumerate(columns):
        if column == header:
            return index
    if 0 <= fallback_index < len(columns):
        return fallback_index
    return None


def _extract_feedback_sheet_cell(row: Any, column_index: int, columns: list[str]) -> Any:
    if isinstance(row, list):
        return row[column_index] if column_index < len(row) else ""
    if isinstance(row, dict):
        column_key = columns[column_index] if column_index < len(columns) else ""
        return row.get(column_key, "")
    return ""


def _extract_feedback_sheet_cell_link_url(document_json: dict[str, Any], row_index: int, column_index: int) -> str:
    cell_meta = document_json.get("cell_meta")
    if not isinstance(cell_meta, dict):
        return ""
    entry = cell_meta.get(f"{row_index}:{column_index}")
    if not isinstance(entry, dict):
        return ""
    link = entry.get("link")
    if not isinstance(link, dict):
        return ""
    return _normalize_feedback_sheet_text(link.get("url"))


def _extract_feedback_course_options_from_sheet(document_json: dict[str, Any]) -> list[AttendanceFeedbackCourseOption]:
    columns = _normalize_feedback_sheet_columns(document_json)
    rows = _extract_feedback_sheet_rows(document_json)
    online_sheet_index = _find_feedback_course_column_index(columns, "online_sheet")
    course_name_index = _find_feedback_course_column_index(columns, "course_name")
    completed_index = _find_feedback_course_column_index(columns, "completed_date")
    if completed_index is None or (online_sheet_index is None and course_name_index is None):
        return []

    seen: set[str] = set()
    result: list[AttendanceFeedbackCourseOption] = []
    for row_index, row in enumerate(rows):
        completed_text = _normalize_feedback_sheet_text(
            _extract_feedback_sheet_cell(row, completed_index, columns)
        )
        if completed_text:
            continue

        course_name = ""
        if online_sheet_index is not None:
            course_name = _normalize_feedback_sheet_text(
                _extract_feedback_sheet_cell(row, online_sheet_index, columns)
            )
        if not course_name and course_name_index is not None:
            course_name = _normalize_feedback_sheet_text(
                _extract_feedback_sheet_cell(row, course_name_index, columns)
            )
        if not course_name or course_name in seen:
            continue
        seen.add(course_name)
        attendance_sheet_url = ""
        if online_sheet_index is not None:
            attendance_sheet_url = _extract_feedback_sheet_cell_link_url(document_json, row_index, online_sheet_index)
        result.append(AttendanceFeedbackCourseOption(name=course_name, attendance_sheet_url=attendance_sheet_url))
    return result


def _extract_feedback_course_link_map_from_sheet(document_json: dict[str, Any]) -> dict[str, str]:
    columns = _normalize_feedback_sheet_columns(document_json)
    rows = _extract_feedback_sheet_rows(document_json)
    online_sheet_index = _find_feedback_course_column_index(columns, "online_sheet")
    course_name_index = _find_feedback_course_column_index(columns, "course_name")
    if online_sheet_index is None and course_name_index is None:
        return {}

    result: dict[str, str] = {}
    for row_index, row in enumerate(rows):
        attendance_sheet_url = ""
        if online_sheet_index is not None:
            attendance_sheet_url = _extract_feedback_sheet_cell_link_url(document_json, row_index, online_sheet_index)
        if not attendance_sheet_url and course_name_index is not None:
            attendance_sheet_url = _extract_feedback_sheet_cell_link_url(document_json, row_index, course_name_index)
        if not attendance_sheet_url:
            continue

        candidates: list[str] = []
        if online_sheet_index is not None:
            candidates.append(_normalize_feedback_sheet_text(
                _extract_feedback_sheet_cell(row, online_sheet_index, columns)
            ))
        if course_name_index is not None:
            candidates.append(_normalize_feedback_sheet_text(
                _extract_feedback_sheet_cell(row, course_name_index, columns)
            ))

        for name in candidates:
            if name and name not in result:
                result[name] = attendance_sheet_url
    return result


def _get_feedback_course_link_map_from_summary_sheet(session: Session) -> dict[str, str] | None:
    source_sheet = session.exec(
        select(SheetDocument).where(SheetDocument.numeric_id == FEEDBACK_COURSE_SOURCE_SHEET_ID)
    ).first()
    if source_sheet is None:
        return None
    return _extract_feedback_course_link_map_from_sheet(dict(source_sheet.document_json or {}))


def _sync_attendance_wjx_sheet_course_links(
    session: Session,
    document_json: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    return _apply_attendance_wjx_sheet_course_links(
        document_json,
        _get_feedback_course_link_map_from_summary_sheet(session),
    )


def _extract_feedback_course_names_from_sheet(document_json: dict[str, Any]) -> list[str]:
    return [item.name for item in _extract_feedback_course_options_from_sheet(document_json)]


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


def _normalize_attendance_order_refund_detail_result(result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(result or {})
    rows = normalized.get("rows")
    normalized_rows: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized_rows.append(
                {
                    "wechat_order_id": _normalize_attendance_order_id(row.get("wechat_order_id")),
                    "merchant_order_id": _normalize_attendance_order_id(row.get("merchant_order_id")),
                    "refund_id": _normalize_attendance_order_id(row.get("refund_id")),
                    "refund_amount": float(row.get("refund_amount") or 0.0),
                    "refund_status": _normalize_order_history_text(row.get("refund_status")),
                    "applicant": _normalize_order_history_text(row.get("applicant")),
                    "submitted_at": _normalize_order_history_text(row.get("submitted_at")),
                    "completed_at": _normalize_order_history_text(row.get("completed_at")),
                }
            )
    normalized["rows"] = normalized_rows

    summary = normalized.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    normalized["summary"] = {
        "order_id": _normalize_attendance_order_id(summary.get("order_id")),
        "matched_order_id": _normalize_attendance_order_id(summary.get("matched_order_id")),
        "query_type": str(summary.get("query_type") or "auto"),
        "row_count": int(summary.get("row_count") or len(normalized_rows)),
        "refund_amount_total": float(summary.get("refund_amount_total") or 0.0),
        "wechat_order_id": _normalize_attendance_order_id(summary.get("wechat_order_id")),
        "merchant_order_id": _normalize_attendance_order_id(summary.get("merchant_order_id")),
        "refund_statuses": [
            _normalize_order_history_text(item)
            for item in (summary.get("refund_statuses") or [])
            if _normalize_order_history_text(item)
        ],
    }
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
    document = _ensure_attendance_wjx_sheet_document(session, create=True)
    if document is not None:
        return _get_next_attendance_wjx_sheet_seq(dict(document.document_json or {}))
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

    entry = session.exec(
        select(AttendanceWjxDataEntry)
        .where(AttendanceWjxDataEntry.activity_id == LOCAL_FEEDBACK_ACTIVITY_ID)
        .where(AttendanceWjxDataEntry.seq == seq)
    ).first()
    if entry is None:
        entry = AttendanceWjxDataEntry(
            activity_id=LOCAL_FEEDBACK_ACTIVITY_ID,
            seq=seq,
            created_at=now,
        )
    entry.submitted_at_text = submitted_at_text
    entry.duration_text = ""
    entry.source = LOCAL_FEEDBACK_SOURCE
    entry.source_detail = LOCAL_FEEDBACK_SOURCE_DETAIL
    entry.source_ip = source_ip
    entry.course_name = course_name
    entry.student_id_text = student_id_text
    entry.student_name = student_name
    entry.correction_request = correction_request
    entry.extra_note = extra_note
    entry.raw_row_json = raw_row
    entry.synced_at = now
    entry.updated_at = now
    session.add(entry)
    state.stored_count = _count_attendance_wjx_data_entries(
        session,
        activity_id=LOCAL_FEEDBACK_ACTIVITY_ID,
    ) + 1
    state.updated_at = now
    session.add(state)
    session.commit()
    session.refresh(entry)
    _upsert_attendance_wjx_sheet_entry(
        session,
        entry,
        preserve_process_status=False,
    )
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


def _build_attendance_wjx_data_page(
    session: Session,
    *,
    template: dict[str, str],
    page: int,
    page_size: int,
    process_status: str | None = None,
    keyword: str | None = None,
) -> AttendanceWjxDataPage:
    activity_id = str(template["activity_id"])
    sheet_document = _ensure_attendance_wjx_sheet_document(session, create=False)
    if sheet_document is not None:
        return _build_attendance_wjx_sheet_data_page(
            session,
            document=sheet_document,
            template=template,
            page=page,
            page_size=page_size,
            process_status=process_status,
            keyword=keyword,
        )

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
    state = session.get(AttendanceWjxDataSyncState, activity_id)
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


def _build_attendance_feedback_form_meta(session: Session) -> AttendanceFeedbackFormMeta:
    source_sheet = session.exec(
        select(SheetDocument).where(SheetDocument.numeric_id == FEEDBACK_COURSE_SOURCE_SHEET_ID)
    ).first()
    course_options: list[AttendanceFeedbackCourseOption] = []
    updated_at: float | None = None
    if source_sheet is not None:
        course_options = _extract_feedback_course_options_from_sheet(dict(source_sheet.document_json or {}))
        updated_at = source_sheet.updated_at

    data_sheet = _ensure_attendance_wjx_sheet_document(session, create=True)
    data_sheet_url = _ensure_note_sheet_anonymous_viewer(session, data_sheet)
    return AttendanceFeedbackFormMeta(
        course_names=[item.name for item in course_options],
        course_options=course_options,
        course_names_updated_at=updated_at,
        data_sheet_url=data_sheet_url,
    )


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
    }


def _ensure_owned_device_for_selection(entry: UserDevice, current_user: User) -> None:
    if entry.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="只能从你自己的设备资产中选择执行设备")
    if not entry.is_active:
        raise HTTPException(status_code=400, detail="当前执行设备已停用")


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


def _execute_order_refund_details_on_entry(entry_snapshot: dict[str, Any], execution_payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(entry_snapshot.get("mode") or "")
    if mode == "local":
        local_device_id = get_device_id()
        if str(entry_snapshot.get("device_id") or "") != local_device_id:
            raise RuntimeError("所选本地执行设备不属于当前节点")
        with ensure_ui_automation_thread_context():
            return query_order_refund_details(
                execution_payload.get("order_id"),
                query_type=execution_payload.get("query_type"),
                weipay_login_users=execution_payload.get("login_users"),
            )

    server_url = (entry_snapshot.get("server_url") or "").rstrip("/")
    token = str(entry_snapshot.get("token") or "")
    if not server_url or not token:
        raise RuntimeError("远程执行设备缺少后端地址或访问令牌")

    response = requests.post(
        f"{server_url}/api/device-control/attendance/order/refund-details",
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


@router.get("/sheets/by-owner", response_model=AttendanceSheetDocumentResponse)
def get_attendance_sheet_document_by_owner(
    owner_type: str = Query(...),
    owner_key: str = Query(...),
    sheet_key: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("notes.sheets")),
):
    ensure_can_use_attendance_service(current_user, session)

    document = _get_attendance_sheet_document_by_owner(
        session,
        owner_type=_normalize_sheet_locator_part(owner_type, field_name="owner_type", lower=True),
        owner_key=_normalize_sheet_locator_part(owner_key, field_name="owner_key"),
        sheet_key=_normalize_sheet_locator_part(sheet_key, field_name="sheet_key", lower=True),
    )
    if document is None:
        raise HTTPException(status_code=404, detail="表格文档不存在")
    return AttendanceSheetDocumentResponse.model_validate(_serialize_sheet_document(document))


@router.put("/sheets", response_model=AttendanceSheetDocumentResponse)
def upsert_attendance_sheet_document(
    payload: AttendanceSheetDocumentUpsertRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("notes.sheets")),
):
    current_user = ensure_can_use_attendance_service(current_user, session)
    owner_type = _normalize_sheet_locator_part(payload.owner_type, field_name="owner_type", lower=True)
    owner_key = _normalize_sheet_locator_part(payload.owner_key, field_name="owner_key")
    sheet_key = _normalize_sheet_locator_part(payload.sheet_key, field_name="sheet_key", lower=True)
    title = str(payload.title or "").strip() or sheet_key
    document_json = dict(payload.document_json or {})

    document = _get_attendance_sheet_document_by_owner(
        session,
        owner_type=owner_type,
        owner_key=owner_key,
        sheet_key=sheet_key,
    )

    if document is None:
        now = time.time()
        document = SheetDocument(
            numeric_id=_get_next_sheet_numeric_id(session),
            scope="attendance",
            owner_type=owner_type,
            owner_key=owner_key,
            sheet_key=sheet_key,
            title=title,
            engine=payload.engine,
            document_json=document_json,
            version=1,
            owner_user_id=current_user.id,
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
            created_at=now,
            updated_at=now,
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        return AttendanceSheetDocumentResponse.model_validate(_serialize_sheet_document(document))

    if (
        document.title == title
        and document.engine == payload.engine
        and dict(document.document_json or {}) == document_json
    ):
        return AttendanceSheetDocumentResponse.model_validate(_serialize_sheet_document(document))

    document.title = title
    document.engine = payload.engine
    document.document_json = document_json
    if document.numeric_id is None:
        document.numeric_id = _get_next_sheet_numeric_id(session)
    if document.owner_user_id is None:
        document.owner_user_id = current_user.id
    document.version = max(int(document.version or 1), 1) + 1
    document.updated_by_user_id = current_user.id
    document.updated_at = time.time()
    session.add(document)
    session.commit()
    session.refresh(document)
    return AttendanceSheetDocumentResponse.model_validate(_serialize_sheet_document(document))


@router.get("/sheets/{sheet_id}", response_model=AttendanceSheetDocumentResponse)
def get_attendance_sheet_document_by_id(
    sheet_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("notes.sheets")),
):
    ensure_can_use_attendance_service(current_user, session)
    normalized_sheet_id = _normalize_sheet_numeric_id(sheet_id)
    document = session.exec(
        select(SheetDocument).where(SheetDocument.numeric_id == normalized_sheet_id)
    ).first()
    if document is None or document.scope != "attendance":
        raise HTTPException(status_code=404, detail="表格文档不存在")
    return AttendanceSheetDocumentResponse.model_validate(_serialize_sheet_document(document))


@router.get("/config")
def get_attendance_config(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(
        require_any_feature_access_dependency(
            "attendance.configs",
            "attendance.orders",
        )
    ),
):
    return _resolve_config_payload(session)


@public_router.get("/wjx-feedback-form", response_model=AttendanceFeedbackFormMeta)
def get_attendance_feedback_form_meta(
    session: Session = Depends(get_session),
):
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


@router.get("/wjx-data/sheet", response_model=AttendanceWjxDataSheetLocation)
def get_attendance_wjx_data_sheet_location(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.wjx-data")),
):
    ensure_can_use_attendance_service(current_user, session)
    document = _ensure_attendance_wjx_sheet_document(session, create=True)
    if document is None or document.numeric_id is None:
        raise HTTPException(status_code=404, detail="没有找到工作簿 2，无法创建问卷数据表")
    sheet_id = int(document.numeric_id)
    return AttendanceWjxDataSheetLocation(
        workbook_id=ATTENDANCE_WJX_DATA_WORKBOOK_ID,
        sheet_id=sheet_id,
        path=f"/notes/sheets?workbook={ATTENDANCE_WJX_DATA_WORKBOOK_ID}&sheet={sheet_id}",
    )


@public_router.get("/wjx-data", response_model=AttendanceWjxDataPage)
def list_attendance_wjx_data(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    process_status: Optional[str] = None,
    keyword: Optional[str] = None,
    template_id: Optional[str] = None,
    session: Session = Depends(get_session),
):
    template = _resolve_wjx_template_payload(template_id)
    return _build_attendance_wjx_data_page(
        session,
        template=template,
        page=page,
        page_size=page_size,
        process_status=process_status,
        keyword=keyword,
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
    sheet_document = _ensure_attendance_wjx_sheet_document(session, create=False)
    if sheet_document is not None:
        current_document = _normalize_attendance_wjx_sheet_document(dict(sheet_document.document_json or {}))
        if _find_attendance_wjx_sheet_row_index(current_document, entry_id) is not None:
            next_status = (
                payload.process_status
                if payload.process_status is not None
                else payload.process_note
            )
            if next_status is not None:
                next_document, _inserted, changed = _upsert_attendance_wjx_sheet_values(
                    current_document,
                    {
                        "序号": entry_id,
                        "处理状态": (next_status or "").strip(),
                    },
                    preserve_process_status=False,
                )
                if changed:
                    _persist_attendance_wjx_sheet_document(
                        session,
                        sheet_document,
                        next_document,
                        actor=current_user,
                    )
                    current_document = _normalize_attendance_wjx_sheet_document(dict(sheet_document.document_json or {}))

            legacy_entries = session.exec(
                select(AttendanceWjxDataEntry)
                .where(AttendanceWjxDataEntry.activity_id.in_([FIXED_WJX_TEMPLATE_ACTIVITY_ID, LOCAL_FEEDBACK_ACTIVITY_ID]))
                .where(AttendanceWjxDataEntry.seq == entry_id)
            ).all()
            if legacy_entries:
                now = time.time()
                for legacy_entry in legacy_entries:
                    if next_status is not None:
                        legacy_entry.process_status = (next_status or "").strip()
                        legacy_entry.process_note = (next_status or "").strip()
                    if payload.match_result is not None:
                        legacy_entry.match_result_json = dict(payload.match_result)
                    if payload.revision_result is not None:
                        legacy_entry.revision_result_json = dict(payload.revision_result)
                    legacy_entry.updated_at = now
                    session.add(legacy_entry)
                session.commit()

            row_index = _find_attendance_wjx_sheet_row_index(current_document, entry_id)
            if row_index is not None:
                columns = list(current_document["columns"])
                item = _serialize_attendance_wjx_sheet_row(
                    current_document["rows"][row_index],
                    columns,
                    updated_at=float(sheet_document.updated_at or 0.0),
                )
                if item is not None:
                    return AttendanceWjxDataItem.model_validate(item)

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
    sheet_document = _ensure_attendance_wjx_sheet_document(session, create=False)
    if sheet_document is not None:
        next_document, removed = _remove_attendance_wjx_sheet_row(
            dict(sheet_document.document_json or {}),
            seq=entry_id,
        )
        if removed:
            _persist_attendance_wjx_sheet_document(
                session,
                sheet_document,
                next_document,
                actor=current_user,
            )
            legacy_entries = session.exec(
                select(AttendanceWjxDataEntry)
                .where(AttendanceWjxDataEntry.activity_id.in_([FIXED_WJX_TEMPLATE_ACTIVITY_ID, LOCAL_FEEDBACK_ACTIVITY_ID]))
                .where(AttendanceWjxDataEntry.seq == entry_id)
            ).all()
            for legacy_entry in legacy_entries:
                session.delete(legacy_entry)
            if legacy_entries:
                session.commit()
            return {
                "deleted": True,
                "entry_id": entry_id,
                "seq": entry_id,
            }

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

    if payload.persist_global_selection and current_user.is_superuser:
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


@router.post("/order-refund-details", response_model=AttendanceOrderRefundDetailResponse)
def query_attendance_order_refund_details(
    payload: AttendanceOrderRefundDetailRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.orders")),
):
    config = get_or_create_attendance_service_config(session)
    extra_config = get_attendance_service_extra_config(session)
    entry = _resolve_run_device(
        session,
        config,
        execution_device_entry_id=payload.execution_device_entry_id,
        current_user=current_user,
    )

    execution_payload = {
        "order_id": payload.order_id,
        "query_type": payload.query_type,
        "login_users": list(payload.login_users or extra_config.get("scan_reminder_users") or []),
    }

    try:
        result = _execute_order_refund_details_on_entry(
            {
                **serialize_user_device(entry),
                "token": entry.token,
            },
            execution_payload,
        )
        result = _normalize_attendance_order_refund_detail_result(result)
    except OrderAutomationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if payload.persist_global_selection and current_user.is_superuser:
        config.execution_device_entry_id = entry.entry_id
        config.updated_by_user_id = current_user.id
        config.updated_at = time.time()
        session.add(config)
        session.commit()

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
    return _build_order_refund_history_page(session, page=page, page_size=page_size)
