from __future__ import annotations

"""Strict read-only projection of the already-loaded Xutian/Heaven model."""

from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
)


_HEAVEN_METHODS = frozenset({"LuaHeavenMgr", "Inst_get"})
_DB_METHODS = frozenset({"DBMgr", "GetConfigTable", "GetConfigTableByIdWithLog", "Inst_get"})
_COUNT_CONFIG = "Heaven.HeavenCount"

# HeavenType.AutoKeyType from the current generated client.  Keep the raw
# numeric keys in evidence as well: the settings panel is only a view over
# HeavenData._AutoFightData, so Runtime is the authoritative switch state.
_AUTO_SETTING_KEYS = {
    3: "quality_3",
    4: "quality_4",
    5: "quality_5",
    6: "quality_6",
    7: "quality_7",
    8: "refill_challenge",
    9: "refill_explore",
    10: "challenge_count",
    14: "quick_auto",
    15: "quality_8",
    16: "skip_animation",
    99: "quality_player",
}


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value)


def _heaven_instance_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _HEAVEN_METHODS)
    instance = _fields(reader, manager.get("inst"))
    if not instance:
        raise FanxiuRuntimeMemoryError("虚天 Runtime Manager 尚未初始化")
    return instance


def _heaven_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    instance = _heaven_instance_fields(reader, root_address)
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("HeavenData"))
    if "_HeavenCountInfo" not in data:
        raise FanxiuRuntimeMemoryError("虚天 Runtime 尚未初始化计数容器")
    return data


def _decode_counts(reader: LuaJitReader, data: dict[Any, Any]) -> dict[int, dict[str, int]]:
    rows = reader.dictionary_fields(data.get("_HeavenCountInfo"))
    result: dict[int, dict[str, int]] = {}
    for raw_key, raw_value in rows.items():
        row = _fields(reader, raw_value)
        item_type = as_int(row.get("type"))
        key_type = as_int(raw_key)
        count = as_int(row.get("count"))
        if item_type not in (1, 2) or count is None:
            continue
        if key_type != item_type:
            raise FanxiuRuntimeMemoryError(
                f"虚天计数字典键值身份冲突：key={key_type}, type={item_type}"
            )
        result[item_type] = {
            "type": item_type,
            "count": max(0, count),
            "recover_time": int(reader.long(row.get("recoverTime")) or 0),
        }
    missing = sorted({1, 2} - set(result))
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"虚天 Runtime 尚未同步完整探查/挑战计数：missing={missing}"
        )
    return result


def _decode_auto_settings(reader: LuaJitReader, data: dict[Any, Any]) -> dict[str, Any]:
    raw = data.get("_AutoFightData")
    if raw is None:
        raise FanxiuRuntimeMemoryError("虚天自动配置尚未初始化")
    rows = dict(_fields(reader, raw))
    storage = {"hash": {str(k): str(v) for k, v in rows.items() if as_int(k) in _AUTO_SETTING_KEYS}, "array": {}}
    if hasattr(raw, "address"):
        table = reader.table(raw.address)
        for numeric_key, raw_value in enumerate(table.get("array", ())):
            if raw_value is not None:
                storage["array"][str(numeric_key)] = str(raw_value)
                rows.setdefault(numeric_key, raw_value)
    if not rows:
        raise FanxiuRuntimeMemoryError("虚天自动配置为空")
    decoded: dict[str, Any] = {}
    raw_evidence: dict[str, Any] = {}
    for raw_key, raw_value in rows.items():
        key = as_int(raw_key)
        if key not in _AUTO_SETTING_KEYS:
            continue
        fields = _fields(reader, raw_value)
        auto_fight = fields.get("autoFight")
        if key == 10:
            count = as_int(auto_fight)
            if count is None or count < 1:
                raise FanxiuRuntimeMemoryError(
                    f"虚天自动挑战次数无效：{auto_fight!r}"
                )
            value: Any = count
        else:
            if not isinstance(auto_fight, bool):
                raise FanxiuRuntimeMemoryError(
                    f"虚天自动配置开关类型无效：key={key}, value={auto_fight!r}"
                )
            value = auto_fight
        name = _AUTO_SETTING_KEYS[key]
        decoded[name] = value
        raw_evidence[str(key)] = {
            "auto_fight": value,
            "use_item": fields.get("useItem"),
            "use_item_2": fields.get("useItem2"),
            "use_item_3": fields.get("useItem3"),
            "use_item_4": fields.get("useItem4"),
        }
    required = {
        "quality_3",
        "quality_4",
        "quality_5",
        "quality_6",
        "quality_7",
        "quality_8",
        "refill_challenge",
        "refill_explore",
        "challenge_count",
        "quick_auto",
        "skip_animation",
    }
    missing = sorted(required - set(decoded))
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"虚天自动配置尚未同步完整：missing={missing}"
        )
    return {"values": decoded, "raw": raw_evidence, "storage": storage}


