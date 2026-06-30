from __future__ import annotations

import json
import re
import time
from collections import Counter, deque
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from backend.core.fanxiu.catalog.lua_config import _find_default_lang_path, parse_fanxiu_generated_lua_config
from backend.core.fanxiu.mail.policy import (
    fanxiu_mail_action_policy_for_record,
    fanxiu_mail_action_policy_for_rewards,
    fanxiu_mail_desired_status_for_rewards,
)
from backend.core.fanxiu.mail.store import (
    clear_fanxiu_mail_records,
    ensure_fanxiu_mail_table,
    format_fanxiu_mail_time_ms,
    mark_fanxiu_mail_action,
    mark_fanxiu_mail_locked,
    normalize_fanxiu_mail_time_text,
    normalize_fanxiu_mail_title,
    upsert_fanxiu_mail_fact,
)
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.packet.tcp_flow import (
    DEFAULT_PARSED_CONFIGS,
    _fanxiu_config_name,
    _format_fanxiu_reward_item,
    _iter_fanxiu_tcp_decoded_sources,
    _load_json_file,
    _strip_lua_rich_text,
    resolve_fanxiu_tcp_live_capture_dir,
    resolve_fanxiu_tcp_store_root,
)
from backend.core.fanxiu.catalog.item import _item_type_meta, _text_value, load_fanxiu_item_runtime_index
from backend.models import FanxiuMailRecord


MAIL_SOURCE_PROTOCOLS = {"SM_NewMail", "SM_MailBox"}
MAIL_ACTION_PROTOCOLS = {
    "CM_ReadMail",
    "SM_ReadMail",
    "CM_GetMailReward",
    "SM_GetMailReward",
    "CM_DeleteMail",
    "SM_DeleteMail",
}
MAIL_LOCK_PROTOCOLS = {
    "CM_LockMail",
    "SM_LockMail",
}
MAIL_PROTOCOL_IDS = {30402, 30404, 30405, 30406, 30407, 30408, 30409, 30410}
MAIL_ALL_PROTOCOLS = MAIL_SOURCE_PROTOCOLS | MAIL_ACTION_PROTOCOLS | MAIL_LOCK_PROTOCOLS


MAIL_TYPE_TITLE_OVERRIDES = {
    # 只用于静态 Envelope 表确实缺失的已核对邮件类型。
}


def _mail_action_status_from_protocol(name: str) -> str:
    if name == "SM_GetMailReward":
        return "claimed"
    if name == "SM_DeleteMail":
        return "deleted"
    if name == "SM_ReadMail":
        return "seen"
    return ""


def _source_observed_at(source: dict[str, Any]) -> str:
    return str(source.get("pcap_modified_at") or source.get("created_at") or "").strip()


def _mail_evidence_has_server_action(evidence: Any, status: str) -> bool:
    if not isinstance(evidence, dict):
        return False
    expected = {
        "claimed": {"SM_GetMailReward"},
        "deleted": {"SM_DeleteMail"},
    }.get(str(status or ""))
    if not expected:
        return False
    actions = evidence.get("mail_actions")
    if isinstance(actions, list):
        for item in actions:
            if isinstance(item, dict) and str(item.get("action_protocol") or "") in expected:
                return True
    return str(evidence.get("action_protocol") or "") in expected


def _iter_list_items(value: Any) -> list[Any]:
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return list(value["items"])
    if isinstance(value, list):
        return list(value)
    return []


def _extract_mail_ids(value: Any) -> list[str]:
    if isinstance(value, dict):
        if value.get("id") not in (None, ""):
            return [str(value.get("id"))]
        return [_stringify_mail_id(item) for item in _iter_list_items(value.get("ids")) if _stringify_mail_id(item)]
    return []


