from __future__ import annotations

from datetime import date
import re
import time
from typing import Any

from sqlmodel import Session, select

from backend.core.attendance.progress_style import (
    highlight_text_refund_progress,
    parse_compact_refund_rules,
    set_cell_background as _set_cell_background,
    sheet_text as _shared_sheet_text,
)
from backend.core.attendance.fanbei_course_sheets import (
    FANBEI_ATTENDANCE_SHEET_NUMERIC_ID,
    has_fanbei_course_storage_sheets,
    rebuild_fanbei_attendance_from_course_sheets,
)
from backend.models import SheetDocument


FANBEI_ATTENDANCE_COURSE_NAME = "d260509梵呗初阶"
FANBEI_ATTENDANCE_ATTENDANCE_SHEET_ID = FANBEI_ATTENDANCE_SHEET_NUMERIC_ID
FANBEI_ATTENDANCE_STEP3_LESSON_COUNT = 11
FANBEI_FULL_ATTENDANCE_TEXT_COLOR = "#5B21B6"
FANBEI_FULL_ATTENDANCE_COLUMN = "全勤"
FANBEI_LIVE_FULL_ATTENDANCE = "直播全勤"
FANBEI_REPLAY_FULL_ATTENDANCE = "回放全勤"


def _sheet_text(value: Any) -> str:
    return _shared_sheet_text(value)


def _normalize_step2_cell(value: Any) -> Any:
    if isinstance(value, str):
        return _sheet_text(value)
    return value


def _normalize_row(row: Any, column_count: int) -> list[Any]:
    if isinstance(row, list):
        return [*row[:column_count], *([""] * max(column_count - len(row), 0))]
    if isinstance(row, dict):
        return [row.get(str(index), "") for index in range(column_count)]
    return [""] * column_count


def _find_column_index(columns: list[str], header: str) -> int | None:
    normalized = _sheet_text(header)
    for index, column in enumerate(columns):
        if _sheet_text(column) == normalized:
            return index
    return None


def _extract_lesson_number(value: Any) -> int | None:
    match = re.search(r"第\s*0*(\d+)\s*课", _sheet_text(value))
    return int(match.group(1)) if match else None


def _build_step2_column_map(sheet_columns: list[str], data_columns: list[str]) -> dict[int, int]:
    lesson_column_by_number = {
        number: index
        for index, column in enumerate(sheet_columns)
        if (number := _extract_lesson_number(column)) is not None
    }
    mapping: dict[int, int] = {}
    for data_index, data_column in enumerate(data_columns):
        if data_index == 0 or _sheet_text(data_column) == "user_id2":
            user_id_index = _find_column_index(sheet_columns, "用户ID")
            if user_id_index is not None:
                mapping[data_index] = user_id_index
            continue
        sheet_index = _find_column_index(sheet_columns, data_column)
        if sheet_index is None:
            lesson_number = _extract_lesson_number(data_column)
            if lesson_number is not None:
                sheet_index = lesson_column_by_number.get(lesson_number)
        if sheet_index is not None:
            mapping[data_index] = sheet_index
    return mapping


