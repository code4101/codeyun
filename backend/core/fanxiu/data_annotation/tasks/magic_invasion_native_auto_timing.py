from __future__ import annotations

"""Durable, idempotent timing ledger for Magic native auto-exorcism batches.

This module owns no GUI operation.  A future/native-auto driver must arm a
batch before its irreversible start click, recover an existing pending marker
instead of replaying it, and settle only a proven 100-run terminal.
"""

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import time
from typing import Any
import uuid

from backend.core.fanxiu.activity.magic_invasion_auto_timing import (
    MAGIC_INVASION_AUTO_TIMING_BATCH_SIZE,
    MAGIC_INVASION_AUTO_TIMING_MAX_BATCHES,
    MAGIC_INVASION_AUTO_TIMING_PROTOCOL_VERSION,
    MagicInvasionAutoTimingMeasurement,
    measurement_from_mapping,
    measure_magic_invasion_auto_batch,
    timing_state_projection,
)
from backend.core.fanxiu.data_annotation.tasks.magic_invasion import (
    MagicInvasionOccurrence,
)


MAGIC_INVASION_AUTO_MEASUREMENT_KEY = "magic_invasion_native_auto_measurement"
# Compatibility name for the short-lived pre-yield implementation.  Both
# resolve to the corrected occurrence state; no parallel ledger is created.
MAGIC_INVASION_AUTO_TIMING_KEY = MAGIC_INVASION_AUTO_MEASUREMENT_KEY

EvidenceReader = Callable[[MagicInvasionOccurrence], Mapping[str, Any]]
EvidenceWriter = Callable[..., Mapping[str, Any]]


def _default_reader(occurrence: MagicInvasionOccurrence) -> Mapping[str, Any]:
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion import (
        load_magic_invasion_occurrence_evidence,
    )

    return load_magic_invasion_occurrence_evidence(occurrence)


def _default_writer(
    occurrence: MagicInvasionOccurrence,
    updates: Mapping[str, Any],
    *,
    message: str,
) -> Mapping[str, Any]:
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion import (
        store_magic_invasion_occurrence_evidence,
    )

    return store_magic_invasion_occurrence_evidence(
        occurrence,
        updates,
        message=message,
    )


def _maximum(value: int) -> int:
    maximum = int(value)
    if maximum < 2 or maximum > MAGIC_INVASION_AUTO_TIMING_MAX_BATCHES:
        raise ValueError(
            "魔道自动除魔测速最大批次必须在 2.."
            f"{MAGIC_INVASION_AUTO_TIMING_MAX_BATCHES} 之间"
        )
    return maximum


def _empty_state(
    occurrence: MagicInvasionOccurrence,
    *,
    max_batches: int,
) -> dict[str, Any]:
    return timing_state_projection(
        occurrence_id=occurrence.occurrence_id,
        measurements=(),
        pending_batch=None,
        max_batches=_maximum(max_batches),
        settled_batch_ids=(),
        skip=None,
    )


