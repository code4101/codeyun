from __future__ import annotations

import threading
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


_FINAL_MARKER = b"LuaFinalCampAnswerMgr"
_FINAL_METHODS = frozenset(
    {"LuaFinalCampAnswerMgr", "Inst_get", "ShowFinalChooseView"}
)
_DB_MARKER = b"GetConfigTableByIdWithLog"
_DB_METHODS = frozenset(
    {"DBMgr", "GetConfigTable", "GetConfigTableByIdWithLog", "Inst_get"}
)
_QUESTION_TABLE_NAME = "CampAnswer.CampAnswer"
_OPTION_TABLE_NAME = "CampAnswer.CampOptions"


def _object_fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    fields = dict(reader.fields(value))
    if not isinstance(value, LuaRef) or value.kind != "table":
        return fields
    table = reader.table(value.address)
    metatable_address = as_int(table.get("metatable"))
    if not metatable_address:
        return fields
    index_value = reader.table(metatable_address)["fields"].get("__index")
    return {**reader.fields(index_value), **fields}


def _integer(value: Any) -> int | None:
    result = as_int(value)
    if result is not None:
        return result
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed


def _array_values(reader: LuaJitReader, value: Any) -> list[Any]:
    """Read a generated-config Lua array without assuming its wrapper type."""

    if isinstance(value, LuaRef) and value.kind == "table":
        table = reader.table(value.address)
        array = [item for item in table.get("array", ()) if item is not None]
        if array:
            return array
        fields = table.get("fields", {})
        numeric = sorted(
            ((key, item) for key, item in fields.items() if _integer(key) is not None),
            key=lambda pair: int(str(pair[0])),
        )
        if numeric:
            return [item for _key, item in numeric]
    try:
        items, _declared_count = reader.list_items(value)
        return items
    except Exception:  # noqa: BLE001 - config arrays vary between game builds
        return []


def _final_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _FINAL_METHODS)
    instance = reader.fields(manager.get("inst"))
    model = reader.fields(instance.get("Model"))
    data = reader.fields(model.get("FinalCampAnswerData"))
    if not data:
        raise FanxiuRuntimeMemoryError("答题决赛 Runtime 模型尚未初始化")
    return data


def _config_table(
    reader: LuaJitReader,
    db_root: int,
    name: str,
) -> dict[Any, Any]:
    manager = manager_index_fields(reader, db_root, _DB_METHODS)
    instance = reader.fields(manager.get("inst"))
    configs = reader.dictionary_fields(instance.get("ConfigDic"))
    table = reader.fields(configs.get(name))
    if not table:
        raise FanxiuRuntimeMemoryError(f"答题决赛配置尚未加载：{name}")
    return table


def _snapshot(
    memory: MumuProcessMemory,
    final_root: int,
    db_root: int,
    *,
    final_cache_hit: bool,
    db_cache_hit: bool,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data = _final_data_fields(reader, final_root)
    quest = _object_fields(reader, data.get("_questInfo"))
    quest_id = _integer(quest.get("questId"))
    progress = _integer(quest.get("progress"))
    start_time_ms = reader.long(quest.get("startTime"))
    if quest_id is None or progress is None or start_time_ms is None:
        raise FanxiuRuntimeMemoryError("答题决赛当前题目字段不完整")

    questions = _config_table(reader, db_root, _QUESTION_TABLE_NAME)
    options_table = _config_table(reader, db_root, _OPTION_TABLE_NAME)
    question_cfg = _object_fields(reader, questions.get(quest_id))
    prompt = str(question_cfg.get("question") or "").strip()
    correct_option_id = _integer(question_cfg.get("answer"))
    option_ids = [
        option_id
        for item in _array_values(reader, question_cfg.get("options"))
        if (option_id := _integer(item)) is not None
    ]
    option_records: list[dict[str, Any]] = []
    for position, option_id in enumerate(option_ids):
        option_cfg = _object_fields(reader, options_table.get(option_id))
        option_records.append(
            {
                "id": option_id,
                "text": str(option_cfg.get("options") or "").strip(),
                "config_position": position,
            }
        )
    correct = next(
        (item for item in option_records if item["id"] == correct_option_id),
        None,
    )
    if (
        not prompt
        or len(option_records) != 4
        or any(not item["text"] for item in option_records)
        or correct is None
    ):
        raise FanxiuRuntimeMemoryError(
            f"答题决赛配置不完整：questId={quest_id}, answer={correct_option_id}"
        )

    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "runtime_memory",
        "protocol": "FinalCampAnswerMgr.Model.FinalCampAnswerData._questInfo",
        "quest_id": quest_id,
        "progress": progress,
        "start_time_ms": start_time_ms,
        "question": prompt,
        "options": option_records,
        "correct_option_id": correct_option_id,
        "correct_answer": correct["text"],
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "captured_at_epoch": time.time(),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "final_root_address": f"0x{final_root:x}",
            "db_root_address": f"0x{db_root:x}",
            "final_root_cache_hit": final_cache_hit,
            "db_root_cache_hit": db_cache_hit,
        },
    }


