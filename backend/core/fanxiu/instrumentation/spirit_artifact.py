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
    resolve_manager_root,
)


_RUNTIME_CACHE_SECONDS = 60.0
_BACKPACK_MARKER = b"LuaBackpackMgr"
_BACKPACK_METHODS = frozenset({"LuaBackpackMgr", "Inst_get"})
_ARTIFACTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("血晶摩诃剑", ("柄", "刃", "穗", "鞘", "珠", "纹")),
    ("天月落星幡", ("镜", "幅", "带", "杆", "印", "纹")),
    ("弥罗宝光幢", ("焰", "柱", "环", "座", "珠", "纹")),
    ("鸿古干天戈", ("锋", "芒", "珠", "坠", "柄", "气")),
    ("青暝岁月灯", ("盏", "芯", "穗", "杆", "纹", "荧")),
    ("苍烟神火炉", ("饰", "盖", "身", "柄", "光", "座")),
    ("御海镇神图", ("卷", "瑚", "海", "轴", "灵", "山")),
    ("六界轮回盘", ("珠", "盘", "焰", "环", "荧", "晶")),
)
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


def _memory_runtime_snapshot() -> dict[str, Any]:
    """Read exact live ItemVO/ext fields when the fixed main-state bridge is unavailable."""

    memory = MumuProcessMemory.discover_cached()
    root_address, cache_hit = resolve_manager_root(
        memory,
        manager_key="spirit-artifact-backpack",
        marker=_BACKPACK_MARKER,
        required_methods=_BACKPACK_METHODS,
        validate=_backpack_data_fields,
    )
    reader = LuaJitReader(memory)
    data = _backpack_data_fields(reader, root_address)
    values = _fields(
        reader,
        _fields(reader, data.get("_SpiritWareItemDic")).get("_valueTable_"),
    )
    selected: dict[tuple[int, int], tuple[tuple[int, int, int], dict[str, Any]]] = {}
    for raw_item in values.values():
        if not isinstance(raw_item, LuaRef) or raw_item.kind != "table":
            continue
        item = _fields(reader, raw_item)
        base_id = as_int(item.get("baseId")) or 0
        position = _artifact_position(base_id)
        if position is None:
            continue
        ext = _fields(reader, item.get("ext"))
        effects = _read_effect_map(
            reader, ext.get("attrMap"), artifact_index=position[0]
        )
        pending_effects = _read_effect_map(
            reader, ext.get("refineMap"), artifact_index=position[0]
        )
        part = {
            "ware_id": position[0] + 1,
            "part": position[1] + 1,
            "artifact_name": _ARTIFACTS[position[0]][0] if position[0] < len(_ARTIFACTS) else "",
            "part_name": _ARTIFACTS[position[0]][1][position[1]] if position[0] < len(_ARTIFACTS) else "",
            "item_id": str(reader.long(item.get("id")) or ""),
            "base_id": base_id,
            "grade": as_int(ext.get("grade")) or 0,
            "realm": as_int(ext.get("pinLevel")) or 0,
            "refine_num": as_int(ext.get("refineNum")) or 0,
            "is_break": bool(ext.get("isBreak")),
            "effects": effects,
            "pending_effects": pending_effects,
        }
        score = (part["grade"], base_id % 100, part["refine_num"])
        if position not in selected or score > selected[position][0]:
            selected[position] = score, part
    observed_artifacts = {position[0] for position in selected}
    complete = bool(observed_artifacts) and all(
        (artifact_index, part_index) in selected
        for artifact_index in observed_artifacts
        for part_index in range(6)
    )
    return {
        "complete": complete,
        "parts": [entry[1] for entry in selected.values()],
        "source": "runtime_memory_current_parts",
        "pid": memory.pid,
        "process_start_ticks": memory.process_start_ticks,
        "root_address": f"0x{root_address:x}",
        "root_cache_hit": cache_hit,
    }


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


def build_spirit_artifact_hall_from_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    """Build a runtime-driven hall projection without discarding exact game fields."""

    positioned: dict[tuple[int, int], dict[str, Any]] = {}
    for raw_part in runtime.get("parts") or []:
        part = dict(raw_part)
        ware_id = int(part.get("ware_id") or 0)
        part_number = int(part.get("part") or 0)
        position = (ware_id - 1, part_number - 1) if ware_id > 0 and 1 <= part_number <= 6 else None
        if position is None:
            position = _artifact_position(int(part.get("base_id") or 0))
        if position is None:
            continue
        positioned[position] = part
    artifact_indexes = sorted({artifact_index for artifact_index, _ in positioned})
    missing = []
    for artifact_index in artifact_indexes:
        fallback_name = _ARTIFACTS[artifact_index][0] if artifact_index < len(_ARTIFACTS) else f"灵器 {artifact_index + 1}"
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
        fallback = _ARTIFACTS[artifact_index] if artifact_index < len(_ARTIFACTS) else (f"灵器 {artifact_index + 1}", tuple(f"部位 {index}" for index in range(1, 7)))
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
            common, exclusive, peerless, effects = _effect_projection(
                artifact_name, list(part.get("effects") or []), exclusive_bases
            )
            _, _, _, pending_effects = _effect_projection(
                artifact_name,
                list(part.get("pending_effects") or []),
                exclusive_bases,
            )
            rows.append(
                {
                    "order": part_index + 1,
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
                    "runtime_ware_id": int(part.get("ware_id") or artifact_index + 1),
                    "runtime_part": int(part.get("part") or part_index + 1),
                    "runtime_refine_num": int(part.get("refine_num") or 0),
                    "runtime_is_break": bool(part.get("is_break")),
                    "runtime_effects": effects,
                    "runtime_pending_effects": pending_effects,
                }
            )
        artifacts.append({"order": artifact_index + 1, "name": artifact_name, "rows": rows})
    return {
        "artifacts": artifacts,
        "runtime_source": str(runtime.get("source") or "lua_main_state_server_sync"),
        "runtime_complete": True,
        "runtime_error": "",
        "runtime_updated_at": time.time(),
        "runtime_item_count": len(runtime.get("parts") or []),
        "runtime_equipped_count": len(positioned),
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
