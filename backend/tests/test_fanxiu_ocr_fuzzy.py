from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.ocr_spatial import (
    OcrTextMatchAmbiguousError,
    find_fuzzy_text_matches,
    select_fuzzy_text_match,
)


def _tokens(text: str, *, x: float = 10, y: float = 100, line_id: str = "menu"):
    return [
        {
            "text": character,
            "x": x + index * 30,
            "y": y,
            "w": 28,
            "h": 36,
            "parent_line_id": line_id,
            "line_order": 0,
            "order": index,
        }
        for index, character in enumerate(text)
    ]


def test_fuzzy_text_match_keeps_real_typo_token_box_and_relative_click_point():
    matches = find_fuzzy_text_matches(_tokens("蓬来仙藏限时团购"), "蓬莱仙藏", min_score=70)
    match = select_fuzzy_text_match(matches, "蓬莱仙藏")

    assert match is not None
    assert match.text == "蓬来仙藏"
    assert match.score == pytest.approx(75.0)
    assert match.box == {"x": 10.0, "y": 100.0, "w": 118.0, "h": 36.0}
    assert match.point(anchor="top_center", offset=(0, -1), offset_unit="height") == (69.0, 64.0)


def test_fuzzy_text_match_rejects_short_incidental_fragment():
    assert find_fuzzy_text_matches(_tokens("仙藏礼包"), "蓬莱仙藏", min_score=60) == []


def test_fuzzy_text_match_rejects_tied_controls_without_explicit_occurrence():
    matches = find_fuzzy_text_matches(
        _tokens("蓬来仙藏", y=100, line_id="first")
        + _tokens("蓬来仙藏", y=200, line_id="second"),
        "蓬莱仙藏",
        min_score=70,
    )

    with pytest.raises(OcrTextMatchAmbiguousError, match="多个近似候选"):
        select_fuzzy_text_match(matches, "蓬莱仙藏")


def test_fuzzy_text_match_never_crosses_visual_lines():
    tokens = _tokens("蓬莱", y=100, line_id="first") + _tokens("仙藏", y=200, line_id="second")

    assert find_fuzzy_text_matches(tokens, "蓬莱仙藏", min_score=60) == []
