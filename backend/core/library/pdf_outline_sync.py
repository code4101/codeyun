"""Coalesced outline writes: durable due time, one file per minute, no active readers.

Readers renew a cheap in-memory lease. Twenty quiet minutes outlive existing
15-minute content tokens; startup uses the same grace period after losing leases.
Failures leave the saved outline intact and retry after thirty minutes.
"""
import logging
import threading
import time

from sqlmodel import Session, select
from sqlalchemy import update

from backend.models import PdfDocument, User
from backend.core.library.pdf_outline import embed_outline, read_outline
from backend.core.library.pdf_merge import _merge_lock

_readers: dict[int, float] = {}
_stop = threading.Event()
_thread: threading.Thread | None = None
READER_GRACE = 20 * 60


def renew_outline_reader(document_id: int) -> None:
    with _merge_lock:
        now = time.time()
        for key, seen in list(_readers.items()):
            if now - seen > READER_GRACE:
                _readers.pop(key, None)
        _readers[document_id] = now


def sync_pending_outline(session: Session, *, now: float | None = None) -> bool:
    """Synchronize at most one due, unread hosted document. False means no work."""
    now = time.time() if now is None else now
    documents = session.exec(select(PdfDocument).where(
        PdfDocument.source_entry_id == "codeyun-pdf-store",
        PdfDocument.metadata_json["outline_sync_after"].as_float() <= now,
    ).order_by(PdfDocument.id)).all()
    for document in documents:
        with _merge_lock:
            if now - _readers.get(document.id, 0) < READER_GRACE:
                continue
            try:
                owner = session.get(User, document.owner_user_id)
                if owner is None:
                    raise RuntimeError("PDF owner no longer exists")
                embed_outline(session, document, read_outline(session, document)["revision"], owner)
            except Exception:
                session.rollback()
                logging.getLogger(__name__).exception("Deferred PDF outline sync failed: %s", document.id)
                session.refresh(document)
                old = document.metadata_json
                if "outline_sync_after" in old:
                    session.execute(update(PdfDocument).where(PdfDocument.id == document.id,
                        PdfDocument.metadata_json == old).values(metadata_json={**old, "outline_sync_after": now + 1800}))
                    session.commit()
            return True
    return False


def start_outline_sync() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    def run():
        from backend.db import engine
        if _stop.wait(READER_GRACE):
            return
        while not _stop.is_set():
            try:
                with Session(engine) as session:
                    sync_pending_outline(session)
            except Exception:
                logging.getLogger(__name__).exception("PDF outline sync scan failed")
            _stop.wait(60)
    _thread = threading.Thread(target=run, name="pdf-outline-sync", daemon=True)
    _thread.start()


def stop_outline_sync() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=5)
