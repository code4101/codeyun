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
    ResourceRankGiftAdapter,
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


def test_lingzhuang_cross_eight_occurrence_uses_proven_page_adapter() -> None:
    snapshot = {
        "occurrences": [
            _occurrence(
                8044301,
                start="2026-08-27T05:00:05+08:00",
                end="2026-08-28T22:00:00+08:00",
            )
        ]
    }

    selected = active_resource_rank_gift_adapters(
        snapshot,
        now=datetime(2026, 8, 27, 14, 30, tzinfo=ZONE),
    )

    assert len(selected) == 1
    adapter, activity_id = selected[0]
    assert (adapter.key, activity_id) == ("lingzhuang-huadao", 8044301)
    assert adapter.intro_scene_id == 675
    assert adapter.page_scene_ids == (676,)


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
    assert runtime.next_times == []


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
    assert runtime.next_times == []


def test_parent_adapter_scopes_runtime_readback_to_one_occurrence(monkeypatch) -> None:
    """Two open occurrences must not make one checkpoint scan both families."""

    first = ResourceRankGiftAdapter(
        key="first-rank",
        label="甲榜",
        schedule_pattern="甲榜",
        activity_ids=(111,),
        page_scene_ids=(597,),
    )
    second = ResourceRankGiftAdapter(
        key="second-rank",
        label="乙榜",
        schedule_pattern="乙榜",
        activity_ids=(222,),
        page_scene_ids=(598,),
    )
    monkeypatch.setattr(
        resource_rank_daily_gift,
        "active_resource_rank_gift_adapters",
        lambda _snapshot, *, now: [(first, 111), (second, 222)],
    )
    monkeypatch.setattr(
        resource_rank_daily_gift,
        "load_worldline_activity_schedule_snapshot",
        lambda: {"occurrences": []},
    )
    readback_ids = []

    def readback(activity_ids):
        readback_ids.append(list(activity_ids))
        return {
            "ok": True,
            "complete": True,
            "active_filter_applied": True,
            "items": [{"id": 1, "is_free": True, "claimable": False}],
        }

    monkeypatch.setattr(
        resource_rank_daily_gift,
        "read_activity_gift_runtime_snapshot",
        readback,
    )

    result = _drain(
        run_resource_rank_daily_gift_flow(
            object(),
            now=datetime(2026, 8, 19, 12, 0, tzinfo=ZONE),
            expected_activity_type="second-rank",
            expected_activity_id=222,
        )
    )

    assert result["boundary"] == "runtime_all_free_claimed"
    assert readback_ids == [[222]]


@pytest.mark.parametrize(
    ("expected_type", "expected_id"),
    [
        ("missing-rank", None),
        ("missing-rank", 111),
        ("first-rank", 999),
    ],
)
def test_parent_adapter_fails_closed_when_expected_occurrence_does_not_match(
    monkeypatch,
    expected_type,
    expected_id,
) -> None:
    adapter = ResourceRankGiftAdapter(
        key="first-rank",
        label="甲榜",
        schedule_pattern="甲榜",
        activity_ids=(111,),
        page_scene_ids=(597,),
    )
    monkeypatch.setattr(
        resource_rank_daily_gift,
        "active_resource_rank_gift_adapters",
        lambda _snapshot, *, now: [(adapter, 111)],
    )
    monkeypatch.setattr(
        resource_rank_daily_gift,
        "load_worldline_activity_schedule_snapshot",
        lambda: {"occurrences": []},
    )

    with pytest.raises(RuntimeError, match="实际匹配 0 个，拒绝假完成"):
        _drain(
            run_resource_rank_daily_gift_flow(
                object(),
                now=datetime(2026, 8, 19, 12, 0, tzinfo=ZONE),
                expected_activity_type=expected_type,
                expected_activity_id=expected_id,
            )
        )


def test_parent_adapter_fails_closed_on_multiple_exact_occurrences(monkeypatch) -> None:
    adapter = ResourceRankGiftAdapter(
        key="first-rank",
        label="甲榜",
        schedule_pattern="甲榜",
        activity_ids=(111,),
        page_scene_ids=(597,),
    )
    monkeypatch.setattr(
        resource_rank_daily_gift,
        "active_resource_rank_gift_adapters",
        lambda _snapshot, *, now: [(adapter, 111), (adapter, 111)],
    )
    monkeypatch.setattr(
        resource_rank_daily_gift,
        "load_worldline_activity_schedule_snapshot",
        lambda: {"occurrences": []},
    )

    with pytest.raises(RuntimeError, match="实际匹配 2 个，拒绝假完成"):
        _drain(
            run_resource_rank_daily_gift_flow(
                object(),
                now=datetime(2026, 8, 19, 12, 0, tzinfo=ZONE),
                expected_activity_type="first-rank",
                expected_activity_id=111,
            )
        )


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


def test_resource_rank_aligns_multiday_bar_row_independently_from_start_column() -> None:
    """A centered multi-day title must not be forced into its start column."""

    entity = RuntimeEntity(
        key="1044311|runtime",
        name="灵装化道 （预赛）",
        payload={
            "activityId": 1044311,
            "name": "灵装化道",
            "littleName": "（预赛）",
            "startTime": int(
                datetime(2026, 8, 26, 5, 0, 5, tzinfo=ZONE).timestamp() * 1000
            ),
            "endTime": int(
                datetime(2026, 8, 26, 22, 0, tzinfo=ZONE).timestamp() * 1000
            ),
        },
    )

    target, entry_date = resource_rank_daily_gift._resolve_resource_rank_schedule_target(
        [entity],
        activity_id=1044311,
        now=datetime(2026, 8, 26, 12, 0, tzinfo=ZONE),
        header_lines=[
            {"text": "今天", "x": 80, "y": 210, "w": 60, "h": 30},
            {"text": "08月27日", "x": 210, "y": 210, "w": 100, "h": 30},
            {"text": "08月28日", "x": 360, "y": 210, "w": 100, "h": 30},
        ],
        calendar_lines=[
            # The label is centered two columns away from the occurrence's
            # start date, as happens when a task bar spans several days.
            {"text": "灵装化道", "x": 365, "y": 470, "w": 130, "h": 36},
            {"text": "（预赛）", "x": 385, "y": 510, "w": 90, "h": 34},
        ],
    )

    assert entry_date.isoformat() == "2026-08-26"
    assert target.x == pytest.approx(110.0)
    assert target.y == pytest.approx(488.0)
    assert target.runtime_key == entity.key


def test_resource_gift_is_internal_and_resource_parent_is_the_only_job() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "resource_rank_daily_free_gift"
    )
    assert definition is not None
    assert definition.scheduler_supported is False

    jobs = [
        item
        for item in scheduler_defaults.default_data_annotation_scheduler_tasks(
            now=datetime(2026, 8, 19, 12, 0)
        )
        if item["id"] in {RESOURCE_RANK_DAILY_GIFT_TASK_ID, "resource-ranking"}
    ]
    assert len(jobs) == 1
    assert jobs[0]["id"] == "resource-ranking"
    assert jobs[0]["task_type"] == "resource_ranking"


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
