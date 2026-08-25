from __future__ import annotations

"""Read the equipped GongFa source-book plan from the live game process.

The module is deliberately read-only.  It describes the books behind the
currently equipped ShenTong and XinFa, then produces a stable, de-duplicated
candidate list.  Navigation and book replacement belong to the behavior tree
and are intentionally outside this module.
"""

import json
import random
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from backend.core.fanxiu.catalog.gongfa import (
    GONGFA_QUALITY_GRADE_NAMES,
    _first_rich_color,
    _gongfa_quality_family_name,
    _gongfa_quality_grade_name,
    load_fanxiu_gongfa_runtime_index,
)
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_manager_root,
    table_ref,
)


_SKILL_MARKER = b"LuaSkillMgr"
_SKILL_METHODS = frozenset({"LuaSkillMgr", "Inst_get"})
_GONGFA_MARKER = b"LuaGongFaNewMgr"
_GONGFA_METHODS = frozenset({"LuaGongFaNewMgr", "Inst_get"})
_HOMEMAKE_MARKER = b"LuaGongfahomemakeMgr"
_HOMEMAKE_METHODS = frozenset(
    {"LuaGongfahomemakeMgr", "Inst_get", "GetGongFaName"}
)
_LINGJIE_XINFA_MARKER = b"LuaLingjiexinfaMgr"
_LINGJIE_XINFA_METHODS = frozenset({"LuaLingjiexinfaMgr", "Inst_get"})
_EQUIPPED_SHENTONG_COUNT = 6
_EQUIPPED_XINFA_COUNT = 6
_NORMAL_SKILL_TYPE = 0
_HOMEMAKE_SKILL_TYPE = 1


def _dictionary_items(reader: LuaJitReader, value: Any) -> list[tuple[Any, Any]]:
    """Return entries from the project's Lua Dictionary wrapper."""

    wrapper = reader.fields(value)
    data_ref = table_ref(wrapper.get("_dt_"))
    if data_ref is None:
        return []
    table = reader.table(data_ref.address)
    items = [
        (index, item)
        for index, item in enumerate(table["array"])
        if item is not None
    ]
    items.extend(table["fields"].items())
    return items


def _dictionary_int_map(reader: LuaJitReader, value: Any) -> dict[int, int]:
    result: dict[int, int] = {}
    for raw_key, raw_value in _dictionary_items(reader, value):
        key = as_int(raw_key)
        parsed = as_int(raw_value)
        if key is not None and parsed is not None:
            result[key] = parsed
    return result


def _long_id(reader: LuaJitReader, value: Any) -> int:
    parsed = reader.long(value)
    return parsed if parsed is not None and parsed > 0 else 0


def _required_fields(
    reader: LuaJitReader,
    value: Any,
    description: str,
) -> dict[Any, Any]:
    fields = reader.fields(value)
    if not fields:
        raise FanxiuRuntimeMemoryError(f"{description}尚未加载")
    return fields


def _skill_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _SKILL_METHODS)
    instance = _required_fields(reader, manager.get("inst"), "SkillMgr 实例")
    model = _required_fields(reader, instance.get("Model"), "SkillMgr.Model")
    data = _required_fields(reader, model.get("SkillData"), "SkillMgr.SkillData")
    groups = _dictionary_items(reader, data.get("groups"))
    if not groups:
        raise FanxiuRuntimeMemoryError("SkillMgr 默认技能组尚未加载")
    return data


def _gongfa_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _GONGFA_METHODS)
    instance = _required_fields(reader, manager.get("inst"), "GongFaNewMgr 实例")
    model = _required_fields(reader, instance.get("Model"), "GongFaNewMgr.Model")
    return _required_fields(
        reader,
        model.get("GongFaNewData"),
        "GongFaNewData",
    )


def _homemake_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _HOMEMAKE_METHODS)
    instance = _required_fields(
        reader,
        manager.get("inst"),
        "GongfahomemakeMgr 实例",
    )
    model = _required_fields(
        reader,
        instance.get("Model"),
        "GongfahomemakeMgr.Model",
    )
    data = _required_fields(
        reader,
        model.get("GongfahomemakeData"),
        "GongfahomemakeData",
    )
    if not _dictionary_items(reader, data.get("homeMakeDic")):
        raise FanxiuRuntimeMemoryError("自创功法列表尚未加载")
    return data


