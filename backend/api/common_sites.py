from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from backend.core.access.auth import get_current_active_user
from backend.core.settings import get_settings
from backend.models import User


router = APIRouter()

_LOGO_MAX_BYTES = 512 * 1024
_LOGO_REQUEST_TIMEOUT_SECONDS = 12


def _normalize_site_origin(site_url: str) -> str:
    try:
        parsed = urlsplit(site_url.strip())
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="网址格式不正确") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail="仅支持 HTTP 或 HTTPS 网站")
    default_port = (parsed.scheme == "http" and port in {None, 80}) or (
        parsed.scheme == "https" and port in {None, 443}
    )
    host = parsed.hostname.lower()
    authority_host = f"[{host}]" if ":" in host else host
    authority = authority_host if default_port else f"{authority_host}:{port}"
    return f"{parsed.scheme}://{authority}"


def _logo_cache_paths(site_origin: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(site_origin.encode("utf-8")).hexdigest()
    cache_dir = get_settings().data_dir / "common-sites" / "logo-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{digest}.bin", cache_dir / f"{digest}.json"


def _read_cached_logo(site_origin: str) -> tuple[Path, str] | None:
    image_path, metadata_path = _logo_cache_paths(site_origin)
    if not image_path.is_file() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        media_type = str(metadata.get("media_type") or "")
    except (OSError, ValueError, TypeError):
        return None
    if not media_type.startswith("image/"):
        return None
    return image_path, media_type


def _download_logo(site_origin: str) -> tuple[bytes, str]:
    hostname = urlsplit(site_origin).hostname or ""
    candidates = (
        (
            "https://www.google.com/s2/favicons",
            {"domain_url": site_origin, "sz": "128"},
        ),
        (f"https://icons.duckduckgo.com/ip3/{hostname}.ico", None),
    )
    last_error: Exception | None = None
    for url, params in candidates:
        try:
            response = requests.get(
                url,
                params=params,
                timeout=_LOGO_REQUEST_TIMEOUT_SECONDS,
                headers={"User-Agent": "CodeYun/1.0 common-site-logo-cache"},
            )
            response.raise_for_status()
            content = response.content
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if not media_type.startswith("image/"):
                raise ValueError("Logo 服务未返回图片")
            if not content or len(content) > _LOGO_MAX_BYTES:
                raise ValueError("Logo 文件为空或过大")
            return content, media_type
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
    raise HTTPException(status_code=502, detail="无法获取网站 Logo") from last_error


def _refresh_cached_logo(site_origin: str) -> tuple[Path, str]:
    content, media_type = _download_logo(site_origin)
    image_path, metadata_path = _logo_cache_paths(site_origin)
    write_id = uuid.uuid4().hex
    temporary_image_path = image_path.with_name(f"{image_path.name}.{write_id}.tmp")
    temporary_metadata_path = metadata_path.with_name(f"{metadata_path.name}.{write_id}.tmp")
    temporary_image_path.write_bytes(content)
    temporary_metadata_path.write_text(
        json.dumps({"site_origin": site_origin, "media_type": media_type}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        temporary_image_path.replace(image_path)
        temporary_metadata_path.replace(metadata_path)
    finally:
        temporary_image_path.unlink(missing_ok=True)
        temporary_metadata_path.unlink(missing_ok=True)
    return image_path, media_type


def _logo_response(site_url: str, *, refresh: bool) -> FileResponse:
    site_origin = _normalize_site_origin(site_url)
    cached = None if refresh else _read_cached_logo(site_origin)
    image_path, media_type = cached or _refresh_cached_logo(site_origin)
    return FileResponse(
        image_path,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/logo")
def get_common_site_logo(
    site_url: str = Query(..., min_length=1, max_length=2048),
    _current_user: User = Depends(get_current_active_user),
) -> FileResponse:
    return _logo_response(site_url, refresh=False)


@router.post("/logo/refresh")
def refresh_common_site_logo(
    site_url: str = Query(..., min_length=1, max_length=2048),
    _current_user: User = Depends(get_current_active_user),
) -> FileResponse:
    return _logo_response(site_url, refresh=True)
