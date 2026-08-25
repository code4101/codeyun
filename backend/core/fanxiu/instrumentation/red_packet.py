from __future__ import annotations

import time
import threading
import json
from pathlib import Path
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    resolve_manager_root,
    table_ref,
)
from backend.core.temp_paths import codeyun_temp_root


_REDBAG_MARKER = b"LuaRedbagMgr"
_REDBAG_METHODS = frozenset(
    {
        "LuaRedbagMgr",
        "OpenRedBag",
        "OnGrabRedBagSuc",
        "Inst_get",
    }
)
_NPC_MARKER = b"LuaNpcMgr"
_NPC_METHODS = frozenset(
    {
        "LuaNpcMgr",
        "InitSingleton",
        "Inst_get",
    }
)
_ALLIANCEBASE_MARKER = b"LuaAlliancebaseMgr"
_ALLIANCEBASE_METHODS = frozenset(
    {
        "LuaAlliancebaseMgr",
        "GetMyAllianceChannelName",
        "Inst_get",
    }
)
_REDBAG_GLOBAL_NAME = "RedbagMgr"
_CHANNEL_CONTEXTS = {
    6: {
        "channel_key": "ALLIANCE",
        "channel_label": "宗门",
    },
}
# Current-version RedBag_RedBag rows proven from the shipped config bundle.
# These event packets have dedicated activity semantics, but a live #34 frame
# can still expose the ordinary orange chat red-packet marker for them.  They
# therefore authorize only the GUI Job's layered visual deep-check, never a
# claim action or a direct choice of group/card/control.
_SPECIAL_EVENT_CONFIGS: dict[int, dict[str, Any]] = {
    5022: {
        "event_type": 9033,
        "event_key": "qmch_reward",
        "daily_num_type": 1,
        "daily_num": -1,
        "receive_condition": "CL|10",
    },
}
_UNAVAILABLE_CACHE_TTL_SECONDS = 120.0
_unavailable_until: dict[tuple[str, int, int], float] = {}
_unavailable_lock = threading.Lock()


def _red_packet_snapshot_path() -> Path:
    return codeyun_temp_root("fanxiu-runtime-memory") / "red-packet-snapshot.json"


def _write_red_packet_snapshot(result: dict[str, Any]) -> None:
    path = _red_packet_snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "updated_at": time.time(),
                "snapshot": result,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def read_cached_red_packet_pending(
    *,
    max_age_seconds: float = 75.0,
) -> dict[str, Any]:
    """Return only an already-published snapshot; never inspect game memory."""

    try:
        payload = json.loads(_red_packet_snapshot_path().read_text(encoding="utf-8"))
        age_seconds = max(0.0, time.time() - float(payload.get("updated_at") or 0))
        snapshot = payload.get("snapshot")
        if (
            not isinstance(snapshot, dict)
            or age_seconds > max(0.0, float(max_age_seconds))
        ):
            raise ValueError("快照不存在或已过期")
        return {
            **snapshot,
            "snapshot_cache_hit": True,
            "snapshot_age_seconds": age_seconds,
        }
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_snapshot",
            "pending": False,
            "pending_count": 0,
            "items": [],
            "pending_groups": [],
            "reason": (
                "红包 Runtime 短时快照尚未生成或已过期；"
                "安全巡检不会现场扫描游戏内存"
            ),
            "snapshot_cache_hit": False,
        }


def _redbag_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager_fields = manager_index_fields(
        reader,
        root_address,
        _REDBAG_METHODS,
    )
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    data_fields = reader.fields(model_fields.get("RedbagData"))
    required = {
        "_RedBagList",
        "_RedBagDetailDic",
        "_UserGrabRedBagDic",
        "_HasOverdueUidDic",
        "_BeLimitRedBagIdDic",
    }
    if not required.issubset(data_fields):
        raise FanxiuRuntimeMemoryError(
            "RedbagMgr RedbagData 尚未初始化",
            code="data_not_loaded",
        )
    return data_fields