def _lingjie_xinfa_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _LINGJIE_XINFA_METHODS)
    instance = _required_fields(
        reader,
        manager.get("inst"),
        "LingjiexinfaMgr 实例",
    )
    model = _required_fields(
        reader,
        instance.get("Model"),
        "LingjiexinfaMgr.Model",
    )
    return _required_fields(
        reader,
        model.get("LingjiexinfaData"),
        "LingjiexinfaData",
    )


def _default_skill_group(
    reader: LuaJitReader,
    skill_data: dict[Any, Any],
) -> dict[Any, Any]:
    groups_wrapper = reader.fields(skill_data.get("groups"))
    data_ref = table_ref(groups_wrapper.get("_dt_"))
    if data_ref is None:
        raise FanxiuRuntimeMemoryError("SkillMgr 默认技能组字典无数据")
    table = reader.table(data_ref.address)
    group = table["array"][1] if len(table["array"]) > 1 else None
    fields = reader.fields(group)
    if not fields:
        raise FanxiuRuntimeMemoryError("SkillMgr 默认技能组 1 尚未加载")
    return fields


def _equipped_shentong_refs(
    reader: LuaJitReader,
    skill_data: dict[Any, Any],
) -> list[dict[str, int]]:
    group = _default_skill_group(reader, skill_data)
    skills, count = reader.list_items(group.get("skills"))
    # CList index 0 is the basic attack.  The six GongFa slots follow it.
    equipped = skills[1 : 1 + _EQUIPPED_SHENTONG_COUNT]
    if len(equipped) != _EQUIPPED_SHENTONG_COUNT:
        raise FanxiuRuntimeMemoryError(
            f"默认技能组槽位不完整：count={count}, equipped={len(equipped)}"
        )
    result: list[dict[str, int]] = []
    for slot, value in enumerate(equipped, 1):
        fields = reader.fields(value)
        skill_id = as_int(fields.get("skillId")) or 0
        skill_type = as_int(fields.get("type"))
        if skill_id <= 0 or skill_type is None:
            raise FanxiuRuntimeMemoryError(f"神通装配槽 {slot} 数据不完整")
        result.append(
            {
                "slot": slot,
                "skill_id": skill_id,
                "skill_type": skill_type,
                "make_id": _long_id(reader, fields.get("makeId")),
            }
        )
    return result


def _equipped_xinfa_refs(
    reader: LuaJitReader,
    gongfa_data: dict[Any, Any],
) -> list[dict[str, int]]:
    items, count = reader.list_items(gongfa_data.get("xinFaPutUpList"))
    result: list[dict[str, int]] = []
    for position, value in enumerate(items, 1):
        fields = reader.fields(value)
        skill = reader.fields(fields.get("xinFaId"))
        skill_id = as_int(skill.get("skillId")) or 0
        skill_type = as_int(skill.get("type"))
        slot = as_int(fields.get("idx"))
        if skill_id <= 0 or skill_type is None or slot is None:
            raise FanxiuRuntimeMemoryError(f"心法装配项 {position} 数据不完整")
        result.append(
            {
                "slot": slot,
                "skill_id": skill_id,
                "skill_type": skill_type,
                "make_id": _long_id(reader, skill.get("makeId")),
            }
        )
    result.sort(key=lambda item: item["slot"])
    if count is not None and len(result) != count:
        raise FanxiuRuntimeMemoryError("心法装配列表读取不完整")
    if len(result) != _EQUIPPED_XINFA_COUNT:
        raise FanxiuRuntimeMemoryError(
            f"心法装配槽位不完整：expected={_EQUIPPED_XINFA_COUNT}, "
            f"actual={len(result)}"
        )
    return result


