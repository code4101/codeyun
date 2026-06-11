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
    _delete_document_column,
    _excel_column_label,
    _extract_document_rows,
    _insert_document_column,
    _is_formula_expression,
    _normalize_document_columns,
    _normalize_document_data_start_row,
    _replace_document_data_rows,
)
from backend.core.attendance_progress_style import (
    PercentageRefundRule,
    ThresholdRefundRule,
    highlight_percentage_refund_progress,
    highlight_presence_progress,
    highlight_text_refund_progress,
    highlight_threshold_refund_progress,
    parse_compact_refund_rules,
    parse_progress_percent,
    set_cell_background,
    sheet_text,
)
from backend.core.course_data_sheet_storage import (
    attendance_row_user_ids,
    build_registration_identity_map,
)
from backend.core import note_sheet_inline_links
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
TRACKING_STATUS_COLUMN = "追踪状态"
FREEZE_TIME_COLUMN = "冻结时间"
LINKED_USER_ID_COLUMN = "关联用户ID"
RULE_VERSION_COLUMN = "规则版本"
CURRENT_RULE = "当前规则"
LEGACY_AFTER_20250522_RULE = "旧规则-20250522后"
LEGACY_BEFORE_20250522_RULE = "旧规则-20250522前"
ZERO_REFUND_COMPLETED_BACKGROUND = "#D9D9D9"

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
    "nickname",
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
DEFAULT_TIMED_VIDEO_RULES: dict[str, dict[str, int]] = {
    CURRENT_RULE: {"当堂": 20, "第1天": 15, "第2天": 10, "第3天": 5, "回放": 0},
    LEGACY_AFTER_20250522_RULE: {"当堂": 20, "第1天": 15, "第2天": 10, "第3天": 5, "回放": 0},
    LEGACY_BEFORE_20250522_RULE: {"当堂": 20, "第1天": 15, "第2天": 10, "第3天": 5, "回放": 0},
}
DEFAULT_CLOCKIN_RULE = "5=100;10=150;15=200"

VIDEO_RULE_SYSTEM_REGULAR = "regular"
VIDEO_RULE_SYSTEM_ZEN_STAGE = "zen_stage"
VIDEO_RULE_SYSTEM_CHALLENGE = "challenge"


@dataclass(frozen=True)
class VideoRuleStrategy:
    system: str
    allows_timed_text_override: bool = False
    uses_zen_stage_refund: bool = False
    uses_challenge_refund: bool = False


VIDEO_RULE_STRATEGIES: dict[str, VideoRuleStrategy] = {
    VIDEO_RULE_SYSTEM_REGULAR: VideoRuleStrategy(
        system=VIDEO_RULE_SYSTEM_REGULAR,
        allows_timed_text_override=True,
    ),
    VIDEO_RULE_SYSTEM_ZEN_STAGE: VideoRuleStrategy(
        system=VIDEO_RULE_SYSTEM_ZEN_STAGE,
        uses_zen_stage_refund=True,
    ),
    VIDEO_RULE_SYSTEM_CHALLENGE: VideoRuleStrategy(
        system=VIDEO_RULE_SYSTEM_CHALLENGE,
        uses_challenge_refund=True,
    ),
}


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
    rule_system: str
    participates_refund: bool
    participates_score: bool
    rules_by_version: dict[str, list[PercentageRefundRule]]
    text_rules_by_version: dict[str, dict[str, int]]
    video_duration: float = 0.0
    start_date: datetime | None = None
    course_name: str = ""


@dataclass(frozen=True)
class LegacyVideoStudyResult:
    text: str
    keep_sequence: int
    updated_fields: dict[str, Any]


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


def video_config_url_from_lesson_id2(value: Any) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return text
    if text.startswith("l_"):
        return f"https://admin.xiaoe-tech.com/t/live_management#/userOperation?id={text}&tabName=UserManage"
    return ""


def _video_config_url(row: dict[str, Any]) -> str:
    return _normalize_text(row.get("url")) or video_config_url_from_lesson_id2(row.get("lesson_id2"))


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


def _is_legacy_completed_video_text(value: Any) -> bool:
    text = _normalize_text(value)
    return text == "准时完成" or bool(re.match(r"延\s*\d+\s*周完成", text))


def _is_legacy_delayed_completed_video_text(value: Any) -> bool:
    return bool(re.match(r"延\s*\d+\s*周完成", _normalize_text(value)))


def _video_refund_progress_percent(value: Any) -> float | None:
    if _is_legacy_completed_video_text(value):
        return 100.0
    return parse_progress_percent(value)


def _video_completed_count(value: Any) -> int:
    play_count = _extract_play_count(value)
    if play_count > 0:
        return play_count
    if _is_legacy_completed_video_text(value):
        return 1
    return 1 if (_video_refund_progress_percent(value) or 0) >= 100 else 0


def _video_completed_count_for_item(item: VideoConfigItem, rule_version: str, value: Any) -> int:
    if _rules_for_version(item.text_rules_by_version, rule_version, fallback_version=CURRENT_RULE):
        text = _normalize_text(value)
        return 1 if "完成" in text or "回放" in text else 0
    return _video_completed_count(value)


def _highlight_video_refund_progress(
    rules: list[PercentageRefundRule],
    value: Any,
) -> tuple[float, str | None]:
    progress_percent = _video_refund_progress_percent(value)
    if progress_percent is None:
        return 0, None
    return highlight_percentage_refund_progress(rules, f"{progress_percent}%")


def _highlight_video_refund_for_item(
    item: VideoConfigItem,
    rule_version: str,
    value: Any,
) -> tuple[float, str | None]:
    text_rules = _rules_for_version(item.text_rules_by_version, rule_version, fallback_version=CURRENT_RULE)
    if text_rules:
        return highlight_text_refund_progress(text_rules, value)
    refund_amount, color = _highlight_video_refund_progress(
        _rules_for_version(item.rules_by_version, rule_version),
        value,
    )
    if _is_zen_stage_video_item(item) and _is_legacy_delayed_completed_video_text(value):
        return 0, ZERO_REFUND_COMPLETED_BACKGROUND
    return refund_amount, color


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
    text = _normalize_text(value)
    lesson_number = _extract_lesson_number(text)
    if lesson_number is not None:
        return f"lesson:{lesson_number}"
    qa_editions = _extract_qa_editions(text)
    if qa_editions:
        return "qa:" + ",".join(str(item) for item in qa_editions)
    if "=" in text:
        text = text.rsplit("=", 1)[1].strip()
    if text:
        return f"title:{text}"
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
    if key.startswith("title:"):
        title = key.split(":", 1)[1]
        return key, "视频", title, 9000, title
    return "", "视频", "", 9000, ""


