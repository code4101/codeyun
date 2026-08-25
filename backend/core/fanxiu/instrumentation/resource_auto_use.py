from __future__ import annotations

"""Strict read-only projections for broad resource auto-use buttons.

The game buttons consume a *set* of resources.  A red dot, OCR label, or the
fact that the native method returned a non-empty list is not enough evidence
to click them safely.  These projectors reconstruct every candidate and every
configured material from already-read state.  Any missing config, ambiguous
material, premium currency, self-select item, or shared-inventory overcommit
makes the snapshot incomplete (fail closed).

No function in this module attaches to the game, executes Lua, sends a packet,
or clicks the UI.  Live-memory adapters may feed their decoded rows into these
pure functions after they have proved that all required tables are loaded.
"""

import json
import re
import struct
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.backpack import _backpack_data_fields
from backend.core.fanxiu.instrumentation.magic_treasure import (
    _owned_talisman_rows,
    _talisman_data_fields,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
)


PET_NATIVE_SOURCE = "PetData.CheckPetCardUpCount"
TALISMAN_NATIVE_SOURCE = "TalismanModel.GetAllUpgradeableTalismanList"

_DIRECT_ITEM_CONSUME_RE = re.compile(
    r"^Item\|(?P<item_id>[1-9]\d*)_(?P<quantity>[1-9]\d*)$"
)
_SELF_SELECT_TERMS = ("自选", "任选", "可选", "选择礼包", "任选礼包")
_CASH_TERMS = (
    "现金",
    "充值",
    "仙玉",
    "元宝",
    "灵石",
    "货币",
    "宗门资金",
)
_ALLOWED_TALISMAN_CATEGORIES = frozenset({"法宝", "先天古宝", "后天古宝"})
_PET_METHODS = frozenset({"Inst_get"})
_DB_METHODS = frozenset(
    {"DBMgr", "GetConfigTable", "GetConfigTableByIdWithLog", "Inst_get"}
)
_BACKPACK_METHODS = frozenset({"Inst_get"})
_PET_CONFIG_TABLE = "Pet.Pet"
_PET_LEVEL_CONFIG_TABLE = "Pet.PetAtlasLevel"
_TALISMAN_STATIC_DEFAULTS = {"talismanType": 0}
_TALISMAN_PIN_STATIC_DEFAULTS = {"level": 0, "pin": 0}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def parse_direct_item_consume(value: Any) -> tuple[int, int] | None:
    """Parse the only currently authorized direct material grammar.

    Expressions with alternatives, currencies, compound rewards, or unknown
    prefixes are intentionally rejected.  They need a separately proven
    semantic decoder before they can be auto-consumed.
    """

    match = _DIRECT_ITEM_CONSUME_RE.fullmatch(str(value or "").strip())
    if not match:
        return None
    return int(match.group("item_id")), int(match.group("quantity"))


def classify_direct_material(
    item_id: int,
    metadata: Mapping[str, Any] | None,
    *,
    allowed_kind: str,
) -> dict[str, Any]:
    """Classify one configured item material, failing closed on weak metadata."""

    item_id = _positive_int(item_id) or 0
    row = dict(metadata or {})
    row_id = _positive_int(row.get("id"))
    name = str(row.get("name") or row.get("name_plain") or "").strip()
    evidence = " ".join(
        str(row.get(key) or "")
        for key in (
            "name",
            "name_plain",
            "type_name",
            "sub_type_name",
            "type_sub_type_name",
            "description",
            "descript_plain",
        )
    )
    if not item_id or row_id != item_id or not name:
        kind = "unknown"
        reason = "缺少与 item_id 一致的版本化道具元数据"
    elif any(term in evidence for term in _SELF_SELECT_TERMS):
        kind = "self_select"
        reason = "道具文案表明它是自选/任选资源"
    elif any(term in evidence for term in _CASH_TERMS):
        kind = "cash"
        reason = "道具文案表明它是现金或货币资源"
    else:
        kind = allowed_kind
        reason = "配置直接引用的普通道具材料"
    return {
        "item_id": item_id,
        "item_name": name,
        "kind": kind,
        "classification_reason": reason,
    }


def _material_metadata(
    catalog_by_id: Mapping[Any, Mapping[str, Any]], item_id: int
) -> Mapping[str, Any] | None:
    return catalog_by_id.get(item_id) or catalog_by_id.get(str(item_id))


