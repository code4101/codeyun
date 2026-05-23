from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.fanxiu_resources import FanxiuResourceError


DEFAULT_ACTIVITY_ROWS = Path("parsed_configs/Activity/rows.json")
DEFAULT_ACTIVITY_GIFT_ROWS = Path("parsed_configs/ActivityGift/rows.json")
DEFAULT_ACTIVITY_FREE_GIFT_ROWS = Path("parsed_configs/ActivityFreeGift/rows.json")
DEFAULT_ACTIVITY_SIGNIN_ROWS = Path("parsed_configs/ActivitySignIn/rows.json")
DEFAULT_ACTIVITY_LIST_REWARD_ROWS = Path("parsed_configs/ActivityListReward/rows.json")
TIMELINE_SOURCE_ROWS = [
    DEFAULT_ACTIVITY_ROWS,
    DEFAULT_ACTIVITY_GIFT_ROWS,
    DEFAULT_ACTIVITY_FREE_GIFT_ROWS,
    DEFAULT_ACTIVITY_SIGNIN_ROWS,
    DEFAULT_ACTIVITY_LIST_REWARD_ROWS,
]
TIMELINE_HINT_LIMIT = 32

_INT_RE = re.compile(r"-?\d+")
_ABS_TIME_RE = re.compile(r"ABS\|(\d{4})_(\d{1,2})_(\d{1,2})_(\d{1,2})_(\d{1,2})_(\d{1,2})")
_DATE_TOKEN_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)\|(?:\d+_)?(20\d{6})")
_ACTIVITY_PASSED_RE = re.compile(r"ActivityPassed\|(\d+)(?:_[^,;]*)?")
_ITEM_REF_RE = re.compile(r"Item\|(\d+)(?:_(-?\d+(?:\.\d+)?))?")
_RELATIVE_TIME_RE = re.compile(r"^([A-Z][A-Z0-9]*)\|")


def _load_optional_json_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise FanxiuResourceError(f"JSON 文件不是行列表：{path}")
    return [item for item in data if isinstance(item, dict)]


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INT_RE.fullmatch(value.strip()):
        return int(value.strip())
    return None


def _text_value(row: dict[str, Any], field: str) -> str:
    value = row.get(f"{field}_plain")
    if value is None or value == "":
        value = row.get(field)
    return "" if value is None else str(value)


def _iter_leaf_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(_iter_leaf_values(item))
        return result
    if isinstance(value, (list, tuple, set)):
        result = []
        for item in value:
            result.extend(_iter_leaf_values(item))
        return result
    return [str(value)]