def _redbag_data_address(
    reader: LuaJitReader,
    root_address: int,
) -> int:
    manager_fields = manager_index_fields(
        reader,
        root_address,
        _REDBAG_METHODS,
    )
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    data_ref = table_ref(model_fields.get("RedbagData"))
    if data_ref is None:
        raise FanxiuRuntimeMemoryError(
            "RedbagMgr RedbagData 尚未初始化",
            code="data_not_loaded",
        )
    return data_ref.address


def _main_lua_state_address(memory: MumuProcessMemory) -> int:
    from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
        _lua_addresses,
    )

    return int(_lua_addresses(memory)["state"], 16)


def _resolve_redbag_root(
    memory: MumuProcessMemory,
    *,
    allow_discovery: bool,
) -> tuple[int, bool, str]:
    """Resolve the loaded RedbagMgr global before bounded marker fallback."""

    try:
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="chat",
            state_address=_main_lua_state_address(memory),
            global_name=_REDBAG_GLOBAL_NAME,
            required_methods=_REDBAG_METHODS,
            validate=lambda reader, address: _redbag_data_fields(
                reader,
                address,
            ),
        )
        return root, cache_hit, "lua_global"
    except FanxiuRuntimeMemoryError as exc:
        # A proven Manager whose model has not naturally loaded is not an
        # address-discovery failure. Heap scanning cannot load its data and
        # would hide the precise state behind a slow manager-not-found error.
        if exc.code == "data_not_loaded":
            raise
    root, cache_hit = resolve_manager_root(
        memory,
        manager_key="chat",
        marker=_REDBAG_MARKER,
        required_methods=_REDBAG_METHODS,
        validate=lambda reader, address: _redbag_data_fields(
            reader,
            address,
        ),
        allow_discovery=allow_discovery,
    )
    return root, cache_hit, "constructor_marker"


def _value_identity(reader: LuaJitReader, value: Any) -> int | float | str | None:
    long_value = reader.long(value)
    if long_value is not None:
        return long_value
    if isinstance(value, (int, float, str)):
        return value
    return None


def _dictionary_identities(
    reader: LuaJitReader,
    value: Any,
) -> set[int | float | str]:
    result: set[int | float | str] = set()
    for key in reader.dictionary_fields(value):
        identity = _value_identity(reader, key)
        if identity is not None:
            result.add(identity)
    return result


def _dictionary_by_identity(
    reader: LuaJitReader,
    value: Any,
) -> dict[int | float | str, Any]:
    result: dict[int | float | str, Any] = {}
    for key, item in reader.dictionary_fields(value).items():
        identity = _value_identity(reader, key)
        if identity is not None:
            result[identity] = item
    return result


def _npc_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager_fields = manager_index_fields(reader, root_address, _NPC_METHODS)
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    data_fields = reader.fields(model_fields.get("NpcData"))
    if "_NpcRedPacketDic" not in data_fields:
        raise FanxiuRuntimeMemoryError("NpcMgr NpcData 红包字段不完整")
    return data_fields


def _npc_data_address(
    reader: LuaJitReader,
    root_address: int,
) -> int:
    manager_fields = manager_index_fields(reader, root_address, _NPC_METHODS)
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    data_ref = table_ref(model_fields.get("NpcData"))
    if data_ref is None:
        raise FanxiuRuntimeMemoryError("NpcMgr 缺少 NpcData")
    return data_ref.address


def _data_address_cache_path(key: str) -> Path:
    return codeyun_temp_root("fanxiu-runtime-memory") / f"{key}-data-root.json"


