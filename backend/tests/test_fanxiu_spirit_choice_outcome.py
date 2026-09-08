"""Pure inventory delivery contracts; no Runtime or GUI simulation."""
from copy import deepcopy

import pytest

from backend.core.fanxiu.data_annotation.tasks.storage_bag_choice_box import (
    StorageBagChoiceBoxBlocked,
    StorageBagChoiceReward,
    verify_spirit_artifact_choice_outcome,
)


REWARD = StorageBagChoiceReward(1, 14000506, "珠", 1, is_spirit_artifact=True)


def body(uid, **overrides):
    return dict(item_id=uid, base_id=14000506, quality=6, grade=1,
                realm=0, quantity=1, is_break=False, ware_id=1, part=5, **overrides)


def snapshot(items):
    return dict(complete=True, source="runtime_spiritware_all_instances",
                pid=12, process_start_ticks=34, items=items)


def test_partial_box_open_proves_only_requested_new_bodies():
    old = body("old")
    proof = verify_spirit_artifact_choice_outcome(
        snapshot([old]), snapshot([old, body("new2"), body("new1")]),
        reward=REWARD, opened_count=2,
    )
    assert proof.quantity == 2
    assert proof.added_item_ids == ("new1", "new2")
    assert (proof.pid, proof.process_start_ticks) == (12, 34)


@pytest.mark.parametrize("fault", [
    "missing", "wrong_base", "nonred", "notraw", "realm", "broken",
    "duplicate", "old_missing", "old_changed", "process", "incomplete", "unknown_realm",
])
def test_ambiguous_or_wrong_delivery_is_rejected(fault):
    before = snapshot([body("old")])
    after = deepcopy(snapshot([body("old"), body("new")]))
    changes = {"wrong_base": ("base_id", 14000606), "nonred": ("quality", 5),
               "notraw": ("grade", 2), "realm": ("realm", 1),
               "broken": ("is_break", True), "unknown_realm": ("realm", None)}
    if fault in changes:
        field, value = changes[fault]
        after["items"][1][field] = value
    elif fault == "missing":
        after["items"].pop()
    elif fault == "duplicate":
        after["items"].append(body("new"))
    elif fault == "old_missing":
        after["items"].pop(0)
    elif fault == "old_changed":
        after["items"][0]["grade"] = 2
    elif fault == "process":
        after["process_start_ticks"] = 35
    elif fault == "incomplete":
        after["complete"] = False
    with pytest.raises(StorageBagChoiceBoxBlocked):
        verify_spirit_artifact_choice_outcome(before, after, reward=REWARD, opened_count=1)
