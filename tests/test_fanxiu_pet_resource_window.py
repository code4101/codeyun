from dataclasses import replace
from datetime import datetime, date

from backend.core.fanxiu.activity.ranking_lifecycle import (
    LINGCHONG_RESOURCE_USE_KIND, RankingOccurrence, checkpoints_for_occurrence,
    due_ranking_checkpoints,
)
from backend.core.fanxiu.activity.lingchong_jingwu import resolve_loaded_pet_talent_ladder_size
import pytest


def test_pet_consumption_requires_occurrence_authorization_and_actual_opening():
    occurrence = RankingOccurrence(
        activity_type='lingchong-jingwu', family='resource_rank', runtime_id='1042911400057',
        activity_id=1042911, cross_count=1, base_id=42910,
        start_at=datetime.fromisoformat('2026-10-07T05:00:05+08:00'),
        prepare_at=datetime.fromisoformat('2026-10-07T05:00:00+08:00'),
        end_at=datetime.fromisoformat('2026-10-07T22:00:00+08:00'),
        close_at=datetime.fromisoformat('2026-10-09T23:58:59+08:00'),
    )
    assert not any(c.checkpoint_kind == LINGCHONG_RESOURCE_USE_KIND for c in
                   checkpoints_for_occurrence(occurrence, business_day=date(2026, 10, 7)))
    authorized = replace(occurrence, pet_resource_use_authorized=True)
    rows = [c for c in checkpoints_for_occurrence(authorized, business_day=date(2026, 10, 7))
            if c.checkpoint_kind == LINGCHONG_RESOURCE_USE_KIND]
    assert len(rows) == 1
    assert rows[0].due_at == occurrence.start_at
    assert not any(c.checkpoint_kind == LINGCHONG_RESOURCE_USE_KIND for c in
                   due_ranking_checkpoints((authorized,), now=occurrence.prepare_at, production_only=True))
    due = due_ranking_checkpoints((authorized,), now=occurrence.start_at, production_only=True)
    assert due[0].checkpoint_kind == LINGCHONG_RESOURCE_USE_KIND
    assert not any(c.checkpoint_kind == LINGCHONG_RESOURCE_USE_KIND for c in
                   due_ranking_checkpoints((authorized,), now=occurrence.start_at,
                                          completed_keys={due[0].key}, production_only=True))
    next_day = [c for c in checkpoints_for_occurrence(authorized, business_day=date(2026, 10, 8))
                if c.checkpoint_kind == LINGCHONG_RESOURCE_USE_KIND]
    assert not next_day


def test_loaded_ids_bind_a_whole_ladder_instead_of_a_missing_tier_subset():
    # Compact identities from the actual 1042911 export: old 12 + new 14.
    old = [{'id': 104291100+i, 'sort': i} for i in range(1, 13)]
    new = [{'id': 104291150+i, 'sort': i} for i in range(1, 15)]
    configs = old + new
    assert resolve_loaded_pet_talent_ladder_size(configs, [r['id'] for r in old]) == 12
    assert resolve_loaded_pet_talent_ladder_size(configs, [r['id'] for r in new]) == 14
    with pytest.raises(ValueError):
        resolve_loaded_pet_talent_ladder_size(configs, [r['id'] for r in new[:12]])
    with pytest.raises(ValueError):
        resolve_loaded_pet_talent_ladder_size(configs, [r['id'] for r in old+new])
    unrelated_short = [{'id': 404350100+i, 'sort': i} for i in range(1, 13)]
    with pytest.raises(ValueError):
        resolve_loaded_pet_talent_ladder_size(unrelated_short, [r['id'] for r in unrelated_short])


def test_pet_rank_binding_uses_single_server_self_and_cross_server_follow():
    from backend.core.fanxiu.activity.exchange_activity_registry import resolve_registered_occurrence_rank_identities
    single = resolve_registered_occurrence_rank_identities(
        activity_type='lingchong-jingwu', game_activity_id=1042911, cross_count=1)
    assert set(single) == {'personal'}
    assert single['personal'].runtime_rank_activity_id == 1042911
    cross = resolve_registered_occurrence_rank_identities(
        activity_type='lingchong-jingwu', game_activity_id=8042901, cross_count=8,
        activity_follow=(42905, 42906))
    assert cross['personal'].runtime_rank_activity_id == 42905
    assert cross['plane'].runtime_rank_activity_id == 42906
