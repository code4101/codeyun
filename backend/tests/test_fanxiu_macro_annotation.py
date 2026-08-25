from __future__ import annotations

import base64

import pytest

from backend.core.fanxiu.game import macro_annotation


@pytest.fixture(autouse=True)
def clear_ocr_frame_cache():
    macro_annotation._clear_ocr_frame_cache()
    yield
    macro_annotation._clear_ocr_frame_cache()


def test_ocr_frame_reuses_result_for_identical_image_and_options(monkeypatch):
    calls = 0

    def fake_preview(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"document": {}}

    monkeypatch.setattr(macro_annotation, "run_paddle_ocr_preview", fake_preview)
    image_data_url = "data:image/png;base64," + base64.b64encode(b"same-image").decode("ascii")

    first = macro_annotation._recognize_data_annotation_ocr_frame(
        image_data_url,
        options={"lang": "ch"},
    )
    second = macro_annotation._recognize_data_annotation_ocr_frame(
        image_data_url,
        options={"lang": "ch"},
    )

    assert calls == 1
    assert first == second
    assert first is not second


def test_ocr_frame_defaults_to_chinese_english_model(monkeypatch):
    captured_options = None

    def fake_preview(*_args, **kwargs):
        nonlocal captured_options
        captured_options = kwargs.get("options")
        return {"document": {}}

    monkeypatch.setattr(macro_annotation, "run_paddle_ocr_preview", fake_preview)
    image_data_url = "data:image/png;base64," + base64.b64encode(b"fanxiu-image").decode("ascii")

    macro_annotation._recognize_data_annotation_ocr_frame(image_data_url)

    assert captured_options == {"lang": "ch", "ocr_version": "PP-OCRv4"}


def test_ocr_frame_filters_non_chinese_english_scripts(monkeypatch):
    def fake_preview(*_args, **_kwargs):
        return {
            "document": {
                "flags": {
                    "paddleocr_payload": {
                        "rec_texts": ["鉴宝순괜A1%", "순괜"],
                        "rec_scores": [0.99, 0.98],
                        "rec_boxes": [[10, 20, 110, 50], [120, 20, 180, 50]],
                        "text_word": [["鉴", "宝", "순", "괜", "A", "1", "%"], ["순", "괜"]],
                        "text_word_boxes": [
                            [[10, 20, 20, 50], [20, 20, 30, 50], [30, 20, 40, 50], [40, 20, 50, 50], [50, 20, 60, 50], [60, 20, 70, 50], [70, 20, 80, 50]],
                            [[120, 20, 140, 50], [140, 20, 160, 50]],
                        ],
                    }
                }
            }
        }

    monkeypatch.setattr(macro_annotation, "run_paddle_ocr_preview", fake_preview)
    image_data_url = "data:image/png;base64," + base64.b64encode(b"fanxiu-filter").decode("ascii")

    response = macro_annotation._recognize_data_annotation_ocr_frame(image_data_url)

    assert [line.text for line in response.lines] == ["鉴宝A1%"]
    assert [token.text for token in response.tokens] == ["鉴", "宝", "A", "1", "%"]


def test_ocr_frame_cache_separates_different_options(monkeypatch):
    calls = 0

    def fake_preview(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"document": {}}

    monkeypatch.setattr(macro_annotation, "run_paddle_ocr_preview", fake_preview)
    image_data_url = "data:image/png;base64," + base64.b64encode(b"same-image").decode("ascii")

    macro_annotation._recognize_data_annotation_ocr_frame(image_data_url, options={"lang": "ch"})
    macro_annotation._recognize_data_annotation_ocr_frame(image_data_url, options={"lang": "en"})

    assert calls == 2


def test_ocr_frame_preserves_paddle_lines_and_links_tokens(monkeypatch):
    def fake_preview(*_args, **_kwargs):
        return {
            "document": {
                "flags": {
                    "paddleocr_payload": {
                        "rec_texts": ["白玉京", "100%"],
                        "rec_scores": [0.99, 0.97],
                        "rec_boxes": [[403, 625, 493, 658], [691, 622, 731, 643]],
                        "text_word": [["白", "玉", "京"], ["1", "0", "0", "%"]],
                        "text_word_boxes": [
                            [[403, 625, 433, 658], [433, 625, 463, 658], [463, 625, 493, 658]],
                            [[691, 622, 701, 643], [701, 622, 711, 643], [711, 622, 721, 643], [721, 622, 731, 643]],
                        ],
                    }
                }
            }
        }

    monkeypatch.setattr(macro_annotation, "run_paddle_ocr_preview", fake_preview)
    image_data_url = "data:image/png;base64," + base64.b64encode(b"dongtian-frame").decode("ascii")

    response = macro_annotation._recognize_data_annotation_ocr_frame(image_data_url)

    assert [line.text for line in response.lines] == ["白玉京", "100%"]
    assert response.lines[0].source == "paddle"
    assert [token.parent_line_id for token in response.tokens] == ["line-0"] * 3 + ["line-1"] * 4
