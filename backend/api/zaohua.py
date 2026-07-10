from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, select

from backend.db import get_session
from backend.core.zaohua.catalog import (
    get_zaohua_icon_path,
    get_zaohua_pasture_image_path,
    get_zaohua_shape_image_path,
    load_zaohua_catalog,
    load_zaohua_pasture_catalog,
)
from backend.core.zaohua.grades import get_crafting_drug_cost_days, get_grade_visual
from backend.core.zaohua.alchemy_solver import solve_alchemy
from backend.core.zaohua.pasture_solver import optimize_pasture_shape
from backend.models import ZaohuaAlchemyRecipe, ZaohuaHerb


router = APIRouter()

PLACEHOLDER_HERB_IDS = tuple(range(70001, 70016))


class ZaohuaAlchemySolveRequest(BaseModel):
    width: int = Field(default=3, ge=1, le=12)
    height: int = Field(default=3, ge=1, le=12)
    limit: int = Field(default=5, ge=1, le=100)
    excluded_item_ids: list[int] = Field(default_factory=list)


class ZaohuaPastureSolveRequest(BaseModel):
    plot_count: int = Field(default=9, ge=1, le=30)
    enabled_building_ids: list[int] = Field(default_factory=list)


def _icon_url(icon_path: Any) -> str:
    normalized = str(icon_path or "").strip().replace("\\", "/").strip("/")
    return f"/api/zaohua/media/icons/{normalized}" if normalized else ""


