"""灵器洗炼词缀：复用客户端 GetMaxValueTypeShow 的阈值和图标语义。

SpiritWareWashAttrItem 仅在 cleanseCfg.full == 0 时显示图标。
0002=满，1002=粉色巅，1003=蓝色巅；品质颜色与这个判定互相独立。
"""

from __future__ import annotations

import math
from typing import Any

from .runtime_memory import (
    FanxiuRuntimeMemoryError, LuaRef, as_int, manager_index_fields,
    resolve_lua_global_manager_root,
)
from .ui_runtime_context import acquire_ui_runtime_context

_DB_METHODS = frozenset({"DBMgr", "GetConfigTable", "GetConfigTableByIdWithLog", "Inst_get"})


def classify_spirit_artifact_affix(value: int, maximum: int, ratio_percent: float, full: int = 0) -> dict[str, Any]:
    """按客户端分支顺序计算档位；full 非零只隐藏词缀，不改变数值档位。"""
    peak = math.floor(maximum * (ratio_percent * 0.01))
    tier = 3 if value >= peak else 2 if value > maximum else 1 if value == maximum else 0
    return {"affix": ("", "满", "巅", "巅")[tier] if full == 0 else "",
            "max_type": tier, "normal_max": maximum, "peak_max": peak,
            "affix_icon": ("", "lingqi_zw_word_0002", "lingqi_zw_word_1002", "lingqi_zw_word_1003")[tier] if full == 0 else "",
            "affix_visible": full == 0 and tier > 0}


def read_spirit_artifact_affix_rules(cleanse_ids: list[int]) -> dict[str, Any]:
    """只读已加载的 DBMgr 配置与真实字段索引，不执行游戏 Lua、不用旧导出推测阈值。"""
    ctx = acquire_ui_runtime_context([])
    reader = ctx.reader

    def config_tables(current_reader, address):
        manager = manager_index_fields(current_reader, address, _DB_METHODS)
        inst = current_reader.fields(manager.get("inst"))
        tables = current_reader.dictionary_fields(inst.get("ConfigDic"))
        if "SpiritWare.SpiritWareCleanse" not in tables:
            raise FanxiuRuntimeMemoryError("灵器洗炼配置尚未自然加载")
        return tables

    root, _, environment = resolve_lua_global_manager_root(
        ctx.memory, manager_key="spirit-artifact-affix-db", state_address=ctx.binding.state_address,
        global_name="DBMgr", required_methods=_DB_METHODS, validate=config_tables,
    )
    tables = config_tables(reader, root)
    env = reader.string_fields(environment, frozenset({"s_globalCfgIdx"}))
    indexes_root = reader.fields(env.get("s_globalCfgIdx"))
    row_cache = {}

    def row(group, name, key, wanted):
        table_key = f"{group}.{name}"
        if table_key not in row_cache:
            row_cache[table_key] = reader.fields(tables.get(table_key))
        table = row_cache[table_key]
        raw = table.get(key)
        indexes = reader.fields(reader.fields(indexes_root.get(group)).get(name))
        # Attribute 以行号存储；客户端 GetConfigTableByKeyAndId 按 id 列查询。
        if not isinstance(raw, LuaRef) and name == "Attribute":
            id_index = as_int(indexes.get("id"))
            for candidate in list(table.values()):
                if not isinstance(candidate, LuaRef) or candidate.kind != "table":
                    continue
                candidate_table = reader.table(candidate.address)
                candidate_fields = candidate_table["fields"]
                candidate_array = candidate_table["array"]
                candidate_id = candidate_fields.get("id", candidate_fields.get(id_index))
                if candidate_id is None and id_index is not None and id_index < len(candidate_array):
                    candidate_id = candidate_array[id_index]
                if isinstance(candidate_id, str):
                    table[candidate_id] = candidate
            raw = table.get(key)
        if not isinstance(raw, LuaRef):
            raise FanxiuRuntimeMemoryError(f"配置未加载：{group}.{name}[{key}]")
        fields = reader.fields(raw)
        array = reader.table(raw.address)["array"]
        result = {}
        for field in wanted:
            index = as_int(indexes.get(field))
            value = fields.get(field)
            if value is None and index is not None:
                value = fields.get(index)
                if value is None and index < len(array):
                    value = array[index]
            result[field] = value
        return result

    config = row("SpiritWare", "ConfigValue", "Item5_MaxRatio", ("value",))
    ratio = float(config["value"])
    from backend.core.fanxiu.catalog.lua_config import _find_default_lang_path, load_fanxiu_lang_map
    lang_path = _find_default_lang_path()
    lang = load_fanxiu_lang_map(lang_path) if lang_path else {}
    rules = {}
    for cleanse_id in sorted(set(cleanse_ids)):
        data = row("SpiritWare", "SpiritWareCleanse", cleanse_id, ("max", "full", "code", "selfName", "type"))
        maximum = as_int(data["max"])
        if maximum is None:
            raise FanxiuRuntimeMemoryError(f"词条 {cleanse_id} 缺少 max，不能判断词缀")
        name_value = data["selfName"] if data["type"] == 3 else row(
            "Attribute", "Attribute", data["code"], ("name",),
        )["name"]
        name = name_value if isinstance(name_value, str) else lang.get(as_int(name_value), "")
        if not name:
            raise FanxiuRuntimeMemoryError(f"词条 {cleanse_id} 的官方名称未解析")
        rules[cleanse_id] = {**data, "name": name, "max": maximum, "full": as_int(data["full"]) or 0}
    return {"ratio_percent": ratio, "rules": rules, "source": "runtime_dbmgr_spiritware_config",
            "pid": ctx.memory.pid, "process_start_ticks": ctx.memory.process_start_ticks}


def enrich_spirit_artifact_effects(effects: list[dict[str, Any]], *, pid: int, process_start_ticks: int) -> None:
    """在原始属性投影前补齐词缀及官方名称，防止 ID 取模猜名导致属性错列。"""
    config = read_spirit_artifact_affix_rules([effect["cleanse_id"] for effect in effects])
    if (pid, process_start_ticks) != (config["pid"], config["process_start_ticks"]):
        raise FanxiuRuntimeMemoryError("属性和词缀配置来自不同游戏进程")
    for effect in effects:
        rule = config["rules"][effect["cleanse_id"]]
        effect.update(classify_spirit_artifact_affix(effect["value"], rule["max"], config["ratio_percent"], rule["full"]))
        effect.update(name=rule["name"], code=rule["code"] or "", type=rule["type"])


def read_spirit_artifact_affix_runtime() -> dict[str, Any]:
    """读取每个部位当前/待保存属性的词缀和阈值，不执行 Lua 或游戏操作。"""
    from .spirit_artifact import read_spirit_artifact_cleanse_runtime
    return read_spirit_artifact_cleanse_runtime()
