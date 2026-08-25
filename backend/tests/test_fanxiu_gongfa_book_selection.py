from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.gongfa_book_selection import (
    GONGFA_CATEGORY_OPTIONS,
    gongfa_category_click_point,
    locate_gongfa_category_options,
)


def _real_scene_439_tokens():
    texts = tuple("全部仙术剑修法修魔修体修")
    xs = (129, 164, 256, 291, 375, 410, 508, 536, 628, 663, 754, 782)
    widths = (60,) * 11 + (48,)
    return [
        {"text": text, "x": x, "y": 566, "w": width, "h": 39}
        for text, x, width in zip(texts, xs, widths, strict=True)
    ]


def test_locates_scene_439_options_by_pairing_character_boxes():
    options = locate_gongfa_category_options(reversed(_real_scene_439_tokens()))

    assert tuple(option.label for option in options) == GONGFA_CATEGORY_OPTIONS
    assert options[1].click_point == pytest.approx((303.5, 585.5))
    assert options[3].click_point == pytest.approx((552.0, 585.5))


def test_expands_merged_word_tokens_before_pairing():
    tokens = []
    for index, label in enumerate(GONGFA_CATEGORY_OPTIONS):
        tokens.append(
            {"text": label, "x": 100 + index * 120, "y": 500, "w": 60, "h": 40}
        )

    options = locate_gongfa_category_options(tokens)

    assert options[4].label == "魔修"
    assert options[4].click_point == pytest.approx((610.0, 520.0))


def test_rejects_incomplete_or_misrecognized_character_sequence():
    tokens = _real_scene_439_tokens()
    tokens[3] = {**tokens[3], "text": "木"}

    with pytest.raises(ValueError, match="单字序列不完整或顺序异常"):
        locate_gongfa_category_options(tokens)


def test_rejects_unknown_target_category():
    with pytest.raises(ValueError, match="未知功法分类"):
        gongfa_category_click_point(_real_scene_439_tokens(), "雷修")