def _error_snapshot(
    source: str,
    errors: list[str],
    *,
    candidates: list[dict[str, Any]] | None = None,
    excluded_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    projected = list(candidates or [])
    return {
        "complete": False,
        "read_only": True,
        "source": source,
        "candidates": projected,
        "candidate_count": len(projected),
        "excluded_candidates": list(excluded_candidates or []),
        "errors": errors,
    }


def _validate_inventory(inventory: Mapping[Any, Any]) -> tuple[dict[int, int], list[str]]:
    counts: dict[int, int] = {}
    errors: list[str] = []
    for raw_id, raw_count in inventory.items():
        item_id = _positive_int(raw_id)
        count = _nonnegative_int(raw_count)
        if item_id is None or count is None:
            errors.append(f"背包计数无效：item_id={raw_id!r}, count={raw_count!r}")
            continue
        counts[item_id] = count
    return counts, errors


def project_pet_quick_swallow_candidates(
    *,
    pets: Iterable[Mapping[str, Any]],
    pet_configs: Mapping[Any, Mapping[str, Any]],
    level_rows: Iterable[Mapping[str, Any]],
    inventory: Mapping[Any, Any],
    item_catalog: Mapping[Any, Mapping[str, Any]],
) -> dict[str, Any]:
    """Reconstruct the ordinary-pet quick-swallow candidate set.

    ``pet_configs`` must contain an explicit ``therion_type`` for every owned
    pet.  This is important: ``PetData._PetLevelCfgDic`` alone cannot prove
    that a pet is ordinary, so a memory adapter must not silently infer zero.
    """

    counts, errors = _validate_inventory(inventory)
    configs = {
        pet_id: dict(row)
        for raw_id, row in pet_configs.items()
        if (pet_id := _positive_int(raw_id)) is not None
    }
    levels: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    for raw in level_rows:
        row = dict(raw)
        pet_id = _positive_int(row.get("pet_id"))
        level = _positive_int(row.get("level"))
        if pet_id is None or level is None or level in levels.get(pet_id, {}):
            errors.append(f"灵兽升阶配置身份无效或重复：{row!r}")
            continue
        consume = parse_direct_item_consume(row.get("consume"))
        if consume is None:
            item_id = _positive_int(row.get("item_id"))
            quantity = _positive_int(row.get("quantity") or row.get("item_num"))
            consume = (item_id, quantity) if item_id and quantity else None
        if consume is None:
            errors.append(f"灵兽 {pet_id} 第 {level} 阶消耗表达式未知")
            continue
        levels[pet_id][level] = {"item_id": consume[0], "quantity": consume[1]}

    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    aggregate = Counter()
    seen: set[int] = set()
    entity_progress: dict[int, int] = {}
    for raw in pets:
        pet = dict(raw)
        pet_id = _positive_int(pet.get("pet_id"))
        current_level = _nonnegative_int(pet.get("level"))
        if pet_id is None or pet_id in seen or current_level is None:
            errors.append(f"已拥有灵兽身份无效或重复：{pet!r}")
            continue
        seen.add(pet_id)
        entity_progress[pet_id] = current_level
        config = configs.get(pet_id)
        therion_type = _nonnegative_int((config or {}).get("therion_type"))
        if config is None or therion_type is None:
            errors.append(f"灵兽 {pet_id} 缺少明确 therion_type 配置")
            continue
        if therion_type != 0:
            # The native BeastBag quick button excludes holy beasts entirely.
            excluded.append(
                {
                    "pet_id": pet_id,
                    "therion_type": therion_type,
                    "reason": "holy_beast_outside_native_ordinary_pet_batch",
                }
            )
            continue
        pet_levels = levels.get(pet_id) or {}
        if not pet_levels:
            errors.append(f"普通灵兽 {pet_id} 的升阶配置未加载")
            continue
        max_level = max(pet_levels)
        if current_level >= max_level:
            continue

        remaining_by_item = dict(counts)
        consumed = Counter()
        upgrade_count = 0
        first_item_id: int | None = None
        for target_level in range(current_level + 1, max_level + 1):
            cost = pet_levels.get(target_level)
            if cost is None:
                errors.append(f"普通灵兽 {pet_id} 缺少连续的第 {target_level} 阶配置")
                break
            item_id = int(cost["item_id"])
            quantity = int(cost["quantity"])
            if first_item_id is None:
                first_item_id = item_id
            elif item_id != first_item_id:
                # Native CheckPetCardUpCount only reads the first item's count
                # once.  A material switch would make its later arithmetic
                # ambiguous, so do not claim an exact projection.
                errors.append(f"普通灵兽 {pet_id} 的连续升阶材料发生切换")
                break
            if remaining_by_item.get(item_id, 0) < quantity:
                break
            remaining_by_item[item_id] -= quantity
            consumed[item_id] += quantity
            upgrade_count += 1
        if upgrade_count <= 0:
            continue

        resources: list[dict[str, Any]] = []
        for item_id, quantity in sorted(consumed.items()):
            material = classify_direct_material(
                item_id,
                _material_metadata(item_catalog, item_id),
                allowed_kind="ordinary_pet_upgrade_item",
            )
            material.update(quantity=quantity, available=counts.get(item_id, 0))
            if material["kind"] != "ordinary_pet_upgrade_item":
                errors.append(
                    f"灵兽 {pet_id} 的材料 {item_id} 未通过安全分类："
                    f"{material['kind']}"
                )
            resources.append(material)
            aggregate[item_id] += quantity
        candidates.append(
            {
                "pet_id": pet_id,
                "therion_type": therion_type,
                "owned": True,
                "current_level": current_level,
                "upgrade_count": upgrade_count,
                "target_level": current_level + upgrade_count,
                "resources": resources,
            }
        )

    for item_id, requested in aggregate.items():
        if requested > counts.get(item_id, 0):
            errors.append(
                f"灵兽批量候选共享材料 {item_id} 超卖："
                f"requested={requested}, available={counts.get(item_id, 0)}"
            )
    if errors:
        snapshot = _error_snapshot(
            PET_NATIVE_SOURCE,
            errors,
            candidates=candidates,
            excluded_candidates=excluded,
        )
        snapshot["unsafe_candidate_count"] = len(candidates)
        return snapshot
    return {
        "complete": True,
        "read_only": True,
        "source": PET_NATIVE_SOURCE,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "excluded_candidates": excluded,
        "material_totals": dict(sorted(aggregate.items())),
        # Post-action verification must not infer success merely because a
        # candidate disappeared.  Keep the complete owned-pet level map and
        # the exact relevant inventory counts in every complete projection.
        "entity_progress": dict(sorted(entity_progress.items())),
        "inventory_counts": dict(sorted(counts.items())),
    }


def project_talisman_quick_upgrade_candidates(
    *,
    talismans: Iterable[Mapping[str, Any]],
    grade_rows: Iterable[Mapping[str, Any]],
    inventory: Mapping[Any, Any],
    item_catalog: Mapping[Any, Mapping[str, Any]],
    max_batch_stages: int = 50,
) -> dict[str, Any]:
    """Reconstruct the native all-upgradeable-talisman batch."""

    counts, errors = _validate_inventory(inventory)
    if _positive_int(max_batch_stages) is None or max_batch_stages > 50:
        errors.append("法宝单批阶数上限必须在 1..50")
    grades: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    for raw in grade_rows:
        row = dict(raw)
        talisman_id = _positive_int(row.get("talisman_id"))
        stage = _nonnegative_int(row.get("stage"))
        if talisman_id is None or stage is None or stage in grades.get(talisman_id, {}):
            errors.append(f"法宝升阶配置身份无效或重复：{row!r}")
            continue
        consume = parse_direct_item_consume(row.get("consume"))
        if consume is None:
            if row.get("consume") in (None, ""):
                # Generated max-stage rows legitimately omit consume.  They
                # are terminal config, not an unknown resource expression.
                grades[talisman_id][stage] = {"terminal": True}
                continue
            errors.append(f"法宝 {talisman_id} 第 {stage} 阶消耗表达式未知")
            continue
        grades[talisman_id][stage] = {
            "item_id": consume[0],
            "quantity": consume[1],
        }

    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    aggregate = Counter()
    seen: set[int] = set()
    for raw in talismans:
        talisman = dict(raw)
        talisman_id = _positive_int(talisman.get("talisman_id"))
        stage = _nonnegative_int(talisman.get("stage"))
        category = str(talisman.get("category") or "")
        active = talisman.get("active") is True
        if talisman_id is None or talisman_id in seen or stage is None:
            errors.append(f"已拥有法宝身份无效或重复：{talisman!r}")
            continue
        seen.add(talisman_id)
        if category not in _ALLOWED_TALISMAN_CATEGORIES:
            errors.append(f"法宝 {talisman_id} 的类别未知：{category or 'empty'}")
            continue
        if not active:
            excluded.append(
                {
                    "talisman_id": talisman_id,
                    "category": category,
                    "reason": "inactive_outside_native_upgradeable_batch",
                }
            )
            continue
        rows = grades.get(talisman_id) or {}
        if stage not in rows:
            if rows and stage >= max(rows):
                # Some families omit the final row altogether.  Runtime stage
                # at/above the greatest configured stage is maxed, therefore
                # outside IsCanUpGradeItemById.
                continue
            errors.append(f"已激活法宝 {talisman_id} 缺少当前第 {stage} 阶配置")
            continue

        remaining_by_item = dict(counts)
        consumed = Counter()
        upgrade_count = 0
        for current_stage in range(stage, stage + max_batch_stages):
            cost = rows.get(current_stage)
            if cost is None:
                # No next row after at least one projected upgrade is a normal
                # maximum-stage boundary.  Missing the current row is handled
                # above; a gap inside a known range is unsafe.
                higher = any(value > current_stage for value in rows)
                if higher:
                    errors.append(
                        f"法宝 {talisman_id} 缺少连续的第 {current_stage} 阶配置"
                    )
                break
            if cost.get("terminal"):
                break
            # The current grade row stores the cost for moving to the next
            # grade.  Native IsCanUpGradeItemById rejects a max-grade object
            # before building the batch, so a missing next row is terminal.
            if current_stage + 1 not in rows:
                break
            item_id = int(cost["item_id"])
            quantity = int(cost["quantity"])
            if remaining_by_item.get(item_id, 0) < quantity:
                break
            remaining_by_item[item_id] -= quantity
            consumed[item_id] += quantity
            upgrade_count += 1
        if upgrade_count <= 0:
            continue

        resources: list[dict[str, Any]] = []
        for item_id, quantity in sorted(consumed.items()):
            material = classify_direct_material(
                item_id,
                _material_metadata(item_catalog, item_id),
                allowed_kind="talisman_upgrade_material",
            )
            material.update(quantity=quantity, available=counts.get(item_id, 0))
            if material["kind"] != "talisman_upgrade_material":
                errors.append(
                    f"法宝 {talisman_id} 的材料 {item_id} 未通过安全分类："
                    f"{material['kind']}"
                )
            resources.append(material)
            aggregate[item_id] += quantity
        candidates.append(
            {
                "talisman_id": talisman_id,
                "category": category,
                "owned": True,
                "active": True,
                "before_stage": stage,
                "upgrade_count": upgrade_count,
                "target_stage": stage + upgrade_count,
                "resources": resources,
            }
        )

    for item_id, requested in aggregate.items():
        if requested > counts.get(item_id, 0):
            errors.append(
                f"法宝批量候选共享材料 {item_id} 超卖："
                f"requested={requested}, available={counts.get(item_id, 0)}"
            )
    if errors:
        snapshot = _error_snapshot(
            TALISMAN_NATIVE_SOURCE,
            errors,
            candidates=candidates,
            excluded_candidates=excluded,
        )
        snapshot["unsafe_candidate_count"] = len(candidates)
        return snapshot
    return {
        "complete": True,
        "read_only": True,
        "source": TALISMAN_NATIVE_SOURCE,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "excluded_candidates": excluded,
        "material_totals": dict(sorted(aggregate.items())),
        "max_batch_stages": max_batch_stages,
    }


def _not_loaded(message: str) -> FanxiuRuntimeMemoryError:
    return FanxiuRuntimeMemoryError(message, code="data_not_loaded")


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value)


