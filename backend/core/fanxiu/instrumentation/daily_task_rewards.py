from __future__ import annotations

"""Strict read-only QuestMgr facts for daily activity-task rewards.

This module only reads already-loaded ``QuestMgr`` data.  It neither opens an
activity UI nor initializes managers, and it never sends a claim packet.
"""

import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Any

from backend.core.fanxiu.instrumentation.daily_task import (
    _dictionary_item,
    _fields,
    _list_values,
    _quest_data_fields,
    _resolve_quest_root,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
)


_ACTIVITY_TASK_TYPE = 3
_FINISH_STATUS = 4
_FINISHED_AND_REWARDED_STATUS = 5


@dataclass(frozen=True)
class TaskRewardDomainSpec:
    key: str
    label: str
    activity_id: int
    task_ids: tuple[int, ...]
    condition_key: str
    thresholds: tuple[int, ...]


LUNDAO_TASK_REWARD_SPEC = TaskRewardDomainSpec(
    key="lundao",
    label="论道",
    activity_id=111601,
    task_ids=tuple(range(11160101, 11160113)),
    condition_key="LundaoDaoxin",
    thresholds=(10000, 20000, 30000, 40000, 55000, 70000, 85000, 100000, 120000, 140000, 170000, 200000),
)

LINGMAI_TASK_REWARD_SPEC = TaskRewardDomainSpec(
    key="lingmai",
    label="灵脉",
    activity_id=2005,
    task_ids=tuple(range(30350001, 30350013)),
    condition_key="UnionVeinsXin",
    thresholds=(200000, 300000, 400000, 500000, 600000, 750000, 900000, 1100000, 1300000, 1550000, 1800000, 2100000),
)

QIXI_MOJIE_TASK_REWARD_SPEC = TaskRewardDomainSpec(
    key="qixi_mojie",
    label="奇袭魔界",
    activity_id=64220001,
    task_ids=tuple(range(64220001, 64220021)),
    condition_key="CrossUnionMemberJoinDemonBossTimes",
    thresholds=tuple(range(7, 141, 7)),
)


def _yunmeng_task_ids(*, prefix: int, score_start: int) -> tuple[int, ...]:
    """Return one exact Yunmeng task ladder from the generated config.

    The game retains two mutually exclusive score ladders for each activity
    instance.  The cultivation (subType=6) and trial (subType=18) rows are
    shared; only the score ladder (subType=13) changes.  Keeping the variants
    separate lets the Runtime projection fail closed instead of authorizing a
    union of rows that can never belong to one live activity instance.
    """

    return (
        *range(prefix + 1, prefix + 9),
        *range(prefix + score_start, prefix + score_start + 8),
        *range(prefix + 17, prefix + 22),
    )


YUNMENG_TASK_REWARD_SPECS = (
    TaskRewardDomainSpec(
        key="yunmeng_1210001_wallet",
        label="云梦试剑",
        activity_id=1210001,
        task_ids=_yunmeng_task_ids(prefix=1160000, score_start=9),
        condition_key="YunmengMixed",
        thresholds=tuple(range(21)),
    ),
    TaskRewardDomainSpec(
        key="yunmeng_1210001_summon",
        label="云梦试剑",
        activity_id=1210001,
        task_ids=_yunmeng_task_ids(prefix=1160000, score_start=22),
        condition_key="YunmengMixed",
        thresholds=tuple(range(21)),
    ),
    TaskRewardDomainSpec(
        key="yunmeng_1210011_wallet",
        label="云梦试剑",
        activity_id=1210011,
        task_ids=_yunmeng_task_ids(prefix=1160100, score_start=9),
        condition_key="YunmengMixed",
        thresholds=tuple(range(21)),
    ),
    TaskRewardDomainSpec(
        key="yunmeng_1210011_summon",
        label="云梦试剑",
        activity_id=1210011,
        task_ids=_yunmeng_task_ids(prefix=1160100, score_start=22),
        condition_key="YunmengMixed",
        thresholds=tuple(range(21)),
    ),
    TaskRewardDomainSpec(
        key="yunmeng_8210001_wallet",
        label="8跨云梦试剑",
        activity_id=8210001,
        task_ids=_yunmeng_task_ids(prefix=8160000, score_start=9),
        condition_key="YunmengMixed",
        thresholds=tuple(range(21)),
    ),
    TaskRewardDomainSpec(
        key="yunmeng_8210001_summon",
        label="8跨云梦试剑",
        activity_id=8210001,
        task_ids=_yunmeng_task_ids(prefix=8160000, score_start=22),
        condition_key="YunmengMixed",
        thresholds=tuple(range(21)),
    ),
)

