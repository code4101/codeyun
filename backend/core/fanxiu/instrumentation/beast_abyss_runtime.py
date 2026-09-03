from __future__ import annotations

"""Strictly read Beast Abyss resources from already-loaded Lua state."""

from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
    _lua_addresses,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    table_ref,
)


_BEAST_METHODS = frozenset({"LuaBeastexplodeMgr", "Inst_get"})
_ENTITY_METHODS = frozenset({"EntityMgr", "Inst_get", "GetUserId"})
_DB_METHODS = frozenset(
    {"DBMgr", "GetConfigTable", "GetConfigTableByIdWithLog", "Inst_get"}
)
_COUNT_CONFIG = "BeastExplode.BeastExplodeCount"
_HIERARCHY_CONFIG = "BeastExplode.BeastExplodeHierarchy"
_COUNT_FIELDS = (
    "id", "item_id", "automatic", "initial", "limit",
    "interval_minutes", "description_locale_id", "supplement_item_id",
    "number_locale_id",
)
_HIERARCHY_FIELDS = (
    "id", "scene_image", "name_locale_id", "enter_tip_locale_id",
    "back_tip_locale_id", "forward_score", "cool_time", "reward_preview",
    "consume", "description_locale_id",
)

_AUTO_OPTION_TYPE_IDS = {
    "fairy_events": 102,
    "beast_events": 101,
    "player_events": 103,
    "auto_use_explore_items": 201,
    "stop_when_killed": 202,
    "fast_auto": 9999,
    "skip_animation": 99999,
}


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value)


def _beast_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _BEAST_METHODS)
    instance = _fields(reader, manager.get("inst"))
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("BeastexplodeData"))
    if "_BeastExplodeCountInfoDic" not in data:
        raise FanxiuRuntimeMemoryError("兽渊 Runtime 尚未初始化计数容器")
    return data


def _decode_count_rows(
    reader: LuaJitReader,
    data: dict[Any, Any],
) -> dict[int, dict[str, int]]:
    raw_rows = reader.dictionary_fields(data.get("_BeastExplodeCountInfoDic"))
    result: dict[int, dict[str, int]] = {}
    for raw_key, raw_value in raw_rows.items():
        row = _fields(reader, raw_value)
        item_type = as_int(row.get("type"))
        key_type = as_int(raw_key)
        count = as_int(row.get("count"))
        recover_time = reader.long(row.get("recoverTime"))
        if item_type is None or count is None or item_type not in (1, 2):
            continue
        if key_type != item_type:
            raise FanxiuRuntimeMemoryError(
                f"兽渊计数字典键值身份冲突：key={key_type}, type={item_type}"
            )
        result[item_type] = {
            "type": item_type,
            "count": max(0, count),
            "recover_time": int(recover_time or 0),
        }
    missing = sorted({1, 2} - set(result))
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"兽渊 Runtime 尚未同步完整探索/挑战计数：missing={missing}"
        )
    return result


def _decode_hierarchy_candidates(
    reader: LuaJitReader,
    data: dict[Any, Any],
) -> list[int]:
    """Return the distinct loaded hierarchy values without guessing the owner."""

    info = _fields(reader, data.get("_BeastExplodeInfo"))
    hierarchy_map = info.get("hierarchyMap")
    if hierarchy_map is None:
        raise FanxiuRuntimeMemoryError("兽渊 Runtime 尚未同步 hierarchyMap")
    values = reader.dictionary_fields(hierarchy_map).values()
    candidates = sorted(
        {
            hierarchy
            for value in values
            if (hierarchy := as_int(value)) is not None and hierarchy > 0
        }
    )
    if not candidates:
        raise FanxiuRuntimeMemoryError("兽渊 Runtime hierarchyMap 中没有有效层级")
    return candidates


def _decode_personal_rank(
    reader: LuaJitReader,
    data: dict[Any, Any],
) -> dict[str, Any]:
    """Read the Beast-owned live rank VO used by the visible info panel."""

    rank_ref = table_ref(data.get("_RankPersonalVO"))
    if rank_ref is None:
        raise FanxiuRuntimeMemoryError("兽渊 Runtime 尚未同步个人排行")
    fields = _fields(reader, rank_ref)
    score = as_int(fields.get("score"))
    rank = as_int(fields.get("rank"))
    if score is None or score < 0:
        raise FanxiuRuntimeMemoryError(f"兽渊个人积分无效：{score!r}")
    if rank is None:
        raise FanxiuRuntimeMemoryError(f"兽渊个人排名无效：{rank!r}")
    return {
        "score": int(score),
        "rank": int(rank),
        "runtime_object_identity": f"0x{rank_ref.address:x}",
    }