def _homemake_index(
    reader: LuaJitReader,
    homemake_data: dict[Any, Any],
) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for _scope_key, scope_value in _dictionary_items(
        reader,
        homemake_data.get("homeMakeDic"),
    ):
        scope = reader.fields(scope_value)
        for _type_key, type_value in _dictionary_items(
            reader,
            scope.get("skillTypeMap"),
        ):
            type_fields = reader.fields(type_value)
            items, _count = reader.list_items(type_fields.get("homeMakeVOList"))
            for value in items:
                fields = reader.fields(value)
                common = reader.fields(fields.get("skillCommonVO"))
                make_id = _long_id(reader, common.get("id"))
                main_id = as_int(common.get("mainId")) or 0
                if make_id <= 0 or main_id <= 0:
                    continue
                result[make_id] = {
                    "make_id": make_id,
                    "name": str(common.get("skillName") or ""),
                    "main_skill_id": main_id,
                    "effect_map": _dictionary_int_map(
                        reader,
                        common.get("effectMap"),
                    ),
                    "xian_effect_map": _dictionary_int_map(
                        reader,
                        common.get("xianEffectMap"),
                    ),
                }
    return result


def _lingjie_xinfa_grid_index(
    reader: LuaJitReader,
    lingjie_data: dict[Any, Any],
) -> dict[int, list[tuple[int, int]]]:
    result: dict[int, list[tuple[int, int]]] = {}
    for raw_skill_id, value in _dictionary_items(
        reader,
        lingjie_data.get("allXinFaDic"),
    ):
        skill_id = as_int(raw_skill_id)
        if skill_id is None:
            continue
        fields = reader.fields(value)
        grids = [
            (grid, book_id)
            for raw_grid, raw_book_id in _dictionary_items(
                reader,
                fields.get("gridMap"),
            )
            if (grid := as_int(raw_grid)) is not None
            and (book_id := as_int(raw_book_id)) is not None
            and book_id > 0
        ]
        if grids:
            result[skill_id] = sorted(grids)
    return result


def _catalog_filter_category(card: dict[str, Any]) -> tuple[str, list[str]]:
    quality_type_name = str(card.get("quality_type_name") or "")
    sub_type_names = sorted(
        {
            str(skill.get("sub_type_name") or "")
            for skill in card.get("skills") or []
            if skill.get("sub_type_name")
        }
    )
    if quality_type_name == "仙术" or {
        "仙书",
        "仙界书",
    }.intersection(sub_type_names):
        return "仙术", sub_type_names
    if quality_type_name in {"剑修", "法修", "魔修", "体修"}:
        return quality_type_name, sub_type_names
    return "", sub_type_names


@lru_cache(maxsize=1)
def _book_catalog_index() -> dict[int, dict[str, Any]]:
    """Load optional book names and types; live IDs remain authoritative."""

    try:
        runtime = load_fanxiu_gongfa_runtime_index(rebuild_missing=False)
    except Exception:
        return {}
    tongxuan_max = _max_tongxuan_index()
    result: dict[int, dict[str, Any]] = {}
    for raw_id, card in runtime.get("cards_by_id", {}).items():
        try:
            book_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        name = str(card.get("name") or "").removeprefix("心法·")
        skill_type = as_int(card.get("skill_type"))
        quality_type_name = str(card.get("quality_type_name") or "")
        filter_category, sub_type_names = _catalog_filter_category(card)
        progression = card.get("progression") if isinstance(card.get("progression"), dict) else {}
        jie_rows = [
            row
            for kind, rows in progression.items()
            if str(kind).endswith("_jie")
            for row in (rows or [])
            if isinstance(row, dict)
        ]
        upgrade_rows = [
            row
            for row in (progression.get("upgrade") or [])
            if isinstance(row, dict)
        ]
        result[book_id] = {
            "name": name,
            "skill_type": skill_type,
            "skill_type_name": str(card.get("skill_type_name") or ""),
            "filter_category": filter_category,
            "quality_type_name": quality_type_name,
            "quality_grade_name": _gongfa_quality_grade_name(card),
            "quality_grade_order": next(
                (
                    index
                    for index, grade_name in enumerate(GONGFA_QUALITY_GRADE_NAMES)
                    if grade_name == _gongfa_quality_grade_name(card)
                ),
                -1,
            ),
            "quality_grade_color": _first_rich_color(card.get("quality_rich_name")),
            "quality_family_name": _gongfa_quality_family_name(card),
            "sub_type_names": sub_type_names,
            "max_jie": max((as_int(row.get("jie")) or 0 for row in jie_rows), default=0),
            "max_wujing": max((max(0, (as_int(row.get("pin")) or 1) - 1) for row in upgrade_rows), default=0),
            "max_tongxuan": tongxuan_max.get(book_id, 0),
        }
    return result


