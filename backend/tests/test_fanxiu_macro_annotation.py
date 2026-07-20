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
