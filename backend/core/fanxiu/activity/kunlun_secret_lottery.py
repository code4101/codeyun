from __future__ import annotations

"""Persist Kunlun Secret draw/grand-prize relationship points."""

from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.activity.bothdraw_lottery_points import (
    BothdrawLotteryDataset,
    BothdrawLotteryPointSpec,
    list_bothdraw_lottery_points,
    record_bothdraw_lottery_point,
)


KUNLUN_LOTTERY_NAMESPACE = "kunlun-secret-draw-grand-prize"
KUNLUN_LOTTERY_POINT_SPEC = BothdrawLotteryPointSpec(
    namespace=KUNLUN_LOTTERY_NAMESPACE,
    activity_label="昆仑秘藏",
    entity_name="昆仑秘藏抽数与大奖次数",
)


def kunlun_week_instance_id(value: datetime | str | None = None) -> str:
    """Return the Thursday-scoped Kunlun business instance containing value."""

    if value is None:
        current = datetime.now().astimezone()
    elif isinstance(value, datetime):
        current = value.astimezone() if value.tzinfo is not None else value
    else:
        current = datetime.fromisoformat(str(value))
    thursday = current.date() - timedelta(days=(current.weekday() - 3) % 7)
    return f"kunlun-secret-{thursday.isoformat()}"


def list_kunlun_lottery_points(
    session: Session,
    *,
    instance_id: str,
) -> BothdrawLotteryDataset:
    return list_bothdraw_lottery_points(
        session,
        spec=KUNLUN_LOTTERY_POINT_SPEC,
        instance_id=instance_id,
    )


def record_kunlun_lottery_point(
    session: Session,
    *,
    snapshot: dict[str, Any],
    instance_id: str | None = None,
) -> BothdrawLotteryDataset:
    captured_at = str(snapshot.get("captured_at") or datetime.now().astimezone().isoformat(timespec="seconds"))
    resolved_instance_id = str(instance_id or kunlun_week_instance_id(captured_at))
    return record_bothdraw_lottery_point(
        session,
        spec=KUNLUN_LOTTERY_POINT_SPEC,
        snapshot=snapshot,
        instance_id=resolved_instance_id,
    )


__all__ = [
    "KUNLUN_LOTTERY_NAMESPACE",
    "list_kunlun_lottery_points",
    "kunlun_week_instance_id",
    "record_kunlun_lottery_point",
]
