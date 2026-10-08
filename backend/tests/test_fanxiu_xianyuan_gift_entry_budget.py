"""Reject an oversized gift attempt before navigation or expensive Runtime reads."""
from types import SimpleNamespace

from backend.core.fanxiu.data_annotation import kernel_scheduler_control as scheduler
from backend.core.fanxiu.data_annotation.tasks import xianyuan_auto_gift as gifts


def test_due_job_rejection_precedes_gui_and_runtime_readers(monkeypatch):
    admission = {'admitted': False, 'protected_task_id': 'legacy-daily-mozu',
                 'retry_at': '2026-10-08T12:31:00+08:00'}
    calls = []

    def budget(**kwargs):
        calls.append(kwargs)
        return admission

    def forbidden(*args, **kwargs):
        raise AssertionError('entry rejection must precede GUI and Runtime reads')

    monkeypatch.setattr(scheduler, 'read_scheduler_batch_admission', budget)
    monkeypatch.setattr(gifts, 'read_selected_storage_gifts_runtime', forbidden)
    runner = SimpleNamespace(_behavior_tree_context=forbidden)
    task = gifts.execute_xianyuan_auto_gift_task(runner, {},
        {'__scheduler_task_id': 'xianyuan-auto-gift'}, None,
        storage_reader=forbidden, xianyuan_reader=forbidden)
    try:
        next(task)
    except StopIteration as finished:
        result = finished.value
    else:
        raise AssertionError('rejected attempt cannot start GUI navigation')
    assert result['ok'] is True
    assert result['outcome'] == 'partial'
    assert '未执行' in result['message']
    assert '完成' not in result['message']
    assert result['action_result']['yield_for_due_job'] == admission
    assert calls[0]['task_id'] == 'xianyuan-auto-gift'
    assert calls[0]['protect_all'] is True
    assert calls[0]['batch_seconds'] >= 600
