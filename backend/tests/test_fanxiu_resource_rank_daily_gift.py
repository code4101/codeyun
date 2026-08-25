from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.core.fanxiu.data_annotation import scheduler_defaults
from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.tasks import resource_rank_daily_gift
from backend.core.fanxiu.data_annotation.tasks.resource_rank_daily_gift import (
    RESOURCE_RANK_DAILY_GIFT_TASK_ID,
    active_resource_rank_gift_adapters,
    next_resource_rank_daily_gift_time,
    project_resource_rank_gift_list_actions,
    run_resource_rank_daily_gift_flow,
    validate_one_free_gift_increment,
)
from backend.core.fanxiu.runtime_gui.alignment import RuntimeEntity
import pytest


ZONE = ZoneInfo("Asia/Shanghai")


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as stop:
        return stop.value


def _occurrence(activity_id: int, *, start: str, end: str, complete: bool = True):
    return {
        "activity_id": activity_id,
        "identity_complete": complete,
        "start_at": start,
        "end_at": end,
    }


def test_trigger_uses_safe_post_open_time() -> None:
    before = datetime(2026, 8, 19, 4, 0, tzinfo=ZONE)
    after = datetime(2026, 8, 19, 12, 0, tzinfo=ZONE)

    assert next_resource_rank_daily_gift_time(before) == datetime(
        2026, 8, 19, 5, 10, tzinfo=ZONE
    )
    assert next_resource_rank_daily_gift_time(after) == datetime(
        2026, 8, 20, 5, 10, tzinfo=ZONE
    )


def test_only_current_open_identity_complete_adapter_is_selected() -> None:
    snapshot = {
        "occurrences": [
            _occurrence(
                1043111,
                start="2026-08-19T05:00:05+08:00",
                end="2026-08-19T22:00:00+08:00",
            ),
            _occurrence(
                4043101,
                start="2026-08-20T05:00:05+08:00",
                end="2026-08-21T22:00:00+08:00",
            ),
            _occurrence(
                1043111,
                start="2026-08-19T05:00:05+08:00",
                end="2026-08-19T22:00:00+08:00",
                complete=False,
            ),
        ]
    }

    selected = active_resource_rank_gift_adapters(
        snapshot,
        now=datetime(2026, 8, 19, 12, 0, tzinfo=ZONE),
    )

    assert [(adapter.key, activity_id) for adapter, activity_id in selected] == [
        ("dandao-wending", 1043111)
    ]


def test_same_multi_day_resource_rank_occurrence_is_active_on_each_open_day() -> None:
    snapshot = {
        "occurrences": [
            _occurrence(
                4043101,
                start="2026-08-20T05:00:05+08:00",
                end="2026-08-21T22:00:00+08:00",
            )
        ]
    }

    for current in (
        datetime(2026, 8, 20, 12, 0, tzinfo=ZONE),
        datetime(2026, 8, 21, 16, 0, tzinfo=ZONE),
    ):
        selected = active_resource_rank_gift_adapters(snapshot, now=current)
        assert [(adapter.key, activity_id) for adapter, activity_id in selected] == [
            ("dandao-wending", 4043101)
        ]


def test_no_active_resource_rank_is_idempotent_and_schedules_next_day(monkeypatch) -> None:
    class Runtime:
        def __init__(self):
            self.next_times = []

        def set_next_time(self, value):
            self.next_times.append(value)

    runtime = Runtime()
    monkeypatch.setattr(
        resource_rank_daily_gift,
        "load_worldline_activity_schedule_snapshot",
        lambda: {"occurrences": []},
    )

    result = _drain(
        run_resource_rank_daily_gift_flow(
            runtime,
            now=datetime(2026, 8, 19, 12, 0, tzinfo=ZONE),
        )
    )

    assert result["claimed_count"] == 0
    assert runtime.next_times == ["2026-08-20 05:10:00"]


def test_exhausted_runtime_short_circuits_before_activity_navigation(monkeypatch) -> None:
    class Runtime:
        def __init__(self):
            self.next_times = []

        def set_next_time(self, value):
            self.next_times.append(value)

    runtime = Runtime()
    monkeypatch.setattr(
        resource_rank_daily_gift,
        "load_worldline_activity_schedule_snapshot",
        lambda: {
            "occurrences": [
                _occurrence(
                    1043111,
                    start="2026-08-19T05:00:05+08:00",
                    end="2026-08-19T22:00:00+08:00",
                )
            ]
        },
    )
    monkeypatch.setattr(
        resource_rank_daily_gift,
        "read_activity_gift_runtime_snapshot",
        lambda _activity_ids: {
            "ok": True,
            "complete": True,
            "active_filter_applied": True,
            "items": [{"id": 1, "is_free": True, "claimable": False}],
        },
    )
    monkeypatch.setattr(
        resource_rank_daily_gift,
        "_open_adapter_gift_page",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("已领尽时不得导航到活动页")
        ),
    )

    result = _drain(
        run_resource_rank_daily_gift_flow(
            runtime,
            now=datetime(2026, 8, 19, 12, 0, tzinfo=ZONE),
        )
    )

    assert result["boundary"] == "runtime_all_free_claimed"
    assert result["claimed_count"] == 0
    assert runtime.next_times == ["2026-08-20 05:10:00"]


