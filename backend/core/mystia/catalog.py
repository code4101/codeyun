from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException

CATALOG_VERSION = 2
DEFAULT_ANALYSIS_ROOT = Path(r"D:\home\chenkunze\data\m2606东方夜雀食堂逆向\mystia_analysis_exports")
CATALOG_PATH = DEFAULT_ANALYSIS_ROOT / "mystia_catalog.json"

MystiaKind = Literal[
    "foods",
    "ingredients",
    "beverages",
    "recipes",
    "guests",
    "special_guests",
    "images",
    "audio",
]


def get_mystia_catalog_path() -> Path:
    return CATALOG_PATH


def get_mystia_asset_file(relative_path: str) -> Path:
    root = DEFAULT_ANALYSIS_ROOT.resolve()
    candidate = (root / relative_path).resolve()
    if root != candidate and root not in candidate.parents:
        raise HTTPException(status_code=400, detail="非法素材路径")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"素材不存在：{relative_path}")
    return candidate


def load_mystia_catalog() -> dict[str, Any]:
    path = get_mystia_catalog_path()
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"东方夜雀食堂图鉴数据尚未生成：{path}",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"东方夜雀食堂图鉴 JSON 损坏：{exc}") from exc
    if data.get("schema_version") != CATALOG_VERSION:
        raise HTTPException(
            status_code=500,
            detail=f"东方夜雀食堂图鉴 schema 不匹配：{data.get('schema_version')} != {CATALOG_VERSION}",
        )
    return data


def list_mystia_catalog_entries(kind: MystiaKind, query: str = "") -> dict[str, Any]:
    catalog = load_mystia_catalog()
    rows = list(catalog.get(kind) or [])
    normalized_query = query.strip().lower()
    if normalized_query:
        rows = [
            row for row in rows
            if normalized_query in json.dumps(row, ensure_ascii=False).lower()
        ]
    return {
        "kind": kind,
        "query": query,
        "total": len(rows),
        "items": rows,
        "stats": catalog.get("stats", {}),
        "source": catalog.get("source", {}),
    }
