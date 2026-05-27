"""修复考勤汇总表 cell_meta 超链接错位问题。

3 个新课程模板插入到开头后，cell_meta 没有同步移位，
导致所有超链接整体向前偏移了 3 行。此脚本将 cell_meta
整体向后移动 3 行以对齐。
"""
import os
import sys
import time
from pathlib import Path

project_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_dir))

os.chdir(project_dir)

from sqlmodel import Session, select

from backend.db import engine
from backend.models import SheetDocument
from backend.api.note_sheets import (
    ATTENDANCE_SUMMARY_SHEET_ID,
    _is_attendance_summary_document,
    _normalize_document_json,
    _normalize_document_columns,
    _extract_document_rows,
    _normalize_document_data_start_row,
    _extract_document_grid_rows,
    _get_formula_reference_row_offset,
    _find_attendance_column_index,
    _extract_attendance_row_start_date,
    _get_next_month_first_day,
    _get_attendance_batch_course_targets,
    _normalize_sheet_row,
    _normalize_sheet_text,
    _shift_cell_meta_rows_for_insert,
    _active_sheet_condition,
    _active_workbook_condition,
    sheet_ref_aliases,
    workbook_ref_aliases,
)
from backend.models import WorkbookDocument, WorkbookSheetLink


def repair():
    with Session(engine) as session:
        document = session.exec(
            select(SheetDocument)
            .where(SheetDocument.numeric_id == ATTENDANCE_SUMMARY_SHEET_ID)
            .where(_active_sheet_condition())
        ).first()

        if document is None:
            print("ERROR: 未找到考勤汇总表 (sheet 4)")
            return

        if not _is_attendance_summary_document(session, document):
            print("ERROR: 文档不是考勤汇总表")
            return

        current = _normalize_document_json(dict(document.document_json or {}))
        columns = _normalize_document_columns(current)
        rows = _extract_document_rows(current)
        data_start_row = _normalize_document_data_start_row(current)
        cell_meta = dict(current.get("cell_meta") or {})

        if not rows or not cell_meta:
            print("No rows or cell_meta, nothing to repair.")
            return

        type_index = _find_attendance_column_index(columns, "course_type")
        start_date_index = _find_attendance_column_index(columns, "start_date")
        formula_row_offset = _get_formula_reference_row_offset(current)
        grid_rows = _extract_document_grid_rows(current)
        column_count = len(columns)

        next_month = _get_next_month_first_day()
        targets = _get_attendance_batch_course_targets(next_month)

        new_course_data_indices = []
        for course_type, target_date in targets:
            for row_index, row in enumerate(rows):
                row_values = _normalize_sheet_row(row, column_count)
                if type_index is not None and _normalize_sheet_text(row_values[type_index]) != course_type:
                    continue
                row_date = _extract_attendance_row_start_date(
                    row_values,
                    row_index=row_index,
                    columns=columns,
                    rows=rows,
                    start_date_index=start_date_index,
                    reference_row_offset=formula_row_offset,
                    grid_rows=grid_rows,
                )
                if row_date == target_date:
                    new_course_data_indices.append(row_index)
                    break

        print(f"data_start_row = {data_start_row}")
        print(f"Found {len(new_course_data_indices)} new course rows at data indices: {new_course_data_indices}")
        print(f"Target courses: {[(ct, str(td)) for ct, td in targets]}")

        if not new_course_data_indices:
            print("No new course templates found — repair may have already been applied or no templates exist.")
            return

        corrupted = False
        for data_idx in new_course_data_indices:
            doc_row = data_start_row + data_idx
            prefix = f"{doc_row}:"
            matching_keys = [k for k in cell_meta if k.startswith(prefix)]
            if matching_keys:
                corrupted = True
                print(f"  CORRUPTED: doc_row {doc_row} has cell_meta entries: {matching_keys[:5]}...")
            else:
                print(f"  OK: doc_row {doc_row} has no cell_meta")

        if not corrupted:
            print("Cell meta is already correct, no repair needed.")
            return

        # 显示修复前的链接分布（C 列 = index 2）
        print("\nBefore repair (column C=2 links):")
        for doc_row in range(data_start_row, data_start_row + 10):
            key = f"{doc_row}:2"
            entry = cell_meta.get(key, {})
            link_url = ""
            if isinstance(entry, dict) and isinstance(entry.get("link"), dict):
                link_url = entry["link"].get("url", "")
            marker = " <-- NEW COURSE" if (doc_row - data_start_row) in new_course_data_indices else ""
            print(f"  doc_row {doc_row} (display C{doc_row + 1}): {link_url or '(no link)'}{marker}")

        amount = len(new_course_data_indices)
        repaired_meta = _shift_cell_meta_rows_for_insert(
            cell_meta,
            insert_index=0,
            amount=amount,
            row_offset=data_start_row,
        )

        print("\nAfter repair (column C=2 links):")
        for doc_row in range(data_start_row, data_start_row + 10):
            key = f"{doc_row}:2"
            entry = repaired_meta.get(key, {})
            link_url = ""
            if isinstance(entry, dict) and isinstance(entry.get("link"), dict):
                link_url = entry["link"].get("url", "")
            marker = " <-- NEW COURSE" if (doc_row - data_start_row) in new_course_data_indices else ""
            print(f"  doc_row {doc_row} (display C{doc_row + 1}): {link_url or '(no link)'}{marker}")

        current["cell_meta"] = repaired_meta
        document.document_json = current
        document.version = max(int(document.version or 1), 1) + 1
        document.updated_at = time.time()
        session.add(document)
        session.commit()
        session.refresh(document)
        print("\nRepair applied and saved successfully.")


if __name__ == "__main__":
    repair()
