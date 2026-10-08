"""Gift/reset navigation text cannot replace the original reward receipts."""
from copy import deepcopy

import pytest

from backend.core.fanxiu.data_annotation.tasks import xianyuan_gift_gui as gifts


def facts(states, *, npc_id=4005):
    return {
        'runtime_complete': True,
        'runtime_debug': {'pid': 21822, 'process_start_ticks': 12589765},
        'people': [{'npc_id': npc_id, 'rewards': [
            {'reward_key': key, 'state': state} for key, state in states.items()
        ]}],
    }


def reader(monkeypatch, after):
    requested = []
    def observe(*, npc_ids):
        requested.append(npc_ids)
        return after
    monkeypatch.setattr(gifts, 'read_xianyuan_atlas_runtime', observe)
    observed = {}
    callback = gifts.selected_npc_reward_completion_reader(
        facts({3890: 2, 3891: 1, 3892: 1}), observed)
    return callback, observed, requested


def test_receipts_complete_without_assuming_bottom_button(monkeypatch):
    after = facts({3890: 2, 3891: 2, 3892: 2})
    callback, observed, requested = reader(monkeypatch, after)
    assert callback() is True
    assert observed['after'] is after
    assert requested == [[4005]]


@pytest.mark.parametrize('states', [
    {3890: 2, 3891: 2},  # An original eligible reward disappeared.
    {3890: 2, 3891: 2, 3892: 0},  # Reset/unlocked state is not a receipt.
    {3890: 2, 3891: 2, 3892: 2, 3893: 1},  # Another reward remains claimable.
])
def test_incomplete_rewards_do_not_finish(monkeypatch, states):
    callback, observed, _ = reader(monkeypatch, facts(states))
    assert callback() is False
    assert observed == {}


@pytest.mark.parametrize('change', ['npc', 'pid', 'start', 'incomplete'])
def test_reject_changed_identity_or_incomplete_runtime(monkeypatch, change):
    after = deepcopy(facts({3890: 2, 3891: 2, 3892: 2}))
    if change == 'npc':
        after['people'][0]['npc_id'] = 4013
    elif change == 'pid':
        after['runtime_debug']['pid'] += 1
    elif change == 'start':
        after['runtime_debug']['process_start_ticks'] += 1
    else:
        after['runtime_complete'] = False
    callback, observed, _ = reader(monkeypatch, after)
    with pytest.raises(RuntimeError):
        callback()
    assert observed == {}
