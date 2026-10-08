"""Selected-gift routing and verified partials retain a real Scheduler trigger."""
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

from backend.core.fanxiu.data_annotation import default_jobs
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.kernel_scheduler_time import parse_scheduler_time
from backend.core.fanxiu.data_annotation.tasks import storage_bag_operation
from backend.core.fanxiu.data_annotation.tasks.xianyuan_auto_gift import xianyuan_auto_gift_next_time


@pytest.mark.parametrize('offset,hour', [(8, 12), (0, 4)])
def test_partial_resumes_and_complete_or_probe_stays_dormant(offset, hour):
    now = datetime(2026, 10, 8, hour, tzinfo=timezone(timedelta(hours=offset)))
    next_time = xianyuan_auto_gift_next_time({'outcome': 'partial'}, {}, now=now)
    assert parse_scheduler_time(next_time) == now.astimezone().replace(tzinfo=None) + timedelta(minutes=10)
    assert xianyuan_auto_gift_next_time({'outcome': 'complete'}, {}) is None
    assert xianyuan_auto_gift_next_time({'outcome': 'partial'}, {'gift_probe_only': True}) is None


def test_due_retry_uses_scheduler_accepted_local_time():
    result = {'outcome': 'partial', 'action_result': {'yield_for_due_job': {
        'retry_at': '2026-10-08T12:00:00+08:00'}}}
    expected = datetime.fromisoformat('2026-10-08T12:00:00+08:00').astimezone().replace(tzinfo=None)
    assert parse_scheduler_time(xianyuan_auto_gift_next_time(result, {})) == expected


@pytest.mark.parametrize('route,scheduled', [('xianyuan_auto_gift', True), ('unknown_route', False)])
def test_complete_bag_schedules_only_the_declared_gift_route(monkeypatch, route, scheduled):
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
    assert ('xianyuan-auto-gift' in writes) is scheduled
    assert writes[-1] == 'storage-bag-operation'
