from __future__ import annotations

"""Strict read-only snapshot of the already-loaded ``BothdrawMgr`` model."""

import time
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from backend.core.fanxiu.catalog.item import load_fanxiu_item_catalog
from backend.core.fanxiu.catalog.lua_config import parse_fanxiu_generated_lua_config
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    manager_index_fields,
    read_runtime_snapshot_with_rebind,
    resolve_lua_global_manager_root,
    resolve_manager_root,
)
from backend.core.fanxiu.instrumentation.wallet import (
    WALLET_METHODS,
    wallet_currency_data,
)


_BOTHDRAW_METHODS = frozenset({"Inst_get", "GetBothInfo", "OpenOptionSelectView"})
_REVENUE_METHODS = frozenset({"Inst_get", "GetRevenueDataInfo", "RevenueDataInfo"})
_QUEST_METHODS = frozenset({"LuaQuestMgr", "Inst_get", "GetTaskState"})
_DEFAULT_CUMULATIVE_REWARD_VISIBLE_SLOT_COUNT = 4
_ACTIVITY_TASK_TYPE = 3
_TASK_STATUS_RECEIVING = 3
_TASK_STATUS_CLAIMABLE = 4
_TASK_STATUS_CLAIMED = 5


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value) if isinstance(value, LuaRef) and value.kind == "table" else {}


def _list_values(reader: LuaJitReader, value: Any) -> list[Any]:
    wrapper = _fields(reader, value)
    data = wrapper.get("_dt_")
    if not isinstance(data, LuaRef) or data.kind != "table":
        return []
    values = [item for item in reader.table(data.address)["array"] if item is not None]
    count = int(wrapper.get("count") or len(values))
    return values[:count]


def _dictionary_values(reader: LuaJitReader, value: Any) -> list[Any]:
    return list(_dictionary_items(reader, value).values())


def _dictionary_items(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    data = _fields(reader, value).get("_dt_")
    if not isinstance(data, LuaRef) or data.kind != "table":
        return {}
    table = reader.table(data.address)
    items = dict(table.get("fields") or {})
    for index, item in enumerate(table.get("array") or []):
        if item is not None:
            items.setdefault(index, item)
    return items


def _bothdraw_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _BOTHDRAW_METHODS)
    instance = _fields(reader, manager.get("inst"))
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("BothdrawData"))
    if "_BothInfoMap" not in data:
        raise FanxiuRuntimeMemoryError("BothdrawMgr 当期活动数据尚未加载")
    return data


def _revenue_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _REVENUE_METHODS)
    instance = _fields(reader, manager.get("inst"))
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("RevenueData"))
    if "V_ActivityDic" not in data:
        raise FanxiuRuntimeMemoryError("RevenueMgr 当期活动数据尚未加载")
    return data


def _quest_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _QUEST_METHODS)
    instance = _fields(reader, manager.get("inst"))
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("QuestData"))
    if "taskInfoMap" not in data or "V_RevenueTaskDic" not in data:
        raise FanxiuRuntimeMemoryError("QuestMgr 活动任务数据尚未加载")
    return data


def _dictionary_item(reader: LuaJitReader, value: Any, key: int) -> Any:
    items = _dictionary_items(reader, value)
    return items.get(key) or items.get(float(key))


def build_bothdraw_task_snapshot(
    *,
    activity_id: int,
    task_configs: Iterable[dict[str, Any]],
    task_entries: Iterable[dict[str, Any]],
    finished_task_ids: Iterable[int],
) -> dict[str, Any]:
    """Combine live Revenue task definitions with authoritative Quest states."""

    configs = sorted(
        (dict(item) for item in task_configs),
        key=lambda item: (int(item.get("sort") or 0), int(item.get("id") or 0)),
    )
    entries = {
        int(item.get("taskId") or item.get("task_id") or 0): dict(item)
        for item in task_entries
        if int(item.get("taskId") or item.get("task_id") or 0) > 0
    }
    finished = {int(value) for value in finished_task_ids if int(value) > 0}
    if not configs:
        raise FanxiuRuntimeMemoryError(
            f"QuestMgr 未提供活动 {int(activity_id)} 的任务定义"
        )

    tasks: list[dict[str, Any]] = []
    missing: list[int] = []
    for config in configs:
        task_id = int(config.get("id") or 0)
        entry = entries.get(task_id)
        if task_id in finished:
            status = _TASK_STATUS_CLAIMED
            state = "claimed"
        elif entry is not None:
            status = int(entry.get("status") or 0)
            state = (
                "claimable"
                if status == _TASK_STATUS_CLAIMABLE
                else "receiving"
                if status == _TASK_STATUS_RECEIVING
                else "claimed"
                if status == _TASK_STATUS_CLAIMED
                else "unknown"
            )
        else:
            missing.append(task_id)
            continue
        tasks.append(
            {
                "task_id": task_id,
                "name": str(config.get("name") or task_id),
                "description": str(config.get("desc") or ""),
                "sort": int(config.get("sort") or 0),
                "status": status,
                "state": state,
                "turn": int((entry or {}).get("turn") or 0),
                "target_turn": int((entry or {}).get("targetTurn") or 0),
                "reward_time": int((entry or {}).get("rewardTime") or 0),
            }
        )
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"活动 {int(activity_id)} 的任务状态不完整：missing={missing}"
        )
    claimable = [item for item in tasks if item["state"] == "claimable"]
    return {
        "activity_id": int(activity_id),
        "task_count": len(tasks),
        "claimed_count": sum(item["state"] == "claimed" for item in tasks),
        "claimable_count": len(claimable),
        "tasks": tasks,
        "claimable": claimable,
        "all_current_rewards_claimed": not claimable,
    }


