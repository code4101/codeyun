from __future__ import annotations

"""Read-only QuestMgr facts for Beast Abyss cultivation-task rewards."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from backend.core.fanxiu.instrumentation.daily_task_rewards import (
    read_activity_task_reward_snapshots,
)


BEAST_ABYSS_CULTIVATION_TASK_IDS = tuple(range(440213, 440223))
_FINISH_STATUS = 4
_FINISHED_AND_REWARDED_STATUS = 5


def build_beast_abyss_cultivation_task_snapshot(
    *,
    task_entries: Sequence[Mapping[str, Any]],
    finished_task_ids: Sequence[int],
) -> dict[str, Any]:
    """Project the single live cultivation rung without inventing old rows.

    This activity retains only the current cultivation rung in ``taskEntryVOs``;
    earlier rows rendered as ``已完成`` are not a complete QuestMgr ladder.
    Therefore completeness means every *present* in-range row is structurally
    valid and unique, not that all ten historical task IDs remain materialized.
    """

    allowed = set(BEAST_ABYSS_CULTIVATION_TASK_IDS)
    finished = {int(value) for value in finished_task_ids if int(value) in allowed}
    grouped: dict[int, list[dict[str, Any]]] = {}
    for raw in task_entries:
        task_id = int(raw.get("taskId") or raw.get("task_id") or 0)
        if task_id in allowed:
            grouped.setdefault(task_id, []).append(dict(raw))

    duplicates = sorted(task_id for task_id, rows in grouped.items() if len(rows) != 1)
    malformed: list[int] = []
    rows: list[dict[str, Any]] = []
    for task_id in BEAST_ABYSS_CULTIVATION_TASK_IDS:
        candidates = grouped.get(task_id) or []
        if not candidates:
            continue
        row = candidates[0]
        try:
            status = int(row.get("status"))
            turn = int(row.get("turn"))
            reward_time = int(row.get("rewardTime", row.get("reward_time")))
        except (TypeError, ValueError):
            malformed.append(task_id)
            continue
        progress = [dict(item) for item in row.get("progressList") or [] if isinstance(item, Mapping)]
        progress_complete = bool(progress) and all(bool(item.get("finish")) for item in progress)
        claimed = task_id in finished or status == _FINISHED_AND_REWARDED_STATUS
        claimable = bool(
            not claimed
            and (
                status == _FINISH_STATUS
                or turn > reward_time
                or (turn == reward_time and progress_complete)
            )
        )
        rows.append(
            {
                "task_id": task_id,
                "status": status,
                "turn": turn,
                "reward_time": reward_time,
                "progress_complete": progress_complete,
                "claimed": claimed,
                "claimable": claimable,
            }
        )

    complete = not duplicates and not malformed
    claimable_ids = [row["task_id"] for row in rows if row["claimable"]]
    claimed_ids = sorted(finished | {row["task_id"] for row in rows if row["claimed"]})
    return {
        "ok": complete,
        "complete": complete,
        "available": bool(rows or finished),
        "state": "claimable" if claimable_ids else "nothing_claimable",
        "authorized_claim_task_ids": claimable_ids if complete else [],
        "claimed_task_ids": claimed_ids,
        "task_rows": rows,
        "duplicate_task_ids": duplicates,
        "malformed_task_ids": malformed,
        "reason": "" if complete else "兽渊修炼任务存在重复或字段不完整",
    }


def read_beast_abyss_cultivation_task_snapshot(
    *,
    shared_reader: Callable[..., dict[str, Any]] = read_activity_task_reward_snapshots,
) -> dict[str, Any]:
    """Read the live cultivation rung from the already-loaded QuestMgr table."""

    shared = shared_reader(include_activity_tasks=True)
    if not shared.get("ok"):
        return {
            "ok": False,
            "complete": False,
            "available": False,
            "state": "unavailable",
            "authorized_claim_task_ids": [],
            "claimed_task_ids": [],
            "reason": str(shared.get("reason") or "QuestMgr 活动任务读取失败"),
        }
    return {
        **build_beast_abyss_cultivation_task_snapshot(
            task_entries=list(shared.get("task_entries") or []),
            finished_task_ids=list(shared.get("finished_task_ids") or []),
        ),
        "source": "runtime_memory",
        "protocol": "QuestMgr.Model.QuestData.taskInfoMap[3]",
        "captured_at": shared.get("captured_at"),
        "evidence": dict(shared.get("evidence") or {}),
    }


__all__ = [
    "BEAST_ABYSS_CULTIVATION_TASK_IDS",
    "build_beast_abyss_cultivation_task_snapshot",
    "read_beast_abyss_cultivation_task_snapshot",
]
