"""Persistent, page-scoped OCR, including native character geometry.

Coordinates use the rendered (rotated) page's top-left origin. The stored affine
transform maps image pixels to unrotated PyMuPDF page points. Native word boxes
are never misrepresented as exact individual character boxes.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

import pymupdf
from fastapi import HTTPException

from backend.core.ocr.preview import run_paddle_ocr_preview
from backend.core.ocr.spatial_document import extract_ocr_spatial_document
from backend.core.ocr.reading_layout import build_reading_layout, LAYOUT_VERSION
from backend.core.settings import get_settings
from backend.core.temp_paths import codeyun_temp_root

_locks = [threading.Lock() for _ in range(32)]
SCHEMA_VERSION = 1
_admission = threading.Condition()
_busy = False
_foreground_waiting = 0
_foreground_at = 0.0


class OcrBackgroundDeferred(Exception):
    """The next background page must yield to interactive PDF recognition."""


def pdf_visual_revision(document) -> str:
    metadata = document.metadata_json
    return (metadata.get("outline_base_hash", document.content_hash)
            if metadata.get("outline_embedded_hash") == document.content_hash else document.content_hash) or ""


def pdf_ocr_cache_directory(source: Path, content_hash: str) -> Path:
    stat = source.stat()
    identity = content_hash or f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    key = hashlib.sha256(f"{SCHEMA_VERSION}:{identity}".encode()).hexdigest()
    return get_settings().data_dir / "pdf-ocr" / key


def cached_pdf_pages(source: Path, content_hash: str) -> set[int]:
    """Completed pages are atomically published JSON files; temporary files don't count."""
    return {int(path.stem) for path in pdf_ocr_cache_directory(source, content_hash).glob("*.json")
            if path.stem.isdecimal()}


def recognize_pdf_page(source: Path, *, content_hash: str, page_number: int, background: bool = False) -> dict:
    """Serialize PDF OCR with foreground priority; a running page is never interrupted."""
    global _busy, _foreground_waiting, _foreground_at
    # Reading existing OCR must never wait behind a slow background recognition.
    target = pdf_ocr_cache_directory(source, content_hash) / f"{page_number}.json"
    if target.is_file():
        try:
            saved = json.loads(target.read_text(encoding="utf-8"))
            if saved.get("layout", {}).get("version") == LAYOUT_VERSION:
                return {k: v for k, v in saved.items() if k != "raw_ocr"}
        except (ValueError, OSError):
            pass
    with _admission:
        if background:
            if _busy or _foreground_waiting or time.monotonic() - _foreground_at < 3:
                raise OcrBackgroundDeferred()
        else:
            _foreground_waiting += 1
            try:
                while _busy:
                    _admission.wait()
            finally:
                _foreground_waiting -= 1
            _foreground_at = time.monotonic()
        _busy = True
    try:
        return _recognize_pdf_page(source, content_hash=content_hash, page_number=page_number)
    finally:
        with _admission:
            _busy = False
            _admission.notify_all()


def _save_result(target: Path, result: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _recognize_pdf_page(source: Path, *, content_hash: str, page_number: int) -> dict:
    """Read saved OCR or recognize one page; atomically persist raw and spatial data.

    Caller authorizes access and materializes the source. Cache identity includes
    source revision and pipeline version; duplicate requests share a bounded lock.
    """
    if page_number < 1:
        raise HTTPException(422, "页码必须大于零")
    directory = pdf_ocr_cache_directory(source, content_hash)
    key = directory.name
    target = directory / f"{page_number}.json"
    with _locks[int(key[:8], 16) % len(_locks)]:
        if target.is_file():
            try:
                saved = json.loads(target.read_text(encoding="utf-8"))
                if saved.get("layout", {}).get("version") != LAYOUT_VERSION:
                    saved["layout"] = build_reading_layout(saved["lines"], saved["geometry"])
                    _save_result(target, saved)
                return {k: v for k, v in saved.items() if k != "raw_ocr"}
            except (ValueError, OSError):
                pass  # Interrupted/invalid old cache is regenerated, not shown as blank.
        with pymupdf.open(source) as pdf:
            if page_number > len(pdf):
                raise HTTPException(422, "页码超出 PDF 页数")
            page = pdf[page_number - 1]
            scale = min(3.0, 3000 / max(page.rect.width, page.rect.height))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, annots=False)
            transform = pymupdf.Matrix(1 / scale, 1 / scale) * page.derotation_matrix
            geometry = {"width": pix.width, "height": pix.height, "unit": "px",
                        "origin": "top-left", "page_width_pt": page.rect.width,
                        "page_height_pt": page.rect.height, "rotation": page.rotation,
                        "pixel_to_page_matrix": list(transform)}
            with tempfile.TemporaryDirectory(dir=codeyun_temp_root("pdf-ocr")) as temp:
                image = Path(temp) / "page.png"
                pix.save(image)
                raw = run_paddle_ocr_preview(image, options={
                    "use_doc_orientation_classify": False, "use_doc_unwarping": False,
                    "use_textline_orientation": False, "return_word_box": True,
                })
        payload = raw.get("document", {}).get("flags", {}).get("paddleocr_payload")
        if not isinstance(payload, dict):
            raise RuntimeError("OCR 服务没有返回识别数据")
        spatial = extract_ocr_spatial_document(payload)
        characters = []
        for token in spatial["tokens"]:
            for index, char in enumerate(token["text"]):
                characters.append({"text": char, "parent_line_id": token["parent_line_id"],
                    "token_order": token["order"], "character_index": index,
                    "box": [token[k] for k in ("x", "y", "w", "h")],
                    "box_granularity": "character" if len(token["text"]) == 1 else "word"})
        result = {"schema_version": SCHEMA_VERSION, "content_hash": content_hash,
                  "page": page_number, "engine": "paddleocr", "created_at": time.time(),
                  "geometry": geometry, **spatial, "characters": characters,
                  "text": "\n".join(line["text"] for line in spatial["lines"])}
        result["layout"] = build_reading_layout(result["lines"], geometry)
        _save_result(target, {**result, "raw_ocr": raw})
        return result


def read_cached_pdf_reading_pages(source: Path, content_hash: str, start: int, end: int) -> list[dict]:
    """Read a bounded text-only batch, without starting recognition or loading a PDF.

    Keep source page/block coordinates for navigation; rebuild derived layout in
    memory when necessary. Missing pages are explicit, never silently omitted.
    """
    if start < 1 or end < start or end - start >= 24:
        raise ValueError("Reading batches must contain 1–24 pages")
    directory = pdf_ocr_cache_directory(source, content_hash)
    pages = []
    for page in range(start, end + 1):
        try:
            saved = json.loads((directory / f"{page}.json").read_text(encoding="utf-8"))
            layout = saved.get("layout", {})
            if layout.get("version") != LAYOUT_VERSION:
                layout = build_reading_layout(saved["lines"], saved["geometry"])
            # Keep title spacing evidence without transferring full-page geometry/raw OCR.
            blocks = []
            for block in layout["blocks"]:
                item = dict(block)
                if block["kind"] == "heading":
                    item["runs"] = [t for t in saved.get("tokens", [])
                                    if t.get("parent_line_id") in block["line_ids"]]
                blocks.append(item)
            pages.append({"page": page, "blocks": blocks, "available": True})
        except (OSError, ValueError, KeyError):
            pages.append({"page": page, "blocks": [], "available": False})
    return pages
