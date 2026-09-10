from __future__ import annotations

"""Occurrence-scoped Magic Invasion initialization for the shared ranking Job."""

from datetime import datetime
import threading
from typing import Any, Iterator

from sqlmodel import Session

from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.data_annotation.schedule_navigation import (
    select_schedule_activity,
)
from backend.core.fanxiu.data_annotation.tasks.magic_invasion import (
    wait_magic_invasion_cover_after_schedule_entry,
)
from backend.core.fanxiu.data_annotation.tasks.magic_invasion_tail import (
    open_magic_invasion_exchange_tab,
    wait_magic_invasion_exchange_shop_ready,
)


MAGIC_HOME_SCENES = (509, 520, 521, 522)
MAGIC_SHOP_SCENE = 519
COMMON_SHOP_DIALOG_SCENE = 566


def _leave_loaded_magic_shop(context: Any, *, label: str) -> Iterator[Any]:
    """Normalize a retained Magic shop/dialog through annotated exits only."""

    _wait_scene_match = yield from context.wait_scene((COMMON_SHOP_DIALOG_SCENE,), wait=5.0, required=False)
    (dialog, _score, _frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if dialog == COMMON_SHOP_DIALOG_SCENE:
        context.click_shape_center(COMMON_SHOP_DIALOG_SCENE, "关闭详情")
        yield from wait_magic_invasion_exchange_shop_ready(
            context,
            label=f"{label}：关闭遗留购买框",
        )
    shop_ready = yield from wait_magic_invasion_exchange_shop_ready(
        context,
        label=f"{label}：识别遗留兑换宝阁",
        attempts=3,
        fail_if_missing=False,
    )
    if not shop_ready:
        return
    context.click_shape_center(MAGIC_SHOP_SCENE, "兑换宝阁标题")
    yield from context.wait_action_settle(0.35)
    context.click_shape_center(MAGIC_SHOP_SCENE, "返回")
    yield from context.wait_scene(
        [34, *MAGIC_HOME_SCENES],
        wait=20.0,
        label=f"{label}：离开遗留兑换宝阁",
    )


def _enter_exact_magic_occurrence(
    context: Any,
    occurrence: RankingOccurrence,
    *,
    runtime_schedule: dict[str, Any],
    label: str,
) -> Iterator[Any]:
    """Enter one Runtime-bound occurrence through the shared #66 navigator."""

    yield from _leave_loaded_magic_shop(context, label=label)
    _wait_scene_match = yield from context.wait_scene((34, 66), wait=5.0, required=False)
    (current, _score, _frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if current == 34:
        yield from context.go_scene(66)
    elif current != 66:
        yield from context.go_scene(34)
        yield from context.go_scene(66)

    ui_now = datetime.now().astimezone()
    selected = yield from select_schedule_activity(
        context,
        r"魔道入侵",
        day_offset=(occurrence.start_at.date() - ui_now.date()).days,
        enter=True,
        runtime_schedule=runtime_schedule,
        require_runtime_alignment=True,
        expected_activity_id=int(occurrence.activity_id),
        expected_runtime_id=str(occurrence.runtime_id),
        expected_cross_count=int(occurrence.cross_count),
        now=ui_now,
    )
    if not str(getattr(selected, "runtime_key", "") or ""):
        raise RuntimeError(f"{label}：#66 未回读精确 Runtime 实例标识")
    match = yield from wait_magic_invasion_cover_after_schedule_entry(
        context,
        [MAGIC_SHOP_SCENE, *MAGIC_HOME_SCENES],
        wait_seconds=30.0,
        label=f"{label}：等待魔道活动页",
    )
    scene = int(getattr(match, "scene_id", match))
    if scene != MAGIC_SHOP_SCENE:
        open_magic_invasion_exchange_tab(context, scene)
    yield from wait_magic_invasion_exchange_shop_ready(context, label=label)


def execute_magic_invasion_initialization_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: RankingOccurrence,
    captured_at: datetime,
    required_fact_watermark: datetime,
) -> Iterator[Any]:
    """Initialize one Magic occurrence through shared UI and Runtime adapters."""

    from backend.core.fanxiu.activity.exchange_event import (
        list_exchange_activity_snapshot,
    )
    from backend.core.fanxiu.activity.ranking_reconcile import (
        reconcile_ranking_occurrence,
        seed_ranking_occurrence,
    )
    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )
    from backend.db import engine

    label = "魔道00:30实例化"
    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True,
        force_refresh=True,
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError(f"{label}：Runtime 日程不可用或不完整")

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
    yield from _enter_exact_magic_occurrence(
        context,
        occurrence,
        runtime_schedule=dict(schedule),
        label=label,
    )

    with Session(engine) as session:
        result = reconcile_ranking_occurrence(
            session,
            occurrence,
            captured_at=captured_at.isoformat(timespec="seconds"),
            required_fact_watermark=required_fact_watermark,
        )
        detail = list_exchange_activity_snapshot(
            session,
            activity_type="magic-invasion",
            activity_id=activity_id,
        ).selected_activity
        detail_activity_id = str(detail.id) if detail is not None else ""
        shop_item_count = len(detail.shop_items) if detail is not None else 0
        shop_fact_fresh = bool(detail and detail.shop_fact_fresh)
        currency_fact_fresh = bool(detail and detail.currency_fact_fresh)
        session.commit()

    facts = dict(result.get("facts") or {})
    if str(result.get("activity_id") or "") != activity_id:
        raise RuntimeError(f"{label}：采集结果切换到其他活动实例")
    if (
        str(result.get("status") or "") != "completed"
        or detail is None
        or detail_activity_id != activity_id
        or shop_item_count <= 0
        or not shop_fact_fresh
        or not currency_fact_fresh
        or int(facts.get("reward_tier_count") or 0) <= 0
    ):
        reason = str(result.get("message") or "").strip()
        raise RuntimeError(
            f"{label}：本期兑换宝阁、钱包或奖励档次未形成完整新鲜事实"
            + (f"：{reason}" if reason else "")
        )

    context.click_shape_center(MAGIC_SHOP_SCENE, "返回")
    landed = yield from context.wait_scene(
        [34, *MAGIC_HOME_SCENES],
        wait=20.0,
        label=f"{label}：离开兑换宝阁",
    )
    if int(getattr(landed, "scene_id", landed)) != 34:
        yield from context.go_scene(34)
    return {
        **result,
        "phase": "initialization",
        "performed_actions": True,
        "message": (
            f"魔道 occurrence {occurrence.runtime_id} 初始化完成："
            f"兑换宝阁 {shop_item_count} 项，奖励档次 "
            f"{int(facts.get('reward_tier_count') or 0)} 档"
        ),
    }


__all__ = ["execute_magic_invasion_initialization_checkpoint"]
