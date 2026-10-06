"""Read the current Xutian reward ladder from loaded QuestMgr facts.

Exploration has two configured versions with the same logical slots. Runtime
membership selects the current version; a missing or duplicate slot authorizes
no claim. Reads never open a page or mutate the game.
"""

import json
from functools import lru_cache
from typing import Any

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.daily_task_rewards import (
    TaskRewardDomainSpec,
    build_activity_task_reward_snapshot,
    read_activity_task_reward_snapshots,
)


@lru_cache(maxsize=1)
def _task_rows():
    return json.loads((resolve_fanxiu_export_root() / "parsed_configs/ActiveTask/rows.json").read_text(encoding="utf-8"))


def build_xutian_task_reward_snapshot(*, activity_id: int, task_entries: list[dict[str, Any]], finished_task_ids: list[int]) -> dict[str, Any]:
    """Require one represented task for every configured cultivation/explore slot."""
    rows = [r for r in _task_rows() if int(r.get("activityId") or 0) == activity_id and int(r.get("type") or 0) == 3]
    represented = {int(r.get("taskId") or r.get("task_id") or 0) for r in task_entries} | set(map(int, finished_task_ids))
    slots = {}
    by_id = {}
    for row in rows:
        task_id = int(row["id"])
        if task_id in by_id or int(row.get("subType") or 0) not in {2, 4}:
            raise RuntimeError("虚天 ActiveTask 配置身份重复或类型未知")
        by_id[task_id] = row
        slots.setdefault((int(row["subType"]), int(row["sort"])), []).append(task_id)
    selected = []
    for slot, candidates in sorted(slots.items()):
        live = set(candidates) & represented
        if len(live) != 1:
            return {"ok": False, "available": False, "complete": False, "authorized_claim_task_ids": [], "reason": f"虚天任务档位 {slot} 的当前版本数={len(live)}"}
        selected.append(next(iter(live)))
    if not slots or {slot[0] for slot in slots} != {2, 4}:
        raise RuntimeError("虚天任务配置缺少完整修炼/探索页")
    snapshot = build_activity_task_reward_snapshot(
        spec=TaskRewardDomainSpec(key=f"xutian_{activity_id}", label="虚天殿", activity_id=activity_id, task_ids=tuple(selected), condition_key="XutianVersioned", thresholds=tuple(0 for _ in selected)),
        task_entries=task_entries, finished_task_ids=finished_task_ids,
    )
    snapshot.update(ok=True, available=True, source="runtime_memory", task_subtypes={str(t): int(by_id[t]["subType"]) for t in selected}, task_names={str(t): by_id[t].get("name_plain") or by_id[t].get("name") for t in selected})
    return snapshot


def read_xutian_task_reward_snapshot(activity_id: int, *, expected_claimed_task_id: int | None = None) -> dict[str, Any]:
    """Read a complete loaded ladder and optionally verify one exact claim."""
    shared = read_activity_task_reward_snapshots((), include_activity_tasks=True)
    if not shared.get("ok") or not shared.get("available") or not shared.get("complete"):
        return {"ok": False, "available": False, "complete": False, "authorized_claim_task_ids": [], "reason": shared.get("reason") or "QuestMgr 事实不完整"}
    result = build_xutian_task_reward_snapshot(activity_id=int(activity_id), task_entries=list(shared.get("task_entries") or []), finished_task_ids=list(shared.get("finished_task_ids") or []))
    result["captured_at"] = shared.get("captured_at")
    result["evidence"] = shared.get("evidence")
    if expected_claimed_task_id is not None:
        result["expected_task_claimed"] = int(expected_claimed_task_id) in result.get("claimed_task_ids", [])
    return result
