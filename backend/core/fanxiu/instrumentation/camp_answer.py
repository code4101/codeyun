from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.final_camp_answer import (
    _DB_MARKER,
    _DB_METHODS,
    _OPTION_TABLE_NAME,
    _QUESTION_TABLE_NAME,
    _array_values,
    _config_table,
    _integer,
    _object_fields,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    manager_index_fields,
    resolve_manager_root,
)


_CAMP_MARKER = b"LuaCampAnswerMgr"
_CAMP_METHODS = frozenset({"LuaCampAnswerMgr", "Inst_get", "BeginAnswer"})


def _camp_answer_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _CAMP_METHODS)
    instance = reader.fields(manager.get("inst"))
    model = reader.fields(instance.get("Model"))
    data = reader.fields(model.get("CampAnswerData"))
    if not data:
        raise FanxiuRuntimeMemoryError("普通答题 Runtime 模型尚未初始化")
    return data


def _snapshot(
    memory: MumuProcessMemory,
    camp_root: int,
    db_root: int,
    *,
    camp_cache_hit: bool,
    db_cache_hit: bool,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data = _camp_answer_data_fields(reader, camp_root)
    info = _object_fields(reader, data.get("_campAnswerInfo"))
    answer_vo = _object_fields(reader, info.get("campAnswerVO"))
    raw_questions, declared_count = reader.list_items(answer_vo.get("questions"))
    if not raw_questions:
        raise FanxiuRuntimeMemoryError("普通答题题目清单尚未下发")

    question_table = _config_table(reader, db_root, _QUESTION_TABLE_NAME)
    option_table = _config_table(reader, db_root, _OPTION_TABLE_NAME)
    questions: list[dict[str, Any]] = []
    for raw in raw_questions:
        question_vo = _object_fields(reader, raw)
        index = _integer(question_vo.get("index"))
        config_id = _integer(question_vo.get("configId"))
        if index is None or config_id is None:
            continue
        config = _object_fields(reader, question_table.get(config_id))
        prompt = str(config.get("question") or "").strip()
        correct_option_id = _integer(config.get("answer"))
        option_ids = [
            option_id
            for value in _array_values(reader, config.get("options"))
            if (option_id := _integer(value)) is not None
        ]
        option_records: list[dict[str, Any]] = []
        for position, option_id in enumerate(option_ids):
            option_config = _object_fields(reader, option_table.get(option_id))
            option_records.append(
                {
                    "id": option_id,
                    "text": str(option_config.get("options") or "").strip(),
                    "position": position,
                }
            )
        correct_position = next(
            (
                item["position"]
                for item in option_records
                if item["id"] == correct_option_id
            ),
            None,
        )
        if (
            not prompt
            or len(option_records) != 3
            or any(not item["text"] for item in option_records)
            or correct_position is None
        ):
            continue
        questions.append(
            {
                "index": index,
                "config_id": config_id,
                "question": prompt,
                "options": option_records,
                "correct_option_id": correct_option_id,
                "correct_position": correct_position,
                "answer": option_records[correct_position]["text"],
                "server_answer": _integer(question_vo.get("answer")),
                "server_correct": bool(question_vo.get("correct")),
                "start_time_ms": reader.long(question_vo.get("startTime")),
                "deadline_ms": reader.long(question_vo.get("deadline")),
            }
        )
    questions.sort(key=lambda item: item["index"])
    if not questions:
        raise FanxiuRuntimeMemoryError("普通答题清单没有可解析题目")

    return {
        "ok": True,
        "available": True,
        "complete": declared_count is not None and len(questions) == declared_count,
        "source": "runtime_memory",
        "protocol": "CampAnswerMgr.Model.CampAnswerData._campAnswerInfo.campAnswerVO.questions",
        "question_count": len(questions),
        "declared_question_count": declared_count,
        "last_answered_index": _integer(info.get("index")) or 0,
        "questions": questions,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "captured_at_epoch": time.time(),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "camp_root_address": f"0x{camp_root:x}",
            "db_root_address": f"0x{db_root:x}",
            "camp_root_cache_hit": camp_cache_hit,
            "db_root_cache_hit": db_cache_hit,
        },
    }


def read_camp_answer_snapshot() -> dict[str, Any]:
    """Synchronously read the ordinary activity's local question plan."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover()
        camp_root, camp_cache_hit = resolve_manager_root(
            memory,
            manager_key="camp-answer",
            marker=_CAMP_MARKER,
            required_methods=_CAMP_METHODS,
            validate=lambda reader, root: _camp_answer_data_fields(reader, root),
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
            camp_root,
            db_root,
            camp_cache_hit=camp_cache_hit,
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
    result = read_camp_answer_snapshot()
    with _CACHE_LOCK:
        _CACHE_RESULT = result
        _CACHE_REFRESHING = False


def get_camp_answer_snapshot(
    *,
    max_age_seconds: float = 2.0,
    min_refresh_interval_seconds: float = 0.1,
) -> dict[str, Any]:
    """Return immediately and refresh the read-only 15-question plan in background."""

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
            name="fanxiu-camp-answer-snapshot",
            daemon=True,
        ).start()
    if cached is None:
        return {
            "ok": False,
            "available": False,
            "fresh": False,
            "source": "runtime_memory",
            "reason": "普通答题 Runtime 快照正在后台预热",
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
        cached["reason"] = "普通答题 Runtime 快照已过期，正在后台刷新"
    return cached


__all__ = ["get_camp_answer_snapshot", "read_camp_answer_snapshot"]
