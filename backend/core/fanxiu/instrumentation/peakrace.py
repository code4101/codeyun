from __future__ import annotations

"""Strictly read-only projection of the loaded Peak Race client model."""

import time
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
    _lua_addresses,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    table_ref,
)


PEAKRACE_MANAGER_KEY = "peakrace"
PEAKRACE_GLOBAL_NAME = "PeakraceMgr"
PEAKRACE_MANAGER_METHODS = frozenset({"Inst_get", "LuaPeakraceMgr"})
_PEAKRACE_DATA_IDENTITY_FIELDS = frozenset(
    {
        "activityDefaultGroupDic",
        "guessActivityRoleFlagDic",
        "guessActivityRoleVODic",
        "successGuessActivityRoleVODic",
    }
)
_PEAKRACE_SELF_DATA_FIELDS = frozenset(
    {
        "activity2GroupMap",
        "currRound",
        "fazeTriggerTimes",
        "guessVOList",
        "selfRank",
        "worshipDailyTimes",
    }
)


def _identity(reader: LuaJitReader, value: Any) -> int | None:
    return reader.long(value) if isinstance(value, LuaRef) else as_int(value)


def _peakrace_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager = manager_index_fields(
        reader,
        root_address,
        PEAKRACE_MANAGER_METHODS,
    )
    instance = reader.fields(manager.get("inst"))
    model = reader.fields(instance.get("Model"))
    data = reader.fields(model.get("PeakraceData"))
    if not _PEAKRACE_DATA_IDENTITY_FIELDS.issubset(data):
        raise FanxiuRuntimeMemoryError(
            "PeakraceMgr.Model.PeakraceData 尚未加载",
            code="data_not_loaded",
        )
    return data


def _required_identity(
    reader: LuaJitReader,
    value: Any,
    *,
    context: str,
) -> int:
    result = _identity(reader, value)
    if result is None:
        raise FanxiuRuntimeMemoryError(
            f"{context} 不是有效整数",
            code="snapshot_incoherent",
        )
    return result


def _activity_groups(
    reader: LuaJitReader,
    value: Any,
) -> list[dict[str, int]]:
    rows: list[dict[str, int]] = []
    for raw_activity_id, raw_group in reader.dictionary_fields(value).items():
        rows.append(
            {
                "activity_id": _required_identity(
                    reader,
                    raw_activity_id,
                    context="巅峰赛子活动 ID",
                ),
                "group": _required_identity(
                    reader,
                    raw_group,
                    context="巅峰赛分组",
                ),
            }
        )
    return sorted(rows, key=lambda row: row["activity_id"])


def _guess_rows(
    reader: LuaJitReader,
    value: Any,
) -> tuple[list[dict[str, Any]], int]:
    raw_guesses, declared_count = reader.list_items(value)
    if declared_count is None or declared_count != len(raw_guesses):
        raise FanxiuRuntimeMemoryError(
            "巅峰赛竞猜列表声明数量与可读项不一致",
            code="snapshot_incoherent",
        )
    guesses: list[dict[str, Any]] = []
    for index, raw_guess in enumerate(raw_guesses, start=1):
        fields = reader.fields(raw_guess)
        raw_role_ids, declared_role_count = reader.list_items(
            fields.get("guessRoleIds")
        )
        if (
            declared_role_count is None
            or declared_role_count != len(raw_role_ids)
        ):
            raise FanxiuRuntimeMemoryError(
                f"巅峰赛第 {index} 项竞猜目标数量不完整",
                code="snapshot_incoherent",
            )
        guesses.append(
            {
                "activity_id": _required_identity(
                    reader,
                    fields.get("activityId"),
                    context=f"巅峰赛第 {index} 项竞猜活动 ID",
                ),
                "group": _required_identity(
                    reader,
                    fields.get("group"),
                    context=f"巅峰赛第 {index} 项竞猜分组",
                ),
                "role_ids": [
                    _required_identity(
                        reader,
                        raw_role_id,
                        context=f"巅峰赛第 {index} 项竞猜角色 ID",
                    )
                    for raw_role_id in raw_role_ids
                ],
                "declared_role_count": declared_role_count,
            }
        )
    return guesses, declared_count


