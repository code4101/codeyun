from __future__ import annotations

import datetime as dt
import re
import time
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from backend.core.runtime.background_task_queue import background_task_queue
from backend.models import SheetDocument


COURSE_COMPLETION_TASK_KEY = "attendance_course_completion"
COURSE_COMPLETION_RUN_TIME = "06:20"
ATTENDANCE_SUMMARY_SHEET_ID = 4
COMPLETABLE_COURSE_TYPES = {"念住", "觉观"}
KQMAIN_ACTIVE_LIST_NAME = "觉观念住类型"
EXCEL_SERIAL_UNIX_EPOCH = 25569


def _normalize_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("value", "")
    return str(value or "").strip()


def _normalize_row(row: Any, column_count: int) -> list[Any]:
    if isinstance(row, list):
        values = list(row)
    elif isinstance(row, dict):
        values = [row.get(f"col_{index}", "") for index in range(column_count)]
    else:
        values = []
    if len(values) < column_count:
        values.extend([""] * (column_count - len(values)))
    return values[:column_count]


def _columns(document: dict[str, Any]) -> list[str]:
    return [_normalize_text(column) for column in (document.get("columns") or [])]


def _column_index(columns: list[str], header: str) -> int:
    try:
        return columns.index(header)
    except ValueError as exc:
        raise RuntimeError(f"课程汇总表缺少字段：{header}") from exc


def _date_from_excel_serial(value: Any) -> dt.date | None:
    try:
        serial = float(_normalize_text(value))
    except ValueError:
        return None
    if serial <= 0:
        return None
    return dt.date(1970, 1, 1) + dt.timedelta(days=int(serial) - EXCEL_SERIAL_UNIX_EPOCH)


def _date_to_excel_serial(value: dt.date) -> int:
    return (value - dt.date(1970, 1, 1)).days + EXCEL_SERIAL_UNIX_EPOCH


def _parse_course_year(course_name: str) -> int | None:
    for pattern in (
        r"(?:^|[^\d])20(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})",
        r"(?:^|[^\d])d(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})",
        r"(?:^|[^\d])(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})",
    ):
        match = re.search(pattern, course_name)
        if not match:
            continue
        try:
            dt.date(2000 + int(match.group("yy")), int(match.group("mm")), int(match.group("dd")))
        except ValueError:
            continue
        return 2000 + int(match.group("yy"))
    return None


def _parse_summary_date(value: Any, *, course_name: str, today: dt.date) -> dt.date | None:
    serial_date = _date_from_excel_serial(value)
    if serial_date is not None:
        return serial_date
    text = _normalize_text(value)
    if not text:
        return None
    for pattern, has_year in (
        (r"(?P<year>20\d{2})[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})", True),
        (r"(?P<month>\d{1,2})[/-](?P<day>\d{1,2})", False),
    ):
        match = re.search(pattern, text)
        if not match:
            continue
        year = int(match.group("year")) if has_year else (_parse_course_year(course_name) or today.year)
        return dt.date(year, int(match.group("month")), int(match.group("day")))
    return None


def _parse_relative_date_formula(value: Any, *, base_date: dt.date | None) -> dt.date | None:
    if base_date is None:
        return None
    text = _normalize_text(value).replace(" ", "")
    match = re.fullmatch(r"=[A-Z]+\d+(?P<operator>[+-])(?P<days>\d+)", text, re.I)
    if not match:
        return None
    days = int(match.group("days"))
    if match.group("operator") == "-":
        days = -days
    return base_date + dt.timedelta(days=days)


def _course_module_suffix(module_name: str) -> str:
    return re.sub(r"^d?\d{6}", "", _normalize_text(module_name))


def _course_name_matches_module(course_name: str, module_name: str) -> bool:
    course = _normalize_text(course_name)
    module = _normalize_text(module_name)
    if not course or not module:
        return False
    normalized_course = course.removeprefix("20")
    normalized_module = module.removeprefix("d")
    return (
        course == module
        or normalized_course == normalized_module
        or course == _course_module_suffix(module)
        or course in module
        or _course_module_suffix(module) in course
    )


