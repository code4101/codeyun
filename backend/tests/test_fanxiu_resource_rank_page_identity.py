"""Deterministic identity gates; live scene/navigation acceptance stays in-game."""
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.tasks.resource_rank_page import (
    resource_rank_page_matches_occurrence,
)


@pytest.mark.parametrize('cross_count,page_kind,tab_index,activity_id,complete,expected', [
    (16, 'server_rank', 0, 16042901, True, True),
    (16, 'local_rank', 0, 16042901, True, False),
    (16, 'server_rank', 1, 16042901, True, False),
    (16, 'server_rank', 0, 1042911, True, False),
    (16, 'server_rank', 0, 16042901, False, False),
    (1, 'local_rank', 0, 16042901, True, True),
    (1, 'server_rank', 0, 16042901, True, False),
])
def test_pet_rank_panel_requires_exact_occurrence_and_personal_schema(
    cross_count, page_kind, tab_index, activity_id, complete, expected,
):
    occurrence = SimpleNamespace(activity_type='lingchong-jingwu',
                                 activity_id=16042901, cross_count=cross_count)
    page = dict(complete=complete, activity_id=activity_id,
                page_kind=page_kind, tab_index=tab_index, selected_rank_activity_id=42907)
    assert resource_rank_page_matches_occurrence(page, occurrence, rank_activity_id=42907) is expected


@pytest.mark.parametrize('selected,expected', [(42908, True), (42907, False), (None, False)])
def test_rank_scope_uses_native_selected_rank_id(selected, expected):
    occurrence = SimpleNamespace(activity_type='lingchong-jingwu',
                                 activity_id=16042901, cross_count=16)
    page = dict(complete=True, activity_id=16042901, page_kind='server_rank',
                tab_index=0, selected_rank_activity_id=selected)
    assert resource_rank_page_matches_occurrence(
        page, occurrence, scope='plane', rank_activity_id=42908,
    ) is expected
