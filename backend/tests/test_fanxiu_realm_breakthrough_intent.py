"""Breakthrough path gate and intent outcome decisions; pure data, no game."""
import pytest
from backend.core.fanxiu.data_annotation.tasks.realm_breakthrough import (
    decide_realm_breakthrough_outcome, realm_breakthrough_path_supported)

REQ = {'level': 250, 'target_level': 251, 'tribulation_dungeon_id': 0}
WINDOW = {'complete': True, 'conditions_met': True, 'dungeon_id_observed': 0, 'implementation': 'LeijieViewNew'}
INTENT = {'target_level': 251, 'role_before': {'role_id': 7, 'level': 250, 'current_exp': 100,
                                              'pid': 1, 'process_start_ticks': 2}}


def _role(level, *, role_id=7, exp=100, pid=1, ticks=2, complete=True):
    return {'complete': complete, 'role_id': role_id, 'level': level, 'current_exp': exp,
            'evidence': {'pid': pid, 'process_start_ticks': ticks}}


def test_only_supported_250_path_with_exact_zero_dungeon_passes():
    assert realm_breakthrough_path_supported(REQ, WINDOW)
    for req in ({**REQ, 'tribulation_dungeon_id': None}, {**REQ, 'tribulation_dungeon_id': 5},
                {**REQ, 'level': 251, 'target_level': 252}, {**REQ, 'tribulation_dungeon_id': False}):
        assert not realm_breakthrough_path_supported(req, WINDOW)
    for window in ({**WINDOW, 'conditions_met': False}, {**WINDOW, 'dungeon_id_observed': None},
                   {**WINDOW, 'dungeon_id_observed': 9}, {**WINDOW, 'complete': False},
                   {**WINDOW, 'implementation': 'LeiJieView_legacy'}, {**WINDOW, 'implementation': 'unknown'}):
        assert not realm_breakthrough_path_supported(REQ, window)


def test_live_action_requires_same_role_and_process():
    assert decide_realm_breakthrough_outcome(INTENT, _role(251), require_same_process=True)[0] == 'verified'
    assert decide_realm_breakthrough_outcome(INTENT, _role(251, role_id=8), require_same_process=True) == (
        'pending', 'different_or_unknown_role')
    assert decide_realm_breakthrough_outcome(INTENT, _role(251, pid=9), require_same_process=True) == (
        'pending', 'process_changed_during_action')


def test_recovery_after_restart_uses_fresh_facts_of_the_same_role():
    assert decide_realm_breakthrough_outcome(INTENT, _role(251, pid=9, ticks=10), require_same_process=False)[0] == 'verified'
    assert decide_realm_breakthrough_outcome(INTENT, _role(251, complete=False), require_same_process=False)[0] == 'pending'


def test_unchanged_level_and_exp_never_release_the_intent():
    # Same role, same process, nothing changed: effect not observed yet, which
    # is not proof the tap was rejected or never committed -> stays pending.
    for role in (_role(250, exp=100), _role(250, pid=9), _role(250, exp=90)):
        for same in (True, False):
            if same and role['evidence']['pid'] != 1:
                continue
            assert decide_realm_breakthrough_outcome(INTENT, role, require_same_process=same) == (
                'pending', 'target_level_not_observed')


def test_old_records_missing_role_or_target_stay_pending():
    assert decide_realm_breakthrough_outcome({'target_level': 251}, _role(251), require_same_process=False)[0] == 'pending'
    assert decide_realm_breakthrough_outcome({'role_before': INTENT['role_before']}, _role(251),
                                             require_same_process=False)[0] == 'pending'


def test_breaking_limitation_parser():
    from backend.core.fanxiu.instrumentation.realm_breakthrough import parse_breaking_limitation
    assert parse_breaking_limitation('291,CL|999') == {'level': 291, 'condition': 'CL|999', 'raw': '291,CL|999'}
    assert parse_breaking_limitation(None)['level'] is None
    for bad in ('abc', '291,', ',CL|1', '0,CL|1'):
        with pytest.raises(Exception):
            parse_breaking_limitation(bad)