def _decode_available_quality_keys(
    reader: LuaJitReader,
    data: dict[Any, Any],
    *,
    current_heaven: int,
) -> list[int]:
    heaven_rows = reader.dictionary_fields(data.get("_AutoFightCfgDic"))
    current = heaven_rows.get(current_heaven) or heaven_rows.get(float(current_heaven))
    if current is None:
        raise FanxiuRuntimeMemoryError(
            f"虚天当前地图自动挑战配置尚未加载：heaven={current_heaven}"
        )
    raw_keys = reader.dictionary_fields(current)
    keys = sorted({as_int(key) for key in raw_keys if as_int(key) is not None})
    normalized = [15 if key == 8 else int(key) for key in keys if key in {3, 4, 5, 6, 7, 8, 99}]
    if not normalized:
        raise FanxiuRuntimeMemoryError("虚天当前地图没有可识别的自动挑战品质")
    return normalized


def _decode_special_options(
    reader: LuaJitReader,
    data: dict[Any, Any],
) -> dict[str, Any]:
    """Project the two lower special-item toggles from HeavenInfo.checks.

    ``HeavenAutoSettingView`` does not store these rows in ``_AutoFightData``;
    their read-only authority is ``HeavenInfo.checks:Contains(itemId)``.  A
    missing checks list is the client's own uninitialized-empty state.
    """

    info = _fields(reader, data.get("_HeavenInfo"))
    if not info:
        raise FanxiuRuntimeMemoryError("虚天 Runtime 业务数据尚未加载")
    first_item = as_int(data.get("_HeightDetectItem"))
    second_item = as_int(data.get("_HeightDetectItem2"))
    if not first_item or not second_item:
        raise FanxiuRuntimeMemoryError("虚天特殊探查道具身份尚未加载")
    raw_checks = info.get("checks")
    values: list[Any] = []
    declared_count = 0
    if raw_checks is not None:
        values, declared_count = reader.list_items(raw_checks)
        if int(declared_count or 0) != len(values):
            raise FanxiuRuntimeMemoryError("虚天特殊探查道具选中列表读取竞态")
    selected_ids = {
        int(item_id)
        for value in values
        if (item_id := as_int(value)) is not None and int(item_id) > 0
    }
    return {
        "find_demon_item_id": first_item,
        "native_soul_lock_item_id": second_item,
        "find_demon_selected": first_item in selected_ids,
        "native_soul_lock_selected": second_item in selected_ids,
        "find_demon_available": bool(data.get("_CanShowUseSpecialItem")),
        "native_soul_lock_available": bool(data.get("_CanShowUseSpecialItem2")),
        "selected_item_ids": sorted(selected_ids),
        "declared_count": int(declared_count or 0),
    }


def _config_table(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _DB_METHODS)
    instance = _fields(reader, manager.get("inst"))
    configs = reader.dictionary_fields(instance.get("ConfigDic"))
    table = _fields(reader, configs.get(_COUNT_CONFIG))
    if not table:
        raise FanxiuRuntimeMemoryError(f"虚天配置尚未加载：{_COUNT_CONFIG}")
    return table


