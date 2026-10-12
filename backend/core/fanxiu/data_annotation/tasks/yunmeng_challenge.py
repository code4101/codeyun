from __future__ import annotations

"""云梦正式逐档挑战：首次100次后领奖，每轮推进宝阁下一个档次。

公共规划器拥有100次下限与末日最高档门禁；原生执行器拥有整轮准入、
设置回读和结算次数证明。资源不足整轮时等待，不裁剪为小批。
"""

from datetime import datetime
import threading
from typing import Any, Iterator

from sqlmodel import Session

from backend.db import engine


def execute_yunmeng_challenge_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: Any,
    captured_at: datetime,
    required_fact_watermark: datetime,
) -> Iterator[Any]:
    """刷新云梦事实后，用当日可用次数把论剑玉推进到目标档次。"""

    del required_fact_watermark
    from backend.core.fanxiu.activity.ranking_reconcile import seed_ranking_occurrence
    from backend.core.fanxiu.activity.yunmeng_exchange import (
        collect_and_store_yunmeng_exchange_activity,
    )
    from backend.core.fanxiu.data_annotation.tasks.yunmeng_active import (
        load_yunmeng_pages,
    )
    from backend.core.fanxiu.data_annotation.tasks.yunmeng_native_auto import (
        execute_yunmeng_native_auto_job,
    )

    label = "云梦_自动挑战"
    with Session(engine) as session:
        activity = seed_ranking_occurrence(
            session,
            occurrence,
            captured_at=captured_at.isoformat(timespec="seconds"),
        )
        activity_id = str(activity.id)
        session.commit()

    context = runner._behavior_tree_context(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    # 事实先就绪：兑换宝阁与钱包是预算门禁，榜单是档次奖励依据。
    yield from load_yunmeng_pages(context, occurrence, label=label)
    with Session(engine) as session:
        collect_and_store_yunmeng_exchange_activity(
            session,
            activity_id=activity_id,
            collect_runtime_shop=True,
        )
        session.commit()
    yield from context.go_scene(34)

    options: dict[str, Any] = {
        "milestone_strategy": True,
        "activity_id": activity_id,
        "instance_key": occurrence.instance_key,
        "scheduler_task_id": str(payload.get("__scheduler_task_id") or "ranking-lifecycle"),
        "max_batches": max(1, int(payload.get("yunmeng_max_batches") or 20)),
        "terminal_polls": max(1, int(payload.get("yunmeng_terminal_polls") or 1800)),
    }
    from .gameplay_rank_reward_cadence import claim_initial_gameplay_rewards_once
    from .yunmeng_task_rewards import claim_yunmeng_task_rewards_if_available
    def claim_current_rewards():
        rewards = yield from claim_yunmeng_task_rewards_if_available()
        if rewards.get("status") in {"nothing_claimable", "already_claimed"}:
            return {**rewards, "status": "completed"}
        return rewards
    def initial_reward_gate():
        return (yield from claim_initial_gameplay_rewards_once(
            occurrence, claim_current_rewards))
    options["initial_reward_gate"] = initial_reward_gate
    # 论剑令补体力是不可逆消耗：只有在作业 payload 里显式授权时才开启。
    if bool(payload.get("yunmeng_use_refill_items", False)):
        options["use_refill_items"] = True
    result = yield from execute_yunmeng_native_auto_job(
        runner,
        ctx,
        options,
        stop_event,
    )
    if str(result.get("activity_id") or "") not in {"", activity_id}:
        raise RuntimeError(f"{label}：挑战结果切换到其他活动实例")
    return {
        **result,
        "status": ("completed" if result.get("target_reached")
                   or result.get("incomplete_reason") == "final_day_reserved" else "pending"),
        "checkpoint": "yunmeng_challenge",
        "target_level": (
            "有限宝阁档次已满足" if result.get("target_reached") else "统一逐档挑战"
        ),
    }


__all__ = ["execute_yunmeng_challenge_checkpoint"]