def _book_name_index() -> dict[int, str]:
    return {
        book_id: str(item.get("name") or "")
        for book_id, item in _book_catalog_index().items()
        if item.get("name")
    }


@lru_cache(maxsize=1)
def _max_star_index() -> dict[tuple[int, int], int]:
    """Load the current game-config maximum star for each book and pin."""

    path = (
        Path(resolve_fanxiu_export_root())
        / "parsed_configs"
        / "GongfaUpgrade"
        / "rows.json"
    )
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    result: dict[tuple[int, int], int] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        book_id = as_int(row.get("gid"))
        pin = as_int(row.get("pin"))
        max_star = as_int(row.get("maxStar"))
        if book_id and pin and max_star:
            result[(book_id, pin)] = max_star
    return result


@lru_cache(maxsize=1)
def _max_tongxuan_index() -> dict[int, int]:
    path = (
        Path(resolve_fanxiu_export_root())
        / "parsed_configs"
        / "GongfaTongxuanUpgrade"
        / "rows.json"
    )
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    result: dict[int, int] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        book_id = as_int(row.get("gid"))
        tongxuan = as_int(row.get("pin"))
        if book_id and tongxuan is not None:
            result[book_id] = max(result.get(book_id, 0), tongxuan)
    return result


def _gongfa_progression_index(
    reader: LuaJitReader,
    gongfa_data: dict[Any, Any],
) -> dict[int, dict[str, int]]:
    """Read every learned book's live progression overlay."""

    max_stars = _max_star_index()
    result: dict[int, dict[str, int]] = {}
    for raw_book_id, value in _dictionary_items(
        reader,
        gongfa_data.get("gongFaDic"),
    ):
        book_id = as_int(raw_book_id)
        item = reader.fields(value)
        vo = reader.fields(item.get("vo"))
        grade = as_int(vo.get("grade"))
        jie = as_int(vo.get("jie"))
        star = as_int(vo.get("star"))
        pin = as_int(vo.get("pin"))
        tongxuan = as_int(vo.get("tongxuan"))
        quality = as_int(vo.get("quality"))
        total_exp = reader.long(vo.get("totalExp"))
        if not book_id or grade is None or star is None or pin is None:
            continue
        result[book_id] = {
            "grade": grade,
            "jie": jie or 0,
            "star": star,
            "pin": pin,
            "tongxuan": tongxuan or 0,
            "quality": quality or 0,
            "total_exp": total_exp or 0,
            "max_star": max_stars.get((book_id, pin), 0),
        }
    return result


def _training_state_values(
    reader: LuaJitReader,
    gongfa_data: dict[Any, Any],
) -> dict[str, Any]:
    """Read the small live state used while consuming GongFa experience."""

    exp_pool = reader.long(gongfa_data.get("_CurGongFaExpPoolValue"))
    if exp_pool is None or exp_pool < 0:
        raise FanxiuRuntimeMemoryError("GongFaNewData 当前功法经验池尚未加载")
    # The game does not initialize isFullTip.  It is an event latch set to
    # true only when consuming experience reports GongFaExpFull, then reset
    # to false after showing the tip.  Lua therefore treats both nil and
    # false as the normal "not full" state.
    full_tip = gongfa_data.get("isFullTip") is True
    return {
        "current_book_full": full_tip,
        "experience_pool": exp_pool,
        "is_long_press": bool(gongfa_data.get("isLongPress")),
        "is_bottle_long_press": bool(gongfa_data.get("isBottleLongPress")),
    }


