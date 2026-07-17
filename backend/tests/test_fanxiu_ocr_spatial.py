from __future__ import annotations

from backend.core.fanxiu.data_annotation.ocr_spatial import locate_text_box, query_spatial_ocr
from backend.core.fanxiu.runtime.behavior_tree import create_fanxiu_runtime_runner


def test_spatial_ocr_estimates_characters_for_partially_covered_line():
    result = query_spatial_ocr(
        [{"text": "12345678", "x": 0, "y": 10, "w": 80, "h": 20}],
        [],
        {"x": 0, "y": 0, "w": 50, "h": 40},
    )

    assert result["text"] == "12345"
    assert result["ambiguous"] is True
    assert result["fragments"][0]["source"] == "estimated_characters"


def test_spatial_ocr_rebuilds_shape_text_across_multiple_blocks():
    result = query_spatial_ocr(
        [
            {"text": "队伍？", "x": 210, "y": 100, "w": 90, "h": 30},
            {"text": "是否创建", "x": 80, "y": 100, "w": 120, "h": 30},
        ],
        [],
        {"x": 60, "y": 90, "w": 260, "h": 50},
    )

    assert result["text"] == "是否创建队伍？"
    assert [fragment["text"] for fragment in result["fragments"]] == ["是否创建", "队伍？"]


def test_spatial_ocr_uses_character_boxes_for_partial_variable_width_text():
    result = query_spatial_ocr(
        [{"text": "甲乙丙丁", "x": 0, "y": 10, "w": 100, "h": 20}],
        [
            {"text": "甲", "x": 0, "y": 10, "w": 10, "h": 20, "line_index": 0},
            {"text": "乙", "x": 12, "y": 10, "w": 18, "h": 20, "line_index": 0},
            {"text": "丙", "x": 40, "y": 10, "w": 35, "h": 20, "line_index": 0},
            {"text": "丁", "x": 80, "y": 10, "w": 20, "h": 20, "line_index": 0},
        ],
        {"x": 10, "y": 0, "w": 68, "h": 40},
    )

    assert result["text"] == "乙丙"
    assert result["ambiguous"] is False
    assert result["fragments"][0]["source"] == "words"


def test_spatial_ocr_uses_character_boxes_for_full_line_and_locates_substring():
    words = [
        {"text": "真", "x": 146, "y": 1144, "w": 36, "h": 49, "line_index": 0},
        {"text": "仙", "x": 183, "y": 1144, "w": 36, "h": 49, "line_index": 0},
        {"text": "试", "x": 220, "y": 1144, "w": 36, "h": 49, "line_index": 0},
        {"text": "炼", "x": 257, "y": 1144, "w": 36, "h": 49, "line_index": 0},
    ]
    result = query_spatial_ocr(
        [{"text": "真仙试炼", "x": 146, "y": 1144, "w": 147, "h": 49}],
        words,
        {"x": 100, "y": 1100, "w": 250, "h": 120},
    )

    assert result["text"] == "真仙试炼"
    assert result["fragments"][0]["source"] == "words"
    assert locate_text_box(result["tokens"], "真仙") == {"x": 146.0, "y": 1144.0, "w": 73.0, "h": 49.0}


def test_spatial_ocr_associates_raw_tokens_by_geometry_after_lines_are_merged():
    words = [
        {"text": "真", "x": 146, "y": 1144, "w": 42, "h": 49, "line_index": 6},
        {"text": "仙", "x": 193, "y": 1144, "w": 44, "h": 49, "line_index": 6},
        {"text": "金", "x": 400, "y": 1144, "w": 42, "h": 49, "line_index": 7},
        {"text": "仙", "x": 447, "y": 1144, "w": 44, "h": 49, "line_index": 7},
    ]
    result = query_spatial_ocr(
        [{"text": "真仙金仙", "x": 146, "y": 1144, "w": 345, "h": 49}],
        words,
        {"x": 120, "y": 1120, "w": 400, "h": 100},
    )

    assert result["text"] == "真仙金仙"
    assert locate_text_box(result["tokens"], "真仙") == {
        "x": 146.0,
        "y": 1144.0,
        "w": 91.0,
        "h": 49.0,
    }


