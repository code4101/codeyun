from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable, TypedDict

from sqlmodel import Session, select

from backend.core.freebill import (
    get_freebill_dashboard,
    list_freebill_raw_files,
    list_freebill_records,
)
from backend.models import SheetDocument, WorkbookDocument, WorkbookSheetLink


FREEBILL_SHEET_OWNER_TYPE = "freebill"
FREEBILL_WORKBOOK_TITLE = "Freebill 账单"
FREEBILL_PAGE_SIZE = 50
INCOME_TEXT_COLOR = "#15803d"
EXPENSE_TEXT_COLOR = "#b91c1c"
MUTED_TEXT_COLOR = "#64748b"


class FreebillColumn(TypedDict, total=False):
    key: str
    label: str
    min_width: int
    max_width: int
    width: int
    align: str
    default_visible: bool
    value_type: str
    value_mode: str
    display_format: str


RECORD_COLUMNS: list[FreebillColumn] = [
    {"key": "create_time", "label": "交易时间", "min_width": 138, "max_width": 176, "value_type": "date", "display_format": "yyyy/mm/dd hh:mm"},
    {"key": "source", "label": "来源", "min_width": 58, "max_width": 82, "value_mode": "fixed_options"},
    {"key": "direction", "label": "收支", "min_width": 58, "max_width": 82, "value_mode": "fixed_options"},
    {"key": "type", "label": "分类", "min_width": 82, "max_width": 150, "value_mode": "fixed_options"},
    {"key": "counterparty", "label": "交易对方", "min_width": 110, "max_width": 210},
    {"key": "product_name", "label": "商品", "min_width": 160, "max_width": 320},
    {"key": "amount", "label": "金额", "min_width": 78, "max_width": 112, "align": "right", "value_type": "number"},
    {"key": "status", "label": "状态", "min_width": 78, "max_width": 126, "value_mode": "fixed_options"},
    {"key": "trade_no", "label": "交易单号", "min_width": 156, "max_width": 240},
    {"key": "merchant_order_no", "label": "商户单号", "min_width": 126, "max_width": 220},
    {"key": "remark", "label": "备注", "min_width": 96, "max_width": 220},
    {"key": "fund_status", "label": "资金状态", "min_width": 78, "max_width": 128, "value_mode": "fixed_options", "default_visible": False},
    {"key": "pay_time", "label": "支付时间", "min_width": 138, "max_width": 176, "value_type": "date", "display_format": "yyyy/mm/dd hh:mm", "default_visible": False},
    {"key": "modify_time", "label": "修改时间", "min_width": 138, "max_width": 176, "value_type": "date", "display_format": "yyyy/mm/dd hh:mm", "default_visible": False},
]

MONTHLY_COLUMNS: list[FreebillColumn] = [
    {"key": "month", "label": "月份", "min_width": 72, "max_width": 96},
    {"key": "income", "label": "收入", "min_width": 82, "max_width": 118, "align": "right", "value_type": "number"},
    {"key": "expense", "label": "支出", "min_width": 82, "max_width": 118, "align": "right", "value_type": "number"},
    {"key": "balance", "label": "结余", "min_width": 82, "max_width": 118, "align": "right", "value_type": "number"},
    {"key": "count", "label": "笔数", "min_width": 58, "max_width": 82, "align": "right", "value_type": "number"},
]

CATEGORY_COLUMNS: list[FreebillColumn] = [
    {"key": "direction", "label": "收支", "min_width": 58, "max_width": 82, "value_mode": "fixed_options"},
    {"key": "name", "label": "分类", "min_width": 100, "max_width": 180, "value_mode": "fixed_options"},
    {"key": "value", "label": "金额", "min_width": 82, "max_width": 118, "align": "right", "value_type": "number"},
    {"key": "count", "label": "笔数", "min_width": 58, "max_width": 82, "align": "right", "value_type": "number"},
]