def _pet_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _PET_METHODS)
    instance = _fields(reader, manager.get("inst"))
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("PetData"))
    if not data or data.get("_PetInfoVo") is None:
        raise _not_loaded("PetMgr.PetData 灵兽同步数据尚未自然加载")
    if data.get("_PetLevelCfgDic") is None:
        raise _not_loaded("PetData._PetLevelCfgDic 尚未由灵兽界面自然预热")
    return data


def _decode_owned_pets(
    reader: LuaJitReader, data: Mapping[Any, Any]
) -> list[dict[str, int]]:
    info = _fields(reader, data.get("_PetInfoVo"))
    raw_rows, declared = reader.list_items(info.get("petInfoVOList"))
    if declared is None:
        raise _not_loaded("PetData.petInfoVOList 尚未形成完整 CList")
    if len(raw_rows) != declared:
        raise FanxiuRuntimeMemoryError(
            f"灵兽列表不完整：declared={declared}, rows={len(raw_rows)}"
        )
    result: list[dict[str, int]] = []
    seen: set[int] = set()
    for raw in raw_rows:
        row = _fields(reader, raw)
        pet_id = _positive_int(row.get("petId"))
        level = _nonnegative_int(row.get("level"))
        if pet_id is None or level is None or pet_id in seen:
            raise FanxiuRuntimeMemoryError("灵兽 Runtime 行身份无效或重复")
        seen.add(pet_id)
        result.append({"pet_id": pet_id, "level": level})
    return result


