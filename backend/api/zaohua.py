from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlmodel import Session, select

from backend.db import get_session
from backend.core.zaohua.catalog import get_zaohua_icon_path
from backend.core.zaohua.grades import get_grade_visual
from backend.models import ZaohuaAlchemyRecipe, ZaohuaHerb


router = APIRouter()


def _icon_url(icon_path: Any) -> str:
    normalized = str(icon_path or "").strip().replace("\\", "/").strip("/")
    return f"/api/zaohua/media/icons/{normalized}" if normalized else ""


def _serialize_item(item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    payload["icon_url"] = _icon_url(payload.get("icon_path"))
    payload.update({
        f"grade_{key}": value
        for key, value in get_grade_visual(payload.get("grade_id"), payload.get("grade_name")).items()
        if key != "name"
    })
    return payload


def _serialize_recipe(record: ZaohuaAlchemyRecipe) -> dict[str, Any]:
    return {
        "recipe_id": record.recipe_id,
        "source_build_id": record.source_build_id,
        "name": record.name,
        "technique": record.technique,
        "output": _serialize_item({
            "item_id": record.output_item_id,
            "name": record.output_item_name,
            "count": record.output_count,
            "grade_id": record.output_grade_id,
            "grade_name": record.output_grade_name,
            "icon_path": record.output_icon_path,
            "price": record.output_price,
        }),
        "attr_limits": list(record.attr_limits or []),
        "example_items": [_serialize_item(item) for item in list(record.example_items or [])],
        "state_rules": list(record.state_rules or []),
        "source_evidence": dict(record.source_json or {}),
        "content_hash": record.content_hash,
    }


def _serialize_herb(record: ZaohuaHerb) -> dict[str, Any]:
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
        "recipe_count": record.recipe_count,
        "recipes": list(record.recipes or []),
        "source_evidence": dict(record.source_json or {}),
        "content_hash": record.content_hash,
    }
    payload.update({
        f"grade_{key}": value
        for key, value in get_grade_visual(record.grade_id, record.grade_name).items()
        if key != "name"
    })
    return payload


@router.get("/media/icons/{resource_path:path}")
def get_zaohua_icon(resource_path: str) -> FileResponse:
    return FileResponse(
        get_zaohua_icon_path(resource_path),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


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


@router.get("/alchemy/recipes")
def list_zaohua_alchemy_recipes(
    q: str = "",
    grade: str = "",
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
    records = session.exec(
        select(ZaohuaAlchemyRecipe)
        .where(*conditions)
        .order_by(ZaohuaAlchemyRecipe.recipe_id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_serialize_recipe(record) for record in records],
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
    return _serialize_recipe(record)


@router.get("/herbs/meta")
def get_zaohua_herb_meta(session: Session = Depends(get_session)) -> dict[str, Any]:
    records = session.exec(
        select(ZaohuaHerb).where(ZaohuaHerb.is_active == True)  # noqa: E712
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
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    conditions = [ZaohuaHerb.is_active == True]  # noqa: E712
    query = q.strip().lower()
    if query:
        conditions.append(ZaohuaHerb.search_text.contains(query))
    if grade.strip():
        conditions.append(ZaohuaHerb.grade_name == grade.strip())
    if element.strip():
        conditions.append(ZaohuaHerb.element_key == element.strip())

    total = session.exec(select(func.count()).select_from(ZaohuaHerb).where(*conditions)).one()
    records = session.exec(
        select(ZaohuaHerb)
        .where(*conditions)
        .order_by(ZaohuaHerb.display_order, ZaohuaHerb.item_id)
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
    record = session.get(ZaohuaHerb, item_id)
    if record is None or not record.is_active:
        raise HTTPException(status_code=404, detail="药材不存在")
    return _serialize_herb(record)
