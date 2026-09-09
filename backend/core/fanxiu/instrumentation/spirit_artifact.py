from __future__ import annotations

"""Project the game's exact equipped spirit-artifact snapshot for the hall UI."""

import threading
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from backend.core.fanxiu.instrumentation.spirit_artifact_runtime_loader import (
    refresh_spirit_artifact_runtime,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
)


_RUNTIME_CACHE_SECONDS = 60.0
_BACKPACK_METHODS = frozenset({"LuaBackpackMgr", "Inst_get"})
_COMMON_LABELS = {
    "混沌道威": ("chaos_power", 5_000),
    "混沌灵威": ("chaos_power", 5_000),
    "攻击": ("attack", 10_000),
    "灵力": ("spirit_power", 1_200_000),
    "气血": ("health", 1_200_000),
    "守御": ("defense", 10_000),
    "防御": ("defense", 10_000),
}
_COMMON_KEYS = ("chaos_power", "attack", "spirit_power", "health", "defense")
_EXCLUSIVE_BASES: dict[str, dict[str, int]] = {
    "血晶摩诃剑": {"暴击附伤": 10_000, "暴击": 30_000},
    "天月落星幡": {"功法附伤": 60_000, "招架": 30_000, "神通吸血": 10_000},
    "弥罗宝光幢": {"法宝附伤": 60_000, "炼体附伤": 60_000, "闪避": 30_000},
    "鸿古干天戈": {"灵兽附伤": 60_000, "仙语附伤": 60_000, "全技能减伤": 10_000},
    "青暝岁月灯": {"灵宝抵御": 24_000, "功法抵御": 24_000, "全技能减伤": 8_000},
    "苍烟神火炉": {"招架": 24_000, "灵兽附伤": 48_000, "法宝附伤": 48_000},
    "御海镇神图": {"仙语附伤": 48_000, "灵暴附伤": 8_000, "灵暴": 24_000},
    # 六界词条使用神识属性的新数值体系；旧页面没有可靠的百分比
    # 分母，因此主表展示游戏原值，展开行继续保留完整原始字段。
    "六界轮回盘": {
        "神识全技能增伤": 0,
        "神识暴击": 0,
        "神识暴击附伤": 0,
        "神识最终增伤": 0,
    },
}
_EXCLUSIVE_ID_NAMES: tuple[tuple[int, int, dict[int, str]], ...] = (
    (160_000, 180_000, {1: "暴击", 2: "暴击附伤"}),
    (200_000, 220_000, {0: "功法附伤", 1: "神通吸血", 2: "招架"}),
    (240_000, 260_000, {0: "法宝附伤", 1: "闪避", 2: "炼体附伤"}),
    (280_000, 300_000, {0: "灵兽附伤", 1: "全技能减伤", 2: "仙语附伤"}),
    (320_000, 340_000, {0: "灵宝抵御", 1: "全技能减伤", 2: "功法抵御"}),
    (350_000, 370_000, {0: "灵兽附伤", 1: "招架", 2: "法宝附伤"}),
    (400_000, 420_000, {0: "仙语附伤", 1: "灵暴", 2: "灵暴附伤"}),
    (
        420_000,
        430_000,
        {0: "神识全技能增伤", 1: "神识暴击", 2: "神识暴击附伤", 3: "神识最终增伤"},
    ),
)

_cache_lock = threading.Lock()
_cached_at = 0.0
_cached_snapshot: dict[str, Any] | None = None
_bridge_failed_process: tuple[int, int] | None = None
_bridge_failure_text = ""
_item_location_cache: dict[tuple[int, int, str], Any] = {}
_item_index_keys: dict[tuple[int, int, str], frozenset[Any]] = {}


