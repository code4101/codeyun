from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import os
from pathlib import Path
import shutil
import sys
import time
import re
from math import ceil
from typing import Any, Literal, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, delete, select

from backend.core.auth import get_current_active_user, get_optional_current_user_from_token
from backend.core.background_task_queue import background_task_queue
from backend.core.feature_access_guard import ensure_feature_access
from backend.core.settings import get_settings
from backend.db import engine, get_session
from backend.models import (
    ResourceAccessGrant,
    SheetDocument,
    User,
    WorkbookDocument,
    WorkbookSheetLink,
)


router = APIRouter()

DEFAULT_NOTE_SHEET_COLUMNS = ["列1", "列2", "列3"]
DEFAULT_NOTE_SHEET_PAGE_SIZE = 100
MAX_NOTE_SHEET_PAGE_SIZE = 1000
RESOURCE_ACCESS_ROLES = ("deny", "viewer", "editor", "manager")
RESOURCE_ACCESS_ROLE_RANK = {"deny": 0, "viewer": 1, "editor": 2, "manager": 3}
RESOURCE_ACCESS_SUBJECT_ANONYMOUS = "anonymous"
RESOURCE_ACCESS_SUBJECT_USER = "user"
RESOURCE_TYPE_WORKBOOK = "workbook"
RESOURCE_TYPE_SHEET = "sheet"
EXCEL_DATE_UNIX_EPOCH_SERIAL = 25569
ATTENDANCE_SUMMARY_WORKBOOK_ID = 2
ATTENDANCE_SUMMARY_SHEET_ID = 4
ATTENDANCE_WJX_DATA_OWNER_TYPE = "attendance_questionnaire"
ATTENDANCE_WJX_DATA_OWNER_KEY = "wjx-data"
ATTENDANCE_WJX_DATA_SHEET_KEY = "data"
ATTENDANCE_WJX_DATA_PUBLIC_EDITABLE_COLUMN_INDEXES = (8,)
ATTENDANCE_TEMPLATE_MONTHLY_SOURCE_COURSES = ("念住", "觉观")
ATTENDANCE_TEMPLATE_ODD_MONTH_SOURCE_COURSES = ("梵呗初阶",)
ATTENDANCE_TEMPLATE_EVEN_MONTH_SOURCE_COURSES = ("梵呗增益",)
ATTENDANCE_FIELD_BINDINGS: dict[str, tuple[str, int]] = {
    "course_type": ("课程类型", 0),
    "course_name": ("课程名称", 1),
    "online_sheet": ("在线考勤表", 2),
    "course_owner": ("考勤负责人", 3),
    "lesson_links": ("课次链接", 4),
    "clockin_links": ("打卡链接", 5),
    "start_date": ("课程开始日期", 8),
    "end_date": ("课程结束日期", 9),
    "completed_date": ("考勤实际完成结点", 10),
    "registration_count": ("报名人数", 12),
}
ATTENDANCE_FIELD_LEGACY_FALLBACKS: dict[str, int] = {
    "start_date": 6,
    "end_date": 7,
    "completed_date": 8,
    "registration_count": 10,
}
ATTENDANCE_TEMPLATE_COURSE_TEXT_RE = re.compile(
    r"(?:(?P<date>\d{8}|\d{6})\s*)?第(?P<edition>\d+)届(?P<course>\S+)",
)
ATTENDANCE_TEMPLATE_LEADING_DATE_RE = re.compile(r"^(?P<date>\d{8}|\d{6})(?P<body>.*)$")
ATTENDANCE_COURSE_SCRIPT_DIR = Path(
    os.environ.get(
        "CODEYUN_KQ5034_COURSES_DIR",
        r"D:\home\chenkunze\slns\kq5034\courses",
    )
)
ATTENDANCE_KQ5034_REPO_DIR = Path(
    os.environ.get(
        "CODEYUN_KQ5034_REPO_DIR",
        r"D:\home\chenkunze\slns\kq5034",
    )
)
ATTENDANCE_XLPROJECT_SRC_DIR = Path(
    os.environ.get(
        "CODEYUN_XLPROJECT_SRC_DIR",
        r"D:\home\chenkunze\slns\xlproject\src",
    )
)
ATTENDANCE_COURSE_SCRIPT_STEM_RE = re.compile(r"^d(?P<date>\d{6})(?P<body>.+)$")
ATTENDANCE_COURSE_SCRIPT_INIT_TOKEN_RE = re.compile(
    r"(super\(\).__init__\(\s*[^,]+,\s*(?:XlPath|XPath)\(__file__\)\.stem\s*,\s*)"
    r"(?P<quote>['\"])(?P<token>[^'\"]+)(?P=quote)",
    re.DOTALL,
)
ATTENDANCE_COURSE_SCRIPT_KDOCS_TOKEN_RE = re.compile(r"kdocs\.cn/l/(?P<token>[A-Za-z0-9]+)")
ATTENDANCE_COURSE_EDITION_RE = re.compile(r"第\s*(?P<edition>\d+)\s*届")
ATTENDANCE_COURSE_SCRIPT_PRODUCT_NAME_RE = re.compile(
    r"(?P<prefix>课程商品名\s*=\s*)(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
)
ATTENDANCE_COURSE_SCRIPT_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
ATTENDANCE_COURSE_SCRIPT_FILE_EXTENSION_RE = re.compile(
    r"\.(?:xlsx|xlsm|xlsb|xls|et|ett|csv|py)$",
    re.IGNORECASE,
)
NATURAL_SORT_SPLIT_RE = re.compile(r"(\d+)")
FORMULA_CELL_REFERENCE_RE = re.compile(r"(^|[^A-Za-z0-9_.$])(\$?)([A-Za-z]{1,3})(\$?)(\d+)(?![A-Za-z0-9_(!])")
FORMULA_PUNCTUATION_TRANSLATION = str.maketrans({
    "，": ",",
    "（": "(",
    "）": ")",
})
DATE_PARSE_FORMULA_RE = re.compile(
    r"""^\s*=\s*(?:DATE_PARSE|日期解析)\s*\(\s*
    (?P<source>\$?[A-Za-z]{1,3}\$?\d+|"(?:[^"]|"")*"|'(?:[^']|'')*')
    (?:\s*,\s*(?P<pattern>"(?:[^"]|"")*"|'(?:[^']|'')*'))?
    (?:\s*,\s*(?P<format>"(?:[^"]|"")*"|'(?:[^']|'')*'))?
    \s*\)\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
CELL_OFFSET_FORMULA_RE = re.compile(
    r"^\s*=\s*(?P<source>\$?[A-Za-z]{1,3}\$?\d+)\s*(?P<operator>[+-])\s*(?P<offset>\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)
FORMULA_CELL_REFERENCE_ONLY_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")
attendance_summary_scheduler = BackgroundScheduler()


class WorkbookRefItem(BaseModel):
    id: int
    title: str


class NoteSheetAccessCapabilities(BaseModel):
    can_read: bool = False
    can_use_local_view: bool = False
    can_edit_data: bool = False
    editable_data_columns: list[int] = Field(default_factory=list)
    can_edit_config: bool = False
    can_run_sheet_actions: bool = False
    can_manage_access: bool = False


class NoteSheetResourceAccess(BaseModel):
    role: Literal["none", "deny", "viewer", "editor", "manager"] = "none"
    capabilities: NoteSheetAccessCapabilities = Field(default_factory=NoteSheetAccessCapabilities)


class NoteSheetSummaryResponse(BaseModel):
    id: int
    title: str
    engine: str
    scope: str
    owner_user_id: Optional[int] = None
    created_by_user_id: Optional[int] = None
    updated_by_user_id: Optional[int] = None
    created_at: float
    updated_at: float
    workbook_items: list[WorkbookRefItem] = Field(default_factory=list)
    access: Optional[NoteSheetResourceAccess] = None


class NoteSheetDetailResponse(NoteSheetSummaryResponse):
    owner_type: str
    owner_key: str
    sheet_key: str
    version: int
    document_json: dict[str, Any] = Field(default_factory=dict)
    pagination: Optional["NoteSheetPaginationResponse"] = None


class NoteSheetPaginationResponse(BaseModel):
    page: int = 1
    page_size: int = DEFAULT_NOTE_SHEET_PAGE_SIZE
    total_rows: int = 0
    page_count: int = 1
    row_offset: int = 0
    loaded_row_count: int = 0


class NoteSheetPagePatchRequest(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_NOTE_SHEET_PAGE_SIZE, ge=1, le=MAX_NOTE_SHEET_PAGE_SIZE)
    row_offset: int = Field(default=0, ge=0)
    loaded_row_count: int = Field(default=0, ge=0)


class NoteSheetCreateRequest(BaseModel):
    title: str = ""
    workbook_id: Optional[int] = None
    document_json: dict[str, Any] = Field(default_factory=dict)


class NoteSheetUpdateRequest(BaseModel):
    title: Optional[str] = None
    document_json: Optional[dict[str, Any]] = None
    page_patch: Optional[NoteSheetPagePatchRequest] = None


class NoteSheetSortRequest(BaseModel):
    column_index: int = Field(ge=0)
    direction: Literal["asc", "desc"] = "asc"


class NoteSheetAttendanceTemplateGenerationRequest(BaseModel):
    target_year: Optional[int] = Field(default=None, ge=1970, le=9999)
    target_month: Optional[int] = Field(default=None, ge=1, le=12)
    target_date: Optional[str] = None


class NoteSheetAttendanceCourseTemplateGenerationRequest(NoteSheetAttendanceTemplateGenerationRequest):
    row_index: Optional[int] = Field(default=None, ge=0)
    course_type: Optional[str] = None


class NoteSheetAttendanceTemplateActionItem(BaseModel):
    course_type: str
    course_name: str = ""
    target_date: str = ""
    row_index: Optional[int] = None
    reason: str = ""


class NoteSheetAttendanceTemplateGenerationResponse(BaseModel):
    sheet: NoteSheetDetailResponse
    generated: list[NoteSheetAttendanceTemplateActionItem] = Field(default_factory=list)
    skipped: list[NoteSheetAttendanceTemplateActionItem] = Field(default_factory=list)


class NoteSheetAttendanceCompletionRequest(BaseModel):
    row_index: int = Field(ge=0)
    completion_date: Optional[str] = None


class NoteSheetAttendanceCompletionResponse(BaseModel):
    sheet: NoteSheetDetailResponse
    row_index: int


class NoteSheetAttendanceCourseScriptStatusItem(BaseModel):
    row_index: int
    course_type: str = ""
    course_name: str = ""
    online_sheet: str = ""
    url: str = ""
    target_stem: str = ""
    target_filename: str = ""
    exists: bool = False
    existing_path: str = ""
    can_generate: bool = False
    reason: str = ""


class NoteSheetAttendanceCourseScriptStatusesResponse(BaseModel):
    statuses: list[NoteSheetAttendanceCourseScriptStatusItem] = Field(default_factory=list)


class NoteSheetAttendanceCourseScriptGenerationRequest(BaseModel):
    row_index: int = Field(ge=0)


class NoteSheetAttendanceCourseScriptGenerationResponse(BaseModel):
    status: NoteSheetAttendanceCourseScriptStatusItem
    source_filename: str = ""
    source_path: str = ""
    created_path: str = ""


class NoteSheetAttendanceCourseScriptOrganizeItem(BaseModel):
    row_index: int
    course_type: str = ""
    online_sheet: str = ""
    target_filename: str = ""
    completed: bool = False
    source_path: str = ""
    target_path: str = ""
    reason: str = ""


class NoteSheetAttendanceCourseScriptOrganizeResponse(BaseModel):
    moved: list[NoteSheetAttendanceCourseScriptOrganizeItem] = Field(default_factory=list)
    skipped: list[NoteSheetAttendanceCourseScriptOrganizeItem] = Field(default_factory=list)


class NoteSheetAttendanceLinkCountUpdateRequest(BaseModel):
    field_key: Literal["lesson_links", "clockin_links"]
    row_index: Optional[int] = Field(default=None, ge=0)


class NoteSheetAttendanceLinkCountUpdateItem(BaseModel):
    row_index: int
    course_name: str = ""
    lookup_name: str = ""
    value: str = ""
    total_count: int = 0
    linked_count: int = 0
    reason: str = ""


class NoteSheetAttendanceLinkCountUpdateResponse(BaseModel):
    sheet: NoteSheetDetailResponse
    updated: list[NoteSheetAttendanceLinkCountUpdateItem] = Field(default_factory=list)
    skipped: list[NoteSheetAttendanceLinkCountUpdateItem] = Field(default_factory=list)


class WorkbookSummaryResponse(BaseModel):
    id: int
    title: str
    owner_user_id: Optional[int] = None
    created_by_user_id: Optional[int] = None
    updated_by_user_id: Optional[int] = None
    created_at: float
    updated_at: float
    sheet_count: int = 0
    access: Optional[NoteSheetResourceAccess] = None


class WorkbookDetailResponse(WorkbookSummaryResponse):
    sheets: list[NoteSheetSummaryResponse] = Field(default_factory=list)


class NoteSheetResourceAccessGrantItem(BaseModel):
    subject_type: Literal["anonymous", "user"]
    subject_key: str
    subject_user_id: Optional[int] = None
    username: str = ""
    nickname: str = ""
    role: Literal["deny", "viewer", "editor", "manager"]


class NoteSheetResourceAccessGrantUpdate(BaseModel):
    subject_type: Literal["anonymous", "user"]
    username: Optional[str] = None
    subject_user_id: Optional[int] = None
    role: Literal["none", "deny", "viewer", "editor", "manager"]


class NoteSheetResourceAccessUpdateRequest(BaseModel):
    grants: list[NoteSheetResourceAccessGrantUpdate] = Field(default_factory=list)


class NoteSheetResourceAccessResponse(BaseModel):
    resource_type: Literal["workbook", "sheet"]
    resource_id: int
    access: NoteSheetResourceAccess
    grants: list[NoteSheetResourceAccessGrantItem] = Field(default_factory=list)


class WorkbookCreateRequest(BaseModel):
    title: str = ""


class WorkbookAttachSheetRequest(BaseModel):
    sheet_id: int


class WorkbookSaveAsRequest(BaseModel):
    mode: Literal["template", "duplicate"] = "duplicate"
    title: str = ""


def _create_default_sheet_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "columns": list(DEFAULT_NOTE_SHEET_COLUMNS),
        "rows": [],
        "grid_rows": [list(DEFAULT_NOTE_SHEET_COLUMNS)],
        "data_start_row": 1,
        "field_row_index": 0,
        "merged_cells": [],
        "view_settings": {
            "show_row_numbers": True,
            "row_marker_numbering": "global",
            "show_column_markers": True,
            "column_marker_style": "letters",
            "pagination": {
                "enabled": False,
                "page_size": DEFAULT_NOTE_SHEET_PAGE_SIZE,
            },
        },
    }


def _normalize_document_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _create_default_sheet_document()
    return dict(value)


def _normalize_document_data_start_row(document_json: dict[str, Any]) -> int:
    raw_value = document_json.get("data_start_row")
    try:
        numeric = int(raw_value)
    except (TypeError, ValueError):
        return 0
    return max(numeric, 0)


def _extract_document_grid_rows(document_json: dict[str, Any]) -> list[Any]:
    grid_rows = document_json.get("grid_rows")
    return list(grid_rows) if isinstance(grid_rows, list) else []


def _replace_document_data_rows(document_json: dict[str, Any], rows: list[Any]) -> dict[str, Any]:
    next_document = dict(document_json)
    next_document["rows"] = rows
    grid_rows = _extract_document_grid_rows(next_document)
    if grid_rows:
        data_start_row = min(_normalize_document_data_start_row(next_document), len(grid_rows))
        next_document["grid_rows"] = [*grid_rows[:data_start_row], *rows]
    return next_document


def _normalize_document_columns(document_json: dict[str, Any]) -> list[str]:
    columns = document_json.get("columns")
    if not isinstance(columns, list):
        return list(DEFAULT_NOTE_SHEET_COLUMNS)
    normalized = [str(column or "").strip() for column in columns]
    return normalized or list(DEFAULT_NOTE_SHEET_COLUMNS)


def _row_has_meaningful_cell(row: Any) -> bool:
    if isinstance(row, list):
        return any(str(cell or "").strip() for cell in row)
    if isinstance(row, dict):
        return any(str(cell or "").strip() for cell in row.values())
    return bool(str(row or "").strip())


def _document_has_structural_customization(document_json: dict[str, Any]) -> bool:
    for key in ("header_groups", "cell_meta", "column_configs", "grid_rows", "merged_cells", "data_start_row", "field_row_index"):
        value = document_json.get(key)
        if key == "grid_rows":
            columns = _normalize_document_columns(document_json)
            if isinstance(value, list) and value and value != [columns]:
                return True
            continue
        if key == "data_start_row":
            if value not in (None, 1):
                return True
            continue
        if key == "field_row_index":
            if value not in (None, 0):
                return True
            continue
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, list) and value:
            return True
    return False


def _document_has_meaningful_content(document_json: dict[str, Any]) -> bool:
    normalized = _normalize_document_json(document_json)
    if _normalize_document_columns(normalized) != DEFAULT_NOTE_SHEET_COLUMNS:
        return True
    if any(_row_has_meaningful_cell(row) for row in _extract_document_rows(normalized)):
        return True
    return _document_has_structural_customization(normalized)


def _is_default_blank_document(document_json: dict[str, Any]) -> bool:
    normalized = _normalize_document_json(document_json)
    return (
        _normalize_document_columns(normalized) == DEFAULT_NOTE_SHEET_COLUMNS
        and not any(_row_has_meaningful_cell(row) for row in _extract_document_rows(normalized))
        and not _document_has_structural_customization(normalized)
    )


def _reject_default_blank_overwrite(current_document: dict[str, Any], incoming_document: dict[str, Any]) -> None:
    if _document_has_meaningful_content(current_document) and _is_default_blank_document(incoming_document):
        raise HTTPException(status_code=409, detail="拒绝使用默认空表覆盖已有表格数据")


def _normalize_page_size(value: int | None) -> int:
    numeric = int(value or DEFAULT_NOTE_SHEET_PAGE_SIZE)
    return min(max(numeric, 1), MAX_NOTE_SHEET_PAGE_SIZE)


def _extract_document_rows(document_json: dict[str, Any]) -> list[Any]:
    rows = document_json.get("rows")
    return list(rows) if isinstance(rows, list) else []


def _get_document_pagination_settings(document_json: dict[str, Any]) -> tuple[bool, int]:
    normalized = _normalize_document_json(document_json)
    view_settings = normalized.get("view_settings")
    if not isinstance(view_settings, dict):
        return False, DEFAULT_NOTE_SHEET_PAGE_SIZE

    pagination = view_settings.get("pagination")
    if not isinstance(pagination, dict):
        return False, DEFAULT_NOTE_SHEET_PAGE_SIZE

    enabled = pagination.get("enabled") is True
    page_size = _normalize_page_size(pagination.get("page_size"))
    return enabled, page_size


def _is_attendance_questionnaire_data_sheet(document: SheetDocument) -> bool:
    return (
        document.owner_type == ATTENDANCE_WJX_DATA_OWNER_TYPE
        and document.owner_key == ATTENDANCE_WJX_DATA_OWNER_KEY
        and document.sheet_key == ATTENDANCE_WJX_DATA_SHEET_KEY
    )


def _sync_attendance_questionnaire_course_links(
    session: Session,
    document_json: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    from backend.api.attendance import _sync_attendance_wjx_sheet_course_links

    return _sync_attendance_wjx_sheet_course_links(session, document_json)


def _sync_attendance_questionnaire_sheet_document(session: Session, document: SheetDocument) -> None:
    if not _is_attendance_questionnaire_data_sheet(document):
        return

    next_document, changed = _sync_attendance_questionnaire_course_links(
        session,
        dict(document.document_json or {}),
    )
    if not changed:
        return

    document.document_json = next_document
    document.version = max(int(document.version or 1), 1) + 1
    document.updated_at = time.time()
    session.add(document)
    session.commit()
    session.refresh(document)


def _build_paged_document(
    document_json: dict[str, Any],
    *,
    page: int,
    page_size: int,
) -> tuple[dict[str, Any], NoteSheetPaginationResponse]:
    normalized = _normalize_document_json(document_json)
    all_rows = _extract_document_rows(normalized)
    safe_page_size = _normalize_page_size(page_size)
    actual_page_count = max(1, ceil(len(all_rows) / safe_page_size) if all_rows else 1)
    safe_page = min(max(int(page or 1), 1), actual_page_count)
    row_offset = min((safe_page - 1) * safe_page_size, len(all_rows))
    page_rows = all_rows[row_offset: row_offset + safe_page_size]
    page_document = {
        **normalized,
        "rows": page_rows,
    }
    grid_rows = _extract_document_grid_rows(normalized)
    if grid_rows:
        data_start_row = min(_normalize_document_data_start_row(normalized), len(grid_rows))
        page_document["grid_rows"] = [*grid_rows[:data_start_row], *page_rows]

    return (
        page_document,
        NoteSheetPaginationResponse(
            page=safe_page,
            page_size=safe_page_size,
            total_rows=len(all_rows),
            page_count=actual_page_count,
            row_offset=row_offset,
            loaded_row_count=len(page_rows),
        ),
    )


def _build_workspace_pagination(
    *,
    page_patch: NoteSheetPagePatchRequest,
    total_rows: int,
    current_row_count: int,
) -> NoteSheetPaginationResponse:
    safe_page_size = _normalize_page_size(page_patch.page_size)
    actual_page_count = max(1, ceil(total_rows / safe_page_size) if total_rows else 1)
    display_page = max(int(page_patch.page or 1), 1)
    return NoteSheetPaginationResponse(
        page=display_page,
        page_size=safe_page_size,
        total_rows=total_rows,
        page_count=max(actual_page_count, display_page),
        row_offset=min(int(page_patch.row_offset or 0), total_rows),
        loaded_row_count=max(current_row_count, 0),
    )


def _merge_paged_document(
    current_document: dict[str, Any],
    incoming_document: dict[str, Any],
    page_patch: NoteSheetPagePatchRequest,
) -> dict[str, Any]:
    normalized_current = _normalize_document_json(current_document)
    normalized_incoming = _normalize_document_json(incoming_document)

    current_rows = _extract_document_rows(normalized_current)
    incoming_rows = _extract_document_rows(normalized_incoming)
    row_offset = min(max(int(page_patch.row_offset or 0), 0), len(current_rows))
    loaded_row_count = max(int(page_patch.loaded_row_count or 0), 0)
    tail_start = min(row_offset + loaded_row_count, len(current_rows))

    merged_rows = [
        *current_rows[:row_offset],
        *incoming_rows,
        *current_rows[tail_start:],
    ]
    next_document = {
        **normalized_current,
        **{
            key: value
            for key, value in normalized_incoming.items()
            if key not in {"rows", "grid_rows"}
        },
        "rows": merged_rows,
    }
    current_grid_rows = _extract_document_grid_rows(normalized_current)
    incoming_grid_rows = _extract_document_grid_rows(normalized_incoming)
    if current_grid_rows or incoming_grid_rows:
        data_start_row = _normalize_document_data_start_row(next_document)
        source_grid_rows = incoming_grid_rows or current_grid_rows
        next_document["grid_rows"] = [*source_grid_rows[:data_start_row], *merged_rows]
    return next_document


def _normalize_restricted_cell_value(value: Any) -> str:
    return "" if value is None else str(value)


def _apply_restricted_data_column_update(
    current_document: dict[str, Any],
    incoming_document: dict[str, Any],
    editable_columns: list[int],
) -> dict[str, Any]:
    current_normalized = _normalize_document_json(current_document)
    incoming_normalized = _normalize_document_json(incoming_document)
    current_columns = _normalize_document_columns(current_normalized)
    incoming_columns = _normalize_document_columns(incoming_normalized)
    if incoming_columns != current_columns:
        raise HTTPException(status_code=403, detail="游客只能编辑开放列，不能修改表格结构")

    allowed_columns = {
        int(index)
        for index in editable_columns
        if 0 <= int(index) < len(current_columns)
    }
    if not allowed_columns:
        raise HTTPException(status_code=403, detail="没有该资源权限")

    current_rows = _extract_document_rows(current_normalized)
    incoming_rows = _extract_document_rows(incoming_normalized)
    if len(current_rows) != len(incoming_rows):
        raise HTTPException(status_code=403, detail="游客只能编辑开放列，不能新增或删除行")

    next_rows: list[Any] = []
    column_count = len(current_columns)
    for row_index, current_row in enumerate(current_rows):
        incoming_row = incoming_rows[row_index]
        current_cells = _normalize_sheet_row(current_row, column_count)
        incoming_cells = _normalize_sheet_row(incoming_row, column_count)
        for column_index in range(column_count):
            if column_index in allowed_columns:
                continue
            if _normalize_restricted_cell_value(incoming_cells[column_index]) != _normalize_restricted_cell_value(current_cells[column_index]):
                raise HTTPException(status_code=403, detail="游客只能编辑开放列")

        next_row = current_row
        for column_index in sorted(allowed_columns):
            next_row = _set_row_cell_value(next_row, current_columns, column_index, incoming_cells[column_index])
        next_rows.append(next_row)

    return _replace_document_data_rows(current_normalized, next_rows)


def _normalize_title(value: str | None, *, default_value: str) -> str:
    normalized = str(value or "").strip()
    return normalized or default_value


def _extract_sort_cell_text(row: Any, column_index: int, columns: list[Any]) -> str:
    if isinstance(row, list):
        raw_value = row[column_index] if column_index < len(row) else ""
    elif isinstance(row, dict):
        column_key = str(columns[column_index]) if column_index < len(columns) else ""
        raw_value = row.get(column_key, "")
    else:
        raw_value = ""
    return "" if raw_value is None else str(raw_value).strip()


def _extract_row_cell_value(row: Any, column_index: int, columns: list[Any]) -> Any:
    if isinstance(row, list):
        return row[column_index] if column_index < len(row) else ""
    if isinstance(row, dict):
        column_key = str(columns[column_index]) if column_index < len(columns) else ""
        return row.get(column_key, "")
    return ""


def _is_formula_expression(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("=")


def _normalize_formula_for_sort(value: str) -> str:
    return value.translate(FORMULA_PUNCTUATION_TRANSLATION)


def _unquote_formula_string(value: str | None) -> str:
    if not value:
        return ""
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        quote = stripped[0]
        inner = stripped[1:-1]
        return inner.replace(quote * 2, quote)
    return stripped


def _date_parts_to_serial(year: int, month: int, day: int) -> float | None:
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    unix_epoch = date(1970, 1, 1)
    return float((parsed - unix_epoch).days + EXCEL_DATE_UNIX_EPOCH_SERIAL)


def _serial_to_date_parts(value: float) -> tuple[int, int, int] | None:
    try:
        parsed = date.fromordinal(date(1970, 1, 1).toordinal() + round(value - EXCEL_DATE_UNIX_EPOCH_SERIAL))
    except (OverflowError, ValueError):
        return None
    return parsed.year, parsed.month, parsed.day


def _normalize_two_digit_year(value: int) -> int:
    return 1900 + value if value >= 70 else 2000 + value


def _parse_compact_date_serial(value: Any, pattern: str = "yyyymmdd") -> float | None:
    text = "" if value is None else str(value).strip()
    normalized_pattern = re.sub(r"[^ymd]", "", str(pattern or "yyyymmdd").lower()) or "yyyymmdd"
    if normalized_pattern not in {"yyyymmdd", "yymmdd"}:
        return None

    match = re.search(r"\d{8}", text)
    if match:
        digits = match.group(0)
        return _date_parts_to_serial(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))

    match = re.search(r"\d{6}", text)
    if match:
        digits = match.group(0)
        return _date_parts_to_serial(
            _normalize_two_digit_year(int(digits[:2])),
            int(digits[2:4]),
            int(digits[4:6]),
        )
    return None


def _parse_date_sort_value(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    text = "" if value is None else str(value).strip()
    if not text:
        return None

    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return float(text)

    separated = re.fullmatch(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", text)
    if separated:
        return _date_parts_to_serial(
            int(separated.group(1)),
            int(separated.group(2)),
            int(separated.group(3)),
        )

    compact = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if compact:
        return _date_parts_to_serial(
            int(compact.group(1)),
            int(compact.group(2)),
            int(compact.group(3)),
        )
    return None


def _parse_number_sort_value(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = "" if value is None else str(value).strip()
    if re.fullmatch(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)", text):
        return float(text)
    return None


def _parse_percent_sort_value(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = "" if value is None else str(value).strip()
    percent_match = re.fullmatch(r"([-+]?(?:\d+(?:\.\d+)?|\.\d+))%", text)
    if percent_match:
        return float(percent_match.group(1)) / 100
    return _parse_number_sort_value(value)


def _parse_formula_cell_reference(value: str) -> tuple[int, int] | None:
    match = FORMULA_CELL_REFERENCE_ONLY_RE.fullmatch(value.strip())
    if not match:
        return None
    column_index = _excel_column_index(match.group(1))
    row_number = int(match.group(2))
    if column_index is None or row_number < 1:
        return None
    return row_number - 1, column_index


def _resolve_formula_reference_value(
    token: str,
    *,
    rows: list[Any],
    columns: list[Any],
    depth: int,
) -> Any:
    reference = _parse_formula_cell_reference(token)
    if reference is None:
        return _unquote_formula_string(token)

    row_index, column_index = reference
    if row_index < 0 or row_index >= len(rows):
        return ""
    raw_value = _extract_row_cell_value(rows[row_index], column_index, columns)
    return _evaluate_formula_sort_value(
        raw_value,
        row_index=row_index,
        column_index=column_index,
        rows=rows,
        columns=columns,
        depth=depth + 1,
    )


def _evaluate_formula_sort_value(
    value: Any,
    *,
    row_index: int,
    column_index: int,
    rows: list[Any],
    columns: list[Any],
    depth: int = 0,
) -> Any:
    if depth > 8 or not _is_formula_expression(value):
        return value

    formula = _normalize_formula_for_sort(str(value))
    date_match = DATE_PARSE_FORMULA_RE.match(formula)
    if date_match:
        source_value = _resolve_formula_reference_value(
            date_match.group("source"),
            rows=rows,
            columns=columns,
            depth=depth,
        )
        pattern = _unquote_formula_string(date_match.group("pattern")) or "yyyymmdd"
        parsed = _parse_compact_date_serial(source_value, pattern)
        return parsed if parsed is not None else value

    offset_match = CELL_OFFSET_FORMULA_RE.match(formula)
    if offset_match:
        source_value = _resolve_formula_reference_value(
            offset_match.group("source"),
            rows=rows,
            columns=columns,
            depth=depth,
        )
        numeric_value = _parse_number_sort_value(source_value)
        if numeric_value is None:
            numeric_value = _parse_date_sort_value(source_value)
        if numeric_value is None:
            return value
        offset = float(offset_match.group("offset"))
        return numeric_value + offset if offset_match.group("operator") == "+" else numeric_value - offset

    return value


def _excel_column_index(label: str) -> int | None:
    index = 0
    for char in label.upper():
        char_code = ord(char)
        if char_code < 65 or char_code > 90:
            return None
        index = index * 26 + char_code - 64
    return index - 1


def _excel_column_label(index: int) -> str:
    current = index
    label = ""
    while True:
        label = chr(65 + (current % 26)) + label
        current = current // 26 - 1
        if current < 0:
            return label


def _apply_formula_reference_column_case(label: str, source_label: str) -> str:
    return label.lower() if source_label == source_label.lower() else label


def _remap_formula_cell_references(
    formula: str,
    *,
    row_index_map: dict[int, int | None] | None = None,
    column_index_map: dict[int, int | None] | None = None,
) -> str:
    if not _is_formula_expression(formula):
        return formula

    def replace(match: re.Match[str]) -> str:
        prefix = match.group(1)
        column_absolute_marker = match.group(2)
        column_label = match.group(3)
        row_absolute_marker = match.group(4)
        row_label = match.group(5)
        source_column_index = _excel_column_index(column_label)
        source_row_number = int(row_label)
        if source_column_index is None or source_row_number < 1:
            return f"{prefix}#REF!"

        source_row_index = source_row_number - 1
        next_column_index = (
            column_index_map.get(source_column_index, source_column_index)
            if column_index_map is not None
            else source_column_index
        )
        next_row_index = (
            row_index_map.get(source_row_index, source_row_index)
            if row_index_map is not None
            else source_row_index
        )
        if next_column_index is None or next_row_index is None or next_column_index < 0 or next_row_index < 0:
            return f"{prefix}#REF!"

        next_column_label = _apply_formula_reference_column_case(
            _excel_column_label(next_column_index),
            column_label,
        )
        return f"{prefix}{column_absolute_marker}{next_column_label}{row_absolute_marker}{next_row_index + 1}"

    return FORMULA_CELL_REFERENCE_RE.sub(replace, formula)


def _remap_formula_value_references(
    value: Any,
    *,
    row_index_map: dict[int, int | None] | None = None,
    column_index_map: dict[int, int | None] | None = None,
) -> Any:
    if not _is_formula_expression(value):
        return value
    return _remap_formula_cell_references(
        value,
        row_index_map=row_index_map,
        column_index_map=column_index_map,
    )


def _remap_row_formula_cell_references(
    row: Any,
    *,
    columns: list[Any],
    row_index_map: dict[int, int | None] | None = None,
    column_index_map: dict[int, int | None] | None = None,
) -> Any:
    if isinstance(row, list):
        return [
            _remap_formula_value_references(
                value,
                row_index_map=row_index_map,
                column_index_map=column_index_map,
            )
            for value in row
        ]

    if isinstance(row, dict):
        next_row = dict(row)
        for column in columns:
            column_key = str(column)
            if column_key not in next_row:
                continue
            next_row[column_key] = _remap_formula_value_references(
                next_row[column_key],
                row_index_map=row_index_map,
                column_index_map=column_index_map,
            )
        return next_row

    return row


def _shift_formula_cell_references(formula: str, *, row_delta: int = 0, column_delta: int = 0) -> str:
    if not _is_formula_expression(formula) or (row_delta == 0 and column_delta == 0):
        return formula

    def replace(match: re.Match[str]) -> str:
        prefix = match.group(1)
        column_absolute_marker = match.group(2)
        column_label = match.group(3)
        row_absolute_marker = match.group(4)
        row_label = match.group(5)
        source_column_index = _excel_column_index(column_label)
        source_row_number = int(row_label)
        if source_column_index is None or source_row_number < 1:
            return f"{prefix}#REF!"

        next_column_index = source_column_index if column_absolute_marker else source_column_index + column_delta
        next_row_number = source_row_number if row_absolute_marker else source_row_number + row_delta
        if next_column_index < 0 or next_row_number < 1:
            return f"{prefix}#REF!"

        next_column_label = _apply_formula_reference_column_case(
            _excel_column_label(next_column_index),
            column_label,
        )
        return f"{prefix}{column_absolute_marker}{next_column_label}{row_absolute_marker}{next_row_number}"

    return FORMULA_CELL_REFERENCE_RE.sub(replace, formula)


def _shift_formula_value_references(value: Any, *, row_delta: int = 0, column_delta: int = 0) -> Any:
    if not _is_formula_expression(value):
        return value
    return _shift_formula_cell_references(str(value), row_delta=row_delta, column_delta=column_delta)


def _normalize_sheet_row(row: Any, column_count: int) -> list[Any]:
    if isinstance(row, list):
        return [*(row[:column_count]), *([""] * max(column_count - len(row), 0))]
    if isinstance(row, dict):
        return [row.get(str(index), "") for index in range(column_count)]
    return [""] * column_count


def _normalize_sheet_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _find_column_index(columns: list[Any], header: str) -> int | None:
    for index, column in enumerate(columns):
        if _normalize_sheet_text(column) == header:
            return index
    return None


def _find_bound_column_index(columns: list[Any], header: str, fallback_index: int | None = None) -> int | None:
    header_index = _find_column_index(columns, header)
    if header_index is not None:
        return header_index
    if fallback_index is not None and 0 <= fallback_index < len(columns):
        return fallback_index
    return None


def _find_attendance_column_index(columns: list[Any], field_key: str) -> int | None:
    binding = ATTENDANCE_FIELD_BINDINGS.get(field_key)
    if binding is None:
        return None
    header, fallback_index = binding
    header_index = _find_column_index(columns, header)
    if header_index is not None:
        return header_index
    if field_key in {"lesson_links", "clockin_links"}:
        return None

    has_link_count_columns = (
        _find_column_index(columns, ATTENDANCE_FIELD_BINDINGS["lesson_links"][0]) is not None
        or _find_column_index(columns, ATTENDANCE_FIELD_BINDINGS["clockin_links"][0]) is not None
    )
    effective_fallback_index = fallback_index if has_link_count_columns else ATTENDANCE_FIELD_LEGACY_FALLBACKS.get(
        field_key,
        fallback_index,
    )
    if 0 <= effective_fallback_index < len(columns):
        return effective_fallback_index
    return None


def _shift_cell_meta_columns_for_insert(cell_meta: Any, insert_index: int, amount: int) -> dict[str, Any]:
    if not isinstance(cell_meta, dict) or amount <= 0:
        return dict(cell_meta) if isinstance(cell_meta, dict) else {}

    shifted: dict[str, Any] = {}
    for key, meta in cell_meta.items():
        parsed = _parse_cell_meta_key(key)
        if parsed is None:
            shifted[str(key)] = meta
            continue
        row_index, column_index = parsed
        next_column_index = column_index + amount if column_index >= insert_index else column_index
        shifted[f"{row_index}:{next_column_index}"] = meta
    return shifted


def _insert_columns_into_header_groups(header_groups: Any, insert_index: int, amount: int) -> list[Any]:
    if not isinstance(header_groups, list) or amount <= 0:
        return list(header_groups) if isinstance(header_groups, list) else []

    next_groups: list[Any] = []
    for row in header_groups:
        if not isinstance(row, list):
            next_groups.append(row)
            continue

        next_row: list[Any] = []
        current_index = 0
        expanded = False
        for cell in row:
            if isinstance(cell, dict):
                next_cell = dict(cell)
                colspan = int(next_cell.get("colspan") or 1)
            else:
                next_cell = cell
                colspan = 1

            colspan = max(colspan, 1)
            if not expanded and current_index <= insert_index < current_index + colspan:
                if isinstance(next_cell, dict):
                    next_cell["colspan"] = colspan + amount
                else:
                    next_cell = {"label": str(next_cell), "colspan": colspan + amount}
                expanded = True

            next_row.append(next_cell)
            current_index += colspan

        if not expanded and insert_index >= current_index:
            next_row.append({"label": "", "colspan": amount})
        next_groups.append(next_row)
    return next_groups


def _shift_merged_cell_columns_for_insert(merged_cells: Any, insert_index: int, amount: int) -> list[Any]:
    if not isinstance(merged_cells, list) or amount <= 0:
        return list(merged_cells) if isinstance(merged_cells, list) else []

    shifted: list[Any] = []
    for cell in merged_cells:
        if not isinstance(cell, dict):
            continue
        row = int(cell.get("row") or 0)
        col = int(cell.get("col") or 0)
        rowspan = max(int(cell.get("rowspan") or 1), 1)
        colspan = max(int(cell.get("colspan") or 1), 1)
        if col < insert_index < col + colspan:
            colspan += amount
        elif col >= insert_index:
            col += amount
        if rowspan > 1 or colspan > 1:
            shifted.append({"row": row, "col": col, "rowspan": rowspan, "colspan": colspan})
    return shifted


def _insert_cell_into_grid_row(row: Any, column_count: int, insert_index: int) -> list[Any]:
    normalized_row = _normalize_sheet_row(row, column_count)
    return [
        *normalized_row[:insert_index],
        "",
        *normalized_row[insert_index:],
    ]


def _insert_document_column(
    document_json: dict[str, Any],
    *,
    insert_index: int,
    header: str,
    width: int = 96,
) -> dict[str, Any]:
    normalized = _normalize_document_json(document_json)
    columns = _normalize_document_columns(normalized)
    bounded_insert_index = min(max(insert_index, 0), len(columns))
    column_index_map = {
        index: index + 1 if index >= bounded_insert_index else index
        for index in range(len(columns))
    }

    remapped_rows = [
        _remap_row_formula_cell_references(row, columns=columns, column_index_map=column_index_map)
        for row in _extract_document_rows(normalized)
    ]
    next_rows: list[Any] = []
    for row in remapped_rows:
        if isinstance(row, list):
            normalized_row = _normalize_sheet_row(row, len(columns))
            next_rows.append([
                *normalized_row[:bounded_insert_index],
                "",
                *normalized_row[bounded_insert_index:],
            ])
        elif isinstance(row, dict):
            next_row = dict(row)
            next_row.setdefault(header, "")
            next_rows.append(next_row)
        else:
            next_rows.append([""] * bounded_insert_index + [""] + [""] * (len(columns) - bounded_insert_index))

    source_widths = normalized.get("column_widths")
    if isinstance(source_widths, list):
        next_widths = [*source_widths[:bounded_insert_index], width, *source_widths[bounded_insert_index:]]
    else:
        next_widths = [width] * (len(columns) + 1)

    next_document = {
        **normalized,
        "columns": [*columns[:bounded_insert_index], header, *columns[bounded_insert_index:]],
        "rows": next_rows,
        "column_widths": next_widths,
    }
    grid_rows = _extract_document_grid_rows(normalized)
    if grid_rows:
        next_document["grid_rows"] = [
            _insert_cell_into_grid_row(row, len(columns), bounded_insert_index)
            for row in grid_rows
        ]
    if "cell_meta" in normalized:
        next_document["cell_meta"] = _shift_cell_meta_columns_for_insert(
            normalized.get("cell_meta"),
            bounded_insert_index,
            1,
        )
    if "merged_cells" in normalized:
        next_document["merged_cells"] = _shift_merged_cell_columns_for_insert(
            normalized.get("merged_cells"),
            bounded_insert_index,
            1,
        )
    if "header_groups" in normalized:
        next_document["header_groups"] = _insert_columns_into_header_groups(
            normalized.get("header_groups"),
            bounded_insert_index,
            1,
        )
    return next_document


def _get_attendance_link_count_field_insert_index(columns: list[Any], field_key: str) -> int:
    if field_key == "lesson_links":
        clockin_index = _find_column_index(columns, ATTENDANCE_FIELD_BINDINGS["clockin_links"][0])
        if clockin_index is not None:
            return clockin_index
        owner_index = _find_attendance_column_index(columns, "course_owner")
        return min((owner_index + 1) if owner_index is not None else 4, len(columns))

    lesson_index = _find_column_index(columns, ATTENDANCE_FIELD_BINDINGS["lesson_links"][0])
    if lesson_index is not None:
        return min(lesson_index + 1, len(columns))
    owner_index = _find_attendance_column_index(columns, "course_owner")
    return min((owner_index + 1) if owner_index is not None else 4, len(columns))


def _ensure_attendance_link_count_columns(document_json: dict[str, Any]) -> dict[str, Any]:
    next_document = _normalize_document_json(document_json)
    for field_key in ("lesson_links", "clockin_links"):
        columns = _normalize_document_columns(next_document)
        header, _fallback_index = ATTENDANCE_FIELD_BINDINGS[field_key]
        if _find_column_index(columns, header) is not None:
            continue
        next_document = _insert_document_column(
            next_document,
            insert_index=_get_attendance_link_count_field_insert_index(columns, field_key),
            header=header,
            width=96,
        )
    return next_document


def _parse_attendance_template_course(value: Any) -> dict[str, Any] | None:
    match = ATTENDANCE_TEMPLATE_COURSE_TEXT_RE.search(_normalize_sheet_text(value))
    if not match:
        return None

    raw_date = match.group("date")
    course_date: date | None = None
    if raw_date:
        if len(raw_date) == 6:
            year = _normalize_two_digit_year(int(raw_date[:2]))
            month = int(raw_date[2:4])
            day = int(raw_date[4:6])
        else:
            year = int(raw_date[:4])
            month = int(raw_date[4:6])
            day = int(raw_date[6:8])

        try:
            course_date = date(year, month, day)
        except ValueError:
            return None

    return {
        "date": course_date,
        "edition": int(match.group("edition")),
        "course": match.group("course"),
    }


def _get_next_month_first_day(today: date | None = None) -> date:
    current = today or date.today()
    year = current.year + (1 if current.month == 12 else 0)
    month = 1 if current.month == 12 else current.month + 1
    return date(year, month, 1)


def _get_next_sunday(today: date | None = None) -> date:
    current = today or date.today()
    days_until_sunday = (6 - current.weekday()) % 7
    if days_until_sunday == 0:
        days_until_sunday = 7
    return current + timedelta(days=days_until_sunday)


def _parse_attendance_date_text(value: Any) -> date | None:
    text = _normalize_sheet_text(value)
    if not text:
        return None

    try:
        return date.fromisoformat(text)
    except ValueError:
        pass

    separated = re.fullmatch(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", text)
    if separated:
        try:
            return date(int(separated.group(1)), int(separated.group(2)), int(separated.group(3)))
        except ValueError:
            return None

    compact = re.fullmatch(r"(\d{6}|\d{8})", text)
    if compact:
        digits = compact.group(1)
        try:
            if len(digits) == 6:
                return date(_normalize_two_digit_year(int(digits[:2])), int(digits[2:4]), int(digits[4:6]))
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            return None
    return None


def _get_attendance_template_target_date(payload: NoteSheetAttendanceTemplateGenerationRequest | None = None) -> date:
    if payload and payload.target_date is not None:
        if payload.target_year is not None or payload.target_month is not None:
            raise HTTPException(status_code=400, detail="target_date 不能和 target_year/target_month 同时提供")
        parsed_date = _parse_attendance_date_text(payload.target_date)
        if parsed_date is None:
            raise HTTPException(status_code=400, detail="target_date 格式不正确")
        return parsed_date
    if payload and payload.target_year is not None and payload.target_month is not None:
        return date(int(payload.target_year), int(payload.target_month), 1)
    if payload and (payload.target_year is not None or payload.target_month is not None):
        raise HTTPException(status_code=400, detail="target_year 和 target_month 必须同时提供")
    return _get_next_month_first_day()


def _is_attendance_zen_course_type(course_type: str) -> bool:
    return "禅宗" in course_type


def _get_attendance_course_template_target_date(
    course_type: str,
    payload: NoteSheetAttendanceCourseTemplateGenerationRequest | None = None,
) -> date:
    if payload and (
        payload.target_date is not None
        or payload.target_year is not None
        or payload.target_month is not None
    ):
        return _get_attendance_template_target_date(payload)
    return _get_next_sunday() if _is_attendance_zen_course_type(course_type) else _get_next_month_first_day()


def _get_attendance_batch_course_types(target_date: date) -> tuple[str, ...]:
    course_types = [*ATTENDANCE_TEMPLATE_MONTHLY_SOURCE_COURSES]
    if target_date.month % 2 == 1:
        course_types.extend(ATTENDANCE_TEMPLATE_ODD_MONTH_SOURCE_COURSES)
    else:
        course_types.extend(ATTENDANCE_TEMPLATE_EVEN_MONTH_SOURCE_COURSES)
    return tuple(course_types)


def _format_attendance_course_date(value: date) -> str:
    return f"{value.year:04d}{value.month:02d}{value.day:02d}"


def _format_attendance_date_serial(value: date) -> str:
    serial = _date_parts_to_serial(value.year, value.month, value.day)
    if serial is None:
        return ""
    return str(int(serial))


def _coerce_attendance_date(value: Any) -> date | None:
    text = _normalize_sheet_text(value)
    if re.fullmatch(r"\d{6}|\d{8}", text):
        parsed_text_date = _parse_attendance_date_text(text)
        if parsed_text_date is not None:
            return parsed_text_date

    sort_value = _parse_date_sort_value(value)
    if sort_value is not None:
        parts = _serial_to_date_parts(sort_value)
        if parts is not None:
            return date(*parts)

    compact_serial = _parse_compact_date_serial(value)
    if compact_serial is not None:
        parts = _serial_to_date_parts(compact_serial)
        if parts is not None:
            return date(*parts)
    return None


def _extract_attendance_row_start_date(
    row: list[Any],
    *,
    row_index: int,
    columns: list[Any],
    rows: list[Any],
    start_date_index: int | None,
) -> date | None:
    if start_date_index is None or start_date_index >= len(row):
        return None
    raw_value = row[start_date_index]
    evaluated_value = _evaluate_formula_sort_value(
        raw_value,
        row_index=row_index,
        column_index=start_date_index,
        rows=rows,
        columns=columns,
    )
    return _coerce_attendance_date(evaluated_value)


def _find_attendance_template_source_row(
    rows: list[Any],
    *,
    columns: list[Any],
    course_type: str,
    target_date: date,
) -> tuple[int, list[Any], dict[str, Any]] | None:
    column_count = len(columns)
    type_index = _find_attendance_column_index(columns, "course_type")
    name_index = _find_attendance_column_index(columns, "course_name")
    online_sheet_index = _find_attendance_column_index(columns, "online_sheet")
    start_date_index = _find_attendance_column_index(columns, "start_date")
    candidates: list[tuple[date, int, list[Any], dict[str, Any]]] = []

    for row_index, source_row in enumerate(rows):
        row = _normalize_sheet_row(source_row, column_count)
        if type_index is None or _normalize_sheet_text(row[type_index]) != course_type:
            continue

        source_date = _extract_attendance_row_start_date(
            row,
            row_index=row_index,
            columns=columns,
            rows=rows,
            start_date_index=start_date_index,
        )
        if source_date is None:
            continue
        if source_date >= target_date:
            continue

        name_value = row[name_index] if name_index is not None and name_index < len(row) else ""
        online_sheet_value = (
            row[online_sheet_index]
            if online_sheet_index is not None and online_sheet_index < len(row)
            else ""
        )
        info = {
            "date": source_date,
            "course_name": _normalize_sheet_text(name_value),
            "online_sheet": _normalize_sheet_text(online_sheet_value),
        }
        candidates.append((source_date, row_index, row, info))

    if not candidates:
        return None

    _source_date, row_index, row, info = max(
        candidates,
        key=lambda item: (item[0], -item[1]),
    )
    return row_index, row, info


def _attendance_template_row_exists(
    rows: list[Any],
    *,
    columns: list[Any],
    course_type: str,
    target_date: date,
) -> bool:
    type_index = _find_attendance_column_index(columns, "course_type")
    start_date_index = _find_attendance_column_index(columns, "start_date")
    column_count = len(columns)

    for row_index, source_row in enumerate(rows):
        row = _normalize_sheet_row(source_row, column_count)
        type_value = _normalize_sheet_text(row[type_index]) if type_index is not None else ""
        if type_value != course_type:
            continue
        start_date = _extract_attendance_row_start_date(
            row,
            row_index=row_index,
            columns=columns,
            rows=rows,
            start_date_index=start_date_index,
        )
        if start_date == target_date:
            return True
    return False


def _set_row_cell_value(row: Any, columns: list[Any], column_index: int, value: Any) -> Any:
    if isinstance(row, list):
        next_row = [*row, *([""] * max(column_index + 1 - len(row), 0))]
        next_row[column_index] = value
        return next_row
    if isinstance(row, dict):
        next_row = dict(row)
        column_key = str(columns[column_index]) if column_index < len(columns) else str(column_index)
        next_row[column_key] = value
        return next_row
    next_row = [""] * max(len(columns), column_index + 1)
    next_row[column_index] = value
    return next_row


def _attendance_row_has_completion(row: Any, columns: list[Any], completed_index: int) -> bool:
    return _normalize_sheet_text(_extract_row_cell_value(row, completed_index, columns)) != ""


def _set_attendance_summary_row_completed(
    document_json: dict[str, Any],
    *,
    row_index: int,
    completion_date: date,
) -> tuple[dict[str, Any], int]:
    normalized = _normalize_document_json(document_json)
    columns = _normalize_document_columns(normalized)
    rows = _extract_document_rows(normalized)
    completed_index = _find_attendance_column_index(columns, "completed_date")
    if completed_index is None:
        raise HTTPException(status_code=400, detail="当前表缺少考勤实际完成结点字段")
    if row_index < 0 or row_index >= len(rows):
        raise HTTPException(status_code=400, detail="row_index 超出表格范围")

    completed_row = _set_row_cell_value(
        rows[row_index],
        columns,
        completed_index,
        _format_attendance_date_serial(completion_date),
    )
    remaining_rows = [
        (source_index, row)
        for source_index, row in enumerate(rows)
        if source_index != row_index
    ]
    insert_index = next(
        (
            index
            for index, (_source_index, row) in enumerate(remaining_rows)
            if _attendance_row_has_completion(row, columns, completed_index)
        ),
        len(remaining_rows),
    )
    ordered_rows = [
        *remaining_rows[:insert_index],
        (row_index, completed_row),
        *remaining_rows[insert_index:],
    ]
    row_index_map = {
        source_index: target_index
        for target_index, (source_index, _row) in enumerate(ordered_rows)
    }
    next_rows = [
        _remap_row_formula_cell_references(
            row,
            columns=columns,
            row_index_map=row_index_map,
        )
        for _source_index, row in ordered_rows
    ]
    next_document = _replace_document_data_rows({
        **normalized,
        "columns": columns,
    }, next_rows)
    if isinstance(normalized.get("cell_meta"), dict):
        row_offset = _normalize_document_data_start_row(normalized) if _extract_document_grid_rows(normalized) else 0
        next_document["cell_meta"] = _remap_cell_meta_rows(
            normalized.get("cell_meta"),
            row_index_map,
            row_offset=row_offset,
        )
    return next_document, row_index_map[row_index]


def _shift_cell_meta_rows_for_insert(cell_meta: Any, insert_index: int, amount: int, *, row_offset: int = 0) -> dict[str, Any]:
    if not isinstance(cell_meta, dict) or amount <= 0:
        return dict(cell_meta) if isinstance(cell_meta, dict) else {}

    shifted: dict[str, Any] = {}
    for key, meta in cell_meta.items():
        parsed = _parse_cell_meta_key(key)
        if parsed is None:
            continue
        row_index, column_index = parsed
        effective_insert_index = insert_index + row_offset
        next_row_index = row_index + amount if row_index >= effective_insert_index else row_index
        shifted[f"{next_row_index}:{column_index}"] = meta
    return shifted


def _increment_attendance_template_edition(text: str, course_type: str) -> str:
    def replace(match: re.Match[str]) -> str:
        matched_course = match.group("course")
        if matched_course != course_type:
            return match.group(0)
        return f"第{int(match.group('edition')) + 1}届{matched_course}"

    return ATTENDANCE_TEMPLATE_COURSE_TEXT_RE.sub(replace, text, count=1)


def _derive_attendance_template_text(value: Any, *, course_type: str, target_date: date) -> str:
    text = _normalize_sheet_text(value)
    if not text:
        return ""

    date_prefix = ""
    body = text
    leading_date = ATTENDANCE_TEMPLATE_LEADING_DATE_RE.match(text)
    if leading_date:
        date_prefix = _format_attendance_course_date(target_date)
        body = leading_date.group("body")

    body = _increment_attendance_template_edition(body, course_type)
    return f"{date_prefix}{body}"


def _build_inserted_attendance_template_row(
    source_row: list[Any],
    *,
    columns: list[Any],
    source_row_index: int,
    target_row_index: int,
    target_date: date,
    course_type: str,
    source_start_date: date | None = None,
) -> list[Any]:
    column_count = len(columns)
    row_delta = target_row_index - source_row_index
    next_row = [
        _shift_formula_value_references(value, row_delta=row_delta)
        for value in _normalize_sheet_row(source_row, column_count)
    ]

    replacements: dict[str, Any] = {
        "course_type": course_type,
        "start_date": _format_attendance_date_serial(target_date),
        "completed_date": "",
    }
    end_date_index = _find_attendance_column_index(columns, "end_date")
    if end_date_index is not None and end_date_index < len(source_row) and source_start_date is not None:
        source_end_value = source_row[end_date_index]
        source_end_date = None if _is_formula_expression(source_end_value) else _coerce_attendance_date(source_end_value)
        if source_end_date is not None:
            replacements["end_date"] = _format_attendance_date_serial(
                target_date + (source_end_date - source_start_date)
            )

    for field_key in ("course_name", "online_sheet"):
        column_index = _find_attendance_column_index(columns, field_key)
        if column_index is not None:
            replacements[field_key] = _derive_attendance_template_text(
                source_row[column_index] if column_index < len(source_row) else "",
                course_type=course_type,
                target_date=target_date,
            )

    for field_key, value in replacements.items():
        column_index = _find_attendance_column_index(columns, field_key)
        if column_index is not None:
            next_row[column_index] = value

    clear_from_index = _find_attendance_column_index(columns, "registration_count")
    if clear_from_index is not None:
        for column_index in range(clear_from_index, len(columns)):
            if not _is_formula_expression(next_row[column_index]):
                next_row[column_index] = ""

    return next_row


def _remap_existing_rows_for_insert(rows: list[Any], *, columns: list[Any], insert_index: int, amount: int) -> list[Any]:
    if amount <= 0:
        return rows
    row_index_map = {
        index: index + amount if index >= insert_index else index
        for index in range(len(rows))
    }
    return [
        _remap_row_formula_cell_references(row, columns=columns, row_index_map=row_index_map)
        for row in rows
    ]


def _build_attendance_template_detail_response(
    session: Session,
    document: SheetDocument,
    response_document: dict[str, Any],
    *,
    access: NoteSheetResourceAccess | None = None,
) -> NoteSheetDetailResponse:
    workbook_items = _list_workbook_refs_for_sheet_ids(session, [document.id]).get(document.id, [])
    paginate_enabled, page_size = _get_document_pagination_settings(response_document)
    if paginate_enabled:
        page_document, pagination = _build_paged_document(response_document, page=1, page_size=page_size)
    else:
        page_document = _normalize_document_json(response_document)
        pagination = None

    return NoteSheetDetailResponse.model_validate(
        _serialize_sheet_detail(
            document,
            workbook_items=workbook_items,
            document_json=page_document,
            pagination=pagination,
            access=access,
        ),
    )


def _is_attendance_summary_document(session: Session, document: SheetDocument) -> bool:
    if int(document.numeric_id or 0) != ATTENDANCE_SUMMARY_SHEET_ID:
        return False

    workbook = session.exec(
        select(WorkbookDocument).where(WorkbookDocument.numeric_id == ATTENDANCE_SUMMARY_WORKBOOK_ID)
    ).first()
    if workbook is None:
        return False

    link = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == workbook.id)
        .where(WorkbookSheetLink.sheet_id == document.id)
    ).first()
    return link is not None


def run_attendance_summary_template_job() -> tuple[int, int]:
    with Session(engine) as session:
        document = session.exec(
            select(SheetDocument).where(SheetDocument.numeric_id == ATTENDANCE_SUMMARY_SHEET_ID)
        ).first()
        if document is None or document.scope != "notes" or not _is_attendance_summary_document(session, document):
            return 0, 0

        current_document = _normalize_document_json(dict(document.document_json or {}))
        next_document, generated, skipped = _generate_attendance_next_month_templates(
            current_document,
            target_date=_get_next_month_first_day(),
        )
        if generated and current_document != next_document:
            document.document_json = next_document
            document.version = max(int(document.version or 1), 1) + 1
            document.updated_by_user_id = document.owner_user_id
            document.updated_at = time.time()
            session.add(document)
            session.commit()

        if generated or skipped:
            print(
                "Attendance summary template job finished: "
                f"generated={len(generated)} skipped={len(skipped)}"
            )
        return len(generated), len(skipped)


def init_attendance_summary_scheduler() -> None:
    if get_settings().is_test:
        return
        
    from backend.db import engine
    from backend.models import AppSetting
    from sqlmodel import Session
    with Session(engine) as session:
        row = session.get(AppSetting, "background_task.attendance_summary_monthly_templates.enabled")
        enabled = bool(row.value.get("enabled", False)) if row and isinstance(row.value, dict) else False
        
    if not enabled:
        return
        
    if not attendance_summary_scheduler.running:
        attendance_summary_scheduler.start()
    attendance_summary_scheduler.add_job(
        lambda: background_task_queue.enqueue(
            "attendance_summary_monthly_templates",
            run_attendance_summary_template_job,
        ),
        CronTrigger.from_crontab("5 0 27 * *"),
        id="attendance_summary_monthly_templates",
        replace_existing=True,
    )
    print("Attendance summary template job scheduled: 5 0 27 * *")


def shutdown_attendance_summary_scheduler() -> None:
    if attendance_summary_scheduler.running:
        attendance_summary_scheduler.shutdown(wait=False)


def _get_attendance_course_name_from_row(row: list[Any], columns: list[Any]) -> str:
    name_index = _find_attendance_column_index(columns, "course_name")
    if name_index is None or name_index >= len(row):
        return ""
    return _normalize_sheet_text(row[name_index])


def _generate_attendance_course_templates(
    document_json: dict[str, Any],
    *,
    targets: list[tuple[str, date]],
) -> tuple[dict[str, Any], list[NoteSheetAttendanceTemplateActionItem], list[NoteSheetAttendanceTemplateActionItem]]:
    normalized = _normalize_document_json(document_json)
    columns = _normalize_document_columns(normalized)
    rows = _extract_document_rows(normalized)
    generated: list[NoteSheetAttendanceTemplateActionItem] = []
    skipped: list[NoteSheetAttendanceTemplateActionItem] = []

    if not rows:
        for course_type, _target_date in targets:
            skipped.append(NoteSheetAttendanceTemplateActionItem(course_type=course_type, reason="当前表没有可复制的模板行"))
        return normalized, generated, skipped

    pending_rows: list[tuple[int, list[Any], date, NoteSheetAttendanceTemplateActionItem]] = []
    seen_targets: set[tuple[str, date]] = set()
    for course_type, target_date in targets:
        target_key = (course_type, target_date)
        if target_key in seen_targets:
            continue
        seen_targets.add(target_key)

        if _attendance_template_row_exists(
            rows,
            columns=columns,
            course_type=course_type,
            target_date=target_date,
        ):
            skipped.append(NoteSheetAttendanceTemplateActionItem(
                course_type=course_type,
                target_date=target_date.isoformat(),
                reason="目标课程已存在",
            ))
            continue

        source = _find_attendance_template_source_row(
            rows,
            columns=columns,
            course_type=course_type,
            target_date=target_date,
        )
        if source is None:
            skipped.append(NoteSheetAttendanceTemplateActionItem(course_type=course_type, reason="没有找到上一次课程模板"))
            continue

        source_row_index, source_row, source_info = source
        preview_row = _build_inserted_attendance_template_row(
            source_row,
            columns=columns,
            source_row_index=source_row_index,
            target_row_index=source_row_index,
            target_date=target_date,
            course_type=course_type,
            source_start_date=source_info["date"],
        )
        target_course_name = _get_attendance_course_name_from_row(preview_row, columns)
        pending_rows.append((
            source_row_index,
            source_row,
            source_info["date"],
            NoteSheetAttendanceTemplateActionItem(
                course_type=course_type,
                course_name=target_course_name,
                target_date=target_date.isoformat(),
            ),
        ))

    if not pending_rows:
        return normalized, generated, skipped

    insert_index = 0
    existing_rows = _remap_existing_rows_for_insert(
        rows,
        columns=columns,
        insert_index=insert_index,
        amount=len(pending_rows),
    )

    inserted_rows: list[list[Any]] = []
    for offset, (source_row_index, source_row, source_start_date, item) in enumerate(pending_rows):
        target_row_index = insert_index + offset
        item_target_date = _parse_attendance_date_text(item.target_date) or _get_next_month_first_day()
        inserted_rows.append(_build_inserted_attendance_template_row(
            source_row,
            columns=columns,
            source_row_index=source_row_index,
            target_row_index=target_row_index,
            target_date=item_target_date,
            course_type=item.course_type,
            source_start_date=source_start_date,
        ))
        item.row_index = target_row_index
        generated.append(item)

    if not inserted_rows:
        return normalized, generated, skipped

    next_rows = [
        *existing_rows[:insert_index],
        *inserted_rows,
        *existing_rows[insert_index:],
    ]
    next_document = _replace_document_data_rows({
        **normalized,
        "columns": columns,
    }, next_rows)
    if "cell_meta" in normalized:
        next_document["cell_meta"] = _shift_cell_meta_rows_for_insert(
            normalized.get("cell_meta"),
            insert_index,
            len(inserted_rows),
            row_offset=_normalize_document_data_start_row(normalized) if _extract_document_grid_rows(normalized) else 0,
        )
    return next_document, generated, skipped


def _generate_attendance_next_month_templates(
    document_json: dict[str, Any],
    *,
    target_date: date,
) -> tuple[dict[str, Any], list[NoteSheetAttendanceTemplateActionItem], list[NoteSheetAttendanceTemplateActionItem]]:
    return _generate_attendance_course_templates(
        document_json,
        targets=[
            (course_type, target_date)
            for course_type in _get_attendance_batch_course_types(target_date)
        ],
    )


def _resolve_attendance_course_template_type(
    document_json: dict[str, Any],
    payload: NoteSheetAttendanceCourseTemplateGenerationRequest,
) -> str:
    if payload.course_type is not None:
        course_type = _normalize_sheet_text(payload.course_type)
        if course_type:
            return course_type

    if payload.row_index is None:
        raise HTTPException(status_code=400, detail="必须提供 row_index 或 course_type")

    normalized = _normalize_document_json(document_json)
    columns = _normalize_document_columns(normalized)
    rows = _extract_document_rows(normalized)
    type_index = _find_attendance_column_index(columns, "course_type")
    if type_index is None:
        raise HTTPException(status_code=400, detail="当前表缺少课程类型字段")
    if payload.row_index >= len(rows):
        raise HTTPException(status_code=400, detail="row_index 超出表格范围")

    row = _normalize_sheet_row(rows[payload.row_index], len(columns))
    course_type = _normalize_sheet_text(row[type_index])
    if not course_type:
        raise HTTPException(status_code=400, detail="选中的课程类型为空")
    return course_type


def _extract_attendance_course_script_token(url: Any) -> str:
    text = _normalize_sheet_text(url)
    if not text:
        return ""

    match = ATTENDANCE_COURSE_SCRIPT_KDOCS_TOKEN_RE.search(text)
    if match:
        return match.group("token")

    tail = text.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    if re.fullmatch(r"[A-Za-z0-9]{8,}", tail):
        return tail
    return ""


def _format_attendance_course_script_prefix(value: date) -> str:
    return f"d{value.year % 100:02d}{value.month:02d}{value.day:02d}"


def _parse_attendance_course_script_stem(stem: str) -> tuple[date, str] | None:
    match = ATTENDANCE_COURSE_SCRIPT_STEM_RE.match(stem)
    if not match:
        return None

    raw_date = match.group("date")
    try:
        parsed_date = date(
            _normalize_two_digit_year(int(raw_date[:2])),
            int(raw_date[2:4]),
            int(raw_date[4:6]),
        )
    except ValueError:
        return None

    body = match.group("body").strip()
    return (parsed_date, body) if body else None


def _parse_attendance_course_text_date(value: Any) -> tuple[date | None, str]:
    text = _normalize_sheet_text(value)
    leading_date = ATTENDANCE_TEMPLATE_LEADING_DATE_RE.match(text)
    if not leading_date:
        chinese_month = re.match(r"^(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<body>.+)$", text)
        if not chinese_month:
            return None, text
        return None, chinese_month.group("body").strip()

    parsed_date = _parse_attendance_date_text(leading_date.group("date"))
    return parsed_date, leading_date.group("body").strip()


def _sanitize_attendance_course_script_body(value: Any) -> str:
    text = _normalize_sheet_text(value)
    text = text.replace("4.5阶", "4点5阶")
    text = ATTENDANCE_COURSE_SCRIPT_INVALID_FILENAME_RE.sub("_", text)
    return text.strip(" .")


def _standardize_attendance_course_script_stem(value: Any) -> str:
    text = _normalize_sheet_text(value)
    if not text:
        return ""

    text = ATTENDANCE_COURSE_SCRIPT_FILE_EXTENSION_RE.sub("", text)
    text = re.sub(r"^\d{2}(\d{6})", r"d\1", text, count=1)
    text = re.sub(r"^(\d{6})(?=\D|$)", r"d\1", text, count=1)
    text = text.replace(".", "点")
    text = ATTENDANCE_COURSE_SCRIPT_INVALID_FILENAME_RE.sub("_", text)
    return text.strip(" .")


def _get_document_cell_link_url(document_json: dict[str, Any], row_index: int, column_index: int) -> str:
    cell_meta = document_json.get("cell_meta")
    if not isinstance(cell_meta, dict):
        return ""
    entry = cell_meta.get(f"{row_index}:{column_index}")
    if not isinstance(entry, dict):
        return ""
    link = entry.get("link")
    if not isinstance(link, dict):
        return ""
    return _normalize_sheet_text(link.get("url"))


def _iter_attendance_course_script_files() -> list[Path]:
    base_dir = ATTENDANCE_COURSE_SCRIPT_DIR
    paths: list[Path] = []
    for directory in (base_dir, base_dir / "已完结"):
        if not directory.exists() or not directory.is_dir():
            continue
        paths.extend(path for path in directory.glob("*.py") if _parse_attendance_course_script_stem(path.stem))
    return paths


def _find_attendance_course_script_by_stem(stem: str) -> Path | None:
    filename = f"{stem}.py"
    for directory in (ATTENDANCE_COURSE_SCRIPT_DIR, ATTENDANCE_COURSE_SCRIPT_DIR / "已完结"):
        path = directory / filename
        if path.exists() and path.is_file():
            return path
    return None


def _normalize_attendance_zen_stage_tokens(value: str) -> set[str]:
    normalized = value.replace("点", ".")
    tokens: set[str] = set()
    if "4.5阶" in normalized:
        tokens.update({"4.5阶", "4点5阶"})
    for token in ("一阶", "二阶", "三阶", "四阶", "五阶", "二三阶"):
        if token in value:
            tokens.add(token)
    return tokens


def _attendance_course_script_matches_type(course_type: str, script_body: str) -> bool:
    normalized_type = _normalize_sheet_text(course_type)
    normalized_body = _normalize_sheet_text(script_body)
    if not normalized_type or not normalized_body:
        return False

    if "禅宗" in normalized_type:
        if "禅宗" not in normalized_body:
            return False
        stage_tokens = _normalize_attendance_zen_stage_tokens(normalized_type)
        if not stage_tokens:
            return True
        return any(token in normalized_body for token in stage_tokens)

    if normalized_type == "念住":
        return "念住" in normalized_body and "闯关" not in normalized_body
    if normalized_type == "念住闯关":
        return "念住闯关" in normalized_body
    if normalized_type == "觉观":
        return "觉观" in normalized_body
    if normalized_type in {"梵呗初阶", "梵呗增益"}:
        return normalized_type in normalized_body
    return normalized_type in normalized_body


def _find_attendance_course_script_template(
    course_type: str,
    *,
    target_date: date,
    target_stem: str,
    script_files: list[Path] | None = None,
) -> Path | None:
    candidates: list[tuple[int, int, str, Path]] = []
    for path in script_files if script_files is not None else _iter_attendance_course_script_files():
        parsed = _parse_attendance_course_script_stem(path.stem)
        if parsed is None or path.stem == target_stem:
            continue
        script_date, script_body = parsed
        if not _attendance_course_script_matches_type(course_type, script_body):
            continue

        date_delta = abs((script_date - target_date).days)
        future_penalty = 1 if script_date > target_date else 0
        candidates.append((date_delta, future_penalty, path.name, path))

    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1], item[2]))[3]


def _build_attendance_course_script_status(
    document_json: dict[str, Any],
    *,
    row_index: int,
    script_files: list[Path] | None = None,
) -> NoteSheetAttendanceCourseScriptStatusItem:
    normalized = _normalize_document_json(document_json)
    columns = _normalize_document_columns(normalized)
    rows = _extract_document_rows(normalized)
    if row_index < 0 or row_index >= len(rows):
        raise HTTPException(status_code=400, detail="row_index 超出表格范围")

    column_count = len(columns)
    row = _normalize_sheet_row(rows[row_index], column_count)
    type_index = _find_attendance_column_index(columns, "course_type")
    name_index = _find_attendance_column_index(columns, "course_name")
    online_sheet_index = _find_attendance_column_index(columns, "online_sheet")
    start_date_index = _find_attendance_column_index(columns, "start_date")

    if online_sheet_index is None:
        return NoteSheetAttendanceCourseScriptStatusItem(row_index=row_index, reason="当前表缺少在线考勤表字段")

    course_type = _normalize_sheet_text(row[type_index]) if type_index is not None else ""
    course_name = _normalize_sheet_text(row[name_index]) if name_index is not None else ""
    online_sheet = _normalize_sheet_text(row[online_sheet_index])
    url = _get_document_cell_link_url(normalized, row_index, online_sheet_index)

    status = NoteSheetAttendanceCourseScriptStatusItem(
        row_index=row_index,
        course_type=course_type,
        course_name=course_name,
        online_sheet=online_sheet,
        url=url,
    )
    if not course_type:
        status.reason = "课程类型为空"
        return status
    if not online_sheet:
        status.reason = "在线考勤表为空"
        return status
    if not url:
        status.reason = "在线考勤表没有超链接"
        return status

    target_stem = _standardize_attendance_course_script_stem(online_sheet)
    parsed_target_stem = _parse_attendance_course_script_stem(target_stem)
    target_date = parsed_target_stem[0] if parsed_target_stem is not None else None

    if parsed_target_stem is None:
        text_date, text_body = _parse_attendance_course_text_date(online_sheet)
        target_date = text_date
        if target_date is None and start_date_index is not None:
            target_date = _extract_attendance_row_start_date(
                row,
                row_index=row_index,
                columns=columns,
                rows=rows,
                start_date_index=start_date_index,
            )
        if target_date is None:
            status.reason = "无法识别课程日期"
            return status

        script_body = _sanitize_attendance_course_script_body(text_body or course_name)
        if not script_body:
            status.reason = "无法识别脚本名称"
            return status

        target_stem = f"{_format_attendance_course_script_prefix(target_date)}{script_body}"

    status.target_stem = target_stem
    status.target_filename = f"{target_stem}.py"

    existing = _find_attendance_course_script_by_stem(target_stem)
    if existing is not None:
        status.exists = True
        status.existing_path = str(existing)
        status.reason = "脚本已存在"
        return status

    source = _find_attendance_course_script_template(
        course_type,
        target_date=target_date,
        target_stem=target_stem,
        script_files=script_files,
    )
    if source is None:
        status.reason = "没有找到同类型脚本模板"
        return status

    status.can_generate = True
    return status


def _load_attendance_course_link_provider():
    for path in (ATTENDANCE_XLPROJECT_SRC_DIR, ATTENDANCE_KQ5034_REPO_DIR):
        path_text = str(path)
        if path.exists() and path_text not in sys.path:
            sys.path.insert(0, path_text)

    try:
        from kqmain import 获取课程链接  # type: ignore

        return 获取课程链接
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"无法加载考勤链接查询工具：{exc}") from exc


def _load_attendance_kqdb_provider():
    for path in (ATTENDANCE_KQ5034_REPO_DIR.parent, ATTENDANCE_XLPROJECT_SRC_DIR):
        path_text = str(path)
        if path.exists() and path_text not in sys.path:
            sys.path.insert(0, path_text)

    try:
        import xlproject.loadenv  # noqa: F401
        from kq5034.db import get_kqdb  # type: ignore

        return get_kqdb
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"无法加载考勤数据库配置：{exc}") from exc


def _resolve_attendance_course_lookup_name(
    document_json: dict[str, Any],
    *,
    row: list[Any],
    row_index: int,
    columns: list[Any],
) -> str:
    name_index = _find_attendance_column_index(columns, "course_name")
    online_sheet_index = _find_attendance_column_index(columns, "online_sheet")
    start_date_index = _find_attendance_column_index(columns, "start_date")

    course_name = _normalize_sheet_text(row[name_index]) if name_index is not None else ""
    online_sheet = _normalize_sheet_text(row[online_sheet_index]) if online_sheet_index is not None else ""

    standardized_stem = _standardize_attendance_course_script_stem(online_sheet)
    if _parse_attendance_course_script_stem(standardized_stem) is not None:
        return standardized_stem

    text_date, text_body = _parse_attendance_course_text_date(online_sheet)
    target_date = text_date
    if target_date is None and start_date_index is not None:
        rows = _extract_document_rows(document_json)
        target_date = _extract_attendance_row_start_date(
            row,
            row_index=row_index,
            columns=columns,
            rows=rows,
            start_date_index=start_date_index,
        )
    body = _sanitize_attendance_course_script_body(text_body or course_name)
    if target_date is not None and body:
        return f"{_format_attendance_course_script_prefix(target_date)}{body}"
    return _sanitize_attendance_course_script_body(online_sheet or course_name)


def _extract_attendance_link_item_url(item: Any) -> str:
    if isinstance(item, dict):
        return _normalize_sheet_text(item.get("url"))
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return _normalize_sheet_text(item[1])
    return ""


def _format_attendance_link_count_value(total_count: int, linked_count: int) -> str:
    if total_count <= 0:
        return "0"
    return str(total_count) if linked_count >= total_count else f"{linked_count}/{total_count}"


def _query_attendance_link_count(
    field_key: Literal["lesson_links", "clockin_links"],
    lookup_name: str,
) -> tuple[int, int]:
    try:
        xldb = _load_attendance_kqdb_provider()()
        if field_key == "lesson_links":
            records = xldb.exec2dict(
                "SELECT lesson_id2 AS url FROM lesson_table WHERE lesson_name LIKE %s ORDER BY lesson_id",
                [f"{lookup_name}-%"],
            )
        else:
            records = xldb.exec2dict(
                "SELECT url FROM clockin_table WHERE name LIKE %s ORDER BY clockin_id",
                [f"{lookup_name}-%"],
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"查询考勤链接失败：{exc}") from exc

    items = list(records or [])
    total_count = len(items)
    linked_count = sum(1 for item in items if _extract_attendance_link_item_url(item))
    return total_count, linked_count


def _update_attendance_link_counts(
    document_json: dict[str, Any],
    *,
    field_key: Literal["lesson_links", "clockin_links"],
    row_index: int | None = None,
) -> tuple[dict[str, Any], list[NoteSheetAttendanceLinkCountUpdateItem], list[NoteSheetAttendanceLinkCountUpdateItem]]:
    normalized = _ensure_attendance_link_count_columns(document_json)
    columns = _normalize_document_columns(normalized)
    rows = _extract_document_rows(normalized)
    target_column_index = _find_column_index(columns, ATTENDANCE_FIELD_BINDINGS[field_key][0])
    if target_column_index is None:
        raise HTTPException(status_code=400, detail="当前表缺少链接统计字段")

    if row_index is not None and (row_index < 0 or row_index >= len(rows)):
        raise HTTPException(status_code=400, detail="row_index 超出表格范围")

    row_indices = [row_index] if row_index is not None else list(range(len(rows)))
    next_rows = list(rows)
    updated: list[NoteSheetAttendanceLinkCountUpdateItem] = []
    skipped: list[NoteSheetAttendanceLinkCountUpdateItem] = []

    for source_row_index in row_indices:
        if source_row_index is None:
            continue
        row = _normalize_sheet_row(next_rows[source_row_index], len(columns))
        lookup_name = _resolve_attendance_course_lookup_name(
            normalized,
            row=row,
            row_index=source_row_index,
            columns=columns,
        )
        course_name_index = _find_attendance_column_index(columns, "course_name")
        course_name = _normalize_sheet_text(row[course_name_index]) if course_name_index is not None else ""
        item = NoteSheetAttendanceLinkCountUpdateItem(
            row_index=source_row_index,
            course_name=course_name,
            lookup_name=lookup_name,
        )
        if not lookup_name:
            item.reason = "课程名为空"
            skipped.append(item)
            continue

        total_count, linked_count = _query_attendance_link_count(field_key, lookup_name)
        value = _format_attendance_link_count_value(total_count, linked_count)
        item.total_count = total_count
        item.linked_count = linked_count
        item.value = value
        next_rows[source_row_index] = _set_row_cell_value(
            row,
            columns,
            target_column_index,
            value,
        )
        updated.append(item)

    return _replace_document_data_rows({
        **normalized,
        "columns": columns,
    }, next_rows), updated, skipped


def _list_attendance_course_script_statuses(document_json: dict[str, Any]) -> list[NoteSheetAttendanceCourseScriptStatusItem]:
    normalized = _normalize_document_json(document_json)
    rows = _extract_document_rows(normalized)
    script_files = _iter_attendance_course_script_files()
    statuses: list[NoteSheetAttendanceCourseScriptStatusItem] = []
    for row_index in range(len(rows)):
        status = _build_attendance_course_script_status(
            normalized,
            row_index=row_index,
            script_files=script_files,
        )
        if status.online_sheet or status.url or status.target_filename:
            statuses.append(status)
    return statuses


def _replace_attendance_course_script_token(source_text: str, next_token: str) -> str:
    match = ATTENDANCE_COURSE_SCRIPT_INIT_TOKEN_RE.search(source_text)
    if not match:
        raise HTTPException(status_code=400, detail="脚本模板中没有找到在线表格 token 参数")

    old_token = match.group("token")

    def replace(match_obj: re.Match[str]) -> str:
        return f"{match_obj.group(1)}{match_obj.group('quote')}{next_token}{match_obj.group('quote')}"

    next_text = ATTENDANCE_COURSE_SCRIPT_INIT_TOKEN_RE.sub(replace, source_text, count=1)
    if old_token and old_token != next_token:
        next_text = next_text.replace(old_token, next_token)
    return next_text


def _extract_attendance_course_edition(value: Any) -> str:
    match = ATTENDANCE_COURSE_EDITION_RE.search(_normalize_sheet_text(value))
    return match.group("edition") if match else ""


def _replace_attendance_course_script_product_edition(source_text: str, *target_texts: Any) -> str:
    next_edition = next(
        (edition for edition in (_extract_attendance_course_edition(text) for text in target_texts) if edition),
        "",
    )
    if not next_edition:
        return source_text

    def replace(match_obj: re.Match[str]) -> str:
        product_name = match_obj.group("value")
        if not ATTENDANCE_COURSE_EDITION_RE.search(product_name):
            return match_obj.group(0)

        next_product_name = ATTENDANCE_COURSE_EDITION_RE.sub(
            f"第{next_edition}届",
            product_name,
            count=1,
        )
        return f"{match_obj.group('prefix')}{match_obj.group('quote')}{next_product_name}{match_obj.group('quote')}"

    return ATTENDANCE_COURSE_SCRIPT_PRODUCT_NAME_RE.sub(replace, source_text, count=1)


def _generate_attendance_course_script(
    document_json: dict[str, Any],
    *,
    row_index: int,
) -> NoteSheetAttendanceCourseScriptGenerationResponse:
    if not ATTENDANCE_COURSE_SCRIPT_DIR.exists() or not ATTENDANCE_COURSE_SCRIPT_DIR.is_dir():
        raise HTTPException(status_code=400, detail="考勤脚本目录不存在")

    script_files = _iter_attendance_course_script_files()
    status = _build_attendance_course_script_status(
        document_json,
        row_index=row_index,
        script_files=script_files,
    )
    if status.exists:
        raise HTTPException(status_code=409, detail="对应 py 脚本已存在")
    if not status.can_generate or not status.target_stem:
        raise HTTPException(status_code=400, detail=status.reason or "无法生成 py 脚本")

    target_date, _target_body = _parse_attendance_course_script_stem(status.target_stem) or (None, "")
    if target_date is None:
        raise HTTPException(status_code=400, detail="无法识别目标脚本日期")

    source_path = _find_attendance_course_script_template(
        status.course_type,
        target_date=target_date,
        target_stem=status.target_stem,
        script_files=script_files,
    )
    if source_path is None:
        raise HTTPException(status_code=400, detail="没有找到同类型脚本模板")

    next_token = _extract_attendance_course_script_token(status.url)
    if not next_token:
        raise HTTPException(status_code=400, detail="在线考勤表链接不是可识别的 KDocs 链接")

    target_path = ATTENDANCE_COURSE_SCRIPT_DIR / status.target_filename
    if target_path.exists():
        raise HTTPException(status_code=409, detail="对应 py 脚本已存在")

    try:
        source_text = source_path.read_text(encoding="utf-8")
        target_text = _replace_attendance_course_script_token(source_text, next_token)
        target_text = _replace_attendance_course_script_product_edition(
            target_text,
            status.target_stem,
            status.course_name,
            status.online_sheet,
        )
        target_path.write_text(target_text, encoding="utf-8")
        shutil.copystat(source_path, target_path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"写入 py 脚本失败：{exc}") from exc

    status.exists = True
    status.can_generate = False
    status.existing_path = str(target_path)
    status.reason = "脚本已生成"
    return NoteSheetAttendanceCourseScriptGenerationResponse(
        status=status,
        source_filename=source_path.name,
        source_path=str(source_path),
        created_path=str(target_path),
    )


def _is_attendance_script_in_directory(path: Path, directory: Path) -> bool:
    try:
        return path.resolve().parent == directory.resolve()
    except OSError:
        return path.parent == directory


def _organize_attendance_course_scripts(
    document_json: dict[str, Any],
) -> NoteSheetAttendanceCourseScriptOrganizeResponse:
    if not ATTENDANCE_COURSE_SCRIPT_DIR.exists() or not ATTENDANCE_COURSE_SCRIPT_DIR.is_dir():
        raise HTTPException(status_code=400, detail="考勤脚本目录不存在")

    normalized = _normalize_document_json(document_json)
    columns = _normalize_document_columns(normalized)
    rows = _extract_document_rows(normalized)
    completed_index = _find_attendance_column_index(columns, "completed_date")
    if completed_index is None:
        raise HTTPException(status_code=400, detail="当前表缺少考勤实际完成结点字段")

    completed_dir = ATTENDANCE_COURSE_SCRIPT_DIR / "已完结"
    moved: list[NoteSheetAttendanceCourseScriptOrganizeItem] = []
    skipped: list[NoteSheetAttendanceCourseScriptOrganizeItem] = []

    for row_index, raw_row in enumerate(rows):
        row = _normalize_sheet_row(raw_row, len(columns))
        completed = bool(_normalize_sheet_text(row[completed_index]))
        status = _build_attendance_course_script_status(normalized, row_index=row_index)
        if not status.target_filename:
            continue

        item = NoteSheetAttendanceCourseScriptOrganizeItem(
            row_index=row_index,
            course_type=status.course_type,
            online_sheet=status.online_sheet,
            target_filename=status.target_filename,
            completed=completed,
        )
        if not status.exists or not status.existing_path:
            item.reason = "脚本不存在"
            skipped.append(item)
            continue

        source_path = Path(status.existing_path)
        desired_dir = completed_dir if completed else ATTENDANCE_COURSE_SCRIPT_DIR
        target_path = desired_dir / status.target_filename
        item.source_path = str(source_path)
        item.target_path = str(target_path)

        if _is_attendance_script_in_directory(source_path, desired_dir):
            item.reason = "位置已正确"
            skipped.append(item)
            continue
        if not source_path.exists() or not source_path.is_file():
            item.reason = "脚本不存在"
            skipped.append(item)
            continue
        if target_path.exists():
            item.reason = "目标位置已存在"
            skipped.append(item)
            continue

        try:
            desired_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_path), str(target_path))
        except OSError as exc:
            item.reason = f"移动失败：{exc}"
            skipped.append(item)
            continue

        moved.append(item)

    return NoteSheetAttendanceCourseScriptOrganizeResponse(moved=moved, skipped=skipped)


def _natural_sort_key(value: str) -> tuple[tuple[int, Any], ...]:
    normalized = value.casefold()
    parts = NATURAL_SORT_SPLIT_RE.split(normalized)
    key: list[tuple[int, Any]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


def _get_column_config(document_json: dict[str, Any], column_index: int, columns: list[Any]) -> dict[str, Any]:
    column_configs = document_json.get("column_configs")
    if not isinstance(column_configs, dict) or column_index < 0 or column_index >= len(columns):
        return {}
    config = column_configs.get(str(columns[column_index]))
    return dict(config) if isinstance(config, dict) else {}


def _get_column_value_type(config: dict[str, Any]) -> str:
    value_type = config.get("value_type")
    if value_type in {"multi_text", "number", "percent", "date", "phone"}:
        return str(value_type)
    return "text"


def _extract_sort_cell_value(
    row: Any,
    *,
    row_index: int,
    column_index: int,
    columns: list[Any],
    rows: list[Any],
) -> Any:
    raw_value = _extract_row_cell_value(row, column_index, columns)
    return _evaluate_formula_sort_value(
        raw_value,
        row_index=row_index,
        column_index=column_index,
        rows=rows,
        columns=columns,
    )


def _sort_value_is_empty(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _build_sort_key(value: Any, value_type: str) -> tuple[int, Any]:
    if value_type == "date":
        date_value = _parse_date_sort_value(value)
        if date_value is not None:
            return 0, date_value
    if value_type == "number":
        number_value = _parse_number_sort_value(value)
        if number_value is not None:
            return 0, number_value
    if value_type == "percent":
        percent_value = _parse_percent_sort_value(value)
        if percent_value is not None:
            return 0, percent_value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return 0, float(value)
    return 1, _natural_sort_key("" if value is None else str(value).strip())


def _parse_cell_meta_key(key: Any) -> tuple[int, int] | None:
    if not isinstance(key, str):
        return None
    row_text, separator, column_text = key.partition(":")
    if separator != ":" or not row_text.isdigit() or not column_text.isdigit():
        return None
    return int(row_text), int(column_text)


def _remap_cell_meta_rows(cell_meta: Any, row_index_map: dict[int, int], *, row_offset: int = 0) -> dict[str, Any]:
    if not isinstance(cell_meta, dict):
        return {}

    remapped: dict[str, Any] = {}
    for key, value in cell_meta.items():
        position = _parse_cell_meta_key(key)
        if position is None:
            remapped[str(key)] = value
            continue

        row_index, column_index = position
        lookup_row_index = row_index - row_offset
        next_row_index = row_index_map.get(lookup_row_index)
        if next_row_index is None:
            remapped[str(key)] = value
        else:
            remapped[f"{next_row_index + row_offset}:{column_index}"] = value
    return remapped


def _sort_sheet_document_rows(
    document_json: dict[str, Any],
    *,
    column_index: int,
    direction: Literal["asc", "desc"],
) -> dict[str, Any]:
    normalized = _normalize_document_json(document_json)
    all_rows = _extract_document_rows(normalized)
    columns = list(normalized.get("columns") or [])
    data_start_row = _normalize_document_data_start_row(normalized)
    merged_cells = normalized.get("merged_cells")
    if isinstance(merged_cells, list):
        for cell in merged_cells:
            if not isinstance(cell, dict):
                continue
            if int(cell.get("row") or 0) >= data_start_row and int(cell.get("rowspan") or 1) > 1:
                raise HTTPException(status_code=400, detail="数据区存在跨行合并，不能排序")
    column_config = _get_column_config(normalized, column_index, columns)
    column_value_type = _get_column_value_type(column_config)

    sortable_rows: list[tuple[int, Any]] = []
    empty_value_rows: list[tuple[int, Any]] = []
    blank_rows: list[tuple[int, Any]] = []

    for row_index, row in enumerate(all_rows):
        if isinstance(row, list):
            row_values = row
        elif isinstance(row, dict):
            row_values = list(row.values())
        else:
            row_values = []
        if not any(str(cell or "").strip() for cell in row_values):
            blank_rows.append((row_index, row))
            continue

        cell_value = _extract_sort_cell_value(
            row,
            row_index=row_index,
            column_index=column_index,
            columns=columns,
            rows=all_rows,
        )
        if _sort_value_is_empty(cell_value):
            empty_value_rows.append((row_index, row))
        else:
            sortable_rows.append((row_index, row))

    sorted_rows = sorted(
        sortable_rows,
        key=lambda item: _build_sort_key(
            _extract_sort_cell_value(
                item[1],
                row_index=item[0],
                column_index=column_index,
                columns=columns,
                rows=all_rows,
            ),
            column_value_type,
        ),
        reverse=direction == "desc",
    )
    ordered_rows = [
        *sorted_rows,
        *empty_value_rows,
        *blank_rows,
    ]
    row_index_map = {
        source_index: target_index
        for target_index, (source_index, _row) in enumerate(ordered_rows)
    }

    next_rows = [
        _remap_row_formula_cell_references(
            row,
            columns=columns,
            row_index_map=row_index_map,
        )
        for _source_index, row in ordered_rows
    ]
    next_document = _replace_document_data_rows(normalized, next_rows)
    if isinstance(normalized.get("cell_meta"), dict):
        row_offset = _normalize_document_data_start_row(normalized) if _extract_document_grid_rows(normalized) else 0
        next_document["cell_meta"] = _remap_cell_meta_rows(
            normalized.get("cell_meta"),
            row_index_map,
            row_offset=row_offset,
        )
    return next_document


def _get_next_sheet_numeric_id(session: Session) -> int:
    current_max = session.exec(
        select(SheetDocument.numeric_id)
        .where(SheetDocument.numeric_id.is_not(None))
        .order_by(SheetDocument.numeric_id.desc())
    ).first()
    return max(int(current_max or 0), 0) + 1


def _get_next_workbook_numeric_id(session: Session) -> int:
    current_max = session.exec(
        select(WorkbookDocument.numeric_id)
        .where(WorkbookDocument.numeric_id.is_not(None))
        .order_by(WorkbookDocument.numeric_id.desc())
    ).first()
    return max(int(current_max or 0), 0) + 1


def _require_sheet_numeric_id(document: SheetDocument) -> int:
    numeric_id = int(document.numeric_id or 0)
    if numeric_id <= 0:
        raise HTTPException(status_code=500, detail="表格编号缺失")
    return numeric_id


def _require_workbook_numeric_id(workbook: WorkbookDocument) -> int:
    numeric_id = int(workbook.numeric_id or 0)
    if numeric_id <= 0:
        raise HTTPException(status_code=500, detail="工作簿编号缺失")
    return numeric_id


def _normalize_resource_role(value: Any) -> Literal["deny", "viewer", "editor", "manager"] | None:
    role = str(value or "").strip()
    if role in RESOURCE_ACCESS_ROLE_RANK:
        return role  # type: ignore[return-value]
    return None


def _build_resource_access(role: str | None) -> NoteSheetResourceAccess:
    normalized_role = _normalize_resource_role(role) if role else None
    if normalized_role is None:
        return NoteSheetResourceAccess(role="none")

    rank = RESOURCE_ACCESS_ROLE_RANK[normalized_role]
    return NoteSheetResourceAccess(
        role=normalized_role,
        capabilities=NoteSheetAccessCapabilities(
            can_read=rank >= RESOURCE_ACCESS_ROLE_RANK["viewer"],
            can_use_local_view=rank >= RESOURCE_ACCESS_ROLE_RANK["viewer"],
            can_edit_data=rank >= RESOURCE_ACCESS_ROLE_RANK["editor"],
            can_edit_config=rank >= RESOURCE_ACCESS_ROLE_RANK["editor"],
            can_run_sheet_actions=rank >= RESOURCE_ACCESS_ROLE_RANK["editor"],
            can_manage_access=rank >= RESOURCE_ACCESS_ROLE_RANK["manager"],
        ),
    )


def _is_attendance_wjx_data_sheet(document: SheetDocument) -> bool:
    return (
        document.scope == "notes"
        and document.owner_type == ATTENDANCE_WJX_DATA_OWNER_TYPE
        and document.owner_key == ATTENDANCE_WJX_DATA_OWNER_KEY
        and document.sheet_key == ATTENDANCE_WJX_DATA_SHEET_KEY
    )


def _apply_sheet_specific_access_capabilities(
    access: NoteSheetResourceAccess,
    document: SheetDocument,
) -> NoteSheetResourceAccess:
    if (
        access.capabilities.can_read
        and not access.capabilities.can_edit_data
        and _is_attendance_wjx_data_sheet(document)
    ):
        access.capabilities.editable_data_columns = list(ATTENDANCE_WJX_DATA_PUBLIC_EDITABLE_COLUMN_INDEXES)
    return access


def _resource_role_allows(role: str | None, required_role: Literal["viewer", "editor", "manager"]) -> bool:
    normalized_role = _normalize_resource_role(role)
    if normalized_role is None:
        return False
    return RESOURCE_ACCESS_ROLE_RANK[normalized_role] >= RESOURCE_ACCESS_ROLE_RANK[required_role]


def _highest_resource_role(left: str | None, right: str | None) -> str | None:
    left_role = _normalize_resource_role(left) if left else None
    right_role = _normalize_resource_role(right) if right else None
    if left_role is None:
        return right_role
    if right_role is None:
        return left_role
    return left_role if RESOURCE_ACCESS_ROLE_RANK[left_role] >= RESOURCE_ACCESS_ROLE_RANK[right_role] else right_role


def _build_resource_subject_key(subject_type: str, subject_user_id: int | None = None) -> str:
    if subject_type == RESOURCE_ACCESS_SUBJECT_ANONYMOUS:
        return RESOURCE_ACCESS_SUBJECT_ANONYMOUS
    if subject_type == RESOURCE_ACCESS_SUBJECT_USER and subject_user_id is not None:
        return f"user:{subject_user_id}"
    raise HTTPException(status_code=400, detail="非法权限主体")


def _current_user_subject_keys(current_user: User | None) -> list[str]:
    if current_user is None:
        return [RESOURCE_ACCESS_SUBJECT_ANONYMOUS]
    return [
        _build_resource_subject_key(RESOURCE_ACCESS_SUBJECT_USER, current_user.id),
        RESOURCE_ACCESS_SUBJECT_ANONYMOUS,
    ]


def _fetch_resource_grants(session: Session, resource_type: str, resource_id: str) -> list[ResourceAccessGrant]:
    return list(session.exec(
        select(ResourceAccessGrant)
        .where(ResourceAccessGrant.resource_type == resource_type)
        .where(ResourceAccessGrant.resource_id == resource_id)
    ).all())


def _resolve_subject_grant_role(
    grants: list[ResourceAccessGrant],
    current_user: User | None,
) -> str | None:
    grant_map = {grant.subject_key: grant for grant in grants}
    for subject_key in _current_user_subject_keys(current_user):
        grant = grant_map.get(subject_key)
        if grant is not None:
            return _normalize_resource_role(grant.role)
    return None


def _get_workbooks_for_sheet(session: Session, document: SheetDocument) -> list[WorkbookDocument]:
    links = session.exec(
        select(WorkbookSheetLink).where(WorkbookSheetLink.sheet_id == document.id)
    ).all()
    workbook_ids = [link.workbook_id for link in links]
    if not workbook_ids:
        return []
    return list(session.exec(
        select(WorkbookDocument).where(WorkbookDocument.id.in_(workbook_ids))
    ).all())


def _get_workbook_by_numeric_id_or_404(session: Session, workbook_id: int) -> WorkbookDocument:
    workbook = session.exec(
        select(WorkbookDocument).where(WorkbookDocument.numeric_id == workbook_id)
    ).first()
    if workbook is None:
        raise HTTPException(status_code=404, detail="工作簿不存在")
    return workbook


def _get_sheet_by_numeric_id_or_404(session: Session, sheet_id: int) -> SheetDocument:
    document = session.exec(
        select(SheetDocument).where(SheetDocument.numeric_id == sheet_id)
    ).first()
    if document is None or document.scope != "notes":
        raise HTTPException(status_code=404, detail="表格不存在")
    return document


def _is_superuser_or_owner(current_user: User | None, owner_user_id: int | None) -> bool:
    return bool(
        current_user is not None
        and (current_user.is_superuser or owner_user_id == current_user.id)
    )


def _resolve_workbook_resource_access(
    session: Session,
    workbook: WorkbookDocument,
    current_user: User | None,
) -> NoteSheetResourceAccess:
    if _is_superuser_or_owner(current_user, workbook.owner_user_id):
        return _build_resource_access("manager")

    role = _resolve_subject_grant_role(
        _fetch_resource_grants(session, RESOURCE_TYPE_WORKBOOK, workbook.id),
        current_user,
    )
    return _build_resource_access(role)


def _resolve_sheet_resource_access(
    session: Session,
    document: SheetDocument,
    current_user: User | None,
    *,
    workbook: WorkbookDocument | None = None,
) -> NoteSheetResourceAccess:
    if _is_superuser_or_owner(current_user, document.owner_user_id):
        return _apply_sheet_specific_access_capabilities(_build_resource_access("manager"), document)
    if workbook is not None and _is_superuser_or_owner(current_user, workbook.owner_user_id):
        return _apply_sheet_specific_access_capabilities(_build_resource_access("manager"), document)
    if workbook is None and any(
        _is_superuser_or_owner(current_user, item.owner_user_id)
        for item in _get_workbooks_for_sheet(session, document)
    ):
        return _apply_sheet_specific_access_capabilities(_build_resource_access("manager"), document)

    direct_role = _resolve_subject_grant_role(
        _fetch_resource_grants(session, RESOURCE_TYPE_SHEET, document.id),
        current_user,
    )
    if direct_role is not None:
        return _apply_sheet_specific_access_capabilities(_build_resource_access(direct_role), document)

    if workbook is not None:
        return _apply_sheet_specific_access_capabilities(
            _resolve_workbook_resource_access(session, workbook, current_user),
            document,
        )

    inherited_role: str | None = None
    for candidate in _get_workbooks_for_sheet(session, document):
        candidate_access = _resolve_workbook_resource_access(session, candidate, current_user)
        inherited_role = _highest_resource_role(inherited_role, candidate_access.role)
    return _apply_sheet_specific_access_capabilities(_build_resource_access(inherited_role), document)


def _require_resource_access(
    access: NoteSheetResourceAccess,
    required_role: Literal["viewer", "editor", "manager"],
) -> None:
    if not _resource_role_allows(access.role, required_role):
        raise HTTPException(status_code=403, detail="没有该资源权限")


def _require_note_sheets_feature(session: Session, current_user: User) -> None:
    ensure_feature_access(session, feature_key="notes.sheets", current_user=current_user)


def _get_note_sheet_or_404(
    session: Session,
    current_user: User | None,
    sheet_id: int,
    *,
    required_role: Literal["viewer", "editor", "manager"] = "viewer",
    workbook_id: int | None = None,
) -> tuple[SheetDocument, NoteSheetResourceAccess, WorkbookDocument | None]:
    document = _get_sheet_by_numeric_id_or_404(session, sheet_id)
    workbook = _get_workbook_by_numeric_id_or_404(session, workbook_id) if workbook_id is not None else None
    if workbook is not None:
        link = session.exec(
            select(WorkbookSheetLink)
            .where(WorkbookSheetLink.workbook_id == workbook.id)
            .where(WorkbookSheetLink.sheet_id == document.id)
        ).first()
        if link is None:
            raise HTTPException(status_code=404, detail="工作簿中不存在该工作表")

    access = _resolve_sheet_resource_access(session, document, current_user, workbook=workbook)
    _require_resource_access(access, required_role)
    return document, access, workbook


def _get_workbook_or_404(
    session: Session,
    current_user: User | None,
    workbook_id: int,
    *,
    required_role: Literal["viewer", "editor", "manager"] = "viewer",
) -> tuple[WorkbookDocument, NoteSheetResourceAccess]:
    workbook = _get_workbook_by_numeric_id_or_404(session, workbook_id)
    access = _resolve_workbook_resource_access(session, workbook, current_user)
    _require_resource_access(access, required_role)
    return workbook, access


def _list_workbook_refs_for_sheet_ids(session: Session, sheet_ids: list[str]) -> dict[str, list[WorkbookRefItem]]:
    if not sheet_ids:
        return {}

    links = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.sheet_id.in_(sheet_ids))
        .order_by(WorkbookSheetLink.order_index, WorkbookSheetLink.created_at)
    ).all()
    if not links:
        return {}

    workbook_ids = sorted({link.workbook_id for link in links})
    workbooks = session.exec(
        select(WorkbookDocument)
        .where(WorkbookDocument.id.in_(workbook_ids))
    ).all()
    workbook_map = {workbook.id: workbook for workbook in workbooks}

    result: dict[str, list[WorkbookRefItem]] = {sheet_id: [] for sheet_id in sheet_ids}
    for link in links:
        workbook = workbook_map.get(link.workbook_id)
        if workbook is None:
            continue
        result.setdefault(link.sheet_id, []).append(
            WorkbookRefItem(id=_require_workbook_numeric_id(workbook), title=workbook.title),
        )
    return result


def _serialize_sheet_summary(
    document: SheetDocument,
    *,
    workbook_items: list[WorkbookRefItem] | None = None,
    access: NoteSheetResourceAccess | None = None,
) -> dict[str, Any]:
    return {
        "id": _require_sheet_numeric_id(document),
        "title": document.title,
        "engine": document.engine,
        "scope": document.scope,
        "owner_user_id": document.owner_user_id,
        "created_by_user_id": document.created_by_user_id,
        "updated_by_user_id": document.updated_by_user_id,
        "created_at": float(document.created_at or 0.0),
        "updated_at": float(document.updated_at or 0.0),
        "workbook_items": workbook_items or [],
        "access": access,
    }


def _serialize_sheet_detail(
    document: SheetDocument,
    *,
    workbook_items: list[WorkbookRefItem] | None = None,
    document_json: dict[str, Any] | None = None,
    pagination: NoteSheetPaginationResponse | None = None,
    access: NoteSheetResourceAccess | None = None,
) -> dict[str, Any]:
    return {
        **_serialize_sheet_summary(document, workbook_items=workbook_items, access=access),
        "owner_type": document.owner_type,
        "owner_key": document.owner_key,
        "sheet_key": document.sheet_key,
        "version": int(document.version or 1),
        "document_json": dict(document_json if document_json is not None else (document.document_json or {})),
        "pagination": pagination,
    }


def _serialize_workbook_summary(
    workbook: WorkbookDocument,
    *,
    sheet_count: int = 0,
    access: NoteSheetResourceAccess | None = None,
) -> dict[str, Any]:
    return {
        "id": _require_workbook_numeric_id(workbook),
        "title": workbook.title,
        "owner_user_id": workbook.owner_user_id,
        "created_by_user_id": workbook.created_by_user_id,
        "updated_by_user_id": workbook.updated_by_user_id,
        "created_at": float(workbook.created_at or 0.0),
        "updated_at": float(workbook.updated_at or 0.0),
        "sheet_count": sheet_count,
        "access": access,
    }


def _serialize_workbook_detail(
    session: Session,
    workbook: WorkbookDocument,
    *,
    current_user: User | None = None,
    access: NoteSheetResourceAccess | None = None,
) -> dict[str, Any]:
    links = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == workbook.id)
        .order_by(WorkbookSheetLink.order_index, WorkbookSheetLink.created_at)
    ).all()
    sheet_ids = [link.sheet_id for link in links]
    sheets = session.exec(
        select(SheetDocument)
        .where(SheetDocument.id.in_(sheet_ids) if sheet_ids else False)
    ).all() if sheet_ids else []
    sheet_map = {sheet.id: sheet for sheet in sheets}
    sheet_workbook_refs = _list_workbook_refs_for_sheet_ids(session, sheet_ids)
    ordered_sheets: list[dict[str, Any]] = []
    for link in links:
        sheet = sheet_map.get(link.sheet_id)
        if sheet is None:
            continue
        sheet_access = _resolve_sheet_resource_access(session, sheet, current_user, workbook=workbook)
        if not sheet_access.capabilities.can_read:
            continue
        ordered_sheets.append(_serialize_sheet_summary(
            sheet,
            workbook_items=sheet_workbook_refs.get(link.sheet_id, []),
            access=sheet_access,
        ))
    return {
        **_serialize_workbook_summary(workbook, sheet_count=len(ordered_sheets), access=access),
        "sheets": ordered_sheets,
    }


def _serialize_resource_access_grants(
    session: Session,
    *,
    resource_type: str,
    resource_id: str,
) -> list[NoteSheetResourceAccessGrantItem]:
    grants = _fetch_resource_grants(session, resource_type, resource_id)
    user_ids = sorted({
        int(grant.subject_user_id)
        for grant in grants
        if grant.subject_user_id is not None
    })
    users = session.exec(select(User).where(User.id.in_(user_ids))).all() if user_ids else []
    user_map = {user.id: user for user in users}

    result: list[NoteSheetResourceAccessGrantItem] = []
    for grant in sorted(grants, key=lambda item: (item.subject_type, item.subject_key)):
        role = _normalize_resource_role(grant.role)
        if role is None:
            continue
        user = user_map.get(grant.subject_user_id) if grant.subject_user_id is not None else None
        result.append(NoteSheetResourceAccessGrantItem(
            subject_type=grant.subject_type,  # type: ignore[arg-type]
            subject_key=grant.subject_key,
            subject_user_id=grant.subject_user_id,
            username=user.username if user is not None else "",
            nickname=user.nickname if user is not None else "",
            role=role,
        ))
    return result


def _resolve_resource_access_update_subject(
    session: Session,
    item: NoteSheetResourceAccessGrantUpdate,
) -> tuple[str, str, int | None]:
    if item.subject_type == RESOURCE_ACCESS_SUBJECT_ANONYMOUS:
        return RESOURCE_ACCESS_SUBJECT_ANONYMOUS, RESOURCE_ACCESS_SUBJECT_ANONYMOUS, None

    user: User | None = None
    if item.subject_user_id is not None:
        user = session.get(User, item.subject_user_id)
    if user is None and item.username:
        user = session.exec(select(User).where(User.username == item.username.strip())).first()
    if user is None:
        raise HTTPException(status_code=400, detail="用户不存在")
    return RESOURCE_ACCESS_SUBJECT_USER, _build_resource_subject_key(RESOURCE_ACCESS_SUBJECT_USER, user.id), user.id


def _save_resource_access_grants(
    session: Session,
    *,
    resource_type: str,
    resource_id: str,
    payload: NoteSheetResourceAccessUpdateRequest,
    current_user: User,
) -> None:
    now = time.time()
    existing = {
        grant.subject_key: grant
        for grant in _fetch_resource_grants(session, resource_type, resource_id)
    }
    desired_subject_keys: set[str] = set()
    normalized_items: dict[str, tuple[str, int | None, str]] = {}

    for item in payload.grants:
        subject_type, subject_key, subject_user_id = _resolve_resource_access_update_subject(session, item)
        desired_subject_keys.add(subject_key)
        if item.role == "none":
            continue
        role = _normalize_resource_role(item.role)
        if role is None:
            raise HTTPException(status_code=400, detail="非法权限角色")
        if subject_type == RESOURCE_ACCESS_SUBJECT_ANONYMOUS and role in {"editor", "manager"}:
            raise HTTPException(status_code=400, detail="游客不能拥有编辑权限")
        normalized_items[subject_key] = (subject_type, subject_user_id, role)

    for subject_key, grant in list(existing.items()):
        if subject_key not in normalized_items:
            session.delete(grant)

    for subject_key, (subject_type, subject_user_id, role) in normalized_items.items():
        grant = existing.get(subject_key)
        if grant is None:
            grant = ResourceAccessGrant(
                resource_type=resource_type,
                resource_id=resource_id,
                subject_key=subject_key,
                subject_type=subject_type,
                subject_user_id=subject_user_id,
                role=role,
                created_at=now,
                updated_at=now,
                updated_by_user_id=current_user.id,
            )
        else:
            grant.subject_type = subject_type
            grant.subject_user_id = subject_user_id
            grant.role = role
            grant.updated_at = now
            grant.updated_by_user_id = current_user.id
        session.add(grant)


def _build_resource_access_response(
    session: Session,
    *,
    resource_type: Literal["workbook", "sheet"],
    numeric_id: int,
    resource_id: str,
    access: NoteSheetResourceAccess,
) -> NoteSheetResourceAccessResponse:
    return NoteSheetResourceAccessResponse(
        resource_type=resource_type,
        resource_id=numeric_id,
        access=access,
        grants=_serialize_resource_access_grants(session, resource_type=resource_type, resource_id=resource_id),
    )


def _clone_sheet_document_json(
    document_json: dict[str, Any],
    *,
    mode: Literal["template", "duplicate"],
) -> dict[str, Any]:
    cloned = deepcopy(_normalize_document_json(document_json))
    if mode == "template":
        cloned["rows"] = []
    return cloned


def _get_next_workbook_link_order(session: Session, workbook_id: str) -> int:
    links = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == workbook_id)
        .order_by(WorkbookSheetLink.order_index.desc(), WorkbookSheetLink.created_at.desc())
    ).all()
    if not links:
        return 10
    return max(int(links[0].order_index or 0), 0) + 10


@router.get("/sheets", response_model=list[NoteSheetSummaryResponse])
def list_note_sheets(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    _require_note_sheets_feature(session, current_user)
    documents = session.exec(
        select(SheetDocument)
        .where(SheetDocument.scope == "notes")
        .where(SheetDocument.owner_user_id == current_user.id)
        .order_by(SheetDocument.updated_at.desc(), SheetDocument.created_at.desc())
    ).all()
    workbook_refs = _list_workbook_refs_for_sheet_ids(session, [document.id for document in documents])
    return [
        NoteSheetSummaryResponse.model_validate(
            _serialize_sheet_summary(
                document,
                workbook_items=workbook_refs.get(document.id, []),
                access=_resolve_sheet_resource_access(session, document, current_user),
            ),
        )
        for document in documents
    ]


@router.post("/sheets", response_model=NoteSheetDetailResponse)
def create_note_sheet(
    payload: NoteSheetCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    _require_note_sheets_feature(session, current_user)
    workbook: WorkbookDocument | None = None
    if payload.workbook_id is not None:
        workbook, _workbook_access = _get_workbook_or_404(
            session,
            current_user,
            payload.workbook_id,
            required_role="editor",
        )

    now = time.time()
    document = SheetDocument(
        numeric_id=_get_next_sheet_numeric_id(session),
        scope="notes",
        owner_type="user",
        owner_key=str(current_user.id),
        sheet_key="pending",
        title=_normalize_title(payload.title, default_value="未命名表格"),
        engine="handsontable",
        document_json=_normalize_document_json(payload.document_json),
        version=1,
        owner_user_id=current_user.id,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(document)
    session.flush()
    document.sheet_key = str(document.numeric_id or document.id)
    session.add(document)
    if workbook is not None:
        session.add(
            WorkbookSheetLink(
                workbook_id=workbook.id,
                sheet_id=document.id,
                order_index=_get_next_workbook_link_order(session, workbook.id),
                created_at=now,
            ),
        )
        workbook.updated_by_user_id = current_user.id
        workbook.updated_at = now
        session.add(workbook)
    session.commit()
    session.refresh(document)
    workbook_items = _list_workbook_refs_for_sheet_ids(session, [document.id]).get(document.id, [])
    return NoteSheetDetailResponse.model_validate(
        _serialize_sheet_detail(
            document,
            workbook_items=workbook_items,
            access=_resolve_sheet_resource_access(session, document, current_user, workbook=workbook),
        ),
    )


@router.get("/sheets/{sheet_id}", response_model=NoteSheetDetailResponse)
def get_note_sheet(
    sheet_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=MAX_NOTE_SHEET_PAGE_SIZE),
    paginate: bool | None = Query(default=None),
    workbook_id: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    document, access, _workbook = _get_note_sheet_or_404(
        session,
        current_user,
        sheet_id,
        required_role="viewer",
        workbook_id=workbook_id,
    )
    _sync_attendance_questionnaire_sheet_document(session, document)
    workbook_items = _list_workbook_refs_for_sheet_ids(session, [document.id]).get(document.id, [])
    full_document = dict(document.document_json or {})
    document_paginate_enabled, document_page_size = _get_document_pagination_settings(full_document)
    effective_paginate = document_paginate_enabled if paginate is None else paginate

    if effective_paginate:
        page_document, pagination = _build_paged_document(
            full_document,
            page=page,
            page_size=page_size if page_size is not None else document_page_size,
        )
    else:
        page_document = _normalize_document_json(full_document)
        pagination = None

    return NoteSheetDetailResponse.model_validate(
        _serialize_sheet_detail(
            document,
            workbook_items=workbook_items,
            document_json=page_document,
            pagination=pagination,
            access=access,
        ),
    )


@router.get("/sheets/{sheet_id}/access", response_model=NoteSheetResourceAccessResponse)
def get_sheet_access(
    sheet_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    document, access, _workbook = _get_note_sheet_or_404(session, current_user, sheet_id, required_role="manager")
    return _build_resource_access_response(
        session,
        resource_type=RESOURCE_TYPE_SHEET,
        numeric_id=_require_sheet_numeric_id(document),
        resource_id=document.id,
        access=access,
    )


@router.put("/sheets/{sheet_id}/access", response_model=NoteSheetResourceAccessResponse)
def update_sheet_access(
    sheet_id: int,
    payload: NoteSheetResourceAccessUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    document, _access, _workbook = _get_note_sheet_or_404(session, current_user, sheet_id, required_role="manager")
    _save_resource_access_grants(
        session,
        resource_type=RESOURCE_TYPE_SHEET,
        resource_id=document.id,
        payload=payload,
        current_user=current_user,
    )
    session.commit()
    access = _resolve_sheet_resource_access(session, document, current_user)
    return _build_resource_access_response(
        session,
        resource_type=RESOURCE_TYPE_SHEET,
        numeric_id=_require_sheet_numeric_id(document),
        resource_id=document.id,
        access=access,
    )


@router.put("/sheets/{sheet_id}", response_model=NoteSheetDetailResponse)
def update_note_sheet(
    sheet_id: int,
    payload: NoteSheetUpdateRequest,
    workbook_id: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    document, access, _workbook = _get_note_sheet_or_404(
        session,
        current_user,
        sheet_id,
        required_role="viewer",
        workbook_id=workbook_id,
    )
    editable_columns = list(access.capabilities.editable_data_columns)
    if not access.capabilities.can_edit_data and not editable_columns:
        raise HTTPException(status_code=403, detail="没有该资源权限")
    if access.capabilities.can_edit_data and current_user is None:
        raise HTTPException(status_code=403, detail="没有该资源权限")
    if not access.capabilities.can_edit_config and payload.title is not None:
        next_payload_title = _normalize_title(payload.title, default_value=document.title or "未命名表格")
        if next_payload_title != (document.title or "未命名表格"):
            raise HTTPException(status_code=403, detail="没有该资源权限")

    next_title = (
        _normalize_title(payload.title, default_value=document.title or "未命名表格")
        if payload.title is not None and access.capabilities.can_edit_config
        else document.title
    )
    current_document = dict(document.document_json or {})
    if payload.document_json is None:
        next_document = current_document
    else:
        _reject_default_blank_overwrite(current_document, payload.document_json)
        if access.capabilities.can_edit_data:
            if payload.page_patch is None:
                next_document = _normalize_document_json(payload.document_json)
            else:
                next_document = _merge_paged_document(current_document, payload.document_json, payload.page_patch)
        else:
            incoming_document = (
                _normalize_document_json(payload.document_json)
                if payload.page_patch is None
                else _merge_paged_document(current_document, payload.document_json, payload.page_patch)
            )
            next_document = _apply_restricted_data_column_update(current_document, incoming_document, editable_columns)

    if _is_attendance_questionnaire_data_sheet(document):
        next_document, _links_changed = _sync_attendance_questionnaire_course_links(session, next_document)

    if document.title != next_title or current_document != next_document:
        document.title = next_title
        document.document_json = next_document
        document.version = max(int(document.version or 1), 1) + 1
        document.updated_by_user_id = current_user.id if current_user is not None else None
        document.updated_at = time.time()
        session.add(document)
        session.commit()
        session.refresh(document)

    workbook_items = _list_workbook_refs_for_sheet_ids(session, [document.id]).get(document.id, [])
    response_document = dict(document.document_json or {})
    response_pagination: NoteSheetPaginationResponse | None = None
    response_paginate_enabled, _response_page_size = _get_document_pagination_settings(response_document)
    if payload.document_json is not None and payload.page_patch is not None:
        response_document = _normalize_document_json(payload.document_json)
        if response_paginate_enabled:
            response_pagination = _build_workspace_pagination(
                page_patch=payload.page_patch,
                total_rows=len(_extract_document_rows(dict(document.document_json or {}))),
                current_row_count=len(_extract_document_rows(response_document)),
            )

    return NoteSheetDetailResponse.model_validate(
        _serialize_sheet_detail(
            document,
            workbook_items=workbook_items,
            document_json=response_document,
            pagination=response_pagination,
            access=access,
        ),
    )


@router.post("/sheets/{sheet_id}/sort", response_model=NoteSheetDetailResponse)
def sort_note_sheet(
    sheet_id: int,
    payload: NoteSheetSortRequest,
    workbook_id: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    document, access, _workbook = _get_note_sheet_or_404(
        session,
        current_user,
        sheet_id,
        required_role="editor",
        workbook_id=workbook_id,
    )
    if current_user is None:
        raise HTTPException(status_code=403, detail="没有该资源权限")
    current_document = _normalize_document_json(dict(document.document_json or {}))
    columns = list(current_document.get("columns") or [])
    if payload.column_index >= len(columns):
        raise HTTPException(status_code=400, detail="排序字段不存在")

    next_document = _sort_sheet_document_rows(
        current_document,
        column_index=payload.column_index,
        direction=payload.direction,
    )

    if current_document != next_document:
        document.document_json = next_document
        document.version = max(int(document.version or 1), 1) + 1
        document.updated_by_user_id = current_user.id
        document.updated_at = time.time()
        session.add(document)
        session.commit()
        session.refresh(document)

    workbook_items = _list_workbook_refs_for_sheet_ids(session, [document.id]).get(document.id, [])
    paginate_enabled, page_size = _get_document_pagination_settings(next_document)
    if paginate_enabled:
        response_document, pagination = _build_paged_document(next_document, page=1, page_size=page_size)
    else:
        response_document = _normalize_document_json(next_document)
        pagination = None

    return NoteSheetDetailResponse.model_validate(
        _serialize_sheet_detail(
            document,
            workbook_items=workbook_items,
            document_json=response_document,
            pagination=pagination,
            access=access,
        ),
    )


@router.post(
    "/sheets/{sheet_id}/attendance-summary/generate-next-month-templates",
    response_model=NoteSheetAttendanceTemplateGenerationResponse,
)
def generate_attendance_summary_next_month_templates(
    sheet_id: int,
    payload: NoteSheetAttendanceTemplateGenerationRequest = NoteSheetAttendanceTemplateGenerationRequest(),
    workbook_id: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    document, access, _workbook = _get_note_sheet_or_404(
        session,
        current_user,
        sheet_id,
        required_role="editor",
        workbook_id=workbook_id,
    )
    if current_user is None:
        raise HTTPException(status_code=403, detail="没有该资源权限")
    if not _is_attendance_summary_document(session, document):
        raise HTTPException(status_code=404, detail="当前工作表没有这个定制功能")

    target_date = _get_attendance_template_target_date(payload)
    current_document = _normalize_document_json(dict(document.document_json or {}))
    next_document, generated, skipped = _generate_attendance_next_month_templates(
        current_document,
        target_date=target_date,
    )

    if generated and current_document != next_document:
        document.document_json = next_document
        document.version = max(int(document.version or 1), 1) + 1
        document.updated_by_user_id = current_user.id
        document.updated_at = time.time()
        session.add(document)
        session.commit()
        session.refresh(document)
    else:
        next_document = current_document

    return NoteSheetAttendanceTemplateGenerationResponse(
        sheet=_build_attendance_template_detail_response(session, document, next_document, access=access),
        generated=generated,
        skipped=skipped,
    )


@router.post(
    "/sheets/{sheet_id}/attendance-summary/generate-course-template",
    response_model=NoteSheetAttendanceTemplateGenerationResponse,
)
def generate_attendance_summary_course_template(
    sheet_id: int,
    payload: NoteSheetAttendanceCourseTemplateGenerationRequest,
    workbook_id: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    document, access, _workbook = _get_note_sheet_or_404(
        session,
        current_user,
        sheet_id,
        required_role="editor",
        workbook_id=workbook_id,
    )
    if current_user is None:
        raise HTTPException(status_code=403, detail="没有该资源权限")
    if not _is_attendance_summary_document(session, document):
        raise HTTPException(status_code=404, detail="当前工作表没有这个定制功能")

    current_document = _normalize_document_json(dict(document.document_json or {}))
    course_type = _resolve_attendance_course_template_type(current_document, payload)
    target_date = _get_attendance_course_template_target_date(course_type, payload)
    next_document, generated, skipped = _generate_attendance_course_templates(
        current_document,
        targets=[(course_type, target_date)],
    )

    if generated and current_document != next_document:
        document.document_json = next_document
        document.version = max(int(document.version or 1), 1) + 1
        document.updated_by_user_id = current_user.id
        document.updated_at = time.time()
        session.add(document)
        session.commit()
        session.refresh(document)
    else:
        next_document = current_document

    return NoteSheetAttendanceTemplateGenerationResponse(
        sheet=_build_attendance_template_detail_response(session, document, next_document, access=access),
        generated=generated,
        skipped=skipped,
    )


@router.get(
    "/sheets/{sheet_id}/attendance-summary/course-script-statuses",
    response_model=NoteSheetAttendanceCourseScriptStatusesResponse,
)
def list_attendance_summary_course_script_statuses(
    sheet_id: int,
    workbook_id: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    document, _access, _workbook = _get_note_sheet_or_404(
        session,
        current_user,
        sheet_id,
        required_role="editor",
        workbook_id=workbook_id,
    )
    if current_user is None:
        raise HTTPException(status_code=403, detail="没有该资源权限")
    if not _is_attendance_summary_document(session, document):
        raise HTTPException(status_code=404, detail="当前工作表没有这个定制功能")

    current_document = _normalize_document_json(dict(document.document_json or {}))
    return NoteSheetAttendanceCourseScriptStatusesResponse(
        statuses=_list_attendance_course_script_statuses(current_document),
    )


@router.post(
    "/sheets/{sheet_id}/attendance-summary/generate-course-script",
    response_model=NoteSheetAttendanceCourseScriptGenerationResponse,
)
def generate_attendance_summary_course_script(
    sheet_id: int,
    payload: NoteSheetAttendanceCourseScriptGenerationRequest,
    workbook_id: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    document, _access, _workbook = _get_note_sheet_or_404(
        session,
        current_user,
        sheet_id,
        required_role="editor",
        workbook_id=workbook_id,
    )
    if current_user is None:
        raise HTTPException(status_code=403, detail="没有该资源权限")
    if not _is_attendance_summary_document(session, document):
        raise HTTPException(status_code=404, detail="当前工作表没有这个定制功能")

    current_document = _normalize_document_json(dict(document.document_json or {}))
    return _generate_attendance_course_script(current_document, row_index=payload.row_index)


@router.post(
    "/sheets/{sheet_id}/attendance-summary/organize-course-scripts",
    response_model=NoteSheetAttendanceCourseScriptOrganizeResponse,
)
def organize_attendance_summary_course_scripts(
    sheet_id: int,
    workbook_id: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    document, _access, _workbook = _get_note_sheet_or_404(
        session,
        current_user,
        sheet_id,
        required_role="editor",
        workbook_id=workbook_id,
    )
    if current_user is None:
        raise HTTPException(status_code=403, detail="没有该资源权限")
    if not _is_attendance_summary_document(session, document):
        raise HTTPException(status_code=404, detail="当前工作表没有这个定制功能")

    current_document = _normalize_document_json(dict(document.document_json or {}))
    return _organize_attendance_course_scripts(current_document)


@router.post(
    "/sheets/{sheet_id}/attendance-summary/update-link-counts",
    response_model=NoteSheetAttendanceLinkCountUpdateResponse,
)
def update_attendance_summary_link_counts(
    sheet_id: int,
    payload: NoteSheetAttendanceLinkCountUpdateRequest,
    workbook_id: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    document, access, _workbook = _get_note_sheet_or_404(
        session,
        current_user,
        sheet_id,
        required_role="editor",
        workbook_id=workbook_id,
    )
    if current_user is None:
        raise HTTPException(status_code=403, detail="没有该资源权限")
    if not _is_attendance_summary_document(session, document):
        raise HTTPException(status_code=404, detail="当前工作表没有这个定制功能")

    current_document = _normalize_document_json(dict(document.document_json or {}))
    next_document, updated, skipped = _update_attendance_link_counts(
        current_document,
        field_key=payload.field_key,
        row_index=payload.row_index,
    )

    if current_document != next_document:
        document.document_json = next_document
        document.version = max(int(document.version or 1), 1) + 1
        document.updated_by_user_id = current_user.id
        document.updated_at = time.time()
        session.add(document)
        session.commit()
        session.refresh(document)
    else:
        next_document = current_document

    return NoteSheetAttendanceLinkCountUpdateResponse(
        sheet=_build_attendance_template_detail_response(session, document, next_document, access=access),
        updated=updated,
        skipped=skipped,
    )


@router.post(
    "/sheets/{sheet_id}/attendance-summary/set-completed",
    response_model=NoteSheetAttendanceCompletionResponse,
)
def set_attendance_summary_row_completed(
    sheet_id: int,
    payload: NoteSheetAttendanceCompletionRequest,
    workbook_id: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    document, access, _workbook = _get_note_sheet_or_404(
        session,
        current_user,
        sheet_id,
        required_role="editor",
        workbook_id=workbook_id,
    )
    if current_user is None:
        raise HTTPException(status_code=403, detail="没有该资源权限")
    if not _is_attendance_summary_document(session, document):
        raise HTTPException(status_code=404, detail="当前工作表没有这个定制功能")

    completion_date = _parse_attendance_date_text(payload.completion_date) if payload.completion_date else date.today()
    if completion_date is None:
        raise HTTPException(status_code=400, detail="completion_date 格式不正确")

    current_document = _normalize_document_json(dict(document.document_json or {}))
    next_document, next_row_index = _set_attendance_summary_row_completed(
        current_document,
        row_index=payload.row_index,
        completion_date=completion_date,
    )

    if current_document != next_document:
        document.document_json = next_document
        document.version = max(int(document.version or 1), 1) + 1
        document.updated_by_user_id = current_user.id
        document.updated_at = time.time()
        session.add(document)
        session.commit()
        session.refresh(document)
    else:
        next_document = current_document

    return NoteSheetAttendanceCompletionResponse(
        sheet=_build_attendance_template_detail_response(session, document, next_document, access=access),
        row_index=next_row_index,
    )


@router.delete("/sheets/{sheet_id}")
def delete_note_sheet(
    sheet_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    _require_note_sheets_feature(session, current_user)
    document, _access, _workbook = _get_note_sheet_or_404(session, current_user, sheet_id, required_role="manager")
    session.exec(delete(WorkbookSheetLink).where(WorkbookSheetLink.sheet_id == document.id))
    session.delete(document)
    session.commit()
    return {"ok": True}


@router.get("/workbooks", response_model=list[WorkbookSummaryResponse])
def list_workbooks(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    _require_note_sheets_feature(session, current_user)
    workbooks = session.exec(
        select(WorkbookDocument)
        .where(WorkbookDocument.owner_user_id == current_user.id)
        .order_by(WorkbookDocument.updated_at.desc(), WorkbookDocument.created_at.desc())
    ).all()
    if not workbooks:
        return []

    workbook_ids = [workbook.id for workbook in workbooks]
    links = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id.in_(workbook_ids))
    ).all()
    counts: dict[str, int] = {}
    for link in links:
        counts[link.workbook_id] = counts.get(link.workbook_id, 0) + 1

    return [
        WorkbookSummaryResponse.model_validate(
            _serialize_workbook_summary(
                workbook,
                sheet_count=counts.get(workbook.id, 0),
                access=_resolve_workbook_resource_access(session, workbook, current_user),
            ),
        )
        for workbook in workbooks
    ]


@router.post("/workbooks", response_model=WorkbookDetailResponse)
def create_workbook(
    payload: WorkbookCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    _require_note_sheets_feature(session, current_user)
    now = time.time()
    workbook = WorkbookDocument(
        numeric_id=_get_next_workbook_numeric_id(session),
        title=_normalize_title(payload.title, default_value="未命名工作簿"),
        owner_user_id=current_user.id,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(workbook)
    session.commit()
    session.refresh(workbook)
    access = _resolve_workbook_resource_access(session, workbook, current_user)
    return WorkbookDetailResponse.model_validate(
        _serialize_workbook_detail(session, workbook, current_user=current_user, access=access),
    )


@router.get("/workbooks/{workbook_id}", response_model=WorkbookDetailResponse)
def get_workbook(
    workbook_id: int,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    workbook, access = _get_workbook_or_404(session, current_user, workbook_id, required_role="viewer")
    return WorkbookDetailResponse.model_validate(
        _serialize_workbook_detail(session, workbook, current_user=current_user, access=access),
    )


@router.get("/workbooks/{workbook_id}/access", response_model=NoteSheetResourceAccessResponse)
def get_workbook_access(
    workbook_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    workbook, access = _get_workbook_or_404(session, current_user, workbook_id, required_role="manager")
    return _build_resource_access_response(
        session,
        resource_type=RESOURCE_TYPE_WORKBOOK,
        numeric_id=_require_workbook_numeric_id(workbook),
        resource_id=workbook.id,
        access=access,
    )


@router.put("/workbooks/{workbook_id}/access", response_model=NoteSheetResourceAccessResponse)
def update_workbook_access(
    workbook_id: int,
    payload: NoteSheetResourceAccessUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    workbook, _access = _get_workbook_or_404(session, current_user, workbook_id, required_role="manager")
    _save_resource_access_grants(
        session,
        resource_type=RESOURCE_TYPE_WORKBOOK,
        resource_id=workbook.id,
        payload=payload,
        current_user=current_user,
    )
    session.commit()
    access = _resolve_workbook_resource_access(session, workbook, current_user)
    return _build_resource_access_response(
        session,
        resource_type=RESOURCE_TYPE_WORKBOOK,
        numeric_id=_require_workbook_numeric_id(workbook),
        resource_id=workbook.id,
        access=access,
    )


@router.post("/workbooks/{workbook_id}/save-as", response_model=WorkbookDetailResponse)
def save_as_workbook(
    workbook_id: int,
    payload: WorkbookSaveAsRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    _require_note_sheets_feature(session, current_user)
    source_workbook, _source_access = _get_workbook_or_404(
        session,
        current_user,
        workbook_id,
        required_role="viewer",
    )
    links = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == source_workbook.id)
        .order_by(WorkbookSheetLink.order_index, WorkbookSheetLink.created_at)
    ).all()
    source_sheet_ids = [link.sheet_id for link in links]
    source_sheets = session.exec(
        select(SheetDocument)
        .where(SheetDocument.id.in_(source_sheet_ids) if source_sheet_ids else False)
    ).all() if source_sheet_ids else []
    source_sheet_map = {sheet.id: sheet for sheet in source_sheets}

    now = time.time()
    workbook = WorkbookDocument(
        numeric_id=_get_next_workbook_numeric_id(session),
        title=_normalize_title(payload.title, default_value="未命名工作簿"),
        owner_user_id=current_user.id,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(workbook)
    session.flush()

    for link in links:
        source_sheet = source_sheet_map.get(link.sheet_id)
        if source_sheet is None:
            continue

        document = SheetDocument(
            numeric_id=_get_next_sheet_numeric_id(session),
            scope=source_sheet.scope,
            owner_type=source_sheet.owner_type,
            owner_key=source_sheet.owner_key,
            sheet_key="pending",
            title=source_sheet.title,
            engine=source_sheet.engine,
            document_json=_clone_sheet_document_json(
                dict(source_sheet.document_json or {}),
                mode=payload.mode,
            ),
            version=1,
            owner_user_id=current_user.id,
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
            created_at=now,
            updated_at=now,
        )
        session.add(document)
        session.flush()
        document.sheet_key = str(document.numeric_id or document.id)
        session.add(document)
        session.add(
            WorkbookSheetLink(
                workbook_id=workbook.id,
                sheet_id=document.id,
                order_index=link.order_index,
                created_at=now,
            ),
        )

    session.commit()
    session.refresh(workbook)
    access = _resolve_workbook_resource_access(session, workbook, current_user)
    return WorkbookDetailResponse.model_validate(
        _serialize_workbook_detail(session, workbook, current_user=current_user, access=access),
    )


@router.post("/workbooks/{workbook_id}/sheets", response_model=WorkbookDetailResponse)
def attach_sheet_to_workbook(
    workbook_id: int,
    payload: WorkbookAttachSheetRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    _require_note_sheets_feature(session, current_user)
    workbook, access = _get_workbook_or_404(session, current_user, workbook_id, required_role="manager")
    document, _sheet_access, _ = _get_note_sheet_or_404(session, current_user, payload.sheet_id, required_role="manager")

    existing = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == workbook.id)
        .where(WorkbookSheetLink.sheet_id == document.id)
    ).first()

    if existing is None:
        session.add(
            WorkbookSheetLink(
                workbook_id=workbook.id,
                sheet_id=document.id,
                order_index=_get_next_workbook_link_order(session, workbook.id),
            ),
        )
        workbook.updated_by_user_id = current_user.id
        workbook.updated_at = time.time()
        session.add(workbook)
        session.commit()
        session.refresh(workbook)

    return WorkbookDetailResponse.model_validate(
        _serialize_workbook_detail(session, workbook, current_user=current_user, access=access),
    )


@router.delete("/workbooks/{workbook_id}/sheets/{sheet_id}", response_model=WorkbookDetailResponse)
def remove_sheet_from_workbook(
    workbook_id: int,
    sheet_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    _require_note_sheets_feature(session, current_user)
    workbook, access = _get_workbook_or_404(session, current_user, workbook_id, required_role="manager")
    document, _sheet_access, _ = _get_note_sheet_or_404(session, current_user, sheet_id, required_role="manager")

    link = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == workbook.id)
        .where(WorkbookSheetLink.sheet_id == document.id)
    ).first()
    if link is not None:
        session.delete(link)
        workbook.updated_by_user_id = current_user.id
        workbook.updated_at = time.time()
        session.add(workbook)
        session.commit()
        session.refresh(workbook)

    return WorkbookDetailResponse.model_validate(
        _serialize_workbook_detail(session, workbook, current_user=current_user, access=access),
    )


@router.delete("/workbooks/{workbook_id}")
def delete_workbook(
    workbook_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    _require_note_sheets_feature(session, current_user)
    workbook, _access = _get_workbook_or_404(session, current_user, workbook_id, required_role="manager")
    session.exec(delete(WorkbookSheetLink).where(WorkbookSheetLink.workbook_id == workbook.id))
    session.delete(workbook)
    session.commit()
    return {"ok": True}
