from __future__ import annotations

import copy
import re
import time
from typing import Any

from sqlmodel import Session, select

from backend.api.note_sheets import (
    _extract_document_rows,
    _normalize_document_columns,
    _normalize_document_data_start_row,
    _replace_document_data_rows,
)
from backend.core.attendance_progress_style import (
    PercentageRefundRule,
    highlight_percentage_refund_progress,
    highlight_presence_progress,
    set_cell_background,
    sheet_text,
)
from backend.models import SheetDocument


NIANZHU_CHUANGGUAN_COURSE_NAME = "d250106念住闯关"
NIANZHU_CHUANGGUAN_ATTENDANCE_SHEET_ID = 21

TRACKING_GROUP_COLUMN = "追踪分组"
TRACKING_STATUS_COLUMN = "追踪状态"
FREEZE_TIME_COLUMN = "冻结时间"
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


def _format_numeric_cell(value: float) -> int | float:
    rounded = round(value)
    if abs(value - rounded) < 1e-9:
        return int(rounded)
    return round(value, 2)


def _find_column_index(columns: list[str], header: str) -> int | None:
    normalized = sheet_text(header)
    for index, column in enumerate(columns):
        if sheet_text(column) == normalized:
            return index
    return None


def _extract_lesson_number(value: Any) -> int | None:
    match = re.search(r"第\s*0*(\d+)\s*课", sheet_text(value))
    return int(match.group(1)) if match else None


def _extract_play_count(value: Any) -> int:
    match = re.search(r"(\d+)\s*遍", sheet_text(value))
    return int(match.group(1)) if match else 0


def _is_active_tracking_row(row: list[Any], columns: list[str]) -> bool:
    mapping = dict(zip(columns, _normalize_row(row, len(columns))))
    has_status = TRACKING_STATUS_COLUMN in mapping
    has_freeze_time = FREEZE_TIME_COLUMN in mapping
    status = sheet_text(mapping.get(TRACKING_STATUS_COLUMN))
    freeze_time = sheet_text(mapping.get(FREEZE_TIME_COLUMN))

    if has_status or has_freeze_time:
        if freeze_time:
            return False
        if status:
            return status == "追踪中"
        return True

    return True


def _build_progress_columns(columns: list[str]) -> tuple[list[tuple[int, int]], list[int]]:
    progress_start = next(
        (index for index, header in enumerate(columns) if _extract_lesson_number(header) is not None),
        -1,
    )
    if progress_start < 0:
        raise RuntimeError("考勤表缺少课次进度列")

    marker_indexes = [
        index for index in (
            _find_column_index(columns, TRACKING_GROUP_COLUMN),
            _find_column_index(columns, TRACKING_STATUS_COLUMN),
            _find_column_index(columns, FREEZE_TIME_COLUMN),
            _find_column_index(columns, RULE_VERSION_COLUMN),
        )
        if index is not None and index >= progress_start
    ]
    progress_end = min(marker_indexes) if marker_indexes else len(columns)

    lesson_columns: list[tuple[int, int]] = []
    non_refund_columns: list[int] = []
    for column_index in range(progress_start, progress_end):
        lesson_number = _extract_lesson_number(columns[column_index])
        if lesson_number is None:
            non_refund_columns.append(column_index)
        else:
            lesson_columns.append((lesson_number, column_index))
    return lesson_columns, non_refund_columns


