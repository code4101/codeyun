from __future__ import annotations

"""Standard manual Job for the Revenue/Mining 万宝臻宝 activity.

Draws are bounded first-hit single draws with sealed intents and exact
Runtime receipts (see :mod:`backend.core.fanxiu.activity.wanbao_draw_receipts`).
The remaining sub-flows are fail-closed until their page-specific helpers have
been proven against the live activity; a read-only Runtime snapshot can only
authorize an idempotent no-op, never stand in for a missing claim action.
"""

import threading
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from backend.core.fanxiu.data_annotation.tasks.activity_store import (
    ActivityStoreOperationResult,
    operate_activity_store_region,
)
from backend.core.fanxiu.activity.lottery_strategy import (
    LotteryGoal,
    LotteryMilestone,
    LotteryPolicy,
    decide_lottery_action,
)
from backend.core.fanxiu.activity.wanbao_draw_receipts import (
    begin_wanbao_single_draw,
    read_wanbao_draw_actions,
    record_wanbao_store_policy_skip,
    settle_wanbao_single_draw,
    void_wanbao_single_draw,
    wanbao_draw_projection,
)
from backend.core.fanxiu.instrumentation.bothdraw_toggle import read_bothdraw_ten_draw_runtime
from backend.core.fanxiu.instrumentation.prize_wheel import read_prize_wheel_reward_window
from backend.core.fanxiu.instrumentation.wanbao_zhenbao import (
    read_wanbao_task_runtime,
    read_wanbao_zhenbao_runtime,
)


WANBAO_ZHENBAO_TASK_TYPE = "wanbao_zhenbao"
WANBAO_ZHENBAO_TASK_ID = "wanbao-zhenbao"
WANBAO_MAIN_SCENE_ID = 600
WANBAO_TASK_SCENE_ID = 601
WANBAO_STORE_SCENE_ID = 602
WANBAO_XIANGZHEN_SCENE_ID = 603
WANBAO_XIANGZHEN_REWARD_SCENE_ID = 604
WANBAO_APPROVED_GEM_PRICES = frozenset({248, 988})
# Owner policy buy_shop_lingshi_resources=false: the gem gift packs are not
# bought.  complete_wanbao_store stays available for an explicit future grant.
WANBAO_STORE_PURCHASE_AUTHORIZED = False
WANBAO_DRAWS_PER_ATTEMPT = 30
# Shared native reward-window layouts and their continue Shapes.
WANBAO_REWARD_CONTINUE_SHAPES = {604: "点击屏幕继续", 647: "继续", 839: "继续"}


@dataclass(frozen=True)
class WanbaoDrawDecision:
    """One bounded draw-policy decision.

    The Job owns navigation and reconciliation; a policy only decides whether
    draw currency may be consumed.  Keeping that seam explicit lets a future
    proven policy reuse the same idempotent surrounding workflow without
    weakening today's fail-closed boundary.
    """

    action: str
    reason: str
    expected_draws: int = 0


class WanbaoDrawPolicy(Protocol):
    def __call__(self, snapshot: dict[str, Any]) -> WanbaoDrawDecision: ...


