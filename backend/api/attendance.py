from __future__ import annotations

import json
import time
import re
from datetime import datetime
from typing import Any, Literal, Optional
from urllib.parse import parse_qs, urlparse

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from pyxllib.cv.rgbfmt import hash_text_to_hex_color
from sqlalchemy import func, or_
from sqlmodel import Session, select

from backend.core.attendance.service import (
    AttendanceServiceError,
    apply_attendance_order_operation_password_env,
    build_attendance_course_data_step_runner_configs,
    encrypt_attendance_secret,
    ensure_can_manage_attendance_service,
    ensure_can_use_attendance_service,
    get_attendance_account_or_404,
    get_attendance_course_data_flow_config,
    get_attendance_course_data_step_default_role,
    get_attendance_wjx_data_entry_or_404,
    get_attendance_service_order_operation_password,
    get_current_account,
    get_current_execution_device,
    get_or_create_attendance_service_config,
    get_or_create_attendance_wjx_data_sync_state,
    get_attendance_service_extra_config,
    get_user_device_or_404,
    list_attendance_accounts,
    normalize_attendance_device_entry_id,
    normalize_attendance_step_device_entry_ids,
    serialize_attendance_order_refund_history,
    serialize_attendance_account,
    serialize_attendance_wjx_data_entry,
    serialize_attendance_wjx_data_sync_state,
    serialize_user_device,
    update_attendance_course_data_flow_config,
    update_attendance_service_extra_config,
)
from backend.core.attendance.order import (
    OrderAutomationError,
    execute_order_action,
    query_order_refund_details,
)
from backend.core.attendance.ai_precheck import build_attendance_wjx_ai_precheck
from backend.core.ai.chat import OllamaClientError, chat_with_provider
from backend.core.access.auth import get_current_user_from_token
from backend.core.devices.device import get_device_id
from backend.core.access.feature_access_guard import (
    require_any_feature_access_dependency,
    require_feature_access_dependency,
)
from backend.core.devices.ui_automation import ensure_ui_automation_thread_context
from backend.db import get_session
from backend.models import (
    AttendanceAccountAsset,
    AttendanceOrderRefundHistory,
    AttendanceWjxDataEntry,
    AttendanceWjxDataSyncState,
    SheetDocument,
    User,
    UserDevice,
    WorkbookDocument,
    WorkbookSheetLink,
)
from backend.core.resources.identity import RESOURCE_TYPE_SHEET, allocate_resource_id
from backend.core.notes.sheet_access import ensure_sheet_anonymous_viewer
from backend.core.notes import sheet_inline_links as note_sheet_inline_links
from backend.core.resources.sheet_identity import allocate_new_sheet_identity
from backend.core.resources.sheet_refs import (
    load_sheets_by_refs,
    load_workbooks_by_refs,
    sheet_public_id,
    sheet_ref_aliases,
    workbook_public_id,
    workbook_ref_aliases,
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
ATTENDANCE_WJX_AI_COLUMN = "AI初判"
ATTENDANCE_WJX_DEPRECATED_DATA_COLUMNS = {"AI初判报告"}
ATTENDANCE_WJX_AUTO_REPAIR_PROVIDER_ID = "codex-cli"
ATTENDANCE_WJX_AUTO_REPAIR_MODEL = "gpt-5.3-codex-spark"
ATTENDANCE_WJX_AUTO_REPAIR_TIMEOUT_SECONDS = 90
ATTENDANCE_WJX_EMPTY_DATA_KEYWORDS = (
    "视频",
    "听课",
    "回放",
    "打卡",
    "作业",
    "学完",
    "完成",
    "没统计",
    "未统计",
    "显示0",
    "空",
    "没有数据",
)
ATTENDANCE_WJX_DATA_COLUMNS = [
    "序号",
    "提交时间",
    "来源",
    "课程",
    "考勤负责人",
    "学号",
    "姓名",
    "修正需求",
    "补充说明",
    "处理状态",
    ATTENDANCE_WJX_AI_COLUMN,
]
ATTENDANCE_WJX_OMITTED_COURSE_OWNER_NAMES = ("陈坤泽",)
FEEDBACK_COURSE_SOURCE_SHEET_ID = 4
FEEDBACK_COURSE_FIELD_BINDINGS: dict[str, tuple[str, int]] = {
    "course_name": ("课程名称", 1),
    "online_sheet": ("在线考勤表", 2),
    "course_owner": ("考勤负责人", 3),
    "completed_date": ("考勤实际完成结点", 10),
}
ORDER_HISTORY_RESULT_TIMESTAMP_PATTERN = re.compile(
    r"(?P<year>\d{4})[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
)
ATTENDANCE_HEADER_TOOL_ZEN_LESSON_RE = re.compile(
    r"^第\s*(?P<week>\d+)\s*周\s*[=＝:：-]\s*(?P<title>.+)$"
)
ATTENDANCE_HEADER_TOOL_COURSE_DATE_PREFIX_RE = re.compile(
    r"^(?P<year>20\d{2})(?P<month>\d{2})(?P<day>\d{2})(?P<rest>.*)$"
)
ATTENDANCE_HEADER_TOOL_LESSON_ADMIN_URL = (
    "https://admin.xiaoe-tech.com/t/live_management#/userOperation?id={lesson_id2}&tabName=UserManage"
)
ATTENDANCE_HEADER_TOOL_CLOCKIN_GROUP_COLORS = ("#5B8FC9", "#A7C8E8")
ATTENDANCE_HEADER_TOOL_WEEK_COLORS = (
    ("#ED7D31", "#F5B183"),
    ("#3B82C4", "#A8C7E8"),
    ("#46A66A", "#B2D9BE"),
    ("#9B6BC8", "#D2B8EB"),
    ("#D85B5B", "#EFB0B0"),
    ("#2A9D9D", "#9FD9D7"),
    ("#B58A2A", "#E2CC88"),
    ("#6B7C93", "#C0CBD8"),
)


class AttendanceConfigUpdateRequest(BaseModel):
    current_wjx_account_id: Optional[str] = None
    execution_device_entry_id: Optional[str] = None
    scan_reminder_users: Optional[list[str]] = None
    order_lookup_mode: Optional[Literal["hybrid", "db_only", "browser_only"]] = None
    order_operation_password: Optional[str] = None
    clear_order_operation_password: Optional[bool] = None


class AttendanceCourseDataFlowConfigUpdateRequest(BaseModel):
    browser_device_entry_id: Optional[str] = None
    data_device_entry_id: Optional[str] = None
    step_device_entry_ids: Optional[dict[str, Optional[str]]] = None


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
    workbook_id: Optional[int] = None
    sheet_id: Optional[int] = None


class AttendanceFeedbackResolvedCourse(BaseModel):
    name: str = ""
    link_url: str = ""
    strong_context: bool = False


class AttendanceWjxDataUpdateRequest(BaseModel):
    process_status: Optional[str] = None
    process_note: Optional[str] = None
    match_result: Optional[dict[str, Any]] = None
    revision_result: Optional[dict[str, Any]] = None


class AttendanceWjxAiPrecheckRequest(BaseModel):
    persist: bool = True
    use_codex_cli: bool = True
    auto_repair: bool = False
    repair_with_remote_browser: bool = True


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


class AttendanceWjxAiPrecheckResponse(BaseModel):
    item: AttendanceWjxDataItem
    precheck: dict[str, Any] = Field(default_factory=dict)


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


class AttendanceFeedbackHistoryResponse(BaseModel):
    items: list[AttendanceWjxDataItem] = Field(default_factory=list)
    total: int


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


class AttendanceHeaderToolRequest(BaseModel):
    course_name: str


class AttendanceHeaderToolGroup(BaseModel):
    label: str
    kind: Literal["clockin", "week"]
    start_column: int
    colspan: int
    background_color: str
    child_background_color: str
    week_index: Optional[int] = None


class AttendanceHeaderToolCell(BaseModel):
    label: str
    url: str = ""
    kind: Literal["clockin", "lesson"]
    column_index: int
    group_label: str
    background_color: str
    source_id: Optional[int] = None
    lesson_id2: str = ""
    week_index: Optional[int] = None


class AttendanceHeaderToolResponse(BaseModel):
    course_name: str
    course_type: str
    groups: list[AttendanceHeaderToolGroup] = Field(default_factory=list)
    cells: list[AttendanceHeaderToolCell] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    plain_text: str = ""
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


def _normalize_attendance_header_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_attendance_header_course_name(value: Any) -> str:
    text = _normalize_attendance_header_text(value)
    match = ATTENDANCE_HEADER_TOOL_COURSE_DATE_PREFIX_RE.match(text)
    if not match:
        return text

    return f"d{match.group('year')[-2:]}{match.group('month')}{match.group('day')}{match.group('rest').lstrip()}"


def _load_attendance_header_kqdb():
    try:
        from kq5034.attendance_api import get_kqdb  # type: ignore

        return get_kqdb()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"无法加载考勤数据库：{exc}") from exc


def _strip_attendance_header_course_prefix(course_name: str, value: Any) -> str:
    text = _normalize_attendance_header_text(value)
    prefix = f"{course_name}-"
    if text.startswith(prefix):
        return text[len(prefix):].strip()
    return text


def _normalize_attendance_header_url(value: Any) -> str:
    text = _normalize_attendance_header_text(value)
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return text
    return ATTENDANCE_HEADER_TOOL_LESSON_ADMIN_URL.format(lesson_id2=text)


def _parse_zen_attendance_lesson(course_name: str, lesson_name: Any) -> tuple[int, str, str]:
    short_name = _strip_attendance_header_course_prefix(course_name, lesson_name)
    match = ATTENDANCE_HEADER_TOOL_ZEN_LESSON_RE.match(short_name)
    if not match:
        raise HTTPException(
            status_code=400,
            detail=f"禅宗课次必须包含“第几周=课次”：{_normalize_attendance_header_text(lesson_name)}",
        )

    week_index = int(match.group("week"))
    title = match.group("title").strip()
    if not title:
        raise HTTPException(
            status_code=400,
            detail=f"禅宗课次缺少课次名称：{_normalize_attendance_header_text(lesson_name)}",
        )
    return week_index, f"第{week_index}周", title


def _query_attendance_header_clockins(course_name: str) -> list[dict[str, Any]]:
    xldb = _load_attendance_header_kqdb()
    try:
        records = xldb.exec2dict(
            "SELECT clockin_id, name, url FROM clockin_table WHERE name LIKE %s ORDER BY clockin_id",
            [f"{course_name}-%"],
        )
        return [dict(row) for row in (records or [])]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"查询打卡链接失败：{exc}") from exc


def _query_attendance_header_lessons(course_name: str) -> list[dict[str, Any]]:
    xldb = _load_attendance_header_kqdb()
    try:
        records = xldb.exec2dict(
            "SELECT lesson_id, lesson_name, lesson_id2 FROM lesson_table "
            "WHERE lesson_name LIKE %s ORDER BY lesson_id",
            [f"{course_name}-%"],
        )
        return [dict(row) for row in (records or [])]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"查询课次链接失败：{exc}") from exc


def _build_attendance_header_document(
    groups: list[AttendanceHeaderToolGroup],
    cells: list[AttendanceHeaderToolCell],
) -> dict[str, Any]:
    columns = [cell.label or f"列{cell.column_index + 1}" for cell in cells]
    top_row = [""] * len(cells)
    second_row = [cell.label for cell in cells]
    cell_meta: dict[str, Any] = {}
    merged_cells: list[dict[str, int]] = []

    for group in groups:
        if 0 <= group.start_column < len(top_row):
            top_row[group.start_column] = group.label
        if group.colspan > 1:
            merged_cells.append({
                "row": 0,
                "col": group.start_column,
                "rowspan": 1,
                "colspan": group.colspan,
            })
        for column in range(group.start_column, min(group.start_column + group.colspan, len(cells))):
            cell_meta[f"0:{column}"] = {
                "style": {
                    "background_color": group.background_color,
                    "text_color": "#111827",
                },
            }

    for cell in cells:
        entry: dict[str, Any] = {
            "style": {
                "background_color": cell.background_color,
                "text_color": "#0645AD" if cell.url else "#111827",
            },
        }
        if cell.url:
            second_row[cell.column_index] = note_sheet_inline_links.with_inline_cell_link(
                second_row[cell.column_index],
                {"url": cell.url},
            )
        cell_meta[f"1:{cell.column_index}"] = entry

    return {
        "schema_version": 1,
        "columns": columns,
        "rows": [],
        "grid_rows": [top_row, second_row],
        "data_start_row": 2,
        "field_row_index": 1,
        "merged_cells": merged_cells,
        "formula_reference_origin": "sheet_v2",
        "cell_meta": cell_meta,
        "column_widths": [max(96, min(180, len(label) * 18 + 36)) for label in columns],
        "view_settings": {
            "show_row_numbers": False,
            "row_marker_numbering": "global",
            "row_marker_origin": "sheet",
            "show_column_markers": True,
            "column_marker_style": "letters",
            "frozen_column_count": 0,
            "pagination": {
                "enabled": False,
                "page_size": 100,
            },
        },
    }


