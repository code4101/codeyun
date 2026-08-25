from __future__ import annotations

"""Standard manual Job for the Revenue/Mining 万宝臻宝 activity.

The draw button is deliberately outside this Job's action surface.  The
remaining sub-flows are fail-closed until their page-specific helpers have
been proven against the live activity; a read-only Runtime snapshot can only
authorize an idempotent no-op, never stand in for a missing claim action.
"""

import threading
import re
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


def _runtime(runner: Any, ctx: dict[str, Any], stop_event: threading.Event) -> Any:
    asset_tree_path = ctx.get("asset_tree_path")
    if not isinstance(asset_tree_path, Path):
        raise RuntimeError("万宝臻宝作业缺少资产树路径")
    return runner._fanxiu_runtime(
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


def _open_wanbao_main(runtime: Any):
    """Resume #604 safely, otherwise establish the canonical #600 landing."""

    scene_id, _score, _frame = runtime.current_scene(
        [
            WANBAO_XIANGZHEN_REWARD_SCENE_ID,
            WANBAO_MAIN_SCENE_ID,
            WANBAO_TASK_SCENE_ID,
            WANBAO_STORE_SCENE_ID,
            34,
        ],
        update=True,
    )
    if int(scene_id or 0) == WANBAO_XIANGZHEN_REWARD_SCENE_ID:
        landing = yield from _settle_wanbao_reward_page(runtime, label="恢复遗留奖励页")
        scene_id = _landing_scene_id(landing)
    if int(scene_id or 0) == WANBAO_MAIN_SCENE_ID:
        return WANBAO_MAIN_SCENE_ID
    if int(scene_id or 0) in {WANBAO_TASK_SCENE_ID, WANBAO_STORE_SCENE_ID}:
        yield from runtime.wait_click(
            int(scene_id),
            "万宝臻宝",
            timeout=10.0,
            label="万宝臻宝：从活动子页恢复主页",
        )
        yield from runtime.wait_view(
            WANBAO_MAIN_SCENE_ID,
            timeout=15.0,
            label="万宝臻宝：确认恢复主页",
        )
        return WANBAO_MAIN_SCENE_ID
    if int(scene_id or 0) != 34:
        yield from runtime.goto_view(34)
    yield from runtime.wait_click(
        34,
        "万宝臻宝",
        timeout=10.0,
        label="万宝臻宝：进入活动",
    )
    yield from runtime.wait_view(
        WANBAO_MAIN_SCENE_ID,
        timeout=15.0,
        label="万宝臻宝：确认主页",
    )
    return WANBAO_MAIN_SCENE_ID


def apply_wanbao_draw_policy(
    runtime: Any,
    snapshot: dict[str, Any],
    *,
    policy: WanbaoDrawPolicy = defer_wanbao_draws,
) -> dict[str, Any]:
    """Apply one policy decision; a stop is an idempotent proven terminal."""

    del runtime
    decision = policy(snapshot)
    if decision.action == "stop" and decision.expected_draws == 0:
        return {
            "complete": True,
            "outcome": "target_complete",
            "clicked": False,
            "expected_draws": 0,
            "reason": decision.reason,
        }
    if decision.action == "claim_rewards":
        raise RuntimeError("万宝臻宝抽奖规划发现待领取累抽奖励，拒绝越过领取阶段")
    if decision.action != "draw" or decision.expected_draws not in {1, 10}:
        raise RuntimeError(f"万宝臻宝抽奖决策无效：{decision}")
    if decision.action == "draw":
        raise RuntimeError(
            "万宝臻宝抽奖执行器尚未通过真实 Runtime/场景验收，拒绝消费资源："
            f"action={decision.action}, expected_draws={decision.expected_draws}"
        )
    raise AssertionError("unreachable")


def _settle_wanbao_reward_page(runtime: Any, *, label: str):
    """Close manual reward pages but never click through auto-closing pages."""

    scene_id, _score, frame = runtime.current_scene(
        [WANBAO_XIANGZHEN_REWARD_SCENE_ID, WANBAO_MAIN_SCENE_ID, 34],
        update=True,
    )
    if int(scene_id or 0) != WANBAO_XIANGZHEN_REWARD_SCENE_ID:
        return scene_id
    text = ""
    if hasattr(runtime, "ocr_text"):
        text = str(runtime.ocr_text(frame_data_url=frame) or "").replace(" ", "")
    if "自动关闭" in text:
        return (yield from runtime.wait_view(
            WANBAO_MAIN_SCENE_ID,
            34,
            timeout=8.0,
            label=f"万宝臻宝：{label}等待自动关闭",
        ))
    yield from runtime.wait_action_settle(5.0)
    fresh_id, _score, _fresh = runtime.current_scene(
        [WANBAO_XIANGZHEN_REWARD_SCENE_ID, WANBAO_MAIN_SCENE_ID, 34],
        update=True,
    )
    if int(fresh_id or 0) != WANBAO_XIANGZHEN_REWARD_SCENE_ID:
        return fresh_id
    yield from runtime.wait_click(
        WANBAO_XIANGZHEN_REWARD_SCENE_ID,
        "点击屏幕继续",
        timeout=10.0,
        label=f"万宝臻宝：{label}点击继续",
    )
    return (yield from runtime.wait_view(
        WANBAO_MAIN_SCENE_ID,
        34,
        timeout=20.0,
        label=f"万宝臻宝：{label}确认收尾",
    ))


def complete_wanbao_tasks(
    runtime: Any,
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
    return _claim_wanbao_tasks(runtime, activity_id=activity_id)


def _claim_wanbao_tasks(runtime: Any, *, activity_id: int):
    yield from runtime.wait_click(
        WANBAO_MAIN_SCENE_ID,
        "任务",
        timeout=10.0,
        label="万宝臻宝：打开任务",
    )
    yield from runtime.wait_view(
        WANBAO_TASK_SCENE_ID,
        timeout=15.0,
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
        runtime.click_frame_point(WANBAO_TASK_SCENE_ID, 470.0, 245.0)
        yield from runtime.wait_action_settle(1.2)
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
    yield from runtime.wait_click(
        WANBAO_TASK_SCENE_ID,
        "万宝臻宝",
        timeout=10.0,
        label="万宝臻宝：任务完成后返回主页",
    )
    yield from runtime.wait_view(
        WANBAO_MAIN_SCENE_ID,
        timeout=15.0,
        label="万宝臻宝：确认任务收尾主页",
    )
    return {
        "complete": True,
        "outcome": "claimed",
        "initial_claimable_task_ids": initial_authorized,
        "claimed_task_ids": claimed_now,
    }


def complete_wanbao_xiangzhen(
    runtime: Any,
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
        runtime,
        before_count=claimable_count,
        before_open_records=int(xiangzhen.get("open_box_record_count") or 0),
    )


def _open_wanbao_xiangzhen(
    runtime: Any,
    *,
    before_count: int,
    before_open_records: int,
):
    yield from runtime.wait_click(
        WANBAO_MAIN_SCENE_ID,
        "飨珍",
        timeout=10.0,
        label="万宝臻宝：打开飨珍",
    )
    yield from runtime.wait_view(
        WANBAO_XIANGZHEN_SCENE_ID,
        timeout=15.0,
        label="万宝臻宝：确认飨珍窗口",
    )
    action_shape = "点击开启" if before_count == 1 else "开启全部"
    yield from runtime.wait_click(
        WANBAO_XIANGZHEN_SCENE_ID,
        action_shape,
        timeout=10.0,
        label=f"万宝臻宝：{action_shape}飨珍",
    )
    if before_count == 1:
        yield from runtime.wait_action_settle(2.0)
        after_open = _require_snapshot()
        xiangzhen_open = after_open.get("xiangzhen") or {}
        if (
            int(xiangzhen_open.get("claimable_box_count") or 0) != 0
            or int(xiangzhen_open.get("open_box_record_count") or 0)
            - before_open_records
            != 1
        ):
            raise RuntimeError("万宝臻宝单个飨珍点击后未形成精确 Runtime 迁移")
        yield from runtime.wait_click(
            WANBAO_XIANGZHEN_SCENE_ID,
            "关闭",
            timeout=10.0,
            label="万宝臻宝：关闭单个飨珍窗口",
        )
        yield from runtime.wait_view(
            WANBAO_MAIN_SCENE_ID,
            timeout=15.0,
            label="万宝臻宝：确认单个飨珍收尾主页",
        )
        return {
            "complete": True,
            "outcome": "opened_all",
            "opened_count": 1,
            "final_scene": WANBAO_MAIN_SCENE_ID,
        }
    yield from runtime.wait_view(
        WANBAO_XIANGZHEN_REWARD_SCENE_ID,
        timeout=20.0,
        label="万宝臻宝：确认飨珍奖励",
    )
    landing = yield from _settle_wanbao_reward_page(runtime, label="关闭飨珍奖励")
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
    runtime: Any,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Claim the live-proven first milestone with Runtime read-back."""

    cumulative = snapshot.get("cumulative_rewards")
    if not isinstance(cumulative, dict):
        raise RuntimeError("万宝臻宝累抽奖励事实缺失")
    claimable_ids = [int(value) for value in cumulative.get("claimable_reward_ids") or []]
    if claimable_ids:
        return _claim_wanbao_cumulative_rewards(
            runtime, snapshot=snapshot, claimable_ids=claimable_ids
        )
    return {
        "complete": True,
        "outcome": "nothing_claimable",
        "claimable_reward_ids": [],
    }


def _claim_wanbao_cumulative_rewards(
    runtime: Any, *, snapshot: dict[str, Any], claimable_ids: list[int]
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
        runtime.click_frame_point(WANBAO_MAIN_SCENE_ID, *proven_centers[slot])
        yield from runtime.wait_view(
            WANBAO_XIANGZHEN_REWARD_SCENE_ID,
            WANBAO_MAIN_SCENE_ID,
            timeout=12.0,
            label=f"万宝臻宝：领取累抽奖励 {reward_id}",
        )
        yield from _settle_wanbao_reward_page(runtime, label=f"累抽奖励 {reward_id}")
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


def complete_wanbao_store(runtime: Any) -> ActivityStoreOperationResult:
    """Buy only the two explicitly approved gem offers; never click cash."""

    return operate_activity_store_region(
        runtime,
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
    """Run all authorized non-draw work and return to #34.

    Scheduler dormancy is persisted by the shared manual-standard-job wrapper
    only after this generator returns.  Any missing helper or incomplete
    business result therefore remains an error instead of clearing next_time.
    """

    del payload
    runtime = _runtime(runner, ctx, stop_event)
    yield from _open_wanbao_main(runtime)

    # Every phase starts from a fresh read-only accounting point.  Never let a
    # successful earlier click authorize a later action from a stale snapshot.
    tasks = yield from _run_step(
        "任务",
        lambda: complete_wanbao_tasks(runtime, _require_snapshot()),
    )
    cumulative = yield from _run_step(
        "累抽奖励",
        lambda: complete_wanbao_cumulative_rewards(runtime, _require_snapshot()),
    )

    draw = apply_wanbao_draw_policy(runtime, _require_snapshot())
    runner._log("skip", f"万宝臻宝：{draw['reason']}，无需继续启宝")

    yield from runtime.wait_click(
        WANBAO_MAIN_SCENE_ID,
        "商店",
        timeout=10.0,
        label="万宝臻宝：打开商店",
    )
    yield from runtime.wait_view(
        WANBAO_STORE_SCENE_ID,
        timeout=15.0,
        label="万宝臻宝：确认商店",
    )
    store = yield from _run_step(
        "商店",
        lambda: complete_wanbao_store(runtime),
    )
    yield from runtime.wait_click(
        WANBAO_STORE_SCENE_ID,
        "万宝臻宝",
        timeout=10.0,
        label="万宝臻宝：从商店返回主页",
    )
    yield from runtime.wait_view(
        WANBAO_MAIN_SCENE_ID,
        timeout=15.0,
        label="万宝臻宝：确认回到主页",
    )
    xiangzhen = yield from _run_step(
        "飨珍",
        lambda: complete_wanbao_xiangzhen(runtime, _require_snapshot()),
    )
    if int(xiangzhen.get("final_scene") or 0) != 34:
        yield from runtime.wait_click(
            WANBAO_MAIN_SCENE_ID,
            "返回",
            timeout=10.0,
            label="万宝臻宝：返回世界",
        )
        yield from runtime.wait_view(34, timeout=15.0, label="万宝臻宝：确认回到世界")

    message = (
        "万宝臻宝：任务、飨珍、累抽奖励与商店均已形成可证明终态；"
        "抽奖目标已满足，未继续消耗；已返回 #34"
    )
    runner._log("success", message)
    return {
        "result": "success",
        "message": message,
        "tasks": tasks,
        "xiangzhen": xiangzhen,
        "cumulative_rewards": cumulative,
        "draw": draw,
        "store_clicked_values": list(store.clicked_values),
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
    "apply_wanbao_draw_policy",
    "complete_wanbao_cumulative_rewards",
    "complete_wanbao_store",
    "complete_wanbao_tasks",
    "complete_wanbao_xiangzhen",
    "defer_wanbao_draws",
    "execute_wanbao_zhenbao_job",
]
