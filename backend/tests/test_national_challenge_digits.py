"""实际艺术字体 OCR 观测的纯解析回归，不模拟游戏流程。"""
import pytest

from backend.core.fanxiu.data_annotation.tasks.national_celebration import resolve_challenge_digits


@pytest.mark.parametrize(('line', 'candidates', 'expected'), [
    ('第母0关', ['40', '', '年0关'], 40),
    ('第好1关', ['4', '4！', '41', '年人'], 41),
    ('第好1关', ['4', '', '4！'], None),
    ('第好1关', ['41', '47'], None),
    ('', ['41'], None),
    ('第母00关', ['00', '100'], 100),
])
def test_challenge_digits_require_complete_unambiguous_number(line, candidates, expected):
    assert resolve_challenge_digits(line, candidates) == expected


# 2026-10-06 第42关实际顶部Paddle token，时钟与14/20是独立原生行。
REAL_BATTLE_HEADER = [{'text': '第', 'x': 381.0, 'y': 19.0, 'w': 33.0, 'h': 50.0, 'parent_line_id': 'line-0', 'line_order': 0, 'order': 0}, {'text': '42', 'x': 426.0, 'y': 19.0, 'w': 25.0, 'h': 50.0, 'parent_line_id': 'line-0', 'line_order': 0, 'order': 1}, {'text': '关', 'x': 464.0, 'y': 19.0, 'w': 33.0, 'h': 50.0, 'parent_line_id': 'line-0', 'line_order': 0, 'order': 2}, {'text': '02', 'x': 733.0, 'y': 35.0, 'w': 22.0, 'h': 33.0, 'parent_line_id': 'line-1', 'line_order': 1, 'order': 0}, {'text': ':', 'x': 766.0, 'y': 35.0, 'w': 5.0, 'h': 33.0, 'parent_line_id': 'line-1', 'line_order': 1, 'order': 1}, {'text': '40', 'x': 777.0, 'y': 35.0, 'w': 22.0, 'h': 33.0, 'parent_line_id': 'line-1', 'line_order': 1, 'order': 2}, {'text': '14', 'x': 405.0, 'y': 75.0, 'w': 24.0, 'h': 36.0, 'parent_line_id': 'line-2', 'line_order': 2, 'order': 0}, {'text': '/', 'x': 441.0, 'y': 75.0, 'w': 6.0, 'h': 36.0, 'parent_line_id': 'line-2', 'line_order': 2, 'order': 1}, {'text': '20', 'x': 459.0, 'y': 75.0, 'w': 24.0, 'h': 36.0, 'parent_line_id': 'line-2', 'line_order': 2, 'order': 2}, {'text': '46', 'x': 735.0, 'y': 81.0, 'w': 20.0, 'h': 30.0, 'parent_line_id': 'line-3', 'line_order': 3, 'order': 0}]


def test_active_battle_does_not_merge_clock_with_wave():
    from backend.core.fanxiu.data_annotation.tasks.national_celebration import parse_active_challenge_state
    assert parse_active_challenge_state(REAL_BATTLE_HEADER) == {'stage': 42, 'wave': 14}


@pytest.mark.parametrize('mutation', ['no_wave', 'duplicate_stage', 'invalid_wave', 'unlinked'])
def test_active_battle_requires_unique_complete_native_header(mutation):
    from copy import deepcopy
    from backend.core.fanxiu.data_annotation.tasks.national_celebration import parse_active_challenge_state
    tokens = deepcopy(REAL_BATTLE_HEADER)
    if mutation == 'no_wave':
        tokens = [t for t in tokens if t['parent_line_id'] != 'line-2']
    elif mutation == 'duplicate_stage':
        tokens += [{**t, 'parent_line_id': 'duplicate-stage'} for t in tokens if t['parent_line_id'] == 'line-0']
    elif mutation == 'invalid_wave':
        next(t for t in tokens if t['parent_line_id'] == 'line-2' and t['order'] == 0)['text'] = '41'
    else:
        for t in tokens:
            t.pop('parent_line_id')
    assert parse_active_challenge_state(tokens) is None


def test_verified_challenge_completion_requires_current_positive_progress():
    from backend.core.fanxiu.data_annotation.tasks.national_celebration import validate_challenge_completion
    hint = {'business_date': '2026-10-06', 'before_stage': 42, 'stage': 43}
    result = validate_challenge_completion(hint, business_date='2026-10-06', current_stage=43)
    assert result['advanced'] is True and result['started'] is False
    with pytest.raises(RuntimeError):
        validate_challenge_completion(hint, business_date='2026-10-06', current_stage=42)


@pytest.mark.parametrize('hint', [
    {'business_date': '2026-10-05', 'before_stage': 42, 'stage': 43},
    {'business_date': '2026-10-06', 'before_stage': True, 'stage': 2},
    {'business_date': '2026-10-06', 'before_stage': 42, 'stage': 42},
    {'business_date': '2026-10-06', 'before_stage': 42, 'stage': 44},
    {},
])
def test_verified_challenge_completion_rejects_stale_or_ambiguous_hint(hint):
    from backend.core.fanxiu.data_annotation.tasks.national_celebration import validate_challenge_completion
    with pytest.raises(ValueError):
        validate_challenge_completion(hint, business_date='2026-10-06')
