"""Merge hosted volumes in place, including their library reading data.

The first volume keeps its ID and shelf position. Files are removed only after
the merged file passes page-by-page rendering checks and the DB transaction
commits. Repeating the same request returns the completed merge.
"""
from __future__ import annotations

import os
import threading
import time
import uuid

import pymupdf
from fastapi import HTTPException
from pypdf import PdfReader, PdfWriter
from sqlmodel import Session, select

from backend.core.temp_paths import codeyun_temp_root
from backend.models import (LibraryAnnotation, PdfBookshelfPlacement, PdfDocument,
                            PdfPageNote, PdfUserState, ResourceAccessGrant, User)

_merge_lock = threading.RLock()  # Background outline sync holds this through reader-lease checks.


def remove_pdf_placeholder_outline(session: Session, user: User, pdf_id: int, branch_title: str) -> dict:
    """Remove a flat scanner-generated page-number branch from a hosted PDF.

    Only accepts a branch with at least 20 numeric leaf bookmarks and optional
    cover/title leaves. Real chapter outlines are rejected, not guessed away.
    Page content and annotations are unchanged; the parent entry is retained.
    """
    return _edit_pdf_outline(session, user, pdf_id, branch_title=branch_title)


def flatten_pdf_volume_outline(session: Session, user: User, pdf_id: int, volume_titles: tuple[str, ...]) -> dict:
    """Remove named top-level volume wrappers and promote their child bookmarks.

    All top-level entries must be named wrappers; repeat calls are harmless.
    Page destinations and nested chapter structure are preserved.
    """
    if not volume_titles:
        raise HTTPException(400, "请指定需要移除的分册层级")
    return _edit_pdf_outline(session, user, pdf_id, volume_titles=volume_titles)


def _without_placeholder_branch(toc, branch_title):
    matches = [i for i, item in enumerate(toc) if item[0] == 1 and item[1] == branch_title]
    if len(matches) != 1:
        raise HTTPException(400, "必须匹配唯一分册入口")
    start = matches[0] + 1
    end = next((i for i in range(start, len(toc)) if toc[i][0] == 1), len(toc))
    leaves = toc[start:end]
    if not leaves:
        return toc, []
    labels = [item[1].rstrip("\x00").strip() for item in leaves]
    if (sum(label.isdecimal() for label in labels) < 20
            or any(item[0] != 2 for item in leaves)
            or any(not label.isdecimal() and label not in {"封面", "书名"} for label in labels)):
        raise HTTPException(409, "该分支包含章节目录，不能作为逐页占位书签清除")
    return toc[:start] + toc[end:], leaves


def _edit_pdf_outline(session, user, pdf_id, *, branch_title="", volume_titles=()):
    from backend.api import pdf_documents as api

    with _merge_lock:
        document = api._get_pdf_by_numeric_id_or_404(session, pdf_id)
        if document.owner_user_id != user.id or document.source_entry_id != api.PDF_HOSTED_ENTRY_ID:
            raise HTTPException(403, "只能修改自己的托管 PDF")
        source_path = api._resolve_hosted_pdf_path(document)
        temporary = codeyun_temp_root("pdf_outline") / f"{uuid.uuid4().hex}.pdf"
        try:
            with pymupdf.open(source_path) as source:
                toc = source.get_toc(simple=False)
                if volume_titles:
                    roots = [row for row in toc if row[0] == 1]
                    if not any(row[1] in volume_titles for row in roots):
                        return {"pdf_id": pdf_id, "removed": 0, "outline_count": len(toc)}
                    if any(row[1] not in volume_titles for row in roots):
                        raise HTTPException(409, "顶层包含其他章节，不能统一移除分册层级")
                    leaves = roots
                    kept = [[row[0] - 1, *row[1:]] for row in toc if row[0] > 1]
                else:
                    kept, leaves = _without_placeholder_branch(toc, branch_title)
                if not leaves:
                    return {"pdf_id": pdf_id, "removed": 0, "outline_count": len(toc)}
                source.set_toc(kept)
                source.save(temporary)
            with pymupdf.open(source_path) as original, pymupdf.open(temporary) as edited:
                if len(original) != len(edited):
                    raise ValueError("页数校验失败")
                for a, b in zip(original, edited):
                    if (a.get_pixmap(matrix=pymupdf.Matrix(.5, .5)).digest
                            != b.get_pixmap(matrix=pymupdf.Matrix(.5, .5)).digest
                            or len(list(a.annots() or [])) != len(list(b.annots() or []))):
                        raise ValueError("页面或批注校验失败，保留原文件")
                if edited.get_toc() != [row[:3] for row in kept]:
                    raise ValueError("书签校验失败")
            path, size, digest = api._copy_pdf_to_hosted_storage(temporary, user)
        finally:
            temporary.unlink(missing_ok=True)
        document.source_absolute_path = path
        document.size_bytes = size
        document.content_hash = digest
        document.hash_algorithm = "sha256"
        document.updated_at = time.time()
        document.updated_by_user_id = user.id
        metadata = {**document.metadata_json, "source_fingerprint": api._pdf_metadata_source_fingerprint(document)}
        if "volume_merge" in metadata:
            metadata["volume_merge"] = {**metadata["volume_merge"], "outline_count": len(kept)}
        document.metadata_json = metadata
        session.add(document)
        for annotation in session.exec(select(LibraryAnnotation).where(
                LibraryAnnotation.resource_type == "pdf",
                LibraryAnnotation.resource_id.in_(api._pdf_document_ref_candidates(document)))).all():
            annotation.source_revision = digest
            session.add(annotation)
        session.commit()
        other = session.exec(select(PdfDocument).where(
            PdfDocument.source_absolute_path == os.fspath(source_path), PdfDocument.id != document.id)).first()
        if other is None:
            source_path.unlink(missing_ok=True)
        return {"pdf_id": pdf_id, "removed": len(leaves), "outline_count": len(kept), "path": path}


