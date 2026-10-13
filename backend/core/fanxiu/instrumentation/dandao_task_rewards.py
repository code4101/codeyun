from __future__ import annotations

"""Strict read-only QuestMgr projection for the live 丹道问鼎 ladder."""

from typing import Any

from backend.core.fanxiu.catalog.dandao_wending import (
    DANDAO_WENDING_METRIC,
    DandaoUnsupportedTaskMetric,
    resolve_dandao_live_task_ids,
    resolve_dandao_task_targets,
    resolve_dandao_live_score_tasks,
)

from backend.core.fanxiu.instrumentation.daily_task_rewards import (
    TaskRewardDomainSpec,
    build_activity_task_reward_snapshot,
    read_activity_task_reward_snapshots,
)


def read_dandao_task_reward_snapshot(activity_id: int) -> dict[str, Any]:
    """Read the exact current ladder without selecting a retained static variant.

    For an unsupported live score metric, preserve the matched native entries
    and finished IDs as research evidence. These raw observations do not make
    ``complete`` true and never authorize claims or resource consumption.
    """

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
            "reason_code": "unsupported_live_task_metric" if isinstance(exc, DandaoUnsupportedTaskMetric) else "invalid_live_task_ladder",
            "unsupported_task_conditions": exc.conditions if isinstance(exc, DandaoUnsupportedTaskMetric) else {},
            "observed_task_entries": sorted(
                (row for row in entries
                 if isinstance(exc, DandaoUnsupportedTaskMetric)
                 and int(row.get("taskId") or row.get("task_id") or 0) in exc.conditions),
                key=lambda row: int(row.get("taskId") or row.get("task_id") or 0)),
            "observed_finished_task_ids": sorted(
                task_id for task_id in finished
                if isinstance(exc, DandaoUnsupportedTaskMetric) and task_id in exc.conditions),
            "captured_at": shared.get("captured_at"),
            "read_only": True,
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


def read_dandao_score_task_observation(activity_id: int) -> dict[str, Any]:
    """Observe the separate live 积分 ladder; never authorize a game action.

    The server progress/target and exact live membership are authoritative.
    No score-to-resource conversion is inferred. Claim execution still needs
    a verified GUI tab/row binding, so authorized_claim_task_ids stays empty.
    """
    shared = read_activity_task_reward_snapshots((), include_activity_tasks=True)
    base = dict(activity_id=int(activity_id), read_only=True,
                consumption_authorized=False, authorized_claim_task_ids=[],
                captured_at=shared.get('captured_at'), evidence=shared.get('evidence', {}))
    if not shared.get('ok') or not shared.get('available'):
        return dict(base, complete=False, reason=shared.get('reason'))
    entries = shared.get('task_entries') or []
    finished = shared.get('finished_task_ids') or []
    try:
        configs = resolve_dandao_live_score_tasks(int(activity_id),
                    task_entries=entries, finished_task_ids=finished)
        spec = TaskRewardDomainSpec(key=f'dandao_score_{activity_id}', label='丹道积分',
                    activity_id=int(activity_id), task_ids=tuple(row['task_id'] for row in configs),
                    condition_key='ActivityItemScore', thresholds=tuple(row['target'] for row in configs))
        projection = build_activity_task_reward_snapshot(spec=spec,
                    task_entries=entries, finished_task_ids=finished)
        by_id = {int(row.get('taskId') or row.get('task_id') or 0): row for row in entries}
        claimed = set(projection['claimed_task_ids'])
        rows = []
        scores = set()
        for config in configs:
            task_id = config['task_id']
            progress = (by_id.get(task_id) or {}).get('progressList') or []
            if task_id not in claimed:
                if len(progress) != 1 or int(progress[0]['target']) != config['target']:
                    raise ValueError(f'丹道积分任务{task_id}原生目标与本期配置不一致')
                score = int(progress[0]['progress'])
                if score < 0:
                    raise ValueError('丹道积分进度为负数')
                if task_id in projection['pending_task_ids']:
                    scores.add(score)
            else:
                score = None
            rows.append(dict(config, observed_progress=score, claimed=task_id in claimed))
        if len(scores) > 1:
            raise ValueError('丹道积分待完成档次进度不唯一')
        rounds = sorted({row['round'] for row in rows})
        current_round = next((number for number in rounds
            if any(not row['claimed'] for row in rows if row['round'] == number)), None)
        offset = next((row['round_offset'] for row in rows
            if row['round'] == current_round), None)
        raw_score = next(iter(scores)) if scores else None
        return dict(base, complete=projection['complete'], rows=rows,
                    current_score=raw_score, current_round=current_round,
                    current_round_score=max(0, raw_score-offset) if raw_score is not None and offset is not None else None,
                    claimable_task_ids=projection['claimable_task_ids'],
                    claimed_task_ids=projection['claimed_task_ids'],
                    pending_task_ids=projection['pending_task_ids'])
    except (ValueError, KeyError, TypeError) as exc:
        return dict(base, complete=False, reason=str(exc))


__all__ = ["read_dandao_task_reward_snapshot", "read_dandao_score_task_observation"]
