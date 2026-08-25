from __future__ import annotations

"""Read already-loaded pet aptitude state from the live LuaJIT model.

This module is deliberately read-only.  It only walks ``PetMgr`` tables that
already exist in process memory; it never calls ``Inst_get``, initializes a
model, executes Lua, or sends a pet-feeding command.
"""

from typing import Any

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
)


PET_APTITUDE_NAMES = {
    1: "攻击资质",
    2: "气血资质",
    3: "灵力资质",
    4: "御兽资质",
    5: "魔兽资质",
}
_PET_METHODS = frozenset({"Inst_get"})
_QUEST_METHODS = frozenset({"LuaQuestMgr", "Inst_get", "GetTaskState"})
_ACTIVITY_TASK_TYPE = 3
LINGCHONG_JINGWU_PARENT_ACTIVITY_ID = 8042901
LINGCHONG_JINGWU_TASK_IDS = tuple(range(804290154, 804290168))


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value)


def _pet_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _PET_METHODS)
    instance = _fields(reader, manager.get("inst"))
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("PetData"))
    if "_PetInfoVo" not in data or "_PetGiftLimitMap" not in data:
        raise FanxiuRuntimeMemoryError("PetMgr 灵兽资质数据尚未完整加载")
    return data


def _pet_info_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    data = _pet_data_fields(reader, root_address)
    info = _fields(reader, data.get("_PetInfoVo"))
    if "petInfoVOList" not in info or "swallowInfoVOS" not in info:
        raise FanxiuRuntimeMemoryError("PetMgr 灵兽信息尚未完整加载")
    return info


def _decode_gift_limits(
    reader: LuaJitReader,
    data: dict[Any, Any],
) -> dict[int, dict[int, int]]:
    result: dict[int, dict[int, int]] = {}
    for raw_pet_id, raw_limits in reader.dictionary_fields(
        data.get("_PetGiftLimitMap")
    ).items():
        pet_id = as_int(raw_pet_id)
        if pet_id is None or pet_id <= 0:
            continue
        limits: dict[int, int] = {}
        for raw_type, raw_limit in reader.dictionary_fields(raw_limits).items():
            aptitude_type = as_int(raw_type)
            limit = reader.long(raw_limit)
            if aptitude_type in PET_APTITUDE_NAMES and limit is not None and limit >= 0:
                limits[int(aptitude_type)] = int(limit)
        if limits:
            result[int(pet_id)] = limits
    return result


def _decode_aptitudes(
    reader: LuaJitReader,
    value: Any,
    *,
    require_complete: bool = True,
) -> dict[int, int]:
    result: dict[int, int] = {}
    for raw_type, raw_value in reader.dictionary_fields(value).items():
        aptitude_type = as_int(raw_type)
        aptitude_value = reader.long(raw_value)
        if aptitude_type not in PET_APTITUDE_NAMES or aptitude_value is None:
            continue
        result[int(aptitude_type)] = int(aptitude_value)
    if require_complete and set(result) != set(PET_APTITUDE_NAMES):
        raise FanxiuRuntimeMemoryError(
            f"灵兽五项资质不完整：types={sorted(result)}"
        )
    return result


def _decode_pet_rows(
    reader: LuaJitReader,
    info: dict[Any, Any],
) -> tuple[list[dict[str, Any]], int | None]:
    raw_rows, declared_count = reader.list_items(info.get("petInfoVOList"))
    if declared_count is not None and len(raw_rows) != declared_count:
        raise FanxiuRuntimeMemoryError(
            f"灵兽列表不完整：count={declared_count}, rows={len(raw_rows)}"
        )
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw_row in raw_rows:
        row = _fields(reader, raw_row)
        pet_id = as_int(row.get("petId"))
        level = as_int(row.get("level"))
        pin = as_int(row.get("pin"))
        if pet_id is None or pet_id <= 0 or level is None or pin is None:
            raise FanxiuRuntimeMemoryError("灵兽行缺少 petId/level/pin")
        if pet_id in seen:
            raise FanxiuRuntimeMemoryError(f"灵兽 petId 重复：{pet_id}")
        seen.add(pet_id)
        # Untrained pets can legitimately have an empty or partial giftMap.
        # Completeness is enforced below for the explicitly requested target.
        aptitudes = _decode_aptitudes(
            reader, row.get("giftMap"), require_complete=False
        )
        rows.append(
            {
                "pet_id": int(pet_id),
                "level": int(level),
                "pin": int(pin),
                "aptitudes": aptitudes,
                "aptitude_total": sum(aptitudes.values()),
            }
        )
    if not rows:
        raise FanxiuRuntimeMemoryError("PetMgr 灵兽列表为空")
    return rows, declared_count


