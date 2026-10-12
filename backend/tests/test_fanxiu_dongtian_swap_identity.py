"""Pure application guards; no emulator, Fake Context or game simulation."""
from backend.core.fanxiu.instrumentation.dongtian_swap import validate_dongtian_swap_picker


def test_observed_master_row_mismatch_cannot_authorize_application():
    # Real #612 observation on 2026-10-12: row labeled 尊主互换1 opened seat3.
    observed = dict(complete=True, selected_team_id=1, target_mine_id=21,
                    native_quality=1, target_seat_id=3,
                    target_fight_score=1421695613089835)
    target = dict(team_id=1, mine_id=21, quality=1, seat_id=1)
    result = validate_dongtian_swap_picker(
        observed, target, team_score=973232969604825, team3_score=107031894143057)
    assert not result['ok']
    assert result['actual'] != result['expected']


def test_strict_combat_and_ally_safety_boundaries():
    target = dict(team_id=1, mine_id=21, quality=1, seat_id=1)
    observed = dict(complete=True, selected_team_id=1, target_mine_id=21,
                    native_quality=1, target_seat_id=1, target_fight_score=80)
    assert not validate_dongtian_swap_picker(observed, target, team_score=100, team3_score=100)['ok']
    observed['target_fight_score'] = 79
    assert validate_dongtian_swap_picker(observed, target, team_score=100, team3_score=100)['ok']
    assert not validate_dongtian_swap_picker(observed, target, team_score=79, team3_score=100)['ok']


def test_incomplete_picker_cannot_authorize_application():
    assert not validate_dongtian_swap_picker({}, {}, team_score=100, team3_score=100)['ok']


def test_complete_own_team_probe_proves_seating_without_map_authorization():
    from backend.core.fanxiu.data_annotation.tasks.dongtian_seating_job import classify_dongtian_team_seating
    probe = dict(ok=True, available=True, complete=True, status='noop_no_idle',
                 teams=[dict(id=i, state=2, mine_id=mine, seat_index=seat, complete=True)
                        for i, mine, seat in [(1, 33, 7), (2, 8, 11), (3, 5, 4)]])
    assert classify_dongtian_team_seating(probe)['status'] == 'all_seated'
    probe['teams'].pop()
    assert not classify_dongtian_team_seating(probe)['ok']


def test_claimed_no_idle_does_not_override_actual_idle_team():
    from backend.core.fanxiu.data_annotation.tasks.dongtian_seating_job import classify_dongtian_team_seating
    probe = dict(ok=True, available=True, complete=True, status='noop_no_idle',
                 teams=[dict(id=i, state=2, mine_id=i, seat_index=1, complete=True) for i in (1, 2, 3)])
    probe['teams'][0].update(state=1, mine_id=0, seat_index=0)
    assert classify_dongtian_team_seating(probe)['status'] == 'reseat_required'