def _artifact_position(base_id: int) -> tuple[int, int] | None:
    # 灵器部位的 baseId 以 14_00GGxx 编组，每六组组成一套灵器。
    # 这里故意不使用本地目录数量作为上限：新灵器会先出现在运行态背包，
    # 静态名称表只能作为旧版本/未加载配置时的显示兜底。
    if not 14_000_101 <= base_id < 15_000_000:
        return None
    group = (base_id - 14_000_000) // 100
    if group < 1:
        return None
    return (group - 1) // 6, (group - 1) % 6


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value)


def _backpack_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _BACKPACK_METHODS)
    data = _fields(
        reader,
        _fields(reader, _fields(reader, manager.get("inst")).get("Model")).get("BackpackData"),
    )
    values = _fields(
        reader,
        _fields(reader, data.get("_SpiritWareItemDic")).get("_valueTable_"),
    )
    if not values:
        raise FanxiuRuntimeMemoryError("灵器实例缓存尚未加载")
    return data


def _cleanse_name(artifact_index: int, cleanse_id: int) -> str:
    if cleanse_id in {1_000_001, 1_000_002}:
        return "灵器无双"
    if 100_000 <= cleanse_id < 110_000:
        return "混沌灵威"
    if 120_000 <= cleanse_id < 130_000:
        return "混沌道威"
    if 110_000 <= cleanse_id < 120_000 or 130_000 <= cleanse_id < 140_000:
        position = cleanse_id % 100
        if 1 <= position <= 24:
            return ("气血", "攻击", "守御", "灵力")[(position - 1) % 4]
    if 430_000 <= cleanse_id < 440_000 or 450_000 <= cleanse_id < 460_000:
        return ("气血", "攻击", "守御", "灵力")[cleanse_id % 10]
    if artifact_index < len(_EXCLUSIVE_ID_NAMES):
        lower, upper, names = _EXCLUSIVE_ID_NAMES[artifact_index]
        if lower <= cleanse_id < upper:
            return names.get(cleanse_id % 10 if artifact_index == 0 else cleanse_id % 3, "")
    return ""


def _read_effect_map(
    reader: LuaJitReader,
    value: Any,
    *,
    artifact_index: int,
) -> list[dict[str, Any]]:
    """Decode one committed or pending cleanse map without changing game state."""

    effects: list[dict[str, Any]] = []
    for raw_key, raw_effect in _fields(reader, value).items():
        effect = _fields(reader, raw_effect)
        cleanse_id = as_int(effect.get("cleanseId")) or as_int(raw_key) or 0
        effects.append(
            {
                "cleanse_id": cleanse_id,
                "value": as_int(effect.get("value")) or 0,
                "base_value": as_int(effect.get("baseValue")) or 0,
                "add_value": as_int(effect.get("addValue")) or 0,
                "quality": as_int(effect.get("quality")) or 0,
                "locked": bool(effect.get("isLock")),
                "name": _cleanse_name(artifact_index, cleanse_id),
                "type": 0,
                "code": "",
                "attribute_id": "",
                "attribute_name": "",
            }
        )
    return effects


def read_spirit_artifact_item_runtime(item_id: str, *, force_relocate: bool = False) -> dict[str, Any]:
    """只读指定实例的最新属性与锁，供 UI 动作精确验收；不选最高阶替代品。

    UI ItemInfoList 在锁回包后不重建，不能作为锁真值。这里直接读取当前
    BackpackData 中该实例的 attrMap/refineMap，避免全馆词缀配置投影开销。
    force_relocate=True 跳过实例位置缓存，重新查找同一实例，不切换目标。
    """
    from .ui_runtime_context import read_ui_runtime_snapshot

    # 锁回包/保存属性会分配新的 Lua 表。让实际消费该映射的观察器统一恢复，
    # 外层 UI 观察器刷新自己的映射不能修复另一次 acquire 的旧映射。
    def project(context):
        started = time.monotonic()
        result = _read_spirit_artifact_item_runtime(context, item_id, force_relocate=force_relocate)
        return {**result, 'runtime_timings': {**context.timings,
                'projection': time.monotonic() - started}, 'runtime_context_mode': context.cache_mode}

    # 公共 fast 上下文保留进程、Lua state 和当前根验证，不重建完整字符串索引。
    return read_ui_runtime_snapshot([], project, fast=True)


