from __future__ import annotations

import argparse
import calendar
import copy
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time
from pathlib import Path
import re
import sys
import time
from typing import Any

from sqlmodel import Session, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api.note_sheets import (
    _extract_document_grid_rows,
    _extract_document_rows,
    _normalize_document_columns,
    _normalize_document_data_start_row,
    _normalize_sheet_row,
)
from backend.db import engine
from backend.models import SheetDocument


REGISTRATION_SHEET_NUMERIC_ID = 20

TRACKING_GROUP_COLUMN = "追踪分组"
TRACKING_STATUS_COLUMN = "追踪状态"
TRACKING_DEADLINE_COLUMN = "追踪截止日"
FROZEN_AT_COLUMN = "冻结时间"
MIGRATION_COLUMNS = [
    TRACKING_GROUP_COLUMN,
    TRACKING_STATUS_COLUMN,
    TRACKING_DEADLINE_COLUMN,
    FROZEN_AT_COLUMN,
]

SUBMITTED_AT_COLUMN = "提交时间"
TRACKING_GROUP_ACTIVE = "B组"
TRACKING_GROUP_FROZEN = "A组"
TRACKING_STATUS_ACTIVE = "追踪中"
TRACKING_STATUS_FROZEN = "已冻结"
FROZEN_BACKGROUND_COLOR = "#F2F2F2"
FROZEN_TEXT_COLOR = "#6B7280"
TRACKING_HEADER_BACKGROUND_COLOR = "#E5E7EB"


@dataclass
class RegistrationRow:
    source_row_index: int
    submitted_at: datetime
    tracking_group: str
    tracking_status: str
    tracking_deadline: date
    frozen_at: str
    target_row_index: int = -1

    @property
    def is_frozen(self) -> bool:
        return self.tracking_status == TRACKING_STATUS_FROZEN


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, max_day))


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime_time.min)

    text = _normalize_text(value)
    if not text:
        return None
    normalized = text.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.replace("/", "-").replace(".", "-")
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:\D+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?", normalized)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    second = int(match.group(6) or 0)
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def _require_sheet(session: Session, numeric_id: int) -> SheetDocument:
    sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == numeric_id)).first()
    if sheet is None:
        raise RuntimeError(f"找不到表格 numeric_id={numeric_id}")
    return sheet


def _enable_pagination(document: dict[str, Any], page_size: int = 50) -> dict[str, Any]:
    next_document = copy.deepcopy(document)
    view_settings = dict(next_document.get("view_settings") or {})
    pagination = dict(view_settings.get("pagination") or {})
    pagination["enabled"] = True
    pagination["page_size"] = page_size
    view_settings["pagination"] = pagination
    next_document["view_settings"] = view_settings
    return next_document


def _append_meta(cell_meta: dict[str, Any], key: str, patch: dict[str, Any]) -> None:
    current = dict(cell_meta.get(key) or {})
    for field, value in patch.items():
        if field == "style" and isinstance(value, dict):
            current["style"] = {**dict(current.get("style") or {}), **value}
        else:
            current[field] = value
    cell_meta[key] = current


def _extend_prefix_rows(
    *,
    document: dict[str, Any],
    columns: list[str],
    final_columns: list[str],
    data_start_row: int,
) -> list[list[Any]]:
    grid_rows = _extract_document_grid_rows(document)
    prefix_rows = grid_rows[:data_start_row] if grid_rows else []
    normalized_prefix_rows = [_normalize_sheet_row(row, len(columns)) for row in prefix_rows]
    while len(normalized_prefix_rows) < data_start_row:
        normalized_prefix_rows.append([""] * len(columns))

    extra_columns = [column for column in MIGRATION_COLUMNS if column not in columns]
    for row_index, row in enumerate(normalized_prefix_rows):
        if not extra_columns:
            normalized_prefix_rows[row_index] = row[:len(final_columns)]
            continue
        extra = [""] * len(extra_columns)
        if row_index == 0:
            extra = extra_columns[:]
        elif row_index == 1:
            notes_by_column = {
                TRACKING_GROUP_COLUMN: "B组为近2个月追踪中，A组为已冻结归档。",
                TRACKING_STATUS_COLUMN: "超过追踪截止日后转为静态结果。",
                TRACKING_DEADLINE_COLUMN: "提交时间顺延2个月。",
                FROZEN_AT_COLUMN: "冻结迁移执行时间。",
            }
            extra = [notes_by_column.get(column, "") for column in extra_columns]
        normalized_prefix_rows[row_index] = [*row, *extra]
    return normalized_prefix_rows


