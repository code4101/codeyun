from __future__ import annotations

from collections import Counter
from typing import Any

from backend.core.fanxiu.instrumentation.red_packet import _main_lua_state_address
from backend.core.fanxiu.instrumentation.runtime_memory import (
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
)


_CHAT_MANAGER_METHODS = frozenset({"LuaChatMgr", "Inst_get", "SendChat"})


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
