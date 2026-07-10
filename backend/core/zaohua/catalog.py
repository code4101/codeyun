from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, select

from backend.models import ZaohuaAlchemyRecipe, ZaohuaHerb


CATALOG_SCHEMA_VERSION = 1
DEFAULT_REVERSE_ROOT = Path(r"D:\home\chenkunze\data\m2607造化仙缘")


def get_zaohua_reverse_root() -> Path:
    configured = os.getenv("ZAOHUA_REVERSE_ROOT", "").strip()
    return Path(configured) if configured else DEFAULT_REVERSE_ROOT


def get_zaohua_catalog_path() -> Path:
    return get_zaohua_reverse_root() / "parsed_configs" / "alchemy" / "alchemy_catalog.json"


def get_zaohua_herb_catalog_path() -> Path:
    return get_zaohua_reverse_root() / "parsed_configs" / "herbs" / "herb_catalog.json"


def get_zaohua_pasture_catalog_path() -> Path:
    return get_zaohua_reverse_root() / "parsed_configs" / "pasture" / "pasture_catalog.json"


def get_zaohua_pasture_image_path(resource_path: str) -> Path:
    normalized = resource_path.strip().replace("\\", "/").strip("/").lower()
    parts = Path(normalized).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=404, detail="灵田素材不存在")
    image_root = (get_zaohua_reverse_root() / "media" / "pasture").resolve()
    image_path = image_root.joinpath(*parts).resolve()
    try:
        image_path.relative_to(image_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="灵田素材不存在") from exc
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="灵田素材不存在")
    return image_path


def get_zaohua_icon_path(resource_path: str) -> Path:
    normalized = resource_path.strip().replace("\\", "/").strip("/").lower()
    if normalized.endswith(".png"):
        normalized = normalized[:-4]
    parts = Path(normalized).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=404, detail="图标不存在")
    icon_root = (get_zaohua_reverse_root() / "media" / "icons").resolve()
    icon_path = (icon_root.joinpath(*parts).with_suffix(".png")).resolve()
    try:
        icon_path.relative_to(icon_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="图标不存在") from exc
    if not icon_path.is_file():
        raise HTTPException(status_code=404, detail="图标不存在")
    return icon_path


def get_zaohua_shape_image_path(draw_id: int) -> Path:
    if draw_id <= 0:
        raise HTTPException(status_code=404, detail="形状图鉴不存在")
    shape_root = (get_zaohua_reverse_root() / "media" / "shapes").resolve()
    shape_path = (shape_root / f"Draw_{draw_id}.png").resolve()
    if not shape_path.is_file():
        raise HTTPException(status_code=404, detail="形状图鉴不存在")
    return shape_path