def _pet_dictionary_fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    """Decode one PetData Dictionary, including its proven 4097-slot form.

    The ordinary-pet atlas contains 4000 consecutive levels.  Its native
    ``Dictionary`` storage is a Lua array with 4097 allocated slots (index 0
    plus 4000 values and spare capacity).  That is one slot above the generic
    Lua table reader's deliberately conservative 4096 limit.  Keep this
    exception local to the Pet reader and accept it only when the wrapper's
    declared count and the complete contiguous key set prove the structure.
    """

    try:
        return reader.dictionary_fields(value)
    except FanxiuRuntimeMemoryError as exc:
        if "Lua table 结构越界" not in str(exc):
            raise

    wrapper = _fields(reader, value)
    declared = _positive_int(wrapper.get("LuaDic_count"))
    storage = wrapper.get("_dt_")
    if (
        declared != 4000
        or not isinstance(storage, LuaRef)
        or storage.kind != "table"
    ):
        raise FanxiuRuntimeMemoryError("灵兽升阶 Dictionary 越界结构未通过身份校验")
    header = reader.memory.read(storage.address, 64)
    array_address = struct.unpack_from("<Q", header, 16)[0]
    array_size, hash_mask = struct.unpack_from("<II", header, 48)
    if array_size != 4097 or hash_mask != 0:
        raise FanxiuRuntimeMemoryError(
            "灵兽升阶 Dictionary 不是已证明的 4000 行数组布局"
        )
    if reader.memory.readable_region(array_address, array_size * 8) is None:
        raise FanxiuRuntimeMemoryError("灵兽升阶 Dictionary 数组地址不可读")
    # ``hash_mask=0`` uses LuaJIT's shared nil-node sentinel.  The bridge does
    # not expose that read-only sentinel mapping, so completeness is proven by
    # the wrapper count and exact array key set below instead of dereferencing
    # the sentinel.
    raw_array = reader.memory.read(
        array_address, array_size * 8, max_size=64 * 1024
    )
    result = {
        index: decoded
        for index in range(array_size)
        if (
            decoded := reader.value(
                struct.unpack_from("<Q", raw_array, index * 8)[0]
            )
        )
        is not None
    }
    if set(result) != set(range(1, declared + 1)):
        raise FanxiuRuntimeMemoryError(
            "灵兽升阶 Dictionary 的声明行数与连续键集合不一致"
        )
    return result


def _pet_level_cursors(
    reader: LuaJitReader,
    data: Mapping[Any, Any],
    *,
    pets: Iterable[Mapping[str, Any]],
    wanted_ids: set[int],
) -> dict[int, dict[str, Any]]:
    """Bind each owned ordinary pet to its validated level table.

    Only the first not-yet-reached row is decoded here (or the terminal row
    when already maxed, to preserve a stable inventory identity).  The native
    method stops as soon as the first material is exhausted, so eagerly
    decoding all 4000 rows for every ordinary pet adds minutes of remote reads
    without proving any additional candidate.
    """

    current_levels = {
        int(row["pet_id"]): int(row["level"])
        for row in pets
        if int(row["pet_id"]) in wanted_ids
    }
    result: dict[int, dict[str, Any]] = {}
    try:
        outer = reader.dictionary_fields(data.get("_PetLevelCfgDic"))
    except FanxiuRuntimeMemoryError as exc:
        raise _not_loaded(
            f"PetData._PetLevelCfgDic 当前不是可完整读取的自然加载快照：{exc}"
        ) from exc
    if not outer:
        raise _not_loaded("PetData._PetLevelCfgDic 为空，需自然打开灵兽成长页")
    covered: set[int] = set()
    for raw_pet_id, raw_levels in outer.items():
        pet_id = _positive_int(raw_pet_id)
        if pet_id is None:
            raise FanxiuRuntimeMemoryError("灵兽升阶缓存包含无效 petId")
        if pet_id not in wanted_ids:
            continue
        covered.add(pet_id)
        try:
            rows = _pet_dictionary_fields(reader, raw_levels)
        except FanxiuRuntimeMemoryError as exc:
            raise _not_loaded(
                f"灵兽 {pet_id} 的升阶缓存仍在加载或已失效：{exc}"
            ) from exc
        if not rows:
            raise FanxiuRuntimeMemoryError(f"灵兽 {pet_id} 的升阶缓存为空")
        numeric_rows = {
            level: raw_value
            for raw_level, raw_value in rows.items()
            if (level := _positive_int(raw_level)) is not None
        }
        max_level = max(numeric_rows, default=0)
        if set(numeric_rows) != set(range(1, max_level + 1)):
            raise FanxiuRuntimeMemoryError(
                f"普通灵兽 {pet_id} 的升阶缓存键不连续"
            )
        current_level = current_levels[pet_id]
        first_item_id: int | None = None
        first_item_num: int | None = None
        if current_level < max_level:
            first_fields = _fields(reader, numeric_rows[current_level + 1])
            first_item_id = _positive_int(first_fields.get("itemId"))
            first_item_num = _positive_int(first_fields.get("itemNum"))
            if first_item_id is None or first_item_num is None:
                raise FanxiuRuntimeMemoryError(
                    f"灵兽 {pet_id} 的下一阶缓存字段不完整"
                )
        else:
            # Preserve the material identity after a batch reaches max level.
            # Otherwise the post-read inventory key set would shrink and an
            # exact before/after consumption proof would become impossible.
            terminal_fields = _fields(reader, numeric_rows[max_level])
            first_item_id = _positive_int(terminal_fields.get("itemId"))
            first_item_num = _positive_int(terminal_fields.get("itemNum"))
            if first_item_id is None or first_item_num is None:
                raise FanxiuRuntimeMemoryError(
                    f"灵兽 {pet_id} 终阶缓存字段不完整"
                )
        result[pet_id] = {
            "current_level": current_level,
            "max_level": max_level,
            "rows": numeric_rows,
            "first_item_id": first_item_id,
            "first_item_num": first_item_num,
        }
    missing = sorted(wanted_ids - covered)
    if missing:
        raise _not_loaded(f"灵兽升阶缓存未覆盖已拥有普通灵兽：missing={missing}")
    return result


