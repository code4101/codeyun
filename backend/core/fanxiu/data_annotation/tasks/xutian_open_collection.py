from __future__ import annotations

"""Load and persist the current Xutian shop from an exact Runtime occurrence.

The schedule seed only describes when the activity exists. V_ShowList is loaded
by the game after entering its exchange page, so a database-only reconcile
cannot create the goods list. This checkpoint only observes; it never buys.
"""

from datetime import datetime
import threading
from typing import Any, Iterator

from sqlmodel import Session

from backend.core.fanxiu.activity.exchange_activity_registry import (
    collect_registered_exchange_activity,
)
from backend.core.fanxiu.activity.exchange_event import list_exchange_activity_snapshot
from backend.core.fanxiu.activity.ranking_reconcile import seed_ranking_occurrence
from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule
from backend.core.fanxiu.data_annotation.schedule_navigation import select_schedule_activity
from backend.db import engine


XUTIAN_HOME_SCENES = (452, 616)
XUTIAN_SHOP_SCENE = 739


def execute_xutian_open_collection_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: Any,
    captured_at: datetime,
    required_fact_watermark: datetime,
) -> Iterator[Any]:
    """Open this occurrence's shop and require a fresh, persisted goods list."""

    label = "虚天殿开启采集"
    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True, force_refresh=True,
    )
    if not schedule.get("available") or not schedule.get("complete"):
        raise RuntimeError(f"{label}：Runtime 日程不可用或不完整")

    with Session(engine) as session:
        activity = seed_ranking_occurrence(
            session, occurrence, captured_at=captured_at.isoformat(timespec="seconds"),
        )
        activity_id = str(activity.id)
        session.commit()

    context = runner._behavior_tree_context(
        ctx, ctx.get("asset_tree_path"), stop_event=stop_event,
    )
    yield from context.go_scene(34)
    yield from context.go_scene(66)
    ui_now = datetime.now().astimezone()
    selected = yield from select_schedule_activity(
        context,
        r"虚天(殿)?",
        day_offset=(occurrence.start_at.date() - ui_now.date()).days,
        enter=True,
        runtime_schedule=schedule,
        require_runtime_alignment=True,
        expected_activity_id=int(occurrence.activity_id),
        expected_runtime_id=str(occurrence.runtime_id),
        expected_cross_count=int(occurrence.cross_count),
        now=ui_now,
    )
    if not str(getattr(selected, "runtime_key", "") or ""):
        raise RuntimeError(f"{label}：#66 未回读精确 Runtime 实例标识")
    home = yield from context.wait_scene(
        [*XUTIAN_HOME_SCENES], wait=30.0, label=f"{label}：进入虚天殿",
    )
    home_scene = int(getattr(home, "scene_id", home))
    context.click_shape_center(home_scene, "兑换宝阁")
    yield from context.wait_scene(
        [XUTIAN_SHOP_SCENE], wait=20.0, label=f"{label}：进入兑换宝阁",
    )

    with Session(engine) as session:
        detail = collect_registered_exchange_activity(
            session, activity_type="xutian-palace", activity_id=activity_id,
        )
        session.commit()
        detail = list_exchange_activity_snapshot(
            session, activity_type="xutian-palace", activity_id=activity_id,
        ).selected_activity
    if detail is None or str(detail.id) != activity_id:
        raise RuntimeError(f"{label}：采集结果切换到其他活动实例")
    captured_shop_at = str(detail.shop_snapshot_captured_at or "")
    try:
        shop_time = datetime.fromisoformat(captured_shop_at)
    except ValueError:
        shop_time = None
    watermark = required_fact_watermark.astimezone()
    if shop_time is not None and shop_time.tzinfo is None:
        shop_time = shop_time.astimezone()
    if (
        not detail.shop_items
        or detail.shop_refresh_status != "updated"
        or shop_time is None
        or shop_time < watermark
    ):
        raise RuntimeError(
            f"{label}：本期兑换宝阁未形成新鲜完整商品清单："
            f"{detail.shop_refresh_reason or '商品数为零或快照过期'}"
        )

    yield from context.go_scene(34)
    return {
        "status": "completed",
        "activity_id": activity_id,
        "activity_type": "xutian-palace",
        "shop_item_count": len(detail.shop_items),
        "message": f"本期虚天殿兑换宝阁 {len(detail.shop_items)} 项已更新",
        "performed_actions": True,
    }


__all__ = ["execute_xutian_open_collection_checkpoint"]