def _extend_column_configs(document: dict[str, Any], final_columns: list[str]) -> dict[str, Any]:
    configs = copy.deepcopy(document.get("column_configs") or {})
    if not isinstance(configs, dict):
        configs = {}
    for column in final_columns:
        configs.setdefault(column, {})
    for column in MIGRATION_COLUMNS:
        configs[column] = {
            **dict(configs.get(column) or {}),
            "header_background_color": TRACKING_HEADER_BACKGROUND_COLOR,
            "hidden": True,
        }
    return configs


def _extend_column_widths(document: dict[str, Any], final_columns: list[str]) -> list[int]:
    widths = list(document.get("column_widths") or [])
    while len(widths) < len(final_columns):
        widths.append(112)
    return widths[:len(final_columns)]


def _extend_header_groups(document: dict[str, Any], final_columns: list[str]) -> list[Any]:
    groups = copy.deepcopy(document.get("header_groups") or [])
    if not isinstance(groups, list):
        return groups
    if not groups or not isinstance(groups[0], list):
        return groups
    current_span = 0
    for item in groups[0]:
        if isinstance(item, dict):
            current_span += int(item.get("colspan") or 1)
    extra_count = len(final_columns) - current_span
    if extra_count > 0:
        groups[0].append({"label": "追踪归档", "colspan": extra_count})
    return groups


def _build_registration_rows(
    rows: list[Any],
    *,
    columns: list[str],
    today: date,
    frozen_at: str,
) -> list[RegistrationRow]:
    submitted_at_index = columns.index(SUBMITTED_AT_COLUMN)
    cutoff_date = _add_months(today, -2)
    result: list[RegistrationRow] = []
    for source_row_index, row in enumerate(rows):
        values = _normalize_sheet_row(row, len(columns))
        submitted_at = _parse_datetime(values[submitted_at_index])
        if submitted_at is None:
            continue
        is_active = submitted_at.date() >= cutoff_date
        result.append(RegistrationRow(
            source_row_index=source_row_index,
            submitted_at=submitted_at,
            tracking_group=TRACKING_GROUP_ACTIVE if is_active else TRACKING_GROUP_FROZEN,
            tracking_status=TRACKING_STATUS_ACTIVE if is_active else TRACKING_STATUS_FROZEN,
            tracking_deadline=_add_months(submitted_at.date(), 2),
            frozen_at="" if is_active else frozen_at,
        ))
    return result


def _sort_registration_rows(rows: list[RegistrationRow]) -> list[RegistrationRow]:
    return sorted(
        rows,
        key=lambda item: (
            0 if item.tracking_group == TRACKING_GROUP_ACTIVE else 1,
            item.submitted_at.timestamp() if item.tracking_group == TRACKING_GROUP_ACTIVE else -item.submitted_at.timestamp(),
            item.source_row_index,
        ),
    )


def _build_final_rows(
    source_rows: list[Any],
    *,
    source_columns: list[str],
    final_columns: list[str],
    sorted_rows: list[RegistrationRow],
) -> list[list[Any]]:
    normalized_source_rows = [_normalize_sheet_row(row, len(source_columns)) for row in source_rows]
    source_maps = [dict(zip(source_columns, row)) for row in normalized_source_rows]
    final_rows: list[list[Any]] = []
    for target_index, item in enumerate(sorted_rows):
        item.target_row_index = target_index
        source_map = source_maps[item.source_row_index]
        row = [source_map.get(column, "") for column in final_columns]
        row[final_columns.index(TRACKING_GROUP_COLUMN)] = item.tracking_group
        row[final_columns.index(TRACKING_STATUS_COLUMN)] = item.tracking_status
        row[final_columns.index(TRACKING_DEADLINE_COLUMN)] = item.tracking_deadline.isoformat()
        row[final_columns.index(FROZEN_AT_COLUMN)] = item.frozen_at
        final_rows.append(row)
    return final_rows


def _remap_cell_meta(
    *,
    document: dict[str, Any],
    sorted_rows: list[RegistrationRow],
    source_column_count: int,
    final_columns: list[str],
    data_start_row: int,
) -> dict[str, Any]:
    row_map = {
        item.source_row_index: item.target_row_index
        for item in sorted_rows
    }
    result: dict[str, Any] = {}
    raw_meta = document.get("cell_meta")
    if isinstance(raw_meta, dict):
        for key, value in raw_meta.items():
            row_text, _sep, column_text = str(key).partition(":")
            try:
                row_index = int(row_text)
                column_index = int(column_text)
            except ValueError:
                continue
            if column_index < 0 or column_index >= source_column_count:
                continue
            if row_index < data_start_row:
                result[f"{row_index}:{column_index}"] = copy.deepcopy(value)
                continue
            source_data_index = row_index - data_start_row
            target_data_index = row_map.get(source_data_index)
            if target_data_index is None:
                continue
            result[f"{data_start_row + target_data_index}:{column_index}"] = copy.deepcopy(value)

    for column in MIGRATION_COLUMNS:
        if column not in final_columns:
            continue
        column_index = final_columns.index(column)
        _append_meta(result, f"0:{column_index}", {"style": {"background_color": TRACKING_HEADER_BACKGROUND_COLOR}})

    for item in sorted_rows:
        if not item.is_frozen:
            continue
        document_row = data_start_row + item.target_row_index
        for column_index in range(len(final_columns)):
            _append_meta(
                result,
                f"{document_row}:{column_index}",
                {"style": {"background_color": FROZEN_BACKGROUND_COLOR, "text_color": FROZEN_TEXT_COLOR}},
            )
    return result