def build_bothdraw_cumulative_rewards(
    *,
    progress: int,
    milestones: Iterable[dict[str, Any]],
    claimed_ids: Iterable[int],
    visible_slot_count: int = _DEFAULT_CUMULATIVE_REWARD_VISIBLE_SLOT_COUNT,
) -> dict[str, Any]:
    """Reproduce the game's cumulative-reward state without visual inference.

    ``visible_slot_count`` is a verified property of the current GUI reward
    grid, not a universal Bothdraw rule.  Most existing activities display
    four slots; Lingxiao's cumulative tab displays two complete rows of four.
    The caller must provide a real page observation before opting into a
    non-default size.
    """

    visible_slot_count = int(visible_slot_count)
    if visible_slot_count <= 0:
        raise ValueError("visible_slot_count 必须为正整数")

    rows = [dict(row) for row in milestones]
    claimed = tuple(int(value) for value in claimed_ids)
    claimed_count = len(claimed)
    if claimed_count > len(rows):
        raise FanxiuRuntimeMemoryError(
            f"累计奖励已领取数量异常：{claimed_count} > {len(rows)}"
        )

    start = (claimed_count // visible_slot_count) * visible_slot_count
    visible_indices = list(
        range(start, min(start + visible_slot_count, len(rows)))
    )
    if len(visible_indices) < visible_slot_count:
        missing = visible_slot_count - len(visible_indices)
        visible_indices = list(range(max(0, start - missing), start)) + visible_indices
    visible_slots = {index: slot for slot, index in enumerate(visible_indices, start=1)}

    rewards: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        threshold = int(row.get("progress") or row.get("times") or 0)
        reward_id = int(row.get("id") or 0)
        is_claimed = index < claimed_count
        can_claim = int(progress) >= threshold and not is_claimed
        rewards.append(
            {
                "index": index,
                "id": reward_id,
                "threshold": threshold,
                "reward": str(row.get("reward") or ""),
                "state": "claimed" if is_claimed else "claimable" if can_claim else "locked",
                "is_claimed": is_claimed,
                "can_claim": can_claim,
                "visible_slot": visible_slots.get(index),
            }
        )
    return {
        "progress": int(progress),
        "claimed_count": claimed_count,
        "claimed_ids": list(claimed),
        "rewards": rewards,
        "claimable": [row for row in rewards if row["can_claim"]],
        "visible_claimable": [
            row
            for row in rewards
            if row["can_claim"] and row["visible_slot"] is not None
        ],
    }


def _current_library_ids(reader: LuaJitReader, data: dict[Any, Any]) -> tuple[int, ...]:
    candidates: list[tuple[int, ...]] = []
    for info in _dictionary_values(reader, data.get("_BothInfoMap")):
        for optional in _list_values(reader, _fields(reader, info).get("optionalVOs")):
            values = tuple(
                int(value)
                for value in _list_values(
                    reader,
                    _fields(reader, optional).get("libraryResIds"),
                )
                if int(value or 0) > 0
            )
            if len(values) == 4:
                candidates.append(values)
    unique = sorted(set(candidates))
    if not unique:
        raise FanxiuRuntimeMemoryError("BothdrawMgr 未提供第一排四个当期候选")
    if len(unique) != 1:
        raise FanxiuRuntimeMemoryError(f"BothdrawMgr 第一排候选不唯一：{unique}")
    return unique[0]


@lru_cache(maxsize=2)
def _optional_gift_rows(export_root_text: str) -> dict[int, dict[str, Any]]:
    root = Path(export_root_text)
    paths = [
        path
        for path in root.glob(
            "by_source/lscripts/generate/cfg/item*/text_assets/OptionalGift.lua"
        )
        if path.is_file()
    ]
    if not paths:
        raise FanxiuRuntimeMemoryError("未找到当前版本 OptionalGift 配置")
    path = max(paths, key=lambda item: item.stat().st_mtime_ns)
    rows = parse_fanxiu_generated_lua_config(path).get("rows") or []
    return {
        int(row.get("id") or row.get("_row_key") or 0): row
        for row in rows
        if isinstance(row, dict) and int(row.get("id") or row.get("_row_key") or 0) > 0
    }


def build_bothdraw_reward_items(
    library_ids: Iterable[int],
    *,
    optional_rows: dict[int, dict[str, Any]],
    item_cards: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve live optional-library rows into current item identities."""

    cards = {
        int(card.get("id") or 0): card
        for card in item_cards
        if isinstance(card, dict) and int(card.get("id") or 0) > 0
    }
    result: list[dict[str, Any]] = []
    for library_id in library_ids:
        row = optional_rows.get(int(library_id)) or {}
        item_id = int(row.get("giftID") or 0)
        card = cards.get(item_id) or {}
        target_id = card.get("linked_talisman_refine_target_id")
        result.append(
            {
                "library_id": int(library_id),
                "item_id": item_id,
                "name": str(card.get("name") or item_id or library_id),
                "target_talisman_id": (
                    int(target_id) if target_id not in (None, "") else None
                ),
                "kind": (
                    "talisman_refine_material"
                    if target_id not in (None, "")
                    else str(card.get("kind") or "")
                ),
            }
        )
    return result


def build_bothdraw_runtime_reward_items(
    library_ids: Iterable[int],
    *,
    runtime_rows: dict[int, dict[str, Any]],
    item_cards: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve the current optional rewards from already-loaded runtime rows.

    Newer Bothdraw activities expose their optional-library rows directly on
    ``BothdrawData.optionalLibrary``.  Those live rows are authoritative for
    the current activity and must not be silently replaced by an older
    exported ``OptionalGift`` table when the ids are unknown there.
    """

    cards = {
        int(card.get("id") or 0): card
        for card in item_cards
        if isinstance(card, dict) and int(card.get("id") or 0) > 0
    }
    result: list[dict[str, Any]] = []
    missing: list[int] = []
    for raw_library_id in library_ids:
        library_id = int(raw_library_id)
        row = runtime_rows.get(library_id) or {}
        item_id = int(row.get("item_id") or 0)
        card = cards.get(item_id) or {}
        if item_id <= 0 or not card:
            missing.append(library_id)
            continue
        target_refine_id = card.get("linked_talisman_refine_target_id")
        if target_refine_id not in (None, ""):
            kind = "talisman_refine_material"
        elif card.get("linked_fashion_id") not in (None, ""):
            kind = "fashion"
        elif card.get("linked_talisman_id") not in (None, ""):
            kind = "talisman"
        elif card.get("linked_gongfa_id") not in (None, ""):
            kind = "gongfa"
        else:
            kind = str(card.get("kind") or "")
        result.append(
            {
                "library_id": library_id,
                "item_id": item_id,
                "name": str(card.get("name") or item_id),
                "target_talisman_id": (
                    int(target_refine_id)
                    if target_refine_id not in (None, "")
                    else None
                ),
                "kind": kind,
                "target_id": int(
                    card.get("linked_fashion_id")
                    or card.get("linked_talisman_id")
                    or card.get("linked_gongfa_id")
                    or target_refine_id
                    or 0
                ),
                "reward_limit": str(row.get("reward_limit") or ""),
            }
        )
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"BothdrawMgr 当期候选物品映射不完整：missing={missing}"
        )
    return result


def _runtime_optional_reward_rows(
    reader: LuaJitReader,
    data: dict[Any, Any],
) -> dict[int, dict[str, Any]] | None:
    """Decode loaded optional-library rows without causing game-side loading."""

    optional_library = data.get("optionalLibrary")
    if not isinstance(optional_library, LuaRef) or optional_library.kind != "table":
        return None
    libraries = _fields(reader, optional_library)
    if not libraries:
        return None
    rows: dict[int, dict[str, Any]] = {}
    for library in libraries.values():
        reward_tab = _fields(reader, library).get("rewardTab")
        if not isinstance(reward_tab, LuaRef) or reward_tab.kind != "table":
            continue
        for value in reader.table(reward_tab.address).get("array") or []:
            fields = _fields(reader, value)
            library_id = int(fields.get("id") or 0)
            reward = _fields(reader, fields.get("reward"))
            item_id = int(reward.get("code") or 0)
            if library_id <= 0 or item_id <= 0:
                continue
            candidate = {
                "item_id": item_id,
                "reward_limit": str(fields.get("rewardLimit") or ""),
            }
            previous = rows.get(library_id)
            if previous is not None and previous != candidate:
                raise FanxiuRuntimeMemoryError(
                    f"BothdrawMgr 候选 {library_id} 映射冲突：{previous} != {candidate}"
                )
            rows[library_id] = candidate
    if not rows:
        raise FanxiuRuntimeMemoryError("BothdrawMgr optionalLibrary 已加载但没有可解码奖励")
    return rows


_KUNLUN_REWARD_LIMIT_RE = re.compile(
    r"^(IsGetFashionMax|IsGetTalismanGradeMax|IsGetGongFaMax)\|"
    r"(\d+)_(\d+)_1$"
)
_KUNLUN_LIMIT_KIND = {
    "IsGetFashionMax": "fashion",
    "IsGetTalismanGradeMax": "talisman",
    "IsGetGongFaMax": "gongfa",
}


def _validated_kunlun_targets(
    reward_items: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in reward_items:
        match = _KUNLUN_REWARD_LIMIT_RE.fullmatch(
            str(item.get("reward_limit") or "")
        )
        if match is None:
            raise FanxiuRuntimeMemoryError(
                f"昆仑候选 {item.get('library_id')} rewardLimit 无法验证"
            )
        limit_kind, raw_target_id, raw_item_id = match.groups()
        kind = _KUNLUN_LIMIT_KIND[limit_kind]
        target_id = int(raw_target_id)
        item_id = int(raw_item_id)
        if (
            kind != str(item.get("kind") or "")
            or target_id != int(item.get("target_id") or 0)
            or item_id != int(item.get("item_id") or 0)
        ):
            raise FanxiuRuntimeMemoryError(
                "昆仑候选 rewardLimit 与目录身份不一致："
                f"limit={(kind, target_id, item_id)}, "
                f"catalog={(item.get('kind'), item.get('target_id'), item.get('item_id'))}"
            )
        result.append(dict(item))
    if len(result) != 4:
        raise FanxiuRuntimeMemoryError(f"昆仑第一排候选不完整：{len(result)}")
    return result


def _read_loaded_fashion_rank(target_id: int) -> dict[str, Any]:
    methods = frozenset({"Inst_get", "GetFashionSexByHandPoint"})

    def data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
        manager = manager_index_fields(reader, root_address, methods)
        instance = _fields(reader, manager.get("inst"))
        model = _fields(reader, instance.get("Model"))
        data = _fields(reader, model.get("FashionData"))
        values, declared_count = reader.list_items(data.get("AllFashionInfoVoList"))
        if not values or (
            declared_count is not None and int(declared_count) != len(values)
        ):
            raise FanxiuRuntimeMemoryError("FashionMgr 时装清单尚未完整加载")
        return data

    memory = MumuProcessMemory.discover_cached()
    state_address = int(_lua_addresses(memory)["state"], 16)
    root, cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key="kunlun-fashion-rank",
        state_address=state_address,
        global_name="FashionMgr",
        required_methods=methods,
        validate=data_fields,
    )
    reader = LuaJitReader(memory)
    values, _count = reader.list_items(
        data_fields(reader, root).get("AllFashionInfoVoList")
    )
    matches = [
        _fields(reader, value)
        for value in values
        if int(_fields(reader, value).get("id") or 0) == int(target_id)
    ]
    if len(matches) != 1:
        raise FanxiuRuntimeMemoryError(
            f"FashionMgr 时装 {target_id} 身份不唯一：{len(matches)}"
        )
    fields = matches[0]
    return {
        "rank": int(fields.get("level") or 0) if bool(fields.get("isGet")) else 0,
        "owned": bool(fields.get("isGet")),
        "cache_hit": cache_hit,
    }


def build_bothdraw_revenue_task_snapshot(
    *,
    activity_id: int,
    task_groups: dict[int, Iterable[dict[str, Any]]],
) -> dict[str, Any]:
    """Normalize the activity-owned RevenueTask view models.

    Some activities do not populate ``V_RevenueTaskDic`` with definitions.
    Their authoritative model instead lives in
    ``QuestData.V_AllTaskInfoDic[activity_id]``: each visible group contains
    task view models with a live ``serverData`` entry until its reward is
    finished.  This helper deliberately accepts only those already-associated
    rows; it never joins stale exported ``ActiveTask`` rows by ID.
    """

    normalized_groups: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    all_tasks: list[dict[str, Any]] = []
    for group_id, raw_rows in sorted(task_groups.items()):
        rows: list[dict[str, Any]] = []
        for position, raw in enumerate(raw_rows, start=1):
            row = dict(raw)
            task_id = int(row.get("id") or 0)
            if task_id <= 0 or task_id in seen_ids:
                raise FanxiuRuntimeMemoryError(
                    f"活动 {int(activity_id)} 的 RevenueTask ID 缺失或重复：{task_id}"
                )
            seen_ids.add(task_id)
            finished = row.get("isFinished")
            if not isinstance(finished, bool):
                raise FanxiuRuntimeMemoryError(
                    f"活动 {int(activity_id)} 的任务 {task_id} 缺少 isFinished 状态"
                )
            server = dict(row.get("serverData") or {})
            if finished:
                if server:
                    raise FanxiuRuntimeMemoryError(
                        f"活动 {int(activity_id)} 的已完成任务 {task_id} 仍携带 serverData"
                    )
                status = _TASK_STATUS_CLAIMED
                state = "claimed"
            else:
                runtime_task_id = int(server.get("taskId") or 0)
                if runtime_task_id != task_id:
                    raise FanxiuRuntimeMemoryError(
                        f"活动 {int(activity_id)} 的任务 {task_id} Runtime 身份不一致：{runtime_task_id}"
                    )
                status = int(server.get("status") or 0)
                state = (
                    "claimable"
                    if status == _TASK_STATUS_CLAIMABLE
                    else "receiving"
                    if status == _TASK_STATUS_RECEIVING
                    else "claimed"
                    if status == _TASK_STATUS_CLAIMED
                    else "unknown"
                )
            task = {
                "task_id": task_id,
                "group_id": int(group_id),
                "position": position,
                "status": status,
                "state": state,
                "turn": int(server.get("turn") or 0),
                "target_turn": int(server.get("targetTurn") or 0),
                "reward_time": int(server.get("rewardTime") or 0),
            }
            rows.append(task)
            all_tasks.append(task)
        if not rows:
            raise FanxiuRuntimeMemoryError(
                f"活动 {int(activity_id)} 的 RevenueTask 分组 {int(group_id)} 为空"
            )
        normalized_groups.append(
            {"group_id": int(group_id), "task_count": len(rows), "tasks": rows}
        )
    if not normalized_groups:
        raise FanxiuRuntimeMemoryError(f"活动 {int(activity_id)} 未加载 RevenueTask 分组")
    claimable = [task for task in all_tasks if task["state"] == "claimable"]
    return {
        "activity_id": int(activity_id),
        "task_count": len(all_tasks),
        "claimed_count": sum(task["state"] == "claimed" for task in all_tasks),
        "claimable_count": len(claimable),
        "task_groups": normalized_groups,
        "tasks": all_tasks,
        "claimable": claimable,
        "all_current_claimable_rewards_claimed": not claimable,
    }


def read_kunlun_first_row_runtime() -> dict[str, Any]:
    """Read Kunlun's four candidates and comparable owned ranks, strictly read-only."""

    started_at = time.perf_counter()
    optional = read_bothdraw_optional_reward_runtime()
    if optional.get("complete") is not True:
        return {
            "ok": False,
            "complete": False,
            "reason": str(optional.get("reason") or "昆仑第一排候选读取不完整"),
            "reward_items": [],
            "owned_items": [],
        }
    try:
        rewards = _validated_kunlun_targets(optional.get("reward_items") or [])
        from backend.core.fanxiu.instrumentation.gongfa_equipment import (
            _GONGFA_MARKER,
            _GONGFA_METHODS,
            _gongfa_data_fields,
            _gongfa_progression_index,
        )
        from backend.core.fanxiu.instrumentation.magic_treasure import (
            _TALISMAN_METHODS,
            _owned_talisman_rows,
            _talisman_data_fields,
        )

        memory = MumuProcessMemory.discover_cached(max_age_seconds=None)
        reader = LuaJitReader(memory)
        talisman_root, talisman_cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="magic-treasure-talisman",
            state_address=int(_lua_addresses(memory)["state"], 16),
            global_name="TalismanMgr",
            required_methods=_TALISMAN_METHODS,
            validate=_talisman_data_fields,
        )
        reader = LuaJitReader(memory)
        talismans = {
            int(row["talisman_id"]): row
            for row in _owned_talisman_rows(
                reader, _talisman_data_fields(reader, talisman_root)
            )
        }
        gongfa_root, gongfa_cache_hit = resolve_manager_root(
            memory,
            manager_key="gongfa-equipment-state",
            marker=_GONGFA_MARKER,
            required_methods=_GONGFA_METHODS,
            validate=_gongfa_data_fields,
        )
        reader = LuaJitReader(memory)
        gongfa = _gongfa_progression_index(
            reader, _gongfa_data_fields(reader, gongfa_root)
        )
        owned: list[dict[str, Any]] = []
        fashion_cache_hit: bool | None = None
        for reward in rewards:
            kind = str(reward["kind"])
            target_id = int(reward["target_id"])
            if kind == "fashion":
                state = _read_loaded_fashion_rank(target_id)
                rank = int(state["rank"])
                is_owned = bool(state["owned"])
                fashion_cache_hit = bool(state["cache_hit"])
            elif kind == "talisman":
                state = talismans.get(target_id)
                rank = int((state or {}).get("stage") or 0)
                is_owned = state is not None
            elif kind == "gongfa":
                state = gongfa.get(target_id)
                rank = int((state or {}).get("jie") or 0)
                is_owned = state is not None
            else:
                raise FanxiuRuntimeMemoryError(f"昆仑候选类型不支持：{kind}")
            owned.append(
                {
                    "target_id": target_id,
                    "item_id": int(reward["item_id"]),
                    "name": str(reward["name"]),
                    "kind": kind,
                    "rank": rank,
                    "weight": 0,
                    "owned": is_owned,
                }
            )
        return {
            "ok": True,
            "complete": True,
            "source": "loaded_runtime_memory+versioned_item_catalog",
            "reward_items": rewards,
            "owned_items": owned,
            "selected_big_reward": optional.get("selected_big_reward"),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "fashion_root_cache_hit": fashion_cache_hit,
                "talisman_root_cache_hit": talisman_cache_hit,
                "gongfa_root_cache_hit": gongfa_cache_hit,
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "complete": False,
            "source": "loaded_runtime_memory+versioned_item_catalog",
            "reason": str(exc),
            "reward_items": [],
            "owned_items": [],
            "elapsed_seconds": time.perf_counter() - started_at,
        }