def _is_monthly_nianzhu_jueguan_course(course_type: str, course_name: str) -> bool:
    normalized_type = _normalize_text(course_type)
    normalized_name = _normalize_text(course_name)
    if normalized_type not in COMPLETABLE_COURSE_TYPES:
        return False
    if "闯关" in normalized_type or "闯关" in normalized_name:
        return False
    return re.search(r"第\s*\d+\s*届\s*(念住|觉观)", normalized_name) is not None


def _summary_row_ready_for_completion(row: list[Any], columns: list[str]) -> tuple[bool, str]:
    required_headers = ["报名人数", "实际总报名费", "已返款", "剩余促学金", "返款率"]
    for header in required_headers:
        try:
            index = _column_index(columns, header)
        except RuntimeError:
            return False, f"缺少{header}字段"
        value = _normalize_text(row[index])
        if not value:
            return False, f"{header}为空"
        if value.upper() == "#DIV/0!":
            return False, f"{header}尚未计算"
        if header == "实际总报名费" and value in {"0", "0.0", "0.00"}:
            return False, "实际总报名费为0"
    return True, ""


def _linked_sheet_id_from_summary_row(row: list[Any], online_sheet_index: int) -> int | None:
    cell = row[online_sheet_index] if online_sheet_index < len(row) else ""
    url = ""
    if isinstance(cell, dict):
        link = cell.get("link")
        if isinstance(link, dict):
            url = str(link.get("url") or "")
    match = re.search(r"(?:[?&]sheet=|/sheet/)(\d+)", url)
    return int(match.group(1)) if match else None


def _to_number(value: Any) -> float:
    try:
        return float(_normalize_text(value))
    except ValueError:
        return 0.0


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")


def _refresh_summary_stats_from_attendance_sheet(
    session: Session,
    document: dict[str, Any],
    *,
    row_index: int,
    course_name: str,
    attendance_sheet_id: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from backend.core.attendance.nianzhu_course_sheets import (
        compact_nianzhu_course_sheet_step2,
        rebuild_nianzhu_attendance_from_course_sheets,
    )

    step2_summary = compact_nianzhu_course_sheet_step2(
        session,
        attendance_sheet_id=attendance_sheet_id,
        course_name=course_name,
    )
    step3_summary = rebuild_nianzhu_attendance_from_course_sheets(
        session,
        attendance_sheet_id=attendance_sheet_id,
        active_only=True,
        course_name=course_name,
    )

    attendance = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == attendance_sheet_id)).first()
    if attendance is None:
        raise RuntimeError(f"未找到课程考勤表：{attendance_sheet_id}")
    attendance_document = dict(attendance.document_json or {})
    attendance_columns = _columns(attendance_document)
    attendance_rows = [
        _normalize_row(row, len(attendance_columns))
        for row in (attendance_document.get("rows") or [])
    ]
    refunded_index = _column_index(attendance_columns, "已返款")
    order_amount_index = _column_index(attendance_columns, "订单金额")
    registration_count = len(attendance_rows)
    refund_count = sum(1 for row in attendance_rows if _to_number(row[order_amount_index]) <= 0)
    refunded_total = sum(_to_number(row[refunded_index]) for row in attendance_rows)

    next_document = dict(document)
    columns = _columns(next_document)
    rows = [_normalize_row(row, len(columns)) for row in (next_document.get("rows") or [])]
    if row_index < 0 or row_index >= len(rows):
        raise RuntimeError(f"课程汇总行不存在：{row_index}")
    row = list(rows[row_index])
    row[_column_index(columns, "报名人数")] = str(registration_count)
    row[_column_index(columns, "退课人数")] = "" if refund_count == 0 else str(refund_count)
    row[_column_index(columns, "已返款")] = _format_number(refunded_total)
    rows[row_index] = row

    try:
        from backend.api.note_sheets import _replace_document_data_rows

        next_document = _replace_document_data_rows({**next_document, "columns": columns}, rows)
    except Exception:
        next_document["rows"] = rows
        grid_rows = next_document.get("grid_rows")
        if isinstance(grid_rows, list):
            data_start = int(next_document.get("data_start_row") or 0)
            next_document["grid_rows"] = [*grid_rows[:data_start], *rows]

    return next_document, {
        "attendance_sheet_id": attendance_sheet_id,
        "registration_count": registration_count,
        "refund_count": refund_count,
        "refunded_total": refunded_total,
        "step2": step2_summary,
        "step3": step3_summary,
    }


