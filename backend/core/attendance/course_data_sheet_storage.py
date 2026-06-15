from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import re
import time
from typing import Any, Iterable

from sqlmodel import Session, select

from backend.api.note_sheets import (
    _extract_document_rows,
    _normalize_document_columns,
)
from backend.core.attendance.progress_style import sheet_text
from backend.core.notes import sheet_inline_links as note_sheet_inline_links
from backend.core.resources.sheet_identity import allocate_new_sheet_identity
from backend.core.resources.sheet_refs import (
    load_sheets_by_refs,
    sheet_public_id,
    sheet_ref_aliases,
    workbook_public_id,
    workbook_ref_aliases,
)
from backend.models import SheetDocument, WorkbookDocument, WorkbookSheetLink


@dataclass(frozen=True)
class CourseSheetSpec:
    sheet_key: str
    title: str
    order_index: int


def normalize_text(value: Any) -> str:
    return sheet_text(value)


def normalize_row(row: Any, column_count: int) -> list[Any]:
    if isinstance(row, list):
        return [*row[:column_count], *([""] * max(column_count - len(row), 0))]
    if isinstance(row, dict):
        return [row.get(str(index), "") for index in range(column_count)]
    return [""] * column_count


def video_lesson_url_from_lesson_id2(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return text
    if text.startswith("l_"):
        return f"https://admin.xiaoe-tech.com/t/live_management#/userOperation?id={text}&tabName=UserManage"
    return ""


def document_field_row_index(document: dict[str, Any]) -> int:
    try:
        field_row_index = int(document.get("field_row_index") or 0)
    except (TypeError, ValueError):
        try:
            field_row_index = int(document.get("data_start_row") or 1) - 1
        except (TypeError, ValueError):
            field_row_index = 0
    return max(field_row_index, 0)


def set_grid_cell_inline_link(
    document: dict[str, Any],
    *,
    row_index: int,
    column_index: int,
    url: str,
) -> tuple[dict[str, Any], bool]:
    normalized_url = normalize_text(url)
    if not normalized_url:
        return document, False

    columns = _normalize_document_columns(document)
    if column_index < 0 or column_index >= len(columns):
        return document, False

    source_grid_rows = document.get("grid_rows")
    if not isinstance(source_grid_rows, list) or row_index < 0:
        return document, False

    grid_rows = [normalize_row(row, len(columns)) for row in source_grid_rows]
    while len(grid_rows) <= row_index:
        grid_rows.append([""] * len(columns))

    next_document = dict(document)
    changed = False

    old_value = grid_rows[row_index][column_index]
    new_value = note_sheet_inline_links.with_inline_cell_link(old_value, {"url": normalized_url})
    if new_value != old_value:
        grid_rows[row_index][column_index] = new_value
        next_document["grid_rows"] = grid_rows
        changed = True

    cell_meta = dict(next_document.get("cell_meta")) if isinstance(next_document.get("cell_meta"), dict) else {}
    meta_key = f"{row_index}:{column_index}"
    previous_meta = cell_meta.get(meta_key)
    next_meta = dict(previous_meta) if isinstance(previous_meta, dict) else {}
    previous_link = next_meta.get("link")
    previous_url = normalize_text(previous_link.get("url")) if isinstance(previous_link, dict) else ""
    if previous_url != normalized_url:
        next_meta["link"] = {"url": normalized_url}
        cell_meta[meta_key] = next_meta
        next_document["cell_meta"] = cell_meta
        changed = True

    return next_document, changed


def json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe_value(item) for item in value]
    return str(value)


def create_simple_document(
    *,
    columns: list[str],
    rows: list[list[Any]],
    numeric_columns: set[str] | None = None,
    hidden_columns: set[str] | None = None,
    page_size: int = 100,
) -> dict[str, Any]:
    numeric_columns = numeric_columns or set()
    hidden_columns = hidden_columns or set()
    column_configs: dict[str, dict[str, Any]] = {}
    for column in columns:
        config: dict[str, Any] = {}
        if column in numeric_columns:
            config["value_type"] = "number"
        if column in hidden_columns:
            config["hidden"] = True
        if config:
            column_configs[column] = config
    return {
        "schema_version": 1,
        "columns": columns,
        "rows": json_safe_value(rows),
        "grid_rows": json_safe_value([columns, *rows]),
        "data_start_row": 1,
        "field_row_index": 0,
        "column_configs": column_configs,
        "cell_meta": {},
        "merged_cells": [],
        "header_groups": [],
        "formula_reference_origin": "sheet_v2",
        "view_settings": {
            "show_row_numbers": True,
            "row_marker_numbering": "global",
            "row_marker_origin": "sheet",
            "show_column_markers": True,
            "column_marker_style": "letters",
            "height_mode": "fill",
            "pagination": {"enabled": len(rows) > page_size, "page_size": page_size},
        },
    }


