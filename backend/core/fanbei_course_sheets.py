from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
import re
from typing import Any

from sqlmodel import Session

from backend.api.note_sheets import (
    _extract_document_rows,
    _normalize_document_columns,
    _replace_document_data_rows,
)
from backend.core.course_data_sheet_storage import (
    CourseSheetSpec,
    attendance_row_user_ids,
    build_registration_identity_map,
    build_registration_user_alias_map,
    document_dict_rows,
    get_sheet,
    has_course_storage_sheets,
    load_course_sheet_bundle,
    make_table_document_from_dicts,
    materialize_course_sheets,
    normalize_row,
    normalize_text,
    update_course_sheet_document,
)


FANBEI_WORKBOOK_NUMERIC_ID = 3
FANBEI_ATTENDANCE_SHEET_NUMERIC_ID = 6
FANBEI_COURSE_NAME = "d260509梵呗初阶"
FANBEI_OWNER_KEY = "20260509-fanbei-chujie"

VIDEO_CONFIG_SHEET_KEY = "video_config"
VIDEO_DATA_SHEET_KEY = "video_data"
CLOCKIN_CONFIG_SHEET_KEY = "clockin_config"
CLOCKIN_DATA_SHEET_KEY = "clockin_data"

VIDEO_CONFIG_COLUMNS = [
    "lesson_id",
    "start_date",
    "end_date",
    "next_update",
    "lesson_id2",
    "shop_id",
    "lesson_name",
    "video_duration",
]
VIDEO_DATA_COLUMNS = [
    "lesson_data_id",
    "user_id2",
    "remark_nm",
    "state",
    "stay_seconds",
    "cum_seconds",
    "studio_seconds",
    "playback_seconds",
    "num_of_comments",
    "studio_amount",
    "study_state",
    "progress",
    "last_play_time",
    "shop_id",
    "update_time",
    "lesson_id",
    "finish_time",
    "comment_times",
    "money",
    "lesson_name",
]
CLOCKIN_CONFIG_COLUMNS = [
    "clockin_id",
    "name",
    "url",
    "start_date",
    "end_date",
    "days",
    "clockin_user_num",
    "total_user_num",
]
CLOCKIN_DATA_BASE_COLUMNS = [
    "clockin_data_id",
    "user_id2",
    "nickname",
    "groupname",
    "publish_time",
    "update_content",
    "update_title",
    "update_type",
    "tags",
    "read_num",
    "like_num",
    "comment_num",
    "is_essence",
    "share_num",
    "update_url",
    "clockin_name",
    "is_repair",
    "task_date",
    "extra",
    "clockin_id",
]
CLOCKIN_DATA_COLUMNS = [*CLOCKIN_DATA_BASE_COLUMNS]

COURSE_SHEET_SPECS = [
    CourseSheetSpec(VIDEO_CONFIG_SHEET_KEY, "视频配置", 30),
    CourseSheetSpec(VIDEO_DATA_SHEET_KEY, "视频数据", 40),
    CourseSheetSpec(CLOCKIN_CONFIG_SHEET_KEY, "打卡配置", 50),
    CourseSheetSpec(CLOCKIN_DATA_SHEET_KEY, "打卡数据", 60),
]
COURSE_STORAGE_SHEET_KEYS = [spec.sheet_key for spec in COURSE_SHEET_SPECS]
DEFAULT_CLOCKIN_TITLES = [f"学修日志{i:02}" for i in range(1, 12)]


def _to_float(value: Any) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, int | float):
        return 0.0 if isinstance(value, float) and (value != value) else float(value)
    if isinstance(value, Decimal):
        return float(value)
    text = normalize_text(value).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0


def _format_numeric_cell(value: float) -> int | float:
    rounded = round(float(value))
    if abs(float(value) - rounded) < 1e-9:
        return int(rounded)
    return round(float(value), 2)


def _format_storage_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return _format_numeric_cell(value)
    if isinstance(value, Decimal):
        return _format_numeric_cell(float(value))
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return normalize_text(value)