def _apply_nianzhu_attendance_step3_to_sheet(
    *,
    session: Session,
    sheet_id: int,
    course_name: str,
) -> dict[str, Any]:
    document = session.exec(
        select(SheetDocument).where(SheetDocument.numeric_id == int(sheet_id))
    ).first()
    if document is None:
        raise RuntimeError(f"考勤表不存在：sheet_id={sheet_id}")

    current_document = dict(document.document_json or {})
    columns = [sheet_text(column) for column in _normalize_document_columns(current_document)]
    if not columns:
        raise RuntimeError("考勤表缺少 columns")

    required_headers = ["优秀学员评分", "视频应返款"]
    indexes: dict[str, int] = {}
    for header in required_headers:
        index = _find_column_index(columns, header)
        if index is None:
            raise RuntimeError(f"考勤表缺少 {header} 列")
        indexes[header] = index

    rule_version_index = _find_column_index(columns, RULE_VERSION_COLUMN)
    lesson_columns, non_refund_columns = _build_progress_columns(columns)

    rows = [_normalize_row(row, len(columns)) for row in _extract_document_rows(current_document)]
    data_start_row = _normalize_document_data_start_row(current_document)
    cell_meta = copy.deepcopy(current_document.get("cell_meta") or {})

    next_rows: list[list[Any]] = []
    updated_rows = 0
    updated_cells = 0
    styled_cells = 0
    skipped_rows = 0
    total_video_refund = 0.0
    total_score = 0
    rows_by_rule: dict[str, int] = {}

    for row_index, row in enumerate(rows):
        next_row = list(row)
        row_changed = False
        document_row = data_start_row + row_index
        if not _is_active_tracking_row(next_row, columns):
            skipped_rows += 1
            next_rows.append(next_row)
            continue

        rule_version = CURRENT_RULE
        if rule_version_index is not None:
            rule_version = sheet_text(next_row[rule_version_index]) or CURRENT_RULE
        rows_by_rule[rule_version] = rows_by_rule.get(rule_version, 0) + 1
        rules = RULES_BY_VERSION.get(rule_version, RULES_BY_VERSION[CURRENT_RULE])

        video_refund = 0.0
        score = 0
        for lesson_number, column_index in lesson_columns:
            text = next_row[column_index]
            refund_amount, color = highlight_percentage_refund_progress(rules, text)
            video_refund += refund_amount
            if lesson_number >= 12 and refund_amount > 0:
                score += max(_extract_play_count(text) - 1, 0)
            if set_cell_background(
                cell_meta,
                document_row=document_row,
                column_index=column_index,
                color=color,
            ):
                styled_cells += 1

        for column_index in non_refund_columns:
            color = highlight_presence_progress(next_row[column_index])
            if set_cell_background(
                cell_meta,
                document_row=document_row,
                column_index=column_index,
                color=color,
            ):
                styled_cells += 1

        total_video_refund += video_refund
        total_score += score
        video_refund_value = _format_numeric_cell(video_refund)
        if next_row[indexes["视频应返款"]] != video_refund_value:
            next_row[indexes["视频应返款"]] = video_refund_value
            row_changed = True
            updated_cells += 1
        if next_row[indexes["优秀学员评分"]] != score:
            next_row[indexes["优秀学员评分"]] = score
            row_changed = True
            updated_cells += 1

        if row_changed:
            updated_rows += 1
        next_rows.append(next_row)

    next_document = dict(current_document)
    next_document["cell_meta"] = cell_meta
    next_document = _replace_document_data_rows(next_document, next_rows)

    if next_document != current_document:
        document.document_json = next_document
        document.version = max(int(document.version or 1), 1) + 1
        document.updated_at = time.time()
        session.add(document)
        session.commit()
        session.refresh(document)

    return {
        "updated_rows": updated_rows,
        "updated_cells": updated_cells,
        "styled_cells": styled_cells,
        "skipped_rows": skipped_rows,
        "lesson_columns": len(lesson_columns),
        "non_refund_progress_columns": len(non_refund_columns),
        "video_refund_total": _format_numeric_cell(total_video_refund),
        "score_total": total_score,
        "rows_by_rule": rows_by_rule,
    }


def _format_nianzhu_attendance_step3_summary(summary: dict[str, Any]) -> str:
    return (
        f"当前 CodeYun 实例已执行念住闯关 step3：计算 {summary['lesson_columns']} 个课次，"
        f"更新返款/评分 {summary['updated_rows']} 行/{summary['updated_cells']} 格，"
        f"渲染 {summary['styled_cells']} 格，跳过冻结行 {summary['skipped_rows']} 行，"
        f"视频应返款合计 {summary['video_refund_total']} 元，"
        f"优秀学员评分合计 {summary['score_total']} 分"
    )


def run_nianzhu_attendance_step3_for_sheet(
    *,
    sheet_id: int = NIANZHU_CHUANGGUAN_ATTENDANCE_SHEET_ID,
    course_name: str = NIANZHU_CHUANGGUAN_COURSE_NAME,
) -> dict[str, Any]:
    from backend.db import engine

    with Session(engine) as session:
        summary = _apply_nianzhu_attendance_step3_to_sheet(
            session=session,
            sheet_id=sheet_id,
            course_name=course_name,
        )

    return {
        "sheet_id": int(sheet_id),
        "course_name": course_name,
        **summary,
        "message": _format_nianzhu_attendance_step3_summary(summary),
    }
