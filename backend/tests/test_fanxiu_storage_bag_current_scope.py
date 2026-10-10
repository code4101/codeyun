"""Pure post-stock planning boundaries; no simulated Runtime or GUI."""
from datetime import datetime
import pytest
from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_execution import (
    remaining_storage_bag_current_items,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_plan import (
    StorageBagAutoClaimEntry, StorageBagAutoClaimPlan,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_operation import (
    next_storage_bag_operation_at, storage_bag_operation_follow_up,
)


def entry(base_id, quantity, template, disposition="action"):
    return StorageBagAutoClaimEntry(base_id, str(base_id), str(base_id), quantity,
                                   1, 1, disposition, "test data", template)


def plan(*entries, routed=(), deferred=()):
    return StorageBagAutoClaimPlan("fresh-post-state", len(entries), tuple(entries),
                                   tuple(routed), tuple(deferred), ())


def test_new_box_proceeds_remain_actionable_after_letter_capacity_closes():
    stones = entry(1001, 5364, "direct_use")
    letters = entry(20300003, 78, "special_use")
    gifts = entry(37130904, 60, None, "routed")
    prayer = entry(38100037, 276, "open_random_box", "deferred")
    current = plan(letters, stones, routed=(gifts,), deferred=(prayer,))
    assert remaining_storage_bag_current_items(
        current, disciple_capacity_finished=True) == (stones,)
    assert current.routed == (gifts,) and current.deferred == (prayer,)


def test_letter_stock_is_not_complete_without_native_capacity_evidence():
    ordinary = entry(20300001, 512, "special_use")
    mutated = entry(20300003, 78, "special_use")
    current = plan(ordinary, mutated)
    assert remaining_storage_bag_current_items(current) == (ordinary, mutated)
    assert remaining_storage_bag_current_items(
        current, disciple_capacity_finished=True) == ()


def test_other_selected_special_items_do_not_share_disciple_capacity():
    unrelated = entry(100, 2, "special_use")
    assert remaining_storage_bag_current_items(
        plan(unrelated), disciple_capacity_finished=True) == (unrelated,)


def test_generated_current_stock_does_not_schedule_tomorrow():
    now = datetime(2026, 10, 9, 3, 20, 42)
    result = {'outcome': 'partial', 'remaining_current_items': [entry(1001, 5364, 'direct_use').to_dict()],
              'unresolved_items': []}
    assert next_storage_bag_operation_at(now, result=result) == datetime(2026, 10, 9, 3, 21, 42)
    assert next_storage_bag_operation_at(now, result={'outcome': 'complete', 'remaining_current_items': []}) == datetime(2026, 10, 10)


@pytest.mark.parametrize('result', [
    {'outcome': 'partial_safe', 'unresolved_items': [{'base_id': 1}]},
    {'outcome': 'partial', 'remaining_current_items': []},
    {'outcome': 'complete', 'remaining_current_items': [{'base_id': 1001}]},
    {'outcome': 'partial', 'remaining_current_items': [{'base_id': 1001}], 'unresolved_items': [{'base_id': 1}]},
])
def test_unproven_scope_neither_closes_daily_job_nor_schedules_normal_follow_up(result):
    with pytest.raises(ValueError):
        next_storage_bag_operation_at(datetime(2026, 10, 9, 3, 20), result=result)


def test_follow_up_evidence_distinguishes_stock_retry_from_due_job_yield():
    now = datetime(2026, 10, 10, 18, 25)
    partial = {'outcome': 'partial', 'remaining_current_items': [{'base_id': 1001}],
               'unresolved_items': []}
    assert storage_bag_operation_follow_up(partial, now=now) == {
        'reason': 'remaining_current_stock', 'next_time': '2026-10-10 18:26:00'}
    retry_at = now.astimezone().replace(minute=33)
    yielded = {**partial, 'yield_for_due_job': {'retry_at': retry_at.isoformat()}}
    assert storage_bag_operation_follow_up(yielded, now=now) == {
        'reason': 'due_job_yield', 'next_time': '2026-10-10 18:33:00'}
    assert 'follow_up' not in partial and 'follow_up' not in yielded


def test_follow_up_daily_closure_requires_proven_scope():
    now = datetime(2026, 10, 10, 18, 39)
    assert storage_bag_operation_follow_up(
        {'outcome': 'complete', 'remaining_current_items': []}, now=now) == {
            'reason': 'daily_closed', 'next_time': '2026-10-11 00:00:00'}
    with pytest.raises(ValueError):
        storage_bag_operation_follow_up(
            {'outcome': 'partial', 'remaining_current_items': []}, now=now)
