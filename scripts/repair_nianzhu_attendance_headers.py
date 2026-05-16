from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
import time
from typing import Any

from openpyxl import load_workbook
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
from scripts.import_legacy_attendance_workbook import _attendance_document


DEFAULT_WORKBOOK_PATH = Path("C:/Users/kzche/Downloads/20250106念住闯关.xlsx")
ATTENDANCE_SHEET_NUMERIC_ID = 21
SOURCE_SHEET_TITLE = "考勤表"


def _copy_source_header_rows(
    document: dict[str, Any],
    source_document: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    columns = _normalize_document_columns(document)
    source_columns = _normalize_document_columns(source_document)
    data_start_row = _normalize_document_data_start_row(document)
    source_data_start_row = _normalize_document_data_start_row(source_document)
    grid_rows = _extract_document_grid_rows(document)
    source_grid_rows = _extract_document_grid_rows(source_document)
    rows = _extract_document_rows(document)

    next_grid_rows = [_normalize_sheet_row(row, len(columns)) for row in grid_rows]
    while len(next_grid_rows) < data_start_row:
        next_grid_rows.append([""] * len(columns))
    source_prefix_rows = [
        _normalize_sheet_row(row, len(source_columns))
        for row in source_grid_rows[:source_data_start_row]
    ]

    changed = 0
    for row_index in range(min(data_start_row, len(source_prefix_rows))):
        for column_index, column in enumerate(columns):
            if column not in source_columns:
                continue
            source_column_index = source_columns.index(column)
            source_value = source_prefix_rows[row_index][source_column_index]
            if next_grid_rows[row_index][column_index] != source_value:
                next_grid_rows[row_index][column_index] = source_value
                changed += 1

    next_document = copy.deepcopy(document)
    next_document["grid_rows"] = [*next_grid_rows[:data_start_row], *rows]
    return next_document, changed


def _copy_source_header_cell_meta(
    document: dict[str, Any],
    source_document: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    columns = _normalize_document_columns(document)
    source_columns = _normalize_document_columns(source_document)
    data_start_row = _normalize_document_data_start_row(document)
    source_data_start_row = _normalize_document_data_start_row(source_document)
    source_meta = copy.deepcopy(source_document.get("cell_meta") or {})
    next_meta = copy.deepcopy(document.get("cell_meta") or {})

    changed = 0
    for row_index in range(data_start_row):
        for column_index, column in enumerate(columns):
            if column not in source_columns:
                continue
            source_column_index = source_columns.index(column)
            source_key = f"{row_index}:{source_column_index}"
            target_key = f"{row_index}:{column_index}"
            if row_index >= source_data_start_row or source_key not in source_meta:
                if target_key in next_meta:
                    del next_meta[target_key]
                    changed += 1
                continue
            source_value = source_meta[source_key]
            if next_meta.get(target_key) != source_value:
                next_meta[target_key] = source_value
                changed += 1

    next_document = copy.deepcopy(document)
    next_document["cell_meta"] = next_meta
    return next_document, changed


def _copy_source_header_merged_cells(
    document: dict[str, Any],
    source_document: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    source_column_count = len(_normalize_document_columns(source_document))
    data_start_row = _normalize_document_data_start_row(document)
    source_cells = [
        copy.deepcopy(cell)
        for cell in source_document.get("merged_cells") or []
        if int(cell.get("row") or 0) < data_start_row
    ]
    non_source_cells = [
        copy.deepcopy(cell)
        for cell in document.get("merged_cells") or []
        if int(cell.get("col") or 0) >= source_column_count or int(cell.get("row") or 0) >= data_start_row
    ]
    next_cells = [*source_cells, *non_source_cells]
    changed = 0 if document.get("merged_cells") == next_cells else 1

    next_document = copy.deepcopy(document)
    next_document["merged_cells"] = next_cells
    return next_document, changed


def run(*, apply: bool, workbook_path: Path = DEFAULT_WORKBOOK_PATH) -> dict[str, Any]:
    workbook = load_workbook(workbook_path, data_only=False)
    source_document = _attendance_document(workbook[SOURCE_SHEET_TITLE])
    with Session(engine) as session:
        sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == ATTENDANCE_SHEET_NUMERIC_ID)).first()
        if sheet is None:
            raise RuntimeError(f"找不到考勤表 numeric_id={ATTENDANCE_SHEET_NUMERIC_ID}")

        document = copy.deepcopy(dict(sheet.document_json or {}))
        document, header_value_changes = _copy_source_header_rows(document, source_document)
        document, header_meta_changes = _copy_source_header_cell_meta(document, source_document)
        document, merged_cell_changes = _copy_source_header_merged_cells(document, source_document)
        summary = {
            "header_value_changes": header_value_changes,
            "header_meta_changes": header_meta_changes,
            "merged_cell_changes": merged_cell_changes,
        }

        if apply and any(summary.values()):
            sheet.document_json = document
            sheet.version = int(sheet.version or 1) + 1
            sheet.updated_at = time.time()
            session.add(sheet)
            session.commit()
        else:
            session.rollback()
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="从原始 Excel 修复念住闯关考勤表表头说明和合并区域。")
    parser.add_argument("--apply", action="store_true", help="实际写入数据库。默认只 dry-run。")
    parser.add_argument("--workbook", default=str(DEFAULT_WORKBOOK_PATH), help="原始 xlsx 路径。")
    args = parser.parse_args()

    summary = run(apply=args.apply, workbook_path=Path(args.workbook))
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] 修复念住闯关考勤表表头")
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