def _read_cached_data_address(
    memory: MumuProcessMemory,
    key: str,
) -> int | None:
    try:
        payload = json.loads(
            _data_address_cache_path(key).read_text(encoding="utf-8")
        )
        if (
            int(payload.get("pid") or 0) != memory.pid
            or int(payload.get("process_start_ticks") or 0)
            != memory.process_start_ticks
        ):
            return None
        address = int(payload.get("data_address") or 0)
        return address or None
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_cached_data_address(
    memory: MumuProcessMemory,
    key: str,
    data_address: int,
) -> None:
    path = _data_address_cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "data_address": int(data_address),
                "updated_at": time.time(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _discard_cached_data_address(key: str) -> None:
    _data_address_cache_path(key).unlink(missing_ok=True)


def _alliancebase_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager_fields = manager_index_fields(
        reader,
        root_address,
        _ALLIANCEBASE_METHODS,
    )
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    data_fields = reader.fields(model_fields.get("data"))
    if "curAllianceInfo" not in data_fields:
        raise FanxiuRuntimeMemoryError("AlliancebaseMgr data.curAllianceInfo 字段不完整")
    return data_fields


def _alliancebase_channel_context(
    memory: MumuProcessMemory,
    *,
    allow_discovery: bool,
) -> dict[str, Any]:
    root_address, cache_hit = resolve_manager_root(
        memory,
        manager_key="alliancebase",
        marker=_ALLIANCEBASE_MARKER,
        required_methods=_ALLIANCEBASE_METHODS,
        validate=lambda reader, root: _alliancebase_data_fields(reader, root),
        allow_discovery=allow_discovery,
    )
    reader = LuaJitReader(memory)
    data_fields = _alliancebase_data_fields(reader, root_address)
    alliance_fields = reader.fields(data_fields.get("curAllianceInfo"))
    channel_name = str(alliance_fields.get("channelName") or "").strip()
    if not channel_name:
        raise FanxiuRuntimeMemoryError(
            "AlliancebaseMgr curAllianceInfo.channelName 为空"
        )
    return {
        "channel": 6,
        "channel_key": "ALLIANCE",
        "channel_label": "宗门",
        "target_name": channel_name,
        "evidence": {
            "protocol": "AlliancebaseMgr.Model.data.curAllianceInfo.channelName",
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": cache_hit,
        },
    }


def _pending_group_contexts(
    items: list[dict[str, Any]],
    channel_contexts: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Group Runtime packets by the game's own channel routing key."""

    contexts = channel_contexts or {}
    groups: dict[tuple[int | None, int | None], dict[str, Any]] = {}
    for item in items:
        channel = as_int(item.get("channel"))
        sub_channel_id = as_int(item.get("sub_channel_id"))
        key = (channel, sub_channel_id)
        group = groups.setdefault(
            key,
            {
                "channel": channel,
                "sub_channel_id": sub_channel_id,
                "group_key": f"{channel}_{sub_channel_id}",
                "pending_count": 0,
                "packet_ids": [],
                "packet_uids": [],
                "sender_names": [],
            },
        )
        group["pending_count"] += 1
        if item.get("id") is not None:
            group["packet_ids"].append(item["id"])
        if item.get("uid") is not None:
            group["packet_uids"].append(item["uid"])
        sender_name = str(item.get("sender_name") or "").strip()
        if sender_name and sender_name not in group["sender_names"]:
            group["sender_names"].append(sender_name)

    result: list[dict[str, Any]] = []
    for (channel, _sub_channel_id), group in groups.items():
        static_context = _CHANNEL_CONTEXTS.get(channel or -1, {})
        live_context = contexts.get(channel or -1, {})
        group.update(static_context)
        group.update(
            {
                key: value
                for key, value in live_context.items()
                if key != "evidence"
            }
        )
        channel_label = str(group.get("channel_label") or "").strip()
        target_name = str(group.get("target_name") or "").strip()
        group["display_name"] = " / ".join(
            value for value in (channel_label, target_name) if value
        )
        result.append(group)
    return sorted(
        result,
        key=lambda item: (
            -int(item.get("pending_count") or 0),
            str(item.get("group_key") or ""),
        ),
    )


def _npc_snapshot(
    memory: MumuProcessMemory,
    data_address: int,
    *,
    cache_hit: bool,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data = reader.table(data_address)["fields"]
    if "_NpcRedPacketDic" not in data:
        raise FanxiuRuntimeMemoryError("NpcMgr NpcData 红包字段不完整")
    npc_packets = reader.dictionary_fields(data.get("_NpcRedPacketDic"))
    count = sum(
        len(reader.dictionary_fields(packet_map))
        for packet_map in npc_packets.values()
    )
    return {
        "ok": True,
        "available": True,
        "source": "runtime_memory",
        "protocol": "NpcMgr.Model.NpcData._NpcRedPacketDic",
        "pending": count > 0,
        "pending_count": count,
        "npc_count": len(npc_packets),
        "evidence": {
            "data_address": f"0x{data_address:x}",
            "data_cache_hit": cache_hit,
        },
    }


def _snapshot(
    memory: MumuProcessMemory,
    data_address: int,
    *,
    cache_hit: bool,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data = reader.table(data_address)["fields"]
    required = {
        "_RedBagList",
        "_RedBagDetailDic",
        "_UserGrabRedBagDic",
        "_HasOverdueUidDic",
        "_BeLimitRedBagIdDic",
    }
    if not required.issubset(data):
        raise FanxiuRuntimeMemoryError("RedbagMgr RedbagData 字段不完整")
    bags, declared_count = reader.list_items(data.get("_RedBagList"))
    rewarded = _dictionary_identities(reader, data.get("_UserGrabRedBagDic"))
    overdue = _dictionary_identities(reader, data.get("_HasOverdueUidDic"))
    limited = _dictionary_identities(reader, data.get("_BeLimitRedBagIdDic"))
    details = _dictionary_by_identity(reader, data.get("_RedBagDetailDic"))

    receive_queue_loaded = "_ReceiveRedBagList" in data
    receive_queue_items: list[Any] = []
    receive_queue_count: int | None = None
    if receive_queue_loaded:
        receive_queue_items, receive_queue_count = reader.list_items(
            data.get("_ReceiveRedBagList")
        )
    id_independent_map_loaded = "_idIndependentMap" in data
    event_map_loaded = "_eventMap" in data
    id_independent_map = (
        reader.dictionary_fields(data.get("_idIndependentMap"))
        if id_independent_map_loaded
        else {}
    )
    event_map = (
        reader.dictionary_fields(data.get("_eventMap"))
        if event_map_loaded
        else {}
    )

    pending: list[dict[str, Any]] = []
    structural_items: list[dict[str, Any]] = []
    special_event_items: list[dict[str, Any]] = []
    semantic_incomplete_reasons: list[str] = []
    claimability_unknown_reasons: list[str] = []
    rejected_count = 0
    for bag in bags:
        fields = reader.fields(bag)
        sender_fields = reader.fields(fields.get("senderVO"))
        uid = _value_identity(reader, fields.get("uid"))
        bag_id = as_int(fields.get("id"))
        detail_loaded = uid is not None and uid in details
        detail_fields = reader.fields(details.get(uid)) if detail_loaded else {}
        is_rewarded = detail_fields.get("isReward")
        total = as_int(detail_fields.get("num"))
        received = as_int(detail_fields.get("receiveNum"))
        item = {
            "uid": uid,
            "id": bag_id,
            "channel": as_int(fields.get("channel")),
            "sub_channel_id": as_int(fields.get("subChannelId")),
            "sender_name": str(
                sender_fields.get("name")
                or fields.get("senderName")
                or ""
            ),
            "sender_id": as_int(sender_fields.get("id")),
            # endTimeStamp is a Lua long wrapper, not a scalar TValue.
            "end_time_epoch_ms": reader.long(fields.get("endTimeStamp")),
            "total": total,
            "received": received,
            "detail_loaded": detail_loaded,
            "config_loaded": False,
            "trigger_candidate": False,
            "claimability": "unknown",
            "action_authorized": False,
            "exclusion_reasons": [],
            "claimability_unknown_reasons": [],
        }
        # Structural projection is a lossless view of the decoded server list.
        # Business filters below may classify an item, but must never erase the
        # fact that the item existed in _RedBagList.
        structural_items.append(item)

        if uid is None:
            rejected_count += 1
            item["exclusion_reasons"].append("missing_uid")
            semantic_incomplete_reasons.append("redbag_item_missing_uid")
            continue
        if uid in rewarded:
            item["exclusion_reasons"].append("server_rewarded")
        if uid in overdue:
            item["exclusion_reasons"].append("server_overdue")
        if uid in limited:
            item["exclusion_reasons"].append("server_limited")
        if is_rewarded is True:
            item["exclusion_reasons"].append("detail_rewarded")
        if total is not None and received is not None and received >= total:
            item["exclusion_reasons"].append("detail_full")

        special_config = _SPECIAL_EVENT_CONFIGS.get(bag_id or -1)
        if special_config is not None:
            end_time = item["end_time_epoch_ms"]
            item["config_loaded"] = True
            expired = end_time is not None and end_time <= int(time.time() * 1000)
            if expired:
                item["claimability"] = "definitively_excluded"
                item["exclusion_reasons"].append("special_event_expired")
            else:
                item["trigger_candidate"] = True
                item["claimability"] = "visual_deep_check_required"
                item["claimability_unknown_reasons"].append(
                    "special_event_visual_state_required"
                )
            special_item = {
                **item,
                **special_config,
                "daily_chat_actionable": False,
                "classification": (
                    "special_event_expired"
                    if expired
                    else "special_event_gui_deep_check_candidate"
                ),
            }
            special_event_items.append(special_item)
            if not expired:
                pending.append(special_item)
                claimability_unknown_reasons.append(
                    "special_event_visual_state_required"
                )
                semantic_incomplete_reasons.append(
                    "special_event_visual_state_required"
                )
            continue

        if item["exclusion_reasons"]:
            item["claimability"] = "definitively_excluded"
            continue

        # Reproducing HasReward(id) requires the exact RedBag config row,
        # GameUtil.CheckCondition(receiveCondition), and the matching
        # _idIndependentMap/_eventMap counter.  Structural routing fields are
        # not a substitute.  Until a current-version adapter covers this id,
        # keep the row diagnostic-only and make the trigger fail closed.
        unknown_reasons = [f"redbag_config_or_condition_uncovered:{bag_id}"]
        if not detail_loaded:
            unknown_reasons.append(f"redbag_detail_not_loaded:{uid}")
        item["claimability_unknown_reasons"].extend(unknown_reasons)
        claimability_unknown_reasons.extend(unknown_reasons)
        semantic_incomplete_reasons.extend(unknown_reasons)
        # An uncovered claimability rule is not evidence that the server item
        # does not exist.  It is a safe positive trigger for the GUI Job's own
        # current-frame guards, while never authorizing a claim action here.
        item["trigger_candidate"] = True
        pending.append(item)

    if not receive_queue_loaded:
        semantic_incomplete_reasons.append("receive_redbag_queue_not_loaded")
    elif receive_queue_count is None:
        semantic_incomplete_reasons.append("receive_redbag_queue_count_unknown")
    elif receive_queue_count > 0:
        semantic_incomplete_reasons.append(
            f"receive_redbag_queue_in_transition:{receive_queue_count}"
        )
    if not id_independent_map_loaded:
        semantic_incomplete_reasons.append("id_independent_map_not_loaded")
    if not event_map_loaded:
        semantic_incomplete_reasons.append("event_map_not_loaded")

    main_ui_items, main_ui_count = reader.list_items(
        data.get("_MainUiRedBagShowList")
    )
    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    complete = declared_count is not None and len(bags) == declared_count
    trigger_complete = complete and all(
        item.get("uid") is not None
        and item.get("channel") is not None
        and item.get("sub_channel_id") is not None
        for item in structural_items
    )
    claimability_complete = (
        complete
        and receive_queue_loaded
        and receive_queue_count == 0
        and id_independent_map_loaded
        and event_map_loaded
        and not claimability_unknown_reasons
        and not any(
            "missing_uid" in item.get("exclusion_reasons", [])
            for item in structural_items
        )
    )
    semantic_complete = complete and not semantic_incomplete_reasons
    transitional_count = int(receive_queue_count or 0)
    pending_count = len(pending) + transitional_count
    return {
        "ok": complete,
        "available": True,
        "source": "runtime_memory",
        "protocol": "RedbagMgr.Model.RedbagData",
        "pending": pending_count > 0,
        "pending_count": pending_count,
        "items": pending,
        "structural_items": structural_items,
        "special_event_items": special_event_items,
        "declared_count": declared_count,
        "decoded_count": len(bags),
        "rejected_count": rejected_count,
        "trigger_candidate_count": len(pending),
        "receive_queue_loaded": receive_queue_loaded,
        "receive_queue_count": receive_queue_count,
        "receive_queue_decoded_count": len(receive_queue_items),
        "receive_queue_in_transition": transitional_count > 0,
        "manager_maps": {
            "id_independent_map_loaded": id_independent_map_loaded,
            "id_independent_map_count": len(id_independent_map),
            "event_map_loaded": event_map_loaded,
            "event_map_count": len(event_map),
        },
        "main_ui_queue_count": (
            main_ui_count if main_ui_count is not None else len(main_ui_items)
        ),
        "complete": complete,
        "trigger_complete": trigger_complete,
        "claimability_complete": claimability_complete,
        "semantic_complete": semantic_complete,
        "semantic_incomplete_reasons": sorted(set(semantic_incomplete_reasons)),
        "claimability_unknown_reasons": sorted(
            set(claimability_unknown_reasons)
        ),
        "action_authorized": False,
        "semantics": "daily_chat_gui_deep_check_candidates",
        "captured_at": captured_at,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "data_address": f"0x{data_address:x}",
            "data_cache_hit": cache_hit,
            "read_at": captured_at,
        },
    }


def _aggregate_sources(
    sources: dict[str, dict[str, Any]],
    errors: dict[str, str],
    *,
    memory: MumuProcessMemory,
    started_at: float,
    manager_loads: dict[str, dict[str, Any]] | None = None,
    channel_contexts: dict[int, dict[str, Any]] | None = None,
    context_errors: dict[str, str] | None = None,
    source_error_codes: dict[str, str] | None = None,
    required_sources: frozenset[str] = frozenset({"chat", "npc"}),
) -> dict[str, Any]:
    complete = required_sources.issubset(sources)
    pending_count = sum(
        int(source.get("pending_count") or 0)
        for source in sources.values()
    )
    chat_items = sources.get("chat", {}).get("items", [])
    return {
        # A missing source is not a negative observation.  In particular,
        # NpcData=0 cannot prove that RedbagData also has no packets.
        "ok": complete and all(
            source.get("ok") for source in sources.values()
        ),
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "pending": pending_count > 0,
        "pending_count": pending_count,
        "items": chat_items,
        "pending_groups": _pending_group_contexts(
            chat_items,
            channel_contexts,
        ),
        "sources": sources,
        "unavailable_sources": errors,
        "unavailable_source_codes": source_error_codes or {},
        "context_errors": context_errors or {},
        "manager_loads": manager_loads or {},
        "reason": (
            ""
            if complete
            else "红包 Runtime 数据源不完整，不能判定当前没有红包"
        ),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
        },
        "elapsed_seconds": time.perf_counter() - started_at,
    }


def read_red_packet_pending(
    *,
    allow_discovery: bool = True,
    allow_runtime_initialization: bool = False,
    unavailable_cache_ttl_seconds: float = _UNAVAILABLE_CACHE_TTL_SECONDS,
    chat_only: bool = False,
) -> dict[str, Any]:
    """Read local red-packet candidates without a Kernel, Cell, or game action."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = (
            MumuProcessMemory.discover()
            if allow_discovery
            else MumuProcessMemory.discover_cached(
                max_age_seconds=None,
                fallback_to_discovery=False,
            )
        )
        sources: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}
        source_error_codes: dict[str, str] = {}
        manager_loads: dict[str, dict[str, Any]] = {}
        source_specs = (
            (
                "chat",
                _REDBAG_MARKER,
                _REDBAG_METHODS,
                _redbag_data_fields,
                _redbag_data_address,
                _snapshot,
            ),
            (
                "npc",
                _NPC_MARKER,
                _NPC_METHODS,
                _npc_data_fields,
                _npc_data_address,
                _npc_snapshot,
            ),
        )
        if chat_only:
            source_specs = source_specs[:1]
        for key, marker, methods, validator, data_address_getter, snapshotter in source_specs:
            unavailable_key = (
                key,
                memory.pid,
                memory.process_start_ticks,
            )
            with _unavailable_lock:
                retry_at = _unavailable_until.get(unavailable_key, 0.0)
            if retry_at > time.monotonic():
                errors[key] = "Runtime 管理器尚未加载（短期缓存）"
                continue
            try:
                data_address = _read_cached_data_address(memory, key)
                data_cache_hit = data_address is not None
                if data_address is None:
                    if not allow_discovery:
                        raise FanxiuRuntimeMemoryError(
                            f"{key} 红包数据地址缓存尚未预热"
                        )
                    if key == "chat":
                        root_address, _root_cache_hit, _manager_resolver = (
                            _resolve_redbag_root(
                                memory,
                                allow_discovery=allow_discovery,
                            )
                        )
                    else:
                        root_address, _root_cache_hit = resolve_manager_root(
                            memory,
                            manager_key=key,
                            marker=marker,
                            required_methods=methods,
                            validate=lambda reader, root, fn=validator: fn(reader, root),
                            allow_discovery=allow_discovery,
                        )
                    data_address = data_address_getter(
                        LuaJitReader(memory),
                        root_address,
                    )
                    _write_cached_data_address(memory, key, data_address)
                try:
                    sources[key] = snapshotter(
                        memory,
                        data_address,
                        cache_hit=data_cache_hit,
                    )
                except Exception:
                    _discard_cached_data_address(key)
                    raise
                with _unavailable_lock:
                    _unavailable_until.pop(unavailable_key, None)
            except Exception as exc:
                reason = str(exc)
                error_code = (
                    exc.code
                    if isinstance(exc, FanxiuRuntimeMemoryError)
                    else ""
                )
                if (
                    key == "chat"
                    and "Runtime 根" in reason
                    and allow_runtime_initialization
                ):
                    from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
                        ensure_redbag_runtime_manager,
                    )

                    load_result = ensure_redbag_runtime_manager()
                    manager_loads[key] = load_result
                    if load_result.get("ok"):
                        try:
                            root_address, _root_cache_hit = resolve_manager_root(
                                memory,
                                manager_key=key,
                                marker=marker,
                                required_methods=methods,
                                validate=lambda reader, root, fn=validator: fn(
                                    reader,
                                    root,
                                ),
                                allow_discovery=allow_discovery,
                            )
                            data_address = data_address_getter(
                                LuaJitReader(memory),
                                root_address,
                            )
                            _write_cached_data_address(
                                memory,
                                key,
                                data_address,
                            )
                            sources[key] = snapshotter(
                                memory,
                                data_address,
                                cache_hit=False,
                            )
                            with _unavailable_lock:
                                _unavailable_until.pop(unavailable_key, None)
                            continue
                        except Exception as retry_exc:
                            reason = (
                                "RedbagMgr 已请求初始化，但重新定位失败："
                                f"{retry_exc}"
                            )
                    else:
                        reason = (
                            f"{reason}；自动初始化失败："
                            f"{load_result.get('reason') or '未知原因'}"
                        )
                errors[key] = reason
                if error_code:
                    source_error_codes[key] = error_code
                if allow_discovery:
                    with _unavailable_lock:
                        _unavailable_until[unavailable_key] = (
                            time.monotonic()
                            + max(
                                _UNAVAILABLE_CACHE_TTL_SECONDS,
                                float(unavailable_cache_ttl_seconds),
                            )
                        )
        if not sources:
            raise FanxiuRuntimeMemoryError(
                "；".join(errors.values()) or "红包 Runtime 数据尚未加载",
                code=(
                    "data_not_loaded"
                    if source_error_codes.get("chat") == "data_not_loaded"
                    else "manager_not_found"
                ),
            )
        channel_contexts: dict[int, dict[str, Any]] = {}
        context_errors: dict[str, str] = {}
        chat_items = sources.get("chat", {}).get("items", [])
        if any(as_int(item.get("channel")) == 6 for item in chat_items):
            try:
                channel_contexts[6] = _alliancebase_channel_context(
                    memory,
                    allow_discovery=allow_discovery,
                )
            except Exception as exc:
                # Context improves navigation, but it is not evidence that a
                # positive RedbagMgr observation is false.
                context_errors["alliancebase"] = str(exc)
        result = _aggregate_sources(
            sources,
            errors,
            memory=memory,
            started_at=started_at,
            manager_loads=manager_loads,
            channel_contexts=channel_contexts,
            context_errors=context_errors,
            source_error_codes=source_error_codes,
            required_sources=frozenset({"chat"}) if chat_only else frozenset({"chat", "npc"}),
        )
        _write_red_packet_snapshot(result)
        return result
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, FanxiuRuntimeMemoryError)
            else f"{type(exc).__name__}: {exc}"
        )
        return {
            "ok": False,
            "available": False,
            "source": "runtime_memory",
            "pending": False,
            "pending_count": 0,
            "items": [],
            "reason": reason,
            "error_code": (
                exc.code
                if isinstance(exc, FanxiuRuntimeMemoryError)
                else "unexpected_error"
            ),
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
            },
            "elapsed_seconds": time.perf_counter() - started_at,
        }


def read_cached_chat_red_packet_pending() -> dict[str, Any]:
    """Read only the cached chat-redbag tables for the patrol hot path.

    NPC red packets are a separate feature and their nested dictionary can be
    large. Traversing it on every chat-redbag patrol made a warmed read take
    more than a second, so only explicit recovery/diagnostics use the combined
    reader above.
    """

    started_at = time.perf_counter()
    try:
        memory = MumuProcessMemory.discover_cached(
            max_age_seconds=None,
            fallback_to_discovery=False,
        )
        data_address = _read_cached_data_address(memory, "chat")
        if data_address is None:
            raise FanxiuRuntimeMemoryError("chat 红包数据地址缓存尚未预热")
        chat = _snapshot(memory, data_address, cache_hit=True)
        items = list(chat.get("items") or [])
        result = {
            "ok": bool(chat.get("ok")),
            "available": True,
            "complete": bool(chat.get("complete")),
            "source": "runtime_memory_chat_hot_path",
            "pending": bool(chat.get("pending")),
            "pending_count": int(chat.get("pending_count") or 0),
            "items": items,
            "pending_groups": _pending_group_contexts(items),
            "sources": {"chat": chat},
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
            },
            "elapsed_seconds": time.perf_counter() - started_at,
        }
        _write_red_packet_snapshot(result)
        return result
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory_chat_hot_path",
            "pending": False,
            "pending_count": 0,
            "items": [],
            "reason": str(exc),
            "elapsed_seconds": time.perf_counter() - started_at,
        }
