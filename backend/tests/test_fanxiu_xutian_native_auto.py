from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

import backend.core.fanxiu.data_annotation.tasks.xutian_native_auto as xutian_auto
from backend.models import FanxiuExchangeActivity
from backend.core.fanxiu.data_annotation.tasks.xutian_native_auto import (
    build_xutian_batch_observation,
    plan_xutian_native_batch,
    validate_xutian_auto_settings,
    xutian_target_quality_keys,
)


@pytest.fixture
def xutian_engine(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'xutian-evidence.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("backend.db.engine", engine)
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.standard_observation.store_runtime_currency_fact",
        lambda _session, _snapshot: {"created": 0, "updated": 0},
    )
    return engine


def _activity(*, offset_start: int = -1, offset_end: int = 1, cross: int = 8):
    today = date.today()
    start = today + timedelta(days=offset_start)
    end = today + timedelta(days=offset_end)
    return FanxiuExchangeActivity(
        activity_type="xutian-palace",
        cross_count=cross,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        game_rank_activity_id=80_891,
        game_shop_base_id=708,
        currency_type=12,
        currency_name="纳元晶",
        evidence={
            "game_activity_id": 8_000_001 + cross,
            "period_record_id": f"runtime:xutian-{cross}-{start.isoformat()}",
            "period_start_time": 1_774_800_000_000 + cross,
            "period_end_time": 1_774_886_400_000 + cross,
            "period_close_panel_time": 1_774_972_800_000 + cross,
        },
    )


def _runtime_identity(*, heaven: int = 3):
    return {
        "pid": 123,
        "process_start_ticks": 456,
        "current_heaven": heaven,
        "completed_challenges_before": 0,
    }


def _marker(batch_id: str = "batch-1", *, requested: int = 10, heaven: int = 3):
    return {
        "batch_id": batch_id,
        "started_at": "2026-08-26T10:00:00+08:00",
        "started_epoch": 1.0,
        "requested_challenges": requested,
        "current_heaven": heaven,
        "runtime_batch_identity": _runtime_identity(heaven=heaven),
        "wallet_before": {
            "exchange_currency": 1_000,
            "cumulative_currency": 2_000,
        },
        "resource_before": {
            "challenge": {"count": 100},
            "explore": {"count": 100},
            "auto_progress": {"running": False, "completed_challenges": 0},
        },
    }


def _resource(*, running: bool = False, completed: int = 10, heaven: int = 3):
    return {
        "source": "runtime_memory",
        "current_heaven": heaven,
        "challenge": {"count": 90},
        "explore": {"count": 90},
        "auto_progress": {
            "running": running,
            "completed_challenges": completed,
        },
        "evidence": {"pid": 999, "process_start_ticks": 1_000},
    }


def _wallet(*, current_delta: int = 1_200, cumulative_delta: int | None = None):
    cumulative_delta = current_delta if cumulative_delta is None else cumulative_delta
    return {
        "exchange_currency": 1_000 + current_delta,
        "cumulative_currency": 2_000 + cumulative_delta,
        "currency_type": 12,
        "currency_amount": 1_000 + current_delta,
        "currency_borrow": 0,
        "captured_at": "2026-08-26T10:01:00+08:00",
    }


def _insert(engine, *activities):
    with Session(engine) as session:
        for activity in activities:
            session.add(activity)
        session.commit()
        for activity in activities:
            session.refresh(activity)


def _stored_activity(engine, activity_id: str):
    with Session(engine) as session:
        return session.get(FanxiuExchangeActivity, activity_id)
from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)


