from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import backend.core.fanxiu.data_annotation.tasks.xutian_active as active
from backend.models import FanxiuExchangeActivity


TZ = timezone(timedelta(hours=8))
START = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)
END = datetime(2026, 9, 1, 22, 0, tzinfo=TZ)
CLOSE = datetime(2026, 9, 1, 22, 5, tzinfo=TZ)
NOW = datetime(2026, 8, 31, 13, 0, tzinfo=TZ)


def _occurrence(**changes):
    values = {
        "activity_type": "xutian-palace",
        "runtime_id": "runtime:xutian-8",
        "activity_id": 8_000_008,
        "cross_count": 8,
        "start_at": START,
        "end_at": END,
        "close_at": CLOSE,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _activity(*, batches=()):
    return FanxiuExchangeActivity(
        instance_key="xutian:8:2026-08-31:2026-09-01",
        activity_type="xutian-palace",
        cross_count=8,
        start_date="2026-08-31",
        end_date="2026-09-01",
        game_rank_activity_id=80_891,
        game_shop_base_id=708,
        currency_type=12,
        currency_name="纳元晶",
        evidence={
            "game_activity_id": 8_000_008,
            "period_record_id": "runtime:xutian-8",
            "period_start_time": int(START.timestamp() * 1000),
            "period_end_time": int(END.timestamp() * 1000),
            "period_close_panel_time": int(CLOSE.timestamp() * 1000),
            "xutian_native_auto_batches": list(batches),
        },
    )


def _finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(active, "job_now", lambda: NOW)

    def completed_task_gate(*_args, **_kwargs):
        if False:
            yield None
        return {
            "status": "completed",
            "phase": "task_rewards",
            "snapshot_complete": True,
            "authorized_remaining": [],
            "claimed_task_ids": [],
        }

    monkeypatch.setattr(
        active,
        "execute_xutian_task_reward_gate",
        completed_task_gate,
    )


def test_occurrence_validation_requires_one_exact_runtime_instance():
    activity = _activity()

    assert active.validate_xutian_active_occurrence(_occurrence(), [activity]) is activity

    with pytest.raises(RuntimeError, match="无法唯一对齐"):
        active.validate_xutian_active_occurrence(
            _occurrence(activity_id=8_000_009), [activity]
        )
    with pytest.raises(RuntimeError, match="非虚天殿"):
        active.validate_xutian_active_occurrence(
            _occurrence(activity_type="magic-invasion"), [activity]
        )


def test_occurrence_validation_ignores_legacy_iso_rows_and_prefers_instance_key():
    current = _activity()
    current.instance_key = "activity:xutian-palace:8000008:current"
    occurrence = _occurrence(instance_key=current.instance_key)
    duplicate = _activity()
    duplicate.id = "duplicate-current-row"
    legacy = _activity()
    legacy.id = "legacy-row"
    legacy.evidence = {
        **dict(legacy.evidence or {}),
        "period_start_time": START.isoformat(),
        "period_end_time": END.isoformat(),
    }

    assert active.validate_xutian_active_occurrence(
        occurrence, [legacy, duplicate, current]
    ) is current


def test_occurrence_validation_tolerates_runtime_row_id_drift():
    activity = _activity()
    activity.evidence["period_record_id"] = "4080001400020"

    matched = active.validate_xutian_active_occurrence(
        _occurrence(runtime_id="4080001400004"),
        [activity],
    )

    assert matched is activity


def test_missing_item_grants_run_probe_with_both_native_consumption_flags_false(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(active, "_load_xutian_activity", lambda _occ: _activity())

    def native(_runner, _ctx, payload, _stop):
        calls.append({
            "requested": payload["requested_challenges"],
            "refill": payload["allow_item_refill"],
            "boost": payload["allow_boost_items"],
        })
        if False:
            yield None
        return {
            "result": "success",
            "observation": {"completed_challenges": 10, "currency_delta": 120},
        }

    monkeypatch.setattr(active, "execute_xutian_native_auto_job", native)

    result = _finish(active.execute_xutian_active_checkpoint(
        object(), {}, {"required_new_currency": 1000}, object(), occurrence=_occurrence()
    ))

    assert result["status"] == "pending"
    assert result["required_new_currency"] == 880
    assert result["retry_at"] == "2026-08-31T20:45:00+08:00"
    assert calls == [{"requested": 10, "refill": False, "boost": False}]


def test_task_reward_gate_runs_before_budget_and_native(monkeypatch):
    events = []
    activity = _activity()
    monkeypatch.setattr(active, "_load_xutian_activity", lambda _occ: activity)

    def gate(*_args, **_kwargs):
        events.append("task_rewards")
        if False:
            yield None
        return {
            "status": "completed",
            "snapshot_complete": True,
            "authorized_remaining": [],
            "claimed_task_ids": [408000110],
        }

    def budget(actual):
        assert actual is activity
        events.append("budget")
        return 1000

    def native(*_args, **_kwargs):
        events.append("native")
        if False:
            yield None
        return {
            "result": "success",
            "observation": {"completed_challenges": 10, "currency_delta": 120},
        }

    monkeypatch.setattr(active, "execute_xutian_task_reward_gate", gate)
    monkeypatch.setattr(active, "_fresh_required_new_currency", budget)
    monkeypatch.setattr(active, "execute_xutian_native_auto_job", native)

    result = _finish(active.execute_xutian_active_checkpoint(
        object(), {}, {}, object(), occurrence=_occurrence()
    ))

    assert events == ["task_rewards", "budget", "native"]
    assert result["task_rewards"]["claimed_task_ids"] == [408000110]
    assert result["next_phase"] == "task_rewards"


def test_blocked_task_reward_gate_stops_budget_and_native(monkeypatch):
    monkeypatch.setattr(active, "_load_xutian_activity", lambda _occ: _activity())

    def blocked(*_args, **_kwargs):
        if False:
            yield None
        return {
            "status": "blocked",
            "snapshot_complete": False,
            "message": "任务页版本未知",
        }

    monkeypatch.setattr(active, "execute_xutian_task_reward_gate", blocked)
    monkeypatch.setattr(
        active,
        "_fresh_required_new_currency",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不得读取预算")),
    )
    monkeypatch.setattr(
        active,
        "execute_xutian_native_auto_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得挑战")),
    )

    result = _finish(active.execute_xutian_active_checkpoint(
        object(), {}, {}, object(), occurrence=_occurrence()
    ))

    assert result["status"] == "blocked"
    assert result["phase"] == "task_rewards"
    assert result["retry_at"] == "2026-08-31T20:45:00+08:00"


