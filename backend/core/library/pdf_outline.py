"""Editable PDF outlines: small, versioned library edits; explicit PDF embedding."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
import time
from pathlib import Path
from functools import lru_cache

import pymupdf
from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlmodel import select

from backend.core.temp_paths import codeyun_temp_root
from backend.models import LibraryAnnotation, PdfDocument


class OutlineEntry(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    page: int | None = Field(default=None, ge=1)
    level: int = Field(ge=0, le=20)


class OutlineUpdate(BaseModel):
    revision: str
    entries: list[OutlineEntry] = Field(max_length=10000)


def _revision(document):
    metadata = document.metadata_json
    content = metadata.get("outline_base_hash", document.content_hash) if metadata.get("outline_embedded_hash") == document.content_hash else document.content_hash
    payload = [content, metadata.get("editable_outline")]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@lru_cache(maxsize=32)
def _read_outline(path, size, mtime):
    with pymupdf.open(path) as pdf:
        return [{"id": f"bookmark-{i}", "title": title.replace("\x00", "").strip() or "未命名目录",
                 "page": page if page > 0 else None, "level": level - 1}
                for i, (level, title, page) in enumerate(pdf.get_toc())]


def read_outline(session, document):
    from backend.api import pdf_documents as api
    custom = document.metadata_json.get("editable_outline")
    if custom is not None:
        entries = custom
    else:
        with api._materialize_pdf_for_metadata(session, document) as path:
            stat = path.stat()
            entries = _read_outline(os.fspath(path), stat.st_size, stat.st_mtime_ns)
    return {"entries": entries, "revision": _revision(document), "custom": custom is not None,
            "can_embed": document.source_entry_id == api.PDF_HOSTED_ENTRY_ID}


def save_outline(session, document, payload):
    if payload.revision != _revision(document):
        raise HTTPException(409, "目录已被其他窗口修改，请重新加载后再编辑")
    previous_level = -1
    seen = set()
    page_count = document.metadata_json.get("page_count", 0)
    for entry in payload.entries:
        if entry.id in seen or entry.level > previous_level + 1 or not entry.title.strip():
            raise HTTPException(422, "目录层级、标题或节点编号无效")
        if entry.page is not None and entry.page > page_count:
            raise HTTPException(422, "目录目标页超出 PDF 页数")
        seen.add(entry.id)
        previous_level = entry.level
    old = document.metadata_json
    metadata = {**old, "editable_outline": [e.model_dump() for e in payload.entries]}
    if metadata["editable_outline"] == old.get("editable_outline"):
        return read_outline(session, document)
    if document.source_entry_id == "codeyun-pdf-store":
        metadata["outline_sync_after"] = time.time() + 600
    result = session.execute(update(PdfDocument).where(
        PdfDocument.id == document.id, PdfDocument.metadata_json == old,
        PdfDocument.content_hash == document.content_hash,
    ).values(metadata_json=metadata).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "图书已变化，请重新加载目录")
    session.commit()
    session.refresh(document)
    return read_outline(session, document)


def embed_outline(session, document, revision, user):
    from backend.api import pdf_documents as api
    from backend.core.library.pdf_merge import _merge_lock
    with _merge_lock:
        session.refresh(document)
        if revision != _revision(document):
            raise HTTPException(409, "目录已变化，请重新加载目录")
        if document.source_entry_id != api.PDF_HOSTED_ENTRY_ID:
            raise HTTPException(400, "仅托管 PDF 支持写入目录")
        entries = document.metadata_json.get("editable_outline")
        if entries is None:
            return read_outline(session, document)
        source = api._resolve_hosted_pdf_path(document)
        temporary = codeyun_temp_root("pdf_outline") / f"{uuid.uuid4().hex}.pdf"
        toc = [[e["level"] + 1, e["title"], e["page"] if e["page"] is not None else -1] for e in entries]
        try:
            with pymupdf.open(source) as pdf:
                page_count = len(pdf)
                pdf.set_toc(toc)
                pdf.save(temporary)
            with pymupdf.open(temporary) as pdf:
                if len(pdf) != page_count or pdf.get_toc() != toc:
                    raise HTTPException(422, "目录写入校验失败")
            path, size, digest = api._copy_pdf_to_hosted_storage(temporary, user)
        finally:
            temporary.unlink(missing_ok=True)
        old = document.metadata_json
        base_hash = old.get("outline_base_hash", document.content_hash) if old.get("outline_embedded_hash") == document.content_hash else document.content_hash
        metadata = {**old, "source_fingerprint": f"sha256:{digest}",
                    "outline_base_hash": base_hash, "outline_embedded_hash": digest}
        metadata.pop("outline_sync_after", None)
        result = session.execute(update(PdfDocument).where(
            PdfDocument.id == document.id, PdfDocument.metadata_json == old,
            PdfDocument.content_hash == document.content_hash,
        ).values(metadata_json=metadata, source_absolute_path=path, size_bytes=size,
                 content_hash=digest, hash_algorithm="sha256").execution_options(synchronize_session=False))
        if result.rowcount != 1:
            session.rollback()
            if path != os.fspath(source) and session.exec(select(PdfDocument).where(
                    PdfDocument.source_absolute_path == path)).first() is None:
                Path(path).unlink(missing_ok=True)
            raise HTTPException(409, "目录已变化，请重新写入")
        for row in session.exec(select(LibraryAnnotation).where(
            LibraryAnnotation.resource_type == "pdf", LibraryAnnotation.resource_id == str(document.numeric_id))).all():
            row.source_revision = digest
            session.add(row)
        session.commit()
        session.refresh(document)
        result = read_outline(session, document)
        if source != Path(path) and session.exec(select(PdfDocument).where(
                PdfDocument.source_absolute_path == os.fspath(source))).first() is None:
            try:
                source.unlink(missing_ok=True)
            except OSError:
                # Windows may still have an in-flight FileResponse holding it.
                result["cleanup_pending"] = True
        return result