def read_bothdraw_optional_reward_runtime() -> dict[str, Any]:
    """Read the current four first-row candidates without loading game data."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        state_address = int(_lua_addresses(memory)["state"], 16)
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="bothdraw-optional-reward",
            state_address=state_address,
            global_name="BothdrawMgr",
            required_methods=_BOTHDRAW_METHODS,
            validate=_bothdraw_data_fields,
        )
        reader = LuaJitReader(memory)
        library_ids = _current_library_ids(reader, _bothdraw_data_fields(reader, root))
        export_root = resolve_fanxiu_export_root()
        catalog = load_fanxiu_item_catalog(
            export_root=export_root,
            rebuild_missing=False,
        )
        runtime_rows = _runtime_optional_reward_rows(
            reader,
            _bothdraw_data_fields(reader, root),
        )
        if runtime_rows is not None:
            reward_items = build_bothdraw_runtime_reward_items(
                library_ids,
                runtime_rows=runtime_rows,
                item_cards=catalog.get("cards") or [],
            )
            reward_mapping_source = "loaded_runtime_optional_library"
        else:
            reward_items = build_bothdraw_reward_items(
                library_ids,
                optional_rows=_optional_gift_rows(str(export_root)),
                item_cards=catalog.get("cards") or [],
            )
            if any(int(item.get("item_id") or 0) <= 0 for item in reward_items):
                raise FanxiuRuntimeMemoryError(
                    "OptionalGift 当期候选物品映射不完整"
                )
            reward_mapping_source = "versioned_optional_gift_config"
        try:
            selected_big_reward = _lottery_snapshot(
                reader,
                _bothdraw_data_fields(reader, root),
                reward_items=reward_items,
            )["selected_big_reward"]
        except FanxiuRuntimeMemoryError as exc:
            # Before the first confirmation there is intentionally no unique
            # selected grand reward.  Candidate discovery remains complete.
            # Any other ambiguity/corruption remains a hard failure so the
            # idempotency gate can never degrade into a blind UI click.
            if not str(exc).rstrip().endswith("：[]"):
                raise
            selected_big_reward = None
        return {
            "ok": True,
            "available": True,
            "complete": len(reward_items) == 4,
            "source": "runtime_memory+versioned_item_config",
            "library_ids": list(library_ids),
            "reward_items": reward_items,
            "selected_big_reward": selected_big_reward,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "root_cache_hit": cache_hit,
                "catalog_path": catalog.get("catalog_path") or "",
                "reward_mapping_source": reward_mapping_source,
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory+versioned_item_config",
            "reason": str(exc),
            "library_ids": [],
            "reward_items": [],
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
            },
        }


def read_bothdraw_task_runtime() -> dict[str, Any]:
    """Read current Penglai task definitions and authoritative claim states."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        state_address = int(_lua_addresses(memory)["state"], 16)
        bothdraw_root, bothdraw_cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="bothdraw-optional-reward",
            state_address=state_address,
            global_name="BothdrawMgr",
            required_methods=_BOTHDRAW_METHODS,
            validate=_bothdraw_data_fields,
            force_refresh=True,
        )
        reader = LuaJitReader(memory)
        info_items = _dictionary_items(
            reader,
            _bothdraw_data_fields(reader, bothdraw_root).get("_BothInfoMap"),
        )
        if len(info_items) != 1:
            raise FanxiuRuntimeMemoryError(
                f"BothdrawMgr 当前活动实例不唯一：{sorted(str(key) for key in info_items)}"
            )
        activity_id = int(next(iter(info_items)))

        quest_root, quest_cache_hit = resolve_manager_root(
            memory,
            manager_key="quest-activity-tasks",
            marker=b"LuaQuestMgr",
            required_methods=_QUEST_METHODS,
            validate=_quest_data_fields,
            force_refresh=True,
        )
        reader = LuaJitReader(memory)
        quest_data = _quest_data_fields(reader, quest_root)
        raw_configs = _dictionary_item(
            reader,
            quest_data.get("V_RevenueTaskDic"),
            activity_id,
        )
        task_configs = [_fields(reader, item) for item in _list_values(reader, raw_configs)]
        activity_tasks = _fields(
            reader,
            _dictionary_item(reader, quest_data.get("taskInfoMap"), _ACTIVITY_TASK_TYPE),
        )
        if not activity_tasks:
            raise FanxiuRuntimeMemoryError("QuestMgr 活动任务状态尚未加载")
        task_entries = [
            _fields(reader, item)
            for item in _list_values(reader, activity_tasks.get("taskEntryVOs"))
        ]
        finished_task_ids = [
            int(item)
            for item in _list_values(reader, activity_tasks.get("finishTasks"))
            if int(item or 0) > 0
        ]
        snapshot = build_bothdraw_task_snapshot(
            activity_id=activity_id,
            task_configs=task_configs,
            task_entries=task_entries,
            finished_task_ids=finished_task_ids,
        )
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **snapshot,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "bothdraw_root_cache_hit": bothdraw_cache_hit,
                "quest_root_cache_hit": quest_cache_hit,
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "reason": str(exc),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
            },
        }


