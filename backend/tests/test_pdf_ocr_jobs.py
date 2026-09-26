from types import SimpleNamespace

import pymupdf
import pytest
from sqlmodel import Session, create_engine

from backend.models import AppSetting, PdfDocument, User
from backend.core.library import pdf_ocr_jobs as jobs
from backend.core.library import pdf_ocr as ocr


@pytest.fixture
def book(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    for model in (User, PdfDocument, AppSetting):
        model.__table__.create(engine)
    source = tmp_path / "book.pdf"
    with pymupdf.open() as pdf:
        for _ in range(3):
            pdf.new_page()
        pdf.save(source)
    from backend.api import pdf_documents as api
    from backend.core.library import pdf_outline_sync
    monkeypatch.setattr(api, "_resolve_hosted_pdf_path", lambda doc: source)
    monkeypatch.setattr(pdf_outline_sync, "renew_outline_reader", lambda id: None)
    clock = [10000.0]
    monkeypatch.setattr(jobs, "time", SimpleNamespace(time=lambda: clock[0], monotonic=lambda: clock[0]))
    completed = {2}
    monkeypatch.setattr(jobs, "cached_pdf_pages", lambda *args: set(completed))
    calls = []
    def recognize(path, *, content_hash, page_number, background):
        assert background
        calls.append(page_number)
        completed.add(page_number)
        return {}
    monkeypatch.setattr(jobs, "recognize_pdf_page", recognize)
    with Session(engine) as session:
        session.add(User(id=1, username="owner", hashed_password="test"))
        session.add(PdfDocument(id=1, numeric_id=1, owner_user_id=1, source_entry_id="codeyun-pdf-store",
            source_absolute_path=str(source), content_hash="test", metadata_json={"page_count":3}))
        session.commit()
    def control(action):
        with Session(engine) as session:
            return jobs.control_book_ocr_job(session, session.get(PdfDocument, 1), action)
    def status():
        with Session(engine) as session:
            return jobs.get_book_ocr_job(session, session.get(PdfDocument, 1))
    yield engine, clock, completed, calls, control, status
    engine.dispose()


def test_resume_cache_skip_and_idempotency(book):
    engine, clock, completed, calls, control, status = book
    assert control("start")["completed"] == 1
    assert control("start")["completed"] == 1
    assert jobs.process_book_ocr_page(engine) == 0
    assert calls == [1]
    # A fresh DB session resumes the persisted cursor and doesn't reread completed pages.
    assert status()["completed"] == 2
    assert control("pause")["status"] == "paused"
    clock[0] += 100
    jobs.process_book_ocr_page(engine)
    assert calls == [1]
    control("resume")
    jobs.process_book_ocr_page(engine)
    assert calls == [1, 3]
    assert status()["status"] == "completed"
    assert control("start")["status"] == "completed"


def test_pause_during_inflight_page_is_preserved(book, monkeypatch):
    engine, clock, completed, calls, control, status = book
    control("start")
    original = jobs.recognize_pdf_page
    def recognize(*args, **kwargs):
        control("pause")
        return original(*args, **kwargs)
    monkeypatch.setattr(jobs, "recognize_pdf_page", recognize)
    jobs.process_book_ocr_page(engine)
    assert status()["status"] == "paused"
    assert status()["completed"] == 2
    control("cancel")
    assert status()["status"] == "cancelled"
    assert completed == {1, 2}


def test_failure_retries_are_bounded_and_foreground_defer_is_not_failure(book, monkeypatch):
    engine, clock, completed, calls, control, status = book
    control("start")
    def deferred(*args, **kwargs):
        raise ocr.OcrBackgroundDeferred()
    monkeypatch.setattr(jobs, "recognize_pdf_page", deferred)
    assert jobs.process_book_ocr_page(engine) == 3
    assert status()["failed"] == 0
    def fail(*args, **kwargs):
        calls.append(kwargs["page_number"])
        raise OSError("offline")
    monkeypatch.setattr(jobs, "recognize_pdf_page", fail)
    for _ in range(8):
        jobs.process_book_ocr_page(engine)
        clock[0] += 100
    assert calls.count(1) == calls.count(3) == 3
    assert status()["status"] == "failed"
    assert status()["completed"] == 1


def test_source_change_cancels_old_work(book):
    engine, clock, completed, calls, control, status = book
    control("start")
    with Session(engine) as session:
        doc = session.get(PdfDocument, 1)
        doc.content_hash = "replaced"
        session.add(doc)
        session.commit()
    jobs.process_book_ocr_page(engine)
    assert not calls
    assert status()["status"] == "idle"


def test_background_cannot_enter_while_foreground_is_running(tmp_path, monkeypatch):
    source = tmp_path / "file.pdf"
    source.write_bytes(b"test")
    monkeypatch.setattr(ocr, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    def recognize(*args, **kwargs):
        with pytest.raises(ocr.OcrBackgroundDeferred):
            ocr.recognize_pdf_page(source, content_hash="test", page_number=2, background=True)
        return {"ok": True}
    monkeypatch.setattr(ocr, "_recognize_pdf_page", recognize)
    assert ocr.recognize_pdf_page(source, content_hash="test", page_number=1) == {"ok": True}


def test_successive_pages_need_no_clock_advance(book):
    engine, clock, completed, calls, control, status = book
    control("start")
    assert jobs.process_book_ocr_page(engine) == 0
    assert jobs.process_book_ocr_page(engine) == 0
    assert calls == [1, 3]
    assert status()["status"] == "completed"