def _apply_step2_data_to_attendance_sheet(
    *,
    session: Session,
    sheet_id: int,
    step2_data: dict[str, Any],
) -> dict[str, int]:
    from backend.api.note_sheets import _replace_document_data_rows

    document = session.exec(
        select(SheetDocument).where(SheetDocument.numeric_id == int(sheet_id))
    ).first()
    if document is None:
        raise RuntimeError(f"考勤表不存在：sheet_id={sheet_id}")

    current_document = dict(document.document_json or {})
    columns = [_sheet_text(column) for column in current_document.get("columns", [])]
    if not columns:
        raise RuntimeError("考勤表缺少 columns")
    user_id_index = _find_column_index(columns, "用户ID")
    if user_id_index is None:
        raise RuntimeError("考勤表缺少 用户ID 列")

    rows = [_normalize_row(row, len(columns)) for row in current_document.get("rows", [])]
    data_columns = [_sheet_text(column) for column in step2_data.get("columns", [])]
    data_rows = step2_data.get("rows") if isinstance(step2_data.get("rows"), list) else []
    if len(data_rows) != len(rows):
        raise RuntimeError(f"step2 返回行数不匹配：sheet={len(rows)} remote={len(data_rows)}")

    column_map = _build_step2_column_map(columns, data_columns)
    if not column_map:
        raise RuntimeError("step2 返回字段无法映射到考勤表列")

    updated_rows = 0
    updated_cells = 0
    next_rows: list[list[Any]] = []
    for row, data_row in zip(rows, data_rows):
        next_row = list(row)
        if not _sheet_text(row[user_id_index]):
            next_rows.append(next_row)
            continue
        normalized_data_row = _normalize_row(data_row, len(data_columns))
        changed = False
        for data_index, sheet_index in column_map.items():
            value = _normalize_step2_cell(normalized_data_row[data_index])
            if next_row[sheet_index] != value:
                next_row[sheet_index] = value
                changed = True
                updated_cells += 1
        if changed:
            updated_rows += 1
        next_rows.append(next_row)

    if updated_cells:
        document.document_json = _replace_document_data_rows(current_document, next_rows)
        document.version = max(int(document.version or 1), 1) + 1
        document.updated_at = time.time()
        session.add(document)
        session.commit()
        session.refresh(document)

    return {
        "updated_rows": updated_rows,
        "updated_cells": updated_cells,
        "mapped_columns": len(column_map),
        "remote_rows": len(data_rows),
    }


def _run_fanbei_attendance_step2_local() -> str:
    from backend.db import engine

    with Session(engine) as session:
        document = session.exec(
            select(SheetDocument).where(
                SheetDocument.numeric_id == FANBEI_ATTENDANCE_ATTENDANCE_SHEET_ID
            )
        ).first()
        if document is None:
            raise RuntimeError(f"考勤表不存在：sheet_id={FANBEI_ATTENDANCE_ATTENDANCE_SHEET_ID}")
        if not has_fanbei_course_storage_sheets(session, attendance_sheet=document):
            raise RuntimeError("梵呗课程工作簿缺少视频/打卡配置与数据 sheet，不能回退到旧 PG 数据源")
        summary = rebuild_fanbei_attendance_from_course_sheets(
            session=session,
            attendance_sheet_id=FANBEI_ATTENDANCE_ATTENDANCE_SHEET_ID,
        )
        session.commit()
    return (
        "当前 CodeYun 实例已从梵呗课程存储 sheet 执行 step2："
        f"映射 {summary['mapped_columns']} 列，"
        f"更新 {summary['updated_rows']} 行/{summary['updated_cells']} 格，"
        f"视频数据 {summary['video_data_rows']} 行压缩为 {summary['video_data_compacted_rows']} 行，"
        f"打卡数据 {summary['clockin_data_rows']} 行"
    )


def _parse_fanbei_course_start_date(course_name: str) -> date:
    match = re.search(r"d(\d{2})(\d{2})(\d{2})", course_name)
    if match is None:
        raise RuntimeError(f"无法从课程名解析开课日期：{course_name}")
    year, month, day = (int(part) for part in match.groups())
    return date(2000 + year, month, day)


def _fanbei_attendance_day_index(course_name: str, today: date | None = None) -> int:
    current = today or date.today()
    return max((current - _parse_fanbei_course_start_date(course_name)).days, 0)


def _format_numeric_cell(value: float) -> int | float:
    rounded = round(value)
    if abs(value - rounded) < 1e-9:
        return int(rounded)
    return round(value, 2)


