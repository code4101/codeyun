"""Pure priority/deadline contracts; no simulated game or GUI."""
from datetime import datetime, timezone, timedelta
import pytest
from backend.core.fanxiu.data_annotation.batch_admission import plan_scheduler_batch_admission


def rows(trigger='2026-10-07 12:30:00', level=1):
    return [{'id': 'resource-ranking', 'dispatch_level': 0},
            {'id': 'mozu', 'dispatch_level': level, 'next_time': trigger}]


@pytest.mark.parametrize('clock,admitted', [('12:25:59', True), ('12:26:00', False),
                                          ('12:29:00', False), ('12:31:00', False)])
def test_upcoming_and_due_trigger_reserves_batch_and_departure(clock, admitted):
    result = plan_scheduler_batch_admission(rows(), task_id='resource-ranking',
        now=datetime.fromisoformat(f'2026-10-07 {clock}'), batch_seconds=180)
    assert result['admitted'] is admitted
    if not admitted:
        assert result['protected_task_id'] == 'mozu'
        assert datetime.fromisoformat(result['retry_at']) > datetime.fromisoformat(f'2026-10-07 {clock}')


def test_completed_trigger_and_same_level_do_not_block():
    for tasks in (rows('2026-10-08 12:30:00'), rows(level=0), rows(None)):
        assert plan_scheduler_batch_admission(tasks, task_id='resource-ranking',
            now=datetime(2026, 10, 7, 12, 29), batch_seconds=180)['admitted']


def test_local_naive_scheduler_time_matches_aware_wall_clock():
    now = datetime(2026, 10, 7, 12, 29, tzinfo=timezone(timedelta(hours=8)))
    result = plan_scheduler_batch_admission(rows(), task_id='resource-ranking',
        now=now, batch_seconds=180)
    assert not result['admitted']
    assert result['retry_at'] == '2026-10-07T12:31:00+08:00'


@pytest.mark.parametrize('budget', [0, -1, float('inf'), float('nan')])
def test_invalid_budget_cannot_authorize(budget):
    with pytest.raises(ValueError):
        plan_scheduler_batch_admission(rows(), task_id='resource-ranking',
            now=datetime(2026, 10, 7), batch_seconds=budget)


def test_missing_formal_job_cannot_authorize():
    with pytest.raises(ValueError):
        plan_scheduler_batch_admission(rows(), task_id='unknown',
            now=datetime(2026, 10, 7), batch_seconds=180)
