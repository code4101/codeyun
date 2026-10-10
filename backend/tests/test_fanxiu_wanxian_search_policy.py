"""Pure traversal budgets and formal diagnostic ownership, no simulated game."""
import json
import pytest

from backend.core.fanxiu.activity.wanxian_search import (
    wanxian_search_directions, wanxian_search_direction_finished,
    record_wanxian_search_evidence,
    wanxian_semantic_unchanged,
)
from backend.core.fanxiu.data_annotation.subtask_execution import job_log_context


@pytest.mark.parametrize('primary, expected', [('up', ('up', 'down')), ('down', ('down', 'up'))])
def test_unknown_cursor_requires_both_halves(primary, expected):
    assert wanxian_search_directions(primary) == expected


@pytest.mark.parametrize('primary', ['left', 'right', '', 'unknown'])
def test_invalid_direction_cannot_invent_a_gesture(primary):
    with pytest.raises(ValueError):
        wanxian_search_directions(primary)


@pytest.mark.parametrize('changed', [True, None])
def test_missing_movement_evidence_is_not_an_endpoint(changed):
    assert not wanxian_search_direction_finished(scroll_index=1, limit=30,
                                                previous_scroll_changed=changed)


def test_unchanged_result_is_consumed_after_observation():
    assert wanxian_search_direction_finished(scroll_index=1, limit=30,
                                            previous_scroll_changed=False)


@pytest.mark.parametrize('limit', [-3, 0, 30, 1000000])
def test_budget_always_finite(limit):
    assert wanxian_search_direction_finished(scroll_index=max(0, min(30, limit)),
        limit=limit, previous_scroll_changed=True)


def test_no_formal_owner_no_receipt():
    assert record_wanxian_search_evidence([], outcome='not_found_unconfirmed', frame_paths=[]) is None


def test_animated_endpoint_uses_nonempty_text_not_pixel_change():
    page = ['完成宗门请安', '1/1', '加速1次神物', '已完成']
    assert wanxian_semantic_unchanged(page, page, 0) == 1
    assert wanxian_semantic_unchanged(page, page, 1) == 2
    assert wanxian_semantic_unchanged(page, ['挑战1次万仙伐劫', '0/1'], 1) == 0


@pytest.mark.parametrize('previous,current', [(None, []), ([], []), (['1/1'], []), ([], ['1/1'])])
def test_blank_loading_ocr_never_proves_an_endpoint(previous, current):
    assert wanxian_semantic_unchanged(previous, current, 3) == 0


def test_real_temp_receipt_is_diagnostic_only(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.core.temp_paths.codeyun_temp_root', lambda *args: tmp_path)
    with job_log_context('weekly-wanxian', 'test-evidence-owner'):
        path = record_wanxian_search_evidence([dict(direction='up', scroll_index=0)],
            outcome='not_found_unconfirmed', frame_paths=[None])
    row = json.loads(__import__('pathlib').Path(path).read_text(encoding='utf-8'))
    assert row['attempt_id'] == 'test-evidence-owner'
    assert row['outcome'] == 'not_found_unconfirmed'
    assert row['business_complete'] is False
    assert 'next_time' not in row
