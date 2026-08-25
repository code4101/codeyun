from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.core.fanxiu.activity.daily_activity_job_registry import (
    XIANMENG_CHALLENGE_TASK_ID,
    YUNMENG_TAIL_TASK_ID,
    build_authorized_daily_activity_job_schedule,
    next_xianmeng_challenge_tail_time,
)


TIMEZONE = ZoneInfo("Asia/Shanghai")


def _operation(
    base_id: int,
    *,
    activity_id: int,
    activity_type: int = 43,
    start_at: str = "2026-08-16T10:00:00+08:00",
    day_relation: str = "starts_today",
    end_at: str = "2026-08-16T22:00:00+08:00",
    close_panel_at: str = "2026-08-16T23:58:59+08:00",
) -> dict:
    return {
        "action": "propose_create",
        "occurrence": {
            "activity_id": activity_id,
            "activity_type": activity_type,
            "base_id": base_id,
            "schedule_id": 6400002,
            "catalog_status": "known",
            "identity_complete": True,
            "day_relation": day_relation,
            "start_at": start_at,
            "end_at": end_at,
            "close_panel_at": close_panel_at,
            "raw": {"baseId": base_id},
        },
    }


def _plan(*operations: dict) -> dict:
    return {
        "status": "ready",
        "target_date": "2026-08-16",
        "operations": list(operations),
    }


def _occurrence_plan(*operations: dict) -> dict:
    plan = _plan()
    plan["occurrences"] = [operation["occurrence"] for operation in operations]
    return plan


@pytest.mark.parametrize("base_id,activity_id", [(28100, 421601), (28200, 421602)])
def test_user_authorized_xianmeng_child_starts_challenge_at_formal_start(
    base_id: int,
    activity_id: int,
) -> None:
    result = build_authorized_daily_activity_job_schedule(
        _plan(_operation(base_id, activity_id=activity_id))
    )

    assert result["desired_next_times"] == {
        XIANMENG_CHALLENGE_TASK_ID: "2026-08-16 10:00:00",
        YUNMENG_TAIL_TASK_ID: None,
    }
    assert result["decisions"][0]["base_id"] == base_id


@pytest.mark.parametrize(
    "base_id,activity_type",
    [
        (28300, 4),  # 仙盟附属榜单
        (28110, 43),  # 64 跨复赛尚未获用户授权
        (99999, 43),
    ],
)
def test_unapproved_parent_module_or_child_cannot_trigger_job(
    base_id: int,
    activity_type: int,
) -> None:
    result = build_authorized_daily_activity_job_schedule(
        _plan(
            _operation(
                base_id,
                activity_id=base_id,
                activity_type=activity_type,
            )
        )
    )

    assert result["desired_next_times"] == {
        XIANMENG_CHALLENGE_TASK_ID: None,
        YUNMENG_TAIL_TASK_ID: None,
    }
    assert result["decisions"] == []


def test_runtime_xianmeng_qualifying_root_schedules_from_full_occurrences() -> None:
    result = build_authorized_daily_activity_job_schedule(
        _occurrence_plan(
            _operation(
                28000,
                activity_id=16420001,
                activity_type=42,
                start_at="2026-08-16T10:00:00+08:00",
                end_at="2026-08-17T22:00:00+08:00",
                close_panel_at="2026-08-18T23:58:59+08:00",
            )
        )
    )

    assert result["desired_next_times"][XIANMENG_CHALLENGE_TASK_ID] == (
        "2026-08-16 10:00:00"
    )
    assert result["decisions"][0]["base_id"] == 28000


def test_runtime_xianmeng_root_starts_a_fresh_cycle_on_explicit_final_day() -> None:
    operation = _operation(
        28000,
        activity_id=16420001,
        activity_type=42,
        start_at="2026-08-16T10:00:00+08:00",
        day_relation="continues_today",
        end_at="2026-08-17T22:00:00+08:00",
        close_panel_at="2026-08-18T23:58:59+08:00",
    )
    operation["occurrence"]["ends_today"] = True
    plan = _occurrence_plan(operation)
    plan["target_date"] = "2026-08-17"

    result = build_authorized_daily_activity_job_schedule(plan)

    assert result["desired_next_times"][XIANMENG_CHALLENGE_TASK_ID] == (
        "2026-08-17 10:00:00"
    )
    assert result["decisions"][0]["base_id"] == 28000