def _read_spirit_artifact_item_runtime(context, item_id: str, *, force_relocate: bool) -> dict[str, Any]:
    from .runtime_memory import resolve_lua_global_manager_root
    memory, reader = context.memory, context.reader
    phases = {}
    measured = time.monotonic()
    root, _, _ = resolve_lua_global_manager_root(
        memory, manager_key="spirit-artifact-item-global",
        state_address=context.binding.state_address, global_name="BackpackMgr",
        required_methods=frozenset({"Inst_get"}), validate=_backpack_data_fields,
    )
    phases['manager_root'] = time.monotonic() - measured
    measured = time.monotonic()
    data = _backpack_data_fields(reader, root)
    values = _fields(reader, _fields(reader, data.get("_SpiritWareItemDic")).get("_valueTable_"))
    phases['item_index'] = time.monotonic() - measured
    measured = time.monotonic()
    # 缓存当前字典的逻辑键，不能缓存洗炼回包即替换的 VO 指针。
    # 每次从新字典取当前对象并核验 UID；键消失/身份不符才全量定位。
    # 先复用键，再检查回包产生的新键；两者都失败才重新定位。
    location_key = (memory.pid, memory.process_start_ticks, str(item_id))
    cached_location = None if force_relocate else _item_location_cache.get(location_key)
    candidates = list(values.items())
    if cached_location is not None and cached_location in values:
        cached_item = _fields(reader, values[cached_location])
        if str(reader.long(cached_item.get("id"))) == str(item_id):
            candidates = [(cached_location, values[cached_location])]
    else:
        _item_location_cache.pop(location_key, None)
        # 回包可能同时替换 Long 字典键和 VO；只检查新增键即可找回该实例。
        # 变化不止一项或未命中时仍回退全量，不能把新键直接当成目标。
        prior_keys = None if force_relocate else _item_index_keys.get(location_key)
        if prior_keys is not None:
            changed = [key for key in values if key not in prior_keys]
            matches = [(key, values[key]) for key in changed
                       if str(reader.long(_fields(reader, values[key]).get('id'))) == str(item_id)]
            if len(matches) == 1:
                candidates = matches
    phases['location_cache'] = time.monotonic() - measured
    measured = time.monotonic()
    found = []
    for dictionary_key, raw in candidates:
        item = _fields(reader, raw)
        if str(reader.long(item.get("id"))) != str(item_id):
            continue
        position = _artifact_position(as_int(item.get("baseId")) or 0)
        if position is None:
            continue
        if len(_item_location_cache) >= 48:
            _item_location_cache.clear()
            _item_index_keys.clear()
        _item_location_cache[location_key] = dictionary_key
        _item_index_keys[location_key] = frozenset(values)
        ext = _fields(reader, item.get("ext"))
        found.append({"item_id": str(item_id), "base_id": as_int(item.get("baseId")),
                      "ware_id": position[0] + 1, "part": position[1] + 1,
                      "is_break": ext.get("isBreak") if type(ext.get("isBreak")) is bool else None,
                      "grade": as_int(ext.get("grade")),
                      "realm": as_int(ext.get("pinLevel")),
                      "quantity": as_int(item.get("num")),
                      "refine_num": as_int(ext.get("refineNum")) or 0,
                      # FillData 每次为候选创建新的属性 VO；内容相同不等于旧回包。
                      # 仅作同进程、同实例连续动作的观察版本，不作持久身份。
                      "pending_revision": sorted(
                          (str(k), v.address) for k, v in _fields(reader, ext.get("refineMap")).items()
                          if isinstance(v, LuaRef)),
                      "effects": _read_effect_map(reader, ext.get("attrMap"), artifact_index=position[0]),
                      "pending_effects": _read_effect_map(reader, ext.get("refineMap"), artifact_index=position[0])})
    if len(found) != 1:
        raise RuntimeError(f"灵器实例 {item_id} 必须唯一，实际 {len(found)}")
    for key in ("effects", "pending_effects"):
        found[0][key] = [{k: effect[k] for k in ("cleanse_id", "value", "quality", "locked")}
                         for effect in found[0][key]]
    phases['item_fields'] = time.monotonic() - measured
    return {**found[0], "pid": memory.pid, "process_start_ticks": memory.process_start_ticks,
            'runtime_projection_timings': phases, 'runtime_candidates': len(candidates)}