def read_final_camp_answer_snapshot() -> dict[str, Any]:
    """Synchronously read the final-round question and native correct answer."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover()
        final_root, final_cache_hit = resolve_manager_root(
            memory,
            manager_key="final-camp-answer",
            marker=_FINAL_MARKER,
            required_methods=_FINAL_METHODS,
            validate=lambda reader, root: _final_data_fields(reader, root),
        )
        db_root, db_cache_hit = resolve_manager_root(
            memory,
            manager_key="db-manager",
            marker=_DB_MARKER,
            required_methods=_DB_METHODS,
            validate=lambda reader, root: _config_table(
                reader, root, _QUESTION_TABLE_NAME
            ),
        )
        result = _snapshot(
            memory,
            final_root,
            db_root,
            final_cache_hit=final_cache_hit,
            db_cache_hit=db_cache_hit,
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
            "source": "runtime_memory",
            "reason": reason,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
            },
        }


_CACHE_LOCK = threading.Lock()
_CACHE_RESULT: dict[str, Any] | None = None
_CACHE_REFRESHING = False
_CACHE_LAST_STARTED = 0.0


def _refresh_cache() -> None:
    global _CACHE_RESULT, _CACHE_REFRESHING
    result = read_final_camp_answer_snapshot()
    with _CACHE_LOCK:
        _CACHE_RESULT = result
        _CACHE_REFRESHING = False


def get_final_camp_answer_snapshot(
    *,
    max_age_seconds: float = 1.0,
    min_refresh_interval_seconds: float = 0.1,
) -> dict[str, Any]:
    """Return immediately; refresh the read-only native snapshot in background."""

    global _CACHE_REFRESHING, _CACHE_LAST_STARTED
    now_monotonic = time.monotonic()
    start_refresh = False
    with _CACHE_LOCK:
        cached = dict(_CACHE_RESULT) if _CACHE_RESULT is not None else None
        if (
            not _CACHE_REFRESHING
            and now_monotonic - _CACHE_LAST_STARTED
            >= max(0.05, float(min_refresh_interval_seconds))
        ):
            _CACHE_REFRESHING = True
            _CACHE_LAST_STARTED = now_monotonic
            start_refresh = True
    if start_refresh:
        threading.Thread(
            target=_refresh_cache,
            name="fanxiu-final-camp-answer-snapshot",
            daemon=True,
        ).start()

    if cached is None:
        return {
            "ok": False,
            "available": False,
            "source": "runtime_memory",
            "reason": "答题决赛 Runtime 快照正在后台预热",
            "refreshing": True,
            "cache_age_seconds": None,
        }
    captured = cached.get("captured_at_epoch")
    age = (
        max(0.0, time.time() - float(captured))
        if isinstance(captured, (int, float))
        else None
    )
    fresh = age is not None and age <= max(0.0, max_age_seconds)
    cached["cache_age_seconds"] = age
    cached["fresh"] = fresh
    cached["refreshing"] = _CACHE_REFRESHING
    if not fresh:
        cached["ok"] = False
        cached["available"] = False
        cached["reason"] = "答题决赛 Runtime 快照已过期，正在后台刷新"
    return cached


__all__ = [
    "get_final_camp_answer_snapshot",
    "read_final_camp_answer_snapshot",
]
