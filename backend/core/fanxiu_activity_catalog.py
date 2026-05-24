from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.core.fanxiu_item_catalog import load_fanxiu_item_runtime_index
from backend.core.fanxiu_resources import FanxiuResourceError, resolve_fanxiu_export_root
from backend.core.fanxiu_timeline import (
    _activity_indexes,
    _base_activity_hint,
    _load_optional_json_rows,
    _text_value,
    card_timeline_sort_value,
    sort_timeline_hints,
)


DEFAULT_ACTIVITY_ROWS = Path("parsed_configs/Activity/rows.json")
DEFAULT_ACTIVITY_GIFT_ROWS = Path("parsed_configs/ActivityGift/rows.json")
DEFAULT_ACTIVITY_FREE_GIFT_ROWS = Path("parsed_configs/ActivityFreeGift/rows.json")
DEFAULT_ACTIVITY_SIGNIN_ROWS = Path("parsed_configs/ActivitySignIn/rows.json")
DEFAULT_ACTIVITY_LIST_REWARD_ROWS = Path("parsed_configs/ActivityListReward/rows.json")
DEFAULT_ACTIVITY_FUND_ROWS = Path("parsed_configs/ActivityFundBase/rows.json")
DEFAULT_ACTIVITY_BATTLE_PASS_ROWS = Path("parsed_configs/ActivityBattlePassBase/rows.json")
DEFAULT_ACTIVITY_LOOP_ROWS = Path("parsed_configs/ActivityLoop/rows.json")
DEFAULT_ACTIVITY_BOSS_ROWS = Path("parsed_configs/ActivityBoss/rows.json")
DEFAULT_ACTIVE_TASK_ROWS = Path("parsed_configs/ActiveTask/rows.json")
DEFAULT_OPEN_FUNCTION_ROWS = Path("parsed_configs/OpenFunction/rows.json")
DEFAULT_SUBPACKAGE_REWARD_ROWS = Path("parsed_configs/SubpackageRewards/rows.json")
DEFAULT_ACTIVITY_CATALOG = Path("parsed_configs/activity_catalog/activity_catalog.json")
ACTIVITY_CATALOG_SCHEMA_VERSION = 5

_INT_RE = re.compile(r"-?\d+")
_WHITESPACE_RE = re.compile(r"\s+")
_BRACKET_TERM_RE = re.compile(r"【([^】]{1,30})】")
_ITEM_REF_RE = re.compile(r"Item\|(\d+)(?:_(-?\d+(?:\.\d+)?))?")
_ABS_TIME_RE = re.compile(r"^ABS\|(\d{4})_(\d{1,2})_(\d{1,2})_(\d{1,2})_(\d{1,2})_(\d{1,2})$")
_DATE8_RE = re.compile(r"20\d{6}")
_TIME_TOKEN_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*)\|(.*)$")

ACTIVITY_KIND_LABELS: dict[str, str] = {
    "gift": "礼包",
    "free_gift": "免费礼包",
    "signin": "签到",
    "rank_reward": "榜单奖励",
    "fund": "基金",
    "battle_pass": "战令",
    "direct_reward": "展示奖励",
    "boss": "首领",
    "loop": "轮换",
    "active_task": "活跃任务",
    "subpackage": "资源包奖励",
    "control": "控制活动",
    "activity": "活动",
}

TIME_KIND_LABELS: dict[str, str] = {
    "absolute": "定时",
    "condition": "条件时间",
    "relative": "相对时程",
    "none": "无时间",
}

TIME_FIELD_LABELS: dict[str, str] = {
    "prepare_time": "准备",
    "start_time": "开始",
    "end_time": "结束",
    "reward_time": "领奖",
    "close_panel_time": "关闭面板",
}

TIME_FIELD_SOURCES: dict[str, str] = {
    "prepare_time": "Activity.prepareTime",
    "start_time": "Activity.startTime",
    "end_time": "Activity.endTime",
    "reward_time": "Activity.rewardTime",
    "close_panel_time": "Activity.closePanelTime",
}

CONDITION_FIELD_LABELS: dict[str, str] = {
    "open_condition": "开启条件",
    "join_condition": "参与条件",
    "show_condition": "显示条件",
    "force_hide_condition": "强制隐藏",
}

CONDITION_FIELD_SOURCES: dict[str, str] = {
    "open_condition": "Activity.openCondition",
    "join_condition": "Activity.joinCondition",
    "show_condition": "Activity.showCondition",
    "force_hide_condition": "Activity.forceHideCondition",
}

CONDITION_TOKEN_LABELS: dict[str, str] = {
    "OpenAfter": "开放后",
    "OpenBefore": "开放前",
    "OpenBetween": "开放区间",
    "SpecifiedTimeSameDay": "指定日期",
    "DateBefore": "日期前",
    "ActivityAfter": "活动后",
    "ActivityBefore": "活动前",
    "ActivityPassed": "已完成活动",
    "InCrossGroup": "跨服组",
    "InActivityCrossGroup": "活动跨服组",
    "CrossGroupDay": "跨服组天数",
    "CrossGroupDayAfter": "跨服组天数后",
    "CrossGroupDayAfterDay": "跨服组天数晚于",
    "CrossGroupDayBeforeDay": "跨服组天数早于",
    "ServerTime": "开服天数",
    "BetweenServerTime": "开服天数区间",
    "betweenServer": "开服区间",
    "CT": "条件",
    "CL": "等级/状态",
    "ActivitybaseId": "活动基准",
    "RevenueCloseIcon": "关闭入口",
}


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INT_RE.fullmatch(value.strip()):
        return int(value.strip())
    return None


def _sort_value(value: Any) -> tuple[int, str]:
    number = _as_int(value)
    if number is not None:
        return (0, f"{number:020d}")
    return (1, str(value or ""))


def _clean_text(value: Any) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "").replace("\u3000", " ")).strip()


def _preview(value: Any, limit: int = 160) -> str:
    text = _clean_text(value)
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _activity_card_key(card: dict[str, Any]) -> str:
    return str(card.get("id") or "").strip()


def _mark_current_card(card: dict[str, Any], *, built_at: str) -> dict[str, Any]:
    card["presence_status"] = "current"
    card["is_stale"] = False
    card["last_seen_at"] = built_at
    card["missing_since"] = ""
    return card


def _merge_previous_activity_cards(
    catalog_path: Path,
    current_cards: list[dict[str, Any]],
    *,
    built_at: str,
) -> list[dict[str, Any]]:
    current_ids = {_activity_card_key(card) for card in current_cards if _activity_card_key(card)}
    if not catalog_path.is_file():
        return current_cards
    try:
        previous = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return current_cards
    previous_cards = previous.get("cards") if isinstance(previous, dict) else None
    if not isinstance(previous_cards, list):
        return current_cards
    merged = list(current_cards)
    seen = set(current_ids)
    for item in previous_cards:
        if not isinstance(item, dict):
            continue
        key = _activity_card_key(item)
        if not key or key in seen:
            continue
        stale = dict(item)
        stale["presence_status"] = "missing"
        stale["is_stale"] = True
        stale["missing_since"] = stale.get("missing_since") or built_at
        stale["last_seen_at"] = stale.get("last_seen_at") or previous.get("built_at") or ""
        stale["source_table"] = stale.get("source_table") or "Activity"
        merged.append(stale)
        seen.add(key)
    return merged


def _format_date_parts(year: str | int, month: str | int, day: str | int) -> str:
    parsed = datetime(int(year), int(month), int(day))
    return parsed.strftime("%Y-%m-%d")


