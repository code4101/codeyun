from backend.core.ocr.reading_layout import build_reading_layout


def line(id, text, x, y, w=400, h=20):
    return dict(line_id=id, text=text, x=x, y=y, w=w, h=h)


def test_chinese_paragraphs_title_indent_and_source_links():
    rows = [line("title", "序一", 240, 40, 60, 35),
            line("a", "这是第一段开始的文字需要连续排列", 90, 200, 360),
            line("b", "这里是第一段继续的文字没有换段", 50, 230),
            line("c", "第一段结束。", 50, 260, 140),
            line("d", "这是第二段开始的文字需要连续排列", 90, 290, 360),
            line("e", "这里是第二段继续的文字没有换段", 50, 320)]
    result = build_reading_layout(rows, dict(height=1000, page_height_pt=500))
    title, first, second = result["blocks"]
    assert title["kind"] == "heading"  # Large centered title near top is not a running header.
    assert title["font_scale"] == 1.75
    assert first["line_ids"] == ["a", "b", "c"]
    assert first["indent_em"] == second["indent_em"] == 2
    assert first["font_size_estimate_pt"] == 10
    assert first["box"] == [50, 200, 400, 80]
    assert second["space_before_em"] >= .65
    assert "\n\n" in result["text"]
    assert len(result["text"].replace("\n", "")) == sum(len(r["text"]) for r in rows)


def test_gap_and_side_by_side_boxes_are_not_joined():
    rows = [line("a", "A long left column of words", 30, 200),
            line("b", "A long right column of words", 500, 200),
            line("c", "A new separated paragraph", 30, 400)]
    assert len(build_reading_layout(rows, {})["blocks"]) == 3


def test_latin_wrapping_retains_word_boundary_and_empty_page():
    rows = [line("a", "These are the beginning words", 30, 200),
            line("b", "followed by more words", 30, 230)]
    result = build_reading_layout(rows, {})
    assert len(result["blocks"]) == 1
    assert "words followed" in result["text"]
    assert build_reading_layout([], {})["blocks"] == []
