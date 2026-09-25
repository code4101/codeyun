from copy import deepcopy

import pytest

from backend.core.fanxiu.data_annotation.tasks.xianyuan_duel import (
    xianyuan_duel_dynamic_signature,
    xianyuan_duel_runtime_facts_advanced,
)


def _round_facts():
    return {
        "self_power": 100,
        "remaining_challenges": 3,
        "remaining_refreshes": 2,
        "rank": 10,
        "targets": [{"target_id": 42, "name": "对手", "score": 200, "team_power": 90}],
    }


def test_self_power_refresh_does_not_complete_round_wait():
    before = _round_facts()
    after = {**before, "self_power": 200, "updated_at": 999}
    assert xianyuan_duel_dynamic_signature(before) == xianyuan_duel_dynamic_signature(after)
    assert not xianyuan_duel_runtime_facts_advanced(before, after)


@pytest.mark.parametrize("field", ["remaining_challenges", "remaining_refreshes", "rank"])
def test_round_counters_and_rank_advance_wait(field):
    before = _round_facts()
    after = {**before, field: before[field] - 1}
    assert xianyuan_duel_runtime_facts_advanced(before, after)


@pytest.mark.parametrize("field,value", [("target_id", 43), ("name", "新对手"), ("score", 201), ("team_power", 95)])
def test_target_changes_advance_wait(field, value):
    before = _round_facts()
    after = deepcopy(before)
    after["targets"][0][field] = value
    assert xianyuan_duel_runtime_facts_advanced(before, after)
