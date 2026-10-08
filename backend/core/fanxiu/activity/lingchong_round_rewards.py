"""灵宠积分轮次：任务领取和轮次礼包分别核验，配置决定每轮目标。"""
from __future__ import annotations

import json
from functools import lru_cache

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.daily_task_rewards import (
    TaskRewardDomainSpec, read_task_reward_spec_fast_snapshot,
)
from backend.core.fanxiu.instrumentation.weekly_activity import read_activity_round_box_snapshot


@lru_cache(maxsize=8)
def lingchong_round_config(activity_id: int):
    root = resolve_fanxiu_export_root() / "parsed_configs"
    tasks = json.loads((root / "ActiveTask/rows.json").read_text(encoding="utf-8"))
    boxes = json.loads((root / "ActiveProgress/rows.json").read_text(encoding="utf-8"))
    return ([r for r in tasks if r.get("activityId") == activity_id and r.get("subType") == 999],
            [r for r in boxes if r.get("activityId") == activity_id])


def build_lingchong_round_rewards(tasks, boxes, quest, box_state):
    """Fail closed on missing current tasks; never advance past an unclaimed box."""
    if not quest.get("ok") or not box_state.get("complete"):
        raise RuntimeError("灵宠轮次任务或礼包事实不可读")
    entries = {int(r["taskId"]): r for r in quest.get("task_entries", [])}
    config_by_id = {r["id"]: r for r in tasks}
    claimed = set(quest.get("finished_task_ids", []))
    box_claimed = set(box_state["claimed_config_ids"])
    if not tasks or not boxes or not box_claimed.issubset({r["id"] for r in boxes}):
        raise RuntimeError("灵宠轮次配置或礼包身份不完整")
    rounds = []
    for box in sorted(boxes, key=lambda r: r["grade"]):
        number = int(box["grade"])
        rows = [r for r in tasks if r.get("times") == number]
        offsets = {int(r.get("sub") or 0) for r in rows}
        if len(rows) != 10 or len(offsets) != 1:
            raise RuntimeError(f"灵宠第{number}轮任务配置不完整")
        offset = offsets.pop()
        target = max(int(r["finishCondition"][0].split("_")[1]) for r in rows)
        all_claimed = all(r["id"] in claimed or entries.get(r["id"], {}).get("status") == 5 for r in rows)
        rounds.append({"round": number, "target": target - offset,
                       "cumulative_target": target, "task_ids": [r["id"] for r in rows],
                       "tasks_claimed": all_claimed, "box_id": box["id"],
                       "box_claimed": box["id"] in box_claimed,
                       "box_claimable": box["process"] <= box_state["active_num"] and box["id"] not in box_claimed,
                       "rewards": box["reward"]})
    current = next((r for r in rounds if not (r["tasks_claimed"] and r["box_claimed"])), None)
    current_tasks = []
    if current:
        for task_id in current["task_ids"]:
            row = entries.get(task_id, {})
            done = task_id in claimed or row.get("status") == 5
            progress = row.get("progressList") or []
            if not done and not progress:
                raise RuntimeError(f"灵宠积分任务{task_id}尚未加载")
            current_tasks.append({"task_id": task_id, "claimed": done,
                                  "name": config_by_id[task_id].get('name_plain') or config_by_id[task_id].get('name'),
                                  "target": int(config_by_id[task_id]["finishCondition"][0].split("_")[1]),
                                  "progress": int(progress[0]["progress"]) if progress else int(config_by_id[task_id]["finishCondition"][0].split("_")[1]),
                                  "claimable": not done and (row.get("status") == 4 or bool(progress[0].get("finish")))})
    return {"ok": True, "complete": True, "rounds": rounds,
            "current_round": current["round"] if current else len(rounds) + 1,
            "current_tasks": current_tasks,
            "score": max((r["progress"] for r in current_tasks), default=rounds[-1]["cumulative_target"]),
            "score_is_lower_bound": current is None or all(r["claimed"] for r in current_tasks),
            "all_claimed": current is None, "box_state": box_state}


def read_lingchong_round_rewards(activity_id: int):
    """Read the exact activity's Quest rows and independent receiveBoxList."""
    tasks, boxes = lingchong_round_config(int(activity_id))
    spec = TaskRewardDomainSpec(key="lingchong_score", label="灵宠积分轮次",
                               activity_id=int(activity_id), task_ids=tuple(r["id"] for r in tasks),
                               condition_key="", thresholds=())
    quest = read_task_reward_spec_fast_snapshot(spec, include_task_entries=True)
    result = build_lingchong_round_rewards(tasks, boxes, quest,
                                           read_activity_round_box_snapshot(activity_id))
    return {**result, "activity_id": int(activity_id), "captured_at": quest.get("captured_at")}
