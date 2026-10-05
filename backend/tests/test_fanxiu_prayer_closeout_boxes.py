"""持久研究到单步补分建议的纯数据契约；不模拟游戏。"""
from copy import deepcopy

import pytest

from backend.core.fanxiu.data_annotation.tasks.prayer_sunday_closeout import plan_prayer_closeout_boxes


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


def test_skip_requires_no_inventory_or_spending():
    result = plan({},[],score=30)
    assert result["outcome"] == "skipped_low_progress"
    assert result["next_open"] is None


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
