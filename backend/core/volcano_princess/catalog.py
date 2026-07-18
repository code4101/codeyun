from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import HTTPException


CATALOG_SCHEMA_VERSION = 1
THEATER_CATALOG_SCHEMA_VERSION = 1
DEFAULT_REVERSE_ROOT = Path(r"D:\home\chenkunze\data\m2607火山的女儿逆向")


def get_volcano_princess_reverse_root() -> Path:
    configured = os.getenv("VOLCANO_PRINCESS_REVERSE_ROOT", "").strip()
    return Path(configured) if configured else DEFAULT_REVERSE_ROOT


def get_audio_catalog_path() -> Path:
    return get_volcano_princess_reverse_root() / "parsed_configs" / "audio_catalog" / "catalog.json"


def get_theater_catalog_path() -> Path:
    return get_volcano_princess_reverse_root() / "parsed_configs" / "theater_catalog" / "catalog.json"


@lru_cache(maxsize=4)
def _read_catalog(path_text: str, mtime_ns: int) -> dict[str, Any]:
    del mtime_ns
    path = Path(path_text)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"火山的女儿音频 catalog 读取失败：{path.name}") from exc
    if payload.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise HTTPException(
            status_code=500,
            detail=(
                "火山的女儿音频 catalog schema 不匹配："
                f"{payload.get('schema_version')} != {CATALOG_SCHEMA_VERSION}"
            ),
        )
    if not isinstance(payload.get("entries"), list):
        raise HTTPException(status_code=500, detail="火山的女儿音频 catalog 缺少 entries")
    return payload


def load_audio_catalog() -> dict[str, Any]:
    path = get_audio_catalog_path()
    if not path.is_file():
        raise HTTPException(status_code=503, detail=f"火山的女儿音频 catalog 尚未生成：{path}")
    return _read_catalog(str(path.resolve()), path.stat().st_mtime_ns)


@lru_cache(maxsize=4)
def _read_theater_catalog(path_text: str, mtime_ns: int) -> dict[str, Any]:
    del mtime_ns
    path = Path(path_text)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"奥拉夫剧院 catalog 读取失败：{path.name}") from exc
    if payload.get("schema_version") != THEATER_CATALOG_SCHEMA_VERSION:
        raise HTTPException(
            status_code=500,
            detail=(
                "奥拉夫剧院 catalog schema 不匹配："
                f"{payload.get('schema_version')} != {THEATER_CATALOG_SCHEMA_VERSION}"
            ),
        )
    if not isinstance(payload.get("questions"), list) or not isinstance(payload.get("dramas"), list):
        raise HTTPException(status_code=500, detail="奥拉夫剧院 catalog 缺少 questions 或 dramas")
    return payload


def load_theater_catalog() -> dict[str, Any]:
    path = get_theater_catalog_path()
    if not path.is_file():
        raise HTTPException(status_code=503, detail=f"奥拉夫剧院 catalog 尚未生成：{path}")
    return _read_theater_catalog(str(path.resolve()), path.stat().st_mtime_ns)


def find_audio_entry(path_id: int, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = catalog or load_audio_catalog()
    entry = next(
        (
            row
            for row in payload["entries"]
            if isinstance(row, dict) and int(row.get("path_id") or 0) == path_id
        ),
        None,
    )
    if not isinstance(entry, dict):
        raise HTTPException(status_code=404, detail="音频不存在")
    return entry


def get_audio_media_path(path_id: int) -> Path:
    entry = find_audio_entry(path_id)
    relative_path = str(entry.get("media_path") or "").strip().replace("\\", "/")
    parts = Path(relative_path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=404, detail="音频文件不存在")

    reverse_root = get_volcano_princess_reverse_root().resolve()
    media_root = (reverse_root / "media" / "audio").resolve()
    target = reverse_root.joinpath(*parts).resolve()
    try:
        target.relative_to(media_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="音频文件不存在") from exc
    if target.suffix.lower() != ".mp3" or not target.is_file():
        raise HTTPException(status_code=404, detail="音频文件不存在")
    return target
