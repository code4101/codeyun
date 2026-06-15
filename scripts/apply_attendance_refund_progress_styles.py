from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
import time
from typing import Any

from sqlmodel import Session, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api.note_sheets import _extract_document_grid_rows, _extract_document_rows, _normalize_document_columns, _normalize_document_data_start_row
from backend.core.attendance.progress_style import (
    highlight_threshold_refund_progress,
    parse_threshold_refund_rules,
    set_cell_background,
)
from backend.db import engine
from backend.models import SheetDocument


def _normalize_row(row: Any, column_count: int) -> list[Any]:
    if isinstance(row, list):
        return [*row[:column_count], *([""] * max(column_count - len(row), 0))]
    return [""] * column_count


def apply_clockin_styles(
    *,
    sheet_id: int,
    target_field: str,
    rule_field: str,
    apply: bool,
) -> dict[str, Any]:
    with Session(engine) as session:
        sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == sheet_id)).first()
        if sheet is None:
            raise RuntimeError(f"找不到表格 numeric_id={sheet_id}")

        document = copy.deepcopy(dict(sheet.document_json or {}))
        columns = _normalize_document_columns(document)
        if target_field not in columns:
            raise RuntimeError(f"表格缺少目标字段：{target_field}")
        if rule_field not in columns:
            raise RuntimeError(f"表格缺少规则字段：{rule_field}")

        target_index = columns.index(target_field)
        rule_index = columns.index(rule_field)
        rows = [_normalize_row(row, len(columns)) for row in _extract_document_rows(document)]
        grid_rows = _extract_document_grid_rows(document)
        data_start_row = _normalize_document_data_start_row(document)
        note_row_index = int(document.get("field_row_index") or 0) + 1
        rule_note = ""
        if 0 <= note_row_index < len(grid_rows):
            note_row = _normalize_row(grid_rows[note_row_index], len(columns))
            rule_note = note_row[rule_index]

        rules = parse_threshold_refund_rules(rule_note)
        if not rules:
            raise RuntimeError(f"{rule_field} 备注中没有可解析的阈值返款规则：{rule_note!r}")

        cell_meta = copy.deepcopy(document.get("cell_meta") or {})
        changed_cells = 0
        styled_cells = 0
        cleared_cells = 0
        total_refund = 0.0
        for row_index, row in enumerate(rows):
            refund_amount, color = highlight_threshold_refund_progress(rules, row[target_index])
            total_refund += refund_amount
            if color:
                styled_cells += 1
            else:
                cleared_cells += 1
            if set_cell_background(
                cell_meta,
                document_row=data_start_row + row_index,
                column_index=target_index,
                color=color,
            ):
                changed_cells += 1

        summary = {
            "sheet_id": sheet_id,
            "rows": len(rows),
            "target_field": target_field,
            "rule_field": rule_field,
            "rules": [
                {"threshold": rule.threshold, "refund_amount": rule.refund_amount}
                for rule in rules
            ],
            "changed_cells": changed_cells,
            "styled_cells": styled_cells,
            "cleared_cells": cleared_cells,
            "refund_total": int(total_refund) if total_refund.is_integer() else round(total_refund, 2),
        }
        if apply and changed_cells:
            document["cell_meta"] = cell_meta
            sheet.document_json = document
            sheet.version = int(sheet.version or 1) + 1
            sheet.updated_at = time.time()
            session.add(sheet)
            session.commit()
        else:
            session.rollback()
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="按考勤返款阈值规则渲染进度列样式。")
    parser.add_argument("--sheet-id", type=int, default=21)
    parser.add_argument("--target-field", default="打卡数")
    parser.add_argument("--rule-field", default="打卡应返款")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    summary = apply_clockin_styles(
        sheet_id=args.sheet_id,
        target_field=args.target_field,
        rule_field=args.rule_field,
        apply=args.apply,
    )
    print(f"[{'APPLY' if args.apply else 'DRY-RUN'}] 考勤返款进度样式")
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
