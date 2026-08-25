from __future__ import annotations

from typing import Any


def _normalized_path_scope(value: Any) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized.rstrip("/").casefold()


def device_media_list_resource_key(payload: dict[str, Any]) -> str:
    """Keep media scans exclusive only when they target the same device scope."""

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    entry_id = str(payload.get("entry_id") or metadata.get("entry_id") or "unknown").strip()
    absolute_path = _normalized_path_scope(
        request.get("absolute_path") or metadata.get("absolute_path")
    )
    if absolute_path:
        scope = absolute_path
    else:
        root = _normalized_path_scope(request.get("root") or metadata.get("root"))
        path = _normalized_path_scope(request.get("path") or metadata.get("path"))
        scope = f"{root}|{path}" if root or path else "default"
    return f"resource:device-media-list:{entry_id}:{scope}"
