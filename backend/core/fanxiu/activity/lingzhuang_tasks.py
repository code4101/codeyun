"""本期灵装任务契约：配置决定档次/轮次，Quest 决定当前进度和已领事实。"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.daily_task_rewards import (
    TaskRewardDomainSpec, read_task_reward_spec_fast_snapshot, read_activity_task_reward_snapshots,
)

_BOUND_TASK_SPECS: dict[int, TaskRewardDomainSpec] = {}


@lru_cache(maxsize=16)
def lingzhuang_task_config(activity_id: int) -> tuple[dict[str, Any], ...]:
    rows = json.loads((resolve_fanxiu_export_root() / "parsed_configs/ActiveTask/rows.json").read_text(encoding="utf-8"))
    selected = tuple(row for row in rows if int(row.get("activityId") or 0) == activity_id
                     and int(row.get("subType") or 0) in (13, 999))
    if not selected:
        raise RuntimeError(f"本期灵装任务配置缺失：{activity_id}")
    return selected


def task_threshold(row: dict[str, Any]) -> int:
    condition = row["finishCondition"][0].split("|", 1)
    return int(condition[1].split("_")[1] if condition[0] == "ActivityItemScore" else condition[1])


def build_lingzhuang_task_progress(config, shared: dict[str, Any]) -> dict[str, Any]:
    """纯投影；替换奖励 ID 按同一消耗档次归并，缺失档次失败关闭。"""
    entries = {int(row["taskId"]): row for row in shared.get("task_entries", [])}
    claimed = {int(value) for value in shared.get("finished_task_ids", [])}
    groups: dict[int, list[dict[str, Any]]] = {}
    for row in config:
        if row["subType"] == 13:
            groups.setdefault(task_threshold(row), []).append(row)
    equipment = []
    for order, (threshold, alternatives) in enumerate(sorted(groups.items()), 1):
        present = [row for row in alternatives if row["id"] in entries]
        rewarded = [row for row in alternatives if row["id"] in claimed]
        if len(present) > 1 or not (present or rewarded):
            raise RuntimeError(f"灵装装备档次 {threshold} 缺失或存在歧义")
        row = (present or rewarded)[0]
        entry = entries.get(row["id"], {})
        progress = entry.get("progressList") or []
        done = row["id"] in claimed or int(entry.get("status") or 0) == 5
        equipment.append(dict(task_id=row["id"], order=order, name=row["name"], target=threshold,
                              progress=int(progress[0]["progress"]) if progress else threshold,
                              finished=done or bool(progress and progress[0].get("finish")), claimed=done))
    score_groups: dict[int, list[dict[str, Any]]] = {}
    for row in config:
        if row["subType"] == 999:
            score_groups.setdefault(int(row["times"]), []).append(row)
    rounds = []
    score_tasks = []
    current_round = None
    for number, rows in sorted(score_groups.items()):
        offsets = {int(row.get("sub") or 0) for row in rows}
        if len(offsets) != 1 or len(rows) != 10:
            raise RuntimeError(f"灵装第 {number} 轮配置不完整")
        offset = offsets.pop()
        rounds.append(dict(round=number, target=max(task_threshold(row) for row in rows) - offset))
        if current_round is not None:
            continue
        projected = []
        for row in sorted(rows, key=lambda row: row["sort"]):
            entry = entries.get(row["id"], {})
            if not entry and row["id"] not in claimed:
                raise RuntimeError(f"灵装积分任务 {row['id']} 尚未加载")
            progress = entry.get("progressList") or []
            done = row["id"] in claimed or int(entry.get("status") or 0) == 5
            projected.append(dict(task_id=row["id"], order=row["sort"], name=row["name"],
                                 target=task_threshold(row) - offset,
                                 progress=max(0, int(progress[0]["progress"]) - offset) if progress else task_threshold(row) - offset,
                                 finished=done or bool(progress and progress[0].get("finish")), claimed=done))
        if not all(row["claimed"] for row in projected):
            current_round, score_tasks = number, projected
    if rounds and current_round is None:
        current_round = len(rounds) + 1
    return dict(equipment_tasks=equipment, equipment_current=max(row["progress"] for row in equipment),
                score_round=current_round, score_total_rounds=len(rounds), score_rounds=rounds,
                score_tasks=score_tasks, score_current=max((row["progress"] for row in score_tasks), default=0 if rounds else None),
                evidence={"equipment_only_phase": not rounds, "quest": shared.get("evidence", {})})


def read_lingzhuang_task_progress(activity_id: int) -> dict[str, Any]:
    config = lingzhuang_task_config(int(activity_id))
    spec = _BOUND_TASK_SPECS.get(activity_id)
    shared = read_task_reward_spec_fast_snapshot(spec, include_task_entries=True) if spec else {}
    if not shared.get("ok"):
        shared = read_activity_task_reward_snapshots((), include_activity_tasks=True)
    if not shared.get("ok"):
        raise RuntimeError(f"灵装任务读取失败：{shared.get('reason')}")
    try:
        facts = build_lingzhuang_task_progress(config, shared)
    except RuntimeError:
        if not spec:
            raise
        # 领奖推进到下一轮后，旧轮绑定不含新轮进度；重新发现一次。
        shared = read_activity_task_reward_snapshots((), include_activity_tasks=True)
        if not shared.get("ok"):
            raise RuntimeError(f"灵装任务读取失败：{shared.get('reason')}")
        facts = build_lingzhuang_task_progress(config, shared)
    # finishTasks 每次完整读取，足以判定已领轮次；只解码当前轮的
    # 十档进度，避免每次强化都远程遍历未来七轮的七十个任务。
    ids = tuple(row["task_id"] for row in facts["equipment_tasks"]) + tuple(row["task_id"] for row in facts["score_tasks"])
    _BOUND_TASK_SPECS[activity_id] = TaskRewardDomainSpec(key="lingzhuang_all", label="灵装化道", activity_id=activity_id,
                                                       task_ids=ids, condition_key="", thresholds=())
    return {**facts, "captured_at": shared["captured_at"]}
