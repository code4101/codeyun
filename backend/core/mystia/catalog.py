from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException

CATALOG_VERSION = 9
DEFAULT_ANALYSIS_ROOT = Path(r"D:\home\chenkunze\data\m2606东方夜雀食堂逆向\mystia_analysis_exports")
CATALOG_PATH = DEFAULT_ANALYSIS_ROOT / "mystia_catalog.json"

MystiaKind = Literal[
    "foods",
    "ingredients",
    "beverages",
    "guests",
    "special_guests",
    "special_guest_records",
    "locations",
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


def list_mystia_catalog_entries(
    kind: MystiaKind,
    query: str = "",
    page: int = 1,
    page_size: int = 50,
    sort_by: str = "",
    sort_order: str = "",
) -> dict[str, Any]:
    catalog = load_mystia_catalog()
    rows = list(catalog.get(kind) or [])
    normalized_query = query.strip().lower()
    if normalized_query:
        rows = [
            row for row in rows
            if normalized_query in json.dumps(row, ensure_ascii=False).lower()
        ]
    normalized_sort_by = sort_by.strip()
    normalized_sort_order = sort_order.strip().lower()
    if normalized_sort_by and normalized_sort_order in {"asc", "desc"}:
        rows.sort(
            key=lambda row: _sort_value(row, normalized_sort_by),
            reverse=normalized_sort_order == "desc",
        )
    normalized_page_size = min(200, max(1, int(page_size or 50)))
    total = len(rows)
    total_pages = max(1, (total + normalized_page_size - 1) // normalized_page_size)
    normalized_page = min(total_pages, max(1, int(page or 1)))
    start = (normalized_page - 1) * normalized_page_size
    items = rows[start:start + normalized_page_size]
    return {
        "kind": kind,
        "query": query,
        "page": normalized_page,
        "page_size": normalized_page_size,
        "sort_by": normalized_sort_by,
        "sort_order": normalized_sort_order if normalized_sort_by else "",
        "total": total,
        "total_pages": total_pages,
        "items": items,
        "stats": catalog.get("stats", {}),
        "source": catalog.get("source", {}),
    }


def _sort_value(row: dict[str, Any], key: str) -> tuple[int, Any]:
    value = row.get(key)
    if value is None or value == "":
        return (1, "")
    if isinstance(value, (int, float)):
        return (0, value)
    return (0, str(value).lower())
