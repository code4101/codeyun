from __future__ import annotations

"""Persist weekly Penglai Xianzang grand-prize draw relationship points."""

import time
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from backend.core.fanxiu.activity.bothdraw_lottery_points import (
    BothdrawLotteryDataset,
    BothdrawLotteryPoint,
    BothdrawLotteryPointSpec,
    list_bothdraw_lottery_points,
    record_bothdraw_lottery_point,
)
from backend.models import FanxiuPacketBusinessRecord


XIANZANG_LOTTERY_POINT_DOMAIN = "relationship_sample"
XIANZANG_LOTTERY_NAMESPACE = "penglai-xianzang-draw-grand-prize"
XIANZANG_AVAILABILITY_DOMAIN = "activity_availability"
XIANZANG_AVAILABILITY_NAMESPACE = "penglai-xianzang"


XianzangLotteryPoint = BothdrawLotteryPoint
XianzangLotteryDataset = BothdrawLotteryDataset
XIANZANG_LOTTERY_POINT_SPEC = BothdrawLotteryPointSpec(
    namespace=XIANZANG_LOTTERY_NAMESPACE,
    activity_label="蓬莱仙藏",
    entity_name="蓬莱仙藏抽数与大奖次数",
)


def xianzang_week_instance_id(value: datetime | str | None = None) -> str:
    """Return the Thursday-scoped business instance containing ``value``."""

    if value is None:
        current = datetime.now().astimezone()
    elif isinstance(value, datetime):
        current = value.astimezone() if value.tzinfo is not None else value
    else:
        current = datetime.fromisoformat(str(value))
    thursday = current.date() - timedelta(days=(current.weekday() - 3) % 7)
    return f"penglai-xianzang-{thursday.isoformat()}"


def list_xianzang_lottery_points(
    session: Session,
    *,
    instance_id: str,
) -> XianzangLotteryDataset:
    return list_bothdraw_lottery_points(
        session,
        spec=XIANZANG_LOTTERY_POINT_SPEC,
        instance_id=instance_id,
    )


def record_xianzang_lottery_point(
    session: Session,
    *,
    snapshot: dict[str, Any],
    instance_id: str | None = None,
) -> XianzangLotteryDataset:
    """Persist one point through the shared monotonic paired-action ledger."""

    captured_at = str(snapshot.get("captured_at") or datetime.now().astimezone().isoformat(timespec="seconds"))
    resolved_instance_id = str(instance_id or xianzang_week_instance_id(captured_at))
    return record_bothdraw_lottery_point(
        session,
        spec=XIANZANG_LOTTERY_POINT_SPEC,
        snapshot={**snapshot, "captured_at": captured_at},
        instance_id=resolved_instance_id,
    )


def record_xianzang_availability(
    session: Session,
    *,
    available: bool,
    reason: str,
    captured_at: str | None = None,
    instance_id: str | None = None,
) -> str:
    observed_at = str(captured_at or datetime.now().astimezone().isoformat(timespec="seconds"))
    resolved_instance_id = str(instance_id or xianzang_week_instance_id(observed_at))
    record_key = f"{XIANZANG_AVAILABILITY_NAMESPACE}:{resolved_instance_id}"
    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == XIANZANG_AVAILABILITY_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == record_key,
        )
    ).first()
    now = time.time()
    if row is None:
        row = FanxiuPacketBusinessRecord(
            domain=XIANZANG_AVAILABILITY_DOMAIN,
            record_key=record_key,
            entity_id=resolved_instance_id,
            entity_name="蓬莱仙藏活动可用性",
        )
    row.source_kind = "bounded_world_menu_ocr"
    row.captured_at = observed_at
    row.captured_date = observed_at[:10]
    row.payload = {"available": bool(available), "reason": str(reason or "")}
    row.updated_at = now
    session.add(row)
    session.commit()
    return resolved_instance_id


__all__ = [
    "XianzangLotteryDataset",
    "XianzangLotteryPoint",
    "list_xianzang_lottery_points",
    "record_xianzang_availability",
    "record_xianzang_lottery_point",
    "xianzang_week_instance_id",
]
