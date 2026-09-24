"""Pure batch arithmetic; GUI behavior is validated against the real game."""

from fractions import Fraction
from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation.tasks.dandao_resource_use import (
    plan_dandao_base_batch,
    plan_dandao_recipe_count,
)
from backend.core.fanxiu.data_annotation.tasks.resource_rank_daily_gift import (
    active_resource_rank_gift_adapters,
)
from backend.core.fanxiu.activity.ranking_lifecycle import (
    DANDAO_RESOURCE_USE_KIND, DANDAO_TAKE_MEDICINE_KIND, RankingOccurrence,
    checkpoints_for_occurrence, due_ranking_checkpoints,
)


def test_unknown_rate_spends_only_the_probe_floor():
    assert plan_dandao_base_batch(120_000 - 27_223, None) == 10_000


def test_latest_observed_rate_controls_half_remaining_with_floor():
    assert plan_dandao_base_batch(92_777, Fraction(27_223, 10_000)) == 17_041
    assert plan_dandao_base_batch(10_000, Fraction(3)) == 10_000
    assert plan_dandao_base_batch(0, None) == 0


@pytest.mark.parametrize("remaining,unit,capacity,expected", [
    (100, 80, 20, 2), (7_600, 80, 747, 95), (10_000, 600, 4, 4),
])
def test_whole_recipe_count_rounds_up_but_never_exceeds_capacity(remaining, unit, capacity, expected):
    assert plan_dandao_recipe_count(remaining, unit, capacity) == expected


def test_invalid_rate_cannot_authorize_consumption():
    with pytest.raises(ValueError):
        plan_dandao_base_batch(10_000, Fraction(0))


def test_eight_server_occurrence_uses_its_own_activity_identity():
    snapshot = {"occurrences": [{
        "activity_id": 8043101, "identity_complete": True,
        "start_at": "2026-09-24T05:00:05+08:00",
        "end_at": "2026-09-25T22:00:00+08:00",
    }]}
    before = datetime.fromisoformat("2026-09-24T05:00:00+08:00")
    opened = datetime.fromisoformat("2026-09-24T05:00:05+08:00")
    assert active_resource_rank_gift_adapters(snapshot, now=before) == []
    assert [(a.key, activity_id) for a, activity_id in
            active_resource_rank_gift_adapters(snapshot, now=opened)] == [
        ("dandao-wending", 8043101),
    ]


@pytest.mark.parametrize("activity_id,cross_count", [(1043111, 1), (8043101, 8)])
def test_alchemy_five_am_checkpoint_waits_for_open_and_catches_up_once(activity_id, cross_count):
    start = datetime.fromisoformat("2026-09-24T05:00:05+08:00")
    end = datetime.fromisoformat("2026-09-25T22:00:00+08:00")
    occurrence = RankingOccurrence(
        activity_type="dandao-wending", family="resource_rank",
        runtime_id=f"runtime-{activity_id}", activity_id=activity_id,
        start_at=start, end_at=end, prepare_at=start, close_at=end,
        cross_count=cross_count,
    )
    checkpoints = checkpoints_for_occurrence(occurrence, business_day=start.date())
    item = next(c for c in checkpoints if c.checkpoint_kind == DANDAO_RESOURCE_USE_KIND)
    assert item.due_at == start
    now = datetime.fromisoformat("2026-09-24T13:00:00+08:00")
    assert item in due_ranking_checkpoints([occurrence], now=now)
    assert item not in due_ranking_checkpoints([occurrence], now=now, completed_keys={item.key})


def test_medicine_is_once_per_occurrence_and_follows_alchemy():
    from dataclasses import replace
    start = datetime.fromisoformat("2026-09-24T05:00:05+08:00")
    end = datetime.fromisoformat("2026-09-25T22:00:00+08:00")
    occurrence = RankingOccurrence(
        activity_type="dandao-wending", family="resource_rank",
        runtime_id="medicine-first", activity_id=8043101,
        start_at=start, end_at=end, prepare_at=start, close_at=end, cross_count=8,
    )
    first = due_ranking_checkpoints([occurrence], now=start)
    kinds = [c.checkpoint_kind for c in first]
    assert kinds.index(DANDAO_RESOURCE_USE_KIND) < kinds.index(DANDAO_TAKE_MEDICINE_KIND)
    medicine = next(c for c in first if c.checkpoint_kind == DANDAO_TAKE_MEDICINE_KIND)
    tomorrow = datetime.fromisoformat("2026-09-25T12:00:00+08:00")
    assert medicine in due_ranking_checkpoints([occurrence], now=tomorrow)
    assert not any(c.checkpoint_kind == DANDAO_TAKE_MEDICINE_KIND for c in
                   due_ranking_checkpoints([occurrence], now=tomorrow, completed_keys={medicine.key}))
    # The same template in another occurrence must not inherit completion.
    next_occurrence = replace(occurrence, runtime_id="medicine-next")
    assert any(c.checkpoint_kind == DANDAO_TAKE_MEDICINE_KIND for c in
               due_ranking_checkpoints([next_occurrence], now=tomorrow, completed_keys={medicine.key}))


def test_batch_medicine_is_only_a_resource_ranking_component():
    from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
    from backend.core.fanxiu.data_annotation.kernel_scheduler_defaults import (
        consolidate_arena_scheduler_instances, default_kernel_scheduler_tasks,
    )

    assert get_fanxiu_data_annotation_task_cell_definition("take_medicine_batch") is None
    assert not any(job["id"] == "take-medicine-batch" for job in default_kernel_scheduler_tasks())
    migrated, changed = consolidate_arena_scheduler_instances([
        {"id": "resource-ranking", "task_type": "resource_ranking", "next_time": None},
        {"id": "take-medicine-batch", "task_type": "take_medicine_batch", "next_time": None},
    ])
    assert changed
    assert [job["id"] for job in migrated] == ["resource-ranking"]