def read_spirit_artifact_inventory_runtime() -> dict[str, Any]:
    """只读全部灵器本体实例，保留同部位的低阶备件，不做最高阶去重。

    grade/realm/quantity 来自当前 ItemVO/ext，quality 来自已加载 Item.Item
    配置。调用方可按 ware_id、part、quality、grade 筛选重置本体；本接口
    不把升阶红点当作重置许可，也不声明实例是否已装配或可消耗。
    realm 未读到时保留 None，不能把缺失境数当作明确的0境材料。
    """
    from .ui_runtime_context import read_ui_runtime_snapshot

    # Purchasing adds new ItemVO allocations outside the cached memory map.
    # Recovery belongs to the shared provider; callers must not repeat a
    # purchase simply because its post-action inventory observation failed.
    return read_ui_runtime_snapshot([], _read_spirit_artifact_inventory_runtime)


def _read_spirit_artifact_inventory_runtime(ctx) -> dict[str, Any]:
    from .runtime_memory import resolve_lua_global_manager_root
    from .item_config import read_loaded_item_metadata

    reader = ctx.reader
    root, _, _ = resolve_lua_global_manager_root(
        ctx.memory, manager_key="spirit-artifact-item-global",
        state_address=ctx.binding.state_address, global_name="BackpackMgr",
        required_methods=frozenset({"Inst_get"}), validate=_backpack_data_fields,
    )

    def read_items():
        data = _backpack_data_fields(reader, root)
        values = _fields(reader, _fields(reader, data.get("_SpiritWareItemDic")).get("_valueTable_"))
        result = []
        seen = set()
        for raw in values.values():
            item = _fields(reader, raw)
            base_id = as_int(item.get("baseId"))
            position = _artifact_position(base_id or 0)
            item_id = str(reader.long(item.get("id")) or "")
            ext = _fields(reader, item.get("ext"))
            grade = as_int(ext.get("grade"))
            quantity = as_int(item.get("num"))
            if position is None or not item_id or item_id in seen or grade is None or quantity is None:
                raise FanxiuRuntimeMemoryError("灵器本体实例身份、阶数或数量不完整")
            if grade < 1 or quantity < 1:
                raise FanxiuRuntimeMemoryError("灵器本体阶数或数量无效")
            seen.add(item_id)
            result.append({"item_id": item_id, "base_id": base_id,
                           "ware_id": position[0] + 1, "part": position[1] + 1,
                           "grade": grade, "realm": as_int(ext.get("pinLevel")),
                           "is_break": ext.get("isBreak") if type(ext.get("isBreak")) is bool else None,
                           "quantity": quantity})
        return sorted(result, key=lambda row: (row["ware_id"], row["part"], row["item_id"]))

    items = read_items()
    metadata, status = read_loaded_item_metadata(
        [row["base_id"] for row in items], memory=ctx.memory, reader=reader,
        state_address=ctx.binding.state_address,
    )
    if not status["complete"] or any(metadata[row["base_id"]]["quality"] is None for row in items):
        raise FanxiuRuntimeMemoryError("灵器本体品质配置未完整加载")
    if read_items() != items:
        raise FanxiuRuntimeMemoryError("读取期间灵器本体库存发生变化，请重读")
    return {"items": [{**row, "quality": metadata[row["base_id"]]["quality"]} for row in items],
            "complete": True, "read_only": True, "captured_at": time.time(),
            "pid": ctx.memory.pid, "process_start_ticks": ctx.memory.process_start_ticks,
            "source": "runtime_spiritware_all_instances"}