def _canonical_state(
    occurrence: MagicInvasionOccurrence,
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    if int(raw.get("protocol_version") or 0) != MAGIC_INVASION_AUTO_TIMING_PROTOCOL_VERSION:
        raise RuntimeError("魔道自动除魔测速状态版本不受支持")
    if str(raw.get("occurrence_id") or "") != occurrence.occurrence_id:
        raise RuntimeError("魔道自动除魔测速状态混入其他 occurrence")
    if int(raw.get("batch_size") or 0) != MAGIC_INVASION_AUTO_TIMING_BATCH_SIZE:
        raise RuntimeError("魔道自动除魔测速状态批次大小不是100")
    try:
        maximum = _maximum(int(raw.get("max_batches") or 0))
        raw_rows = raw.get("measurements") or ()
        if not isinstance(raw_rows, (list, tuple)) or not all(
            isinstance(row, Mapping) for row in raw_rows
        ):
            raise ValueError("measurements 不是对象列表")
        rows = [measurement_from_mapping(row) for row in raw_rows]
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"魔道自动除魔测速状态无效：{exc}") from exc
    if len(rows) > maximum:
        raise RuntimeError("魔道自动除魔测速样本超过最大批次安全边界")
    if [row.batch_index for row in rows] != list(range(1, len(rows) + 1)):
        raise RuntimeError("魔道自动除魔测速批次序号不连续")
    ids = [row.batch_id for row in rows]
    settled = [str(value) for value in raw.get("settled_batch_ids") or ()]
    if not all(settled) or len(set(settled)) != len(settled) or settled != ids:
        raise RuntimeError("魔道自动除魔测速 settled_batch_ids 与样本不一致")

    raw_pending = raw.get("pending_batch")
    pending: dict[str, Any] | None = None
    if raw_pending is not None:
        if not isinstance(raw_pending, Mapping):
            raise RuntimeError("魔道自动除魔测速 pending_batch 不是对象")
        pending = dict(raw_pending)
        if (
            len(rows) >= maximum
            or not str(pending.get("batch_id") or "")
            or str(pending.get("batch_id")) in settled
            or int(pending.get("batch_index") or 0) != len(rows) + 1
            or int(pending.get("requested_exorcisms") or 0)
            != MAGIC_INVASION_AUTO_TIMING_BATCH_SIZE
            or float(pending.get("armed_at_epoch") or 0) <= 0
        ):
            raise RuntimeError("魔道自动除魔测速 pending_batch 不连续或无效")

    raw_skip = raw.get("skip")
    skip: dict[str, Any] | None = None
    if raw_skip is not None:
        if not isinstance(raw_skip, Mapping):
            raise RuntimeError("魔道自动除魔测速 skip 不是对象")
        skip = dict(raw_skip)
        if (
            pending is not None
            or not str(skip.get("reason") or "")
            or float(skip.get("skipped_at_epoch") or 0) <= 0
            or skip.get("next_occurrence_required") is not True
        ):
            raise RuntimeError("魔道自动除魔测速 skip 状态无效")
        observed = skip.get("observed_magic_crystal")
        if observed is not None and int(observed) < 0:
            raise RuntimeError("魔道自动除魔测速 skip 魔晶观测无效")

    canonical = timing_state_projection(
        occurrence_id=occurrence.occurrence_id,
        measurements=rows,
        pending_batch=pending,
        max_batches=maximum,
        settled_batch_ids=settled,
        skip=skip,
    )
    if str(raw.get("status") or "") != canonical["status"]:
        raise RuntimeError("魔道自动除魔测速持久化状态与样本不一致")
    for key in (
        "stable",
        "skipped_this_occurrence",
        "pending_next_occurrence",
        "reward_flow_allowed",
        "model",
    ):
        if raw.get(key) != canonical[key]:
            raise RuntimeError(f"魔道自动除魔测速持久化字段与样本不一致：{key}")
    return canonical


def load_magic_invasion_auto_timing_state(
    occurrence: MagicInvasionOccurrence,
    *,
    max_batches: int = MAGIC_INVASION_AUTO_TIMING_MAX_BATCHES,
    evidence_reader: EvidenceReader | None = None,
) -> dict[str, Any]:
    reader = evidence_reader or _default_reader
    evidence = reader(occurrence)
    raw = evidence.get(MAGIC_INVASION_AUTO_TIMING_KEY)
    if raw is None:
        return _empty_state(occurrence, max_batches=max_batches)
    if not isinstance(raw, Mapping):
        raise RuntimeError("魔道自动除魔测速持久化状态不是对象")
    return _canonical_state(occurrence, raw)


def plan_magic_invasion_auto_timing_step(state: Mapping[str, Any]) -> str:
    status = str(state.get("status") or "")
    if status == "batch_pending":
        return "recover_pending_batch"
    if status == "collecting":
        return "arm_next_batch"
    if status in {"stable", "max_batches_reached", "skipped_this_occurrence"}:
        return "proceed_to_rewards"
    raise RuntimeError(f"未知魔道自动除魔测速状态：{status!r}")


