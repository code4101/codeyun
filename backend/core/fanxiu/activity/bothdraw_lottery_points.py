from __future__ import annotations

"""Parameterized persistence for authoritative Bothdraw lottery points."""

import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from backend.models import FanxiuPacketBusinessRecord
from backend.core.fanxiu.activity.lottery_observation_ledger import (
    validate_lottery_observation_append,
)


LOTTERY_POINT_DOMAIN = "relationship_sample"


@dataclass(frozen=True)
class BothdrawLotteryPointSpec:
    namespace: str
    activity_label: str
    entity_name: str


class BothdrawLotteryPoint(BaseModel):
    id: str
    captured_at: str
    observation_order: float | None = None
    x: int
    y: int
    dx: int
    dy: int
    selected_library_id: int
    selected_item_id: int
    selected_item_name: str
    activity_id: int | None = None
    observation_kind: str | None = None
    action_phase: str | None = None
    action_id: str | None = None
    ledger_protocol: str | None = None
    draw_mode: str | None = None
    requested_batch_size: int | None = None
    batch_size: int | None = None
    # Runtime-backed breakdown for ordinary-pool activities.  ``y`` remains
    # the chart ordinate; this list makes its multi-grand-prize composition
    # auditable without reading result-page artwork.
    big_prize_hits: list[dict[str, Any]] = Field(default_factory=list)
    available_currency: int | None = None
    available_draws: int | None = None
    cost_type: int | None = None
    cost_per_draw: int | None = None
    progress: int | None = None
    claimed_count: int | None = None
    claimed_ids: list[int] = Field(default_factory=list)
    selected_big_count: int | None = None
    selected_big_capacity: int | None = None
    selected_big_remaining: int | None = None
    hit_big: int | None = None
    hit_big_total: int | None = None
    available_currency_before: int | None = None
    available_currency_after: int | None = None
    available_draws_before: int | None = None
    available_draws_after: int | None = None


class BothdrawLotteryDataset(BaseModel):
    namespace: str
    entity_id: str
    samples: list[BothdrawLotteryPoint] = Field(default_factory=list)


