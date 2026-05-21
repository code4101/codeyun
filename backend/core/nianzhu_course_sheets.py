from __future__ import annotations

import contextlib
import copy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import fnmatch
import json
from pathlib import Path
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
    ThresholdRefundRule,
    highlight_percentage_refund_progress,
    highlight_presence_progress,
    highlight_threshold_refund_progress,
    parse_progress_percent,
    set_cell_background,
    sheet_text,
)
from backend.core.sheet_identity import allocate_new_sheet_identity
from backend.core.sheet_refs import (
    load_sheets_by_refs,
    sheet_public_id,
    sheet_ref_aliases,
    workbook_public_id,
    workbook_ref_aliases,
)
from backend.models import SheetDocument, WorkbookDocument, WorkbookSheetLink


NIANZHU_WORKBOOK_NUMERIC_ID = 7
NIANZHU_ATTENDANCE_SHEET_NUMERIC_ID = 21
NIANZHU_COURSE_NAME = "d250106念住闯关"
NIANZHU_OWNER_KEY = "20250106-nianzhu-chuangguan"

ATTENDANCE_SHEET_KEY = "attendance"
VIDEO_CONFIG_SHEET_KEY = "video_config"
VIDEO_DATA_SHEET_KEY = "video_data"
CLOCKIN_CONFIG_SHEET_KEY = "clockin_config"
CLOCKIN_DATA_SHEET_KEY = "clockin_data"

TRACKING_GROUP_COLUMN = "追踪分组"
ACTIVE_TRACKING_GROUP = "B组"
RULE_VERSION_COLUMN = "规则版本"
CURRENT_RULE = "当前规则"
LEGACY_AFTER_20250522_RULE = "旧规则-20250522后"
LEGACY_BEFORE_20250522_RULE = "旧规则-20250522前"

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
CLOCKIN_EXTRA_COLUMN_PREFIX = "extra_"

DEFAULT_VIDEO_RULES: dict[str, str] = {
    CURRENT_RULE: "90%=20",
    LEGACY_AFTER_20250522_RULE: "50%=20",
    LEGACY_BEFORE_20250522_RULE: "90%=10;150%=15;200%=20",
}
DEFAULT_CLOCKIN_RULE = "5=100;10=150;15=200"


@dataclass(frozen=True)
class CourseSheetSpec:
    sheet_key: str
    title: str
    order_index: int


@dataclass(frozen=True)
class VideoConfigItem:
    order_index: int
    lesson_id: str
    course_key: str
    lesson_name: str
    item_type: str
    lesson_number: int | None
    participates_refund: bool
    participates_score: bool
    rules_by_version: dict[str, list[PercentageRefundRule]]


COURSE_SHEET_SPECS = [
    CourseSheetSpec(VIDEO_CONFIG_SHEET_KEY, "视频配置", 30),
    CourseSheetSpec(VIDEO_DATA_SHEET_KEY, "视频数据", 40),
    CourseSheetSpec(CLOCKIN_CONFIG_SHEET_KEY, "打卡配置", 50),
    CourseSheetSpec(CLOCKIN_DATA_SHEET_KEY, "打卡数据", 60),
]

COURSE_LOCAL_NAME_COLUMNS = {
    VIDEO_CONFIG_SHEET_KEY: ("lesson_name",),
    VIDEO_DATA_SHEET_KEY: ("lesson_name",),
    CLOCKIN_CONFIG_SHEET_KEY: ("name",),
    CLOCKIN_DATA_SHEET_KEY: ("clockin_name",),
}


def _normalize_text(value: Any) -> str:
    return sheet_text(value)


def _strip_course_name_prefix(value: Any, course_name: str) -> str:
    text = _normalize_text(value)
    prefix = _normalize_text(course_name)
    if not text or not prefix or not text.startswith(prefix):
        return text
    suffix = text[len(prefix):].strip()
    suffix = re.sub(r"^[\s\-－—–_:：]+", "", suffix).strip()
    return suffix or text


def _normalize_row(row: Any, column_count: int) -> list[Any]:
    if isinstance(row, list):
        return [*row[:column_count], *([""] * max(column_count - len(row), 0))]
    if isinstance(row, dict):
        return [row.get(str(index), "") for index in range(column_count)]
    return [""] * column_count


def _normalize_bool(value: Any) -> bool:
    text = _normalize_text(value).lower()
    return text in {"1", "true", "yes", "y", "是", "参与", "计入"}


def _format_numeric_cell(value: float) -> int | float:
    rounded = round(value)
    if abs(value - rounded) < 1e-9:
        return int(rounded)
    return round(value, 2)


def _to_float(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)
    text = _normalize_text(value).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0


def _find_column_index(columns: list[str], header: str) -> int | None:
    normalized = _normalize_text(header)
    for index, column in enumerate(columns):
        if _normalize_text(column) == normalized:
            return index
    return None


def _require_column_index(columns: list[str], header: str) -> int:
    index = _find_column_index(columns, header)
    if index is None:
        raise RuntimeError(f"考勤表缺少 {header} 列")
    return index


def _extract_lesson_number(value: Any) -> int | None:
    match = re.search(r"第\s*0*(\d+)\s*课", _normalize_text(value))
    return int(match.group(1)) if match else None


def _extract_play_count(value: Any) -> int:
    match = re.search(r"(\d+)\s*遍", _normalize_text(value))
    return int(match.group(1)) if match else 0


def _strip_progress_title(value: Any) -> str:
    text = _normalize_text(value)
    return re.sub(r"^\d{1,2}:\d{2}\s*[~～-]\s*\d{1,2}:\d{2}\s*", "", text).strip()


def _format_legacy_value(value: Any) -> Any:
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
    return _normalize_text(value)


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    text = value.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _extra_column_name(key: Any) -> str:
    return f"{CLOCKIN_EXTRA_COLUMN_PREFIX}{_normalize_text(key).strip()}"


