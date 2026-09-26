from backend.core.fanxiu.activity.magic_invasion_challenge_planning import (
    magic_invasion_milestones, plan_magic_invasion_reward_batch,
)

TIERS = [{"target_total_tokens": n, "target_remaining_tokens": n, "goods_id": i}
         for i,n in enumerate([92000,102000,112000,152000])]


def plan(balance, samples, capacity=1000, cumulative=None):
    return plan_magic_invasion_reward_batch(milestones=TIERS, current_currency=balance,
        cumulative_currency=balance if cumulative is None else cumulative,
        samples=[{"completed_exorcisms": n, "magic_crystal_delta": delta} for n,delta in samples], capacity=capacity)


def test_first_100_then_latest_batch_next_tier_without_halving():
    assert plan(0,[])["count"] == 100
    p = plan(100246,[(100,46589),(100,46377)])
    assert (p["target"]["target_total_tokens"],p["count"]) == (102000,4)


def test_shortfall_preserves_target_and_spends_nothing():
    p=plan(100246,[(100,46377)],capacity=3)
    assert (p["status"],p["needed"],p["count"],p["deficit"]) == ("pass",4,0,1)
    assert plan(0,[],capacity=99)["deficit"] == 1


def test_replan_same_tier_or_skip_funded_tiers():
    p=plan(90000,[(10,1000)])
    assert p["count"] == 20 and p["target"]["target_total_tokens"] == 92000
    p=plan(112000,[(10,22000)])
    assert p["target"]["target_total_tokens"] == 152000
    assert plan(152000,[(10,0)])["status"] == "completed"
    assert plan(112000,[(10,0)])["reason"] == "no_positive_yield"


def test_cumulative_gate_and_purchased_reserved_rows():
    assert plan(200000,[(100,50000)],cumulative=100000)["count"] == 4
    items=[dict(goods_id=1,name="locked",priority_order=2,source_order=0,locked=True,
                token_cost=400,purchase_limit=230,purchased_count=10,cumulative_tokens=92000),
           dict(goods_id=2,priority_order=13,source_order=1,purchase_limit=-1)]
    rows=magic_invasion_milestones(items)
    assert len(rows)==1 and rows[0]["target_remaining_tokens"]==88000
