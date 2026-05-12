from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable, TypedDict

from sqlmodel import Session, select

from backend.models import SheetDocument, WorkbookDocument, WorkbookSheetLink

from .eastmoney_sync import (
    list_fund_flow_records,
    list_latest_position_snapshots,
    list_sync_runs,
    list_trade_records,
)


EASTMONEY_SHEET_OWNER_TYPE = "eastmoney"
EASTMONEY_WORKBOOK_TITLE = "东方财富"
EASTMONEY_PAGE_SIZE = 50
NEGATIVE_MONEY_TEXT_COLOR = "#15803d"
WARNING_TEXT_COLOR = "#b45309"
DANGER_TEXT_COLOR = "#b91c1c"

SIGNED_TRADE_MONEY_KEYS = {"occurrence_amount", "amount"}
TRADE_FEE_KEYS = {"fee", "commission", "stamp_tax", "transfer_fee", "other_fee"}
OPERATION_FEE_KEYS = {"fee", "stamp_tax", "transfer_fee"}


class EastmoneyColumn(TypedDict, total=False):
    key: str
    prop: str
    label: str
    min_width: int
    max_width: int
    width: int
    align: str
    default_visible: bool


OPERATION_RECORD_COLUMNS: list[EastmoneyColumn] = [
    {"key": "flow_date", "prop": "flow_date", "label": "日期", "min_width": 74, "max_width": 104},
    {"key": "flow_category", "prop": "flow_category", "label": "操作", "min_width": 76, "max_width": 112},
    {"key": "security_code", "prop": "security_code", "label": "代码", "min_width": 64, "max_width": 92},
    {"key": "security_name", "prop": "security_name", "label": "名称", "min_width": 64, "max_width": 150},
    {"key": "quantity", "prop": "quantity", "label": "数量", "min_width": 64, "max_width": 96, "align": "right"},
    {"key": "price", "prop": "price", "label": "价格", "min_width": 58, "max_width": 88, "align": "right"},
    {"key": "occurrence_amount", "prop": "occurrence_amount", "label": "发生金额", "min_width": 82, "max_width": 122, "align": "right"},
    {"key": "fee", "prop": "fee", "label": "手续费", "min_width": 64, "max_width": 92, "align": "right"},
    {"key": "stamp_tax", "prop": "stamp_tax", "label": "印花税", "min_width": 64, "max_width": 92, "align": "right"},
    {"key": "transfer_fee", "prop": "transfer_fee", "label": "过户费", "min_width": 64, "max_width": 92, "align": "right"},
    {"key": "fund_balance", "prop": "fund_balance", "label": "资金余额", "min_width": 82, "max_width": 122, "align": "right"},
    {"key": "source", "prop": "source", "label": "来源", "min_width": 82, "max_width": 120},
    {"key": "last_seen_at", "prop": "last_seen_at", "label": "入库时间", "min_width": 118, "max_width": 170},
]

