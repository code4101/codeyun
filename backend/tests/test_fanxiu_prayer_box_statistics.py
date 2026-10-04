"""历史收益合计契约，不模拟游戏。"""
import pytest
from backend.core.fanxiu.storage_bag_prayer_research import summarize_prayer_box_experiments

def sample(key,n,score,category="仙花"):
    return dict(action_key=key,base_id=1,opened_count=n,score_delta=score,prayer_category=category,catalog_fingerprint="F1")

def test_weighted_history_mean_not_average_of_batches():
    row=summarize_prayer_box_experiments([sample("a",10,1778),sample("b",2,600)])[0]
    assert row["mean_score_per_box"] == pytest.approx(2378/12)
    assert row["opened_count"] == 12
    assert row["individual_score_stddev"] is None

def test_zero_sample_remains_unproven_and_categories_separate():
    rows=summarize_prayer_box_experiments([sample("a",3,0),sample("b",2,20,"洗灵")])
    assert len(rows)==2
    assert rows[0]["eligibility"]=="zero_observed_unproven_pool"

def test_duplicate_receipt_rejected():
    with pytest.raises(ValueError):
        summarize_prayer_box_experiments([sample("a",10,1778),sample("a",10,1778)])


def test_all_tied_uses_preserved_and_alchemy_excluded():
    from backend.core.fanxiu.storage_bag_prayer_research import choose_prayer_box_allocation
    r=choose_prayer_box_allocation({"仙花":600,"洗灵":300,"炼丹":999999},{"仙花":160000,"洗灵":80000})
    assert r["usable_categories"]==["仙花","洗灵"]
    assert r["recommended_category"] is None


def test_exclusive_before_flexible_and_unknown_excluded():
    from backend.core.fanxiu.storage_bag_prayer_research import order_prayer_top_up_resources
    items=[dict(base_id=1,eligibility_verified=True,usable_categories=["仙花","洗灵"]),
           dict(base_id=2,eligibility_verified=True,usable_categories=["仙花"]),
           dict(base_id=3,eligibility_verified=False,usable_categories=["仙花"])]
    assert [r["base_id"] for r in order_prayer_top_up_resources(items,current_category="仙花")]==[2,1]


def test_missing_prayer_measurement_prevents_recommendation():
    from backend.core.fanxiu.storage_bag_prayer_research import plan_choice_prayer_allocations
    research=dict(choice_plans=[dict(base_id=1,catalog_fingerprint="F1",options=[
        dict(choice_key="a",prayer_category="仙花"),dict(choice_key="b",prayer_category="洗灵")])],
        summaries=[dict(base_id=1,catalog_fingerprint="F1",choice_key="a",prayer_category="仙花",mean_score_per_box=600)])
    r=plan_choice_prayer_allocations(research,{"仙花":160000})[0]
    assert r["status"]=="waiting_corresponding_prayer"
    assert r["usable_categories"]==[]


def test_outer_box_separated_from_inner_prayer_potential():
    from backend.core.fanxiu.storage_bag_prayer_research import classify_prayer_box
    flower=dict(id=3,name="瑶池玉莲",type=1,description="【仙花】")
    choice=dict(id=2,name="材料自选匣",type=21,optional_gift_rewards=[dict(id=3,count=6)])
    outer=dict(id=1,name="外箱",type=2,effect_description="随机",optional_gift_rewards=[dict(id=2,count=1)])
    rule=classify_prayer_box(outer,{"1":outer,"2":choice,"3":flower})
    assert rule["is_outer_container"] is True
    assert rule["allocation_categories"]==["仙花"]
    assert rule["exclude_from_prayer_top_up"] is True
    assert classify_prayer_box(choice,{"2":choice,"3":flower})["is_outer_container"] is False
