from __future__ import annotations

from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens, locate_text_box, query_ocr_lines, query_spatial_ocr
from backend.core.fanxiu.runtime.behavior_tree import create_fanxiu_runtime_runner


def _tokens(text: str, *, x: float = 0, y: float = 10, width: float = 20, height: float = 20, line_id: str | None = None, line_order: int = 0):
    return [
        {
            "text": char, "x": x + index * width, "y": y, "w": width, "h": height,
            **({"parent_line_id": line_id, "line_order": line_order, "order": index} if line_id else {}),
        }
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
    assert result["fragments"][0]["source"] == "roi_tokens_unlinked"


def test_spatial_ocr_groups_rows_by_geometry_without_line_metadata():
    tokens = _tokens("甲乙", x=10, y=10, line_id="line-0") + _tokens("丙丁", x=10, y=60, line_id="line-1", line_order=1)

    assert [fragment["text"] for fragment in group_ocr_tokens(tokens)] == ["甲乙", "丙丁"]


def test_paddle_parent_lines_keep_distant_gui_labels_separate_on_the_same_row():
    tokens = (
        _tokens("盟玉清道宗12/12", x=71, y=883, width=30, height=31, line_id="line-0")
        + _tokens("太明玉墟", x=638, y=886, width=29, height=31, line_id="line-1", line_order=1)
    )

    fragments = group_ocr_tokens(tokens)

    assert [fragment["text"] for fragment in fragments] == ["盟玉清道宗12/12", "太明玉墟"]
    assert fragments[1]["x"] == 638.0


def test_native_lines_keep_location_separate_from_progress_and_timer():
    lines = [
        {"line_id": "line-0", "order": 0, "text": "白玉京", "x": 403, "y": 625, "w": 90, "h": 33, "source": "paddle"},
        {"line_id": "line-1", "order": 1, "text": "100%", "x": 691, "y": 622, "w": 40, "h": 21, "source": "paddle"},
        {"line_id": "line-2", "order": 2, "text": "01:23:35", "x": 760, "y": 622, "w": 80, "h": 21, "source": "paddle"},
    ]

    assert [line["text"] for line in query_ocr_lines(lines, {"x": 0, "y": 0, "w": 900, "h": 1600})] == ["白玉京", "100%", "01:23:35"]


def test_unlinked_tokens_are_not_guessed_back_into_a_line():
    fragments = group_ocr_tokens(_tokens("甲乙", x=10, y=10))

    assert [fragment["text"] for fragment in fragments] == ["甲", "乙"]
    assert all(fragment["source"] == "unlinked_token_fallback" for fragment in fragments)


def test_locate_substring_uses_exact_character_boxes():
    tokens = [
        {"text": "真", "x": 146, "y": 1144, "w": 36, "h": 49},
        {"text": "仙", "x": 183, "y": 1144, "w": 36, "h": 49},
        {"text": "试", "x": 220, "y": 1144, "w": 36, "h": 49},
        {"text": "炼", "x": 257, "y": 1144, "w": 36, "h": 49},
    ]

    assert locate_text_box(tokens, "真仙") == {"x": 146.0, "y": 1144.0, "w": 73.0, "h": 49.0}


def test_locate_substring_never_crosses_paddle_parent_lines():
    tokens = (
        _tokens("白玉", x=400, y=625, line_id="line-0")
        + _tokens("京100%", x=700, y=622, line_id="line-1", line_order=1)
    )

    assert locate_text_box(tokens, "白玉京") is None


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


def test_runtime_fragments_use_cached_native_lines_not_token_geometry(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    native_lines = [
        {"line_id": "line-0", "order": 0, "text": "盟玉清道宗12/12", "x": 71, "y": 883, "w": 359, "h": 31, "source": "paddle"},
        {"line_id": "line-1", "order": 1, "text": "太明玉墟", "x": 638, "y": 886, "w": 116, "h": 31, "source": "paddle"},
    ]

    monkeypatch.setattr(runner, "_ocr_frame", lambda *_args, **_kwargs: {"lines": native_lines, "tokens": []})
    ctx: dict = {}

    assert runner._cached_ocr_fragments(ctx, "dongtian-frame") == native_lines
    assert ctx["_ocr_tokens_cache"]["version"] == 4
    assert ctx["_ocr_tokens_cache"]["lines"] == native_lines