def _pet_level_rows_for_inventory(
    reader: LuaJitReader,
    cursors: Mapping[int, Mapping[str, Any]],
    inventory: Mapping[int, int],
) -> list[dict[str, int]]:
    """Decode exactly the prefix visited by native CheckPetCardUpCount."""

    result: list[dict[str, int]] = []
    for pet_id, cursor in cursors.items():
        current_level = int(cursor["current_level"])
        max_level = int(cursor["max_level"])
        first_item_id = _positive_int(cursor.get("first_item_id"))
        rows = cursor["rows"]
        if current_level > max_level:
            raise FanxiuRuntimeMemoryError(
                f"灵兽 {pet_id} 当前等级超过配置上限"
            )
        if current_level == max_level:
            fields = _fields(reader, rows[max_level])
            item_id = _positive_int(fields.get("itemId"))
            item_num = _positive_int(fields.get("itemNum"))
            if item_id is None or item_num is None:
                raise FanxiuRuntimeMemoryError(
                    f"灵兽 {pet_id} 终阶缓存字段不完整"
                )
            result.append(
                {
                    "pet_id": pet_id,
                    "level": max_level,
                    "item_id": item_id,
                    "item_num": item_num,
                }
            )
            continue
        if first_item_id is None:
            raise FanxiuRuntimeMemoryError(
                f"灵兽 {pet_id} 缺少下一阶材料身份"
            )
        available = int(inventory.get(first_item_id, 0))
        for level in range(current_level + 1, max_level + 1):
            fields = _fields(reader, rows[level])
            item_id = _positive_int(fields.get("itemId"))
            item_num = _positive_int(fields.get("itemNum"))
            if item_id is None or item_num is None:
                raise FanxiuRuntimeMemoryError(
                    f"灵兽 {pet_id} 第 {level} 阶缓存字段不完整"
                )
            result.append(
                {
                    "pet_id": pet_id,
                    "level": level,
                    "item_id": item_id,
                    "item_num": item_num,
                }
            )
            # CheckPetCardUpCount reads the first material's inventory once.
            # A later switch is retained in the prefix so the pure projector
            # can reject the ambiguity instead of silently changing materials.
            if item_id != first_item_id or available < item_num:
                break
            available -= item_num
    return result


def _runtime_config_null_defaults(
    reader: LuaJitReader,
    table: Mapping[Any, Any],
    indexes: Mapping[str, int],
) -> dict[str, Any]:
    """Read generated config ``_key2null`` through its shared __index closure.

    Packed rows omit values equal to their generated defaults.  Reading only
    the row array would therefore turn an explicit runtime default such as
    ``therionType=0`` into an unsafe guess.  The shipped LuaJIT target stores
    the closure's GCupval pointers at ``GCfuncL + 0x28`` and each closed TValue
    at ``GCupval + 0x10``.  We identify (rather than positionally assume) the
    captured index map and default array, and fail closed on any ambiguity.
    """

    rows = [value for value in table.values() if isinstance(value, LuaRef)]
    if not rows:
        raise _not_loaded("Runtime 配置表没有可验证的 packed row")
    first = reader.table(rows[0].address)
    metatable = int(first.get("metatable") or 0)
    if not metatable:
        raise FanxiuRuntimeMemoryError("Runtime packed 配置行缺少 metatable")
    for row in rows[1:16]:
        if int(reader.table(row.address).get("metatable") or 0) != metatable:
            raise FanxiuRuntimeMemoryError("Runtime packed 配置行 metatable 不一致")
    index_function = reader.table(metatable)["fields"].get("__index")
    if not isinstance(index_function, LuaRef) or index_function.kind != "function":
        raise FanxiuRuntimeMemoryError("Runtime packed 配置缺少 __index closure")
    function_header = reader.memory.read(index_function.address, 12)
    upvalue_count = function_header[11]
    if not 2 <= upvalue_count <= 8:
        raise FanxiuRuntimeMemoryError(
            f"Runtime packed 配置 closure upvalue 数异常：{upvalue_count}"
        )
    raw_pointers = reader.memory.read(
        index_function.address + 0x28, upvalue_count * 8
    )
    captured_tables: list[dict[str, Any]] = []
    for offset in range(0, len(raw_pointers), 8):
        pointer = struct.unpack_from("<Q", raw_pointers, offset)[0]
        try:
            value_raw = struct.unpack(
                "<Q", reader.memory.read(pointer + 0x10, 8)
            )[0]
            value = reader.value(value_raw)
            if not isinstance(value, LuaRef) or value.kind != "table":
                continue
            decoded = reader.table(value.address)
        except FanxiuRuntimeMemoryError:
            continue
        captured_tables.append(decoded)

    index_matches = [
        item
        for item in captured_tables
        if all(as_int(item["fields"].get(key)) == index for key, index in indexes.items())
    ]
    if len(index_matches) != 1:
        raise FanxiuRuntimeMemoryError("Runtime packed 配置 index map 无法唯一验证")
    max_index = max(indexes.values())
    # The generated null-default table contains textual defaults for textual
    # columns; the adjacent type-code table contains only numeric 0/1 values.
    default_matches = [
        item
        for item in captured_tables
        if len(item["array"]) > max_index
        and any(
            isinstance(item["array"][index], str)
            for index in indexes.values()
            if index < len(item["array"])
        )
    ]
    if len(default_matches) != 1:
        raise FanxiuRuntimeMemoryError("Runtime packed 配置 null-default 表无法唯一验证")
    defaults = default_matches[0]["array"]
    return {field: defaults[index] for field, index in indexes.items()}


def _runtime_config_table(
    reader: LuaJitReader, db_root: int, table_name: str
) -> dict[Any, Any]:
    manager = manager_index_fields(reader, db_root, _DB_METHODS)
    instance = _fields(reader, manager.get("inst"))
    configs = reader.dictionary_fields(instance.get("ConfigDic"))
    table = _fields(reader, configs.get(table_name))
    if not table:
        raise _not_loaded(f"DBMgr.ConfigDic[{table_name!r}] 尚未自然加载")
    return table