def _snapshot(*, count: int = 10) -> dict:
    raw = {
        str(key): {
            "use_item": True,
            "use_item_3": True,
            "use_item_4": True,
        }
        for key in (6, 7, 15)
    }
    return {
        "special_options": {
            "find_demon_available": False,
            "native_soul_lock_available": False,
            "find_demon_selected": False,
            "native_soul_lock_selected": False,
        },
        "available_quality_keys": [3, 4, 5, 6, 7, 15, 99],
        "auto_settings": {
            "quality_3": False,
            "quality_4": False,
            "quality_5": False,
            "quality_6": True,
            "quality_7": True,
            "quality_8": True,
            "quality_player": False,
            "refill_challenge": True,
            "refill_explore": True,
            "quick_auto": True,
            "skip_animation": True,
            "challenge_count": count,
        },
        "evidence": {"auto_settings_raw": raw},
    }


def test_target_quality_policy_starts_at_xianpin_and_maps_quality_8_key():
    assert xutian_target_quality_keys([3, 4, 5, 6, 7, 15, 99]) == {6, 7, 15}


@pytest.mark.parametrize(
    ("task_type", "retired_job_id"),
    [
        ("xutian_palace_native_auto", "xutian-palace-native-auto"),
        ("yunmeng_trial_auto_challenge", "yunmeng-trial-auto-challenge"),
        ("yunmeng_tail", "yunmeng-tail"),
    ],
)
def test_gameplay_subtask_is_runtime_callable_but_not_a_scheduler_job(
    task_type: str,
    retired_job_id: str,
):
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(task_type)
    assert definition is not None
    assert definition.scheduler_supported is False
    assert definition.standard_job is False
    assert all(
        item["id"] != retired_job_id
        for item in default_data_annotation_scheduler_tasks()
    )


def test_runtime_settings_validator_requires_both_groups_and_every_boost():
    snapshot = _snapshot()
    assert validate_xutian_auto_settings(snapshot, requested_challenges=10) == []

    snapshot["auto_settings"]["skip_animation"] = False
    snapshot["evidence"]["auto_settings_raw"]["7"]["use_item_3"] = False
    mismatches = validate_xutian_auto_settings(snapshot, requested_challenges=10)

    assert "skip_animation 应为 True" in mismatches
    assert "quality_7.use_item_3 应开启" in mismatches


def test_settings_validator_fails_closed_for_incomplete_runtime_snapshot():
    assert validate_xutian_auto_settings({}, requested_challenges=10) == [
        "Runtime 自动配置快照不完整"
    ]


def test_xutian_batch_planner_starts_with_ten_then_halves_estimate():
    probe = plan_xutian_native_batch(required_new_currency=167_210)
    next_batch = plan_xutian_native_batch(
        required_new_currency=160_000,
        measured_currency_delta=1_000,
        measured_challenges=10,
    )

    assert (probe.requested_challenges, probe.planning_mode) == (10, "probe")
    assert (next_batch.requested_challenges, next_batch.planning_mode) == (
        500,
        "capped_geometric_half",
    )


def test_batch_observation_requires_runtime_terminal_and_positive_wallet_delta():
    before = {
        "challenge": {"count": 24_047},
        "explore": {"count": 24_361},
        "auto_progress": {"running": False, "completed_challenges": 0},
    }
    after = {
        "challenge": {"count": 24_037},
        "explore": {"count": 24_351},
        "auto_progress": {"running": False, "completed_challenges": 10},
    }

    row = build_xutian_batch_observation(
        requested_challenges=10,
        before_resource=before,
        after_resource=after,
        currency_before=25_290,
        currency_after=26_490,
        elapsed_seconds=4.0,
    )

    assert row["currency_delta"] == 1_200
    assert row["currency_per_challenge"] == 120
    assert row["seconds_per_challenge"] == 0.4
    assert row["challenge_count_before"] == 24_047
    assert row["challenge_count_after"] == 24_037

    after["auto_progress"]["running"] = True
    with pytest.raises(ValueError, match="仍在运行"):
        build_xutian_batch_observation(
            requested_challenges=10,
            before_resource=before,
            after_resource=after,
            currency_before=25_290,
            currency_after=26_490,
            elapsed_seconds=4.0,
        )


