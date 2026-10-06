"""持久研究到单步补分建议的纯数据契约；不模拟游戏。"""
from copy import deepcopy

import pytest

from backend.core.fanxiu.data_annotation.tasks.prayer_sunday_closeout import plan_prayer_closeout_boxes
from backend.core.fanxiu.data_annotation.tasks.prayer_sunday_closeout import select_prayer_closeout_choice,plan_prayer_closeout_transition


def progress(score=80):
    return dict(complete=True, current_round=1, total_rounds=5,
                current_score=score, round_target_score=100, round_score_gap=100-score)


def rule(base_id, kind="fixed", categories=None):
    return dict(base_id=base_id, catalog_fingerprint="v1", operation_kind=kind,
                classification_complete=True, allocation_categories=categories or ["仙花"])


def summary(base_id, category="仙花", score=10, choice=""):
    return dict(base_id=base_id, catalog_fingerprint="v1", prayer_category=category,
                choice_key=choice, mean_score_per_box=score, eligibility="verified_positive")


def stock(*ids):
    return [dict(base_id=i, quantity=5, catalog_fingerprint="v1") for i in ids]


def plan(research, inventory, score=80):
    return plan_prayer_closeout_boxes(progress(score), prayer_category="仙花",
        research=research, inventory=inventory, round_targets={"仙花":100, "洗灵":50})


def choice_plan():
    return dict(base_id=2, catalog_fingerprint="v1", options=[
        dict(choice_key="flower", prayer_category="仙花"),
        dict(choice_key="wash", prayer_category="洗灵")])


def test_exclusive_precedes_tied_choice_and_only_one_box_is_proposed():
    research = dict(rules=[rule(2,"choice",["仙花","洗灵"]),rule(1)],
        choice_plans=[choice_plan()], summaries=[summary(2,score=10,choice="flower"),
        summary(2,"洗灵",5,"wash"),summary(1,score=3)])
    before = deepcopy(research)
    result = plan(research,stock(1,2))
    assert [r["base_id"] for r in result["resources"]] == [1,2]
    assert result["resources"][1]["usable_categories"] == ["仙花","洗灵"]
    assert result["next_open"]["open_quantity"] == 1
    assert result["next_open"]["base_id"] == 1
    assert result["stop_after_current_round"] is True
    assert research == before


def test_explicit_resource_chooses_only_within_the_same_usage_priority():
    research=dict(rules=[rule(1),rule(3),rule(2,'choice',['仙花','洗灵'])],
        choice_plans=[choice_plan()],summaries=[summary(1),summary(3),
        summary(2,score=10,choice='flower'),summary(2,'洗灵',5,'wash')])
    args=dict(prayer_category='仙花',research=research,inventory=stock(1,2,3),
        round_targets={'仙花':100,'洗灵':50})
    result=plan_prayer_closeout_boxes(progress(),**args,explicit_base_id=3)
    assert result['next_open']['base_id']==3
    assert [r['base_id'] for r in result['resources']]==[1,3,2]
    with pytest.raises(ValueError,match='多用途'):
        plan_prayer_closeout_boxes(progress(),**args,explicit_base_id=2)
    with pytest.raises(ValueError,match='可用正收益'):
        plan_prayer_closeout_boxes(progress(),**args,explicit_base_id=9)


def test_unmeasured_choice_not_available_for_closeout():
    research = dict(rules=[rule(2,"choice")],choice_plans=[choice_plan()],
                    summaries=[summary(2,choice="flower")])
    assert plan(research,stock(2))["next_open"] is None


def test_old_catalog_outer_and_unverified_yields_are_excluded():
    rules = [rule(1), {**rule(2),"is_outer_container":True},rule(3),rule(4,"random",["仙花","洗灵"])]
    research = dict(rules=rules,summaries=[summary(1),summary(2),
        {**summary(3),"eligibility":"zero_observed_unproven_pool"},summary(4)])
    inventory = stock(1,2,3,4)
    inventory[0]["catalog_fingerprint"] = "old"
    result = plan(research,inventory)
    assert result["outcome"] == "waiting_verified_resources"
    assert result["resources"] == []


def test_random_mean_never_turns_into_a_guaranteed_batch():
    result = plan(dict(rules=[rule(1,"random")],summaries=[summary(1,score=0.1)]),stock(1))
    assert result["next_open"]["open_quantity"] == 1
    assert result["result"] == "pending"


def test_exclusive_alchemy_remains_available_without_competing_with_composite_choices():
    dedicated = {**rule(1,"random"),"resource_categories":["炼丹"],"allocation_categories":[]}
    mixed = {**rule(2,"random"),"resource_categories":["炼丹","仙花"],"allocation_categories":["仙花"]}
    research = dict(rules=[dedicated,mixed],summaries=[summary(1,"炼丹"),summary(2,"炼丹")])
    result = plan_prayer_closeout_boxes(progress(),prayer_category="炼丹",research=research,
        inventory=stock(1,2),round_targets={"炼丹":100})
    assert [r["base_id"] for r in result["resources"]] == [1]
    assert result["next_open"]["usable_categories"] == ["炼丹"]


