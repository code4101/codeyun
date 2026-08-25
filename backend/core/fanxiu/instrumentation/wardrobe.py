from __future__ import annotations

"""Read the loaded wardrobe catalogue and player progression, strictly read-only."""

import time
from typing import Any

from backend.core.fanxiu.catalog.lua_config import (
    _find_default_lang_path,
    load_fanxiu_lang_map,
)
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root

from .magic_treasure import _config_row, _localized_text, _plain_text
from .redbag_runtime_loader import _lua_addresses
from .runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
)


_FASHION_METHODS = frozenset({"Inst_get", "GetFashionSexByHandPoint"})
_SECTION_BY_TYPE = {
    1: ("shizhuang", "时装"),
    2: ("wuqi", "武器"),
    3: ("huanshen", "环身"),
    4: ("beishi", "背饰"),
    5: ("yuqi", "御器"),
}


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value)


def _fashion_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _FASHION_METHODS)
    instance = _fields(reader, manager.get("inst"))
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("FashionData"))
    values, declared_count = reader.list_items(data.get("AllFashionInfoVoList"))
    if not values or declared_count is None or int(declared_count) != len(values):
        raise FanxiuRuntimeMemoryError(
            f"FashionMgr 衣装清单尚未完整加载：count={declared_count}, rows={len(values)}"
        )
    if as_int(data.get("AllFashionNum")) not in (None, len(values)):
        raise FanxiuRuntimeMemoryError(
            f"FashionMgr 衣装总数不一致：AllFashionNum={data.get('AllFashionNum')}, rows={len(values)}"
        )
    return data


def _fashion_config_indexes(
    reader: LuaJitReader,
    environment_address: int,
    table_name: str,
) -> dict[str, int]:
    environment = reader.string_fields(environment_address, frozenset({"s_globalCfgIdx"}))
    root = environment.get("s_globalCfgIdx")
    group = _fields(reader, root).get("Fashion")
    raw_indexes = _fields(reader, group).get(table_name)
    indexes = {
        str(key): int(index)
        for key, index in _fields(reader, raw_indexes).items()
        if isinstance(key, str) and as_int(index) is not None
    }
    required = {"id", "name", "type", "item"}
    if table_name == "Fashion" and not required.issubset(indexes):
        raise FanxiuRuntimeMemoryError(
            f"Fashion.Fashion 字段索引不完整：missing={sorted(required - set(indexes))}"
        )
    return indexes


def _fashion_row(
    reader: LuaJitReader,
    value: Any,
    indexes: dict[str, int],
) -> dict[str, Any]:
    if not isinstance(value, LuaRef) or value.kind != "table":
        return {}
    return _config_row(reader, value, indexes)


def read_wardrobe_hall_runtime() -> dict[str, Any]:
    """Return all five wardrobe categories and current owned levels.

    The function reads only an already loaded ``FashionMgr``.  It never creates
    the manager, refreshes data, executes Lua, installs hooks, or sends commands.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached(max_age_seconds=None)
        state_address = int(_lua_addresses(memory)["state"], 16)
        root, cache_hit, environment_address = resolve_lua_global_manager_root(
            memory,
            manager_key="wardrobe-fashion-manager",
            state_address=state_address,
            global_name="FashionMgr",
            required_methods=_FASHION_METHODS,
            validate=_fashion_data_fields,
        )
        reader = LuaJitReader(memory)
        data = _fashion_data_fields(reader, root)
        config_indexes = _fashion_config_indexes(reader, environment_address, "Fashion")
        lang_path = _find_default_lang_path(resolve_fanxiu_export_root())
        lang_map = load_fanxiu_lang_map(lang_path) if lang_path else {}
        values, declared_count = reader.list_items(data.get("AllFashionInfoVoList"))

        items: list[dict[str, Any]] = []
        seen: set[int] = set()
        expected = 0
        for value in values:
            fields = _fields(reader, value)
            fashion_id = as_int(fields.get("id"))
            config = _fashion_row(reader, fields.get("configData"), config_indexes)
            config_id = as_int(config.get("id"))
            if fashion_id is None or config_id != fashion_id:
                raise FanxiuRuntimeMemoryError(
                    f"FashionMgr 衣装身份不一致：vo={fashion_id}, config={config_id}"
                )
            if fashion_id in seen:
                raise FanxiuRuntimeMemoryError(f"FashionMgr 衣装身份重复：{fashion_id}")
            seen.add(fashion_id)
            type_id = as_int(config.get("type")) or 0
            section = _SECTION_BY_TYPE.get(type_id)
            if section is None:
                continue
            expected += 1
            runtime_item_id = as_int(fields.get("item"))
            config_item_id = as_int(config.get("item"))
            if runtime_item_id not in (None, config_item_id):
                raise FanxiuRuntimeMemoryError(
                    f"FashionMgr 衣装 {fashion_id} 道具身份不一致："
                    f"vo={runtime_item_id}, config={config_item_id}"
                )
            owned = bool(fields.get("isGet"))
            name = _plain_text(
                _localized_text(config.get("name"), reader=reader, lang_map=lang_map)
            )
            items.append(
                {
                    "fashion_id": fashion_id,
                    "item_id": int(config_item_id or runtime_item_id or 0),
                    "name": name or f"衣装 {fashion_id}",
                    "section_key": section[0],
                    "category": section[1],
                    "type_id": type_id,
                    "rank": int(as_int(fields.get("level")) or 0) if owned else 0,
                    "owned": owned,
                    "max_level": int(as_int(fields.get("MaxLevel")) or 0),
                    "show_max_level": int(as_int(fields.get("showMaxLevel")) or 0),
                    "is_max_level": bool(fields.get("isMaxLevel")),
                    "is_forever": bool(fields.get("isForever")),
                    "dress": bool(fields.get("dress")),
                    "condition": str(config.get("condition") or ""),
                }
            )
        if len(items) != expected:
            raise FanxiuRuntimeMemoryError(
                f"衣装阁五类投影不完整：expected={expected}, rows={len(items)}"
            )
        return {
            "ok": True,
            "complete": True,
            "source": "loaded_runtime_memory",
            "items": items,
            "captured_timestamp": time.time(),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "manager_root_cache_hit": cache_hit,
                "declared_fashion_count": int(declared_count or 0),
                "wardrobe_item_count": len(items),
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "complete": False,
            "source": "loaded_runtime_memory",
            "reason": str(exc),
            "items": [],
            "captured_timestamp": time.time(),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
            },
        }
