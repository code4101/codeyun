"""Watcher replacement contracts; real dispatch lock, no Kernel/game actions."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from filelock import FileLock, Timeout
from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control


@pytest.mark.parametrize('heartbeat_active', [True, False])
@pytest.mark.parametrize('owner', ['busy', 'projection', 'attempt', 'dispatch_lock', 'unknown_kernel', 'read_failure'])
def test_api_preserves_live_dispatcher_until_terminal_receipt_writer_finishes(tmp_path, monkeypatch, heartbeat_active, owner):
    actions = []
    process = SimpleNamespace(is_running=lambda: True,
        cmdline=lambda: ['python', 'scripts/fanxiu_bt.py', 'watch-doctor'],
        terminate=lambda: actions.append('terminate'), wait=lambda **kw: actions.append('wait'))
    monkeypatch.setattr(control.psutil, 'Process', lambda pid: process)
    monkeypatch.setattr(control, 'read_scheduler_settings', lambda: {'job_group_enabled':True})
    monkeypatch.setattr(control, 'read_doctor_watch_heartbeat', lambda **kw: {'active':heartbeat_active,'code_consistent':False,'pid':123})
    monkeypatch.setattr(control, 'fanxiu_kernel_scheduler_state_path', lambda: tmp_path / 'scheduler.json')
    monkeypatch.setattr('backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status',
                        lambda: {'execution_state':'busy' if owner == 'busy' else None if owner == 'unknown_kernel' else 'idle'})
    monkeypatch.setattr(control, 'read_kernel_scheduler_status', lambda: {'running':owner == 'projection'})
    def tasks():
        if owner == 'read_failure':
            raise OSError('state unavailable')
        return [{'id':'daily-redpacket','last_result':'running','attempt_id':'a'*32}] if owner == 'attempt' else []
    monkeypatch.setattr(control, 'read_scheduler_tasks', tasks)
    def forbidden(*a, **kw):
        raise AssertionError('replacement must not spawn a dispatcher')
    monkeypatch.setattr(control, 'popen_python_script_service', forbidden)
    lease = FileLock(str(tmp_path / 'scheduler_cell_dispatch.lock'), timeout=0) if owner == 'dispatch_lock' else nullcontext()
    with lease:
        result = control.ensure_doctor_watch_background()
    assert result['started'] is False
    assert result['reason'] == ('replacement_state_unavailable' if owner == 'read_failure' else 'replacement_deferred_active_execution')
    assert actions == []


def test_idle_replacement_holds_real_dispatch_lane_during_process_termination(tmp_path, monkeypatch):
    actions = []
    path = tmp_path / 'scheduler_cell_dispatch.lock'
    def terminate():
        with pytest.raises(Timeout):
            with FileLock(str(path), timeout=0):
                pass
        actions.append('terminate')
    process = SimpleNamespace(is_running=lambda: True,
        cmdline=lambda: ['python', 'scripts/fanxiu_bt.py', 'watch-doctor'],
        terminate=terminate, wait=lambda **kw: actions.append('wait'))
    monkeypatch.setattr(control.psutil, 'Process', lambda pid: process)
    monkeypatch.setattr(control, 'read_scheduler_settings', lambda: {'job_group_enabled':True})
    monkeypatch.setattr(control, 'read_doctor_watch_heartbeat', lambda **kw: {'active':True,'code_consistent':False,'pid':123})
    monkeypatch.setattr(control, 'fanxiu_kernel_scheduler_state_path', lambda: tmp_path / 'scheduler.json')
    monkeypatch.setattr('backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status', lambda: {'execution_state':'idle'})
    monkeypatch.setattr(control, 'read_kernel_scheduler_status', lambda: {})
    monkeypatch.setattr(control, 'read_scheduler_tasks', lambda: [])
    monkeypatch.setattr(control, 'codeyun_temp_root', lambda *a: tmp_path)
    monkeypatch.setattr(control, 'popen_python_script_service', lambda *a, **kw: SimpleNamespace(pid=456))
    result = control.ensure_doctor_watch_background()
    assert result['started'] is True
    assert result['replaced_pid'] == 123
    assert actions == ['terminate','wait']
    with FileLock(str(path), timeout=0):
        pass
