from __future__ import annotations

import math
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
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import acquire_lua_global_runtime_context
from backend.core.fanxiu.instrumentation.resource_auto_use import read_runtime_config_rows
from backend.core.fanxiu.instrumentation.item_config import read_loaded_item_text


_LINGQUAN_METHODS = frozenset(
    {
        "LuaAlliancespringquestionMgr",
        "ReqQuestionInfo",
        "GetQuestionActState",
        "Inst_get",
    }
)
_DB_METHODS = frozenset(
    {
        "DBMgr",
        "GetConfigTable",
        "GetConfigTableByIdWithLog",
        "Inst_get",
    }
)
_QUESTION_BANK_NAME = "Alliance.QuestionBank"


def _object_fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    """Read an object table plus a table-backed __index, when present."""

    fields = dict(reader.fields(value))
    if not isinstance(value, LuaRef) or value.kind != "table":
        return fields
    table = reader.table(value.address)
    metatable_address = as_int(table.get("metatable"))
    if not metatable_address:
        return fields
    index_value = reader.table(metatable_address)["fields"].get("__index")
    inherited = reader.fields(index_value)
    return {**inherited, **fields}


def _lingquan_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager_fields = manager_index_fields(
        reader,
        root_address,
        _LINGQUAN_METHODS,
    )
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    data_fields = reader.fields(model_fields.get("data"))
    if not data_fields:
        raise FanxiuRuntimeMemoryError("灵泉问答 Runtime 模型尚未初始化")
    return data_fields


def _question_bank(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager_fields = manager_index_fields(reader, root_address, _DB_METHODS)
    instance_fields = reader.fields(manager_fields.get("inst"))
    config_dictionary = reader.dictionary_fields(instance_fields.get("ConfigDic"))
    bank = reader.fields(config_dictionary.get(_QUESTION_BANK_NAME))
    if not bank:
        raise FanxiuRuntimeMemoryError("灵泉题库尚未加载到游戏 Runtime")
    return bank


def _phase_and_remaining(
    *,
    now_ms: int,
    start_time_ms: int,
    prepare_ms: int,
    question_ms: int,
    answer_ms: int,
) -> tuple[str, int]:
    elapsed = max(0, now_ms - start_time_ms)
    prepare_end = prepare_ms
    question_end = prepare_end + question_ms
    answer_end = question_end + answer_ms
    if elapsed <= prepare_end:
        return "prepare", max(0, prepare_end - elapsed)
    if elapsed <= question_end:
        return "question", max(0, question_end - elapsed)
    if elapsed <= answer_end:
        return "answer", max(0, answer_end - elapsed)
    return "closed", 0


def _snapshot(
    memory: MumuProcessMemory,
    lingquan_root: int,
    db_root: int,
    *,
    lingquan_cache_hit: bool,
    db_cache_hit: bool,
    environment_address: int,
    state_address: int,
    now_ms: int | None = None,
    reader: LuaJitReader | None = None,
) -> dict[str, Any]:
    reader = reader if reader is not None else LuaJitReader(memory)
    data = _lingquan_data_fields(reader, lingquan_root)
    question_info = _object_fields(reader, data.get("questionInfo"))
    base_info = _object_fields(reader, data.get("baseInfo"))
    question_id = as_int(question_info.get("questionId"))
    progress = as_int(question_info.get("progress"))
    start_time_ms = reader.long(question_info.get("startTime"))
    prepare_ms = as_int(base_info.get("prepareTime"))
    question_ms = as_int(base_info.get("questionShowTime"))
    answer_ms = as_int(base_info.get("answerShowTime"))
    question_total = as_int(base_info.get("questionNum"))
    if None in {
        question_id,
        progress,
        start_time_ms,
        prepare_ms,
        question_ms,
        answer_ms,
    }:
        raise FanxiuRuntimeMemoryError("灵泉问答 Runtime 字段不完整")

    bank = _question_bank(reader, db_root)
    raw_question = bank.get(question_id)
    if raw_question is None:
        raise FanxiuRuntimeMemoryError(
            f"灵泉题库中没有当前题目：questionId={question_id}"
        )
    question_fields = read_runtime_config_rows(
        reader, [raw_question], environment_address=environment_address,
        state_address=state_address, group_name="Alliance", table_name="QuestionBank",
        fields=("id", "content", "showAnswer"),
    )[0]
    if as_int(question_fields.get("id")) != question_id:
        raise FanxiuRuntimeMemoryError("灵泉压缩题库行与当前题目ID不一致")
    text_ids = {as_int(question_fields.get(key)) for key in ("content", "showAnswer")
                if not isinstance(question_fields.get(key), str)}
    if None in text_ids or any(value <= 0 for value in text_ids):
        raise FanxiuRuntimeMemoryError("灵泉题干或答案缺少有效文本身份")
    texts = read_loaded_item_text(text_ids, reader=reader, state_address=state_address,
                                  environment_address=environment_address)
    if not texts["complete"]:
        raise FanxiuRuntimeMemoryError(f"灵泉题干或答案语言文本未加载：{texts['missing_ids']}")
    def text(field):
        value = question_fields[field]
        return (value if isinstance(value, str) else texts["texts_by_id"][as_int(value)]).strip()
    question, answer = text("content"), text("showAnswer")
    if not question or not answer:
        raise FanxiuRuntimeMemoryError("灵泉题干或答案为空，未提供答题授权")

    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    phase, remaining_ms = _phase_and_remaining(
        now_ms=current_ms,
        start_time_ms=start_time_ms,
        prepare_ms=prepare_ms,
        question_ms=question_ms,
        answer_ms=answer_ms,
    )
    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "ok": True,
        "available": True,
        "source": "runtime_memory",
        "protocol": "AlliancespringquestionMgr.Model.data.questionInfo",
        "question_id": question_id,
        "question_index": progress,
        "question_total": question_total,
        "question": question,
        "answer": answer,
        "phase": phase,
        "remaining_ms": remaining_ms,
        "remaining_seconds": int(math.ceil(remaining_ms / 1000)),
        "start_time_ms": start_time_ms,
        "captured_at": captured_at,
        "captured_at_epoch": time.time(),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "lingquan_root_address": f"0x{lingquan_root:x}",
            "db_root_address": f"0x{db_root:x}",
            "lingquan_root_cache_hit": lingquan_cache_hit,
            "db_root_cache_hit": db_cache_hit,
        },
    }