def read_gongfa_training_snapshot() -> dict[str, Any]:
    """Read the current training/full flag without rebuilding equipment."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        reader = LuaJitReader(memory)
        root, root_cache_hit = resolve_manager_root(
            memory,
            manager_key="gongfa-equipment-state",
            marker=_GONGFA_MARKER,
            required_methods=_GONGFA_METHODS,
            validate=_gongfa_data_fields,
        )
        values = _training_state_values(
            reader,
            _gongfa_data_fields(reader, root),
        )
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory",
            "protocol": (
                "LuaGongFaNewMgr.Model.GongFaNewData."
                "[isFullTip,_CurGongFaExpPoolValue]"
            ),
            **values,
            "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "root_address": f"0x{root:x}",
                "root_cache_hit": root_cache_hit,
            },
        }
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, FanxiuRuntimeMemoryError)
            else f"{type(exc).__name__}: {exc}"
        )
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "current_book_full": None,
            "experience_pool": None,
            "reason": reason,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
            },
        }


def _progression_view(state: dict[str, int]) -> dict[str, Any]:
    grade = state.get("grade")
    star = state.get("star")
    max_star = state.get("max_star")
    level_cap = None
    if (
        grade is not None
        and star is not None
        and max_star is not None
        and star > 0
        and max_star > 0
    ):
        level_cap = (star - 1) * 50 if star >= max_star else star * 50
    return {
        "known": level_cap is not None,
        "grade": grade,
        "star": star,
        "pin": state.get("pin"),
        "max_star": max_star,
        "level_cap": level_cap,
        "upgradeable": (
            grade < level_cap
            if grade is not None and level_cap is not None
            else None
        ),
    }


def select_first_upgradable_book(
    books: Iterable[dict[str, Any]],
    progression_by_book: dict[int, dict[str, int]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, int | None]:
    """Attach live levels and select the first currently upgradable book.

    Selection always starts at the beginning of the newly computed list.  It
    deliberately accepts no current-book id or previous cursor.  If an early
    book has unknown progression, later books cannot be selected safely.

    :return: ``(enriched_books, selected, blocked_priority)``.
    """

    enriched: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    blocked_priority: int | None = None
    for source in books:
        book = dict(source)
        state = progression_by_book.get(int(book.get("book_id") or 0), {})
        progression = _progression_view(state)
        book["progression"] = progression
        enriched.append(book)
        if selected is not None or blocked_priority is not None:
            continue
        if not progression["known"]:
            blocked_priority = int(book.get("priority") or 0)
        elif progression["upgradeable"]:
            selected = book
    return enriched, selected, blocked_priority


def select_fallback_upgradable_book(
    primary_book_ids: Iterable[int],
    progression_by_book: dict[int, dict[str, int]],
    catalog_by_book: dict[int, dict[str, Any]],
    *,
    rng: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[int]]:
    """Select learned non-primary ShenTong/XinFa by descending quality grade.

    Only books outside the freshly built equipment dependency list are
    considered. Equal highest quality grades are selected randomly on every
    call; current cultivation level only provides a stable display order.
    Unknown learned ShenTong/XinFa states block fallback selection instead of
    being silently skipped.
    """

    excluded = {int(book_id) for book_id in primary_book_ids}
    candidates: list[dict[str, Any]] = []
    blocked_book_ids: list[int] = []
    for book_id, state in progression_by_book.items():
        if book_id in excluded:
            continue
        catalog = catalog_by_book.get(book_id, {})
        if not catalog:
            blocked_book_ids.append(book_id)
            continue
        if catalog.get("skill_type") not in {2, 5}:
            continue
        quality_grade_order_raw = catalog.get("quality_grade_order")
        quality_grade_order = (
            int(quality_grade_order_raw)
            if quality_grade_order_raw is not None
            else -1
        )
        if quality_grade_order < 0:
            blocked_book_ids.append(book_id)
            continue
        progression = _progression_view(state)
        if not progression["known"]:
            blocked_book_ids.append(book_id)
            continue
        if not progression["upgradeable"]:
            continue
        candidates.append(
            {
                "book_id": book_id,
                "name": str(catalog.get("name") or ""),
                "skill_type": catalog.get("skill_type"),
                "skill_type_name": str(catalog.get("skill_type_name") or ""),
                "filter_category": str(catalog.get("filter_category") or ""),
                "quality_grade_name": str(catalog.get("quality_grade_name") or ""),
                "quality_grade_order": quality_grade_order,
                "progression": progression,
                "selection_pool": "fallback_learned",
            }
        )
    candidates.sort(
        key=lambda item: (
            -int(item["quality_grade_order"]),
            -int(item["progression"]["grade"]),
            int(item["book_id"]),
        )
    )
    if blocked_book_ids or not candidates:
        return candidates, None, sorted(blocked_book_ids)
    highest_quality_grade = candidates[0]["quality_grade_order"]
    tied = [
        item
        for item in candidates
        if item["quality_grade_order"] == highest_quality_grade
    ]
    chooser = rng if rng is not None else random.SystemRandom()
    selected = dict(chooser.choice(tied))
    selected["highest_quality_grade_tie_count"] = len(tied)
    return candidates, selected, []


def _book_ref(
    *,
    skill_id: int,
    skill_to_book: dict[int, int],
    names: dict[int, str],
    effect_id: int | None = None,
) -> dict[str, Any]:
    book_id = skill_to_book.get(skill_id, skill_id)
    result = {
        "book_id": book_id,
        "name": names.get(book_id, ""),
        "source_skill_id": skill_id,
        "canonical": skill_id in skill_to_book,
    }
    if effect_id is not None and effect_id > 0:
        result["effect_id"] = effect_id
    return result


def _homemake_components(
    data: dict[str, Any],
    *,
    skill_to_book: dict[int, int],
    names: dict[int, str],
) -> dict[str, list[dict[str, Any]]]:
    main_skill_id = int(data["main_skill_id"])
    side_skill_ids = sorted(
        skill_id
        for skill_id in data.get("effect_map", {})
        if skill_id != main_skill_id
    )
    return {
        "main": [
            _book_ref(
                skill_id=main_skill_id,
                skill_to_book=skill_to_book,
                names=names,
                effect_id=as_int(data.get("effect_map", {}).get(main_skill_id)),
            )
        ],
        "xian": [
            _book_ref(
                skill_id=skill_id,
                skill_to_book=skill_to_book,
                names=names,
                effect_id=as_int(effect_id),
            )
            for effect_id, skill_id in sorted(data.get("xian_effect_map", {}).items())
        ],
        "side": [
            _book_ref(
                skill_id=skill_id,
                skill_to_book=skill_to_book,
                names=names,
                effect_id=as_int(data.get("effect_map", {}).get(skill_id)),
            )
            for skill_id in side_skill_ids
        ],
        "grid": [],
    }


def _equipped_records(
    shentong_refs: Iterable[dict[str, int]],
    xinfa_refs: Iterable[dict[str, int]],
    *,
    homemake: dict[int, dict[str, Any]],
    xinfa_grids: dict[int, list[tuple[int, int]]],
    skill_to_book: dict[int, int],
    names: dict[int, str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for category, refs in (("gongfa", shentong_refs), ("xinfa", xinfa_refs)):
        for ref in refs:
            make_id = int(ref.get("make_id") or 0)
            custom = homemake.get(make_id)
            if custom is not None:
                components = _homemake_components(
                    custom,
                    skill_to_book=skill_to_book,
                    names=names,
                )
                equipped_name = str(custom.get("name") or "")
                composition = "homemake"
            else:
                grids = xinfa_grids.get(int(ref["skill_id"]), [])
                normal_main = (
                    [
                        _book_ref(
                            skill_id=int(ref["skill_id"]),
                            skill_to_book=skill_to_book,
                            names=names,
                        )
                    ]
                    if not grids and int(ref["skill_id"]) in skill_to_book
                    else []
                )
                components = {
                    "main": normal_main,
                    "xian": [],
                    "side": [],
                    "grid": [
                        {
                            "book_id": book_id,
                            "name": names.get(book_id, ""),
                            "source_skill_id": None,
                            "canonical": True,
                            "grid": grid,
                        }
                        for grid, book_id in grids
                    ],
                }
                equipped_name = ""
                composition = "lingjie_xinfa_grid" if grids else "normal"
            records.append(
                {
                    "category": category,
                    "slot": int(ref["slot"]),
                    "skill_id": int(ref["skill_id"]),
                    "skill_type": int(ref["skill_type"]),
                    "make_id": make_id,
                    "name": equipped_name,
                    "composition": composition,
                    "components": components,
                }
            )
    return records


def build_ordered_book_plan(
    equipped: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Build the stable unique source-book order from normalized equipment.

    ShenTong follows its equipped slot order.  Within each slot, its main
    book, Xian book, then side books are emitted before moving to the next
    equipped ShenTong.  XinFa follows afterwards in slot order; a homemade
    XinFa uses main then side books, while a LingJie grid XinFa keeps its grid
    order.

    :return: ``(books, duplicate_count)``.  Duplicate books retain every usage
        in the first occurrence instead of producing another candidate.
    """

    rows = list(equipped)
    gongfa = sorted(
        (row for row in rows if row.get("category") == "gongfa"),
        key=lambda row: int(row.get("slot") or 0),
    )
    xinfa = sorted(
        (row for row in rows if row.get("category") == "xinfa"),
        key=lambda row: int(row.get("slot") or 0),
    )
    occurrences: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
    for row in gongfa:
        for role in ("main", "xian", "side"):
            for book in row.get("components", {}).get(role, []):
                occurrences.append((row, role, book))
    for row in xinfa:
        role_order = (
            ("grid",)
            if row.get("composition") == "lingjie_xinfa_grid"
            else ("main", "side")
        )
        for role in role_order:
            for book in row.get("components", {}).get(role, []):
                occurrences.append((row, role, book))

    unique: dict[int, dict[str, Any]] = {}
    duplicate_count = 0
    for row, role, book in occurrences:
        book_id = int(book.get("book_id") or 0)
        if book_id <= 0:
            continue
        usage = {
            "category": row.get("category"),
            "slot": int(row.get("slot") or 0),
            "equipped_name": str(row.get("name") or ""),
            "role": role,
        }
        if as_int(book.get("source_skill_id")):
            usage["source_skill_id"] = as_int(book.get("source_skill_id"))
        if as_int(book.get("effect_id")):
            usage["effect_id"] = as_int(book.get("effect_id"))
        if book.get("grid") is not None:
            usage["grid"] = int(book["grid"])
        existing = unique.get(book_id)
        if existing is not None:
            existing["usages"].append(usage)
            duplicate_count += 1
            continue
        entry = {
            "priority": len(unique) + 1,
            "book_id": book_id,
            "name": str(book.get("name") or ""),
            "source_skill_id": book.get("source_skill_id"),
            "canonical": bool(book.get("canonical", False)),
            "first_usage": usage,
            "usages": [usage],
        }
        unique[book_id] = entry
    return list(unique.values()), duplicate_count


