"""Exact transaction contracts, without simulating game UI."""
from copy import deepcopy

import pytest

from backend.core.fanxiu.data_annotation.tasks.storage_bag_direct_use import (
    StorageBagDirectUseBlocked, StorageBagDirectUseRequest,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_vip import (
    VIP_PROGRESS_SOURCE, verify_vip_direct_use_delta,
)


def transaction():
    identity = {"pid": 1, "process_start_ticks": 2}
    before = {"complete": True, "source": "active_backpack_panel_item_info_list",
              "fingerprint": "before", "evidence": identity,
              "items": [{"instance_id": "vip", "base_id": 1010, "num": 8450}]}
    after = dict(before, fingerprint="after", items=[])
    progress = {"complete": True, "source": VIP_PROGRESS_SOURCE,
                "captured_at": "2026-10-08T02:00:00+08:00", "vip_level": 9,
                "current_vip_exp": 595000, "completed_level_exp": 400000,
                "cumulative_vip_exp": 995000, "evidence": dict(identity, role_id=3)}
    upgraded = dict(progress, captured_at="2026-10-08T02:00:02+08:00",
                    vip_level=10, current_vip_exp=3450, completed_level_exp=1000000,
                    cumulative_vip_exp=1003450, evidence=dict(progress["evidence"]))
    return before, after, progress, upgraded


def verify(values):
    before, after, progress, upgraded = values
    return verify_vip_direct_use_delta(before, after,
        request=StorageBagDirectUseRequest(1010, "vip", "VIP经验", 8450),
        wallet_before=progress, wallet_after=upgraded)


def test_cumulative_delta_survives_level_rollover_and_retains_account_proof():
    receipt = verify(transaction()).receipt()
    assert receipt["opened_count"] == receipt["rewards"][0]["quantity"] == 8450
    assert receipt["evidence"]["role_id"] == 3
    assert receipt["evidence"]["progress_after"]["vip_level"] == 10
    assert receipt["action_key"].startswith("storage-bag-vip:")


@pytest.mark.parametrize("fault", ["remainder", "account", "quantity", "other_item", "process"])
def test_non_exact_or_cross_account_transactions_are_rejected(fault):
    values = deepcopy(transaction())
    if fault == "remainder":
        values[3]["cumulative_vip_exp"] = 3450
    elif fault == "account":
        values[3]["evidence"]["role_id"] = 4
    elif fault == "quantity":
        values[1]["items"] = [{"instance_id": "vip", "base_id": 1010, "num": 1}]
    elif fault == "other_item":
        values[1]["items"] = [{"instance_id": "other", "base_id": 1001, "num": 1}]
    else:
        values[3]["evidence"]["pid"] = 4
    with pytest.raises(StorageBagDirectUseBlocked):
        verify(values)