def load_zaohua_catalog() -> dict[str, Any]:
    path = get_zaohua_catalog_path()
    if not path.is_file():
        raise HTTPException(status_code=503, detail=f"造化仙缘炼丹 catalog 尚未生成：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"造化仙缘炼丹 catalog 读取失败：{path.name}") from exc
    if data.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise HTTPException(
            status_code=500,
            detail=f"造化仙缘炼丹 catalog schema 不匹配：{data.get('schema_version')} != {CATALOG_SCHEMA_VERSION}",
        )
    return data


def load_zaohua_herb_catalog() -> dict[str, Any]:
    path = get_zaohua_herb_catalog_path()
    if not path.is_file():
        raise HTTPException(status_code=503, detail=f"造化仙缘药材 catalog 尚未生成：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"造化仙缘药材 catalog 读取失败：{path.name}") from exc
    if data.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise HTTPException(
            status_code=500,
            detail=f"造化仙缘药材 catalog schema 不匹配：{data.get('schema_version')} != {CATALOG_SCHEMA_VERSION}",
        )
    return data


def load_zaohua_pasture_catalog() -> dict[str, Any]:
    path = get_zaohua_pasture_catalog_path()
    if not path.is_file():
        raise HTTPException(status_code=503, detail=f"造化仙缘灵田 catalog 尚未生成：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"造化仙缘灵田 catalog 读取失败：{path.name}") from exc
    if data.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise HTTPException(status_code=500, detail="造化仙缘灵田 catalog schema 不匹配")
    return data


def sync_zaohua_catalog_to_database(session: Session, catalog: dict[str, Any] | None = None) -> dict[str, int]:
    payload = catalog or load_zaohua_catalog()
    recipes = [row for row in payload.get("recipes", []) if isinstance(row, dict)]
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    build_id = str(source.get("steam_build_id") or "")
    now = time.time()

    existing = {
        row.recipe_id: row
        for row in session.exec(select(ZaohuaAlchemyRecipe)).all()
    }
    seen: set[int] = set()
    created = 0
    updated = 0

    for recipe in recipes:
        recipe_id = int(recipe.get("recipe_id") or 0)
        if recipe_id <= 0:
            continue
        seen.add(recipe_id)
        output = recipe.get("output") if isinstance(recipe.get("output"), dict) else {}
        values = {
            "source_build_id": build_id,
            "name": str(recipe.get("name") or f"丹方 #{recipe_id}"),
            "technique": str(recipe.get("technique") or ""),
            "output_item_id": int(output.get("item_id") or 0),
            "output_item_name": str(output.get("name") or ""),
            "output_count": int(recipe.get("output_count") or 0),
            "output_grade_id": int(output.get("grade_id") or 0),
            "output_grade_name": str(output.get("grade_name") or ""),
            "output_icon_path": str(output.get("icon_path") or ""),
            "output_price": float(output.get("price") or 0),
            "output_description": str(output.get("description") or ""),
            "output_effect_description": str(output.get("effect_description") or ""),
            "output_use_effect": str(output.get("use_effect") or ""),
            "output_augment": int(output.get("augment") or 0),
            "output_efficacy": int(output.get("efficacy") or 0),
            "attr_limits": list(recipe.get("attr_limits") or []),
            "example_items": list(recipe.get("example_items") or []),
            "state_rules": list(recipe.get("state_rules") or []),
            "search_text": str(recipe.get("search_text") or ""),
            "source_json": dict(recipe.get("source_evidence") or {}),
            "content_hash": str(recipe.get("content_hash") or ""),
            "is_active": True,
            "updated_at": now,
        }
        record = existing.get(recipe_id)
        if record is None:
            record = ZaohuaAlchemyRecipe(recipe_id=recipe_id, created_at=now, **values)
            session.add(record)
            created += 1
        else:
            for key, value in values.items():
                setattr(record, key, value)
            session.add(record)
            updated += 1

    deactivated = 0
    for recipe_id, record in existing.items():
        if recipe_id in seen or not record.is_active:
            continue
        record.is_active = False
        record.updated_at = now
        session.add(record)
        deactivated += 1

    session.commit()
    return {
        "catalog_count": len(recipes),
        "created": created,
        "updated": updated,
        "deactivated": deactivated,
    }


def sync_zaohua_herb_catalog_to_database(
    session: Session,
    catalog: dict[str, Any] | None = None,
) -> dict[str, int]:
    payload = catalog or load_zaohua_herb_catalog()
    herbs = [row for row in payload.get("herbs", []) if isinstance(row, dict)]
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    build_id = str(source.get("steam_build_id") or "")
    now = time.time()
    existing = {row.item_id: row for row in session.exec(select(ZaohuaHerb)).all()}
    seen: set[int] = set()
    created = 0
    updated = 0

    for herb in herbs:
        item_id = int(herb.get("item_id") or 0)
        if item_id <= 0:
            continue
        seen.add(item_id)
        values = {
            "source_build_id": build_id,
            "display_order": int(herb.get("display_order") or 0),
            "name": str(herb.get("name") or f"药材 #{item_id}"),
            "description": str(herb.get("description") or ""),
            "effect_description": str(herb.get("effect_description") or ""),
            "icon_path": str(herb.get("icon_path") or ""),
            "grade_id": int(herb.get("grade_id") or 0),
            "grade_name": str(herb.get("grade_name") or ""),
            "element_id": int(herb.get("element_id") or 0),
            "element_key": str(herb.get("element_key") or "none"),
            "element_name": str(herb.get("element_name") or "无"),
            "price": float(herb.get("price") or 0),
            "lingqi": int(herb.get("lingqi") or 0),
            "crafting_attributes": list(herb.get("crafting_attributes") or []),
            "recipe_count": int(herb.get("recipe_count") or 0),
            "recipes": list(herb.get("recipes") or []),
            "search_text": str(herb.get("search_text") or ""),
            "source_json": dict(herb.get("source_evidence") or {}),
            "content_hash": str(herb.get("content_hash") or ""),
            "is_active": True,
            "updated_at": now,
        }
        record = existing.get(item_id)
        if record is None:
            record = ZaohuaHerb(item_id=item_id, created_at=now, **values)
            session.add(record)
            created += 1
        else:
            for key, value in values.items():
                setattr(record, key, value)
            session.add(record)
            updated += 1

    deactivated = 0
    for item_id, record in existing.items():
        if item_id in seen or not record.is_active:
            continue
        record.is_active = False
        record.updated_at = now
        session.add(record)
        deactivated += 1

    session.commit()
    return {
        "catalog_count": len(herbs),
        "created": created,
        "updated": updated,
        "deactivated": deactivated,
    }