def _read_snapshot_values(
    memory: MumuProcessMemory,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any] | None,
    list[dict[str, Any]],
    dict[str, Any],
]:
    reader = LuaJitReader(memory)
    skill_root, skill_cache_hit = resolve_manager_root(
        memory,
        manager_key="gongfa-equipment-skill",
        marker=_SKILL_MARKER,
        required_methods=_SKILL_METHODS,
        validate=_skill_data_fields,
    )
    gongfa_root, gongfa_cache_hit = resolve_manager_root(
        memory,
        manager_key="gongfa-equipment-state",
        marker=_GONGFA_MARKER,
        required_methods=_GONGFA_METHODS,
        validate=_gongfa_data_fields,
    )
    homemake_root, homemake_cache_hit = resolve_manager_root(
        memory,
        manager_key="gongfa-equipment-homemake",
        marker=_HOMEMAKE_MARKER,
        required_methods=_HOMEMAKE_METHODS,
        validate=_homemake_data_fields,
    )
    lingjie_root, lingjie_cache_hit = resolve_manager_root(
        memory,
        manager_key="gongfa-equipment-lingjie-xinfa",
        marker=_LINGJIE_XINFA_MARKER,
        required_methods=_LINGJIE_XINFA_METHODS,
        validate=_lingjie_xinfa_data_fields,
    )

    skill_data = _skill_data_fields(reader, skill_root)
    gongfa_data = _gongfa_data_fields(reader, gongfa_root)
    homemake_data = _homemake_data_fields(reader, homemake_root)
    lingjie_data = _lingjie_xinfa_data_fields(reader, lingjie_root)
    skill_to_book = _dictionary_int_map(
        reader,
        homemake_data.get("gongFaSkillDic"),
    )
    if not skill_to_book:
        raise FanxiuRuntimeMemoryError("功法技能到底书映射尚未加载")
    names = _book_name_index()
    catalog = _book_catalog_index()
    equipped = _equipped_records(
        _equipped_shentong_refs(reader, skill_data),
        _equipped_xinfa_refs(reader, gongfa_data),
        homemake=_homemake_index(reader, homemake_data),
        xinfa_grids=_lingjie_xinfa_grid_index(reader, lingjie_data),
        skill_to_book=skill_to_book,
        names=names,
    )
    books, duplicate_count = build_ordered_book_plan(equipped)
    progression = _gongfa_progression_index(reader, gongfa_data)
    books, primary_selected, blocked_priority = select_first_upgradable_book(
        books,
        progression,
    )
    fallback_candidates: list[dict[str, Any]] = []
    fallback_selected: dict[str, Any] | None = None
    fallback_blocked_book_ids: list[int] = []
    if primary_selected is None and blocked_priority is None:
        (
            fallback_candidates,
            fallback_selected,
            fallback_blocked_book_ids,
        ) = select_fallback_upgradable_book(
            (book["book_id"] for book in books),
            progression,
            catalog,
        )
    selected = primary_selected or fallback_selected
    if primary_selected is not None:
        selected_catalog = catalog.get(int(primary_selected["book_id"]), {})
        selected = {
            **primary_selected,
            "selection_pool": "equipped_dependency",
            "skill_type": selected_catalog.get("skill_type"),
            "skill_type_name": str(
                selected_catalog.get("skill_type_name") or ""
            ),
            "filter_category": str(
                selected_catalog.get("filter_category") or ""
            ),
        }
    evidence = {
        "roots": {
            "skill": f"0x{skill_root:x}",
            "gongfa": f"0x{gongfa_root:x}",
            "homemake": f"0x{homemake_root:x}",
            "lingjie_xinfa": f"0x{lingjie_root:x}",
        },
        "root_cache_hits": {
            "skill": skill_cache_hit,
            "gongfa": gongfa_cache_hit,
            "homemake": homemake_cache_hit,
            "lingjie_xinfa": lingjie_cache_hit,
        },
        "duplicate_count": duplicate_count,
        "name_catalog_available": bool(names),
        "selection_blocked_priority": blocked_priority,
        "fallback_blocked_book_ids": fallback_blocked_book_ids,
    }
    return equipped, books, selected, fallback_candidates, evidence


