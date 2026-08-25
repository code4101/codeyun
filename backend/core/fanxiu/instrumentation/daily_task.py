from __future__ import annotations

"""Strict read-only facts for already-loaded daily QuestMgr tasks."""

import time
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    manager_index_fields,
    resolve_lua_global_manager_root,
    resolve_manager_root,
)


_QUEST_MARKER = b"LuaQuestMgr"
_QUEST_METHODS = frozenset({"LuaQuestMgr", "Inst_get", "GetTaskState"})
_DAILY_TASK_TYPE = 1
_DONE_STATUSES = frozenset({4, 5})


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value) if isinstance(value, LuaRef) and value.kind == "table" else {}


def _list_values(reader: LuaJitReader, value: Any) -> list[Any]:
    wrapper = _fields(reader, value)
    data = wrapper.get("_dt_")
    if not isinstance(data, LuaRef) or data.kind != "table":
        return []
    values = [item for item in reader.table(data.address).get("array", []) if item is not None]
    return values[: int(wrapper.get("count") or len(values))]


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


def _dictionary_item(reader: LuaJitReader, value: Any, key: int) -> Any:
    items = _dictionary_items(reader, value)
    return items.get(key) or items.get(float(key))


def _quest_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _QUEST_METHODS)
    instance = _fields(reader, manager.get("inst"))
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("QuestData"))
    if "taskInfoMap" not in data:
        raise FanxiuRuntimeMemoryError("QuestMgr 日常任务数据尚未加载", code="data_not_loaded")
    return data


def _resolve_quest_root(memory: MumuProcessMemory) -> tuple[int, bool, str]:
    try:
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="quest-daily-tasks",
            state_address=int(_lua_addresses(memory)["state"], 16),
            global_name="QuestMgr",
            required_methods=_QUEST_METHODS,
            validate=lambda reader, address: _quest_data_fields(reader, address),
        )
        return root, cache_hit, "lua_global"
    except FanxiuRuntimeMemoryError as exc:
        if exc.code == "data_not_loaded":
            raise
    root, cache_hit = resolve_manager_root(
        memory,
        manager_key="quest-daily-tasks",
        marker=_QUEST_MARKER,
        required_methods=_QUEST_METHODS,
        validate=lambda reader, address: _quest_data_fields(reader, address),
    )
    return root, cache_hit, "constructor_marker"


def build_daily_task_snapshot(
    *, task_id: int, task_entries: list[dict[str, Any]], finished_task_ids: list[int]
) -> dict[str, Any]:
    entries = {
        int(item.get("taskId") or item.get("task_id") or 0): dict(item)
        for item in task_entries
        if int(item.get("taskId") or item.get("task_id") or 0) > 0
    }
    entry = entries.get(int(task_id)) or {}
    finished = {int(item) for item in finished_task_ids if int(item) > 0}
    status = int(entry.get("status") or 0) if entry else None
    turn = int(entry.get("turn") or 0) if entry else None
    target_turn = int(entry.get("targetTurn") or entry.get("target_turn") or 0) if entry else None
    done = int(task_id) in finished or bool(
        status in _DONE_STATUSES and target_turn and turn is not None and turn >= target_turn
    )
    return {
        "task_id": int(task_id),
        "present": bool(entry),
        "status": status,
        "turn": turn,
        "target_turn": target_turn,
        "done": done,
        "daily_task_count": len(entries),
        "finished_task_count": len(finished),
    }


def read_daily_task_snapshot(task_id: int) -> dict[str, Any]:
    """Read one daily task without navigating or initializing game state."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        root, cache_hit, resolver = _resolve_quest_root(memory)
        reader = LuaJitReader(memory)
        data = _quest_data_fields(reader, root)
        daily = _fields(reader, _dictionary_item(reader, data.get("taskInfoMap"), _DAILY_TASK_TYPE))
        if not daily:
            raise FanxiuRuntimeMemoryError("QuestMgr 日常任务状态尚未加载", code="data_not_loaded")
        entries = [_fields(reader, item) for item in _list_values(reader, daily.get("taskEntryVOs"))]
        finished = [int(item) for item in _list_values(reader, daily.get("finishTasks")) if int(item or 0) > 0]
        result = build_daily_task_snapshot(
            task_id=int(task_id), task_entries=entries, finished_task_ids=finished
        )
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory",
            "protocol": "QuestMgr.Model.QuestData.taskInfoMap[1]",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **result,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "root_address": f"0x{root:x}",
                "root_cache_hit": cache_hit,
                "resolver": resolver,
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "task_id": int(task_id),
            "reason": str(exc),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
            },
        }
