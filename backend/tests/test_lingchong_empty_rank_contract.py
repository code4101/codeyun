"""Pure projection of complete empty/native facts; no simulated game flow."""
from copy import deepcopy

import pytest

from backend.core.fanxiu.activity.lingchong_jingwu import project_lingchong_jingwu_rank_rows


def native_empty_fact():
    return {
        'rank_vo_type': 'ActivityRankPersonalVO', 'rank_list_size': 0,
        'items': [], 'rank': -1, 'score': 0,
        'protocol': 'runtime_memory_activity_rank',
        'evidence': {'pid': 3412, 'process_start_ticks': 50735,
                     'occurrence_runtime_id': '1042911400004'},
    }


@pytest.mark.parametrize('scope,vo', [('personal', 'ActivityRankPersonalVO'),
                                    ('plane', 'ActivityRankCrossServerVO')])
def test_confirmed_empty_scope_has_no_invented_player_or_reward_guard(scope, vo):
    fact = native_empty_fact()
    fact['rank_vo_type'] = vo
    original = deepcopy(fact)
    assert project_lingchong_jingwu_rank_rows(fact, scope=scope) == []
    assert fact == original


@pytest.mark.parametrize('change', [
    {'rank_list_size': None}, {'rank_list_size': False}, {'rank_list_size': -1},
    {'rank_list_size': 1}, {'items': None}, {'items': [None]},
    {'protocol': ''}, {'rank': 1}, {'rank': None},
    {'evidence': {}}, {'evidence': {'pid': 3412, 'process_start_ticks': 50735}},
    {'evidence': {'pid': 0, 'process_start_ticks': 50735, 'occurrence_runtime_id': '1042911400004'}},
])
def test_missing_partial_or_unbound_facts_still_fail_closed(change):
    fact = {**native_empty_fact(), **change}
    with pytest.raises(ValueError):
        project_lingchong_jingwu_rank_rows(fact, scope='personal')


def test_wrong_vo_scope_is_rejected_even_for_confirmed_empty_board():
    with pytest.raises(ValueError, match='VO'):
        project_lingchong_jingwu_rank_rows(native_empty_fact(), scope='plane')
