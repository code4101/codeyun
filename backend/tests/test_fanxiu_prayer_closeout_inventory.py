"""完整库存到研究候选的纯投影；不模拟GUI或执行动作。"""
from copy import deepcopy

import pytest

from backend.core.fanxiu.storage_bag_prayer_research import classify_prayer_box
from backend.core.fanxiu.data_annotation.tasks.prayer_box_closeout_execution import prayer_closeout_inventory


def facts():
    flower=dict(id=3,name="瑶池玉莲",type=1,description="【仙花】")
    box=dict(id=1,name="固定箱",type=2,optional_gift_rewards=[dict(id=3,count=2)])
    cards={"1":box,"3":flower}
    research=dict(rules=[classify_prayer_box(box,cards)])
    snapshot=dict(complete=True,source="active_backpack_panel_item_info_list",fingerprint="bag1",
        evidence=dict(pid=10,process_start_ticks=20),items=[
        dict(instance_id="a",base_id=1,num=2),dict(instance_id="b",base_id=1,num=3),
        dict(is_padding=True)])
    return snapshot,cards,research


def test_instances_are_aggregated_and_catalog_version_is_current():
    snapshot,cards,research=facts()
    before=deepcopy(snapshot)
    out=prayer_closeout_inventory(snapshot,cards,research)
    assert out==[dict(base_id=1,quantity=5,catalog_fingerprint=research["rules"][0]["catalog_fingerprint"])]
    assert snapshot==before


@pytest.mark.parametrize("change",[
    dict(complete=False),dict(fingerprint=""),dict(source="other"),
    dict(evidence=dict(pid=-1,process_start_ticks=20)),
    dict(evidence=dict(pid=10,process_start_ticks=True)),
])
def test_incomplete_or_unbound_snapshot_cannot_supply_spending_candidates(change):
    snapshot,cards,research=facts();snapshot.update(change)
    with pytest.raises(ValueError):prayer_closeout_inventory(snapshot,cards,research)


def test_duplicate_instance_cannot_double_inventory():
    snapshot,cards,research=facts();snapshot["items"][1]["instance_id"]="a"
    with pytest.raises(ValueError,match="重复"):
        prayer_closeout_inventory(snapshot,cards,research)


def test_changed_child_card_cannot_reuse_old_root_card_yield():
    snapshot,cards,research=facts()
    cards["3"]["description"]="普通材料"
    assert prayer_closeout_inventory(snapshot,cards,research)==[]


def test_index_only_card_drift_keeps_measured_fingerprint_but_contract_change_does_not():
    snapshot,cards,research=facts()
    measured=research["rules"][0]["catalog_fingerprint"]
    cards["1"]["icon_reuse_count"]=11
    assert prayer_closeout_inventory(snapshot,cards,research)[0]["catalog_fingerprint"]==measured
    cards["1"]["effect_value"]="pool_B"
    assert prayer_closeout_inventory(snapshot,cards,research)[0]["catalog_fingerprint"]!=measured
    cards["1"]["optional_gift_rewards"]=[dict(id=3,count=3)]
    assert prayer_closeout_inventory(snapshot,cards,research)==[]