def test_active_calendar_entry_absence_is_not_reported_as_success(monkeypatch) -> None:
    class Runtime:
        def __init__(self):
            self.next_times = []

        def set_next_time(self, value):
            self.next_times.append(value)

    runtime = Runtime()
    monkeypatch.setattr(
        resource_rank_daily_gift,
        "load_worldline_activity_schedule_snapshot",
        lambda: {
            "occurrences": [
                _occurrence(
                    1043111,
                    start="2026-08-19T05:00:05+08:00",
                    end="2026-08-19T22:00:00+08:00",
                )
            ]
        },
    )
    monkeypatch.setattr(
        resource_rank_daily_gift,
        "read_activity_gift_runtime_snapshot",
        lambda _activity_ids: {
            "ok": True,
            "complete": True,
            "active_filter_applied": False,
            "items": [{"id": 1, "is_free": True, "claimable": True}],
        },
    )

    def absent_page(*_args, **_kwargs):
        raise RuntimeError("calendar entry missing")
        yield

    monkeypatch.setattr(
        resource_rank_daily_gift,
        "_open_adapter_gift_page",
        absent_page,
    )

    with pytest.raises(RuntimeError, match="calendar entry missing"):
        _drain(
            run_resource_rank_daily_gift_flow(
                runtime,
                now=datetime(2026, 8, 19, 12, 0, tzinfo=ZONE),
            )
        )

    assert runtime.next_times == []


def test_resource_rank_uses_occurrence_start_date_not_current_day() -> None:
    entity = RuntimeEntity(
        key="4043101|runtime",
        name="丹道问鼎",
        payload={
            "activityId": 4043101,
            "startTime": int(datetime(2026, 8, 20, 5, 0, 5, tzinfo=ZONE).timestamp() * 1000),
            "endTime": int(datetime(2026, 8, 21, 22, 0, tzinfo=ZONE).timestamp() * 1000),
        },
    )

    entry_date = resource_rank_daily_gift._resource_rank_schedule_entry_date(
        [entity],
        activity_id=4043101,
        now=datetime(2026, 8, 21, 16, 0, tzinfo=ZONE),
    )

    assert entry_date.isoformat() == "2026-08-20"


def test_standard_registry_and_scheduler_contain_one_daily_job() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "resource_rank_daily_free_gift"
    )
    assert definition is not None
    assert definition.scheduler_supported is True

    jobs = [
        item
        for item in scheduler_defaults.default_data_annotation_scheduler_tasks(
            now=datetime(2026, 8, 19, 12, 0)
        )
        if item["id"] == RESOURCE_RANK_DAILY_GIFT_TASK_ID
    ]
    assert len(jobs) == 1
    assert jobs[0]["next_time"] == "2026-08-20 05:10:00"


def test_gift_list_uses_free_prefix_and_stops_at_first_stone_price() -> None:
    actions = project_resource_rank_gift_list_actions(
        [
            {"text": "3", "x": 280, "y": 520, "w": 12, "h": 20},
            {"text": "免费", "x": 670, "y": 600, "w": 90, "h": 42},
            {"text": "免费", "x": 670, "y": 900, "w": 90, "h": 42},
            {"text": "488", "x": 695, "y": 1200, "w": 65, "h": 38},
            {"text": "￥6", "x": 690, "y": 1400, "w": 70, "h": 38},
        ]
    )

    assert [(item.kind, item.text) for item in actions] == [
        ("free", "免费"),
        ("free", "免费"),
        ("spirit_stone", "488"),
    ]


def test_runtime_readback_requires_exactly_one_free_increment() -> None:
    before = {
        "items": [
            {"id": 1, "is_free": True, "purchased_times": 0},
            {"id": 2, "is_free": True, "purchased_times": 0},
            {"id": 3, "is_free": False, "purchased_times": 0},
        ]
    }
    after = {
        "items": [
            {"id": 1, "is_free": True, "purchased_times": 0},
            {"id": 2, "is_free": True, "purchased_times": 1},
            {"id": 3, "is_free": False, "purchased_times": 0},
        ]
    }

    assert validate_one_free_gift_increment(before, after) == 2

    after["items"][2]["purchased_times"] = 1
    with pytest.raises(RuntimeError, match="非免费"):
        validate_one_free_gift_increment(before, after)


def test_runtime_readback_rejects_no_increment() -> None:
    snapshot = {"items": [{"id": 1, "is_free": True, "purchased_times": 0}]}

    with pytest.raises(RuntimeError, match="增量数为 0"):
        validate_one_free_gift_increment(snapshot, snapshot)


def test_exhausted_runtime_free_rows_are_not_claimable() -> None:
    snapshot = {
        "items": [
            {
                "id": 1,
                "is_free": True,
                "purchased_times": 1,
                "remaining_times": 0,
                "claimable": False,
            },
            {
                "id": 2,
                "is_free": False,
                "purchased_times": 0,
                "remaining_times": 1,
                "claimable": False,
            },
        ]
    }

    assert resource_rank_daily_gift._claimable_free_gift_ids(snapshot) == ()