def test_ocr_center_uses_exact_character_boxes_instead_of_uniform_line_width():
    runner = create_fanxiu_runtime_runner()
    image = {
        "width": 900,
        "height": 1600,
        "shapes": [{"title": "试炼", "x": 0.12, "y": 0.70, "w": 0.82, "h": 0.07}],
    }
    lines = [{"text": "真仙试炼金仙试炼", "x": 146, "y": 1144, "w": 414, "h": 49}]
    words = [
        {"text": "真", "x": 146, "y": 1144, "w": 42, "h": 49, "line_index": 6},
        {"text": "仙", "x": 193, "y": 1144, "w": 44, "h": 49, "line_index": 6},
    ]

    assert runner._ocr_centers_in_shape(
        lines,
        image,
        "试炼",
        include=("真仙",),
        words=words,
    ) == [(191.5, 1168.5, "真仙试炼金仙试炼")]


def test_shape_ocr_runs_full_frame_once_and_reuses_spatial_index(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image = {
        "id": 322,
        "title": "创建团队",
        "filename": "0322.png",
        "width": 900,
        "height": 1600,
    }
    first = {
        "title": "问题前半",
        "imageMatchRole": "off",
        "ocrMatchRole": "required",
        "ocrText": "是否创建",
        "x": 0.1,
        "y": 0.3,
        "w": 0.2,
        "h": 0.05,
    }
    second = {
        "title": "问题整句",
        "imageMatchRole": "off",
        "ocrMatchRole": "required",
        "ocrText": "是否创建队伍",
        "x": 0.1,
        "y": 0.3,
        "w": 0.4,
        "h": 0.05,
    }
    calls: list[tuple[str, dict | None]] = []

    def fake_ocr_frame(frame: str, *, options=None):
        calls.append((frame, options))
        return {
            "lines": [
                {"text": "是否创建", "x": 90, "y": 480, "w": 150, "h": 50},
                {"text": "队伍？", "x": 245, "y": 480, "w": 100, "h": 50},
            ],
            "words": [],
        }

    monkeypatch.setattr(runner, "_ocr_frame", fake_ocr_frame)
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}

    first_result = runner._match_shape(ctx, image, first, "same-frame", condition="ocr")
    second_result = runner._match_shape(ctx, image, second, "same-frame", condition="ocr")

    assert first_result["matched"] is True
    assert second_result["matched"] is True
    assert second_result["ocr_text"] == "是否创建队伍？"
    assert calls == [("same-frame", {"return_word_box": True})]

    runner._match_shape(ctx, image, second, "changed-frame", condition="ocr")
    assert calls == [
        ("same-frame", {"return_word_box": True}),
        ("changed-frame", {"return_word_box": True}),
    ]


def test_shape_region_ocr_rebuilds_from_the_same_full_frame_cache(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image = {
        "id": 356,
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "试炼", "x": 0.12, "y": 0.70, "w": 0.82, "h": 0.07},
        ],
    }
    calls: list[tuple[str, dict | None]] = []

    def fake_ocr_frame(frame: str, *, options=None):
        calls.append((frame, options))
        return {
            "lines": [{"text": "真仙试炼金仙试炼", "x": 146, "y": 1144, "w": 414, "h": 49}],
            "words": [
                {"text": "真", "x": 146, "y": 1144, "w": 42, "h": 49, "line_index": 6},
                {"text": "仙", "x": 193, "y": 1144, "w": 44, "h": 49, "line_index": 6},
                {"text": "金", "x": 400, "y": 1144, "w": 42, "h": 49, "line_index": 7},
                {"text": "仙", "x": 447, "y": 1144, "w": 44, "h": 49, "line_index": 7},
            ],
        }

    monkeypatch.setattr(runner, "_ocr_frame", fake_ocr_frame)
    ctx = {"images": {356: image}}

    lines = runner._ocr_lines_in_shapes("same-frame", image, ("试炼",), padding=0, ctx=ctx)
    words = runner._ocr_words_in_shapes("same-frame", image, ("试炼",), padding=0, ctx=ctx)

    assert "".join(line["text"] for line in lines) == "真仙金仙"
    assert "".join(word["text"] for word in words) == "真仙金仙"
    assert calls == [("same-frame", {"return_word_box": True})]
