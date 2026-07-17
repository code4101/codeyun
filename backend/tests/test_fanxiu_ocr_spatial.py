from __future__ import annotations

from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens, locate_text_box, query_spatial_ocr
from backend.core.fanxiu.runtime.behavior_tree import create_fanxiu_runtime_runner


def _tokens(text: str, *, x: float = 0, y: float = 10, width: float = 20, height: float = 20):
    return [
        {"text": char, "x": x + index * width, "y": y, "w": width, "h": height}
        for index, char in enumerate(text)
    ]


def test_spatial_ocr_selects_tokens_then_groups_text():
    result = query_spatial_ocr(
        _tokens("是否创建队伍？", x=80, y=100, width=20, height=30),
        {"x": 60, "y": 90, "w": 260, "h": 50},
    )

    assert result["text"] == "是否创建队伍？"
    assert [fragment["text"] for fragment in result["fragments"]] == ["是否创建队伍？"]


def test_spatial_ocr_uses_real_variable_width_character_boxes():
    tokens = [
        {"text": "甲", "x": 0, "y": 10, "w": 10, "h": 20},
        {"text": "乙", "x": 12, "y": 10, "w": 18, "h": 20},
        {"text": "丙", "x": 40, "y": 10, "w": 35, "h": 20},
        {"text": "丁", "x": 80, "y": 10, "w": 20, "h": 20},
    ]

    result = query_spatial_ocr(tokens, {"x": 10, "y": 0, "w": 68, "h": 40})

    assert result["text"] == "乙丙"
    assert result["fragments"][0]["source"] == "tokens"


def test_spatial_ocr_groups_rows_by_geometry_without_line_metadata():
    tokens = _tokens("甲乙", x=10, y=10) + _tokens("丙丁", x=10, y=60)

    assert [fragment["text"] for fragment in group_ocr_tokens(tokens)] == ["甲乙", "丙丁"]


def test_locate_substring_uses_exact_character_boxes():
    tokens = [
        {"text": "真", "x": 146, "y": 1144, "w": 36, "h": 49},
        {"text": "仙", "x": 183, "y": 1144, "w": 36, "h": 49},
        {"text": "试", "x": 220, "y": 1144, "w": 36, "h": 49},
        {"text": "炼", "x": 257, "y": 1144, "w": 36, "h": 49},
    ]

    assert locate_text_box(tokens, "真仙") == {"x": 146.0, "y": 1144.0, "w": 73.0, "h": 49.0}


def test_spatial_ocr_includes_character_at_thirty_percent_overlap():
    result = query_spatial_ocr(
        _tokens("天地", y=0),
        {"x": 14, "y": 0, "w": 6, "h": 20},
    )

    assert result["text"] == "天"


def test_spatial_ocr_excludes_character_below_thirty_percent_even_when_center_inside():
    result = query_spatial_ocr(
        [{"text": "天", "x": 0, "y": 0, "w": 100, "h": 100}],
        {"x": 49, "y": 49, "w": 2, "h": 2},
    )

    assert result == {"text": "", "fragments": [], "tokens": []}


def test_shape_match_keeps_exact_token_box():
    runner = create_fanxiu_runtime_runner()
    image = {"id": 34, "width": 900, "height": 1600}
    shape = {
        "title": "左侧菜单",
        "ocrText": "天道",
        "ocrMatchMode": "contains",
        "x": 0,
        "y": 0.4,
        "w": 0.3,
        "h": 0.2,
    }
    frame = "same-frame"
    ctx = {
        "_ocr_tokens_cache": {
            "version": 3,
            "frame": frame,
            "tokens": [
                {"text": "天", "x": 11, "y": 721, "w": 31, "h": 35},
                {"text": "道", "x": 46, "y": 721, "w": 31, "h": 35},
            ],
        }
    }

    result = runner._shape_cached_frame_ocr_match(ctx, image, shape, frame)

    assert result["matched"] is True
    assert result["ocr_box"] == {"x": 11.0, "y": 721.0, "w": 66.0, "h": 35.0}


def test_shape_ocr_reuses_one_token_cache_for_multiple_shapes(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image = {"id": 322, "width": 900, "height": 1600}
    first = {"title": "前半", "imageMatchRole": "off", "ocrMatchRole": "required", "ocrText": "是否创建", "ocrMatchMode": "contains", "x": 0.08, "y": 0.29, "w": 0.2, "h": 0.06}
    second = {"title": "整句", "imageMatchRole": "off", "ocrMatchRole": "required", "ocrText": "是否创建队伍", "ocrMatchMode": "contains", "x": 0.08, "y": 0.29, "w": 0.4, "h": 0.06}
    calls: list[tuple[str, dict | None]] = []

    def fake_ocr_frame(frame: str, *, options=None):
        calls.append((frame, options))
        return {"tokens": _tokens("是否创建队伍？", x=80, y=480, width=25, height=50)}

    monkeypatch.setattr(runner, "_ocr_frame", fake_ocr_frame)
    ctx: dict = {}

    assert runner._match_shape(ctx, image, first, "same-frame", condition="ocr")["matched"] is True
    assert runner._match_shape(ctx, image, second, "same-frame", condition="ocr")["matched"] is True
    assert calls == [("same-frame", {"return_word_box": True})]
