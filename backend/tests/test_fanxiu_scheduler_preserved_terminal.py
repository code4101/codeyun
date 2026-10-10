"""Attempt/timestamp ownership checks, without simulating game execution."""
from datetime import datetime
import pytest
from backend.core.fanxiu.data_annotation.kernel_scheduler_terminal import project_preserved_scheduler_success


def evidence():
    start = datetime(2026, 10, 10, 16, 10, 15)
    end = datetime(2026, 10, 10, 16, 12, 54)
    task = dict(id='daily-lundao-seat', started_at=str(start), last_run_at=str(start),
                last_result='error', attempt_id=None,
                last_message='先前 Cell/Kernel 执行尝试已作废；AI 接管保留原触发时间，等待诊断')
    snapshot = dict(scheduler_task_id=task['id'], scheduler_attempt_id='original',
                    scheduler_terminal_result='success', status='success', running=False,
                    current_scene=34, started_at=start.timestamp(), finished_at=end.timestamp(),
                    scheduler_terminal_at=end.timestamp(), logs=[dict(attempt_id='original',
                    item_id=task['id'], message='论道_座位：保留三清，下次 2026-10-10 16:42:12')])
    return task, snapshot


def test_preserved_terminal_restores_original_trigger_without_changing_run_identity():
    task, snapshot = evidence()
    result = project_preserved_scheduler_success(task, snapshot, next_time='2026-10-10 16:42:12')
    assert result['last_result'] == 'success'
    assert result['next_time'] == '2026-10-10 16:42:12'
    assert result['finished_at'] == '2026-10-10 16:12:54'
    assert task['last_result'] == 'error'  # pure projection, no in-place mutation


@pytest.mark.parametrize('changes', [dict(last_result='success'), dict(attempt_id='newer'),
    dict(started_at='2026-10-10 16:20:00'), dict(last_message='真实业务失败')])
def test_recovery_rejects_newer_runs_and_real_business_errors(changes):
    task, snapshot = evidence()
    with pytest.raises(ValueError):
        project_preserved_scheduler_success({**task, **changes}, snapshot, next_time='2026-10-10 16:42:12')


@pytest.mark.parametrize('changes', [dict(scheduler_attempt_id='other'),
    dict(scheduler_task_id='other'), dict(scheduler_terminal_result='error'),
    dict(running=True), dict(current_scene=943), dict(logs=[]), dict(finished_at=0)])
def test_recovery_requires_matching_native_success_and_explicit_trigger(changes):
    task, snapshot = evidence()
    with pytest.raises(ValueError):
        project_preserved_scheduler_success(task, {**snapshot, **changes}, next_time='2026-10-10 16:42:12')


@pytest.mark.parametrize('settled', [False, True])
def test_idle_handoff_drains_terminal_before_disabling_engineering(monkeypatch, tmp_path, settled):
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
    events = []
    rows = [dict(id='job-a', last_result='running', attempt_id='same')]
    monkeypatch.setattr(control, 'kernel_scheduler_status', lambda **kw:
                        {'running': False, 'kernel': {'execution_state': 'idle'}})
    monkeypatch.setattr(control, 'read_scheduler_tasks', lambda **kw: [dict(t) for t in rows])
    def reconcile(tasks, **kw):
        events.append('reconcile')
        if settled:
            rows[0].update(last_result='success', attempt_id=None)
        return settled
    monkeypatch.setattr(control, 'reconcile_stale_scheduler_attempts', reconcile)
    monkeypatch.setattr(control, 'read_scheduler_settings', lambda **kw: {'job_group_enabled': True})
    monkeypatch.setattr(control, 'set_scheduler_job_group_enabled', lambda enabled, **kw:
                        events.append('disable') or {'job_group_enabled': enabled})
    result = control.take_ai_control('entry-a', only_if_idle=True,
                                    scheduler_state_path=tmp_path/'state.json',
                                    scheduler_settings_path=tmp_path/'settings.json')
    if settled:
        assert events == ['reconcile', 'disable']
        assert result['ai_control_acquired'] is True
    else:
        assert events == ['reconcile']
        assert result['ai_control_acquired'] is False
        assert result['handoff_reason'] == 'scheduler_terminal_pending'


def test_terminal_cas_never_overwrites_new_attempt(tmp_path):
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
    state = tmp_path/'state.json'
    row = dict(id='daily-lundao-seat', task_type='daily_lundao', last_result='running',
               attempt_id='newer', next_time='2026-10-10 16:42:12')
    assert control.write_scheduler_tasks([row], scheduler_state_path=state,
                                         preserve_execution_state=False)
    assert control.write_scheduler_tasks(
        [{**row, 'attempt_id': None, 'last_result': 'success'}],
        scheduler_state_path=state, execution_update_ids={row['id']},
        expected_execution_attempt_ids={row['id']: 'older'}) is False
    observed = next(t for t in control.read_scheduler_tasks(scheduler_state_path=state)
                    if t['id'] == row['id'])
    assert observed['attempt_id'] == 'newer'
    assert observed['last_result'] == 'running'