def _build_attendance_header_tool_response(course_name_source: Any) -> AttendanceHeaderToolResponse:
    course_name = _normalize_attendance_header_course_name(course_name_source)
    if not course_name:
        raise HTTPException(status_code=400, detail="请输入课程前缀")
    if "禅宗" not in course_name:
        raise HTTPException(status_code=400, detail="暂不支持的类型：目前仅支持禅宗")

    clockins = _query_attendance_header_clockins(course_name)
    lessons = _query_attendance_header_lessons(course_name)
    if not lessons:
        raise HTTPException(status_code=404, detail="没有找到匹配的课次数据")

    groups: list[AttendanceHeaderToolGroup] = []
    cells: list[AttendanceHeaderToolCell] = []

    if clockins:
        group_color, child_color = ATTENDANCE_HEADER_TOOL_CLOCKIN_GROUP_COLORS
        groups.append(AttendanceHeaderToolGroup(
            label="打卡数据",
            kind="clockin",
            start_column=0,
            colspan=len(clockins),
            background_color=group_color,
            child_background_color=child_color,
        ))
        for row in clockins:
            column_index = len(cells)
            cells.append(AttendanceHeaderToolCell(
                label=_strip_attendance_header_course_prefix(course_name, row.get("name")),
                url=_normalize_attendance_header_text(row.get("url")),
                kind="clockin",
                column_index=column_index,
                group_label="打卡数据",
                background_color=child_color,
                source_id=int(row.get("clockin_id") or 0) or None,
            ))

    current_week_index: int | None = None
    current_group: AttendanceHeaderToolGroup | None = None

    for row in lessons:
        week_index, week_label, lesson_title = _parse_zen_attendance_lesson(course_name, row.get("lesson_name"))
        if current_week_index != week_index:
            group_color, child_color = ATTENDANCE_HEADER_TOOL_WEEK_COLORS[
                (len([group for group in groups if group.kind == "week"])) % len(ATTENDANCE_HEADER_TOOL_WEEK_COLORS)
            ]
            current_group = AttendanceHeaderToolGroup(
                label=week_label,
                kind="week",
                start_column=len(cells),
                colspan=0,
                background_color=group_color,
                child_background_color=child_color,
                week_index=week_index,
            )
            groups.append(current_group)
            current_week_index = week_index

        if current_group is None:
            raise HTTPException(status_code=500, detail="生成周分组失败")

        current_group.colspan += 1
        column_index = len(cells)
        lesson_id2 = _normalize_attendance_header_text(row.get("lesson_id2"))
        cells.append(AttendanceHeaderToolCell(
            label=lesson_title,
            url=_normalize_attendance_header_url(lesson_id2),
            kind="lesson",
            column_index=column_index,
            group_label=current_group.label,
            background_color=current_group.child_background_color,
            source_id=int(row.get("lesson_id") or 0) or None,
            lesson_id2=lesson_id2,
            week_index=week_index,
        ))

    if not cells:
        raise HTTPException(status_code=404, detail="没有找到可生成的表头数据")

    document_json = _build_attendance_header_document(groups, cells)
    rows = [
        [_extract_inline_cell_text(cell) for cell in row]
        for row in list(document_json["grid_rows"])
        if isinstance(row, list)
    ]
    return AttendanceHeaderToolResponse(
        course_name=course_name,
        course_type="禅宗",
        groups=groups,
        cells=cells,
        rows=rows,
        plain_text="\n".join("\t".join(row) for row in rows),
        document_json=document_json,
    )


def _get_next_sheet_numeric_id(session: Session, legacy_pk: str) -> int:
    return allocate_resource_id(session, RESOURCE_TYPE_SHEET, legacy_pk)


def _get_next_workbook_link_order(session: Session, workbook_id: str) -> int:
    workbook_map = load_workbooks_by_refs(session, [workbook_id])
    workbook_refs = {
        ref
        for workbook in {str(workbook.id): workbook for workbook in workbook_map.values()}.values()
        for ref in workbook_ref_aliases(workbook)
    } or {str(workbook_id)}
    current_max = session.exec(
        select(func.max(WorkbookSheetLink.order_index))
        .where(WorkbookSheetLink.workbook_id.in_(workbook_refs))
    ).one()
    return max(int(current_max or 0), 0) + 10


def _build_public_note_sheet_url(document: SheetDocument | None) -> str:
    if document is None or document.numeric_id is None:
        return ""
    return f"/sheet/{int(document.numeric_id)}"


def _ensure_note_sheet_anonymous_viewer(session: Session, document: SheetDocument | None) -> str:
    if document is None:
        return ""
    numeric_id = int(document.numeric_id or 0)
    if numeric_id <= 0:
        raise HTTPException(status_code=500, detail="考勤表格资源编号缺失")
    ensure_sheet_anonymous_viewer(session, document)
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
            "考勤负责人": {"display_mode": "single_line"},
            "学号": {"display_mode": "single_line"},
            "姓名": {"display_mode": "single_line"},
            ATTENDANCE_WJX_AI_COLUMN: {"display_mode": "wrap"},
        },
        "view_settings": {
            "show_row_numbers": True,
            "row_marker_numbering": "global",
            "row_marker_origin": "sheet",
            "show_column_markers": True,
            "column_marker_style": "letters",
            "frozen_column_count": 0,
            "pagination": {
                "enabled": True,
                "page_size": 100,
            },
        },
    }


def _normalize_attendance_wjx_sheet_cell(value: Any) -> str:
    return _normalize_wjx_data_text(value)


def _coerce_inline_cell_object(value: Any) -> dict[str, Any] | None:
    return note_sheet_inline_links.coerce_inline_cell_object(value)


def _extract_inline_cell_text(value: Any) -> str:
    record = _coerce_inline_cell_object(value)
    if record is not None:
        for key in ("value", "text", "title", "label", "name"):
            if key in record:
                text = _extract_inline_cell_text(record.get(key))
                if text:
                    return text
        link = record.get("link")
        if isinstance(link, dict):
            return _extract_inline_cell_text(link.get("title") or link.get("url"))
        return ""

    if isinstance(value, (list, tuple)):
        return ", ".join(
            text
            for text in (_extract_inline_cell_text(item) for item in value)
            if text
        )
    return _normalize_scalar_wjx_data_text(value)


def _extract_inline_cell_link_url(value: Any) -> str:
    record = _coerce_inline_cell_object(value)
    if record is None:
        return ""
    link = record.get("link")
    if not isinstance(link, dict):
        return ""
    return _extract_inline_cell_text(link.get("url"))


def _normalize_attendance_wjx_course_owner_display(value: Any) -> str:
    text = _normalize_attendance_wjx_sheet_cell(value)
    if not text:
        return ""
    for omitted_name in ATTENDANCE_WJX_OMITTED_COURSE_OWNER_NAMES:
        text = text.replace(omitted_name, "")
    text = re.sub(r"[，、/／]+", ",", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"(?:,\s*){2,}", ", ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ,")


def _normalize_attendance_wjx_sheet_source_columns(value: Any) -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()
    if isinstance(value, list):
        for item in value:
            header = str(item or "").strip()
            if header and header not in seen:
                columns.append(header)
                seen.add(header)
    return columns


def _normalize_attendance_wjx_sheet_columns(value: Any) -> list[str]:
    source_columns = _normalize_attendance_wjx_sheet_source_columns(value)
    columns: list[str] = []
    seen: set[str] = set()
    for header in ATTENDANCE_WJX_DATA_COLUMNS:
        if header not in seen:
            columns.append(header)
            seen.add(header)
    for header in source_columns:
        if header in ATTENDANCE_WJX_DEPRECATED_DATA_COLUMNS:
            continue
        if header not in seen:
            columns.append(header)
            seen.add(header)
    return columns


def _normalize_attendance_wjx_sheet_row(
    row: Any,
    columns: list[str],
    *,
    source_columns: list[str] | None = None,
) -> list[str]:
    if isinstance(row, dict):
        values = []
        for column in columns:
            if column == ATTENDANCE_WJX_AI_COLUMN:
                values.append(_normalize_attendance_wjx_sheet_cell(row.get(column) or row.get("AI初判报告", "")))
            else:
                values.append(_normalize_attendance_wjx_sheet_cell(row.get(column, "")))
    elif isinstance(row, list):
        source_values = [_normalize_attendance_wjx_sheet_cell(cell) for cell in row]
        if source_columns:
            source_by_header = {
                header: source_values[index] if index < len(source_values) else ""
                for index, header in enumerate(source_columns)
            }
            values = []
            for column in columns:
                if column == ATTENDANCE_WJX_AI_COLUMN:
                    values.append(source_by_header.get(column) or source_by_header.get("AI初判报告", ""))
                else:
                    values.append(source_by_header.get(column, ""))
        else:
            values = source_values
    else:
        values = []
    if len(values) < len(columns):
        values.extend([""] * (len(columns) - len(values)))
    return values[:len(columns)]


def _parse_attendance_wjx_sheet_seq_text(value: Any) -> int | None:
    text = _normalize_attendance_wjx_sheet_cell(value)
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        return int(float(text))
    return None


def _deduplicate_attendance_wjx_sheet_rows(rows: list[list[str]], columns: list[str]) -> list[list[str]]:
    try:
        seq_index = columns.index("序号")
    except ValueError:
        return rows

    field_indexes = {header: index for index, header in enumerate(columns)}

    def cell(row: list[str], header: str) -> str:
        index = field_indexes.get(header)
        if index is None or index >= len(row):
            return ""
        return _normalize_attendance_wjx_sheet_cell(row[index])

    def score(row: list[str]) -> int:
        # Prefer rows that preserve manual processing and AI diagnosis.
        total = 0
        if cell(row, "处理状态"):
            total += 100000
        if cell(row, ATTENDANCE_WJX_AI_COLUMN):
            total += 10000
        if cell(row, "补充说明"):
            total += 1000
        if cell(row, "修正需求"):
            total += 100
        total += sum(1 for value in row if _normalize_attendance_wjx_sheet_cell(value))
        return total

    best_by_seq: dict[int, int] = {}
    seq_count = 0
    for index, row in enumerate(rows):
        seq = _parse_attendance_wjx_sheet_seq_text(row[seq_index] if seq_index < len(row) else "")
        if seq is None:
            continue
        seq_count += 1
        current_best = best_by_seq.get(seq)
        if current_best is None or (score(row), -index) > (score(rows[current_best]), -current_best):
            best_by_seq[seq] = index

    if len(best_by_seq) == seq_count:
        return rows

    kept_indexes = set(best_by_seq.values())
    deduped_rows: list[list[str]] = []
    for index, row in enumerate(rows):
        seq = _parse_attendance_wjx_sheet_seq_text(row[seq_index] if seq_index < len(row) else "")
        if seq is None or index in kept_indexes:
            deduped_rows.append(row)
    return deduped_rows


def _build_attendance_wjx_sheet_column_index_map(
    source_columns: list[str],
    target_columns: list[str],
) -> dict[int, int]:
    if not source_columns:
        return {}
    target_by_header = {header: index for index, header in enumerate(target_columns)}
    return {
        source_index: target_by_header[header]
        for source_index, header in enumerate(source_columns)
        if header in target_by_header
    }


def _remap_attendance_wjx_sheet_cell_meta_columns(
    cell_meta: Any,
    *,
    source_columns: list[str],
    target_columns: list[str],
) -> dict[str, Any] | None:
    if not isinstance(cell_meta, dict):
        return None
    column_index_map = _build_attendance_wjx_sheet_column_index_map(source_columns, target_columns)
    if not column_index_map:
        return dict(cell_meta)

    remapped: dict[str, Any] = {}
    for key, value in cell_meta.items():
        position = _parse_attendance_wjx_sheet_cell_meta_key(key)
        if position is None:
            remapped[str(key)] = value
            continue
        row_index, source_column_index = position
        target_column_index = column_index_map.get(source_column_index, source_column_index)
        remapped[f"{row_index}:{target_column_index}"] = value
    return remapped


def _strip_attendance_wjx_sheet_cell_meta_links(cell_meta: Any) -> dict[str, Any]:
    if not isinstance(cell_meta, dict):
        return {}
    next_meta: dict[str, Any] = {}
    for key, entry in cell_meta.items():
        if not isinstance(entry, dict):
            next_meta[str(key)] = entry
            continue
        next_entry = dict(entry)
        next_entry.pop("link", None)
        if next_entry:
            next_meta[str(key)] = next_entry
    return next_meta