def _peakrace_snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data = _peakrace_data_fields(reader, root_address)
    self_data_ref = data.get("selfData")
    self_data = reader.fields(self_data_ref)
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    evidence = {
        "pid": memory.pid,
        "process_start_ticks": memory.process_start_ticks,
        "manager_cache_hit": root_cache_hit,
        "manager_resolver": "lua_global",
        "protocol": "PeakraceMgr.Model.PeakraceData.selfData",
    }
    if not self_data:
        return {
            "ok": False,
            "available": True,
            "complete": False,
            "source": "runtime_memory",
            "source_kind": "peakrace_runtime_memory",
            "error_code": "data_not_loaded",
            "reason": "巅峰赛个人数据尚未由游戏自然加载",
            "manager_loaded": True,
            "self_data_loaded": False,
            "self_rank": None,
            "current_round": None,
            "activity_groups": [],
            "guesses": [],
            "declared_guess_count": None,
            "faze_trigger_times": None,
            "worship_daily_times": None,
            "score_rank_loaded": table_ref(data.get("otherRankMap")) is not None,
            "captured_at": captured_at,
            "evidence": evidence,
        }
    if not _PEAKRACE_SELF_DATA_FIELDS.issubset(self_data):
        missing = sorted(_PEAKRACE_SELF_DATA_FIELDS.difference(self_data))
        raise FanxiuRuntimeMemoryError(
            "巅峰赛个人数据结构不完整，缺少字段：" + ",".join(missing),
            code="schema_mismatch",
        )

    activity_groups = _activity_groups(
        reader,
        self_data.get("activity2GroupMap"),
    )
    guesses, declared_guess_count = _guess_rows(
        reader,
        self_data.get("guessVOList"),
    )
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "runtime_memory",
        "source_kind": "peakrace_runtime_memory",
        "error_code": None,
        "reason": None,
        "manager_loaded": True,
        "self_data_loaded": True,
        "self_rank": _required_identity(
            reader,
            self_data.get("selfRank"),
            context="巅峰赛个人排名",
        ),
        "current_round": _required_identity(
            reader,
            self_data.get("currRound"),
            context="巅峰赛当前轮次",
        ),
        "activity_groups": activity_groups,
        "guesses": guesses,
        "declared_guess_count": declared_guess_count,
        "faze_trigger_times": _required_identity(
            reader,
            self_data.get("fazeTriggerTimes"),
            context="巅峰赛法则触发次数",
        ),
        "worship_daily_times": _required_identity(
            reader,
            self_data.get("worshipDailyTimes"),
            context="巅峰赛今日膜拜次数",
        ),
        "score_rank_loaded": table_ref(data.get("otherRankMap")) is not None,
        "captured_at": captured_at,
        "evidence": evidence,
    }


def read_peakrace_runtime_snapshot(
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Read the already-loaded Peak Race self model without invoking Lua.

    The reader resolves only the exact ``_G.PeakraceMgr`` global. It never
    calls ``Inst_get``, sends ``CM_PeakRaceSelfData``, or falls back to a heap
    scan. When the game has not naturally loaded ``selfData``, the result is
    available but incomplete with ``error_code=data_not_loaded``.

    :param bool force_refresh: Ignore the process-bound Manager root cache.
    :return dict: A structured snapshot with completeness and evidence fields.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached(fallback_to_discovery=True)
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key=PEAKRACE_MANAGER_KEY,
            state_address=int(_lua_addresses(memory)["state"], 16),
            global_name=PEAKRACE_GLOBAL_NAME,
            required_methods=PEAKRACE_MANAGER_METHODS,
            validate=_peakrace_data_fields,
            force_refresh=bool(force_refresh),
        )
        result = _peakrace_snapshot(
            memory,
            root,
            root_cache_hit=cache_hit,
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
        return result
    except Exception as exc:
        error_code = (
            exc.code
            if isinstance(exc, FanxiuRuntimeMemoryError)
            else "unexpected_error"
        )
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "source_kind": "peakrace_runtime_memory",
            "error_code": error_code,
            "reason": str(exc)
            if isinstance(exc, FanxiuRuntimeMemoryError)
            else f"{type(exc).__name__}: {exc}",
            "manager_loaded": False,
            "self_data_loaded": False,
            "self_rank": None,
            "current_round": None,
            "activity_groups": [],
            "guesses": [],
            "declared_guess_count": None,
            "faze_trigger_times": None,
            "worship_daily_times": None,
            "score_rank_loaded": False,
            "captured_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
                "manager_resolver": "lua_global",
                "protocol": "PeakraceMgr.Model.PeakraceData.selfData",
            },
        }


__all__ = ["read_peakrace_runtime_snapshot"]
