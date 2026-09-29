"""Deterministic certificate and persistent restart-budget contracts only."""
import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.core.fanxiu.data_annotation.popup_recovery import (
    exit_shape_signature, reserve_popup_restart, verified_exit_recovery,
)


def test_certificate_revoked_by_geometry_or_matching_change():
    shape = dict(title='关闭', x=.4, y=.8, w=.1, h=.05, imageMatchRole='off')
    assert not verified_exit_recovery(shape)
    shape['verifiedExitRecovery'] = dict(action='restart_emulator', shape_signature=exit_shape_signature(shape))
    assert verified_exit_recovery(shape)
    inherited = {**shape, '_inheritanceHostSceneId': 693}
    assert verified_exit_recovery(inherited)
    shape['verifiedExitRecovery'].update(scene_id=693, frame_size=[900, 1600])
    assert verified_exit_recovery(shape, scene_id=693, frame_size=(900, 1600))
    assert not verified_exit_recovery(shape, scene_id=694, frame_size=(900, 1600))
    assert not verified_exit_recovery(shape, scene_id=693, frame_size=(720, 1280))
    for key, value in [('x', .5), ('imageMatchRole', 'required'), ('title', '购买')]:
        changed = copy.deepcopy(shape)
        changed[key] = value
        assert not verified_exit_recovery(changed)


def test_restart_budget_survives_new_caller_and_is_atomic(tmp_path):
    path = tmp_path / 'budget.json'
    def reserve(_):
        try:
            reserve_popup_restart(path, now=2000)
            return True
        except RuntimeError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, range(4))) == 1
    with pytest.raises(RuntimeError):
        reserve_popup_restart(path, now=3799)
    reserve_popup_restart(path, now=3800)


@pytest.mark.parametrize('text', ['{', '[]', '{"last_restart_at": "nan"}'])
def test_corrupt_budget_fails_closed(tmp_path, text):
    path = tmp_path / 'budget.json'
    path.write_text(text)
    with pytest.raises((ValueError, TypeError, KeyError)):
        reserve_popup_restart(path, now=2000)


def test_scheduler_finishes_attempt_then_recovers_logs_in_and_retries(monkeypatch, tmp_path):
    """Submission-lease ordering only; no synthetic game or recognition."""
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
    from backend.core.fanxiu.data_annotation import popup_recovery
    from backend.core.fanxiu.behavior_tree.kernel import FanxiuKernel
    events = []
    task = {'id': 'original', 'task_type': 'test'}
    def terminal(**kw):
        events.append(('terminal', kw['task']['id'], kw['scheduled_attempt']))
        if len(events) == 1:
            return {'status': 'error', 'error_type': 'VerifiedPopupExitStalled', 'error': 'verified'}
        return {'status': 'success', 'phase': 'done'}
    def restart(**kw):
        assert kw['dispatch_lease'].is_locked
        events.append('restart')
    monkeypatch.setattr(control, '_run_scheduler_task_cell_and_record_terminal_owned', terminal)
    monkeypatch.setattr(popup_recovery, 'restart_after_verified_popup_stall', restart)
    monkeypatch.setattr(FanxiuKernel, 'restart', lambda *a, **kw: {'ok': True})
    monkeypatch.setattr(control, 'ensure_scheduler_kernel_code_current', lambda **kw: {'ready': True})
    monkeypatch.setattr(control, 'read_scheduler_tasks', lambda **kw: [{'id': 'login-game', 'task_type': 'login_game'}])
    monkeypatch.setattr('backend.core.fanxiu.data_annotation.ai_assistance.request_ai_assistance', lambda *a, **kw: None)
    result = control._run_scheduler_task_cell_and_record_terminal(
        entry=None, entry_id='test', task=task, scheduled_attempt=True,
        scheduler_state_path=tmp_path / 'scheduler.json')
    assert result['status'] == 'success'
    assert events == [('terminal', 'original', True), 'restart',
                      ('terminal', 'login-game', False), ('terminal', 'original', False)]