def _memory_runtime_snapshot() -> dict[str, Any]:
    """Read exact server equipment and batch its current attributes.

    The equipped provider owns putUpSet and inventory association. A second
    fresh equipped observation brackets attribute enrichment; a changed set,
    process, or selected item metadata rejects the whole snapshot. Known empty
    slots are explicit, never inferred from missing inventory. This full-hall
    composition still needs live acceptance after replacing a high-grade item
    with a lower-grade spare and on changing the loaded ware universe.
    """
    from .spirit_artifact_equipped import read_spirit_artifact_equipped_runtime
    from .spirit_artifact_affixes import enrich_spirit_artifact_effects
    from .ui_runtime_context import read_ui_runtime_snapshot
    from .runtime_memory import resolve_lua_global_manager_root

    before = read_spirit_artifact_equipped_runtime()

    def read_selected(ctx):
        identity = (ctx.memory.pid, ctx.memory.process_start_ticks)
        if identity != (before['pid'], before['process_start_ticks']):
            raise FanxiuRuntimeMemoryError('读取灵器属性前游戏进程变化')
        reader = ctx.reader
        root, _, _ = resolve_lua_global_manager_root(
            ctx.memory, manager_key="spirit-artifact-item-global",
            state_address=ctx.binding.state_address, global_name="BackpackMgr",
            required_methods=frozenset({"Inst_get"}), validate=_backpack_data_fields,
        )
        data = _backpack_data_fields(reader, root)
        values = _fields(reader, _fields(reader, data.get("_SpiritWareItemDic")).get("_valueTable_"))
        wanted = {row['item_id']: row for row in before['items']}
        parts = []
        found = set()
        for raw_item in values.values():
            item = _fields(reader, raw_item)
            uid = str(reader.long(item.get('id')) or '')
            if uid not in wanted:
                continue
            if uid in found:
                raise FanxiuRuntimeMemoryError('已装配灵器本体库存实例重复')
            found.add(uid)
            expected = wanted[uid]
            ext = _fields(reader, item.get('ext'))
            base_id = as_int(item.get('baseId'))
            grade = as_int(ext.get('grade'))
            realm = as_int(ext.get('pinLevel')) or 0
            is_break = ext.get('isBreak') if type(ext.get('isBreak')) is bool else None
            if (base_id, grade, realm, is_break) != (
                expected['base_id'], expected['grade'], expected['realm'], expected['is_break']
            ):
                raise FanxiuRuntimeMemoryError('读取期间已装配灵器本体状态变化')
            index = expected['ware_id'] - 1
            parts.append({**expected,
                'refine_num': as_int(ext.get('refineNum')) or 0,
                'effects': _read_effect_map(reader, ext.get('attrMap'), artifact_index=index),
                'pending_effects': _read_effect_map(reader, ext.get('refineMap'), artifact_index=index),
            })
        if found != set(wanted):
            raise FanxiuRuntimeMemoryError('已装配灵器本体属性读取缺失')
        return parts

    parts = read_ui_runtime_snapshot([], read_selected)
    effects = [effect for part in parts for key in ('effects', 'pending_effects') for effect in part[key]]
    if effects:
        enrich_spirit_artifact_effects(effects, pid=before['pid'], process_start_ticks=before['process_start_ticks'])
    after = read_spirit_artifact_equipped_runtime()
    if any(before[key] != after[key] for key in ('pid', 'process_start_ticks', 'slots', 'items')):
        raise FanxiuRuntimeMemoryError('读取期间服务器装配集合或本体状态变化')
    for slot in before['slots']:
        if slot['item_id'] is None:
            parts.append({**slot, 'base_id': 0, 'grade': 0, 'realm': 0,
                          'is_break': False, 'effects': [], 'pending_effects': [], 'empty_slot': True})
    return {'complete': True, 'parts': parts,
            'ware_ids': sorted({slot['ware_id'] for slot in before['slots']}),
            'source': 'runtime_server_put_up_set_current_parts',
            'pid': before['pid'], 'process_start_ticks': before['process_start_ticks']}


