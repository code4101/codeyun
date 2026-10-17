"""Dispatch-hold release rule; pure data, no game or scheduler state."""
import math

from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
    dispatch_hold_release_decision,
    task_terminal_ended_at_world,
)

HOLD = {'status': 'active', 'held_at_epoch': 100.0, 'role_id': 7, 'pid': 1, 'process_start_ticks': 2}
NOW = 210.0


def _world(**over):
    return {'kind': 'world_scene', 'scene_id': 34, 'observed_at': 200.0, 'role_id': 7, 'pid': 1,
            'process_start_ticks': 2, **over}


def test_fresh_world_after_hold_releases():
    assert dispatch_hold_release_decision(HOLD, _world(), now=NOW) == (True, 'fresh_world_after_hold')
    assert dispatch_hold_release_decision(HOLD, _world(pid=9), now=NOW) == (True, 'process_changed_since_hold')
    terminal = {**_world(), 'kind': 'task_terminal'}
    assert dispatch_hold_release_decision(HOLD, terminal, now=NOW)[0] is True


def test_non_finite_future_or_stale_times_never_release():
    for bad in (math.nan, math.inf, -1, 0, True, None):
        assert dispatch_hold_release_decision(HOLD, _world(observed_at=bad), now=NOW) == (False, 'evidence_time_invalid')
    assert dispatch_hold_release_decision(HOLD, _world(observed_at=NOW + 5), now=NOW) == (False, 'evidence_not_fresh')
    assert dispatch_hold_release_decision(HOLD, _world(observed_at=85.0), now=NOW) == (False, 'evidence_not_fresh')
    assert dispatch_hold_release_decision(HOLD, _world(observed_at=100.0), now=150.0) == (False, 'evidence_not_after_hold')
    assert dispatch_hold_release_decision({**HOLD, 'held_at_epoch': math.nan}, _world(), now=NOW)[0] is False


def test_wrong_scene_role_or_unknown_process_never_releases():
    assert dispatch_hold_release_decision(HOLD, _world(scene_id=420), now=NOW) == (False, 'evidence_not_world_34')
    assert dispatch_hold_release_decision(HOLD, _world(role_id=8), now=NOW) == (False, 'different_role')
    assert dispatch_hold_release_decision(HOLD, _world(pid=None), now=NOW) == (False, 'evidence_process_unknown')
    assert dispatch_hold_release_decision(HOLD, {**_world(), 'kind': 'kernel_idle'}, now=NOW)[0] is False
    assert dispatch_hold_release_decision({**HOLD, 'status': 'released'}, _world(), now=NOW)[0] is False


def test_legacy_hold_without_timestamp_still_needs_fresh_world_evidence():
    legacy = {'label': 'old'}
    assert dispatch_hold_release_decision(legacy, _world(), now=NOW)[0] is True
    assert dispatch_hold_release_decision(legacy, _world(observed_at=-1), now=NOW)[0] is False
    assert dispatch_hold_release_decision(legacy, _world(observed_at=85.0), now=NOW)[0] is False


def test_task_terminal_world_accepts_kernel_cell_wrapper():
    business = {'result': 'success', 'current_scene': 34}
    assert task_terminal_ended_at_world(business) is True
    assert task_terminal_ended_at_world({'result': 'success', 'message': '', 'business_result': business}) is True
    assert task_terminal_ended_at_world(
        {'result': 'success', 'business_result': {'result': 'success', 'current_scene': None}}) is False
    assert task_terminal_ended_at_world({'result': 'error', 'business_result': business}) is False