def _decode_count_configs(
    reader: LuaJitReader, root_address: int, *, environment_address: int,
) -> dict[int, dict[str, int]]:
    """Resolve generated columns from the live schema, never guessed slots."""
    from backend.core.fanxiu.instrumentation.resource_auto_use import read_runtime_config_rows

    table = _config_table(reader, root_address)
    selected = [(as_int(k), v) for k, v in table.items() if as_int(k) in (1, 2)]
    rows = read_runtime_config_rows(
        reader, [v for _k, v in selected], environment_address=environment_address,
        group_name="Heaven", table_name="HeavenCount", fields=("id", "itemId2", "init", "limit", "interval"),
    )
    result: dict[int, dict[str, int]] = {}
    for (item_type, _raw), row in zip(selected, rows, strict=True):
        if as_int(row.get("id")) != item_type:
            raise FanxiuRuntimeMemoryError("虚天计数配置键值身份冲突")
        supplement_item_id = as_int(row.get("itemId2"))
        initial = as_int(row.get("init"))
        natural_limit = as_int(row.get("limit"))
        recovery_interval = as_int(row.get("interval"))
        if not supplement_item_id or supplement_item_id <= 0:
            raise FanxiuRuntimeMemoryError(f"虚天计数配置缺少补充道具：type={item_type}")
        if any(v is None or v < 0 for v in (initial, natural_limit, recovery_interval)):
            raise FanxiuRuntimeMemoryError(f"虚天自然恢复配置无效：type={item_type}, row={row}")
        # The native UpdateMaxUse adds owned item count directly to free
        # challenge count. Budget within that native limit; init/limit describe
        # natural restoration and must not multiply the item's capacity.
        result[item_type] = {"type": item_type, "supplement_item_id": supplement_item_id,
                             "initial": initial, "natural_limit": natural_limit,
                             "recovery_interval": recovery_interval, "restored_per_item": 1}
    if set(result) != {1, 2}:
        raise FanxiuRuntimeMemoryError("虚天探查/挑战配置行不完整")
    return result


def read_xutian_snipe_confirmation() -> dict[str, Any]:
    """Read the active native confirmation, including selected consumables.

    No navigation or Lua invocation. Rebind the active panel on each observation;
    callers must freshly verify item/count/target before the irreversible tap.
    """
    from .ui_runtime_context import (read_ui_runtime_snapshot,
        read_active_ui_window_panels, read_ui_object_field)
    from .runtime_memory import table_ref

    def read(context):
        panels = read_active_ui_window_panels(context, 'HeavenDungeonEnterView')
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('虚天狙击确认页未唯一加载')
        def field(obj, name):
            ref = table_ref(obj)
            if ref is None:
                raise FanxiuRuntimeMemoryError(f'虚天确认对象缺失：{name}')
            return read_ui_object_field(context, ref.address, name)
        host = panels[0]
        result = {name: field(host, name) for name in
            ('snipeNum', 'isPointDetect', 'palyerId', '_SelectMultiState',
             '_SelectMultiState2', '_SelectMultiState3', '_CanPassFight')}
        from .resource_auto_use import read_runtime_config_rows
        def item_id(cfg):
            return read_runtime_config_rows(context.reader, [table_ref(cfg)],
                environment_address=context.binding.environment_address,
                group_name='Item', table_name='Item', fields=('id',))[0]['id']
        result['snipe_item_id'] = item_id(field(host, 'snipeItemCfg'))
        result['extra_item_ids'] = [item_id(cfg) if table_ref(cfg) else None
            for cfg in (field(host, '_ItemCfg'), field(host, '_ItemCfg2'), field(host, '_ItemCfg3'))]
        target = table_ref(result.pop('palyerId'))
        if target is None:
            raise FanxiuRuntimeMemoryError('虚天狙击玩家ID未加载')
        result['target_id'] = context.reader.long(target)
        if (as_int(result['snipeNum']) is None or result['isPointDetect'] is not True
                or as_int(result['snipe_item_id']) is None or not result['target_id']):
            raise FanxiuRuntimeMemoryError(f'虚天狙击确认事实不完整：{result!r}')
        return {'captured_at': datetime.now().astimezone().isoformat(),
                'source': 'runtime_memory', 'read_only': True, **result}
    return read_ui_runtime_snapshot({'HeavenDungeonEnterView'}, read)