RAW_FILE_COLUMNS: list[FreebillColumn] = [
    {"key": "source", "label": "来源", "min_width": 58, "max_width": 82, "value_mode": "fixed_options"},
    {"key": "relative_path", "label": "原始路径", "min_width": 220, "max_width": 420},
    {"key": "extension", "label": "类型", "min_width": 52, "max_width": 72, "value_mode": "fixed_options"},
    {"key": "size_bytes", "label": "大小", "min_width": 74, "max_width": 104, "align": "right"},
    {"key": "import_status", "label": "状态", "min_width": 70, "max_width": 104, "value_mode": "fixed_options"},
    {"key": "sha256", "label": "SHA256", "min_width": 180, "max_width": 260},
    {"key": "archived_path", "label": "归档路径", "min_width": 220, "max_width": 420, "default_visible": False},
    {"key": "modified_at", "label": "原文件时间", "min_width": 128, "max_width": 176, "value_type": "date", "display_format": "yyyy/mm/dd hh:mm"},
    {"key": "note", "label": "备注", "min_width": 100, "max_width": 220},
]

SHEET_SPECS = [
    ("records", "账单明细", RECORD_COLUMNS),
    ("monthly", "月度汇总", MONTHLY_COLUMNS),
    ("categories", "分类汇总", CATEGORY_COLUMNS),
    ("raw-files", "原始文件", RAW_FILE_COLUMNS),
]


def refresh_freebill_sheet_workbook(
    session: Session,
    *,
    user_id: int,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    owner_key = str(user_id)
    now = time.time()
    workbook = _get_or_create_freebill_workbook(session, user_id=user_id, actor_user_id=actor_user_id, now=now)
    datasets = _build_freebill_sheet_datasets()
    sheet_items: list[dict[str, Any]] = []

    for order_index, (sheet_key, title, columns) in enumerate(SHEET_SPECS):
        rows = datasets[sheet_key]
        document_json = _build_sheet_document(columns, rows, sheet_key=sheet_key)
        sheet = _upsert_freebill_sheet(
            session,
            owner_key=owner_key,
            sheet_key=sheet_key,
            title=title,
            document_json=document_json,
            user_id=user_id,
            actor_user_id=actor_user_id,
            now=now,
        )
        if float(sheet.updated_at or 0.0) == now:
            workbook.updated_by_user_id = actor_user_id or user_id
            workbook.updated_at = now
        _ensure_workbook_link(session, workbook=workbook, sheet=sheet, order_index=order_index, now=now)
        sheet_items.append({
            "key": sheet_key,
            "title": sheet.title,
            "sheet_id": _require_sheet_numeric_id(sheet),
            "row_count": len(rows),
            "updated_at": float(sheet.updated_at or 0.0),
        })

    session.add(workbook)
    session.commit()
    session.refresh(workbook)
    for item in sheet_items:
        sheet = _get_freebill_sheet(session, owner_key=owner_key, sheet_key=item["key"])
        if sheet is not None:
            item["updated_at"] = float(sheet.updated_at or 0.0)

    return _serialize_freebill_workbook_payload(workbook, sheet_items, refreshed_at=now)


def get_freebill_sheet_workbook(
    session: Session,
    *,
    user_id: int,
) -> dict[str, Any] | None:
    workbook = _find_existing_freebill_workbook(session, user_id=user_id)
    if workbook is None:
        return None
    return _serialize_freebill_workbook_payload(
        workbook,
        _list_freebill_workbook_sheets(session, workbook=workbook),
        refreshed_at=float(workbook.updated_at or 0.0),
    )


def _build_freebill_sheet_datasets() -> dict[str, list[dict[str, Any]]]:
    records = _collect_paginated(lambda *, limit, offset: list_freebill_records(limit=limit, offset=offset))
    dashboard = get_freebill_dashboard(category_limit=None, monthly_limit=None)
    monthly_rows = [
        {
            **item,
            "balance": float(item.get("income") or 0) - float(item.get("expense") or 0),
        }
        for item in dashboard["monthly_trend"]
    ]
    category_rows = [
        {"direction": "支出", **item}
        for item in dashboard["expense_categories"]
    ] + [
        {"direction": "收入", **item}
        for item in dashboard["income_categories"]
    ]
    raw_files = _collect_paginated(lambda *, limit, offset: list_freebill_raw_files(limit=limit, offset=offset))
    return {
        "records": records,
        "monthly": monthly_rows,
        "categories": category_rows,
        "raw-files": raw_files,
    }


def _list_freebill_workbook_sheets(session: Session, *, workbook: WorkbookDocument) -> list[dict[str, Any]]:
    links = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == workbook.id)
        .order_by(WorkbookSheetLink.order_index, WorkbookSheetLink.created_at)
    ).all()
    if not links:
        return []

    sheet_ids = [link.sheet_id for link in links]
    sheets = session.exec(select(SheetDocument).where(SheetDocument.id.in_(sheet_ids))).all()
    sheet_map = {sheet.id: sheet for sheet in sheets}
    items: list[dict[str, Any]] = []
    for link in links:
        sheet = sheet_map.get(link.sheet_id)
        if sheet is None or sheet.owner_type != FREEBILL_SHEET_OWNER_TYPE:
            continue
        document_json = dict(sheet.document_json or {})
        rows = document_json.get("rows")
        items.append({
            "key": sheet.sheet_key,
            "title": sheet.title,
            "sheet_id": _require_sheet_numeric_id(sheet),
            "row_count": len(rows) if isinstance(rows, list) else 0,
            "updated_at": float(sheet.updated_at or 0.0),
        })
    return items


