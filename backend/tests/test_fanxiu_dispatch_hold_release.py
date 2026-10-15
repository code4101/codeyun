"""Dispatch-hold release rule; pure data, no game or scheduler state."""
from backend.core.fanxiu.data_annotation.kernel_scheduler_control import dispatch_hold_release_decision

HOLD = {'status': 'active', 'held_at_epoch': 100.0, 'role_id': 7, 'pid': 1, 'process_start_ticks': 2}


def _world(**over):
    return {'kind': 'world_scene', 'scene_id': 34, 'observed_at': 200.0, 'role_id': 7, 'pid': 1,
            'process_start_ticks': 2, **over}


def test_fresh_world_after_hold_releases():
    assert dispatch_hold_release_decision(HOLD, _world()) == (True, 'fresh_world_after_hold')
    assert dispatch_hold_release_decision(HOLD, _world(pid=9)) == (True, 'process_changed_since_hold')
    terminal = {'kind': 'task_terminal', 'scene_id': 34, 'observed_at': 200.0}
    assert dispatch_hold_release_decision(HOLD, terminal)[0] is True


def test_stale_wrong_or_unknown_evidence_never_releases():
    assert dispatch_hold_release_decision(HOLD, _world(observed_at=100.0)) == (False, 'evidence_not_after_hold')
    assert dispatch_hold_release_decision(HOLD, _world(scene_id=420)) == (False, 'evidence_not_world_34')
    assert dispatch_hold_release_decision(HOLD, _world(role_id=8)) == (False, 'different_role')
    assert dispatch_hold_release_decision(HOLD, _world(pid=None)) == (False, 'evidence_process_unknown')
    assert dispatch_hold_release_decision(HOLD, _world(observed_at=None)) == (False, 'evidence_time_unknown')
    assert dispatch_hold_release_decision(HOLD, {**_world(), 'kind': 'kernel_idle'})[0] is False
    assert dispatch_hold_release_decision({**HOLD, 'status': 'released'}, _world())[0] is False


def test_legacy_hold_without_timestamp_needs_fresh_world_evidence():
    legacy = {'label': 'old'}
    assert dispatch_hold_release_decision(legacy, _world())[0] is True
    assert dispatch_hold_release_decision(legacy, _world(scene_id=419))[0] is False
