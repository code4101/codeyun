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