def _parse_datetime_cell(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = normalize_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _extract_lesson_number(value: Any) -> int | None:
    match = re.search(r"第\s*0*(\d+)\s*课", normalize_text(value))
    return int(match.group(1)) if match else None


def _find_column_index(columns: list[str], header: str) -> int | None:
    normalized = normalize_text(header)
    for index, column in enumerate(columns):
        if normalize_text(column) == normalized:
            return index
    return None


def _query_legacy_lesson_rows(course_name: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from kq5034.attendance_api import get_kqdb  # type: ignore
    except Exception as exc:
        return [], f"无法导入 kq5034.attendance_api.get_kqdb: {exc}"

    sql = (
        "SELECT lesson_id, start_date, end_date, next_update, lesson_id2, shop_id, "
        "lesson_name, video_duration "
        "FROM lesson_table WHERE lesson_name LIKE %s ORDER BY lesson_id"
    )
    try:
        xldb = get_kqdb()
        try:
            records = xldb.exec2dict(sql, [f"%{course_name}%"])
        except TypeError:
            safe = course_name.replace("'", "''")
            records = xldb.exec2dict(sql.replace("%s", f"'%{safe}%'"))
    except Exception as exc:
        return [], f"查询 lesson_table 失败: {exc}"
    return _legacy_records(records), None


def _query_legacy_lesson_data_rows(legacy_lesson_ids: list[int]) -> tuple[list[dict[str, Any]], str | None]:
    if not legacy_lesson_ids:
        return [], None
    try:
        from kq5034.attendance_api import get_kqdb  # type: ignore
    except Exception as exc:
        return [], f"无法导入 kq5034.attendance_api.get_kqdb: {exc}"

    id_list = ",".join(str(int(item)) for item in legacy_lesson_ids)
    sql = (
        "SELECT lesson_data_id, user_id2, remark_nm, state, stay_seconds, cum_seconds, "
        "studio_seconds, playback_seconds, num_of_comments, studio_amount, study_state, "
        "progress, last_play_time, shop_id, update_time, lesson_id, finish_time, "
        "comment_times, money, lesson_name "
        f"FROM lesson_data_table WHERE lesson_id IN ({id_list}) "
        "ORDER BY lesson_id, lesson_data_id"
    )
    try:
        records = get_kqdb().exec2dict(sql)
    except Exception as exc:
        return [], f"查询 lesson_data_table 失败: {exc}"
    return _legacy_records(records), None


def _query_legacy_clockin_rows(course_name: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from kq5034.attendance_api import get_kqdb  # type: ignore
    except Exception as exc:
        return [], f"无法导入 kq5034.attendance_api.get_kqdb: {exc}"

    sql = (
        "SELECT clockin_id, name, url, start_date, end_date, days, clockin_user_num, total_user_num "
        "FROM clockin_table WHERE name LIKE %s ORDER BY clockin_id"
    )
    try:
        xldb = get_kqdb()
        try:
            records = xldb.exec2dict(sql, [f"{course_name}-%"])
        except TypeError:
            safe = course_name.replace("'", "''")
            records = xldb.exec2dict(sql.replace("%s", f"'{safe}-%'"))
    except Exception as exc:
        return [], f"查询 clockin_table 失败: {exc}"
    return _legacy_records(records), None


def _query_legacy_clockin_data_rows(legacy_clockin_ids: list[int]) -> tuple[list[dict[str, Any]], str | None]:
    if not legacy_clockin_ids:
        return [], None
    try:
        from kq5034.attendance_api import get_kqdb  # type: ignore
    except Exception as exc:
        return [], f"无法导入 kq5034.attendance_api.get_kqdb: {exc}"

    id_list = ",".join(str(int(item)) for item in legacy_clockin_ids)
    sql = (
        "SELECT clockin_data_id, user_id2, nickname, groupname, publish_time, update_content, "
        "update_title, update_type, tags, read_num, like_num, comment_num, is_essence, "
        "share_num, update_url, clockin_name, is_repair, task_date, extra, clockin_id "
        f"FROM clockin_data_table WHERE clockin_id IN ({id_list}) "
        "ORDER BY clockin_id, clockin_data_id"
    )
    try:
        records = get_kqdb().exec2dict(sql)
    except Exception as exc:
        return [], f"查询 clockin_data_table 失败: {exc}"
    return _legacy_records(records), None


def _legacy_records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "fetchall"):
        value = value.fetchall()
    return [dict(record) for record in (value or []) if isinstance(record, dict)]


def _attendance_lesson_config_rows(attendance_document: dict[str, Any], *, course_name: str) -> list[dict[str, Any]]:
    columns = _normalize_document_columns(attendance_document)
    rows: list[dict[str, Any]] = []
    for index, column in enumerate(columns, start=1):
        lesson_number = _extract_lesson_number(column)
        if lesson_number is None:
            continue
        rows.append({
            "lesson_id": index,
            "lesson_name": f"{course_name}-第{lesson_number:02d}课",
            "shop_id": 1,
        })
    return rows


def _course_sheet_documents_from_attendance(
    attendance_document: dict[str, Any],
    *,
    course_name: str,
) -> dict[str, dict[str, Any]]:
    legacy_lessons, lesson_error = _query_legacy_lesson_rows(course_name)
    if not legacy_lessons:
        legacy_lessons = _attendance_lesson_config_rows(attendance_document, course_name=course_name)
    lesson_ids = [int(_to_float(row.get("lesson_id"))) for row in legacy_lessons if _to_float(row.get("lesson_id")) > 0]
    legacy_lesson_data, lesson_data_error = _query_legacy_lesson_data_rows(lesson_ids)

    legacy_clockins, clockin_error = _query_legacy_clockin_rows(course_name)
    clockin_ids = [int(_to_float(row.get("clockin_id"))) for row in legacy_clockins if _to_float(row.get("clockin_id")) > 0]
    legacy_clockin_data, clockin_data_error = _query_legacy_clockin_data_rows(clockin_ids)

    video_config_document = make_table_document_from_dicts(
        columns=VIDEO_CONFIG_COLUMNS,
        rows=legacy_lessons,
        numeric_columns={"lesson_id", "shop_id", "video_duration"},
        page_size=100,
    )
    video_config_document["source_meta"] = {"legacy_lesson_rows": len(legacy_lessons), "legacy_lesson_error": lesson_error}

    video_data_document = make_table_document_from_dicts(
        columns=VIDEO_DATA_COLUMNS,
        rows=legacy_lesson_data,
        numeric_columns={
            "lesson_data_id",
            "stay_seconds",
            "cum_seconds",
            "studio_seconds",
            "playback_seconds",
            "progress",
            "shop_id",
            "lesson_id",
        },
        page_size=200,
    )
    video_data_document["source_meta"] = {
        "legacy_lesson_data_rows": len(legacy_lesson_data),
        "legacy_lesson_data_error": lesson_data_error,
    }

    clockin_config_document = make_table_document_from_dicts(
        columns=CLOCKIN_CONFIG_COLUMNS,
        rows=legacy_clockins,
        numeric_columns={"clockin_id", "days", "clockin_user_num", "total_user_num"},
        page_size=100,
    )
    clockin_config_document["source_meta"] = {
        "legacy_clockin_rows": len(legacy_clockins),
        "legacy_clockin_error": clockin_error,
    }

    clockin_data_document = make_table_document_from_dicts(
        columns=CLOCKIN_DATA_COLUMNS,
        rows=legacy_clockin_data,
        numeric_columns={"clockin_data_id", "clockin_id", "read_num", "like_num", "comment_num", "share_num"},
        page_size=200,
    )
    clockin_data_document["source_meta"] = {
        "legacy_clockin_data_rows": len(legacy_clockin_data),
        "legacy_clockin_data_error": clockin_data_error,
    }

    return {
        VIDEO_CONFIG_SHEET_KEY: video_config_document,
        VIDEO_DATA_SHEET_KEY: video_data_document,
        CLOCKIN_CONFIG_SHEET_KEY: clockin_config_document,
        CLOCKIN_DATA_SHEET_KEY: clockin_data_document,
    }


def materialize_fanbei_course_sheets(
    session: Session,
    *,
    workbook_id: int = FANBEI_WORKBOOK_NUMERIC_ID,
    attendance_sheet_id: int = FANBEI_ATTENDANCE_SHEET_NUMERIC_ID,
    course_name: str = FANBEI_COURSE_NAME,
    replace: bool = False,
) -> dict[str, Any]:
    attendance = get_sheet(session, attendance_sheet_id)
    documents = _course_sheet_documents_from_attendance(
        copy.deepcopy(dict(attendance.document_json or {})),
        course_name=course_name,
    )
    summary = materialize_course_sheets(
        session,
        workbook_id=workbook_id,
        attendance_sheet_id=attendance_sheet_id,
        default_owner_key=FANBEI_OWNER_KEY,
        specs=COURSE_SHEET_SPECS,
        documents=documents,
        replace=replace,
    )
    return {"course_name": course_name, **summary}


def _load_fanbei_course_sheet_bundle(session: Session, *, attendance: Any) -> dict[str, Any]:
    return load_course_sheet_bundle(
        session,
        attendance=attendance,
        sheet_keys=COURSE_STORAGE_SHEET_KEYS,
        default_owner_key=FANBEI_OWNER_KEY,
        course_label="梵呗",
    )


def has_fanbei_course_storage_sheets(session: Session, *, attendance_sheet: Any) -> bool:
    return has_course_storage_sheets(
        session,
        attendance_sheet=attendance_sheet,
        sheet_keys=COURSE_STORAGE_SHEET_KEYS,
        default_owner_key=FANBEI_OWNER_KEY,
    )


def _lesson_result_and_keep_row(lesson: dict[str, Any], source_rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any] | None]:
    items = [dict(row) for row in source_rows if normalize_text(row.get("user_id2"))]
    if not items:
        return "", None

    items.sort(key=lambda row: _parse_datetime_cell(row.get("update_time")) or datetime.min)
    last_studio_seconds = 0.0
    last_playback_seconds = 0.0
    total_seconds = _to_float(lesson.get("video_duration"))

    for row in items:
        studio_seconds = _to_float(row.get("studio_seconds"))
        playback_seconds = _to_float(row.get("playback_seconds"))
        if studio_seconds < last_studio_seconds:
            studio_seconds = last_studio_seconds
        else:
            last_studio_seconds = studio_seconds
        if playback_seconds < last_playback_seconds:
            playback_seconds = last_playback_seconds
        else:
            last_playback_seconds = playback_seconds
        row["studio_seconds"] = _format_numeric_cell(studio_seconds)
        row["playback_seconds"] = _format_numeric_cell(playback_seconds)
        cum_seconds = studio_seconds + playback_seconds
        row["cum_seconds"] = _format_numeric_cell(cum_seconds)
        if total_seconds > 0:
            row["progress"] = int(cum_seconds / total_seconds * 100)
        else:
            row["progress"] = _format_numeric_cell(_to_float(row.get("progress")))

    max_progress_item = max(items, key=lambda row: _to_float(row.get("progress")))
    max_progress = int(_to_float(max_progress_item.get("progress")))
    if max_progress < 50:
        return f"学习中/{max_progress}%", max_progress_item

    first_finished_item = next(row for row in items if _to_float(row.get("progress")) >= 50)
    if normalize_text(first_finished_item.get("lesson_data_id")) != normalize_text(max_progress_item.get("lesson_data_id")):
        for field in ["stay_seconds", "cum_seconds", "studio_seconds", "playback_seconds", "study_state", "progress"]:
            first_finished_item[field] = max_progress_item.get(field)

    if total_seconds > 0 and _to_float(first_finished_item.get("studio_seconds")) / total_seconds >= 0.5:
        return f"当堂完成/{max_progress}%", first_finished_item

    start_date = _parse_datetime_cell(lesson.get("start_date")) or datetime.min
    video_end_time = start_date + timedelta(seconds=total_seconds)
    update_time = _parse_datetime_cell(first_finished_item.get("update_time")) or video_end_time
    delta_d = max(int((update_time - video_end_time).total_seconds() / (3600 * 24)), 1)
    return f"第{delta_d}天回放/{max_progress}%", first_finished_item


def _lesson_output_column(lesson_name: Any) -> str:
    lesson_number = _extract_lesson_number(lesson_name)
    if lesson_number is not None:
        return f"第{lesson_number:02d}课"
    text = normalize_text(lesson_name)
    return re.sub(r"^d\d{6}[^-－—–_:：]*[\s\-－—–_:：]*", "", text).lstrip("-").strip() or text


def _build_video_progress(
    video_config_rows: list[dict[str, Any]],
    video_data_rows: list[dict[str, Any]],
    *,
    user_alias_map: dict[str, str] | None = None,
) -> tuple[list[str], dict[tuple[str, str], str], list[dict[str, Any]]]:
    alias_map = _normalize_user_alias_map(user_alias_map)
    data_by_lesson_user: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in video_data_rows:
        user_id = _resolve_user_id(row.get("user_id2"), alias_map)
        lesson_id = normalize_text(row.get("lesson_id"))
        if not user_id or not lesson_id:
            continue
        if user_id != normalize_text(row.get("user_id2")):
            row = {**row, "user_id2": user_id}
        data_by_lesson_user.setdefault((lesson_id, user_id), []).append(row)

    output_columns: list[str] = []
    output: dict[tuple[str, str], str] = {}
    compacted_rows: list[dict[str, Any]] = []
    for lesson in sorted(video_config_rows, key=lambda row: (_extract_lesson_number(row.get("lesson_name")) or 9999, _to_float(row.get("lesson_id")))):
        lesson_id = normalize_text(lesson.get("lesson_id"))
        if not lesson_id:
            continue
        output_column = _lesson_output_column(lesson.get("lesson_name"))
        if output_column not in output_columns:
            output_columns.append(output_column)
        for (data_lesson_id, user_id), rows in data_by_lesson_user.items():
            if data_lesson_id != lesson_id:
                continue
            result, keep_row = _lesson_result_and_keep_row(lesson, rows)
            output[(user_id, output_column)] = result
            if keep_row:
                compacted_rows.append(keep_row)

    for index, row in enumerate(compacted_rows, start=1):
        row["lesson_data_id"] = index
    return output_columns, output, compacted_rows


def _parse_publish_date(value: Any) -> str:
    dt = _parse_datetime_cell(value)
    if dt is not None:
        return dt.date().isoformat()
    return ""


def _normalize_user_alias_map(user_alias_map: dict[str, str] | None = None) -> dict[str, str]:
    result: dict[str, str] = {}
    for source, target in (user_alias_map or {}).items():
        source_text = normalize_text(source)
        target_text = normalize_text(target)
        if source_text and target_text and source_text != target_text:
            result[source_text] = target_text
    return result


def _resolve_user_id(value: Any, alias_map: dict[str, str]) -> str:
    user_id = normalize_text(value)
    return alias_map.get(user_id, user_id)


def _build_clockin_counts(
    clockin_data_rows: list[dict[str, Any]],
    *,
    titles: list[str] | None = None,
    user_alias_map: dict[str, str] | None = None,
) -> dict[str, int]:
    alias_map = _normalize_user_alias_map(user_alias_map)
    title_set = set(titles or [])
    grouped_keys: dict[str, set[str]] = {}
    for sequence, row in enumerate(clockin_data_rows):
        user_id = _resolve_user_id(row.get("user_id2"), alias_map)
        if not user_id:
            continue
        title = normalize_text(row.get("update_title"))
        if title.startswith("测试-"):
            continue
        if title_set and title not in title_set:
            continue
        task_date = normalize_text(row.get("task_date"))
        if task_date:
            clockin_key = f"task:{task_date}|{title}"
        elif publish_date := _parse_publish_date(row.get("publish_time")):
            clockin_key = f"publish:{publish_date}|{title}"
        else:
            clockin_key = f"row:{sequence}|{title}"
        grouped_keys.setdefault(user_id, set()).add(clockin_key)
    return {user_id: len(keys) for user_id, keys in grouped_keys.items()}


def _compact_video_data_sheet(session: Session, sheet: Any, rows: list[dict[str, Any]]) -> bool:
    current_document = dict(sheet.document_json or {})
    next_document = make_table_document_from_dicts(
        columns=VIDEO_DATA_COLUMNS,
        rows=rows,
        numeric_columns={
            "lesson_data_id",
            "stay_seconds",
            "cum_seconds",
            "studio_seconds",
            "playback_seconds",
            "progress",
            "shop_id",
            "lesson_id",
        },
        page_size=200,
    )
    next_document["source_meta"] = dict(current_document.get("source_meta") or {})
    if next_document == current_document:
        return False
    update_course_sheet_document(sheet, next_document)
    session.add(sheet)
    return True


def build_fanbei_attendance_step2_data_from_course_sheets(
    session: Session,
    *,
    attendance_sheet_id: int = FANBEI_ATTENDANCE_SHEET_NUMERIC_ID,
    clockin_titles: list[str] | None = None,
    compact_video_data: bool = True,
    user_alias_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    attendance = get_sheet(session, attendance_sheet_id)
    bundle = _load_fanbei_course_sheet_bundle(session, attendance=attendance)
    attendance_document = dict(attendance.document_json or {})
    columns = _normalize_document_columns(attendance_document)
    rows = [normalize_row(row, len(columns)) for row in _extract_document_rows(attendance_document)]
    registration_identity_map = build_registration_identity_map(
        session,
        attendance=attendance,
        default_owner_key=FANBEI_OWNER_KEY,
    )
    user_ids = [
        (attendance_row_user_ids(row, columns, registration_identity_map=registration_identity_map) or [""])[0]
        for row in rows
    ]

    video_config_rows = document_dict_rows(dict(bundle[VIDEO_CONFIG_SHEET_KEY].document_json or {}))
    video_data_sheet = bundle[VIDEO_DATA_SHEET_KEY]
    video_data_rows = document_dict_rows(dict(video_data_sheet.document_json or {}))
    clockin_data_rows = document_dict_rows(dict(bundle[CLOCKIN_DATA_SHEET_KEY].document_json or {}))
    effective_user_alias_map = {
        **build_registration_user_alias_map(registration_identity_map),
        **_normalize_user_alias_map(user_alias_map),
    }

    lesson_columns, video_progress, compacted_video_rows = _build_video_progress(
        video_config_rows,
        video_data_rows,
        user_alias_map=effective_user_alias_map,
    )
    clockin_counts = _build_clockin_counts(
        clockin_data_rows,
        titles=clockin_titles or DEFAULT_CLOCKIN_TITLES,
        user_alias_map=effective_user_alias_map,
    )
    if compact_video_data and not effective_user_alias_map:
        _compact_video_data_sheet(session, video_data_sheet, compacted_video_rows)

    output_columns = ["user_id2", "打卡数", *lesson_columns]
    output_rows: list[list[Any]] = []
    for user_id in user_ids:
        row = [user_id, clockin_counts.get(user_id, "")]
        row.extend(video_progress.get((user_id, column), "") for column in lesson_columns)
        output_rows.append(row)

    return {
        "columns": output_columns,
        "rows": output_rows,
        "user_count": len(user_ids),
        "video_config_rows": len(video_config_rows),
        "video_data_rows": len(video_data_rows),
        "video_data_compacted_rows": len(compacted_video_rows),
        "clockin_data_rows": len(clockin_data_rows),
        "clockin_titles": clockin_titles or DEFAULT_CLOCKIN_TITLES,
    }


def _build_step2_column_map(sheet_columns: list[str], data_columns: list[str]) -> dict[int, int]:
    lesson_column_by_number = {
        number: index
        for index, column in enumerate(sheet_columns)
        if (number := _extract_lesson_number(column)) is not None
    }
    mapping: dict[int, int] = {}
    for data_index, data_column in enumerate(data_columns):
        if data_index == 0 or normalize_text(data_column) == "user_id2":
            continue
        sheet_index = _find_column_index(sheet_columns, data_column)
        if sheet_index is None:
            lesson_number = _extract_lesson_number(data_column)
            if lesson_number is not None:
                sheet_index = lesson_column_by_number.get(lesson_number)
        if sheet_index is not None:
            mapping[data_index] = sheet_index
    return mapping


def rebuild_fanbei_attendance_from_course_sheets(
    session: Session,
    *,
    attendance_sheet_id: int = FANBEI_ATTENDANCE_SHEET_NUMERIC_ID,
    clockin_titles: list[str] | None = None,
    user_alias_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    attendance = get_sheet(session, attendance_sheet_id)
    current_document = copy.deepcopy(dict(attendance.document_json or {}))
    columns = _normalize_document_columns(current_document)
    rows = [normalize_row(row, len(columns)) for row in _extract_document_rows(current_document)]

    step2_data = build_fanbei_attendance_step2_data_from_course_sheets(
        session,
        attendance_sheet_id=attendance_sheet_id,
        clockin_titles=clockin_titles,
        compact_video_data=True,
        user_alias_map=user_alias_map,
    )
    data_columns = [normalize_text(column) for column in step2_data["columns"]]
    data_rows = step2_data["rows"]
    if len(data_rows) != len(rows):
        raise RuntimeError(f"step2 返回行数不匹配：sheet={len(rows)} storage={len(data_rows)}")

    column_map = _build_step2_column_map(columns, data_columns)
    if not column_map:
        raise RuntimeError("step2 存储字段无法映射到考勤表列")

    next_rows: list[list[Any]] = []
    updated_rows = 0
    updated_cells = 0
    for row, data_row in zip(rows, data_rows):
        next_row = list(row)
        normalized_data_row = normalize_row(data_row, len(data_columns))
        if not normalize_text(normalized_data_row[0] if normalized_data_row else ""):
            next_rows.append(next_row)
            continue
        changed = False
        for data_index, sheet_index in column_map.items():
            value = normalized_data_row[data_index]
            if next_row[sheet_index] != value:
                next_row[sheet_index] = value
                changed = True
                updated_cells += 1
        if changed:
            updated_rows += 1
        next_rows.append(next_row)

    next_document = _replace_document_data_rows(dict(current_document), next_rows)
    if next_document != current_document:
        attendance.document_json = next_document
        attendance.version = max(int(attendance.version or 1), 1) + 1
        import time

        attendance.updated_at = time.time()
        session.add(attendance)

    return {
        "attendance_sheet_id": int(attendance.numeric_id or 0),
        "updated_rows": updated_rows,
        "updated_cells": updated_cells,
        "mapped_columns": len(column_map),
        **{key: step2_data[key] for key in [
            "video_config_rows",
            "video_data_rows",
            "video_data_compacted_rows",
            "clockin_data_rows",
        ]},
    }


def list_fanbei_course_storage_sheets(
    session: Session,
    *,
    attendance_sheet_id: int = FANBEI_ATTENDANCE_SHEET_NUMERIC_ID,
) -> list[dict[str, Any]]:
    attendance = get_sheet(session, attendance_sheet_id)
    bundle = _load_fanbei_course_sheet_bundle(session, attendance=attendance)
    return [
        {
            "sheet_key": key,
            "id": int(sheet.numeric_id or 0),
            "title": sheet.title,
            "rows": len(_extract_document_rows(dict(sheet.document_json or {}))),
        }
        for key, sheet in bundle.items()
    ]
