from __future__ import annotations

from copy import deepcopy

import pytest

from backend.core.fanxiu.data_annotation.tasks.magic_invasion_native_auto import parse_magic_invasion_completed_count


@pytest.mark.parametrize("text, expected", [
    ("已完成预设的自动除魔次数！本次自动除魔完成除魔次数：100次", 100),
    ("已完成除魔次数：100次 获得除魔功勋", 100),
    ("本次自动除魔完成除魔次数：99次", 99),
    ("已完成预设的自动除魔次数100次", None),
    ("自动除魔次数：100次", None),
    ("已完成除魔次数", None),
])
def test_completed_count_requires_actual_completion_text(text, expected):
    assert parse_magic_invasion_completed_count(text) == expected

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


def test_recovered_completion_keeps_upper_bound_timing_through_ledger_reentry() -> None:
    store = _EvidenceStore()
    occurrence = _occurrence()
    armed = arm_magic_invasion_auto_timing_batch(
        occurrence, evidence_reader=store.read, evidence_writer=store.write,
    )
    arguments = dict(
        batch_id=armed["pending_batch"]["batch_id"], completed_exorcisms=100,
        magic_crystal_delta=25, ranking_score_delta=343535,
        duration_seconds=2104.77, duration_reliable=False,
        evidence_reader=store.read, evidence_writer=store.write,
    )
    settled = settle_magic_invasion_auto_timing_batch(occurrence, **arguments)
    assert settled["measurements"][0]["duration_reliable"] is False
    assert settled["model"]["duration_reliable"] is False
    assert settled["model"]["duration_is_upper_bound"] is True
    assert settle_magic_invasion_auto_timing_batch(occurrence, **arguments)["already_settled"] is True
    with pytest.raises(RuntimeError, match="重复结算"):
        settle_magic_invasion_auto_timing_batch(occurrence, **{**arguments, "duration_reliable": True})


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


@pytest.mark.parametrize("batch_id,count", [("other-batch", 100), ("batch-1", 99)])
def test_completion_trace_rejects_wrong_batch_or_count_before_writing(monkeypatch, batch_id, count):
    from backend.core.fanxiu.data_annotation.tasks import magic_invasion_native_auto as driver
    store = _EvidenceStore()
    state = arm_magic_invasion_auto_timing_batch(
        _occurrence(), armed_at_epoch=1000, batch_id_factory=lambda: "batch-1",
        evidence_reader=store.read, evidence_writer=store.write,
    )
    monkeypatch.setattr(driver, "load_magic_invasion_auto_timing_state", lambda occurrence: state)
    writes = store.writes
    with pytest.raises((RuntimeError, ValueError)):
        driver.record_magic_invasion_completion_trace(
            _occurrence(), batch_id=batch_id, completed_exorcisms=count,
            ranking_score_delta=1, frame_path="unused.png",
        )
    assert store.writes == writes
    assert state["pending_batch"]["batch_id"] == "batch-1"


@pytest.mark.parametrize("completed,running", [(99, False), (100, True), (100, None)])
def test_completion_rejects_partial_running_or_unknown_native_state(completed, running):
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion_native_auto import validate_magic_invasion_completion_count
    with pytest.raises(RuntimeError):
        validate_magic_invasion_completion_count(completed, 100, is_in_auto=running)


def test_completion_exit_preserves_each_native_page_contract():
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion_native_auto import (
        magic_invasion_completion_exit, validate_magic_invasion_completion_count,
    )
    validate_magic_invasion_completion_count(100, 100, is_in_auto=False)
    assert magic_invasion_completion_exit(873) == (873, "确定")
    assert magic_invasion_completion_exit(874) == (874, "继续")
    with pytest.raises(ValueError):
        magic_invasion_completion_exit(512)


@pytest.mark.parametrize("offset,admitted", [(-90.001, True), (-90, False), (-1, False), (0, False), (1, False)])
def test_final_start_gate_keeps_planner_reservation_at_real_configuration_end(offset, admitted):
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion_native_auto import magic_invasion_auto_start_window_open
    occurrence = _occurrence()
    fast_end = occurrence.end_time_ms / 1000 - 3600
    assert magic_invasion_auto_start_window_open(occurrence, now_epoch=fast_end + offset) is admitted