def _apply_attendance_wjx_sheet_inline_links_to_rows(
    rows: list[list[Any]],
    rows_source: Any,
    *,
    source_columns: list[str],
    target_columns: list[str],
) -> bool:
    if not isinstance(rows_source, list):
        return False
    source_index_by_target: dict[int, int] = {}
    for target_index, header in enumerate(target_columns):
        try:
            source_index_by_target[target_index] = source_columns.index(header)
        except ValueError:
            continue

    changed = False
    for row_index, row in enumerate(rows):
        source_row = rows_source[row_index] if row_index < len(rows_source) else None
        if not isinstance(source_row, list):
            continue
        for target_index, source_index in source_index_by_target.items():
            if source_index >= len(source_row) or target_index >= len(row):
                continue
            url = _extract_inline_cell_link_url(source_row[source_index])
            if not url:
                continue
            next_cell = {
                "value": _extract_inline_cell_text(row[target_index]),
                "link": {"url": url},
            }
            if row[target_index] != next_cell:
                row[target_index] = next_cell
                changed = True
    return changed


def _remap_attendance_wjx_sheet_column_widths(
    column_widths: Any,
    *,
    source_columns: list[str],
    target_columns: list[str],
) -> list[Any] | None:
    if not isinstance(column_widths, list):
        return None
    if not source_columns:
        return list(column_widths)

    source_index_by_header = {header: index for index, header in enumerate(source_columns)}
    course_width = ""
    course_source_index = source_index_by_header.get("课程")
    if course_source_index is not None and course_source_index < len(column_widths):
        course_width = column_widths[course_source_index]

    remapped: list[Any] = []
    for header in target_columns:
        source_index = source_index_by_header.get(header)
        if source_index is not None and source_index < len(column_widths):
            remapped.append(column_widths[source_index])
        elif header == "考勤负责人" and course_width != "":
            remapped.append(course_width)
        else:
            remapped.append(None)
    return remapped


def _normalize_attendance_wjx_sheet_data_start_row(document_json: dict[str, Any]) -> int:
    grid_rows = document_json.get("grid_rows")
    if not isinstance(grid_rows, list):
        return 0
    try:
        return max(int(document_json.get("data_start_row") or 0), 0)
    except (TypeError, ValueError):
        return 0


def _normalize_attendance_wjx_sheet_document(value: Any) -> dict[str, Any]:
    source = dict(value) if isinstance(value, dict) else {}
    source_columns = _normalize_attendance_wjx_sheet_source_columns(source.get("columns"))
    source_has_ai_column = ATTENDANCE_WJX_AI_COLUMN in source_columns
    columns = _normalize_attendance_wjx_sheet_columns(source.get("columns"))
    rows_source = source.get("rows")
    rows = [
        _normalize_attendance_wjx_sheet_row(row, columns, source_columns=source_columns)
        for row in (rows_source if isinstance(rows_source, list) else [])
    ]
    rows = _deduplicate_attendance_wjx_sheet_rows(rows, columns)

    default_document = _create_default_attendance_wjx_sheet_document()
    source_column_configs = source.get("column_configs") if isinstance(source.get("column_configs"), dict) else {}
    column_configs = dict(source_column_configs)
    for header, config in default_document["column_configs"].items():
        if header == ATTENDANCE_WJX_AI_COLUMN and source_has_ai_column:
            if isinstance(column_configs.get(header), dict):
                column_configs[header] = dict(column_configs[header])
            continue
        if not isinstance(column_configs.get(header), dict):
            column_configs[header] = dict(config)
            continue
        merged_config = dict(config)
        merged_config.update(column_configs[header])
        column_configs[header] = merged_config
    for header in ATTENDANCE_WJX_DEPRECATED_DATA_COLUMNS:
        column_configs.pop(header, None)
    if not source_has_ai_column:
        ai_config = dict(column_configs.get(ATTENDANCE_WJX_AI_COLUMN) or {})
        ai_config.setdefault("display_mode", "wrap")
        column_configs[ATTENDANCE_WJX_AI_COLUMN] = ai_config
    if not isinstance(source_column_configs.get("考勤负责人"), dict) and isinstance(column_configs.get("课程"), dict):
        owner_config = dict(column_configs["课程"])
        owner_config.setdefault("display_mode", "single_line")
        column_configs["考勤负责人"] = owner_config
    elif not isinstance(column_configs.get("考勤负责人"), dict):
        course_config = column_configs.get("课程")
        column_configs["考勤负责人"] = dict(course_config) if isinstance(course_config, dict) else {"display_mode": "single_line"}

    view_settings = dict(source.get("view_settings") if isinstance(source.get("view_settings"), dict) else {})
    view_settings.setdefault("show_row_numbers", True)
    view_settings.setdefault("row_marker_numbering", "global")
    view_settings.setdefault("row_marker_origin", "sheet")
    view_settings.setdefault("show_column_markers", True)
    view_settings.setdefault("column_marker_style", "letters")
    view_settings.setdefault("frozen_column_count", 0)
    pagination = dict(view_settings.get("pagination") if isinstance(view_settings.get("pagination"), dict) else {})
    pagination.setdefault("enabled", True)
    pagination.setdefault("page_size", 100)
    view_settings["pagination"] = pagination

    normalized = {
        **source,
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "column_configs": column_configs,
        "view_settings": view_settings,
    }
    cell_meta = _remap_attendance_wjx_sheet_cell_meta_columns(
        source.get("cell_meta"),
        source_columns=source_columns,
        target_columns=columns,
    )
    if cell_meta is not None:
        normalized["cell_meta"] = _strip_attendance_wjx_sheet_cell_meta_links(cell_meta)
    column_widths = _remap_attendance_wjx_sheet_column_widths(
        source.get("column_widths"),
        source_columns=source_columns,
        target_columns=columns,
    )
    if column_widths is not None:
        normalized["column_widths"] = column_widths
    grid_rows = source.get("grid_rows")
    if isinstance(grid_rows, list):
        data_start_row = min(_normalize_attendance_wjx_sheet_data_start_row(normalized), len(grid_rows))
        try:
            field_row_index = int(source.get("field_row_index") or 0)
        except (TypeError, ValueError):
            field_row_index = 0
        prefix_rows = []
        for prefix_index, row in enumerate(grid_rows[:data_start_row]):
            if prefix_index == field_row_index:
                prefix_rows.append(list(columns))
                continue
            prefix_rows.append(_normalize_attendance_wjx_sheet_row(row, columns, source_columns=source_columns))
        normalized["grid_rows"] = [*prefix_rows, *rows]
        normalized["data_start_row"] = data_start_row
    _apply_attendance_wjx_sheet_inline_links_to_rows(
        rows,
        rows_source,
        source_columns=source_columns,
        target_columns=columns,
    )
    if isinstance(normalized.get("grid_rows"), list):
        data_start_row = _normalize_attendance_wjx_sheet_data_start_row(normalized)
        normalized["grid_rows"] = [*normalized["grid_rows"][:data_start_row], *rows]
    return normalized


def _get_attendance_wjx_sheet_column_index(columns: list[str], header: str) -> int:
    try:
        return columns.index(header)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"问卷数据表缺少字段：{header}") from exc


def _get_attendance_wjx_sheet_cell(row: list[str], columns: list[str], header: str) -> str:
    index = _get_attendance_wjx_sheet_column_index(columns, header)
    return _normalize_attendance_wjx_sheet_cell(row[index] if index < len(row) else "")


def _set_attendance_wjx_sheet_cell_link(
    document_json: dict[str, Any],
    *,
    row_index: int,
    column_index: int,
    url: str,
) -> bool:
    row_offset = _normalize_attendance_wjx_sheet_data_start_row(document_json)
    normalized_url = _normalize_attendance_wjx_sheet_cell(url)
    rows = document_json.get("rows")
    if not isinstance(rows, list) or row_index < 0 or row_index >= len(rows):
        return False
    row = rows[row_index]
    if not isinstance(row, list):
        return False
    while len(row) <= column_index:
        row.append("")

    current_cell = row[column_index]
    current_value = _extract_inline_cell_text(current_cell)
    current_url = _extract_inline_cell_link_url(current_cell)
    next_cell: Any = (
        {"value": current_value, "link": {"url": normalized_url}}
        if normalized_url
        else current_value
    )
    changed = next_cell != current_cell or current_url != normalized_url
    row[column_index] = next_cell

    grid_rows = document_json.get("grid_rows")
    document_row_index = row_index + row_offset
    if isinstance(grid_rows, list) and 0 <= document_row_index < len(grid_rows):
        grid_row = grid_rows[document_row_index]
        if isinstance(grid_row, list):
            while len(grid_row) <= column_index:
                grid_row.append("")
            if grid_row[column_index] != next_cell:
                grid_row[column_index] = next_cell
                changed = True

    cell_meta = dict(document_json.get("cell_meta") if isinstance(document_json.get("cell_meta"), dict) else {})
    for meta_row in (document_row_index, row_index):
        key = f"{meta_row}:{column_index}"
        entry = cell_meta.get(key)
        if isinstance(entry, dict) and "link" in entry:
            next_entry = dict(entry)
            next_entry.pop("link", None)
            if next_entry:
                cell_meta[key] = next_entry
            else:
                cell_meta.pop(key, None)
            changed = True
    document_json["cell_meta"] = cell_meta
    return changed


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

    row_offset = _normalize_attendance_wjx_sheet_data_start_row(document_json)
    insert_document_row = insert_index + row_offset
    shifted: dict[str, Any] = {}
    for key, value in cell_meta.items():
        position = _parse_attendance_wjx_sheet_cell_meta_key(key)
        if position is None:
            shifted[str(key)] = value
            continue

        row_index, column_index = position
        next_row_index = row_index + amount if row_index >= insert_document_row else row_index
        shifted[f"{next_row_index}:{column_index}"] = value
    document_json["cell_meta"] = shifted


def _sync_attendance_wjx_sheet_course_info_for_row(
    document_json: dict[str, Any],
    *,
    row_index: int,
    row: list[str],
    columns: list[str],
    course_link_map: dict[str, str] | None,
    course_owner_map: dict[str, str] | None = None,
) -> bool:
    course_column_index = _get_attendance_wjx_sheet_column_index(columns, "课程")
    course_name = _normalize_attendance_wjx_sheet_cell(row[course_column_index] if course_column_index < len(row) else "")
    changed = False
    if course_link_map is not None:
        changed = _set_attendance_wjx_sheet_cell_link(
            document_json,
            row_index=row_index,
            column_index=course_column_index,
            url=course_link_map.get(course_name, ""),
        ) or changed

    if course_owner_map is not None:
        course_owner = _normalize_attendance_wjx_course_owner_display(course_owner_map.get(course_name, ""))
        owner_column_index = _get_attendance_wjx_sheet_column_index(columns, "考勤负责人")
        current_owner = _normalize_attendance_wjx_sheet_cell(
            row[owner_column_index] if owner_column_index < len(row) else ""
        )
        next_owner = course_owner if course_owner else _normalize_attendance_wjx_course_owner_display(current_owner)
        if next_owner != current_owner:
            _set_attendance_wjx_sheet_cell(row, columns, "考勤负责人", next_owner)
            changed = True

    return changed


def _clear_attendance_wjx_sheet_column_links(
    document_json: dict[str, Any],
    *,
    column_index: int,
    row_count: int,
) -> bool:
    if row_count <= 0:
        return False

    row_offset = _normalize_attendance_wjx_sheet_data_start_row(document_json)
    rows = document_json.get("rows")
    grid_rows = document_json.get("grid_rows")
    changed = False
    if isinstance(rows, list):
        for row_index, row in enumerate(rows[:row_count]):
            if not isinstance(row, list) or column_index >= len(row):
                continue
            cell = row[column_index]
            if _extract_inline_cell_link_url(cell):
                row[column_index] = _extract_inline_cell_text(cell)
                changed = True
    if isinstance(grid_rows, list):
        for row_index in range(row_offset, min(row_offset + row_count, len(grid_rows))):
            row = grid_rows[row_index]
            if not isinstance(row, list) or column_index >= len(row):
                continue
            cell = row[column_index]
            if _extract_inline_cell_link_url(cell):
                row[column_index] = _extract_inline_cell_text(cell)
                changed = True

    cell_meta = document_json.get("cell_meta")
    if not isinstance(cell_meta, dict):
        return changed

    candidate_rows = set(range(row_count)) | set(range(row_offset, row_offset + row_count))
    next_meta = dict(cell_meta)
    for row_index in candidate_rows:
        key = f"{row_index}:{column_index}"
        entry = next_meta.get(key)
        if not isinstance(entry, dict) or "link" not in entry:
            continue
        next_entry = dict(entry)
        next_entry.pop("link", None)
        if next_entry:
            next_meta[key] = next_entry
        else:
            next_meta.pop(key, None)
        changed = True

    if changed:
        document_json["cell_meta"] = next_meta
    return changed