def _clockin_extra_columns_from_dicts(rows: list[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        extra = _parse_json_object(row.get("extra"))
        for key in extra:
            column = _extra_column_name(key)
            if column == CLOCKIN_EXTRA_COLUMN_PREFIX or column in seen:
                continue
            seen.add(column)
            columns.append(column)
    return columns


def _clockin_data_columns_for_rows(
    rows: list[dict[str, Any]],
    *,
    existing_columns: list[str] | None = None,
) -> list[str]:
    columns = [*CLOCKIN_DATA_BASE_COLUMNS]
    seen = set(columns)
    for column in existing_columns or []:
        if column.startswith(CLOCKIN_EXTRA_COLUMN_PREFIX) and column not in seen:
            seen.add(column)
            columns.append(column)
    for column in _clockin_extra_columns_from_dicts(rows):
        if column not in seen:
            seen.add(column)
            columns.append(column)
    return columns


def _clockin_data_dict_to_row(row: dict[str, Any], columns: list[str]) -> list[Any]:
    extra = _parse_json_object(row.get("extra"))
    values: list[Any] = []
    for column in columns:
        if column.startswith(CLOCKIN_EXTRA_COLUMN_PREFIX):
            values.append(_format_legacy_value(extra.get(column.removeprefix(CLOCKIN_EXTRA_COLUMN_PREFIX))))
        else:
            values.append(_format_legacy_value(row.get(column)))
    return values


def _extract_qa_editions(value: Any) -> tuple[int, ...]:
    text = _normalize_text(value)
    match = re.search(r"第\s*([0-9、,，\s]+)\s*届.*?答疑", text)
    if not match:
        return ()
    editions: list[int] = []
    for token in re.split(r"[、,，\s]+", match.group(1)):
        if not token:
            continue
        try:
            editions.append(int(token))
        except ValueError:
            continue
    return tuple(editions)


def _course_item_key(value: Any) -> str:
    lesson_number = _extract_lesson_number(value)
    if lesson_number is not None:
        return f"lesson:{lesson_number}"
    qa_editions = _extract_qa_editions(value)
    if qa_editions:
        return "qa:" + ",".join(str(item) for item in qa_editions)
    return ""


def _course_item_parts(value: Any) -> tuple[str, str, str, int, str]:
    key = _course_item_key(value)
    if key.startswith("lesson:"):
        lesson_number = int(key.split(":", 1)[1])
        return key, "课次", str(lesson_number), lesson_number, f"L{lesson_number:02d}"
    if key.startswith("qa:"):
        editions = [int(item) for item in key.split(":", 1)[1].split(",") if item]
        label = "、".join(str(item) for item in editions)
        local_id = "Q" + "-".join(str(item) for item in editions)
        first = editions[0] if editions else 0
        return key, "答疑", f"第{label}届答疑", 1000 + first, local_id
    return "", "视频", "", 9000, ""


def _query_legacy_lesson_rows(course_name: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from kq5034.attendance_api import get_kqdb  # type: ignore
    except Exception as exc:
        return [], f"无法导入 kq5034.attendance_api.get_kqdb: {exc}"

    terms = [course_name]
    if "念住闯关" not in terms:
        terms.append("念住闯关")
    terms = [term for index, term in enumerate(terms) if term and term not in terms[:index]]
    where = " OR ".join("lesson_name LIKE %s" for _ in terms)
    params = [f"%{term}%" for term in terms]
    sql = (
        "SELECT lesson_id, start_date, end_date, next_update, lesson_id2, shop_id, "
        "lesson_name, video_duration "
        f"FROM lesson_table WHERE {where} ORDER BY lesson_id"
    )
    try:
        xldb = get_kqdb()
        try:
            records = xldb.exec2dict(sql, params)
        except TypeError:
            fallback_sql = sql
            for param in params:
                quoted = "'" + param.replace("'", "''") + "'"
                fallback_sql = fallback_sql.replace("%s", quoted, 1)
            records = xldb.exec2dict(fallback_sql)
    except Exception as exc:
        return [], f"查询 lesson_table 失败: {exc}"

    return [dict(record) for record in _legacy_records(records)], None


def _legacy_records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "fetchall"):
        value = value.fetchall()
    return [dict(record) for record in (value or []) if isinstance(record, dict)]


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

    terms = [course_name]
    if "念住闯关" not in terms:
        terms.append("念住闯关")
    terms = [term for index, term in enumerate(terms) if term and term not in terms[:index]]
    where = " OR ".join("name LIKE %s" for _ in terms)
    params = [f"%{term}%" for term in terms]
    sql = (
        "SELECT clockin_id, name, url, start_date, end_date, days, clockin_user_num, total_user_num "
        f"FROM clockin_table WHERE {where} ORDER BY clockin_id"
    )
    try:
        xldb = get_kqdb()
        try:
            records = xldb.exec2dict(sql, params)
        except TypeError:
            fallback_sql = sql
            for param in params:
                fallback_sql = fallback_sql.replace("%s", "'" + param.replace("'", "''") + "'", 1)
            records = xldb.exec2dict(fallback_sql)
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


def _progress_column_range(columns: list[str]) -> range:
    start = _find_column_index(columns, "打卡数")
    if start is None:
        start = next(
            (index for index, column in enumerate(columns) if _extract_lesson_number(column) is not None),
            -1,
        )
    else:
        start += 1
    if start < 0 or start >= len(columns):
        return range(0)

    end = _find_column_index(columns, TRACKING_GROUP_COLUMN)
    if end is None:
        end = len(columns)
    return range(start, max(start, end))


def _document_cell_link(document: dict[str, Any], *, row_index: int, column_index: int) -> str:
    meta = document.get("cell_meta")
    if not isinstance(meta, dict):
        return ""
    cell_meta = meta.get(f"{row_index}:{column_index}")
    if not isinstance(cell_meta, dict):
        return ""
    link = cell_meta.get("link")
    if not isinstance(link, dict):
        return ""
    return _normalize_text(link.get("url"))


def _parse_percentage_rules(value: Any) -> list[PercentageRefundRule]:
    text = _normalize_text(value)
    rules: list[PercentageRefundRule] = []
    for threshold, amount in re.findall(r"(\d+(?:\.\d+)?)\s*%?\s*(?:=|:|：)\s*(\d+(?:\.\d+)?)", text):
        threshold_value = float(threshold)
        amount_value = float(amount)
        if threshold_value > 0 and amount_value > 0:
            rules.append(PercentageRefundRule(threshold_value, amount_value))
    return sorted(rules, key=lambda item: item.threshold_percent)


def _parse_threshold_rules(value: Any) -> list[ThresholdRefundRule]:
    text = _normalize_text(value)
    rules: list[ThresholdRefundRule] = []
    for threshold, amount in re.findall(r"(\d+(?:\.\d+)?)\s*(?:=|:|：)\s*(\d+(?:\.\d+)?)", text):
        threshold_value = float(threshold)
        amount_value = float(amount)
        if threshold_value > 0 and amount_value > 0:
            rules.append(ThresholdRefundRule(threshold_value, amount_value))
    return sorted(rules, key=lambda item: item.threshold)


def _parse_clockin_rule_from_formula(value: Any) -> str:
    text = _normalize_text(value)
    pairs = [
        (float(threshold), float(amount))
        for threshold, amount in re.findall(r">=\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)", text)
    ]
    if not pairs:
        return ""
    pairs.sort(key=lambda item: item[0])
    return ";".join(f"{_format_numeric_cell(threshold)}={_format_numeric_cell(amount)}" for threshold, amount in pairs)


def _first_nonempty_row_value(rows: list[list[Any]], column_index: int) -> Any:
    for row in rows:
        if column_index < len(row) and _normalize_text(row[column_index]):
            return row[column_index]
    return ""


def _create_simple_document(
    *,
    columns: list[str],
    rows: list[list[Any]],
    numeric_columns: set[str] | None = None,
    hidden_columns: set[str] | None = None,
    page_size: int = 100,
) -> dict[str, Any]:
    numeric_columns = numeric_columns or set()
    hidden_columns = hidden_columns or set()
    column_configs: dict[str, dict[str, Any]] = {}
    for column in columns:
        config: dict[str, Any] = {}
        if column in numeric_columns:
            config["value_type"] = "number"
        if column in hidden_columns:
            config["hidden"] = True
        if config:
            column_configs[column] = config
    return {
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "grid_rows": [columns, *rows],
        "data_start_row": 1,
        "field_row_index": 0,
        "column_configs": column_configs,
        "cell_meta": {},
        "merged_cells": [],
        "header_groups": [],
        "formula_reference_origin": "sheet_v2",
        "view_settings": {
            "show_row_numbers": True,
            "row_marker_numbering": "global",
            "row_marker_origin": "sheet",
            "show_column_markers": True,
            "column_marker_style": "letters",
            "height_mode": "fill",
            "pagination": {"enabled": len(rows) > page_size, "page_size": page_size},
        },
    }


def _sheet_rows_as_dicts(document: dict[str, Any]) -> list[dict[str, Any]]:
    columns = _normalize_document_columns(document)
    rows = [_normalize_row(row, len(columns)) for row in _extract_document_rows(document)]
    return [dict(zip(columns, row)) for row in rows]


def _row_identity_key(row: dict[str, Any] | list[Any], columns: list[str] | None = None) -> str:
    if isinstance(row, list):
        if columns is None:
            return ""
        mapping = dict(zip(columns, _normalize_row(row, len(columns))))
    else:
        mapping = row
    user_id = _normalize_text(mapping.get("用户ID")) or _normalize_text(mapping.get("user_id2"))
    if user_id:
        return f"user:{user_id}"
    student_id = _normalize_text(mapping.get("学号"))
    if student_id:
        return f"student:{student_id}"
    return ""


def _build_video_config_document(
    attendance_document: dict[str, Any],
    *,
    course_name: str = NIANZHU_COURSE_NAME,
) -> dict[str, Any]:
    columns = _normalize_document_columns(attendance_document)
    progress_by_key: dict[str, tuple[int, str]] = {}
    for column_index in _progress_column_range(columns):
        field_name = columns[column_index]
        key = _course_item_key(field_name)
        if key:
            progress_by_key.setdefault(key, (column_index, field_name))

    legacy_rows, legacy_error = _query_legacy_lesson_rows(course_name)
    rows: list[list[Any]] = []
    used_progress_keys: set[str] = set()
    legacy_lesson_id_map: dict[str, int] = {}

    for legacy_index, legacy_row in enumerate(legacy_rows, start=1):
        legacy_lesson_id = _normalize_text(legacy_row.get("lesson_id"))
        if legacy_lesson_id:
            legacy_lesson_id_map[legacy_lesson_id] = legacy_index
        legacy_lesson_name = _normalize_text(legacy_row.get("lesson_name"))
        lesson_name = _strip_course_name_prefix(legacy_lesson_name, course_name)
        key, _item_type, _lesson_number_text, _order_index, _local_id = _course_item_parts(lesson_name)
        progress = progress_by_key.get(key) if key else None
        if progress is not None:
            used_progress_keys.add(key)
        rows.append([
            legacy_index,
            _format_legacy_value(legacy_row.get("start_date")),
            _format_legacy_value(legacy_row.get("end_date")),
            _format_legacy_value(legacy_row.get("next_update")),
            _format_legacy_value(legacy_row.get("lesson_id2")),
            _format_legacy_value(legacy_row.get("shop_id")),
            lesson_name,
            _format_legacy_value(legacy_row.get("video_duration")),
        ])

    for fallback_index, column_index in enumerate(_progress_column_range(columns), start=1):
        field_name = columns[column_index]
        key, _item_type, _lesson_number_text, _order_index, _local_id = _course_item_parts(field_name)
        if key and key in used_progress_keys:
            continue
        rows.append([
            len(rows) + 1,
            "",
            "",
            "",
            "",
            "",
            field_name,
            "",
        ])
    document = _create_simple_document(
        columns=VIDEO_CONFIG_COLUMNS,
        rows=rows,
        numeric_columns={"lesson_id", "shop_id", "video_duration"},
        page_size=100,
    )
    document["source_meta"] = {
        "course_name": course_name,
        "legacy_lesson_rows": len(legacy_rows),
        "legacy_lesson_error": legacy_error,
        "legacy_lesson_id_map": legacy_lesson_id_map,
    }
    return document


def _build_video_data_document(attendance_document: dict[str, Any], video_config_document: dict[str, Any]) -> dict[str, Any]:
    columns = _normalize_document_columns(attendance_document)
    rows = [_normalize_row(row, len(columns)) for row in _extract_document_rows(attendance_document)]
    lesson_id_by_key = {
        _course_item_key(row.get("lesson_name")): _normalize_text(row.get("lesson_id"))
        for row in _sheet_rows_as_dicts(video_config_document)
        if _course_item_key(row.get("lesson_name")) and _normalize_text(row.get("lesson_id"))
    }
    source_meta = dict(video_config_document.get("source_meta") or {})
    legacy_lesson_id_map = {
        _normalize_text(key): int(value)
        for key, value in dict(source_meta.get("legacy_lesson_id_map") or {}).items()
        if _normalize_text(key) and _to_float(value) > 0
    }
    legacy_rows, legacy_error = _query_legacy_lesson_data_rows([int(item) for item in legacy_lesson_id_map])
    if legacy_rows:
        result: list[list[Any]] = []
        for local_index, legacy_row in enumerate(legacy_rows, start=1):
            local_lesson_id = legacy_lesson_id_map.get(_normalize_text(legacy_row.get("lesson_id")))
            if local_lesson_id is None:
                continue
            result.append([
                local_index,
                _format_legacy_value(legacy_row.get("user_id2")),
                _format_legacy_value(legacy_row.get("remark_nm")),
                _format_legacy_value(legacy_row.get("state")),
                _format_legacy_value(legacy_row.get("stay_seconds")),
                _format_legacy_value(legacy_row.get("cum_seconds")),
                _format_legacy_value(legacy_row.get("studio_seconds")),
                _format_legacy_value(legacy_row.get("playback_seconds")),
                _format_legacy_value(legacy_row.get("num_of_comments")),
                _format_legacy_value(legacy_row.get("studio_amount")),
                _format_legacy_value(legacy_row.get("study_state")),
                _format_legacy_value(legacy_row.get("progress")),
                _format_legacy_value(legacy_row.get("last_play_time")),
                _format_legacy_value(legacy_row.get("shop_id")),
                _format_legacy_value(legacy_row.get("update_time")),
                local_lesson_id,
                _format_legacy_value(legacy_row.get("finish_time")),
                _format_legacy_value(legacy_row.get("comment_times")),
                _format_legacy_value(legacy_row.get("money")),
                _strip_course_name_prefix(
                    _format_legacy_value(legacy_row.get("lesson_name")),
                    _normalize_text(source_meta.get("course_name")),
                ),
            ])
        document = _create_simple_document(
            columns=VIDEO_DATA_COLUMNS,
            rows=result,
            numeric_columns={
                "lesson_data_id",
                "stay_seconds",
                "cum_seconds",
                "studio_seconds",
                "playback_seconds",
                "num_of_comments",
                "studio_amount",
                "progress",
                "shop_id",
                "lesson_id",
                "comment_times",
                "money",
            },
            page_size=200,
        )
        document["source_meta"] = {
            "legacy_lesson_data_rows": len(legacy_rows),
            "legacy_lesson_data_error": legacy_error,
        }
        return document

    source_time = datetime.now().isoformat(sep=" ", timespec="seconds")
    student_id_index = _find_column_index(columns, "学号")
    name_index = _find_column_index(columns, "姓名")
    user_id_index = _find_column_index(columns, "用户ID")
    result: list[list[Any]] = []
    local_index = 0
    for row in rows:
        user_id = row[user_id_index] if user_id_index is not None else ""
        for column_index in _progress_column_range(columns):
            progress_text = _normalize_text(row[column_index])
            if not progress_text:
                continue
            field_name = columns[column_index]
            lesson_id = lesson_id_by_key.get(_course_item_key(field_name), "")
            if not lesson_id:
                continue
            local_index += 1
            progress = parse_progress_percent(progress_text) or 0
            study_state = "已完成" if _extract_play_count(progress_text) > 0 else "学习中"
            result.append([
                local_index,
                user_id,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                study_state,
                _format_numeric_cell(progress),
                "",
                "",
                source_time,
                _format_numeric_cell(_to_float(lesson_id)),
                "",
                "",
                "",
                "",
            ])
    return _create_simple_document(
        columns=VIDEO_DATA_COLUMNS,
        rows=result,
        numeric_columns={"lesson_data_id", "progress", "lesson_id"},
        page_size=200,
    )


def _build_clockin_config_document(
    attendance_document: dict[str, Any],
    *,
    course_name: str = NIANZHU_COURSE_NAME,
) -> dict[str, Any]:
    columns = _normalize_document_columns(attendance_document)
    clockin_index = _find_column_index(columns, "打卡数")
    legacy_rows, legacy_error = _query_legacy_clockin_rows(course_name)
    legacy_clockin_id_map: dict[str, int] = {}
    rows: list[list[Any]] = []
    for local_index, legacy_row in enumerate(legacy_rows, start=1):
        legacy_clockin_id = _normalize_text(legacy_row.get("clockin_id"))
        if legacy_clockin_id:
            legacy_clockin_id_map[legacy_clockin_id] = local_index
        rows.append([
            local_index,
            _strip_course_name_prefix(_format_legacy_value(legacy_row.get("name")), course_name),
            _format_legacy_value(legacy_row.get("url")),
            _format_legacy_value(legacy_row.get("start_date")),
            _format_legacy_value(legacy_row.get("end_date")),
            _format_legacy_value(legacy_row.get("days")),
            _format_legacy_value(legacy_row.get("clockin_user_num")),
            _format_legacy_value(legacy_row.get("total_user_num")),
        ])
    if not rows:
        rows.append([1, "打卡数" if clockin_index is not None else "", "", "", "", "", "", ""])
    document = _create_simple_document(
        columns=CLOCKIN_CONFIG_COLUMNS,
        rows=rows,
        numeric_columns={"clockin_id", "days", "clockin_user_num", "total_user_num"},
        page_size=100,
    )
    document["source_meta"] = {
        "course_name": course_name,
        "legacy_clockin_rows": len(legacy_rows),
        "legacy_clockin_error": legacy_error,
        "legacy_clockin_id_map": legacy_clockin_id_map,
    }
    return document


def _build_clockin_data_document(
    attendance_document: dict[str, Any],
    clockin_config_document: dict[str, Any],
) -> dict[str, Any]:
    columns = _normalize_document_columns(attendance_document)
    rows = [_normalize_row(row, len(columns)) for row in _extract_document_rows(attendance_document)]
    user_id_index = _find_column_index(columns, "用户ID")
    clockin_index = _find_column_index(columns, "打卡数")
    source_meta = dict(clockin_config_document.get("source_meta") or {})
    legacy_clockin_id_map = {
        _normalize_text(key): int(value)
        for key, value in dict(source_meta.get("legacy_clockin_id_map") or {}).items()
        if _normalize_text(key) and _to_float(value) > 0
    }
    legacy_rows, legacy_error = _query_legacy_clockin_data_rows([int(item) for item in legacy_clockin_id_map])
    result_dicts: list[dict[str, Any]] = []
    if legacy_rows:
        for local_index, legacy_row in enumerate(legacy_rows, start=1):
            local_clockin_id = legacy_clockin_id_map.get(_normalize_text(legacy_row.get("clockin_id")))
            if local_clockin_id is None:
                continue
            result_dicts.append({
                "clockin_data_id": local_index,
                "user_id2": legacy_row.get("user_id2"),
                "nickname": legacy_row.get("nickname"),
                "groupname": legacy_row.get("groupname"),
                "publish_time": legacy_row.get("publish_time"),
                "update_content": legacy_row.get("update_content"),
                "update_title": legacy_row.get("update_title"),
                "update_type": legacy_row.get("update_type"),
                "tags": legacy_row.get("tags"),
                "read_num": legacy_row.get("read_num"),
                "like_num": legacy_row.get("like_num"),
                "comment_num": legacy_row.get("comment_num"),
                "is_essence": legacy_row.get("is_essence") if legacy_row.get("is_essence") is not None else "",
                "share_num": legacy_row.get("share_num"),
                "update_url": legacy_row.get("update_url"),
                "clockin_name": _strip_course_name_prefix(
                    legacy_row.get("clockin_name"),
                    _normalize_text(source_meta.get("course_name")),
                ),
                "is_repair": legacy_row.get("is_repair") if legacy_row.get("is_repair") is not None else "",
                "task_date": legacy_row.get("task_date"),
                "extra": legacy_row.get("extra"),
                "clockin_id": local_clockin_id,
            })
        clockin_data_columns = _clockin_data_columns_for_rows(result_dicts)
        document = _create_simple_document(
            columns=clockin_data_columns,
            rows=[_clockin_data_dict_to_row(row, clockin_data_columns) for row in result_dicts],
            numeric_columns={
                "clockin_data_id",
                "read_num",
                "like_num",
                "comment_num",
                "share_num",
                "clockin_id",
            },
            page_size=200,
        )
        document["source_meta"] = {
            "legacy_clockin_data_rows": len(legacy_rows),
            "legacy_clockin_data_error": legacy_error,
        }
        return document

    result: list[list[Any]] = []
    if clockin_index is not None:
        local_index = 0
        for row in rows:
            clockin_count = row[clockin_index]
            if not _normalize_text(clockin_count):
                continue
            for _ in range(max(int(_to_float(clockin_count)), 0)):
                local_index += 1
                result.append([
                    local_index,
                    row[user_id_index] if user_id_index is not None else "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "{}",
                    1,
                ])
    return _create_simple_document(
        columns=CLOCKIN_DATA_COLUMNS,
        rows=result,
        numeric_columns={"clockin_data_id", "clockin_id"},
        page_size=200,
    )


def _get_workbook(session: Session, workbook_id: int) -> WorkbookDocument:
    workbook = session.exec(select(WorkbookDocument).where(WorkbookDocument.numeric_id == int(workbook_id))).first()
    if workbook is None:
        raise RuntimeError(f"工作簿不存在：workbook_id={workbook_id}")
    return workbook


def _get_sheet(session: Session, sheet_id: int) -> SheetDocument:
    sheet = session.exec(select(SheetDocument).where(SheetDocument.numeric_id == int(sheet_id))).first()
    if sheet is None:
        raise RuntimeError(f"表格不存在：sheet_id={sheet_id}")
    return sheet


def _find_course_sheet(session: Session, *, owner_key: str, sheet_key: str) -> SheetDocument | None:
    return session.exec(
        select(SheetDocument)
        .where(SheetDocument.scope == "notes")
        .where(SheetDocument.owner_type == "course_workbook")
        .where(SheetDocument.owner_key == owner_key)
        .where(SheetDocument.sheet_key == sheet_key)
    ).first()


def _ensure_workbook_link(
    session: Session,
    *,
    workbook: WorkbookDocument,
    sheet: SheetDocument,
    order_index: int,
) -> bool:
    link = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id.in_(workbook_ref_aliases(workbook)))
        .where(WorkbookSheetLink.sheet_id.in_(sheet_ref_aliases(sheet)))
    ).first()
    if link is None:
        session.add(
            WorkbookSheetLink(
                workbook_id=workbook_public_id(workbook),
                sheet_id=sheet_public_id(sheet),
                order_index=order_index,
                created_at=time.time(),
            )
        )
        return True
    changed = False
    workbook_ref = workbook_public_id(workbook)
    sheet_ref = sheet_public_id(sheet)
    if link.workbook_id != workbook_ref:
        link.workbook_id = workbook_ref
        changed = True
    if link.sheet_id != sheet_ref:
        link.sheet_id = sheet_ref
        changed = True
    if int(link.order_index or 0) != int(order_index):
        link.order_index = int(order_index)
        changed = True
    if changed:
        session.add(link)
    return changed