def defer_wanbao_draws(snapshot: dict[str, Any]) -> WanbaoDrawDecision:
    """Compatibility name for the now-authoritative first-hit planner."""

    draw = snapshot.get("draw")
    if not isinstance(draw, dict):
        raise RuntimeError("万宝臻宝 Runtime 缺少抽奖策略事实")
    if draw.get("strategy") != "first_hit" or draw.get("enabled") is not True:
        raise RuntimeError("万宝臻宝抽奖策略不是已授权 first_hit，拒绝运行")
    cumulative = snapshot.get("cumulative_rewards") or {}
    milestones: list[LotteryMilestone] = []
    for row in cumulative.get("milestones") or []:
        reward = str(row.get("reward") or "")
        match = re.fullmatch(r"Item\|40017_(\d+)", reward)
        milestones.append(
            LotteryMilestone(
                threshold=int(row.get("target") or 0),
                reward_draws=int(match.group(1)) if match else 0,
                state="claimed"
                if row.get("claimed")
                else "claimable"
                if row.get("claimable")
                else "locked",
                reward_id=int(row.get("id") or 0),
            )
        )
    decision = decide_lottery_action(
        {
            "complete": True,
            "available_draws": int(draw.get("available_draws") or 0),
            "progress": int(draw.get("progress") or 0),
            "hit_count": int(draw.get("y") or 0),
            "claimable": list(cumulative.get("claimable_reward_ids") or []),
        },
        policy=LotteryPolicy(
            goal=LotteryGoal("first_hit"),
            remainder_mode="single",
            top_up_positive_refund_after_goal=True,
        ),
        milestones=milestones,
    )
    return WanbaoDrawDecision(
        action=decision.action,
        reason=decision.reason,
        expected_draws=decision.requested_batch_size,
    )


def _behavior_tree_context(runner: Any, ctx: dict[str, Any], stop_event: threading.Event) -> Any:
    asset_tree_path = ctx.get("asset_tree_path")
    if not isinstance(asset_tree_path, Path):
        raise RuntimeError("万宝臻宝作业缺少资产树路径")
    return runner._behavior_tree_context(
        ctx,
        asset_tree_path,
        stop_event=stop_event,
    )


def _require_snapshot() -> dict[str, Any]:
    snapshot = read_wanbao_zhenbao_runtime()
    if snapshot.get("complete") is not True:
        raise RuntimeError(
            f"万宝臻宝 Runtime 快照不完整：{snapshot.get('reason') or '未知原因'}"
        )
    # Reading an activity snapshot is also the accounting boundary for every
    # following action.  Draw policy validation happens at the policy seam so
    # a future implementation can evolve without changing this reader guard.
    for section in ("tasks", "xiangzhen", "cumulative_rewards", "draw"):
        if not isinstance(snapshot.get(section), dict):
            raise RuntimeError(f"万宝臻宝 Runtime 缺少 {section} 事实")
    return snapshot


def _landing_scene_id(landing: Any) -> int:
    return int(getattr(landing, "id", landing) or 0)


def _close_wanbao_reward_window(context: Any, *, appear_seconds: float):
    """Close the shared native reward window owned by the preceding action.

    A single draw's popup can open many seconds after the Runtime receipt, so
    ``appear_seconds`` bounds the wait for it.  Auto-closing windows are left
    to close themselves; a manual one is clicked only after its animation has
    ended and one shared continue layout is confirmed.
    """

    appear_deadline = time.monotonic() + max(0.0, appear_seconds)
    hard_deadline = appear_deadline + 45.0
    seen = False
    clicks = 0
    while time.monotonic() < hard_deadline:
        window = read_prize_wheel_reward_window()
        if not window.get("is_open"):
            if seen or time.monotonic() >= appear_deadline:
                return {"seen": seen, "close_clicks": clicks}
        else:
            seen = True
            if (window.get("auto_close_seconds") == 0 and not window.get("auto_close_timer_id")
                    and window.get("quick_hit_end") is True):
                if clicks >= 2:
                    raise RuntimeError("万宝奖励窗口两次关闭仍未结束，保留现场")
                # The native window is the identity; its frame may classify as
                # any shared "点击屏幕继续" layout, each with its own Shape.
                scene = yield from context.wait_scene_exact(
                    list(WANBAO_REWARD_CONTINUE_SHAPES), timeout=10.0,
                    observation_scenes=[WANBAO_MAIN_SCENE_ID],
                    label="万宝臻宝：确认原生奖励窗口",
                )
                fresh = read_prize_wheel_reward_window()
                if not (fresh.get("is_open") and fresh.get("panel_address") == window.get("panel_address")
                        and fresh.get("quick_hit_end") is True):
                    continue
                context.click_shape_center_fast(
                    scene.scene_id, WANBAO_REWARD_CONTINUE_SHAPES[int(scene.scene_id)]
                )
                clicks += 1
        yield from context.wait_action_settle(0.5)
    raise RuntimeError("万宝奖励窗口尚未结束，保留现场")


