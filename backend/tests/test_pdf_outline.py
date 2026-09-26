from types import SimpleNamespace

import pymupdf
import pytest
from fastapi import HTTPException
from sqlmodel import Session, create_engine

from backend.api import pdf_documents as api
from backend.core.library.pdf_outline import OutlineEntry, OutlineUpdate, read_outline, save_outline, embed_outline
from backend.models import PdfDocument, User, LibraryAnnotation


@pytest.fixture
def book(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    for model in (User, PdfDocument, LibraryAnnotation):
        model.__table__.create(engine)
    monkeypatch.setattr(api, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    path = tmp_path / "source.pdf"
    with pymupdf.open() as pdf:
        for i in range(3):
            pdf.new_page().insert_text((40, 40), f"page {i}")
        pdf[0].add_text_annot((60, 60), "preserve me")
        pdf.set_toc([[1, "Original", 1]])
        pdf.save(path)
    with Session(engine) as session:
        user = User(id=1, username="owner", hashed_password="test")
        doc = PdfDocument(id=1, numeric_id=1, owner_user_id=1, source_entry_id=api.PDF_HOSTED_ENTRY_ID,
                          source_absolute_path=str(path), content_hash="old", metadata_json={"page_count": 3})
        session.add(user)
        session.add(doc)
        session.commit()
        yield session, user, doc, path


def test_edit_is_small_and_embedding_preserves_content(book):
    session, user, doc, source = book
    original_bytes = source.read_bytes()
    state = read_outline(session, doc)
    assert state["entries"][0]["title"] == "Original"
    entries = [OutlineEntry(id="a", title="Part", page=1, level=0),
               OutlineEntry(id="b", title="New chapter", page=3, level=1)]
    updated = save_outline(session, doc, OutlineUpdate(revision=state["revision"], entries=entries))
    assert source.read_bytes() == original_bytes
    assert updated["entries"][1]["page"] == 3
    with pytest.raises(HTTPException) as error:
        save_outline(session, doc, OutlineUpdate(revision=state["revision"], entries=[]))
    assert error.value.status_code == 409
    embedded = embed_outline(session, doc, updated["revision"], user)
    assert embedded["revision"] != updated["revision"]
    with pymupdf.open(doc.source_absolute_path) as output, pymupdf.open(stream=original_bytes, filetype="pdf") as old:
        assert output.get_toc() == [[1, "Part", 1], [2, "New chapter", 3]]
        assert len(list(output[0].annots())) == 1
        assert [p.get_pixmap().digest for p in output] == [p.get_pixmap().digest for p in old]


@pytest.mark.parametrize("entries", [
    [dict(id="a", title="Bad", page=1, level=1)],
    [dict(id="a", title="Bad", page=4, level=0)],
    [dict(id="a", title=" ", page=1, level=0)],
    [dict(id="a", title="A", page=1, level=0), dict(id="a", title="B", page=2, level=0)],
])
def test_invalid_outline_rejected(book, entries):
    session, user, doc, source = book
    state = read_outline(session, doc)
    with pytest.raises(HTTPException) as error:
        save_outline(session, doc, OutlineUpdate(revision=state["revision"], entries=entries))
    assert error.value.status_code == 422
    assert "editable_outline" not in doc.metadata_json


def test_empty_outline_is_intentional(book):
    session, user, doc, source = book
    state = read_outline(session, doc)
    saved = save_outline(session, doc, OutlineUpdate(revision=state["revision"], entries=[]))
    embed_outline(session, doc, saved["revision"], user)
    with pymupdf.open(doc.source_absolute_path) as pdf:
        assert pdf.get_toc() == []