def read_gongfa_equipment_book_plan_snapshot() -> dict[str, Any]:
    """Read the current equipped source books and their unique priority plan.

    This is an event-triggered read intended for the moment when a full book
    must be replaced.  It does not poll equipment during ordinary experience
    consumption and does not perform any game input.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        (
            equipped,
            books,
            selected,
            fallback_candidates,
            evidence,
        ) = _read_snapshot_values(memory)
        category_counts = {
            category: sum(
                row["category"] == category for row in equipped
            )
            for category in ("gongfa", "xinfa")
        }
        complete = (
            bool(books)
            and category_counts["gongfa"] == _EQUIPPED_SHENTONG_COUNT
            and category_counts["xinfa"] == _EQUIPPED_XINFA_COUNT
            and all(book["canonical"] for book in books)
            and all(book["progression"]["known"] for book in books)
            and not evidence["fallback_blocked_book_ids"]
            and (selected is None or bool(selected.get("filter_category")))
            and all(
                any(row["components"].values()) for row in equipped
            )
        )
        return {
            "ok": True,
            "available": True,
            "complete": complete,
            "source": "runtime_memory",
            "state": "ready" if complete else "partial",
            "protocol": (
                "SkillMgr.defaultGroup+GongFaNewData.xinFaPutUpList+"
                "GongfahomemakeData.effectMap/xianEffectMap+"
                "LingjiexinfaData.gridMap"
            ),
            "trigger": "book_full_before_replacement",
            "equipped": equipped,
            "books": books,
            "book_count": len(books),
            "next_upgradable_book": selected,
            "fallback_candidates": fallback_candidates,
            "fallback_candidate_count": len(fallback_candidates),
            "all_books_full": (
                selected is None
                and complete
                and not evidence["fallback_blocked_book_ids"]
            ),
            "equipped_counts": category_counts,
            "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                **evidence,
            },
        }
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, FanxiuRuntimeMemoryError)
            else f"{type(exc).__name__}: {exc}"
        )
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "state": "unknown",
            "trigger": "book_full_before_replacement",
            "reason": reason,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
            },
        }
