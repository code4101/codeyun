"""Search completed OCR only. Compact sidecars avoid rereading character geometry.

The PDF is never rendered or recognized by search. Each sidecar is invalidated
by its source page JSON timestamp and cached in a bounded in-process LRU.
"""
import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path

from backend.core.library.pdf_ocr import pdf_ocr_cache_directory


@lru_cache(maxsize=2048)
def _blocks(path: str, mtime: int, size: int):
    source = Path(path)
    sidecar = source.with_suffix(".search.json")
    if sidecar.exists() and sidecar.stat().st_mtime_ns >= mtime:
        try:
            return json.loads(sidecar.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    result = json.loads(source.read_text(encoding="utf-8"))
    lines = {line["line_id"]: line["text"] for line in result["lines"]}
    groups = result.get("layout", {}).get("blocks")
    texts = ["".join(lines.get(id, "") for id in block["line_ids"]) for block in groups] if groups else list(lines.values())
    blocks = ["".join(text.lower().split()) for text in texts]
    fd, temporary = tempfile.mkstemp(dir=source.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(blocks, stream, ensure_ascii=False)
        os.replace(temporary, sidecar)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return blocks


def search_pdf_ocr(source: Path, revision: str, query: str, start: int, end: int, offset: int = 0):
    """Return bounded results plus coverage; matches never cross paragraph boundaries."""
    term = "".join(query.lower().split())
    folder = pdf_ocr_cache_directory(source, revision)
    pages = sorted((int(p.stem), p) for p in folder.glob("*.json")
                   if p.stem.isdecimal() and start <= int(p.stem) <= end)
    hits, count, indexed = [], 0, 0
    if not term:
        return {"hits": [], "total": 0, "indexed_pages": len(pages), "scope_pages": end-start+1}
    for page, path in pages:
        try:
            stat = path.stat()
            blocks = _blocks(str(path), stat.st_mtime_ns, stat.st_size)
        except (OSError, ValueError, KeyError):
            continue
        indexed += 1
        occurrence = 0
        for text in blocks:
            position = text.find(term) if term else -1
            while position >= 0:
                if offset <= count < offset + 50:
                    hits.append({"page": page, "occurrence": occurrence,
                                 "snippet": text[max(0, position-24):position+len(term)+40]})
                occurrence += 1
                count += 1
                position = text.find(term, position + len(term))
    return {"hits": hits, "total": count, "indexed_pages": indexed, "scope_pages": end-start+1}