def read_bothdraw_revenue_task_runtime(*, expected_activity_id: int) -> dict[str, Any]:
    """Read an activity's own RevenueTask UI models, without stale config joins."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        expected_activity_id = int(expected_activity_id)
        if expected_activity_id <= 0:
            raise ValueError("expected_activity_id 必须为正整数")
        memory = MumuProcessMemory.discover_cached()
        state_address = int(_lua_addresses(memory)["state"], 16)
        bothdraw_root, bothdraw_cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="bothdraw-revenue-task-identity",
            state_address=state_address,
            global_name="BothdrawMgr",
            required_methods=_BOTHDRAW_METHODS,
            validate=_bothdraw_data_fields,
            force_refresh=True,
        )
        reader = LuaJitReader(memory)
        info_items = _dictionary_items(
            reader,
            _bothdraw_data_fields(reader, bothdraw_root).get("_BothInfoMap"),
        )
        if len(info_items) != 1:
            raise FanxiuRuntimeMemoryError(
                f"BothdrawMgr 当前活动实例不唯一：{sorted(str(key) for key in info_items)}"
            )
        activity_id = int(next(iter(info_items)))
        if activity_id != expected_activity_id:
            raise FanxiuRuntimeMemoryError(
                f"当前抽奖活动身份不匹配：expected={expected_activity_id}, actual={activity_id}"
            )

        quest_root, quest_cache_hit = resolve_manager_root(
            memory,
            manager_key="quest-revenue-task-model",
            marker=b"LuaQuestMgr",
            required_methods=_QUEST_METHODS,
            validate=_quest_data_fields,
            force_refresh=True,
        )
        reader = LuaJitReader(memory)
        quest_data = _quest_data_fields(reader, quest_root)
        activity_model = _fields(
            reader,
            _dictionary_item(reader, quest_data.get("V_AllTaskInfoDic"), activity_id),
        )
        storage = activity_model.get("_dt_")
        if not isinstance(storage, LuaRef) or storage.kind != "table":
            raise FanxiuRuntimeMemoryError(
                f"活动 {activity_id} 的 RevenueTask UI 模型尚未加载"
            )
        fields = reader.table(storage.address).get("fields") or {}
        all_rows = _dictionary_items(reader, fields.get("vodic"))
        if not all_rows:
            raise FanxiuRuntimeMemoryError(
                f"活动 {activity_id} 的 RevenueTask 行尚未加载"
            )
        groups: dict[int, list[dict[str, Any]]] = {}
        grouped_ids: set[int] = set()
        for raw_group_id, group_value in fields.items():
            if not isinstance(raw_group_id, (int, float)) or raw_group_id < 0:
                continue
            if int(raw_group_id) != raw_group_id:
                continue
            group_id = int(raw_group_id)
            rows: list[dict[str, Any]] = []
            for value in _list_values(reader, group_value):
                row = _fields(reader, value)
                task_id = int(row.get("id") or 0)
                if task_id <= 0:
                    raise FanxiuRuntimeMemoryError(
                        f"活动 {activity_id} 的 RevenueTask 分组 {group_id} 含无效任务 ID"
                    )
                if task_id in grouped_ids:
                    raise FanxiuRuntimeMemoryError(
                        f"活动 {activity_id} 的 RevenueTask 跨分组重复：{task_id}"
                    )
                grouped_ids.add(task_id)
                rows.append(
                    {
                        "id": task_id,
                        "isFinished": row.get("isFinished"),
                        "serverData": _fields(reader, row.get("serverData")),
                    }
                )
            if rows:
                groups[group_id] = rows
        all_ids = {int(key) for key in all_rows}
        if grouped_ids != all_ids:
            raise FanxiuRuntimeMemoryError(
                f"活动 {activity_id} 的 RevenueTask 分组与全量行不一致："
                f"missing={sorted(all_ids - grouped_ids)}, extra={sorted(grouped_ids - all_ids)}"
            )
        snapshot = build_bothdraw_revenue_task_snapshot(
            activity_id=activity_id,
            task_groups=groups,
        )
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory.activity_owned_revenue_task_ui",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **snapshot,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "bothdraw_root_cache_hit": bothdraw_cache_hit,
                "quest_root_cache_hit": quest_cache_hit,
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory.activity_owned_revenue_task_ui",
            "reason": str(exc),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
            },
        }


def _lottery_snapshot(
    reader: LuaJitReader,
    data: dict[Any, Any],
    *,
    reward_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build one current activity point from the already-loaded play VO."""

    info_items = _dictionary_items(reader, data.get("_BothInfoMap"))
    if len(info_items) != 1:
        raise FanxiuRuntimeMemoryError(
            f"BothdrawMgr 当前活动实例不唯一：{sorted(str(key) for key in info_items)}"
        )
    activity_id, info = next(iter(info_items.items()))
    fields = _fields(reader, info)
    optional_map = _dictionary_items(reader, fields.get("rewardOptionalMap"))
    big_count = _dictionary_items(reader, fields.get("bigCount"))
    resolved_items = {
        int(item.get("library_id") or 0): item
        for item in reward_items
        if int(item.get("library_id") or 0) > 0
    }
    selected: list[dict[str, Any]] = []
    for raw_big in _list_values(reader, fields.get("bigItems")):
        big_fields = _fields(reader, raw_big)
        big_id = int(big_fields.get("id") or 0)
        capacity = int(big_fields.get("times") or 0)
        library_id = int(optional_map.get(float(big_id)) or optional_map.get(big_id) or 0)
        if big_id <= 0 or library_id <= 0:
            continue
        count = int(big_count.get(float(big_id)) or big_count.get(big_id) or 0)
        if capacity <= 0 or count < 0 or count > capacity:
            raise FanxiuRuntimeMemoryError(
                "BothdrawMgr 已选大奖库存异常："
                f"big_id={big_id}, count={count}, capacity={capacity}"
            )
        item = resolved_items.get(library_id) or {}
        selected.append(
            {
                "big_id": big_id,
                "library_id": library_id,
                "item_id": int(item.get("item_id") or 0),
                "name": str(item.get("name") or library_id),
                "count": count,
                "capacity": capacity,
                "remaining": capacity - count,
            }
        )
    if len(selected) != 1:
        raise FanxiuRuntimeMemoryError(
            f"BothdrawMgr 当前已选大奖不唯一：{selected}"
        )
    return {
        "activity_id": int(activity_id),
        "x": int(fields.get("times") or 0),
        # hitBigTotal is the server's activity-lifetime total and therefore
        # remains monotonic even if a replenish clears bigCount.
        "y": int(fields.get("hitBigTotal") or 0),
        "selected_big_reward": selected[0],
        "selected_big_count": int(selected[0]["count"]),
        "selected_big_capacity": int(selected[0]["capacity"]),
        "selected_big_remaining": int(selected[0]["remaining"]),
        "hit_big": int(fields.get("hitBig") or 0),
        "hit_big_total": int(fields.get("hitBigTotal") or 0),
    }


