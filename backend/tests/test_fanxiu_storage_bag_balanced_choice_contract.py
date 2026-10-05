"""均衡配置与真实目录结构的映射契约；不运行游戏组件。"""
import pytest

from backend.core.fanxiu.data_annotation.tasks.storage_bag_choice_box import StorageBagChoiceReward
from backend.core.fanxiu.data_annotation.tasks.storage_bag_balanced_choice import BalancedBoxChoice
from backend.core.fanxiu.data_annotation.tasks.storage_bag_balanced_choice_execution import validate_balanced_box_choices


def choices():
    return [BalancedBoxChoice(1,2,"材料",1),BalancedBoxChoice(2,3,"材料",1)]


def rewards():
    return [StorageBagChoiceReward(1,1,"甲",2),StorageBagChoiceReward(2,2,"乙",3)]


def test_full_matching_reward_map_accepted():
    validate_balanced_box_choices(rewards(),choices())


def test_missing_reward_or_duplicate_mapping_rejected_before_any_action():
    with pytest.raises(ValueError,match="全部真实"):
        validate_balanced_box_choices(rewards(),choices()[:1])
    with pytest.raises(ValueError,match="全部真实"):
        validate_balanced_box_choices(rewards(),choices()+choices()[:1])


def test_normalized_duplicate_names_cannot_become_ambiguous_batch_requests():
    rs=[StorageBagChoiceReward(1,1,"甲·乙",2),StorageBagChoiceReward(2,2,"甲乙",3)]
    with pytest.raises(ValueError,match="名称重复"):
        validate_balanced_box_choices(rs,choices())


def test_actual_yield_and_independent_business_destinations_are_checked():
    with pytest.raises(ValueError,match="数量"):
        validate_balanced_box_choices(rewards(),[BalancedBoxChoice(1,99,"材料",1),choices()[1]])
    rs=[StorageBagChoiceReward(1,1,"仙侣",2,is_partner=True),rewards()[1]]
    with pytest.raises(ValueError,match="仙侣"):
        validate_balanced_box_choices(rs,choices())