def _open_wanbao_main(context: Any):
    """Resume #604 safely, otherwise establish the canonical #600 landing."""

    yield from _close_wanbao_reward_window(context, appear_seconds=0.0)
    _wait_scene_match = yield from context.wait_scene([WANBAO_XIANGZHEN_REWARD_SCENE_ID, WANBAO_MAIN_SCENE_ID, WANBAO_TASK_SCENE_ID, WANBAO_STORE_SCENE_ID, 34], wait=5.0, required=False)
    (scene_id, _score, _frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if int(scene_id or 0) == WANBAO_XIANGZHEN_REWARD_SCENE_ID:
        landing = yield from _settle_wanbao_reward_page(context, label="恢复遗留奖励页")
        scene_id = _landing_scene_id(landing)
    if int(scene_id or 0) == WANBAO_MAIN_SCENE_ID:
        return WANBAO_MAIN_SCENE_ID
    if int(scene_id or 0) in {WANBAO_TASK_SCENE_ID, WANBAO_STORE_SCENE_ID}:
        yield from context.wait_click(
            int(scene_id),
            "万宝臻宝",
            timeout=10.0,
            label="万宝臻宝：从活动子页恢复主页",
        )
        yield from context.wait_scene(
            [WANBAO_MAIN_SCENE_ID],
            wait=15.0,
            label="万宝臻宝：确认恢复主页",
        )
        return WANBAO_MAIN_SCENE_ID
    if int(scene_id or 0) != 34:
        yield from context.go_scene(34)
    yield from context.wait_click(
        34,
        "万宝臻宝",
        timeout=10.0,
        label="万宝臻宝：进入活动",
    )
    yield from context.wait_scene(
        [WANBAO_MAIN_SCENE_ID],
        wait=15.0,
        label="万宝臻宝：确认主页",
    )
    return WANBAO_MAIN_SCENE_ID


def _wanbao_claim_proven(snapshot: dict[str, Any]) -> bool:
    """Whether every claimable milestone lies in the live-proven first slot."""

    cumulative = snapshot.get("cumulative_rewards") or {}
    proven = {int(row.get("id") or 0) for row in list(cumulative.get("milestones") or [])[:1]}
    return {int(value) for value in cumulative.get("claimable_reward_ids") or []} <= proven


def _ensure_wanbao_single_mode(context: Any, *, activity_id: int):
    """Clear 探宝十次 only after reading, then re-reading, V_UseTenTimes."""

    state = read_bothdraw_ten_draw_runtime(expected_activity_id=activity_id)
    if state.get("complete") is not True or not isinstance(state.get("ten_draw_enabled"), bool):
        raise RuntimeError(f"万宝探宝十次开关状态不完整：{state.get('reason') or '未知原因'}")
    if state["ten_draw_enabled"] is False:
        return
    yield from context.wait_click(
        WANBAO_MAIN_SCENE_ID, "探宝十次", timeout=10.0, label="万宝臻宝：取消探宝十次",
    )
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        state = read_bothdraw_ten_draw_runtime(expected_activity_id=activity_id)
        if state.get("complete") is True and state.get("ten_draw_enabled") is False:
            return
        yield from context.wait_action_settle(0.25)
    raise RuntimeError("万宝探宝十次取消后未由 V_UseTenTimes 复验")


def _settle_wanbao_draw(context: Any, action: dict[str, Any], *, timeout_seconds: float = 30.0):
    """Settle only the original sealed intent; never click 启宝 again here."""

    activity_id = int(action["activity_id"])
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            after = wanbao_draw_projection(
                read_wanbao_zhenbao_runtime(expected_activity_id=activity_id)
            )
            return settle_wanbao_single_draw(action["action_id"], after)
        except ValueError as exc:
            last_error = str(exc)
        yield from context.wait_action_settle(1.0)
    raise RuntimeError(
        f"万宝原intent {action['action_id']} 未结，保留页面与凭证，禁止重抽：{last_error}"
    )


def execute_wanbao_draws(context: Any, *, max_draws: int = WANBAO_DRAWS_PER_ATTEMPT):
    """Bounded first-hit single draws for retainable 40017 tickets.

    40017 survives to the next occurrence, so the owner's standing theme rule
    selects first_hit: stop as soon as one grand prize is proven.  Every draw
    takes a fresh snapshot, a sealed intent and an exact Runtime receipt; ten
    draws are never used.  A chunk boundary or an unproven milestone claim is
    ``complete=False`` pending work, never a business completion.
    """

    if type(max_draws) is not int or not 1 <= max_draws <= 200:
        raise ValueError("万宝单抽数量需在 1 至 200 之间")
    pending = [r for r in read_wanbao_draw_actions() if r.get("status") == "pending"]
    if len(pending) > 1:
        raise RuntimeError("万宝存在多个未结抽奖intent，保留现场")
    draws: list[dict[str, Any]] = []
    if pending:
        action = pending[0]
        try:
            row = yield from _settle_wanbao_draw(context, action, timeout_seconds=5.0)
        except RuntimeError:
            # Reentry never clicks again; an unchanged world after the
            # settle window proves the original click had no effect.
            void_wanbao_single_draw(
                action["action_id"],
                wanbao_draw_projection(read_wanbao_zhenbao_runtime(
                    expected_activity_id=int(action["activity_id"]))),
            )
            context.runner._log("warning", f"万宝臻宝：原intent {action['action_id']} 经 Runtime 证明无效果，已作废")
        else:
            draws.append({"action_id": row["action_id"], "effect": row["effect"], "reconciled": True})
            yield from _close_wanbao_reward_window(context, appear_seconds=30.0)
    while True:
        snapshot = _require_snapshot()
        draw = snapshot["draw"]
        if draw.get("target_complete") is True:
            return {"complete": True, "outcome": "first_hit_reached", "draws": draws}
        if not int(draw.get("available_draws") or 0):
            return {"complete": True, "outcome": "draws_exhausted_before_target", "draws": draws}
        if (snapshot.get("cumulative_rewards") or {}).get("claimable_reward_ids"):
            if not _wanbao_claim_proven(snapshot):
                return {"complete": False, "outcome": "cumulative_claim_unproven", "draws": draws}
            yield from _run_step(
                "累抽奖励", lambda: complete_wanbao_cumulative_rewards(context, snapshot)
            )
            continue
        if len(draws) >= max_draws:
            return {"complete": False, "outcome": "bounded_chunk", "draws": draws}
        activity_id = int(snapshot["activity_id"])
        yield from _ensure_wanbao_single_mode(context, activity_id=activity_id)
        before = wanbao_draw_projection(_require_snapshot())
        action = begin_wanbao_single_draw(before)
        yield from context.wait_click(
            WANBAO_MAIN_SCENE_ID, "启宝", timeout=10.0, label="万宝臻宝：单抽启宝",
        )
        row = yield from _settle_wanbao_draw(context, action)
        draws.append({"action_id": row["action_id"], "effect": row["effect"]})
        context.runner._log("success", f"万宝臻宝：单抽回执 {row['effect']}")
        yield from _close_wanbao_reward_window(context, appear_seconds=30.0)
        yield from context.wait_scene_exact(
            [WANBAO_MAIN_SCENE_ID], timeout=15.0, label="万宝臻宝：单抽后确认主页",
        )
        if row["effect"]["grand_prize_count"]:
            return {"complete": True, "outcome": "first_hit_reached", "draws": draws}


def _settle_wanbao_reward_page(context: Any, *, label: str):
    """Close manual reward pages but never click through auto-closing pages."""

    _wait_scene_match = yield from context.wait_scene([WANBAO_XIANGZHEN_REWARD_SCENE_ID, WANBAO_MAIN_SCENE_ID, 34], wait=5.0, required=False)
    (scene_id, _score, frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if int(scene_id or 0) != WANBAO_XIANGZHEN_REWARD_SCENE_ID:
        return scene_id
    text = ""
    if hasattr(context, "ocr_text"):
        text = str(context.ocr_text(frame_data_url=frame) or "").replace(" ", "")
    if "自动关闭" in text:
        return (yield from context.wait_scene(
            [WANBAO_MAIN_SCENE_ID,
            34],
            wait=8.0,
            label=f"万宝臻宝：{label}等待自动关闭",
        ))
    yield from context.wait_action_settle(5.0)
    _wait_scene_match = yield from context.wait_scene([WANBAO_XIANGZHEN_REWARD_SCENE_ID, WANBAO_MAIN_SCENE_ID, 34], wait=5.0, required=False)
    (fresh_id, _score, _fresh) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if int(fresh_id or 0) != WANBAO_XIANGZHEN_REWARD_SCENE_ID:
        return fresh_id
    yield from context.wait_click(
        WANBAO_XIANGZHEN_REWARD_SCENE_ID,
        "点击屏幕继续",
        timeout=10.0,
        label=f"万宝臻宝：{label}点击继续",
    )
    return (yield from context.wait_scene(
        [WANBAO_MAIN_SCENE_ID,
        34],
        wait=20.0,
        label=f"万宝臻宝：{label}确认收尾",
    ))


def complete_wanbao_tasks(
    context: Any,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Claim the removing first row and verify every taskId transition."""

    tasks = snapshot.get("tasks")
    if not isinstance(tasks, dict):
        raise RuntimeError("万宝臻宝任务事实缺失")
    claimable_count = int(tasks.get("claimable_count") or 0)
    if not claimable_count:
        return {
            "complete": True,
            "outcome": "nothing_claimable",
            "claimable_count": 0,
            "claimed_task_ids": [],
        }
    activity_id = int(snapshot.get("activity_id") or tasks.get("activity_id") or 0)
    if activity_id <= 0:
        raise RuntimeError("万宝臻宝任务缺少 activity_id")
    return _claim_wanbao_tasks(context, activity_id=activity_id)


def _claim_wanbao_tasks(context: Any, *, activity_id: int):
    yield from context.wait_click(
        WANBAO_MAIN_SCENE_ID,
        "任务",
        timeout=10.0,
        label="万宝臻宝：打开任务",
    )
    yield from context.wait_scene(
        [WANBAO_TASK_SCENE_ID],
        wait=15.0,
        label="万宝臻宝：确认任务页",
    )
    claimed_now: list[int] = []
    before = read_wanbao_task_runtime(expected_activity_id=int(activity_id))
    if before.get("complete") is not True:
        raise RuntimeError(f"万宝臻宝任务快照不完整：{before.get('reason') or '未知原因'}")
    authorized = [
        int(task["task_id"])
        for task in before.get("tasks") or []
        if task.get("state") == "claimable"
    ]
    initial_authorized = list(authorized)
    while authorized:
        expected = authorized[0]
        context.click_frame_point(WANBAO_TASK_SCENE_ID, 470.0, 245.0)
        yield from context.wait_action_settle(1.2)
        after = read_wanbao_task_runtime(expected_activity_id=int(activity_id))
        if after.get("complete") is not True:
            raise RuntimeError(f"万宝臻宝任务 {expected} 点击后快照不完整")
        claimed_after = {
            int(task["task_id"])
            for task in after.get("tasks") or []
            if task.get("state") == "claimed"
        }
        remaining_after = [
            int(task["task_id"])
            for task in after.get("tasks") or []
            if task.get("state") == "claimable"
        ]
        if expected not in claimed_after or remaining_after != authorized[1:]:
            raise RuntimeError(
                f"万宝臻宝任务 {expected} 未形成精确单步迁移：remaining={remaining_after}"
            )
        claimed_now.append(expected)
        authorized = remaining_after
    yield from context.wait_click(
        WANBAO_TASK_SCENE_ID,
        "万宝臻宝",
        timeout=10.0,
        label="万宝臻宝：任务完成后返回主页",
    )
    yield from context.wait_scene(
        [WANBAO_MAIN_SCENE_ID],
        wait=15.0,
        label="万宝臻宝：确认任务收尾主页",
    )
    return {
        "complete": True,
        "outcome": "claimed",
        "initial_claimable_task_ids": initial_authorized,
        "claimed_task_ids": claimed_now,
    }


def complete_wanbao_xiangzhen(
    context: Any,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Open all authorized 飨珍 once and verify MiningData afterwards."""

    xiangzhen = snapshot.get("xiangzhen")
    if not isinstance(xiangzhen, dict):
        raise RuntimeError("万宝臻宝飨珍事实缺失")
    claimable_count = int(xiangzhen.get("claimable_box_count") or 0)
    if not claimable_count:
        return {
            "complete": True,
            "outcome": "nothing_claimable",
            "claimable_count": 0,
            "final_scene": WANBAO_MAIN_SCENE_ID,
        }
    if xiangzhen.get("claim_action_ready") is not True:
        raise RuntimeError(
            f"万宝臻宝飨珍未获 Runtime 授权：{xiangzhen.get('claim_action_reason') or '未知原因'}"
        )
    return _open_wanbao_xiangzhen(
        context,
        before_count=claimable_count,
        before_open_records=int(xiangzhen.get("open_box_record_count") or 0),
    )


def _open_wanbao_xiangzhen(
    context: Any,
    *,
    before_count: int,
    before_open_records: int,
):
    yield from context.wait_click(
        WANBAO_MAIN_SCENE_ID,
        "飨珍",
        timeout=10.0,
        label="万宝臻宝：打开飨珍",
    )
    yield from context.wait_scene(
        [WANBAO_XIANGZHEN_SCENE_ID],
        wait=15.0,
        label="万宝臻宝：确认飨珍窗口",
    )
    action_shape = "点击开启" if before_count == 1 else "开启全部"
    yield from context.wait_click(
        WANBAO_XIANGZHEN_SCENE_ID,
        action_shape,
        timeout=10.0,
        label=f"万宝臻宝：{action_shape}飨珍",
    )
    if before_count == 1:
        yield from context.wait_action_settle(2.0)
        after_open = _require_snapshot()
        xiangzhen_open = after_open.get("xiangzhen") or {}
        if (
            int(xiangzhen_open.get("claimable_box_count") or 0) != 0
            or int(xiangzhen_open.get("open_box_record_count") or 0)
            - before_open_records
            != 1
        ):
            raise RuntimeError("万宝臻宝单个飨珍点击后未形成精确 Runtime 迁移")
        yield from context.wait_click(
            WANBAO_XIANGZHEN_SCENE_ID,
            "关闭",
            timeout=10.0,
            label="万宝臻宝：关闭单个飨珍窗口",
        )
        yield from context.wait_scene(
            [WANBAO_MAIN_SCENE_ID],
            wait=15.0,
            label="万宝臻宝：确认单个飨珍收尾主页",
        )
        return {
            "complete": True,
            "outcome": "opened_all",
            "opened_count": 1,
            "final_scene": WANBAO_MAIN_SCENE_ID,
        }
    yield from context.wait_scene(
        [WANBAO_XIANGZHEN_REWARD_SCENE_ID],
        wait=20.0,
        label="万宝臻宝：确认飨珍奖励",
    )
    landing = yield from _settle_wanbao_reward_page(context, label="关闭飨珍奖励")
    landing_id = _landing_scene_id(landing)
    after = _require_snapshot()
    xiangzhen_after = after.get("xiangzhen") or {}
    after_claimable = int(xiangzhen_after.get("claimable_box_count") or 0)
    after_records = int(xiangzhen_after.get("open_box_record_count") or 0)
    if after_claimable != 0 or after_records - before_open_records != before_count:
        raise RuntimeError(
            "万宝臻宝飨珍点击后状态不完整："
            f"claimable={after_claimable}, open_records={after_records}"
        )
    return {
        "complete": True,
        "outcome": "opened_all",
        "opened_count": before_count,
        "final_scene": landing_id,
    }


def complete_wanbao_cumulative_rewards(
    context: Any,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Claim the live-proven first milestone with Runtime read-back."""

    cumulative = snapshot.get("cumulative_rewards")
    if not isinstance(cumulative, dict):
        raise RuntimeError("万宝臻宝累抽奖励事实缺失")
    claimable_ids = [int(value) for value in cumulative.get("claimable_reward_ids") or []]
    if claimable_ids:
        return _claim_wanbao_cumulative_rewards(
            context, snapshot=snapshot, claimable_ids=claimable_ids
        )
    return {
        "complete": True,
        "outcome": "nothing_claimable",
        "claimable_reward_ids": [],
    }


def _claim_wanbao_cumulative_rewards(
    context: Any, *, snapshot: dict[str, Any], claimable_ids: list[int]
):
    milestones = list((snapshot.get("cumulative_rewards") or {}).get("milestones") or [])
    proven_ids = [int(row.get("id") or 0) for row in milestones[:1]]
    proven_centers = ((250.0, 980.0),)
    claimed_now: list[int] = []
    activity_id = int(snapshot.get("activity_id") or 0)
    for reward_id in claimable_ids:
        if reward_id not in proven_ids:
            raise RuntimeError(
                f"万宝臻宝累抽奖励 {reward_id} 不在已验收的首档动作范围"
            )
        slot = proven_ids.index(reward_id)
        context.click_frame_point(WANBAO_MAIN_SCENE_ID, *proven_centers[slot])
        yield from context.wait_scene(
            [WANBAO_XIANGZHEN_REWARD_SCENE_ID,
            WANBAO_MAIN_SCENE_ID],
            wait=12.0,
            label=f"万宝臻宝：领取累抽奖励 {reward_id}",
        )
        yield from _settle_wanbao_reward_page(context, label=f"累抽奖励 {reward_id}")
        after = read_wanbao_zhenbao_runtime(expected_activity_id=activity_id)
        cumulative_after = after.get("cumulative_rewards") or {}
        if reward_id not in {
            int(value) for value in cumulative_after.get("claimed_reward_ids") or []
        }:
            raise RuntimeError(f"万宝臻宝累抽奖励 {reward_id} 点击后未进入已领取账本")
        claimed_now.append(reward_id)
    return {
        "complete": True,
        "outcome": "claimed",
        "claimed_reward_ids": claimed_now,
    }


def complete_wanbao_store(context: Any) -> ActivityStoreOperationResult:
    """Buy only the two explicitly approved gem offers; never click cash."""

    return operate_activity_store_region(
        context,
        scene_id=WANBAO_STORE_SCENE_ID,
        region_title="购买区",
        stability_timeout_seconds=20.0,
        purchase_timeout_seconds=20.0,
        select_targets=lambda scan: tuple(
            target
            for target in scan.targets
            if not target.is_cash and target.value in WANBAO_APPROVED_GEM_PRICES
        ),
    )


def _skip_wanbao_store(runner: Any, *, activity_id: int) -> dict[str, Any]:
    """Apply the owner's no-purchase store policy without entering #602.

    The 248/988 whitelist only bounds what the purchase helper could click; it
    is not a purchase authorization.  The skip is durable and auditable and
    never reported as a purchase.
    """

    if WANBAO_STORE_PURCHASE_AUTHORIZED:
        raise RuntimeError("万宝商店购买授权需重新验收 #602 识别与购买流程")
    row = record_wanbao_store_policy_skip(
        activity_id, reason="owner policy buy_shop_lingshi_resources=false：灵石礼包不购买",
    )
    runner._log("skip", f"万宝臻宝：商店按策略跳过，未购买（活动 {activity_id}，记录于 {row['recorded_at']}）")
    return {"outcome": "policy_skip", "purchased": False, "record": row}


def _run_step(
    label: str,
    operation: Callable[[], Any],
):
    result = operation()
    if hasattr(result, "send"):
        result = yield from result
    if isinstance(result, ActivityStoreOperationResult):
        if result.completed is not True:
            raise RuntimeError(f"万宝臻宝{label}未形成完成态")
        return result
    if not isinstance(result, dict) or result.get("complete") is not True:
        raise RuntimeError(f"万宝臻宝{label} helper 未证明业务完成")
    return result


def execute_wanbao_zhenbao_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
):
    """Run authorized free work and bounded first-hit draws, then return to #34.

    Scheduler dormancy is persisted by the shared manual-standard-job wrapper
    only after this generator returns.  Any missing helper or incomplete
    business result therefore remains an error instead of clearing next_time.
    """

    max_draws = int(payload.get("wanbao_max_draws") or WANBAO_DRAWS_PER_ATTEMPT)
    context = _behavior_tree_context(runner, ctx, stop_event)
    yield from _open_wanbao_main(context)

    # Every phase starts from a fresh read-only accounting point.  Never let a
    # successful earlier click authorize a later action from a stale snapshot.
    tasks = yield from _run_step(
        "任务",
        lambda: complete_wanbao_tasks(context, _require_snapshot()),
    )
    cumulative = yield from _run_step(
        "累抽奖励",
        lambda: complete_wanbao_cumulative_rewards(context, _require_snapshot()),
    )

    draw = yield from execute_wanbao_draws(context, max_draws=max_draws)
    runner._log("info", f"万宝臻宝：抽奖阶段 {draw['outcome']}，本次单抽 {len(draw['draws'])} 次")

    store = _skip_wanbao_store(runner, activity_id=int(_require_snapshot()["activity_id"]))
    xiangzhen = yield from _run_step(
        "飨珍",
        lambda: complete_wanbao_xiangzhen(context, _require_snapshot()),
    )
    if int(xiangzhen.get("final_scene") or 0) != 34:
        yield from context.wait_click(
            WANBAO_MAIN_SCENE_ID,
            "返回",
            timeout=10.0,
            label="万宝臻宝：返回世界",
        )
        yield from context.wait_scene([34], wait=15.0, label="万宝臻宝：确认回到世界")

    if draw["complete"] is not True:
        message = f"万宝臻宝：抽奖阶段未完成（{draw['outcome']}），保留阶段并等待重试；已返回 #34"
        runner._log("info", message)
        return {
            "result": "pending",
            "message": message,
            "tasks": tasks,
            "xiangzhen": xiangzhen,
            "cumulative_rewards": cumulative,
            "draw": draw,
            "store": store,
            "final_scene": 34,
        }
    message = (
        "万宝臻宝：任务、飨珍、累抽奖励已形成可证明终态；"
        f"抽奖阶段 {draw['outcome']}；商店按策略跳过、未购买；已返回 #34"
    )
    runner._log("success", message)
    return {
        "result": "success",
        "message": message,
        "tasks": tasks,
        "xiangzhen": xiangzhen,
        "cumulative_rewards": cumulative,
        "draw": draw,
        "store": store,
        "final_scene": 34,
    }


__all__ = [
    "WANBAO_APPROVED_GEM_PRICES",
    "WANBAO_MAIN_SCENE_ID",
    "WANBAO_STORE_SCENE_ID",
    "WANBAO_TASK_SCENE_ID",
    "WANBAO_ZHENBAO_TASK_ID",
    "WANBAO_ZHENBAO_TASK_TYPE",
    "WanbaoDrawDecision",
    "WanbaoDrawPolicy",
    "execute_wanbao_draws",
    "complete_wanbao_cumulative_rewards",
    "complete_wanbao_store",
    "complete_wanbao_tasks",
    "complete_wanbao_xiangzhen",
    "defer_wanbao_draws",
    "execute_wanbao_zhenbao_job",
]