def _serialize_item(item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    payload["icon_url"] = _icon_url(payload.get("icon_path"))
    grade_visual = get_grade_visual(payload.get("grade_id"), payload.get("grade_name"))
    payload.update({
        f"grade_{key}": value
        for key, value in grade_visual.items()
        if key != "name"
    })
    payload["grade_rank"] = grade_visual["order"] if grade_visual["order"] <= 15 else 0
    return payload


def _serialize_furnace(item: dict[str, Any]) -> dict[str, Any]:
    payload = _serialize_item(item)
    payload["source_build_id"] = str(item.get("source_build_id") or "")
    return payload


def _format_month_duration(months: int) -> str:
    years, remaining_months = divmod(max(0, months), 12)
    parts = []
    if years:
        parts.append(f"{years} 年")
    if remaining_months:
        parts.append(f"{remaining_months} 月")
    return " ".join(parts)


def _output_effect_text(item: dict[str, Any]) -> str:
    localized = str(item.get("effect_description") or "").strip()
    if localized:
        return localized
    augment = int(item.get("augment") or 0)
    efficacy = int(item.get("efficacy") or 0)
    if augment > 0:
        effect = f"修炼效率 +{augment}%"
        duration = _format_month_duration(efficacy)
        return f"{effect}，持续 {duration}" if duration else effect
    return str(item.get("description") or "").strip()


def _serialize_recipe(
    record: ZaohuaAlchemyRecipe,
    herb_attributes: dict[int, list[dict]] | None = None,
) -> dict[str, Any]:
    attributes_by_item_id = herb_attributes or {}
    example_items = []
    for item in list(record.example_items or []):
        payload = _serialize_item(item)
        item_id = int(payload.get("item_id") or 0)
        payload["crafting_attributes"] = list(
            attributes_by_item_id.get(item_id, payload.get("crafting_attributes") or [])
        )
        example_items.append(payload)
    output = _serialize_item({
        "item_id": record.output_item_id,
        "name": record.output_item_name,
        "count": record.output_count,
        "grade_id": record.output_grade_id,
        "grade_name": record.output_grade_name,
        "icon_path": record.output_icon_path,
        "price": record.output_price,
        "description": record.output_description,
        "effect_description": record.output_effect_description,
        "use_effect": record.output_use_effect,
        "augment": record.output_augment,
        "efficacy": record.output_efficacy,
    })
    output["effect_text"] = _output_effect_text(output)
    return {
        "recipe_id": record.recipe_id,
        "source_build_id": record.source_build_id,
        "name": record.name,
        "technique": record.technique,
        "output": output,
        "cost_days": get_crafting_drug_cost_days(record.output_grade_id, record.output_grade_name),
        "attr_limits": list(record.attr_limits or []),
        "example_items": example_items,
        "state_rules": list(record.state_rules or []),
        "source_evidence": dict(record.source_json or {}),
        "content_hash": record.content_hash,
    }


def _recipe_herb_attributes(
    records: list[ZaohuaAlchemyRecipe],
    session: Session,
) -> dict[int, list[dict]]:
    item_ids = {
        int(item.get("item_id") or 0)
        for record in records
        for item in list(record.example_items or [])
        if int(item.get("item_id") or 0) > 0
    }
    if not item_ids:
        return {}
    herbs = session.exec(
        select(ZaohuaHerb).where(
            ZaohuaHerb.item_id.in_(item_ids),
            ZaohuaHerb.is_active == True,  # noqa: E712
        )
    ).all()
    return {
        herb.item_id: list(herb.crafting_attributes or [])
        for herb in herbs
    }


def _serialize_herb(record: ZaohuaHerb) -> dict[str, Any]:
    source_json = dict(record.source_json or {})
    shape = source_json.pop("shape", None)
    if isinstance(shape, dict) and int(shape.get("draw_id") or 0) > 0:
        shape["image_url"] = f"/api/zaohua/media/shapes/{int(shape['draw_id'])}"
    payload = {
        "item_id": record.item_id,
        "source_build_id": record.source_build_id,
        "display_order": record.display_order,
        "name": record.name,
        "description": record.description,
        "effect_description": record.effect_description,
        "icon_path": record.icon_path,
        "icon_url": _icon_url(record.icon_path),
        "grade_id": record.grade_id,
        "grade_name": record.grade_name,
        "element_id": record.element_id,
        "element_key": record.element_key,
        "element_name": record.element_name,
        "price": record.price,
        "lingqi": record.lingqi,
        "crafting_attributes": list(record.crafting_attributes or []),
        "recipe_count": record.recipe_count,
        "recipes": list(record.recipes or []),
        "shape": shape,
        "source_evidence": source_json,
        "content_hash": record.content_hash,
    }
    grade_visual = get_grade_visual(record.grade_id, record.grade_name)
    payload.update({
        f"grade_{key}": value
        for key, value in grade_visual.items()
        if key != "name"
    })
    payload["grade_rank"] = grade_visual["order"] if grade_visual["order"] <= 15 else 0
    return payload


@router.get("/media/icons/{resource_path:path}")
def get_zaohua_icon(resource_path: str) -> FileResponse:
    return FileResponse(
        get_zaohua_icon_path(resource_path),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/media/shapes/{draw_id}")
def get_zaohua_shape_image(draw_id: int) -> FileResponse:
    return FileResponse(
        get_zaohua_shape_image_path(draw_id),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/media/pasture/{resource_path:path}")
def get_zaohua_pasture_image(resource_path: str) -> FileResponse:
    return FileResponse(
        get_zaohua_pasture_image_path(resource_path),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/pasture/meta")
def get_zaohua_pasture_meta() -> dict[str, Any]:
    catalog = load_zaohua_pasture_catalog()
    buildings = []
    for row in catalog.get("buildings", []):
        item = dict(row)
        media_path = str(item.get("image_media_path") or "").replace("\\", "/")
        if media_path.startswith("pasture/"):
            media_path = media_path[len("pasture/"):]
        item["image_url"] = f"/api/zaohua/media/pasture/{media_path}" if media_path else ""
        buildings.append(item)
    return {
        "source": catalog.get("source", {}),
        "stats": catalog.get("stats", {}),
        "model": catalog.get("model", {}),
        "buildings": buildings,
    }


@router.post("/pasture/solve")
def solve_zaohua_pasture(request: ZaohuaPastureSolveRequest) -> dict[str, Any]:
    catalog = load_zaohua_pasture_catalog()
    try:
        return optimize_pasture_shape(
            request.plot_count,
            catalog.get("buildings", []),
            request.enabled_building_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/alchemy/meta")
def get_zaohua_alchemy_meta(session: Session = Depends(get_session)) -> dict[str, Any]:
    records = session.exec(
        select(ZaohuaAlchemyRecipe).where(ZaohuaAlchemyRecipe.is_active == True)  # noqa: E712
    ).all()
    grade_counts = Counter(record.output_grade_name or "未命名" for record in records)
    grade_ids = {
        record.output_grade_name or "未命名": record.output_grade_id
        for record in records
    }
    build_ids = sorted({record.source_build_id for record in records if record.source_build_id})
    return {
        "recipe_count": len(records),
        "build_ids": build_ids,
        "grades": sorted(
            [
                {
                    "name": name,
                    "count": count,
                    "grade_id": grade_ids.get(name, 0),
                    **{
                        key: value
                        for key, value in get_grade_visual(grade_ids.get(name, 0), name).items()
                        if key != "name"
                    },
                }
                for name, count in grade_counts.items()
            ],
            key=lambda item: (item["order"], item["name"]),
        ),
        "storage": "database",
    }


@router.get("/furnaces/meta")
def get_zaohua_furnace_meta() -> dict[str, Any]:
    catalog = load_zaohua_catalog()
    furnaces = [row for row in catalog.get("furnaces", []) if isinstance(row, dict)]
    grade_counts = Counter(str(row.get("grade_name") or "未命名") for row in furnaces)
    grade_ids = {str(row.get("grade_name") or "未命名"): int(row.get("grade_id") or 0) for row in furnaces}
    element_counts = Counter((str(row.get("element_key") or "none"), str(row.get("element_name") or "无")) for row in furnaces)
    return {
        "furnace_count": len(furnaces),
        "build_ids": [str(catalog.get("source", {}).get("steam_build_id") or "")],
        "grades": sorted([
            {"name": name, "count": count, "grade_id": grade_ids[name], **{
                key: value for key, value in get_grade_visual(grade_ids[name], name).items() if key != "name"
            }} for name, count in grade_counts.items()
        ], key=lambda item: (item["order"], item["name"])),
        "elements": [{"key": key, "name": name, "count": count} for (key, name), count in element_counts.items()],
        "storage": "static_catalog",
    }


@router.get("/furnaces")
def list_zaohua_furnaces(
    q: str = "", grade: str = "", element: str = "",
    sort_by: str = Query("number", pattern="^(number|grade)$"),
    sort_order: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1), page_size: int = Query(40, ge=1, le=200),
) -> dict[str, Any]:
    catalog = load_zaohua_catalog()
    rows = [row for row in catalog.get("furnaces", []) if isinstance(row, dict)]
    query = q.strip().lower()
    rows = [row for row in rows if (not query or query in str(row.get("search_text") or ""))
            and (not grade or row.get("grade_name") == grade)
            and (not element or row.get("element_key") == element)]
    key = (lambda row: int(row.get("grade_id") or 0)) if sort_by == "grade" else (lambda row: int(row.get("display_order") or 0))
    rows.sort(key=lambda row: (key(row), int(row.get("item_id") or 0)), reverse=sort_order == "desc")
    total = len(rows)
    start = (page - 1) * page_size
    return {"items": [_serialize_furnace(row) for row in rows[start:start + page_size]], "page": page, "page_size": page_size, "total": total}


@router.get("/furnaces/{item_id}")
def get_zaohua_furnace(item_id: int) -> dict[str, Any]:
    catalog = load_zaohua_catalog()
    row = next((row for row in catalog.get("furnaces", []) if int(row.get("item_id") or 0) == item_id), None)
    if not isinstance(row, dict):
        raise HTTPException(status_code=404, detail="丹炉不存在")
    return _serialize_furnace(row)


@router.get("/alchemy/recipes")
def list_zaohua_alchemy_recipes(
    q: str = "",
    grade: str = "",
    sort_by: str = Query("number", pattern="^(number|grade)$"),
    sort_order: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    conditions = [ZaohuaAlchemyRecipe.is_active == True]  # noqa: E712
    query = q.strip().lower()
    if query:
        conditions.append(ZaohuaAlchemyRecipe.search_text.contains(query))
    if grade.strip():
        conditions.append(ZaohuaAlchemyRecipe.output_grade_name == grade.strip())

    total = session.exec(
        select(func.count()).select_from(ZaohuaAlchemyRecipe).where(*conditions)
    ).one()
    primary_sort = (
        ZaohuaAlchemyRecipe.output_grade_id
        if sort_by == "grade"
        else ZaohuaAlchemyRecipe.recipe_id
    )
    order_by = primary_sort.desc() if sort_order == "desc" else primary_sort.asc()
    records = session.exec(
        select(ZaohuaAlchemyRecipe)
        .where(*conditions)
        .order_by(order_by, ZaohuaAlchemyRecipe.recipe_id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    herb_attributes = _recipe_herb_attributes(records, session)
    return {
        "items": [_serialize_recipe(record, herb_attributes) for record in records],
        "page": page,
        "page_size": page_size,
        "total": int(total),
    }


@router.get("/alchemy/recipes/{recipe_id}")
def get_zaohua_alchemy_recipe(
    recipe_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    record = session.get(ZaohuaAlchemyRecipe, recipe_id)
    if record is None or not record.is_active:
        raise HTTPException(status_code=404, detail="丹方不存在")
    herb_attributes = _recipe_herb_attributes([record], session)
    return _serialize_recipe(record, herb_attributes)


@router.post("/alchemy/recipes/{recipe_id}/solve")
def solve_zaohua_alchemy_recipe(
    recipe_id: int,
    request: ZaohuaAlchemySolveRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    recipe = session.get(ZaohuaAlchemyRecipe, recipe_id)
    if recipe is None or not recipe.is_active:
        raise HTTPException(status_code=404, detail="丹药不存在")
    herbs = session.exec(
        select(ZaohuaHerb).where(
            ZaohuaHerb.is_active == True,  # noqa: E712
            ZaohuaHerb.item_id.notin_(PLACEHOLDER_HERB_IDS),
        )
    ).all()
    result = solve_alchemy(
        recipe,
        herbs,
        request.width,
        request.height,
        request.limit,
        excluded_item_ids=request.excluded_item_ids,
    )
    for herb in result["available_herbs"]:
        herb["icon_url"] = _icon_url(herb.get("icon_path"))
    for solution in result["solutions"]:
        for placement in solution["placements"]:
            draw_id = int(placement.get("shape_draw_id") or 0)
            placement["shape_image_url"] = f"/api/zaohua/media/shapes/{draw_id}" if draw_id else ""
    return {
        "recipe_id": recipe.recipe_id,
        "furnace": {"width": request.width, "height": request.height},
        **result,
    }


@router.get("/herbs/meta")
def get_zaohua_herb_meta(session: Session = Depends(get_session)) -> dict[str, Any]:
    records = session.exec(
        select(ZaohuaHerb).where(
            ZaohuaHerb.is_active == True,  # noqa: E712
            ZaohuaHerb.item_id.notin_(PLACEHOLDER_HERB_IDS),
        )
    ).all()
    grade_counts = Counter(record.grade_name or "未命名" for record in records)
    grade_ids = {record.grade_name or "未命名": record.grade_id for record in records}
    element_counts = Counter((record.element_key, record.element_name or "无") for record in records)
    element_orders = {
        "gold": 1,
        "water": 2,
        "wood": 3,
        "fire": 4,
        "soil": 5,
        "ice": 6,
        "wind": 7,
        "thunder": 8,
        "none": 99,
    }
    return {
        "herb_count": len(records),
        "build_ids": sorted({record.source_build_id for record in records if record.source_build_id}),
        "grades": sorted(
            [
                {
                    "name": name,
                    "count": count,
                    "grade_id": grade_ids.get(name, 0),
                    **{
                        key: value
                        for key, value in get_grade_visual(grade_ids.get(name, 0), name).items()
                        if key != "name"
                    },
                }
                for name, count in grade_counts.items()
            ],
            key=lambda item: (item["order"], item["name"]),
        ),
        "elements": sorted(
            [
                {"key": key, "name": name, "count": count}
                for (key, name), count in element_counts.items()
            ],
            key=lambda item: (element_orders.get(item["key"], 99), item["name"]),
        ),
        "storage": "database",
    }


@router.get("/herbs")
def list_zaohua_herbs(
    q: str = "",
    grade: str = "",
    element: str = "",
    sort_by: str = Query("number", pattern="^(number|grade)$"),
    sort_order: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    conditions = [
        ZaohuaHerb.is_active == True,  # noqa: E712
        ZaohuaHerb.item_id.notin_(PLACEHOLDER_HERB_IDS),
    ]
    query = q.strip().lower()
    if query:
        conditions.append(ZaohuaHerb.search_text.contains(query))
    if grade.strip():
        conditions.append(ZaohuaHerb.grade_name == grade.strip())
    if element.strip():
        conditions.append(ZaohuaHerb.element_key == element.strip())

    total = session.exec(select(func.count()).select_from(ZaohuaHerb).where(*conditions)).one()
    primary_sort = ZaohuaHerb.grade_id if sort_by == "grade" else ZaohuaHerb.display_order
    order_by = primary_sort.desc() if sort_order == "desc" else primary_sort.asc()
    records = session.exec(
        select(ZaohuaHerb)
        .where(*conditions)
        .order_by(order_by, ZaohuaHerb.display_order.asc(), ZaohuaHerb.item_id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_serialize_herb(record) for record in records],
        "page": page,
        "page_size": page_size,
        "total": int(total),
    }


@router.get("/herbs/{item_id}")
def get_zaohua_herb(item_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    if item_id in PLACEHOLDER_HERB_IDS:
        raise HTTPException(status_code=404, detail="药材不存在")
    record = session.get(ZaohuaHerb, item_id)
    if record is None or not record.is_active:
        raise HTTPException(status_code=404, detail="药材不存在")
    return _serialize_herb(record)
