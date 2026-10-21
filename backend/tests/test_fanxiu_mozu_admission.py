"""Pure time-window decisions; no game scene, Runtime or click simulation."""
from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation.tasks.mozu import plan_daily_mozu_admission


@pytest.mark.parametrize('clock,next_day', [
    ('12:29:59', False), ('12:35:00.000001', True), ('12:37:57', True),
])
def test_outside_window_skips_without_claiming_participation(clock, next_day):
    now = datetime.fromisoformat('2026-10-10T' + clock + '+08:00')
    decision = plan_daily_mozu_admission(now)
    assert decision['result'] == 'success'
    assert decision['outcome'] == 'skipped_window'
    assert decision['game_actions_performed'] is False
    assert decision['current_scene'] is None
    assert decision['next_time'] == f"2026-10-{'11' if next_day else '10'} 12:30:00"


@pytest.mark.parametrize('clock', ['12:30:00', '12:34:12', '12:35:00'])
def test_existing_inclusive_window_still_admits(clock):
    assert plan_daily_mozu_admission(datetime.fromisoformat('2026-10-10T' + clock)) is None


def test_skip_fields_survive_terminal_projection_and_exact_receipt(tmp_path):
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import build_task_cell_terminal
    from backend.core.fanxiu.data_annotation.kernel_scheduler_results import (
        record_scheduler_cell_result, read_scheduler_cell_result,
    )
    decision = plan_daily_mozu_admission(datetime(2026, 10, 10, 12, 37, 57))
    # The business provider consumes next_time before returning admission.
    next_time = decision.pop('next_time')
    inner = build_task_cell_terminal('success', decision['message'], decision)
    outer = build_task_cell_terminal('success', inner['message'], inner['business_result'])
    record_scheduler_cell_result(
        'legacy-daily-mozu', 'e' * 32,
        cell_result={'status': 'success', 'msg_id': 'protocol-cell-identity'},
        business_result=outer['business_result'], started_at='2026-10-10 12:37:57',
        finished_at='2026-10-10 12:37:58', scheduler_result='success', directory=tmp_path,
    )
    actual = read_scheduler_cell_result('legacy-daily-mozu', 'e' * 32, directory=tmp_path)
    assert actual['business_result'] == decision
    assert actual['business_result']['game_actions_performed'] is False
    assert actual['business_result']['outcome'] == 'skipped_window'
    assert actual['scheduler_result'] == 'success'
    assert actual['cell_result']['msg_id'] == 'protocol-cell-identity'
    assert next_time == '2026-10-11 12:30:00'


_COMPLETE = {'complete': True, 'reason': None}


@pytest.mark.parametrize('scene_id,after', [
    (85, {'complete': True, 'left_times': 1}),
    (34, {'complete': True, 'left_times': 1}),
    (None, {'complete': False, 'reason': 'manager_missing', 'left_times': 0}),
])
def test_generic_or_world_landing_without_counter_drop_never_proves_participation(scene_id, after):
    from backend.core.fanxiu.data_annotation.tasks.mozu import judge_mozu_landing
    judgement = judge_mozu_landing(scene_id, {**_COMPLETE, 'left_times': 1}, after)
    assert judgement['participation_confirmed'] is False
    assert judgement['after']['left_times'] in (1, None)
    assert judgement['after']['complete'] is after['complete']


def test_landing_confirmed_by_battlefield_id_or_complete_counter_drop():
    from backend.core.fanxiu.data_annotation.tasks.mozu import judge_mozu_landing
    assert judge_mozu_landing(338, {'complete': False}, {'complete': False})['entry_observed'] is True
    by_counter = judge_mozu_landing(34, {**_COMPLETE, 'left_times': 2}, {**_COMPLETE, 'left_times': 1})
    assert by_counter['participation_confirmed'] and by_counter['runtime_confirmed']
    assert not by_counter['entry_observed']
