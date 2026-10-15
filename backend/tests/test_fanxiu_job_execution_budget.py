"""Job budget vs evidence-renewed liveness lease; pure clock arithmetic, no game."""
import pytest

from backend.core.fanxiu.data_annotation.behavior_tree_container import JobExecutionBudget


def test_base_budget_stops_without_lease():
    budget = JobExecutionBudget(1800, started_at=0.0)
    assert budget.exceeded(1800) is None
    assert '无活性租约' in budget.exceeded(1801)


def test_live_lease_keeps_a_healthy_long_job_and_lapses_without_evidence():
    budget = JobExecutionBudget(1800, started_at=0.0)
    budget.renew(1790.0, 150, '玄荒战斗画面 #419')
    assert budget.exceeded(1900) is None          # healthy battle past 1800 s
    budget.renew(1930.0, 150, '玄荒战斗画面 #419')
    assert budget.exceeded(2070) is None
    assert '已过期' in budget.exceeded(2081)       # no new evidence -> stops


def test_absolute_ceiling_and_lease_cap_still_apply():
    budget = JobExecutionBudget(1800, started_at=0.0, max_lease_seconds=600)
    budget.renew(7100.0, 10_000, 'battle')
    assert budget.lease_until == 7700.0            # capped to max_lease_seconds
    assert budget.exceeded(7199) is None
    assert '绝对上限' in budget.exceeded(7201)       # 4 x 1800 ceiling


def test_dispatch_hold_only_for_jobs_with_liveness_evidence():
    plain = JobExecutionBudget(1800, started_at=0.0)
    assert plain.exceeded(1801) and not plain.requires_dispatch_hold()
    leased = JobExecutionBudget(1800, started_at=0.0)
    leased.renew(10.0, 150, 'battle')
    assert leased.requires_dispatch_hold()


def test_fresh_budget_never_inherits_a_previous_lease():
    first = JobExecutionBudget(1800, started_at=0.0)
    first.renew(1790.0, 150, 'battle')
    second = JobExecutionBudget(1800, started_at=0.0)
    assert second.lease_until is None and '无活性租约' in second.exceeded(1801)


def test_unbounded_job_and_invalid_renewals():
    assert JobExecutionBudget(None, started_at=0.0).exceeded(10**9) is None
    budget = JobExecutionBudget(60, started_at=0.0)
    for seconds, reason in ((0, 'x'), (-1, 'x'), (10, '')):
        with pytest.raises(ValueError):
            budget.renew(1.0, seconds, reason)