def _archive_due_summary_rows(
    session: Session,
    document: dict[str, Any],
    *,
    today: dt.date,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    from backend.api.note_sheets import _set_attendance_summary_row_completed

    next_document = dict(document)
    columns = _columns(next_document)
    column_count = len(columns)
    type_index = _column_index(columns, "课程类型")
    online_sheet_index = _column_index(columns, "在线考勤表")
    end_date_index = _column_index(columns, "课程结束日期")
    completed_index = _column_index(columns, "考勤实际完成结点")
    start_date_index = _column_index(columns, "课程开始日期")

    rows = [_normalize_row(row, column_count) for row in (next_document.get("rows") or [])]
    if not rows:
        return next_document, [], []

    archived: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row_index, row in reversed(list(enumerate(rows))):
        course_type = _normalize_text(row[type_index])
        completed_text = _normalize_text(row[completed_index])
        if completed_text:
            continue
        course_name = _normalize_text(row[online_sheet_index])
        if not _is_monthly_nianzhu_jueguan_course(course_type, course_name):
            continue
        start_date = _parse_summary_date(row[start_date_index], course_name=course_name, today=today)
        end_date = _parse_summary_date(row[end_date_index], course_name=course_name, today=today)
        if end_date is None:
            end_date = _parse_relative_date_formula(row[end_date_index], base_date=start_date)
        if end_date is None or end_date >= today:
            continue
        attendance_sheet_id = _linked_sheet_id_from_summary_row(row, online_sheet_index)
        if attendance_sheet_id is None:
            skipped.append(
                {
                    "row_index": row_index,
                    "course_type": course_type,
                    "course_name": course_name,
                    "course_end_date": end_date.isoformat(),
                    "reason": "在线考勤表不是本地工作簿链接",
                }
            )
            continue
        current_rows = [_normalize_row(current_row, column_count) for current_row in (next_document.get("rows") or [])]
        current_row_index = next(
            (
                index
                for index, current_row in enumerate(current_rows)
                if _normalize_text(current_row[online_sheet_index]) == course_name
                and not _normalize_text(current_row[completed_index])
            ),
            None,
        )
        if current_row_index is None:
            continue
        next_document, stats_summary = _refresh_summary_stats_from_attendance_sheet(
            session,
            next_document,
            row_index=current_row_index,
            course_name=course_name,
            attendance_sheet_id=attendance_sheet_id,
        )
        rows = [_normalize_row(current_row, column_count) for current_row in (next_document.get("rows") or [])]
        row = rows[current_row_index]
        ready, reason = _summary_row_ready_for_completion(row, columns)
        if not ready:
            skipped.append(
                {
                    "row_index": row_index,
                    "course_type": course_type,
                    "course_name": course_name,
                    "course_end_date": end_date.isoformat(),
                    "reason": reason,
                }
            )
            continue
        completion_date = end_date + dt.timedelta(days=1)
        next_document, next_row_index = _set_attendance_summary_row_completed(
            next_document,
            row_index=current_row_index,
            completion_date=completion_date,
        )
        archived.append(
            {
                "row_index": row_index,
                "next_row_index": next_row_index,
                "course_type": course_type,
                "course_name": course_name,
                "course_end_date": end_date.isoformat(),
                "completed_date": completion_date.isoformat(),
                "stats": stats_summary,
            }
        )

    archived.sort(key=lambda item: int(item.get("row_index") or 0))
    skipped.sort(key=lambda item: int(item.get("row_index") or 0))
    return next_document, archived, skipped


def _update_kqmain_active_courses(kqmain_path: Path, archived_courses: list[dict[str, Any]]) -> dict[str, Any]:
    if not archived_courses:
        return {"path": str(kqmain_path), "removed": [], "changed": False, "missing": False}
    if not kqmain_path.exists():
        return {"path": str(kqmain_path), "removed": [], "changed": False, "missing": True}

    source = kqmain_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"(?P<prefix>^{re.escape(KQMAIN_ACTIVE_LIST_NAME)}\s*=\s*\[\n)(?P<body>.*?)(?P<suffix>^\])",
        re.S | re.M,
    )
    match = pattern.search(source)
    if not match:
        return {"path": str(kqmain_path), "removed": [], "changed": False, "missing": False, "list_missing": True}

    raw_items = re.findall(r'^\s*["\'](?P<name>[^"\']+)["\'],?\s*$', match.group("body"), re.M)
    removed: list[str] = []
    kept: list[str] = []
    for item in raw_items:
        if any(_course_name_matches_module(str(course.get("course_name") or ""), item) for course in archived_courses):
            removed.append(item)
        else:
            kept.append(item)

    if not removed:
        return {"path": str(kqmain_path), "removed": [], "changed": False, "missing": False}

    body = "".join(f'    "{item}",\n' for item in kept)
    next_source = source[:match.start()] + match.group("prefix") + body + match.group("suffix") + source[match.end():]
    kqmain_path.write_text(next_source, encoding="utf-8", newline="")
    return {"path": str(kqmain_path), "removed": removed, "changed": True, "missing": False}


