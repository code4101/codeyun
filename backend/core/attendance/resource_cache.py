from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from backend.core.settings import get_settings


SUCCESS_TTL_SECONDS = 3 * 60 * 60
EMPTY_TTL_SECONDS = 15 * 60
MAX_CACHE_BYTES = 64 * 1024 * 1024
_SAFE_SUFFIX_RE = re.compile(r"^\.[a-zA-Z0-9]{1,12}$")
_CACHE_LOCK = threading.RLock()


def _canonicalize_url(value: str) -> str:
    text = str(value or "").strip()
    if not text or "://" not in text:
        return text

    parts = urlsplit(text)
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    fragment = parts.fragment
    if "?" in fragment:
        fragment_path, fragment_query = fragment.split("?", 1)
        fragment = f"{fragment_path}?{urlencode(sorted(parse_qsl(fragment_query, keep_blank_values=True)))}"
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path.rstrip("/") or "/",
        query,
        fragment,
    ))


def _normalize_options(options: dict[str, Any] | None) -> dict[str, str]:
    return {
        str(key): str(value or "").strip()
        for key, value in sorted((options or {}).items())
        if str(value or "").strip()
    }


def resource_cache_identity(
    *,
    resource_type: str,
    shop_id: int,
    resource_url: str,
    options: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    identity = {
        "resource_type": str(resource_type or "").strip().lower(),
        "shop_id": int(shop_id),
        "resource_url": _canonicalize_url(resource_url),
        "options": _normalize_options(options),
    }
    if identity["resource_type"] not in {"video", "clockin"}:
        raise ValueError(f"不支持的考勤资源类型：{resource_type}")
    if not identity["resource_url"]:
        raise ValueError("考勤资源URL不能为空")
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), identity


def _cache_root(root: str | Path | None = None) -> Path:
    path = Path(root) if root is not None else get_settings().data_dir / "attendance" / "resource-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _metadata_path(root: Path, cache_key: str) -> Path:
    return root / f"{cache_key}.json"


def _payload_path(root: Path, cache_key: str, suffix: str) -> Path:
    return root / f"{cache_key}{suffix}"


def _safe_suffix(value: str | None) -> str:
    suffix = str(value or "").strip().lower()
    return suffix if _SAFE_SUFFIX_RE.fullmatch(suffix) else ".bin"


def _atomic_write(path: Path, content: bytes) -> None:
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temp_path.write_bytes(content)
    os.replace(temp_path, path)


def _read_metadata(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def cleanup_expired_resource_cache(*, root: str | Path | None = None, now: float | None = None) -> int:
    with _CACHE_LOCK:
        cache_root = _cache_root(root)
        current_time = float(time.time() if now is None else now)
        removed = 0
        referenced_payloads: set[str] = set()

        for metadata_path in cache_root.glob("*.json"):
            metadata = _read_metadata(metadata_path)
            payload_name = str((metadata or {}).get("payload_name") or "")
            if payload_name:
                referenced_payloads.add(payload_name)
            if metadata is not None and float(metadata.get("expires_at") or 0) > current_time:
                continue
            if payload_name:
                (cache_root / payload_name).unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            removed += 1

        for payload_path in cache_root.iterdir():
            if (
                payload_path.is_file()
                and not payload_path.name.startswith(".")
                and payload_path.suffix != ".json"
                and payload_path.name not in referenced_payloads
            ):
                payload_path.unlink(missing_ok=True)
                removed += 1
        return removed


def lookup_resource_cache(
    *,
    resource_type: str,
    shop_id: int,
    resource_url: str,
    options: dict[str, Any] | None = None,
    root: str | Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    cache_root = _cache_root(root)
    current_time = float(time.time() if now is None else now)
    cleanup_expired_resource_cache(root=cache_root, now=current_time)
    cache_key, identity = resource_cache_identity(
        resource_type=resource_type,
        shop_id=shop_id,
        resource_url=resource_url,
        options=options,
    )
    metadata = _read_metadata(_metadata_path(cache_root, cache_key))
    if metadata is None or float(metadata.get("expires_at") or 0) <= current_time:
        return {"hit": False, "cache_key": cache_key, "identity": identity}

    response = {
        "hit": True,
        "empty": bool(metadata.get("empty")),
        "cache_key": cache_key,
        "identity": identity,
        "captured_at": metadata.get("captured_at"),
        "expires_at": metadata.get("expires_at"),
        "suffix": metadata.get("suffix") or ".bin",
        "size": int(metadata.get("size") or 0),
        "sha256": metadata.get("sha256") or "",
    }
    if response["empty"]:
        return response

    payload_path = cache_root / str(metadata.get("payload_name") or "")
    if not payload_path.is_file():
        _metadata_path(cache_root, cache_key).unlink(missing_ok=True)
        return {"hit": False, "cache_key": cache_key, "identity": identity}
    content = payload_path.read_bytes()
    if hashlib.sha256(content).hexdigest() != response["sha256"]:
        payload_path.unlink(missing_ok=True)
        _metadata_path(cache_root, cache_key).unlink(missing_ok=True)
        return {"hit": False, "cache_key": cache_key, "identity": identity}
    response["content_base64"] = base64.b64encode(content).decode("ascii")
    return response


def store_resource_cache(
    *,
    resource_type: str,
    shop_id: int,
    resource_url: str,
    options: dict[str, Any] | None = None,
    content_base64: str = "",
    suffix: str = ".bin",
    empty: bool = False,
    root: str | Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    cache_root = _cache_root(root)
    current_time = float(time.time() if now is None else now)
    cleanup_expired_resource_cache(root=cache_root, now=current_time)
    cache_key, identity = resource_cache_identity(
        resource_type=resource_type,
        shop_id=shop_id,
        resource_url=resource_url,
        options=options,
    )
    normalized_suffix = _safe_suffix(suffix)
    try:
        content = b"" if empty else base64.b64decode(content_base64, validate=True)
    except binascii.Error as exc:
        raise ValueError("考勤资源缓存内容不是有效的base64") from exc
    if not empty and not content:
        raise ValueError("非空考勤资源缓存缺少文件内容")
    if len(content) > MAX_CACHE_BYTES:
        raise ValueError(f"考勤资源缓存文件超过限制：{len(content)} > {MAX_CACHE_BYTES}")

    payload_name = ""
    if not empty:
        payload_path = _payload_path(cache_root, cache_key, normalized_suffix)
        _atomic_write(payload_path, content)
        payload_name = payload_path.name

    ttl_seconds = EMPTY_TTL_SECONDS if empty else SUCCESS_TTL_SECONDS
    metadata = {
        "cache_key": cache_key,
        "identity": identity,
        "empty": bool(empty),
        "captured_at": current_time,
        "expires_at": current_time + ttl_seconds,
        "suffix": normalized_suffix,
        "payload_name": payload_name,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest() if content else "",
    }
    _atomic_write(
        _metadata_path(cache_root, cache_key),
        json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode("utf-8"),
    )
    return {
        "stored": True,
        **metadata,
    }
