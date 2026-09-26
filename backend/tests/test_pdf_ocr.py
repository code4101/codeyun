import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pymupdf
import pytest
from fastapi import HTTPException

from backend.core.library import pdf_ocr as ocr


@pytest.fixture
def source(tmp_path, monkeypatch):
    path = tmp_path / "source.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=200, height=300)
        page.set_rotation(90)
        pdf.save(path)
    monkeypatch.setattr(ocr, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    return path


def test_persistent_geometry_and_concurrent_dedup(source, monkeypatch):
    calls = []
    payload = {"rec_texts": ["中文abc"], "rec_boxes": [[30, 60, 180, 90]],
               "text_word": [["中", "文", "abc"]],
               "text_word_boxes": [[[30, 60, 60, 90], [60, 60, 90, 90], [90, 60, 180, 90]]]}

    def predict(path, **kwargs):
        assert path.is_file()
        assert not kwargs["options"]["use_doc_unwarping"]
        calls.append(1)
        return {"document": {"flags": {"paddleocr_payload": payload}}}

    monkeypatch.setattr(ocr, "run_paddle_ocr_preview", predict)
    def read():
        return ocr.recognize_pdf_page(source, content_hash="test", page_number=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: read(), range(2)))
    assert len(calls) == 1
    assert results[0] == results[1]
    result = results[0]
    assert result["text"] == "中文abc"
    assert len(result["characters"]) == 5
    assert result["characters"][0]["box_granularity"] == "character"
    assert result["characters"][2]["box_granularity"] == "word"
    point = pymupdf.Point(30, 60) * pymupdf.Matrix(result["geometry"]["pixel_to_page_matrix"])
    assert point.x == pytest.approx(20)
    assert point.y == pytest.approx(290)
    saved = json.loads(next(source.parent.glob("pdf-ocr/*/*.json")).read_text(encoding="utf-8"))
    assert saved["raw_ocr"]["document"]["flags"]["paddleocr_payload"] == payload
    # Existing recognition gets layout upgrades without running the engine again.
    cache_path = next(source.parent.glob("pdf-ocr/*/*.json"))
    saved.pop("layout")
    cache_path.write_text(json.dumps(saved), encoding="utf-8")
    assert read()["layout"]["blocks"][0]["text"] == "中文abc"
    assert len(calls) == 1
    ocr.recognize_pdf_page(source, content_hash="changed", page_number=1)
    assert len(calls) == 2


def test_failure_is_not_saved_as_empty(source, monkeypatch):
    monkeypatch.setattr(ocr, "run_paddle_ocr_preview", lambda *a, **kw: {})
    with pytest.raises(RuntimeError):
        ocr.recognize_pdf_page(source, content_hash="test", page_number=1)
    assert not list(source.parent.glob("pdf-ocr/*/*.json"))


@pytest.mark.parametrize("page", [0, 2])
def test_invalid_page(source, page):
    with pytest.raises(HTTPException) as exc:
        ocr.recognize_pdf_page(source, content_hash="test", page_number=page)
    assert exc.value.status_code == 422
