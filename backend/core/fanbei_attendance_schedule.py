from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
import re
import time
from typing import Any, Literal

import requests
from sqlmodel import Session, select

from backend.core.attendance_progress_style import (
    highlight_text_refund_progress,
    parse_compact_refund_rules,
    set_cell_background as _set_cell_background,
    sheet_text as _shared_sheet_text,
)
from backend.core.attendance_service import get_current_execution_device, get_or_create_attendance_service_config
from backend.core.background_task_queue import background_task_queue
from backend.core.device import get_device_id
from backend.core.ui_automation import ensure_ui_automation_thread_context
from backend.models import SheetDocument
from kq5034.attendance_api import build_fanbei_attendance_step2_data, sync_fanbei_attendance_step1


FANBEI_ATTENDANCE_EVENING_TASK_KEY = "attendance_fanbei_evening_steps"
FANBEI_ATTENDANCE_MORNING_TASK_KEY = "attendance_fanbei_morning_steps"
FANBEI_ATTENDANCE_EVENING_RUN_TIME = "21:00"
FANBEI_ATTENDANCE_MORNING_RUN_TIME = "07:10"
FANBEI_ATTENDANCE_COURSE_NAME = "d260509梵呗初阶"
FANBEI_ATTENDANCE_SHOP_ID = 1
FANBEI_ATTENDANCE_ATTENDANCE_SHEET_ID = 6
FANBEI_ATTENDANCE_REMOTE_TIMEOUT_SECONDS = 3600
FANBEI_ATTENDANCE_STEP2_CLOCKIN_NAMES = ["打卡数"]
FANBEI_ATTENDANCE_STEP2_CLOCKIN_TITLES = [f"学修日志{i:02}" for i in range(1, 12)]
FANBEI_ATTENDANCE_STEP3_LESSON_COUNT = 11


@dataclass(frozen=True)
class FanbeiAttendanceStep:
    number: int
    title: str
    action: Callable[[], str | dict[str, Any] | None]


def _empty_step() -> str:
    return "空实现"


def _snapshot_execution_device() -> dict[str, Any]:
    from backend.db import engine

    with Session(engine) as session:
        config = get_or_create_attendance_service_config(session)
        entry = get_current_execution_device(session, config)
        if entry is None:
            raise RuntimeError("请先在考勤配置中选择执行设备")
        if not entry.is_active:
            raise RuntimeError("当前考勤执行设备已停用")
        return {
            "entry_id": entry.entry_id,
            "user_id": entry.user_id,
            "device_id": entry.device_id,
            "name": entry.name,
            "mode": entry.mode,
            "server_url": entry.server_url,
            "token": entry.token,
            "is_active": entry.is_active,
            "order_index": entry.order_index,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
        }


def _remote_headers(entry_snapshot: dict[str, Any]) -> dict[str, str]:
    token = str(entry_snapshot.get("token") or "")
    return {
        "Authorization": f"Bearer {token}",
        "X-Device-Token": token,
    }


def _remote_error_detail(response: requests.Response) -> str:
    try:
        detail = response.json().get("detail")
    except Exception:
        detail = response.text.strip()
    return str(detail or f"远程执行失败，HTTP {response.status_code}")


