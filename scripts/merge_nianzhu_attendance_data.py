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
    _build_table_text_grid,
    _extract_document_grid_rows,
    _extract_document_rows,
    _normalize_document_columns,
    _normalize_document_data_start_row,
    _normalize_sheet_row,
    _remap_formula_value_references,
)
from backend.db import engine
from backend.models import SheetDocument, WorkbookDocument, WorkbookSheetLink
from scripts.apply_nianzhu_attendance_progress_styles import apply_nianzhu_course_progress_styles_to_document


WORKBOOK_NUMERIC_ID = 7
REGISTRATION_SHEET_NUMERIC_ID = 20
TARGET_ATTENDANCE_SHEET_NUMERIC_ID = 21
ATTENDANCE_SOURCE_SHEET_NUMERIC_IDS = (21, 22, 23)

TRACKING_GROUP_COLUMN = "追踪分组"
TRACKING_STATUS_COLUMN = "追踪状态"
TRACKING_DEADLINE_COLUMN = "追踪截止日"
FROZEN_AT_COLUMN = "冻结时间"
RULE_VERSION_COLUMN = "规则版本"
SOURCE_SHEET_COLUMN = "来源sheet"
MIGRATION_COLUMNS = [
    TRACKING_GROUP_COLUMN,
    TRACKING_STATUS_COLUMN,
    TRACKING_DEADLINE_COLUMN,
    FROZEN_AT_COLUMN,
    RULE_VERSION_COLUMN,
    SOURCE_SHEET_COLUMN,
]

TRACKING_GROUP_ACTIVE = "B组"
TRACKING_GROUP_FROZEN = "A组"
TRACKING_STATUS_ACTIVE = "追踪中"
TRACKING_STATUS_FROZEN = "已冻结"
FROZEN_BACKGROUND_COLOR = "#F2F2F2"
FROZEN_TEXT_COLOR = "#6B7280"
TRACKING_HEADER_BACKGROUND_COLOR = "#E5E7EB"

RULE_VERSION_BY_SHEET_TITLE = {
    "考勤表2025年5月22日以前": "旧规则-20250522前",
    "归档-考勤表2025年5月22日以前": "旧规则-20250522前",
    "考勤表2025年5月22日以后": "旧规则-20250522后",
    "归档-考勤表2025年5月22日以后": "旧规则-20250522后",
}


@dataclass
class SourceSheetData:
    sheet: SheetDocument
    document: dict[str, Any]
    columns: list[str]
    rows: list[Any]
    evaluated_rows: list[list[Any]]
    data_start_row: int


@dataclass
class MergedAttendanceRow:
    source: SourceSheetData
    source_row_index: int
    registration_datetime: datetime
    registration_date: date
    tracking_group: str
    tracking_status: str
    tracking_deadline: date
    frozen_at: str
    rule_version: str
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


def _parse_date(value: Any) -> date | None:
    parsed = _parse_datetime(value)
    return parsed.date() if parsed is not None else None


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


def _require_workbook(session: Session, numeric_id: int) -> WorkbookDocument:
    workbook = session.exec(select(WorkbookDocument).where(WorkbookDocument.numeric_id == numeric_id)).first()
    if workbook is None:
        raise RuntimeError(f"找不到工作簿 numeric_id={numeric_id}")
    return workbook


def _load_source_sheet(session: Session, numeric_id: int) -> SourceSheetData:
    sheet = _require_sheet(session, numeric_id)
    document = copy.deepcopy(dict(sheet.document_json or {}))
    columns = _normalize_document_columns(document)
    rows = _extract_document_rows(document)
    data_start_row = _normalize_document_data_start_row(document)
    text_grid = _build_table_text_grid(document, columns=columns, rows=rows)
    evaluated_rows = text_grid[data_start_row:data_start_row + len(rows)]
    return SourceSheetData(
        sheet=sheet,
        document=document,
        columns=columns,
        rows=rows,
        evaluated_rows=evaluated_rows,
        data_start_row=data_start_row,
    )


def _source_rule_version(source: SourceSheetData) -> str:
    return RULE_VERSION_BY_SHEET_TITLE.get(_canonical_sheet_title(source.sheet.title), "当前规则")


def _canonical_sheet_title(value: Any) -> str:
    title = _normalize_text(value)
    return title.removeprefix("归档-")