def _decode_hierarchy_map(
    reader: LuaJitReader,
    data: dict[Any, Any],
) -> dict[int, int]:
    """Decode the authoritative ``userId -> hierarchy`` Runtime map."""

    info = _fields(reader, data.get("_BeastExplodeInfo"))
    hierarchy_map = info.get("hierarchyMap")
    if hierarchy_map is None:
        raise FanxiuRuntimeMemoryError("兽渊 Runtime 尚未同步 hierarchyMap")
    result: dict[int, int] = {}
    for raw_user_id, raw_hierarchy in reader.dictionary_fields(hierarchy_map).items():
        user_id = reader.long(raw_user_id)
        hierarchy = as_int(raw_hierarchy)
        if user_id is None or user_id <= 0 or hierarchy is None or hierarchy <= 0:
            continue
        if user_id in result and result[user_id] != hierarchy:
            raise FanxiuRuntimeMemoryError(
                f"兽渊 hierarchyMap 玩家层级冲突：user_id={user_id}"
            )
        result[user_id] = hierarchy
    if not result:
        raise FanxiuRuntimeMemoryError("兽渊 Runtime hierarchyMap 中没有有效玩家层级")
    return result


def _entity_user_id(reader: LuaJitReader, root_address: int) -> int:
    """Read the existing local player's ID without calling ``GetUserId``."""

    manager = manager_index_fields(reader, root_address, _ENTITY_METHODS)
    instance = _fields(reader, manager.get("inst"))
    user_view = _fields(reader, instance.get("UserView"))
    entity = _fields(reader, user_view.get("Entity"))
    user_id = reader.long(entity.get("V_ID"))
    if user_id is None or user_id <= 0:
        raise FanxiuRuntimeMemoryError("EntityMgr 尚未加载本机玩家 V_ID")
    return user_id


def _config_table(
    reader: LuaJitReader,
    db_root: int,
    name: str,
) -> dict[Any, Any]:
    manager = manager_index_fields(reader, db_root, _DB_METHODS)
    instance = _fields(reader, manager.get("inst"))
    configs = reader.dictionary_fields(instance.get("ConfigDic"))
    table = _fields(reader, configs.get(name))
    if not table:
        raise FanxiuRuntimeMemoryError(f"兽渊配置尚未加载：{name}")
    return table


def _plain_config_value(reader: LuaJitReader, value: Any) -> Any:
    if not isinstance(value, LuaRef):
        return value
    if value.kind != "table":
        return None
    array = list(reader.table(value.address).get("array", ()))
    return [
        _plain_config_value(reader, current)
        for current in array[1:]
        if current is not None
    ]


def _row_fields(
    reader: LuaJitReader,
    value: Any,
    *,
    field_names: tuple[str, ...],
) -> dict[str, Any]:
    raw_fields = _fields(reader, value)
    named = {
        str(key): _plain_config_value(reader, current)
        for key, current in raw_fields.items()
        if isinstance(key, str)
    }
    if isinstance(value, LuaRef) and value.kind == "table":
        array = list(reader.table(value.address).get("array", ()))
        for index, field_name in enumerate(field_names, start=1):
            # Generated config rows are sparse Lua arrays.  ``reader.table``'s
            # compact array projection can stop at an omitted/default slot,
            # while the same numeric key is still present in the hash fields.
            # Prefer that exact 1-based key and only fall back to the dense
            # projection; otherwise hierarchy rows 2/3 lose ``consume``.
            current = raw_fields.get(index)
            if current is None and index < len(array):
                current = array[index]
            named.setdefault(field_name, _plain_config_value(reader, current))
    return named


