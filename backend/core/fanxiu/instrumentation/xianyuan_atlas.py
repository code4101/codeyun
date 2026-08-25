from __future__ import annotations

"""Read and persist the current account's Xianyuan encyclopedia.

The reader stays outside the game process.  It only decodes already-loaded
LuaJIT tables and never invokes Lua, initializes a manager, or sends a packet.
"""

import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.catalog.inventory_snapshot_store import (
    load_inventory_hall_snapshot,
    upsert_inventory_hall_snapshot,
)
from backend.core.fanxiu.catalog.lua_config import (
    _find_default_lang_path,
    load_fanxiu_lang_map,
)
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.red_packet import (
    _NPC_MARKER,
    _NPC_METHODS,
    _npc_data_address,
    _npc_data_fields,
    _read_cached_data_address,
    _write_cached_data_address,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    resolve_manager_root,
)
from backend.db import engine


XIANYUAN_ATLAS_KEY = "xianyuan_atlas"


@lru_cache(maxsize=1)
def _item_index() -> dict[int, dict[str, Any]]:
    path = Path(resolve_fanxiu_export_root()) / "parsed_configs" / "Item" / "rows.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return {
        int(row["id"]): row
        for row in rows if isinstance(row, dict) and as_int(row.get("id"))
    }


def _parsed_config_index(table_name: str) -> dict[int, dict[str, Any]]:
    path = (
        Path(resolve_fanxiu_export_root())
        / "parsed_configs"
        / table_name
        / "rows.json"
    )
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return {
        int(row["id"]): row
        for row in rows
        if isinstance(row, dict) and as_int(row.get("id"))
    }


