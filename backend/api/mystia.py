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
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(default=50, ge=1, le=200, description="每页数量"),
    sort_by: str = Query(default="", description="排序字段"),
    sort_order: str = Query(default="", pattern="^(|asc|desc)$", description="排序方向"),
) -> dict:
    return list_mystia_catalog_entries(
        kind,
        q,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/asset/{relative_path:path}")
def get_mystia_asset(relative_path: str) -> FileResponse:
    return FileResponse(get_mystia_asset_file(relative_path))