TRADE_RECORD_COLUMNS: list[EastmoneyColumn] = [
    {"key": "trade_date", "prop": "trade_date", "label": "日期", "min_width": 74, "max_width": 104},
    {"key": "trade_time", "prop": "trade_time", "label": "时间", "min_width": 68, "max_width": 94},
    {"key": "security_code", "prop": "security_code", "label": "代码", "min_width": 64, "max_width": 92},
    {"key": "security_name", "prop": "security_name", "label": "名称", "min_width": 64, "max_width": 150},
    {"key": "direction", "prop": "direction", "label": "方向", "min_width": 58, "max_width": 82},
    {"key": "quantity", "prop": "quantity", "label": "数量", "min_width": 64, "max_width": 96, "align": "right"},
    {"key": "price", "prop": "price", "label": "价格", "min_width": 58, "max_width": 88, "align": "right"},
    {"key": "occurrence_amount", "prop": "occurrence_amount", "label": "发生金额", "min_width": 82, "max_width": 122, "align": "right"},
    {"key": "amount", "prop": "amount", "label": "成交金额", "min_width": 82, "max_width": 122, "align": "right"},
    {"key": "fee", "prop": "fee", "label": "费用", "min_width": 58, "max_width": 84, "align": "right"},
    {"key": "commission", "prop": "commission", "label": "佣金", "min_width": 58, "max_width": 84, "align": "right", "default_visible": False},
    {"key": "stamp_tax", "prop": "stamp_tax", "label": "印花税", "min_width": 64, "max_width": 92, "align": "right", "default_visible": False},
    {"key": "transfer_fee", "prop": "transfer_fee", "label": "过户费", "min_width": 64, "max_width": 92, "align": "right", "default_visible": False},
    {"key": "other_fee", "prop": "other_fee", "label": "其他费用", "min_width": 76, "max_width": 104, "align": "right", "default_visible": False},
    {"key": "currency", "prop": "currency", "label": "币种", "min_width": 58, "max_width": 82},
    {"key": "source", "prop": "source", "label": "来源", "min_width": 72, "max_width": 106},
    {"key": "deal_id", "prop": "deal_id", "label": "成交编号", "min_width": 112, "max_width": 190},
    {"key": "occurrence_date", "prop": "occurrence_date", "label": "发生日期", "min_width": 74, "max_width": 104, "default_visible": False},
    {"key": "occurrence_time", "prop": "occurrence_time", "label": "发生时间", "min_width": 68, "max_width": 94, "default_visible": False},
    {"key": "shareholder_account", "prop": "shareholder_account", "label": "股东账号", "min_width": 96, "max_width": 136, "default_visible": False},
    {"key": "share_balance", "prop": "share_balance", "label": "股份余额", "min_width": 82, "max_width": 118, "align": "right", "default_visible": False},
    {"key": "fund_balance", "prop": "fund_balance", "label": "资金余额", "min_width": 82, "max_width": 122, "align": "right", "default_visible": False},
    {"key": "extended_name", "prop": "extended_name", "label": "扩位简称", "min_width": 96, "max_width": 160, "default_visible": False},
    {"key": "last_seen_at", "prop": "last_seen_at", "label": "入库时间", "min_width": 118, "max_width": 170},
]

POSITION_RECORD_COLUMNS: list[EastmoneyColumn] = [
    {"key": "market", "prop": "market", "label": "市场", "min_width": 58, "max_width": 88},
    {"key": "security_code", "prop": "security_code", "label": "代码", "min_width": 64, "max_width": 92},
    {"key": "security_name", "prop": "security_name", "label": "名称", "min_width": 64, "max_width": 150},
    {"key": "quantity", "prop": "quantity", "label": "持仓数量", "min_width": 78, "max_width": 110, "align": "right"},
    {"key": "available_quantity", "prop": "available_quantity", "label": "可用数量", "min_width": 78, "max_width": 110, "align": "right"},
    {"key": "cost_price", "prop": "cost_price", "label": "成本价", "min_width": 64, "max_width": 92, "align": "right"},
    {"key": "current_price", "prop": "current_price", "label": "现价", "min_width": 58, "max_width": 88, "align": "right"},
    {"key": "market_value", "prop": "market_value", "label": "市值", "min_width": 70, "max_width": 110, "align": "right"},
    {"key": "pnl", "prop": "pnl", "label": "盈亏", "min_width": 70, "max_width": 110, "align": "right"},
    {"key": "pnl_ratio", "prop": "pnl_ratio", "label": "盈亏比例", "min_width": 76, "max_width": 106, "align": "right"},
    {"key": "currency", "prop": "currency", "label": "币种", "min_width": 58, "max_width": 82},
    {"key": "source", "prop": "source", "label": "来源", "min_width": 86, "max_width": 128},
    {"key": "captured_at", "prop": "captured_at", "label": "快照时间", "min_width": 118, "max_width": 170},
]

