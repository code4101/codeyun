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
