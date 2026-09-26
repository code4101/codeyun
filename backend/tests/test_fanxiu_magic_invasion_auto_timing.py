from __future__ import annotations

from copy import deepcopy

import pytest

from backend.core.fanxiu.activity.magic_invasion_auto_timing import (
    MAGIC_INVASION_AUTO_TIMING_BATCH_SIZE,
    build_magic_invasion_auto_measurement_model,
    is_magic_invasion_auto_crystal_yield_stable,
    measurement_from_mapping,
    measure_magic_invasion_auto_batch,
)
from backend.core.fanxiu.data_annotation.tasks.magic_invasion import (
    MagicInvasionOccurrence,
)
from backend.core.fanxiu.data_annotation.tasks.magic_invasion_native_auto_timing import (
    MAGIC_INVASION_AUTO_TIMING_KEY,
    load_magic_invasion_auto_measurement_state,
    arm_magic_invasion_auto_timing_batch,
    load_magic_invasion_auto_timing_state,
    plan_magic_invasion_auto_timing_step,
    settle_magic_invasion_auto_timing_batch,
    skip_magic_invasion_auto_measurement_for_occurrence,
)


def _occurrence(value: str = "9001") -> MagicInvasionOccurrence:
    return MagicInvasionOccurrence(value, 7, int(value), 1_000, 2_000, 1, "server")


class _EvidenceStore:
    def __init__(self) -> None:
        self.value: dict = {"sibling": {"kept": True}}
        self.writes = 0

    def read(self, _occurrence):
        return deepcopy(self.value)

    def write(self, _occurrence, updates, *, message):
        assert message
        self.value.update(deepcopy(dict(updates)))
        self.writes += 1
        return deepcopy(self.value)


def _measurement(
    index: int,
    magic_crystal_delta: int,
    duration: float = 100.0,
    ranking_score_delta: int = 500,
):
    return measure_magic_invasion_auto_batch(
        batch_id=f"b{index}",
        batch_index=index,
        requested_exorcisms=100,
        completed_exorcisms=100,
        magic_crystal_delta=magic_crystal_delta,
        ranking_score_delta=ranking_score_delta,
        duration_seconds=duration,
    )


def test_crystal_stability_reuses_inclusive_fifty_percent_boundary() -> None:
    first = _measurement(1, 100)

    assert is_magic_invasion_auto_crystal_yield_stable(first, _measurement(2, 150))
    assert not is_magic_invasion_auto_crystal_yield_stable(first, _measurement(2, 151))
    assert is_magic_invasion_auto_crystal_yield_stable(first, _measurement(2, 50))
    assert not is_magic_invasion_auto_crystal_yield_stable(
        _measurement(1, 0), _measurement(2, 10)
    )


def test_timing_model_uses_origin_anchored_batch_scatter_rate() -> None:
    model = build_magic_invasion_auto_measurement_model(
        [
            _measurement(1, 1000, 100.0, 500),
            _measurement(2, 1200, 120.0, 700),
        ]
    )

    assert model.points == (
        (100, 1000, 500, 100.0),
        (100, 1200, 700, 120.0),
    )
    assert model.magic_crystal_per_exorcism == pytest.approx(11)
    assert model.ranking_score_per_exorcism == pytest.approx(6)
    assert model.seconds_per_exorcism == pytest.approx(1.1)
    assert model.stable is True


def test_measurement_rejects_partial_or_non_100_batch() -> None:
    with pytest.raises(ValueError, match="固定为100次"):
        measure_magic_invasion_auto_batch(
            batch_id="x",
            batch_index=1,
            requested_exorcisms=99,
            completed_exorcisms=99,
            magic_crystal_delta=1,
            ranking_score_delta=1,
            duration_seconds=1,
        )
    with pytest.raises(ValueError, match="缺少字段.*magic_crystal_delta"):
        measurement_from_mapping(
            {
                "batch_id": "x",
                "batch_index": 1,
                "requested_exorcisms": 100,
                "completed_exorcisms": 100,
                "ranking_score_delta": 1,
                "duration_seconds": 1,
                "seconds_per_exorcism": 0.01,
            }
        )
    with pytest.raises(ValueError, match="未完整完成100次"):
        measure_magic_invasion_auto_batch(
            batch_id="x",
            batch_index=1,
            requested_exorcisms=100,
            completed_exorcisms=99,
            magic_crystal_delta=1,
            ranking_score_delta=1,
            duration_seconds=1,
        )


def test_arm_is_durable_and_retry_recovers_same_pending_batch() -> None:
    store = _EvidenceStore()
    occurrence = _occurrence()
    first = arm_magic_invasion_auto_timing_batch(
        occurrence,
        armed_at_epoch=1000,
        batch_id_factory=lambda: "batch-1",
        evidence_reader=store.read,
        evidence_writer=store.write,
    )
    retry = arm_magic_invasion_auto_timing_batch(
        occurrence,
        armed_at_epoch=2000,
        batch_id_factory=lambda: "must-not-be-used",
        evidence_reader=store.read,
        evidence_writer=store.write,
    )

    assert first["pending_batch"]["batch_id"] == "batch-1"
    assert first["recovered_pending"] is False
    assert retry["pending_batch"] == first["pending_batch"]
    assert retry["recovered_pending"] is True
    assert plan_magic_invasion_auto_timing_step(retry) == "recover_pending_batch"
    assert store.writes == 1
    assert store.value["sibling"] == {"kept": True}