def test_task_reward_gate_cannot_complete_with_authorized_reward_remaining(
    monkeypatch,
):
    monkeypatch.setattr(active, "_load_xutian_activity", lambda _occ: _activity())

    def invalid(*_args, **_kwargs):
        if False:
            yield None
        return {
            "status": "completed",
            "snapshot_complete": True,
            "authorized_remaining": [408000110],
        }

    monkeypatch.setattr(active, "execute_xutian_task_reward_gate", invalid)

    with pytest.raises(RuntimeError, match="仍有可领取"):
        _finish(active.execute_xutian_active_checkpoint(
            object(), {}, {"required_new_currency": 0}, object(),
            occurrence=_occurrence(),
        ))


def test_pending_native_marker_recovers_before_task_reward_gate(monkeypatch):
    events = []
    activity = _activity()
    activity.evidence[active.XUTIAN_NATIVE_AUTO_START_MARK] = {"batch_id": "old"}
    monkeypatch.setattr(active, "_load_xutian_activity", lambda _occ: activity)

    def gate(*_args, **_kwargs):
        events.append("task_rewards")
        if False:
            yield None
        return {}

    def native(*_args, **_kwargs):
        events.append("native_recovery")
        if False:
            yield None
        return {"result": "success", "recovered": True}

    monkeypatch.setattr(active, "execute_xutian_task_reward_gate", gate)
    monkeypatch.setattr(active, "execute_xutian_native_auto_job", native)
    monkeypatch.setattr(
        active,
        "_fresh_required_new_currency",
        lambda *_args: (_ for _ in ()).throw(AssertionError("恢复前不得读取预算")),
    )

    result = _finish(active.execute_xutian_active_checkpoint(
        object(), {}, {}, object(), occurrence=_occurrence()
    ))

    assert events == ["native_recovery"]
    assert result["status"] == "pending"
    assert result["phase"] == "native_recovery"
    assert result["next_phase"] == "task_rewards"