def _apply_attendance_wjx_sheet_course_links(
    document_json: dict[str, Any],
    course_link_map: dict[str, str] | None,
    course_owner_map: dict[str, str] | None = None,
) -> tuple[dict[str, Any], bool]:
    source_rows = document_json.get("rows") if isinstance(document_json, dict) else None
    source_row_count = len(source_rows) if isinstance(source_rows, list) else 0
    document = _normalize_attendance_wjx_sheet_document(document_json)
    changed = len(document["rows"]) != source_row_count
    if course_link_map is None and course_owner_map is None:
        return document, changed

    columns = list(document["columns"])
    if course_link_map is not None:
        course_column_index = _get_attendance_wjx_sheet_column_index(columns, "课程")
        changed = _clear_attendance_wjx_sheet_column_links(
            document,
            column_index=course_column_index,
            row_count=len(document["rows"]),
        ) or changed
    for row_index, row in enumerate(document["rows"]):
        changed = _sync_attendance_wjx_sheet_course_info_for_row(
            document,
            row_index=row_index,
            row=row,
            columns=columns,
            course_link_map=course_link_map,
            course_owner_map=course_owner_map,
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
    revision_result = dict(entry.revision_result_json or {})
    ai_precheck = revision_result.get("ai_precheck") if isinstance(revision_result.get("ai_precheck"), dict) else {}
    ai_text = ai_precheck.get("report") or ai_precheck.get("summary", "")
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
        ATTENDANCE_WJX_AI_COLUMN: ai_text,
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
        ATTENDANCE_WJX_AI_COLUMN: "",
    }


def _upsert_attendance_wjx_sheet_values(
    document_json: dict[str, Any],
    values: dict[str, Any],
    *,
    preserve_process_status: bool = True,
    course_link_map: dict[str, str] | None = None,
    course_owner_map: dict[str, str] | None = None,
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
    course_info_changed = _sync_attendance_wjx_sheet_course_info_for_row(
        document,
        row_index=row_index,
        row=row,
        columns=columns,
        course_link_map=course_link_map,
        course_owner_map=course_owner_map,
    )
    return document, inserted, inserted or row != original_row or course_info_changed


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
        select(WorkbookSheetLink).where(WorkbookSheetLink.workbook_id.in_(workbook_ref_aliases(workbook)))
    ).all()
    if not links:
        return None

    linked_sheet_ids = [link.sheet_id for link in links]
    sheet_map = load_sheets_by_refs(session, linked_sheet_ids)
    for sheet in {str(sheet.id): sheet for sheet in sheet_map.values()}.values():
        if sheet.title == ATTENDANCE_WJX_DATA_SHEET_TITLE:
            return sheet
    return None


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
        document_identity = allocate_new_sheet_identity(session)
        document = SheetDocument(
            id=document_identity.primary_id,
            numeric_id=document_identity.numeric_id,
            legacy_id=document_identity.legacy_id,
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
            document.numeric_id = _get_next_sheet_numeric_id(session, str(document.legacy_id or document.id))
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
        .where(WorkbookSheetLink.workbook_id.in_(workbook_ref_aliases(workbook)))
        .where(WorkbookSheetLink.sheet_id.in_(sheet_ref_aliases(document)))
    ).first()
    if link is None:
        workbook_ref = workbook_public_id(workbook)
        sheet_ref = sheet_public_id(document)
        session.add(
            WorkbookSheetLink(
                workbook_id=workbook_ref,
                sheet_id=sheet_ref,
                order_index=_get_next_workbook_link_order(session, workbook_ref),
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


def _find_existing_attendance_wjx_sheet_location_document(session: Session) -> SheetDocument | None:
    workbook = _get_attendance_wjx_data_workbook(session)
    if workbook is None:
        return None

    document = _find_attendance_wjx_sheet_document(session, workbook)
    if document is None or document.numeric_id is None:
        return None

    link = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id.in_(workbook_ref_aliases(workbook)))
        .where(WorkbookSheetLink.sheet_id.in_(sheet_ref_aliases(document)))
    ).first()
    if link is None:
        return None

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
    course_link_map, course_owner_map = _get_feedback_course_maps_from_summary_sheet(session)
    for entry in entries:
        next_document, _inserted, _changed = _upsert_attendance_wjx_sheet_values(
            next_document,
            _entry_to_attendance_wjx_sheet_values(entry),
            preserve_process_status=False,
            course_link_map=course_link_map,
            course_owner_map=course_owner_map,
        )
    return next_document


def _sync_attendance_wjx_sheet_rows_from_entries(
    session: Session,
    document_json: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    activity_ids = [FIXED_WJX_TEMPLATE_ACTIVITY_ID, LOCAL_FEEDBACK_ACTIVITY_ID]
    entries = session.exec(
        select(AttendanceWjxDataEntry)
        .where(AttendanceWjxDataEntry.activity_id.in_(activity_ids))
        .order_by(AttendanceWjxDataEntry.seq.asc(), AttendanceWjxDataEntry.id.asc())
    ).all()
    if not entries:
        return _normalize_attendance_wjx_sheet_document(document_json), False

    next_document = _normalize_attendance_wjx_sheet_document(document_json)
    columns = list(next_document["columns"])
    rows = [list(row) for row in next_document["rows"]]
    row_index_by_seq: dict[int, int] = {}
    for row_index, row in enumerate(rows):
        seq = _parse_attendance_wjx_sheet_seq(_get_attendance_wjx_sheet_cell(row, columns, "序号"))
        if seq is not None:
            row_index_by_seq.setdefault(seq, row_index)

    course_link_map, course_owner_map = _get_feedback_course_maps_from_summary_sheet(session)
    changed = False
    for entry in entries:
        seq = _parse_attendance_wjx_sheet_seq(entry.seq)
        if seq is None:
            continue
        values = _entry_to_attendance_wjx_sheet_values(entry)
        row_index = row_index_by_seq.get(seq)
        inserted = row_index is None
        if inserted:
            row = [""] * len(columns)
            row_index = _get_attendance_wjx_sheet_insert_index(rows, columns, seq)
            rows.insert(row_index, row)
            row_index_by_seq = {
                current_seq: (current_index + 1 if current_index >= row_index else current_index)
                for current_seq, current_index in row_index_by_seq.items()
            }
            row_index_by_seq[seq] = row_index
            _shift_attendance_wjx_sheet_cell_meta_rows_for_insert(
                next_document,
                insert_index=row_index,
                amount=1,
            )
        else:
            row = list(rows[row_index])

        original_row = list(row)
        existing_process_status = _get_attendance_wjx_sheet_cell(row, columns, "处理状态")
        for header in ATTENDANCE_WJX_DATA_COLUMNS:
            if header == "处理状态" and existing_process_status:
                continue
            if header in values:
                _set_attendance_wjx_sheet_cell(row, columns, header, values.get(header))

        rows[row_index] = row
        next_document["rows"] = rows
        course_info_changed = _sync_attendance_wjx_sheet_course_info_for_row(
            next_document,
            row_index=row_index,
            row=row,
            columns=columns,
            course_link_map=course_link_map,
            course_owner_map=course_owner_map,
        )
        changed = changed or inserted or row != original_row or course_info_changed
    return next_document, changed


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
    course_link_url: str = "",
) -> None:
    document = _ensure_attendance_wjx_sheet_document(session, create=True)
    if document is None:
        return
    course_link_map, course_owner_map = _get_feedback_course_maps_from_summary_sheet(session)
    normalized_course_link_url = _normalize_attendance_wjx_sheet_cell(course_link_url)
    if normalized_course_link_url and entry.course_name:
        course_link_map = dict(course_link_map or {})
        course_link_map[_normalize_attendance_wjx_sheet_cell(entry.course_name)] = normalized_course_link_url
    next_document, _inserted, changed = _upsert_attendance_wjx_sheet_values(
        dict(document.document_json or {}),
        _entry_to_attendance_wjx_sheet_values(entry),
        preserve_process_status=preserve_process_status,
        course_link_map=course_link_map,
        course_owner_map=course_owner_map,
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
    course_link_map, course_owner_map = _get_feedback_course_maps_from_summary_sheet(session)
    for row in rows:
        values = _wjx_raw_row_to_attendance_wjx_sheet_values(row)
        if values is None:
            continue
        next_document, _inserted, row_changed = _upsert_attendance_wjx_sheet_values(
            next_document,
            values,
            preserve_process_status=True,
            course_link_map=course_link_map,
            course_owner_map=course_owner_map,
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
    ai_report = _get_attendance_wjx_sheet_cell(row, columns, ATTENDANCE_WJX_AI_COLUMN)
    revision_result: dict[str, Any] = {}
    if ai_report:
        revision_result["ai_precheck"] = {
            "summary": "",
            "report": ai_report,
        }
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
        "revision_result": revision_result,
        "raw_row": {},
        "synced_at": updated_at,
        "created_at": updated_at,
        "updated_at": updated_at,
    }
    item["foreground_colors"] = _build_attendance_wjx_sheet_foreground_colors(item)
    return item


def _sync_attendance_wjx_entries_from_sheet_document(
    session: Session,
    document_json: dict[str, Any],
) -> int:
    document = _normalize_attendance_wjx_sheet_document(document_json)
    columns = list(document["columns"])
    updated_count = 0
    now = time.time()
    for row in document["rows"]:
        seq = _parse_attendance_wjx_sheet_seq(_get_attendance_wjx_sheet_cell(row, columns, "序号"))
        if seq is None:
            continue

        process_status = _get_attendance_wjx_sheet_cell(row, columns, "处理状态")
        entries = session.exec(
            select(AttendanceWjxDataEntry)
            .where(AttendanceWjxDataEntry.activity_id.in_([FIXED_WJX_TEMPLATE_ACTIVITY_ID, LOCAL_FEEDBACK_ACTIVITY_ID]))
            .where(AttendanceWjxDataEntry.seq == seq)
        ).all()
        for entry in entries:
            if entry.process_status == process_status and entry.process_note == process_status:
                continue
            entry.process_status = process_status
            entry.process_note = process_status
            entry.updated_at = now
            session.add(entry)
            updated_count += 1
    if updated_count:
        session.commit()
    return updated_count


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
                    "revision_result",
                )
            )
            haystack = f"{haystack} {item.get('revision_result')}"
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
    return _normalize_wjx_data_text(value)


def _normalize_feedback_sheet_columns(document_json: dict[str, Any]) -> list[str]:
    columns = document_json.get("columns")
    if not isinstance(columns, list):
        return []
    return [str(column or "").strip() for column in columns]


def _extract_feedback_sheet_rows(document_json: dict[str, Any]) -> list[Any]:
    rows = document_json.get("rows")
    return list(rows) if isinstance(rows, list) else []


def _normalize_feedback_sheet_data_start_row(document_json: dict[str, Any]) -> int:
    grid_rows = document_json.get("grid_rows")
    if not isinstance(grid_rows, list) or not grid_rows:
        return 0
    try:
        return max(int(document_json.get("data_start_row") or 0), 0)
    except (TypeError, ValueError):
        return 0


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
    meta_row_index = row_index + _normalize_feedback_sheet_data_start_row(document_json)
    rows = _extract_feedback_sheet_rows(document_json)
    columns = _normalize_feedback_sheet_columns(document_json)
    if 0 <= row_index < len(rows):
        url = _extract_inline_cell_link_url(_extract_feedback_sheet_cell(rows[row_index], column_index, columns))
        if url:
            return url
    grid_rows = document_json.get("grid_rows")
    if isinstance(grid_rows, list) and 0 <= meta_row_index < len(grid_rows):
        url = _extract_inline_cell_link_url(_extract_feedback_sheet_cell(grid_rows[meta_row_index], column_index, columns))
        if url:
            return url
    return ""


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


def _extract_feedback_course_owner_map_from_sheet(document_json: dict[str, Any]) -> dict[str, str]:
    columns = _normalize_feedback_sheet_columns(document_json)
    rows = _extract_feedback_sheet_rows(document_json)
    online_sheet_index = _find_feedback_course_column_index(columns, "online_sheet")
    course_name_index = _find_feedback_course_column_index(columns, "course_name")
    owner_index = _find_feedback_course_column_index(columns, "course_owner")
    if owner_index is None or (online_sheet_index is None and course_name_index is None):
        return {}

    result: dict[str, str] = {}
    for row in rows:
        course_owner = _normalize_attendance_wjx_course_owner_display(
            _extract_feedback_sheet_cell(row, owner_index, columns)
        )
        if not course_owner:
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
                result[name] = course_owner
    return result


