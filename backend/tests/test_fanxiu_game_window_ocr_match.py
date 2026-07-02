import numpy as np

from backend.core.fanxiu.runtime.mumu_control import _apply_alpha_mask_for_ocr, _ocr_shape_box, _ocr_text_matches


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


def test_ocr_default_alpha_mask_uses_envelope_instead_of_glyph_stencil():
    crop = np.zeros((8, 10, 3), dtype=np.uint8)
    alpha = np.zeros((8, 10), dtype=np.uint8)
    alpha[2, 2] = 255
    alpha[2, 6] = 255

    masked = _apply_alpha_mask_for_ocr(crop, alpha)

    assert np.array_equal(masked[2, 4], np.array([0, 0, 0], dtype=np.uint8))


def test_ocr_raw_alpha_mask_keeps_legacy_stencil_behavior():
    crop = np.zeros((8, 10, 3), dtype=np.uint8)
    alpha = np.zeros((8, 10), dtype=np.uint8)
    alpha[2, 2] = 255
    alpha[2, 6] = 255

    masked = _apply_alpha_mask_for_ocr(crop, alpha, ocr_mask_mode="raw-alpha")

    assert np.array_equal(masked[2, 4], np.array([255, 255, 255], dtype=np.uint8))

