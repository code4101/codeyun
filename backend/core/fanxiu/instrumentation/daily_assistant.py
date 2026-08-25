from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_manager_root,
)


_ACTIVETASK_MARKER = b"LuaActivetaskMgr"
_ACTIVETASK_METHODS = frozenset(
    {
        "LuaActivetaskMgr",
        "Inst_get",
    }
)


def _daily_helper_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager = manager_index_fields(
        reader,
        root_address,
        _ACTIVETASK_METHODS,
    )
    instance = reader.fields(manager.get("inst"))
    model = reader.fields(instance.get("Model"))
    data = reader.fields(model.get("NewDailyHelperData"))
    required = {
        "subModuleIdDic",
        "selectNewDailyHelperModuleIds",
        "showNewDailyHelperModuleIds",
        "moduleExecuteCountDic",
        "resSubModuleList",
    }
    if not required.issubset(data):
        raise FanxiuRuntimeMemoryError(
            "ActivetaskMgr NewDailyHelperData 尚未初始化"
        )
    return data


def _config_item(
    reader: LuaJitReader,
    value: Any,
) -> dict[str, Any] | None:
    if not isinstance(value, LuaRef) or value.kind != "table":
        return None
    array = reader.table(value.address)["array"]
    if len(array) < 5:
        return None
    submodule_id = as_int(array[1])
    module_id = as_int(array[2])
    if submodule_id is None or module_id is None:
        return None
    return {
        "submodule_id": submodule_id,
        "module_id": module_id,
        "name": str(array[3] or ""),
        "function_type": str(array[4] or ""),
    }


def _number_list(
    reader: LuaJitReader,
    value: Any,
) -> tuple[list[int], int | None]:
    values, declared_count = reader.list_items(value)
    return (
        [
            number
            for item in values
            if (number := as_int(item)) is not None
        ],
        declared_count,
    )


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data = _daily_helper_fields(reader, root_address)

    config_by_id: dict[int, dict[str, Any]] = {}
    group_count = 0
    for _module_id, value in reader.fields(
        data.get("subModuleIdDic")
    ).items():
        group_count += 1
        items, _declared_count = reader.list_items(value)
        for item in items:
            config = _config_item(reader, item)
            if config is not None:
                config_by_id[config["submodule_id"]] = config

    selected_module_ids, selected_count = _number_list(
        reader,
        data.get("selectNewDailyHelperModuleIds"),
    )
    shown_module_ids, shown_count = _number_list(
        reader,
        data.get("showNewDailyHelperModuleIds"),
    )
    unlocked_submodule_ids, unlocked_count = _number_list(
        reader,
        data.get("unlockNewDailyHelperSubModuleIds"),
    )
    result_values, result_count = reader.list_items(
        data.get("resSubModuleList")
    )
    result_submodules = [
        config
        for value in result_values
        if (config := _config_item(reader, value)) is not None
    ]
    execute_counts = {
        str(submodule_id): count
        for key, value in reader.fields(
            data.get("moduleExecuteCountDic")
        ).items()
        if (submodule_id := as_int(key)) is not None
        and (count := as_int(value)) is not None
    }
    for item in result_submodules:
        item["execute_count"] = execute_counts.get(
            str(item["submodule_id"])
        )

    complete = (
        group_count > 0
        and bool(config_by_id)
        and selected_count is not None
        and selected_count == len(selected_module_ids)
        and shown_count is not None
        and shown_count == len(shown_module_ids)
        and result_count is not None
        and result_count == len(result_submodules)
    )
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": "ActivetaskMgr.Model.NewDailyHelperData",
        "server_helper_id": str(
            data.get("_serverNewDailyHelperId") or ""
        ),
        "module_group_count": group_count,
        "submodule_count": len(config_by_id),
        "selected_module_ids": selected_module_ids,
        "selected_module_count": selected_count,
        "shown_module_ids": shown_module_ids,
        "shown_module_count": shown_count,
        "unlocked_submodule_ids": unlocked_submodule_ids,
        "unlocked_submodule_count": unlocked_count,
        "result_submodules": result_submodules,
        "result_submodule_count": result_count,
        "execute_counts": execute_counts,
        "result_fingerprint": [
            [
                item["submodule_id"],
                item.get("execute_count"),
            ]
            for item in result_submodules
        ],
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
        },
    }


def read_daily_assistant_snapshot() -> dict[str, Any]:
    """Read the current one-key assistant model without GUI or packets."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        root, root_cache_hit = resolve_manager_root(
            memory,
            manager_key="daily_assistant",
            marker=_ACTIVETASK_MARKER,
            required_methods=_ACTIVETASK_METHODS,
            validate=lambda reader, address: _daily_helper_fields(
                reader,
                address,
            ),
        )
        result = _snapshot(
            memory,
            root,
            root_cache_hit=root_cache_hit,
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
        return result
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
            "reason": reason,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks
                    if memory is not None
                    else None
                ),
            },
        }
