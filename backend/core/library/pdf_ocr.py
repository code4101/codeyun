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


def _save_result(target: Path, result: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def recognize_pdf_page(source: Path, *, content_hash: str, page_number: int) -> dict:
    """Read saved OCR or recognize one page; atomically persist raw and spatial data.

    Caller authorizes access and materializes the source. Cache identity includes
    source revision and pipeline version; duplicate requests share a bounded lock.
    """
    if page_number < 1:
        raise HTTPException(422, "页码必须大于零")
    stat = source.stat()
    identity = content_hash or f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    key = hashlib.sha256(f"{SCHEMA_VERSION}:{identity}".encode()).hexdigest()
    target = get_settings().data_dir / "pdf-ocr" / key / f"{page_number}.json"
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