def test_fresh_occurrence_bootstraps_probe_when_budget_models_are_not_loaded(
    monkeypatch,
):
    monkeypatch.setattr(active, "_load_xutian_activity", lambda _occ: _activity())
    monkeypatch.setattr(
        active,
        "_fresh_required_new_currency",
        lambda _activity: (_ for _ in ()).throw(
            RuntimeError("虚天 active 收尾道具预算 freshness 门禁失效：等待模型")
        ),
    )
    seen = []

    def native(_runner, _ctx, payload, _stop):
        seen.append(payload["requested_challenges"])
        if False:
            yield None
        return {
            "result": "success",
            "observation": {"completed_challenges": 10, "currency_delta": 120},
        }

    monkeypatch.setattr(active, "execute_xutian_native_auto_job", native)
    result = _finish(active.execute_xutian_active_checkpoint(
        object(), {}, {}, object(), occurrence=_occurrence()
    ))

    assert seen == [10]
    assert result["status"] == "pending"
    assert result["retry_at"] == "2026-08-31T20:45:00+08:00"


def test_wallet_bootstrap_row_allows_exactly_one_real_yield_probe(monkeypatch):
    bootstrap = {
        "requested_challenges": 1,
        "completed_challenges": 1,
        "currency_delta": 0,
        "wallet_bootstrap": True,
        "yield_eligible": False,
    }
    monkeypatch.setattr(
        active, "_load_xutian_activity", lambda _occ: _activity(batches=[bootstrap])
    )
    monkeypatch.setattr(
        active,
        "_fresh_required_new_currency",
        lambda _activity: (_ for _ in ()).throw(
            RuntimeError("虚天 active 收尾道具预算 freshness 门禁失效：等待模型")
        ),
    )
    seen = []

    def native(_runner, _ctx, payload, _stop):
        seen.append(dict(payload))
        if False:
            yield None
        return {
            "result": "success",
            "observation": {"completed_challenges": 10, "currency_delta": 120},
        }

    monkeypatch.setattr(active, "execute_xutian_native_auto_job", native)
    result = _finish(active.execute_xutian_active_checkpoint(
        object(), {}, {}, object(), occurrence=_occurrence()
    ))

    assert seen[0]["requested_challenges"] == 10
    assert seen[0]["allow_unloaded_wallet_bootstrap"] is False
    assert result["status"] == "pending"
    assert "等待刷新兑换预算" in result["message"]


def test_stale_budget_after_real_yield_probe_fails_closed(monkeypatch):
    yield_row = {
        "requested_challenges": 10,
        "completed_challenges": 10,
        "currency_delta": 120,
        "yield_eligible": True,
    }
    monkeypatch.setattr(
        active, "_load_xutian_activity", lambda _occ: _activity(batches=[yield_row])
    )
    monkeypatch.setattr(
        active,
        "_fresh_required_new_currency",
        lambda _activity: (_ for _ in ()).throw(
            RuntimeError("虚天 active 收尾道具预算 freshness 门禁失效：仍未刷新")
        ),
    )
    monkeypatch.setattr(
        active,
        "execute_xutian_native_auto_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("freshness 失效后不得继续消耗")
        ),
    )

    with pytest.raises(RuntimeError, match="freshness 门禁失效"):
        _finish(active.execute_xutian_active_checkpoint(
            object(), {}, {}, object(), occurrence=_occurrence()
        ))


