from __future__ import annotations

"""Strict read-only QuestMgr projection for the live 丹道问鼎 ladder."""

from typing import Any

from backend.core.fanxiu.catalog.dandao_wending import (
    DANDAO_WENDING_METRIC,
    resolve_dandao_live_task_ids,
    resolve_dandao_task_targets,
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

    targets = resolve_dandao_task_targets(int(activity_id), task_ids)
    spec = TaskRewardDomainSpec(
        key=f"dandao_{int(activity_id)}",
        label="丹道问鼎",
        activity_id=int(activity_id),
        task_ids=task_ids,
        condition_key=DANDAO_WENDING_METRIC,
        thresholds=targets,
    )
    projection = build_activity_task_reward_snapshot(
        spec=spec,
        task_entries=entries,
        finished_task_ids=finished,
    )
    pending_ids = {int(value) for value in projection.get("pending_task_ids") or []}
    pending_progress = {
        int(progress.get("progress"))
        for row in entries
        if int(row.get("taskId") or row.get("task_id") or 0) in pending_ids
        for progress in row.get("progressList") or []
        if progress.get("progress") is not None
    }
    # Pending milestones expose the same current MedicalExp. Completed rows
    # may disappear from taskEntryVOs or cap at their own target, so they are
    # not an authoritative current-progress source.
    if pending_ids and len(pending_progress) != 1:
        return {
            "ok": False,
            "available": True,
            "complete": False,
            "activity_id": int(activity_id),
            "authorized_claim_task_ids": [],
            "reason": f"丹道问鼎待完成任务的本期熟练度读数不唯一：{sorted(pending_progress)}",
        }
    return {
        "ok": True,
        "available": True,
        "source": "runtime_memory",
        "protocol": shared.get("protocol"),
        "captured_at": shared.get("captured_at"),
        **projection,
        "activity_progress": next(iter(pending_progress)) if pending_progress else None,
        "task_target": targets[-1],
        "all_tasks_complete": bool(projection.get("complete") and not pending_ids),
        "evidence": {
            **dict(shared.get("evidence") or {}),
            "membership": "QuestMgr taskEntryVOs + finishTasks joined to ActiveTask",
            "process_refresh_retry": process_refresh_retry,
        },
    }


__all__ = ["read_dandao_task_reward_snapshot"]