def _append_meta(cell_meta: dict[str, Any], key: str, patch: dict[str, Any]) -> None:
    current = dict(cell_meta.get(key) or {})
    for field, value in patch.items():
        if field == "style" and isinstance(value, dict):
            current["style"] = {**dict(current.get("style") or {}), **value}
        else:
            current[field] = value
    cell_meta[key] = current


def _preserve_header_cell_meta(document: dict[str, Any], data_start_row: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    raw_meta = document.get("cell_meta")
    if not isinstance(raw_meta, dict):
        return result
    for key, value in raw_meta.items():
        row_text = str(key).split(":", 1)[0]
        try:
            row_index = int(row_text)
        except ValueError:
            continue
        if row_index < data_start_row:
            result[str(key)] = copy.deepcopy(value)
    return result


def _enable_pagination(document: dict[str, Any], page_size: int = 50) -> dict[str, Any]:
    next_document = copy.deepcopy(document)
    view_settings = dict(next_document.get("view_settings") or {})
    pagination = dict(view_settings.get("pagination") or {})
    pagination["enabled"] = True
    pagination["page_size"] = page_size
    view_settings["pagination"] = pagination
    next_document["view_settings"] = view_settings
    return next_document


def _extend_prefix_rows(
    *,
    document: dict[str, Any],
    columns: list[str],
    final_columns: list[str],
    data_start_row: int,
) -> list[list[Any]]:
    grid_rows = _extract_document_grid_rows(document)
    prefix_rows = grid_rows[:data_start_row] if grid_rows else []
    normalized_prefix_rows = [
        _normalize_sheet_row(row, len(columns))
        for row in prefix_rows
    ]
    while len(normalized_prefix_rows) < data_start_row:
        normalized_prefix_rows.append([""] * len(columns))

    extra_count = len(final_columns) - len(columns)
    for row_index, row in enumerate(normalized_prefix_rows):
        if extra_count <= 0:
            normalized_prefix_rows[row_index] = row[:len(final_columns)]
            continue
        extra = [""] * extra_count
        if row_index == 0:
            extra[0] = "追踪归档"
        elif row_index == 1:
            extra = MIGRATION_COLUMNS[:extra_count]
        elif row_index == 2:
            notes_by_column = {
                TRACKING_GROUP_COLUMN: "B组为近2个月追踪中，A组为已冻结归档。",
                TRACKING_STATUS_COLUMN: "超过追踪截止日后转为静态结果。",
                TRACKING_DEADLINE_COLUMN: "报名日期顺延2个月。",
                FROZEN_AT_COLUMN: "冻结迁移执行时间。",
                RULE_VERSION_COLUMN: "保留原始表规则口径。",
                SOURCE_SHEET_COLUMN: "迁移前所在sheet。",
            }
            extra = [notes_by_column.get(column, "") for column in MIGRATION_COLUMNS[:extra_count]]
        normalized_prefix_rows[row_index] = [*row, *extra]

    return normalized_prefix_rows


def _extend_column_configs(
    document: dict[str, Any],
    final_columns: list[str],
) -> dict[str, Any]:
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
    if extra_count <= 0:
        return groups
    groups[0].append({"label": "追踪归档", "colspan": extra_count})
    return groups


def _extend_merged_cells(document: dict[str, Any], source_column_count: int, extra_count: int) -> list[Any]:
    merged_cells = copy.deepcopy(document.get("merged_cells") or [])
    if extra_count > 1:
        merged_cells.append({"row": 0, "col": source_column_count, "rowspan": 1, "colspan": extra_count})
    return merged_cells


def _column_index_map(source_columns: list[str], target_columns: list[str]) -> dict[int, int | None]:
    mapping: dict[int, int | None] = {}
    for index, column in enumerate(source_columns):
        mapping[index] = target_columns.index(column) if column in target_columns else None
    return mapping


def _build_source_row_mapping(source: SourceSheetData) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    for row_index, row in enumerate(source.rows):
        raw_values = _normalize_sheet_row(row, len(source.columns))
        evaluated_values = _normalize_sheet_row(source.evaluated_rows[row_index], len(source.columns))
        mappings.append({
            "raw": dict(zip(source.columns, raw_values)),
            "evaluated": dict(zip(source.columns, evaluated_values)),
        })
    return mappings


def _collect_attendance_rows(
    sources: list[SourceSheetData],
    *,
    today: date,
    frozen_at: str,
) -> list[MergedAttendanceRow]:
    cutoff_date = _add_months(today, -2)
    result: list[MergedAttendanceRow] = []
    for source in sources:
        registration_index = source.columns.index("报名日期") if "报名日期" in source.columns else -1
        if registration_index < 0:
            raise RuntimeError(f"{source.sheet.title} 缺少报名日期列")
        source_sheet_index = source.columns.index(SOURCE_SHEET_COLUMN) if SOURCE_SHEET_COLUMN in source.columns else -1
        for row_index, row in enumerate(source.rows):
            values = _normalize_sheet_row(row, len(source.columns))
            if source.sheet.numeric_id == TARGET_ATTENDANCE_SHEET_NUMERIC_ID and source_sheet_index >= 0:
                row_source_title = _canonical_sheet_title(values[source_sheet_index])
                if row_source_title and row_source_title != "考勤表":
                    continue
            registration_datetime = _parse_datetime(values[registration_index])
            if registration_datetime is None:
                continue
            registration_date = registration_datetime.date()
            is_active = registration_date >= cutoff_date
            result.append(MergedAttendanceRow(
                source=source,
                source_row_index=row_index,
                registration_datetime=registration_datetime,
                registration_date=registration_date,
                tracking_group=TRACKING_GROUP_ACTIVE if is_active else TRACKING_GROUP_FROZEN,
                tracking_status=TRACKING_STATUS_ACTIVE if is_active else TRACKING_STATUS_FROZEN,
                tracking_deadline=_add_months(registration_date, 2),
                frozen_at="" if is_active else frozen_at,
                rule_version=_source_rule_version(source),
            ))
    return result


def _sort_merged_rows(rows: list[MergedAttendanceRow]) -> list[MergedAttendanceRow]:
    return sorted(
        rows,
        key=lambda item: (
            0 if item.tracking_group == TRACKING_GROUP_ACTIVE else 1,
            item.registration_datetime.timestamp()
            if item.tracking_group == TRACKING_GROUP_ACTIVE
            else -item.registration_datetime.timestamp(),
            item.source.sheet.numeric_id or 0,
            item.source_row_index,
        ),
    )


def _build_final_rows(
    sorted_rows: list[MergedAttendanceRow],
    *,
    final_columns: list[str],
) -> list[list[Any]]:
    sources_by_id = {
        item.source.sheet.numeric_id: item.source
        for item in sorted_rows
    }
    source_row_mappings = {
        source.sheet.numeric_id: _build_source_row_mapping(source)
        for source in sources_by_id.values()
    }
    source_target_row_maps: dict[int | None, dict[int, int]] = {}
    for target_index, item in enumerate(sorted_rows):
        item.target_row_index = target_index
        source_target_row_maps.setdefault(item.source.sheet.numeric_id, {})[item.source_row_index] = target_index

    source_column_maps = {
        source.sheet.numeric_id: _column_index_map(source.columns, final_columns)
        for source in sources_by_id.values()
    }

    final_rows: list[list[Any]] = []
    for item in sorted_rows:
        mappings = source_row_mappings[item.source.sheet.numeric_id][item.source_row_index]
        source_values = mappings["evaluated" if item.is_frozen else "raw"]
        final_row = [source_values.get(column, "") for column in final_columns]
        final_row[final_columns.index(TRACKING_GROUP_COLUMN)] = item.tracking_group
        final_row[final_columns.index(TRACKING_STATUS_COLUMN)] = item.tracking_status
        final_row[final_columns.index(TRACKING_DEADLINE_COLUMN)] = item.tracking_deadline.isoformat()
        final_row[final_columns.index(FROZEN_AT_COLUMN)] = item.frozen_at
        final_row[final_columns.index(RULE_VERSION_COLUMN)] = item.rule_version
        final_row[final_columns.index(SOURCE_SHEET_COLUMN)] = _canonical_sheet_title(item.source.sheet.title)

        if not item.is_frozen:
            row_map = source_target_row_maps.get(item.source.sheet.numeric_id, {})
            column_map = source_column_maps.get(item.source.sheet.numeric_id, {})
            final_row = [
                _remap_formula_value_references(
                    value,
                    row_index_map=row_map,
                    column_index_map=column_map,
                    row_index_offset=item.source.data_start_row,
                )
                for value in final_row
            ]
        final_rows.append(final_row)
    return final_rows


def _build_cell_meta(
    *,
    base_document: dict[str, Any],
    sorted_rows: list[MergedAttendanceRow],
    final_columns: list[str],
    data_start_row: int,
) -> dict[str, Any]:
    cell_meta = _preserve_header_cell_meta(base_document, data_start_row)
    if "返款配置" in final_columns:
        frozen_style_end_column = final_columns.index("返款配置")
    elif "打卡数" in final_columns:
        frozen_style_end_column = final_columns.index("打卡数") - 1
    else:
        frozen_style_end_column = 15
    for column in MIGRATION_COLUMNS:
        if column not in final_columns:
            continue
        column_index = final_columns.index(column)
        _append_meta(cell_meta, f"0:{column_index}", {"style": {"background_color": TRACKING_HEADER_BACKGROUND_COLOR}})
        _append_meta(cell_meta, f"1:{column_index}", {"style": {"background_color": TRACKING_HEADER_BACKGROUND_COLOR}})

    for item in sorted_rows:
        if not item.is_frozen:
            continue
        document_row_index = data_start_row + item.target_row_index
        for column_index in range(max(0, min(frozen_style_end_column + 1, len(final_columns)))):
            _append_meta(
                cell_meta,
                f"{document_row_index}:{column_index}",
                {"style": {"background_color": FROZEN_BACKGROUND_COLOR, "text_color": FROZEN_TEXT_COLOR}},
            )
    return cell_meta


def _assert_frozen_rows_static(rows: list[list[Any]], sorted_rows: list[MergedAttendanceRow]) -> None:
    for row, item in zip(rows, sorted_rows):
        if not item.is_frozen:
            continue
        formula_cells = [value for value in row if isinstance(value, str) and value.startswith("=")]
        if formula_cells:
            raise RuntimeError(
                f"冻结行仍包含公式：source={item.source.sheet.title} row={item.source_row_index + 1} "
                f"formula={formula_cells[0]}"
            )


def _build_merged_attendance_document(
    *,
    base_source: SourceSheetData,
    sources: list[SourceSheetData],
    today: date,
    frozen_at: str,
) -> tuple[dict[str, Any], list[MergedAttendanceRow]]:
    source_columns = list(base_source.columns)
    final_columns = [*source_columns, *[column for column in MIGRATION_COLUMNS if column not in source_columns]]
    sorted_rows = _sort_merged_rows(_collect_attendance_rows(sources, today=today, frozen_at=frozen_at))
    final_rows = _build_final_rows(sorted_rows, final_columns=final_columns)
    _assert_frozen_rows_static(final_rows, sorted_rows)

    data_start_row = base_source.data_start_row
    prefix_rows = _extend_prefix_rows(
        document=base_source.document,
        columns=source_columns,
        final_columns=final_columns,
        data_start_row=data_start_row,
    )
    next_document = copy.deepcopy(base_source.document)
    next_document["columns"] = final_columns
    next_document["rows"] = final_rows
    next_document["grid_rows"] = [*prefix_rows, *final_rows]
    next_document["column_configs"] = _extend_column_configs(next_document, final_columns)
    next_document["column_widths"] = _extend_column_widths(next_document, final_columns)
    next_document["header_groups"] = _extend_header_groups(next_document, final_columns)
    next_document["merged_cells"] = _extend_merged_cells(
        next_document,
        source_column_count=len(source_columns),
        extra_count=len(final_columns) - len(source_columns),
    )
    next_document["cell_meta"] = _build_cell_meta(
        base_document=base_source.document,
        sorted_rows=sorted_rows,
        final_columns=final_columns,
        data_start_row=data_start_row,
    )
    next_document = _enable_pagination(next_document, page_size=50)
    next_document, _ = apply_nianzhu_course_progress_styles_to_document(next_document)
    return next_document, sorted_rows


def _update_sheet_document(session: Session, sheet: SheetDocument, document: dict[str, Any]) -> None:
    sheet.document_json = document
    sheet.version = int(sheet.version or 1) + 1
    sheet.updated_at = time.time()
    session.add(sheet)


def _rename_archive_sheets(session: Session) -> None:
    archive_titles = {
        22: "归档-考勤表2025年5月22日以后",
        23: "归档-考勤表2025年5月22日以前",
    }
    for numeric_id, title in archive_titles.items():
        sheet = _require_sheet(session, numeric_id)
        if sheet.title != title:
            sheet.title = title
            sheet.updated_at = time.time()
            sheet.version = int(sheet.version or 1) + 1
            session.add(sheet)


def _reorder_workbook_sheets(session: Session, workbook: WorkbookDocument) -> None:
    order_by_sheet_id = {
        TARGET_ATTENDANCE_SHEET_NUMERIC_ID: 10,
        REGISTRATION_SHEET_NUMERIC_ID: 20,
        22: 90,
        23: 100,
    }
    links = session.exec(select(WorkbookSheetLink).where(WorkbookSheetLink.workbook_id == workbook.id)).all()
    sheets_by_id = {
        sheet.id: sheet
        for sheet in session.exec(select(SheetDocument).where(SheetDocument.id.in_([link.sheet_id for link in links]))).all()
    }
    for link in links:
        sheet = sheets_by_id.get(link.sheet_id)
        if sheet is None:
            continue
        order_index = order_by_sheet_id.get(int(sheet.numeric_id or -1))
        if order_index is not None and link.order_index != order_index:
            link.order_index = order_index
            session.add(link)


def _summarize(sorted_rows: list[MergedAttendanceRow]) -> dict[str, Any]:
    active = [item for item in sorted_rows if not item.is_frozen]
    frozen = [item for item in sorted_rows if item.is_frozen]
    by_source: dict[str, int] = {}
    for item in sorted_rows:
        source_title = _canonical_sheet_title(item.source.sheet.title)
        by_source[source_title] = by_source.get(source_title, 0) + 1
    return {
        "total": len(sorted_rows),
        "active": len(active),
        "frozen": len(frozen),
        "first_active_date": active[0].registration_date.isoformat() if active else "",
        "last_active_date": active[-1].registration_date.isoformat() if active else "",
        "first_frozen_date": frozen[0].registration_date.isoformat() if frozen else "",
        "last_frozen_date": frozen[-1].registration_date.isoformat() if frozen else "",
        "by_source": by_source,
    }


def run(*, apply: bool, today: date | None = None) -> dict[str, Any]:
    run_today = today or date.today()
    frozen_at = datetime.now().isoformat(sep=" ", timespec="seconds")
    with Session(engine) as session:
        workbook = _require_workbook(session, WORKBOOK_NUMERIC_ID)
        sources = [_load_source_sheet(session, numeric_id) for numeric_id in ATTENDANCE_SOURCE_SHEET_NUMERIC_IDS]
        base_source = next(source for source in sources if source.sheet.numeric_id == TARGET_ATTENDANCE_SHEET_NUMERIC_ID)
        attendance_document, sorted_rows = _build_merged_attendance_document(
            base_source=base_source,
            sources=sources,
            today=run_today,
            frozen_at=frozen_at,
        )

        registration_sheet = _require_sheet(session, REGISTRATION_SHEET_NUMERIC_ID)
        registration_document = _enable_pagination(dict(registration_sheet.document_json or {}), page_size=50)

        summary = _summarize(sorted_rows)
        summary["today"] = run_today.isoformat()
        summary["cutoff_date"] = _add_months(run_today, -2).isoformat()
        summary["target_columns"] = len(attendance_document.get("columns") or [])
        summary["target_rows"] = len(attendance_document.get("rows") or [])
        summary["registration_rows"] = len(_extract_document_rows(registration_document))

        if apply:
            _update_sheet_document(session, base_source.sheet, attendance_document)
            _update_sheet_document(session, registration_sheet, registration_document)
            _rename_archive_sheets(session)
            _reorder_workbook_sheets(session, workbook)
            workbook.updated_at = time.time()
            session.add(workbook)
            session.commit()
        else:
            session.rollback()

        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="合并 20250106 念住闯关考勤表，并冻结2个月前数据。")
    parser.add_argument("--apply", action="store_true", help="实际写入数据库。默认只 dry-run。")
    parser.add_argument("--today", default="", help="按指定日期判断2个月窗口，格式 YYYY-MM-DD。")
    args = parser.parse_args()

    today = date.fromisoformat(args.today) if args.today else None
    summary = run(apply=args.apply, today=today)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] 20250106念住闯关考勤合并")
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
