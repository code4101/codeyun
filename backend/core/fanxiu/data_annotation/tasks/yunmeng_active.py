from __future__ import annotations

"""云梦试剑开启后的机会式事实采集。

云梦在统一玩法榜里只有「收尾兑换」是生产 checkpoint，活动期间没有任何作业真的
打开过云梦页面，运行态兑换页因此长期没有加载者，兑换宝阁与钱包事实一直是空的。

本模块补上活动开启后的一次「加载 + 采集」：进入云梦主页 → 打开榜单（加载个人/
位面）→ 打开兑换宝阁（加载商店与钱包）→ 只写回已加载的运行态事实。这里不挑战、
不购买；购买仍然只属于收尾 checkpoint。
"""

from datetime import date, datetime
import threading
from typing import Any, Iterator

from sqlmodel import Session

from backend.db import engine


YUNMENG_HOME_SCENE = 558
YUNMENG_RANK_SCENE = 565
YUNMENG_SHOP_SCENE = 559


def _as_local(value: datetime) -> datetime:
    return value.astimezone() if value.tzinfo is not None else value


def _parse_captured_at(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return _as_local(parsed)


def enter_yunmeng_activity_home(
    context: Any,
    *,
    target_date: date,
) -> Iterator[Any]:
    """Normalize through the world anchor and open the dated 云梦试剑 instance.

    ``#66`` always anchors its date columns on the real current day, so the
    occurrence date must be expressed as ``day_offset`` while ``now`` stays the
    GUI's current moment.  Passing a historical occurrence date as ``now`` once
    shifted the anchor too and opened the 常规活动 overlay instead of the
    activity page.
    """

    from backend.core.fanxiu.data_annotation.schedule_navigation import (
        select_schedule_activity,
    )

    ui_now = datetime.now().astimezone()
    yield from context.go_scene(34)
    yield from context.go_scene(66)
    yield from select_schedule_activity(
        context,
        r"云梦试剑",
        day_offset=(target_date - ui_now.date()).days,
        enter=True,
        require_runtime_alignment=True,
        now=ui_now,
    )


def load_yunmeng_pages(context: Any, occurrence: Any, *, label: str) -> Iterator[Any]:
    """Load this occurrence's rank and shop pages so Runtime facts exist."""

    yield from enter_yunmeng_activity_home(
        context,
        target_date=occurrence.end_at.astimezone().date(),
    )
    yield from context.wait_scene_or_ocr(
        YUNMENG_HOME_SCENE,
        lambda text: "云梦试剑" in text and "挑战次数" in text,
        timeout=30.0,
        label=f"{label}：等待云梦主页",
    )
    context.click_shape_center(YUNMENG_HOME_SCENE, "榜单")
    yield from context.wait_scene(
        [YUNMENG_RANK_SCENE], wait=20.0, label=f"{label}：进入榜单",
    )
    context.click_shape_center(YUNMENG_RANK_SCENE, "个人")
    yield from context.wait_action_settle(1.0)
    context.click_shape_center(YUNMENG_RANK_SCENE, "位面")
    yield from context.wait_action_settle(1.0)
    context.click_shape_center(YUNMENG_RANK_SCENE, "云梦试剑")
    yield from context.wait_scene_or_ocr(
        YUNMENG_HOME_SCENE,
        lambda text: "挑战次数" in text,
        timeout=20.0,
        label=f"{label}：返回云梦主页",
    )
    context.click_shape_center(YUNMENG_HOME_SCENE, "兑换宝阁")
    yield from context.wait_scene(
        [YUNMENG_SHOP_SCENE], wait=20.0, label=f"{label}：进入兑换宝阁",
    )


def execute_yunmeng_open_collection_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: Any,
    captured_at: datetime,
    required_fact_watermark: datetime,
) -> Iterator[Any]:
    """在活动开启后加载并采集云梦兑换宝阁事实；只采集，不挑战、不购买。"""

    from backend.core.fanxiu.activity.exchange_event import (
        list_exchange_activity_snapshot,
    )
    from backend.core.fanxiu.activity.ranking_reconcile import (
        reconcile_ranking_occurrence,
        seed_ranking_occurrence,
    )
    from backend.core.fanxiu.activity.yunmeng_exchange import (
        collect_and_store_yunmeng_shop_snapshot,
    )

    watermark = _as_local(required_fact_watermark)
    label = "云梦开启采集"
    with Session(engine) as session:
        activity = seed_ranking_occurrence(
            session,
            occurrence,
            captured_at=captured_at.isoformat(timespec="seconds"),
        )
        activity_id = str(activity.id)
        detail = list_exchange_activity_snapshot(
            session,
            activity_type="yunmeng-trial",
            activity_id=activity_id,
        ).selected_activity
        snapshot_time = _parse_captured_at(detail.shop_snapshot_captured_at)
        shop_ready = bool(
            detail.shop_items and snapshot_time and snapshot_time >= watermark
        )
        session.commit()

    context = None
    if not shop_ready:
        context = runner._behavior_tree_context(
            ctx,
            ctx.get("asset_tree_path"),
            stop_event=stop_event,
        )
        yield from load_yunmeng_pages(context, occurrence, label=label)
        with Session(engine) as session:
            collect_and_store_yunmeng_shop_snapshot(
                session,
                activity_id=activity_id,
            )
            session.commit()
            detail = list_exchange_activity_snapshot(
                session,
                activity_type="yunmeng-trial",
                activity_id=activity_id,
            ).selected_activity
            if str(detail.id) != activity_id or not detail.shop_items:
                raise RuntimeError(f"{label}：兑换宝阁未保存完整目标实例数据")

    with Session(engine) as session:
        result = reconcile_ranking_occurrence(
            session,
            occurrence,
            captured_at=captured_at.isoformat(timespec="seconds"),
            required_fact_watermark=watermark,
            collect_live_facts=False,
        )
        session.commit()
    if str(result.get("activity_id") or "") != activity_id:
        raise RuntimeError(f"{label}：采集结果切换到其他活动实例")
    if context is not None:
        yield from context.go_scene(34)
    return result


__all__ = [
    "YUNMENG_HOME_SCENE",
    "YUNMENG_RANK_SCENE",
    "YUNMENG_SHOP_SCENE",
    "enter_yunmeng_activity_home",
    "execute_yunmeng_open_collection_checkpoint",
    "load_yunmeng_pages",
]
