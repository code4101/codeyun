from __future__ import annotations

"""Strict read-only QuestMgr projection for the live 丹道问鼎 ladder."""

from typing import Any

from backend.core.fanxiu.activity.dandao_wending import (
    DANDAO_WENDING_METRIC,
    resolve_dandao_live_task_ids,
)
from backend.core.fanxiu.instrumentation.daily_task_rewards import (
    TaskRewardDomainSpec,
    build_activity_task_reward_snapshot,
    read_activity_task_reward_snapshots,
)


def read_dandao_task_reward_snapshot(activity_id: int) -> dict[str, Any]:
    """Read the exact current ladder without selecting a retained static variant."""

    shared = read_activity_task_reward_snapshots(
        (),
        include_activity_tasks=True,
    )
    process_refresh_retry = False
    if not shared.get("ok") and shared.get("failed_stage") in {
        "quest_root_resolution",
        "quest_data_decode",
        "activity_task_decode",
    }:
        process_refresh_retry = True
        shared = read_activity_task_reward_snapshots(
            (),
            include_activity_tasks=True,
            force_process_refresh=True,
        )
    if not shared.get("ok") or not shared.get("available"):
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "activity_id": int(activity_id),
            "authorized_claim_task_ids": [],
            "reason": str(shared.get("reason") or "QuestMgr 活动任务状态不可用"),
            "evidence": {
                **dict(shared.get("evidence") or {}),
                "process_refresh_retry": process_refresh_retry,
            },
        }
    entries = [
        dict(row)
        for row in shared.get("task_entries") or []
        if isinstance(row, dict)
    ]
    finished = [int(value) for value in shared.get("finished_task_ids") or []]
    try:
        task_ids = resolve_dandao_live_task_ids(
            int(activity_id),
            task_entries=entries,
            finished_task_ids=finished,
        )
    except (TypeError, ValueError) as exc:
        return {
            "ok": False,
            "available": True,
            "complete": False,
            "activity_id": int(activity_id),
            "authorized_claim_task_ids": [],
            "reason": str(exc),
            "evidence": {
                **dict(shared.get("evidence") or {}),
                "process_refresh_retry": process_refresh_retry,
            },
        }

    spec = TaskRewardDomainSpec(
        key=f"dandao_{int(activity_id)}",
        label="丹道问鼎",
        activity_id=int(activity_id),
        task_ids=task_ids,
        condition_key=DANDAO_WENDING_METRIC,
        thresholds=tuple(range(1, len(task_ids) + 1)),
    )
    projection = build_activity_task_reward_snapshot(
        spec=spec,
        task_entries=entries,
        finished_task_ids=finished,
    )
    return {
        "ok": True,
        "available": True,
        "source": "runtime_memory",
        "protocol": shared.get("protocol"),
        "captured_at": shared.get("captured_at"),
        **projection,
        "evidence": {
            **dict(shared.get("evidence") or {}),
            "membership": "QuestMgr taskEntryVOs + finishTasks joined to ActiveTask",
            "process_refresh_retry": process_refresh_retry,
        },
    }


__all__ = ["read_dandao_task_reward_snapshot"]
