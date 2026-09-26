from types import SimpleNamespace

import pymupdf
from sqlmodel import Session, create_engine, select

from backend.api import pdf_documents as api
from backend.core.library.pdf_merge import merge_hosted_pdf_volumes
from backend.models import (LibraryAnnotation, PdfBookshelfPlacement, PdfDocument,
                            PdfPageNote, PdfUserState, ResourceAccessGrant, User)


def test_merge_preserves_pages_notes_progress_and_removes_sources(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    models = (User, PdfDocument, PdfUserState, PdfPageNote, PdfBookshelfPlacement, ResourceAccessGrant, LibraryAnnotation)
    for model in models:
        model.__table__.create(engine)
    monkeypatch.setattr(api, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    with Session(engine) as session:
        user = User(id=2, username="reader", hashed_password="test")
        session.add(user)
        paths = []
        for number in (1, 2):
            path = tmp_path / f"volume{number}.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page()
                page.insert_text((70, 70), f"Volume {number}")
                page.add_text_annot((90, 90), f"note {number}")
                pdf.set_toc([[1, "Chapter", 1]])
                pdf.save(path)
            paths.append(path)
            session.add(PdfDocument(id=number, numeric_id=number, owner_user_id=2,
                source_entry_id=api.PDF_HOSTED_ENTRY_ID, source_absolute_path=str(path)))
            session.add(PdfUserState(pdf_document_id=str(number), user_id=2, current_page=1, updated_at=number))
            session.add(PdfPageNote(pdf_document_id=str(number), user_id=2, page_number=1, content_html=f"note {number}"))
            session.add(PdfBookshelfPlacement(pdf_document_id=str(number), user_id=2, shelf_index=number))
            session.add(LibraryAnnotation(resource_type="pdf", resource_id=str(number), user_id=2, chapter_id="page:1", quote_text="Volume"))
        session.commit()
        result = merge_hosted_pdf_volumes(session, user, [1, 2], "Combined", ["Upper", "Lower"])
        assert result["page_count"] == 2
        assert result["outline_count"] == 4
        assert result["undeleted_files"] == []
        assert all(not path.exists() for path in paths)
        assert session.get(PdfDocument, 2) is None
        state = session.exec(select(PdfUserState)).one()
        assert (state.pdf_document_id, state.current_page) == ("1", 2)
        assert sorted(n.page_number for n in session.exec(select(PdfPageNote))) == [1, 2]
        assert {a.chapter_id for a in session.exec(select(LibraryAnnotation))} == {"page:1", "page:2"}
        assert session.exec(select(PdfBookshelfPlacement)).one().shelf_index == 1
        assert merge_hosted_pdf_volumes(session, user, [1, 2], "Combined", ["Upper", "Lower"])["path"] == result["path"]