SYNC_RUN_COLUMNS: list[EastmoneyColumn] = [
    {"key": "status", "label": "状态", "min_width": 70, "max_width": 92},
    {"key": "date_range", "label": "日期范围", "min_width": 156, "max_width": 190},
    {"key": "account_label", "prop": "account_label", "label": "账户", "min_width": 118, "max_width": 180},
    {"key": "inserted_count", "prop": "inserted_count", "label": "新增", "min_width": 58, "max_width": 82, "align": "right"},
    {"key": "updated_count", "prop": "updated_count", "label": "更新", "min_width": 58, "max_width": 82, "align": "right"},
    {"key": "position_count", "prop": "position_count", "label": "持仓快照", "min_width": 78, "max_width": 106, "align": "right"},
    {"key": "started_at", "prop": "started_at", "label": "开始时间", "min_width": 118, "max_width": 160},
    {"key": "error_message", "prop": "error_message", "label": "错误", "min_width": 80, "max_width": 220},
]

SHEET_SPECS = [
    ("operation-history", "操作明细", OPERATION_RECORD_COLUMNS),
    ("local-history", "成交明细", TRADE_RECORD_COLUMNS),
    ("positions", "持仓", POSITION_RECORD_COLUMNS),
    ("sync-runs", "同步记录", SYNC_RUN_COLUMNS),
]