def document_dict_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    columns = _normalize_document_columns(document)
    rows = [normalize_row(row, len(columns)) for row in _extract_document_rows(document)]
    return [dict(zip(columns, row)) for row in rows]


def split_linked_user_ids(value: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[,，;；\s]+", normalize_text(value)):
        user_id = item.strip()
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        result.append(user_id)
    return result


def build_registration_identity_map(
    session: Session,
    *,
    attendance: SheetDocument,
    default_owner_key: str,
) -> dict[str, list[str]]:
    owner_key = normalize_text(attendance.owner_key) or default_owner_key
    registration = find_course_sheet(session, owner_key=owner_key, sheet_key="registration")
    if registration is None:
        return {}

    identities: dict[str, list[str]] = {}
    for row in document_dict_rows(dict(registration.document_json or {})):
        student_id = normalize_text(row.get("序号")) or normalize_text(row.get("学号"))
        if not student_id:
            continue

        user_ids: list[str] = []
        seen: set[str] = set()
        for user_id in [normalize_text(row.get("用户ID")), *split_linked_user_ids(row.get("关联用户ID"))]:
            if not user_id or user_id in seen:
                continue
            seen.add(user_id)
            user_ids.append(user_id)
        identities[student_id] = user_ids
    return identities


def attendance_row_user_ids(
    row: dict[str, Any] | list[Any],
    columns: list[str] | None = None,
    *,
    registration_identity_map: dict[str, list[str]] | None = None,
) -> list[str]:
    if isinstance(row, list):
        mapping = dict(zip(columns or [], normalize_row(row, len(columns or []))))
    else:
        mapping = row

    student_id = normalize_text(mapping.get("学号")) or normalize_text(mapping.get("序号"))
    if student_id and registration_identity_map is not None:
        registration_user_ids = registration_identity_map.get(student_id)
        if registration_user_ids:
            return list(registration_user_ids)

    user_ids: list[str] = []
    seen: set[str] = set()
    for user_id in [normalize_text(mapping.get("用户ID")), *split_linked_user_ids(mapping.get("关联用户ID"))]:
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        user_ids.append(user_id)
    return user_ids


def build_registration_user_alias_map(registration_identity_map: dict[str, list[str]]) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for user_ids in registration_identity_map.values():
        primary_user_id = user_ids[0] if user_ids else ""
        if not primary_user_id:
            continue
        for linked_user_id in user_ids[1:]:
            if linked_user_id and linked_user_id != primary_user_id:
                alias_map[linked_user_id] = primary_user_id
    return alias_map


def make_table_document_from_dicts(
    *,
    columns: list[str],
    rows: Iterable[dict[str, Any]],
    numeric_columns: set[str] | None = None,
    hidden_columns: set[str] | None = None,
    page_size: int = 100,
) -> dict[str, Any]:
    return create_simple_document(
        columns=columns,
        rows=[[json_safe_value(row.get(column, "")) for column in columns] for row in rows],
        numeric_columns=numeric_columns,
        hidden_columns=hidden_columns,
        page_size=page_size,
    )


def get_workbook(session: Session, workbook_id: int) -> WorkbookDocument:
    workbook = session.exec(select(WorkbookDocument).where(WorkbookDocument.numeric_id == int(workbook_id))).first()
    if workbook is None:
        raise RuntimeError(f"工作簿不存在：workbook_id={workbook_id}")
    return workbook


def get_sheet(session: Session, sheet_id: int) -> SheetDocument:
    sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == int(sheet_id))).first()
    if sheet is None:
        raise RuntimeError(f"表格不存在：sheet_id={sheet_id}")
    return sheet


def find_course_sheet(session: Session, *, owner_key: str, sheet_key: str) -> SheetDocument | None:
    return session.exec(
        select(SheetDocument)
        .where(SheetDocument.scope == "notes")
        .where(SheetDocument.owner_type == "course_workbook")
        .where(SheetDocument.owner_key == owner_key)
        .where(SheetDocument.sheet_key == sheet_key)
    ).first()


def ensure_workbook_link(
    session: Session,
    *,
    workbook: WorkbookDocument,
    sheet: SheetDocument,
    order_index: int,
) -> bool:
    link = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id.in_(workbook_ref_aliases(workbook)))
        .where(WorkbookSheetLink.sheet_id.in_(sheet_ref_aliases(sheet)))
    ).first()
    if link is None:
        session.add(
            WorkbookSheetLink(
                workbook_id=workbook_public_id(workbook),
                sheet_id=sheet_public_id(sheet),
                order_index=order_index,
                created_at=time.time(),
            )
        )
        return True

    changed = False
    workbook_ref = workbook_public_id(workbook)
    sheet_ref = sheet_public_id(sheet)
    if link.workbook_id != workbook_ref:
        link.workbook_id = workbook_ref
        changed = True
    if link.sheet_id != sheet_ref:
        link.sheet_id = sheet_ref
        changed = True
    if int(link.order_index or 0) != int(order_index):
        link.order_index = int(order_index)
        changed = True
    if changed:
        session.add(link)
    return changed


