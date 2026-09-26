from __future__ import annotations

"""Pure 100-run yield/score/timing model for Magic native auto-exorcism."""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Any

from backend.core.fanxiu.activity.batch_stability import (
    positive_relative_change_is_stable,
)


MAGIC_INVASION_AUTO_TIMING_BATCH_SIZE = 100
MAGIC_INVASION_AUTO_TIMING_MAX_BATCHES = 5
MAGIC_INVASION_AUTO_TIMING_MAXIMUM_CHANGE = Fraction(1, 2)
MAGIC_INVASION_AUTO_TIMING_PROTOCOL_VERSION = 3


@dataclass(frozen=True)
class MagicInvasionAutoTimingMeasurement:
    batch_id: str
    batch_index: int
    requested_exorcisms: int
    completed_exorcisms: int
    magic_crystal_delta: int
    ranking_score_delta: int
    duration_seconds: float
    seconds_per_exorcism: float


@dataclass(frozen=True)
class MagicInvasionAutoTimingModel:
    points: tuple[tuple[int, int, int, float], ...]
    magic_crystal_per_exorcism: float
    ranking_score_per_exorcism: float
    seconds_per_exorcism: float
    stable: bool
    maximum_change: Fraction = MAGIC_INVASION_AUTO_TIMING_MAXIMUM_CHANGE


def measure_magic_invasion_auto_batch(
    *,
    batch_id: str,
    batch_index: int,
    requested_exorcisms: int,
    completed_exorcisms: int,
    magic_crystal_delta: int,
    ranking_score_delta: int,
    duration_seconds: float,
) -> MagicInvasionAutoTimingMeasurement:
    if not str(batch_id or ""):
        raise ValueError("魔道自动除魔测速批次缺少 batch_id")
    if int(batch_index) <= 0:
        raise ValueError("魔道自动除魔测速批次序号无效")
    if int(requested_exorcisms) != MAGIC_INVASION_AUTO_TIMING_BATCH_SIZE:
        raise ValueError("魔道自动除魔测速必须固定为100次一批")
    if int(completed_exorcisms) != int(requested_exorcisms):
        raise ValueError("魔道自动除魔测速未完整完成100次")
    crystal_delta = int(magic_crystal_delta)
    score_delta = int(ranking_score_delta)
    if crystal_delta < 0:
        raise ValueError("魔道自动除魔本批魔晶增量不能为负数")
    if score_delta < 0:
        raise ValueError("魔道自动除魔本批排行积分增量不能为负数")
    duration = float(duration_seconds)
    if duration <= 0:
        raise ValueError("魔道自动除魔测速耗时必须为正数")
    return MagicInvasionAutoTimingMeasurement(
        batch_id=str(batch_id),
        batch_index=int(batch_index),
        requested_exorcisms=int(requested_exorcisms),
        completed_exorcisms=int(completed_exorcisms),
        magic_crystal_delta=crystal_delta,
        ranking_score_delta=score_delta,
        duration_seconds=duration,
        seconds_per_exorcism=duration / int(completed_exorcisms),
    )


def is_magic_invasion_auto_crystal_yield_stable(
    previous: MagicInvasionAutoTimingMeasurement,
    current: MagicInvasionAutoTimingMeasurement,
    *,
    maximum_change: Fraction = MAGIC_INVASION_AUTO_TIMING_MAXIMUM_CHANGE,
) -> bool:
    if previous.completed_exorcisms != current.completed_exorcisms:
        raise ValueError("魔道自动除魔稳定性比较必须使用等大批次")
    # A zero-yield batch is a valid observation but cannot provide a positive
    # denominator or prove a stable earning rate.
    if previous.magic_crystal_delta <= 0 or current.magic_crystal_delta <= 0:
        return False
    return positive_relative_change_is_stable(
        previous.magic_crystal_delta,
        current.magic_crystal_delta,
        maximum_change=maximum_change,
    )