TASK_REWARD_DOMAIN_SPECS = {
    spec.key: spec
    for spec in (
        LUNDAO_TASK_REWARD_SPEC,
        LINGMAI_TASK_REWARD_SPEC,
        QIXI_MOJIE_TASK_REWARD_SPEC,
        *YUNMENG_TASK_REWARD_SPECS,
    )
}
# Only the three daily domains participate in the aggregate daily job.
# Yunmeng variants are opt-in because exactly one of four retained config
# ladders may be live, and they are orchestrated by the Yunmeng workflow.
TASK_REWARD_DOMAIN_ORDER = ("lundao", "lingmai", "qixi_mojie")


_ENTRY_INDEX_CACHE_LOCK = RLock()
_ENTRY_INDEX_CACHE: dict[tuple[int, int, int, int, int], dict[int, int]] = {}
_ALL_EXPECTED_TASK_IDS = frozenset(
    task_id for spec in TASK_REWARD_DOMAIN_SPECS.values() for task_id in spec.task_ids
)


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _progress_complete(progress: Any) -> bool:
    return bool(progress) and all(bool(item.get("finish")) for item in progress if isinstance(item, dict))


def build_activity_task_reward_snapshot(
    *,
    spec: TaskRewardDomainSpec,
    task_entries: list[dict[str, Any]],
    finished_task_ids: list[int],
) -> dict[str, Any]:
    """Normalize one domain and fail closed when the loaded task set is partial."""

    expected = set(spec.task_ids)
    finished = {task_id for item in finished_task_ids if (task_id := _positive_int(item)) in expected}
    grouped: dict[int, list[dict[str, Any]]] = {}
    for raw in task_entries:
        task_id = _positive_int(raw.get("taskId") or raw.get("task_id"))
        if task_id in expected:
            grouped.setdefault(task_id, []).append(dict(raw))

    duplicate_task_ids = sorted(task_id for task_id, rows in grouped.items() if len(rows) != 1)
    task_states: list[dict[str, Any]] = []
    malformed_task_ids: list[int] = []
    claimable_task_ids: list[int] = []
    claimed_task_ids: list[int] = []
    pending_task_ids: list[int] = []

    for task_id in spec.task_ids:
        rows = grouped.get(task_id) or []
        entry = rows[0] if rows else {}
        status = _integer(entry.get("status")) if entry else None
        turn = _integer(entry.get("turn")) if entry else None
        reward_time = _integer(entry.get("rewardTime", entry.get("reward_time"))) if entry else None
        progress = entry.get("progressList", entry.get("progress_list", [])) if entry else []
        progress_done = _progress_complete(progress)
        claimed = task_id in finished or status == _FINISHED_AND_REWARDED_STATUS
        malformed = bool(entry) and (status is None or turn is None or reward_time is None)
        claimable = bool(
            entry
            and not claimed
            and not malformed
            and (
                status == _FINISH_STATUS
                or turn > reward_time
                or (turn == reward_time and progress_done)
            )
        )
        if claimed:
            claimed_task_ids.append(task_id)
        elif claimable:
            claimable_task_ids.append(task_id)
        elif entry:
            pending_task_ids.append(task_id)
        if malformed:
            malformed_task_ids.append(task_id)
        task_states.append(
            {
                "task_id": task_id,
                "present": bool(entry),
                "status": status,
                "turn": turn,
                "reward_time": reward_time,
                "progress_complete": progress_done,
                "claimed": claimed,
                "claimable": claimable,
            }
        )

    represented = set(grouped) | finished
    missing_task_ids = sorted(expected - represented)
    complete = not missing_task_ids and not malformed_task_ids and not duplicate_task_ids
    if not complete:
        state = "ambiguous"
    elif claimable_task_ids:
        state = "claimable"
    elif len(claimed_task_ids) == len(spec.task_ids):
        state = "already_claimed"
    else:
        state = "none"

    return {
        "domain": spec.key,
        "domain_label": spec.label,
        "activity_id": spec.activity_id,
        "expected_task_count": len(spec.task_ids),
        "observed_task_count": len(grouped),
        "claimable_task_ids": claimable_task_ids,
        # This is the only list a future executor may consume. Partial Runtime
        # state never authorizes a click or a CM_FinishAndRewQuest packet.
        "authorized_claim_task_ids": claimable_task_ids if complete else [],
        "claimed_task_ids": claimed_task_ids,
        "pending_task_ids": pending_task_ids,
        "missing_task_ids": missing_task_ids,
        "malformed_task_ids": malformed_task_ids,
        "duplicate_task_ids": duplicate_task_ids,
        "complete": complete,
        "state": state,
        "tasks": task_states,
    }


