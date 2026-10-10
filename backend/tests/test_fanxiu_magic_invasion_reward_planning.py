from datetime import datetime

from backend.core.fanxiu.activity.magic_invasion_challenge_planning import (
    magic_invasion_milestones, plan_magic_invasion_reward_batch,
    magic_invasion_seconds_per_exorcism,
    plan_magic_invasion_timed_reward_batch,
)

TIERS = [{"target_total_tokens": n, "target_remaining_tokens": n, "goods_id": i}
         for i,n in enumerate([92000,102000,112000,152000])]


def test_recovered_yield_does_not_replace_continuous_duration():
    continuous = {"completed_exorcisms": 100, "duration_seconds": 300,
                  "magic_crystal_delta": 46000, "duration_reliable": True}
    recovered = {"completed_exorcisms": 100, "duration_seconds": 3600,
                 "magic_crystal_delta": 47000, "duration_reliable": False}
    assert magic_invasion_seconds_per_exorcism([continuous, recovered]) == 3
    assert recovered["magic_crystal_delta"] == 47000
    assert magic_invasion_seconds_per_exorcism([recovered]) is None


def test_duration_selection_rejects_invalid_and_preserves_legacy_contract():
    legacy = {"completed_exorcisms": 10, "duration_seconds": 40}
    invalid = [{"completed_exorcisms": 0, "duration_seconds": 10},
               {"completed_exorcisms": True, "duration_seconds": 10},
               {"completed_exorcisms": 10, "duration_seconds": float('inf')},
               {"completed_exorcisms": 10, "duration_seconds": float('nan')}]
    assert magic_invasion_seconds_per_exorcism([legacy, *invalid]) == 4
    assert magic_invasion_seconds_per_exorcism(invalid) is None


def test_timed_capacity_retains_recovered_yield_and_funded_completion():
    now = datetime.fromisoformat('2026-10-10T19:05:00+08:00')
    end = datetime.fromisoformat('2026-10-10T22:00:00+08:00')
    recovered = dict(completed_exorcisms=100, magic_crystal_delta=46000,
                     duration_seconds=3600, duration_reliable=False)
    def timed(balance, rows):
        return plan_magic_invasion_timed_reward_batch(
            milestones=TIERS, current_currency=balance, cumulative_currency=balance,
            samples=rows, resource_capacity=1000, now=now, activity_end_at=end)
    pending = timed(100000, [recovered])
    assert (pending['status'], pending['count'], pending['reason']) == (
        'pending', 0, 'reliable_duration_missing')
    assert timed(152000, [recovered])['status'] == 'completed'
    continuous = {**recovered, 'duration_seconds': 300, 'duration_reliable': True}
    plan = timed(100000, [continuous, recovered])
    assert plan['time_capacity'] == 2270
    assert plan['capacity'] == 1000
    assert plan['count'] == 100


def test_missing_duration_cannot_reopen_expired_fast_mode_or_activity():
    end = datetime.fromisoformat('2026-10-10T22:00:00+08:00')
    recovered = dict(completed_exorcisms=100, magic_crystal_delta=46000,
                     duration_seconds=3600, duration_reliable=False)
    for time, reason in [('20:58:30', 'fast_mode_window_closed'),
                         ('21:30:00', 'fast_mode_window_closed'),
                         ('22:00:00', 'activity_ended')]:
        result = plan_magic_invasion_timed_reward_batch(
            milestones=TIERS, current_currency=100000, cumulative_currency=100000,
            samples=[recovered], resource_capacity=1000,
            now=datetime.fromisoformat(f'2026-10-10T{time}+08:00'), activity_end_at=end)
        assert (result['status'], result['reason'], result['count']) == ('pass', reason, 0)
    funded = plan_magic_invasion_timed_reward_batch(
        milestones=TIERS, current_currency=152000, cumulative_currency=152000,
        samples=[recovered], resource_capacity=1000,
        now=datetime.fromisoformat('2026-10-10T21:30:00+08:00'), activity_end_at=end)
    assert funded['status'] == 'completed'


def plan(balance, samples, capacity=1000, cumulative=None):
    return plan_magic_invasion_reward_batch(milestones=TIERS, current_currency=balance,
        cumulative_currency=balance if cumulative is None else cumulative,
        now=datetime.fromisoformat("2026-09-26T19:05:00+08:00"),
        activity_end_at=datetime.fromisoformat("2026-09-26T22:00:00+08:00"),
        samples=[{"completed_exorcisms": n, "magic_crystal_delta": delta} for n,delta in samples], capacity=capacity)


def test_first_100_then_latest_batch_next_tier_without_halving():
    assert plan(0,[])["count"] == 100
    p = plan(100246,[(100,46589),(100,46377)])
    assert (p["target"]["target_total_tokens"],p["count"]) == (102000,100)


def test_shortfall_preserves_target_and_spends_nothing():
    p=plan(100246,[(100,46377)],capacity=3)
    assert (p["status"],p["needed"],p["count"],p["deficit"]) == ("pass",100,0,97)
    assert plan(0,[],capacity=99)["deficit"] == 1


def test_replan_same_tier_or_skip_funded_tiers():
    p=plan(90000,[(10,1000)])
    assert p["count"] == 100 and p["target"]["target_total_tokens"] == 92000
    p=plan(112000,[(10,22000)])
    assert p["target"]["target_total_tokens"] == 152000
    assert plan(152000,[(10,0)])["status"] == "completed"
    assert plan(112000,[(10,0)])["reason"] == "no_positive_yield"


def test_cumulative_gate_and_purchased_reserved_rows():
    assert plan(200000,[(100,50000)],cumulative=100000)["count"] == 100
    items=[dict(goods_id=1,name="locked",priority_order=2,source_order=0,locked=True,
                token_cost=400,purchase_limit=230,purchased_count=10,cumulative_tokens=92000),
           dict(goods_id=2,priority_order=13,source_order=1,purchase_limit=-1)]
    rows=magic_invasion_milestones(items)
    assert len(rows)==1 and rows[0]["target_remaining_tokens"]==88000