def _read_bothdraw_lottery_snapshot(
    memory: MumuProcessMemory,
    force_refresh: bool,
) -> dict[str, Any]:
    state_address = int(_lua_addresses(memory)["state"], 16)
    root, cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key="bothdraw-optional-reward",
        state_address=state_address,
        global_name="BothdrawMgr",
        required_methods=_BOTHDRAW_METHODS,
        validate=_bothdraw_data_fields,
        force_refresh=force_refresh,
    )
    reader = LuaJitReader(memory)
    data = _bothdraw_data_fields(reader, root)
    reward_items, reward_mapping_source, catalog_path = _reward_items_for_data(
        reader, data
    )
    return {
        **_lottery_snapshot(reader, data, reward_items=reward_items),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_cache_hit": cache_hit,
            "logical_root": "BothdrawMgr",
            "root_rebound": force_refresh,
            "catalog_path": catalog_path,
            "reward_mapping_source": reward_mapping_source,
        },
    }


def _basic_lottery_snapshot(activity_id: int, fields: dict[Any, Any]) -> dict[str, Any]:
    """Project a Bothdraw point when the activity has no optional grand prize.

    A few temporary activities use the same manager and cumulative-reward
    protocol but do not expose ``optionalVOs`` / ``bigItems``.  Their point is
    still useful for ticket, milestone and empirical pool observations; it
    must simply not masquerade as a selected-grand-prize probability sample.
    """

    return {
        "activity_id": int(activity_id),
        "x": int(fields.get("times") or 0),
        "y": int(fields.get("hitBigTotal") or 0),
        # ``hitBig`` is a server-synchronized counter with activity-specific
        # reset semantics; ``hitBigTotal`` is the monotonic scatter ordinate.
        "hit_big": int(fields.get("hitBig") or 0),
        "hit_big_total": int(fields.get("hitBigTotal") or 0),
        "selected_big_reward": None,
        "probability_pool_kind": "ordinary_pool",
    }


