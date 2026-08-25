from __future__ import annotations

"""Build and persist a database-backed wardrobe snapshot."""

import time
from datetime import date
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.catalog.inventory import load_wardrobe_hall
from backend.core.fanxiu.catalog.inventory_snapshot_store import (
    load_inventory_hall_snapshot,
    upsert_inventory_hall_snapshot,
)
from backend.core.fanxiu.catalog.item import load_fanxiu_item_catalog
from backend.core.fanxiu.catalog.resources import FanxiuResourceError
from backend.db import engine

from .wardrobe import read_wardrobe_hall_runtime


WARDROBE_HALL_KEY = "wardrobe_hall"
_SECTIONS = ("shizhuang", "wuqi", "huanshen", "beishi", "yuqi")
_MANUAL_FIELDS = ("main_use", "acquisition", "note_id", "date")


def _catalog_by_fashion_id() -> dict[int, dict[str, Any]]:
    catalog = load_fanxiu_item_catalog(rebuild_missing=False)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for card in catalog.get("cards") or []:
        if not isinstance(card, dict):
            continue
        fashion_id = card.get("linked_fashion_id")
        if fashion_id in (None, ""):
            continue
        grouped.setdefault(int(fashion_id), []).append(card)
    result: dict[int, dict[str, Any]] = {}
    for fashion_id, cards in grouped.items():
        result[fashion_id] = sorted(
            cards,
            key=lambda card: (
                int(card.get("id") or 0) != int(next((
                    detail.get("item_id")
                    for detail in card.get("effect_details") or []
                    if isinstance(detail, dict) and detail.get("kind") == "fashion"
                ), card.get("id") or 0)),
                -len(str(card.get("description") or "")),
            ),
        )[0]
    return result


def _existing_items(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    payload = snapshot if isinstance(snapshot, dict) else {}
    return [
        dict(item)
        for section in _SECTIONS
        for item in payload.get(section) or []
        if isinstance(item, dict)
    ]


def _sort_key(item: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        -int(bool(item.get("owned"))),
        -int(item.get("quality") or 0),
        -int(item.get("rank") or 0),
        str(item.get("name") or ""),
    )


def build_wardrobe_database_snapshot(
    runtime: dict[str, Any],
    existing: dict[str, Any] | None = None,
    catalog_by_fashion_id: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if runtime.get("complete") is not True or not runtime.get("items"):
        raise ValueError(str(runtime.get("reason") or "衣装阁运行态快照不完整"))
    old_items = _existing_items(existing)
    old_by_fashion_id = {
        int(item["fashion_id"]): item
        for item in old_items
        if item.get("fashion_id") not in (None, "")
    }
    old_by_item_id = {
        int(item["item_id"]): item
        for item in old_items
        if item.get("item_id") not in (None, "", 0)
    }
    old_by_name = {
        str(item.get("name") or "").strip(): item
        for item in old_items
        if str(item.get("name") or "").strip()
    }
    sections: dict[str, list[dict[str, Any]]] = {key: [] for key in _SECTIONS}
    knowledge = catalog_by_fashion_id or {}
    captured_date = date.today().isoformat()
    for raw in runtime.get("items") or []:
        item = dict(raw)
        fashion_id = int(item.get("fashion_id") or 0)
        item_id = int(item.get("item_id") or 0)
        card = dict(knowledge.get(fashion_id) or {})
        prior = (
            old_by_fashion_id.get(fashion_id)
            or old_by_item_id.get(item_id)
            or old_by_name.get(str(card.get("name") or item.get("name") or "").strip())
            or {}
        )
        item.update(
            {
                "id": str(fashion_id),
                "name": str(card.get("name") or item.get("name") or f"衣装 {fashion_id}"),
                "shenlian": 0,
                "type": "",
                "quality": card.get("quality"),
                "catalog_icon": str(card.get("icon") or ""),
                "catalog_description": str(card.get("description") or ""),
                "catalog_effect_description": str(card.get("effect_description") or ""),
                "catalog_quality_name": str(card.get("quality_name") or ""),
                "catalog_quality_color": str(card.get("quality_color") or ""),
                "knowledge_source": "item_catalog" if card else "runtime_memory",
                "date": str(prior.get("date") or captured_date),
            }
        )
        for field in _MANUAL_FIELDS:
            if prior.get(field) not in (None, ""):
                item[field] = prior[field]
        section = str(item.pop("section_key", ""))
        if section not in sections:
            raise ValueError(f"衣装 {fashion_id} 分类无效：{section}")
        sections[section].append(item)
    for rows in sections.values():
        rows.sort(key=_sort_key)
    return {
        **sections,
        "runtime_source": str(runtime.get("source") or "loaded_runtime_memory"),
        "runtime_complete": True,
        "runtime_error": "",
        "runtime_updated_at": float(runtime.get("captured_timestamp") or time.time()),
        "runtime_item_count": sum(len(rows) for rows in sections.values()),
        "runtime_owned_count": sum(
            int(bool(item.get("owned"))) for rows in sections.values() for item in rows
        ),
        "runtime_debug": {
            **dict(runtime.get("evidence") or {}),
            "elapsed_seconds": float(runtime.get("elapsed_seconds") or 0),
            "item_catalog_fashion_count": len(knowledge),
        },
    }


def collect_wardrobe_snapshot_once() -> dict[str, Any]:
    runtime = read_wardrobe_hall_runtime()
    if runtime.get("complete") is not True:
        raise RuntimeError(str(runtime.get("reason") or "衣装阁动态插桩数据尚未完整加载"))
    knowledge_error = ""
    try:
        knowledge = _catalog_by_fashion_id()
    except (FanxiuResourceError, OSError, ValueError) as exc:
        knowledge = {}
        knowledge_error = f"{type(exc).__name__}: {exc}"
    with Session(engine) as session:
        existing = load_inventory_hall_snapshot(session, WARDROBE_HALL_KEY)
        if not existing:
            existing = load_wardrobe_hall()
        snapshot = build_wardrobe_database_snapshot(runtime, existing, knowledge)
        snapshot["runtime_debug"]["item_catalog_error"] = knowledge_error
        upsert_inventory_hall_snapshot(
            session,
            WARDROBE_HALL_KEY,
            snapshot,
            source_kind="dynamic_instrumentation",
            entity_name="衣装阁",
            require_complete_runtime=True,
        )
    return snapshot