def test_configuration_crossing_start_deadline_never_arms_or_starts(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import magic_invasion_native_auto as driver
    from backend.core.fanxiu.data_annotation.tasks import magic_invasion_reward_ledger as ledger
    occurrence = _occurrence()
    deadline = occurrence.end_time_ms / 1000 - 3690
    clock = [deadline - 10]
    state = {"measurements": [], "pending_batch": None}
    monkeypatch.setattr(driver.time, "time", lambda: clock[0])
    monkeypatch.setattr(ledger, "load_magic_invasion_reward_state", lambda occurrence: state)

    def forbidden(*args, **kwargs):
        pytest.fail("expired configuration must not arm or start a batch")

    def configure(*args, **kwargs):
        clock[0] = deadline
        yield None
        return {"count": {"after": 100}}

    monkeypatch.setattr(ledger, "arm_magic_invasion_reward_batch", forbidden)
    monkeypatch.setattr(driver, "start_magic_invasion_auto", forbidden)
    monkeypatch.setattr(driver, "configure_magic_invasion_auto_for_batch", configure)
    monkeypatch.setattr(driver, "_wallet_snapshot", lambda occurrence: {"exchange_currency": 1})
    monkeypatch.setattr(driver, "read_magic_invasion_counters", lambda: {
        "is_in_auto": False, "ranking_score": 1, "pid": 1, "process_start_ticks": 2})
    from types import SimpleNamespace
    context = SimpleNamespace(report_progress=lambda *args, **kwargs: None)
    operation = driver.run_magic_invasion_auto_batch(context, occurrence, phase="formal", count=100)
    while True:
        try:
            next(operation)
        except StopIteration as done:
            assert done.value["reason"] == "fast_mode_window_closed"
            assert done.value["state"] is state
            break
    assert state["pending_batch"] is None


@pytest.mark.parametrize("scenes,running,expected", [
    ([512, 873], False, "completed"),
    ([512] * 5, False, "奖励页缺失"),
    ([512], None, "状态未知"),
])
def test_completion_waits_for_late_page_but_requires_proven_count(monkeypatch, scenes, running, expected):
    from types import SimpleNamespace
    from backend.core.fanxiu.data_annotation.tasks import magic_invasion_native_auto as driver
    clock = [0.0]
    scene_iter = iter(scenes)

    class Context:
        def wait_scene(self, *args, **kwargs):
            yield None
            return SimpleNamespace(scene_id=next(scene_iter))

        def wait_action_settle(self, seconds):
            clock[0] += 6.0
            yield None

        def ocr_text_in_shapes(self, *args, **kwargs):
            return "本次自动除魔完成除魔次数：100次"

    monkeypatch.setattr(driver.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(driver, "read_magic_invasion_counters", lambda: {"is_in_auto": running})
    operation = driver.wait_magic_invasion_auto_completion(Context(), count=100)
    if expected == "completed":
        while True:
            try:
                next(operation)
            except StopIteration as done:
                assert done.value["completed_exorcisms"] == 100
                assert done.value["terminal_scene_id"] == 873
                break
    else:
        with pytest.raises(RuntimeError, match=expected):
            list(operation)


@pytest.mark.parametrize("failures,error,expected_calls", [
    (1, "wait_click #512 [自动除魔图标] 超时，最后 0% OCR=", ["entry", "world", "entry"]),
    (2, "wait_click #512 [自动除魔图标] 超时，最后 0% OCR=", ["entry", "world", "entry"]),
    (1, "Runtime进程身份变化", ["entry"]),
])
def test_auto_entry_recovers_cover_once_without_starting_batch(monkeypatch, failures, error, expected_calls):
    from backend.core.fanxiu.data_annotation.tasks import magic_invasion_native_auto as driver
    calls = []

    def map_scene(context):
        yield None
        return 512

    def settings_scene(*args, **kwargs):
        yield None
        return (698, 100, None)

    class Context:
        def wait_click(self, scene, shape):
            assert (scene, shape) == (512, "自动除魔图标")
            calls.append("entry")
            yield None
            if calls.count("entry") <= failures:
                raise RuntimeError(error)

        def go_scene(self, scene):
            assert scene == 34
            calls.append("world")
            yield None

    monkeypatch.setattr(driver, "ensure_magic_invasion_map", map_scene)
    monkeypatch.setattr(driver, "_wait_scene", settings_scene)
    operation = driver.open_magic_invasion_auto_settings(Context())
    if failures == 1 and error.startswith("wait_click"):
        while True:
            try:
                next(operation)
            except StopIteration as done:
                assert done.value == 698
                break
    else:
        import re
        with pytest.raises(RuntimeError, match=re.escape(error)):
            list(operation)
    assert calls == expected_calls