def _run_step1_on_entry(entry_snapshot: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(entry_snapshot.get("mode") or "")
    if mode == "local":
        if str(entry_snapshot.get("device_id") or "") != get_device_id():
            raise RuntimeError("所选本地执行设备不属于当前节点")
        with ensure_ui_automation_thread_context():
            return sync_fanbei_attendance_step1(**payload)

    server_url = str(entry_snapshot.get("server_url") or "").rstrip("/")
    token = str(entry_snapshot.get("token") or "")
    if not server_url or not token:
        raise RuntimeError("远程执行设备缺少后端地址或访问令牌")

    session = requests.Session()
    session.trust_env = False
    response = session.post(
        f"{server_url}/api/device-control/attendance/fanbei/step1",
        json=payload,
        headers=_remote_headers(entry_snapshot),
        timeout=FANBEI_ATTENDANCE_REMOTE_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise RuntimeError(_remote_error_detail(response))
    return response.json()


def _run_step2_data_on_entry(entry_snapshot: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(entry_snapshot.get("mode") or "")
    if mode == "local":
        if str(entry_snapshot.get("device_id") or "") != get_device_id():
            raise RuntimeError("所选本地执行设备不属于当前节点")
        return build_fanbei_attendance_step2_data(**payload)

    server_url = str(entry_snapshot.get("server_url") or "").rstrip("/")
    token = str(entry_snapshot.get("token") or "")
    if not server_url or not token:
        raise RuntimeError("远程执行设备缺少后端地址或访问令牌")

    session = requests.Session()
    session.trust_env = False
    response = session.post(
        f"{server_url}/api/device-control/attendance/fanbei/step2-data",
        json=payload,
        headers=_remote_headers(entry_snapshot),
        timeout=FANBEI_ATTENDANCE_REMOTE_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise RuntimeError(_remote_error_detail(response))
    return response.json()


def run_fanbei_attendance_step1() -> str:
    payload = {
        "course_name": FANBEI_ATTENDANCE_COURSE_NAME,
        "shop_id": FANBEI_ATTENDANCE_SHOP_ID,
        "update_lessons": True,
        "update_clockins": True,
        "clockin_pattern": f"{FANBEI_ATTENDANCE_COURSE_NAME}-*",
        "close_browser": True,
    }
    entry_snapshot = _snapshot_execution_device()
    result = _run_step1_on_entry(entry_snapshot, payload)
    lesson_count = result.get("lesson_update_count")
    clockin_count = result.get("clockin_update_count")
    device_name = str(entry_snapshot.get("name") or entry_snapshot.get("device_id") or "执行设备")
    return f"{device_name} 已执行 step1：课次 {lesson_count} 条，打卡 {clockin_count} 项"


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


def run_fanbei_attendance_step2() -> str:
    from backend.db import engine

    with Session(engine) as session:
        document = session.exec(
            select(SheetDocument).where(
                SheetDocument.numeric_id == FANBEI_ATTENDANCE_ATTENDANCE_SHEET_ID
            )
        ).first()
        if document is None:
            raise RuntimeError(f"考勤表不存在：sheet_id={FANBEI_ATTENDANCE_ATTENDANCE_SHEET_ID}")
        current_document = dict(document.document_json or {})
        columns = [_sheet_text(column) for column in current_document.get("columns", [])]
        user_id_index = _find_column_index(columns, "用户ID")
        if user_id_index is None:
            raise RuntimeError("考勤表缺少 用户ID 列")
        rows = [_normalize_row(row, len(columns)) for row in current_document.get("rows", [])]
        user_ids = [_sheet_text(row[user_id_index]) for row in rows]

    payload = {
        "course_name": FANBEI_ATTENDANCE_COURSE_NAME,
        "user_ids": user_ids,
        "clockin_names": FANBEI_ATTENDANCE_STEP2_CLOCKIN_NAMES,
        "clockin_titles": FANBEI_ATTENDANCE_STEP2_CLOCKIN_TITLES,
    }
    entry_snapshot = _snapshot_execution_device()
    result = _run_step2_data_on_entry(entry_snapshot, payload)

    with Session(engine) as session:
        summary = _apply_step2_data_to_attendance_sheet(
            session=session,
            sheet_id=FANBEI_ATTENDANCE_ATTENDANCE_SHEET_ID,
            step2_data=result,
        )

    device_name = str(entry_snapshot.get("name") or entry_snapshot.get("device_id") or "执行设备")
    return (
        f"{device_name} 已执行 step2：映射 {summary['mapped_columns']} 列，"
        f"更新 {summary['updated_rows']} 行/{summary['updated_cells']} 格"
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


def _parse_fanbei_video_refund_rules(text: Any) -> dict[str, int]:
    return parse_compact_refund_rules(
        text,
        default={"当堂": 40, "第1天": 32, "第2天": 24, "第3天": 16, "第4天": 8, "回放": 0},
    )


def _highlight_course_progress(refund_rules: dict[str, int], text: Any) -> tuple[float, str | None]:
    return highlight_text_refund_progress(refund_rules, text)


def _apply_fanbei_attendance_step3_to_sheet(
    *,
    session: Session,
    sheet_id: int,
    course_name: str,
    today: date | None = None,
) -> dict[str, Any]:
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

    required_headers = [
        "视频应返款",
    ]
    indexes: dict[str, int] = {}
    for header in required_headers:
        index = _find_column_index(columns, header)
        if index is None:
            raise RuntimeError(f"考勤表缺少 {header} 列")
        indexes[header] = index

    lesson_columns = sorted(
        (
            (number, index)
            for index, column in enumerate(columns)
            if (number := _extract_lesson_number(column)) is not None
            and 1 <= number <= FANBEI_ATTENDANCE_STEP3_LESSON_COUNT
        ),
        key=lambda item: item[0],
    )
    if not lesson_columns:
        raise RuntimeError("考勤表缺少课次列")

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
    cell_meta = dict(current_document.get("cell_meta") or {})
    updated_rows = 0
    updated_cells = 0
    styled_cells = 0
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
            if _set_cell_background(
                cell_meta,
                document_row=document_row,
                column_index=column_index,
                color=color,
            ):
                styled_cells += 1

        total_video_refund += video_refund
        video_refund_value = _format_numeric_cell(video_refund)
        if next_row[indexes["视频应返款"]] != video_refund_value:
            next_row[indexes["视频应返款"]] = video_refund_value
            row_changed = True
            updated_cells += 1

        if row_changed:
            updated_rows += 1
        next_rows.append(next_row)

    next_document = dict(current_document)
    next_document["grid_rows"] = grid_rows
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
        "lesson_columns": len(lesson_columns),
        "video_refund_total": _format_numeric_cell(total_video_refund),
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


def run_fanbei_attendance_step3() -> str:
    return run_fanbei_attendance_step3_for_sheet()["message"]


FANBEI_ATTENDANCE_STEPS: dict[int, FanbeiAttendanceStep] = {
    1: FanbeiAttendanceStep(1, "step1 下载小鹅通数据", run_fanbei_attendance_step1),
    2: FanbeiAttendanceStep(2, "step2 写回考勤数据", run_fanbei_attendance_step2),
    3: FanbeiAttendanceStep(3, "step3 计算返款与进度高亮", run_fanbei_attendance_step3),
    4: FanbeiAttendanceStep(4, "step4（待实现）", _empty_step),
    5: FanbeiAttendanceStep(5, "step5（待实现）", _empty_step),
    6: FanbeiAttendanceStep(6, "step6（待实现）", _empty_step),
}


def run_fanbei_attendance_steps(
    step_numbers: Iterable[int],
    *,
    period: Literal["evening", "morning"],
) -> str:
    executed_steps: list[str] = []
    step_results: list[str] = []
    for step_number in step_numbers:
        step = FANBEI_ATTENDANCE_STEPS[step_number]
        result = step.action()
        executed_steps.append(f"step{step.number}")
        if result:
            step_results.append(f"step{step.number}: {result}")
    suffix = f"；{'; '.join(step_results)}" if step_results else ""
    return f"梵呗考勤{period}流程已执行：{', '.join(executed_steps)}{suffix}"


def run_fanbei_attendance_evening_steps() -> str:
    return run_fanbei_attendance_steps((1, 2, 3), period="evening")


def run_fanbei_attendance_morning_steps() -> str:
    return run_fanbei_attendance_steps((4, 5, 6), period="morning")


def enqueue_fanbei_attendance_evening_steps() -> str:
    return background_task_queue.enqueue(
        FANBEI_ATTENDANCE_EVENING_TASK_KEY,
        run_fanbei_attendance_evening_steps,
        metadata={
            "course": "梵呗",
            "period": "evening",
            "steps": [1, 2, 3],
            "implemented_steps": [1, 2, 3],
        },
    )


def enqueue_fanbei_attendance_morning_steps() -> str:
    return background_task_queue.enqueue(
        FANBEI_ATTENDANCE_MORNING_TASK_KEY,
        run_fanbei_attendance_morning_steps,
        metadata={
            "course": "梵呗",
            "period": "morning",
            "steps": [4, 5, 6],
            "placeholder": True,
        },
    )
