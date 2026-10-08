"""实例授权为纯业务契约，不模拟游戏、点击或Runtime。"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from backend.core.fanxiu.activity.ranking_lifecycle import (
    RankingOccurrence, authorize_lingchong_resource_occurrences,
    checkpoints_for_occurrence, LINGCHONG_RESOURCE_USE_KIND, LINGCHONG_ROUND_REWARDS_KIND,
)


def test_changed_runtime_id_cannot_silently_drop_same_period_authorization():
    start = datetime(2026, 10, 7, 5, 0, 5, tzinfo=timezone(timedelta(hours=8)))
    current = RankingOccurrence(activity_type='lingchong-jingwu', family='resource_rank',
        runtime_id='1042911400004', activity_id=1042911, start_at=start,
        end_at=start.replace(hour=22, minute=0, second=0), prepare_at=start,
        close_at=start.replace(hour=22, minute=0, second=0), cross_count=1)
    old = replace(current, runtime_id='1042911400057')
    with pytest.raises(ValueError, match='失配'):
        authorize_lingchong_resource_occurrences([current], [old.instance_key])
    authorized, = authorize_lingchong_resource_occurrences([current], [current.instance_key])
    assert authorized.pet_resource_use_authorized
    assert not current.pet_resource_use_authorized
    unselected, = authorize_lingchong_resource_occurrences([current], [])
    assert not unselected.pet_resource_use_authorized
    following = replace(current, start_at=start + timedelta(days=1), end_at=current.end_at + timedelta(days=1))
    next_period, = authorize_lingchong_resource_occurrences([following], [current.instance_key])
    assert not next_period.pet_resource_use_authorized


def test_round_reward_authorization_is_separate_and_occurrence_bound():
    start = datetime(2026, 10, 8, 5, 0, 5, tzinfo=timezone(timedelta(hours=8)))
    current = RankingOccurrence(activity_type='lingchong-jingwu', family='resource_rank',
        runtime_id='16042901400004', activity_id=16042901, start_at=start,
        end_at=start + timedelta(days=1, hours=17), prepare_at=start,
        close_at=start + timedelta(days=2), cross_count=16)
    base, = authorize_lingchong_resource_occurrences([current], [current.instance_key])
    base_kinds = {r.checkpoint_kind for r in checkpoints_for_occurrence(base, business_day=start.date())}
    assert LINGCHONG_RESOURCE_USE_KIND in base_kinds
    assert LINGCHONG_ROUND_REWARDS_KIND not in base_kinds
    rounds, = authorize_lingchong_resource_occurrences([current], [], round_instance_keys=[current.instance_key])
    first = [r for r in checkpoints_for_occurrence(rounds, business_day=start.date()) if r.checkpoint_kind == LINGCHONG_ROUND_REWARDS_KIND]
    second = [r for r in checkpoints_for_occurrence(rounds, business_day=(start + timedelta(days=1)).date()) if r.checkpoint_kind == LINGCHONG_ROUND_REWARDS_KIND]
    assert len(first) == len(second) == 1 and first[0].key == second[0].key
    assert not rounds.pet_resource_use_authorized
    local = replace(current, cross_count=1)
    with pytest.raises(ValueError, match='16跨'):
        authorize_lingchong_resource_occurrences([local], [], round_instance_keys=[local.instance_key])
