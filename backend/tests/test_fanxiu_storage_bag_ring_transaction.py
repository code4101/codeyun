"""Independent-inventory attribution contracts, without a simulated UI."""
from copy import deepcopy
import pytest

from backend.core.fanxiu.data_annotation.tasks.storage_bag_ring import (
    RING_INVENTORY_SOURCE, verify_ring_reward_delta,
)


def observations():
    before = {"complete": True, "source": RING_INVENTORY_SOURCE, "fingerprint": "before",
              "captured_at": "2026-10-08T02:00:00+08:00",
              "evidence": {"pid": 1, "process_start_ticks": 2},
              "items": [{"instance_id": "ring", "base_id": 13010001, "num": 11},
                        {"instance_id": "other", "base_id": 13060000, "num": 5}]}
    after = deepcopy(before)
    after.update(fingerprint="after", captured_at="2026-10-08T02:00:02+08:00")
    after["items"][0]["num"] += 1
    return before, after


def verify(before, after):
    return verify_ring_reward_delta(before, after, targets={13010001: "二阶离火灵环"},
                                    opened_count=1, process_identity=(1, 2))


def test_one_consumed_box_requires_one_exact_independent_ring():
    rewards = verify(*observations())
    assert rewards == [{"item_id": 13010001, "reward_key": "item:13010001",
                        "name": "二阶离火灵环", "quantity": 1}]


@pytest.mark.parametrize("fault", ["empty", "excess", "other", "process", "source"])
def test_unattributed_or_wrong_source_changes_fail_closed(fault):
    before, after = observations()
    if fault == "empty": after["items"][0]["num"] -= 1
    elif fault == "excess": after["items"][0]["num"] += 1
    elif fault == "other": after["items"][1]["num"] += 1
    elif fault == "process": after["evidence"]["pid"] = 3
    else: after["source"] = "normal_backpack"
    with pytest.raises(ValueError): verify(before, after)
