"""每日均衡备注、版本配置和整单额度预检的纯数据契约。"""
from copy import deepcopy

import pytest

from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_plan import StorageBagAutoClaimEntry
from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_policy import parse_storage_bag_choice_note
from backend.core.fanxiu.data_annotation.tasks.storage_bag_choice_box import choose_reward_from_note,StorageBagChoiceBoxBlocked
from backend.core.fanxiu.data_annotation.tasks.storage_bag_balanced_choice_execution import (
    storage_bag_balanced_profiles,balanced_choice_catalog_fingerprint,
)


def facts(quantity=4):
    box=dict(id=10,name="材料自选箱",type=21,sub_type=21,optional_gift_rewards=[
        dict(id=1,name="甲材料",count=2),dict(id=2,name="乙材料",count=3)])
    cards={"10":box,"1":dict(id=1,name="甲材料",type=1),"2":dict(id=2,name="乙材料",type=1)}
    entry=StorageBagAutoClaimEntry(10,"a","材料自选箱",quantity,0,0,"action","已分类",
                                  template="choice_box",note="按库存均衡分配")
    profile=dict(catalog_fingerprint=balanced_choice_catalog_fingerprint(box,cards),
                 non_prayer_verified=True,balance_unit="材料单位",rewards=[
                 dict(reward_id=1,units_per_item=1,available_boxes=None),
                 dict(reward_id=2,units_per_item=1,available_boxes=None)])
    payload=dict(storage_bag_balanced_choice_enabled=True,storage_bag_balanced_choices={"10":profile})
    return payload,[entry],cards


def test_exact_note_and_complete_daily_profile_map_to_catalog_yields():
    payload,entries,cards=facts()
    assert parse_storage_bag_choice_note(entries[0].note)==("balanced_inventory","")
    result=storage_bag_balanced_profiles(payload,entries,cards)
    assert [c.count_per_box for c in result[10]]==[2,3]


def test_no_selected_balanced_note_requires_no_new_configuration():
    assert storage_bag_balanced_profiles({},[],{})=={}


def test_default_gate_missing_qualification_and_old_version_fail_closed():
    payload,entries,cards=facts()
    for change in ({"storage_bag_balanced_choice_enabled":False},
                   {"storage_bag_balanced_choice_enabled":1}):
        with pytest.raises(ValueError,match="验收开关"):
            storage_bag_balanced_profiles({**payload,**change},entries,cards)
    for change in ({"non_prayer_verified":False},{"catalog_fingerprint":"old"}):
        altered=deepcopy(payload);altered["storage_bag_balanced_choices"]["10"].update(change)
        with pytest.raises(ValueError,match="配置缺失/目录版本"):
            storage_bag_balanced_profiles(altered,entries,cards)


def test_child_card_change_invalidates_qualification_even_when_box_unchanged():
    payload,entries,cards=facts();cards["1"]["description"]="改变过的材料说明"
    with pytest.raises(ValueError,match="目录版本"):
        storage_bag_balanced_profiles(payload,entries,cards)


def test_prayer_resource_cannot_be_authorized_by_daily_profile_flag():
    payload,entries,cards=facts();cards["1"]["description"]="【仙花】"
    payload["storage_bag_balanced_choices"]["10"]["catalog_fingerprint"]=balanced_choice_catalog_fingerprint(cards["10"],cards)
    with pytest.raises(ValueError,match="祈愿候选"):
        storage_bag_balanced_profiles(payload,entries,cards)


def test_total_limit_covers_all_instances_and_missing_limit_is_unknown():
    payload,entries,cards=facts()
    profile=payload["storage_bag_balanced_choices"]["10"]
    for row in profile["rewards"]:row["available_boxes"]=3
    assert storage_bag_balanced_profiles(payload,entries,cards)
    with pytest.raises(ValueError,match="全部选中实例"):
        storage_bag_balanced_profiles(payload,entries+entries,cards)
    del profile["rewards"][0]["available_boxes"]
    with pytest.raises(ValueError,match="明确可选额度"):
        storage_bag_balanced_profiles(payload,entries,cards)


def test_balanced_note_never_falls_back_to_first_available_choice():
    with pytest.raises(StorageBagChoiceBoxBlocked,match="不能降级"):
        choose_reward_from_note("按库存均衡分配",[],{},[])