def _query_legacy_lesson_rows(course_name: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from kq5034.attendance_api import get_kqdb  # type: ignore
    except Exception as exc:
        return [], f"无法导入 kq5034.attendance_api.get_kqdb: {exc}"

    terms = [course_name]
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

    marker_indexes = [
        index for index in (
            _find_column_index(columns, TRACKING_GROUP_COLUMN),
            _find_column_index(columns, TRACKING_STATUS_COLUMN),
            _find_column_index(columns, FREEZE_TIME_COLUMN),
            _find_column_index(columns, RULE_VERSION_COLUMN),
        )
        if index is not None and index >= start
    ]
    end = min(marker_indexes) if marker_indexes else len(columns)
    return range(start, max(start, end))


NIANZHU_ATTENDANCE_SCHEMA_META_COLUMNS = [
    RULE_VERSION_COLUMN,
    TRACKING_GROUP_COLUMN,
    TRACKING_STATUS_COLUMN,
    FREEZE_TIME_COLUMN,
]
NIANZHU_ATTENDANCE_SOURCE_ONLY_COLUMNS = [
    "商户订单号",
]
NIANZHU_ATTENDANCE_MANAGED_FORMULA_COLUMNS = {
    "禅客",
    "完成视频数",
    "视频应返款",
    "打卡应返款",
    "总应返款",
    "当前应返款",
}


def _find_nianzhu_refund_insert_index(columns: list[str], header: str) -> int:
    if header == "已返款":
        order_amount_index = _find_column_index(columns, "订单金额")
        if order_amount_index is not None:
            return order_amount_index
        total_refund_index = _find_column_index(columns, "总应返款")
        if total_refund_index is not None:
            return min(total_refund_index + 1, len(columns))

    refunded_index = _find_column_index(columns, "已返款")
    if refunded_index is not None:
        return min(refunded_index + 1, len(columns))
    order_amount_index = _find_column_index(columns, "订单金额")
    if order_amount_index is not None:
        return min(order_amount_index + 1, len(columns))
    clockin_index = _find_column_index(columns, "打卡数")
    return clockin_index if clockin_index is not None else len(columns)


def _move_nianzhu_document_column(
    document: dict[str, Any],
    *,
    from_index: int,
    to_index: int,
) -> dict[str, Any]:
    columns = _normalize_document_columns(document)
    if from_index < 0 or from_index >= len(columns) or to_index < 0 or to_index >= len(columns) or from_index == to_index:
        return document

    def move_item(items: list[Any]) -> list[Any]:
        next_items = list(items)
        item = next_items.pop(from_index)
        next_items.insert(to_index, item)
        return next_items

    def move_row(row: Any) -> list[Any]:
        return move_item(_normalize_row(row, len(columns)))

    next_document = dict(document)
    next_document["columns"] = move_item(columns)
    next_document["rows"] = [move_row(row) for row in _extract_document_rows(document)]

    grid_rows = document.get("grid_rows")
    if isinstance(grid_rows, list):
        next_document["grid_rows"] = [move_row(row) for row in grid_rows]

    column_widths = document.get("column_widths")
    if isinstance(column_widths, list) and len(column_widths) == len(columns):
        next_document["column_widths"] = move_item(column_widths)

    entity_columns = document.get("entity_columns")
    if isinstance(entity_columns, list) and len(entity_columns) == len(columns):
        next_document["entity_columns"] = move_item(entity_columns)

    cell_meta = document.get("cell_meta")
    if isinstance(cell_meta, dict):
        index_map: dict[int, int] = {}
        for index in range(len(columns)):
            if index == from_index:
                index_map[index] = to_index
            elif from_index < to_index and from_index < index <= to_index:
                index_map[index] = index - 1
            elif to_index < from_index and to_index <= index < from_index:
                index_map[index] = index + 1
            else:
                index_map[index] = index

        next_cell_meta: dict[str, Any] = {}
        for key, value in cell_meta.items():
            match = re.fullmatch(r"(-?\d+):(-?\d+)", str(key))
            if not match:
                next_cell_meta[str(key)] = value
                continue
            row_index = int(match.group(1))
            column_index = int(match.group(2))
            next_cell_meta[f"{row_index}:{index_map.get(column_index, column_index)}"] = value
        next_document["cell_meta"] = next_cell_meta

    return next_document


def _ensure_nianzhu_refund_column_order(document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    columns = _normalize_document_columns(document)
    refunded_index = _find_column_index(columns, "已返款")
    order_amount_index = _find_column_index(columns, "订单金额")
    if refunded_index is None or order_amount_index is None or refunded_index < order_amount_index:
        return document, False
    return _move_nianzhu_document_column(
        document,
        from_index=refunded_index,
        to_index=order_amount_index,
    ), True


def _find_nianzhu_meta_insert_index(columns: list[str], header: str) -> int:
    header_order = NIANZHU_ATTENDANCE_SCHEMA_META_COLUMNS.index(header)
    for next_header in NIANZHU_ATTENDANCE_SCHEMA_META_COLUMNS[header_order + 1:]:
        next_index = _find_column_index(columns, next_header)
        if next_index is not None:
            return next_index
    for previous_header in reversed(NIANZHU_ATTENDANCE_SCHEMA_META_COLUMNS[:header_order]):
        previous_index = _find_column_index(columns, previous_header)
        if previous_index is not None:
            return min(previous_index + 1, len(columns))
    return len(columns)


def _set_document_field_header(
    document: dict[str, Any],
    *,
    column_index: int,
    header: str,
) -> dict[str, Any]:
    grid_rows = document.get("grid_rows")
    if not isinstance(grid_rows, list):
        return document
    try:
        field_row_index = int(document.get("field_row_index") or 0)
    except (TypeError, ValueError):
        field_row_index = 0
    if field_row_index < 0 or field_row_index >= len(grid_rows):
        return document

    columns = _normalize_document_columns(document)
    row = _normalize_row(grid_rows[field_row_index], len(columns))
    if column_index < 0 or column_index >= len(row) or row[column_index] == header:
        return document
    row[column_index] = header
    next_grid_rows = list(grid_rows)
    next_grid_rows[field_row_index] = row
    return {**document, "grid_rows": next_grid_rows}


def _insert_nianzhu_attendance_column(
    document: dict[str, Any],
    *,
    header: str,
    insert_index: int,
    width: int = 96,
) -> dict[str, Any]:
    next_document = _insert_document_column(
        document,
        insert_index=insert_index,
        header=header,
        width=width,
    )
    return _set_document_field_header(
        next_document,
        column_index=min(max(insert_index, 0), len(_normalize_document_columns(next_document)) - 1),
        header=header,
    )


def _fill_nianzhu_attendance_schema_defaults(
    document: dict[str, Any],
    *,
    inserted_columns: set[str],
) -> tuple[dict[str, Any], int]:
    if not inserted_columns:
        return document, 0

    columns = _normalize_document_columns(document)
    rows = [_normalize_row(row, len(columns)) for row in _extract_document_rows(document)]
    indexes = {
        header: _find_column_index(columns, header)
        for header in [
            "分组",
            "已返款",
            "当前应返款",
            RULE_VERSION_COLUMN,
            TRACKING_GROUP_COLUMN,
            TRACKING_STATUS_COLUMN,
            FREEZE_TIME_COLUMN,
        ]
    }

    changed_cells = 0
    for row in rows:
        if "已返款" in inserted_columns and indexes["已返款"] is not None and not _normalize_text(row[indexes["已返款"]]):
            row[indexes["已返款"]] = 0
            changed_cells += 1
        if "当前应返款" in inserted_columns and indexes["当前应返款"] is not None and not _normalize_text(row[indexes["当前应返款"]]):
            row[indexes["当前应返款"]] = 0
            changed_cells += 1
        if RULE_VERSION_COLUMN in inserted_columns and indexes[RULE_VERSION_COLUMN] is not None and not _normalize_text(row[indexes[RULE_VERSION_COLUMN]]):
            row[indexes[RULE_VERSION_COLUMN]] = CURRENT_RULE
            changed_cells += 1
        if TRACKING_GROUP_COLUMN in inserted_columns and indexes[TRACKING_GROUP_COLUMN] is not None:
            tracking_group = _normalize_text(row[indexes[TRACKING_GROUP_COLUMN]])
            source_group = ""
            group_index = indexes["分组"]
            if group_index is not None and group_index != indexes[TRACKING_GROUP_COLUMN]:
                source_group = _normalize_text(row[group_index])
            if not tracking_group and source_group:
                row[indexes[TRACKING_GROUP_COLUMN]] = source_group
                changed_cells += 1
        if TRACKING_STATUS_COLUMN in inserted_columns and indexes[TRACKING_STATUS_COLUMN] is not None and not _normalize_text(row[indexes[TRACKING_STATUS_COLUMN]]):
            row[indexes[TRACKING_STATUS_COLUMN]] = "追踪中"
            changed_cells += 1
        if FREEZE_TIME_COLUMN in inserted_columns and indexes[FREEZE_TIME_COLUMN] is not None and row[indexes[FREEZE_TIME_COLUMN]] is None:
            row[indexes[FREEZE_TIME_COLUMN]] = ""
            changed_cells += 1

    if changed_cells <= 0:
        return document, 0
    return _replace_document_data_rows(document, rows), changed_cells


def _requires_attendance_tracking_meta_columns(document: dict[str, Any], *, course_name: str = "") -> bool:
    resolved_course_name = _normalize_text(course_name)
    if not resolved_course_name:
        source_meta = dict(document.get("source_meta") or {})
        resolved_course_name = _normalize_text(source_meta.get("course_name"))
    if not resolved_course_name:
        columns = _normalize_document_columns(document)
        if any(_find_column_index(columns, header) is not None for header in NIANZHU_ATTENDANCE_SCHEMA_META_COLUMNS):
            return True
        return True
    return "闯关" in resolved_course_name


def _ensure_nianzhu_attendance_schema(
    document: dict[str, Any],
    *,
    course_name: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    next_document = copy.deepcopy(document)
    inserted_columns: list[str] = []
    removed_columns: list[str] = []
    requires_tracking_meta_columns = _requires_attendance_tracking_meta_columns(
        next_document,
        course_name=course_name,
    )

    removable_columns = [
        *NIANZHU_ATTENDANCE_SOURCE_ONLY_COLUMNS,
        *([] if requires_tracking_meta_columns else NIANZHU_ATTENDANCE_SCHEMA_META_COLUMNS),
    ]
    for header in removable_columns:
        columns = _normalize_document_columns(next_document)
        column_index = _find_column_index(columns, header)
        if column_index is None:
            continue
        next_document = _delete_document_column(next_document, delete_index=column_index)
        removed_columns.append(header)

    next_document, refund_columns_reordered = _ensure_nianzhu_refund_column_order(next_document)

    columns = _normalize_document_columns(next_document)
    has_refund_context = any(
        _find_column_index(columns, header) is not None
        for header in ["总应返款", "订单金额", "已返款", "当前应返款"]
    )
    if has_refund_context:
        for header in ["已返款", "当前应返款"]:
            columns = _normalize_document_columns(next_document)
            if _find_column_index(columns, header) is not None:
                continue
            next_document = _insert_nianzhu_attendance_column(
                next_document,
                header=header,
                insert_index=_find_nianzhu_refund_insert_index(columns, header),
                width=96,
            )
            inserted_columns.append(header)

    if requires_tracking_meta_columns:
        for header in NIANZHU_ATTENDANCE_SCHEMA_META_COLUMNS:
            columns = _normalize_document_columns(next_document)
            if _find_column_index(columns, header) is not None:
                continue
            next_document = _insert_nianzhu_attendance_column(
                next_document,
                header=header,
                insert_index=_find_nianzhu_meta_insert_index(columns, header),
                width=96,
            )
            inserted_columns.append(header)

    next_document, defaulted_cells = _fill_nianzhu_attendance_schema_defaults(
        next_document,
        inserted_columns=set(inserted_columns),
    )
    return next_document, {
        "schema_changed": next_document != document,
        "schema_inserted_columns": inserted_columns,
        "schema_removed_columns": removed_columns,
        "schema_refund_columns_reordered": refund_columns_reordered,
        "schema_defaulted_cells": defaulted_cells,
    }


def _document_cell_link(document: dict[str, Any], *, row_index: int, column_index: int) -> str:
    rows = document.get("rows")
    if isinstance(rows, list) and 0 <= row_index < len(rows):
        row = rows[row_index]
        if isinstance(row, list) and 0 <= column_index < len(row):
            url = note_sheet_inline_links.inline_cell_link_url(row[column_index])
            if url:
                return _normalize_text(url)

    grid_rows = document.get("grid_rows")
    data_start_row = 0
    try:
        data_start_row = max(int(document.get("data_start_row") or 0), 0)
    except (TypeError, ValueError):
        data_start_row = 0
    document_row_index = data_start_row + row_index
    if isinstance(grid_rows, list) and 0 <= document_row_index < len(grid_rows):
        columns = document.get("columns")
        column_count = max(column_index + 1, len(columns) if isinstance(columns, list) else 0)
        row = note_sheet_inline_links.normalize_row(grid_rows[document_row_index], column_count)
        if 0 <= column_index < len(row):
            return _normalize_text(note_sheet_inline_links.inline_cell_link_url(row[column_index]))
    return ""


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


def _parse_timed_text_rules(value: Any) -> dict[str, int]:
    if isinstance(value, dict):
        result: dict[str, int] = {}
        for key, amount in value.items():
            label = _normalize_text(key)
            if not label:
                continue
            numeric_amount = int(_to_float(amount))
            if numeric_amount >= 0:
                result[label] = numeric_amount
        return result

    text = _normalize_text(value)
    if not text:
        return {}
    result: dict[str, int] = {}
    for label, amount in re.findall(r"([^=;；:：]+?)\s*(?:=|:|：)\s*(\d+)", text):
        label_text = label.strip()
        if label_text:
            result[label_text] = int(amount)
    return result


def _parse_attendance_video_refund_rules(value: Any) -> dict[str, int]:
    compact_rules = parse_compact_refund_rules(value)
    if compact_rules:
        return compact_rules
    return _parse_timed_text_rules(value)


def _attendance_video_refund_rules_override(document: dict[str, Any], columns: list[str]) -> dict[str, int]:
    refund_index = _find_column_index(columns, "视频应返款")
    if refund_index is None:
        return {}
    note_value = _grid_cell_value(
        document,
        max(_normalize_document_data_start_row(document) - 1, 0),
        refund_index,
    )
    return _parse_attendance_video_refund_rules(note_value)


def _attendance_legacy_zen_video_refund_amount(document: dict[str, Any], columns: list[str]) -> float:
    refund_index = _find_column_index(columns, "视频应返款")
    if refund_index is None:
        return 0.0
    note_value = _grid_cell_value(
        document,
        max(_normalize_document_data_start_row(document) - 1, 0),
        refund_index,
    )
    match = re.search(r"\*\s*(\d+(?:\.\d+)?)\s*元", _normalize_text(note_value))
    if not match:
        return 0.0
    return _to_float(match.group(1))


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


def _split_linked_user_ids(value: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[,，;；\s]+", _normalize_text(value)):
        user_id = item.strip()
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        result.append(user_id)
    return result


def _user_identity_key(user_id: Any) -> str:
    text = _normalize_text(user_id)
    if not text:
        return ""
    if text.startswith("user:"):
        return text
    return f"user:{text}"


def _row_identity_keys(
    row: dict[str, Any] | list[Any],
    columns: list[str] | None = None,
    *,
    registration_identity_map: dict[str, list[str]] | None = None,
) -> list[str]:
    mapping = _row_mapping(row, columns)
    result: list[str] = []
    seen: set[str] = set()

    for user_id in attendance_row_user_ids(
        mapping,
        registration_identity_map=registration_identity_map,
    ):
        user_key = _user_identity_key(user_id)
        if user_key and user_key not in seen:
            result.append(user_key)
            seen.add(user_key)

    if result:
        return result

    primary_key = _row_identity_key(mapping)
    if primary_key:
        result.append(primary_key)
    return result


def _row_mapping(row: dict[str, Any] | list[Any], columns: list[str] | None = None) -> dict[str, Any]:
    if isinstance(row, list):
        if columns is None:
            return {}
        return dict(zip(columns, _normalize_row(row, len(columns))))
    return row


def _is_active_tracking_row(
    row: dict[str, Any] | list[Any],
    columns: list[str] | None = None,
    *,
    active_only: bool = True,
) -> bool:
    if not active_only:
        return True

    mapping = _row_mapping(row, columns)
    has_status = TRACKING_STATUS_COLUMN in mapping
    has_freeze_time = FREEZE_TIME_COLUMN in mapping
    status = _normalize_text(mapping.get(TRACKING_STATUS_COLUMN))
    freeze_time = _normalize_text(mapping.get(FREEZE_TIME_COLUMN))

    if has_status or has_freeze_time:
        if freeze_time:
            return False
        if status:
            return status == "追踪中"
        return True

    return True


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
        result: list[dict[str, Any]] = []
        for local_index, legacy_row in enumerate(legacy_rows, start=1):
            local_lesson_id = legacy_lesson_id_map.get(_normalize_text(legacy_row.get("lesson_id")))
            if local_lesson_id is None:
                continue
            row = {
                column: _format_legacy_value(legacy_row.get(column))
                for column in VIDEO_DATA_COLUMNS
            }
            row["lesson_data_id"] = local_index
            row["lesson_id"] = local_lesson_id
            row["lesson_name"] = _strip_course_name_prefix(
                _format_legacy_value(legacy_row.get("lesson_name")),
                _normalize_text(source_meta.get("course_name")),
            )
            result.append(row)
        document = _make_table_document_from_dicts(
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
    result: list[dict[str, Any]] = []
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
            result.append({
                "lesson_data_id": local_index,
                "user_id2": user_id,
                "study_state": study_state,
                "progress": _format_numeric_cell(progress),
                "update_time": source_time,
                "lesson_id": _format_numeric_cell(_to_float(lesson_id)),
            })
    return _make_table_document_from_dicts(
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
    is_zen_stage_course = _is_zen_stage_course_text(rule_text)
    start_date = _parse_datetime_cell(row.get("start_date")) or now
    end_date = _parse_datetime_cell(row.get("end_date")) or datetime(9999, 12, 31, 23, 59, 59)
    current_next_update = _parse_datetime_cell(row.get("next_update")) or start_date
    video_duration = _to_float(row.get("video_duration"))
    video_end_time = start_date if "闯关" in rule_text else start_date + timedelta(seconds=video_duration)
    update_interval = timedelta(days=7 if is_zen_stage_course else 1)

    if now < video_end_time:
        return _format_datetime_for_sheet(video_end_time)
    if now >= end_date:
        return _format_datetime_for_sheet(datetime(9999, 12, 31, 23, 59, 59))

    next_time = current_next_update if is_zen_stage_course else video_end_time
    while next_time <= now:
        next_time += update_interval
    if next_time > end_date:
        next_time = end_date
    return _format_datetime_for_sheet(next_time)


def _is_zen_stage_course_text(text: str) -> bool:
    normalized = _normalize_text(text)
    return "禅宗" in normalized or "修道班" in normalized


def _resolve_video_rule_system(*, course_name: Any, lesson_name: Any, is_qa_item: bool = False) -> str:
    if is_qa_item:
        return VIDEO_RULE_SYSTEM_REGULAR
    text = f"{_normalize_text(course_name)} {_normalize_text(lesson_name)}"
    if "念住闯关" in text:
        return VIDEO_RULE_SYSTEM_CHALLENGE
    if _is_zen_stage_course_text(text):
        return VIDEO_RULE_SYSTEM_ZEN_STAGE
    return VIDEO_RULE_SYSTEM_REGULAR


def _resolve_video_rule_strategy(*, course_name: Any, lesson_name: Any, is_qa_item: bool = False) -> VideoRuleStrategy:
    return VIDEO_RULE_STRATEGIES[_resolve_video_rule_system(
        course_name=course_name,
        lesson_name=lesson_name,
        is_qa_item=is_qa_item,
    )]


def _is_zen_stage_video_item(item: VideoConfigItem) -> bool:
    return item.rule_system == VIDEO_RULE_SYSTEM_ZEN_STAGE


def _is_challenge_video_item(item: VideoConfigItem) -> bool:
    return item.rule_system == VIDEO_RULE_SYSTEM_CHALLENGE


def _is_regular_video_item(item: VideoConfigItem) -> bool:
    return item.rule_system == VIDEO_RULE_SYSTEM_REGULAR


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
        "用户昵称": "nickname",
        "备注名": "remark_nm",
        "状态": "state",
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


def _ensure_course_source_meta(document: dict[str, Any], *, course_name: str) -> bool:
    resolved_course_name = _normalize_text(course_name)
    if not resolved_course_name:
        return False
    source_meta = dict(document.get("source_meta") or {})
    current_course_name = _normalize_text(source_meta.get("course_name"))
    if current_course_name and not (
        current_course_name == NIANZHU_COURSE_NAME and resolved_course_name != NIANZHU_COURSE_NAME
    ):
        return False
    source_meta["course_name"] = resolved_course_name
    document["source_meta"] = source_meta
    return True


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

    ensure_attendance_runtime()
    from kq5034.tools import KqTools  # type: ignore

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
                        imported_rows = _parse_lesson_data_export_rows(
                            file,
                            lesson_id=local_lesson_id,
                            update_time=now,
                        )
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
        if _ensure_course_source_meta(document_json, course_name=course_name):
            sheet.document_json = document_json
            sheet.version = max(int(sheet.version or 1), 1) + 1
            sheet.updated_by_user_id = owner_user_id
            sheet.updated_at = time.time()
            session.add(sheet)
            changed = True
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


def _set_grid_cell_inline_link(
    document: dict[str, Any],
    *,
    row_index: int,
    column_index: int,
    url: str,
) -> tuple[dict[str, Any], bool]:
    normalized_url = _normalize_text(url)
    if not normalized_url:
        return document, False

    columns = _normalize_document_columns(document)
    if column_index < 0 or column_index >= len(columns):
        return document, False

    source_grid_rows = document.get("grid_rows")
    if not isinstance(source_grid_rows, list) or row_index < 0:
        return document, False

    grid_rows = [note_sheet_inline_links.normalize_row(row, len(columns)) for row in source_grid_rows]
    while len(grid_rows) <= row_index:
        grid_rows.append([""] * len(columns))

    next_document = dict(document)
    changed = False

    old_value = grid_rows[row_index][column_index]
    new_value = note_sheet_inline_links.with_inline_cell_link(old_value, {"url": normalized_url})
    if new_value != old_value:
        grid_rows[row_index][column_index] = new_value
        next_document["grid_rows"] = grid_rows
        changed = True

    cell_meta = dict(next_document.get("cell_meta")) if isinstance(next_document.get("cell_meta"), dict) else {}
    meta_key = f"{row_index}:{column_index}"
    previous_meta = cell_meta.get(meta_key)
    next_meta = dict(previous_meta) if isinstance(previous_meta, dict) else {}
    previous_link = next_meta.get("link")
    previous_url = _normalize_text(previous_link.get("url")) if isinstance(previous_link, dict) else ""
    if previous_url != normalized_url:
        next_meta["link"] = {"url": normalized_url}
        cell_meta[meta_key] = next_meta
        next_document["cell_meta"] = cell_meta
        changed = True

    return next_document, changed


def apply_course_attendance_header_links_for_response(
    session: Session,
    *,
    attendance: SheetDocument,
    document_json: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    if _normalize_text(attendance.sheet_key) != ATTENDANCE_SHEET_KEY:
        return document_json, 0

    try:
        bundle = _load_course_sheet_bundle(session, attendance=attendance)
    except RuntimeError:
        return document_json, 0

    columns = _normalize_document_columns(document_json)
    if not columns:
        return document_json, 0

    try:
        header_row_index = max(int(document_json.get("field_row_index")), 0)
    except (TypeError, ValueError):
        header_row_index = max(_normalize_document_data_start_row(document_json) - 2, 0)

    video_url_by_key: dict[str, str] = {}
    for row in _sheet_rows_as_dicts(dict(bundle[VIDEO_CONFIG_SHEET_KEY].document_json or {})):
        key = _course_item_key(row.get("lesson_name"))
        url = _video_config_url(row)
        if key and url:
            video_url_by_key.setdefault(key, url)

    clockin_url_by_name: dict[str, str] = {}
    for row in _sheet_rows_as_dicts(dict(bundle[CLOCKIN_CONFIG_SHEET_KEY].document_json or {})):
        name = _normalize_text(row.get("name"))
        url = _normalize_text(row.get("url"))
        if name and url:
            clockin_url_by_name.setdefault(name, url)

    next_document = document_json
    changed_count = 0
    for column_index, column in enumerate(columns):
        url = ""
        if _normalize_text(column) == "打卡数":
            url = clockin_url_by_name.get("打卡数", "")
        if not url:
            key = _course_item_key(column)
            url = video_url_by_key.get(key, "") if key else ""
        if not url:
            continue
        next_document, changed = _set_grid_cell_inline_link(
            next_document,
            row_index=header_row_index,
            column_index=column_index,
            url=url,
        )
        if changed:
            changed_count += 1

    return next_document, changed_count


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


def _load_video_config(
    document: dict[str, Any],
    *,
    timed_video_rules_override: dict[str, int] | None = None,
) -> list[VideoConfigItem]:
    items: list[VideoConfigItem] = []
    source_meta = dict(document.get("source_meta") or {})
    course_name = _normalize_text(source_meta.get("course_name"))
    for row in _sheet_rows_as_dicts(document):
        lesson_id = _normalize_text(row.get("lesson_id"))
        lesson_name = _normalize_text(row.get("lesson_name"))
        course_key = _course_item_key(lesson_name)
        if not lesson_id or not lesson_name or not course_key:
            continue
        lesson_number = _extract_lesson_number(lesson_name)
        is_qa_item = course_key.startswith("qa:")
        rule_strategy = _resolve_video_rule_strategy(course_name=course_name, lesson_name=lesson_name, is_qa_item=is_qa_item)
        rule_system = rule_strategy.system
        item_type = "课次" if lesson_number is not None or rule_system == VIDEO_RULE_SYSTEM_ZEN_STAGE else ("答疑" if is_qa_item else "视频")
        participates_refund = lesson_number is not None or rule_system == VIDEO_RULE_SYSTEM_ZEN_STAGE
        refund_rule_mode = _normalize_text(source_meta.get("video_refund_rule_mode"))
        if rule_strategy.uses_challenge_refund:
            refund_rule_mode = ""
        if timed_video_rules_override and rule_strategy.allows_timed_text_override:
            refund_rule_mode = "timed_text"
        rules_by_version: dict[str, list[PercentageRefundRule]] = {
            CURRENT_RULE: _parse_percentage_rules(
                DEFAULT_VIDEO_RULES[CURRENT_RULE]
                if participates_refund and refund_rule_mode != "timed_text"
                else "",
            ),
            LEGACY_AFTER_20250522_RULE: _parse_percentage_rules(
                DEFAULT_VIDEO_RULES[LEGACY_AFTER_20250522_RULE]
                if participates_refund and refund_rule_mode != "timed_text"
                else "",
            ),
            LEGACY_BEFORE_20250522_RULE: _parse_percentage_rules(
                DEFAULT_VIDEO_RULES[LEGACY_BEFORE_20250522_RULE]
                if participates_refund and refund_rule_mode != "timed_text"
                else "",
            ),
        }
        custom_text_rules = dict(timed_video_rules_override or _parse_timed_text_rules(source_meta.get("timed_video_rules")))
        text_rules_by_version: dict[str, dict[str, int]] = {}
        for version, rules in DEFAULT_TIMED_VIDEO_RULES.items():
            if participates_refund and refund_rule_mode == "timed_text":
                text_rules_by_version[version] = dict(custom_text_rules or rules)
            else:
                text_rules_by_version[version] = {}
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
            rule_system=rule_system,
            participates_refund=participates_refund,
            participates_score=bool(lesson_number is not None and lesson_number >= 12),
            rules_by_version=rules_by_version,
            text_rules_by_version=text_rules_by_version,
            video_duration=_to_float(row.get("video_duration")),
            start_date=_parse_datetime_cell(row.get("start_date")),
            course_name=course_name,
        ))
    return sorted(items, key=lambda item: item.order_index)


LEGACY_VIDEO_UPDATE_COLUMNS = [
    "stay_seconds",
    "cum_seconds",
    "studio_seconds",
    "playback_seconds",
    "study_state",
    "progress",
]


def _challenge_progress_text_from_percent(progress: int | float) -> str:
    if progress <= 0:
        return ""
    if progress < 90:
        return f"学习中/{progress}%"
    if progress >= 200:
        times = 3
    elif progress >= 150:
        times = 2
    else:
        times = 1
    return f"{times}遍/{progress}%"


def _legacy_video_algorithm_kind(lesson: VideoConfigItem) -> str:
    if lesson.rule_system == VIDEO_RULE_SYSTEM_ZEN_STAGE:
        return "b"
    if lesson.rule_system == VIDEO_RULE_SYSTEM_CHALLENGE:
        return "c"
    return "a"


def _custom_fillna_like_kq5034(df: Any, default_fill_value: Any = 0, numeric_fill_value: Any = 0) -> None:
    import pandas as pd

    for column in df.columns:
        if numeric_fill_value is not None and pd.api.types.is_numeric_dtype(df[column]):
            df[column] = df[column].fillna(numeric_fill_value)
        elif pd.api.types.is_object_dtype(df[column]) or pd.api.types.is_string_dtype(df[column]):
            df[column] = df[column].fillna(default_fill_value)


def _legacy_video_items_frame(indexed_rows: list[tuple[int, dict[str, Any]]]):
    import pandas as pd

    records: list[dict[str, Any]] = []
    indexes: list[int] = []
    for sequence, row in indexed_rows:
        record = dict(row)
        for column in [
            "lesson_data_id",
            "stay_seconds",
            "cum_seconds",
            "studio_seconds",
            "playback_seconds",
            "progress",
        ]:
            record[column] = _format_numeric_cell(_to_float(record.get(column)))
        record["update_time"] = _parse_datetime_cell(record.get("update_time"))
        record["study_state"] = _normalize_text(record.get("study_state"))
        records.append(record)
        indexes.append(sequence)

    items = pd.DataFrame.from_records(records, index=indexes)
    for column in LEGACY_VIDEO_UPDATE_COLUMNS:
        if column not in items:
            items[column] = 0 if column != "study_state" else ""
    return items


def _legacy_video_updated_fields(item: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in LEGACY_VIDEO_UPDATE_COLUMNS:
        if column == "study_state":
            result[column] = _format_legacy_value(item[column])
        else:
            result[column] = _format_numeric_cell(_to_float(item[column]))
    return result


def _legacy_video_existing_progress_result(indexed_rows: list[tuple[int, dict[str, Any]]]) -> LegacyVideoStudyResult:
    best_sequence, best_row = max(
        indexed_rows,
        key=lambda item: (_to_float(item[1].get("progress")), -item[0]),
    )
    progress = int(_to_float(best_row.get("progress")))
    return LegacyVideoStudyResult(
        text=_challenge_progress_text_from_percent(progress),
        keep_sequence=best_sequence,
        updated_fields={},
    )


def _legacy_video_study_result_a(
    lesson: VideoConfigItem,
    indexed_rows: list[tuple[int, dict[str, Any]]],
) -> LegacyVideoStudyResult:
    items = _legacy_video_items_frame(indexed_rows)
    items.sort_values("update_time", inplace=True)
    _custom_fillna_like_kq5034(items, 0, numeric_fill_value=0)

    last_studio_seconds = 0
    last_playback_seconds = 0
    for idx in items.index:
        curr_studio = items.at[idx, "studio_seconds"]
        curr_playback = items.at[idx, "playback_seconds"]

        if curr_studio < last_studio_seconds:
            items.at[idx, "studio_seconds"] = last_studio_seconds
        else:
            last_studio_seconds = curr_studio

        if curr_playback < last_playback_seconds:
            items.at[idx, "playback_seconds"] = last_playback_seconds
        else:
            last_playback_seconds = curr_playback

        items.at[idx, "cum_seconds"] = items.at[idx, "studio_seconds"] + items.at[idx, "playback_seconds"]

    total_seconds = lesson.video_duration
    if total_seconds <= 0:
        return _legacy_video_existing_progress_result(indexed_rows)

    items["progress"] = items["cum_seconds"].apply(lambda x: int(x / total_seconds * 100))
    max_progress_item = items.loc[items["progress"].idxmax()]

    if max_progress_item["progress"] < 50:
        return LegacyVideoStudyResult(
            text=f'学习中/{max_progress_item["progress"]}%',
            keep_sequence=int(max_progress_item.name),
            updated_fields=_legacy_video_updated_fields(max_progress_item),
        )

    first_finished_item = items[items["progress"] >= 50].iloc[0].copy()
    if first_finished_item["lesson_data_id"] != max_progress_item["lesson_data_id"]:
        for column in LEGACY_VIDEO_UPDATE_COLUMNS:
            first_finished_item[column] = max_progress_item[column]

    if first_finished_item["studio_seconds"] / total_seconds >= 0.5:
        text = f'当堂完成/{max_progress_item["progress"]}%'
    else:
        start_date = lesson.start_date or _parse_datetime_cell("")
        update_time = first_finished_item["update_time"]
        if start_date is None or update_time is None:
            delta_d = 1
        else:
            dt1 = start_date + timedelta(seconds=lesson.video_duration)
            delta_d = max(int((update_time - dt1).total_seconds() / (3600 * 24)), 1)
        text = f'第{int(delta_d)}天回放/{max_progress_item["progress"]}%'

    return LegacyVideoStudyResult(
        text=text,
        keep_sequence=int(first_finished_item.name),
        updated_fields=_legacy_video_updated_fields(first_finished_item),
    )


def _legacy_video_study_result_b(
    lesson: VideoConfigItem,
    indexed_rows: list[tuple[int, dict[str, Any]]],
) -> LegacyVideoStudyResult:
    import pandas as pd

    items = _legacy_video_items_frame(indexed_rows)
    items.sort_values("update_time", inplace=True)
    _custom_fillna_like_kq5034(items, 0, numeric_fill_value=0)

    def update_single_progress(item):
        if pd.isna(item["cum_seconds"]):
            item["cum_seconds"] = 0
        if pd.isna(item["progress"]):
            item["progress"] = 0

        if item["progress"] < 100 and (item["cum_seconds"] >= 1800 or "已完成" in str(item["study_state"])):
            item["progress"] = 100

        return item

    items = items.apply(update_single_progress, axis=1)
    x = items.loc[items["progress"].idxmax()]

    if x["progress"] >= 100:
        start_date = pd.to_datetime(lesson.start_date)
        update_time = pd.to_datetime(x["update_time"])
        target_complete_date = start_date + pd.Timedelta(days=0.5 + 7)
        diff = update_time - target_complete_date
        if diff.days < 1:
            text = "准时完成"
        else:
            delay_days = diff.days - 0.5
            delay_weeks = int(delay_days // 7) + 1
            text = f"延{delay_weeks}周完成"
    else:
        progress, cum_seconds = x["progress"], x["cum_seconds"]
        if progress:
            text = f"进度{progress}%"
        elif cum_seconds:
            text = f"观看{cum_seconds // 60}分钟"
        else:
            text = ""

    return LegacyVideoStudyResult(text=text, keep_sequence=int(x.name), updated_fields={})


def _legacy_video_study_result_c(
    lesson: VideoConfigItem,
    indexed_rows: list[tuple[int, dict[str, Any]]],
) -> LegacyVideoStudyResult:
    items = _legacy_video_items_frame(indexed_rows)
    items.sort_values("update_time", inplace=True)
    _custom_fillna_like_kq5034(items, 0, numeric_fill_value=0)

    total_seconds = lesson.video_duration
    if total_seconds <= 0:
        return _legacy_video_existing_progress_result(indexed_rows)

    items["progress"] = items["cum_seconds"].apply(lambda x: int(x / total_seconds * 100))
    item = items.loc[items["progress"].idxmax()]
    return LegacyVideoStudyResult(
        text=_challenge_progress_text_from_percent(item["progress"]),
        keep_sequence=int(item.name),
        updated_fields={},
    )


def _legacy_video_study_result(
    lesson: VideoConfigItem,
    indexed_rows: list[tuple[int, dict[str, Any]]],
) -> LegacyVideoStudyResult:
    algorithm_kind = _legacy_video_algorithm_kind(lesson)
    if algorithm_kind == "b":
        return _legacy_video_study_result_b(lesson, indexed_rows)
    if algorithm_kind == "c":
        return _legacy_video_study_result_c(lesson, indexed_rows)
    return _legacy_video_study_result_a(lesson, indexed_rows)


def _has_legacy_video_rows_to_remove(
    indexed_rows: list[tuple[int, dict[str, Any]]],
    keep_sequence: int,
) -> bool:
    keep_row = next((row for sequence, row in indexed_rows if sequence == keep_sequence), None)
    keep_lesson_data_id = _normalize_text(keep_row.get("lesson_data_id")) if keep_row else ""
    if not keep_lesson_data_id:
        return len(indexed_rows) > 1
    return bool({
        _normalize_text(row.get("lesson_data_id"))
        for _sequence, row in indexed_rows
        if _normalize_text(row.get("lesson_data_id"))
    } - {keep_lesson_data_id})


def _compute_legacy_video_study_results(
    document: dict[str, Any],
    video_config: list[VideoConfigItem] | None = None,
) -> tuple[dict[tuple[str, str], LegacyVideoStudyResult], int]:
    video_config_by_lesson_id = {
        item.lesson_id: item
        for item in video_config or []
    }
    grouped_rows: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
    preserved_unusable_rows = 0
    for sequence, row in enumerate(_sheet_rows_as_dicts(document)):
        key = _row_identity_key(row)
        lesson_id = _normalize_text(row.get("lesson_id"))
        if not key or not lesson_id or lesson_id not in video_config_by_lesson_id:
            preserved_unusable_rows += 1
            continue
        grouped_rows.setdefault((key, lesson_id), []).append((sequence, row))

    results: dict[tuple[str, str], LegacyVideoStudyResult] = {}
    for group_key, indexed_rows in grouped_rows.items():
        lesson = video_config_by_lesson_id[group_key[1]]
        result = _legacy_video_study_result(lesson, indexed_rows)
        if result.updated_fields and not _has_legacy_video_rows_to_remove(indexed_rows, result.keep_sequence):
            result = LegacyVideoStudyResult(
                text=result.text,
                keep_sequence=result.keep_sequence,
                updated_fields={},
            )
        results[group_key] = result
    return results, preserved_unusable_rows


def _load_video_data(
    document: dict[str, Any],
    video_config: list[VideoConfigItem] | None = None,
) -> dict[tuple[str, str], str]:
    results, _preserved_unusable_rows = _compute_legacy_video_study_results(document, video_config)
    return {
        key: result.text
        for key, result in results.items()
    }


def _select_video_progress_for_identity_keys(
    video_data: dict[tuple[str, str], str],
    identity_keys: list[str],
    item: VideoConfigItem,
    rule_version: str,
) -> str:
    values = [
        _normalize_text(video_data.get((identity_key, item.lesson_id)))
        for identity_key in identity_keys
    ]
    candidates = [value for value in values if value]
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]

    if not item.participates_refund:
        return max(
            candidates,
            key=lambda value: (
                1 if value else 0,
                parse_progress_percent(value) or 0,
                len(value),
            ),
        )

    rules = _rules_for_version(item.rules_by_version, rule_version)

    def sort_key(value: str) -> tuple[float, int, float, int]:
        refund_amount, _color = _highlight_video_refund_for_item(item, rule_version, value)
        return (
            refund_amount,
            _video_completed_count_for_item(item, rule_version, value),
            _video_refund_progress_percent(value) or 0,
            len(value),
        )

    return max(candidates, key=sort_key)


def _is_no_video_progress_text(value: Any) -> bool:
    text = _normalize_text(value)
    if not text:
        return True
    progress_percent = parse_progress_percent(text)
    return progress_percent is not None and progress_percent <= 0


def _compact_nianzhu_video_data_document(
    document: dict[str, Any],
    video_config: list[VideoConfigItem] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_rows = _sheet_rows_as_dicts(document)
    results, preserved_unusable_rows = _compute_legacy_video_study_results(document, video_config)
    selected_sequences = {result.keep_sequence for result in results.values()}
    updated_fields_by_sequence: dict[int, dict[str, Any]] = {}
    for result in results.values():
        if result.updated_fields:
            updated_fields_by_sequence[result.keep_sequence] = result.updated_fields
    compacted_rows: list[dict[str, Any]] = []
    for sequence, row in enumerate(source_rows):
        key = _row_identity_key(row)
        lesson_id = _normalize_text(row.get("lesson_id"))
        group_key = (key, lesson_id)
        if not key or not lesson_id or group_key not in results:
            compacted_rows.append(dict(row))
            continue
        if sequence in selected_sequences:
            next_row = dict(row)
            next_row.update(updated_fields_by_sequence.get(sequence, {}))
            compacted_rows.append(next_row)

    columns = _normalize_document_columns(document) or VIDEO_DATA_COLUMNS
    next_document = _make_table_document_from_dicts(
        columns=columns,
        rows=compacted_rows,
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
        source_meta=dict(document.get("source_meta") or {}),
    )
    changed = next_document != document
    summary = {
        "video_data_rows_before": len(source_rows),
        "video_data_rows_after": len(compacted_rows),
        "video_data_removed_rows": max(len(source_rows) - len(compacted_rows), 0),
        "video_data_preserved_unusable_rows": preserved_unusable_rows,
        "video_data_user_lesson_pairs": len(results),
        "changed": changed,
    }
    return next_document, summary


def compact_nianzhu_course_sheet_step2(
    session: Session,
    *,
    attendance_sheet_id: int = NIANZHU_ATTENDANCE_SHEET_NUMERIC_ID,
    course_name: str = "",
) -> dict[str, Any]:
    attendance = _get_sheet(session, attendance_sheet_id)
    bundle = _load_course_sheet_bundle(session, attendance=attendance)
    video_config_document = dict(bundle[VIDEO_CONFIG_SHEET_KEY].document_json or {})
    video_config_sheet = bundle[VIDEO_CONFIG_SHEET_KEY]
    if _ensure_course_source_meta(video_config_document, course_name=course_name):
        video_config_sheet.document_json = video_config_document
        video_config_sheet.version = max(int(video_config_sheet.version or 1), 1) + 1
        video_config_sheet.updated_at = time.time()
        session.add(video_config_sheet)
    video_data_sheet = bundle[VIDEO_DATA_SHEET_KEY]
    current_document = dict(video_data_sheet.document_json or {})
    video_config = _load_video_config(video_config_document)
    next_document, summary = _compact_nianzhu_video_data_document(current_document, video_config)
    if summary["changed"]:
        _update_course_sheet_document(video_data_sheet, next_document)
        session.add(video_data_sheet)
    return {
        "attendance_sheet_id": int(attendance.numeric_id or 0),
        "video_data_sheet_id": int(video_data_sheet.numeric_id or 0),
        "video_config_rows": len(video_config),
        **summary,
    }


def _load_clockin_rules(document: dict[str, Any]) -> dict[str, dict[str, list[ThresholdRefundRule]]]:
    rows = _sheet_rows_as_dicts(document)
    if not rows:
        return {}
    result: dict[str, dict[str, list[ThresholdRefundRule]]] = {}
    for row in rows:
        field_name = _normalize_text(row.get("name")) or "打卡数"
        result[field_name] = {
            CURRENT_RULE: _parse_threshold_rules(DEFAULT_CLOCKIN_RULE),
            LEGACY_AFTER_20250522_RULE: _parse_threshold_rules(DEFAULT_CLOCKIN_RULE),
            LEGACY_BEFORE_20250522_RULE: _parse_threshold_rules(DEFAULT_CLOCKIN_RULE),
        }
    return result


def _extract_course_edition_number(course_name: str) -> int | None:
    match = re.search(r"第\s*0*(\d+)\s*届", _normalize_text(course_name))
    return int(match.group(1)) if match else None


def _clockin_title_allowlist_for_course(course_name: str) -> set[str] | None:
    normalized = _normalize_text(course_name)
    edition = _extract_course_edition_number(normalized)
    if "觉观" in normalized:
        titles = {f"【打卡】中心教室-{index}" for index in range(1, 23)}
        if edition:
            titles.update(f"【打卡】第{edition}届中心教室-{index}" for index in range(1, 23))
            titles.update(f"【第{edition}届中心教室】—第{index}课打卡" for index in range(1, 23))
        return titles

    if "念住" in normalized and "念住闯关" not in normalized:
        titles = {f"念住学修日志-{index:02}" for index in range(1, 22)}
        if edition:
            titles.update(f"第{edition}届念住学修日志-{index:02}" for index in range(1, 22))
        return titles

    return None


def _resolve_course_name_from_documents(
    *documents: dict[str, Any],
    course_name: str = "",
) -> str:
    resolved_course_name = _normalize_text(course_name)
    if resolved_course_name:
        return resolved_course_name
    for document in documents:
        source_meta = dict(document.get("source_meta") or {})
        resolved_course_name = _normalize_text(source_meta.get("course_name"))
        if resolved_course_name:
            return resolved_course_name
    return ""


def _collect_clockin_data(
    document: dict[str, Any],
    *,
    allowed_titles: set[str] | None = None,
) -> tuple[dict[tuple[str, str], set[str]], dict[tuple[str, str], float]]:
    grouped_keys: dict[tuple[str, str], set[str]] = {}
    result: dict[tuple[str, str], float] = {}
    for sequence, row in enumerate(_sheet_rows_as_dicts(document)):
        key = _row_identity_key(row)
        if not key:
            continue
        if "clockin_data_id" in row:
            title = _normalize_text(row.get("update_title"))
            if title.startswith("测试-"):
                continue
            if allowed_titles is not None and title not in allowed_titles:
                continue
            task_date = _normalize_text(row.get("task_date"))
            publish_time = _parse_datetime_cell(row.get("publish_time"))
            if allowed_titles is not None:
                clockin_key = f"title:{title}"
            elif task_date:
                clockin_key = f"task:{task_date}|{title}"
            elif publish_time is not None:
                clockin_key = f"publish:{publish_time.date()}|{title}"
            else:
                clockin_key = f"row:{sequence}|{title}"
            field_name = _normalize_text(row.get("clockin_name")) or "打卡数"
            grouped_keys.setdefault((key, field_name), set()).add(clockin_key)
            continue

        field_name = _normalize_text(row.get("字段名")) or "打卡数"
        result[(key, field_name)] = result.get((key, field_name), 0.0) + _to_float(row.get("打卡数"))
    return grouped_keys, result


def _load_clockin_data(document: dict[str, Any]) -> dict[tuple[str, str], float]:
    grouped_keys, result = _collect_clockin_data(document)
    next_result = dict(result)
    for key, clockin_keys in grouped_keys.items():
        next_result[key] = next_result.get(key, 0.0) + len(clockin_keys)
    return next_result


def _clockin_count_for_identity_keys(
    grouped_keys: dict[tuple[str, str], set[str]],
    numeric_counts: dict[tuple[str, str], float],
    identity_keys: list[str],
    field_name: str,
) -> float:
    merged_keys: set[str] = set()
    count = 0.0
    for identity_key in identity_keys:
        merged_keys.update(grouped_keys.get((identity_key, field_name), set()))
        count += numeric_counts.get((identity_key, field_name), 0.0)
    return count + len(merged_keys)


def _clockin_output_fields(
    clockin_config_document: dict[str, Any],
    columns: list[str],
) -> list[tuple[str, str, int]]:
    config_names = [
        _normalize_text(row.get("name"))
        for row in _sheet_rows_as_dicts(clockin_config_document)
        if _normalize_text(row.get("name"))
    ] or ["打卡数"]

    result: list[tuple[str, str, int]] = []
    seen: set[tuple[str, int]] = set()
    for field_name in config_names:
        output_name = field_name
        column_index = _find_column_index(columns, output_name)
        if column_index is None and field_name in {"打卡", "每日打卡"}:
            output_name = "打卡数"
            column_index = _find_column_index(columns, output_name)
        if column_index is None:
            continue
        key = (field_name, column_index)
        if key in seen:
            continue
        seen.add(key)
        result.append((field_name, output_name, column_index))
    return result


def _set_entity_cell_background(
    document: dict[str, Any],
    *,
    document_row: int,
    column_index: int,
    color: str | None,
) -> bool:
    entity_rows = document.get("entity_rows")
    entity_columns = document.get("entity_columns")
    entity_cells = document.get("entity_cells")
    if not isinstance(entity_rows, list) or not isinstance(entity_columns, list) or not isinstance(entity_cells, dict):
        return False
    if document_row < 0 or document_row >= len(entity_rows) or column_index < 0 or column_index >= len(entity_columns):
        return False

    row_record = entity_rows[document_row]
    column_record = entity_columns[column_index]
    row_id = row_record.get("id") if isinstance(row_record, dict) else None
    column_id = column_record.get("id") if isinstance(column_record, dict) else None
    if not row_id or not column_id:
        return False

    row_cells = entity_cells.get(row_id)
    if not isinstance(row_cells, dict):
        if not color:
            return False
        row_cells = {}
        entity_cells[row_id] = row_cells

    previous_cell = row_cells.get(column_id)
    next_cell = dict(previous_cell) if isinstance(previous_cell, dict) else {}
    style = dict(next_cell.get("style")) if isinstance(next_cell.get("style"), dict) else {}
    previous_color = style.get("background_color")

    if color:
        style["background_color"] = color
    else:
        style.pop("background_color", None)

    if style:
        next_cell["style"] = style
    else:
        next_cell.pop("style", None)

    if next_cell:
        row_cells[column_id] = next_cell
    else:
        row_cells.pop(column_id, None)
        if not row_cells:
            entity_cells.pop(row_id, None)

    return previous_color != color


def _set_attendance_cell_background(
    document: dict[str, Any],
    cell_meta: dict[str, Any],
    *,
    document_row: int,
    column_index: int,
    color: str | None,
) -> bool:
    legacy_changed = set_cell_background(
        cell_meta,
        document_row=document_row,
        column_index=column_index,
        color=color,
    )
    entity_changed = _set_entity_cell_background(
        document,
        document_row=document_row,
        column_index=column_index,
        color=color,
    )
    return legacy_changed or entity_changed


def _clear_blank_progress_backgrounds(
    document: dict[str, Any],
    cell_meta: dict[str, Any],
    *,
    row: list[Any],
    document_row: int,
    column_indexes: list[int],
) -> int:
    cleared_count = 0
    for column_index in column_indexes:
        if column_index < 0 or column_index >= len(row):
            continue
        if not _is_no_video_progress_text(row[column_index]):
            continue
        if _set_attendance_cell_background(
            document,
            cell_meta,
            document_row=document_row,
            column_index=column_index,
            color=None,
        ):
            cleared_count += 1
    return cleared_count


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
    course_name: str = "",
    manual_video_progress_overrides: dict[tuple[int, int], str] | None = None,
) -> dict[str, Any]:
    attendance = _get_sheet(session, attendance_sheet_id)
    bundle = _load_course_sheet_bundle(session, attendance=attendance)
    original_document = copy.deepcopy(dict(attendance.document_json or {}))
    effective_course_name = _resolve_course_name_from_documents(
        dict(bundle[VIDEO_CONFIG_SHEET_KEY].document_json or {}),
        dict(bundle[VIDEO_DATA_SHEET_KEY].document_json or {}),
        dict(bundle[CLOCKIN_CONFIG_SHEET_KEY].document_json or {}),
        dict(bundle[CLOCKIN_DATA_SHEET_KEY].document_json or {}),
        course_name=course_name,
    )
    current_document, schema_summary = _ensure_nianzhu_attendance_schema(
        original_document,
        course_name=effective_course_name,
    )
    current_document, formula_config_defaulted_cells = _ensure_refund_baseline_config_cell(current_document)
    if formula_config_defaulted_cells:
        schema_summary["formula_config_defaulted_cells"] = formula_config_defaulted_cells
    current_document, refund_period_config_repaired_cells = _ensure_refund_period_config_cell(current_document)
    if refund_period_config_repaired_cells:
        schema_summary["refund_period_config_repaired_cells"] = refund_period_config_repaired_cells
    columns = _normalize_document_columns(current_document)
    rows = [_normalize_row(row, len(columns)) for row in _extract_document_rows(current_document)]
    data_start_row = _normalize_document_data_start_row(current_document)

    indexes = {
        "优秀学员评分": _find_column_index(columns, "优秀学员评分"),
        "禅客": _find_column_index(columns, "禅客"),
        "完成视频数": _find_column_index(columns, "完成视频数"),
        "视频应返款": _find_column_index(columns, "视频应返款"),
        "打卡应返款": _find_column_index(columns, "打卡应返款"),
        "总应返款": _find_column_index(columns, "总应返款"),
        "已返款": _find_column_index(columns, "已返款"),
        "订单金额": _find_column_index(columns, "订单金额"),
        "当前应返款": _find_column_index(columns, "当前应返款"),
    }
    rule_version_index = _find_column_index(columns, RULE_VERSION_COLUMN)

    video_config_sheet = bundle[VIDEO_CONFIG_SHEET_KEY]
    video_config_document = dict(video_config_sheet.document_json or {})
    if _ensure_course_source_meta(video_config_document, course_name=course_name):
        video_config_sheet.document_json = video_config_document
        video_config_sheet.version = max(int(video_config_sheet.version or 1), 1) + 1
        video_config_sheet.updated_at = time.time()
        session.add(video_config_sheet)
    video_config = _load_video_config(
        video_config_document,
        timed_video_rules_override=_attendance_video_refund_rules_override(current_document, columns),
    )
    legacy_zen_video_refund_amount = _attendance_legacy_zen_video_refund_amount(current_document, columns)
    video_data = _load_video_data(dict(bundle[VIDEO_DATA_SHEET_KEY].document_json or {}), video_config)
    clockin_config_document = dict(bundle[CLOCKIN_CONFIG_SHEET_KEY].document_json or {})
    clockin_rules = _load_clockin_rules(clockin_config_document)
    clockin_output_fields = _clockin_output_fields(clockin_config_document, columns)
    clockin_data_document = dict(bundle[CLOCKIN_DATA_SHEET_KEY].document_json or {})
    clockin_title_allowlist = _clockin_title_allowlist_for_course(
        _resolve_course_name_from_documents(
            video_config_document,
            clockin_config_document,
            clockin_data_document,
            course_name=course_name,
        )
    )
    strict_clockin_source = clockin_title_allowlist is not None and bool(clockin_data_document.get("rows"))
    clockin_key_groups, clockin_numeric_counts = _collect_clockin_data(
        clockin_data_document,
        allowed_titles=clockin_title_allowlist,
    )
    registration_identity_map = build_registration_identity_map(
        session,
        attendance=attendance,
        default_owner_key=NIANZHU_OWNER_KEY,
    )
    video_course_keys = {item.course_key for item in video_config if item.course_key}
    progress_column_by_key: dict[str, int] = {}
    for column_index, column_name in enumerate(columns):
        key = _course_item_key(column_name)
        if key and key in video_course_keys and key not in progress_column_by_key:
            progress_column_by_key[key] = column_index
    video_column_indexes = {
        item.lesson_id: progress_column_by_key.get(item.course_key)
        for item in video_config
    }
    blank_progress_cleanup_columns = sorted({
        column_index
        for column_index in video_column_indexes.values()
        if column_index is not None
    })

    cell_meta = copy.deepcopy(current_document.get("cell_meta") or {})
    next_rows: list[list[Any]] = []
    updated_rows = 0
    updated_cells = 0
    styled_cells = 0
    skipped_rows = 0
    missing_identity_rows = 0
    total_video_refund = 0.0
    total_clockin_refund = 0.0
    has_refund_tracking_context = (
        indexes["总应返款"] is not None
        and indexes["订单金额"] is not None
        and indexes["已返款"] is not None
        and indexes["当前应返款"] is not None
    )

    for row_index, row in enumerate(rows):
        next_row = list(row)
        document_row = data_start_row + row_index
        row_number = _formula_row_number(current_document, row_index)
        row_changed = False
        if _sync_row_local_managed_formulas(
            current_document,
            next_row,
            columns=columns,
            document_row=document_row,
            row_number=row_number,
        ):
            updated_cells += 1
            row_changed = True
        styled_cells += _clear_blank_progress_backgrounds(
            current_document,
            cell_meta,
            row=next_row,
            document_row=document_row,
            column_indexes=blank_progress_cleanup_columns,
        )
        if not _is_active_tracking_row(next_row, columns, active_only=active_only):
            skipped_rows += 1
            if row_changed:
                updated_rows += 1
            next_rows.append(next_row)
            continue

        identity_keys = _row_identity_keys(
            next_row,
            columns,
            registration_identity_map=registration_identity_map,
        )
        if not identity_keys:
            missing_identity_rows += 1
            if row_changed:
                updated_rows += 1
            next_rows.append(next_row)
            continue

        rule_version = CURRENT_RULE
        if rule_version_index is not None:
            rule_version = _normalize_text(next_row[rule_version_index]) or CURRENT_RULE

        video_refund = 0.0
        completed_video_count = 0
        score = 0

        for item in video_config:
            column_index = video_column_indexes.get(item.lesson_id)
            if column_index is None:
                continue
            value = _select_video_progress_for_identity_keys(video_data, identity_keys, item, rule_version)
            if _is_no_video_progress_text(value) and not _is_no_video_progress_text(next_row[column_index]):
                value = _normalize_text(next_row[column_index])
            if manual_video_progress_overrides:
                override_label = manual_video_progress_overrides.get((document_row, column_index))
                if override_label is not None:
                    override_value = _video_revision_value_for_item(item, rule_version, override_label)
                    if override_value is not None:
                        value = override_value
            if next_row[column_index] != value:
                next_row[column_index] = value
                updated_cells += 1
                row_changed = True

            color: str | None = None
            if item.participates_refund:
                completed_count = _video_completed_count_for_item(item, rule_version, value)
                if completed_count > 0:
                    completed_video_count += 1
                refund_amount, color = _highlight_video_refund_for_item(item, rule_version, value)
                video_refund += refund_amount
                if item.participates_score and refund_amount > 0:
                    score += max(completed_count - 1, 0)
            else:
                color = highlight_presence_progress(value)

            if _set_attendance_cell_background(
                current_document,
                cell_meta,
                document_row=document_row,
                column_index=column_index,
                color=color,
            ):
                styled_cells += 1

        clockin_refund = 0.0
        for field_name, output_name, column_index in clockin_output_fields:
            clockin_count = _clockin_count_for_identity_keys(
                clockin_key_groups,
                clockin_numeric_counts,
                identity_keys,
                field_name,
            )
            existing_clockin_count = _to_float(next_row[column_index])
            if clockin_count <= 0 and existing_clockin_count > 0 and not strict_clockin_source:
                clockin_count = existing_clockin_count
            clockin_value = _format_numeric_cell(clockin_count)
            if clockin_count <= 0 and output_name == "打卡数":
                clockin_value = ""
            if next_row[column_index] != clockin_value:
                next_row[column_index] = clockin_value
                updated_cells += 1
                row_changed = True

            clockin_refund_rules = (
                _clockin_refund_rules_for_formula(
                    current_document,
                    refund_column_index=indexes["打卡应返款"],
                    rule_version=rule_version,
                    clockin_rules=clockin_rules,
                )
                if output_name == "打卡数"
                else _rules_for_version(clockin_rules.get(field_name, {}), rule_version)
            )
            field_refund, clockin_color = highlight_threshold_refund_progress(
                clockin_refund_rules,
                clockin_count,
            )
            if output_name == "打卡数":
                clockin_refund += field_refund
                if _set_attendance_cell_background(
                    current_document,
                    cell_meta,
                    document_row=document_row,
                    column_index=column_index,
                    color=clockin_color,
                ):
                    styled_cells += 1

        total_clockin_refund += clockin_refund
        completed_video_formula = _build_completed_video_count_formula(
            video_config,
            video_column_indexes,
            row_number=row_number,
            rule_version=rule_version,
        )
        video_refund_formula = _build_video_refund_formula(
            video_config,
            video_column_indexes,
            row_number=row_number,
            rule_version=rule_version,
            legacy_zen_refund_amount=legacy_zen_video_refund_amount,
        )
        clockin_refund_formula = (
            _build_clockin_refund_formula(
                columns,
                row_number=row_number,
                rules=_clockin_refund_rules_for_formula(
                    current_document,
                    refund_column_index=indexes["打卡应返款"],
                    rule_version=rule_version,
                    clockin_rules=clockin_rules,
                ),
            )
            if has_refund_tracking_context
            else None
        )
        total_refund_formula = _build_total_refund_formula(
            columns,
            row_number=row_number,
            config_row_number=_formula_config_row_number(current_document),
        )
        current_refund_formula = _build_current_refund_formula(columns, row_number=row_number)
        zen_guest_formula = _build_zen_guest_formula(columns, row_number=row_number)
        scalar_updates = {
            "优秀学员评分": score,
            "完成视频数": completed_video_formula or completed_video_count,
            "视频应返款": video_refund_formula or _format_numeric_cell(video_refund),
            "打卡应返款": clockin_refund_formula or _format_numeric_cell(clockin_refund),
        }
        if zen_guest_formula:
            scalar_updates["禅客"] = zen_guest_formula
        if total_refund_formula:
            scalar_updates["总应返款"] = total_refund_formula
        if current_refund_formula:
            scalar_updates["当前应返款"] = current_refund_formula
        total_video_refund += video_refund
        for field_name, value in scalar_updates.items():
            column_index = indexes[field_name]
            if column_index is None:
                continue
            managed_formula = field_name in NIANZHU_ATTENDANCE_MANAGED_FORMULA_COLUMNS and _is_formula_expression(value)
            if _is_formula_expression(next_row[column_index]) and not managed_formula:
                continue
            if _set_row_value(
                current_document,
                next_row,
                document_row=document_row,
                column_index=column_index,
                value=value,
            ):
                updated_cells += 1
                row_changed = True

        if row_changed:
            updated_rows += 1
        next_rows.append(next_row)

    next_document = dict(current_document)
    next_document["cell_meta"] = cell_meta
    next_document = _replace_document_data_rows(next_document, next_rows)
    if next_document != original_document:
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
        **schema_summary,
    }


def _video_revision_value_for_item(item: VideoConfigItem, rule_version: str, label: Any) -> str | None:
    text = re.sub(r"\s+", "", _normalize_text(label))
    if not text:
        return None

    algorithm_kind = _legacy_video_algorithm_kind(item)
    if algorithm_kind == "b":
        if text in {"准时完成", "当周完成"}:
            return "准时完成"
        match = re.fullmatch(r"延(\d+)周完成", text)
        if match:
            return f"延{int(match.group(1))}周完成"
        return None

    if algorithm_kind == "c":
        match = re.fullmatch(r"([123])遍(?:完成)?", text)
        if match:
            play_count = int(match.group(1))
            progress = 100 if play_count == 1 else 150 if play_count == 2 else 200
            return f"{play_count}遍/{progress}%"
        return None

    text_rules = _rules_for_version(item.text_rules_by_version, rule_version, fallback_version=CURRENT_RULE)
    if isinstance(text_rules, dict) and text_rules:
        if text == "当堂完成":
            return "当堂完成/100%"
        match = re.fullmatch(r"第(\d+)天回放", text)
        if match:
            return f"第{int(match.group(1))}天回放/100%"
    return None


def apply_nianzhu_attendance_video_revision(
    session: Session,
    *,
    attendance_sheet_id: int,
    cells: list[dict[str, int]],
    revision_label: str,
    active_only: bool = True,
    course_name: str = "",
) -> dict[str, Any]:
    attendance = _get_sheet(session, attendance_sheet_id)
    bundle = _load_course_sheet_bundle(session, attendance=attendance)
    effective_course_name = _resolve_course_name_from_documents(
        dict(bundle[VIDEO_CONFIG_SHEET_KEY].document_json or {}),
        dict(bundle[VIDEO_DATA_SHEET_KEY].document_json or {}),
        dict(bundle[CLOCKIN_CONFIG_SHEET_KEY].document_json or {}),
        dict(bundle[CLOCKIN_DATA_SHEET_KEY].document_json or {}),
        course_name=course_name,
    )
    current_document, _schema_summary = _ensure_nianzhu_attendance_schema(
        dict(attendance.document_json or {}),
        course_name=effective_course_name,
    )
    columns = _normalize_document_columns(current_document)
    rows = [_normalize_row(row, len(columns)) for row in _extract_document_rows(current_document)]
    data_start_row = _normalize_document_data_start_row(current_document)
    rule_version_index = _find_column_index(columns, RULE_VERSION_COLUMN)

    video_config_sheet = bundle[VIDEO_CONFIG_SHEET_KEY]
    video_config = _load_video_config(
        dict(video_config_sheet.document_json or {}),
        timed_video_rules_override=_attendance_video_refund_rules_override(current_document, columns),
    )
    video_course_keys = {item.course_key for item in video_config if item.course_key}
    progress_column_by_key: dict[str, int] = {}
    for column_index, column_name in enumerate(columns):
        key = _course_item_key(column_name)
        if key and key in video_course_keys and key not in progress_column_by_key:
            progress_column_by_key[key] = column_index

    item_by_column: dict[int, VideoConfigItem] = {}
    for item in video_config:
        column_index = progress_column_by_key.get(item.course_key)
        if column_index is not None:
            item_by_column[column_index] = item

    if not item_by_column:
        raise ValueError("当前考勤表没有可修订的视频数据列")

    overrides: dict[tuple[int, int], str] = {}
    seen: set[tuple[int, int]] = set()
    for cell in cells:
        row_index = int(cell.get("row_index", -1))
        column_index = int(cell.get("column_index", -1))
        if row_index < 0 or row_index >= len(rows):
            raise ValueError("选区包含不存在的数据行")
        item = item_by_column.get(column_index)
        if item is None:
            raise ValueError("选区必须完全位于视频数据区域")
        rule_version = CURRENT_RULE
        if rule_version_index is not None:
            rule_version = _normalize_text(rows[row_index][rule_version_index]) or CURRENT_RULE
        if _video_revision_value_for_item(item, rule_version, revision_label) is None:
            raise ValueError(f"{revision_label} 不适用于选中的视频列")
        document_row = data_start_row + row_index
        key = (document_row, column_index)
        if key in seen:
            continue
        seen.add(key)
        overrides[key] = revision_label

    if not overrides:
        raise ValueError("请选择要修订的视频数据单元格")

    summary = rebuild_nianzhu_attendance_from_course_sheets(
        session,
        attendance_sheet_id=attendance_sheet_id,
        active_only=active_only,
        course_name=course_name,
        manual_video_progress_overrides=overrides,
    )
    summary["revision_label"] = revision_label
    summary["revision_target_count"] = len(overrides)
    return summary


def _grid_cell_value(document: dict[str, Any], row_index: int, column_index: int) -> Any:
    rows = document.get("grid_rows")
    if not isinstance(rows, list) or row_index < 0 or row_index >= len(rows):
        return ""
    row = rows[row_index]
    if not isinstance(row, list) or column_index < 0 or column_index >= len(row):
        return ""
    return row[column_index]


def _formula_row_number(document: dict[str, Any], row_index: int) -> int:
    return _normalize_document_data_start_row(document) + row_index + 1


def _formula_config_row_number(document: dict[str, Any]) -> int:
    return max(_normalize_document_data_start_row(document), 1)


def _formula_cell_ref(column_index: int, row_number: int) -> str:
    return f"{_excel_column_label(column_index)}{row_number}"


def _formula_absolute_cell_ref(column_index: int, row_number: int) -> str:
    return f"${_excel_column_label(column_index)}${row_number}"


def _formula_row_ranges(column_indexes: list[int], row_number: int) -> list[str]:
    sorted_indexes = sorted({index for index in column_indexes if index >= 0})
    if not sorted_indexes:
        return []

    ranges: list[str] = []
    start = previous = sorted_indexes[0]
    for index in sorted_indexes[1:]:
        if index == previous + 1:
            previous = index
            continue
        start_ref = _formula_cell_ref(start, row_number)
        previous_ref = _formula_cell_ref(previous, row_number)
        ranges.append(start_ref if start == previous else f"{start_ref}:{previous_ref}")
        start = previous = index
    start_ref = _formula_cell_ref(start, row_number)
    previous_ref = _formula_cell_ref(previous, row_number)
    ranges.append(start_ref if start == previous else f"{start_ref}:{previous_ref}")
    return ranges


def _build_completed_video_count_formula(
    video_config: list[VideoConfigItem],
    video_column_indexes: dict[str, int | None],
    *,
    row_number: int,
    rule_version: str,
) -> str | None:
    challenge_column_indexes = [
        column_index
        for item in video_config
        for column_index in [video_column_indexes.get(item.lesson_id)]
        if item.participates_refund and column_index is not None and _is_challenge_video_item(item)
    ]
    if challenge_column_indexes:
        ranges = _formula_row_ranges(challenge_column_indexes, row_number)
        if not ranges:
            return None
        return "=" + "+".join(f'COUNTIF({range_ref},"*遍*")' for range_ref in ranges)
    column_indexes = [
        column_index
        for item in video_config
        for column_index in [video_column_indexes.get(item.lesson_id)]
        if (
            item.participates_refund
            and column_index is not None
            and (
                _rules_for_version(item.text_rules_by_version, rule_version, fallback_version=CURRENT_RULE)
                or _is_regular_video_item(item)
                or _is_zen_stage_video_item(item)
            )
        )
    ]
    ranges = _formula_row_ranges(column_indexes, row_number)
    if not ranges:
        return None
    parts = [
        f'COUNTIF({range_ref},"*完成*")'
        for range_ref in ranges
    ]
    parts.extend(
        f'COUNTIF({range_ref},"*回放*")'
        for range_ref in ranges
    )
    return "=" + "+".join(parts)


def _build_legacy_zen_video_refund_formula(
    video_config: list[VideoConfigItem],
    video_column_indexes: dict[str, int | None],
    *,
    row_number: int,
    refund_amount: float,
) -> str | None:
    if refund_amount <= 0:
        return None
    column_indexes = [
        column_index
        for item in video_config
        for column_index in [video_column_indexes.get(item.lesson_id)]
        if item.participates_refund
        and column_index is not None
        and _is_zen_stage_video_item(item)
    ]
    ranges = _formula_row_ranges(column_indexes, row_number)
    if not ranges:
        return None
    amount_text = _format_numeric_cell(refund_amount)
    return "=" + "+".join(f'COUNTIF({range_ref},"准时完成")*{amount_text}' for range_ref in ranges)


def _build_video_refund_formula(
    video_config: list[VideoConfigItem],
    video_column_indexes: dict[str, int | None],
    *,
    row_number: int,
    rule_version: str,
    legacy_zen_refund_amount: float,
) -> str | None:
    systems = {item.rule_system for item in video_config if item.participates_refund}
    if VIDEO_RULE_SYSTEM_CHALLENGE in systems:
        return _build_challenge_video_refund_formula(
            video_config,
            video_column_indexes,
            row_number=row_number,
            rule_version=rule_version,
        )
    if VIDEO_RULE_SYSTEM_ZEN_STAGE in systems:
        return _build_legacy_zen_video_refund_formula(
            video_config,
            video_column_indexes,
            row_number=row_number,
            refund_amount=legacy_zen_refund_amount,
        )
    return _build_timed_video_refund_formula(
        video_config,
        video_column_indexes,
        row_number=row_number,
        rule_version=rule_version,
    )


def _build_challenge_video_refund_formula(
    video_config: list[VideoConfigItem],
    video_column_indexes: dict[str, int | None],
    *,
    row_number: int,
    rule_version: str,
) -> str | None:
    items = [
        item
        for item in video_config
        if (
            item.participates_refund
            and video_column_indexes.get(item.lesson_id) is not None
            and _is_challenge_video_item(item)
        )
    ]
    if not items:
        return None

    ranges = _formula_row_ranges(
        [video_column_indexes[item.lesson_id] for item in items if video_column_indexes.get(item.lesson_id) is not None],
        row_number,
    )
    if not ranges:
        return None

    rules = _rules_for_version(items[0].rules_by_version, rule_version)
    thresholds = [(int(rule.threshold_percent), rule.refund_amount) for rule in rules]
    if thresholds == [(90, 20)]:
        return "=" + "+".join(f'COUNTIF({range_ref},"*遍*")*20' for range_ref in ranges)
    if thresholds == [(90, 10), (150, 15), (200, 20)]:
        parts: list[str] = []
        for range_ref in ranges:
            parts.extend([
                f'COUNTIF({range_ref},"*1遍*")*10',
                f'COUNTIF({range_ref},"*2遍*")*15',
                f'COUNTIF({range_ref},"*3遍*")*20',
            ])
        return "=" + "+".join(parts)
    return None


def _build_timed_video_refund_formula(
    video_config: list[VideoConfigItem],
    video_column_indexes: dict[str, int | None],
    *,
    row_number: int,
    rule_version: str,
) -> str | None:
    column_indexes = [
        column_index
        for item in video_config
        for column_index in [video_column_indexes.get(item.lesson_id)]
        if (
            item.participates_refund
            and column_index is not None
            and isinstance(
                _rules_for_version(item.text_rules_by_version, rule_version, fallback_version=CURRENT_RULE),
                dict,
            )
            and _rules_for_version(item.text_rules_by_version, rule_version, fallback_version=CURRENT_RULE)
        )
    ]
    ranges = _formula_row_ranges(column_indexes, row_number)
    if not ranges:
        return None

    rules_by_label: dict[str, int] = {}
    for item in video_config:
        text_rules = _rules_for_version(item.text_rules_by_version, rule_version, fallback_version=CURRENT_RULE)
        if not isinstance(text_rules, dict):
            continue
        for label, amount in text_rules.items():
            if amount > 0:
                rules_by_label[label] = amount
    if not rules_by_label:
        return None

    parts: list[str] = []
    for label, amount in rules_by_label.items():
        for range_ref in ranges:
            parts.append(f'COUNTIF({range_ref},"*{label}*")*{_format_numeric_cell(amount)}')
    return "=" + "+".join(parts)


def _parse_clockin_rules_from_note(value: Any) -> list[ThresholdRefundRule]:
    text = _normalize_text(value)
    if not text:
        return []
    match = re.search(
        r"达到[^0-9]*(\d+(?:\s*/\s*\d+)+)[^0-9]+(?:累计)?返回[^0-9]*(\d+(?:\s*/\s*\d+)+)",
        text,
    )
    if not match:
        return []
    thresholds = [int(_to_float(item)) for item in re.split(r"\s*/\s*", match.group(1)) if _normalize_text(item)]
    amounts = [int(_to_float(item)) for item in re.split(r"\s*/\s*", match.group(2)) if _normalize_text(item)]
    if len(thresholds) != len(amounts):
        return []
    return [
        ThresholdRefundRule(float(threshold), float(amount))
        for threshold, amount in zip(thresholds, amounts)
        if threshold > 0 and amount >= 0
    ]


def _clockin_refund_rules_for_formula(
    document: dict[str, Any],
    *,
    refund_column_index: int | None,
    rule_version: str,
    clockin_rules: dict[str, dict[str, list[ThresholdRefundRule]]],
) -> list[ThresholdRefundRule]:
    if refund_column_index is not None:
        note_rules = _parse_clockin_rules_from_note(
            _grid_cell_value(
                document,
                max(_normalize_document_data_start_row(document) - 1, 0),
                refund_column_index,
            )
        )
        if note_rules:
            return sorted(note_rules, key=lambda item: item.threshold)
    return _rules_for_version(clockin_rules.get("打卡数", {}), rule_version)


def _build_clockin_refund_formula(
    columns: list[str],
    *,
    row_number: int,
    rules: list[ThresholdRefundRule],
) -> str | None:
    clockin_index = _find_column_index(columns, "打卡数")
    if clockin_index is None or not rules:
        return None
    clockin_ref = _formula_cell_ref(clockin_index, row_number)
    parts: list[str] = []
    for rule in sorted(rules, key=lambda item: item.threshold, reverse=True):
        parts.extend([
            f"{clockin_ref}>={_format_numeric_cell(rule.threshold)}",
            str(_format_numeric_cell(rule.refund_amount)),
        ])
    parts.append("0")
    return "=SWITCH(TRUE," + ",".join(parts) + ")"


def _build_total_refund_formula(
    columns: list[str],
    *,
    row_number: int,
    config_row_number: int,
) -> str | None:
    video_index = _find_column_index(columns, "视频应返款")
    clockin_index = _find_column_index(columns, "打卡应返款")
    order_amount_index = _find_column_index(columns, "订单金额")
    if video_index is None or clockin_index is None or order_amount_index is None:
        return None
    video_ref = _formula_cell_ref(video_index, row_number)
    clockin_ref = _formula_cell_ref(clockin_index, row_number)
    order_ref = _formula_cell_ref(order_amount_index, row_number)
    baseline_ref = _formula_absolute_cell_ref(order_amount_index, config_row_number)
    return f"=MIN(IFERROR({video_ref}+{clockin_ref}+{order_ref}-IF({baseline_ref}>0,{baseline_ref},{order_ref}),0),{order_ref})"


def _build_current_refund_formula(columns: list[str], *, row_number: int) -> str | None:
    total_index = _find_column_index(columns, "总应返款")
    refunded_index = _find_column_index(columns, "已返款")
    order_amount_index = _find_column_index(columns, "订单金额")
    if total_index is None or refunded_index is None or order_amount_index is None:
        return None
    total_ref = _formula_cell_ref(total_index, row_number)
    refunded_ref = _formula_cell_ref(refunded_index, row_number)
    order_ref = _formula_cell_ref(order_amount_index, row_number)
    return f"=({order_ref}>0)*({total_ref}-{refunded_ref})"


def _build_zen_guest_formula(columns: list[str], *, row_number: int) -> str | None:
    completed_video_index = _find_column_index(columns, "完成视频数")
    clockin_index = _find_column_index(columns, "打卡数")
    if completed_video_index is None or clockin_index is None:
        return None
    completed_video_ref = _formula_cell_ref(completed_video_index, row_number)
    clockin_ref = _formula_cell_ref(clockin_index, row_number)
    return f'=IF(AND({completed_video_ref}>=11,{clockin_ref}>=7),"是","")'


def _sync_row_local_managed_formulas(
    document: dict[str, Any],
    row: list[Any],
    *,
    columns: list[str],
    document_row: int,
    row_number: int,
) -> int:
    updated_cells = 0
    zen_guest_index = _find_column_index(columns, "禅客")
    zen_guest_formula = _build_zen_guest_formula(columns, row_number=row_number)
    if zen_guest_formula and _set_row_value(
        document,
        row,
        document_row=document_row,
        column_index=zen_guest_index,
        value=zen_guest_formula,
    ):
        updated_cells += 1
    return updated_cells


def _ensure_refund_baseline_config_cell(document: dict[str, Any]) -> tuple[dict[str, Any], int]:
    columns = _normalize_document_columns(document)
    order_amount_index = _find_column_index(columns, "订单金额")
    if order_amount_index is None:
        return document, 0

    config_row_index = max(_normalize_document_data_start_row(document) - 1, 0)
    current_value = _grid_cell_value(document, config_row_index, order_amount_index)
    if _normalize_text(current_value) and _to_float(current_value) > 0:
        return document, 0

    rows = [_normalize_row(row, len(columns)) for row in _extract_document_rows(document)]
    baseline = ""
    for row in rows:
        amount = _to_float(row[order_amount_index])
        if amount > 0:
            baseline = str(_format_numeric_cell(amount))
            break
    if not baseline:
        total_index = _find_column_index(columns, "总应返款")
        baseline = _normalize_text(_grid_cell_value(document, config_row_index, total_index if total_index is not None else -1))
    if not baseline:
        return document, 0

    grid_rows = copy.deepcopy(document.get("grid_rows") or [])
    if not isinstance(grid_rows, list):
        return document, 0
    while len(grid_rows) <= config_row_index:
        grid_rows.append([""] * len(columns))
    config_row = _normalize_row(grid_rows[config_row_index], len(columns))
    config_row[order_amount_index] = baseline
    grid_rows[config_row_index] = config_row
    next_document = dict(document)
    next_document["grid_rows"] = grid_rows
    _set_entity_cell_value(
        next_document,
        document_row=config_row_index,
        column_index=order_amount_index,
        value=baseline,
    )
    return next_document, 1


def _ensure_refund_period_config_cell(document: dict[str, Any]) -> tuple[dict[str, Any], int]:
    columns = _normalize_document_columns(document)
    refunded_index = _find_column_index(columns, "已返款")
    if refunded_index is None:
        return document, 0

    config_row_index = max(_normalize_document_data_start_row(document) - 1, 0)
    current_value = _grid_cell_value(document, config_row_index, refunded_index)
    current_text = _normalize_text(current_value)
    period_formula = '="第"&返款周期&"天"'
    if current_value == period_formula:
        return document, 0
    if current_text and not _is_numeric_literal(current_value) and "返款周期" not in current_text:
        return document, 0

    grid_rows = copy.deepcopy(document.get("grid_rows") or [])
    if not isinstance(grid_rows, list):
        return document, 0
    while len(grid_rows) <= config_row_index:
        grid_rows.append([""] * len(columns))
    config_row = _normalize_row(grid_rows[config_row_index], len(columns))
    config_row[refunded_index] = period_formula
    grid_rows[config_row_index] = config_row
    next_document = dict(document)
    next_document["grid_rows"] = grid_rows
    _set_entity_cell_value(
        next_document,
        document_row=config_row_index,
        column_index=refunded_index,
        value=period_formula,
    )
    return next_document, 1


def _set_entity_cell_value(
    document: dict[str, Any],
    *,
    document_row: int,
    column_index: int,
    value: Any,
) -> bool:
    entity_rows = document.get("entity_rows")
    entity_columns = document.get("entity_columns")
    entity_cells = document.get("entity_cells")
    if not isinstance(entity_rows, list) or not isinstance(entity_columns, list) or not isinstance(entity_cells, dict):
        return False
    if document_row < 0 or document_row >= len(entity_rows) or column_index < 0 or column_index >= len(entity_columns):
        return False

    row_record = entity_rows[document_row]
    column_record = entity_columns[column_index]
    row_id = row_record.get("id") if isinstance(row_record, dict) else None
    column_id = column_record.get("id") if isinstance(column_record, dict) else None
    if not row_id or not column_id:
        return False

    row_cells = entity_cells.get(row_id)
    if not isinstance(row_cells, dict):
        return False
    cell = row_cells.get(column_id)
    if not isinstance(cell, dict) or "value" not in cell:
        return False
    if cell.get("value") == value:
        return False
    next_cell = dict(cell)
    next_cell["value"] = value
    row_cells[column_id] = next_cell
    return True


def _is_numeric_literal(value: Any) -> bool:
    if isinstance(value, int | float):
        return True
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", _normalize_text(value).replace(",", "")))


def _cell_value_equivalent(current_value: Any, next_value: Any) -> bool:
    if current_value == next_value:
        return True
    if not _is_numeric_literal(current_value) or not _is_numeric_literal(next_value):
        return False
    return abs(_to_float(current_value) - _to_float(next_value)) < 1e-9


def _set_row_value(
    document: dict[str, Any],
    row: list[Any],
    *,
    document_row: int,
    column_index: int | None,
    value: Any,
) -> bool:
    if column_index is None or column_index < 0 or column_index >= len(row):
        return False
    if _cell_value_equivalent(row[column_index], value):
        return False
    row[column_index] = value
    _set_entity_cell_value(
        document,
        document_row=document_row,
        column_index=column_index,
        value=value,
    )
    return True


def _valid_refund_order_number(value: Any) -> bool:
    return len(_normalize_text(value)) in {19, 24}


def _formula_total_refund_amount(
    *,
    video_refund: float,
    clockin_refund: float,
    order_amount: float,
    baseline_amount: float,
) -> float:
    deduction = baseline_amount if baseline_amount > 0 else order_amount
    return min(video_refund + clockin_refund + order_amount - deduction, order_amount)


def repair_nianzhu_clockin_refunds_from_course_sheets(
    session: Session,
    *,
    attendance_sheet_id: int = NIANZHU_ATTENDANCE_SHEET_NUMERIC_ID,
    active_only: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    attendance = _get_sheet(session, attendance_sheet_id)
    bundle = _load_course_sheet_bundle(session, attendance=attendance)
    original_document = copy.deepcopy(dict(attendance.document_json or {}))
    effective_course_name = _resolve_course_name_from_documents(
        dict(bundle[VIDEO_CONFIG_SHEET_KEY].document_json or {}),
        dict(bundle[VIDEO_DATA_SHEET_KEY].document_json or {}),
        dict(bundle[CLOCKIN_CONFIG_SHEET_KEY].document_json or {}),
        dict(bundle[CLOCKIN_DATA_SHEET_KEY].document_json or {}),
    )
    current_document, schema_summary = _ensure_nianzhu_attendance_schema(
        original_document,
        course_name=effective_course_name,
    )
    columns = _normalize_document_columns(current_document)
    rows = [_normalize_row(row, len(columns)) for row in _extract_document_rows(current_document)]
    data_start_row = _normalize_document_data_start_row(current_document)

    clockin_config_document = dict(bundle[CLOCKIN_CONFIG_SHEET_KEY].document_json or {})
    clockin_rules = _load_clockin_rules(clockin_config_document)
    clockin_output_fields = _clockin_output_fields(clockin_config_document, columns)
    clockin_data_document = dict(bundle[CLOCKIN_DATA_SHEET_KEY].document_json or {})
    clockin_title_allowlist = _clockin_title_allowlist_for_course(
        _resolve_course_name_from_documents(
            dict(bundle[VIDEO_CONFIG_SHEET_KEY].document_json or {}),
            clockin_config_document,
            clockin_data_document,
        )
    )
    clockin_key_groups, clockin_numeric_counts = _collect_clockin_data(
        clockin_data_document,
        allowed_titles=clockin_title_allowlist,
    )
    registration_identity_map = build_registration_identity_map(
        session,
        attendance=attendance,
        default_owner_key=NIANZHU_OWNER_KEY,
    )

    indexes = {
        "视频应返款": _find_column_index(columns, "视频应返款"),
        "打卡应返款": _find_column_index(columns, "打卡应返款"),
        "总应返款": _find_column_index(columns, "总应返款"),
        "已返款": _find_column_index(columns, "已返款"),
        "订单金额": _find_column_index(columns, "订单金额"),
        "当前应返款": _find_column_index(columns, "当前应返款"),
        "学号": _find_column_index(columns, "学号"),
        "姓名": _find_column_index(columns, "姓名"),
    }
    rule_version_index = _find_column_index(columns, RULE_VERSION_COLUMN)
    baseline_amount = _to_float(
        _grid_cell_value(
            current_document,
            max(data_start_row - 1, 0),
            indexes["已返款"] if indexes["已返款"] is not None else -1,
        )
    )

    cell_meta = copy.deepcopy(current_document.get("cell_meta") or {})
    next_rows: list[list[Any]] = []
    changed_rows: list[dict[str, Any]] = []
    updated_rows = 0
    updated_cells = 0
    styled_cells = 0
    skipped_rows = 0
    missing_identity_rows = 0
    missing_source_rows = 0

    for row_index, row in enumerate(rows):
        next_row = list(row)
        document_row = data_start_row + row_index
        if not _is_active_tracking_row(next_row, columns, active_only=active_only):
            skipped_rows += 1
            next_rows.append(next_row)
            continue

        identity_keys = _row_identity_keys(
            next_row,
            columns,
            registration_identity_map=registration_identity_map,
        )
        if not identity_keys:
            missing_identity_rows += 1
            next_rows.append(next_row)
            continue

        rule_version = CURRENT_RULE
        if rule_version_index is not None:
            rule_version = _normalize_text(next_row[rule_version_index]) or CURRENT_RULE

        row_changed = False
        row_change: dict[str, Any] | None = None
        last_clockin_refund = 0.0
        has_positive_clockin_source = False

        for field_name, output_name, column_index in clockin_output_fields:
            clockin_count = _clockin_count_for_identity_keys(
                clockin_key_groups,
                clockin_numeric_counts,
                identity_keys,
                field_name,
            )
            if clockin_count <= 0:
                continue

            has_positive_clockin_source = True
            old_clockin_value = next_row[column_index]
            clockin_value = _format_numeric_cell(clockin_count)
            if _set_row_value(
                current_document,
                next_row,
                document_row=document_row,
                column_index=column_index,
                value=clockin_value,
            ):
                updated_cells += 1
                row_changed = True

            clockin_refund_rules = (
                _clockin_refund_rules_for_formula(
                    current_document,
                    refund_column_index=indexes["打卡应返款"],
                    rule_version=rule_version,
                    clockin_rules=clockin_rules,
                )
                if output_name == "打卡数"
                else _rules_for_version(clockin_rules.get(field_name, {}), rule_version)
            )
            clockin_refund, clockin_color = highlight_threshold_refund_progress(
                clockin_refund_rules,
                clockin_count,
            )
            if output_name == "打卡数":
                last_clockin_refund += clockin_refund
                if indexes["打卡应返款"] is not None and not _is_formula_expression(next_row[indexes["打卡应返款"]]):
                    old_clockin_refund = next_row[indexes["打卡应返款"]]
                    if _set_row_value(
                        current_document,
                        next_row,
                        document_row=document_row,
                        column_index=indexes["打卡应返款"],
                        value=_format_numeric_cell(clockin_refund),
                    ):
                        updated_cells += 1
                        row_changed = True
                else:
                    old_clockin_refund = ""
                if _set_attendance_cell_background(
                    current_document,
                    cell_meta,
                    document_row=document_row,
                    column_index=column_index,
                    color=clockin_color,
                ):
                    styled_cells += 1

                next_clockin_refund = next_row[indexes["打卡应返款"]] if indexes["打卡应返款"] is not None else ""
                if (
                    not _cell_value_equivalent(old_clockin_value, clockin_value)
                    or not _cell_value_equivalent(old_clockin_refund, next_clockin_refund)
                ):
                    row_change = row_change or {
                        "row": row_index + 1,
                        "student_id": next_row[indexes["学号"]] if indexes["学号"] is not None else "",
                        "name": next_row[indexes["姓名"]] if indexes["姓名"] is not None else "",
                    }
                    row_change["clockin_count_before"] = old_clockin_value
                    row_change["clockin_count_after"] = clockin_value
                    row_change["clockin_refund_before"] = old_clockin_refund
                    row_change["clockin_refund_after"] = next_clockin_refund

        if not has_positive_clockin_source:
            missing_source_rows += 1
            next_rows.append(next_row)
            continue

        total_index = indexes["总应返款"]
        current_index = indexes["当前应返款"]
        order_amount_index = indexes["订单金额"]
        computed_total_refund: float | None = None
        if order_amount_index is not None:
            computed_total_refund = _formula_total_refund_amount(
                video_refund=_to_float(next_row[indexes["视频应返款"]]) if indexes["视频应返款"] is not None else 0.0,
                clockin_refund=last_clockin_refund,
                order_amount=_to_float(next_row[order_amount_index]),
                baseline_amount=baseline_amount,
            )
        if (
            total_index is not None
            and computed_total_refund is not None
            and not _is_formula_expression(next_row[total_index])
        ):
            old_total_refund = next_row[total_index]
            if _set_row_value(
                current_document,
                next_row,
                document_row=document_row,
                column_index=total_index,
                value=_format_numeric_cell(computed_total_refund),
            ):
                updated_cells += 1
                row_changed = True
                row_change = row_change or {
                    "row": row_index + 1,
                    "student_id": next_row[indexes["学号"]] if indexes["学号"] is not None else "",
                    "name": next_row[indexes["姓名"]] if indexes["姓名"] is not None else "",
                }
                row_change["total_refund_before"] = old_total_refund
                row_change["total_refund_after"] = next_row[total_index]

        if (
            current_index is not None
            and computed_total_refund is not None
            and not _is_formula_expression(next_row[current_index])
        ):
            old_current_refund = next_row[current_index]
            refunded_amount = _to_float(next_row[indexes["已返款"]]) if indexes["已返款"] is not None else 0.0
            order_amount = _to_float(next_row[order_amount_index]) if order_amount_index is not None else 0.0
            current_refund = max(0.0, computed_total_refund - refunded_amount) if order_amount > 0 else 0.0
            if _set_row_value(
                current_document,
                next_row,
                document_row=document_row,
                column_index=current_index,
                value=_format_numeric_cell(current_refund),
            ):
                updated_cells += 1
                row_changed = True
                row_change = row_change or {
                    "row": row_index + 1,
                    "student_id": next_row[indexes["学号"]] if indexes["学号"] is not None else "",
                    "name": next_row[indexes["姓名"]] if indexes["姓名"] is not None else "",
                }
                row_change["current_refund_before"] = old_current_refund
                row_change["current_refund_after"] = next_row[current_index]

        if row_changed:
            updated_rows += 1
            if row_change is not None:
                changed_rows.append(row_change)
        next_rows.append(next_row)

    next_document = dict(current_document)
    next_document["cell_meta"] = cell_meta
    next_document = _replace_document_data_rows(next_document, next_rows)
    changed = next_document != original_document
    if changed and not dry_run:
        attendance.document_json = next_document
        attendance.version = max(int(attendance.version or 1), 1) + 1
        attendance.updated_at = time.time()
        session.add(attendance)

    return {
        "attendance_sheet_id": int(attendance.numeric_id or 0),
        "active_only": active_only,
        "dry_run": dry_run,
        "changed": changed,
        "rows": len(rows),
        "updated_rows": updated_rows,
        "updated_cells": updated_cells,
        "styled_cells": styled_cells,
        "skipped_rows": skipped_rows,
        "missing_identity_rows": missing_identity_rows,
        "missing_source_rows": missing_source_rows,
        "changed_rows": changed_rows,
        **schema_summary,
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