def arm_magic_invasion_auto_timing_batch(
    occurrence: MagicInvasionOccurrence,
    *,
    max_batches: int = MAGIC_INVASION_AUTO_TIMING_MAX_BATCHES,
    armed_at_epoch: float | None = None,
    batch_id_factory: Callable[[], str] | None = None,
    evidence_reader: EvidenceReader | None = None,
    evidence_writer: EvidenceWriter | None = None,
) -> dict[str, Any]:
    reader = evidence_reader or _default_reader
    writer = evidence_writer or _default_writer
    state = load_magic_invasion_auto_timing_state(
        occurrence,
        max_batches=max_batches,
        evidence_reader=reader,
    )
    action = plan_magic_invasion_auto_timing_step(state)
    if action == "recover_pending_batch":
        return {**state, "recovered_pending": True}
    if action == "proceed_to_rewards":
        return {**state, "recovered_pending": False}

    epoch = float(armed_at_epoch if armed_at_epoch is not None else time.time())
    if epoch <= 0:
        raise ValueError("魔道自动除魔测速批次授权时间无效")
    factory = batch_id_factory or (lambda: uuid.uuid4().hex)
    batch_id = str(factory() or "")
    if not batch_id:
        raise ValueError("魔道自动除魔测速批次授权缺少 batch_id")
    marker = {
        "batch_id": batch_id,
        "batch_index": len(state["measurements"]) + 1,
        "requested_exorcisms": MAGIC_INVASION_AUTO_TIMING_BATCH_SIZE,
        "armed_at_epoch": epoch,
        "armed_at": datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
    }
    updated = timing_state_projection(
        occurrence_id=occurrence.occurrence_id,
        measurements=[measurement_from_mapping(row) for row in state["measurements"]],
        pending_batch=marker,
        max_batches=int(state["max_batches"]),
        settled_batch_ids=state["settled_batch_ids"],
        skip=None,
    )
    persisted = writer(
        occurrence,
        {MAGIC_INVASION_AUTO_TIMING_KEY: updated},
        message=f"魔道自动除魔测速第 {marker['batch_index']} 批已授权",
    )
    stored = persisted.get(MAGIC_INVASION_AUTO_TIMING_KEY)
    if not isinstance(stored, Mapping):
        raise RuntimeError("魔道自动除魔测速批次授权落盘回读失败")
    return {**_canonical_state(occurrence, stored), "recovered_pending": False}


def settle_magic_invasion_auto_timing_batch(
    occurrence: MagicInvasionOccurrence,
    *,
    batch_id: str,
    completed_exorcisms: int,
    magic_crystal_delta: int,
    ranking_score_delta: int,
    duration_seconds: float,
    evidence_reader: EvidenceReader | None = None,
    evidence_writer: EvidenceWriter | None = None,
) -> dict[str, Any]:
    reader = evidence_reader or _default_reader
    writer = evidence_writer or _default_writer
    state = load_magic_invasion_auto_timing_state(
        occurrence,
        evidence_reader=reader,
    )
    target_id = str(batch_id or "")
    if target_id in state["settled_batch_ids"]:
        stored = next(
            measurement_from_mapping(row)
            for row in state["measurements"]
            if str(row.get("batch_id") or "") == target_id
        )
        repeated = measure_magic_invasion_auto_batch(
            batch_id=target_id,
            batch_index=stored.batch_index,
            requested_exorcisms=stored.requested_exorcisms,
            completed_exorcisms=int(completed_exorcisms),
            magic_crystal_delta=int(magic_crystal_delta),
            ranking_score_delta=int(ranking_score_delta),
            duration_seconds=float(duration_seconds),
        )
        if repeated != stored:
            raise RuntimeError("魔道自动除魔测速重复结算与已落盘样本冲突")
        return {**state, "already_settled": True}
    pending = state.get("pending_batch")
    if not isinstance(pending, Mapping) or str(pending.get("batch_id") or "") != target_id:
        raise RuntimeError("魔道自动除魔测速结算未命中当前 pending_batch")
    measurement = measure_magic_invasion_auto_batch(
        batch_id=target_id,
        batch_index=int(pending["batch_index"]),
        requested_exorcisms=int(pending["requested_exorcisms"]),
        completed_exorcisms=int(completed_exorcisms),
        magic_crystal_delta=int(magic_crystal_delta),
        ranking_score_delta=int(ranking_score_delta),
        duration_seconds=float(duration_seconds),
    )
    rows = [measurement_from_mapping(row) for row in state["measurements"]]
    rows.append(measurement)
    updated = timing_state_projection(
        occurrence_id=occurrence.occurrence_id,
        measurements=rows,
        pending_batch=None,
        max_batches=int(state["max_batches"]),
        settled_batch_ids=[*state["settled_batch_ids"], target_id],
        skip=None,
    )
    persisted = writer(
        occurrence,
        {MAGIC_INVASION_AUTO_TIMING_KEY: updated},
        message=(
            f"魔道自动除魔测速第 {measurement.batch_index} 批已结算："
            f"{measurement.duration_seconds:.3f}s"
        ),
    )
    stored = persisted.get(MAGIC_INVASION_AUTO_TIMING_KEY)
    if not isinstance(stored, Mapping):
        raise RuntimeError("魔道自动除魔测速批次结算落盘回读失败")
    return {**_canonical_state(occurrence, stored), "already_settled": False}