def test_single_sample_allows_rewards_and_settlement_is_idempotent() -> None:
    store = _EvidenceStore()
    occurrence = _occurrence()
    arm_magic_invasion_auto_timing_batch(occurrence, armed_at_epoch=1001,
        batch_id_factory=lambda: "batch-1", evidence_reader=store.read, evidence_writer=store.write)
    args = dict(batch_id="batch-1", completed_exorcisms=100, magic_crystal_delta=1000,
                ranking_score_delta=500, duration_seconds=100.0,
                evidence_reader=store.read, evidence_writer=store.write)
    settled = settle_magic_invasion_auto_timing_batch(occurrence, **args)
    assert settled["status"] == "sampled"
    assert settled["reward_flow_allowed"] is True
    assert plan_magic_invasion_auto_timing_step(settled) == "proceed_to_rewards"
    writes = store.writes
    retry = settle_magic_invasion_auto_timing_batch(occurrence, **args)
    stopped = arm_magic_invasion_auto_timing_batch(occurrence,
        evidence_reader=store.read, evidence_writer=store.write)
    assert retry["already_settled"] is True
    assert stopped["pending_batch"] is None
    assert len(stopped["measurements"]) == 1
    assert store.writes == writes
    with pytest.raises(RuntimeError, match="重复结算.*冲突"):
        settle_magic_invasion_auto_timing_batch(occurrence, **{**args, "magic_crystal_delta": 1001})


def test_legacy_single_sample_migrates_without_another_challenge() -> None:
    store = _EvidenceStore()
    occurrence = _occurrence()
    arm_magic_invasion_auto_timing_batch(occurrence, armed_at_epoch=1001,
        batch_id_factory=lambda: "legacy-1", evidence_reader=store.read, evidence_writer=store.write)
    settle_magic_invasion_auto_timing_batch(occurrence, batch_id="legacy-1",
        completed_exorcisms=100, magic_crystal_delta=1000, ranking_score_delta=500,
        duration_seconds=100, evidence_reader=store.read, evidence_writer=store.write)
    raw = store.value[MAGIC_INVASION_AUTO_TIMING_KEY]
    raw.update(protocol_version=2, status="collecting", reward_flow_allowed=False)
    migrated = load_magic_invasion_auto_timing_state(occurrence, evidence_reader=store.read)
    assert migrated["protocol_version"] == 3
    assert migrated["reward_flow_allowed"] is True
    assert len(migrated["measurements"]) == 1
    assert plan_magic_invasion_auto_timing_step(migrated) == "proceed_to_rewards"


def test_overflow_skip_persists_no_fake_sample_and_allows_rewards() -> None:
    store = _EvidenceStore()
    occurrence = _occurrence()

    skipped = skip_magic_invasion_auto_measurement_for_occurrence(
        occurrence,
        reason="magic_crystal_overflow",
        observed_magic_crystal=310_000,
        skipped_at_epoch=1000,
        evidence_reader=store.read,
        evidence_writer=store.write,
    )
    writes = store.writes
    retry = skip_magic_invasion_auto_measurement_for_occurrence(
        occurrence,
        reason="magic_crystal_overflow",
        observed_magic_crystal=310_000,
        skipped_at_epoch=2000,
        evidence_reader=store.read,
        evidence_writer=store.write,
    )

    assert skipped["status"] == "skipped_this_occurrence"
    assert skipped["measurements"] == []
    assert skipped["model"] is None
    assert skipped["skipped_this_occurrence"] is True
    assert skipped["pending_next_occurrence"] is True
    assert skipped["reward_flow_allowed"] is True
    assert plan_magic_invasion_auto_timing_step(skipped) == "proceed_to_rewards"
    assert retry["already_skipped"] is True
    assert store.writes == writes


def test_overflow_skip_rejects_pending_irreversible_batch() -> None:
    store = _EvidenceStore()
    occurrence = _occurrence()
    arm_magic_invasion_auto_timing_batch(
        occurrence,
        armed_at_epoch=1000,
        batch_id_factory=lambda: "batch-1",
        evidence_reader=store.read,
        evidence_writer=store.write,
    )

    with pytest.raises(RuntimeError, match="pending_batch"):
        skip_magic_invasion_auto_measurement_for_occurrence(
            occurrence,
            reason="magic_crystal_overflow",
            observed_magic_crystal=310_000,
            skipped_at_epoch=1001,
            evidence_reader=store.read,
            evidence_writer=store.write,
        )


def test_state_fails_closed_for_wrong_occurrence_and_excessive_maximum() -> None:
    store = _EvidenceStore()
    occurrence = _occurrence()
    armed = arm_magic_invasion_auto_timing_batch(
        occurrence,
        armed_at_epoch=1000,
        batch_id_factory=lambda: "batch-1",
        evidence_reader=store.read,
        evidence_writer=store.write,
    )
    assert armed["status"] == "batch_pending"

    with pytest.raises(RuntimeError, match="其他 occurrence"):
        load_magic_invasion_auto_timing_state(
            _occurrence("9002"), evidence_reader=store.read
        )
    empty = _EvidenceStore()
    with pytest.raises(ValueError, match="2..5"):
        load_magic_invasion_auto_timing_state(
            occurrence,
            max_batches=6,
            evidence_reader=empty.read,
        )


def test_state_payload_is_json_ready() -> None:
    store = _EvidenceStore()
    state = arm_magic_invasion_auto_timing_batch(
        _occurrence(),
        armed_at_epoch=1000,
        batch_id_factory=lambda: "batch-1",
        evidence_reader=store.read,
        evidence_writer=store.write,
    )

    import json

    json.dumps(store.value[MAGIC_INVASION_AUTO_TIMING_KEY])
    assert state["batch_size"] == 100
    assert load_magic_invasion_auto_measurement_state(
        _occurrence(), evidence_reader=store.read
    )["status"] == "batch_pending"