def _serialize_freebill_workbook_payload(
    workbook: WorkbookDocument,
    sheet_items: list[dict[str, Any]],
    *,
    refreshed_at: float,
) -> dict[str, Any]:
    return {
        "workbook": {
            "id": _require_workbook_numeric_id(workbook),
            "title": workbook.title,
            "updated_at": float(workbook.updated_at or 0.0),
        },
        "sheets": sheet_items,
        "refreshed_at": refreshed_at,
    }


def _collect_paginated(fetch_page: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    limit = 1000
    offset = 0
    items: list[dict[str, Any]] = []
    total: int | None = None
    while True:
        page = fetch_page(limit=limit, offset=offset)
        page_items = list(page.get("items") or [])
        items.extend(page_items)
        total = int(page.get("total") or len(items))
        if len(items) >= total or len(page_items) < limit:
            return items
        offset += limit


def _get_or_create_freebill_workbook(
    session: Session,
    *,
    user_id: int,
    actor_user_id: int | None,
    now: float,
) -> WorkbookDocument:
    existing_workbook = _find_existing_freebill_workbook(session, user_id=user_id)
    if existing_workbook is not None:
        return existing_workbook

    workbook = WorkbookDocument(
        numeric_id=_get_next_workbook_numeric_id(session),
        title=FREEBILL_WORKBOOK_TITLE,
        owner_user_id=user_id,
        created_by_user_id=actor_user_id or user_id,
        updated_by_user_id=actor_user_id or user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(workbook)
    session.flush()
    return workbook


def _find_existing_freebill_workbook(session: Session, *, user_id: int) -> WorkbookDocument | None:
    owner_key = str(user_id)
    sheets = session.exec(
        select(SheetDocument)
        .where(SheetDocument.scope == "notes")
        .where(SheetDocument.owner_type == FREEBILL_SHEET_OWNER_TYPE)
        .where(SheetDocument.owner_key == owner_key)
    ).all()
    sheet_ids = [sheet.id for sheet in sheets]
    if sheet_ids:
        links = session.exec(
            select(WorkbookSheetLink)
            .where(WorkbookSheetLink.sheet_id.in_(sheet_ids))
            .order_by(WorkbookSheetLink.created_at)
        ).all()
        for link in links:
            workbook = session.get(WorkbookDocument, link.workbook_id)
            if workbook is not None and workbook.owner_user_id == user_id:
                return workbook

    return session.exec(
        select(WorkbookDocument)
        .where(WorkbookDocument.owner_user_id == user_id)
        .where(WorkbookDocument.title == FREEBILL_WORKBOOK_TITLE)
        .order_by(WorkbookDocument.created_at)
    ).first()


def _upsert_freebill_sheet(
    session: Session,
    *,
    owner_key: str,
    sheet_key: str,
    title: str,
    document_json: dict[str, Any],
    user_id: int,
    actor_user_id: int | None,
    now: float,
) -> SheetDocument:
    sheet = _get_freebill_sheet(session, owner_key=owner_key, sheet_key=sheet_key)
    if sheet is None:
        sheet = SheetDocument(
            numeric_id=_get_next_sheet_numeric_id(session),
            scope="notes",
            owner_type=FREEBILL_SHEET_OWNER_TYPE,
            owner_key=owner_key,
            sheet_key=sheet_key,
            title=title,
            engine="handsontable",
            document_json=document_json,
            version=1,
            owner_user_id=user_id,
            created_by_user_id=actor_user_id or user_id,
            updated_by_user_id=actor_user_id or user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(sheet)
        session.flush()
        return sheet

    if sheet.title != title or dict(sheet.document_json or {}) != document_json:
        sheet.title = title
        sheet.document_json = document_json
        sheet.version = max(int(sheet.version or 1), 1) + 1
        sheet.updated_by_user_id = actor_user_id or user_id
        sheet.updated_at = now
        session.add(sheet)
        session.flush()
    return sheet


def _get_freebill_sheet(session: Session, *, owner_key: str, sheet_key: str) -> SheetDocument | None:
    return session.exec(
        select(SheetDocument)
        .where(SheetDocument.scope == "notes")
        .where(SheetDocument.owner_type == FREEBILL_SHEET_OWNER_TYPE)
        .where(SheetDocument.owner_key == owner_key)
        .where(SheetDocument.sheet_key == sheet_key)
    ).first()


def _ensure_workbook_link(
    session: Session,
    *,
    workbook: WorkbookDocument,
    sheet: SheetDocument,
    order_index: int,
    now: float,
) -> None:
    link = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == workbook.id)
        .where(WorkbookSheetLink.sheet_id == sheet.id)
    ).first()
    if link is None:
        session.add(
            WorkbookSheetLink(
                workbook_id=workbook.id,
                sheet_id=sheet.id,
                order_index=order_index,
                created_at=now,
            ),
        )
        workbook.updated_by_user_id = sheet.updated_by_user_id
        workbook.updated_at = now
        return

    if link.order_index != order_index:
        link.order_index = order_index
        session.add(link)
        workbook.updated_by_user_id = sheet.updated_by_user_id
        workbook.updated_at = now


def _build_sheet_document(columns: list[FreebillColumn], rows: list[dict[str, Any]], *, sheet_key: str) -> dict[str, Any]:
    headers = [column["label"] for column in columns]
    sheet_rows = [[_get_cell_text(row, column, sheet_key=sheet_key) for column in columns] for row in rows]
    cell_meta: dict[str, dict[str, Any]] = {}
    for row_index, row in enumerate(rows):
        for column_index, column in enumerate(columns):
            style = _get_cell_style(row, column["key"], sheet_key=sheet_key)
            if style:
                cell_meta[f"{row_index + 1}:{column_index}"] = {"style": style}

    return {
        "schema_version": 1,
        "columns": headers,
        "rows": sheet_rows,
        "grid_rows": [headers, *sheet_rows],
        "data_start_row": 1,
        "field_row_index": 0,
        "formula_reference_origin": "sheet_v2",
        "merged_cells": [],
        "header_groups": [],
        "cell_meta": cell_meta,
        "column_configs": _build_column_configs(columns),
        "column_widths": [
            _get_column_width(column, sheet_rows, column_index)
            for column_index, column in enumerate(columns)
        ],
        "view_settings": {
            "show_row_numbers": True,
            "row_marker_numbering": "global",
            "row_marker_origin": "sheet",
            "show_column_markers": True,
            "column_marker_style": "letters",
            "frozen_column_count": 0,
            "pagination": {
                "enabled": True,
                "page_size": FREEBILL_PAGE_SIZE,
            },
        },
    }


def _build_column_configs(columns: list[FreebillColumn]) -> dict[str, dict[str, Any]]:
    configs: dict[str, dict[str, Any]] = {}
    for column in columns:
        config: dict[str, Any] = {
            "filter_enabled": True,
            "width_mode": "fixed",
        }
        if column.get("align"):
            config["align"] = column["align"]
        if column.get("value_type"):
            config["value_type"] = column["value_type"]
        if column.get("value_mode"):
            config["value_mode"] = column["value_mode"]
        if column.get("display_format"):
            config["display_format"] = column["display_format"]
        if column.get("default_visible") is False:
            config["hidden"] = True
        configs[column["label"]] = config
    return configs


def _get_cell_text(row: dict[str, Any], column: FreebillColumn, *, sheet_key: str) -> str:
    key = column["key"]
    value = row.get(key)
    if key in {"amount", "income", "expense", "balance", "value"}:
        return _format_money(value)
    if key == "size_bytes":
        return _format_file_size(value)
    if key in {"modified_at", "first_seen_at", "last_seen_at", "imported_at"}:
        return _format_timestamp(value)
    if key == "relative_path":
        return str(row.get("relative_path") or row.get("original_name") or "")
    if key == "sha256":
        return str(value or "")[:16]
    return str(value or "")


def _get_cell_style(row: dict[str, Any], key: str, *, sheet_key: str) -> dict[str, str] | None:
    if key == "direction":
        direction = str(row.get("direction") or "")
        if direction == "收入":
            return {"text_color": INCOME_TEXT_COLOR}
        if direction == "支出":
            return {"text_color": EXPENSE_TEXT_COLOR}
    if key == "amount":
        direction = str(row.get("direction") or "")
        if direction == "收入":
            return {"text_color": INCOME_TEXT_COLOR}
        if direction == "支出":
            return {"text_color": EXPENSE_TEXT_COLOR}
    if key in {"income"}:
        return {"text_color": INCOME_TEXT_COLOR}
    if key in {"expense"}:
        return {"text_color": EXPENSE_TEXT_COLOR}
    if key == "balance":
        balance = float(row.get("balance") or 0)
        return {"text_color": EXPENSE_TEXT_COLOR if balance < 0 else INCOME_TEXT_COLOR}
    if key == "sha256":
        return {"text_color": MUTED_TEXT_COLOR}
    return None


def _format_money(value: Any) -> str:
    try:
        number_value = float(value or 0)
    except (TypeError, ValueError):
        return str(value or "")
    normalized = 0 if number_value == 0 else number_value
    return f"{normalized:.2f}"


def _format_file_size(value: Any) -> str:
    try:
        size = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.2f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _format_timestamp(value: Any) -> str:
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _get_column_width(column: FreebillColumn, rows: list[list[str]], column_index: int) -> int:
    if column.get("width"):
        return int(column["width"])
    max_text_width = max(
        [_visual_text_width(column["label"]), *[_visual_text_width(row[column_index] if column_index < len(row) else "") for row in rows]],
    )
    measured_width = int(max_text_width * 7 + (34 if column.get("align") == "right" else 28))
    return min(max(measured_width, int(column.get("min_width") or 72)), int(column.get("max_width") or 220))


def _visual_text_width(value: Any) -> int:
    width = 0
    for char in str(value or ""):
        width += 2 if "\u3000" <= char <= "\uffef" or "\u4e00" <= char <= "\u9fff" else 1
    return width


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
        raise RuntimeError("Freebill 表格编号缺失")
    return numeric_id


def _require_workbook_numeric_id(workbook: WorkbookDocument) -> int:
    numeric_id = int(workbook.numeric_id or 0)
    if numeric_id <= 0:
        raise RuntimeError("Freebill 工作簿编号缺失")
    return numeric_id
