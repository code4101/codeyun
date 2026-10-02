from __future__ import annotations

import threading
from collections.abc import Generator
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from backend.core.fanxiu.activity.exchange_event import is_exchange_activity_active
from backend.core.fanxiu.activity.resource_ranking import (
    LINGZHUANG_HUADAO_ACTIVITY_TYPE,
)
from backend.core.fanxiu.data_annotation.equipment import (
    EquipmentStrengtheningResourceExhausted,
    complete_equipment_strengthening_tasks,
)
from backend.db import engine
from backend.models import FanxiuExchangeActivity


DEFAULT_TARGET_TIER = 14
STANDARD_JOB_ID = "lingzhuang-strengthening"
LINGZHUANG_EQUIPMENT_TASK_SCENE = 735
LINGZHUANG_SCORE_TASK_SCENE = 912


def open_lingzhuang_task_page(context: Any, *, game_task_activity_id: int, tab: str):
    """同一任务入口记忆上次页签；先确认实际页，再切换所需的装备/积分页。"""
    from backend.core.fanxiu.data_annotation.tasks.resource_rank_daily_gift import (
        RESOURCE_RANK_GIFT_ADAPTERS, open_resource_rank_activity_page,
    )
    target = {"装备": LINGZHUANG_EQUIPMENT_TASK_SCENE, "积分": LINGZHUANG_SCORE_TASK_SCENE}[tab]
    panels = [LINGZHUANG_EQUIPMENT_TASK_SCENE, LINGZHUANG_SCORE_TASK_SCENE]
    scene = yield from context.wait_scene([*panels, 676], wait=5, required=False)
    if scene is None or scene.scene_id not in panels:
        adapter = next(a for a in RESOURCE_RANK_GIFT_ADAPTERS if a.key == "lingzhuang-huadao")
        yield from open_resource_rank_activity_page(context, adapter,
            activity_id=game_task_activity_id, now=datetime.now().astimezone())
        scene = yield from context.wait_click_then_scene(676, "任务", panels, timeout=20)
    if scene.scene_id != target:
        yield from context.wait_click_then_scene(scene.scene_id, tab + "页签", [target], timeout=20)
    return target


def resolve_lingzhuang_strengthening_activity(
    session: Session,
    *,
    activity_id: str | None = None,
    today: date | None = None,
) -> FanxiuExchangeActivity | None:
    """Resolve the requested or latest currently active activity instance."""

    current_day = today or date.today()
    requested_id = str(activity_id or "").strip()
    if requested_id:
        activity = session.get(FanxiuExchangeActivity, requested_id)
        if activity is None or activity.activity_type != LINGZHUANG_HUADAO_ACTIVITY_TYPE:
            raise ValueError("灵装化道_强化：指定活动实例不存在")
        if not is_exchange_activity_active(activity, today=current_day):
            raise ValueError("灵装化道_强化：指定活动实例不在有效日期内")
        return activity

    activities = list(
        session.exec(
            select(FanxiuExchangeActivity)
            .where(
                FanxiuExchangeActivity.activity_type
                == LINGZHUANG_HUADAO_ACTIVITY_TYPE
            )
            .order_by(
                col(FanxiuExchangeActivity.start_date).desc(),
                col(FanxiuExchangeActivity.end_date).desc(),
                col(FanxiuExchangeActivity.cross_count).desc(),
            )
        ).all()
    )
    return next(
        (
            activity
            for activity in activities
            if is_exchange_activity_active(activity, today=current_day)
        ),
        None,
    )