def read_xutian_resource_snapshot(*, include_auto_settings: bool = True) -> dict[str, Any]:
    """Read live counters, current map and supplement-item capacity.

    Resource planning may explicitly omit the separately loaded auto settings.
    Default execution reads still require the complete settings contract; an
    omitted settings snapshot must never authorize starting an auto batch.
    """

    memory = MumuProcessMemory.discover_cached()
    state_address = int(_lua_addresses(memory)["state"], 16)
    reader = LuaJitReader(memory)
    heaven_root, heaven_cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key="xutian-resources",
        state_address=state_address,
        global_name="HeavenMgr",
        required_methods=_HEAVEN_METHODS,
        validate=lambda current_reader, address: _decode_counts(
            current_reader, _heaven_data_fields(current_reader, address)
        ),
    )
    db_root, db_cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key="xutian-db-config",
        state_address=state_address,
        global_name="DBMgr",
        required_methods=_DB_METHODS,
        validate=lambda current_reader, address: _config_table(current_reader, address),
    )
    data = _heaven_data_fields(reader, heaven_root)
    instance = _heaven_instance_fields(reader, heaven_root)
    counts = _decode_counts(reader, data)
    auto_settings = _decode_auto_settings(reader, data) if include_auto_settings else {"values": {}, "raw": {}}
    configs = _decode_count_configs(reader, db_root, environment_address=_environment)
    info = _fields(reader, data.get("_HeavenInfo"))
    if not info:
        raise FanxiuRuntimeMemoryError("虚天 Runtime 业务数据尚未加载")
    current_heaven = as_int(info.get("heaven"))
    if current_heaven is None or current_heaven < 0:
        raise FanxiuRuntimeMemoryError("虚天 Runtime 当前地图字段无效")
    available_quality_keys = _decode_available_quality_keys(
        reader,
        data,
        current_heaven=current_heaven,
    ) if include_auto_settings else []
    item_ids = {row["supplement_item_id"] for row in configs.values()}
    item_counts, backpack_evidence = read_backpack_item_counts(
        item_ids, manager_key="xutian-resource-budget"
    )
    explore_item = configs[1]
    challenge_item = configs[2]
    return {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "runtime_memory",
        "read_only": True,
        "explore": counts[1],
        "challenge": counts[2],
        "current_heaven": current_heaven,
        "detect_num": max(0, as_int(info.get("detectNum")) or 0),
        "multiple_enabled": bool(info.get("multiple")),
        "quick_check_enabled": bool(info.get("quickCheck")),
        "auto_progress": {
            "running": bool(instance.get("_quickAutoStart")),
            "completed_challenges": max(0, as_int(data.get("_AutoFightCount")) or 0),
        },
        "auto_settings": auto_settings["values"],
        "auto_settings_included": include_auto_settings,
        "special_options": _decode_special_options(reader, data),
        "available_quality_keys": available_quality_keys,
        "count_configs": configs,
        "supplement_item_counts": item_counts,
        "capacity": {
            "explore_without_items": counts[1]["count"],
            "explore_with_items": counts[1]["count"] + item_counts.get(explore_item["supplement_item_id"], 0) * explore_item["restored_per_item"],
            "challenge_without_items": counts[2]["count"],
            "challenge_with_items": counts[2]["count"] + item_counts.get(challenge_item["supplement_item_id"], 0) * challenge_item["restored_per_item"],
        },
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "heaven_root": f"0x{heaven_root:x}",
            "heaven_root_cache_hit": heaven_cache_hit,
            "db_root": f"0x{db_root:x}",
            "db_root_cache_hit": db_cache_hit,
            "backpack": backpack_evidence,
            "auto_settings_raw": auto_settings["raw"],
        },
    }