def _serialize_entry(reader: LuaJitReader, value: Any) -> dict[str, Any]:
    fields = _fields(reader, value)
    progress = []
    for item in _list_values(reader, fields.get("progressList")):
        progress_fields = _fields(reader, item)
        progress.append(
            {
                "finish": bool(progress_fields.get("finish")),
                "progress": progress_fields.get("progress"),
                "target": progress_fields.get("target"),
            }
        )
    return {
        "taskId": fields.get("taskId"),
        "status": fields.get("status"),
        "turn": fields.get("turn"),
        "rewardTime": fields.get("rewardTime"),
        "progressList": progress,
    }


def _list_values_with_identity(
    reader: LuaJitReader,
    value: Any,
) -> tuple[list[Any], tuple[int, int] | None]:
    """Return current list values and an identity for its backing Lua table."""

    values = _list_values(reader, value)
    if not isinstance(value, LuaRef) or value.kind != "table":
        return values, None
    wrapper = _fields(reader, value)
    data = wrapper.get("_dt_")
    if not isinstance(data, LuaRef) or data.kind != "table":
        return values, None
    return values, (int(data.address), int(wrapper.get("count") or len(values)))


def _selected_string_fields(
    reader: LuaJitReader,
    value: Any,
    names: frozenset[str],
) -> dict[str, Any]:
    """Read selected fields, falling back to a full row only for compatibility."""

    if isinstance(value, LuaRef) and value.kind == "table":
        selected = reader.string_fields(value.address, names)
        if names.issubset(selected):
            return selected
        # Some generated VO tables can proxy a field through their metatable.
        # Keep that known schema compatible, but only expand this one target row.
        full = _fields(reader, value)
        return {name: full.get(name) for name in names}
    full = _fields(reader, value)
    return {name: full.get(name) for name in names}


def _serialize_selected_entry(reader: LuaJitReader, value: Any) -> dict[str, Any]:
    names = frozenset({"taskId", "status", "turn", "rewardTime", "progressList"})
    fields = _selected_string_fields(reader, value, names)
    progress: list[dict[str, Any]] = []
    turn = _integer(fields.get("turn"))
    reward_time = _integer(fields.get("rewardTime"))
    # Progress is only relevant to the equality edge case. Avoid expanding it
    # for the overwhelmingly common pending/claimable/claimed rows.
    if turn is not None and reward_time is not None and turn == reward_time:
        for item in _list_values(reader, fields.get("progressList")):
            progress_fields = _selected_string_fields(
                reader,
                item,
                frozenset({"finish", "progress", "target"}),
            )
            progress.append(
                {
                    "finish": bool(progress_fields.get("finish")),
                    "progress": progress_fields.get("progress"),
                    "target": progress_fields.get("target"),
                }
            )
    return {
        "taskId": fields.get("taskId"),
        "status": fields.get("status"),
        "turn": fields.get("turn"),
        "rewardTime": fields.get("rewardTime"),
        "progressList": progress,
    }


def _entry_index_cache_key(
    *,
    memory: MumuProcessMemory,
    root: int,
    list_identity: tuple[int, int] | None,
) -> tuple[int, int, int, int, int] | None:
    if list_identity is None:
        return None
    return (
        int(memory.pid),
        int(memory.process_start_ticks),
        int(root),
        int(list_identity[0]),
        int(list_identity[1]),
    )


def _cache_entry_indices(
    key: tuple[int, int, int, int, int] | None,
    entries: list[dict[str, Any]],
) -> None:
    if key is None:
        return
    grouped: dict[int, list[int]] = {}
    for index, entry in enumerate(entries):
        task_id = _positive_int(entry.get("taskId"))
        if task_id in _ALL_EXPECTED_TASK_IDS:
            grouped.setdefault(task_id, []).append(index)
    mapping = {
        task_id: indices[0]
        for task_id, indices in grouped.items()
        if len(indices) == 1
    }
    _store_entry_index_mapping(key, mapping)