def _environment_config_indexes(
    reader: LuaJitReader,
    environment_address: int,
    *,
    group_name: str,
    table_name: str,
) -> dict[str, int]:
    environment = reader.string_fields(
        environment_address, frozenset({"s_globalCfgIdx"})
    )
    root = environment.get("s_globalCfgIdx")
    group = _fields(reader, root).get(group_name)
    indexes = _fields(reader, _fields(reader, group).get(table_name))
    result = {
        str(key): int(index)
        for key, index in indexes.items()
        if isinstance(key, str) and as_int(index) is not None
    }
    if not result:
        raise _not_loaded(
            f"s_globalCfgIdx[{group_name!r}][{table_name!r}] 尚未加载"
        )
    return result


def _packed_config_value(
    reader: LuaJitReader, raw: Any, indexes: Mapping[str, int], field: str
) -> Any:
    direct = _fields(reader, raw)
    value = direct.get(field)
    if value is not None:
        return value
    if not isinstance(raw, LuaRef) or raw.kind != "table":
        return None
    index = indexes.get(field)
    array = list(reader.table(raw.address).get("array") or ())
    return array[index] if index is not None and 0 <= index < len(array) else None


def _decode_pet_types(
    reader: LuaJitReader,
    table: Mapping[Any, Any],
    indexes: Mapping[str, int],
    defaults: Mapping[str, Any],
    *,
    wanted_ids: set[int],
) -> dict[int, dict[str, int]]:
    result: dict[int, dict[str, int]] = {}
    for raw_key, raw in table.items():
        pet_id = _positive_int(_packed_config_value(reader, raw, indexes, "id"))
        pet_id = pet_id or _positive_int(raw_key)
        if pet_id not in wanted_ids:
            continue
        packed_therion_type = _packed_config_value(
            reader, raw, indexes, "therionType"
        )
        therion_type = _nonnegative_int(
            defaults.get("therionType")
            if packed_therion_type is None
            else packed_therion_type
        )
        if therion_type is None:
            raise FanxiuRuntimeMemoryError(
                f"Pet.Pet[{pet_id}] 缺少明确 therionType，禁止默认普通灵兽"
            )
        result[pet_id] = {"therion_type": therion_type}
    missing = sorted(wanted_ids - set(result))
    if missing:
        raise _not_loaded(f"Pet.Pet 配置未覆盖已拥有灵兽：missing={missing}")
    return result


def _inventory_counts_from_root(
    reader: LuaJitReader,
    root_address: int,
    item_ids: Iterable[int],
) -> dict[int, int]:
    requested = {int(item_id) for item_id in item_ids}
    counts = {item_id: 0 for item_id in requested}
    data = _backpack_data_fields(reader, root_address)
    item_index = _fields(reader, data.get("ItemVoDic"))
    for raw_base_id, raw_dictionary in item_index.items():
        base_id = as_int(raw_base_id)
        if base_id not in counts:
            continue
        values = _fields(reader, _fields(reader, raw_dictionary).get("_valueTable_"))
        for raw_item in values.values():
            item = _fields(reader, raw_item)
            if as_int(item.get("baseId")) == base_id:
                counts[base_id] += max(0, as_int(item.get("num")) or 0)
    return counts


def _catalog_subset(item_ids: Iterable[int]) -> dict[str, Mapping[str, Any]]:
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"]
    result: dict[str, Mapping[str, Any]] = {}
    for item_id in sorted(set(item_ids)):
        row = cards.get(str(item_id))
        if row is not None:
            result[str(item_id)] = row
    return result


@lru_cache(maxsize=4)
def _load_talisman_static_rows_cached(
    root_text: str,
    talisman_mtime: int,
    grade_mtime: int,
    pin_mtime: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    del talisman_mtime, grade_mtime, pin_mtime
    root = Path(root_text)

    def load(table_name: str) -> list[dict[str, Any]]:
        path = root / "parsed_configs" / table_name / "rows.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not all(
            isinstance(row, dict) for row in payload
        ):
            raise FanxiuRuntimeMemoryError(
                f"版本化配置 {table_name} 不是完整 rows 列表"
            )
        return payload

    return load("Talisman"), load("TalismanGrade"), load("TalismanPin")


def _load_talisman_static_rows() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, str]
]:
    root = resolve_fanxiu_export_root()
    paths = {
        name: root / "parsed_configs" / name / "rows.json"
        for name in ("Talisman", "TalismanGrade", "TalismanPin")
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"版本化法宝配置缺失：{missing}"
        )
    base, grades, pins = _load_talisman_static_rows_cached(
        str(root),
        paths["Talisman"].stat().st_mtime_ns,
        paths["TalismanGrade"].stat().st_mtime_ns,
        paths["TalismanPin"].stat().st_mtime_ns,
    )
    return base, grades, pins, {
        "talisman_config_path": str(paths["Talisman"]),
        "talisman_grade_config_path": str(paths["TalismanGrade"]),
        "talisman_pin_config_path": str(paths["TalismanPin"]),
        "static_default_evidence": (
            "Talisman.lua _key2null[talismanType]=0; "
            "TalismanPin.lua _key2null[level]=0,pin=0"
        ),
    }


def _resolve_loaded_global(
    memory: MumuProcessMemory,
    *,
    state_address: int,
    manager_key: str,
    global_name: str,
    required_methods: frozenset[str],
    validate: Any,
) -> tuple[int, bool, int]:
    try:
        return resolve_lua_global_manager_root(
            memory,
            manager_key=manager_key,
            state_address=state_address,
            global_name=global_name,
            required_methods=required_methods,
            validate=validate,
        )
    except FanxiuRuntimeMemoryError as exc:
        # This reader has no marker/heap-scan fallback: a missing loaded global
        # or naturally absent data is a loading state, not a discovery request.
        if exc.code == "data_not_loaded":
            raise
        raise _not_loaded(f"已加载全局 {global_name} 不可用：{exc}") from exc


