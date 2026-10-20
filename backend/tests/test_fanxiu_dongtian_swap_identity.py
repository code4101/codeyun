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


def _seat_snapshot(**seats):
    """``key -> (guarder_type, role_id)``; ``None`` marks an incomplete seat."""
    mines = {}
    for name, value in seats.items():
        mine_id, quality, seat_id = (int(part) for part in name[1:].split('_'))
        seat = dict(quality=quality, id=seat_id, complete=value is not None, guarder_present=value is not None)
        if value is not None:
            seat.update(guarder_type=value[0], guarder_role_id=value[1])
        mines.setdefault(mine_id, []).append(seat)
    return dict(mines=[dict(id=mine_id, seats=s) for mine_id, s in mines.items()])


def test_defender_score_binds_only_identity_stable_across_scan():
    from backend.core.fanxiu.data_annotation.tasks.dongtian_seating_job import (
        bind_dongtian_defender_scores, retain_dongtian_defender_scores)
    before = _seat_snapshot(k21_1_1=(1, 101), k21_2_5=(1, 501), k21_2_6=(1, 601), k21_2_7=(1, 701), k21_2_8=None)
    after = _seat_snapshot(k21_1_1=(1, 101), k21_2_5=(1, 502), k21_2_6=(1, True), k21_2_7=(1, 701), k21_2_8=(1, 801))
    scanned = {'21:1:1': 10, '21:2:5': 50, '21:2:6': 60, '21:2:7': 70, '21:2:8': 80}
    scores, identities = bind_dongtian_defender_scores(before, after, scanned, {'21:1:1': 101})
    # changed occupant, bool role id and pre-scan unknown are all dropped
    assert scores == {'21:1:1': 10, '21:2:7': 70}
    assert identities == {'21:1:1': (1, 101), '21:2:7': (1, 701)}
    # retain after a correct bind: unchanged kept, later change dropped
    later = _seat_snapshot(k21_1_1=(1, 101), k21_2_7=(1, 702))
    assert retain_dongtian_defender_scores(later, scores, identities) == {'21:1:1': 10}


def test_master_score_requires_matching_detail_identity():
    from backend.core.fanxiu.data_annotation.tasks.dongtian_seating_job import bind_dongtian_defender_scores
    snapshot = _seat_snapshot(k21_1_1=(1, 101))
    for detail in ({'21:1:1': 303}, {'21:1:1': None}, {'21:1:1': True}, {}, None):
        assert bind_dongtian_defender_scores(snapshot, snapshot, {'21:1:1': 10}, detail) == ({}, {})