def _ordinary_big_prize_items(
    reader: LuaJitReader, fields: dict[Any, Any]
) -> list[dict[str, Any]]:
    """Read the server-loaded big-prize configuration and its hit counters.

    ``bigItems`` is the activity's own configured big-prize list.  The server
    updates ``bigCount`` from ``SM_BothDraw.hitItemMap`` by matching the
    received item to this list, so this is Runtime business data rather than
    an OCR interpretation of the reward screen.
    """

    counts = _dictionary_items(reader, fields.get("bigCount"))
    prizes: list[dict[str, Any]] = []
    for raw in _list_values(reader, fields.get("bigItems")):
        row = _fields(reader, raw)
        prize_id = int(row.get("id") or 0)
        if prize_id <= 0 or row.get("isBig") is not True:
            raise FanxiuRuntimeMemoryError("BothdrawMgr 大奖配置行不完整")
        count = counts.get(prize_id)
        if count is None:
            count = counts.get(float(prize_id), 0)
        prizes.append(
            {
                "id": prize_id,
                "reward": str(row.get("reward") or ""),
                "sort": int(row.get("sort") or 0),
                "weight": int(row.get("weight") or 0),
                "hit_count": int(count or 0),
            }
        )
    if not prizes:
        raise FanxiuRuntimeMemoryError("BothdrawMgr 未加载当前活动大奖配置")
    return prizes