def merge_hosted_pdf_volumes(session: Session, user: User, pdf_ids: list[int],
                             title: str, volume_titles: list[str]) -> dict:
    """Replace same-owner hosted volumes with one independent, verified PDF.

    Latest per-user reading position wins; later-volume pages are offset. Page
    notes and text annotations retain their IDs. Explicit sharing must match.
    Returns any old file that could not be deleted, rather than hiding failures.
    """
    from backend.api import pdf_documents as api

    if len(pdf_ids) < 2 or len(set(pdf_ids)) != len(pdf_ids) or len(volume_titles) != len(pdf_ids):
        raise HTTPException(400, "至少需要两本不同的 PDF，并提供对应分册名")
    if not title.strip():
        raise HTTPException(400, "合并书名不能为空")
    with _merge_lock:
        primary = api._get_pdf_by_numeric_id_or_404(session, pdf_ids[0])
        if primary.owner_user_id != user.id:
            raise HTTPException(403, "只能合并自己拥有的图书")
        completed = primary.metadata_json.get("volume_merge", {})
        if completed.get("source_ids") == pdf_ids:
            return {"pdf_id": primary.numeric_id, **completed, "path": primary.source_absolute_path}
        documents = [primary] + [api._get_pdf_by_numeric_id_or_404(session, i) for i in pdf_ids[1:]]
        if any(d.owner_user_id != user.id or d.source_entry_id != api.PDF_HOSTED_ENTRY_ID for d in documents):
            raise HTTPException(400, "合并仅支持自己拥有的托管 PDF")
        paths = [api._resolve_hosted_pdf_path(d) for d in documents]
        refs = [api._pdf_document_ref_candidates(d) for d in documents]
        grants = [session.exec(select(ResourceAccessGrant).where(
            ResourceAccessGrant.resource_type == "pdf", ResourceAccessGrant.resource_id.in_(r))).all() for r in refs]
        grant_keys = [{(g.subject_key, g.role) for g in rows} for rows in grants]
        if any(keys != grant_keys[0] for keys in grant_keys):
            raise HTTPException(409, "分册分享权限不同，请先统一权限")

        output = codeyun_temp_root("pdf_merge") / f"{uuid.uuid4().hex}.pdf"
        counts, offsets = [], []
        writer = PdfWriter()
        try:
            for path, label in zip(paths, volume_titles):
                offsets.append(sum(counts))
                reader = PdfReader(path)
                counts.append(len(reader.pages))
                writer.append(reader, outline_item=label, import_outline=True)
            writer.add_metadata({"/Title": title, "/Author": api._pdf_display_author(primary)})
            writer.write(output)
            writer.close()
            # Compare every rendered page, including embedded annotation appearances,
            # before allowing any changes to library records or source files.
            with pymupdf.open(output) as merged:
                if len(merged) != sum(counts):
                    raise ValueError("合并页数校验失败")
                for path, offset in zip(paths, offsets):
                    with pymupdf.open(path) as source:
                        for index, page in enumerate(source):
                            target = merged[index + offset]
                            a = page.get_pixmap(matrix=pymupdf.Matrix(0.5, 0.5))
                            b = target.get_pixmap(matrix=pymupdf.Matrix(0.5, 0.5))
                            if (a.width, a.height, a.digest) != (b.width, b.height, b.digest):
                                raise ValueError(f"第 {index + offset + 1} 页渲染不一致，保留原文件")
                            if len(list(page.annots() or [])) != len(list(target.annots() or [])):
                                raise ValueError("PDF 批注数量不一致，保留原文件")
                outline_count = len(merged.get_toc())
            hosted_path, size, digest = api._copy_pdf_to_hosted_storage(output, user)
        finally:
            writer.close()
            output.unlink(missing_ok=True)

        target_ref = str(primary.numeric_id)
        states_by_user, placements_by_user = {}, {}
        migrated_notes = migrated_annotations = 0
        for refs_for_volume, offset in zip(refs, offsets):
            for state in session.exec(select(PdfUserState).where(PdfUserState.pdf_document_id.in_(refs_for_volume))).all():
                states_by_user.setdefault(state.user_id, []).append((state, offset))
            for placement in session.exec(select(PdfBookshelfPlacement).where(PdfBookshelfPlacement.pdf_document_id.in_(refs_for_volume))).all():
                placements_by_user.setdefault(placement.user_id, []).append(placement)
            for note in session.exec(select(PdfPageNote).where(PdfPageNote.pdf_document_id.in_(refs_for_volume))).all():
                note.pdf_document_id = target_ref
                note.page_number += offset
                session.add(note)
                migrated_notes += 1
            for annotation in session.exec(select(LibraryAnnotation).where(
                    LibraryAnnotation.resource_type == "pdf", LibraryAnnotation.resource_id.in_(refs_for_volume))).all():
                if not annotation.chapter_id.startswith("page:"):
                    raise HTTPException(409, "PDF 批注页码无法迁移")
                annotation.chapter_id = f"page:{int(annotation.chapter_id[5:]) + offset}"
                annotation.resource_id = target_ref
                annotation.source_revision = digest
                session.add(annotation)
                migrated_annotations += 1
        # Delete colliding per-user rows before changing their unique resource keys.
        winners = []
        for rows in states_by_user.values():
            winner, offset = max(rows, key=lambda item: item[0].updated_at)
            for state, _ in rows:
                if state is not winner:
                    session.delete(state)
            winners.append((winner, offset))
        for rows in placements_by_user.values():
            for row in rows[1:]:
                session.delete(row)
        session.flush()
        for state, offset in winners:
            state.pdf_document_id = target_ref
            state.current_page += offset
            state.state_json = {**state.state_json, "expanded_outline_ids": []}
            session.add(state)
        for rows in placements_by_user.values():
            rows[0].pdf_document_id = target_ref
            session.add(rows[0])
        for rows in grants[1:]:
            for row in rows:
                session.delete(row)
        for document in documents[1:]:
            session.delete(document)
        primary.source_absolute_path = hosted_path
        primary.source_device_file_id = None
        primary.content_hash = digest
        primary.hash_algorithm = "sha256"
        primary.size_bytes = size
        primary.title = title + ".pdf"
        primary.updated_at = time.time()
        primary.updated_by_user_id = user.id
        result = {"source_ids": pdf_ids, "page_count": sum(counts), "offsets": offsets,
                  "outline_count": outline_count, "page_notes": migrated_notes,
                  "text_annotations": migrated_annotations}
        metadata = {**primary.metadata_json, "volume_merge": result,
                    "imported_filename": primary.title,
                    "source_fingerprint": api._pdf_metadata_source_fingerprint(primary),
                    "page_count": sum(counts)}
        metadata["title_naming"] = {**metadata.get("title_naming", {}), "display_title": title,
            "display_volume": "", "source": "manual", "status": "ready",
            "source_fingerprint": api._pdf_title_source_fingerprint(primary)}
        primary.metadata_json = metadata
        session.add(primary)
        session.commit()
        remaining = []
        # Resolve remaining references too: a legacy absolute path may refer to
        # the same relocated content-addressed file.
        referenced = set()
        for document in session.exec(select(PdfDocument).where(PdfDocument.source_entry_id == api.PDF_HOSTED_ENTRY_ID)).all():
            try:
                referenced.add(api._resolve_hosted_pdf_path(document))
            except HTTPException:
                continue
        for path in set(paths) - referenced:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                remaining.append(os.fspath(path))
        return {"pdf_id": primary.numeric_id, **result, "path": hosted_path, "undeleted_files": remaining}