@lru_cache(maxsize=1)
def _optional_gift_groups() -> dict[str, list[int]]:
    path = (
        Path(resolve_fanxiu_export_root())
        / "parsed_configs"
        / "OptionalGift"
        / "rows.json"
    )
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    result: dict[str, list[int]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        group_id = str(row.get("groupID") or "")
        gift_id = as_int(row.get("giftID"))
        if group_id and gift_id:
            result.setdefault(group_id, []).append(gift_id)
    return result


def _optional_item_ids(item: dict[str, Any]) -> list[int]:
    effect_value = str(item.get("effectValue") or "")
    if not effect_value.startswith("1_"):
        return []
    return _optional_gift_groups().get(effect_value.split("_", 1)[1], [])


def _optional_leaf_item_ids(item_id: int, seen: set[int] | None = None) -> list[int]:
    seen = set(seen or ())
    if item_id in seen:
        return []
    seen.add(item_id)
    item = _item_index().get(item_id) or {}
    children = _optional_item_ids(item)
    if not children:
        return [item_id]
    leaves: list[int] = []
    for child_id in children:
        leaves.extend(_optional_leaf_item_ids(child_id, seen))
    return list(dict.fromkeys(leaves))


def _project_selectable_rewards(rewards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = _item_index()
    projected: list[dict[str, Any]] = []
    for source in rewards:
        reward = dict(source)
        item_id = as_int(reward.get("item_id")) or 0
        direct_children = _optional_item_ids(items.get(item_id) or {})
        if not direct_children:
            continue
        options: list[dict[str, Any]] = []
        for leaf_id in _optional_leaf_item_ids(item_id):
            item = items.get(leaf_id) or {}
            options.append({
                "item_id": leaf_id,
                "name": str(item.get("name_plain") or item.get("name") or leaf_id),
                "kind": _reward_kind(item),
            })
        reward["optional_items"] = options
        reward["optional_item_count"] = len(options)
        reward["contains_wujing"] = any(option["kind"] == "悟境" for option in options)
        projected.append(reward)
    return projected


@lru_cache(maxsize=1)
def _npc_favor_thresholds() -> dict[tuple[int, int], dict[str, int]]:
    path = (
        Path(resolve_fanxiu_export_root())
        / "parsed_configs"
        / "NpcFavor"
        / "rows.json"
    )
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    result: dict[tuple[int, int], dict[str, int]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        npc_id = as_int(row.get("npc")) or 0
        grade = as_int(row.get("grade")) or 0
        if npc_id <= 0 or grade <= 0:
            continue
        result[(npc_id, grade)] = {
            "favor": as_int(row.get("favor")) or 0,
            "reset_favor": as_int(row.get("resetFavor")) or 0,
            "step": as_int(row.get("step")) or 1,
        }
    return result


def _npc_reset_steps(npc_id: int) -> list[dict[str, int]]:
    thresholds = {
        grade: values
        for (threshold_npc_id, grade), values in _npc_favor_thresholds().items()
        if threshold_npc_id == npc_id
    }
    steps = sorted({int(values.get("step") or 1) for values in thresholds.values()})
    result: list[dict[str, int]] = []
    for step in steps:
        levels = sorted(
            grade
            for grade, values in thresholds.items()
            if int(values.get("step") or 1) == step
        )
        if not levels:
            continue
        start_level = levels[0] - 1
        end_level = levels[-1]
        start_favor = int((thresholds.get(start_level) or {}).get("reset_favor") or 0)
        end_favor = int((thresholds.get(end_level) or {}).get("reset_favor") or 0)
        result.append({
            "step": step,
            "start_level": start_level,
            "end_level": end_level,
            "favor_cost": max(0, end_favor - start_favor),
        })
    return result


def _target_support_by_item(target: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Map rewards that can advance one high-grade Gongfa book."""

    target_id = as_int(target.get("book_id")) or 0
    target_name = str(target.get("name") or "")
    target_quality = str(target.get("quality_grade_name") or "")
    if not target_id or target_quality not in {"仙品", "神品"}:
        return {}
    items = _item_index()
    support: dict[int, dict[str, Any]] = {}
    for item_id, item in items.items():
        item_type = as_int(item.get("type")) or 0
        subtype = as_int(item.get("subType")) or 0
        effect_value = str(item.get("effectValue") or "")
        name = str(item.get("name_plain") or item.get("name") or "")
        description = str(item.get("descript_plain") or "")
        kind = ""
        mode = "直接"
        if effect_value == str(target_id):
            if item_type == 3:
                kind = "融合"
            elif item_type == 999 and subtype == 33:
                kind = "悟境"
            elif item_type == 999 and subtype == 87:
                kind = "通玄"
        elif name == "悟境残页" or "真悟阁中兑换各功法" in description:
            kind, mode = "悟境", "兑换"
        elif target_quality == "仙品" and name == "功法残篇·仙品":
            kind, mode = "融合", "兑换"
        elif target_name and target_name in description and "通玄" in name:
            kind, mode = "通玄", "合成"
        if kind:
            support[item_id] = {"kind": kind, "mode": mode}

    # A reward box helps the target when one of its selectable children does.
    changed = True
    while changed:
        changed = False
        for item_id, item in items.items():
            if item_id in support:
                continue
            child_support = [
                support[child_id]
                for child_id in _optional_item_ids(item)
                if child_id in support
            ]
            if not child_support:
                continue
            kinds = sorted({entry["kind"] for entry in child_support})
            support[item_id] = {"kind": "、".join(kinds), "mode": "自选"}
            changed = True
    return support


def _project_target_recommendations(
    people: list[dict[str, Any]],
    target: dict[str, Any] | None,
    *,
    support_kind: str | None = None,
    require_activity_flower: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    support = _target_support_by_item(target or {})
    projected: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for source in people:
        person = dict(source)
        target_rewards: list[dict[str, Any]] = []
        for source_reward in person.get("rewards") or []:
            reward = dict(source_reward)
            support_entry = support.get(as_int(reward.get("item_id")) or 0)
            if (
                not support_entry
                or (
                    support_kind
                    and support_kind not in str(support_entry.get("kind") or "")
                )
            ):
                continue
            reward["target_support_kind"] = support_entry["kind"]
            reward["target_support_mode"] = support_entry["mode"]
            target_rewards.append(reward)
        progress_level = (
            as_int(person.get("reset_favor_level"))
            or as_int(person.get("favor_level"))
            or 0
        )
        next_level = min(
            (as_int(reward.get("level")) or 0 for reward in target_rewards),
            default=None,
        )
        distance = (
            max(0, int(next_level) - progress_level)
            if next_level is not None
            else None
        )
        threshold = _npc_favor_thresholds().get((
            as_int(person.get("npc_id")) or 0,
            int(next_level or 0),
        ))
        # Once the permanent favor level has reached this reward grade, the
        # repeatable reward is driven by the separate reset progress.  A
        # completed cycle clears resetFavorLevel/resetFavor back to zero while
        # leaving favorLvl at the permanent cap, so `reset level > 0` alone is
        # not enough to detect the active progression track.
        uses_reset_progress = (
            (as_int(person.get("reset_favor_level")) or 0) > 0
            or (
                next_level is not None
                and (as_int(person.get("favor_level")) or 0) >= int(next_level)
            )
        )
        current_favor = (
            as_int(person.get("reset_favor")) or 0
            if uses_reset_progress
            else as_int(person.get("favor")) or 0
        )
        required_favor = (
            int(threshold["reset_favor" if uses_reset_progress else "favor"])
            if threshold and next_level is not None
            else None
        )
        favor_gap = (
            max(0, required_favor - current_favor)
            if required_favor is not None
            else None
        )
        npc_id = as_int(person.get("npc_id")) or 0
        npc_thresholds = {
            grade: values
            for (threshold_npc_id, grade), values in _npc_favor_thresholds().items()
            if threshold_npc_id == npc_id
        }
        reset_options: list[dict[str, Any]] = []
        reward_steps: dict[int, list[dict[str, Any]]] = {}
        for reward in target_rewards:
            level = as_int(reward.get("level")) or 0
            step = int((npc_thresholds.get(level) or {}).get("step") or 1)
            reward_steps.setdefault(step, []).append(reward)
        for step, step_rewards in reward_steps.items():
            step_levels = sorted(
                grade
                for grade, values in npc_thresholds.items()
                if int(values.get("step") or 1) == step
            )
            if not step_levels:
                continue
            start_level = step_levels[0] - 1
            end_level = step_levels[-1]
            start_favor = int(
                (npc_thresholds.get(start_level) or {}).get("reset_favor") or 0
            )
            end_favor = int(
                (npc_thresholds.get(end_level) or {}).get("reset_favor") or 0
            )
            cost = max(0, end_favor - start_favor)
            reward_count = len(step_rewards)
            reset_options.append({
                "step": step,
                "start_level": start_level,
                "end_level": end_level,
                "favor_cost": cost,
                "reward_count": reward_count,
                "average_wujing_cost": cost / reward_count if reward_count else None,
                "reward_levels": sorted(as_int(reward.get("level")) or 0 for reward in step_rewards),
            })
        reset_options.sort(key=lambda option: (
            float(option.get("average_wujing_cost") or 2**63 - 1),
            int(option.get("favor_cost") or 0),
            int(option.get("step") or 0),
        ))
        best_reset_option = reset_options[0] if reset_options else None
        person.update({
            "target_rewards": target_rewards,
            "target_reward_count": len(target_rewards),
            "target_support_kinds": sorted({
                str(reward["target_support_kind"])
                for reward in target_rewards
            }),
            "target_next_level": next_level,
            "target_level_distance": distance,
            "target_current_favor": current_favor,
            "target_required_favor": required_favor,
            "target_favor_gap": favor_gap,
            "target_reset_options": reset_options,
            "target_best_reset_step": best_reset_option.get("step") if best_reset_option else None,
            "target_cycle_start_level": best_reset_option.get("start_level") if best_reset_option else None,
            "target_cycle_end_level": best_reset_option.get("end_level") if best_reset_option else None,
            "target_cycle_favor_cost": best_reset_option.get("favor_cost") if best_reset_option else None,
            "target_cycle_reward_count": best_reset_option.get("reward_count") if best_reset_option else 0,
            "target_average_wujing_cost": best_reset_option.get("average_wujing_cost") if best_reset_option else None,
            "target_recommendation_rank": None,
        })
        projected.append(person)
        if (
            person.get("giftable")
            and not person.get("hostile")
            and target_rewards
            and (
                not require_activity_flower
                or int(person.get("activity_flower_gift_count") or 0) > 0
            )
        ):
            candidates.append(person)
    def recommendation_key(person: dict[str, Any]) -> tuple[int, int, int]:
        return (
            int(person.get("target_average_wujing_cost"))
            if person.get("target_average_wujing_cost") is not None
            else 2**63 - 1,
            1 if person.get("gift_restriction") else 0,
            -int(person.get("target_reward_count") or 0),
        )

    candidates.sort(key=lambda person: (
        *recommendation_key(person),
        int(person.get("npc_id") or 0),
    ))
    dense_rank = 0
    previous_key: tuple[int, int, int] | None = None
    for person in candidates:
        current_key = recommendation_key(person)
        if current_key != previous_key:
            dense_rank += 1
            previous_key = current_key
        person["target_recommendation_rank"] = dense_rank
    recommended = candidates[0] if candidates else None
    recommendation = None
    if recommended is not None:
        recommendation = {
            "npc_id": recommended["npc_id"],
            "name": recommended["name"],
            "next_level": recommended["target_next_level"],
            "level_distance": recommended["target_level_distance"],
            "favor_gap": recommended["target_favor_gap"],
            "cycle_favor_cost": recommended["target_cycle_favor_cost"],
            "average_wujing_cost": recommended["target_average_wujing_cost"],
            "reward_count": recommended["target_reward_count"],
            "support_kinds": recommended["target_support_kinds"],
        }
    return projected, recommendation


def _first_supported_wujing_target(
    people: list[dict[str, Any]],
    books: list[dict[str, Any]],
) -> tuple[
    dict[str, Any] | None,
    list[dict[str, Any]],
    dict[str, Any] | None,
]:
    """Choose the first high-grade book, in upgrade order, helped by Xianyuan."""

    for book in books:
        if str(book.get("quality_grade_name") or "") not in {"仙品", "神品"}:
            continue
        projected, recommendation = _project_target_recommendations(
            people,
            book,
            support_kind="悟境",
            require_activity_flower=True,
        )
        if recommendation is not None:
            return book, projected, recommendation
    projected, _ = _project_target_recommendations(
        people,
        None,
        support_kind="悟境",
        require_activity_flower=True,
    )
    return None, projected, None


@lru_cache(maxsize=1)
def _npc_gift_catalogs() -> tuple[
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
]:
    return (
        _parsed_config_index("Npc"),
        _parsed_config_index("NpcHobby"),
        _parsed_config_index("NpcFavorability"),
    )


def _npc_gift_options(npc_id: int) -> dict[str, Any]:
    npcs, hobbies, favorability = _npc_gift_catalogs()
    items = _item_index()
    npc = npcs.get(npc_id) or {}
    hobby_ids = [
        value
        for value in (as_int(raw) for raw in (npc.get("hobby") or []))
        if value
    ]
    groups: list[dict[str, Any]] = []
    options: list[dict[str, Any]] = []
    seen_items: set[int] = set()
    career_desc = as_int(npc.get("careerDesc")) or 0
    cant_send_flower = (as_int(npc.get("cantSendFlower")) or 0) == 1
    for hobby_id in hobby_ids:
        hobby = hobbies.get(hobby_id) or {}
        activity_gift = bool(
            (as_int(hobby.get("hobbyEffect")) or 0)
            or (as_int(hobby.get("giftEffect")) or 0)
        )
        hobby_item_ids = [
            value
            for value in (as_int(raw) for raw in (hobby.get("items") or []))
            if value
        ]
        groups.append({
            "hobby_id": hobby_id,
            "name": str(hobby.get("name_plain") or hobby.get("name") or hobby_id),
            "description": str(
                hobby.get("describe_plain")
                or hobby.get("descript_plain")
                or hobby.get("describe")
                or hobby.get("descript")
                or ""
            ),
            "item_count": len(hobby_item_ids),
            "activity_gift": activity_gift,
        })
        for item_id in hobby_item_ids:
            if item_id in seen_items:
                continue
            seen_items.add(item_id)
            favor = favorability.get(item_id) or {}
            item = items.get(item_id) or {}
            gift_type = as_int(favor.get("type")) or 0
            career_conditional = bool(
                cant_send_flower and career_desc > 0 and gift_type in {1, 2}
            )
            options.append({
                "item_id": item_id,
                "name": str(item.get("name_plain") or item.get("name") or item_id),
                "description": str(item.get("descript_plain") or ""),
                "hobby_id": hobby_id,
                "hobby_name": str(hobby.get("name_plain") or hobby.get("name") or hobby_id),
                "favorability": as_int(favor.get("favorability")) or 0,
                "gift_type": gift_type,
                "activity_gift": activity_gift,
                "career_conditional": career_conditional,
            })
    return {
        "can_send_config": (as_int(npc.get("canSend")) or 0) > 0,
        "no_gift_description": str(
            npc.get("noGiftDesc_plain") or npc.get("noGiftDesc") or ""
        ),
        "career_desc": career_desc,
        "cant_send_flower": cant_send_flower,
        "gift_restriction": (
            "功法流派不符时，仙花与仙宝不可赠送"
            if cant_send_flower and career_desc > 0
            else ""
        ),
        "hobby_groups": groups,
        "gift_options": options,
        "gift_option_count": len(options),
        "activity_flower_gift_count": sum(
            bool(option["activity_gift"]) for option in options
        ),
    }


@lru_cache(maxsize=1)
def _lang_index() -> dict[int, str]:
    path = _find_default_lang_path(resolve_fanxiu_export_root())
    return load_fanxiu_lang_map(path) if path else {}


def _data_root(memory: MumuProcessMemory) -> tuple[int, bool]:
    cached = _read_cached_data_address(memory, "npc")
    if cached:
        try:
            fields = LuaJitReader(memory).fields(LuaRef("table", cached))
            if "_NpcInfoList" in fields and "npcBaseDataTable" in fields:
                return cached, True
        except FanxiuRuntimeMemoryError:
            pass
    root, root_cache_hit = resolve_manager_root(
        memory,
        manager_key="npc",
        marker=_NPC_MARKER,
        required_methods=_NPC_METHODS,
        validate=lambda reader, address: _npc_data_fields(reader, address),
    )
    address = _npc_data_address(LuaJitReader(memory), root)
    _write_cached_data_address(memory, "npc", address)
    return address, root_cache_hit


def _name_index(reader: LuaJitReader, npc_data: dict[Any, Any]) -> dict[int, dict[str, Any]]:
    names = _lang_index()
    result: dict[int, dict[str, Any]] = {}
    for raw_id, raw_config in reader.fields(npc_data.get("npcBaseDataTable")).items():
        npc_id = reader.long(raw_id)
        if not npc_id or not isinstance(raw_config, LuaRef) or raw_config.kind != "table":
            continue
        array = reader.table(raw_config.address).get("array") or []
        lang_id = as_int(array[2]) if len(array) > 2 else None
        result[npc_id] = {
            "name": str(names.get(lang_id or 0) or f"仙缘 {npc_id}"),
            "name_lang_id": lang_id,
        }
    return result


def _reward_kind(item: dict[str, Any]) -> str:
    item_type = as_int(item.get("type")) or 0
    subtype = as_int(item.get("subType")) or 0
    name = str(item.get("name_plain") or item.get("name") or "")
    if item_type == 3:
        return "功法"
    if item_type == 999 and subtype == 33:
        return "悟境"
    if "功法" in name or "心法" in name or "真悟" in name or name.startswith("悟·"):
        return "功法相关"
    if item_type == 21:
        return "自选/宝匣"
    return "物资"


def _rewards(
    reader: LuaJitReader,
    fields: dict[Any, Any],
) -> list[dict[str, Any]]:
    items = _item_index()
    states = {
        reader.long(key): as_int(value) or 0
        for key, value in reader.dictionary_fields(fields.get("favorUpgradeMap")).items()
        if reader.long(key)
    }
    raw_rewards, _ = reader.list_items(fields.get("giftRewardList"))
    result: list[dict[str, Any]] = []
    for raw_reward in raw_rewards:
        reward = reader.fields(raw_reward)
        item_id = as_int(reward.get("id")) or 0
        reward_key = as_int(reward.get("key")) or 0
        knowledge = items.get(item_id) or {}
        state = states.get(reward_key, 0)
        result.append({
            "level": as_int(reward.get("lvl")) or 0,
            "reward_key": reward_key,
            "item_id": item_id,
            "name": str(knowledge.get("name_plain") or knowledge.get("name") or item_id),
            "count": as_int(reward.get("num")) or 0,
            "kind": _reward_kind(knowledge),
            "state": state,
            "state_name": {0: "未解锁", 1: "可领取", 2: "已领取", 3: "失效"}.get(state, "未知"),
            "description": str(knowledge.get("descript_plain") or ""),
        })
    return sorted(result, key=lambda item: (item["level"], item["reward_key"]))


def read_xianyuan_atlas_runtime() -> dict[str, Any]:
    started = time.perf_counter()
    memory = MumuProcessMemory.discover_cached(max_age_seconds=None)
    reader = LuaJitReader(memory)
    address, cache_hit = _data_root(memory)
    data = reader.fields(LuaRef("table", address))
    names = _name_index(reader, data)
    raw_npcs, runtime_object_count = reader.list_items(data.get("_NpcInfoList"))
    people: list[dict[str, Any]] = []
    for runtime_index, raw_npc in enumerate(raw_npcs):
        fields = reader.fields(raw_npc)
        npc_id = as_int(fields.get("id")) or 0
        open_state = as_int(fields.get("isOpen")) or 0
        if not npc_id or open_state <= 0:
            continue
        favor = as_int(fields.get("favor")) or 0
        favor_level = as_int(fields.get("favorLvl")) or 0
        rewards = _rewards(reader, fields)
        gift_profile = _npc_gift_options(npc_id)
        hostile = favor < 0 or favor_level < 0
        giftable = bool(gift_profile["can_send_config"]) and not hostile
        if hostile:
            relation_type = "敌对"
        elif giftable:
            relation_type = "可送礼"
        else:
            relation_type = "已结识"
        reward_kinds = sorted({reward["kind"] for reward in rewards})
        book_reward_count = sum(
            reward["kind"] in {"功法", "悟境", "功法相关"}
            for reward in rewards
        )
        people.append({
            "npc_id": npc_id,
            # Preserve the native list identity before the presentation sort
            # below. GUI list alignment must never consume presentation order.
            "runtime_index": runtime_index,
            **names.get(npc_id, {"name": f"仙缘 {npc_id}", "name_lang_id": None}),
            **gift_profile,
            "open_state": open_state,
            "relation_type": relation_type,
            "hostile": hostile,
            "giftable": giftable,
            "favor_level": favor_level,
            "favor": favor,
            "reset_favor_level": as_int(fields.get("resetFavorLevel")) or 0,
            "reset_favor": as_int(fields.get("resetFavor")) or 0,
            "space_type": as_int(fields.get("spaceType")) or 0,
            "reward_count": len(rewards),
            "book_reward_count": book_reward_count,
            "reward_kinds": reward_kinds,
            "claimable_count": sum(reward["state"] == 1 for reward in rewards),
            "claimed_count": sum(reward["state"] == 2 for reward in rewards),
            "rewards": rewards,
        })
    people.sort(key=lambda item: (
        not item["giftable"],
        item["hostile"],
        -item["book_reward_count"],
        item["name"],
    ))
    return {
        "people": people,
        "runtime_complete": bool(people),
        "runtime_error": "",
        "runtime_updated_at": time.time(),
        "runtime_item_count": len(people),
        "summary": {
            "opened_count": len(people),
            "giftable_count": sum(person["giftable"] for person in people),
            "hostile_count": sum(person["hostile"] for person in people),
            "with_storage_count": sum(bool(person["rewards"]) for person in people),
            "runtime_object_count": runtime_object_count or len(raw_npcs),
        },
        "runtime_debug": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "npc_data_address": f"0x{address:x}",
            "npc_data_cache_hit": cache_hit,
            "elapsed_seconds": time.perf_counter() - started,
            "name_source": "npcBaseDataTable[2] -> Chinese lang map",
            "storage_source": "NpcInfoVo.giftRewardList + favorUpgradeMap",
            "gift_option_source": "Npc.hobby -> NpcHobby.items -> NpcFavorability",
        },
    }


def collect_xianyuan_atlas_snapshot_once() -> dict[str, Any]:
    snapshot = read_xianyuan_atlas_runtime()
    if not snapshot.get("runtime_complete"):
        raise RuntimeError("仙缘动态插桩数据尚未完整加载")
    with Session(engine) as session:
        upsert_inventory_hall_snapshot(
            session,
            XIANYUAN_ATLAS_KEY,
            snapshot,
            source_kind="dynamic_instrumentation",
            entity_name="仙缘图鉴",
            require_complete_runtime=True,
        )
    return snapshot


def load_xianyuan_atlas_snapshot(session: Session) -> dict[str, Any]:
    snapshot = load_inventory_hall_snapshot(session, XIANYUAN_ATLAS_KEY) or {
        "people": [],
        "runtime_complete": False,
        "runtime_error": "尚未从游戏更新",
        "runtime_updated_at": 0,
        "runtime_item_count": 0,
        "summary": {},
        "runtime_debug": {},
    }
    # Gift eligibility is versioned static config.  Enrich older runtime
    # snapshots at read time so exporting a newer config does not require any
    # game interaction or a fresh memory collection.
    snapshot = dict(snapshot)
    people: list[dict[str, Any]] = []
    for person in snapshot.get("people") or []:
        if not isinstance(person, dict):
            continue
        person = dict(person)
        person.pop("recommended_visible", None)
        profile = _npc_gift_options(as_int(person.get("npc_id")) or 0)
        hostile = bool(person.get("hostile"))
        giftable = bool(profile["can_send_config"]) and not hostile
        selectable_rewards = _project_selectable_rewards(list(person.get("rewards") or []))
        reset_steps = _npc_reset_steps(as_int(person.get("npc_id")) or 0)
        people.append({
            **person,
            **profile,
            "selectable_rewards": selectable_rewards,
            "selectable_reward_count": len(selectable_rewards),
            "wujing_selectable_reward_count": sum(
                bool(reward.get("contains_wujing"))
                for reward in selectable_rewards
            ),
            "reset_steps": reset_steps,
            "giftable": giftable,
            "relation_type": "敌对" if hostile else "可送礼" if giftable else "已结识",
        })
    target: dict[str, Any] | None = None
    recommendation: dict[str, Any] | None = None
    gongfa_snapshot = load_inventory_hall_snapshot(session, "gongfa_atlas")
    if gongfa_snapshot:
        from backend.core.fanxiu.instrumentation.gongfa_atlas import _project_gongfa_books

        gongfa_books = _project_gongfa_books(list(gongfa_snapshot.get("books") or []))
        target, people, recommendation = _first_supported_wujing_target(
            people,
            gongfa_books,
        )
    else:
        people, recommendation = _project_target_recommendations(
            people,
            None,
            support_kind="悟境",
            require_activity_flower=True,
        )
    snapshot["people"] = people
    snapshot["target_gongfa"] = (
        {
            key: target.get(key)
            for key in (
                "book_id", "name", "quality_grade_name", "filter_category",
                "jie", "max_jie", "wujing", "max_wujing",
                "tongxuan", "max_tongxuan", "upgrade_index",
            )
        }
        if target else None
    )
    snapshot["recommendation"] = recommendation
    summary = dict(snapshot.get("summary") or {})
    summary.pop("recommended_visible_count", None)
    summary["giftable_count"] = sum(person["giftable"] for person in people)
    summary["hostile_count"] = sum(person.get("hostile", False) for person in people)
    snapshot["summary"] = summary
    runtime_debug = dict(snapshot.get("runtime_debug") or {})
    runtime_debug["gift_option_source"] = (
        "Npc.hobby -> NpcHobby.items -> NpcFavorability"
    )
    snapshot["runtime_debug"] = runtime_debug
    return snapshot
