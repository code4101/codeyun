from types import SimpleNamespace

from backend.api import pdf_documents
from backend.models import PdfDocument


def _document_with_manual_metadata(*, display_title: str, display_author: str = "") -> PdfDocument:
    document = PdfDocument(
        numeric_id=101,
        title="20260721 原始文件名.pdf",
        owner_user_id=7,
        metadata_json={"pdf_author": "内嵌作者"},
    )
    document.metadata_json["title_naming"] = {
        "schema_version": pdf_documents.PDF_TITLE_NAMING_SCHEMA_VERSION,
        "source_fingerprint": pdf_documents._pdf_title_source_fingerprint(document),
        "display_title": display_title,
        "display_author": display_author,
        "status": "ready",
        "source": "manual",
    }
    return document


def test_manual_pdf_metadata_is_preserved_and_skips_ai_generation():
    document = _document_with_manual_metadata(
        display_title="3.0 软件工程（第三版）",
        display_author="用户填写作者",
    )

    assert pdf_documents._pdf_display_title(document) == "3.0 软件工程（第三版）"
    assert pdf_documents._pdf_display_author(document) == "用户填写作者"
    assert pdf_documents._prepare_pdf_display_title_generation(None, [document]) == []


def test_pdf_display_title_removes_original_book_edition_prefix():
    document = _document_with_manual_metadata(
        display_title="宏观经济学（原书第9版）",
        display_author="罗宾·巴德",
    )

    assert pdf_documents._pdf_display_title(document) == "宏观经济学（第9版）"
    assert pdf_documents._sanitize_pdf_display_title(
        "宏观经济学（原书第9版）",
        "宏观经济学.pdf",
    ) == "宏观经济学（第9版）"


def test_manual_empty_author_does_not_fall_back_to_embedded_pdf_author():
    document = _document_with_manual_metadata(display_title="人工书名", display_author="")

    assert pdf_documents._pdf_display_author(document) == ""


def test_update_pdf_metadata_marks_user_values_as_manual(monkeypatch):
    document = PdfDocument(
        numeric_id=101,
        title="原始文件名.pdf",
        owner_user_id=7,
        metadata_json={"page_count": 12},
    )
    access = pdf_documents._build_resource_access("manager", SimpleNamespace(id=7))

    class FakeSession:
        def add(self, value):
            assert value is document

        def commit(self):
            pass

        def refresh(self, value):
            assert value is document

    monkeypatch.setattr(
        pdf_documents,
        "_get_pdf_document_or_404",
        lambda *_args, **_kwargs: (document, access),
    )
    monkeypatch.setattr(
        pdf_documents,
        "_serialize_pdf_detail",
        lambda *_args, **_kwargs: {"display_title": pdf_documents._pdf_display_title(document)},
    )

    result = pdf_documents.update_pdf_document_metadata(
        101,
        pdf_documents.PdfMetadataUpdateRequest(
            display_title="  用户 自定义书名  ",
            display_author="作者甲",
            start_date="2008-01",
        ),
        session=FakeSession(),
        current_user=SimpleNamespace(id=7),
    )

    naming = document.metadata_json["title_naming"]
    assert naming["source"] == "manual"
    assert naming["display_title"] == "用户 自定义书名"
    assert naming["display_author"] == "作者甲"
    assert document.metadata_json["start_date"] == "2008-01"
    assert document.metadata_json["library_appearance"] == {
        "cover_color_override": None,
    }
    assert result == {"display_title": "用户 自定义书名"}