def _store_entry_index_mapping(
    key: tuple[int, int, int, int, int] | None,
    mapping: dict[int, int],
) -> None:
    if key is None:
        return
    process_root = key[:3]
    with _ENTRY_INDEX_CACHE_LOCK:
        for stale_key in tuple(_ENTRY_INDEX_CACHE):
            if stale_key[:3] == process_root and stale_key != key:
                _ENTRY_INDEX_CACHE.pop(stale_key, None)
        _ENTRY_INDEX_CACHE[key] = dict(mapping)


def _derive_entry_indices_after_claim(
    key: tuple[int, int, int, int, int] | None,
    expected_claimed_task_id: int | None,
) -> dict[int, int] | None:
    """Shift cached CList slots after one expected claimed row is removed."""

    if key is None or expected_claimed_task_id is None:
        return None
    with _ENTRY_INDEX_CACHE_LOCK:
        candidates = [
            (cached_key, dict(mapping))
            for cached_key, mapping in _ENTRY_INDEX_CACHE.items()
            if cached_key[:3] == key[:3]
            and cached_key[4] == key[4] + 1
            and expected_claimed_task_id in mapping
        ]
    if len(candidates) != 1:
        return None
    _previous_key, previous = candidates[0]
    removed_index = previous.pop(expected_claimed_task_id)
    shifted = {
        task_id: index - 1 if index > removed_index else index
        for task_id, index in previous.items()
    }
    _store_entry_index_mapping(key, shifted)
    return shifted


def _rebuild_entry_indices(
    reader: LuaJitReader,
    values: list[Any],
    key: tuple[int, int, int, int, int] | None,
) -> dict[int, int]:
    grouped: dict[int, list[int]] = {}
    for index, value in enumerate(values):
        task_id = _positive_int(
            _selected_string_fields(reader, value, frozenset({"taskId"})).get("taskId")
        )
        if task_id in _ALL_EXPECTED_TASK_IDS:
            grouped.setdefault(task_id, []).append(index)
    mapping = {
        task_id: indices[0]
        for task_id, indices in grouped.items()
        if len(indices) == 1
    }
    _store_entry_index_mapping(key, mapping)
    return mapping


def _validated_domain_specs(
    domains: Iterable[str] | None,
) -> tuple[TaskRewardDomainSpec, ...]:
    domain_keys = TASK_REWARD_DOMAIN_ORDER if domains is None else tuple(domains)
    unknown = [domain for domain in domain_keys if domain not in TASK_REWARD_DOMAIN_SPECS]
    if unknown:
        raise ValueError(f"未知任务奖励域: {unknown[0]}")
    if len(set(domain_keys)) != len(domain_keys):
        raise ValueError("任务奖励域不能重复")
    return tuple(TASK_REWARD_DOMAIN_SPECS[domain] for domain in domain_keys)


def _unavailable_domain_snapshot(
    spec: TaskRewardDomainSpec,
    reason: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "available": False,
        "complete": False,
        "source": "runtime_memory",
        "domain": spec.key,
        "domain_label": spec.label,
        "activity_id": spec.activity_id,
        "state": "unavailable",
        "authorized_claim_task_ids": [],
        "reason": reason,
    }