def _stringify_mail_id(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("id", "value", "long", "mail_id", "mailId"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def _extract_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _extract_lock_ids(parsed: dict[str, Any]) -> set[str]:
    items = _iter_list_items(parsed.get("locks"))
    return {_stringify_mail_id(item) for item in items if _stringify_mail_id(item)}


def _mail_status_from_vo(mail_vo: dict[str, Any]) -> str:
    if bool(mail_vo.get("rewardGetted")):
        return "claimed"
    return "seen"


def _coerce_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = float(text) if "." in text else int(text)
    except ValueError:
        return None
    return int(number) if isinstance(number, float) and number.is_integer() else number


def _put_mail_item_name(
    index: dict[str, dict[str, Any]],
    item_id: Any,
    *,
    name: Any,
    source: str,
    quality: Any = "",
    item_type: Any = "",
    icon: Any = "",
    small_icon: Any = "",
    description: Any = "",
) -> None:
    item_id_text = str(item_id or "").strip()
    name_text = _strip_lua_rich_text(str(name or "").strip())
    if not item_id_text or not name_text or item_id_text in index:
        return
    row: dict[str, Any] = {"name": name_text, "source": source}
    if quality:
        row["quality"] = quality
    if item_type:
        row["type"] = item_type
    if icon:
        row["icon"] = icon
    if small_icon:
        row["small_icon"] = small_icon
    if description:
        row["description"] = description
    index[item_id_text] = row


def _load_quality_name_map(export_root: Path) -> dict[str, str]:
    path = export_root / DEFAULT_PARSED_CONFIGS / "Quality" / "rows.json"
    if not path.is_file():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(rows, list):
        return {}
    output: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_id = str(row.get("id", row.get("_row_key")) or "").strip()
        name = _text_value(row, "name").strip()
        if row_id and name:
            output[row_id] = _strip_lua_rich_text(name)
    return output


def _load_item_rows_name_index(export_root: Path) -> dict[str, dict[str, Any]]:
    path = export_root / DEFAULT_PARSED_CONFIGS / "Item" / "rows.json"
    if not path.is_file():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(rows, list):
        return {}

    quality_names = _load_quality_name_map(export_root)
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        item_id = row.get("id", row.get("_row_key"))
        name = _text_value(row, "name").strip()
        if not name:
            continue
        type_meta = _item_type_meta(row)
        quality_key = str(row.get("quality") or "").strip()
        _put_mail_item_name(
            index,
            item_id,
            name=name,
            quality=quality_names.get(quality_key, quality_key),
            item_type=type_meta.get("type_sub_type_name") or type_meta.get("type_name") or "",
            icon=row.get("icon") or row.get("iconId") or "",
            small_icon=row.get("smallIcon") or row.get("small_icon") or "",
            description=_text_value(row, "describe") or _text_value(row, "desc"),
            source="item_rows",
        )
    return index


def _find_item_lua_path(export_root: Path) -> Path | None:
    config_root = export_root / "by_source" / "lscripts" / "generate" / "cfg"
    candidates = sorted(config_root.glob("item_*/text_assets/Item.lua"), key=lambda item: item.parts[-3])
    return candidates[-1] if candidates else None


def _load_item_lua_name_index(export_root: Path) -> dict[str, dict[str, Any]]:
    path = _find_item_lua_path(export_root)
    if not path or not path.is_file():
        return {}
    try:
        parsed = parse_fanxiu_generated_lua_config(path, lang_path=_find_default_lang_path(export_root))
    except Exception:
        return {}

    index: dict[str, dict[str, Any]] = {}
    for row in parsed.get("rows") or []:
        if not isinstance(row, dict):
            continue
        item_id = row.get("id", row.get("_row_key"))
        name = _text_value(row, "name").strip()
        if not name:
            continue
        type_meta = _item_type_meta(row)
        _put_mail_item_name(
            index,
            item_id,
            name=name,
            quality=str(row.get("quality") or "").strip(),
            item_type=type_meta.get("type_sub_type_name") or type_meta.get("type_name") or "",
            icon=row.get("icon") or row.get("iconId") or "",
            small_icon=row.get("smallIcon") or row.get("small_icon") or "",
            description=_text_value(row, "describe") or _text_value(row, "desc"),
            source="item_lua",
        )
    return index


@lru_cache(maxsize=8)
def _load_mail_item_name_index_cached(export_root_text: str, data_dir_text: str) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    try:
        runtime_index = load_fanxiu_item_runtime_index(export_root=export_root_text or None, rebuild_missing=False)
        for item_id, card in (runtime_index.get("cards_by_id") or {}).items():
            if not isinstance(card, dict):
                continue
            _put_mail_item_name(
                index,
                item_id,
                name=card.get("name"),
                quality=card.get("quality_name") or card.get("quality_tab") or "",
                item_type=card.get("type_name") or "",
                icon=card.get("icon") or "",
                small_icon=card.get("small_icon") or "",
                description=card.get("description") or card.get("effect_text") or card.get("effect_description") or "",
                source="item_catalog",
            )
    except Exception:
        pass

    try:
        export_root = resolve_fanxiu_export_root(export_root_text or None)
        for item_id, row in _load_item_rows_name_index(export_root).items():
            index.setdefault(item_id, row)
        for item_id, row in _load_item_lua_name_index(export_root).items():
            index.setdefault(item_id, row)
    except Exception:
        pass

    try:
        tcp_root = resolve_fanxiu_tcp_store_root(data_dir_text or None)
        analysis_paths = sorted(
            tcp_root.glob("**/all_bag_full_items.analysis.json"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
        for path in analysis_paths[:8]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for row in data.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                item_id = str(row.get("base_id") or "").strip()
                name = str(row.get("name") or "").strip()
                _put_mail_item_name(
                    index,
                    item_id,
                    name=name,
                    quality=row.get("quality") or "",
                    item_type=row.get("type") or "",
                    source="bag_analysis",
                )
    except Exception:
        pass

    return index


def _load_mail_item_name_index(
    *,
    export_root: str | Path | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    root = str(resolve_fanxiu_export_root(export_root))
    data = str(data_dir or "")
    return _load_mail_item_name_index_cached(root, data)


def _mail_item_name_source_counts(index: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("source") or "unknown") for row in index.values() if isinstance(row, dict))
    return dict(counts.most_common())


def _reward_item_name(item_id: Any, export_root: str | Path | None, item_name_index: dict[str, dict[str, Any]] | None = None) -> str:
    item_id_text = str(item_id or "").strip()
    indexed = (item_name_index or {}).get(item_id_text) or {}
    name = str(indexed.get("name") or "").strip()
    if name:
        return name
    name = _fanxiu_config_name(export_root, "Item", item_id)
    return name or ""


def _normalize_mail_reward_item(
    reward: Any,
    export_root: str | Path | None = None,
    item_name_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(reward, dict):
        return None
    reward_class = str(reward.get("_class") or "")
    if reward_class and reward_class not in {"RewardResult", "NoticeRewardVO", "RewardItem"}:
        return None
    source = reward
    super_value = reward.get("_super")
    if isinstance(super_value, dict):
        source = {**super_value, **reward}
    item_id = source.get("code")
    if item_id in (None, "", 0):
        item_id = source.get("baseId")
    if item_id in (None, "", 0):
        item_id = source.get("itemId") or source.get("item") or source.get("id")
    amount = source.get("amount")
    if amount in (None, ""):
        amount = source.get("num") or source.get("count")
    indexed = (item_name_index or {}).get(str(item_id or "").strip()) or {}
    normalized: dict[str, Any] = {
        "item_id": str(item_id or ""),
        "item_name": _reward_item_name(item_id, export_root, item_name_index),
        "amount": _coerce_number(amount),
        "text": _format_fanxiu_reward_item(reward, export_root),
    }
    if indexed.get("quality"):
        normalized["quality"] = indexed.get("quality")
    if indexed.get("type"):
        normalized["item_type"] = indexed.get("type")
    if indexed.get("icon"):
        normalized["icon"] = indexed.get("icon")
    if indexed.get("small_icon"):
        normalized["small_icon"] = indexed.get("small_icon")
    if indexed.get("description"):
        normalized["description"] = indexed.get("description")
    if indexed.get("source"):
        normalized["name_source"] = indexed.get("source")
    reward_type = source.get("type")
    if reward_type not in (None, ""):
        normalized["type"] = reward_type
    if reward_class:
        normalized["class"] = reward_class
    return normalized if normalized["item_id"] or normalized["amount"] is not None or normalized["text"] else None


def _normalize_mail_rewards(
    mail_vo: dict[str, Any],
    export_root: str | Path | None = None,
    item_name_index: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rewards = mail_vo.get("rewards")
    items = _iter_list_items(rewards)
    normalized: list[dict[str, Any]] = []
    for item in items:
        reward = _normalize_mail_reward_item(item, export_root, item_name_index)
        if not reward:
            continue
        normalized.append(reward)
    return normalized


def _mail_rewards_summary(rewards: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for reward in rewards:
        item_id = str(reward.get("item_id") or "").strip()
        name = str(reward.get("item_name") or "").strip() or (f"未知道具 #{item_id}" if item_id else "")
        amount = reward.get("amount")
        if name and amount is not None:
            parts.append(f"{name} x{amount}")
        elif reward.get("text"):
            parts.append(str(reward.get("text")))
        elif name:
            parts.append(name)
    return "，".join(parts)


def _mail_attachment_content_summary(rewards: list[dict[str, Any]]) -> str:
    summary = _mail_rewards_summary(rewards)
    return f"附件：{summary}" if summary else ""


def _mail_empty_content_summary(title: str, mail_vo: dict[str, Any]) -> str:
    title_text = _clean_mail_text(title or _mail_title_from_vo(mail_vo, {})) or "未知邮件"
    return f"抓包未携带正文或附件；邮件类型：{title_text}"


def _clean_mail_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"</?color(?:=[^>]+)?>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _format_mail_param_number(value: Any) -> str:
    number = _coerce_number(value)
    if isinstance(number, float):
        return f"{number:g}"
    if isinstance(number, int):
        return str(number)
    return _clean_mail_text(value)


def _format_mail_duration_ms(value: Any) -> str:
    number = _coerce_number(value)
    if not isinstance(number, (int, float)) or number <= 0:
        return ""
    seconds = float(number) / 1000
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{int(seconds // 3600)}小时"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{int(seconds // 60)}分钟"
    return f"{seconds:g}秒"


def _mail_i18n_param_groups(
    mail_vo: dict[str, Any],
    *,
    export_root: str | Path | None = None,
    item_name_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, list[str]]:
    items = _iter_list_items(mail_vo.get("i18nParams"))
    groups: dict[str, list[str]] = {"NAME": [], "NUM": [], "REWARD": [], "UNKNOWN": []}
    for item in items:
        if not isinstance(item, dict):
            continue
        super_value = item.get("_super")
        key = ""
        if isinstance(super_value, dict):
            key = _clean_mail_text(super_value.get("key")).upper()
        if not key:
            key = _clean_mail_text(item.get("key")).upper()
        value = item.get("value")
        if value in (None, ""):
            type_id = item.get("_type_id")
            offset = item.get("_unparsed_at")
            if type_id is not None or offset is not None:
                groups["UNKNOWN"].append(f"type={type_id}, offset={offset}")
            continue
        if key == "NAME":
            groups["NAME"].append(_clean_mail_text(value))
        elif key == "NUM" or key.endswith("NUM") or key == "N":
            groups["NUM"].append(_format_mail_param_number(value))
        elif key == "REWARD":
            rewards = []
            if isinstance(value, dict):
                for reward in _iter_list_items(value):
                    normalized = _normalize_mail_reward_item(reward, export_root, item_name_index)
                    if normalized:
                        rewards.append(_mail_rewards_summary([normalized]))
            groups["REWARD"].extend(text for text in rewards if text)
        elif key:
            groups.setdefault(key, []).append(_clean_mail_text(value))
        else:
            text = _format_mail_param_number(value)
            if text:
                groups["UNKNOWN"].append(text)
    return groups


def _mail_i18n_param_values(
    mail_vo: dict[str, Any],
    *,
    export_root: str | Path | None = None,
    item_name_index: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    values: list[str] = []
    for item in _iter_list_items(mail_vo.get("i18nParams")):
        if not isinstance(item, dict):
            continue
        super_value = item.get("_super")
        key = ""
        if isinstance(super_value, dict):
            key = _clean_mail_text(super_value.get("key")).upper()
        if not key:
            key = _clean_mail_text(item.get("key")).upper()
        value = item.get("value")
        if value in (None, ""):
            continue
        if key == "REWARD":
            rewards: list[str] = []
            if isinstance(value, dict):
                for reward in _iter_list_items(value):
                    normalized = _normalize_mail_reward_item(reward, export_root, item_name_index)
                    if normalized:
                        rewards.append(_mail_rewards_summary([normalized]))
            values.extend(text for text in rewards if text)
            continue
        values.append(_format_mail_param_number(value))
    return [value for value in values if value]


def _mail_i18n_template_values(
    mail_vo: dict[str, Any],
    template: str,
    *,
    export_root: str | Path | None = None,
    item_name_index: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    groups = _mail_i18n_param_groups(mail_vo, export_root=export_root, item_name_index=item_name_index)
    queues: dict[str, deque[str]] = {key: deque(values) for key, values in groups.items()}
    sequential_values = deque(
        _mail_i18n_param_values(mail_vo, export_root=export_root, item_name_index=item_name_index)
    )
    raw_num_values: deque[Any] = deque()
    for item in _iter_list_items(mail_vo.get("i18nParams")):
        if not isinstance(item, dict):
            continue
        super_value = item.get("_super")
        key = ""
        if isinstance(super_value, dict):
            key = _clean_mail_text(super_value.get("key")).upper()
        if not key:
            key = _clean_mail_text(item.get("key")).upper()
        if key in {"NUM", "N"} and item.get("value") not in (None, ""):
            raw_num_values.append(item.get("value"))

    values: list[str] = []
    for match in re.finditer(r"\$([^$]+)\$", template or ""):
        placeholder = _clean_mail_text(match.group(1)).upper()
        value = ""
        if placeholder == "TIME":
            raw_value = raw_num_values.popleft() if raw_num_values else None
            value = _format_mail_duration_ms(raw_value)
            if value and queues.get("NUM"):
                queues["NUM"].popleft()
            if not value and queues.get("NUM"):
                value = str(queues["NUM"].popleft())
        elif placeholder in {"L_REWARDS", "REWARDS", "REWARD"}:
            if queues.get("REWARD"):
                value = str(queues["REWARD"].popleft())
        elif queues.get(placeholder):
            value = str(queues[placeholder].popleft())
        elif placeholder.startswith("L_") and queues.get(placeholder[2:]):
            value = str(queues[placeholder[2:]].popleft())
        elif sequential_values:
            value = str(sequential_values.popleft())
        values.append(value)
    return values


def _mail_i18n_param_text(
    mail_vo: dict[str, Any],
    *,
    export_root: str | Path | None = None,
    item_name_index: dict[str, dict[str, Any]] | None = None,
) -> str:
    groups = _mail_i18n_param_groups(mail_vo, export_root=export_root, item_name_index=item_name_index)
    parts: list[str] = []
    if groups.get("NAME"):
        parts.append("名称=" + "，".join(groups["NAME"]))
    if groups.get("NUM"):
        parts.append("数值=" + "，".join(groups["NUM"]))
    if groups.get("REWARD"):
        parts.append("奖励=" + "，".join(groups["REWARD"]))
    for key, values in groups.items():
        if key in {"NAME", "NUM", "REWARD", "UNKNOWN"} or not values:
            continue
        parts.append(f"{key}=" + "，".join(values))
    if groups.get("UNKNOWN"):
        parts.append("未命名=" + "，".join(groups["UNKNOWN"]))
    return "；".join(parts)


def _mail_content_fallback_from_params(
    title: str,
    mail_vo: dict[str, Any],
    *,
    export_root: str | Path | None = None,
    item_name_index: dict[str, dict[str, Any]] | None = None,
) -> str:
    groups = _mail_i18n_param_groups(mail_vo, export_root=export_root, item_name_index=item_name_index)
    names = groups.get("NAME") or []
    nums = groups.get("NUM") or []
    rewards = groups.get("REWARD") or []
    title_text = _clean_mail_text(title or _mail_title_from_vo(mail_vo, {}))
    if title_text == "修炼值自动领取通知" and (names or nums):
        detail = names[0] if names else "修炼值"
        if nums:
            detail = f"{detail} x{nums[0]}"
        return f"自动领取：{detail}"
    if title_text == "灵脉收益":
        parts = []
        if names:
            parts.append(f"所在灵脉：{names[0]}")
        if nums:
            duration = _format_mail_duration_ms(nums[0])
            if duration:
                parts.append(f"聚灵时间：{duration}")
        if rewards:
            parts.append("收益：" + "，".join(rewards))
        return "；".join(parts)
    params = _mail_i18n_param_text(mail_vo, export_root=export_root, item_name_index=item_name_index)
    return f"参数：{params}" if params else ""


@lru_cache(maxsize=8)
def _load_system_message_rows_cached(path_text: str, mtime_ns: int, size: int) -> dict[int, dict[str, Any]]:
    rows = json.loads(Path(path_text).read_text(encoding="utf-8"))
    result: dict[int, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_id = row.get("id", row.get("_row_key"))
        try:
            result[int(row_id)] = row
        except (TypeError, ValueError):
            continue
    return result


def _load_system_message_rows(export_root: str | Path | None = None) -> dict[int, dict[str, Any]]:
    root = resolve_fanxiu_export_root(export_root)
    rows_path = root / DEFAULT_PARSED_CONFIGS / "SystemMessage" / "rows.json"
    if not rows_path.is_file():
        return {}
    stat = rows_path.stat()
    return _load_system_message_rows_cached(str(rows_path), stat.st_mtime_ns, stat.st_size)


def _render_system_message_template(template: str, values: list[str]) -> str:
    index = 0

    def replace(_match: re.Match[str]) -> str:
        nonlocal index
        value = values[index] if index < len(values) else ""
        index += 1
        return value

    rendered = re.sub(r"\$[^$]+\$", replace, template or "")
    return _clean_mail_text(rendered)


def _mail_content_from_system_message(
    mail_vo: dict[str, Any],
    envelope: dict[str, Any],
    *,
    export_root: str | Path | None = None,
    item_name_index: dict[str, dict[str, Any]] | None = None,
) -> str:
    try:
        content_id = int(envelope.get("contentId") or 0)
    except (TypeError, ValueError):
        return ""
    if content_id <= 0:
        return ""
    message = _load_system_message_rows(export_root).get(content_id) or {}
    template = str(message.get("text_plain") or message.get("text") or "").strip()
    if not template:
        return ""
    values = _mail_i18n_template_values(
        mail_vo,
        template,
        export_root=export_root,
        item_name_index=item_name_index,
    )
    return _render_system_message_template(template, values)


def _mail_content_from_vo(
    mail_vo: dict[str, Any],
    envelopes: dict[int, dict[str, Any]],
    *,
    title: str = "",
    export_root: str | Path | None = None,
    item_name_index: dict[str, dict[str, Any]] | None = None,
) -> str:
    content = _clean_mail_text(mail_vo.get("content"))
    if content:
        return content
    try:
        mail_type = int(mail_vo.get("type"))
    except (TypeError, ValueError):
        mail_type = 0
    envelope = envelopes.get(mail_type) or {}
    for key in ("content_plain", "content", "desc_plain", "desc"):
        value = _clean_mail_text(envelope.get(key))
        if value:
            return value
    value = _mail_content_from_system_message(
        mail_vo,
        envelope,
        export_root=export_root,
        item_name_index=item_name_index,
    )
    if value:
        return value
    return _mail_content_fallback_from_params(
        title,
        mail_vo,
        export_root=export_root,
        item_name_index=item_name_index,
    )


def _resolve_envelope_config_path(export_root: str | Path | None = None) -> Path | None:
    root = resolve_fanxiu_export_root(export_root)
    candidates = [
        path
        for path in root.glob("by_source/lscripts/generate/cfg/envelope_*/text_assets/Envelope.lua")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _load_mail_envelope_rows_from_parsed_config(export_root: str | Path | None = None) -> list[dict[str, Any]]:
    root = resolve_fanxiu_export_root(export_root)
    rows_path = root / DEFAULT_PARSED_CONFIGS / "Envelope" / "rows.json"
    if not rows_path.is_file():
        return []
    try:
        rows = json.loads(rows_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def load_fanxiu_mail_envelope_titles(export_root: str | Path | None = None) -> dict[int, dict[str, Any]]:
    parsed_rows = _load_mail_envelope_rows_from_parsed_config(export_root)
    if parsed_rows:
        rows: dict[int, dict[str, Any]] = {}
        for row in parsed_rows:
            row_id = row.get("id", row.get("_row_key"))
            try:
                rows[int(row_id)] = row
            except (TypeError, ValueError):
                continue
        if rows:
            return rows

    envelope_path = _resolve_envelope_config_path(export_root)
    if not envelope_path:
        return {}
    lang_path = _find_default_lang_path(export_root)
    parsed = parse_fanxiu_generated_lua_config(envelope_path, lang_path=lang_path)
    rows: dict[int, dict[str, Any]] = {}
    for row in parsed.get("rows") or []:
        row_id = row.get("_row_key")
        if not isinstance(row_id, int):
            continue
        rows[row_id] = row
    return rows


def _mail_title_from_vo(mail_vo: dict[str, Any], envelopes: dict[int, dict[str, Any]]) -> str:
    title = str(mail_vo.get("title") or "").strip()
    if title:
        return title
    try:
        mail_type = int(mail_vo.get("type"))
    except (TypeError, ValueError):
        return ""
    envelope = envelopes.get(mail_type) or {}
    for key in ("title_plain", "title"):
        value = str(envelope.get(key) or "").strip()
        if value:
            return value
    override = MAIL_TYPE_TITLE_OVERRIDES.get(mail_type)
    if override:
        return override
    return ""


def _existing_mail_title_for_id(session: Session, mail_id: str) -> str:
    mail_id_text = str(mail_id or "").strip()
    if not mail_id_text:
        return ""
    existing = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == mail_id_text)).first()
    if not existing:
        return ""
    return str(existing.title or existing.normalized_title or "").strip()


def _existing_mail_title_for_type(session: Session, mail_type: Any) -> str:
    mail_type_text = str(mail_type or "").strip()
    if not mail_type_text:
        return ""
    records = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_type == mail_type_text)).all()
    titles: Counter[str] = Counter()
    for record in records:
        title = _clean_mail_text(record.title or record.normalized_title)
        if title and not re.fullmatch(r"未知邮件类型\d+", title):
            titles[title] += max(1, int(record.seen_count or 0))
    if not titles:
        return ""
    return titles.most_common(1)[0][0]


def _mail_title_fallback(mail_vo: dict[str, Any]) -> str:
    mail_type = str(mail_vo.get("type") or "").strip()
    return f"未知邮件类型{mail_type}" if mail_type else ""


def _parse_mail_time_text(value: Any) -> datetime | None:
    text = normalize_fanxiu_mail_time_text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y年%m月%d日%H:%M")
    except ValueError:
        return None


def _parse_capture_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _pcap_name_time(path: Path) -> datetime | None:
    match = re.search(r"(\d{8})_(\d{6})", path.name)
    if not match:
        return None
    try:
        return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _encode_lusuo_zigzag_varint(value: int) -> bytes:
    encoded = (int(value) << 1) ^ (int(value) >> 63)
    output = bytearray()
    while True:
        item = encoded & 0x7F
        encoded >>= 7
        if encoded:
            output.append(item | 0x80)
        else:
            output.append(item)
            break
    return bytes(output)


def _mail_id_raw_patterns(mail_id: str) -> list[bytes]:
    text = str(mail_id or "").strip()
    if not text:
        return []
    patterns = [text.encode("ascii", errors="ignore")]
    try:
        patterns.append(_encode_lusuo_zigzag_varint(int(text)))
    except ValueError:
        pass
    return [item for item in patterns if item]


def _scan_raw_pcaps_for_mail_ids(
    paths: list[Path],
    mail_ids: list[str],
    *,
    max_hits: int = 40,
) -> list[dict[str, Any]]:
    patterns = {
        mail_id: _mail_id_raw_patterns(mail_id)
        for mail_id in dict.fromkeys(str(item or "").strip() for item in mail_ids)
        if mail_id
    }
    if not patterns:
        return []
    hits: list[dict[str, Any]] = []
    for path in paths:
        try:
            data = path.read_bytes()
            stat = path.stat()
        except OSError:
            continue
        matched_ids: list[str] = []
        for mail_id, id_patterns in patterns.items():
            if any(pattern and data.find(pattern) >= 0 for pattern in id_patterns):
                matched_ids.append(mail_id)
        if not matched_ids:
            continue
        hits.append(
            {
                "name": path.name,
                "path": str(path),
                "size": stat.st_size,
                "mail_ids": matched_ids,
            }
        )
        if len(hits) >= max_hits:
            break
    return hits


def _iter_mail_vos(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    mail_vo = parsed.get("mailVo")
    if isinstance(mail_vo, dict):
        return [mail_vo]
    mail_vos = parsed.get("mailVos")
    return [item for item in _iter_list_items(mail_vos) if isinstance(item, dict)]


def _packet_evidence(source: dict[str, Any], frame: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "protocol": frame.get("name") or "",
        "direction": frame.get("direction") or "",
        "pro_id": frame.get("pro_id"),
        "sn": frame.get("sn"),
        "frame_index": index,
        "record_id": source.get("record_id") or "",
        "pcap_name": source.get("pcap_name") or "",
        "source_path": str(source.get("decoded_path") or ""),
    }


def trace_fanxiu_mail_packet_gap(
    session: Session,
    *,
    title: str,
    time_text: str,
    data_dir: str | Path | None = None,
    window_minutes: int = 8,
    max_sources: int = 24,
) -> dict[str, Any]:
    """Explain where a visible mail row disappears from the packet pipeline."""

    ensure_fanxiu_mail_table()
    normalized_title = normalize_fanxiu_mail_title(title)
    normalized_time = normalize_fanxiu_mail_time_text(time_text)
    target_dt = _parse_mail_time_text(normalized_time)
    window = max(1, int(window_minutes or 8))
    max_source_count = max(1, int(max_sources or 24))

    exact = session.exec(
        select(FanxiuMailRecord)
        .where(
            FanxiuMailRecord.source == "packet",
            FanxiuMailRecord.normalized_title == normalized_title,
            FanxiuMailRecord.create_time_text == normalized_time,
        )
        .limit(5)
    ).all()
    same_title = session.exec(
        select(FanxiuMailRecord)
        .where(FanxiuMailRecord.source == "packet", FanxiuMailRecord.normalized_title == normalized_title)
        .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
        .limit(5)
    ).all()
    same_time = session.exec(
        select(FanxiuMailRecord)
        .where(FanxiuMailRecord.source == "packet", FanxiuMailRecord.create_time_text == normalized_time)
        .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
        .limit(5)
    ).all()

    decoded_sources: list[dict[str, Any]] = []
    decoded_protocol_counts: Counter[str] = Counter()
    mail_protocol_frames = 0
    mail_source_protocol_frames = 0
    mail_action_protocol_frames = 0
    mail_lock_protocol_frames = 0
    unparsed_mail_protocol_frames = 0
    unknown_mail_protocol_frames = 0
    decoded_text_hits: list[dict[str, Any]] = []
    decoded_mail_action_ids: list[dict[str, Any]] = []
    if target_dt is not None:
        start = target_dt - timedelta(minutes=window)
        end = target_dt + timedelta(minutes=window)
        for source in _iter_fanxiu_tcp_decoded_sources(data_dir):
            captured_at = _parse_capture_time(_source_observed_at(source))
            if captured_at is None or captured_at < start or captured_at > end:
                continue
            decoded_path = Path(str(source.get("decoded_path") or ""))
            data = _load_json_file(decoded_path) or {}
            frames = data.get("frames") if isinstance(data, dict) else []
            frame_count = len(frames) if isinstance(frames, list) else 0
            source_mail_counts: Counter[str] = Counter()
            source_unparsed = 0
            source_unknown = 0
            for frame in frames if isinstance(frames, list) else []:
                if not isinstance(frame, dict):
                    continue
                name = str(frame.get("name") or "")
                try:
                    pro_id = int(frame.get("pro_id") or 0)
                except (TypeError, ValueError):
                    pro_id = 0
                if name in MAIL_ALL_PROTOCOLS or pro_id in MAIL_PROTOCOL_IDS:
                    mail_protocol_frames += 1
                    if name in MAIL_SOURCE_PROTOCOLS:
                        mail_source_protocol_frames += 1
                    elif name in MAIL_ACTION_PROTOCOLS:
                        mail_action_protocol_frames += 1
                    elif name in MAIL_LOCK_PROTOCOLS:
                        mail_lock_protocol_frames += 1
                    key = name or str(pro_id)
                    source_mail_counts[key] += 1
                    decoded_protocol_counts[key] += 1
                    if not isinstance(frame.get("parsed"), dict):
                        source_unparsed += 1
                        unparsed_mail_protocol_frames += 1
                    if name in MAIL_ACTION_PROTOCOLS and isinstance(frame.get("parsed"), dict):
                        for mail_id in _extract_mail_ids(frame.get("parsed") or {}):
                            if len(decoded_mail_action_ids) < 20:
                                decoded_mail_action_ids.append(
                                    {
                                        "id": mail_id,
                                        "protocol": name,
                                        "created_at": _source_observed_at(source),
                                        "pcap_name": source.get("pcap_name") or "",
                                    }
                                )
                if pro_id in MAIL_PROTOCOL_IDS and not name:
                    source_unknown += 1
                    unknown_mail_protocol_frames += 1
            hit_terms = []
            if decoded_path.is_file():
                try:
                    text = decoded_path.read_text(encoding="utf-8", errors="ignore")
                    hit_terms = [
                        term
                        for term in {normalized_title, title, normalized_time}
                        if term and term in text
                    ]
                except OSError:
                    hit_terms = []
            if hit_terms and len(decoded_text_hits) < 8:
                decoded_text_hits.append(
                    {
                        "created_at": _source_observed_at(source),
                        "pcap_name": source.get("pcap_name") or "",
                        "decoded_path": str(decoded_path),
                        "terms": hit_terms,
                    }
                )
            if len(decoded_sources) < max_source_count:
                decoded_sources.append(
                    {
                        "created_at": _source_observed_at(source),
                        "pcap_name": source.get("pcap_name") or "",
                        "source_kind": source.get("source_kind") or "",
                        "decoded_path": str(decoded_path),
                        "frame_count": frame_count,
                        "mail_protocol_counts": dict(source_mail_counts),
                        "unparsed_mail_protocol_frames": source_unparsed,
                        "unknown_mail_protocol_frames": source_unknown,
                    }
                )

    raw_pcaps: list[dict[str, Any]] = []
    raw_pcap_paths: list[Path] = []
    raw_day_pcap_paths: list[Path] = []
    if target_dt is not None:
        start = target_dt - timedelta(minutes=window)
        end = target_dt + timedelta(minutes=window)
        live_dir = resolve_fanxiu_tcp_live_capture_dir(data_dir)
        if live_dir.is_dir():
            for path in sorted(live_dir.glob("*.pcap"), key=lambda item: item.name):
                pcap_dt = _pcap_name_time(path)
                try:
                    stat = path.stat()
                except OSError:
                    continue
                mtime_dt = datetime.fromtimestamp(stat.st_mtime)
                observed_dt = pcap_dt or mtime_dt
                if observed_dt.date() == target_dt.date():
                    raw_day_pcap_paths.append(path)
                if observed_dt < start or observed_dt > end:
                    continue
                raw_pcap_paths.append(path)
                if len(raw_pcaps) < max_source_count:
                    raw_pcaps.append(
                        {
                            "name": path.name,
                            "path": str(path),
                            "size": stat.st_size,
                            "mtime": mtime_dt.strftime("%Y-%m-%d %H:%M:%S"),
                        }
                    )

    action_mail_ids = [str(item.get("id") or "") for item in decoded_mail_action_ids if isinstance(item, dict)]
    raw_action_id_hits = _scan_raw_pcaps_for_mail_ids(raw_pcap_paths, action_mail_ids)
    raw_action_id_day_hits = _scan_raw_pcaps_for_mail_ids(raw_day_pcap_paths, action_mail_ids)

    if exact:
        diagnosis = "packet_fact_exists"
    elif target_dt is None:
        diagnosis = "invalid_visible_mail_time"
    elif mail_source_protocol_frames:
        diagnosis = "mail_protocol_unparsed_or_unmatched" if unparsed_mail_protocol_frames else "mail_protocol_present_but_unmatched"
    elif mail_protocol_frames:
        diagnosis = "decoded_window_has_mail_actions_but_no_source_fact"
    elif decoded_sources:
        diagnosis = "decoded_window_has_no_mail_protocol"
    elif raw_pcaps:
        diagnosis = "raw_pcap_window_not_decoded"
    else:
        diagnosis = "no_capture_window"

    def _record_summary(record: FanxiuMailRecord) -> dict[str, Any]:
        evidence = record.evidence if isinstance(record.evidence, dict) else {}
        return {
            "title": record.title,
            "time_text": record.create_time_text,
            "mail_id": record.mail_id,
            "status": record.status,
            "action_policy": record.action_policy,
            "protocol": evidence.get("protocol") or "",
            "pcap_name": evidence.get("pcap_name") or "",
        }

    return {
        "title": title,
        "normalized_title": normalized_title,
        "time_text": normalized_time,
        "window_minutes": window,
        "diagnosis": diagnosis,
        "exact_records": [_record_summary(record) for record in exact],
        "same_title_records": [_record_summary(record) for record in same_title],
        "same_time_records": [_record_summary(record) for record in same_time],
        "decoded_source_count": len(decoded_sources),
        "decoded_sources": decoded_sources,
        "decoded_mail_protocol_counts": dict(decoded_protocol_counts),
        "mail_protocol_frames": mail_protocol_frames,
        "mail_source_protocol_frames": mail_source_protocol_frames,
        "mail_action_protocol_frames": mail_action_protocol_frames,
        "mail_lock_protocol_frames": mail_lock_protocol_frames,
        "unparsed_mail_protocol_frames": unparsed_mail_protocol_frames,
        "unknown_mail_protocol_frames": unknown_mail_protocol_frames,
        "decoded_mail_action_ids": decoded_mail_action_ids,
        "decoded_text_hits": decoded_text_hits,
        "raw_pcap_count": len(raw_pcaps),
        "raw_pcaps": raw_pcaps,
        "raw_action_id_hits": raw_action_id_hits,
        "raw_action_id_day_hits": raw_action_id_day_hits,
    }


def sync_fanxiu_mail_packets(
    session: Session,
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
    clear_existing: bool = False,
    decoded_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ensure_fanxiu_mail_table()
    deleted_existing = clear_fanxiu_mail_records(session) if clear_existing else 0
    if clear_existing:
        session.flush()

    root = resolve_fanxiu_export_root(export_root)
    envelopes = load_fanxiu_mail_envelope_titles(export_root)
    item_name_index = _load_mail_item_name_index(export_root=export_root, data_dir=data_dir)
    sources = decoded_sources if decoded_sources is not None else _iter_fanxiu_tcp_decoded_sources(data_dir)
    inserted = 0
    updated = 0
    source_packets = 0
    action_packets = 0
    orphan_action_packets = 0
    orphan_action_samples: list[dict[str, Any]] = []
    skipped_mail_vo = 0
    unknown_mail_protocol_packets = 0
    unparsed_mail_protocol_packets = 0
    unknown_mail_protocol_samples: list[dict[str, Any]] = []
    status_events: dict[str, list[dict[str, Any]]] = {}
    protocol_counts: Counter[str] = Counter()

    for source in reversed(sources):
        decoded_path = Path(str(source.get("decoded_path") or ""))
        data = _load_json_file(decoded_path) or {}
        frames = data.get("frames") or []
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict):
                continue
            name = str(frame.get("name") or "")
            try:
                pro_id = int(frame.get("pro_id") or 0)
            except (TypeError, ValueError):
                pro_id = 0
            if pro_id in MAIL_PROTOCOL_IDS and not name:
                unknown_mail_protocol_packets += 1
                if len(unknown_mail_protocol_samples) < 12:
                    unknown_mail_protocol_samples.append(_packet_evidence(source, frame, index))
            if name not in MAIL_SOURCE_PROTOCOLS and name not in MAIL_ACTION_PROTOCOLS and name not in MAIL_LOCK_PROTOCOLS:
                continue
            protocol_counts[name] += 1
            parsed = frame.get("parsed")
            if not isinstance(parsed, dict):
                if pro_id in MAIL_PROTOCOL_IDS:
                    unparsed_mail_protocol_packets += 1
                continue
            if name in MAIL_SOURCE_PROTOCOLS:
                source_packets += 1
                lock_ids = _extract_lock_ids(parsed) if name == "SM_MailBox" else set()
                for mail_vo in _iter_mail_vos(parsed):
                    mail_id = str(mail_vo.get("id") or "").strip()
                    title = (
                        _mail_title_from_vo(mail_vo, envelopes)
                        or _existing_mail_title_for_id(session, mail_id)
                        or _existing_mail_title_for_type(session, mail_vo.get("type"))
                        or _mail_title_fallback(mail_vo)
                    )
                    create_time_ms = mail_vo.get("createTime")
                    create_time_text = format_fanxiu_mail_time_ms(create_time_ms)
                    if not mail_id or not title or not create_time_text:
                        skipped_mail_vo += 1
                        continue
                    rewards = _normalize_mail_rewards(mail_vo, export_root, item_name_index)
                    content_text = _mail_content_from_vo(
                        mail_vo,
                        envelopes,
                        title=title,
                        export_root=export_root,
                        item_name_index=item_name_index,
                    )
                    if not content_text and rewards:
                        content_text = _mail_attachment_content_summary(rewards)
                    if not content_text:
                        content_text = _mail_empty_content_summary(title, mail_vo)
                    payload = {"mailVo": mail_vo}
                    if rewards:
                        payload["mail_rewards"] = rewards
                        payload["mail_rewards_summary"] = _mail_rewards_summary(rewards)
                    if content_text:
                        payload["mail_content_text"] = content_text
                    record, created = upsert_fanxiu_mail_fact(
                        session,
                        title=title,
                        mail_id=mail_id,
                        mail_type=str(mail_vo.get("type") or ""),
                        create_time_text=create_time_text,
                        create_time_ms=int(create_time_ms),
                        source="packet",
                        action_policy=fanxiu_mail_action_policy_for_rewards(rewards),
                        status=fanxiu_mail_desired_status_for_rewards(rewards),
                        locked=(mail_id in lock_ids) if name == "SM_MailBox" else None,
                        payload=payload,
                        evidence=_packet_evidence(source, frame, index),
                        seen_capture_at=_source_observed_at(source),
                    )
                    if created:
                        inserted += 1
                    else:
                        updated += 1
                    session.add(record)
            elif name in MAIL_ACTION_PROTOCOLS:
                action_packets += 1
                ids = _extract_mail_ids(parsed)
                if not ids:
                    continue
                status = _mail_action_status_from_protocol(name)
                evidence = _packet_evidence(source, frame, index)
                evidence["action_protocol"] = name
                evidence["action_observed_at"] = _source_observed_at(source)
                for mail_id in ids:
                    status_events.setdefault(mail_id, []).append({"status": status, "evidence": evidence})

    action_updated = 0
    for mail_id, events in status_events.items():
        record = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == mail_id)).first()
        if not record:
            orphan_action_packets += len(events)
            event = events[-1] if events else {}
            status = str(event.get("status") or "seen").strip() or "seen"
            evidence = dict(event.get("evidence") or {})
            evidence["orphan_action"] = True
            evidence["orphan_action_reason"] = "action packet observed before any decoded mail source fact"
            evidence["mail_actions"] = [item.get("evidence") for item in events[-8:] if isinstance(item, dict)]
            record, created = upsert_fanxiu_mail_fact(
                session,
                title=f"未知邮件动作{mail_id}",
                mail_id=mail_id,
                create_time_text="",
                source="packet_orphan_action",
                status=status,
                action_policy="",
                payload={
                    "orphan_action_status": status,
                    "mail_rewards_unresolved": True,
                    "mail_rewards_unresolved_reason": "action packet observed before any decoded MailVo rewards",
                },
                evidence=evidence,
                seen_capture_at=str(evidence.get("action_observed_at") or ""),
            )
            if created:
                inserted += 1
            else:
                updated += 1
            session.add(record)
            if len(orphan_action_samples) < 12:
                sample = dict(evidence)
                sample["mail_id"] = mail_id
                sample["status"] = status
                orphan_action_samples.append(sample)
            continue
        final = ""
        for candidate in ("claimed", "deleted", "seen"):
            if any(event.get("status") == candidate for event in events):
                final = candidate
                break
        if not final:
            continue
        evidence = dict(record.evidence or {})
        evidence["mail_actions"] = [event.get("evidence") for event in events[-8:]]
        if mark_fanxiu_mail_action(session, record.mail_key, status=final, evidence=evidence):
            action_updated += 1

    lock_updated = 0
    for source in reversed(sources):
        decoded_path = Path(str(source.get("decoded_path") or ""))
        data = _load_json_file(decoded_path) or {}
        frames = data.get("frames") or []
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict):
                continue
            name = str(frame.get("name") or "")
            if name not in MAIL_LOCK_PROTOCOLS:
                continue
            parsed = frame.get("parsed")
            if not isinstance(parsed, dict):
                continue
            mail_id = str(parsed.get("id") or "").strip()
            locked = _extract_bool(parsed.get("lock"))
            if not mail_id or locked is None:
                continue
            evidence = _packet_evidence(source, frame, index)
            evidence["lock_protocol"] = name
            evidence["lock_observed_at"] = _source_observed_at(source)
            if mark_fanxiu_mail_locked(session, mail_id, locked=locked, evidence=evidence):
                lock_updated += 1

    policy_updated = 0
    records = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.source == "packet")).all()
    for record in records:
        evidence = record.evidence if isinstance(record.evidence, dict) else {}
        runtime_action = str(evidence.get("runtime_action") or "")
        runtime_requested_action = str(evidence.get("runtime_requested_action") or "")
        record_status = str(record.status or "").strip().lower()
        if record_status in {"锁定", "留存", "可领"}:
            policy = fanxiu_mail_action_policy_for_record(record)
            if str(record.action_policy or "") == policy:
                continue
            record.action_policy = policy
            record.locked = record_status == "锁定"
            session.add(record)
            policy_updated += 1
            continue
        if runtime_action == "missing_from_list" and record_status not in {"claimed", "deleted", "missing_from_list"}:
            record.status = "missing_from_list"
            session.add(record)
        elif runtime_requested_action in {"claim", "delete"} and record_status not in {
            "claimed",
            "deleted",
            "claim_requested",
            "delete_requested",
            "missing_from_list",
        }:
            record.status = f"{runtime_requested_action}_requested"
            session.add(record)
        elif runtime_action in {"claim", "delete"} and record_status in {"claimed", "deleted"}:
            if not _mail_evidence_has_server_action(evidence, record_status):
                record.status = "seen"
                evidence["runtime_final_status_reverted_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                evidence["runtime_final_status_reverted_reason"] = "缺少服务端邮件动作回包证据"
                record.evidence = evidence
                session.add(record)
        policy = fanxiu_mail_action_policy_for_record(record)
        if str(record.action_policy or "") == policy:
            continue
        record.action_policy = policy
        session.add(record)
        policy_updated += 1

    session.commit()
    total_records = session.exec(select(FanxiuMailRecord)).all()
    action_policy_counts = Counter(str(record.action_policy or "").strip() for record in total_records)
    return {
        "ok": True,
        "cleared": deleted_existing,
        "inserted": inserted,
        "updated": updated,
        "action_updated": action_updated,
        "lock_updated": lock_updated,
        "policy_updated": policy_updated,
        "record_count": len(total_records),
        "source_count": len(sources),
        "source_packets": source_packets,
        "action_packets": action_packets,
        "orphan_action_packets": orphan_action_packets,
        "orphan_action_samples": orphan_action_samples,
        "skipped_mail_vo": skipped_mail_vo,
        "unknown_mail_protocol_packets": unknown_mail_protocol_packets,
        "unparsed_mail_protocol_packets": unparsed_mail_protocol_packets,
        "unknown_mail_protocol_samples": unknown_mail_protocol_samples,
        "envelope_count": len(envelopes),
        "export_root": str(root),
        "export_root_exists": root.exists(),
        "item_name_index_count": len(item_name_index),
        "item_name_source_counts": _mail_item_name_source_counts(item_name_index),
        "claim_policy_count": action_policy_counts.get("claim", 0),
        "delete_policy_count": action_policy_counts.get("delete", 0),
        "protocol_counts": dict(protocol_counts.most_common()),
        "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