def test_arm_requires_one_current_activity(xutian_engine):
    with pytest.raises(RuntimeError, match="当前活动实例不存在"):
        xutian_auto._arm_pending_xutian_batch(_marker())

    activity = _activity()
    _insert(xutian_engine, activity)
    activity_id, persisted = xutian_auto._arm_pending_xutian_batch(_marker())

    assert activity_id == activity.id
    assert persisted["occurrence"]["activity_record_id"] == activity.id
    assert persisted["occurrence"]["runtime_record_id"].startswith("runtime:xutian")
    assert xutian_auto._load_pending_xutian_batch() == (activity.id, persisted)


def test_arm_fails_closed_for_overlapping_current_activities(xutian_engine):
    _insert(xutian_engine, _activity(cross=8), _activity(cross=16))

    with pytest.raises(RuntimeError, match="occurrence 不唯一"):
        xutian_auto._arm_pending_xutian_batch(_marker())


def test_global_pending_survives_activity_date_rollover(xutian_engine):
    activity = _activity(offset_start=-3, offset_end=-2)
    marker = _marker()
    marker["occurrence"] = xutian_auto._xutian_occurrence_identity(activity)
    activity.evidence = {
        **dict(activity.evidence),
        xutian_auto.XUTIAN_NATIVE_AUTO_START_MARK: marker,
    }
    _insert(xutian_engine, activity)

    assert xutian_auto._load_pending_xutian_batch() == (activity.id, marker)


def test_multiple_global_pending_markers_fail_closed(xutian_engine):
    activities = [_activity(cross=8), _activity(cross=16)]
    for index, activity in enumerate(activities):
        marker = _marker(f"batch-{index}")
        marker["occurrence"] = xutian_auto._xutian_occurrence_identity(activity)
        activity.evidence = {
            **dict(activity.evidence),
            xutian_auto.XUTIAN_NATIVE_AUTO_START_MARK: marker,
        }
    _insert(xutian_engine, *activities)

    with pytest.raises(RuntimeError, match="多个未结批次"):
        xutian_auto._load_pending_xutian_batch()


def test_arm_readback_failure_refuses_start_and_leaves_marker(
    xutian_engine, monkeypatch
):
    activity = _activity()
    _insert(xutian_engine, activity)
    monkeypatch.setattr(xutian_auto, "_readback_pending_marker", lambda *_: None)

    with pytest.raises(RuntimeError, match="未持久化"):
        xutian_auto._arm_pending_xutian_batch(_marker())

    stored = _stored_activity(xutian_engine, activity.id)
    assert xutian_auto.XUTIAN_NATIVE_AUTO_START_MARK in stored.evidence


def test_pending_occurrence_identity_mismatch_fails_closed(xutian_engine):
    activity = _activity()
    marker = _marker()
    marker["occurrence"] = xutian_auto._xutian_occurrence_identity(activity)
    marker["occurrence"]["cross_count"] = 32
    activity.evidence = {
        **dict(activity.evidence),
        xutian_auto.XUTIAN_NATIVE_AUTO_START_MARK: marker,
    }
    _insert(xutian_engine, activity)

    with pytest.raises(RuntimeError, match="occurrence 身份不一致"):
        xutian_auto._load_pending_xutian_batch()


def test_pending_terminal_requires_not_running_and_exact_completion():
    marker = _marker()
    with pytest.raises(RuntimeError, match="仍在运行"):
        xutian_auto._validate_pending_batch_terminal(
            marker, resource_after=_resource(running=True), wallet_after=_wallet()
        )
    with pytest.raises(RuntimeError, match="未证明精确批次完成"):
        xutian_auto._validate_pending_batch_terminal(
            marker, resource_after=_resource(completed=0), wallet_after=_wallet()
        )


def test_pending_terminal_accepts_restart_identity_but_requires_same_heaven():
    marker = _marker()
    observation = xutian_auto._validate_pending_batch_terminal(
        marker,
        resource_after=_resource(completed=10, heaven=3),
        wallet_after=_wallet(),
    )
    assert observation["completed_challenges"] == 10
    assert observation["currency_delta"] == 1_200

    with pytest.raises(RuntimeError, match="地图身份变化"):
        xutian_auto._validate_pending_batch_terminal(
            marker,
            resource_after=_resource(completed=10, heaven=4),
            wallet_after=_wallet(),
        )


