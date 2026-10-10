"""A partial bag run must hand off to due jobs without losing its next retry."""
from datetime import datetime
from threading import Event

import pytest

from backend.core.fanxiu.data_annotation import default_jobs
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.kernel_scheduler_time import parse_scheduler_time
from backend.core.fanxiu.data_annotation.tasks import storage_bag_operation


def drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


@pytest.mark.parametrize('retry_at', [
    '2026-10-08T12:00:00+08:00',
    '2026-10-08T04:00:00+00:00',
])
def test_partial_yield_writes_valid_same_instant_retry(monkeypatch, retry_at):
    default_jobs.register_fanxiu_default_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition('storage_bag_operation')
    result = {'outcome': 'partial', 'remaining_planned_action_count': 5,
              'yield_for_due_job': {'admitted': False, 'retry_at': retry_at}}
    writes = []

    def execute(*args, **kwargs):
        yield from ()
        return result

    class Runner:
        def _persist_scheduler_task_next_time(self, task_id, next_time):
            # Enforce the real Scheduler's public timestamp parser contract.
            parsed = parse_scheduler_time(next_time)
            assert parsed is not None
            writes.append((task_id, parsed))

    monkeypatch.setattr(storage_bag_operation, 'execute_storage_bag_operation_task', execute)
    actual = drain(definition.handler(Runner(), {}, {}, Event()))
    assert {key: actual[key] for key in result} == result
    assert 'follow_up' not in result
    expected = datetime.fromisoformat(retry_at).astimezone().replace(tzinfo=None)
    assert writes == [('storage-bag-operation', expected)]
    assert actual['follow_up'] == {
        'reason': 'due_job_yield', 'next_time': expected.strftime('%Y-%m-%d %H:%M:%S')}
    assert actual['remaining_planned_action_count'] == 5


def test_unfinished_without_explicit_due_yield_does_not_advance_daily(monkeypatch):
    default_jobs.register_fanxiu_default_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition('storage_bag_operation')
    writes = []

    def execute(*args, **kwargs):
        yield from ()
        return {'outcome': 'partial', 'remaining_planned_action_count': 5}

    class Runner:
        def _persist_scheduler_task_next_time(self, *args):
            writes.append(args)

    monkeypatch.setattr(storage_bag_operation, 'execute_storage_bag_operation_task', execute)
    with pytest.raises(RuntimeError, match='未完成'):
        drain(definition.handler(Runner(), {}, {}, Event()))
    assert writes == []
