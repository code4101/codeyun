from __future__ import annotations

import json

from backend.core.fanxiu.game.ocr_utils import _extract_ocr_line_entries, _join_ocr_line_entries


def _shape(text: str, x1: float, y1: float, x2: float, y2: float) -> dict:
    return {
        "label": json.dumps({"text": text}, ensure_ascii=False),
        "points": [[x1, y1], [x2, y2]],
    }


def test_ocr_line_rebuild_keeps_nearby_fragments_together() -> None:
    document = {
        "shapes": [
            _shape("是否创建", 80, 100, 200, 140),
            _shape("队伍？", 210, 101, 300, 141),
        ]
    }

    groups = _extract_ocr_line_entries(document)

    assert [_join_ocr_line_entries(group) for group in groups] == ["是否创建队伍？"]


def test_ocr_line_rebuild_does_not_join_distant_buttons_on_same_row() -> None:
    document = {
        "shapes": [
            _shape("取消", 263, 1035, 349, 1084),
            _shape("战斗", 570, 1036, 657, 1085),
        ]
    }

    groups = _extract_ocr_line_entries(document)

    assert [_join_ocr_line_entries(group) for group in groups] == ["取消", "战斗"]