def read_activity_task_reward_snapshots(
    domains: Iterable[str] | None = None,
    *,
    include_activity_tasks: bool = False,
    force_process_refresh: bool = False,
) -> dict[str, Any]:
    """Read several reward domains from one shared ``QuestMgr`` decode.

    ``domains=None`` reads all standard domains in ``TASK_REWARD_DOMAIN_ORDER``.
    The process, QuestMgr root and ``taskInfoMap[3]`` container are resolved only
    once.  Domain projection is pure and happens after the shared task rows have
    been fully decoded, so no domain can accidentally consume a fresher or older
    Runtime view than another one.
    """

    specs = _validated_domain_specs(domains)
    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    stage_timings: dict[str, float] = {}
    failed_stage: str | None = None
    try:
        stage_started = time.perf_counter()
        failed_stage = "process_discovery"
        memory = (
            MumuProcessMemory.discover_cached(max_age_seconds=0.0)
            if force_process_refresh
            else MumuProcessMemory.discover_cached()
        )
        stage_timings["process_discovery_seconds"] = (
            time.perf_counter() - stage_started
        )

        stage_started = time.perf_counter()
        failed_stage = "quest_root_resolution"
        root, cache_hit, resolver = _resolve_quest_root(memory)
        stage_timings["quest_root_resolution_seconds"] = (
            time.perf_counter() - stage_started
        )

        stage_started = time.perf_counter()
        failed_stage = "quest_data_decode"
        reader = LuaJitReader(memory)
        data = _quest_data_fields(reader, root)
        activity_tasks = _fields(
            reader,
            _dictionary_item(reader, data.get("taskInfoMap"), _ACTIVITY_TASK_TYPE),
        )
        if not activity_tasks:
            raise FanxiuRuntimeMemoryError("QuestMgr 活动任务状态尚未加载", code="data_not_loaded")
        stage_timings["quest_data_decode_seconds"] = time.perf_counter() - stage_started

        stage_started = time.perf_counter()
        failed_stage = "activity_task_decode"
        entry_values, entry_list_identity = _list_values_with_identity(
            reader,
            activity_tasks.get("taskEntryVOs"),
        )
        entries = [_serialize_entry(reader, item) for item in entry_values]
        _cache_entry_indices(
            _entry_index_cache_key(
                memory=memory,
                root=root,
                list_identity=entry_list_identity,
            ),
            entries,
        )
        finished = [
            task_id
            for item in _list_values(reader, activity_tasks.get("finishTasks"))
            if (task_id := _positive_int(item)) is not None
        ]
        stage_timings["activity_task_decode_seconds"] = (
            time.perf_counter() - stage_started
        )

        stage_started = time.perf_counter()
        failed_stage = "domain_projection"
        captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
        domain_snapshots = {
            spec.key: {
                "ok": True,
                "available": True,
                "source": "runtime_memory",
                "protocol": "QuestMgr.Model.QuestData.taskInfoMap[3]",
                "captured_at": captured_at,
                **build_activity_task_reward_snapshot(
                    spec=spec,
                    task_entries=entries,
                    finished_task_ids=finished,
                ),
            }
            for spec in specs
        }
        stage_timings["domain_projection_seconds"] = time.perf_counter() - stage_started
        elapsed_seconds = time.perf_counter() - started_at
        evidence = {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root:x}",
            "root_cache_hit": cache_hit,
            "resolver": resolver,
            "shared_activity_task_entry_count": len(entries),
            "shared_finished_task_count": len(finished),
        }
        result = {
            "ok": True,
            "available": True,
            "source": "runtime_memory",
            "protocol": "QuestMgr.Model.QuestData.taskInfoMap[3]",
            "captured_at": captured_at,
            "domain_order": [spec.key for spec in specs],
            "domains": domain_snapshots,
            "complete": all(
                snapshot["complete"] for snapshot in domain_snapshots.values()
            ),
            "elapsed_seconds": elapsed_seconds,
            "stage_timings": stage_timings,
            "evidence": evidence,
        }
        if include_activity_tasks:
            # Internal engineering consumers such as rotating resource-rank
            # activities need the exact live task membership before they can
            # construct a fail-closed domain spec.  This remains a process-
            # external read of the already-loaded QuestMgr table.
            result["task_entries"] = entries
            result["finished_task_ids"] = finished
        return result
    except Exception as exc:
        if failed_stage is not None:
            stage_timings[f"{failed_stage}_failed_seconds"] = (
                time.perf_counter() - stage_started
            )
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
            "domain_order": [spec.key for spec in specs],
            "domains": {
                spec.key: _unavailable_domain_snapshot(spec, reason) for spec in specs
            },
            "reason": reason,
            "failed_stage": failed_stage,
            "elapsed_seconds": time.perf_counter() - started_at,
            "stage_timings": stage_timings,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
            },
        }


def read_all_activity_task_reward_snapshots() -> dict[str, Any]:
    """Read all standard reward domains in one shared Runtime observation."""

    return read_activity_task_reward_snapshots()


