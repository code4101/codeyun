"""Formal resource jobs exclude their own due clock and preserve urgent work."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.core.fanxiu.data_annotation import kernel_scheduler_control as scheduler


@pytest.mark.parametrize('task_id', ['storage-bag-operation', 'xianyuan-auto-gift'])
@pytest.mark.parametrize('urgent_at,admitted', [
    ('2026-10-08 12:30:00', True),
    ('2026-10-08 12:16:00', False),
    ('2026-10-08 12:00:00', False),
])
def test_public_formal_admission_never_yields_to_itself(monkeypatch, task_id, urgent_at, admitted):
    tasks = [{'id': task_id, 'dispatch_level': 0,
              'next_time': '2026-10-08 12:00:00', 'last_result': 'running'},
             {'id': 'legacy-daily-mozu', 'dispatch_level': 1, 'next_time': urgent_at}]
    monkeypatch.setattr(scheduler, 'read_scheduler_tasks', lambda: tasks)
    monkeypatch.setattr(scheduler, 'scheduler_tasks_for_dispatch', lambda rows: rows)
    now = datetime(2026, 10, 8, 12, 11, tzinfo=timezone(timedelta(hours=8)))
    result = scheduler.read_scheduler_batch_admission(
        task_id=task_id, now=now, batch_seconds=600, release_seconds=120)
    assert result['admitted'] is admitted
    if not admitted:
        assert result['protected_task_id'] == 'legacy-daily-mozu'
    # A research context has no owning Job and correctly sees that due clock.
    research = scheduler.read_scheduler_research_batch_admission(
        now=now, batch_seconds=600, release_seconds=120)
    assert research['admitted'] is False
    expected = 'legacy-daily-mozu' if urgent_at == tasks[0]['next_time'] else task_id
    assert research['protected_task_id'] == expected


@pytest.mark.parametrize('task_id', ['storage-bag-operation', 'xianyuan-auto-gift'])
def test_formal_protect_all_preserves_same_level_duty_and_excludes_owner(monkeypatch, task_id):
    tasks = [{'id': task_id, 'dispatch_level': 0, 'next_time': '2026-10-08 12:00:00'},
             {'id': 'legacy-daily-assistant', 'dispatch_level': 0,
              'next_time': '2026-10-08 12:00:00'}]
    monkeypatch.setattr(scheduler, 'read_scheduler_tasks', lambda: tasks)
    monkeypatch.setattr(scheduler, 'scheduler_tasks_for_dispatch', lambda rows: rows)
    result = scheduler.read_scheduler_batch_admission(task_id=task_id,
        now=datetime(2026, 10, 8, 12, 11), batch_seconds=600, protect_all=True)
    assert result['admitted'] is False
    assert result['protected_task_id'] == 'legacy-daily-assistant'


def test_declared_formal_identity_must_exist_even_when_protecting_all(monkeypatch):
    monkeypatch.setattr(scheduler, 'read_scheduler_tasks', lambda: [])
    monkeypatch.setattr(scheduler, 'scheduler_tasks_for_dispatch', lambda rows: rows)
    with pytest.raises(ValueError, match='正式作业不存在'):
        scheduler.read_scheduler_batch_admission(task_id='missing-job',
            now=datetime(2026, 10, 8, 12, 11), batch_seconds=600, protect_all=True)
    assert scheduler.read_scheduler_research_batch_admission(
        now=datetime(2026, 10, 8, 12, 11), batch_seconds=600)['admitted'] is True


def test_resource_order_breaks_mutual_yield_then_releases_downstream(monkeypatch):
    tasks = [{'id': 'storage-bag-operation', 'dispatch_level': 0, 'next_time': '2026-10-08 12:31:00'},
             {'id': 'xianyuan-auto-gift', 'dispatch_level': 0, 'next_time': '2026-10-08 12:32:00'}]
    monkeypatch.setattr(scheduler, 'read_scheduler_tasks', lambda: tasks)
    monkeypatch.setattr(scheduler, 'scheduler_tasks_for_dispatch', lambda rows: rows)
    options = {'now': datetime(2026, 10, 8, 12, 36), 'batch_seconds': 600,
               'protect_all': True,
               'ordered_task_ids': ('storage-bag-operation', 'xianyuan-auto-gift')}
    assert scheduler.read_scheduler_batch_admission(task_id='storage-bag-operation', **options)['admitted'] is True
    downstream = scheduler.read_scheduler_batch_admission(task_id='xianyuan-auto-gift', **options)
    assert downstream['admitted'] is False
    assert downstream['protected_task_id'] == 'storage-bag-operation'
    # Once the upstream queue closes its daily trigger, the gift Job can run.
    tasks[0]['next_time'] = '2026-10-09 00:00:00'
    assert scheduler.read_scheduler_batch_admission(task_id='xianyuan-auto-gift', **options)['admitted'] is True


@pytest.mark.parametrize('task_id', ['storage-bag-operation', 'xianyuan-auto-gift'])
def test_resource_order_never_masks_unrelated_same_level_due_duty(monkeypatch, task_id):
    tasks = [{'id': 'storage-bag-operation', 'dispatch_level': 0, 'next_time': '2026-10-08 12:31:00'},
             {'id': 'xianyuan-auto-gift', 'dispatch_level': 0, 'next_time': '2026-10-08 12:32:00'},
             {'id': 'daily-redpacket', 'dispatch_level': 0, 'next_time': '2026-10-08 12:00:00'}]
    monkeypatch.setattr(scheduler, 'read_scheduler_tasks', lambda: tasks)
    monkeypatch.setattr(scheduler, 'scheduler_tasks_for_dispatch', lambda rows: rows)
    result = scheduler.read_scheduler_batch_admission(task_id=task_id,
        now=datetime(2026, 10, 8, 12, 36), batch_seconds=600, protect_all=True,
        ordered_task_ids=('storage-bag-operation', 'xianyuan-auto-gift'))
    assert result['admitted'] is False
    assert result['protected_task_id'] == 'daily-redpacket'


@pytest.mark.parametrize('task_id', ['storage-bag-operation', 'xianyuan-auto-gift'])
def test_resource_job_reserves_equal_level_duties_without_self_blocking(monkeypatch, task_id):
    tasks = [{'id': task_id, 'dispatch_level': 0, 'next_time': '2026-10-08 12:00:00'},
             {'id': 'moyu-challenge', 'dispatch_level': 0, 'next_time': '2026-10-08 12:16:00'}]
    monkeypatch.setattr(scheduler, 'read_scheduler_tasks', lambda: tasks)
    monkeypatch.setattr(scheduler, 'scheduler_tasks_for_dispatch', lambda rows: rows)
    now = datetime(2026, 10, 8, 12, 11, tzinfo=timezone(timedelta(hours=8)))
    protected = scheduler.read_scheduler_batch_admission(task_id=task_id,
        now=now, batch_seconds=600, release_seconds=120, protect_all=True)
    assert protected['admitted'] is False
    assert protected['protected_task_id'] == 'moyu-challenge'
    tasks.pop()
    assert scheduler.read_scheduler_batch_admission(task_id=task_id,
        now=now, batch_seconds=600, release_seconds=120, protect_all=True)['admitted'] is True