def test_first_authorized_attempt_runs_exactly_one_probe_and_returns_pending(
    monkeypatch,
):
    requested = []
    monkeypatch.setattr(active, "_load_xutian_activity", lambda _occ: _activity())

    def native(_runner, _ctx, payload, _stop):
        requested.append((
            payload["requested_challenges"],
            payload["allow_item_refill"],
            payload["allow_boost_items"],
        ))
        if False:
            yield None
        return {
            "result": "success",
            "observation": {
                "completed_challenges": 10,
                "currency_delta": 120,
            },
        }

    monkeypatch.setattr(active, "execute_xutian_native_auto_job", native)
    result = _finish(active.execute_xutian_active_checkpoint(
        object(),
        {},
        {
            "required_new_currency": 1000,
            "allow_xutian_boost_items": True,
        },
        object(),
        occurrence=_occurrence(),
    ))

    assert requested == [(10, False, True)]
    assert result["status"] == "pending"
    assert result["required_new_currency"] == 880
    assert result["observation"]["currency_delta"] == 120
    assert result["retry_at"] == "2026-08-31T20:45:00+08:00"


def test_probe_can_complete_target(monkeypatch):
    monkeypatch.setattr(active, "_load_xutian_activity", lambda _occ: _activity())

    def native(*_args, **_kwargs):
        if False:
            yield None
        return {
            "result": "success",
            "observation": {
                "completed_challenges": 10,
                "currency_delta": 120,
            },
        }

    monkeypatch.setattr(active, "execute_xutian_native_auto_job", native)
    result = _finish(active.execute_xutian_active_checkpoint(
        object(),
        {},
        {
            "required_new_currency": 100,
            "allow_xutian_boost_items": True,
        },
        object(),
        occurrence=_occurrence(),
    ))

    assert result["status"] == "pending"
    assert result["required_new_currency"] == 0
    assert result["next_phase"] == "task_rewards"


def test_existing_probe_can_be_explicitly_paused_before_runtime_target(monkeypatch):
    calls = []
    activity = _activity(batches=[{
        "completed_challenges": 10,
        "currency_delta": 100,
    }])
    monkeypatch.setattr(active, "_load_xutian_activity", lambda _occ: activity)
    monkeypatch.setattr(
        active,
        "execute_xutian_native_auto_job",
        lambda *_args, **_kwargs: calls.append("native"),
    )

    result = _finish(active.execute_xutian_active_checkpoint(
        object(),
        {},
        {
            "required_new_currency": 1000,
            "disable_xutian_scaled_batch": True,
        },
        object(),
        occurrence=_occurrence(),
    ))

    assert result["status"] == "pending"
    assert result["plan"]["planning_mode"] == "runtime_target"
    assert result["plan"]["requested_challenges"] == "max"
    assert calls == []


def test_existing_probe_uses_live_wallet_target_and_native_maximum(monkeypatch):
    requested = []
    activity = _activity(batches=[{
        "completed_challenges": 10,
        "currency_delta": 100,
    }])
    monkeypatch.setattr(active, "_load_xutian_activity", lambda _occ: activity)

    def native(_runner, _ctx, payload, _stop):
        requested.append((payload["requested_challenges"], payload["required_new_currency"]))
        if False:
            yield None
        return {
            "result": "success",
            "observation": {
                "requested_challenges": 1390,
                "completed_challenges": 350,
                "currency_delta": 1000,
            },
        }

    monkeypatch.setattr(active, "execute_xutian_native_auto_job", native)
    result = _finish(active.execute_xutian_active_checkpoint(
        object(),
        {},
        {"required_new_currency": 1000},
        object(),
        occurrence=_occurrence(),
    ))

    assert requested == [("max", 1000)]
    assert result["status"] == "pending"
    assert result["required_new_currency"] == 0


