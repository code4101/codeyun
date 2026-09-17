from __future__ import annotations

"""Claim the 社团灵宠 task-reward rows through their verified GUI contract.

The page is adapted here (not presented as a universal layout). The entry is
gated by the authoritative Runtime task state, so a fully claimed activity is a
no-op with zero clicks and no navigation. Each row's left half holds the
title/condition/progress and its right half holds reward icons, which must
never be clicked; the claim area is therefore the whole-row left safe region.
"""

from collections.abc import Generator
from typing import Any

from backend.core.fanxiu.data_annotation.tasks.task_reward_rows import claim_task_rows_by_ocr


SHEQUN_LINGCHONG_TASK_SCENE_ID = 747
SHEQUN_LINGCHONG_ACTIVITY_ID = 4043501
EXPECTED_TIER_COUNT = 14


def claim_shequn_lingchong_task_rewards(
    context: Any,
    *,
    activity_id: int = SHEQUN_LINGCHONG_ACTIVITY_ID,
    scene_id: int = SHEQUN_LINGCHONG_TASK_SCENE_ID,
    first_row_shape: str = "首行领取区",
    observer_shape: str = "第三行任务标题",
    progress_shape: str = "首行进度",
    click_settle_seconds: float = 3.0,
    no_change_confirmations: int = 3,
    max_clicks: int = 30,
) -> Generator[Any, None, dict[str, Any]]:
    """Claim the naturally advancing list, or no-op when all tiers are claimed.

    Runtime is the business gate: it must be loaded, complete, exactly 14
    tiers, fully finished and fully claimed before this task reports complete.
    When every tier is already claimed the entry returns immediately without
    ``wait_scene`` or any click. When every tier is finished the shared loop
    may run without ``progress_shape`` (the Runtime completion is the gate);
    otherwise the per-click progress gate is kept so an unmet row can never
    navigate away.
    """

    from backend.core.fanxiu.data_annotation.tasks.pet_resource_use import (
        read_lingchong_task_milestones)

    def read_state():
        snapshot = read_lingchong_task_milestones(int(activity_id))
        state = snapshot.get("reward_state") or {}
        return snapshot, state

    snapshot, state = read_state()
    task_ids = {int(value) for value in snapshot.get("task_ids") or []}
    claimed = {int(value) for value in state.get("claimed_task_ids") or []}
    claimable = [int(value) for value in state.get("claimable_task_ids") or []]
    if not (
        snapshot.get("ok")
        and snapshot.get("complete")
        and state.get("complete")
        and len(task_ids) == EXPECTED_TIER_COUNT
    ):
        raise RuntimeError(
            "社团灵宠任务奖励：Runtime 业务状态不完整，拒绝进入"
            f"（ok={snapshot.get('ok')}, complete={snapshot.get('complete')}, "
            f"reward_complete={state.get('complete')}, tiers={len(task_ids)}）"
        )
    if task_ids <= claimed and not claimable:
        return {"checked": True, "gui_opened": False, "already_claimed": True,
                "reason": "already_claimed", "clicks": 0, "detected_advances": 0,
                "unchanged_confirmations": 0, "claimable_task_ids": []}

    landed = yield from context.wait_scene([scene_id], wait=15)
    if int(landed) != int(scene_id):
        raise RuntimeError(f"社团灵宠任务页场景身份无效：{landed}")

    all_finished = all(
        bool(row.get("finished")) for row in snapshot.get("milestones") or []
    )
    result = yield from claim_task_rows_by_ocr(
        context,
        scene_id=int(scene_id),
        first_row_shape=first_row_shape,
        observer_shape=observer_shape,
        label="社团灵宠任务奖励",
        progress_shape=None if all_finished else progress_shape,
        click_settle_seconds=click_settle_seconds,
        no_change_confirmations=no_change_confirmations,
        max_clicks=max_clicks,
    )

    _final_snapshot, final_state = read_state()
    final_claimed = {int(value) for value in final_state.get("claimed_task_ids") or []}
    final_claimable = [int(value) for value in final_state.get("claimable_task_ids") or []]
    if not (task_ids <= final_claimed and not final_claimable):
        raise RuntimeError(
            "社团灵宠任务奖励：GUI 结束后 Runtime 仍有未领档："
            f"claimed={sorted(final_claimed)}, claimable={final_claimable}"
        )
    return {"checked": True, "gui_opened": True, "already_claimed": False,
            "claimable_task_ids": [], **result}


__all__ = [
    "claim_shequn_lingchong_task_rewards",
    "SHEQUN_LINGCHONG_ACTIVITY_ID",
    "SHEQUN_LINGCHONG_TASK_SCENE_ID",
]