def test_runtime_xianmeng_root_does_not_trigger_on_nonfinal_continuation_day() -> None:
    operation = _operation(
        28000,
        activity_id=16420001,
        activity_type=42,
        start_at="2026-08-15T10:00:00+08:00",
        day_relation="continues_today",
        end_at="2026-08-17T22:00:00+08:00",
    )
    operation["occurrence"]["ends_today"] = False
    plan = _occurrence_plan(operation)
    plan["target_date"] = "2026-08-16"

    result = build_authorized_daily_activity_job_schedule(plan)

    assert result["desired_next_times"][XIANMENG_CHALLENGE_TASK_ID] is None
    assert result["decisions"] == []


def test_full_occurrences_drive_schedule_even_when_sync_operations_are_empty() -> None:
    plan = _occurrence_plan(_operation(28100, activity_id=421601))
    plan["operations"] = []

    result = build_authorized_daily_activity_job_schedule(plan)

    assert result["desired_next_times"][XIANMENG_CHALLENGE_TASK_ID] == (
        "2026-08-16 10:00:00"
    )


def test_missing_authorized_activity_clears_the_managed_job_desired_state() -> None:
    assert build_authorized_daily_activity_job_schedule(_plan())[
        "desired_next_times"
    ] == {XIANMENG_CHALLENGE_TASK_ID: None, YUNMENG_TAIL_TASK_ID: None}


def test_yunmeng_claim_grace_schedules_tail_five_minutes_after_sync() -> None:
    operation = _operation(
        210001,
        activity_id=8210001,
        activity_type=21,
        start_at="2026-08-15T10:00:00+08:00",
        day_relation="claim_grace_today",
        end_at="2026-08-15T22:00:00+08:00",
        close_panel_at="2026-08-16T23:58:59+08:00",
    )
    result = build_authorized_daily_activity_job_schedule(
        _plan(operation),
        now=datetime(2026, 8, 16, 0, 20, 7, tzinfo=TIMEZONE),
    )

    assert result["desired_next_times"] == {
        XIANMENG_CHALLENGE_TASK_ID: None,
        YUNMENG_TAIL_TASK_ID: "2026-08-16 00:25:07",
    }
    assert result["decisions"][0]["binding_id"] == (
        "yunmeng-ending-phase-starts-yunmeng-tail"
    )


def test_conflicting_authorized_child_times_fail_closed() -> None:
    with pytest.raises(ValueError, match="冲突时间"):
        build_authorized_daily_activity_job_schedule(
            _plan(
                _operation(28100, activity_id=421601),
                _operation(
                    28200,
                    activity_id=421602,
                    start_at="2026-08-16T11:00:00+08:00",
                ),
            )
        )


def test_xianmeng_stamina_sweeps_use_2110_then_2150_on_an_authorized_day() -> None:
    plan = _plan(_operation(28100, activity_id=421601))

    assert next_xianmeng_challenge_tail_time(
        plan,
        now=datetime(2026, 8, 16, 10, 30, tzinfo=TIMEZONE),
    ) == "2026-08-16 21:10:00"
    assert next_xianmeng_challenge_tail_time(
        plan,
        now=datetime(2026, 8, 16, 21, 20, tzinfo=TIMEZONE),
    ) == "2026-08-16 21:50:00"
    assert next_xianmeng_challenge_tail_time(
        plan,
        now=datetime(2026, 8, 16, 21, 50, tzinfo=TIMEZONE),
    ) is None
    assert next_xianmeng_challenge_tail_time(
        _plan(),
        now=datetime(2026, 8, 16, 10, 30, tzinfo=TIMEZONE),
    ) is None