def test_explicit_item_refill_grant_is_mapped_to_native_payload(monkeypatch):
    refill_flags = []
    monkeypatch.setattr(active, "_load_xutian_activity", lambda _occ: _activity())

    def native(_runner, _ctx, payload, _stop):
        refill_flags.append(payload["allow_item_refill"])
        if False:
            yield None
        return {
            "result": "success",
            "observation": {"completed_challenges": 10, "currency_delta": 120},
        }

    monkeypatch.setattr(active, "execute_xutian_native_auto_job", native)
    result = _finish(active.execute_xutian_active_checkpoint(
        object(),
        {},
        {
            "required_new_currency": 100,
            "allow_xutian_item_refill": True,
            "allow_xutian_boost_items": True,
        },
        object(),
        occurrence=_occurrence(),
    ))

    assert result["status"] == "pending"
    assert refill_flags == [True]


def test_zero_gap_completes_without_resource_authorization(monkeypatch):
    monkeypatch.setattr(active, "_load_xutian_activity", lambda _occ: _activity())

    result = _finish(active.execute_xutian_active_checkpoint(
        object(), {}, {"required_new_currency": 0}, object(), occurrence=_occurrence()
    ))

    assert result["status"] == "completed"
    assert result["required_new_currency"] == 0
    assert result["performed_actions"] is False
    assert result["phase"] == "completed"
    assert result["task_rewards"]["snapshot_complete"] is True


def test_scheduler_payload_without_gap_calculates_fresh_budget_and_runs_probe(
    monkeypatch,
):
    calls = []
    activity = _activity()
    monkeypatch.setattr(active, "_load_xutian_activity", lambda _occ: activity)
    monkeypatch.setattr(
        active,
        "_fresh_required_new_currency",
        lambda actual: 1000 if actual is activity else -1,
    )

    def native(_runner, _ctx, payload, _stop):
        calls.append(payload["requested_challenges"])
        if False:
            yield None
        return {
            "result": "success",
            "observation": {"completed_challenges": 10, "currency_delta": 120},
        }

    monkeypatch.setattr(active, "execute_xutian_native_auto_job", native)
    result = _finish(active.execute_xutian_active_checkpoint(
        object(), {}, {}, object(), occurrence=_occurrence()
    ))

    assert calls == [10]
    assert result["status"] == "pending"
    assert result["required_new_currency"] == 880


def test_scheduler_payload_without_gap_completes_from_fresh_wallet_budget(
    monkeypatch,
):
    monkeypatch.setattr(active, "_load_xutian_activity", lambda _occ: _activity())
    monkeypatch.setattr(active, "_fresh_required_new_currency", lambda _activity: 0)

    result = _finish(active.execute_xutian_active_checkpoint(
        object(), {}, {}, object(), occurrence=_occurrence()
    ))

    assert result["status"] == "completed"
    assert result["performed_actions"] is False


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 8, 31, 13, 0, tzinfo=TZ), "2026-08-31T20:45:00+08:00"),
        (datetime(2026, 8, 31, 20, 50, tzinfo=TZ), "2026-08-31T21:15:00+08:00"),
        (datetime(2026, 8, 31, 21, 20, tzinfo=TZ), "2026-08-31T21:35:00+08:00"),
        (datetime(2026, 8, 31, 21, 40, tzinfo=TZ), "2026-08-31T21:50:00+08:00"),
        (datetime(2026, 9, 1, 22, 2, tzinfo=TZ), "2026-09-01T22:05:00+08:00"),
    ],
)
def test_retry_windows_skip_elapsed_slots_then_clip_to_close(now, expected):
    assert active._retry_at(_occurrence(), now=now).isoformat(timespec="seconds") == expected
