from backend.core.fanxiu_sunlogin_rotate import _ocr_shape_box, _ocr_text_matches


def test_ocr_text_match_modes():
    assert _ocr_text_matches("邮 件", "邮件", "contains")
    assert _ocr_text_matches("邮件", "邮件", "exact")
    assert not _ocr_text_matches("邮件列表", "邮件", "exact")
    assert _ocr_text_matches("邮件12", "邮?12", "wildcard")
    assert _ocr_text_matches("邮件列表", "邮*", "wildcard")
    assert not _ocr_text_matches("邮件列表", "邮?", "wildcard")
    assert _ocr_text_matches("邮件12", r"邮件\d+", "regex")
    assert not _ocr_text_matches("邮件", r"[", "regex")


def test_ocr_shape_box_offsets_rectangle_points():
    box = _ocr_shape_box(
        {"points": [[2, 3], [12, 18]]},
        offset_x=100,
        offset_y=200,
        frame_width=400,
        frame_height=500,
    )

    assert box == {
        "name": "ocr",
        "x": 102,
        "y": 203,
        "w": 10,
        "h": 15,
    }