def build_magic_invasion_auto_measurement_model(
    measurements: Sequence[MagicInvasionAutoTimingMeasurement],
) -> MagicInvasionAutoTimingModel:
    rows = tuple(measurements)
    if not rows:
        raise ValueError("魔道自动除魔批次模型没有有效测速点")
    if any(
        row.requested_exorcisms != MAGIC_INVASION_AUTO_TIMING_BATCH_SIZE
        or row.completed_exorcisms != row.requested_exorcisms
        or row.duration_seconds <= 0
        for row in rows
    ):
        raise ValueError("魔道自动除魔批次模型包含无效测速点")
    denominator = sum(row.requested_exorcisms ** 2 for row in rows)
    numerator = sum(
        row.requested_exorcisms * row.duration_seconds for row in rows
    )
    stable = len(rows) >= 2 and is_magic_invasion_auto_crystal_yield_stable(
        rows[-2], rows[-1]
    )
    return MagicInvasionAutoTimingModel(
        points=tuple(
            (
                row.requested_exorcisms,
                row.magic_crystal_delta,
                row.ranking_score_delta,
                row.duration_seconds,
            )
            for row in rows
        ),
        magic_crystal_per_exorcism=sum(
            row.requested_exorcisms * row.magic_crystal_delta for row in rows
        ) / denominator,
        ranking_score_per_exorcism=sum(
            row.requested_exorcisms * row.ranking_score_delta for row in rows
        ) / denominator,
        seconds_per_exorcism=numerator / denominator,
        stable=stable,
    )


def measurement_from_mapping(
    raw: Mapping[str, Any],
) -> MagicInvasionAutoTimingMeasurement:
    required = {
        "batch_id",
        "batch_index",
        "requested_exorcisms",
        "completed_exorcisms",
        "magic_crystal_delta",
        "ranking_score_delta",
        "duration_seconds",
        "seconds_per_exorcism",
    }
    missing = sorted(required.difference(raw))
    if missing:
        raise ValueError("魔道自动除魔测速点缺少字段：" + ", ".join(missing))
    measurement = measure_magic_invasion_auto_batch(
        batch_id=str(raw.get("batch_id") or ""),
        batch_index=int(raw.get("batch_index") or 0),
        requested_exorcisms=int(raw.get("requested_exorcisms") or 0),
        completed_exorcisms=int(raw.get("completed_exorcisms") or 0),
        magic_crystal_delta=int(raw.get("magic_crystal_delta") or 0),
        ranking_score_delta=int(raw.get("ranking_score_delta") or 0),
        duration_seconds=float(raw.get("duration_seconds") or 0),
    )
    stored_rate = float(raw.get("seconds_per_exorcism") or 0)
    if stored_rate != measurement.seconds_per_exorcism:
        raise ValueError("魔道自动除魔测速点单位耗时不一致")
    return measurement


def timing_state_projection(
    *,
    occurrence_id: str,
    measurements: Sequence[MagicInvasionAutoTimingMeasurement],
    pending_batch: Mapping[str, Any] | None,
    max_batches: int,
    settled_batch_ids: Sequence[str],
    skip: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = tuple(measurements)
    stable = (
        pending_batch is None
        and len(rows) >= 2
        and skip is None
        and is_magic_invasion_auto_crystal_yield_stable(rows[-2], rows[-1])
    )
    if skip is not None:
        status = "skipped_this_occurrence"
    elif pending_batch is not None:
        status = "batch_pending"
    elif rows:
        status = "sampled"
    else:
        status = "collecting"
    model = build_magic_invasion_auto_measurement_model(rows) if rows else None
    return {
        "protocol_version": MAGIC_INVASION_AUTO_TIMING_PROTOCOL_VERSION,
        "occurrence_id": str(occurrence_id),
        "status": status,
        "batch_size": MAGIC_INVASION_AUTO_TIMING_BATCH_SIZE,
        "max_batches": int(max_batches),
        "measurements": [asdict(row) for row in rows],
        "settled_batch_ids": list(settled_batch_ids),
        "pending_batch": dict(pending_batch) if pending_batch is not None else None,
        "skip": dict(skip) if skip is not None else None,
        "skipped_this_occurrence": skip is not None,
        "pending_next_occurrence": skip is not None,
        "reward_flow_allowed": status
        in {"sampled", "skipped_this_occurrence"},
        "stable": stable,
        "model": (
            {
                "points": [list(point) for point in model.points],
                "seconds_per_exorcism": model.seconds_per_exorcism,
                "magic_crystal_per_exorcism": model.magic_crystal_per_exorcism,
                "ranking_score_per_exorcism": model.ranking_score_per_exorcism,
                "stable": model.stable,
                "maximum_change": float(model.maximum_change),
            }
            if model is not None
            else None
        ),
    }


__all__ = [
    "MAGIC_INVASION_AUTO_TIMING_BATCH_SIZE",
    "MAGIC_INVASION_AUTO_TIMING_MAX_BATCHES",
    "MAGIC_INVASION_AUTO_TIMING_MAXIMUM_CHANGE",
    "MagicInvasionAutoTimingMeasurement",
    "MagicInvasionAutoTimingModel",
    "build_magic_invasion_auto_measurement_model",
    "is_magic_invasion_auto_crystal_yield_stable",
    "measure_magic_invasion_auto_batch",
    "measurement_from_mapping",
    "timing_state_projection",
]
