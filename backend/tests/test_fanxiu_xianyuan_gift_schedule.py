"""Weekly gift cadence remains independent of daily bag stock observations."""
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

from backend.core.fanxiu.data_annotation import default_jobs
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.kernel_scheduler_time import parse_scheduler_time
from backend.core.fanxiu.data_annotation.tasks import storage_bag_operation
from backend.core.fanxiu.data_annotation.tasks.xianyuan_auto_gift import xianyuan_auto_gift_next_time


@pytest.mark.parametrize('offset,hour', [(8, 12), (0, 4)])
def test_off_cycle_research_preserves_next_monday(offset, hour):
    now = datetime(2026, 10, 8, hour, tzinfo=timezone(timedelta(hours=offset)))
    next_time = xianyuan_auto_gift_next_time({'outcome': 'partial'}, {}, now=now)
    expected = datetime(2026, 10, 12)
    assert parse_scheduler_time(next_time) == expected
    assert parse_scheduler_time(xianyuan_auto_gift_next_time({'outcome': 'complete'}, {}, now=now)) == expected
    assert parse_scheduler_time(xianyuan_auto_gift_next_time({'outcome': 'partial'}, {'gift_probe_only': True}, now=now)) == expected


def test_due_retry_uses_scheduler_accepted_local_time():
    result = {'outcome': 'partial', 'action_result': {'yield_for_due_job': {
        'retry_at': '2026-10-12T12:00:00+08:00'}}}
    now = datetime.fromisoformat('2026-10-12T11:50:00+08:00')
    expected = datetime.fromisoformat('2026-10-12T12:00:00+08:00').astimezone().replace(tzinfo=None)
    assert parse_scheduler_time(xianyuan_auto_gift_next_time(result, {}, now=now)) == expected


@pytest.mark.parametrize('route', ['xianyuan_auto_gift', 'unknown_route'])
def test_complete_bag_never_wakes_weekly_gifts(monkeypatch, route):
    default_jobs.register_fanxiu_default_jobs()
    result = {'outcome': 'complete', 'routed_items': [{'external_route': route}]}
    def execute(*args, **kwargs):
        yield from ()
        return result
    writes = []
    class Runner:
        def _persist_scheduler_task_next_time(self, task_id, next_time):
            assert parse_scheduler_time(next_time) is not None
            writes.append(task_id)
    monkeypatch.setattr(storage_bag_operation, 'execute_storage_bag_operation_task', execute)
    flow = get_fanxiu_data_annotation_task_cell_definition('storage_bag_operation').handler(Runner(), {}, {}, Event())
    with pytest.raises(StopIteration):
        next(flow)
    assert 'xianyuan-auto-gift' not in writes
    assert writes[-1] == 'storage-bag-operation'


def test_monday_partial_resumes_but_midnight_boundary_returns_next_week():
    now = datetime.fromisoformat('2026-10-12T12:00:00+08:00')
    assert parse_scheduler_time(xianyuan_auto_gift_next_time({'outcome': 'partial'}, {}, now=now)) == datetime(2026,10,12,12,10)
    now = datetime.fromisoformat('2026-10-12T23:55:00+08:00')
    assert parse_scheduler_time(xianyuan_auto_gift_next_time({'outcome': 'partial'}, {}, now=now)) == datetime(2026,10,19)


def test_utc_sunday_is_already_business_monday():
    now = datetime.fromisoformat('2026-10-11T18:00:00+00:00')
    assert parse_scheduler_time(xianyuan_auto_gift_next_time({'outcome': 'partial'}, {}, now=now)) == datetime(2026,10,12,2,10)


def test_same_week_trigger_resumes_partial_until_next_monday():
    yielded = {'outcome': 'partial', 'action_result': {'yield_for_due_job': {
        'retry_at': '2026-10-20T03:36:53+08:00'}}}
    now = datetime.fromisoformat('2026-10-20T03:35:54+08:00')
    trigger = '2026-10-19 00:00:00'
    assert parse_scheduler_time(xianyuan_auto_gift_next_time(yielded, {}, now=now, trigger=trigger)) == \
        datetime.fromisoformat('2026-10-20T03:36:53+08:00').astimezone().replace(tzinfo=None)
    now = datetime.fromisoformat('2026-10-21T10:40:00+08:00')
    assert parse_scheduler_time(xianyuan_auto_gift_next_time({'outcome': 'partial'}, {}, now=now,
        trigger='2026-10-21 10:35:00')) == datetime(2026, 10, 21, 10, 50)
    now = datetime.fromisoformat('2026-10-25T23:55:00+08:00')
    assert parse_scheduler_time(xianyuan_auto_gift_next_time({'outcome': 'partial'}, {}, now=now,
        trigger=trigger)) == datetime(2026, 10, 26)


@pytest.mark.parametrize('trigger', ['2026-10-26 00:00:00', '2026-10-12 00:00:00', None])
def test_off_cycle_or_previous_week_trigger_waits_for_next_monday(trigger):
    now = datetime.fromisoformat('2026-10-21T10:40:00+08:00')
    for result in ({'outcome': 'partial'}, {'outcome': 'complete'}):
        assert parse_scheduler_time(xianyuan_auto_gift_next_time(result, {}, now=now, trigger=trigger)) == \
            datetime(2026, 10, 26)
    assert parse_scheduler_time(xianyuan_auto_gift_next_time({'outcome': 'partial'},
        {'gift_probe_only': True}, now=now, trigger='2026-10-21 10:35:00')) == datetime(2026, 10, 26)
