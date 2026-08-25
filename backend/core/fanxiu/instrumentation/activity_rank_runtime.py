from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time
from typing import Any, Iterable

from backend.core.fanxiu.catalog.server_mapping import (
    resolve_fanxiu_region_server_by_id,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_manager_root,
)


ACTIVITY_RANK_MARKER = b"LuaActivityrankMgr"
ACTIVITY_RANK_METHODS = frozenset({"LuaActivityrankMgr", "Inst_get"})
ACTIVITY_RANK_MANAGER_KEY = "activity-rank"


@dataclass(frozen=True)
class ActivityRankSnapshot:
    """Game-level ranking data, independent of any concrete activity."""

    activity_id: int
    rank_list_size: int
    declared_rank_count: int
    self_ranking: dict[str, Any]
    rankings: tuple[dict[str, Any], ...]

    @property
    def loaded_rank_count(self) -> int:
        return len(self.rankings)


def required_runtime_fields(
    reader: LuaJitReader,
    value: Any,
    names: tuple[str, ...],
    context: str,
) -> dict[Any, Any]:
    fields = reader.fields(value)
    missing = [name for name in names if name not in fields]
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"{context} 尚未初始化，缺少字段：{','.join(missing)}",
            code="schema_mismatch",
        )
    return fields


def _activity_rank_dictionary(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, ACTIVITY_RANK_METHODS)
    instance = required_runtime_fields(
        reader, manager.get("inst"), ("Model",), "活动榜管理器"
    )
    model = required_runtime_fields(
        reader, instance["Model"], ("ActivityrankData",), "活动榜模型"
    )
    data = required_runtime_fields(
        reader,
        model["ActivityrankData"],
        ("V_RankDataDic",),
        "活动榜数据",
    )
    return reader.dictionary_fields(data["V_RankDataDic"])


def loaded_activity_rank_ids(
    reader: LuaJitReader,
    root_address: int,
) -> tuple[int, ...]:
    return tuple(
        sorted(
            activity_id
            for key in _activity_rank_dictionary(reader, root_address)
            if (activity_id := as_int(key)) is not None
        )
    )


def _ranking_row(reader: LuaJitReader, value: Any) -> dict[str, Any] | None:
    fields = reader.fields(value)
    rank = as_int(fields.get("rank"))
    score = as_int(fields.get("score"))
    if rank is None or score is None:
        return None
    server_id = as_int(fields.get("serverId"))
    if server_id is None:
        for raw_candidate in (fields.get("id"), fields.get("key")):
            candidate = as_int(raw_candidate)
            if candidate is None:
                try:
                    candidate = int(str(raw_candidate or "").strip())
                except ValueError:
                    continue
            if resolve_fanxiu_region_server_by_id(candidate).get("server_name"):
                server_id = candidate
                break
    resolved_server = resolve_fanxiu_region_server_by_id(server_id)
    return {
        "rank": rank,
        "score": score,
        "role_key": str(
            fields.get("key") or f"{server_id or 0}:{fields.get('name') or ''}"
        ),
        "name": str(fields.get("name") or ""),
        "server_id": server_id,
        "server_name": str(
            fields.get("serverName")
            or resolved_server.get("server_name")
            or ""
        ),
        "club_name": str(fields.get("clubName") or ""),
    }


def read_activity_rank_snapshot(
    reader: LuaJitReader,
    root_address: int,
    activity_id: int,
) -> ActivityRankSnapshot:
    rank_dictionary = _activity_rank_dictionary(reader, root_address)
    rank_value = next(
        (
            value
            for key, value in rank_dictionary.items()
            if as_int(key) == int(activity_id)
        ),
        None,
    )
    if rank_value is None:
        loaded_ids = [as_int(key) for key in rank_dictionary]
        loaded_text = ",".join(str(value) for value in loaded_ids if value is not None)
        suffix = f"；当前已加载：{loaded_text}" if loaded_text else ""
        raise FanxiuRuntimeMemoryError(
            f"活动榜 {int(activity_id)} 尚未同步到游戏缓存{suffix}",
            code="data_not_loaded",
        )
    rank_info = required_runtime_fields(
        reader,
        rank_value,
        ("selfRankVO", "rankVOS", "rankListSize"),
        f"活动榜 {int(activity_id)}",
    )
    self_row = _ranking_row(reader, rank_info["selfRankVO"])
    if self_row is None:
        raise FanxiuRuntimeMemoryError(
            f"活动榜 {int(activity_id)} 的个人排名无效",
            code="snapshot_incoherent",
        )
    rank_items, declared_count = reader.list_items(rank_info["rankVOS"])
    rankings = tuple(
        sorted(
            (
                row
                for value in rank_items
                if (row := _ranking_row(reader, value)) is not None
            ),
            key=lambda row: int(row["rank"]),
        )
    )
    rank_list_size = as_int(rank_info.get("rankListSize"))
    if rank_list_size is None:
        raise FanxiuRuntimeMemoryError(
            f"活动榜 {int(activity_id)} 的总人数无效",
            code="snapshot_incoherent",
        )
    return ActivityRankSnapshot(
        activity_id=int(activity_id),
        rank_list_size=rank_list_size,
        declared_rank_count=declared_count,
        self_ranking=self_row,
        rankings=rankings,
    )