def refresh_eastmoney_sheet_workbook(
    session: Session,
    *,
    user_id: int,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    owner_key = str(user_id)
    now = time.time()
    workbook = _get_or_create_eastmoney_workbook(session, user_id=user_id, actor_user_id=actor_user_id, now=now)
    datasets = _build_eastmoney_sheet_datasets(session, user_id=user_id)
    sheet_items: list[dict[str, Any]] = []

    for order_index, (sheet_key, title, columns) in enumerate(SHEET_SPECS):
        rows = datasets[sheet_key]
        document_json = _build_sheet_document(columns, rows, sheet_key=sheet_key)
        sheet = _upsert_eastmoney_sheet(
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
        sheet = _get_eastmoney_sheet(session, owner_key=owner_key, sheet_key=item["key"])
        if sheet is not None:
            item["updated_at"] = float(sheet.updated_at or 0.0)

    return {
        "workbook": {
            "id": _require_workbook_numeric_id(workbook),
            "title": workbook.title,
            "updated_at": float(workbook.updated_at or 0.0),
        },
        "sheets": sheet_items,
        "refreshed_at": now,
    }


def _build_eastmoney_sheet_datasets(session: Session, *, user_id: int) -> dict[str, list[dict[str, Any]]]:
    return {
        "operation-history": _collect_paginated(
            lambda *, limit, offset: list_fund_flow_records(
                session,
                user_id=user_id,
                limit=limit,
                offset=offset,
            ),
        ),
        "local-history": _collect_paginated(
            lambda *, limit, offset: list_trade_records(
                session,
                user_id=user_id,
                limit=limit,
                offset=offset,
            ),
        ),
        "positions": list_latest_position_snapshots(session, user_id=user_id)["items"],
        "sync-runs": list_sync_runs(session, user_id=user_id, limit=100),
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


def _get_or_create_eastmoney_workbook(
    session: Session,
    *,
    user_id: int,
    actor_user_id: int | None,
    now: float,
) -> WorkbookDocument:
    existing_workbook = _find_existing_eastmoney_workbook(session, user_id=user_id)
    if existing_workbook is not None:
        return existing_workbook

    workbook = WorkbookDocument(
        numeric_id=_get_next_workbook_numeric_id(session),
        title=EASTMONEY_WORKBOOK_TITLE,
        owner_user_id=user_id,
        created_by_user_id=actor_user_id or user_id,
        updated_by_user_id=actor_user_id or user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(workbook)
    session.flush()
    return workbook


def _find_existing_eastmoney_workbook(session: Session, *, user_id: int) -> WorkbookDocument | None:
    owner_key = str(user_id)
    sheets = session.exec(
        select(SheetDocument)
        .where(SheetDocument.scope == "notes")
        .where(SheetDocument.owner_type == EASTMONEY_SHEET_OWNER_TYPE)
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
        .where(WorkbookDocument.title == EASTMONEY_WORKBOOK_TITLE)
        .order_by(WorkbookDocument.created_at)
    ).first()


def _upsert_eastmoney_sheet(
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
    sheet = _get_eastmoney_sheet(session, owner_key=owner_key, sheet_key=sheet_key)
    if sheet is None:
        sheet = SheetDocument(
            numeric_id=_get_next_sheet_numeric_id(session),
            scope="notes",
            owner_type=EASTMONEY_SHEET_OWNER_TYPE,
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


def _get_eastmoney_sheet(session: Session, *, owner_key: str, sheet_key: str) -> SheetDocument | None:
    return session.exec(
        select(SheetDocument)
        .where(SheetDocument.scope == "notes")
        .where(SheetDocument.owner_type == EASTMONEY_SHEET_OWNER_TYPE)
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


def _build_sheet_document(columns: list[EastmoneyColumn], rows: list[dict[str, Any]], *, sheet_key: str) -> dict[str, Any]:
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
                "page_size": EASTMONEY_PAGE_SIZE,
            },
        },
    }


def _build_column_configs(columns: list[EastmoneyColumn]) -> dict[str, dict[str, Any]]:
    configs: dict[str, dict[str, Any]] = {}
    for column in columns:
        config: dict[str, Any] = {
            "filter_enabled": True,
            "width_mode": "fixed",
        }
        if column.get("align"):
            config["align"] = column["align"]
        if column.get("default_visible") is False:
            config["hidden"] = True
        configs[column["label"]] = config
    return configs


def _get_cell_text(row: dict[str, Any], column: EastmoneyColumn, *, sheet_key: str) -> str:
    key = column["key"]
    if sheet_key == "operation-history":
        return _get_operation_cell_text(row, key)
    if sheet_key == "local-history":
        return _get_trade_cell_text(row, key)
    if sheet_key == "positions":
        return _get_position_cell_text(row, key)
    if sheet_key == "sync-runs":
        return _get_sync_run_cell_text(row, key)
    return str(row.get(column.get("prop") or key) or "")


def _get_operation_cell_text(row: dict[str, Any], key: str) -> str:
    if key == "source":
        return _source_label(row.get("source"))
    if key == "last_seen_at":
        return _format_time(row.get("last_seen_at"))
    if key == "occurrence_amount":
        return _format_smart_money(row.get("occurrence_amount"))
    if key in OPERATION_FEE_KEYS:
        return _format_signed_smart_money(row.get(key), -1)
    if key == "fund_balance":
        return _format_smart_money(row.get("fund_balance"))
    return str(row.get(key) or "")


def _get_trade_cell_text(row: dict[str, Any], key: str) -> str:
    if key == "source":
        return _source_label(row.get("source"))
    if key == "last_seen_at":
        return _format_time(row.get("last_seen_at"))
    if key == "quantity":
        return _format_signed_trade_text(row.get("quantity"), -1 if _is_sell_trade(row) else 1)
    if key == "occurrence_amount":
        return _format_signed_smart_money(row.get("occurrence_amount") or row.get("amount"), -1 if _is_buy_trade(row) else 1)
    if key in SIGNED_TRADE_MONEY_KEYS:
        return _format_signed_smart_money(row.get(key), -1 if _is_buy_trade(row) else 1)
    if key in TRADE_FEE_KEYS:
        return _format_signed_smart_money(row.get(key), -1)
    if key == "fund_balance":
        return _format_smart_money(row.get("fund_balance"))
    return str(row.get(key) or "")


def _get_position_cell_text(row: dict[str, Any], key: str) -> str:
    if key == "source":
        return _source_label(row.get("source"))
    if key == "captured_at":
        return _format_time(row.get("captured_at"))
    if key in {"market_value", "pnl"}:
        return _format_smart_money(row.get(key))
    return str(row.get(key) or "")


def _get_sync_run_cell_text(row: dict[str, Any], key: str) -> str:
    if key == "status":
        return _status_label(row.get("status"))
    if key == "date_range":
        return f"{row.get('start_date') or ''} 至 {row.get('end_date') or ''}"
    if key == "started_at":
        return _format_time(row.get("started_at"))
    return str(row.get(key) or "")


def _get_cell_style(row: dict[str, Any], key: str, *, sheet_key: str) -> dict[str, str] | None:
    text = ""
    if sheet_key == "operation-history" and key in {"occurrence_amount", *OPERATION_FEE_KEYS, "fund_balance"}:
        text = _get_operation_cell_text(row, key)
    elif sheet_key == "local-history" and key in {*SIGNED_TRADE_MONEY_KEYS, *TRADE_FEE_KEYS, "quantity", "fund_balance"}:
        text = _get_trade_cell_text(row, key)
    elif sheet_key == "positions" and key in {"pnl", "pnl_ratio"}:
        text = _get_position_cell_text(row, key)
    elif sheet_key == "sync-runs":
        if key == "error_message" and row.get("error_message"):
            return {"text_color": DANGER_TEXT_COLOR}
        if key == "status":
            status = str(row.get("status") or "")
            if status == "success":
                return {"text_color": NEGATIVE_MONEY_TEXT_COLOR}
            if status == "login_required":
                return {"text_color": WARNING_TEXT_COLOR}
            if status == "failed":
                return {"text_color": DANGER_TEXT_COLOR}
    return {"text_color": NEGATIVE_MONEY_TEXT_COLOR} if _parse_money_value(text) < 0 else None


def _is_buy_trade(row: dict[str, Any]) -> bool:
    return "买" in str(row.get("direction") or "")


def _is_sell_trade(row: dict[str, Any]) -> bool:
    return "卖" in str(row.get("direction") or "")


def _format_signed_trade_text(value: Any, sign: int) -> str:
    text = str(value or "").strip()
    number_value = _parse_money_value(text)
    if number_value != number_value or number_value == 0:
        return text
    unsigned_text = text.lstrip("+-")
    return f"-{unsigned_text}" if sign < 0 else unsigned_text


def _format_signed_smart_money(value: Any, sign: int) -> str:
    number_value = _parse_money_value(value)
    if number_value != number_value:
        return str(value or "").strip()
    return _format_smart_money_number(abs(number_value) * sign)


def _parse_money_value(value: Any) -> float:
    text = str(value or "").strip()
    if not text or text == "-":
        return float("nan")
    multiplier = 10000 if "万" in text else 1
    normalized = "".join(char for char in text.replace(",", "").replace("，", "") if char.isdigit() or char in ".+-")
    if not normalized or normalized in {"-", "+"}:
        return float("nan")
    try:
        return float(normalized) * multiplier
    except ValueError:
        return float("nan")


def _format_smart_money(value: Any) -> str:
    text = str(value or "").strip()
    number_value = _parse_money_value(text)
    if number_value != number_value:
        return text
    return _format_smart_money_number(number_value)


def _format_smart_money_number(value: float) -> str:
    if value != value:
        return ""
    normalized_value = 0 if value == 0 else value
    sign = "-" if normalized_value < 0 else ""
    abs_value = abs(normalized_value)
    if abs_value < 100:
        return f"{sign}{abs_value:.2f}"
    if abs_value < 10000:
        return f"{sign}{abs_value:.0f}"
    return f"{sign}{abs_value / 10000:.2f}万"


def _format_time(value: Any) -> str:
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    dt = datetime.fromtimestamp(timestamp)
    return f"{dt.year}/{dt.month}/{dt.day} {dt:%H:%M:%S}"


def _source_label(value: Any) -> str:
    source = str(value or "")
    return {
        "normal_history_deal": "普通交易",
        "hk_history_deal": "港股通",
        "mobile_trade_detail": "手机明细",
        "pdf_statement": "电子对账单",
        "pdf_statement_flow": "对账单成交",
        "normal_position": "普通持仓",
        "hk_position": "沪港通持仓",
        "sgt_position": "深港通持仓",
        "pdf_statement_position": "对账单持仓",
    }.get(source, source or "-")


def _status_label(value: Any) -> str:
    status = str(value or "")
    return {
        "success": "成功",
        "running": "运行中",
        "login_required": "需登录",
        "failed": "失败",
    }.get(status, status or "-")


def _get_column_width(column: EastmoneyColumn, rows: list[list[str]], column_index: int) -> int:
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
        raise RuntimeError("东方财富表格编号缺失")
    return numeric_id


def _require_workbook_numeric_id(workbook: WorkbookDocument) -> int:
    numeric_id = int(workbook.numeric_id or 0)
    if numeric_id <= 0:
        raise RuntimeError("东方财富工作簿编号缺失")
    return numeric_id