def derive_bothdraw_ordinary_draw_delta(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    """Derive an ordinary-pool result only from two Runtime snapshots.

    ``x`` is the actual server-accepted draw count, so a ten-draw control may
    legitimately produce ``draw_delta < 10``. ``y`` and per-big-prize counter
    deltas together define the empirical big-prize outcome.
    """

    if not before.get("complete") or not after.get("complete"):
        raise FanxiuRuntimeMemoryError("Bothdraw 抽奖前后 Runtime 快照不完整")
    before_activity = int(before.get("activity_id") or 0)
    after_activity = int(after.get("activity_id") or 0)
    if before_activity <= 0 or before_activity != after_activity:
        raise FanxiuRuntimeMemoryError("Bothdraw 抽奖前后活动身份不一致")
    draw_delta = int(after.get("x") or 0) - int(before.get("x") or 0)
    big_delta = int(after.get("y") or 0) - int(before.get("y") or 0)
    if draw_delta <= 0 or big_delta < 0 or big_delta > draw_delta:
        raise FanxiuRuntimeMemoryError("Bothdraw 抽奖前后累计计数不满足单调契约")
    before_items = {
        int(row.get("id") or 0): row
        for row in before.get("big_prize_items") or []
        if isinstance(row, dict) and int(row.get("id") or 0) > 0
    }
    after_items = {
        int(row.get("id") or 0): row
        for row in after.get("big_prize_items") or []
        if isinstance(row, dict) and int(row.get("id") or 0) > 0
    }
    if not after_items or set(before_items) != set(after_items):
        raise FanxiuRuntimeMemoryError("Bothdraw 抽奖前后大奖配置不一致")
    hit_items: list[dict[str, Any]] = []
    for prize_id, after_row in after_items.items():
        increment = int(after_row.get("hit_count") or 0) - int(before_items[prize_id].get("hit_count") or 0)
        if increment < 0:
            raise FanxiuRuntimeMemoryError("Bothdraw 大奖配置计数发生回退")
        if increment:
            hit_items.append({**after_row, "hit_increment": increment})
    if sum(int(item["hit_increment"]) for item in hit_items) != big_delta:
        raise FanxiuRuntimeMemoryError("Bothdraw 大奖配置差值与累计大奖差值不一致")
    return {"activity_id": after_activity, "draw_delta": draw_delta, "big_delta": big_delta, "hit_big_prize_items": hit_items}


def _reward_items_for_data(
    reader: LuaJitReader,
    data: dict[Any, Any],
) -> tuple[list[dict[str, Any]], str, str]:
    """Resolve the current optional library once for all Bothdraw snapshots."""

    library_ids = _current_library_ids(reader, data)
    export_root = resolve_fanxiu_export_root()
    catalog = load_fanxiu_item_catalog(export_root=export_root, rebuild_missing=False)
    runtime_rows = _runtime_optional_reward_rows(reader, data)
    if runtime_rows is not None:
        reward_items = build_bothdraw_runtime_reward_items(
            library_ids,
            runtime_rows=runtime_rows,
            item_cards=catalog.get("cards") or [],
        )
        reward_mapping_source = "loaded_runtime_optional_library"
    else:
        reward_items = build_bothdraw_reward_items(
            library_ids,
            optional_rows=_optional_gift_rows(str(export_root)),
            item_cards=catalog.get("cards") or [],
        )
        if any(int(item.get("item_id") or 0) <= 0 for item in reward_items):
            raise FanxiuRuntimeMemoryError("OptionalGift 当期抽奖物品映射不完整")
        reward_mapping_source = "versioned_optional_gift_config"
    return reward_items, reward_mapping_source, str(catalog.get("catalog_path") or "")


def read_bothdraw_lottery_runtime() -> dict[str, Any]:
    """Read a coherent draw snapshot through the logical ``BothdrawMgr`` root."""

    started_at = time.perf_counter()
    try:
        point = read_runtime_snapshot_with_rebind(
            _read_bothdraw_lottery_snapshot,
            force_rebind_first=True,
        )
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory+versioned_item_config",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **point,
            "elapsed_seconds": time.perf_counter() - started_at,
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory+versioned_item_config",
            "reason": str(exc),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {},
        }