def test_closeout_resolves_current_round_and_excludes_unknown_comparison_targets():
    research = dict(rules=[rule(1)], summaries=[summary(1)])
    result = plan_prayer_closeout_boxes(progress(), prayer_category="仙花", research=research,
        inventory=stock(1), round_targets={"仙花": {"1": 100}, "洗灵": {"2": 50}})
    assert result['next_open']['base_id'] == 1
    with pytest.raises(ValueError, match="第1轮最高档规则尚未核实"):
        plan_prayer_closeout_boxes(progress(), prayer_category="仙花", research=research,
            inventory=stock(1), round_targets={"仙花": {"2": 100}})


def test_skip_requires_no_inventory_or_spending():
    result = plan({},[],score=30)
    assert result["outcome"] == "skipped_low_progress"
    assert result["next_open"] is None


def test_other_prayers_also_choose_finer_boxes_for_the_tail_gap():
    research = dict(rules=[rule(1), rule(2)], summaries=[summary(1, score=64), summary(2, score=2)])
    assert plan(research, stock(1, 2), score=99)['next_open']['base_id'] == 2


@pytest.mark.parametrize('gap,base', [(200,3),(90,2),(30,1),(1,1)])
def test_alchemy_uses_finer_boxes_for_tail_gap(gap,base):
    research=dict(rules=[rule(i,categories=['炼丹']) for i in (1,2,3)],
        summaries=[summary(1,'炼丹',2),summary(2,'炼丹',64),summary(3,'炼丹',116)],choice_plans=[])
    p=dict(complete=True,current_round=5,total_rounds=5,current_score=1000-gap,
           round_target_score=1000,round_score_gap=gap)
    result=plan_prayer_closeout_boxes(p,prayer_category='炼丹',research=research,
        inventory=stock(1,2,3),round_targets={'炼丹':1000})
    assert result['next_open']['base_id']==base
    assert result['stop_after_current_round'] is False


def test_duplicate_inventory_and_wrong_round_target_rejected():
    with pytest.raises(ValueError,match="唯一"):
        plan({},stock(1,1))
    with pytest.raises(ValueError,match="目标"):
        plan_prayer_closeout_boxes(progress(),prayer_category="仙花",research={},inventory=[],
                                  round_targets={"仙花":160000})


def test_same_category_options_use_best_yield_and_preserve_tied_options():
    cp = choice_plan()
    cp["options"] = [dict(choice_key=c,prayer_category="仙花") for c in ("a","b","c")]
    research = dict(rules=[rule(2,"choice")],choice_plans=[cp],summaries=[
        summary(2,score=20,choice="a"),summary(2,score=20,choice="b"),summary(2,score=1,choice="c")])
    result = plan(research,stock(2))
    assert result["next_open"]["expected_score_per_box"] == 20
    assert result["next_open"]["choice_keys"] == ["a","b"]


def test_same_category_best_keeps_cross_category_tie_despite_last_low_option():
    cp = choice_plan()
    cp["options"] = [dict(choice_key="a",prayer_category="仙花"),
                     dict(choice_key="b",prayer_category="仙花"),
                     dict(choice_key="wash",prayer_category="洗灵")]
    research = dict(rules=[rule(2,"choice",["仙花","洗灵"])],choice_plans=[cp],
        summaries=[summary(2,score=20,choice="a"),summary(2,score=5,choice="b"),
                   summary(2,"洗灵",10,"wash")])
    result = plan(research,stock(2))
    assert result["next_open"]["usable_categories"] == ["仙花","洗灵"]
    assert result["next_open"]["choice_keys"] == ["a"]


def test_declared_tie_policy_selects_one_action_without_overwriting_all_uses():
    action={"choice_keys":["b","a"],"usable_categories":["仙花","洗灵"]}
    assert select_prayer_closeout_choice(action)=="a"
    assert action["choice_keys"]==["b","a"]
    assert action["usable_categories"]==["仙花","洗灵"]
    assert select_prayer_closeout_choice(action,explicit_choice="b")=="b"
    with pytest.raises(ValueError,match="明确指定"):
        select_prayer_closeout_choice(action,policy="require_explicit")
    with pytest.raises(ValueError,match="最优集合"):
        select_prayer_closeout_choice(action,explicit_choice="other")


@pytest.mark.parametrize("after,outcome",[
    ({"activity_key":"weekB"},"activity_changed"),({"current_round":2},"round_changed")])
def test_activity_or_round_transition_stops_without_claiming_completion(after,outcome):
    before={"complete":True,"activity_key":"weekA","current_round":1}
    result=plan_prayer_closeout_transition(before,{**before,**after})
    assert result["result"]=="pending"
    assert (result["action"],result["outcome"])==("stop",outcome)
    assert plan_prayer_closeout_transition(before,before) is None