def read_xutian_auto_settings_snapshot() -> dict[str, Any]:
    """Read only the live auto panel model for bounded click verification.

    Unlike :func:`read_xutian_resource_snapshot`, this deliberately avoids the
    DB config and backpack walks.  A settings reconciler calls it before and
    after each GUI action, so keeping this projection narrow is both faster and
    less exposed to unrelated Lua table replacement.
    """

    memory = MumuProcessMemory.discover_cached()
    state_address = int(_lua_addresses(memory)["state"], 16)
    reader = LuaJitReader(memory)
    heaven_root, heaven_cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key="xutian-auto-settings",
        state_address=state_address,
        global_name="HeavenMgr",
        required_methods=_HEAVEN_METHODS,
        validate=lambda current_reader, address: _decode_auto_settings(
            current_reader, _heaven_data_fields(current_reader, address)
        ),
    )
    instance = _heaven_instance_fields(reader, heaven_root)
    data = _heaven_data_fields(reader, heaven_root)
    info = _fields(reader, data.get("_HeavenInfo"))
    if not info:
        raise FanxiuRuntimeMemoryError("虚天 Runtime 业务数据尚未加载")
    current_heaven = as_int(info.get("heaven"))
    if current_heaven is None or current_heaven < 0:
        raise FanxiuRuntimeMemoryError("虚天 Runtime 当前地图字段无效")
    auto_settings = _decode_auto_settings(reader, data)
    return {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "runtime_memory",
        "read_only": True,
        "current_heaven": current_heaven,
        # The native loop checks its requested limit before the next action,
        # while one three-challenge action can advance _AutoFightCount by 3.
        # Keep this cheap panel projection sufficient for terminal bounds.
        "multiple_enabled": bool(info.get("multiple")),
        "auto_progress": {
            "running": bool(instance.get("_quickAutoStart")),
            "completed_challenges": max(
                0, as_int(data.get("_AutoFightCount")) or 0
            ),
        },
        "auto_settings": auto_settings["values"],
        "special_options": _decode_special_options(reader, data),
        "available_quality_keys": _decode_available_quality_keys(
            reader,
            data,
            current_heaven=current_heaven,
        ),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "heaven_root": f"0x{heaven_root:x}",
            "heaven_root_cache_hit": heaven_cache_hit,
            "auto_settings_raw": auto_settings["raw"],
            "projection": "auto_settings_only",
            "auto_settings_storage": auto_settings["storage"],
        },
    }


def read_xutian_auto_toggle_controls(*, quality_keys: tuple[int, ...] | None = None) -> dict[str, Any]:
    """Read the active auto panel's declared toggle dictionary for diagnosis.

    This projection never invokes callbacks or changes settings. Traversal is
    limited to the independently identified panel, its toggle dictionary and
    the declared row controls and their label wrappers; Lua references remain
    diagnostic addresses. No callbacks are invoked.
    """
    from backend.core.fanxiu.instrumentation.ui_runtime_context import (
        active_ui_component_objects, has_ui_object_fields, read_ui_object_field,
        read_ui_runtime_snapshot,
    )
    from backend.core.fanxiu.instrumentation.runtime_memory import table_ref
    required = frozenset({"_AutoToggleDic", "_CurMaxFightCount", "_MaxSliderValue", "StartBtn"})
    def project(context):
        panels = [p for p in active_ui_component_objects(context)
                  if has_ui_object_fields(context, p.address, required)]
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError(f"虚天自动设置面板数量为 {len(panels)}")
        value = read_ui_object_field(context, panels[0].address, "_AutoToggleDic")
        rows = context.reader.dictionary_fields(value)
        if len(rows) > 64:
            raise FanxiuRuntimeMemoryError("虚天自动开关字典超过64行")
        if quality_keys is not None:
            if set(quality_keys) - {3, 4, 5, 6, 7, 15, 99}:
                raise ValueError("未知虚天品质键")
            rows = {k: v for k, v in rows.items() if as_int(k) in set(quality_keys)}
        return {"captured_at": datetime.now().astimezone().isoformat(), "read_only": True,
                "panel_address": hex(panels[0].address),
                "panel_fields": context.reader.fields(panels[0]),
                "rows": {str(k): context.reader.fields(v) if table_ref(v) else v for k, v in rows.items()},
                "controls": {str(k): {name: context.reader.fields(control)
                    for name, control in context.reader.fields(v).items()
                    if name in {"BasicToggle", "ItemToggle", "ItemToggle3", "ItemToggle4"} and table_ref(control)}
                    for k, v in rows.items() if table_ref(v)},
                "labels": {str(k): {name: context.reader.fields(label)
                    for name, label in context.reader.fields(context.reader.fields(v).get("BasicToggle")).items()
                    if name == "ToggleTxt" and table_ref(label)}
                    for k, v in rows.items() if table_ref(v) and table_ref(context.reader.fields(v).get("BasicToggle"))},
                "boost_listeners": {str(k): {name: {str(lk): lv for lk, lv in context.reader.fields(context.reader.fields(control).get("listener")).items()}
                    for name, control in context.reader.fields(v).items()
                    if name == "ItemToggle4" and table_ref(control)}
                    for k, v in rows.items() if table_ref(v)},
                "boost_callbacks": {str(k): {name: {str(lk): lv for lk, lv in context.reader.dictionary_fields(
                        context.reader.fields(context.reader.fields(control).get("listener")).get("callBackDict")).items()}
                    for name, control in context.reader.fields(v).items()
                    if name == "ItemToggle4" and table_ref(control)}
                    for k, v in rows.items() if table_ref(v)},
                "evidence": {"pid": context.binding.pid, "process_start_ticks": context.binding.process_start_ticks}}
    return read_ui_runtime_snapshot(required, project, fast=True)