def _decode_pending_swallow_rows(
    reader: LuaJitReader,
    info: dict[Any, Any],
) -> tuple[list[dict[str, Any]], int | None]:
    raw_rows, declared_count = reader.list_items(info.get("swallowInfoVOS"))
    rows: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        fields = _fields(reader, raw_row)
        rows.append(
            {
                str(key): (
                    int(long_value)
                    if (long_value := reader.long(value)) is not None
                    else value
                )
                for key, value in fields.items()
                if isinstance(key, str)
            }
        )
    return rows, declared_count


def _quest_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _QUEST_METHODS)
    instance = _fields(reader, manager.get("inst"))
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("QuestData"))
    if "taskInfoMap" not in data:
        raise FanxiuRuntimeMemoryError("QuestMgr 活动任务状态尚未加载")
    return data


def _dictionary_item(reader: LuaJitReader, value: Any, key: int) -> Any:
    items = reader.dictionary_fields(value)
    return items.get(key) or items.get(float(key))


def _decode_pet_talent_tasks(
    reader: LuaJitReader,
    data: dict[Any, Any],
    *,
    expected_task_ids: tuple[int, ...] = LINGCHONG_JINGWU_TASK_IDS,
) -> dict[str, Any]:
    activity = _fields(
        reader,
        _dictionary_item(reader, data.get("taskInfoMap"), _ACTIVITY_TASK_TYPE),
    )
    if not activity:
        raise FanxiuRuntimeMemoryError("QuestMgr 活动任务容器尚未加载")
    expected = set(expected_task_ids)
    rows: dict[int, dict[str, Any]] = {}
    raw_entries, declared_entry_count = reader.list_items(
        activity.get("taskEntryVOs")
    )
    for raw_entry in raw_entries:
        entry = _fields(reader, raw_entry)
        task_id = as_int(entry.get("taskId"))
        if task_id not in expected:
            continue
        if task_id in rows:
            raise FanxiuRuntimeMemoryError(f"QuestMgr 活动任务 ID 重复：{task_id}")
        raw_progress, declared_progress_count = reader.list_items(
            entry.get("progressList")
        )
        if declared_progress_count != 1 or len(raw_progress) != 1:
            raise FanxiuRuntimeMemoryError(
                f"QuestMgr PetTalent 进度行不唯一：task_id={task_id}"
            )
        progress = _fields(reader, raw_progress[0])
        current = as_int(progress.get("progress"))
        target = as_int(progress.get("target"))
        if current is None or current < 0 or target is None or target <= 0:
            raise FanxiuRuntimeMemoryError(
                f"QuestMgr PetTalent 进度无效：task_id={task_id}"
            )
        rows[int(task_id)] = {
            "task_id": int(task_id),
            "status": int(as_int(entry.get("status")) or 0),
            "turn": int(as_int(entry.get("turn")) or 0),
            "progress": int(current),
            "target": int(target),
            "finished": bool(progress.get("finish")) or current >= target,
        }
    missing = sorted(expected - set(rows))
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"QuestMgr 本期 PetTalent 任务不完整：missing={missing}"
        )
    raw_finished, declared_finished_count = reader.list_items(
        activity.get("finishTasks")
    )
    finished_ids = {
        int(value)
        for raw_value in raw_finished
        if (value := as_int(raw_value)) is not None and value > 0
    }
    for task_id in finished_ids & expected:
        rows[task_id]["finished"] = True
        rows[task_id]["status"] = 5
    return {
        "tasks": [rows[task_id] for task_id in sorted(rows)],
        "declared_activity_entry_count": declared_entry_count,
        "declared_finished_task_count": declared_finished_count,
        "matched_task_count": len(rows),
    }