def _extract_item_ids(value: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for text in _iter_leaf_values(value):
        for match in _ITEM_REF_RE.finditer(text):
            item_id = match.group(1)
            if item_id not in seen:
                seen.add(item_id)
                result.append(item_id)
    return result


def extract_activity_passed_ids(value: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for text in _iter_leaf_values(value):
        for match in _ACTIVITY_PASSED_RE.finditer(text):
            activity_id = match.group(1)
            if activity_id not in seen:
                seen.add(activity_id)
                result.append(activity_id)
    return result


def _format_date(year: str, month: str, day: str) -> str:
    parsed = datetime(int(year), int(month), int(day))
    return parsed.strftime("%Y-%m-%d")


def _format_time(hour: str, minute: str, second: str) -> str:
    return f"{int(hour):02d}:{int(minute):02d}:{int(second):02d}"


def _format_date8(value: str) -> str:
    parsed = datetime.strptime(value, "%Y%m%d")
    return parsed.strftime("%Y-%m-%d")


def _extract_abs_time(value: Any) -> dict[str, str] | None:
    for text in _iter_leaf_values(value):
        match = _ABS_TIME_RE.search(text)
        if not match:
            continue
        try:
            return {
                "date": _format_date(match.group(1), match.group(2), match.group(3)),
                "time": _format_time(match.group(4), match.group(5), match.group(6)),
                "evidence": match.group(0),
            }
        except ValueError:
            continue
    return None


def _condition_date_candidates(value: Any) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for text in _iter_leaf_values(value):
        for match in _DATE_TOKEN_RE.finditer(text):
            token = match.group(1)
            try:
                date = _format_date8(match.group(2))
            except ValueError:
                continue
            if "Before" in token:
                continue
            priority = 0
            if "After" in token:
                priority = 1
            elif "SameDay" in token or "SpecifiedTime" in token:
                priority = 2
            else:
                priority = 3
            candidates.append(
                {
                    "date": date,
                    "token": token,
                    "evidence": match.group(0),
                    "priority": str(priority),
                }
            )
    return sorted(candidates, key=lambda item: (int(item["priority"]), item["date"], item["token"]))


def _relative_time_code(value: Any) -> str:
    for text in _iter_leaf_values(value):
        match = _RELATIVE_TIME_RE.match(text.strip())
        if match and match.group(1) != "ABS":
            return match.group(1)
    return ""


def _activity_name(row: dict[str, Any]) -> str:
    return _text_value(row, "name") or _text_value(row, "littleName") or str(row.get("id") or "")


def _base_activity_hint(
    activity: dict[str, Any] | None,
    *,
    relation: str,
    source: str,
    condition: Any = None,
    prefer_condition_date: bool = False,
) -> dict[str, Any] | None:
    condition_dates = _condition_date_candidates(condition)
    if prefer_condition_date and condition_dates:
        picked = condition_dates[0]
        hint = {
            "date": picked["date"],
            "time": "",
            "kind": "condition_date",
            "confidence": "medium",
            "label": "活动条件",
            "source": source,
            "relation": relation,
            "evidence": picked["evidence"],
        }
    else:
        exact = _extract_abs_time((activity or {}).get("startTime"))
        if exact:
            hint = {
                "date": exact["date"],
                "time": exact["time"],
                "kind": "activity_start",
                "confidence": "high",
                "label": "活动开始",
                "source": source,
                "relation": relation,
                "evidence": exact["evidence"],
            }
        elif condition_dates:
            picked = condition_dates[0]
            hint = {
                "date": picked["date"],
                "time": "",
                "kind": "condition_date",
                "confidence": "medium",
                "label": "活动条件",
                "source": source,
                "relation": relation,
                "evidence": picked["evidence"],
            }
        else:
            fallback_dates = _condition_date_candidates(
                [
                    (activity or {}).get("openCondition"),
                    (activity or {}).get("showCondition"),
                    (activity or {}).get("forceShowCondition"),
                    (activity or {}).get("forceHideCondition"),
                ]
            )
            if fallback_dates:
                picked = fallback_dates[0]
                hint = {
                    "date": picked["date"],
                    "time": "",
                    "kind": "condition_date",
                    "confidence": "medium",
                    "label": "活动条件",
                    "source": source,
                    "relation": relation,
                    "evidence": picked["evidence"],
                }
            else:
                code = _relative_time_code((activity or {}).get("startTime") or (activity or {}).get("prepareTime"))
                if not code:
                    return None
                hint = {
                    "date": "",
                    "time": "",
                    "kind": "relative_schedule",
                    "confidence": "low",
                    "label": "相对时程",
                    "source": source,
                    "relation": relation,
                    "evidence": str((activity or {}).get("startTime") or (activity or {}).get("prepareTime") or ""),
                    "time_code": code,
                }

    if activity:
        hint.update(
            {
                "activity_id": str(activity.get("id") or ""),
                "activity_name": _activity_name(activity),
                "activity_little_name": _text_value(activity, "littleName"),
                "activity_base_id": str(activity.get("baseId") or ""),
            }
        )
    return hint


def _activity_indexes(activity_rows: list[dict[str, Any]]) -> dict[str, dict[int, list[dict[str, Any]]] | dict[int, dict[str, Any]]]:
    by_id: dict[int, dict[str, Any]] = {}
    by_base_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_reward_group: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in activity_rows:
        activity_id = _as_int(row.get("id"))
        if activity_id is not None:
            by_id[activity_id] = row
        base_id = _as_int(row.get("baseId"))
        if base_id is not None:
            by_base_id[base_id].append(row)
        reward_group = _as_int(row.get("rewardGroup"))
        if reward_group is not None:
            by_reward_group[reward_group].append(row)
    return {
        "by_id": by_id,
        "by_base_id": dict(by_base_id),
        "by_reward_group": dict(by_reward_group),
    }


def _activity_for_reward_row(
    row: dict[str, Any],
    indexes: dict[str, dict[int, list[dict[str, Any]]] | dict[int, dict[str, Any]]],
    activity_key: str,
) -> dict[str, Any] | None:
    value = _as_int(row.get(activity_key))
    if value is None:
        return None
    by_id = indexes["by_id"]
    by_base_id = indexes["by_base_id"]
    by_reward_group = indexes["by_reward_group"]
    if activity_key == "activityId" and isinstance(by_id, dict):
        return by_id.get(value)  # type: ignore[return-value]
    if activity_key == "baseId" and isinstance(by_base_id, dict):
        return (by_base_id.get(value) or [None])[0]  # type: ignore[return-value]
    if activity_key == "group" and isinstance(by_reward_group, dict):
        return (by_reward_group.get(value) or [None])[0]  # type: ignore[return-value]
    return None


def _append_item_hint(
    hints_by_id: dict[str, list[dict[str, Any]]],
    *,
    item_id: str,
    hint: dict[str, Any] | None,
    reward_row: dict[str, Any] | None = None,
) -> None:
    if not hint:
        return
    if reward_row:
        hint = {
            **hint,
            "reward_row_id": str(reward_row.get("id") or reward_row.get("_row_key") or ""),
        }
    hints_by_id[item_id].append(hint)


def _collect_reward_table_item_hints(
    root: Path,
    indexes: dict[str, dict[int, list[dict[str, Any]]] | dict[int, dict[str, Any]]],
    hints_by_id: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    sources = [
        (
            "ActivityGift",
            DEFAULT_ACTIVITY_GIFT_ROWS,
            ("activityId",),
            ("reward",),
            "活动礼包奖励",
        ),
        (
            "ActivityFreeGift",
            DEFAULT_ACTIVITY_FREE_GIFT_ROWS,
            ("activityId", "baseId"),
            ("reward",),
            "活动免费礼包",
        ),
        (
            "ActivitySignIn",
            DEFAULT_ACTIVITY_SIGNIN_ROWS,
            ("activityId",),
            ("reward",),
            "活动签到奖励",
        ),
        (
            "ActivityListReward",
            DEFAULT_ACTIVITY_LIST_REWARD_ROWS,
            ("group",),
            ("reward", "allianceReward", "outstandingReward", "titleItem"),
            "活动榜单奖励",
        ),
    ]
    counts: dict[str, int] = {}
    for table_name, relative_path, activity_keys, reward_fields, relation_label in sources:
        rows = _load_optional_json_rows(root / relative_path)
        counts[table_name] = len(rows)
        for row in rows:
            activity = None
            for activity_key in activity_keys:
                activity = _activity_for_reward_row(row, indexes, activity_key)
                if activity:
                    break
            condition = row.get("showCondition") or row.get("condition") or row.get("showLimitCondition")
            for field in reward_fields:
                for item_id in _extract_item_ids(row.get(field)):
                    hint = _base_activity_hint(
                        activity,
                        relation="reward_item",
                        source=f"{table_name}.{field}",
                        condition=condition,
                        prefer_condition_date=True,
                    )
                    if hint:
                        hint["label"] = relation_label
                    _append_item_hint(hints_by_id, item_id=item_id, hint=hint, reward_row=row)
    return counts


def _collect_activity_direct_item_hints(
    activity_rows: list[dict[str, Any]],
    hints_by_id: dict[str, list[dict[str, Any]]],
) -> int:
    count = 0
    for activity in activity_rows:
        for field in ("showReward", "reward", "task"):
            for item_id in _extract_item_ids(activity.get(field)):
                hint = _base_activity_hint(activity, relation="activity_field", source=f"Activity.{field}")
                if hint:
                    hint["label"] = "活动展示"
                    _append_item_hint(hints_by_id, item_id=item_id, hint=hint, reward_row=activity)
                    count += 1
    return count


_CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}
_RELATION_ORDER = {"show_condition": 0, "activity_field": 1, "reward_item": 2, "consume_item": 3}


def _timeline_sort_key(hint: dict[str, Any]) -> tuple[int, str, int, int, int, str]:
    return (
        1 if not hint.get("date") else 0,
        str(hint.get("date") or "9999-99-99"),
        _CONFIDENCE_ORDER.get(str(hint.get("confidence") or ""), 9),
        _RELATION_ORDER.get(str(hint.get("relation") or ""), 9),
        _as_int(hint.get("activity_id")) or 10**12,
        str(hint.get("source") or ""),
    )


def _timeline_display_key(hint: dict[str, Any]) -> tuple[str, str, str, str, str, str, str, str]:
    activity_name = str(hint.get("activity_name") or hint.get("activity_little_name") or "").strip()
    activity_identity = activity_name or str(hint.get("activity_id") or "").strip()
    return (
        str(hint.get("date") or ""),
        str(hint.get("time") or ""),
        str(hint.get("time_code") or ""),
        activity_identity,
        str(hint.get("label") or ""),
        str(hint.get("relation") or ""),
        str(hint.get("via_item_id") or ""),
        str(hint.get("via_item_name") or ""),
    )


def _append_unique_text(row: dict[str, Any], field: str, value: Any) -> None:
    text = str(value or "").strip()
    if not text:
        return
    items = [str(item) for item in row.get(field) or []]
    if text not in items:
        items.append(text)
    row[field] = items


def sort_timeline_hints(hints: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_display_key: dict[tuple[str, str, str, str, str, str, str, str], dict[str, Any]] = {}
    for hint in sorted(hints, key=_timeline_sort_key):
        key = _timeline_display_key(hint)
        row = by_display_key.get(key)
        if row is None:
            row = dict(hint)
            _append_unique_text(row, "activity_ids", hint.get("activity_id"))
            _append_unique_text(row, "sources", hint.get("source"))
            _append_unique_text(row, "evidences", hint.get("evidence"))
            by_display_key[key] = row
            merged.append(row)
            continue
        row["merged_count"] = int(row.get("merged_count") or 1) + 1
        _append_unique_text(row, "activity_ids", hint.get("activity_id"))
        _append_unique_text(row, "sources", hint.get("source"))
        _append_unique_text(row, "evidences", hint.get("evidence"))
    for row in merged:
        if len(row.get("activity_ids") or []) <= 1:
            row.pop("activity_ids", None)
        if len(row.get("sources") or []) <= 1:
            row.pop("sources", None)
        if len(row.get("evidences") or []) <= 1:
            row.pop("evidences", None)
    return merged[:limit] if limit > 0 else merged


def first_timeline_hint(hints: list[dict[str, Any]]) -> dict[str, Any] | None:
    sorted_hints = sort_timeline_hints(hints, limit=1)
    return sorted_hints[0] if sorted_hints else None


def timeline_sort_value(hint: Any) -> int:
    if not isinstance(hint, dict):
        return 0
    date = str(hint.get("date") or "").strip()
    if not date:
        return 0
    time_text = str(hint.get("time") or "00:00:00").strip() or "00:00:00"
    digits = re.sub(r"\D", "", f"{date} {time_text}")
    if len(digits) < 8:
        return 0
    return int((digits + "000000")[:14])


def card_timeline_sort_value(card: dict[str, Any]) -> int:
    hint = card.get("first_time_hint")
    if not hint and isinstance(card.get("time_hints"), list):
        hint = first_timeline_hint(card["time_hints"])
    return timeline_sort_value(hint)


def build_timeline_context(root: Path) -> dict[str, Any]:
    activity_rows = _load_optional_json_rows(root / DEFAULT_ACTIVITY_ROWS)
    indexes = _activity_indexes(activity_rows)
    hints_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reward_table_counts = _collect_reward_table_item_hints(root, indexes, hints_by_id)
    direct_item_hint_count = _collect_activity_direct_item_hints(activity_rows, hints_by_id)
    item_hints_by_id = {
        item_id: sort_timeline_hints(hints, limit=TIMELINE_HINT_LIMIT)
        for item_id, hints in hints_by_id.items()
    }
    return {
        "activity_rows": activity_rows,
        "activity_by_id": indexes["by_id"],
        "item_hints_by_id": item_hints_by_id,
        "stats": {
            "activity_count": len(activity_rows),
            "activity_reward_table_counts": reward_table_counts,
            "activity_direct_item_hint_count": direct_item_hint_count,
            "item_with_time_hint_count": len(item_hints_by_id),
        },
    }


def build_activity_passed_hints(
    show_condition: Any,
    activity_by_id: dict[int, dict[str, Any]],
    *,
    relation: str = "show_condition",
    source: str = "showCondition",
) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for activity_id in extract_activity_passed_ids(show_condition):
        parsed_id = _as_int(activity_id)
        activity = activity_by_id.get(parsed_id or -1)
        hint = _base_activity_hint(activity, relation=relation, source=source)
        if hint:
            hint["label"] = "活动解锁"
            if not hint.get("activity_id"):
                hint["activity_id"] = activity_id
            hints.append(hint)
    return sort_timeline_hints(hints, limit=TIMELINE_HINT_LIMIT)


def clone_hints_via_item(
    hints: list[dict[str, Any]],
    *,
    item_id: Any,
    item_name: Any = "",
    relation: str = "consume_item",
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for hint in hints:
        result.append(
            {
                **hint,
                "relation": relation,
                "via_item_id": str(item_id or ""),
                "via_item_name": str(item_name or ""),
            }
        )
    return result
