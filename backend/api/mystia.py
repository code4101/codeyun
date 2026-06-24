from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from backend.core.mystia.catalog import (
    MystiaKind,
    get_mystia_asset_file,
    list_mystia_catalog_entries,
    load_mystia_catalog,
)

router = APIRouter()


@router.get("/catalog")
def get_mystia_catalog_summary() -> dict:
    catalog = load_mystia_catalog()
    return {
        "schema_version": catalog.get("schema_version"),
        "source": catalog.get("source", {}),
        "stats": catalog.get("stats", {}),
    }


@router.get("/catalog/{kind}")
def get_mystia_catalog_entries(
    kind: MystiaKind,
    q: str = Query(default="", description="按名称、描述、标签或原始字段搜索"),
) -> dict:
    return list_mystia_catalog_entries(kind, q)


@router.get("/asset/{relative_path:path}")
def get_mystia_asset(relative_path: str) -> FileResponse:
    return FileResponse(get_mystia_asset_file(relative_path))