def _numeric_value(value: Any) -> float:
    try:
        return float(_sheet_text(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _parse_fanbei_video_refund_rules(text: Any) -> dict[str, int]:
    return parse_compact_refund_rules(
        text,
        default={"当堂": 40, "第1天": 32, "第2天": 24, "第3天": 16, "第4天": 8, "回放": 0},
    )


def _fanbei_step3_lesson_count(document_json: dict[str, Any]) -> int:
    source_meta = document_json.get("source_meta")
    if isinstance(source_meta, dict):
        value = source_meta.get("official_lesson_count") or source_meta.get("lesson_count")
        try:
            count = int(value)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            return count
    return FANBEI_ATTENDANCE_STEP3_LESSON_COUNT


def _highlight_course_progress(refund_rules: dict[str, int], text: Any) -> tuple[float, str | None]:
    return highlight_text_refund_progress(refund_rules, text)


def _classify_fanbei_stage_full_attendance(
    *,
    clockin_count: float,
    required_count: int,
    lesson_values: list[Any],
) -> str:
    lesson_texts = [_sheet_text(value) for value in lesson_values]
    if (
        required_count <= 0
        or len(lesson_texts) != required_count
        or clockin_count < required_count
        or not all(text.startswith("当堂完成") or "回放" in text for text in lesson_texts)
    ):
        return ""
    if all(text.startswith("当堂完成") for text in lesson_texts):
        return FANBEI_LIVE_FULL_ATTENDANCE
    return FANBEI_REPLAY_FULL_ATTENDANCE


def _set_fanbei_full_attendance_foreground(
    cell_meta: dict[str, Any],
    *,
    document_row: int,
    column_index: int,
    highlighted: bool,
) -> bool:
    key = f"{document_row}:{column_index}"
    previous_meta = cell_meta.get(key)
    meta = dict(previous_meta) if isinstance(previous_meta, dict) else {}
    style = dict(meta.get("style")) if isinstance(meta.get("style"), dict) else {}
    previous_color = style.get("text_color")

    if highlighted:
        style["text_color"] = FANBEI_FULL_ATTENDANCE_TEXT_COLOR
    elif previous_color == FANBEI_FULL_ATTENDANCE_TEXT_COLOR:
        style.pop("text_color", None)
    else:
        return False

    if style:
        meta["style"] = style
    else:
        meta.pop("style", None)
    if meta:
        cell_meta[key] = meta
    else:
        cell_meta.pop(key, None)
    return previous_color != style.get("text_color")


def _set_fanbei_entity_style_value(
    document_json: dict[str, Any],
    *,
    document_row: int,
    column_index: int,
    style_key: str,
    value: str | None,
    remove_only_if: str | None = None,
) -> bool:
    """同步 Step 3 样式到前端优先读取的实体单元格存储。"""

    entity_rows = document_json.get("entity_rows")
    entity_columns = document_json.get("entity_columns")
    if (
        not isinstance(entity_rows, list)
        or not isinstance(entity_columns, list)
        or document_row < 0
        or document_row >= len(entity_rows)
        or column_index < 0
        or column_index >= len(entity_columns)
    ):
        return False

    row = entity_rows[document_row]
    column = entity_columns[column_index]
    row_id = _sheet_text(row.get("id")) if isinstance(row, dict) else ""
    column_id = _sheet_text(column.get("id")) if isinstance(column, dict) else ""
    if not row_id or not column_id:
        return False

    entity_cells = dict(document_json.get("entity_cells") or {})
    row_cells = dict(entity_cells.get(row_id) or {})
    previous_cell = row_cells.get(column_id)
    next_cell = dict(previous_cell) if isinstance(previous_cell, dict) else {}
    style = dict(next_cell.get("style")) if isinstance(next_cell.get("style"), dict) else {}
    previous_value = style.get(style_key)

    if value is not None:
        style[style_key] = value
    elif remove_only_if is None or previous_value == remove_only_if:
        style.pop(style_key, None)
    else:
        return False

    if previous_value == style.get(style_key):
        return False
    if style:
        next_cell["style"] = style
    else:
        next_cell.pop("style", None)
    if next_cell:
        row_cells[column_id] = next_cell
    else:
        row_cells.pop(column_id, None)
    if row_cells:
        entity_cells[row_id] = row_cells
    else:
        entity_cells.pop(row_id, None)
    document_json["entity_cells"] = entity_cells
    return True


def _apply_fanbei_attendance_step3_to_sheet(
    *,
    session: Session,
    sheet_id: int,
    course_name: str,
    today: date | None = None,
) -> dict[str, Any]:
    from backend.api.note_sheets import _insert_document_column, _replace_document_data_rows

    document = session.exec(
        select(SheetDocument).where(SheetDocument.numeric_id == int(sheet_id))
    ).first()
    if document is None:
        raise RuntimeError(f"考勤表不存在：sheet_id={sheet_id}")

    stored_document = dict(document.document_json or {})
    current_document = dict(stored_document)
    columns = [_sheet_text(column) for column in current_document.get("columns", [])]
    if not columns:
        raise RuntimeError("考勤表缺少 columns")

    full_attendance_index = _find_column_index(columns, FANBEI_FULL_ATTENDANCE_COLUMN)
    if full_attendance_index is None:
        completed_video_index = _find_column_index(columns, "完成视频数")
        if completed_video_index is None:
            raise RuntimeError("考勤表缺少 完成视频数 列")
        current_document = _insert_document_column(
            current_document,
            insert_index=completed_video_index,
            header=FANBEI_FULL_ATTENDANCE_COLUMN,
            width=64,
        )
        columns = [_sheet_text(column) for column in current_document.get("columns", [])]
        full_attendance_index = completed_video_index

    required_headers = [
        "视频应返款",
    ]
    indexes: dict[str, int] = {}
    for header in required_headers:
        index = _find_column_index(columns, header)
        if index is None:
            raise RuntimeError(f"考勤表缺少 {header} 列")
        indexes[header] = index

    lesson_count = _fanbei_step3_lesson_count(current_document)
    lesson_columns = sorted(
        (
            (number, index)
            for index, column in enumerate(columns)
            if (number := _extract_lesson_number(column)) is not None
            and 1 <= number <= lesson_count
        ),
        key=lambda item: item[0],
    )
    if not lesson_columns:
        raise RuntimeError("考勤表缺少课次列")

    required_count = min(_fanbei_attendance_day_index(course_name, today=today), len(lesson_columns))
    due_lesson_indexes = [column_index for _, column_index in lesson_columns[:required_count]]
    clockin_index = _find_column_index(columns, "打卡数")
    student_id_index = _find_column_index(columns, "学号")

    rows = [_normalize_row(row, len(columns)) for row in current_document.get("rows", [])]
    grid_rows = list(current_document.get("grid_rows") or [])
    data_start_row = max(int(current_document.get("data_start_row") or 0), 0)
    note_row_index = int(current_document.get("field_row_index") or 1) + 1
    video_note = ""
    if 0 <= note_row_index < len(grid_rows) and isinstance(grid_rows[note_row_index], list):
        note_row = _normalize_row(grid_rows[note_row_index], len(columns))
        video_note = note_row[indexes["视频应返款"]]
        grid_rows[note_row_index] = note_row
    refund_rules = _parse_fanbei_video_refund_rules(video_note)

    next_rows: list[list[Any]] = []
    next_document = dict(current_document)
    cell_meta = dict(current_document.get("cell_meta") or {})
    updated_rows = 0
    updated_cells = 0
    styled_cells = 0
    full_attendance_rows = 0
    live_full_attendance_rows = 0
    replay_full_attendance_rows = 0
    full_attendance_styled_cells = 0
    total_video_refund = 0.0

    for row_index, row in enumerate(rows):
        next_row = list(row)
        row_changed = False
        document_row = data_start_row + row_index
        video_refund = 0

        for _, column_index in lesson_columns:
            text = _sheet_text(next_row[column_index])
            refund_amount, color = _highlight_course_progress(refund_rules, text)
            video_refund += refund_amount
            legacy_style_changed = _set_cell_background(
                cell_meta,
                document_row=document_row,
                column_index=column_index,
                color=color,
            )
            entity_style_changed = _set_fanbei_entity_style_value(
                next_document,
                document_row=document_row,
                column_index=column_index,
                style_key="background_color",
                value=color,
            )
            if legacy_style_changed or entity_style_changed:
                styled_cells += 1

        full_attendance_value = ""
        if (
            clockin_index is not None
            and (student_id_index is None or bool(_sheet_text(next_row[student_id_index])))
        ):
            full_attendance_value = _classify_fanbei_stage_full_attendance(
                clockin_count=_numeric_value(next_row[clockin_index]),
                required_count=required_count,
                lesson_values=[next_row[index] for index in due_lesson_indexes],
            )
        is_full_attendance = bool(full_attendance_value)
        if is_full_attendance:
            full_attendance_rows += 1
            if full_attendance_value == FANBEI_LIVE_FULL_ATTENDANCE:
                live_full_attendance_rows += 1
            else:
                replay_full_attendance_rows += 1
        if next_row[full_attendance_index] != full_attendance_value:
            next_row[full_attendance_index] = full_attendance_value
            row_changed = True
            updated_cells += 1
        for column_index in range(len(columns)):
            legacy_foreground_changed = _set_fanbei_full_attendance_foreground(
                cell_meta,
                document_row=document_row,
                column_index=column_index,
                highlighted=is_full_attendance,
            )
            entity_foreground_changed = _set_fanbei_entity_style_value(
                next_document,
                document_row=document_row,
                column_index=column_index,
                style_key="text_color",
                value=FANBEI_FULL_ATTENDANCE_TEXT_COLOR if is_full_attendance else None,
                remove_only_if=FANBEI_FULL_ATTENDANCE_TEXT_COLOR,
            )
            if legacy_foreground_changed or entity_foreground_changed:
                full_attendance_styled_cells += 1

        total_video_refund += video_refund
        video_refund_value = _format_numeric_cell(video_refund)
        if next_row[indexes["视频应返款"]] != video_refund_value:
            next_row[indexes["视频应返款"]] = video_refund_value
            row_changed = True
            updated_cells += 1

        if row_changed:
            updated_rows += 1
        next_rows.append(next_row)

    next_document["grid_rows"] = grid_rows
    next_document["cell_meta"] = cell_meta
    next_document = _replace_document_data_rows(next_document, next_rows)

    if next_document != stored_document:
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
        "lesson_columns": len(lesson_columns),
        "video_refund_total": _format_numeric_cell(total_video_refund),
        "full_attendance_rows": full_attendance_rows,
        "live_full_attendance_rows": live_full_attendance_rows,
        "replay_full_attendance_rows": replay_full_attendance_rows,
        "full_attendance_styled_cells": full_attendance_styled_cells,
    }


def _format_fanbei_attendance_step3_summary(summary: dict[str, Any]) -> str:
    return (
        f"当前 CodeYun 实例已执行 step3：计算 {summary['lesson_columns']} 个课次，"
        f"更新视频应返款 {summary['updated_rows']} 行/{summary['updated_cells']} 格，"
        f"渲染 {summary['styled_cells']} 格，视频应返款合计 {summary['video_refund_total']} 元"
    )


def run_fanbei_attendance_step3_for_sheet(
    *,
    sheet_id: int = FANBEI_ATTENDANCE_ATTENDANCE_SHEET_ID,
    course_name: str = FANBEI_ATTENDANCE_COURSE_NAME,
) -> dict[str, Any]:
    from backend.db import engine

    with Session(engine) as session:
        summary = _apply_fanbei_attendance_step3_to_sheet(
            session=session,
            sheet_id=sheet_id,
            course_name=course_name,
        )

    return {
        "sheet_id": int(sheet_id),
        "course_name": course_name,
        **summary,
        "message": _format_fanbei_attendance_step3_summary(summary),
    }


__all__ = [
    "FANBEI_ATTENDANCE_ATTENDANCE_SHEET_ID",
    "FANBEI_ATTENDANCE_COURSE_NAME",
    "_run_fanbei_attendance_step2_local",
    "run_fanbei_attendance_step3_for_sheet",
]
