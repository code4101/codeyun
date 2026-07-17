from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from backend.core.volcano_princess.catalog import (
    find_audio_entry,
    get_audio_media_path,
    load_audio_catalog,
)


router = APIRouter()


def _serialize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    path_id = int(entry.get("path_id") or 0)
    return {
        "id": str(entry.get("id") or f"resources.assets:{path_id}"),
        "path_id": path_id,
        "name": str(entry.get("name") or f"AudioClip #{path_id}"),
        "category": str(entry.get("category") or "voice_or_effect"),
        "duration_seconds": float(entry.get("duration_seconds") or 0),
        "channels": int(entry.get("channels") or 0),
        "frequency_hz": int(entry.get("frequency_hz") or 0),
        "source_asset": str(entry.get("source_asset") or ""),
        "media_bytes": int(entry.get("media_bytes") or 0),
        "media_sha256": str(entry.get("media_sha256") or ""),
        "media_url": f"/api/volcano-princess/media/audio/{path_id}",
    }


@router.get("/audio/meta")
def get_audio_meta() -> dict[str, Any]:
    catalog = load_audio_catalog()
    source = dict(catalog.get("source") or {})
    source.pop("game_root", None)
    source.pop("asset_path", None)
    return {
        "app_id": catalog.get("app_id"),
        "app_name": catalog.get("app_name"),
        "generated_at": catalog.get("generated_at"),
        "source": source,
        "summary": dict(catalog.get("summary") or {}),
        "categories": ["music_or_ambience", "voice_or_effect", "short_clip"],
    }


@router.get("/audio")
def list_audio_entries(
    q: str = "",
    category: str = Query("", pattern="^(|music_or_ambience|voice_or_effect|short_clip)$"),
    sort_by: str = Query("path_id", pattern="^(path_id|name|duration)$"),
    sort_order: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    catalog = load_audio_catalog()
    rows = [row for row in catalog["entries"] if isinstance(row, dict)]
    needle = q.strip().casefold()
    if needle:
        rows = [
            row
            for row in rows
            if needle in str(row.get("name") or "").casefold()
            or needle in str(row.get("path_id") or "")
        ]
    if category:
        rows = [row for row in rows if row.get("category") == category]

    def row_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        path_id = int(row.get("path_id") or 0)
        if sort_by == "name":
            return (str(row.get("name") or "").casefold(), path_id)
        if sort_by == "duration":
            return (float(row.get("duration_seconds") or 0), path_id)
        return (path_id,)

    rows.sort(key=row_sort_key, reverse=sort_order == "desc")

    total = len(rows)
    start = (page - 1) * page_size
    source = dict(catalog.get("source") or {})
    return {
        "items": [_serialize_entry(row) for row in rows[start : start + page_size]],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": max(1, math.ceil(total / page_size)),
        "source": {
            "build_id": source.get("build_id"),
            "engine": source.get("engine"),
        },
    }


@router.get("/audio/{path_id}")
def get_audio_entry(path_id: int) -> dict[str, Any]:
    return _serialize_entry(find_audio_entry(path_id))


@router.get("/media/audio/{path_id}")
def get_audio_media(path_id: int) -> FileResponse:
    entry = find_audio_entry(path_id)
    content_hash = str(entry.get("media_sha256") or "")
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    if content_hash:
        headers["ETag"] = f'"{content_hash}"'
    return FileResponse(get_audio_media_path(path_id), media_type="audio/mpeg", headers=headers)