def _format_time_parts(hour: str | int, minute: str | int, second: str | int) -> str:
    return f"{int(hour):02d}:{int(minute):02d}:{int(second):02d}"


def _format_date8(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _compact_raw_parts(values: list[int], *, used_tail: int) -> str:
    prefix = values[: max(0, len(values) - used_tail)]
    return " / ".join(str(item) for item in prefix)


def _parse_time_expr(label: str, field: str, value: Any) -> dict[str, Any] | None:
    raw = _clean_text(value)
    if not raw:
        return None
    match = _ABS_TIME_RE.match(raw)
    if match:
        try:
            date = _format_date_parts(match.group(1), match.group(2), match.group(3))
            time_text = _format_time_parts(match.group(4), match.group(5), match.group(6))
        except ValueError:
            return {
                "field": field,
                "label": label,
                "raw": raw,
                "summary": raw,
                "items": [{"kind": "raw", "token": "ABS", "raw": raw, "text": raw}],
            }
        return {
            "field": field,
            "label": label,
            "raw": raw,
            "summary": f"{date} {time_text}",
            "items": [
                {
                    "kind": "absolute",
                    "token": "ABS",
                    "raw": raw,
                    "date": date,
                    "time": time_text,
                    "text": f"{date} {time_text}",
                }
            ],
        }
    token_match = _TIME_TOKEN_RE.match(raw)
    if not token_match:
        return {"field": field, "label": label, "raw": raw, "summary": raw, "items": [{"kind": "raw", "raw": raw, "text": raw}]}
    token = token_match.group(1)
    payload = token_match.group(2)
    date = None
    date_match = _DATE8_RE.search(payload)
    if date_match:
        date = _format_date8(date_match.group(0))
    numbers = [int(item) for item in _INT_RE.findall(payload)]
    item: dict[str, Any] = {"kind": "relative", "token": token, "raw": raw}
    if date:
        item["date"] = date
    if len(numbers) >= 4:
        day, hour, minute, second = numbers[-4], numbers[-3], numbers[-2], numbers[-1]
        item.update({"day": day, "time": _format_time_parts(hour, minute, second), "time_code": token})
        prefix = _compact_raw_parts(numbers, used_tail=4)
        prefix_text = f"参数 {prefix} / " if prefix else ""
        text = f"{prefix_text}第 {day} 天 {item['time']}"
        if date:
            text = f"{date} · {text}"
        item["text"] = f"{token} · {text}"
    elif len(numbers) >= 3:
        hour, minute, second = numbers[-3], numbers[-2], numbers[-1]
        item.update({"time": _format_time_parts(hour, minute, second), "time_code": token})
        prefix = _compact_raw_parts(numbers, used_tail=3)
        prefix_text = f"参数 {prefix} / " if prefix else ""
        text = f"{prefix_text}{item['time']}"
        if date:
            text = f"{date} · {text}"
        item["text"] = f"{token} · {text}"
    else:
        item["time_code"] = token
        item["text"] = raw
    return {"field": field, "label": label, "raw": raw, "summary": item["text"], "items": [item]}


def _split_condition_groups(value: Any) -> list[list[str]]:
    raw = _clean_text(value)
    if not raw:
        return []
    groups: list[list[str]] = []
    for group_text in raw.split(";"):
        tokens = [token.strip() for token in group_text.split(",") if token.strip()]
        if tokens:
            groups.append(tokens)
    return groups


def _parse_condition_token(token_text: str) -> dict[str, Any]:
    token, _, payload = token_text.partition("|")
    label = CONDITION_TOKEN_LABELS.get(token, token)
    dates = [date for date in (_format_date8(item) for item in _DATE8_RE.findall(payload)) if date]
    item: dict[str, Any] = {
        "token": token,
        "label": label,
        "value": payload,
        "raw": token_text,
    }
    if dates:
        item["dates"] = dates
        item["date"] = dates[0]
    if token == "OpenBetween" and len(dates) >= 2:
        item["text"] = f"{label} {dates[0]} 至 {dates[1]}"
    elif dates:
        item["text"] = f"{label} {dates[0]}"
    elif token in {"InCrossGroup", "InActivityCrossGroup", "CrossGroup"} and payload:
        item["text"] = f"{label} {payload}"
    elif token in {"ServerTime", "BetweenServerTime", "betweenServer"} and payload:
        item["text"] = f"{label} {payload}"
    elif payload:
        item["text"] = f"{label} {payload}"
    else:
        item["text"] = label
    return item


def _condition_code_summary(parsed_groups: list[dict[str, Any]]) -> str:
    condition_ids: list[str] = []
    for group in parsed_groups:
        items = group.get("items") or []
        if len(items) != 1:
            return ""
        item = items[0]
        if item.get("token") != "CT" or not item.get("value"):
            return ""
        condition_ids.append(str(item["value"]))
    if not condition_ids:
        return ""
    suffix = " 任一已完成" if len(condition_ids) > 1 else " 已完成"
    return f"任务ID {'/'.join(condition_ids)}{suffix}"


def _parse_condition_field(label: str, field: str, value: Any, *, description: Any = None) -> dict[str, Any] | None:
    raw = _clean_text(value)
    if not raw:
        return None
    description_text = _clean_text(description)
    parsed_groups: list[dict[str, Any]] = []
    for tokens in _split_condition_groups(raw):
        items = [_parse_condition_token(token) for token in tokens]
        parsed_groups.append({"join": "AND", "items": items, "summary": "，".join(str(item.get("text") or "") for item in items)})
    if not parsed_groups:
        return None
    raw_summary = "；或 ".join(group["summary"] for group in parsed_groups if group.get("summary"))
    code_summary = _condition_code_summary(parsed_groups)
    summary = raw_summary
    if description_text:
        summary = f"{description_text}（{code_summary}）" if code_summary else description_text
    return {
        "field": field,
        "label": label,
        "raw": raw,
        "summary": summary or raw,
        "raw_summary": raw_summary or raw,
        "description": description_text,
        "code_summary": code_summary,
        "groups": parsed_groups,
    }


def _load_json_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FanxiuResourceError(f"活动配置文件不存在：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise FanxiuResourceError(f"JSON 文件不是行列表：{path}")
    return [item for item in data if isinstance(item, dict)]


def _resolve_catalog_file(
    export_root: str | Path | None,
    *,
    rebuild_missing: bool = True,
) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    path = root / DEFAULT_ACTIVITY_CATALOG
    if not path.is_file():
        if not rebuild_missing:
            raise FanxiuResourceError(f"活动图鉴索引不存在：{path}")
        build_fanxiu_activity_catalog(export_root=root)
    return path


def _load_activity_catalog_cached(path_text: str, mtime_ns: int, size: int, export_root_text: str) -> dict[str, Any]:
    del mtime_ns, size, export_root_text
    data = json.loads(Path(path_text).read_text(encoding="utf-8"))
    if int(data.get("schema_version") or 0) != ACTIVITY_CATALOG_SCHEMA_VERSION:
        root = resolve_fanxiu_export_root(export_root_text)
        data = build_fanxiu_activity_catalog(export_root=root)
    data["catalog_path"] = path_text
    return data


_load_activity_catalog_cached = lru_cache(maxsize=4)(_load_activity_catalog_cached)


def _iter_leaf_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(_iter_leaf_values(item))
        return result
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            result.extend(_iter_leaf_values(item))
        return result
    return [str(value)]


def _format_count(value: str | None) -> str:
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer():
        return str(int(number))
    return str(number)


def _linked_item(item_id: str, count: str, item_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    card = item_by_id.get(str(item_id)) or {}
    name = _clean_text(card.get("name")) or str(item_id)
    return {
        "id": str(item_id),
        "name": name,
        "icon": card.get("icon"),
        "small_icon": card.get("small_icon"),
        "quality": card.get("quality"),
        "quality_name": card.get("quality_name"),
        "count": _format_count(count),
        "description": _preview(card.get("description") or card.get("effect_description"), 180),
    }


def _extract_reward_items(value: Any, item_by_id: dict[str, dict[str, Any]], *, limit: int = 80) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for text in _iter_leaf_values(value):
        for match in _ITEM_REF_RE.finditer(text):
            item_id = match.group(1)
            count = match.group(2) or ""
            key = (item_id, count)
            if key in seen:
                continue
            seen.add(key)
            result.append(_linked_item(item_id, count, item_by_id))
            if len(result) >= limit:
                return result
    return result


def _raw_value_text(value: Any, *, limit: int = 8) -> list[str]:
    values = [_clean_text(item) for item in _iter_leaf_values(value)]
    return [item for item in values if item][:limit]


def _activity_name(row: dict[str, Any]) -> str:
    return _text_value(row, "name") or _text_value(row, "littleName") or str(row.get("id") or "")


def _activity_little_name(row: dict[str, Any]) -> str:
    return _text_value(row, "littleName")


def _activity_title_name(row: dict[str, Any]) -> str:
    return _text_value(row, "tittleName")


def _activity_time_fields(row: dict[str, Any]) -> list[dict[str, Any]]:
    values = {
        "prepare_time": row.get("prepareTime"),
        "start_time": row.get("startTime"),
        "end_time": row.get("endTime"),
        "reward_time": row.get("rewardTime"),
        "close_panel_time": row.get("closePanelTime"),
    }
    result: list[dict[str, Any]] = []
    for field, value in values.items():
        parsed = _parse_time_expr(TIME_FIELD_LABELS[field], field, value)
        if parsed:
            result.append(parsed)
    return result


def _activity_condition_fields(row: dict[str, Any]) -> list[dict[str, Any]]:
    values = {
        "open_condition": row.get("openCondition"),
        "join_condition": row.get("joinCondition"),
        "show_condition": row.get("showCondition"),
        "force_hide_condition": row.get("forceHideCondition"),
    }
    join_description = _text_value(row, "joinConditionDescribe")
    descriptions = {
        "join_condition": join_description,
        "show_condition": join_description if row.get("showCondition") == row.get("joinCondition") else "",
    }
    result: list[dict[str, Any]] = []
    for field, value in values.items():
        parsed = _parse_condition_field(CONDITION_FIELD_LABELS[field], field, value, description=descriptions.get(field))
        if parsed:
            result.append(parsed)
    return result


def _activity_hint_base(row: dict[str, Any], *, label: str, source: str, evidence: str) -> dict[str, Any]:
    return {
        "label": label,
        "source": source,
        "relation": "activity",
        "evidence": evidence,
        "activity_id": str(row.get("id") or ""),
        "activity_name": _activity_name(row),
        "activity_little_name": _activity_little_name(row),
        "activity_base_id": str(row.get("baseId") or ""),
    }


def _activity_time_hints(row: dict[str, Any]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for field in _activity_time_fields(row):
        source = TIME_FIELD_SOURCES.get(str(field.get("field") or ""), "Activity")
        for item in field.get("items") or []:
            hint = _activity_hint_base(row, label=str(field.get("label") or ""), source=source, evidence=str(item.get("raw") or field.get("raw") or ""))
            if item.get("date"):
                hint.update(
                    {
                        "date": item.get("date"),
                        "time": item.get("time") or "",
                        "kind": "activity_start" if field.get("field") == "start_time" else "activity_time",
                        "confidence": "high" if item.get("kind") == "absolute" else "medium",
                    }
                )
            elif item.get("time_code"):
                hint.update(
                    {
                        "date": "",
                        "time": "",
                        "time_code": item.get("time_code"),
                        "kind": "relative_schedule",
                        "confidence": "low",
                    }
                )
            else:
                continue
            hints.append(hint)
    for field in _activity_condition_fields(row):
        source = CONDITION_FIELD_SOURCES.get(str(field.get("field") or ""), "Activity")
        for group in field.get("groups") or []:
            for item in group.get("items") or []:
                for date in item.get("dates") or []:
                    hint = _activity_hint_base(
                        row,
                        label=f"{field.get('label')} · {item.get('label')}",
                        source=source,
                        evidence=str(item.get("raw") or field.get("raw") or ""),
                    )
                    hint.update(
                        {
                            "date": date,
                            "time": "",
                            "kind": "condition_date",
                            "confidence": "medium",
                        }
                    )
                    hints.append(hint)
    if not hints:
        hint = _base_activity_hint(row, relation="activity", source="Activity.startTime")
        if not hint:
            return []
        hint["label"] = "活动开始"
        hints.append(hint)
    return sort_timeline_hints(hints, limit=24)


def _activity_primary_time_hint(hints: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not hints:
        return None
    for hint in hints:
        if hint.get("source") == "Activity.startTime" and hint.get("date"):
            return hint
    for hint in hints:
        if hint.get("source") == "Activity.startTime":
            return hint
    for hint in hints:
        if hint.get("date"):
            return hint
    return hints[0]


def _time_kind_from_hint(hint: dict[str, Any] | None) -> str:
    if not hint:
        return "none"
    kind = str(hint.get("kind") or "")
    if kind in {"activity_start", "activity_time"}:
        return "absolute"
    if kind == "condition_date":
        return "condition"
    if kind == "relative_schedule":
        return "relative"
    return "none"


def _compact_related_row(
    row: dict[str, Any],
    *,
    source: str,
    title_fields: tuple[str, ...],
    reward_fields: tuple[str, ...],
    item_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    title = ""
    for field in title_fields:
        title = _text_value(row, field)
        if title:
            break
    reward_values: list[Any] = [row.get(field) for field in reward_fields]
    reward_items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for value in reward_values:
        for item in _extract_reward_items(value, item_by_id):
            key = (str(item.get("id") or ""), str(item.get("count") or ""))
            if key in seen:
                continue
            seen.add(key)
            reward_items.append(item)
    meta_values = [
        f"ID {row.get('id') or row.get('fundId') or row.get('_row_key')}",
        f"次数 {row.get('times')}" if row.get("times") not in (None, "") else "",
        f"天 {row.get('day')}" if row.get("day") not in (None, "") else "",
        f"轮 {row.get('turn')}" if row.get("turn") not in (None, "") else "",
        f"类型 {row.get('giftType')}" if row.get("giftType") not in (None, "") else "",
        f"付费 {row.get('payId')}" if row.get("payId") not in (None, "") else "",
    ]
    return {
        "source": source,
        "row_key": row.get("_row_key") or row.get("id") or row.get("fundId"),
        "title": title,
        "meta": " / ".join(item for item in meta_values if item),
        "costs": _raw_value_text(row.get("costs") or row.get("discountItem") or row.get("payId"), limit=4),
        "reward_items": reward_items,
        "raw_rewards": _raw_value_text(reward_values, limit=12),
        "condition": _clean_text(row.get("showCondition") or row.get("condition") or row.get("showLimitCondition")),
    }


def _section(
    key: str,
    title: str,
    rows: list[dict[str, Any]],
    *,
    source: str,
    title_fields: tuple[str, ...],
    reward_fields: tuple[str, ...],
    item_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    compact_rows = [
        _compact_related_row(
            row,
            source=source,
            title_fields=title_fields,
            reward_fields=reward_fields,
            item_by_id=item_by_id,
        )
        for row in rows
    ]
    compact_rows = [row for row in compact_rows if row.get("title") or row.get("reward_items") or row.get("raw_rewards")]
    if not compact_rows:
        return None
    return {
        "key": key,
        "title": title,
        "count": len(compact_rows),
        "rows": compact_rows,
    }


def _normalize_activity_loop_days(value: Any) -> list[tuple[str, list[Any]]]:
    if isinstance(value, dict):
        return [(str(day), items if isinstance(items, list) else [items]) for day, items in value.items()]
    if isinstance(value, list):
        return [(str(index + 1), items if isinstance(items, list) else [items]) for index, items in enumerate(value)]
    return []


def _build_loop_entries_by_activity(loop_rows: list[dict[str, Any]], activity_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    activity_by_id = {str(row.get("id")): row for row in activity_rows if row.get("id") not in (None, "")}
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in loop_rows:
        loop_id = row.get("id") or row.get("_row_key")
        for day, values in _normalize_activity_loop_days(row.get("day")):
            for value in values:
                activity_id = str(value)
                if not activity_id:
                    continue
                activity = activity_by_id.get(activity_id) or {}
                result[activity_id].append(
                    {
                        "loop_id": loop_id,
                        "day": day,
                        "activity_id": value,
                        "activity_name": _activity_name(activity) if activity else "",
                    }
                )
    return {
        key: sorted(rows, key=lambda item: (_sort_value(item.get("loop_id")), _sort_value(item.get("day"))))
        for key, rows in result.items()
    }


def _build_boss_rows_by_activity(activity_rows: list[dict[str, Any]], boss_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    activities_by_id = {str(row.get("id")): row for row in activity_rows if row.get("id") not in (None, "")}
    activities_by_base: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in activity_rows:
        if row.get("baseId") not in (None, ""):
            activities_by_base[str(row.get("baseId"))].append(row)
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in boss_rows:
        value = str(row.get("id") or row.get("monsterId") or "")
        if not value:
            continue
        matched: list[dict[str, Any]]
        if value in activities_by_id:
            matched = [activities_by_id[value]]
        else:
            matched = activities_by_base.get(value, [])
        for activity in matched:
            result[str(activity.get("id"))].append(row)
    return dict(result)


def _compact_boss_row(row: dict[str, Any], *, item_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    reward_values = [row.get("partakeReward"), row.get("lastHitReward")]
    reward_items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for value in reward_values:
        for item in _extract_reward_items(value, item_by_id):
            key = (str(item.get("id") or ""), str(item.get("count") or ""))
            if key in seen:
                continue
            seen.add(key)
            reward_items.append(item)
    meta_values = [
        f"区域 {row.get('region')}" if row.get("region") not in (None, "") else "",
        f"地图 {row.get('map')}" if row.get("map") not in (None, "") else "",
        f"BOSS组 {row.get('bossGroupId')}" if row.get("bossGroupId") not in (None, "") else "",
        f"刷新点 {row.get('refreshPointId')}" if row.get("refreshPointId") not in (None, "") else "",
    ]
    return {
        "source": "ActivityBoss",
        "row_key": row.get("_row_key") or row.get("id"),
        "title": f"首领 {row.get('monsterId') or row.get('id')}",
        "meta": " / ".join(item for item in meta_values if item),
        "costs": [],
        "reward_items": reward_items,
        "raw_rewards": _raw_value_text(reward_values, limit=12),
        "condition": _clean_text(row.get("trackRoad")),
    }


def _boss_section(boss_rows: list[dict[str, Any]], *, item_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    compact_rows = [_compact_boss_row(row, item_by_id=item_by_id) for row in boss_rows]
    compact_rows = [row for row in compact_rows if row.get("reward_items") or row.get("raw_rewards") or row.get("meta")]
    if not compact_rows:
        return None
    return {
        "key": "boss",
        "title": "首领奖励",
        "count": len(compact_rows),
        "rows": compact_rows,
    }


def _resolve_open_function(row: dict[str, Any], functions_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    jump = _clean_text(row.get("jump"))
    ids = []
    for pattern in ("JumpInterface|", "OpenFunction|"):
        if jump.startswith(pattern):
            ids.append(jump.removeprefix(pattern).split("_", 1)[0])
    for value in ids:
        function = functions_by_id.get(str(value))
        if not function:
            continue
        return {
            "id": function.get("id"),
            "name": _text_value(function, "name") or str(function.get("id") or ""),
            "description": _text_value(function, "descript"),
            "condition": function.get("condition"),
            "unlock": _text_value(function, "descriptUnlock"),
            "lua_path": function.get("luaPath"),
            "window_id": function.get("windowId"),
            "icon": function.get("icon"),
        }
    return None


def _compact_active_task_row(row: dict[str, Any], *, item_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    reward_values = [row.get("goods")]
    reward_items = _extract_reward_items(reward_values, item_by_id)
    description = _text_value(row, "desc") or _text_value(row, "conditionDes")
    terms = _extract_terms(row.get("name"), description, row.get("finishCondition"), limit=16)
    card = {
        "id": f"task:{row.get('id') or row.get('_row_key')}",
        "name": _text_value(row, "name") or f"活跃任务 {row.get('id') or row.get('_row_key')}",
        "activity_type": None,
        "base_id": None,
        "group_id": None,
        "parent_activity_id": None,
        "sub_type": row.get("subType"),
        "reward_group": None,
        "icon": row.get("resource"),
        "sort": row.get("sort"),
        "mainui_pos": None,
        "jump": None,
        "prepare_time": None,
        "start_time": None,
        "end_time": None,
        "reward_time": None,
        "close_panel_time": None,
        "open_condition": row.get("openCondition"),
        "join_condition": row.get("finishCondition"),
        "show_condition": row.get("showCondition"),
        "force_hide_condition": row.get("hideCondition"),
        "join_condition_description": _text_value(row, "conditionDes"),
        "description": description,
        "kind_keys": ["active_task"],
        "kind_names": _activity_kind_names(["active_task"]),
        "time_kind": "none",
        "time_kind_name": TIME_KIND_LABELS["none"],
        "time_hints": [],
        "first_time_hint": None,
        "reward_sections": [],
        "reward_preview": "",
        "source_row_key": row.get("_row_key"),
        "terms": terms,
        "source_table": "ActiveTask",
    }
    if reward_items:
        card["reward_sections"] = [
            {
                "key": "active_task",
                "title": "任务奖励",
                "count": 1,
                "rows": [
                    {
                        "source": "ActiveTask",
                        "row_key": row.get("_row_key") or row.get("id"),
                        "title": _text_value(row, "name"),
                        "meta": f"活跃度 {row.get('activeNum')}" if row.get("activeNum") not in (None, "") else "",
                        "costs": [],
                        "reward_items": reward_items,
                        "raw_rewards": _raw_value_text(reward_values, limit=8),
                        "condition": _clean_text(row.get("finishCondition")),
                    }
                ],
            }
        ]
        card["reward_preview"] = _reward_preview_from_sections(card["reward_sections"])
    return card


def _compact_subpackage_reward_row(row: dict[str, Any], *, item_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    reward_items = _extract_reward_items(row.get("rewards"), item_by_id)
    reward_preview = "、".join(item.get("name") or "" for item in reward_items if item.get("name"))
    name = "资源包奖励" if row.get("id") in (None, "") else f"资源包奖励 {row.get('id')}"
    message = _text_value(row, "msg")
    card = {
        "id": f"subpackage:{row.get('id') or row.get('_row_key')}",
        "name": name,
        "activity_type": None,
        "base_id": None,
        "group_id": None,
        "parent_activity_id": None,
        "sub_type": None,
        "reward_group": None,
        "icon": reward_items[0].get("icon") if reward_items else None,
        "sort": row.get("id"),
        "mainui_pos": None,
        "jump": None,
        "prepare_time": None,
        "start_time": None,
        "end_time": None,
        "reward_time": None,
        "close_panel_time": None,
        "open_condition": row.get("condition"),
        "join_condition": None,
        "show_condition": None,
        "force_hide_condition": None,
        "join_condition_description": "",
        "description": message,
        "kind_keys": ["subpackage"],
        "kind_names": _activity_kind_names(["subpackage"]),
        "time_kind": "none",
        "time_kind_name": TIME_KIND_LABELS["none"],
        "time_hints": [],
        "first_time_hint": None,
        "reward_sections": [
            {
                "key": "subpackage",
                "title": "资源包奖励",
                "count": 1,
                "rows": [
                    {
                        "source": "SubpackageRewards",
                        "row_key": row.get("_row_key") or row.get("id"),
                        "title": name,
                        "meta": "隐藏进度" if row.get("hideProgress") else "",
                        "costs": [],
                        "reward_items": reward_items,
                        "raw_rewards": _raw_value_text(row.get("rewards"), limit=12),
                        "condition": _clean_text(row.get("condition")),
                    }
                ],
            }
        ],
        "reward_preview": reward_preview,
        "source_row_key": row.get("_row_key"),
        "terms": _extract_terms(name, message, reward_preview, limit=16),
        "source_table": "SubpackageRewards",
    }
    return card


def _activity_kind_keys(row: dict[str, Any], sections: list[dict[str, Any]]) -> list[str]:
    keys = [str(section.get("key") or "") for section in sections if section.get("key")]
    if any(_extract_reward_items(row.get(field), {}) for field in ("showReward", "reward", "task")):
        keys.append("direct_reward")
    name = _activity_name(row)
    if "空活动" in name or "控制" in name:
        keys.append("control")
    if not keys:
        keys.append("activity")
    return list(dict.fromkeys(keys))


def _activity_kind_names(keys: list[str]) -> list[str]:
    return [ACTIVITY_KIND_LABELS.get(key, key) for key in keys]


def _extract_terms(*values: Any, limit: int = 12) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        for match in _BRACKET_TERM_RE.finditer(str(value or "")):
            text = _clean_text(match.group(1))
            if text and text not in seen:
                seen.add(text)
                terms.append(text)
                if len(terms) >= limit:
                    return terms
    return terms


def _reward_preview_from_sections(sections: list[dict[str, Any]], *, limit: int = 12) -> str:
    names: list[str] = []
    seen: set[str] = set()
    for section in sections:
        for row in section.get("rows") or []:
            for item in row.get("reward_items") or []:
                name = _clean_text(item.get("name"))
                if name and name not in seen:
                    seen.add(name)
                    names.append(name)
                    if len(names) >= limit:
                        return "、".join(names)
    return "、".join(names)


def _compact_activity_row(
    row: dict[str, Any],
    *,
    gift_rows: list[dict[str, Any]],
    free_gift_rows: list[dict[str, Any]],
    signin_rows: list[dict[str, Any]],
    list_reward_rows: list[dict[str, Any]],
    fund_rows: list[dict[str, Any]],
    battle_pass_rows: list[dict[str, Any]],
    boss_rows: list[dict[str, Any]],
    loop_entries: list[dict[str, Any]],
    jump_target: dict[str, Any] | None,
    item_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    sections = [
        item
        for item in [
            _section(
                "gift",
                "礼包",
                gift_rows,
                source="ActivityGift",
                title_fields=("title", "name"),
                reward_fields=("reward",),
                item_by_id=item_by_id,
            ),
            _section(
                "free_gift",
                "免费礼包",
                free_gift_rows,
                source="ActivityFreeGift",
                title_fields=("title", "name"),
                reward_fields=("reward",),
                item_by_id=item_by_id,
            ),
            _section(
                "signin",
                "签到",
                signin_rows,
                source="ActivitySignIn",
                title_fields=("title", "name"),
                reward_fields=("reward",),
                item_by_id=item_by_id,
            ),
            _section(
                "rank_reward",
                "榜单奖励",
                list_reward_rows,
                source="ActivityListReward",
                title_fields=("allianceDes", "name"),
                reward_fields=("reward", "allianceReward", "outstandingReward", "titleItem"),
                item_by_id=item_by_id,
            ),
            _section(
                "fund",
                "基金",
                fund_rows,
                source="ActivityFundBase",
                title_fields=("name", "progressName"),
                reward_fields=("totalReward", "reward"),
                item_by_id=item_by_id,
            ),
            _section(
                "battle_pass",
                "战令",
                battle_pass_rows,
                source="ActivityBattlePassBase",
                title_fields=("name", "progressName"),
                reward_fields=("discountItem", "reward"),
                item_by_id=item_by_id,
            ),
            _boss_section(boss_rows, item_by_id=item_by_id),
        ]
        if item
    ]
    direct_rewards: list[dict[str, Any]] = []
    for field in ("showReward", "reward", "task"):
        direct_rewards.extend(_extract_reward_items(row.get(field), item_by_id))
    if direct_rewards:
        sections.insert(
            0,
            {
                "key": "direct_reward",
                "title": "活动展示",
                "count": 1,
                "rows": [
                    {
                        "source": "Activity",
                        "row_key": row.get("_row_key") or row.get("id"),
                        "title": _activity_name(row),
                        "meta": "Activity",
                        "costs": [],
                        "reward_items": direct_rewards,
                        "raw_rewards": _raw_value_text([row.get("showReward"), row.get("reward"), row.get("task")], limit=12),
                        "condition": "",
                    }
                ],
            },
        )
    time_fields = _activity_time_fields(row)
    condition_fields = _activity_condition_fields(row)
    hints = _activity_time_hints(row)
    first_hint = _activity_primary_time_hint(hints)
    kind_keys = _activity_kind_keys(row, sections)
    if boss_rows:
        kind_keys.append("boss")
    if loop_entries:
        kind_keys.append("loop")
    kind_keys = list(dict.fromkeys(kind_keys))
    reward_preview = _reward_preview_from_sections(sections)
    description = _text_value(row, "describe")
    join_description = _text_value(row, "joinConditionDescribe")
    card = {
        "id": row.get("id"),
        "name": _activity_name(row),
        "little_name": _activity_little_name(row),
        "title_name": _activity_title_name(row),
        "activity_type": row.get("activityId"),
        "base_id": row.get("baseId"),
        "group_id": row.get("groupId"),
        "parent_activity_id": row.get("parentActId"),
        "sub_type": row.get("subType"),
        "reward_group": row.get("rewardGroup"),
        "icon": row.get("icon"),
        "sort": row.get("sort"),
        "mainui_pos": row.get("mainuiPos"),
        "jump": row.get("jump"),
        "prepare_time": row.get("prepareTime"),
        "start_time": row.get("startTime"),
        "end_time": row.get("endTime"),
        "reward_time": row.get("rewardTime"),
        "close_panel_time": row.get("closePanelTime"),
        "open_condition": row.get("openCondition"),
        "join_condition": row.get("joinCondition"),
        "show_condition": row.get("showCondition"),
        "force_hide_condition": row.get("forceHideCondition"),
        "join_condition_description": join_description,
        "description": description,
        "time_fields": time_fields,
        "condition_fields": condition_fields,
        "kind_keys": kind_keys,
        "kind_names": _activity_kind_names(kind_keys),
        "time_kind": _time_kind_from_hint(first_hint),
        "time_kind_name": TIME_KIND_LABELS.get(_time_kind_from_hint(first_hint), "无时间"),
        "time_hints": hints,
        "first_time_hint": first_hint,
        "reward_sections": sections,
        "reward_preview": reward_preview,
        "loop_entries": loop_entries,
        "jump_target": jump_target,
        "source_row_key": row.get("_row_key"),
        "source_table": "Activity",
    }
    card["terms"] = _extract_terms(
        card["name"],
        card["little_name"],
        card["title_name"],
        description,
        join_description,
        reward_preview,
        " ".join(str(item.get("loop_id") or "") for item in loop_entries),
        _clean_text((jump_target or {}).get("name")),
        limit=16,
    )
    return card


def _index_rows_by_key(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        if value not in (None, ""):
            result[str(value)].append(row)
    return dict(result)


def _build_free_gifts_by_activity(activity_rows: list[dict[str, Any]], free_gift_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    activities_by_base: dict[str, list[dict[str, Any]]] = defaultdict(list)
    activities_by_id: dict[str, dict[str, Any]] = {}
    for row in activity_rows:
        if row.get("baseId") not in (None, ""):
            activities_by_base[str(row.get("baseId"))].append(row)
        if row.get("id") not in (None, ""):
            activities_by_id[str(row.get("id"))] = row
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in free_gift_rows:
        values = row.get("baseId")
        if not isinstance(values, list):
            values = [values]
        matched: set[str] = set()
        for value in values:
            if value in (None, ""):
                continue
            for activity in activities_by_base.get(str(value), []):
                activity_id = str(activity.get("id"))
                if activity_id not in matched:
                    result[activity_id].append(row)
                    matched.add(activity_id)
            activity = activities_by_id.get(str(value))
            if activity:
                activity_id = str(activity.get("id"))
                if activity_id not in matched:
                    result[activity_id].append(row)
    return dict(result)


def _build_fund_rows_by_activity(activity_rows: list[dict[str, Any]], fund_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    activities_by_base: dict[str, list[dict[str, Any]]] = defaultdict(list)
    activities_by_id: dict[str, dict[str, Any]] = {}
    for row in activity_rows:
        if row.get("baseId") not in (None, ""):
            activities_by_base[str(row.get("baseId"))].append(row)
        if row.get("id") not in (None, ""):
            activities_by_id[str(row.get("id"))] = row
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in fund_rows:
        value = row.get("activityBaseId")
        if value in (None, ""):
            continue
        for activity in activities_by_base.get(str(value), []):
            result[str(activity.get("id"))].append(row)
        activity = activities_by_id.get(str(value))
        if activity:
            result[str(activity.get("id"))].append(row)
    return dict(result)


def _build_list_rewards_by_activity(activity_rows: list[dict[str, Any]], list_reward_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    activities_by_reward_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in activity_rows:
        if row.get("rewardGroup") not in (None, ""):
            activities_by_reward_group[str(row.get("rewardGroup"))].append(row)
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in list_reward_rows:
        group = row.get("group")
        if group in (None, ""):
            continue
        for activity in activities_by_reward_group.get(str(group), []):
            result[str(activity.get("id"))].append(row)
    return dict(result)


def _build_activity_type_options(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    samples: dict[str, list[str]] = defaultdict(list)
    for card in cards:
        key = str(card.get("activity_type") or "")
        if not key:
            continue
        item = grouped.setdefault(key, {"value": key, "label": f"玩法 {key}", "count": 0, "activity_type": card.get("activity_type")})
        item["count"] += 1
        name = str(card.get("name") or "").strip()
        if name and name not in samples[key] and len(samples[key]) < 2:
            samples[key].append(name)
    for key, item in grouped.items():
        suffix = " / ".join(samples.get(key) or [])
        if suffix:
            item["label"] = f"{suffix} · {key}"
    return sorted(grouped.values(), key=lambda item: (-int(item.get("count") or 0), _sort_value(item.get("activity_type"))))


def _build_activity_kind_options(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        for key, name in zip(card.get("kind_keys") or [], card.get("kind_names") or []):
            item = grouped.setdefault(str(key), {"value": str(key), "label": str(name or key), "count": 0})
            item["count"] += 1
    return sorted(grouped.values(), key=lambda item: (-int(item.get("count") or 0), str(item.get("label") or "")))


def _build_activity_time_options(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        key = str(card.get("time_kind") or "none")
        item = grouped.setdefault(key, {"value": key, "label": TIME_KIND_LABELS.get(key, key), "count": 0})
        item["count"] += 1
    order = {"absolute": 0, "condition": 1, "relative": 2, "none": 3}
    return sorted(grouped.values(), key=lambda item: (order.get(str(item.get("value")), 9), str(item.get("label") or "")))


def _activity_search_doc(card: dict[str, Any], index: int) -> dict[str, Any]:
    reward_text = " ".join(
        str(item.get("name") or "")
        for section in card.get("reward_sections") or []
        for row in section.get("rows") or []
        for item in row.get("reward_items") or []
    )
    condition_text = " ".join(
        str(card.get(key) or "")
        for key in ("open_condition", "join_condition", "show_condition", "force_hide_condition")
    )
    loop_text = " ".join(
        f"{item.get('loop_id') or ''} {item.get('day') or ''} {item.get('activity_name') or ''}"
        for item in card.get("loop_entries") or []
    )
    jump_target = card.get("jump_target") or {}
    jump_text = " ".join(str(jump_target.get(key) or "") for key in ("id", "name", "description", "unlock", "lua_path"))
    type_values = tuple(
        value
        for value in [
            str(card.get("activity_type") or "").strip(),
            str(card.get("source_table") or "").strip(),
            *(str(name or "").strip() for name in card.get("kind_names") or []),
            *(str(key or "").strip() for key in card.get("kind_keys") or []),
        ]
        if value
    )
    return {
        "index": index,
        "card": card,
        "id": str(card.get("id") or "").lower(),
        "name": str(card.get("name") or "").lower(),
        "little_name": str(card.get("little_name") or "").lower(),
        "title_name": str(card.get("title_name") or "").lower(),
        "description": str(card.get("description") or "").lower(),
        "join_condition_description": str(card.get("join_condition_description") or "").lower(),
        "reward_text": reward_text.lower(),
        "condition_text": condition_text.lower(),
        "loop_text": loop_text.lower(),
        "jump_text": jump_text.lower(),
        "type_values": type_values,
        "kind_values": tuple(str(key or "") for key in card.get("kind_keys") or [] if key),
        "time_kind": str(card.get("time_kind") or "none"),
        "combined": " ".join(
            [
                str(card.get("id") or ""),
                str(card.get("activity_type") or ""),
                str(card.get("base_id") or ""),
                str(card.get("name") or ""),
                str(card.get("little_name") or ""),
                str(card.get("title_name") or ""),
                str(card.get("description") or ""),
                str(card.get("join_condition_description") or ""),
                reward_text,
                condition_text,
                loop_text,
                jump_text,
                " ".join(card.get("kind_names") or []),
            ]
        ).lower(),
    }


@lru_cache(maxsize=4)
def _load_activity_runtime_index_cached(path_text: str, mtime_ns: int, size: int, export_root_text: str) -> dict[str, Any]:
    catalog = _load_activity_catalog_cached(path_text, mtime_ns, size, export_root_text)
    cards = [card for card in catalog.get("cards") or [] if isinstance(card, dict)]
    cards_by_id = {str(card.get("id")): card for card in cards if card.get("id") not in (None, "")}
    return {
        "catalog": catalog,
        "cards_by_id": cards_by_id,
        "activity_type_options": _build_activity_type_options(cards),
        "kind_options": _build_activity_kind_options(cards),
        "time_options": _build_activity_time_options(cards),
        "search_docs": tuple(_activity_search_doc(card, index) for index, card in enumerate(cards)),
    }


def load_fanxiu_activity_runtime_index(
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    catalog_path = _resolve_catalog_file(export_root, rebuild_missing=rebuild_missing)
    root = resolve_fanxiu_export_root(export_root)
    stat = catalog_path.stat()
    return _load_activity_runtime_index_cached(str(catalog_path), stat.st_mtime_ns, stat.st_size, str(root))


def _score_activity_doc(doc: dict[str, Any], terms: tuple[str, ...]) -> int:
    if not terms:
        return 1
    if not all(term in doc["combined"] for term in terms):
        return 0
    score = 0
    for term in terms:
        if doc["id"] == term:
            score += 180
        if doc["name"] == term:
            score += 220
        if term in doc["name"]:
            score += 90
        if term in doc["little_name"] or term in doc["title_name"]:
            score += 50
        if term in doc["reward_text"]:
            score += 34
        if term in doc["description"] or term in doc["join_condition_description"]:
            score += 20
        if term in doc["condition_text"]:
            score += 10
        if term in doc["loop_text"] or term in doc["jump_text"]:
            score += 12
    return score


def _format_activity_search_item(card: dict[str, Any], score: int) -> dict[str, Any]:
    return {
        "id": card.get("id"),
        "name": card.get("name") or str(card.get("id") or "未命名"),
        "little_name": card.get("little_name"),
        "title_name": card.get("title_name"),
        "activity_type": card.get("activity_type"),
        "base_id": card.get("base_id"),
        "icon": card.get("icon"),
        "kind_keys": card.get("kind_keys") or [],
        "kind_names": card.get("kind_names") or [],
        "time_kind": card.get("time_kind"),
        "time_kind_name": card.get("time_kind_name"),
        "description_preview": _preview(card.get("description") or card.get("join_condition_description"), 140),
        "reward_preview": _preview(card.get("reward_preview"), 180),
        "first_time_hint": card.get("first_time_hint"),
        "loop_entries": (card.get("loop_entries") or [])[:6],
        "source_table": card.get("source_table"),
        "presence_status": card.get("presence_status") or "current",
        "is_stale": bool(card.get("is_stale")),
        "last_seen_at": card.get("last_seen_at"),
        "missing_since": card.get("missing_since"),
        "terms": (card.get("terms") or [])[:8],
        "score": score,
    }


def _build_activity_facet_index(scored_rows: list[tuple[int, int, dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    rows: dict[str, dict[str, list[str]]] = {
        "kind_key": {},
        "time_kind": {},
        "activity_type": {},
    }
    object_ids: list[str] = []
    for _score, _index, card, doc in scored_rows:
        object_id = str(card.get("id") or "")
        if not object_id:
            continue
        object_ids.append(object_id)
        for kind_key in doc.get("kind_values") or ():
            rows["kind_key"].setdefault(str(kind_key), []).append(object_id)
        time_kind = str(doc.get("time_kind") or "none")
        rows["time_kind"].setdefault(time_kind, []).append(object_id)
        activity_type = str(card.get("activity_type") or "")
        if activity_type:
            rows["activity_type"].setdefault(activity_type, []).append(object_id)
    return {"object_ids": object_ids, "rows": rows}


def search_fanxiu_activity_cards(
    *,
    query: str = "",
    kind_key: str = "",
    time_kind: str = "",
    activity_type: str = "",
    sort_by: str = "default",
    sort_order: str = "asc",
    limit: int = 80,
    offset: int = 0,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    runtime_index = load_fanxiu_activity_runtime_index(export_root=export_root, rebuild_missing=rebuild_missing)
    catalog = runtime_index["catalog"]
    kind_key = str(kind_key or "").strip()
    time_kind = str(time_kind or "").strip()
    activity_type = str(activity_type or "").strip()
    sort_by = str(sort_by or "default").strip()
    sort_order = str(sort_order or "asc").strip().lower()
    if sort_by not in {"default", "time"}:
        sort_by = "default"
    if sort_order not in {"asc", "desc"}:
        sort_order = "asc"
    terms = tuple(item.strip().lower() for item in re.split(r"\s+", query or "") if item.strip())
    query_rows: list[tuple[int, int, dict[str, Any], dict[str, Any]]] = []
    for doc in runtime_index["search_docs"]:
        card = doc["card"]
        score = _score_activity_doc(doc, terms)
        if score <= 0:
            continue
        query_rows.append((score, int(doc["index"]), card, doc))
    scored_rows: list[tuple[int, int, dict[str, Any]]] = []
    for score, index, card, doc in query_rows:
        if kind_key and kind_key not in doc["kind_values"]:
            continue
        if time_kind and time_kind != doc["time_kind"]:
            continue
        if activity_type and activity_type != str(card.get("activity_type") or ""):
            continue
        scored_rows.append((score, index, card))
    if sort_by == "time":
        if sort_order == "desc":
            scored_rows.sort(key=lambda item: (1 if item[2].get("is_stale") else 0, -card_timeline_sort_value(item[2]), _sort_value(item[2].get("id")), item[1]))
        else:
            scored_rows.sort(key=lambda item: (1 if item[2].get("is_stale") else 0, card_timeline_sort_value(item[2]), _sort_value(item[2].get("id")), item[1]))
    elif terms:
        scored_rows.sort(key=lambda item: (-item[0], 1 if item[2].get("is_stale") else 0, _sort_value(item[2].get("id")), item[1]))
    else:
        scored_rows.sort(key=lambda item: (1 if item[2].get("is_stale") else 0, _sort_value(item[2].get("sort")), _sort_value(item[2].get("id")), item[1]))
    page_rows = scored_rows[offset : offset + limit]
    return {
        "query": query,
        "kind_key": kind_key,
        "time_kind": time_kind,
        "activity_type": activity_type,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "limit": limit,
        "offset": offset,
        "total": len(scored_rows),
        "stats": catalog.get("stats") or {},
        "catalog_path": catalog["catalog_path"],
        "kind_options": runtime_index["kind_options"],
        "time_options": runtime_index["time_options"],
        "activity_type_options": runtime_index["activity_type_options"],
        "facet_index": _build_activity_facet_index(query_rows),
        "items": [_format_activity_search_item(card, score) for score, _index, card in page_rows],
    }


def get_fanxiu_activity_card(
    activity_id: str | int,
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    requested = str(activity_id)
    runtime_index = load_fanxiu_activity_runtime_index(export_root=export_root, rebuild_missing=rebuild_missing)
    catalog = runtime_index["catalog"]
    card = runtime_index["cards_by_id"].get(requested)
    if card:
        return {"catalog_path": catalog["catalog_path"], "card": {**card, "terms": (card.get("terms") or [])[:20]}}
    raise FanxiuResourceError(f"没有找到活动：{activity_id}")


def build_fanxiu_activity_catalog(*, export_root: str | Path | None = None) -> dict[str, Any]:
    built_at = _utc_now_text()
    root = resolve_fanxiu_export_root(export_root)
    activity_path = root / DEFAULT_ACTIVITY_ROWS
    activity_rows = _load_json_rows(activity_path)
    gift_rows = _load_optional_json_rows(root / DEFAULT_ACTIVITY_GIFT_ROWS)
    free_gift_rows = _load_optional_json_rows(root / DEFAULT_ACTIVITY_FREE_GIFT_ROWS)
    signin_rows = _load_optional_json_rows(root / DEFAULT_ACTIVITY_SIGNIN_ROWS)
    list_reward_rows = _load_optional_json_rows(root / DEFAULT_ACTIVITY_LIST_REWARD_ROWS)
    fund_rows = _load_optional_json_rows(root / DEFAULT_ACTIVITY_FUND_ROWS)
    battle_pass_rows = _load_optional_json_rows(root / DEFAULT_ACTIVITY_BATTLE_PASS_ROWS)
    loop_rows = _load_optional_json_rows(root / DEFAULT_ACTIVITY_LOOP_ROWS)
    boss_rows = _load_optional_json_rows(root / DEFAULT_ACTIVITY_BOSS_ROWS)
    active_task_rows = _load_optional_json_rows(root / DEFAULT_ACTIVE_TASK_ROWS)
    open_function_rows = _load_optional_json_rows(root / DEFAULT_OPEN_FUNCTION_ROWS)
    subpackage_reward_rows = _load_optional_json_rows(root / DEFAULT_SUBPACKAGE_REWARD_ROWS)

    item_runtime = load_fanxiu_item_runtime_index(export_root=root, rebuild_missing=True)
    item_by_id: dict[str, dict[str, Any]] = item_runtime.get("cards_by_id") or {}
    gift_by_activity = _index_rows_by_key(gift_rows, "activityId")
    signin_by_activity = _index_rows_by_key(signin_rows, "activityId")
    free_gift_by_activity = _build_free_gifts_by_activity(activity_rows, free_gift_rows)
    list_reward_by_activity = _build_list_rewards_by_activity(activity_rows, list_reward_rows)
    fund_by_activity = _build_fund_rows_by_activity(activity_rows, fund_rows)
    battle_pass_by_activity = _index_rows_by_key(battle_pass_rows, "taskActivityId")
    loop_entries_by_activity = _build_loop_entries_by_activity(loop_rows, activity_rows)
    boss_by_activity = _build_boss_rows_by_activity(activity_rows, boss_rows)
    functions_by_id = {str(row.get("id")): row for row in open_function_rows if row.get("id") not in (None, "")}

    current_cards = [
        _mark_current_card(
            _compact_activity_row(
                row,
                gift_rows=gift_by_activity.get(str(row.get("id")), []),
                free_gift_rows=free_gift_by_activity.get(str(row.get("id")), []),
                signin_rows=signin_by_activity.get(str(row.get("id")), []),
                list_reward_rows=list_reward_by_activity.get(str(row.get("id")), []),
                fund_rows=fund_by_activity.get(str(row.get("id")), []),
                battle_pass_rows=battle_pass_by_activity.get(str(row.get("id")), []),
                boss_rows=boss_by_activity.get(str(row.get("id")), []),
                loop_entries=loop_entries_by_activity.get(str(row.get("id")), []),
                jump_target=_resolve_open_function(row, functions_by_id),
                item_by_id=item_by_id,
            ),
            built_at=built_at,
        )
        for row in sorted(activity_rows, key=lambda item: (_sort_value(item.get("sort")), _sort_value(item.get("id"))))
    ]
    current_cards.extend(
        _mark_current_card(_compact_active_task_row(row, item_by_id=item_by_id), built_at=built_at)
        for row in sorted(active_task_rows, key=lambda item: (_sort_value(item.get("sort")), _sort_value(item.get("id"))))
    )
    current_cards.extend(
        _mark_current_card(_compact_subpackage_reward_row(row, item_by_id=item_by_id), built_at=built_at)
        for row in sorted(subpackage_reward_rows, key=lambda item: _sort_value(item.get("id")))
    )

    out_dir = root / "parsed_configs" / "activity_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = out_dir / "activity_catalog.json"
    cards = _merge_previous_activity_cards(catalog_path, current_cards, built_at=built_at)
    current_card_count = sum(1 for card in cards if not card.get("is_stale"))
    stale_card_count = sum(1 for card in cards if card.get("is_stale"))
    stats = {
        "activity_count": len(activity_rows),
        "activity_gift_count": len(gift_rows),
        "activity_free_gift_count": len(free_gift_rows),
        "activity_signin_count": len(signin_rows),
        "activity_list_reward_count": len(list_reward_rows),
        "activity_fund_count": len(fund_rows),
        "activity_battle_pass_count": len(battle_pass_rows),
        "activity_loop_count": len(loop_rows),
        "activity_boss_count": len(boss_rows),
        "active_task_count": len(active_task_rows),
        "open_function_count": len(open_function_rows),
        "subpackage_reward_count": len(subpackage_reward_rows),
        "catalog_card_count": len(cards),
        "current_card_count": current_card_count,
        "stale_card_count": stale_card_count,
        "activity_with_time_hint_count": sum(1 for card in cards if card.get("first_time_hint")),
        "activity_with_reward_count": sum(1 for card in cards if card.get("reward_sections")),
        "activity_with_loop_count": sum(1 for card in cards if card.get("loop_entries")),
        "activity_with_jump_target_count": sum(1 for card in cards if card.get("jump_target")),
        "activity_kind_count": len({key for card in cards for key in card.get("kind_keys") or []}),
        "activity_type_count": len({str(card.get("activity_type") or "") for card in cards if card.get("activity_type") not in (None, "")}),
        "time_kind_count": len({str(card.get("time_kind") or "none") for card in cards}),
    }
    catalog = {
        "schema_version": ACTIVITY_CATALOG_SCHEMA_VERSION,
        "built_at": built_at,
        "source": {
            "activity_rows": str(activity_path),
            "activity_gift_rows": str(root / DEFAULT_ACTIVITY_GIFT_ROWS),
            "activity_free_gift_rows": str(root / DEFAULT_ACTIVITY_FREE_GIFT_ROWS),
            "activity_signin_rows": str(root / DEFAULT_ACTIVITY_SIGNIN_ROWS),
            "activity_list_reward_rows": str(root / DEFAULT_ACTIVITY_LIST_REWARD_ROWS),
            "activity_fund_rows": str(root / DEFAULT_ACTIVITY_FUND_ROWS),
            "activity_battle_pass_rows": str(root / DEFAULT_ACTIVITY_BATTLE_PASS_ROWS),
            "activity_loop_rows": str(root / DEFAULT_ACTIVITY_LOOP_ROWS),
            "activity_boss_rows": str(root / DEFAULT_ACTIVITY_BOSS_ROWS),
            "active_task_rows": str(root / DEFAULT_ACTIVE_TASK_ROWS),
            "open_function_rows": str(root / DEFAULT_OPEN_FUNCTION_ROWS),
            "subpackage_reward_rows": str(root / DEFAULT_SUBPACKAGE_REWARD_ROWS),
        },
        "stats": stats,
        "cards": cards,
    }
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**catalog, "catalog_path": str(catalog_path)}
