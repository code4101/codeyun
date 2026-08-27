from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
import re
from typing import Any

from backend.core.fanxiu.instrumentation.red_packet import _main_lua_state_address
from backend.core.fanxiu.instrumentation.runtime_memory import (
    LuaRef,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
)


_CHAT_MANAGER_METHODS = frozenset({"LuaChatMgr", "Inst_get", "SendChat"})
_DB_MANAGER_METHODS = frozenset(
    {"DBMgr", "GetConfigTable", "GetConfigTableByIdWithLog", "Inst_get"}
)
_CHAT_GROUP_TAB_LABELS = {
    -1: "全部",
    1: "活动",
    2: "群聊",
    3: "私聊",
    4: "系统",
}


def select_chat_channel_route(
    channel: int,
    channel_rows: Iterable[Mapping[str, Any]],
    group_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve one chat tab from Runtime config facts, never from GUI badges."""

    matching_channels = [
        dict(row)
        for row in channel_rows
        if as_int(row.get("id")) == int(channel)
    ]
    if len(matching_channels) != 1:
        raise RuntimeError(
            f"Runtime Chat.Chat channel={int(channel)} 配置不唯一：{len(matching_channels)}"
        )
    group_type = as_int(matching_channels[0].get("groupType"))
    if group_type is None:
        raise RuntimeError(f"Runtime Chat.Chat channel={int(channel)} 缺少 groupType")
    matching_groups = [
        dict(row)
        for row in group_rows
        if as_int(row.get("type")) == group_type
    ]
    if len(matching_groups) != 1:
        raise RuntimeError(
            f"Runtime Chat.ChatGroup type={group_type} 配置不唯一：{len(matching_groups)}"
        )
    tab_label = _CHAT_GROUP_TAB_LABELS.get(group_type)
    if not tab_label:
        raise RuntimeError(f"Runtime chat groupType={group_type} 没有受支持的 GUI Tab")
    group = matching_groups[0]
    return {
        "channel": int(channel),
        "group_type": group_type,
        "tab_label": tab_label,
        "sort": as_int(group.get("sort")),
        "name_id": as_int(group.get("name")),
    }


def _db_config_table(
    reader: LuaJitReader,
    root_address: int,
    table_name: str,
) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _DB_MANAGER_METHODS)
    instance = reader.fields(manager.get("inst"))
    configs = reader.dictionary_fields(instance.get("ConfigDic"))
    table = reader.fields(configs.get(table_name))
    if not table:
        raise RuntimeError(f"DBMgr.ConfigDic[{table_name!r}] 尚未自然加载")
    return table


def _config_indexes(
    reader: LuaJitReader,
    environment_address: int,
    group_name: str,
    table_name: str,
) -> dict[str, int]:
    environment = reader.string_fields(
        environment_address,
        frozenset({"s_globalCfgIdx"}),
    )
    root = environment.get("s_globalCfgIdx")
    group = reader.fields(reader.fields(root).get(group_name))
    indexes = reader.fields(group.get(table_name))
    result = {
        str(key): int(index)
        for key, index in indexes.items()
        if isinstance(key, str) and as_int(index) is not None
    }
    if not result:
        raise RuntimeError(
            f"s_globalCfgIdx[{group_name!r}][{table_name!r}] 尚未加载"
        )
    return result


def _packed_value(
    reader: LuaJitReader,
    raw: Any,
    indexes: Mapping[str, int],
    field: str,
) -> Any:
    direct = reader.fields(raw)
    if direct.get(field) is not None:
        return direct[field]
    if not isinstance(raw, LuaRef) or raw.kind != "table":
        return None
    index = indexes.get(field)
    array = list(reader.table(raw.address).get("array") or ())
    return array[index] if index is not None and 0 <= index < len(array) else None


def _decode_config_rows(
    reader: LuaJitReader,
    table: Mapping[Any, Any],
    indexes: Mapping[str, int],
    fields: Iterable[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_key, raw in table.items():
        row = {field: _packed_value(reader, raw, indexes, field) for field in fields}
        if row.get("id") is None:
            row["id"] = raw_key
        rows.append(row)
    return rows


def read_chat_channel_route(channel: int) -> dict[str, Any]:
    """Read the authoritative channel→group→tab route from live Runtime config."""

    memory = MumuProcessMemory.discover_cached(
        max_age_seconds=None,
        fallback_to_discovery=True,
    )
    reader = LuaJitReader(memory)
    state_address = _main_lua_state_address(memory)
    db_root, cache_hit, environment = resolve_lua_global_manager_root(
        memory,
        manager_key="chat-route-db",
        state_address=state_address,
        global_name="DBMgr",
        required_methods=_DB_MANAGER_METHODS,
        validate=lambda current_reader, address: _db_config_table(
            current_reader,
            address,
            "Chat.Chat",
        ),
    )
    channel_table = _db_config_table(reader, db_root, "Chat.Chat")
    group_table = _db_config_table(reader, db_root, "Chat.ChatGroup")
    channel_indexes = _config_indexes(reader, environment, "Chat", "Chat")
    group_indexes = _config_indexes(reader, environment, "Chat", "ChatGroup")
    route = select_chat_channel_route(
        int(channel),
        _decode_config_rows(
            reader,
            channel_table,
            channel_indexes,
            ("id", "groupType"),
        ),
        _decode_config_rows(
            reader,
            group_table,
            group_indexes,
            ("type", "sort", "name"),
        ),
    )
    return {
        **route,
        "available": True,
        "source": "DBMgr.ConfigDic[Chat.Chat/Chat.ChatGroup]",
        "manager_cache_hit": bool(cache_hit),
    }


def select_chat_row_anchors(message: Mapping[str, Any]) -> list[str]:
    """Build visible OCR anchors from one authoritative Runtime list preview."""

    raw_content = str(message.get("content") or message.get("chat_href") or "")
    visible_content = re.sub(r"<[^>]*>|\{[^}]*\}", " ", raw_content)
    pieces = re.findall(r"[\u3400-\u9fffA-Za-z0-9]{4,}", visible_content)
    sender = re.sub(r"\s+", "", str(message.get("sender_name") or ""))
    candidates = [
        *(piece[:14] for piece in pieces if len(piece) >= 4),
        sender[:14] if len(sender) >= 4 else "",
    ]
    return list(dict.fromkeys(item for item in candidates if item))


def read_chat_channel_gui_target(channel: int, sub_channel_id: int) -> dict[str, Any]:
    """Project an exact Runtime channel into facts usable for GUI row alignment."""

    route = read_chat_channel_route(channel)
    snapshot = read_chat_channel_messages(channel, sub_channel_id, max_messages=8)
    messages = list(snapshot.get("messages") or ())
    if not messages:
        raise RuntimeError(
            f"Runtime chat {int(channel)}_{int(sub_channel_id)} 没有列表预览消息"
        )
    latest = messages[-1]
    anchors = select_chat_row_anchors(latest)
    if not anchors:
        raise RuntimeError(
            f"Runtime chat {int(channel)}_{int(sub_channel_id)} 无法生成 GUI 对齐锚点"
        )
    return {
        **route,
        "sub_channel_id": int(sub_channel_id),
        "channel_key": f"{int(channel)}_{int(sub_channel_id)}",
        "anchors": anchors,
        "latest_message": latest,
        "source": (
            "ChatMgr.Model.ChatData._ChatDataDic + "
            "DBMgr.ConfigDic[Chat.Chat/Chat.ChatGroup]"
        ),
    }


def _chat_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _CHAT_MANAGER_METHODS)
    instance = reader.fields(manager.get("inst"))
    model = reader.fields(instance.get("Model"))
    chat_data = reader.fields(model.get("ChatData"))
    if "_ChatDataDic" not in chat_data:
        raise RuntimeError("ChatMgr.Model.ChatData 尚未初始化")
    return chat_data


def read_chat_channel_messages(
    channel: int,
    sub_channel_id: int,
    *,
    max_messages: int = 50,
) -> dict[str, Any]:
    """Read already-loaded messages for one exact Runtime chat channel."""

    memory = MumuProcessMemory.discover_cached(
        max_age_seconds=None,
        fallback_to_discovery=True,
    )
    reader = LuaJitReader(memory)
    root, cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key="chat-message",
        state_address=_main_lua_state_address(memory),
        global_name="ChatMgr",
        required_methods=_CHAT_MANAGER_METHODS,
        validate=lambda current_reader, address: _chat_data_fields(
            current_reader,
            address,
        ),
    )
    chat_data = _chat_data_fields(reader, root)
    channels = reader.dictionary_fields(chat_data.get("_ChatDataDic"))
    channel_key = f"{int(channel)}_{int(sub_channel_id)}"
    raw_items, declared_count = reader.list_items(channels.get(channel_key))
    limit = max(1, int(max_messages))
    messages: list[dict[str, Any]] = []
    for raw_item in raw_items[-limit:]:
        fields = reader.fields(raw_item)
        sender = reader.fields(fields.get("sender"))
        messages.append(
            {
                "content": str(fields.get("content") or ""),
                "chat_href": str(fields.get("chatHref") or ""),
                "light_chat_href": str(fields.get("lightChatHref") or ""),
                "content_type": as_int(fields.get("contentType")),
                "sender_name": str(sender.get("name") or ""),
                "create_time_epoch_ms": reader.long(fields.get("createTime")),
            }
        )
    return {
        "available": True,
        "source": "ChatMgr.Model.ChatData._ChatDataDic",
        "channel": int(channel),
        "sub_channel_id": int(sub_channel_id),
        "channel_key": channel_key,
        "declared_count": declared_count,
        "messages": messages,
        "manager_cache_hit": bool(cache_hit),
    }


def select_repeated_chat_phrase(
    messages: list[dict[str, Any]],
    *,
    min_repetitions: int = 3,
    min_agreement_ratio: float = 0.6,
) -> dict[str, Any]:
    """Select one dominant plain-text phrase from loaded Runtime messages."""

    candidates = [
        str(item.get("content") or item.get("chat_href") or "").strip()
        for item in messages
        if item.get("content_type") in (None, 0)
        and str(item.get("content") or item.get("chat_href") or "").strip()
    ]
    if not candidates:
        return {
            "ready": False,
            "reason": "channel_has_no_plain_text_messages",
            "phrase": "",
            "occurrences": 0,
            "candidate_count": 0,
            "agreement_ratio": 0.0,
        }
    phrase, occurrences = Counter(candidates).most_common(1)[0]
    ratio = occurrences / len(candidates)
    ready = bool(
        occurrences >= max(1, int(min_repetitions))
        and ratio >= max(0.0, min(1.0, float(min_agreement_ratio)))
    )
    return {
        "ready": ready,
        "reason": "dominant_repeated_phrase" if ready else "phrase_consensus_insufficient",
        "phrase": phrase if ready else "",
        "occurrences": occurrences,
        "candidate_count": len(candidates),
        "agreement_ratio": ratio,
    }


def read_repeated_chat_phrase(
    channel: int,
    sub_channel_id: int,
    *,
    max_messages: int = 50,
    min_repetitions: int = 3,
    min_agreement_ratio: float = 0.6,
) -> dict[str, Any]:
    snapshot = read_chat_channel_messages(
        channel,
        sub_channel_id,
        max_messages=max_messages,
    )
    consensus = select_repeated_chat_phrase(
        snapshot["messages"],
        min_repetitions=min_repetitions,
        min_agreement_ratio=min_agreement_ratio,
    )
    return {**snapshot, **consensus}