def read_beast_abyss_resource_snapshot() -> dict[str, Any]:
    """Read current exploration/challenge counters and loaded configs only."""

    # Prefer the validated process cache, but allow the normal process-external
    # read-only discovery path when this is the first Runtime reader in the
    # process.  A cold cache is not evidence that the game state is absent.
    memory = MumuProcessMemory.discover_cached()
    state_address = int(_lua_addresses(memory)["state"], 16)
    reader = LuaJitReader(memory)
    beast_root, beast_cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key="beast-abyss-resources",
        state_address=state_address,
        global_name="BeastexplodeMgr",
        required_methods=_BEAST_METHODS,
        validate=lambda current_reader, address: _decode_count_rows(
            current_reader,
            _beast_data_fields(current_reader, address),
        ),
    )
    entity_root, entity_cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key="beast-abyss-entity",
        state_address=state_address,
        global_name="EntityMgr",
        required_methods=_ENTITY_METHODS,
        validate=lambda current_reader, address: _entity_user_id(
            current_reader, address
        ),
    )
    db_root, db_cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key="beast-abyss-db-config",
        state_address=state_address,
        global_name="DBMgr",
        required_methods=_DB_METHODS,
        validate=lambda current_reader, address: _config_table(
            current_reader,
            address,
            _COUNT_CONFIG,
        ),
    )
    data = _beast_data_fields(reader, beast_root)
    counts = _decode_count_rows(reader, data)
    count_configs = {
        int(key): _row_fields(reader, value, field_names=_COUNT_FIELDS)
        for key, value in _config_table(reader, db_root, _COUNT_CONFIG).items()
        if as_int(key) in (1, 2)
    }
    if set(count_configs) != {1, 2}:
        raise FanxiuRuntimeMemoryError("兽渊探索/挑战配置行不完整")
    hierarchy_configs = {
        int(key): _row_fields(reader, value, field_names=_HIERARCHY_FIELDS)
        for key, value in _config_table(
            reader,
            db_root,
            _HIERARCHY_CONFIG,
        ).items()
        if as_int(key) is not None
    }
    hierarchy_map = _decode_hierarchy_map(reader, data)
    hierarchy_candidates = sorted(set(hierarchy_map.values()))
    current_user_id = _entity_user_id(reader, entity_root)
    current_hierarchy = hierarchy_map.get(current_user_id)
    if current_hierarchy is None:
        raise FanxiuRuntimeMemoryError(
            "兽渊 hierarchyMap 缺少本机玩家："
            f"user_id={current_user_id}, loaded_users={len(hierarchy_map)}"
        )
    current_hierarchy_config = (
        hierarchy_configs.get(current_hierarchy)
        if current_hierarchy is not None
        else None
    )
    if current_hierarchy is not None and not current_hierarchy_config:
        raise FanxiuRuntimeMemoryError(
            f"兽渊当前层级缺少已加载配置：hierarchy={current_hierarchy}"
        )
    info = _fields(reader, data.get("_BeastExplodeInfo"))
    personal_rank = _decode_personal_rank(reader, data)
    return {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "runtime_memory",
        "read_only": True,
        "explore": counts[1],
        "challenge": counts[2],
        "point_get": max(0, as_int(info.get("pointGet")) or 0),
        "quick_check": bool(info.get("quickCheck")),
        "personal_rank": personal_rank,
        "hierarchy_candidates": hierarchy_candidates,
        "current_user_id": current_user_id,
        "current_hierarchy": current_hierarchy,
        "current_hierarchy_config": current_hierarchy_config,
        "count_configs": count_configs,
        "hierarchy_configs": hierarchy_configs,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "beast_root": f"0x{beast_root:x}",
            "beast_root_cache_hit": beast_cache_hit,
            "personal_rank_object_identity": personal_rank["runtime_object_identity"],
            "entity_root": f"0x{entity_root:x}",
            "entity_root_cache_hit": entity_cache_hit,
            "db_root": f"0x{db_root:x}",
            "db_root_cache_hit": db_cache_hit,
        },
    }