def skip_magic_invasion_auto_measurement_for_occurrence(
    occurrence: MagicInvasionOccurrence,
    *,
    reason: str,
    observed_magic_crystal: int | None = None,
    skipped_at_epoch: float | None = None,
    evidence_reader: EvidenceReader | None = None,
    evidence_writer: EvidenceWriter | None = None,
) -> dict[str, Any]:
    """Skip sampling without fabricating a point, while allowing reward flow."""

    reader = evidence_reader or _default_reader
    writer = evidence_writer or _default_writer
    state = load_magic_invasion_auto_timing_state(
        occurrence,
        evidence_reader=reader,
    )
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        raise ValueError("魔道自动除魔测速跳过原因为空")
    observed = (
        int(observed_magic_crystal)
        if observed_magic_crystal is not None
        else None
    )
    if observed is not None and observed < 0:
        raise ValueError("魔道自动除魔测速跳过魔晶观测无效")
    existing_skip = state.get("skip")
    if isinstance(existing_skip, Mapping):
        if str(existing_skip.get("reason") or "") != normalized_reason:
            raise RuntimeError("魔道自动除魔测速重复跳过原因冲突")
        existing_observed = existing_skip.get("observed_magic_crystal")
        if observed is not None and (
            existing_observed is None or int(existing_observed) != observed
        ):
            raise RuntimeError("魔道自动除魔测速重复跳过魔晶观测冲突")
        return {**state, "already_skipped": True}
    if state.get("pending_batch") is not None:
        raise RuntimeError("魔道自动除魔仍有 pending_batch，拒绝直接跳过")
    if state["status"] in {"stable", "max_batches_reached"}:
        raise RuntimeError("魔道自动除魔测速已终结，拒绝改写为跳过")
    epoch = float(skipped_at_epoch if skipped_at_epoch is not None else time.time())
    if epoch <= 0:
        raise ValueError("魔道自动除魔测速跳过时间无效")
    marker = {
        "reason": normalized_reason,
        "observed_magic_crystal": observed,
        "skipped_at_epoch": epoch,
        "skipped_at": datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
        "next_occurrence_required": True,
    }
    rows = [measurement_from_mapping(row) for row in state["measurements"]]
    updated = timing_state_projection(
        occurrence_id=occurrence.occurrence_id,
        measurements=rows,
        pending_batch=None,
        max_batches=int(state["max_batches"]),
        settled_batch_ids=state["settled_batch_ids"],
        skip=marker,
    )
    persisted = writer(
        occurrence,
        {MAGIC_INVASION_AUTO_TIMING_KEY: updated},
        message=f"魔道自动除魔本 occurrence 跳过测速：{normalized_reason}",
    )
    stored = persisted.get(MAGIC_INVASION_AUTO_TIMING_KEY)
    if not isinstance(stored, Mapping):
        raise RuntimeError("魔道自动除魔测速跳过状态落盘回读失败")
    return {**_canonical_state(occurrence, stored), "already_skipped": False}


__all__ = [
    "MAGIC_INVASION_AUTO_MEASUREMENT_KEY",
    "MAGIC_INVASION_AUTO_TIMING_KEY",
    "arm_magic_invasion_auto_timing_batch",
    "load_magic_invasion_auto_timing_state",
    "plan_magic_invasion_auto_timing_step",
    "settle_magic_invasion_auto_timing_batch",
    "skip_magic_invasion_auto_measurement_for_occurrence",
]

# Business-facing names emphasize that crystal/score are the required sample
# and duration is only an accompanying observation.
load_magic_invasion_auto_measurement_state = load_magic_invasion_auto_timing_state
plan_magic_invasion_auto_measurement_step = plan_magic_invasion_auto_timing_step
arm_magic_invasion_auto_measurement_batch = arm_magic_invasion_auto_timing_batch
settle_magic_invasion_auto_measurement_batch = settle_magic_invasion_auto_timing_batch

__all__ += [
    "arm_magic_invasion_auto_measurement_batch",
    "load_magic_invasion_auto_measurement_state",
    "plan_magic_invasion_auto_measurement_step",
    "settle_magic_invasion_auto_measurement_batch",
]