def resolve_activity_rank_root(
    memory: MumuProcessMemory,
    *,
    allow_discovery: bool,
    force_refresh: bool = False,
) -> tuple[int, bool]:
    def validate(reader: LuaJitReader, address: int) -> None:
        # Root identity belongs to ActivityrankMgr, not to one concrete event.
        # An unopened event may legitimately be absent from V_RankDataDic and
        # must not invalidate a correct Manager root or trigger another scan.
        _activity_rank_dictionary(reader, address)

    return resolve_manager_root(
        memory,
        manager_key=ACTIVITY_RANK_MANAGER_KEY,
        marker=ACTIVITY_RANK_MARKER,
        required_methods=ACTIVITY_RANK_METHODS,
        validate=validate,
        allow_discovery=allow_discovery,
        force_refresh=force_refresh,
    )


def prepare_activity_rank_runtime(activity_ids: Iterable[int]) -> dict[str, Any]:
    """Explicit slow recovery path; ordinary reads must not call this implicitly."""

    memory = MumuProcessMemory.discover_cached()
    requested_ids = tuple(dict.fromkeys(int(value) for value in activity_ids))
    root, cache_hit = resolve_activity_rank_root(
        memory,
        allow_discovery=True,
        force_refresh=True,
    )
    loaded_ids = loaded_activity_rank_ids(LuaJitReader(memory), root)
    missing_ids = [value for value in requested_ids if value not in loaded_ids]
    return {
        "ok": not missing_ids,
        "complete": not missing_ids,
        "error_code": "data_not_loaded" if missing_ids else None,
        "recovery_required": bool(missing_ids),
        "reason": (
            "活动榜数据尚未加载：" + ",".join(str(value) for value in missing_ids)
            if missing_ids
            else None
        ),
        "pid": memory.pid,
        "process_start_ticks": memory.process_start_ticks,
        "root_address": f"0x{root:x}",
        "root_cache_hit": cache_hit,
        "loaded_activity_ids": list(loaded_ids),
        "missing_activity_ids": missing_ids,
    }


def read_activity_rank_runtime_snapshot(activity_id: int) -> dict[str, Any]:
    """Fast, fail-closed snapshot for any already-loaded activity leaderboard."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached(fallback_to_discovery=False)
        root, root_cache_hit = resolve_activity_rank_root(
            memory,
            allow_discovery=False,
        )
        snapshot = read_activity_rank_snapshot(
            LuaJitReader(memory), root, int(activity_id)
        )
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory",
            "rank_activity_id": snapshot.activity_id,
            "rank_list_size": snapshot.rank_list_size,
            "loaded_rank_count": snapshot.loaded_rank_count,
            "declared_rank_count": snapshot.declared_rank_count,
            "self_ranking": snapshot.self_ranking,
            "rankings": list(snapshot.rankings),
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "root_cache_hit": root_cache_hit,
                "resolution_path": ["process_cache", "root_cache", "snapshot"],
            },
        }
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
            "rank_activity_id": int(activity_id),
            "reason": str(exc),
            "error_code": error_code,
            "recovery_required": error_code in {
                "process_cache_miss",
                "root_cache_miss",
                "data_not_loaded",
            },
            "rank_list_size": 0,
            "loaded_rank_count": 0,
            "declared_rank_count": 0,
            "self_ranking": None,
            "rankings": [],
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
                "root_cache_hit": False,
                "resolution_path": [
                    "process_cache"
                    if error_code == "process_cache_miss"
                    else "root_cache"
                    if error_code == "root_cache_miss"
                    else "snapshot"
                ],
            },
        }
