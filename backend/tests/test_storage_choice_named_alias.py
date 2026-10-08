"""Saved choice-note aliases authorize exactly one available Catalog reward."""

import pytest

from backend.core.fanxiu.data_annotation.tasks.storage_bag_choice_box import (
    StorageBagChoiceBoxBlocked,
    StorageBagChoiceReward,
    choose_reward_from_note,
)

REWARDS = (
    StorageBagChoiceReward(1, 101, "魔道·四倍功勋符", 10),
    StorageBagChoiceReward(2, 102, "虚天·四倍积分符", 10),
    StorageBagChoiceReward(3, 103, "云梦·四倍积分令", 10),
)


@pytest.mark.parametrize("note", ["选魔道4倍符", "选魔道四倍符", "选魔道·四倍功勋符"])
def test_explicit_alias_selects_same_catalog_reward(note):
    assert choose_reward_from_note(note, REWARDS, {1: True, 2: True, 3: True}, (1, 2, 3)) == REWARDS[0]


@pytest.mark.parametrize("note", ["选魔道2倍符", "选四倍符", "选魔道4倍积分符"])
def test_similar_names_do_not_authorize_choice(note):
    with pytest.raises(StorageBagChoiceBoxBlocked):
        choose_reward_from_note(note, REWARDS, {1: True, 2: True, 3: True}, (1, 2, 3))


def test_alias_preserves_availability_gate():
    with pytest.raises(StorageBagChoiceBoxBlocked):
        choose_reward_from_note("选魔道4倍符", REWARDS, {1: False, 2: True, 3: True}, (1, 2, 3))


def test_alias_preserves_unique_catalog_gate():
    duplicate = StorageBagChoiceReward(4, 104, "魔道四倍功勋符", 10)
    with pytest.raises(StorageBagChoiceBoxBlocked):
        choose_reward_from_note("选魔道4倍符", REWARDS + (duplicate,), dict.fromkeys((1, 2, 3, 4), True), (1, 2, 3, 4))