def _upsert_course_sheet(
    session: Session,
    *,
    workbook: WorkbookDocument,
    owner_key: str,
    spec: CourseSheetSpec,
    document_json: dict[str, Any],
    owner_user_id: int | None,
    replace: bool,
) -> tuple[SheetDocument, bool, bool]:
    now = time.time()
    sheet = _find_course_sheet(session, owner_key=owner_key, sheet_key=spec.sheet_key)
    created = False
    changed = False
    if sheet is None:
        identity = allocate_new_sheet_identity(session)
        sheet = SheetDocument(
            id=identity.primary_id,
            numeric_id=identity.numeric_id,
            legacy_id=identity.legacy_id,
            scope="notes",
            owner_type="course_workbook",
            owner_key=owner_key,
            sheet_key=spec.sheet_key,
            title=spec.title,
            engine="handsontable",
            document_json=document_json,
            version=1,
            owner_user_id=owner_user_id,
            created_by_user_id=owner_user_id,
            updated_by_user_id=owner_user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(sheet)
        session.flush()
        created = True
        changed = True
    elif replace:
        if sheet.title != spec.title or dict(sheet.document_json or {}) != document_json:
            sheet.title = spec.title
            sheet.document_json = document_json
            sheet.version = max(int(sheet.version or 1), 1) + 1
            sheet.updated_by_user_id = owner_user_id
            sheet.updated_at = now
            changed = True
        if sheet.owner_user_id is None and owner_user_id is not None:
            sheet.owner_user_id = owner_user_id
            changed = True
        if changed:
            session.add(sheet)

    link_changed = _ensure_workbook_link(session, workbook=workbook, sheet=sheet, order_index=spec.order_index)
    return sheet, created, changed or link_changed


def _course_sheet_documents_from_attendance(
    attendance_document: dict[str, Any],
    *,
    course_name: str = NIANZHU_COURSE_NAME,
) -> dict[str, dict[str, Any]]:
    video_config_document = _build_video_config_document(attendance_document, course_name=course_name)
    clockin_config_document = _build_clockin_config_document(attendance_document, course_name=course_name)
    return {
        VIDEO_CONFIG_SHEET_KEY: video_config_document,
        VIDEO_DATA_SHEET_KEY: _build_video_data_document(attendance_document, video_config_document),
        CLOCKIN_CONFIG_SHEET_KEY: clockin_config_document,
        CLOCKIN_DATA_SHEET_KEY: _build_clockin_data_document(attendance_document, clockin_config_document),
    }


def _parse_datetime_cell(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = _normalize_text(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d"):
        with contextlib.suppress(ValueError):
            return datetime.strptime(text, fmt)
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(text)
    return None


def _format_datetime_for_sheet(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _compute_next_lesson_update(
    row: dict[str, Any],
    *,
    course_name: str = "",
    now: datetime | None = None,
) -> str:
    now = now or datetime.now()
    lesson_name = _normalize_text(row.get("lesson_name"))
    rule_text = f"{_normalize_text(course_name)} {lesson_name}".strip()
    start_date = _parse_datetime_cell(row.get("start_date")) or now
    end_date = _parse_datetime_cell(row.get("end_date")) or datetime(9999, 12, 31, 23, 59, 59)
    current_next_update = _parse_datetime_cell(row.get("next_update")) or start_date
    video_duration = _to_float(row.get("video_duration"))
    video_end_time = start_date if "闯关" in rule_text else start_date + timedelta(seconds=video_duration)
    update_interval = timedelta(days=7 if "禅宗" in rule_text else 1)

    if now < video_end_time:
        return _format_datetime_for_sheet(video_end_time)
    if now >= end_date:
        return _format_datetime_for_sheet(datetime(9999, 12, 31, 23, 59, 59))

    next_time = current_next_update if "禅宗" in rule_text else video_end_time
    while next_time <= now:
        next_time += update_interval
    if next_time > end_date:
        next_time = end_date
    return _format_datetime_for_sheet(next_time)


def _read_export_table(file: str | Path):
    import pandas as pd

    file_text = str(file)
    try:
        df = pd.read_excel(file_text)
    except ValueError:
        df = pd.read_csv(file_text)
    df = df.where(pd.notna(df), "")
    return df


def _parse_time_text(value: Any) -> str:
    text = _normalize_text(value).strip()
    return "" if not text or "--" in text else text


def _parse_export_progress(value: Any) -> int | float | str:
    text = _normalize_text(value).strip()
    if not text:
        return ""
    numeric = _to_float(text)
    return _format_numeric_cell(numeric)


def _parse_duration_seconds(value: Any) -> int | float:
    if isinstance(value, int | float):
        return _format_numeric_cell(float(value))
    text = _normalize_text(value).strip()
    if not text:
        return 0
    seconds = 0
    matched = False
    for pattern, factor in [(r"(\d+)小时", 3600), (r"(\d+)分钟", 60), (r"(\d+)(?:秒|$)", 1)]:
        match = re.search(pattern, text)
        if match:
            matched = True
            seconds += int(match.group(1)) * factor
    if matched:
        return seconds
    return _format_numeric_cell(_to_float(text))


def _parse_lesson_data_export_rows(
    file: str | Path,
    *,
    lesson_id: int,
    update_time: datetime | None = None,
) -> list[dict[str, Any]]:
    df = _read_export_table(file)
    if "累计观看时长(秒)" not in df and "累计观看时长" in df and "累计播放时长（秒）" not in df:
        df["累计播放时长（秒）"] = df["累计观看时长"].map(_parse_duration_seconds)
    if "播放进度" in df:
        df["播放进度"] = df["播放进度"].map(_parse_export_progress)
    for name in ["上次播放时间", "完成时间"]:
        if name in df:
            df[name] = df[name].map(_parse_time_text)

    zhname2en = {
        "用户ID": "user_id2",
        "参与状态": "study_state",
        "播放进度": "progress",
        "累计观看时长(秒)": "cum_seconds",
        "累计播放时长（秒）": "cum_seconds",
        "上次播放时间": "last_play_time",
        "完成时间": "finish_time",
        "直播间停留时长(秒)": "stay_seconds",
        "直播观看时长(秒)": "studio_seconds",
        "回放观看时长(秒)": "playback_seconds",
        "评论次数": "comment_times",
        "直播间成交金额": "money",
    }
    source_time = update_time or datetime.now()
    rows: list[dict[str, Any]] = []
    for _, source_row in df.iterrows():
        row: dict[str, Any] = {"lesson_id": lesson_id, "update_time": _format_datetime_for_sheet(source_time)}
        for source_name, target_name in zhname2en.items():
            if source_name in source_row:
                row[target_name] = source_row[source_name]
        rows.append(row)
    return rows


def _parse_clockin_data_export_rows(
    file: str | Path,
    *,
    clockin_id: int,
    clockin_name: str,
) -> list[dict[str, Any]]:
    df = _read_export_table(file)
    zhname2en = {
        "用户ID": "user_id2",
        "用户昵称": "nickname",
        "分组": "groupname",
        "发布时间": "publish_time",
        "动态内容": "update_content",
        "动态标题": "update_title",
        "动态类型": "update_type",
        "标签": "tags",
        "阅读人数": "read_num",
        "点赞数": "like_num",
        "评论数": "comment_num",
        "精华主题": "is_essence",
        "分享次数": "share_num",
        "动态链接": "update_url",
        "user_id": "user_id2",
        "是否补打卡": "is_repair",
        "打卡日历": "task_date",
        "打卡时间": "publish_time",
        "所属主题": "update_title",
        "日记链接": "update_url",
        "文字内容": "update_content",
        "是否精选": "is_essence",
        "所属作业": "update_title",
        "用户id": "user_id2",
        "任务名称": "update_title",
    }
    if not any(column in zhname2en for column in df.columns):
        raise RuntimeError(f"打卡文件表头异常，无法写入 sheet：{file}")

    for bool_col in ["精华主题", "是否补打卡", "是否精选"]:
        if bool_col in df:
            df[bool_col] = df[bool_col].map({"是": True, "否": False}).fillna(df[bool_col])
    if "文字内容" in df:
        for idx, source_row in df.iterrows():
            text = _normalize_text(source_row["文字内容"])
            for extra_col in ["图片内容", "语音内容", "视频内容"]:
                if extra_col in source_row and _normalize_text(source_row[extra_col]):
                    text += f"\n{extra_col}：" + _normalize_text(source_row[extra_col])
            df.at[idx, "文字内容"] = text

    rows: list[dict[str, Any]] = []
    for _, source_row in df.iterrows():
        extra: dict[str, Any] = {}
        row: dict[str, Any] = {"clockin_name": clockin_name, "clockin_id": clockin_id, "extra": extra}
        for source_name in source_row.keys():
            value = source_row[source_name]
            target_name = zhname2en.get(source_name)
            if target_name:
                row[target_name] = value
            else:
                extra[source_name] = value
        rows.append(row)
    return rows


def _document_dict_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    return _sheet_rows_as_dicts(document)


def _renumber_rows(rows: list[dict[str, Any]], id_column: str) -> None:
    for index, row in enumerate(rows, start=1):
        row[id_column] = index


def _make_table_document_from_dicts(
    *,
    columns: list[str],
    rows: list[dict[str, Any]],
    numeric_columns: set[str],
    page_size: int,
    source_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document = _create_simple_document(
        columns=columns,
        rows=[[ _format_legacy_value(row.get(column)) for column in columns] for row in rows],
        numeric_columns=numeric_columns,
        page_size=page_size,
    )
    if source_meta:
        document["source_meta"] = source_meta
    return document


def _update_course_sheet_document(sheet: SheetDocument, document: dict[str, Any]) -> None:
    sheet.document_json = document
    sheet.version = max(int(sheet.version or 1), 1) + 1
    sheet.updated_at = time.time()


def _match_clockin_name(name: str, pattern: str) -> bool:
    if not pattern:
        return True
    return fnmatch.fnmatchcase(name, pattern) if "*" in pattern else name == pattern


def _local_clockin_pattern(pattern: str, course_name: str) -> str:
    return _strip_course_name_prefix(pattern, course_name)


def _parse_clockin_urls(url_value: Any) -> list[str]:
    text = _normalize_text(url_value).strip()
    if not text:
        return []
    with contextlib.suppress(json.JSONDecodeError):
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [_normalize_text(item).strip() for item in parsed if _normalize_text(item).strip()]
    return [text]


def run_nianzhu_course_sheet_step1(
    session: Session,
    *,
    workbook_id: int = NIANZHU_WORKBOOK_NUMERIC_ID,
    attendance_sheet_id: int = NIANZHU_ATTENDANCE_SHEET_NUMERIC_ID,
    course_name: str = NIANZHU_COURSE_NAME,
    shop_id: int = 1,
    update_lessons: bool = True,
    update_clockins: bool = True,
    clockin_pattern: str = "",
    close_browser: bool = True,
) -> dict[str, Any]:
    from kq5034.attendance_api import _close_kqtools_browser, _normalize_shop, ensure_attendance_runtime  # type: ignore
    from kq5034.tools import KqTools  # type: ignore

    ensure_attendance_runtime()
    workbook = _get_workbook(session, workbook_id)
    attendance = _get_sheet(session, attendance_sheet_id)
    owner_key = _normalize_text(attendance.owner_key) or NIANZHU_OWNER_KEY
    bundle = _load_course_sheet_bundle(session, attendance=attendance)
    normalized_shop_id, shop_name = _normalize_shop(shop_id)
    resolved_clockin_pattern = _normalize_text(clockin_pattern)
    effective_clockin_pattern = _local_clockin_pattern(resolved_clockin_pattern, course_name)
    now = datetime.now()

    kqtools = KqTools()
    lesson_export_count = 0
    lesson_data_insert_count = 0
    lesson_errors: list[str] = []
    clockin_names: list[str] = []
    clockin_data_insert_count = 0
    clockin_errors: list[str] = []

    try:
        kqtools.xe2.switch_shop(shop_name)

        if update_lessons:
            video_config_sheet = bundle[VIDEO_CONFIG_SHEET_KEY]
            video_data_sheet = bundle[VIDEO_DATA_SHEET_KEY]
            video_config_document = dict(video_config_sheet.document_json or {})
            video_data_document = dict(video_data_sheet.document_json or {})
            video_config_columns = _normalize_document_columns(video_config_document) or VIDEO_CONFIG_COLUMNS
            video_config_rows = _document_dict_rows(video_config_document)
            video_data_rows = _document_dict_rows(video_data_document)
            for config_row in video_config_rows:
                if int(_to_float(config_row.get("shop_id"))) != normalized_shop_id:
                    continue
                next_update = _parse_datetime_cell(config_row.get("next_update"))
                if next_update is None or next_update > now:
                    continue
                local_lesson_id = int(_to_float(config_row.get("lesson_id")))
                if local_lesson_id <= 0 or not _normalize_text(config_row.get("lesson_id2")):
                    continue
                try:
                    file = kqtools.xe2.export_lesson_data(config_row)
                    imported_rows: list[dict[str, Any]] = []
                    if file is not None:
                        imported_rows = _parse_lesson_data_export_rows(file, lesson_id=local_lesson_id, update_time=now)
                        video_data_rows.extend(imported_rows)
                    rule_text = f"{_normalize_text(course_name)} {_normalize_text(config_row.get('lesson_name'))}"
                    if imported_rows or "闯关" in rule_text:
                        config_row["next_update"] = _compute_next_lesson_update(
                            config_row,
                            course_name=course_name,
                            now=now,
                        )
                    lesson_export_count += 1
                    lesson_data_insert_count += len(imported_rows)
                except Exception as exc:
                    lesson_errors.append(f"{config_row.get('lesson_name')}: {exc}")

            _renumber_rows(video_data_rows, "lesson_data_id")
            video_config_document = _make_table_document_from_dicts(
                columns=video_config_columns,
                rows=video_config_rows,
                numeric_columns={"lesson_id", "shop_id", "video_duration"},
                page_size=100,
                source_meta=dict(video_config_document.get("source_meta") or {}),
            )
            video_data_document = _make_table_document_from_dicts(
                columns=VIDEO_DATA_COLUMNS,
                rows=video_data_rows,
                numeric_columns={
                    "lesson_data_id",
                    "stay_seconds",
                    "cum_seconds",
                    "studio_seconds",
                    "playback_seconds",
                    "num_of_comments",
                    "studio_amount",
                    "progress",
                    "shop_id",
                    "lesson_id",
                    "comment_times",
                    "money",
                },
                page_size=200,
                source_meta=dict(video_data_document.get("source_meta") or {}),
            )
            _update_course_sheet_document(video_config_sheet, video_config_document)
            _update_course_sheet_document(video_data_sheet, video_data_document)
            session.add(video_config_sheet)
            session.add(video_data_sheet)

        if update_clockins:
            clockin_config_sheet = bundle[CLOCKIN_CONFIG_SHEET_KEY]
            clockin_data_sheet = bundle[CLOCKIN_DATA_SHEET_KEY]
            clockin_config_document = dict(clockin_config_sheet.document_json or {})
            clockin_data_document = dict(clockin_data_sheet.document_json or {})
            clockin_config_rows = _document_dict_rows(clockin_config_document)
            clockin_data_rows = _document_dict_rows(clockin_data_document)
            for config_row in clockin_config_rows:
                clockin_name = _normalize_text(config_row.get("name"))
                if not clockin_name or not _match_clockin_name(clockin_name, effective_clockin_pattern):
                    continue
                local_clockin_id = int(_to_float(config_row.get("clockin_id")))
                urls = _parse_clockin_urls(config_row.get("url"))
                if local_clockin_id <= 0 or not urls:
                    continue
                files: list[Any] = []
                failed_urls: list[str] = []
                for url in urls:
                    try:
                        file = kqtools.xe2.export_clockin_data(
                            url,
                            start_date=_normalize_text(config_row.get("start_date")) or None,
                            end_date=_normalize_text(config_row.get("end_date")) or None,
                        )
                        if file:
                            files.append(file)
                    except Exception as exc:
                        failed_urls.append(f"{url}: {exc}")
                if failed_urls:
                    clockin_errors.extend([f"{clockin_name} {item}" for item in failed_urls])
                    continue
                imported_rows: list[dict[str, Any]] = []
                try:
                    for file in files:
                        imported_rows.extend(
                            _parse_clockin_data_export_rows(
                                file,
                                clockin_id=local_clockin_id,
                                clockin_name=clockin_name,
                            )
                        )
                    if imported_rows:
                        clockin_data_rows = [
                            row for row in clockin_data_rows
                            if int(_to_float(row.get("clockin_id"))) != local_clockin_id
                        ]
                        clockin_data_rows.extend(imported_rows)
                        clockin_names.append(clockin_name)
                        clockin_data_insert_count += len(imported_rows)
                except Exception as exc:
                    clockin_errors.append(f"{clockin_name}: {exc}")
                finally:
                    for file in files:
                        with contextlib.suppress(Exception):
                            delete = getattr(file, "delete", None)
                            if callable(delete):
                                delete()
                            else:
                                Path(str(file)).unlink(missing_ok=True)

            _renumber_rows(clockin_data_rows, "clockin_data_id")
            clockin_data_source_meta = dict(clockin_data_document.get("source_meta") or {})
            clockin_data_columns = _clockin_data_columns_for_rows(
                clockin_data_rows,
                existing_columns=_normalize_document_columns(clockin_data_document),
            )
            clockin_data_document = _create_simple_document(
                columns=clockin_data_columns,
                rows=[_clockin_data_dict_to_row(row, clockin_data_columns) for row in clockin_data_rows],
                numeric_columns={
                    "clockin_data_id",
                    "read_num",
                    "like_num",
                    "comment_num",
                    "share_num",
                    "clockin_id",
                },
                page_size=200,
            )
            clockin_data_document["source_meta"] = clockin_data_source_meta
            _update_course_sheet_document(clockin_data_sheet, clockin_data_document)
            session.add(clockin_data_sheet)

        workbook.updated_at = time.time()
        session.add(workbook)
    finally:
        if close_browser:
            _close_kqtools_browser(kqtools)

    return {
        "workbook_id": int(workbook.numeric_id or 0),
        "owner_key": owner_key,
        "course_name": course_name,
        "shop_id": normalized_shop_id,
        "shop_name": shop_name,
        "update_lessons": bool(update_lessons),
        "lesson_export_count": lesson_export_count,
        "lesson_data_insert_count": lesson_data_insert_count,
        "lesson_errors": lesson_errors,
        "update_clockins": bool(update_clockins),
        "clockin_pattern": resolved_clockin_pattern,
        "effective_clockin_pattern": effective_clockin_pattern,
        "clockin_names": clockin_names,
        "clockin_update_count": len(clockin_names),
        "clockin_data_insert_count": clockin_data_insert_count,
        "clockin_errors": clockin_errors,
    }


def materialize_nianzhu_course_sheets(
    session: Session,
    *,
    workbook_id: int = NIANZHU_WORKBOOK_NUMERIC_ID,
    attendance_sheet_id: int = NIANZHU_ATTENDANCE_SHEET_NUMERIC_ID,
    course_name: str = NIANZHU_COURSE_NAME,
    replace: bool = False,
) -> dict[str, Any]:
    workbook = _get_workbook(session, workbook_id)
    attendance = _get_sheet(session, attendance_sheet_id)
    owner_key = _normalize_text(attendance.owner_key) or NIANZHU_OWNER_KEY
    owner_user_id = attendance.owner_user_id or workbook.owner_user_id
    documents = _course_sheet_documents_from_attendance(
        copy.deepcopy(dict(attendance.document_json or {})),
        course_name=course_name,
    )
    video_source_meta = dict(documents[VIDEO_CONFIG_SHEET_KEY].get("source_meta") or {})
    video_data_source_meta = dict(documents[VIDEO_DATA_SHEET_KEY].get("source_meta") or {})
    clockin_source_meta = dict(documents[CLOCKIN_CONFIG_SHEET_KEY].get("source_meta") or {})
    clockin_data_source_meta = dict(documents[CLOCKIN_DATA_SHEET_KEY].get("source_meta") or {})

    sheet_summaries: list[dict[str, Any]] = []
    changed_any = False
    for spec in COURSE_SHEET_SPECS:
        sheet, created, changed = _upsert_course_sheet(
            session,
            workbook=workbook,
            owner_key=owner_key,
            spec=spec,
            document_json=documents[spec.sheet_key],
            owner_user_id=owner_user_id,
            replace=replace,
        )
        document_json = dict(sheet.document_json or {})
        changed_any = changed_any or changed
        sheet_summaries.append({
            "sheet_key": sheet.sheet_key,
            "title": sheet.title,
            "id": int(sheet.numeric_id or 0),
            "created": created,
            "changed": changed,
            "rows": len(_extract_document_rows(document_json)),
        })

    if changed_any:
        workbook.updated_by_user_id = owner_user_id
        workbook.updated_at = time.time()
        session.add(workbook)

    return {
        "workbook_id": int(workbook.numeric_id or 0),
        "attendance_sheet_id": int(attendance.numeric_id or 0),
        "owner_key": owner_key,
        "course_name": course_name,
        **video_source_meta,
        **video_data_source_meta,
        **clockin_source_meta,
        **clockin_data_source_meta,
        "replace": replace,
        "changed": changed_any,
        "sheets": sheet_summaries,
    }


def _load_course_sheet_bundle(
    session: Session,
    *,
    attendance: SheetDocument,
) -> dict[str, SheetDocument]:
    owner_key = _normalize_text(attendance.owner_key) or NIANZHU_OWNER_KEY
    result: dict[str, SheetDocument] = {}
    for sheet_key in [VIDEO_CONFIG_SHEET_KEY, VIDEO_DATA_SHEET_KEY, CLOCKIN_CONFIG_SHEET_KEY, CLOCKIN_DATA_SHEET_KEY]:
        sheet = _find_course_sheet(session, owner_key=owner_key, sheet_key=sheet_key)
        if sheet is None:
            raise RuntimeError(f"念住闯关课程工作簿缺少 sheet：{sheet_key}")
        result[sheet_key] = sheet
    return result


def has_nianzhu_course_storage_sheets(session: Session, *, attendance_sheet: SheetDocument) -> bool:
    owner_key = _normalize_text(attendance_sheet.owner_key) or NIANZHU_OWNER_KEY
    return all(
        _find_course_sheet(session, owner_key=owner_key, sheet_key=sheet_key) is not None
        for sheet_key in [VIDEO_CONFIG_SHEET_KEY, VIDEO_DATA_SHEET_KEY, CLOCKIN_CONFIG_SHEET_KEY, CLOCKIN_DATA_SHEET_KEY]
    )


def _strip_course_prefix_from_row(
    row: Any,
    *,
    columns: list[str],
    target_columns: tuple[str, ...],
    course_name: str,
) -> tuple[Any, int]:
    changed_cells = 0
    target_indexes = {
        index
        for index, column in enumerate(columns)
        if column in target_columns
    }
    if not target_indexes:
        return row, 0

    if isinstance(row, list):
        next_row = list(row)
        if len(next_row) < len(columns):
            next_row.extend([""] * (len(columns) - len(next_row)))
        for index in target_indexes:
            old_value = next_row[index]
            new_value = _strip_course_name_prefix(old_value, course_name)
            if new_value != _normalize_text(old_value):
                next_row[index] = new_value
                changed_cells += 1
        return next_row, changed_cells

    if isinstance(row, dict):
        next_row = dict(row)
        for column in target_columns:
            old_value = next_row.get(column)
            new_value = _strip_course_name_prefix(old_value, course_name)
            if new_value != _normalize_text(old_value):
                next_row[column] = new_value
                changed_cells += 1
        return next_row, changed_cells

    return row, 0


def normalize_nianzhu_course_sheet_names(
    session: Session,
    *,
    attendance_sheet_id: int = NIANZHU_ATTENDANCE_SHEET_NUMERIC_ID,
    course_name: str = NIANZHU_COURSE_NAME,
) -> dict[str, Any]:
    attendance = _get_sheet(session, attendance_sheet_id)
    bundle = _load_course_sheet_bundle(session, attendance=attendance)
    sheet_summaries: list[dict[str, Any]] = []
    total_changed_cells = 0

    for sheet_key, target_columns in COURSE_LOCAL_NAME_COLUMNS.items():
        sheet = bundle[sheet_key]
        current_document = dict(sheet.document_json or {})
        columns = _normalize_document_columns(current_document)
        rows = _extract_document_rows(current_document)
        next_rows: list[Any] = []
        changed_cells = 0
        for row in rows:
            next_row, row_changed_cells = _strip_course_prefix_from_row(
                row,
                columns=columns,
                target_columns=target_columns,
                course_name=course_name,
            )
            next_rows.append(next_row)
            changed_cells += row_changed_cells

        if changed_cells:
            sheet.document_json = _replace_document_data_rows(current_document, next_rows)
            sheet.version = max(int(sheet.version or 1), 1) + 1
            sheet.updated_at = time.time()
            session.add(sheet)

        total_changed_cells += changed_cells
        sheet_summaries.append({
            "sheet_key": sheet_key,
            "sheet_id": int(sheet.numeric_id or 0),
            "title": sheet.title,
            "target_columns": list(target_columns),
            "rows": len(rows),
            "changed_cells": changed_cells,
        })

    return {
        "attendance_sheet_id": int(attendance.numeric_id or 0),
        "course_name": course_name,
        "changed_cells": total_changed_cells,
        "sheets": sheet_summaries,
    }


def _load_video_config(document: dict[str, Any]) -> list[VideoConfigItem]:
    items: list[VideoConfigItem] = []
    for row in _sheet_rows_as_dicts(document):
        lesson_id = _normalize_text(row.get("lesson_id"))
        lesson_name = _normalize_text(row.get("lesson_name"))
        course_key = _course_item_key(lesson_name)
        if not lesson_id or not lesson_name or not course_key:
            continue
        lesson_number = _extract_lesson_number(lesson_name)
        item_type = "课次" if lesson_number is not None else "答疑"
        participates_refund = lesson_number is not None
        rules_by_version: dict[str, list[PercentageRefundRule]] = {
            CURRENT_RULE: _parse_percentage_rules(DEFAULT_VIDEO_RULES[CURRENT_RULE] if participates_refund else ""),
            LEGACY_AFTER_20250522_RULE: _parse_percentage_rules(
                DEFAULT_VIDEO_RULES[LEGACY_AFTER_20250522_RULE] if participates_refund else "",
            ),
            LEGACY_BEFORE_20250522_RULE: _parse_percentage_rules(
                DEFAULT_VIDEO_RULES[LEGACY_BEFORE_20250522_RULE] if participates_refund else "",
            ),
        }
        try:
            order_index = int(_to_float(lesson_id))
        except ValueError:
            order_index = len(items) + 1
        items.append(VideoConfigItem(
            order_index=order_index,
            lesson_id=lesson_id,
            course_key=course_key,
            lesson_name=lesson_name,
            item_type=item_type,
            lesson_number=lesson_number,
            participates_refund=participates_refund,
            participates_score=bool(lesson_number is not None and lesson_number >= 12),
            rules_by_version=rules_by_version,
        ))
    return sorted(items, key=lambda item: item.order_index)


def _progress_text_from_lesson_data_row(row: dict[str, Any]) -> str:
    old_text = _normalize_text(row.get("进度文本"))
    if old_text:
        return old_text
    progress = int(_to_float(row.get("progress")))
    if progress <= 0:
        return ""
    study_state = _normalize_text(row.get("study_state"))
    if study_state and "完成" not in study_state:
        return f"{study_state}/{progress}%"
    return f"{max((progress + 99) // 100, 1)}遍/{progress}%"


def _load_video_data(document: dict[str, Any]) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for row in _sheet_rows_as_dicts(document):
        key = _row_identity_key(row)
        lesson_id = _normalize_text(row.get("lesson_id"))
        if not key or not lesson_id:
            continue
        result[(key, lesson_id)] = _progress_text_from_lesson_data_row(row)
    return result


def _load_clockin_rules(document: dict[str, Any]) -> dict[str, dict[str, list[ThresholdRefundRule]]]:
    if _sheet_rows_as_dicts(document):
        return {
            "打卡数": {
                CURRENT_RULE: _parse_threshold_rules(DEFAULT_CLOCKIN_RULE),
                LEGACY_AFTER_20250522_RULE: _parse_threshold_rules(DEFAULT_CLOCKIN_RULE),
                LEGACY_BEFORE_20250522_RULE: _parse_threshold_rules(DEFAULT_CLOCKIN_RULE),
            },
        }
    return {}


def _load_clockin_data(document: dict[str, Any]) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for row in _sheet_rows_as_dicts(document):
        key = _row_identity_key(row)
        if not key:
            continue
        if "clockin_data_id" in row:
            result[(key, "打卡数")] = result.get((key, "打卡数"), 0.0) + 1
        else:
            field_name = _normalize_text(row.get("字段名")) or "打卡数"
            result[(key, field_name)] = result.get((key, field_name), 0.0) + _to_float(row.get("打卡数"))
    return result


def _rules_for_version(
    rules_by_version: dict[str, list[Any]],
    rule_version: str,
    fallback_version: str = CURRENT_RULE,
) -> list[Any]:
    return rules_by_version.get(rule_version) or rules_by_version.get(fallback_version) or []


def rebuild_nianzhu_attendance_from_course_sheets(
    session: Session,
    *,
    attendance_sheet_id: int = NIANZHU_ATTENDANCE_SHEET_NUMERIC_ID,
    active_only: bool = True,
) -> dict[str, Any]:
    attendance = _get_sheet(session, attendance_sheet_id)
    bundle = _load_course_sheet_bundle(session, attendance=attendance)
    current_document = copy.deepcopy(dict(attendance.document_json or {}))
    columns = _normalize_document_columns(current_document)
    rows = [_normalize_row(row, len(columns)) for row in _extract_document_rows(current_document)]
    data_start_row = _normalize_document_data_start_row(current_document)

    user_id_index = _require_column_index(columns, "用户ID")
    _ = user_id_index
    indexes = {
        "优秀学员评分": _require_column_index(columns, "优秀学员评分"),
        "完成视频数": _require_column_index(columns, "完成视频数"),
        "视频应返款": _require_column_index(columns, "视频应返款"),
        "打卡应返款": _require_column_index(columns, "打卡应返款"),
        "打卡数": _require_column_index(columns, "打卡数"),
    }
    tracking_group_index = _find_column_index(columns, TRACKING_GROUP_COLUMN)
    rule_version_index = _find_column_index(columns, RULE_VERSION_COLUMN)

    video_config = _load_video_config(dict(bundle[VIDEO_CONFIG_SHEET_KEY].document_json or {}))
    video_data = _load_video_data(dict(bundle[VIDEO_DATA_SHEET_KEY].document_json or {}))
    clockin_rules = _load_clockin_rules(dict(bundle[CLOCKIN_CONFIG_SHEET_KEY].document_json or {}))
    clockin_data = _load_clockin_data(dict(bundle[CLOCKIN_DATA_SHEET_KEY].document_json or {}))
    progress_column_by_key = {
        _course_item_key(columns[column_index]): column_index
        for column_index in _progress_column_range(columns)
        if _course_item_key(columns[column_index])
    }
    video_column_indexes = {
        item.lesson_id: progress_column_by_key.get(item.course_key)
        for item in video_config
    }

    cell_meta = copy.deepcopy(current_document.get("cell_meta") or {})
    next_rows: list[list[Any]] = []
    updated_rows = 0
    updated_cells = 0
    styled_cells = 0
    skipped_rows = 0
    missing_identity_rows = 0
    total_video_refund = 0.0
    total_clockin_refund = 0.0

    for row_index, row in enumerate(rows):
        next_row = list(row)
        if (
            active_only
            and tracking_group_index is not None
            and _normalize_text(next_row[tracking_group_index]) != ACTIVE_TRACKING_GROUP
        ):
            skipped_rows += 1
            next_rows.append(next_row)
            continue

        identity_key = _row_identity_key(next_row, columns)
        if not identity_key:
            missing_identity_rows += 1
            next_rows.append(next_row)
            continue

        rule_version = CURRENT_RULE
        if rule_version_index is not None:
            rule_version = _normalize_text(next_row[rule_version_index]) or CURRENT_RULE

        row_changed = False
        document_row = data_start_row + row_index
        video_refund = 0.0
        completed_video_count = 0
        score = 0

        for item in video_config:
            column_index = video_column_indexes.get(item.lesson_id)
            if column_index is None:
                continue
            value = video_data.get((identity_key, item.lesson_id), "")
            if next_row[column_index] != value:
                next_row[column_index] = value
                updated_cells += 1
                row_changed = True

            color: str | None = None
            if item.participates_refund:
                if _extract_play_count(value) > 0:
                    completed_video_count += 1
                refund_amount, color = highlight_percentage_refund_progress(
                    _rules_for_version(item.rules_by_version, rule_version),
                    value,
                )
                video_refund += refund_amount
                if item.participates_score and refund_amount > 0:
                    score += max(_extract_play_count(value) - 1, 0)
            else:
                color = highlight_presence_progress(value)

            if set_cell_background(
                cell_meta,
                document_row=document_row,
                column_index=column_index,
                color=color,
            ):
                styled_cells += 1

        clockin_count = clockin_data.get((identity_key, "打卡数"), 0.0)
        clockin_value = _format_numeric_cell(clockin_count)
        if clockin_count <= 0:
            clockin_value = ""
        if next_row[indexes["打卡数"]] != clockin_value:
            next_row[indexes["打卡数"]] = clockin_value
            updated_cells += 1
            row_changed = True

        clockin_refund, clockin_color = highlight_threshold_refund_progress(
            _rules_for_version(clockin_rules.get("打卡数", {}), rule_version),
            clockin_count,
        )
        total_clockin_refund += clockin_refund
        if set_cell_background(
            cell_meta,
            document_row=document_row,
            column_index=indexes["打卡数"],
            color=clockin_color,
        ):
            styled_cells += 1

        scalar_updates = {
            "优秀学员评分": score,
            "完成视频数": completed_video_count,
            "视频应返款": _format_numeric_cell(video_refund),
            "打卡应返款": _format_numeric_cell(clockin_refund),
        }
        total_video_refund += video_refund
        for field_name, value in scalar_updates.items():
            column_index = indexes[field_name]
            if next_row[column_index] != value:
                next_row[column_index] = value
                updated_cells += 1
                row_changed = True

        if row_changed:
            updated_rows += 1
        next_rows.append(next_row)

    next_document = dict(current_document)
    next_document["cell_meta"] = cell_meta
    next_document = _replace_document_data_rows(next_document, next_rows)
    if next_document != current_document:
        attendance.document_json = next_document
        attendance.version = max(int(attendance.version or 1), 1) + 1
        attendance.updated_at = time.time()
        session.add(attendance)

    return {
        "attendance_sheet_id": int(attendance.numeric_id or 0),
        "active_only": active_only,
        "rows": len(rows),
        "updated_rows": updated_rows,
        "updated_cells": updated_cells,
        "styled_cells": styled_cells,
        "skipped_rows": skipped_rows,
        "missing_identity_rows": missing_identity_rows,
        "video_config_rows": len(video_config),
        "video_data_rows": len(_extract_document_rows(dict(bundle[VIDEO_DATA_SHEET_KEY].document_json or {}))),
        "clockin_data_rows": len(_extract_document_rows(dict(bundle[CLOCKIN_DATA_SHEET_KEY].document_json or {}))),
        "video_refund_total": _format_numeric_cell(total_video_refund),
        "clockin_refund_total": _format_numeric_cell(total_clockin_refund),
    }


def list_nianzhu_course_storage_sheets(
    session: Session,
    *,
    workbook_id: int = NIANZHU_WORKBOOK_NUMERIC_ID,
) -> list[dict[str, Any]]:
    workbook = _get_workbook(session, workbook_id)
    links = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id.in_(workbook_ref_aliases(workbook)))
        .order_by(WorkbookSheetLink.order_index, WorkbookSheetLink.created_at)
    ).all()
    sheet_map = load_sheets_by_refs(session, [link.sheet_id for link in links])
    result: list[dict[str, Any]] = []
    for link in links:
        sheet = sheet_map.get(str(link.sheet_id))
        if sheet is None:
            continue
        result.append({
            "id": int(sheet.numeric_id or 0),
            "title": sheet.title,
            "sheet_key": sheet.sheet_key,
            "order_index": int(link.order_index or 0),
            "rows": len(_extract_document_rows(dict(sheet.document_json or {}))),
        })
    return result