def _build_document(document: dict[str, Any], *, today: date, frozen_at: str) -> tuple[dict[str, Any], list[RegistrationRow]]:
    source_columns = _normalize_document_columns(document)
    if SUBMITTED_AT_COLUMN not in source_columns:
        raise RuntimeError(f"报名表缺少字段：{SUBMITTED_AT_COLUMN}")
    source_rows = _extract_document_rows(document)
    final_columns = [*source_columns, *[column for column in MIGRATION_COLUMNS if column not in source_columns]]
    data_start_row = _normalize_document_data_start_row(document)
    sorted_rows = _sort_registration_rows(_build_registration_rows(
        source_rows,
        columns=source_columns,
        today=today,
        frozen_at=frozen_at,
    ))
    final_rows = _build_final_rows(
        source_rows,
        source_columns=source_columns,
        final_columns=final_columns,
        sorted_rows=sorted_rows,
    )
    prefix_rows = _extend_prefix_rows(
        document=document,
        columns=source_columns,
        final_columns=final_columns,
        data_start_row=data_start_row,
    )

    next_document = copy.deepcopy(document)
    next_document["columns"] = final_columns
    next_document["rows"] = final_rows
    next_document["grid_rows"] = [*prefix_rows, *final_rows]
    next_document["column_configs"] = _extend_column_configs(next_document, final_columns)
    next_document["column_widths"] = _extend_column_widths(next_document, final_columns)
    next_document["header_groups"] = _extend_header_groups(next_document, final_columns)
    next_document["cell_meta"] = _remap_cell_meta(
        document=document,
        sorted_rows=sorted_rows,
        source_column_count=len(source_columns),
        final_columns=final_columns,
        data_start_row=data_start_row,
    )
    next_document = _enable_pagination(next_document, page_size=50)
    return next_document, sorted_rows


def _summarize(rows: list[RegistrationRow], document: dict[str, Any], today: date) -> dict[str, Any]:
    active = [row for row in rows if not row.is_frozen]
    frozen = [row for row in rows if row.is_frozen]
    return {
        "today": today.isoformat(),
        "cutoff_date": _add_months(today, -2).isoformat(),
        "total": len(rows),
        "active": len(active),
        "frozen": len(frozen),
        "first_active_time": active[0].submitted_at.isoformat(sep=" ") if active else "",
        "last_active_time": active[-1].submitted_at.isoformat(sep=" ") if active else "",
        "first_frozen_time": frozen[0].submitted_at.isoformat(sep=" ") if frozen else "",
        "last_frozen_time": frozen[-1].submitted_at.isoformat(sep=" ") if frozen else "",
        "columns": len(document.get("columns") or []),
        "rows": len(document.get("rows") or []),
    }


def run(*, apply: bool, today: date | None = None) -> dict[str, Any]:
    run_today = today or date.today()
    frozen_at = datetime.now().isoformat(sep=" ", timespec="seconds")
    with Session(engine) as session:
        sheet = _require_sheet(session, REGISTRATION_SHEET_NUMERIC_ID)
        document = copy.deepcopy(dict(sheet.document_json or {}))
        next_document, sorted_rows = _build_document(document, today=run_today, frozen_at=frozen_at)
        summary = _summarize(sorted_rows, next_document, run_today)

        if apply:
            sheet.document_json = next_document
            sheet.version = int(sheet.version or 1) + 1
            sheet.updated_at = time.time()
            session.add(sheet)
            session.commit()
        else:
            session.rollback()
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="整理 20250106 念住闯关报名表 A/B 组排序。")
    parser.add_argument("--apply", action="store_true", help="实际写入数据库。默认只 dry-run。")
    parser.add_argument("--today", default="", help="按指定日期判断2个月窗口，格式 YYYY-MM-DD。")
    args = parser.parse_args()

    today = date.fromisoformat(args.today) if args.today else None
    summary = run(apply=args.apply, today=today)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] 整理念住闯关报名表")
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
