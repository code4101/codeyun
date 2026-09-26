"""Pure selection contracts; GUI behavior is verified in the real game."""

import pytest

from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_policy import parse_storage_bag_choice_note
from backend.core.fanxiu.data_annotation.tasks.storage_bag_choice_box import (
    StorageBagChoiceBoxBlocked, StorageBagChoiceReward, choose_reward_from_note,
    choice_wallet_types,
)

NOTE = "选库存最少的，全开"
REWARDS = tuple(StorageBagChoiceReward(i, 100 + i, str(i), i) for i in range(1, 5))


def choose(counts, rewards=REWARDS, visible=(1, 2, 3, 4)):
    return choose_reward_from_note(NOTE, rewards, {i: True for i in visible}, visible,
                                   inventory_counts=counts)


def test_policy_selects_raw_stock_once_not_yield_or_balancing():
    assert parse_storage_bag_choice_note(NOTE) == ("lowest_inventory_all", "")
    assert choose({101: 5, 102: 10, 103: 20, 104: 30}).base_id == 101
    assert choose({101: 8, 102: 7, 103: 0, 104: 9}).base_id == 103


def test_tie_uses_panel_slot_even_when_input_order_differs():
    assert choose({101: 0, 102: 0, 103: 0, 104: 0}, REWARDS[::-1]).slot == 1


@pytest.mark.parametrize("counts", [None, {101: 0}, {101: -1, 102: 2, 103: 3, 104: 4}])
def test_missing_or_invalid_stock_is_not_zero(counts):
    with pytest.raises(StorageBagChoiceBoxBlocked):
        choose(counts)


def test_hidden_candidates_cannot_be_ignored():
    with pytest.raises(StorageBagChoiceBoxBlocked):
        choose({101: 1, 102: 2, 103: 3, 104: 0}, visible=(1, 2, 3))


def test_catalog_routes_wallet_materials_instead_of_treating_missing_bag_as_zero():
    cards = {str(r.base_id): {"effect_value": f"WALLET|{r.base_id}"} for r in REWARDS}
    assert choice_wallet_types(REWARDS, cards) == {r.base_id: r.base_id for r in REWARDS}
    with pytest.raises(StorageBagChoiceBoxBlocked):
        choice_wallet_types(REWARDS, {})