def _collect_pet_runtime_inputs(
    memory: MumuProcessMemory, stage_timings: dict[str, float]
) -> tuple[dict[str, Any], dict[str, Any]]:
    state_started = time.perf_counter()
    state_address = int(_lua_addresses(memory)["state"], 16)
    stage_timings["lua_state_seconds"] = time.perf_counter() - state_started

    started = time.perf_counter()
    pet_root, pet_cache_hit, environment = _resolve_loaded_global(
        memory,
        state_address=state_address,
        manager_key="resource-auto-use-pet",
        global_name="PetMgr",
        required_methods=_PET_METHODS,
        validate=_pet_data_fields,
    )
    stage_timings["pet_manager_resolution_seconds"] = time.perf_counter() - started
    reader = LuaJitReader(memory)
    started = time.perf_counter()
    try:
        data = _pet_data_fields(reader, pet_root)
        pets = _decode_owned_pets(reader, data)
    finally:
        stage_timings["pet_model_decode_seconds"] = time.perf_counter() - started
    wanted_ids = {row["pet_id"] for row in pets}

    started = time.perf_counter()
    db_root, db_cache_hit, _db_environment = _resolve_loaded_global(
        memory,
        state_address=state_address,
        manager_key="resource-auto-use-pet-db",
        global_name="DBMgr",
        required_methods=_DB_METHODS,
        validate=lambda current, root: _runtime_config_table(
            current, root, _PET_CONFIG_TABLE
        ),
    )
    indexes = _environment_config_indexes(
        reader, environment, group_name="Pet", table_name="Pet"
    )
    pet_table = _runtime_config_table(reader, db_root, _PET_CONFIG_TABLE)
    defaults = _runtime_config_null_defaults(reader, pet_table, indexes)
    pet_configs = _decode_pet_types(
        reader, pet_table, indexes, defaults, wanted_ids=wanted_ids
    )
    stage_timings["pet_config_resolution_seconds"] = time.perf_counter() - started

    started = time.perf_counter()
    ordinary_ids = {
        pet_id
        for pet_id, config in pet_configs.items()
        if config["therion_type"] == 0
    }
    level_cursors = _pet_level_cursors(
        reader, data, pets=pets, wanted_ids=ordinary_ids
    )
    stage_timings["pet_level_cache_seconds"] = time.perf_counter() - started

    item_ids = {
        int(cursor["first_item_id"])
        for cursor in level_cursors.values()
        if cursor.get("first_item_id") is not None
    }
    started = time.perf_counter()
    backpack_root, backpack_cache_hit, _backpack_environment = _resolve_loaded_global(
        memory,
        state_address=state_address,
        manager_key="resource-auto-use-pet-backpack",
        global_name="BackpackMgr",
        required_methods=_BACKPACK_METHODS,
        validate=_backpack_data_fields,
    )
    inventory = _inventory_counts_from_root(reader, backpack_root, item_ids)
    stage_timings["backpack_resolution_seconds"] = time.perf_counter() - started
    started = time.perf_counter()
    level_rows = _pet_level_rows_for_inventory(
        reader, level_cursors, inventory
    )
    stage_timings["pet_candidate_prefix_seconds"] = time.perf_counter() - started
    started = time.perf_counter()
    item_catalog = _catalog_subset(item_ids)
    stage_timings["item_catalog_seconds"] = time.perf_counter() - started
    return {
        "pets": pets,
        "pet_configs": pet_configs,
        "level_rows": level_rows,
        "inventory": inventory,
        "item_catalog": item_catalog,
    }, {
        "pet_root": f"0x{pet_root:x}",
        "pet_root_cache_hit": pet_cache_hit,
        "db_root": f"0x{db_root:x}",
        "db_root_cache_hit": db_cache_hit,
        "backpack_root": f"0x{backpack_root:x}",
        "backpack_root_cache_hit": backpack_cache_hit,
        "pet_config_table": _PET_CONFIG_TABLE,
        "pet_level_config_table": _PET_LEVEL_CONFIG_TABLE,
        "therion_type_default": defaults.get("therionType"),
        "therion_type_default_source": (
            "Pet.Pet packed-row __index closure _key2null runtime capture"
        ),
    }