def read_pet_aptitude_runtime(*, expected_pet_id: int | None = None) -> dict[str, Any]:
    """Return the authoritative loaded pet list and optional unique target."""

    memory = MumuProcessMemory.discover_cached()
    state_address = int(_lua_addresses(memory)["state"], 16)
    root, cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key="pet-aptitude",
        state_address=state_address,
        global_name="PetMgr",
        required_methods=_PET_METHODS,
        validate=lambda reader, address: _decode_pet_rows(
            reader, _pet_info_fields(reader, address)
        ),
    )
    reader = LuaJitReader(memory)
    data = _pet_data_fields(reader, root)
    info = _fields(reader, data.get("_PetInfoVo"))
    pets, declared_pet_count = _decode_pet_rows(reader, info)
    gift_limits = _decode_gift_limits(reader, data)
    pending, declared_pending_count = _decode_pending_swallow_rows(reader, info)

    target = None
    if expected_pet_id is not None:
        matches = [row for row in pets if row["pet_id"] == int(expected_pet_id)]
        if len(matches) != 1:
            raise FanxiuRuntimeMemoryError(
                f"目标灵兽身份不唯一：pet_id={expected_pet_id}, matches={len(matches)}"
            )
        target = matches[0]
        if set(target["aptitudes"]) != set(PET_APTITUDE_NAMES):
            raise FanxiuRuntimeMemoryError(
                f"目标灵兽五项资质不完整：pet_id={expected_pet_id}, "
                f"types={sorted(target['aptitudes'])}"
            )
        target_limits = gift_limits.get(int(expected_pet_id), {})
        if set(target_limits) != set(PET_APTITUDE_NAMES):
            raise FanxiuRuntimeMemoryError(
                f"目标灵兽五项资质上限不完整：pet_id={expected_pet_id}, "
                f"types={sorted(target_limits)}"
            )
        target["gift_limits"] = target_limits
        target["gift_remaining"] = {
            aptitude_type: max(
                0,
                target_limits[aptitude_type] - target["aptitudes"][aptitude_type],
            )
            for aptitude_type in PET_APTITUDE_NAMES
        }

    return {
        "ok": True,
        "available": True,
        "read_only": True,
        "pid": memory.pid,
        "process_start_ticks": memory.process_start_ticks,
        "pet_root": f"0x{root:x}",
        "pet_root_cache_hit": cache_hit,
        "declared_pet_count": declared_pet_count,
        "pets": pets,
        "target": target,
        "pending_swallow_count": declared_pending_count,
        "pending_swallow_rows": pending,
    }


def read_pet_talent_quest_runtime() -> dict[str, Any]:
    """Read the current 14-row PetTalent ladder directly from QuestMgr."""

    memory = MumuProcessMemory.discover_cached()
    state_address = int(_lua_addresses(memory)["state"], 16)
    root, cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key="pet-talent-quest",
        state_address=state_address,
        global_name="QuestMgr",
        required_methods=_QUEST_METHODS,
        validate=lambda reader, address: _decode_pet_talent_tasks(
            reader, _quest_data_fields(reader, address)
        ),
    )
    reader = LuaJitReader(memory)
    decoded = _decode_pet_talent_tasks(reader, _quest_data_fields(reader, root))
    target_task = next(
        row for row in decoded["tasks"] if row["task_id"] == 804290164
    )
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "read_only": True,
        "parent_activity_id": LINGCHONG_JINGWU_PARENT_ACTIVITY_ID,
        "target_task": target_task,
        **decoded,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "quest_root": f"0x{root:x}",
            "quest_root_cache_hit": cache_hit,
            "protocol": "QuestMgr.Model.QuestData.taskInfoMap[3]",
            "expected_task_ids": list(LINGCHONG_JINGWU_TASK_IDS),
        },
    }