def upsert_course_sheet(
    session: Session,
    *,
    workbook: WorkbookDocument,
    owner_key: str,
    spec: CourseSheetSpec,
    document_json: dict[str, Any],
    owner_user_id: int | None,
    replace: bool,
) -> tuple[SheetDocument, bool, bool]:
    now = time.time()
    document_json = json_safe_value(document_json)
    sheet = find_course_sheet(session, owner_key=owner_key, sheet_key=spec.sheet_key)
    created = False
    changed = False
    if sheet is None:
        identity = allocate_new_sheet_identity(session)
        sheet = SheetDocument(
            id=identity.primary_id,
            numeric_id=identity.numeric_id,
            legacy_id=identity.legacy_id,
            scope="notes",
            owner_type="course_workbook",
            owner_key=owner_key,
            sheet_key=spec.sheet_key,
            title=spec.title,
            engine="handsontable",
            document_json=document_json,
            version=1,
            owner_user_id=owner_user_id,
            created_by_user_id=owner_user_id,
            updated_by_user_id=owner_user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(sheet)
        session.flush()
        created = True
        changed = True
    elif replace:
        if sheet.title != spec.title or dict(sheet.document_json or {}) != document_json:
            sheet.title = spec.title
            sheet.document_json = document_json
            sheet.version = max(int(sheet.version or 1), 1) + 1
            sheet.updated_by_user_id = owner_user_id
            sheet.updated_at = now
            changed = True
        if sheet.owner_user_id is None and owner_user_id is not None:
            sheet.owner_user_id = owner_user_id
            changed = True
        if changed:
            session.add(sheet)

    link_changed = ensure_workbook_link(session, workbook=workbook, sheet=sheet, order_index=spec.order_index)
    return sheet, created, changed or link_changed


def materialize_course_sheets(
    session: Session,
    *,
    workbook_id: int,
    attendance_sheet_id: int,
    default_owner_key: str,
    specs: Iterable[CourseSheetSpec],
    documents: dict[str, dict[str, Any]],
    replace: bool = False,
) -> dict[str, Any]:
    workbook = get_workbook(session, workbook_id)
    attendance = get_sheet(session, attendance_sheet_id)
    owner_key = normalize_text(attendance.owner_key) or default_owner_key
    owner_user_id = attendance.owner_user_id or workbook.owner_user_id

    sheet_summaries: list[dict[str, Any]] = []
    changed_any = False
    for spec in specs:
        sheet, created, changed = upsert_course_sheet(
            session,
            workbook=workbook,
            owner_key=owner_key,
            spec=spec,
            document_json=documents[spec.sheet_key],
            owner_user_id=owner_user_id,
            replace=replace,
        )
        document_json = dict(sheet.document_json or {})
        changed_any = changed_any or changed
        sheet_summaries.append({
            "sheet_key": sheet.sheet_key,
            "title": sheet.title,
            "id": int(sheet.numeric_id or 0),
            "created": created,
            "changed": changed,
            "rows": len(_extract_document_rows(document_json)),
        })

    if changed_any:
        workbook.updated_by_user_id = owner_user_id
        workbook.updated_at = time.time()
        session.add(workbook)

    return {
        "workbook_id": int(workbook.numeric_id or 0),
        "attendance_sheet_id": int(attendance.numeric_id or 0),
        "owner_key": owner_key,
        "replace": replace,
        "changed": changed_any,
        "sheets": sheet_summaries,
    }


def load_course_sheet_bundle(
    session: Session,
    *,
    attendance: SheetDocument,
    sheet_keys: Iterable[str],
    default_owner_key: str,
    course_label: str,
) -> dict[str, SheetDocument]:
    owner_key = normalize_text(attendance.owner_key) or default_owner_key
    result: dict[str, SheetDocument] = {}
    for sheet_key in sheet_keys:
        sheet = find_course_sheet(session, owner_key=owner_key, sheet_key=sheet_key)
        if sheet is None:
            raise RuntimeError(f"{course_label}课程工作簿缺少 sheet：{sheet_key}")
        result[sheet_key] = sheet
    return result


def has_course_storage_sheets(
    session: Session,
    *,
    attendance_sheet: SheetDocument,
    sheet_keys: Iterable[str],
    default_owner_key: str,
) -> bool:
    owner_key = normalize_text(attendance_sheet.owner_key) or default_owner_key
    return all(find_course_sheet(session, owner_key=owner_key, sheet_key=sheet_key) is not None for sheet_key in sheet_keys)


def update_course_sheet_document(sheet: SheetDocument, document: dict[str, Any]) -> None:
    sheet.document_json = document
    sheet.version = max(int(sheet.version or 1), 1) + 1
    sheet.updated_at = time.time()
