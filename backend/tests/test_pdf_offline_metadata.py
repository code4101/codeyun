"""Offline exports keep normal access control, private notes and bounded OCR reads."""
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from backend.api import pdf_documents
from backend.core.library import pdf_ocr, pdf_outline
from backend.models import PdfPageNote
from backend.tests.test_pdf_bookshelf_sharing import _engine, _seed_shared_shelf


def test_offline_metadata_exports_reader_notes_and_existing_ocr_only(tmp_path, monkeypatch):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"test fixture")

    @contextmanager
    def materialize(*_):
        yield source

    monkeypatch.setattr(pdf_documents, "_ensure_pdf_metadata", lambda *_: None)
    monkeypatch.setattr(pdf_documents, "_materialize_pdf_for_metadata", materialize)
    monkeypatch.setattr(pdf_outline, "read_outline", lambda *_: {"entries": [], "revision": "v1", "custom": False, "can_embed": False})
    batches = []

    def cached(_path, _revision, start, end):
        batches.append((start, end))
        return [{"page": page, "available": page == 25, "blocks": [{"text": "already recognized"}]} for page in range(start, end + 1)]

    monkeypatch.setattr(pdf_ocr, "read_cached_pdf_reading_pages", cached)
    monkeypatch.setattr(pdf_ocr, "recognize_pdf_page", lambda *_: pytest.fail("Offline export must not start OCR"))
    with Session(_engine()) as session:
        owner, viewer, outsider, document, _shelf = _seed_shared_shelf(session)
        document.metadata_json = {"page_count": 25, "status": "ready"}
        session.add(document)
        session.add(PdfPageNote(pdf_document_id="101", user_id=owner.id, page_number=1, content_html="owner private note"))
        session.add(PdfPageNote(pdf_document_id="101", user_id=viewer.id, page_number=25, content_html="reader note"))
        session.commit()
        export = pdf_documents.get_pdf_offline_metadata(101, session, viewer)
        validated = pdf_documents.PdfOfflineMetadataPayload.model_validate(export)
        assert validated.document.id == 101
        assert [note.content_html for note in validated.notes] == ["reader note"]
        assert all(not note.can_edit for note in validated.notes)
        assert batches == [(1, 24), (25, 25)]
        assert validated.reading_pages == [{"page": 25, "available": True, "blocks": [{"text": "already recognized"}]}]
        with pytest.raises(HTTPException) as rejected:
            pdf_documents.get_pdf_offline_metadata(101, session, outsider)
        assert rejected.value.status_code in (403, 404)
        with pytest.raises(HTTPException):
            pdf_documents.get_pdf_offline_metadata(101, session, None)
