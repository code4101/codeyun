from __future__ import annotations

import argparse
import copy
import re
from pathlib import Path
import sys
import time
from typing import Any

from sqlmodel import Session, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api.note_sheets import _extract_document_rows, _normalize_document_columns, _normalize_document_data_start_row
from backend.core.attendance.progress_style import (
    PercentageRefundRule,
    highlight_percentage_refund_progress,
    highlight_presence_progress,
    set_cell_background,
)
from backend.db import engine
from backend.models import SheetDocument


ATTENDANCE_SHEET_NUMERIC_ID = 21
TRACKING_GROUP_COLUMN = "追踪分组"
RULE_VERSION_COLUMN = "规则版本"
CURRENT_RULE = "当前规则"
LEGACY_AFTER_20250522_RULE = "旧规则-20250522后"
LEGACY_BEFORE_20250522_RULE = "旧规则-20250522前"

RULES_BY_VERSION = {
    CURRENT_RULE: [PercentageRefundRule(90, 20)],
    LEGACY_AFTER_20250522_RULE: [PercentageRefundRule(50, 20)],
    LEGACY_BEFORE_20250522_RULE: [
        PercentageRefundRule(90, 10),
        PercentageRefundRule(150, 15),
        PercentageRefundRule(200, 20),
    ],
}


def _normalize_row(row: Any, column_count: int) -> list[Any]:
    if isinstance(row, list):
        return [*row[:column_count], *([""] * max(column_count - len(row), 0))]
    return [""] * column_count


def _is_lesson_progress_column(header: str) -> bool:
    return re.search(r"第\s*0*\d+\s*课", str(header or "")) is not None


def apply_nianzhu_course_progress_styles_to_document(document: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    next_document = copy.deepcopy(document)
    columns = _normalize_document_columns(next_document)
    if RULE_VERSION_COLUMN not in columns:
        raise RuntimeError(f"考勤表缺少 {RULE_VERSION_COLUMN} 列")
    rule_version_index = columns.index(RULE_VERSION_COLUMN)
    progress_end = columns.index(TRACKING_GROUP_COLUMN) if TRACKING_GROUP_COLUMN in columns else len(columns)
    progress_start = next(
        (index for index, header in enumerate(columns) if _is_lesson_progress_column(str(header))),
        -1,
    )
    if progress_start < 0:
        raise RuntimeError("考勤表缺少课次进度列")

    lesson_columns = [
        index
        for index in range(progress_start, progress_end)
        if _is_lesson_progress_column(str(columns[index]))
    ]
    rows = [_normalize_row(row, len(columns)) for row in _extract_document_rows(next_document)]
    data_start_row = _normalize_document_data_start_row(next_document)
    cell_meta = copy.deepcopy(next_document.get("cell_meta") or {})

    changed_cells = 0
    lesson_styled_cells = 0
    lesson_cleared_cells = 0
    non_refund_progress_styled_cells = 0
    non_refund_progress_cleared_cells = 0
    refund_total = 0.0
    rows_by_rule: dict[str, int] = {}

    for row_index, row in enumerate(rows):
        document_row = data_start_row + row_index
        rule_version = str(row[rule_version_index] or CURRENT_RULE).strip() or CURRENT_RULE
        rows_by_rule[rule_version] = rows_by_rule.get(rule_version, 0) + 1
        rules = RULES_BY_VERSION.get(rule_version, RULES_BY_VERSION[CURRENT_RULE])

        for column_index in range(progress_start, progress_end):
            if column_index not in lesson_columns:
                color = highlight_presence_progress(row[column_index])
                if color:
                    non_refund_progress_styled_cells += 1
                else:
                    non_refund_progress_cleared_cells += 1
                if set_cell_background(
                    cell_meta,
                    document_row=document_row,
                    column_index=column_index,
                    color=color,
                ):
                    changed_cells += 1
                continue

            refund_amount, color = highlight_percentage_refund_progress(rules, row[column_index])
            refund_total += refund_amount
            if color:
                lesson_styled_cells += 1
            else:
                lesson_cleared_cells += 1
            if set_cell_background(
                cell_meta,
                document_row=document_row,
                column_index=column_index,
                color=color,
            ):
                changed_cells += 1

    next_document["cell_meta"] = cell_meta
    summary = {
        "rows": len(rows),
        "lesson_columns": len(lesson_columns),
        "changed_cells": changed_cells,
        "lesson_styled_cells": lesson_styled_cells,
        "lesson_cleared_cells": lesson_cleared_cells,
        "non_refund_progress_styled_cells": non_refund_progress_styled_cells,
        "non_refund_progress_cleared_cells": non_refund_progress_cleared_cells,
        "refund_total": int(refund_total) if refund_total.is_integer() else round(refund_total, 2),
        "rows_by_rule": rows_by_rule,
    }
    return next_document, summary


def run(*, sheet_id: int = ATTENDANCE_SHEET_NUMERIC_ID, apply: bool = False) -> dict[str, Any]:
    with Session(engine) as session:
        sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == sheet_id)).first()
        if sheet is None:
            raise RuntimeError(f"找不到考勤表 numeric_id={sheet_id}")
        document = copy.deepcopy(dict(sheet.document_json or {}))
        next_document, summary = apply_nianzhu_course_progress_styles_to_document(document)
        if apply and next_document != document:
            sheet.document_json = next_document
            sheet.version = int(sheet.version or 1) + 1
            sheet.updated_at = time.time()
            session.add(sheet)
            session.commit()
        else:
            session.rollback()
        return {"sheet_id": sheet_id, **summary}


def main() -> None:
    parser = argparse.ArgumentParser(description="按念住闯关不同规则版本渲染课程进度返款高亮。")
    parser.add_argument("--sheet-id", type=int, default=ATTENDANCE_SHEET_NUMERIC_ID)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    summary = run(sheet_id=args.sheet_id, apply=args.apply)
    print(f"[{'APPLY' if args.apply else 'DRY-RUN'}] 念住闯关课程进度返款样式")
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
