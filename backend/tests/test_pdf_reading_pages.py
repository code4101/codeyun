import json
import pytest
from backend.core.library import pdf_ocr


def test_cached_reading_is_bounded_and_never_recognizes(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_ocr, "pdf_ocr_cache_directory", lambda *_: tmp_path)
    monkeypatch.setattr(pdf_ocr, "recognize_pdf_page", lambda *a, **k: pytest.fail("must not OCR"))
    raw = {"geometry": {"height": 1000}, "lines": [
        {"line_id": "a", "text": "这是用来验证缓存升级的正常正文内容", "x": 50, "y": 300, "w": 400, "h": 20}],
        "layout": {"version": 0}, "raw_ocr": {"large": "payload"}}
    (tmp_path / "1.json").write_text(json.dumps(raw), encoding="utf-8")
    result = pdf_ocr.read_cached_pdf_reading_pages(tmp_path, "revision", 1, 2)
    assert result[0]["available"]
    assert result[0]["blocks"][0]["text"] == raw["lines"][0]["text"]
    assert "raw_ocr" not in result[0]
    assert result[1] == {"page": 2, "available": False, "blocks": []}
    assert json.loads((tmp_path / "1.json").read_text())["layout"]["version"] == 0
    with pytest.raises(ValueError):
        pdf_ocr.read_cached_pdf_reading_pages(tmp_path, "revision", 1, 25)