def _get_feedback_course_maps_from_summary_sheet(
    session: Session,
) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    source_sheet = session.exec(
        select(SheetDocument).where(SheetDocument.numeric_id == FEEDBACK_COURSE_SOURCE_SHEET_ID)
    ).first()
    if source_sheet is None:
        return None, None
    document_json = dict(source_sheet.document_json or {})
    return (
        _extract_feedback_course_link_map_from_sheet(document_json),
        _extract_feedback_course_owner_map_from_sheet(document_json),
    )


def _get_feedback_course_link_map_from_summary_sheet(session: Session) -> dict[str, str] | None:
    course_link_map, _course_owner_map = _get_feedback_course_maps_from_summary_sheet(session)
    return course_link_map


def _get_feedback_course_owner_map_from_summary_sheet(session: Session) -> dict[str, str] | None:
    _course_link_map, course_owner_map = _get_feedback_course_maps_from_summary_sheet(session)
    return course_owner_map


def _sync_attendance_wjx_sheet_course_links(
    session: Session,
    document_json: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    course_link_map, course_owner_map = _get_feedback_course_maps_from_summary_sheet(session)
    return _apply_attendance_wjx_sheet_course_links(
        document_json,
        course_link_map,
        course_owner_map,
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


def _sum_successful_refund_detail_amount(rows: list[dict[str, Any]]) -> float:
    total = 0.0
    for row in rows:
        status = _normalize_order_history_text(row.get("refund_status"))
        if status and "成功" not in status:
            continue
        amount = _coerce_order_history_number(row.get("refund_amount"))
        if amount is not None:
            total += amount
    return round(total, 2)


def _refund_guard_order_id(row: dict[str, Any]) -> str:
    return (
        _normalize_attendance_order_id(row.get("商户订单号"))
        or _normalize_attendance_order_id(row.get("微信支付订单号"))
    )


def _verify_refund_execution_confirmed(
    entry_snapshot: dict[str, Any],
    execution_payload: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    rows = [dict(row) for row in list(result.get("rows") or []) if isinstance(row, dict)]
    checked: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    login_users = list(execution_payload.get("login_users") or [])

    for row in rows:
        refund_amount = _coerce_order_history_number(row.get("退款额度")) or 0.0
        result_text = _normalize_order_history_text(row.get("执行退款"))
        if refund_amount <= 0 or "已退款" not in result_text:
            continue

        order_id = _refund_guard_order_id(row)
        expected_total = _coerce_order_history_number(row.get("已返款"))
        if not order_id or expected_total is None:
            failures.append({
                "student_name": _normalize_order_history_text(row.get("学员名称")),
                "order_id": order_id,
                "refund_amount": refund_amount,
                "expected_refunded": expected_total,
                "actual_refunded": None,
                "reason": "缺少订单号或目标已返款金额，无法确认支付侧退款结果",
            })
            continue

        detail = _execute_order_refund_details_on_entry(
            entry_snapshot,
            {
                "order_id": order_id,
                "query_type": "merchant_order" if not order_id.isdigit() else "pay_order",
                "login_users": login_users,
            },
        )
        actual_total = _sum_successful_refund_detail_amount(list(detail.get("rows") or []))
        check = {
            "student_name": _normalize_order_history_text(row.get("学员名称")),
            "order_id": order_id,
            "refund_amount": round(refund_amount, 2),
            "expected_refunded": round(expected_total, 2),
            "actual_refunded": actual_total,
        }
        checked.append(check)
        if actual_total + 0.01 < expected_total:
            failures.append({**check, "reason": "支付侧成功退款累计额未达到本次执行后的目标已返款金额"})

    if failures:
        detail_text = "；".join(
            (
                f"{item.get('student_name') or item.get('order_id')}: "
                f"支付侧{item.get('actual_refunded')} / 目标{item.get('expected_refunded')}"
            )
            for item in failures[:5]
        )
        raise RuntimeError(
            "退款执行后支付侧确认失败，已阻止写入退款历史和后续流程推进。"
            f"{detail_text}"
        )

    return {"checked": checked, "failure_count": 0}


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


def _normalize_scalar_wjx_data_text(value: Any) -> str:
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


def _normalize_wjx_data_text(value: Any) -> str:
    return _extract_inline_cell_text(value)


def _normalize_required_feedback_text(value: Any, *, field_name: str) -> str:
    text = _normalize_wjx_data_text(value)
    if not text:
        raise HTTPException(status_code=400, detail=f"{field_name}不能为空")
    return text


def _is_generic_feedback_course_name(value: Any) -> bool:
    normalized = re.sub(r"\s+", "", _normalize_wjx_data_text(value))
    return normalized in {"考勤表", "问卷数据"}


def _normalize_feedback_workbook_course_title(value: Any) -> str:
    text = _normalize_wjx_data_text(value).strip()
    if not text or _is_generic_feedback_course_name(text):
        return ""
    text = re.sub(r"^d?\d{6,8}(第\d+届(?:念住|觉观))$", r"\1", text)
    return text


def _is_feedback_course_attendance_sheet(sheet: SheetDocument | None) -> bool:
    return bool(
        sheet is not None
        and sheet.owner_type == "course_workbook"
        and sheet.sheet_key == "attendance"
    )


def _build_feedback_workbook_url(workbook: WorkbookDocument, sheet: SheetDocument | None = None) -> str:
    workbook_id = workbook.numeric_id
    if workbook_id is None:
        return ""
    if sheet is not None and sheet.numeric_id is not None:
        return f"/workbook/{workbook_id}?sheet={sheet.numeric_id}"
    return f"/workbook/{workbook_id}"


def _resolve_feedback_course_from_context(
    session: Session,
    payload: AttendanceFeedbackSubmitRequest,
) -> AttendanceFeedbackResolvedCourse:
    sheet = None
    if payload.sheet_id is not None:
        sheet = session.exec(
            select(SheetDocument).where(SheetDocument.numeric_id == int(payload.sheet_id))
        ).first()

    if _is_feedback_course_attendance_sheet(sheet):
        assert sheet is not None
        sheet_refs = sheet_ref_aliases(sheet)
        links = session.exec(
            select(WorkbookSheetLink)
            .where(WorkbookSheetLink.sheet_id.in_(sheet_refs))
            .order_by(WorkbookSheetLink.order_index, WorkbookSheetLink.created_at)
        ).all()
        workbook_map = load_workbooks_by_refs(session, [link.workbook_id for link in links])
        if payload.workbook_id is not None:
            for workbook in workbook_map.values():
                if workbook.numeric_id == int(payload.workbook_id):
                    title = _normalize_feedback_workbook_course_title(workbook.title)
                    if title:
                        return AttendanceFeedbackResolvedCourse(
                            name=title,
                            link_url=_build_feedback_workbook_url(workbook, sheet),
                            strong_context=True,
                        )
        for link in links:
            workbook = workbook_map.get(str(link.workbook_id))
            title = _normalize_feedback_workbook_course_title(workbook.title if workbook is not None else "")
            if title:
                return AttendanceFeedbackResolvedCourse(
                    name=title,
                    link_url=_build_feedback_workbook_url(workbook, sheet),
                    strong_context=True,
                )

    if payload.workbook_id is not None and sheet is None:
        workbook = session.exec(
            select(WorkbookDocument).where(WorkbookDocument.numeric_id == int(payload.workbook_id))
        ).first()
        title = _normalize_feedback_workbook_course_title(workbook.title if workbook is not None else "")
        if title:
            return AttendanceFeedbackResolvedCourse(
                name=title,
                link_url=_build_feedback_workbook_url(workbook, sheet),
            )

    return AttendanceFeedbackResolvedCourse()


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
    course_link_url = ""
    resolved_course = AttendanceFeedbackResolvedCourse()
    if _is_generic_feedback_course_name(course_name) or payload.workbook_id is not None or payload.sheet_id is not None:
        resolved_course = _resolve_feedback_course_from_context(session, payload)
    has_conflicting_strong_context = (
        resolved_course.strong_context
        and resolved_course.name
        and _normalize_feedback_history_match_text(course_name) != _normalize_feedback_history_match_text(resolved_course.name)
    )
    if _is_generic_feedback_course_name(course_name) or has_conflicting_strong_context:
        course_name = resolved_course.name
        course_link_url = resolved_course.link_url
    if not course_name:
        raise HTTPException(status_code=400, detail="所属课程不能是考勤表，请从课程或工作簿名称提交")
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
        course_link_url=course_link_url,
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


def _normalize_feedback_history_match_text(value: Any) -> str:
    return re.sub(r"\s+", "", _normalize_wjx_data_text(value))


def _collect_attendance_feedback_history_source_items(session: Session) -> list[dict[str, Any]]:
    sheet_document = _ensure_attendance_wjx_sheet_document(session, create=False)
    if sheet_document is not None:
        document_json = _normalize_attendance_wjx_sheet_document(dict(sheet_document.document_json or {}))
        columns = list(document_json["columns"])
        return [
            item
            for row in document_json["rows"]
            if (
                item := _serialize_attendance_wjx_sheet_row(
                    row,
                    columns,
                    updated_at=float(sheet_document.updated_at or 0.0),
                )
            )
            is not None
        ]

    template = _get_fixed_wjx_template_payload()
    activity_ids = _list_attendance_wjx_data_activity_ids(template)
    statement = (
        select(AttendanceWjxDataEntry)
        .where(AttendanceWjxDataEntry.activity_id.in_(activity_ids))
        .order_by(
            AttendanceWjxDataEntry.seq.desc(),
            AttendanceWjxDataEntry.synced_at.desc(),
            AttendanceWjxDataEntry.id.desc(),
        )
    )
    return [serialize_attendance_wjx_data_entry(row) for row in session.exec(statement).all()]


def _build_attendance_feedback_history(
    session: Session,
    *,
    course_name: str,
    student_id_text: str = "",
    student_name: str = "",
    limit: int = 8,
) -> AttendanceFeedbackHistoryResponse:
    course_key = _normalize_feedback_history_match_text(course_name)
    student_id_key = _normalize_feedback_history_match_text(student_id_text)
    student_name_key = _normalize_feedback_history_match_text(student_name)
    if not course_key or (not student_id_key and not student_name_key):
        return AttendanceFeedbackHistoryResponse(items=[], total=0)

    matched_items: list[dict[str, Any]] = []
    for item in _collect_attendance_feedback_history_source_items(session):
        item_course_key = _normalize_feedback_history_match_text(item.get("course_name"))
        if item_course_key != course_key:
            continue

        item_student_id_key = _normalize_feedback_history_match_text(item.get("student_id_text"))
        item_student_name_key = _normalize_feedback_history_match_text(item.get("student_name"))
        student_id_matches = bool(student_id_key and item_student_id_key == student_id_key)
        student_name_matches = bool(student_name_key and item_student_name_key == student_name_key)
        if not student_id_matches and not student_name_matches:
            continue
        matched_items.append(item)

    matched_items.sort(
        key=lambda item: (
            int(item.get("seq") or 0),
            float(item.get("synced_at") or 0.0),
            int(item.get("id") or 0),
        ),
        reverse=True,
    )
    total = len(matched_items)
    normalized_limit = min(max(1, int(limit or 8)), 30)
    return AttendanceFeedbackHistoryResponse(
        items=[AttendanceWjxDataItem.model_validate(item) for item in matched_items[:normalized_limit]],
        total=total,
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


def _resolve_course_data_flow_config_payload(session: Session) -> dict[str, Any]:
    service_config = get_or_create_attendance_service_config(session)
    data_flow_config = get_attendance_course_data_flow_config(session)
    browser_device_entry_id = normalize_attendance_device_entry_id(data_flow_config.get("browser_device_entry_id"))
    fallback_browser_device_entry_id = normalize_attendance_device_entry_id(service_config.execution_device_entry_id)
    effective_browser_device_entry_id = browser_device_entry_id or fallback_browser_device_entry_id
    data_device_entry_id = normalize_attendance_device_entry_id(data_flow_config.get("data_device_entry_id"))
    step_device_entry_ids = normalize_attendance_step_device_entry_ids(data_flow_config.get("step_device_entry_ids"))
    browser_device = session.get(UserDevice, effective_browser_device_entry_id) if effective_browser_device_entry_id else None
    data_device = session.get(UserDevice, data_device_entry_id) if data_device_entry_id else None
    return {
        "course_data_flow": {
            "browser_device_entry_id": browser_device_entry_id or None,
            "fallback_browser_device_entry_id": fallback_browser_device_entry_id or None,
            "effective_browser_device_entry_id": effective_browser_device_entry_id or None,
            "data_device_entry_id": data_device_entry_id or None,
            "step_device_entry_ids": step_device_entry_ids,
            "step_runners": build_attendance_course_data_step_runner_configs(session, service_config, data_flow_config),
        },
        "current_browser_device": serialize_user_device(browser_device),
        "current_data_device": serialize_user_device(data_device),
    }


def _ensure_owned_device_for_selection(entry: UserDevice, current_user: User) -> None:
    if entry.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="只能从你自己的设备资产中选择执行设备")
    if not entry.is_active:
        raise HTTPException(status_code=400, detail="当前执行设备已停用")


def _ensure_owned_attendance_device_for_config(
    session: Session,
    entry_id: str,
    current_user: User,
    *,
    role_label: str,
) -> UserDevice:
    entry = get_user_device_or_404(session, entry_id)
    if entry.user_id != current_user.id:
        raise HTTPException(status_code=403, detail=f"只能从你自己的设备资产中选择{role_label}")
    if not entry.is_active:
        raise HTTPException(status_code=400, detail=f"当前{role_label}已停用")
    return entry


def _normalize_step_device_entry_ids_for_update(value: dict[str, Optional[str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_step, raw_entry_id in value.items():
        try:
            step_number = int(raw_step)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="课程数据 step_device_entry_ids 只支持 step1-step6") from exc
        if step_number < 1 or step_number > 6:
            raise HTTPException(status_code=400, detail="课程数据 step_device_entry_ids 只支持 step1-step6")
        entry_id = _normalize_optional_id(raw_entry_id)
        if entry_id:
            result[str(step_number)] = entry_id
    return result


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


def _build_user_device_from_snapshot(entry_snapshot: dict[str, Any], *, server_url: str, token: str) -> UserDevice:
    return UserDevice(
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


def _post_remote_attendance_json(
    entry_snapshot: dict[str, Any],
    *,
    path: str,
    payload: dict[str, Any],
    timeout: int,
) -> requests.Response:
    server_url = (entry_snapshot.get("server_url") or "").rstrip("/")
    token = str(entry_snapshot.get("token") or "")
    if not server_url or not token:
        raise RuntimeError("远程执行设备缺少后端地址或访问令牌")

    entry = _build_user_device_from_snapshot(entry_snapshot, server_url=server_url, token=token)
    with requests.Session() as http:
        http.trust_env = False
        return http.post(
            f"{server_url}{path}",
            json=payload,
            headers=_build_remote_headers(entry),
            timeout=timeout,
        )


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

    response = _post_remote_attendance_json(
        entry_snapshot,
        path="/api/device-control/attendance/order/execute",
        payload=execution_payload,
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

    def post_remote(payload: dict[str, Any]) -> dict[str, Any]:
        response = _post_remote_attendance_json(
            entry_snapshot,
            path="/api/device-control/attendance/order/refund-details",
            payload=payload,
            timeout=600,
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = response.text.strip()
            raise RuntimeError(detail or f"远程执行失败，HTTP {response.status_code}")
        return response.json()

    result = post_remote(execution_payload)
    if result.get("rows") or str(execution_payload.get("query_type") or "auto").strip().lower() != "auto":
        return result

    order_id = _normalize_attendance_order_id(execution_payload.get("order_id"))
    if not order_id:
        return result
    precise_query_type = "pay_order" if order_id.isdigit() and order_id.startswith("42") else (
        "refund_id" if order_id.isdigit() else "merchant_order"
    )
    retry_result = post_remote({**execution_payload, "order_id": order_id, "query_type": precise_query_type})
    return retry_result if retry_result.get("rows") else result


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
        document_identity = allocate_new_sheet_identity(session)
        document = SheetDocument(
            id=document_identity.primary_id,
            numeric_id=document_identity.numeric_id,
            legacy_id=document_identity.legacy_id,
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
        document.numeric_id = _get_next_sheet_numeric_id(session, str(document.legacy_id or document.id))
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


@router.post("/header-tool/generate", response_model=AttendanceHeaderToolResponse)
def generate_attendance_header_tool(
    payload: AttendanceHeaderToolRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.header-tool")),
):
    ensure_can_use_attendance_service(current_user, session)
    return _build_attendance_header_tool_response(payload.course_name)


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


@router.get("/course-data-flow/config")
def get_attendance_course_data_flow_config_route(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.configs")),
):
    ensure_can_use_attendance_service(current_user, session)
    return _resolve_course_data_flow_config_payload(session)


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


@router.put("/course-data-flow/config")
def update_attendance_course_data_flow_config_route(
    payload: AttendanceCourseDataFlowConfigUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.configs")),
):
    current_user = ensure_can_manage_attendance_service(current_user)
    browser_device_entry_id = _normalize_optional_id(payload.browser_device_entry_id)
    data_device_entry_id = _normalize_optional_id(payload.data_device_entry_id)
    step_device_entry_ids: dict[str, str] | None = None

    if payload.browser_device_entry_id is not None and browser_device_entry_id is not None:
        _ensure_owned_attendance_device_for_config(
            session,
            browser_device_entry_id,
            current_user,
            role_label="课程数据浏览器设备",
        )
    if payload.data_device_entry_id is not None and data_device_entry_id is not None:
        _ensure_owned_attendance_device_for_config(
            session,
            data_device_entry_id,
            current_user,
            role_label="课程数据主机",
        )

    if payload.step_device_entry_ids is not None:
        step_device_entry_ids = _normalize_step_device_entry_ids_for_update(payload.step_device_entry_ids)
        for step_key, step_entry_id in step_device_entry_ids.items():
            default_role = get_attendance_course_data_step_default_role(int(step_key))
            role_label = "课程数据浏览器设备" if default_role == "browser_device" else "课程数据主机"
            _ensure_owned_attendance_device_for_config(
                session,
                step_entry_id,
                current_user,
                role_label=f"step{step_key}{role_label}",
            )

    update_attendance_course_data_flow_config(
        session,
        browser_device_entry_id=payload.browser_device_entry_id if payload.browser_device_entry_id is not None else None,
        data_device_entry_id=payload.data_device_entry_id if payload.data_device_entry_id is not None else None,
        step_device_entry_ids=step_device_entry_ids,
    )
    return _resolve_course_data_flow_config_payload(session)


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


@public_router.get("/wjx-feedback/history", response_model=AttendanceFeedbackHistoryResponse)
def list_attendance_feedback_history(
    course_name: str = Query(default=""),
    student_id_text: str = Query(default=""),
    student_name: str = Query(default=""),
    limit: int = Query(default=8, ge=1, le=30),
    session: Session = Depends(get_session),
):
    return _build_attendance_feedback_history(
        session,
        course_name=course_name,
        student_id_text=student_id_text,
        student_name=student_name,
        limit=limit,
    )


@router.get("/wjx-data/sheet", response_model=AttendanceWjxDataSheetLocation)
def get_attendance_wjx_data_sheet_location(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.wjx-data")),
):
    ensure_can_use_attendance_service(current_user, session)
    document = _find_existing_attendance_wjx_sheet_location_document(session)
    if document is None:
        document = _ensure_attendance_wjx_sheet_document(session, create=True)
    if document is None or document.numeric_id is None:
        raise HTTPException(status_code=404, detail="没有找到工作簿 2，无法创建问卷数据表")
    sheet_id = int(document.numeric_id)
    return AttendanceWjxDataSheetLocation(
        workbook_id=ATTENDANCE_WJX_DATA_WORKBOOK_ID,
        sheet_id=sheet_id,
        path=f"/workbook/{ATTENDANCE_WJX_DATA_WORKBOOK_ID}?sheet={sheet_id}",
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


def _resolve_attendance_wjx_precheck_entry(session: Session, entry_id: int) -> AttendanceWjxDataEntry:
    entry = session.exec(
        select(AttendanceWjxDataEntry)
        .where(AttendanceWjxDataEntry.activity_id.in_([FIXED_WJX_TEMPLATE_ACTIVITY_ID, LOCAL_FEEDBACK_ACTIVITY_ID]))
        .where(AttendanceWjxDataEntry.seq == entry_id)
        .order_by(AttendanceWjxDataEntry.id.desc())
    ).first()
    if entry is not None:
        return entry
    return get_attendance_wjx_data_entry_or_404(session, entry_id)


def _attendance_wjx_parse_workbook_sheet_url(url: Any) -> tuple[int | None, int | None]:
    text = _normalize_attendance_wjx_sheet_cell(url)
    if not text:
        return None, None
    parse_target = text
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", parse_target):
        parse_target = "/" + parse_target.lstrip("/")
        parse_target = f"http://codeyun.local{parse_target}"
    parsed = urlparse(parse_target)
    workbook_id: int | None = None
    sheet_id: int | None = None
    workbook_match = re.search(r"/workbook/(?P<id>\d+)", parsed.path)
    if workbook_match:
        workbook_id = int(workbook_match.group("id"))
    raw_sheet = (parse_qs(parsed.query).get("sheet") or [""])[0]
    if re.fullmatch(r"\d+", str(raw_sheet or "")):
        sheet_id = int(raw_sheet)
    return workbook_id, sheet_id


def _attendance_wjx_student_number_key(value: Any) -> tuple[int, ...] | tuple[str]:
    text = _normalize_attendance_wjx_sheet_cell(value)
    numbers = re.findall(r"\d+", text)
    if numbers:
        return tuple(int(number) for number in numbers)
    return (text,) if text else tuple()


def _attendance_wjx_unique_texts(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_attendance_wjx_sheet_cell(value)
        if not text or text.lower() == "none" or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _attendance_wjx_sheet_cell_text(
    note_sheets_api: Any,
    row: list[Any],
    columns: list[str],
    header: str,
) -> str:
    index = note_sheets_api._find_column_index(columns, header)
    if index is None or index < 0 or index >= len(row):
        return ""
    return note_sheets_api._normalize_sheet_text(row[index])


def _attendance_wjx_set_sheet_cell(
    note_sheets_api: Any,
    row: list[Any],
    columns: list[str],
    header: str,
    value: Any,
) -> bool:
    index = note_sheets_api._find_column_index(columns, header)
    if index is None or index < 0:
        return False
    while len(row) < len(columns):
        row.append("")
    next_value = "" if value is None else str(value)
    if row[index] == next_value:
        return False
    row[index] = next_value
    return True


def _attendance_wjx_note_sheet_rows(
    note_sheets_api: Any,
    document: SheetDocument,
) -> tuple[dict[str, Any], list[str], list[list[Any]]]:
    document_json = note_sheets_api._normalize_document_json(dict(document.document_json or {}))
    columns = note_sheets_api._normalize_document_columns(document_json)
    rows = [
        note_sheets_api._normalize_sheet_row(row, len(columns))
        for row in note_sheets_api._extract_document_rows(document_json)
    ]
    return document_json, columns, rows


def _attendance_wjx_persist_note_sheet_document(
    session: Session,
    note_sheets_api: Any,
    document: SheetDocument,
    next_document: dict[str, Any],
    *,
    actor: User,
) -> bool:
    normalized = note_sheets_api._normalize_document_json(next_document)
    if dict(document.document_json or {}) == normalized:
        return False
    document.document_json = normalized
    document.version = max(int(document.version or 1), 1) + 1
    document.updated_by_user_id = actor.id
    document.updated_at = time.time()
    session.add(document)
    return True


def _attendance_wjx_find_course_context(
    session: Session,
    entry: AttendanceWjxDataEntry,
) -> dict[str, Any]:
    note_sheets_api = __import__("backend.api.note_sheets", fromlist=["_normalize_document_json"])
    result: dict[str, Any] = {
        "note_sheets_api": note_sheets_api,
        "warnings": [],
    }
    wjx_document = _ensure_attendance_wjx_sheet_document(session, create=False)
    if wjx_document is None:
        result["warnings"].append("未找到问卷数据表")
        return result

    wjx_json = _normalize_attendance_wjx_sheet_document(dict(wjx_document.document_json or {}))
    row_index = _find_attendance_wjx_sheet_row_index(wjx_json, entry.seq)
    if row_index is None:
        result["warnings"].append(f"问卷数据表中未找到序号 {entry.seq}")
        return result

    columns = list(wjx_json.get("columns") or [])
    rows = list(wjx_json.get("rows") or [])
    row = rows[row_index] if row_index < len(rows) else []
    course_index = _get_attendance_wjx_sheet_column_index(columns, "课程")
    course_cell = row[course_index] if isinstance(row, list) and course_index < len(row) else ""
    course_link_url = _extract_inline_cell_link_url(course_cell)
    workbook_id, linked_sheet_id = _attendance_wjx_parse_workbook_sheet_url(course_link_url)
    result.update({
        "wjx_sheet_id": wjx_document.numeric_id,
        "wjx_row_index": row_index,
        "course_link_url": course_link_url,
        "course_workbook_id": workbook_id,
        "linked_sheet_id": linked_sheet_id,
    })
    if workbook_id is None:
        result["warnings"].append("问卷课程单元格缺少 workbook 链接")
        return result

    workbook = session.exec(
        select(WorkbookDocument).where(WorkbookDocument.numeric_id == workbook_id)
    ).first()
    if workbook is None:
        result["warnings"].append(f"未找到课程 workbook：{workbook_id}")
        return result

    registration_document = note_sheets_api._get_workbook_sheet_by_key_or_title(
        session,
        workbook,
        sheet_key="registration",
        title="报名表",
    )
    attendance_document = note_sheets_api._get_workbook_sheet_by_key_or_title(
        session,
        workbook,
        sheet_key="attendance",
        title="考勤表",
    )
    if attendance_document is None and linked_sheet_id is not None:
        attendance_document = session.exec(
            select(SheetDocument).where(SheetDocument.numeric_id == linked_sheet_id)
        ).first()

    result.update({
        "workbook": workbook,
        "registration_document": registration_document,
        "attendance_document": attendance_document,
    })
    if registration_document is None:
        result["warnings"].append("课程 workbook 中未找到报名表")
    if attendance_document is None:
        result["warnings"].append("课程 workbook 中未找到考勤表")
    return result


def _attendance_wjx_find_student_row(
    note_sheets_api: Any,
    rows: list[list[Any]],
    columns: list[str],
    entry: AttendanceWjxDataEntry,
    *,
    student_headers: tuple[str, ...],
) -> tuple[int | None, list[Any] | None, dict[str, Any]]:
    target_key = _attendance_wjx_student_number_key(entry.student_id_text)
    target_name = _normalize_attendance_wjx_sheet_cell(entry.student_name)
    best: tuple[int, int] | None = None
    for index, row in enumerate(rows):
        student_match = False
        for header in student_headers:
            value = _attendance_wjx_sheet_cell_text(note_sheets_api, row, columns, header)
            if target_key and _attendance_wjx_student_number_key(value) == target_key:
                student_match = True
                break
        name = _attendance_wjx_sheet_cell_text(note_sheets_api, row, columns, "姓名")
        name_match = bool(target_name and name == target_name)
        score = 0
        if student_match and name_match:
            score = 4
        elif student_match:
            score = 3
        elif name_match:
            score = 1
        if score and (best is None or score > best[0]):
            best = (score, index)

    if best is None:
        return None, None, {"matched": False, "reason": "未按学号/姓名匹配到行"}
    row_index = best[1]
    return row_index, rows[row_index], {"matched": True, "score": best[0]}


def _attendance_wjx_number_value(value: Any) -> float:
    text = _normalize_attendance_wjx_sheet_cell(value)
    if not text or text.startswith("="):
        return 0.0
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    if not match:
        return 0.0
    try:
        return float(match.group())
    except ValueError:
        return 0.0


def _attendance_wjx_analyze_empty_attendance(
    note_sheets_api: Any,
    row: list[Any] | None,
    columns: list[str],
    entry: AttendanceWjxDataEntry,
) -> dict[str, Any]:
    feedback_text = " ".join([
        _normalize_attendance_wjx_sheet_cell(entry.correction_request),
        _normalize_attendance_wjx_sheet_cell(entry.extra_note),
    ])
    keyword_hit = any(keyword in feedback_text for keyword in ATTENDANCE_WJX_EMPTY_DATA_KEYWORDS)
    if row is None:
        return {
            "likely_empty_user_id_issue": keyword_hit,
            "keyword_hit": keyword_hit,
            "numeric_nonzero_count": 0,
            "progress_nonempty_count": 0,
        }

    numeric_headers = (
        "完成视频数",
        "视频应返款",
        "打卡数",
        "打卡应返款",
        "总应返款",
        "当前应返款",
    )
    numeric_nonzero_count = 0
    for header in numeric_headers:
        value = _attendance_wjx_sheet_cell_text(note_sheets_api, row, columns, header)
        if abs(_attendance_wjx_number_value(value)) > 0.0001:
            numeric_nonzero_count += 1

    progress_nonempty_count = 0
    for column, value in zip(columns, row):
        column_text = _normalize_attendance_wjx_sheet_cell(column)
        if not (("课" in column_text) or re.search(r"\d{1,2}月|\d{1,2}[/-]\d{1,2}|~|～", column_text)):
            continue
        text = note_sheets_api._normalize_sheet_text(value)
        if text and not text.startswith("=") and any(token in text for token in ("完成", "回放", "%", "第")):
            progress_nonempty_count += 1

    likely_empty = keyword_hit and numeric_nonzero_count == 0 and progress_nonempty_count == 0
    return {
        "likely_empty_user_id_issue": likely_empty,
        "keyword_hit": keyword_hit,
        "numeric_nonzero_count": numeric_nonzero_count,
        "progress_nonempty_count": progress_nonempty_count,
    }


def _attendance_wjx_collect_lookup_inputs(
    note_sheets_api: Any,
    registration_row: list[Any] | None,
    registration_columns: list[str],
    entry: AttendanceWjxDataEntry,
) -> dict[str, list[str]]:
    names = [entry.student_name]
    phones: list[Any] = []
    if registration_row is not None:
        for header in ("姓名", "微信昵称", "昵称"):
            names.append(_attendance_wjx_sheet_cell_text(note_sheets_api, registration_row, registration_columns, header))
        for header in ("手机号", "错误手机号", "联系电话", "手机"):
            phones.append(_attendance_wjx_sheet_cell_text(note_sheets_api, registration_row, registration_columns, header))
    return {
        "names": _attendance_wjx_unique_texts(names),
        "phones": _attendance_wjx_unique_texts(phones),
    }


def _attendance_wjx_lookup_user_candidate(
    session: Session,
    current_user: User,
    note_sheets_api: Any,
    *,
    entry: AttendanceWjxDataEntry,
    workbook: WorkbookDocument,
    registration_document: SheetDocument,
    names: list[str],
    phones: list[str],
    current_user_id: str,
    use_remote_browser: bool,
) -> dict[str, Any]:
    shop_id = note_sheets_api._resolve_registration_shop_id(session, registration_document, workbook)
    result: dict[str, Any] = {
        "shop_id": shop_id,
        "names": names,
        "phones": phones,
        "local": {},
        "remote": {},
        "user_id": "",
        "score": 0,
        "source": "",
        "warnings": [],
    }

    if not names and not phones:
        result["warnings"].append("缺少姓名和手机号，无法查找用户")
        return result

    try:
        get_kqdb = note_sheets_api._load_attendance_kqdb_provider()
        lookup_user = note_sheets_api._load_attendance_user_lookup_provider()
        kqdb = get_kqdb()
        user_id, weight = lookup_user(
            names,
            phones,
            course_name=entry.course_name,
            course_product_name="",
            shop_id=shop_id,
            return_mode=1,
            kqdb=kqdb,
        )
        local_user_id = _normalize_attendance_wjx_sheet_cell(user_id)
        local_score = _attendance_wjx_number_value(weight)
        result["local"] = {"user_id": local_user_id, "score": local_score}
        if local_user_id:
            result.update({"user_id": local_user_id, "score": local_score, "source": "local_db"})
    except Exception as exc:
        result["warnings"].append(f"本地用户库查询失败：{exc}")

    needs_remote = (
        use_remote_browser
        and (
            not result.get("user_id")
            or _normalize_attendance_wjx_sheet_cell(result.get("user_id")) == current_user_id
            or float(result.get("score") or 0) < 90
        )
    )
    if needs_remote:
        try:
            remote_results = note_sheets_api._lookup_registration_users_with_remote_browser(
                session,
                current_user,
                course_name=entry.course_name,
                shop_id=shop_id,
                items=[{"key": "target", "names": names, "phones": phones}],
            )
            remote = remote_results.get("target", {})
            remote_user_id = _normalize_attendance_wjx_sheet_cell(remote.get("user_id"))
            result["remote"] = dict(remote)
            if remote_user_id and remote_user_id != result.get("user_id"):
                result.update({"user_id": remote_user_id, "score": 95, "source": "mi15_browser"})
        except Exception as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else exc
            result["warnings"].append(f"mi15 用户回查失败：{detail}")

    return result


def _attendance_wjx_extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", stripped, re.S)
    if not match:
        return None
    try:
        payload = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _attendance_wjx_codex_repair_decision(context: dict[str, Any]) -> dict[str, Any]:
    prompt = (
        "你是 CodeYun 考勤问卷空数据修复审查器，只返回 JSON。\n"
        "任务：判断是否可以把报名表/考勤表里的用户ID改成候选用户ID。\n"
        "只有同时满足这些条件才 decision=apply：\n"
        "1. 学员反馈指向视频/打卡/作业等考勤数据为空或缺失；\n"
        "2. 当前考勤行的关键统计确实接近空数据；\n"
        "3. 候选用户ID来自本地用户库高分命中或 mi15 浏览器回查；\n"
        "4. 候选用户ID与当前表内ID不同，且课程、姓名、学号上下文一致。\n"
        "否则 decision=skip 或 needs_review。\n"
        "输出格式：{\"decision\":\"apply|skip|needs_review\","
        "\"confidence\":0.0,\"reason\":\"一句中文原因\"}。\n\n"
        f"CONTEXT:\n{json.dumps(context, ensure_ascii=False, indent=2, default=str)}"
    )
    response = chat_with_provider(
        provider_id=ATTENDANCE_WJX_AUTO_REPAIR_PROVIDER_ID,
        model=ATTENDANCE_WJX_AUTO_REPAIR_MODEL,
        messages=[{"role": "user", "content": prompt}],
        system_prompt="你只做考勤用户ID修复审查，只返回 JSON。",
        response_format="json",
        timeout_seconds=ATTENDANCE_WJX_AUTO_REPAIR_TIMEOUT_SECONDS,
    )
    payload = _attendance_wjx_extract_json_object(str(response.get("content") or ""))
    if not payload:
        raise OllamaClientError("gpt-5.3-codex-spark 未返回可解析 JSON")
    decision = _normalize_attendance_wjx_sheet_cell(payload.get("decision")).lower()
    if decision not in {"apply", "skip", "needs_review"}:
        decision = "needs_review"
    return {
        "decision": decision,
        "confidence": _attendance_wjx_number_value(payload.get("confidence")),
        "reason": _normalize_attendance_wjx_sheet_cell(payload.get("reason")),
        "model": str(response.get("model") or ATTENDANCE_WJX_AUTO_REPAIR_MODEL),
    }


def _attendance_wjx_apply_user_id_repair(
    session: Session,
    current_user: User,
    note_sheets_api: Any,
    *,
    registration_document: SheetDocument,
    registration_json: dict[str, Any],
    registration_columns: list[str],
    registration_rows: list[list[Any]],
    registration_row_index: int | None,
    attendance_document: SheetDocument,
    attendance_json: dict[str, Any],
    attendance_columns: list[str],
    attendance_rows: list[list[Any]],
    attendance_row_index: int | None,
    candidate_user_id: str,
    candidate_score: float,
) -> dict[str, Any]:
    changed_sheets: list[str] = []
    if registration_row_index is not None and 0 <= registration_row_index < len(registration_rows):
        row = list(registration_rows[registration_row_index])
        changed = _attendance_wjx_set_sheet_cell(note_sheets_api, row, registration_columns, "用户ID", candidate_user_id)
        changed = _attendance_wjx_set_sheet_cell(
            note_sheets_api,
            row,
            registration_columns,
            "匹配得分",
            str(int(candidate_score)) if candidate_score else "",
        ) or changed
        if changed:
            registration_rows[registration_row_index] = row
            next_document = note_sheets_api._replace_document_data_rows(registration_json, registration_rows)
            if _attendance_wjx_persist_note_sheet_document(
                session,
                note_sheets_api,
                registration_document,
                next_document,
                actor=current_user,
            ):
                changed_sheets.append("报名表")

    if attendance_row_index is not None and 0 <= attendance_row_index < len(attendance_rows):
        row = list(attendance_rows[attendance_row_index])
        changed = _attendance_wjx_set_sheet_cell(note_sheets_api, row, attendance_columns, "用户ID", candidate_user_id)
        if changed:
            attendance_rows[attendance_row_index] = row
            next_document = note_sheets_api._replace_document_data_rows(attendance_json, attendance_rows)
            if _attendance_wjx_persist_note_sheet_document(
                session,
                note_sheets_api,
                attendance_document,
                next_document,
                actor=current_user,
            ):
                changed_sheets.append("考勤表")

    return {"changed_sheets": changed_sheets, "changed": bool(changed_sheets)}


def _attendance_wjx_try_rebuild_after_user_id_repair(
    session: Session,
    *,
    course_name: str,
    attendance_sheet_id: int | None,
) -> dict[str, Any]:
    if attendance_sheet_id is None:
        return {"attempted": False, "reason": "缺少考勤表 sheet_id"}
    try:
        if "梵呗" in course_name:
            from backend.core.attendance.fanbei_course_sheets import rebuild_fanbei_attendance_from_course_sheets

            summary = rebuild_fanbei_attendance_from_course_sheets(
                session,
                attendance_sheet_id=attendance_sheet_id,
            )
        elif "念住" in course_name or "觉观" in course_name:
            from backend.core.attendance.nianzhu_course_sheets import rebuild_nianzhu_attendance_from_course_sheets

            summary = rebuild_nianzhu_attendance_from_course_sheets(
                session,
                attendance_sheet_id=attendance_sheet_id,
                active_only=True,
                course_name=course_name,
            )
        else:
            return {"attempted": False, "reason": "课程类型暂未接入本地重算"}
        return {"attempted": True, "summary": summary}
    except Exception as exc:
        return {"attempted": True, "error": str(exc)}


def _try_auto_repair_attendance_wjx_empty_user_id(
    session: Session,
    current_user: User,
    entry: AttendanceWjxDataEntry,
    *,
    apply_changes: bool,
    use_remote_browser: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "requested": True,
        "applied": False,
        "status": "needs_review",
        "summary": "",
        "warnings": [],
        "codex_spark": {
            "requested": True,
            "used": False,
            "model": ATTENDANCE_WJX_AUTO_REPAIR_MODEL,
        },
    }
    context = _attendance_wjx_find_course_context(session, entry)
    result["warnings"].extend(context.get("warnings") or [])
    note_sheets_api = context.get("note_sheets_api")
    workbook = context.get("workbook")
    registration_document = context.get("registration_document")
    attendance_document = context.get("attendance_document")
    if note_sheets_api is None or workbook is None or registration_document is None or attendance_document is None:
        result.update({"status": "not_applicable", "summary": "缺少课程报名表或考勤表上下文，未自动修复"})
        return result

    registration_json, registration_columns, registration_rows = _attendance_wjx_note_sheet_rows(
        note_sheets_api,
        registration_document,
    )
    attendance_json, attendance_columns, attendance_rows = _attendance_wjx_note_sheet_rows(
        note_sheets_api,
        attendance_document,
    )
    registration_row_index, registration_row, registration_match = _attendance_wjx_find_student_row(
        note_sheets_api,
        registration_rows,
        registration_columns,
        entry,
        student_headers=("序号", "学号"),
    )
    attendance_row_index, attendance_row, attendance_match = _attendance_wjx_find_student_row(
        note_sheets_api,
        attendance_rows,
        attendance_columns,
        entry,
        student_headers=("学号", "序号"),
    )
    result["matches"] = {
        "registration": registration_match,
        "attendance": attendance_match,
    }
    if registration_row is None or attendance_row is None:
        result.update({"status": "not_applicable", "summary": "未匹配到报名表或考勤表学员行，未自动修复"})
        return result

    empty_analysis = _attendance_wjx_analyze_empty_attendance(
        note_sheets_api,
        attendance_row,
        attendance_columns,
        entry,
    )
    lookup_inputs = _attendance_wjx_collect_lookup_inputs(
        note_sheets_api,
        registration_row,
        registration_columns,
        entry,
    )
    registration_user_id = _attendance_wjx_sheet_cell_text(
        note_sheets_api,
        registration_row,
        registration_columns,
        "用户ID",
    )
    attendance_user_id = _attendance_wjx_sheet_cell_text(
        note_sheets_api,
        attendance_row,
        attendance_columns,
        "用户ID",
    )
    current_user_id = registration_user_id or attendance_user_id
    lookup_result = _attendance_wjx_lookup_user_candidate(
        session,
        current_user,
        note_sheets_api,
        entry=entry,
        workbook=workbook,
        registration_document=registration_document,
        names=lookup_inputs["names"],
        phones=lookup_inputs["phones"],
        current_user_id=current_user_id,
        use_remote_browser=use_remote_browser,
    )
    result["warnings"].extend(lookup_result.get("warnings") or [])
    candidate_user_id = _normalize_attendance_wjx_sheet_cell(lookup_result.get("user_id"))
    candidate_score = _attendance_wjx_number_value(lookup_result.get("score"))
    needs_write = bool(candidate_user_id and (
        candidate_user_id != registration_user_id or candidate_user_id != attendance_user_id
    ))
    deterministic_safe = bool(
        empty_analysis.get("likely_empty_user_id_issue")
        and needs_write
        and candidate_score >= 90
    )
    repair_context = {
        "entry": {
            "seq": entry.seq,
            "course_name": entry.course_name,
            "student_id_text": entry.student_id_text,
            "student_name": entry.student_name,
            "correction_request": entry.correction_request,
            "extra_note": entry.extra_note,
        },
        "workbook": {
            "numeric_id": workbook.numeric_id,
            "title": workbook.title,
        },
        "registration": {
            "sheet_id": registration_document.numeric_id,
            "row_index": registration_row_index,
            "current_user_id": registration_user_id,
        },
        "attendance": {
            "sheet_id": attendance_document.numeric_id,
            "row_index": attendance_row_index,
            "current_user_id": attendance_user_id,
            "empty_analysis": empty_analysis,
        },
        "lookup": lookup_result,
        "deterministic_safe": deterministic_safe,
        "apply_changes": apply_changes,
    }
    result["context"] = repair_context

    if not candidate_user_id:
        result.update({"status": "unmatched", "summary": "未查到候选用户ID，未自动修复"})
        result["codex_spark"]["requested"] = False
        return result
    if not needs_write:
        result.update({"status": "already_aligned", "summary": "候选用户ID与表内用户ID一致，未改表"})
        result["codex_spark"]["requested"] = False
        return result

    try:
        decision = _attendance_wjx_codex_repair_decision(repair_context)
        result["codex_spark"].update({
            "used": True,
            "decision": decision.get("decision"),
            "confidence": decision.get("confidence"),
            "reason": decision.get("reason"),
            "model": decision.get("model") or ATTENDANCE_WJX_AUTO_REPAIR_MODEL,
        })
    except OllamaClientError as exc:
        result["warnings"].append(f"gpt-5.3-codex-spark 修复审查失败：{exc}")
        decision = {"decision": "needs_review", "confidence": 0, "reason": "AI审查失败"}

    can_apply = bool(
        apply_changes
        and deterministic_safe
        and decision.get("decision") == "apply"
        and _attendance_wjx_number_value(decision.get("confidence")) >= 0.75
    )
    if not can_apply:
        result.update({
            "status": "needs_review",
            "summary": "已找到候选用户ID，但未通过自动写表条件",
        })
        return result

    apply_result = _attendance_wjx_apply_user_id_repair(
        session,
        current_user,
        note_sheets_api,
        registration_document=registration_document,
        registration_json=registration_json,
        registration_columns=registration_columns,
        registration_rows=registration_rows,
        registration_row_index=registration_row_index,
        attendance_document=attendance_document,
        attendance_json=attendance_json,
        attendance_columns=attendance_columns,
        attendance_rows=attendance_rows,
        attendance_row_index=attendance_row_index,
        candidate_user_id=candidate_user_id,
        candidate_score=candidate_score,
    )
    rebuild = _attendance_wjx_try_rebuild_after_user_id_repair(
        session,
        course_name=entry.course_name,
        attendance_sheet_id=attendance_document.numeric_id,
    )
    result.update({
        "applied": bool(apply_result.get("changed")),
        "status": "applied" if apply_result.get("changed") else "already_aligned",
        "summary": f"已将用户ID修复为 {candidate_user_id}",
        "candidate_user_id": candidate_user_id,
        "candidate_score": candidate_score,
        "candidate_source": lookup_result.get("source"),
        "apply_result": apply_result,
        "rebuild": rebuild,
    })
    session.flush()
    return result


def _append_auto_repair_to_precheck_report(precheck: dict[str, Any], repair: dict[str, Any]) -> None:
    summary = _normalize_attendance_wjx_sheet_cell(repair.get("summary"))
    if not summary:
        return
    prefix = "自动修复尝试"
    details = [f"{prefix}：{summary}。"]
    if repair.get("applied"):
        rebuild = repair.get("rebuild") if isinstance(repair.get("rebuild"), dict) else {}
        if rebuild.get("attempted") and not rebuild.get("error"):
            details.append("已触发本地重算；如果仍为空，需要再由 mi15 采集原始视频/打卡数据。")
        elif rebuild.get("error"):
            details.append(f"本地重算失败：{rebuild.get('error')}。")
    else:
        codex = repair.get("codex_spark") if isinstance(repair.get("codex_spark"), dict) else {}
        reason = _normalize_attendance_wjx_sheet_cell(codex.get("reason"))
        if reason:
            details.append(f"AI审查原因：{reason}。")
    report = _normalize_attendance_wjx_sheet_cell(precheck.get("report"))
    precheck["report"] = f"{report}\n\n{''.join(details)}" if report else "".join(details)
    precheck["auto_repair"] = repair
    if repair.get("applied"):
        precheck["read_only"] = False


@router.post("/wjx-data/{entry_id}/ai-precheck", response_model=AttendanceWjxAiPrecheckResponse)
def run_attendance_wjx_data_ai_precheck(
    entry_id: int,
    payload: AttendanceWjxAiPrecheckRequest = AttendanceWjxAiPrecheckRequest(),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
    _: User | None = Depends(require_feature_access_dependency("attendance.wjx-data")),
):
    ensure_can_use_attendance_service(current_user, session)
    entry = _resolve_attendance_wjx_precheck_entry(session, entry_id)
    precheck = build_attendance_wjx_ai_precheck(
        entry,
        session,
        use_codex_cli=payload.use_codex_cli,
    )
    if payload.auto_repair:
        repair = _try_auto_repair_attendance_wjx_empty_user_id(
            session,
            current_user,
            entry,
            apply_changes=payload.persist,
            use_remote_browser=payload.repair_with_remote_browser,
        )
        _append_auto_repair_to_precheck_report(precheck, repair)

    if not payload.persist:
        item = serialize_attendance_wjx_data_entry(entry)
        revision_result = dict(item.get("revision_result") or {})
        revision_result["ai_precheck"] = precheck
        item["revision_result"] = revision_result
        return AttendanceWjxAiPrecheckResponse(
            item=AttendanceWjxDataItem.model_validate(item),
            precheck=precheck,
        )

    now = time.time()
    legacy_entries = session.exec(
        select(AttendanceWjxDataEntry)
        .where(AttendanceWjxDataEntry.activity_id.in_([FIXED_WJX_TEMPLATE_ACTIVITY_ID, LOCAL_FEEDBACK_ACTIVITY_ID]))
        .where(AttendanceWjxDataEntry.seq == entry.seq)
    ).all()
    if not legacy_entries:
        legacy_entries = [entry]

    for legacy_entry in legacy_entries:
        revision_result = dict(legacy_entry.revision_result_json or {})
        revision_result["ai_precheck"] = precheck
        legacy_entry.revision_result_json = revision_result
        legacy_entry.updated_at = now
        session.add(legacy_entry)
    session.commit()

    sheet_document = _ensure_attendance_wjx_sheet_document(session, create=True)
    if sheet_document is not None:
        next_document, _inserted, changed = _upsert_attendance_wjx_sheet_values(
            dict(sheet_document.document_json or {}),
            {
                "序号": entry.seq,
                ATTENDANCE_WJX_AI_COLUMN: precheck.get("report") or precheck.get("summary", ""),
            },
            preserve_process_status=True,
        )
        if changed:
            _persist_attendance_wjx_sheet_document(
                session,
                sheet_document,
                next_document,
                actor=current_user,
            )

    session.refresh(entry)
    return AttendanceWjxAiPrecheckResponse(
        item=AttendanceWjxDataItem.model_validate(serialize_attendance_wjx_data_entry(entry)),
        precheck=precheck,
    )


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

    entry_snapshot = {
        **serialize_user_device(entry),
        "token": entry.token,
    }
    try:
        result = _execute_order_on_entry(
            entry_snapshot,
            execution_payload,
        )
        result = _normalize_attendance_order_execution_result(result)
        refund_confirmation = (
            _verify_refund_execution_confirmed(entry_snapshot, execution_payload, result)
            if payload.action == "refund"
            else None
        )
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
        "refund_confirmation": refund_confirmation,
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
