"""九转坤元的原生文案解析和奖励数学边界；不模拟游戏动作。"""
from copy import deepcopy

import pytest

from backend.core.fanxiu.storage_bag_prayer_research import (
    soil_box_reward_modifier, validate_fixed_box_reward_attribution, summarize_prayer_box_experiments,
)


def snapshot():
    # 2026-10-05 实机技能结构；进程及时间替换为纯契约测试值。
    return dict(source='active_danling_detail_level_panel', tab=1, observed_at=1000,
        evidence=dict(pid=1,process_start_ticks=2),
        current=dict(id=1,stage=128,level=30,name='坤元土灵'),
        current_skill=dict(name='九转坤元',pet_id=1,stage=128,level=30,
            config=dict(petId=1,stage=30,effName=190145,description=190175),
            description='每次使用<color=#193970>【土灵灵草宝匣】</color>，<color=#2a4b10>15%</color>概率获得<color=#9e1e09>双倍收益</color>'))


def request():
    return dict(base_id=39032101,opened_count=9,catalog_fingerprint='v1',
        reward_modifier_snapshot=snapshot(),evidence=dict(pid=1,process_start_ticks=2,observed_at_before=1010),
        rewards=[dict(item_id=3,quantity=120),dict(item_id=4,quantity=120)])


def rule():
    return dict(catalog_fingerprint='v1',reward_leaves=[
        dict(item_id=3,count_path_product=12),dict(item_id=4,count_path_product=12)])


def test_real_skill_text_selects_current_probability_and_condition_version():
    proof=snapshot();one=soil_box_reward_modifier(proof)
    assert one['probability']==.15
    changed=deepcopy(proof)
    changed['current_skill']['description']=changed['current_skill']['description'].replace('15%','14.7%')
    assert soil_box_reward_modifier(changed)['fingerprint']!=one['fingerprint']
    same_effect=deepcopy(proof)
    same_effect['current']['stage']=129
    same_effect['current_skill']['stage']=129
    assert soil_box_reward_modifier(same_effect)['fingerprint']==one['fingerprint']


@pytest.mark.parametrize('quantity',[108,120,216])
def test_single_bonus_per_box_includes_nominal_and_bonus_samples(quantity):
    e=request()
    for reward in e['rewards']:reward['quantity']=quantity
    validate_fixed_box_reward_attribution(e,rule())


@pytest.mark.parametrize('change',[
    {'rewards':[dict(item_id=3,quantity=120),dict(item_id=4,quantity=108)]},
    {'rewards':[dict(item_id=3,quantity=228)]},
    {'rewards':[dict(item_id=3,quantity=121)]},
    {'evidence':dict(pid=9,process_start_ticks=2,observed_at_before=1010)},
    {'evidence':dict(pid=1,process_start_ticks=2,observed_at_before=999)},
    {'evidence':dict(pid=1,process_start_ticks=2,observed_at_before=1901)},
])
def test_unexplained_reward_or_stale_foreign_condition_is_rejected(change):
    with pytest.raises(ValueError):validate_fixed_box_reward_attribution({**request(),**change},rule())


def test_nominal_only_samples_do_not_claim_unconditional_expectation():
    e=dict(action_key='x',base_id=39032101,opened_count=1,score_delta=53,prayer_category='炼丹',catalog_fingerprint='v1')
    old=summarize_prayer_box_experiments([e])[0]
    assert old['eligibility']=='waiting_verified_reward_context'
    new={**e,'action_key':'y','reward_modifier_fingerprint':'condition-v1'}
    assert len(summarize_prayer_box_experiments([e,new]))==2


def test_closeout_selects_only_current_verified_reward_condition():
    from backend.core.fanxiu.data_annotation.tasks.prayer_sunday_closeout import plan_prayer_closeout_boxes

    proof=snapshot();fingerprint=soil_box_reward_modifier(proof)['fingerprint']
    base=39032101
    research=dict(rules=[dict(base_id=base, catalog_fingerprint='v1',
        classification_complete=True, operation_kind='fixed',resource_categories=['炼丹'])],
        summaries=[dict(base_id=base,catalog_fingerprint='v1',prayer_category='炼丹',
            eligibility='verified_positive',mean_score_per_box=60,reward_modifier_fingerprint=fingerprint)])
    args=dict(prayer_category='炼丹',research=research,
        inventory=[dict(base_id=base,quantity=100,catalog_fingerprint='v1')],
        round_targets={'炼丹':100})
    progress=dict(complete=True,current_round=1,total_rounds=5,
        current_score=40,round_target_score=100,round_score_gap=60)
    assert plan_prayer_closeout_boxes(progress,**args)['next_open'] is None
    assert plan_prayer_closeout_boxes(progress,**args,
        reward_modifier_snapshots={base:proof})['next_open']['base_id']==base
    changed=deepcopy(proof)
    changed['current_skill']['description']=changed['current_skill']['description'].replace('15%','14%')
    assert plan_prayer_closeout_boxes(progress,**args,
        reward_modifier_snapshots={base:changed})['next_open'] is None
    with pytest.raises(ValueError,match='物品不一致'):
        plan_prayer_closeout_boxes(progress,**args,reward_modifier_snapshots={1:proof})
