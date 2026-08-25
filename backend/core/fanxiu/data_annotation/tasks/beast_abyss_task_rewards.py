from __future__ import annotations

"""Idempotent Beast Abyss cultivation-reward maintenance transaction."""

from collections.abc import Callable, Iterator
from typing import Any

from backend.core.fanxiu.instrumentation.beast_abyss_task_rewards import (
    read_beast_abyss_cultivation_task_snapshot,
)


BEAST_ABYSS_EXPLORE_SCENE_ID = 657
BEAST_ABYSS_CULTIVATION_TASK_SCENE_ID = 664
BEAST_ABYSS_HOME_SCENE_ID = 535


def _view_id(value: Any) -> int | None:
    value = getattr(value, "id", value)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def claim_beast_abyss_cultivation_rewards(
    runtime: Any,
    *,
    reader: Callable[[], dict[str, Any]] = read_beast_abyss_cultivation_task_snapshot,
    max_claims: int = 10,
) -> Iterator[Any]:
    """Always inspect, then claim every server-authorized cultivation rung.

    A no-reward retry performs no GUI action.  When rewards exist, the game
    sorts the current claimable rung to the first row; every click is authorized
    by one task ID and must move that exact ID into the claimed ledger.
    """

    snapshot = reader()
    if not snapshot.get("ok") or not snapshot.get("complete"):
        raise RuntimeError(str(snapshot.get("reason") or "兽渊修炼任务事实不完整"))
    authorized = [int(value) for value in snapshot.get("authorized_claim_task_ids") or []]
    if not authorized:
        return {
            "checked": True,
            "claimed_task_ids": [],
            "remaining_claimable": [],
            "gui_opened": False,
        }

    scene_id, score, _frame = runtime.current_scene(
        [BEAST_ABYSS_EXPLORE_SCENE_ID, BEAST_ABYSS_CULTIVATION_TASK_SCENE_ID],
        update=True,
    )
    if int(scene_id or 0) == BEAST_ABYSS_EXPLORE_SCENE_ID:
        landed = yield from runtime.wait_click_then_view(
            BEAST_ABYSS_EXPLORE_SCENE_ID,
            "任务",
            [BEAST_ABYSS_CULTIVATION_TASK_SCENE_ID],
            timeout=20.0,
            label="兽渊探秘：进入任务修炼页",
        )
        scene_id = _view_id(landed)
    if int(scene_id or 0) != BEAST_ABYSS_CULTIVATION_TASK_SCENE_ID or float(score or 0) < 80.0:
        # ``score`` belongs to the pre-navigation read.  A successful
        # wait_click_then_view already proves the target; only use it when no
        # navigation occurred.
        if int(scene_id or 0) != BEAST_ABYSS_CULTIVATION_TASK_SCENE_ID:
            raise RuntimeError(f"兽渊修炼奖励要求从 #657/#664 开始：scene={scene_id}")

    claimed_now: list[int] = []
    for _attempt in range(max(1, int(max_claims))):
        authorized = [int(value) for value in snapshot.get("authorized_claim_task_ids") or []]
        if not authorized:
            break
        expected = authorized[0]
        page, page_score, _frame = runtime.current_scene(
            [BEAST_ABYSS_CULTIVATION_TASK_SCENE_ID],
            update=True,
        )
        if int(page or 0) != BEAST_ABYSS_CULTIVATION_TASK_SCENE_ID or float(page_score or 0) < 80.0:
            raise RuntimeError("兽渊修炼奖励点击前页面身份无效")
        runtime.click_shape_center(
            BEAST_ABYSS_CULTIVATION_TASK_SCENE_ID,
            "首条任务进度区",
        )
        yield from runtime.wait_action_settle(1.2)
        after = reader()
        claimed_after = {int(value) for value in after.get("claimed_task_ids") or []}
        remaining_after = [
            int(value) for value in after.get("authorized_claim_task_ids") or []
        ]
        if (
            not after.get("ok")
            or not after.get("complete")
            or expected not in claimed_after
            or expected in remaining_after
        ):
            raise RuntimeError(f"兽渊修炼任务 {expected} 点击后未形成精确已领取迁移")
        claimed_now.append(expected)
        snapshot = after
    else:
        if list(snapshot.get("authorized_claim_task_ids") or []):
            raise RuntimeError(f"兽渊修炼任务连续领取超过 {max_claims} 次仍未收敛")

    returned = yield from runtime.wait_click_then_view(
        BEAST_ABYSS_CULTIVATION_TASK_SCENE_ID,
        "兽渊探秘页签",
        [BEAST_ABYSS_HOME_SCENE_ID],
        timeout=20.0,
        label="兽渊探秘：修炼奖励检查后返回活动主页",
    )
    if _view_id(returned) != BEAST_ABYSS_HOME_SCENE_ID:
        raise RuntimeError("兽渊修炼奖励检查后未返回活动主页")
    yield from runtime.goto_view(BEAST_ABYSS_EXPLORE_SCENE_ID)
    final_scene, final_score, _frame = runtime.current_scene(
        [BEAST_ABYSS_EXPLORE_SCENE_ID],
        update=True,
    )
    if int(final_scene or 0) != BEAST_ABYSS_EXPLORE_SCENE_ID or float(final_score or 0) < 80.0:
        raise RuntimeError("兽渊修炼奖励检查后未恢复探查页")
    return {
        "checked": True,
        "claimed_task_ids": claimed_now,
        "remaining_claimable": [],
        "gui_opened": True,
    }


__all__ = ["claim_beast_abyss_cultivation_rewards"]