def claim_lingzhuang_equipment_rewards(
    context: Any, *, game_task_activity_id: int, cross_count: int = 1,
) -> Generator[Any, None, dict[str, Any]]:
    """按 OCR 定位推进领奖，结束时用本期任务记录核验无可领奖励。"""
    from backend.core.fanxiu.activity.lingzhuang_strengthening import (
        read_lingzhuang_equipment_reward_snapshot,
    )
    from backend.core.fanxiu.data_annotation.tasks.task_reward_rows import claim_task_rows_by_ocr
    yield from open_lingzhuang_task_page(context, game_task_activity_id=game_task_activity_id, tab="装备")
    before = read_lingzhuang_equipment_reward_snapshot(
        game_task_activity_id=game_task_activity_id, cross_count=cross_count,
    )
    if not before.get("complete"):
        raise RuntimeError("灵装化道装备奖励：本期任务记录不完整")
    if before.get("authorized_claim_task_ids"):
        result = yield from claim_task_rows_by_ocr(
            context, scene_id=735, first_row_shape="首条任务领取区",
            observer_shape="首行任务标题", progress_shape="首行任务进度",
            progress_context_shape="首条任务领取区",
            label="灵装化道装备奖励", claimed_texts=("已完成", "已领取"), max_clicks=20,
        )
    else:
        result = {"reason": "runtime_no_claimable_reward", "clicks": 0,
                  "detected_advances": 0}
    # 小字号进度 OCR 可能把已达成读成未达成；不能据此写 completed。
    # 精确活动 ID 的当前已领记录同时避免跨期沿用旧领奖事实。
    verified = (read_lingzhuang_equipment_reward_snapshot(
        game_task_activity_id=game_task_activity_id, cross_count=cross_count,
    ) if result["clicks"] else before)
    if not verified.get("complete") or verified.get("authorized_claim_task_ids"):
        raise RuntimeError(
            "灵装化道装备奖励尚未领完："
            f"{verified.get('authorized_claim_task_ids') or verified.get('state')}"
        )
    yield from context.wait_click_then_scene(735, "榜单", [676], timeout=15)
    return {"ok": True, **result,
            "claimed_task_ids": verified.get("claimed_task_ids", [])}


def execute_lingzhuang_strengthening_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
):
    """Enter strengthening and consume resources up to one equipment-task tier."""

    with Session(engine) as session:
        activity = resolve_lingzhuang_strengthening_activity(
            session,
            activity_id=str(payload.get("activity_id") or "").strip() or None,
        )
    if activity is None:
        message = "灵装化道_强化：当前没有有效活动实例，未操作游戏"
        runner._log("skip", message)
        return {"ok": False, "outcome": "no_active_activity", "message": message}

    asset_tree_path = ctx.get("asset_tree_path")
    if not isinstance(asset_tree_path, Path):
        raise RuntimeError("灵装化道_强化：缺少资产树路径，无法执行")
    context = runner._behavior_tree_context(
        ctx,
        asset_tree_path,
        stop_event=stop_event,
    )

    raw_target_progress = payload.get("target_progress")
    target_progress = (
        int(raw_target_progress)
        if raw_target_progress is not None and str(raw_target_progress).strip()
        else None
    )
    raw_target_tier = payload.get("target_tier")
    target_tier = (
        int(raw_target_tier)
        if raw_target_tier is not None and str(raw_target_tier).strip()
        else (None if target_progress is not None else DEFAULT_TARGET_TIER)
    )

    try:
        game_task_activity_id = int(
            (getattr(activity, "evidence", None) or {}).get("game_activity_id") or 0
        ) or None
        if game_task_activity_id is None:
            raise RuntimeError("灵装化道_强化：本期活动缺少游戏任务 ID，需先同步活动实例")
        result = yield from complete_equipment_strengthening_tasks(
            context,
            activity_id=activity.id,
            target_progress=target_progress,
            target_tier=target_tier,
            cross_count=int(activity.cross_count),
            game_task_activity_id=game_task_activity_id,
            max_clicks=max(1, int(payload.get("max_clicks") or 200)),
            max_overshoot_percent=int(payload.get("max_overshoot_percent", 5)),
        )
    except EquipmentStrengtheningResourceExhausted as exc:
        message = f"灵装化道_强化：{exc}，按当前存量正常停止"
        runner._log("skip", message)
        return {
            "ok": False,
            "outcome": "insufficient_resource",
            "message": message,
            "activity_id": activity.id,
            "target_tier": target_tier,
            "target_progress": exc.target_progress,
            "equipment_progress": exc.equipment_progress,
            "cumulative_material": exc.cumulative_material,
        }

    rewards = yield from claim_lingzhuang_equipment_rewards(
        context, game_task_activity_id=game_task_activity_id, cross_count=int(activity.cross_count),
    )
    message = (
        f"灵装化道_强化：装备任务已到 {int(result['equipment_progress'])}"
        f" / {int(result['target_progress'])}"
    )
    runner._log("success", message)
    return {
        **result,
        "outcome": "target_reached",
        "message": message,
        "activity_id": activity.id,
        "rewards": rewards,
    }


__all__ = [
    "DEFAULT_TARGET_TIER",
    "STANDARD_JOB_ID",
    "execute_lingzhuang_strengthening_task",
    "claim_lingzhuang_equipment_rewards",
    "open_lingzhuang_task_page",
    "LINGZHUANG_EQUIPMENT_TASK_SCENE",
    "LINGZHUANG_SCORE_TASK_SCENE",
    "resolve_lingzhuang_strengthening_activity",
]