def list_bothdraw_lottery_points(
    session: Session,
    *,
    spec: BothdrawLotteryPointSpec,
    instance_id: str,
) -> BothdrawLotteryDataset:
    prefix = f"{spec.namespace}:{instance_id}:"
    rows = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == LOTTERY_POINT_DOMAIN,
            FanxiuPacketBusinessRecord.entity_id == instance_id,
            FanxiuPacketBusinessRecord.record_key.startswith(prefix),
        )
    ).all()
    samples: list[BothdrawLotteryPoint] = []
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        try:
            samples.append(
                BothdrawLotteryPoint(
                    id=row.id,
                    captured_at=row.captured_at,
                    observation_order=_optional_float(
                        payload.get("observation_order") or row.updated_at
                    ),
                    x=int(payload["x"]),
                    y=int(payload["y"]),
                    dx=int(payload.get("dx") or 0),
                    dy=int(payload.get("dy") or 0),
                    selected_library_id=int(payload["selected_library_id"]),
                    selected_item_id=int(payload.get("selected_item_id") or 0),
                    selected_item_name=str(payload.get("selected_item_name") or ""),
                    activity_id=_optional_int(payload.get("activity_id")),
                    observation_kind=_optional_str(payload.get("observation_kind")),
                    action_phase=_optional_str(
                        payload.get("action_phase") or payload.get("observation_kind")
                    ),
                    action_id=_optional_str(payload.get("action_id")),
                    ledger_protocol=_optional_str(payload.get("ledger_protocol")),
                    draw_mode=_optional_str(payload.get("draw_mode")),
                    requested_batch_size=_optional_int(payload.get("requested_batch_size")),
                    batch_size=_optional_int(payload.get("batch_size")),
                    big_prize_hits=_big_prize_hits(payload.get("big_prize_hits")),
                    available_currency=_optional_int(payload.get("available_currency")),
                    available_draws=_optional_int(payload.get("available_draws")),
                    cost_type=_optional_int(payload.get("cost_type")),
                    cost_per_draw=_optional_int(payload.get("cost_per_draw")),
                    progress=_optional_int(payload.get("progress")),
                    claimed_count=_optional_int(payload.get("claimed_count")),
                    claimed_ids=_int_list(payload.get("claimed_ids")),
                    selected_big_count=_optional_int(payload.get("selected_big_count")),
                    selected_big_capacity=_optional_int(
                        payload.get("selected_big_capacity")
                    ),
                    selected_big_remaining=_optional_int(
                        payload.get("selected_big_remaining")
                    ),
                    hit_big=_optional_int(payload.get("hit_big")),
                    hit_big_total=_optional_int(payload.get("hit_big_total")),
                    available_currency_before=_optional_int(
                        payload.get("available_currency_before")
                    ),
                    available_currency_after=_optional_int(
                        payload.get("available_currency_after")
                    ),
                    available_draws_before=_optional_int(
                        payload.get("available_draws_before")
                    ),
                    available_draws_after=_optional_int(
                        payload.get("available_draws_after")
                    ),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    samples.sort(
        key=lambda item: (
            item.x,
            item.captured_at,
            item.observation_order or 0.0,
            item.id,
        )
    )
    return BothdrawLotteryDataset(
        namespace=spec.namespace,
        entity_id=instance_id,
        samples=samples,
    )


def record_bothdraw_lottery_point(
    session: Session,
    *,
    spec: BothdrawLotteryPointSpec,
    snapshot: dict[str, Any],
    instance_id: str,
) -> BothdrawLotteryDataset:
    if not snapshot.get("complete"):
        raise ValueError(str(snapshot.get("reason") or f"{spec.activity_label}抽奖运行态数据不完整"))
    captured_at = str(snapshot.get("captured_at") or "")
    if not captured_at:
        raise ValueError(f"{spec.activity_label}抽奖点缺少采集时间")
    x = int(snapshot.get("x") or 0)
    y = int(snapshot.get("y") or 0)
    selected = snapshot.get("selected_big_reward")
    if x < 0 or y < 0 or not isinstance(selected, dict):
        raise ValueError(f"{spec.activity_label}抽奖点缺少有效累计次数或已选大奖")
    library_id = int(selected.get("library_id") or 0)
    if library_id <= 0:
        raise ValueError(f"{spec.activity_label}当前已选大奖无法确定，拒绝记录概率样本")

    existing = list_bothdraw_lottery_points(
        session, spec=spec, instance_id=instance_id
    )
    ledger_append = validate_lottery_observation_append(
        (point.model_dump() for point in existing.samples),
        snapshot,
    )
    if ledger_append.idempotent:
        return existing
    same_x_points = [point for point in existing.samples if point.x == x]
    for same_x in same_x_points:
        if same_x.y != y or same_x.selected_library_id != library_id:
            raise ValueError(
                f"{spec.activity_label}抽奖点冲突：x={x} 已记录"
                f" y={same_x.y}/大奖={same_x.selected_library_id}，"
                f"本次 y={y}/大奖={library_id}"
            )
    action_id = _optional_str(snapshot.get("action_id"))
    action_phase = _optional_str(
        snapshot.get("action_phase") or snapshot.get("observation_kind")
    )
    if action_id and action_phase:
        same_observation = next(
            (
                point
                for point in same_x_points
                if point.action_id == action_id
                and (point.action_phase or point.observation_kind) == action_phase
            ),
            None,
        )
        if same_observation is not None:
            return existing
    elif same_x_points:
        # Compatibility for the old one-record-per-x format.  Old callers had
        # no action identity, so an identical cumulative point is idempotent.
        return existing

    if existing.samples:
        max_x = max(point.x for point in existing.samples)
        latest_points = [point for point in existing.samples if point.x == max_x]
        previous = latest_points[-1]
        if x < max_x or y < previous.y:
            raise ValueError(
                f"{spec.activity_label}抽奖累计值倒退："
                f"({previous.x},{previous.y}) -> ({x},{y})"
            )
        if previous.selected_library_id != library_id:
            raise ValueError("同一活动实例的已选大奖发生变化，拒绝混合概率样本")
        dx = x - max_x
        dy = y - previous.y
        if dx < 0 or dy < 0 or dy > dx:
            raise ValueError(f"{spec.activity_label}抽奖增量非法：dx={dx}, dy={dy}")
    else:
        dx = 0
        dy = 0

    now = time.time()
    payload = {
        "observation_order": now,
        "x": x,
        "y": y,
        "dx": dx,
        "dy": dy,
        "selected_library_id": library_id,
        "selected_item_id": int(selected.get("item_id") or 0),
        "selected_item_name": str(selected.get("name") or library_id),
        "activity_id": int(snapshot.get("activity_id") or 0),
        "observation_kind": str(snapshot.get("observation_kind") or "") or None,
        "action_phase": action_phase,
        "action_id": action_id,
        "ledger_protocol": _optional_str(snapshot.get("ledger_protocol")),
        "draw_mode": str(snapshot.get("draw_mode") or "") or None,
        "requested_batch_size": _optional_int(snapshot.get("requested_batch_size")),
        "batch_size": _optional_int(snapshot.get("batch_size")),
        "big_prize_hits": _big_prize_hits(snapshot.get("hit_big_prize_items")),
        "available_currency": _optional_int(snapshot.get("available_currency")),
        "available_draws": _optional_int(snapshot.get("available_draws")),
        "cost_type": _optional_int(snapshot.get("cost_type")),
        "cost_per_draw": _optional_int(snapshot.get("cost_per_draw")),
        "progress": _optional_int(snapshot.get("progress")),
        "claimed_count": _optional_int(snapshot.get("claimed_count")),
        "claimed_ids": _int_list(snapshot.get("claimed_ids")),
        "selected_big_count": _optional_int(snapshot.get("selected_big_count")),
        "selected_big_capacity": _optional_int(
            snapshot.get("selected_big_capacity")
        ),
        "selected_big_remaining": _optional_int(
            snapshot.get("selected_big_remaining")
        ),
        "hit_big": _optional_int(snapshot.get("hit_big")),
        "hit_big_total": _optional_int(snapshot.get("hit_big_total")),
        "available_currency_before": _optional_int(
            snapshot.get("available_currency_before")
        ),
        "available_currency_after": _optional_int(
            snapshot.get("available_currency_after")
        ),
        "available_draws_before": _optional_int(
            snapshot.get("available_draws_before")
        ),
        "available_draws_after": _optional_int(
            snapshot.get("available_draws_after")
        ),
        "runtime": {
            "selected_big_count": int(snapshot.get("selected_big_count") or 0),
            "selected_big_capacity": _optional_int(
                snapshot.get("selected_big_capacity")
            ),
            "selected_big_remaining": _optional_int(
                snapshot.get("selected_big_remaining")
            ),
            "hit_big": int(snapshot.get("hit_big") or 0),
            "hit_big_total": int(snapshot.get("hit_big_total") or 0),
        },
    }
    row = FanxiuPacketBusinessRecord(
        domain=LOTTERY_POINT_DOMAIN,
        record_key=_record_key(
            spec=spec,
            instance_id=instance_id,
            x=x,
            action_id=action_id,
            action_phase=action_phase,
        ),
        source_kind="read_only_runtime_before_after_gui_action",
        entity_id=instance_id,
        entity_name=spec.entity_name,
        captured_at=captured_at,
        captured_date=captured_at[:10],
        payload=payload,
        evidence=dict(snapshot.get("evidence") or {}),
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return list_bothdraw_lottery_points(
        session, spec=spec, instance_id=instance_id
    )


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [int(item) for item in value]


def _big_prize_hits(value: Any) -> list[dict[str, Any]]:
    """Normalize the Runtime grand-prize delta audit trail.

    A point may be a passive observation and legitimately carry no delta
    list.  When present, every row must be a concrete Runtime prize config
    with a positive increment; arbitrary result-screen payload is rejected.
    """

    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("大奖命中明细不是列表")
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("大奖命中明细存在非对象行")
        prize_id = int(row.get("id") or 0)
        increment = int(row.get("hit_increment") or 0)
        if prize_id <= 0 or increment <= 0 or prize_id in seen:
            raise ValueError("大奖命中明细缺少唯一 Runtime 配置或增量")
        seen.add(prize_id)
        normalized.append(
            {
                "id": prize_id,
                "reward": str(row.get("reward") or ""),
                "hit_count": int(row.get("hit_count") or 0),
                "hit_increment": increment,
            }
        )
    return normalized


def _record_key(
    *,
    spec: BothdrawLotteryPointSpec,
    instance_id: str,
    x: int,
    action_id: str | None,
    action_phase: str | None,
) -> str:
    if action_id and action_phase:
        return f"{spec.namespace}:{instance_id}:{x}:action:{action_id}:{action_phase}"
    return f"{spec.namespace}:{instance_id}:{x}"


__all__ = [
    "BothdrawLotteryDataset",
    "BothdrawLotteryPoint",
    "BothdrawLotteryPointSpec",
    "list_bothdraw_lottery_points",
    "record_bothdraw_lottery_point",
]