def read_activity_task_reward_fast_snapshot(
    domain: str,
    *,
    expected_claimed_task_id: int | None = None,
) -> dict[str, Any]:
    """Re-read one domain by validated task-list slots.

    This is the post-click verification path. It always reads the current
    process identity, QuestMgr root, activity-task list and complete
    ``finishTasks`` list. Only the expected domain's 12/20 task rows are fully
    decoded. A cached slot is trusted only after its current ``taskId`` matches;
    otherwise the task-id index is rebuilt from the current list before any
    snapshot is returned.
    """

    specs = _validated_domain_specs((domain,))
    spec = specs[0]
    if expected_claimed_task_id is not None:
        expected_claimed_task_id = int(expected_claimed_task_id)
        if expected_claimed_task_id not in spec.task_ids:
            raise ValueError(
                f"任务 {expected_claimed_task_id} 不属于奖励域 {domain}"
            )

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    stage_timings: dict[str, float] = {}
    failed_stage: str | None = None
    stage_started = started_at
    try:
        stage_started = time.perf_counter()
        failed_stage = "process_discovery"
        memory = MumuProcessMemory.discover_cached()
        stage_timings["process_discovery_seconds"] = time.perf_counter() - stage_started

        stage_started = time.perf_counter()
        failed_stage = "quest_root_resolution"
        root, cache_hit, resolver = _resolve_quest_root(memory)
        stage_timings["quest_root_resolution_seconds"] = time.perf_counter() - stage_started

        stage_started = time.perf_counter()
        failed_stage = "quest_data_decode"
        reader = LuaJitReader(memory)
        data = _quest_data_fields(reader, root)
        activity_tasks = _fields(
            reader,
            _dictionary_item(reader, data.get("taskInfoMap"), _ACTIVITY_TASK_TYPE),
        )
        if not activity_tasks:
            raise FanxiuRuntimeMemoryError(
                "QuestMgr 活动任务状态尚未加载",
                code="data_not_loaded",
            )
        stage_timings["quest_data_decode_seconds"] = time.perf_counter() - stage_started

        stage_started = time.perf_counter()
        failed_stage = "task_list_read"
        entry_values, entry_list_identity = _list_values_with_identity(
            reader,
            activity_tasks.get("taskEntryVOs"),
        )
        cache_key = _entry_index_cache_key(
            memory=memory,
            root=root,
            list_identity=entry_list_identity,
        )
        stage_timings["task_list_read_seconds"] = time.perf_counter() - stage_started

        stage_started = time.perf_counter()
        failed_stage = "finish_task_decode"
        finished = [
            task_id
            for item in _list_values(reader, activity_tasks.get("finishTasks"))
            if (task_id := _positive_int(item)) is not None
        ]
        finished_in_domain = set(finished).intersection(spec.task_ids)
        stage_timings["finish_task_decode_seconds"] = time.perf_counter() - stage_started

        stage_started = time.perf_counter()
        failed_stage = "entry_index_resolution"
        with _ENTRY_INDEX_CACHE_LOCK:
            cached_mapping = dict(_ENTRY_INDEX_CACHE.get(cache_key, {})) if cache_key else {}
        mapping = cached_mapping
        index_source = "cached"
        if not mapping and expected_claimed_task_id in finished_in_domain:
            derived = _derive_entry_indices_after_claim(
                cache_key,
                expected_claimed_task_id,
            )
            if derived is not None:
                mapping = derived
                index_source = "derived_after_expected_claim"
        # Claimed rows may be removed from taskEntryVOs and represented only in
        # finishTasks. That is a complete current fact, not a missing index.
        required_ids = set(spec.task_ids) - finished_in_domain
        if not required_ids.issubset(mapping):
            mapping = _rebuild_entry_indices(reader, entry_values, cache_key)
            index_source = "rebuilt"
        if not required_ids.issubset(mapping):
            missing = sorted(required_ids - set(mapping))
            raise FanxiuRuntimeMemoryError(
                f"QuestMgr 奖励任务索引不完整: {missing}",
                code="snapshot_incomplete",
            )
        stage_timings["entry_index_resolution_seconds"] = time.perf_counter() - stage_started

        stage_started = time.perf_counter()
        failed_stage = "selected_task_decode"
        entries: list[dict[str, Any]] = []
        slot_mismatch = False
        for task_id in spec.task_ids:
            if task_id in finished_in_domain:
                continue
            index = mapping[task_id]
            if index < 0 or index >= len(entry_values):
                slot_mismatch = True
                break
            entry = _serialize_selected_entry(reader, entry_values[index])
            if _positive_int(entry.get("taskId")) != task_id:
                slot_mismatch = True
                break
            entries.append(entry)
        if slot_mismatch:
            if cache_key is not None:
                with _ENTRY_INDEX_CACHE_LOCK:
                    _ENTRY_INDEX_CACHE.pop(cache_key, None)
            mapping = _rebuild_entry_indices(reader, entry_values, cache_key)
            index_source = "rebuilt_after_mismatch"
            if not required_ids.issubset(mapping):
                raise FanxiuRuntimeMemoryError(
                    "QuestMgr 奖励任务槽位身份校验失败",
                    code="snapshot_incomplete",
                )
            entries = []
            for task_id in spec.task_ids:
                if task_id in finished_in_domain:
                    continue
                entry = _serialize_selected_entry(reader, entry_values[mapping[task_id]])
                if _positive_int(entry.get("taskId")) != task_id:
                    raise FanxiuRuntimeMemoryError(
                        "QuestMgr 奖励任务槽位重建后身份仍不一致",
                        code="snapshot_incomplete",
                    )
                entries.append(entry)
        stage_timings["selected_task_decode_seconds"] = time.perf_counter() - stage_started

        stage_started = time.perf_counter()
        failed_stage = "domain_projection"
        captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
        snapshot = build_activity_task_reward_snapshot(
            spec=spec,
            task_entries=entries,
            finished_task_ids=finished,
        )
        expected_state = next(
            (
                row
                for row in snapshot["tasks"]
                if row["task_id"] == expected_claimed_task_id
            ),
            None,
        )
        stage_timings["domain_projection_seconds"] = time.perf_counter() - stage_started
        return {
            "ok": True,
            "available": True,
            "source": "runtime_memory",
            "protocol": "QuestMgr.Model.QuestData.taskInfoMap[3]",
            "captured_at": captured_at,
            **snapshot,
            "expected_claimed_task_id": expected_claimed_task_id,
            "expected_task_claimed": (
                bool(expected_state and expected_state["claimed"])
                if expected_claimed_task_id is not None
                else None
            ),
            "elapsed_seconds": time.perf_counter() - started_at,
            "stage_timings": stage_timings,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "root_address": f"0x{root:x}",
                "root_cache_hit": cache_hit,
                "resolver": resolver,
                "entry_index_source": index_source,
                "shared_activity_task_entry_count": len(entry_values),
                "selected_task_entry_count": len(entries),
                "shared_finished_task_count": len(finished),
            },
        }
    except Exception as exc:
        if failed_stage is not None:
            stage_timings[f"{failed_stage}_failed_seconds"] = (
                time.perf_counter() - stage_started
            )
        reason = (
            str(exc)
            if isinstance(exc, FanxiuRuntimeMemoryError)
            else f"{type(exc).__name__}: {exc}"
        )
        return {
            **_unavailable_domain_snapshot(spec, reason),
            "expected_claimed_task_id": expected_claimed_task_id,
            "expected_task_claimed": None,
            "failed_stage": failed_stage,
            "elapsed_seconds": time.perf_counter() - started_at,
            "stage_timings": stage_timings,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
            },
        }


def read_activity_task_reward_snapshot(domain: str) -> dict[str, Any]:
    """Read one reward domain, preserving the legacy single-domain shape."""

    batch = read_activity_task_reward_snapshots((domain,))
    snapshot = dict(batch["domains"][domain])
    snapshot["elapsed_seconds"] = batch["elapsed_seconds"]
    snapshot["stage_timings"] = batch["stage_timings"]
    snapshot["evidence"] = batch["evidence"]
    if not batch["ok"]:
        snapshot["failed_stage"] = batch.get("failed_stage")
    return snapshot


__all__ = [
    "LINGMAI_TASK_REWARD_SPEC",
    "LUNDAO_TASK_REWARD_SPEC",
    "QIXI_MOJIE_TASK_REWARD_SPEC",
    "TASK_REWARD_DOMAIN_ORDER",
    "TASK_REWARD_DOMAIN_SPECS",
    "TaskRewardDomainSpec",
    "build_activity_task_reward_snapshot",
    "read_activity_task_reward_fast_snapshot",
    "read_activity_task_reward_snapshot",
    "read_activity_task_reward_snapshots",
    "read_all_activity_task_reward_snapshots",
]