def default_kqmain_path() -> Path:
    from backend.core.settings import get_settings

    configured = str(getattr(get_settings(), "kqmain_path", "") or "").strip()
    if configured:
        return Path(configured)
    return Path("D:/home/chenkunze/slns/kq5034/kqmain.py")


def run_attendance_course_completion_job(
    session: Session,
    *,
    today: dt.date | None = None,
    sheet_id: int = ATTENDANCE_SUMMARY_SHEET_ID,
    kqmain_path: Path | None = None,
) -> dict[str, Any]:
    today = today or dt.date.today()
    sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == int(sheet_id))).first()
    if sheet is None:
        raise RuntimeError(f"未找到课程汇总 sheet：{sheet_id}")

    current_document = dict(sheet.document_json or {})
    next_document, archived, skipped = _archive_due_summary_rows(session, current_document, today=today)
    sheet_changed = bool(archived and next_document != current_document)
    if sheet_changed:
        sheet.document_json = next_document
        sheet.version = max(int(sheet.version or 1), 1) + 1
        sheet.updated_at = time.time()
        session.add(sheet)

    kqmain_result = _update_kqmain_active_courses(kqmain_path or default_kqmain_path(), archived)
    return {
        "sheet_id": sheet_id,
        "today": today.isoformat(),
        "archived_count": len(archived),
        "archived_courses": archived,
        "skipped_count": len(skipped),
        "skipped_courses": skipped,
        "sheet_changed": sheet_changed,
        "kqmain": kqmain_result,
    }


def _run_attendance_course_completion_job_in_session() -> dict[str, Any]:
    from backend.db import engine

    with Session(engine) as session:
        result = run_attendance_course_completion_job(session)
        session.commit()
        print(
            "Attendance course completion finished: "
            f"archived={result['archived_count']} "
            f"kqmain_removed={len(result.get('kqmain', {}).get('removed') or [])}"
        )
        return result


def enqueue_attendance_course_completion_job() -> str:
    task_id, _queued = background_task_queue.enqueue_once(
        COURSE_COMPLETION_TASK_KEY,
        _run_attendance_course_completion_job_in_session,
    )
    return task_id
