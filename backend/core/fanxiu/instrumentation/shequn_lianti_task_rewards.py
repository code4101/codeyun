"""Read the current 社团炼体 reward ladder, without operating the game.

Static configuration retains multiple ladders. Only the complete live QuestMgr
membership selects one; rank position and a static 必拿 label do not replace
the owner's task-reward target. Production execution remains unregistered until
the page and resource-use path pass live acceptance.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Mapping, Sequence

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from .activity_runtime import read_activity_period_runtime_snapshot
from .daily_task_rewards import (
    TaskRewardDomainSpec, build_activity_task_reward_snapshot,
    read_activity_task_reward_snapshots,
)

SHEQUN_LIANTI_ACTIVITY_ID = 8043401


def build_shequn_lianti_task_spec(
    config_rows: Sequence[Mapping[str, Any]], *,
    task_entries: Sequence[Mapping[str, Any]], finished_task_ids: Sequence[int],
) -> TaskRewardDomainSpec:
    """Join exact live IDs, rejecting mixed variants and incomplete ladders."""
    candidates = {}
    for row in config_rows:
        if int(row.get("activityId") or 0) != SHEQUN_LIANTI_ACTIVITY_ID:
            continue
        task_id = int(row.get("id") or 0)
        conditions = row.get("finishCondition") or []
        match = (re.fullmatch(r"PhysicalFightScore\|(\d+)", str(conditions[0]))
                 if len(conditions) == 1 else None)
        if task_id <= 0 or task_id in candidates or match is None:
            raise ValueError("社团炼体任务配置身份或计分条件无效")
        target, order = int(match[1]), int(row.get("sort") or 0)
        if target <= 0 or order <= 0:
            raise ValueError("社团炼体任务门槛或顺序无效")
        candidates[task_id] = (target, order)
    represented = {int(value) for value in finished_task_ids}
    represented.update(int(row.get("taskId") or row.get("task_id") or 0)
                       for row in task_entries)
    selected = sorted((target, order, task_id)
                      for task_id, (target, order) in candidates.items()
                      if task_id in represented)
    if not selected:
        raise ValueError("本期社团炼体任务尚未加载")
    targets = [row[0] for row in selected]
    orders = sorted(row[1] for row in selected)
    if (len(set(targets)) != len(targets)
            or orders != list(range(1, len(selected) + 1))
            or max(targets) != max(target for target, _ in candidates.values())):
        raise ValueError("社团炼体本期梯度不完整或混入多套版本")
    target_by_id = {task_id: target for target, _, task_id in selected}
    for entry in task_entries:
        task_id = int(entry.get("taskId") or entry.get("task_id") or 0)
        if task_id not in target_by_id:
            continue
        progress = entry.get("progressList") or []
        if (len(progress) != 1
                or int(progress[0].get("target") or 0) != target_by_id[task_id]
                or progress[0].get("progress") is None
                or not 0 <= int(progress[0]["progress"]) <= target_by_id[task_id]):
            raise ValueError("社团炼体实时任务门槛与配置不一致")
    return TaskRewardDomainSpec(
        key=f"shequn_lianti_{SHEQUN_LIANTI_ACTIVITY_ID}", label="社团炼体",
        activity_id=SHEQUN_LIANTI_ACTIVITY_ID,
        task_ids=tuple(row[2] for row in selected),
        condition_key="PhysicalFightScore", thresholds=tuple(targets),
    )


def read_shequn_lianti_task_reward_snapshot(*, runtime_id: int | None = None) -> dict[str, Any]:
    """Read only in the exact live period; before opening return zero actions.

    The opening is 05:00:05 in the current period, not an inferred 05:00. All
    reward tiers must be claimed for goal completion; reaching score alone is
    an intermediate fact. No rank, purchase, resource use or GUI action is
    authorized by this observation.
    """
    period = read_activity_period_runtime_snapshot(SHEQUN_LIANTI_ACTIVITY_ID, runtime_id=runtime_id)
    now = datetime.now().astimezone()
    if not period["start_time_ms"] <= now.timestamp() * 1000 <= period["end_time_ms"]:
        return {"ok": True, "available": False, "complete": False,
                "reason": "outside_activity_window", "period": period,
                "authorized_claim_task_ids": [], "goal_completed": False}
    shared = read_activity_task_reward_snapshots((), include_activity_tasks=True)
    if not (shared.get("ok") and shared.get("available") and shared.get("complete")):
        return {"ok": False, "complete": False, "goal_completed": False,
                "authorized_claim_task_ids": [], "period": period,
                "reason": shared.get("reason") or "QuestMgr 任务事实不完整"}
    root = resolve_fanxiu_export_root()
    raw = json.loads((root / "parsed_configs/ActiveTask/rows.json").read_text(encoding="utf-8"))
    rows = raw if isinstance(raw, list) else raw.get("rows", raw)
    rows = list(rows.values()) if isinstance(rows, dict) else rows
    entries = shared.get("task_entries") or []
    finished = shared.get("finished_task_ids") or []
    spec = build_shequn_lianti_task_spec(rows, task_entries=entries, finished_task_ids=finished)
    result = build_activity_task_reward_snapshot(
        spec=spec, task_entries=entries, finished_task_ids=finished)
    selected_entries = [dict(row) for row in entries
                        if int(row.get("taskId") or 0) in spec.task_ids]
    pending = set(result["pending_task_ids"])
    pending_scores = {int(row["progressList"][0]["progress"])
                      for row in selected_entries if int(row["taskId"]) in pending}
    if pending and len(pending_scores) != 1:
        raise RuntimeError("社团炼体未完成任务的实时积分不一致")
    # Claimed/finished rows can cap their displayed progress at their target.
    # Pending rows expose the live common score. With none pending, the target
    # is only a proven lower bound; no additional resource use is needed.
    score = next(iter(pending_scores)) if pending else max(spec.thresholds)
    after = read_activity_period_runtime_snapshot(SHEQUN_LIANTI_ACTIVITY_ID, runtime_id=runtime_id)
    if after != period:
        raise RuntimeError("社团炼体任务读取期间活动实例或游戏进程变化")
    return {**result, "ok": True, "available": True, "period": period,
            "captured_at": shared.get("captured_at"),
            "source": "runtime_memory.QuestMgr+ActiveTask",
            "evidence": shared.get("evidence"),
            "target_score": max(spec.thresholds), "task_ids": list(spec.task_ids),
            "task_entries": selected_entries, "activity_progress": score,
            "progress_is_exact": bool(pending),
            "score_deficit": max(0, max(spec.thresholds) - score),
            "goal_completed": result["complete"] and result["state"] == "already_claimed"}


__all__ = ["build_shequn_lianti_task_spec", "read_shequn_lianti_task_reward_snapshot"]