def read_xutian_auto_count_snapshot() -> dict[str, Any]:
    """Read the active Heaven settings panel's count and native range.

    The panel recomputes ``_MaxSliderValue`` after the refill switch changes.
    This value is the native slider range, while the caller remains
    responsible for its own bounded-batch and resource-spending policy.
    """

    from backend.core.fanxiu.instrumentation.ui_runtime_context import (
        active_ui_component_objects,
        has_ui_object_fields,
        read_ui_object_field,
        read_ui_runtime_snapshot,
    )

    required = frozenset({
        "_CurMaxFightCount",
        "_MaxSliderValue",
        "_AutoToggleDic",
        "FightCount",
        "Slider",
        "AddBtn",
        "SubBtn",
        "StartBtn",
        "AutoFight1",
        "AutoFight11",
    })

    def read_panel(context):
        candidates = []
        for component in active_ui_component_objects(context):
            if has_ui_object_fields(context, component.address, required):
                candidates.append(component)
        if len(candidates) != 1:
            raise FanxiuRuntimeMemoryError(
                f"active 虚天自动挑战设置面板数量为 {len(candidates)}"
            )
        panel = candidates[0]
        current = as_int(
            read_ui_object_field(context, panel.address, "_CurMaxFightCount")
        )
        maximum = as_int(
            read_ui_object_field(context, panel.address, "_MaxSliderValue")
        )
        if current is None or current < 1:
            raise FanxiuRuntimeMemoryError(
                f"虚天自动挑战当前次数无效：{current!r}"
            )
        if maximum is None or maximum < current:
            raise FanxiuRuntimeMemoryError(
                f"虚天自动挑战原生最大值无效：current={current!r}, maximum={maximum!r}"
            )
        return {
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "active_xutian_auto_settings_panel",
            "read_only": True,
            "current": int(current),
            "minimum": 1,
            "maximum": int(maximum),
            "evidence": {
                "pid": context.binding.pid,
                "process_start_ticks": context.binding.process_start_ticks,
                "panel_address": f"0x{panel.address:x}",
                "current_field": "_CurMaxFightCount",
                "maximum_field": "_MaxSliderValue",
            },
        }

    return read_ui_runtime_snapshot(required, read_panel, fast=True)


__all__ = [
    "read_xutian_auto_toggle_controls",
    "read_xutian_auto_count_snapshot",
    "read_xutian_auto_settings_snapshot",
    "read_xutian_resource_snapshot",
]