def test_pending_terminal_rejects_ambiguous_wallet_delta():
    with pytest.raises(RuntimeError, match="绝对钱包未形成唯一正向增量"):
        xutian_auto._validate_pending_batch_terminal(
            _marker(),
            resource_after=_resource(),
            wallet_after=_wallet(current_delta=1_200, cumulative_delta=1_100),
        )


def test_settlement_is_activity_bound_atomic_and_batch_idempotent(xutian_engine):
    activity = _activity()
    _insert(xutian_engine, activity)
    activity_id, persisted = xutian_auto._arm_pending_xutian_batch(_marker())
    observation = xutian_auto._validate_pending_batch_terminal(
        persisted,
        resource_after=_resource(),
        wallet_after=_wallet(),
    )

    assert xutian_auto._settle_pending_xutian_batch(
        activity_id,
        "batch-1",
        wallet_after=_wallet(),
        observation=observation,
    ) == activity_id
    assert xutian_auto._settle_pending_xutian_batch(
        activity_id,
        "batch-1",
        wallet_after=_wallet(),
        observation=observation,
    ) == activity_id

    stored = _stored_activity(xutian_engine, activity_id)
    assert xutian_auto.XUTIAN_NATIVE_AUTO_START_MARK not in stored.evidence
    batches = stored.evidence[xutian_auto.XUTIAN_NATIVE_AUTO_BATCHES_KEY]
    assert [item["batch_id"] for item in batches] == ["batch-1"]
    assert batches[0]["activity_id"] == activity_id


def test_settlement_failure_rolls_back_observation_and_marker_clear(
    xutian_engine, monkeypatch
):
    activity = _activity()
    _insert(xutian_engine, activity)
    activity_id, persisted = xutian_auto._arm_pending_xutian_batch(_marker())
    observation = xutian_auto._validate_pending_batch_terminal(
        persisted, resource_after=_resource(), wallet_after=_wallet()
    )
    original_commit = xutian_auto._commit_activity_evidence

    def fail_commit(*_args, **_kwargs):
        raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(xutian_auto, "_commit_activity_evidence", fail_commit)
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        xutian_auto._settle_pending_xutian_batch(
            activity_id,
            "batch-1",
            wallet_after=_wallet(),
            observation=observation,
        )
    monkeypatch.setattr(xutian_auto, "_commit_activity_evidence", original_commit)

    stored = _stored_activity(xutian_engine, activity_id)
    assert xutian_auto.XUTIAN_NATIVE_AUTO_START_MARK in stored.evidence
    assert not stored.evidence.get(xutian_auto.XUTIAN_NATIVE_AUTO_BATCHES_KEY)


def test_old_batch_cannot_clear_a_newer_marker(xutian_engine):
    activity = _activity()
    _insert(xutian_engine, activity)
    activity_id, old_marker = xutian_auto._arm_pending_xutian_batch(_marker("old"))
    old_observation = xutian_auto._validate_pending_batch_terminal(
        old_marker, resource_after=_resource(), wallet_after=_wallet()
    )
    xutian_auto._settle_pending_xutian_batch(
        activity_id,
        "old",
        wallet_after=_wallet(),
        observation=old_observation,
    )
    _activity_id, new_marker = xutian_auto._arm_pending_xutian_batch(_marker("new"))

    with pytest.raises(RuntimeError, match="旧批次不得清理新证据"):
        xutian_auto._settle_pending_xutian_batch(
            activity_id,
            "old",
            wallet_after=_wallet(),
            observation=old_observation,
        )

    stored = _stored_activity(xutian_engine, activity_id)
    assert stored.evidence[xutian_auto.XUTIAN_NATIVE_AUTO_START_MARK] == new_marker
