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
    read_task_reward_spec_fast_snapshot,
)

SHEQUN_LIANTI_ACTIVITY_ID = 8043401

# Cache only the verified ladder identity, never addresses, task progress or
# claimed state. Every read still validates the live period and task slots.
# Keep one period/process/configuration revision, so another occurrence must
# establish complete native membership before using the shared fast reader.
_VERIFIED_PERIOD_SPECS: dict[tuple, TaskRewardDomainSpec] = {}


def build_shequn_lianti_claim_reward_items(before, after):
    """Declared items of newly verified claims, never measured bag increments.

    Bind both observations to one exact occurrence/ladder and require unchanged
    reward definitions. Historical finished tasks must not trigger new work.
    Unknown reward syntax fails closed rather than guessing an item identity.
    """
    if (not before.get('complete') or not after.get('complete')
            or not before.get('occurrence_key')
            or before.get('occurrence_key') != after.get('occurrence_key')
            or set(before.get('task_ids') or []) != set(after.get('task_ids') or [])):
        raise ValueError('社团炼体领奖奖励必须属于同一完整实例')
    ladder = set(after['task_ids'])
    prior = set(before.get('claimed_task_ids') or [])
    current = set(after.get('claimed_task_ids') or [])
    if not prior <= current <= ladder:
        raise ValueError('社团炼体领奖任务身份或累计凭证不一致')
    new_ids = sorted(current - prior)
    if not set(new_ids) <= set(before.get('claimable_task_ids') or []):
        raise ValueError('社团炼体新增领奖超出原已达标档次')
    rewards = []
    for task_id in new_ids:
        key = str(task_id)
        tokens = (after.get('task_reward_tokens') or {}).get(key)
        if not tokens or tokens != (before.get('task_reward_tokens') or {}).get(key):
            raise ValueError('社团炼体领奖奖励配置缺失或变化')
        for token in tokens:
            match = re.fullmatch(r'Item\|([1-9]\d*)_([1-9]\d*)(?:_([1-9]\d*))?', str(token))
            if match is None:
                raise ValueError('社团炼体领奖奖励格式尚未支持')
            rewards.append({'task_id': task_id, 'item_id': int(match[1]),
                            'quantity': int(match[2]), 'source_token': token})
    return {'claimed_now_task_ids': new_ids, 'declared_reward_items': rewards,
            'claimed_reward_item_ids': sorted({row['item_id'] for row in rewards})}


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
    global _VERIFIED_PERIOD_SPECS
    period = read_activity_period_runtime_snapshot(SHEQUN_LIANTI_ACTIVITY_ID, runtime_id=runtime_id)
    now = datetime.now().astimezone()
    if not period["start_time_ms"] <= now.timestamp() * 1000 <= period["end_time_ms"]:
        return {"ok": True, "available": False, "complete": False,
                "reason": "outside_activity_window", "period": period,
                "authorized_claim_task_ids": [], "goal_completed": False}
    root = resolve_fanxiu_export_root()
    config_path = root / "parsed_configs/ActiveTask/rows.json"
    identity = period.get('evidence') or {}
    period_key = (period['activity_id'], period['runtime_id'],
                  period['start_time_ms'], period['end_time_ms'],
                  identity.get('pid'), identity.get('process_start_ticks'),
                  config_path.stat().st_mtime_ns)
    cached_spec = _VERIFIED_PERIOD_SPECS.get(period_key)
    read_path = 'full_membership'
    if cached_spec is not None:
        shared = read_task_reward_spec_fast_snapshot(cached_spec, include_task_entries=True)
        if shared.get('ok') and shared.get('available') and shared.get('complete'):
            read_path = 'verified_period_spec'
        else:
            # A missing/changed slot is a reason to rediscover membership,
            # never to reuse old progress or infer already-claimed rewards.
            _VERIFIED_PERIOD_SPECS = {}
            shared = read_activity_task_reward_snapshots((), include_activity_tasks=True)
    else:
        shared = read_activity_task_reward_snapshots((), include_activity_tasks=True)
    if not (shared.get("ok") and shared.get("available") and shared.get("complete")):
        return {"ok": False, "complete": False, "goal_completed": False,
                "authorized_claim_task_ids": [], "period": period,
                "reason": shared.get("reason") or "QuestMgr 任务事实不完整"}
    raw = json.loads(config_path.read_text(encoding="utf-8"))
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
    if identity.get('pid') is not None and identity.get('process_start_ticks') is not None:
        _VERIFIED_PERIOD_SPECS = {period_key: spec}
    return {**result, "ok": True, "available": True, "period": period,
            "captured_at": shared.get("captured_at"),
            "source": "runtime_memory.QuestMgr+ActiveTask",
            "evidence": shared.get("evidence"),
            "task_read_path": read_path,
            "task_read_elapsed_seconds": shared.get('elapsed_seconds'),
            "target_score": max(spec.thresholds), "task_ids": list(spec.task_ids),
            "task_reward_tokens": {str(row['id']): list(row.get('reward') or [])
                                   for row in rows if int(row.get('id') or 0) in spec.task_ids},
            "task_entries": selected_entries, "activity_progress": score,
            "progress_is_exact": bool(pending),
            "score_deficit": max(0, max(spec.thresholds) - score),
            "goal_completed": result["complete"] and result["state"] == "already_claimed"}


__all__ = ["build_shequn_lianti_task_spec", "read_shequn_lianti_task_reward_snapshot",
           "build_shequn_lianti_claim_reward_items"]