def read_beast_abyss_budget_snapshot() -> dict[str, Any]:
    """Combine counters, generated config and supplement-item inventory."""

    from backend.core.fanxiu.instrumentation.backpack import (
        read_backpack_item_counts,
    )

    snapshot = read_beast_abyss_resource_snapshot()
    current_hierarchy = snapshot.get("current_hierarchy")
    current_hierarchy_config = snapshot.get("current_hierarchy_config")
    if current_hierarchy is None or not isinstance(current_hierarchy_config, dict):
        raise FanxiuRuntimeMemoryError(
            "兽渊当前层级不能唯一确定，拒绝计算探索预算："
            f"candidates={snapshot.get('hierarchy_candidates')}"
        )
    explore_cost = as_int(current_hierarchy_config.get("consume"))
    if explore_cost is None or explore_cost <= 0:
        raise FanxiuRuntimeMemoryError(
            f"兽渊当前层级探索消耗无效：hierarchy={current_hierarchy}, consume={explore_cost}"
        )
    hierarchy_costs = [
        int(value)
        for config in dict(snapshot.get("hierarchy_configs") or {}).values()
        if (value := as_int(dict(config or {}).get("consume"))) is not None
        and int(value) > 0
    ]
    if not hierarchy_costs:
        raise FanxiuRuntimeMemoryError("兽渊层级配置没有可用的探索消耗")
    max_explore_cost = max(hierarchy_costs)
    item_ids = {
        int(config.get("supplement_item_id") or 0)
        for config in snapshot["count_configs"].values()
        if int(config.get("supplement_item_id") or 0) > 0
    }
    counts, evidence = read_backpack_item_counts(
        item_ids,
        manager_key="beast-abyss-resource-budget",
    )
    snapshot["supplement_item_counts"] = counts
    snapshot["evidence"]["backpack"] = evidence
    explore_config = snapshot["count_configs"][1]
    challenge_config = snapshot["count_configs"][2]
    explore_item_id = int(explore_config.get("supplement_item_id") or 0)
    challenge_item_id = int(challenge_config.get("supplement_item_id") or 0)
    snapshot["capacity"] = {
        "current_hierarchy": int(current_hierarchy),
        "explore_cost": int(explore_cost),
        "max_explore_cost": int(max_explore_cost),
        "explore_points_without_items": int(snapshot["explore"]["count"]),
        "explore_points_with_items": (
            int(snapshot["explore"]["count"])
            + int(counts.get(explore_item_id, 0))
            * int(explore_config.get("automatic") or 0)
        ),
        "explore_attempts_without_items": (
            int(snapshot["explore"]["count"]) // max_explore_cost
        ),
        "explore_attempts_with_items": (
            int(snapshot["explore"]["count"])
            + int(counts.get(explore_item_id, 0))
            * int(explore_config.get("automatic") or 0)
        ) // max_explore_cost,
        "challenge_without_items": int(snapshot["challenge"]["count"]),
        "challenge_with_items": (
            int(snapshot["challenge"]["count"])
            + int(counts.get(challenge_item_id, 0))
            * int(challenge_config.get("automatic") or 0)
        ),
    }
    return snapshot


def read_beast_abyss_auto_count_snapshot() -> dict[str, Any]:
    """Read the live #658 ``useNum`` field used for OCR fallback.

    Beast Abyss does not use Heaven's ``_AutoFightData[10]`` model.  Its
    loaded settings panel owns the integer as ``useNum``; panel identity is
    proven by the complete Beast-specific sibling field set below.
    """

    from backend.core.fanxiu.instrumentation.ui_runtime_context import (
        active_ui_component_objects,
        read_ui_object_field,
        read_ui_runtime_snapshot,
    )

    required = frozenset({
        "useNum",
        "useMax",
        "SliderNum",
        "btnConfirm",
        "BeastexplodeHelpTypeItem1",
        "BeastexplodeHelpTypeItem2",
        "OnSliderChangedFunID",
    })

    def read_panel(context):
        candidates = []
        for component in active_ui_component_objects(context):
            fields = context.reader.fields(component)
            if required.issubset(fields):
                candidates.append(component)
        if len(candidates) != 1:
            raise FanxiuRuntimeMemoryError(
                f"active 兽渊自动探查设置面板数量为 {len(candidates)}"
            )
        panel = candidates[0]
        current = as_int(read_ui_object_field(context, panel.address, "useNum"))
        maximum = as_int(read_ui_object_field(context, panel.address, "useMax"))
        if current is None or current < 1:
            raise FanxiuRuntimeMemoryError(f"兽渊自动探查 useNum 无效：{current!r}")
        if maximum is None or maximum < current:
            raise FanxiuRuntimeMemoryError(
                f"兽渊自动探查 useMax 无效：current={current!r}, maximum={maximum!r}"
            )
        return {
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "active_beast_abyss_auto_panel",
            "read_only": True,
            "current": int(current),
            "minimum": 1,
            "maximum": int(maximum),
            "evidence": {
                "pid": context.binding.pid,
                "process_start_ticks": context.binding.process_start_ticks,
                "panel_address": f"0x{panel.address:x}",
                "field": "useNum",
            },
        }

    return read_ui_runtime_snapshot(required, read_panel, fast=True)