def _format_percent(raw_value: int, base_value: int) -> str:
    if raw_value <= 0 or base_value <= 0:
        return ""
    percent = (Decimal(raw_value) * Decimal(100) / Decimal(base_value)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return f"{percent}%"


def _format_effect_value(raw_value: int, base_value: int) -> str:
    return _format_percent(raw_value, base_value) if base_value > 0 else (str(raw_value) if raw_value else "")


def _effect_projection(
    artifact_name: str,
    effects: list[dict[str, Any]],
    exclusive_bases: dict[str, int],
) -> tuple[dict[str, int], dict[str, int], list[int], list[dict[str, Any]]]:
    common = {key: 0 for key in _COMMON_KEYS}
    exclusive = {key: 0 for key in exclusive_bases}
    peerless = [0, 0]
    exact_effects: list[dict[str, Any]] = []
    for raw_effect in effects:
        effect = dict(raw_effect)
        cleanse_id = int(effect.get("cleanse_id") or 0)
        value = int(effect.get("value") or 0)
        official_name = str(effect.get("name") or effect.get("attribute_name") or "").strip()
        projection = ""
        base_value = 0
        if cleanse_id in {1_000_001, 1_000_002}:
            peerless[cleanse_id - 1_000_001] = int(
                (Decimal(value) / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            projection = f"artifact_peerless_{cleanse_id - 1_000_000}"
            effect_percent = f"{peerless[cleanse_id - 1_000_001]}%"
        elif official_name in _COMMON_LABELS:
            projection, base_value = _COMMON_LABELS[official_name]
            common[projection] += value
            effect_percent = _format_percent(value, base_value)
        elif official_name in exclusive:
            projection = official_name
            base_value = exclusive_bases[official_name]
            exclusive[official_name] += value
            effect_percent = _format_percent(value, base_value)
        else:
            effect_percent = ""
        exact_effects.append(
            {
                **effect,
                "official_name": official_name,
                "projection": projection,
                "projection_base_value": base_value,
                "percent": effect_percent,
            }
        )
    return common, exclusive, peerless, sorted(
        exact_effects, key=lambda item: int(item.get("cleanse_id") or 0)
    )


def project_spirit_artifact_part_row(
    part: dict[str, Any], *, artifact_name: str, part_name: str,
    exclusive_bases: dict[str, int] | None = None,
) -> dict[str, Any]:
    """纯投影单个已观察部件；与全馆展示共用，不读取 Runtime 或存储。"""
    ware_id, part_number = int(part["ware_id"]), int(part["part"])
    if ware_id <= 0 or part_number not in range(1, 7):
        raise ValueError("灵器或部位编号无效")
    if part.get("empty_slot") is True and any(part.get(key) for key in (
        "item_id", "base_id", "grade", "effects", "pending_effects",
    )):
        raise ValueError("明确空槽却含本体或属性，不能投影为初始零阶")
    if exclusive_bases is None:
        exclusive_bases = dict(_EXCLUSIVE_BASES.get(artifact_name) or {})
        if not exclusive_bases:
            for effect in part.get("effects") or []:
                name = str(effect.get("name") or effect.get("attribute_name") or "")
                if name and name not in _COMMON_LABELS and name != "灵器无双":
                    exclusive_bases.setdefault(name, 0)
    common, exclusive, peerless, effects = _effect_projection(
        artifact_name, list(part.get("effects") or []), exclusive_bases
    )
    _, _, _, pending_effects = _effect_projection(
        artifact_name,
        list(part.get("pending_effects") or []),
        exclusive_bases,
    )
    return {
        "order": part_number,
        "part_name": part_name,
        "rank": int(part.get("grade") or 0),
        "realm": int(part.get("realm") or 0),
        "artifact_peerless_1": peerless[0],
        "artifact_peerless_2": peerless[1],
        "chaos_power": _format_percent(common["chaos_power"], 5_000),
        "attack": _format_percent(common["attack"], 10_000),
        "spirit_power": _format_percent(common["spirit_power"], 1_200_000),
        "health": _format_percent(common["health"], 1_200_000),
        "defense": _format_percent(common["defense"], 10_000),
        "stat_raw_values": {
            key: str(value) if value else "" for key, value in common.items()
        },
        "exclusive_stats": {
            key: _format_effect_value(value, exclusive_bases[key])
            for key, value in exclusive.items()
        },
        "exclusive_stat_raw_values": {
            key: str(value) if value else "" for key, value in exclusive.items()
        },
        "runtime_base_id": int(part.get("base_id") or 0),
        "runtime_item_id": str(part.get("item_id") or ""),
        "runtime_ware_id": int(part.get("ware_id") or ware_id),
        "runtime_part": int(part.get("part") or part_number),
        "runtime_refine_num": int(part.get("refine_num") or 0),
        "runtime_is_break": part.get("is_break") if type(part.get("is_break")) is bool else None,
        "runtime_effects": effects,
        "runtime_pending_effects": pending_effects,
        "runtime_empty_slot": part.get("empty_slot") is True,
    }


def build_spirit_artifact_hall_from_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    """Build a runtime-driven hall projection without discarding exact game fields."""

    if runtime.get("complete") is not True:
        raise RuntimeError("服务器装配快照未证明完整")
    from ..catalog.spirit_artifact_identity import load_spirit_artifact_templates
    templates = load_spirit_artifact_templates()
    positioned: dict[tuple[int, int], dict[str, Any]] = {}
    used_ids: set[str] = set()
    for raw_part in runtime.get("parts") or []:
        part = dict(raw_part)
        ware_id = int(part.get("ware_id") or 0)
        part_number = int(part.get("part") or 0)
        position = (ware_id - 1, part_number - 1) if ware_id > 0 and 1 <= part_number <= 6 else None
        if position is None:
            position = _artifact_position(int(part.get("base_id") or 0))
        if position is None:
            raise RuntimeError("服务器装配部位身份无效")
        uid = str(part.get("item_id") or "")
        if position in positioned or (uid and uid in used_ids):
            raise RuntimeError("服务器装配部位或本体引用重复")
        if not uid and part.get("empty_slot") is not True:
            raise RuntimeError("服务器装配本体引用缺失，不能推断空槽")
        if uid:
            used_ids.add(uid)
        positioned[position] = part
    artifact_indexes = sorted({artifact_index for artifact_index, _ in positioned})
    if not artifact_indexes:
        raise RuntimeError("服务器装配灵器集合为空")
    if "ware_ids" in runtime and {index + 1 for index in artifact_indexes} != set(runtime["ware_ids"]):
        raise RuntimeError("服务器装配灵器集合不完整")
    missing = []
    for artifact_index in artifact_indexes:
        fallback_name = templates.get(artifact_index + 1, (f"灵器 {artifact_index + 1}", ()))[0]
        artifact_name = next(
            (
                str(part.get("artifact_name") or "").strip()
                for (index, _), part in positioned.items()
                if index == artifact_index and str(part.get("artifact_name") or "").strip()
            ),
            fallback_name,
        )
        for part_index in range(6):
            if (artifact_index, part_index) not in positioned:
                missing.append(f"{artifact_name}·部位 {part_index + 1}")
    if missing:
        raise RuntimeError(f"服务器装配引用不完整：缺少 {', '.join(missing)}")

    artifacts: list[dict[str, Any]] = []
    for artifact_index in artifact_indexes:
        fallback = templates.get(artifact_index + 1, (f"灵器 {artifact_index + 1}", tuple(f"部位 {index}" for index in range(1, 7))))
        first_part = positioned[(artifact_index, 0)]
        artifact_name = str(first_part.get("artifact_name") or fallback[0]).strip()
        exclusive_bases = dict(_EXCLUSIVE_BASES.get(artifact_name) or {})
        if not exclusive_bases:
            for part_index in range(6):
                for effect in positioned[(artifact_index, part_index)].get("effects") or []:
                    official_name = str(effect.get("name") or effect.get("attribute_name") or "").strip()
                    if official_name and official_name not in _COMMON_LABELS and official_name != "灵器无双":
                        exclusive_bases.setdefault(official_name, 0)
        rows: list[dict[str, Any]] = []
        for part_index in range(6):
            part = positioned[(artifact_index, part_index)]
            part_name = str(part.get("part_name") or fallback[1][part_index]).strip()
            rows.append(project_spirit_artifact_part_row(
                {**part, "ware_id": artifact_index + 1, "part": part_index + 1},
                artifact_name=artifact_name, part_name=part_name,
                exclusive_bases=exclusive_bases,
            ))
        artifacts.append({"order": artifact_index + 1, "name": artifact_name, "rows": rows})
    return {
        "artifacts": artifacts,
        "runtime_source": str(runtime.get("source") or "lua_main_state_server_sync"),
        "runtime_complete": True,
        "runtime_error": "",
        "runtime_updated_at": time.time(),
        "runtime_item_count": len(runtime.get("parts") or []),
        "runtime_equipped_count": len(used_ids),
        "runtime_debug": {
            "pid": runtime.get("pid"),
            "process_start_ticks": runtime.get("process_start_ticks"),
            "bridge_sha256": runtime.get("bridge_sha256"),
            "bridge_error": runtime.get("bridge_error"),
            "root_address": runtime.get("root_address"),
            "root_cache_hit": runtime.get("root_cache_hit"),
        },
    }


def read_spirit_artifact_hall_runtime(*, force: bool = False) -> dict[str, Any]:
    """Refresh and project the authoritative server-side equipped references."""

    global _cached_at, _cached_snapshot, _bridge_failed_process, _bridge_failure_text
    now = time.monotonic()
    with _cache_lock:
        if (
            not force
            and _cached_snapshot is not None
            and now - _cached_at <= _RUNTIME_CACHE_SECONDS
        ):
            return dict(_cached_snapshot)
        memory = MumuProcessMemory.discover_cached()
        process_identity = (memory.pid, memory.process_start_ticks)
        if process_identity == _bridge_failed_process:
            runtime = _memory_runtime_snapshot()
            runtime["bridge_error"] = _bridge_failure_text
        else:
            try:
                runtime = refresh_spirit_artifact_runtime()
                _bridge_failed_process = None
                _bridge_failure_text = ""
            except Exception as bridge_error:
                _bridge_failed_process = process_identity
                _bridge_failure_text = f"{type(bridge_error).__name__}: {bridge_error}"
                runtime = _memory_runtime_snapshot()
                runtime["bridge_error"] = _bridge_failure_text
        snapshot = build_spirit_artifact_hall_from_runtime(runtime)
        _cached_snapshot = snapshot
        _cached_at = time.monotonic()
        return dict(snapshot)


def read_spirit_artifact_cleanse_runtime() -> dict[str, Any]:
    """Return a fresh, strictly read-only cleanse snapshot including ``refineMap``.

    This deliberately bypasses the 60-second hall cache and the optional bridge:
    a pending native-auto candidate is short-lived and must be read from the same
    live process immediately before any future save/cancel decision.
    """

    return build_spirit_artifact_hall_from_runtime(_memory_runtime_snapshot())
