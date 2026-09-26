import json
from types import SimpleNamespace

from backend.core.library import pdf_ocr, pdf_search


def test_search_scopes_paragraph_boundaries_coverage_and_pagination(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_ocr, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    source = tmp_path / "source.pdf"
    source.write_bytes(b"not read by search")
    folder = pdf_ocr.pdf_ocr_cache_directory(source, "revision")
    folder.mkdir(parents=True)
    def save(page, lines, groups):
        (folder / f"{page}.json").write_text(json.dumps({
            "lines": [{"line_id":str(i),"text":text} for i,text in enumerate(lines)],
            "layout":{"blocks":[{"line_ids":ids} for ids in groups]},
        }), encoding="utf-8")
    save(1, ["惟", "海", "下一段"], [["0","1"],["2"]])
    save(3, ["惟海" * 60], [["0"]])
    result = pdf_search.search_pdf_ocr(source, "revision", "惟海", 1, 5)
    assert result["total"] == 61 and len(result["hits"]) == 50
    assert result["indexed_pages"] == 2 and result["scope_pages"] == 5
    assert result["hits"][0]["page"] == 1
    assert result["hits"][1]["occurrence"] == 0
    assert pdf_search.search_pdf_ocr(source,"revision","海下一",1,5)["total"] == 0
    assert pdf_search.search_pdf_ocr(source,"revision","惟海",1,2)["total"] == 1
    more = pdf_search.search_pdf_ocr(source,"revision","惟海",1,5,50)
    assert len(more["hits"]) == 11 and more["hits"][0]["occurrence"] == 49
    assert len(list(folder.glob("*.search.json"))) == 2
    assert pdf_search.search_pdf_ocr(source,"different","惟海",1,5)["indexed_pages"] == 0
    # Newly recognized pages and changed layouts become searchable without restarting.
    save(1,["替换文字"],[["0"]])
    assert pdf_search.search_pdf_ocr(source,"revision","替换",1,1)["total"] == 1