def read_beast_abyss_auto_options_snapshot() -> dict[str, Any]:
    """Read all eight #658 option facts in one Runtime observation.

    The seven ordinary toggles are authoritative members of
    ``BeastexplodeData._AutoFightList``.  The optional special-item toggle is
    local setting data keyed by the one special-item row currently rendered
    in ``V_UseItemList``.  GUI code uses this snapshot only to decide which
    fixed semantic Shapes to click, then reads one more snapshot to verify the
    batch result.
    """

    from backend.core.fanxiu.instrumentation.ui_runtime_context import (
        active_ui_component_objects,
        read_ui_object_field,
        read_ui_runtime_snapshot,
    )

    required = frozenset({
        "useNum",
        "useMax",
        "tbHelpItem",
        "V_UseItemList",
        "BeastexplodeHelpTypeItem1",
        "OnSliderChangedFunID",
    })

    def decode_auto_types(reader: LuaJitReader, root_address: int) -> tuple[int, ...]:
        data = _beast_data_fields(reader, root_address)
        values, declared_count = reader.list_items(data.get("_AutoFightList"))
        if declared_count is None:
            raise FanxiuRuntimeMemoryError("兽渊自动选项列表尚未加载")
        return tuple(
            sorted({
                value
                for raw in values
                if (value := as_int(raw)) is not None
            })
        )

    def read_panel(context):
        candidates = []
        for component in active_ui_component_objects(context):
            fields = context.reader.fields(component)
            if required.issubset(fields):
                candidates.append(component)
        if len(candidates) != 1:
            raise FanxiuRuntimeMemoryError(
                f"active 兽渊自动探查设置面板数量为 {len(candidates)}"
            )
        panel = candidates[0]

        beast_root, cache_hit, _environment = resolve_lua_global_manager_root(
            context.memory,
            manager_key="beast-abyss-auto-options",
            state_address=context.binding.state_address,
            global_name="BeastexplodeMgr",
            required_methods=_BEAST_METHODS,
            validate=decode_auto_types,
        )
        selected_type_ids = set(decode_auto_types(context.reader, beast_root))
        data = _beast_data_fields(context.reader, beast_root)

        special_list = table_ref(
            read_ui_object_field(context, panel.address, "V_UseItemList")
        )
        if special_list is None:
            raise FanxiuRuntimeMemoryError("兽渊特殊道具选项列表尚未加载")
        special_values, special_count = context.reader.list_items(special_list)
        special_rows = [table_ref(value) for value in special_values]
        special_rows = [value for value in special_rows if value is not None]
        if special_count != 1 or len(special_rows) != 1:
            raise FanxiuRuntimeMemoryError(
                "兽渊特殊道具选项无法唯一确定："
                f"declared={special_count!r}, loaded={len(special_rows)}"
            )
        special_data = table_ref(
            read_ui_object_field(context, special_rows[0].address, "V_Data")
        )
        if special_data is None:
            raise FanxiuRuntimeMemoryError("兽渊特殊道具选项缺少 V_Data")
        # Generated BeastExplodeUse rows are compact positional arrays rather
        # than named tables: [1]=id, [2]=itemId.
        special_array = list(context.reader.table(special_data.address).get("array", ()))
        special_config_id = as_int(
            special_array[1] if len(special_array) > 1 else None
        )
        special_item_id = as_int(
            special_array[2] if len(special_array) > 2 else None
        )
        if special_config_id is None or special_config_id <= 0:
            raise FanxiuRuntimeMemoryError("兽渊特殊道具选项缺少有效配置 id")

        setting_rows = context.reader.dictionary_fields(data.get("_AutoSettingData"))
        special_setting = None
        for raw_key, raw_value in setting_rows.items():
            if as_int(raw_key) == special_config_id:
                special_setting = context.reader.fields(raw_value)
                break
        special_selected = bool(
            special_setting and special_setting.get("toggle") is True
        )

        options = {
            name: type_id in selected_type_ids
            for name, type_id in _AUTO_OPTION_TYPE_IDS.items()
        }
        options["use_find_demon_talisman"] = special_selected
        return {
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "active_beast_abyss_auto_panel",
            "read_only": True,
            "options": options,
            "evidence": {
                "pid": context.binding.pid,
                "process_start_ticks": context.binding.process_start_ticks,
                "panel_address": f"0x{panel.address:x}",
                "beast_root": f"0x{beast_root:x}",
                "beast_root_cache_hit": cache_hit,
                "selected_type_ids": sorted(selected_type_ids),
                "special_config_id": special_config_id,
                "special_item_id": special_item_id,
            },
        }

    return read_ui_runtime_snapshot(required, read_panel, fast=True)


__all__ = [
    "read_beast_abyss_auto_count_snapshot",
    "read_beast_abyss_auto_options_snapshot",
    "read_beast_abyss_budget_snapshot",
    "read_beast_abyss_resource_snapshot",
]
