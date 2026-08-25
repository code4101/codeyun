from __future__ import annotations

"""Persist Lingxiao Xianhui's cumulative draw scatter points.

This Bothdraw variant has one activity-wide prize pool instead of a player
selected optional grand prize.  The stable activity id is consequently the
pool identity used by the shared scatter store.
"""

from datetime import datetime
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.activity.bothdraw_lottery_points import (
    BothdrawLotteryDataset,
    BothdrawLotteryPointSpec,
    list_bothdraw_lottery_points,
    record_bothdraw_lottery_point,
)


LINGXIAO_ACTIVITY_ID = 3001003
LINGXIAO_LOTTERY_NAMESPACE = "lingxiao-xianhui-draw-pool"
LINGXIAO_LOTTERY_POINT_SPEC = BothdrawLotteryPointSpec(
    namespace=LINGXIAO_LOTTERY_NAMESPACE,
    activity_label="凌霄仙会",
    entity_name="凌霄仙会累计抽数与大奖命中",
)


def lingxiao_instance_id(activity_end: datetime | str) -> str:
    """Use the observed activity end date as the stable temporary instance."""

    value = (
        activity_end
        if isinstance(activity_end, datetime)
        else datetime.fromisoformat(str(activity_end))
    )
    return f"lingxiao-xianhui-{value.date().isoformat()}"


def list_lingxiao_lottery_points(
    session: Session, *, instance_id: str
) -> BothdrawLotteryDataset:
    return list_bothdraw_lottery_points(
        session, spec=LINGXIAO_LOTTERY_POINT_SPEC, instance_id=instance_id
    )


def record_lingxiao_lottery_point(
    session: Session,
    *,
    snapshot: dict[str, Any],
    instance_id: str,
) -> BothdrawLotteryDataset:
    """Record a coherent Bothdraw observation without inventing an item row."""

    activity_id = int(snapshot.get("activity_id") or 0)
    if activity_id != LINGXIAO_ACTIVITY_ID:
        raise ValueError("当前 Runtime 快照不是凌霄仙会")
    normalized = dict(snapshot)
    normalized["selected_big_reward"] = {
        # The shared schema names this field after configurable activities.
        # Here it denotes the single, immutable activity prize pool.
        "library_id": activity_id,
        "item_id": activity_id,
        "name": "凌霄仙会活动奖池",
    }
    normalized.setdefault("selected_big_count", int(snapshot.get("y") or 0))
    normalized.setdefault("hit_big_total", int(snapshot.get("y") or 0))
    return record_bothdraw_lottery_point(
        session,
        spec=LINGXIAO_LOTTERY_POINT_SPEC,
        snapshot=normalized,
        instance_id=instance_id,
    )


__all__ = [
    "LINGXIAO_LOTTERY_NAMESPACE",
    "lingxiao_instance_id",
    "list_lingxiao_lottery_points",
    "record_lingxiao_lottery_point",
]
