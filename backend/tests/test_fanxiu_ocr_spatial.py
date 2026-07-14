from __future__ import annotations

from backend.core.fanxiu.data_annotation.ocr_spatial import query_spatial_ocr
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
