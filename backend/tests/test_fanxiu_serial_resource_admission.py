"""Related serial jobs progress without weakening unrelated due-job protection."""
from datetime import datetime

from backend.core.fanxiu.data_annotation.batch_admission import plan_scheduler_batch_admission
from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_policy import SELECTED_RESOURCE_JOB_ORDER


def admission(task_id, *, external_due=False):
    rows = [{'id': job, 'next_time': '2026-10-08 12:31:00', 'dispatch_level': 0}
            for job in SELECTED_RESOURCE_JOB_ORDER]
    if external_due:
        rows.append({'id': 'daily-duty', 'next_time': '2026-10-08 12:36:00', 'dispatch_level': 0})
    return plan_scheduler_batch_admission(rows, task_id=task_id,
        now=datetime(2026, 10, 8, 12, 36), batch_seconds=600,
        protect_all=True, ordered_task_ids=SELECTED_RESOURCE_JOB_ORDER)


def test_upstream_progresses_when_both_components_are_due():
    assert admission(SELECTED_RESOURCE_JOB_ORDER[0]) == {'admitted': True}


def test_downstream_protects_unfinished_upstream():
    result = admission(SELECTED_RESOURCE_JOB_ORDER[1])
    assert not result['admitted']
    assert result['protected_task_id'] == SELECTED_RESOURCE_JOB_ORDER[0]


def test_upstream_still_yields_to_unrelated_equal_level_due_duty():
    result = admission(SELECTED_RESOURCE_JOB_ORDER[0], external_due=True)
    assert not result['admitted']
    assert result['protected_task_id'] == 'daily-duty'