def _collect_talisman_runtime_inputs(
    memory: MumuProcessMemory, stage_timings: dict[str, float]
) -> tuple[dict[str, Any], dict[str, Any]]:
    state_started = time.perf_counter()
    state_address = int(_lua_addresses(memory)["state"], 16)
    stage_timings["lua_state_seconds"] = time.perf_counter() - state_started
    started = time.perf_counter()
    root, cache_hit, _environment = _resolve_loaded_global(
        memory,
        state_address=state_address,
        manager_key="resource-auto-use-talisman",
        global_name="TalismanMgr",
        required_methods=frozenset({"LuaTalismanMgr", "Inst_get", "OpenUpgradeView"}),
        validate=_talisman_data_fields,
    )
    stage_timings["talisman_manager_resolution_seconds"] = time.perf_counter() - started
    reader = LuaJitReader(memory)
    started = time.perf_counter()
    try:
        data = _talisman_data_fields(reader, root)
        owned = _owned_talisman_rows(reader, data)
    finally:
        stage_timings["talisman_model_decode_seconds"] = time.perf_counter() - started
    owned_ids = {int(row["talisman_id"]) for row in owned}

    started = time.perf_counter()
    base_rows, static_grade_rows, static_pin_rows, static_evidence = (
        _load_talisman_static_rows()
    )
    configs = {
        int(row["id"]): {**_TALISMAN_STATIC_DEFAULTS, **row}
        for row in base_rows
        if _positive_int(row.get("id")) in owned_ids
    }
    if set(configs) != owned_ids:
        raise FanxiuRuntimeMemoryError(
            f"版本化法宝基础配置未覆盖 Runtime 已拥有对象："
            f"missing={sorted(owned_ids - set(configs))}"
        )
    grade_rows = [
        {
            "talisman_id": int(row["Talismanid"]),
            "stage": int(row["stage"]),
            "consume": row.get("consume"),
        }
        for row in static_grade_rows
        if _positive_int(row.get("Talismanid")) in owned_ids
        and _positive_int(row.get("stage")) is not None
    ]
    grades_by_id = defaultdict(list)
    for row in grade_rows:
        grades_by_id[row["talisman_id"]].append(row)
    if set(grades_by_id) != owned_ids:
        raise FanxiuRuntimeMemoryError(
            f"版本化法宝升阶配置未覆盖 Runtime 已拥有对象："
            f"missing={sorted(owned_ids - set(grades_by_id))}"
        )
    pins_by_identity = {
        (int(row["talismanId"]), int(row.get("level") or 0)): {
            **_TALISMAN_PIN_STATIC_DEFAULTS,
            **row,
        }
        for row in static_pin_rows
        if _positive_int(row.get("talismanId")) in owned_ids
    }
    stage_timings["versioned_config_seconds"] = time.perf_counter() - started

    talismans: list[dict[str, Any]] = []
    for row in owned:
        talisman_id = int(row["talisman_id"])
        stage = int(row.get("stage") or 0)
        wujing_level = int(row.get("wujing_level") or 0)
        talisman_type = _nonnegative_int(configs[talisman_id].get("talismanType"))
        if talisman_type is None:
            raise FanxiuRuntimeMemoryError(
                f"法宝 {talisman_id} 缺少明确 talismanType"
            )
        if talisman_type == 1:
            category = "先天古宝"
        elif talisman_type != 0:
            raise FanxiuRuntimeMemoryError(
                f"法宝 {talisman_id} 出现未知 talismanType={talisman_type}"
            )
        elif wujing_level <= 0:
            category = "法宝"
        else:
            current_wujing = pins_by_identity.get((talisman_id, wujing_level))
            pin = _nonnegative_int((current_wujing or {}).get("pin"))
            if pin is None:
                raise FanxiuRuntimeMemoryError(
                    f"版本化法宝神炼配置未覆盖 Runtime 当前等级："
                    f"talisman_id={talisman_id}, level={wujing_level}"
                )
            category = "后天古宝" if pin > 0 else "法宝"
        talismans.append(
            {
                "talisman_id": talisman_id,
                "stage": stage,
                "category": category,
                "active": stage > 0,
            }
        )

    item_ids = {
        parsed[0]
        for row in grade_rows
        if (parsed := parse_direct_item_consume(row.get("consume"))) is not None
    }
    started = time.perf_counter()
    backpack_root, backpack_cache_hit, _backpack_environment = _resolve_loaded_global(
        memory,
        state_address=state_address,
        manager_key="resource-auto-use-talisman-backpack",
        global_name="BackpackMgr",
        required_methods=_BACKPACK_METHODS,
        validate=_backpack_data_fields,
    )
    inventory = _inventory_counts_from_root(reader, backpack_root, item_ids)
    stage_timings["backpack_resolution_seconds"] = time.perf_counter() - started
    started = time.perf_counter()
    item_catalog = _catalog_subset(item_ids)
    stage_timings["item_catalog_seconds"] = time.perf_counter() - started
    return {
        "talismans": talismans,
        "grade_rows": grade_rows,
        "inventory": inventory,
        "item_catalog": item_catalog,
    }, {
        "talisman_root": f"0x{root:x}",
        "talisman_root_cache_hit": cache_hit,
        "backpack_root": f"0x{backpack_root:x}",
        "backpack_root_cache_hit": backpack_cache_hit,
        **static_evidence,
    }


def _read_live_snapshot(kind: str) -> dict[str, Any]:
    started = time.perf_counter()
    timings: dict[str, float] = {}
    memory: MumuProcessMemory | None = None
    try:
        stage_started = time.perf_counter()
        memory = MumuProcessMemory.discover_cached()
        timings["process_discovery_seconds"] = time.perf_counter() - stage_started
        if kind == "pet":
            inputs, evidence = _collect_pet_runtime_inputs(memory, timings)
            project_started = time.perf_counter()
            snapshot = project_pet_quick_swallow_candidates(**inputs)
        elif kind == "talisman":
            inputs, evidence = _collect_talisman_runtime_inputs(memory, timings)
            project_started = time.perf_counter()
            snapshot = project_talisman_quick_upgrade_candidates(**inputs)
        else:  # pragma: no cover - internal programming error
            raise ValueError(f"unsupported resource reader kind: {kind}")
        timings["projection_seconds"] = time.perf_counter() - project_started
        complete = bool(snapshot.get("complete"))
        return {
            "ok": complete,
            "available": True,
            "state": "Loaded" if complete else "Incomplete",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **snapshot,
            "elapsed_seconds": time.perf_counter() - started,
            "stage_timings": timings,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                **evidence,
            },
        }
    except Exception as exc:
        is_runtime = isinstance(exc, FanxiuRuntimeMemoryError)
        not_loaded = is_runtime and exc.code == "data_not_loaded"
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "read_only": True,
            "source": (
                PET_NATIVE_SOURCE if kind == "pet" else TALISMAN_NATIVE_SOURCE
            ),
            "state": "NotLoaded" if not_loaded else "Unavailable",
            "reason": str(exc) if is_runtime else f"{type(exc).__name__}: {exc}",
            "candidates": [],
            "candidate_count": 0,
            "elapsed_seconds": time.perf_counter() - started,
            "stage_timings": timings,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
            },
        }


def read_pet_quick_swallow_runtime() -> dict[str, Any]:
    """Read the naturally loaded native ordinary-pet batch without Lua calls."""

    return _read_live_snapshot("pet")


def read_talisman_quick_upgrade_runtime() -> dict[str, Any]:
    """Read the naturally loaded native talisman batch without Lua calls."""

    return _read_live_snapshot("talisman")


__all__ = [
    "PET_NATIVE_SOURCE",
    "TALISMAN_NATIVE_SOURCE",
    "classify_direct_material",
    "parse_direct_item_consume",
    "project_pet_quick_swallow_candidates",
    "project_talisman_quick_upgrade_candidates",
    "read_pet_quick_swallow_runtime",
    "read_talisman_quick_upgrade_runtime",
]
