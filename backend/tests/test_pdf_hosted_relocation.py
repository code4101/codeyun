from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import pdf_documents as pdf
from backend.models import PdfDocument


def relocated_document(tmp_path, monkeypatch):
    root = tmp_path / "current-data"
    monkeypatch.setattr(pdf, "get_settings", lambda: SimpleNamespace(data_dir=root))
    document = PdfDocument(
        owner_user_id=2,
        title="book.pdf",
        source_entry_id=pdf.PDF_HOSTED_ENTRY_ID,
        source_absolute_path=str(tmp_path / "old-data" / "book.pdf"),
        content_hash="a" * 64,
        hash_algorithm="sha256",
    )
    target = root / "pdf-documents" / "user_2" / f"{document.content_hash}.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"%PDF-1.7\n")
    return document, target


def test_content_endpoint_reads_relocated_hosted_file(tmp_path, monkeypatch):
    document, target = relocated_document(tmp_path, monkeypatch)
    monkeypatch.setattr(pdf, "_decode_pdf_content_token", lambda *_: document)
    response = pdf.get_pdf_content(1, request=None, token="test", session=None)
    assert response.path == str(target.resolve())


def test_cached_metadata_also_repairs_persisted_location(tmp_path, monkeypatch):
    document, target = relocated_document(tmp_path, monkeypatch)
    document.metadata_json = {
        "schema_version": pdf.PDF_METADATA_SCHEMA_VERSION,
        "source_fingerprint": pdf._pdf_metadata_source_fingerprint(document),
        "status": "ready",
        "page_count": 532,
    }
    saved = []
    commits = []
    session = SimpleNamespace(add=saved.append, commit=lambda: commits.append(True), refresh=lambda _: None)
    pdf._ensure_pdf_metadata(session, [document])
    assert document.source_absolute_path == str(target.resolve())
    assert document.metadata_json["page_count"] == 532
    assert saved == [document]
    assert commits == [True]


@pytest.mark.parametrize("invalid_source", ["missing", "other-owner", "invalid-hash"])
def test_relocation_does_not_allow_arbitrary_external_paths(tmp_path, monkeypatch, invalid_source):
    document, target = relocated_document(tmp_path, monkeypatch)
    if invalid_source == "missing":
        target.unlink()
    elif invalid_source == "other-owner":
        document.owner_user_id = 3
    else:
        document.content_hash = "../../outside"
    with pytest.raises(HTTPException) as error:
        pdf._resolve_hosted_pdf_path(document)
    assert error.value.status_code == 400


def test_legacy_in_root_file_remains_readable(tmp_path, monkeypatch):
    document, target = relocated_document(tmp_path, monkeypatch)
    document.content_hash = None
    document.source_absolute_path = str(target)
    assert pdf._resolve_hosted_pdf_path(document) == target.resolve()
