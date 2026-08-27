from __future__ import annotations

"""Occurrence-specific QuestMgr facts for Tiandi Yiju task rewards."""

import json
from functools import lru_cache
from typing import Any, Mapping

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.daily_task_rewards import (
    TaskRewardDomainSpec,
    build_activity_task_reward_snapshot,
    read_activity_task_reward_snapshots,
)


_RUNTIME_TO_TASK_ACTIVITY_ID = {
    8090001: 8090001,  # 本服预赛
    8090004: 8090002,  # 8 跨棋盘；8090002 本身只是分组/赛程面
}


@lru_cache(maxsize=1)
def _active_task_rows() -> tuple[dict[str, Any], ...]:
    path = resolve_fanxiu_export_root() / "parsed_configs/ActiveTask/rows.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError("ActiveTask 静态配置不是列表")
    return tuple(dict(row) for row in rows if isinstance(row, Mapping))


def tiandi_yiju_task_activity_id(activity_id: int) -> int:
    """Map a playable Runtime occurrence to its ActiveTask configuration."""

    try:
        return _RUNTIME_TO_TASK_ACTIVITY_ID[int(activity_id)]
    except KeyError as exc:
        raise RuntimeError(f"天地弈局 activityId={activity_id} 不是可操作棋盘实例") from exc


def build_tiandi_yiju_task_reward_snapshot(
    *,
    activity_id: int,
    task_entries: list[dict[str, Any]],
    finished_task_ids: list[int],
) -> dict[str, Any]:
    """Select exactly one live score ladder and the shared cultivation ladder."""

    try:
        task_activity_id = tiandi_yiju_task_activity_id(activity_id)
    except RuntimeError as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "state": "unavailable",
            "reason": str(exc),
            "authorized_claim_task_ids": [],
        }

    rows = [
        row
        for row in _active_task_rows()
        if int(row.get("activityId") or 0) == task_activity_id
        and int(row.get("type") or 0) == 3
        and int(row.get("subType") or 0) in {6, 7}
    ]
    static_by_id: dict[int, dict[str, Any]] = {}
    duplicate_ids: set[int] = set()
    for row in rows:
        task_id = int(row.get("id") or 0)
        if task_id <= 0:
            continue
        if task_id in static_by_id:
            duplicate_ids.add(task_id)
        static_by_id[task_id] = row
    if duplicate_ids:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "state": "ambiguous",
            "reason": f"ActiveTask 天地弈局任务 ID 重复：{min(duplicate_ids)}",
            "authorized_claim_task_ids": [],
        }

    represented_ids = {
        int(entry.get("taskId") or entry.get("task_id") or 0)
        for entry in task_entries
        if int(entry.get("taskId") or entry.get("task_id") or 0) in static_by_id
    }
    represented_ids.update(
        int(task_id) for task_id in finished_task_ids if int(task_id) in static_by_id
    )
    logical_slots: dict[tuple[int, int], list[int]] = {}
    for task_id, row in static_by_id.items():
        slot = (int(row.get("subType") or 0), int(row.get("sort") or 0))
        logical_slots.setdefault(slot, []).append(task_id)

    live_ids: list[int] = []
    missing_slots: list[tuple[int, int]] = []
    ambiguous_slots: list[tuple[int, int]] = []
    for slot, candidates in sorted(logical_slots.items()):
        represented = sorted(set(candidates) & represented_ids)
        if not represented:
            missing_slots.append(slot)
        elif len(represented) > 1:
            ambiguous_slots.append(slot)
        else:
            live_ids.append(represented[0])
    if missing_slots:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "state": "unavailable",
            "reason": (
                f"QuestMgr 未完整加载 activityId={task_activity_id} 的天地弈局任务档位："
                f"{missing_slots[0]}"
            ),
            "authorized_claim_task_ids": [],
        }
    if ambiguous_slots:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "state": "ambiguous",
            "reason": f"QuestMgr 同时命中多个天地弈局任务版本：{ambiguous_slots[0]}",
            "authorized_claim_task_ids": [],
        }

    ordered_ids = tuple(
        sorted(
            live_ids,
            key=lambda task_id: (
                int(static_by_id[task_id].get("subType") or 0),
                int(static_by_id[task_id].get("sort") or 0),
                task_id,
            ),
        )
    )
    spec = TaskRewardDomainSpec(
        key=f"tiandi_yiju_{activity_id}",
        label="天地弈局",
        activity_id=task_activity_id,
        task_ids=ordered_ids,
        condition_key="AlliancePlayChessVersioned",
        thresholds=tuple(0 for _ in ordered_ids),
    )
    snapshot = build_activity_task_reward_snapshot(
        spec=spec,
        task_entries=task_entries,
        finished_task_ids=finished_task_ids,
    )
    snapshot.update(
        {
            "ok": True,
            "available": True,
            "runtime_activity_id": int(activity_id),
            "task_activity_id": task_activity_id,
            "source": "runtime_memory",
            "protocol": "QuestMgr.Model.QuestData.taskInfoMap[3]",
            "task_subtypes": {
                str(task_id): int(static_by_id[task_id].get("subType") or 0)
                for task_id in ordered_ids
            },
            "task_names": {
                str(task_id): str(
                    static_by_id[task_id].get("name_plain")
                    or static_by_id[task_id].get("name")
                    or ""
                )
                for task_id in ordered_ids
            },
        }
    )
    return snapshot


def read_tiandi_yiju_task_reward_snapshot(
    activity_id: int,
    *,
    expected_claimed_task_id: int | None = None,
) -> dict[str, Any]:
    shared = read_activity_task_reward_snapshots((), include_activity_tasks=True)
    if not shared.get("ok") or not shared.get("available"):
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "state": "unavailable",
            "reason": str(shared.get("reason") or "QuestMgr 读取失败"),
            "authorized_claim_task_ids": [],
        }
    snapshot = build_tiandi_yiju_task_reward_snapshot(
        activity_id=int(activity_id),
        task_entries=list(shared.get("task_entries") or []),
        finished_task_ids=list(shared.get("finished_task_ids") or []),
    )
    if expected_claimed_task_id is not None:
        expected = int(expected_claimed_task_id)
        snapshot["expected_task_claimed"] = expected in {
            int(value) for value in snapshot.get("claimed_task_ids") or []
        }
    return snapshot


__all__ = [
    "build_tiandi_yiju_task_reward_snapshot",
    "read_tiandi_yiju_task_reward_snapshot",
    "tiandi_yiju_task_activity_id",
]
