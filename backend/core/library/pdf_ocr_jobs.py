"""Durable whole-book OCR intent, consumed one page at a time by a single worker.

Only pending pages are rendered. Pause/cancel take effect between pages; successful
page files survive every control action and process restart. The worker rests at
least ten seconds, or three times the previous page duration, between pages.
"""
import logging
import threading
import time
import uuid

import pymupdf
from fastapi import HTTPException
from sqlmodel import Session, select

from backend.models import AppSetting, PdfDocument
from backend.core.library.pdf_ocr import (
    cached_pdf_pages, pdf_visual_revision, recognize_pdf_page, OcrBackgroundDeferred,
)

PREFIX = "pdf-ocr-job:"
_lock = threading.RLock()
_stop = threading.Event()
_thread = None


def _save(session, key, state):
    row = session.get(AppSetting, key) or AppSetting(key=key)
    row.value = dict(state)
    row.updated_at = time.time()
    session.add(row)
    session.commit()


def _public(state):
    return {k: state.get(k) for k in ("status", "total", "current_page", "error", "updated_at")} | {
        "completed": len(state.get("done", [])), "failed": len(state.get("failures", {})),
    }


def get_book_ocr_job(session, document):
    row = session.get(AppSetting, PREFIX + str(document.id))
    if not row or row.value.get("revision") != pdf_visual_revision(document):
        return {"status": "idle", "total": 0, "completed": 0, "failed": 0, "current_page": None, "error": None}
    return _public(row.value)


def control_book_ocr_job(session, document, action: str):
    """Idempotent controls; caller must authorize document manager access."""
    from backend.api.pdf_documents import _resolve_hosted_pdf_path
    if action not in {"start", "pause", "resume", "cancel"}:
        raise HTTPException(422, "无效的 OCR 操作")
    if document.source_entry_id != "codeyun-pdf-store":
        raise HTTPException(422, "全书 OCR 暂仅支持已上传的 PDF")
    with _lock:
        key = PREFIX + str(document.id)
        row = session.get(AppSetting, key)
        if row:
            session.refresh(row)
        state = dict(row.value) if row else {}
        revision = pdf_visual_revision(document)
        if action in {"pause", "cancel"}:
            if state.get("revision") == revision and state.get("status") in {"running", "paused"}:
                state["status"] = "paused" if action == "pause" else "cancelled"
                state["updated_at"] = time.time()
                _save(session, key, state)
            return get_book_ocr_job(session, document)
        if state.get("revision") == revision and state.get("status") == "running":
            return _public(state)
        source = _resolve_hosted_pdf_path(document)
        with pymupdf.open(source) as pdf:
            total = len(pdf)
        done = sorted(p for p in cached_pdf_pages(source, revision) if 1 <= p <= total)
        state = {"generation": uuid.uuid4().hex, "document_id": document.id, "revision": revision,
                 "status": "completed" if len(done) == total else "running", "total": total,
                 "done": done, "failures": {}, "current_page": None, "error": None,
                 "next_at": time.time(), "updated_at": time.time()}
        _save(session, key, state)
        return _public(state)


def process_book_ocr_page(db_engine) -> float:
    """One resumable unit of work; return the minimum delay before the next page."""
    from backend.api.pdf_documents import _resolve_hosted_pdf_path
    from backend.core.library.pdf_outline_sync import renew_outline_reader
    with _lock, Session(db_engine) as session:
        rows = session.exec(select(AppSetting).where(AppSetting.key.startswith(PREFIX),
            AppSetting.value["status"].as_string() == "running").order_by(AppSetting.updated_at)).all()
        chosen = None
        for row in rows:
            state = dict(row.value)
            if state.get("error") and state.get("next_at", 0) > time.time():
                continue
            document = session.get(PdfDocument, state["document_id"])
            if not document or pdf_visual_revision(document) != state["revision"]:
                state.update(status="cancelled", error="PDF 内容已变化，请重新启动", updated_at=time.time())
                _save(session, row.key, state)
                continue
            try:
                # Keep outline rewrites away from an in-flight recognition page.
                renew_outline_reader(document.id)
                session.refresh(document)
                if pdf_visual_revision(document) != state["revision"]:
                    state.update(status="cancelled", error="PDF 内容已变化，请重新启动", updated_at=time.time())
                    _save(session, row.key, state)
                    continue
                source = _resolve_hosted_pdf_path(document)
                done = set(state["done"]) | {p for p in cached_pdf_pages(source, state["revision"]) if 1 <= p <= state["total"]}
            except Exception:
                state.update(status="failed", error="PDF 文件暂不可用", updated_at=time.time())
                _save(session, row.key, state)
                continue
            state["done"] = sorted(done)
            failures = {k: v for k, v in state["failures"].items() if int(k) not in done}
            state["failures"] = failures
            page = next((p for p in range(1, state["total"] + 1) if p not in done and str(p) not in failures), None)
            if page is None:
                page = next((int(k) for k, v in failures.items() if v < 3), None)
            if page is None:
                state.update(status="completed" if not failures else "failed", current_page=None, updated_at=time.time())
                _save(session, row.key, state)
                continue
            state.update(current_page=page, updated_at=time.time())
            _save(session, row.key, state)
            chosen = (row.key, state, source, page)
            break
    if chosen is None:
        return 5
    key, state, source, page = chosen
    error = None
    try:
        recognize_pdf_page(source, content_hash=state["revision"], page_number=page, background=True)
    except OcrBackgroundDeferred:
        return 3
    except Exception:
        logging.getLogger(__name__).exception("Whole-book OCR page failed: %s page %s", key, page)
        error = f"第 {page} 页识别失败，稍后重试"
    # Successful pages run back-to-back; only failures need retry backoff.
    delay = 10 if error else 0
    with _lock, Session(db_engine) as session:
        row = session.get(AppSetting, key)
        if row and row.value.get("generation") == state["generation"]:
            latest = dict(row.value)  # Preserve pause/cancel received during recognition.
            if error:
                failures = dict(latest["failures"])
                failures[str(page)] = failures.get(str(page), 0) + 1
                latest["failures"] = failures
            else:
                latest["done"] = sorted(set(latest["done"]) | {page})
                latest["failures"] = {k:v for k,v in latest["failures"].items() if k != str(page)}
            if latest["status"] == "running" and len(latest["done"]) == latest["total"]:
                latest["status"] = "completed"
            latest.update(current_page=None, error=error, next_at=time.time() + delay, updated_at=time.time())
            _save(session, key, latest)
    return delay


def start_book_ocr_worker():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    def run():
        from backend.db import engine
        while not _stop.is_set():
            try:
                delay = process_book_ocr_page(engine)
            except Exception:
                logging.getLogger(__name__).exception("Whole-book OCR worker failed")
                delay = 30
            if _stop.wait(delay):
                return
    _thread = threading.Thread(target=run, name="pdf-book-ocr", daemon=True)
    _thread.start()


def stop_book_ocr_worker():
    _stop.set()
    if _thread:
        _thread.join(timeout=5)