def read_bothdraw_basic_runtime() -> dict[str, Any]:
    """Read a Bothdraw variant that has no optional-grand-prize rows.

    Some temporary activities share ``BothdrawMgr`` but only expose ordinary
    prize pools.  The legacy lottery reader rightly refuses those activities
    because it cannot create a selected-grand-prize probability sample.  This
    smaller projection deliberately keeps that invariant while still exposing
    the activity id, cumulative draw count and configured ticket cost.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        state_address = int(_lua_addresses(memory)["state"], 16)
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="bothdraw-basic",
            state_address=state_address,
            global_name="BothdrawMgr",
            required_methods=_BOTHDRAW_METHODS,
            validate=_bothdraw_data_fields,
            force_refresh=True,
        )
        reader = LuaJitReader(memory)
        data = _bothdraw_data_fields(reader, root)
        info_items = _dictionary_items(reader, data.get("_BothInfoMap"))
        if len(info_items) != 1:
            raise FanxiuRuntimeMemoryError(
                f"BothdrawMgr 当前活动实例不唯一：{sorted(str(key) for key in info_items)}"
            )
        activity_id, info = next(iter(info_items.items()))
        fields = _fields(reader, info)
        big_prize_items = _ordinary_big_prize_items(reader, fields)
        revenue_root, revenue_cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="revenue-bothdraw-basic",
            state_address=state_address,
            global_name="RevenueMgr",
            required_methods=_REVENUE_METHODS,
            validate=_revenue_data_fields,
            force_refresh=True,
        )
        reader = LuaJitReader(memory)
        revenue = _fields(
            reader,
            _dictionary_item(
                reader, _revenue_data_fields(reader, revenue_root).get("V_ActivityDic"), int(activity_id)
            ),
        )
        base = _fields(reader, revenue.get("revenueBaseVO"))
        cost_type = int(base.get("costType") or 0)
        cost_per_draw = int(base.get("costValue") or 0)
        if cost_type <= 0 or cost_per_draw <= 0:
            raise FanxiuRuntimeMemoryError("BothdrawMgr 活动消耗配置不完整")
        available_currency: int | None = None
        wallet_reason = ""
        try:
            wallet_root, wallet_cache_hit, _environment = resolve_lua_global_manager_root(
                memory,
                manager_key=f"wallet-currency-{cost_type}",
                state_address=state_address,
                global_name="WalletMgr",
                required_methods=WALLET_METHODS,
                validate=lambda current_reader, address: wallet_currency_data(current_reader, address, cost_type),
                force_refresh=True,
            )
            wallet = wallet_currency_data(LuaJitReader(memory), wallet_root, cost_type)
            available_currency = int(wallet["exchange_currency"])
        except Exception as exc:  # missing zero-balance wallets are not a draw permit
            wallet_reason = str(exc)
            wallet_cache_hit = False
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "activity_id": int(activity_id),
            "x": int(fields.get("times") or 0),
            "y": int(fields.get("hitBigTotal") or 0),
            "hit_big": int(fields.get("hitBig") or 0),
            "hit_big_total": int(fields.get("hitBigTotal") or 0),
            "big_prize_items": big_prize_items,
            "progress": int(fields.get("progress") or 0),
            "cost_type": cost_type,
            "cost_per_draw": cost_per_draw,
            "available_currency": available_currency,
            "available_draws": (available_currency // cost_per_draw if available_currency is not None else None),
            "wallet_complete": available_currency is not None,
            "wallet_reason": wallet_reason,
            "evidence": {"pid": memory.pid, "process_start_ticks": memory.process_start_ticks, "bothdraw_root_cache_hit": cache_hit, "revenue_root_cache_hit": revenue_cache_hit, "wallet_root_cache_hit": wallet_cache_hit},
            "elapsed_seconds": time.perf_counter() - started_at,
        }
    except Exception as exc:
        return {"ok": False, "available": False, "complete": False, "source": "runtime_memory", "reason": str(exc), "elapsed_seconds": time.perf_counter() - started_at, "evidence": {}}


def _read_bothdraw_cumulative_rewards_runtime_once(
    *,
    include_selected_big_reward: bool = True,
    visible_slot_count: int = _DEFAULT_CUMULATIVE_REWARD_VISIBLE_SLOT_COUNT,
    force_refresh_roots: bool = False,
) -> dict[str, Any]:
    """Read exact cumulative-reward eligibility from loaded game models.

    ``include_selected_big_reward=False`` supports ordinary-pool Bothdraw
    variants: cumulative tiers and wallet facts remain authoritative even
    though a selected optional reward does not exist.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        state_address = int(_lua_addresses(memory)["state"], 16)
        bothdraw_root, bothdraw_cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="bothdraw-optional-reward",
            state_address=state_address,
            global_name="BothdrawMgr",
            required_methods=_BOTHDRAW_METHODS,
            validate=_bothdraw_data_fields,
            # The active Bothdraw activity can be replaced while the process
            # survives.  A stale-but-structurally-valid manager root otherwise
            # decodes another activity's ladder, which downstream code can
            # only reject after losing the page-bound observation window.
            force_refresh=True,
        )
        reader = LuaJitReader(memory)
        bothdraw_data = _bothdraw_data_fields(reader, bothdraw_root)
        info_items = _dictionary_items(reader, bothdraw_data.get("_BothInfoMap"))
        if len(info_items) != 1:
            raise FanxiuRuntimeMemoryError(
                f"BothdrawMgr 当前活动实例不唯一：{sorted(str(key) for key in info_items)}"
            )
        activity_id, play = next(iter(info_items.items()))
        activity_id = int(activity_id)
        play_fields = _fields(reader, play)
        progress = int(play_fields.get("progress") or 0)
        if include_selected_big_reward:
            reward_items, reward_mapping_source, catalog_path = _reward_items_for_data(
                reader, bothdraw_data
            )
            lottery = _lottery_snapshot(reader, bothdraw_data, reward_items=reward_items)
            if int(lottery.get("activity_id") or 0) != activity_id:
                raise FanxiuRuntimeMemoryError("BothdrawMgr 抽奖与累计奖励活动实例不一致")
        else:
            lottery = _basic_lottery_snapshot(activity_id, play_fields)
            reward_mapping_source = "not_applicable_ordinary_pool"
            catalog_path = ""

        revenue_root, revenue_cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="revenue-cumulative-reward",
            state_address=state_address,
            global_name="RevenueMgr",
            required_methods=_REVENUE_METHODS,
            validate=_revenue_data_fields,
            force_refresh=bool(force_refresh_roots),
        )
        reader = LuaJitReader(memory)
        revenue_data = _revenue_data_fields(reader, revenue_root)
        revenue_vo = _dictionary_item(
            reader,
            revenue_data.get("V_ActivityDic"),
            activity_id,
        )
        revenue_fields = _fields(reader, revenue_vo)
        if not revenue_fields:
            raise FanxiuRuntimeMemoryError(
                f"RevenueMgr 未加载活动 {activity_id} 的累计奖励数据"
            )
        rate_fields = _fields(reader, revenue_fields.get("rateConfigs"))
        milestones = [
            _fields(reader, value)
            for value in _list_values(reader, rate_fields.get("itemList"))
        ]
        milestones = [row for row in milestones if int(row.get("id") or 0) > 0]
        revenue_play_fields = _fields(reader, revenue_fields.get("revenuePlayVO"))
        claimed_ids = [
            int(value)
            for value in _list_values(reader, revenue_play_fields.get("draws"))
        ]
        if not milestones:
            raise FanxiuRuntimeMemoryError("RevenueMgr 未提供累计奖励档位")
        base_fields = _fields(reader, revenue_fields.get("revenueBaseVO"))
        cost_type = int(base_fields.get("costType") or 0)
        cost_per_draw = int(base_fields.get("costValue") or 0)
        if cost_type <= 0 or cost_per_draw <= 0:
            raise FanxiuRuntimeMemoryError(
                f"RevenueMgr 鉴宝消耗配置异常：type={cost_type}, value={cost_per_draw}"
            )
        snapshot = build_bothdraw_cumulative_rewards(
            progress=progress,
            milestones=milestones,
            claimed_ids=claimed_ids,
            visible_slot_count=visible_slot_count,
        )
        # Cumulative eligibility is authoritative even before the wallet has
        # naturally synced this activity's ticket item.  Do not erase a
        # claimable free milestone merely because the later draw gate cannot
        # yet establish ticket balance; callers use ``wallet_complete`` to
        # distinguish those two permissions.
        available_currency: int | None = None
        wallet_complete = False
        wallet_reason = ""
        wallet_cache_hit = False
        try:
            wallet_root, wallet_cache_hit, _environment = resolve_lua_global_manager_root(
                memory,
                manager_key=f"wallet-currency-{cost_type}",
                state_address=state_address,
                global_name="WalletMgr",
                required_methods=WALLET_METHODS,
                validate=lambda current_reader, address: wallet_currency_data(
                    current_reader,
                    address,
                    cost_type,
                ),
                force_refresh=bool(force_refresh_roots),
            )
            reader = LuaJitReader(memory)
            wallet = wallet_currency_data(reader, wallet_root, cost_type)
            available_currency = int(wallet["exchange_currency"])
            wallet_complete = True
        except Exception as exc:
            wallet_reason = str(exc)
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **lottery,
            "cost_type": cost_type,
            "cost_per_draw": cost_per_draw,
            "available_currency": available_currency,
            "available_draws": (
                max(0, available_currency // cost_per_draw)
                if available_currency is not None
                else None
            ),
            "wallet_complete": wallet_complete,
            "wallet_reason": wallet_reason,
            **snapshot,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "bothdraw_root_cache_hit": bothdraw_cache_hit,
                "revenue_root_cache_hit": revenue_cache_hit,
                "wallet_root_cache_hit": wallet_cache_hit,
                "catalog_path": catalog_path,
                "reward_mapping_source": reward_mapping_source,
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "reason": str(exc),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
            },
        }


def read_bothdraw_cumulative_rewards_runtime(
    *,
    include_selected_big_reward: bool = True,
    visible_slot_count: int = _DEFAULT_CUMULATIVE_REWARD_VISIBLE_SLOT_COUNT,
) -> dict[str, Any]:
    """Read one coherent snapshot, cold-rebinding once after a stale Lua node.

    Bothdraw/Revenue child tables can be replaced when the activity switches
    between task, store and main pages.  Raw LuaRef addresses never survive a
    retry: the second pass creates new readers and force-resolves every logical
    manager root.  A repeated failure remains fail-closed.
    """

    snapshot = _read_bothdraw_cumulative_rewards_runtime_once(
        include_selected_big_reward=include_selected_big_reward,
        visible_slot_count=visible_slot_count,
    )
    if (
        not snapshot.get("complete")
        and "Lua table node 地址无效" in str(snapshot.get("reason") or "")
    ):
        return _read_bothdraw_cumulative_rewards_runtime_once(
            include_selected_big_reward=include_selected_big_reward,
            visible_slot_count=visible_slot_count,
            force_refresh_roots=True,
        )
    return snapshot


__all__ = [
    "build_bothdraw_task_snapshot",
    "build_bothdraw_revenue_task_snapshot",
    "build_bothdraw_cumulative_rewards",
    "build_bothdraw_reward_items",
    "build_bothdraw_runtime_reward_items",
    "derive_bothdraw_ordinary_draw_delta",
    "read_bothdraw_basic_runtime",
    "read_bothdraw_cumulative_rewards_runtime",
    "read_bothdraw_lottery_runtime",
    "read_bothdraw_optional_reward_runtime",
    "read_bothdraw_task_runtime",
    "read_bothdraw_revenue_task_runtime",
    "read_kunlun_first_row_runtime",
]
