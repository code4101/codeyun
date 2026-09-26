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


def test_same_size_centered_section_heading_uses_surrounding_whitespace():
    rows = [line("a", "这是前面的正文内容用来建立正文边界", 50, 200),
            line("b", "这是连续的正文内容仍然正常组成段落", 50, 230),
            line("title", "2.3 某个小节", 180, 300, 140),
            line("c", "这是小节之后的正文内容保持正常段落", 50, 370),
            line("d", "这是接下来继续的正文内容仍保持连续", 50, 400)]
    result = build_reading_layout(rows, dict(height=1000))
    heading = next(b for b in result["blocks"] if b["id"] == "title")
    assert heading["kind"] == "heading"
    assert heading["align"] == "center"
    assert heading["font_scale"] == 1
    # A centered short line without the vertical separation is not sufficient.
    rows[2]["y"] = 260
    rows[3]["y"] = 290
    rows[4]["y"] = 320
    result = build_reading_layout(rows, dict(height=1000))
    assert all(b["kind"] != "heading" for b in result["blocks"])


def test_smaller_note_keeps_relative_size_and_separate_block():
    rows = [line("a", "正文第一行文字用于建立稳定的主要字号", 50, 200, h=20),
            line("b", "正文第二行文字继续保持相同的主要字号", 50, 230, h=21),
            line("c", "正文第三行文字继续保持相同的主要字号", 50, 260, h=20),
            line("note", "① 这是小字号注释，应该保留相对字号", 50, 300, h=14)]
    result = build_reading_layout(rows, dict(height=1000))
    note = next(b for b in result["blocks"] if b["id"] == "note")
    assert note["kind"] == "paragraph"
    assert note["font_scale"] == .7
    assert result["blocks"][0]["font_scale"] == 1
    assert result["blocks"][0]["line_ids"] == ["a", "b", "c"]


def test_font_bands_absorb_body_noise_and_quantize_smaller_text():
    from backend.core.ocr.reading_layout import normalized_font_scale
    assert {normalized_font_scale(h, 14) for h in (13.6, 13.9, 14, 14.4, 15.5)} == {1.0}
    assert {normalized_font_scale(h, 20) for h in (13.6, 14, 14.4)} == {.7}
    rows = [line(str(i), "这是足够长的正文内容用于判断字体和连续段落", 50, 200+i*30, h=h)
            for i, h in enumerate((20, 22.5, 19.5, 20))]
    result = build_reading_layout(rows, dict(height=1000))
    assert len(result["blocks"]) == 1
    assert result["blocks"][0]["font_scale"] == 1


def test_paragraph_font_uses_all_lines_not_noisy_first_line():
    rows = [line(str(i), "这是同一个正文段落的连续内容不能因字框波动拆开", 50, 200+i*34, h=h)
            for i, h in enumerate((24, 20, 19, 20, 20))]
    result = build_reading_layout(rows, dict(height=1000))
    assert len(result["blocks"]) == 1
    assert result["blocks"][0]["font_scale"] == 1
    assert result["blocks"][0]["line_ids"] == [str(i) for i in range(5)]