def read_lingquan_question_snapshot() -> dict[str, Any]:
    """Read loaded question globals without heap scans or initialization.

    The main Lua environment owns both managers. A fresh global observation
    validates their signatures and uses one reader for their current values;
    a missing instance or question remains unavailable, never a request to
    invoke Inst_get/ReqQuestionInfo or discover a constructor marker.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        context = acquire_lua_global_runtime_context(("AlliancespringquestionMgr", "DBMgr"))
        memory = context.memory
        context.loaded_manager_fields("AlliancespringquestionMgr", _LINGQUAN_METHODS)
        context.loaded_manager_fields("DBMgr", _DB_METHODS)
        lingquan = table_ref(context.field(context.binding.environment_address, "AlliancespringquestionMgr"))
        db = table_ref(context.field(context.binding.environment_address, "DBMgr"))
        if lingquan is None or db is None:
            raise FanxiuRuntimeMemoryError("灵泉问答全局在观察中消失", code="not_loaded")
        lingquan_root, db_root = lingquan.address, db.address
        result = _snapshot(
            memory,
            lingquan_root,
            db_root,
            lingquan_cache_hit=False,
            db_cache_hit=False,
            reader=context.reader,
            environment_address=context.binding.environment_address,
            state_address=context.binding.state_address,
        )
        result["evidence"]["manager_source"] = "loaded_lua_globals"
        result["evidence"]["global_phase_timings_seconds"] = context.timings
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
    result = read_lingquan_question_snapshot()
    with _CACHE_LOCK:
        _CACHE_RESULT = result
        _CACHE_REFRESHING = False


def get_lingquan_question_snapshot(
    *,
    max_age_seconds: float = 2.0,
    min_refresh_interval_seconds: float = 0.2,
) -> dict[str, Any]:
    """Return immediately from cache and refresh the read-only snapshot in background."""

    from backend.core.fanxiu.client.remote_transport import remote_device_active
    if remote_device_active():
        from .scoped_snapshot import read_scoped_snapshot
        return read_scoped_snapshot(read_lingquan_question_snapshot, max_age_seconds=max_age_seconds)

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
            name="fanxiu-lingquan-runtime-snapshot",
            daemon=True,
        ).start()

    if cached is None:
        return {
            "ok": False,
            "available": False,
            "source": "runtime_memory",
            "reason": "灵泉 Runtime 快照正在后台预热",
            "refreshing": True,
            "cache_age_seconds": None,
        }

    captured_epoch = cached.get("captured_at_epoch")
    cache_age = (
        max(0.0, time.time() - float(captured_epoch))
        if isinstance(captured_epoch, (int, float))
        else None
    )
    fresh = cache_age is not None and cache_age <= max(0.0, max_age_seconds)
    cached["cache_age_seconds"] = cache_age
    cached["fresh"] = fresh
    cached["refreshing"] = _CACHE_REFRESHING
    if not fresh:
        cached["available"] = False
        cached["ok"] = False
        cached["reason"] = "灵泉 Runtime 快照已过期，正在后台刷新"
    return cached
