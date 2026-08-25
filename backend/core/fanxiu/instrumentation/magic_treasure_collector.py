from __future__ import annotations

"""Produce and persist the current magic-treasure hall database snapshot."""

import time
from datetime import date
from typing import Any

from sqlmodel import Session

from backend.db import engine
from backend.core.fanxiu.catalog.item import load_fanxiu_talisman_item_knowledge
from backend.core.fanxiu.catalog.resources import FanxiuResourceError
from backend.core.fanxiu.catalog.inventory_snapshot_store import (
    load_inventory_hall_snapshot,
    upsert_inventory_hall_snapshot,
)

from .magic_treasure import read_magic_treasure_hall_runtime


MAGIC_TREASURE_HALL_KEY = "magic_treasure_hall"
_SECTIONS = ("fabao", "xiantiangubao", "houtiangubao")
_MANUAL_FIELDS = ("main_use", "acquisition", "note_id")
_CATALOG_FIELDS = (
    "catalog_item_id",
    "catalog_name",
    "catalog_icon",
    "catalog_description",
    "catalog_effect_description",
    "catalog_quality",
    "catalog_quality_name",
    "catalog_quality_color",
    "catalog_refine_item_id",
    "catalog_refine_name",
)


def _item_sort_key(item: dict[str, Any]) -> tuple[int, int, int, int, str]:
    catalog_quality = item.get("catalog_quality")
    quality = int(catalog_quality if catalog_quality not in (None, "") else item.get("quality") or 0)
    return (
        -int(bool(item.get("owned"))),
        -quality,
        -int(item.get("rank") or 0),
        -int(item.get("wujing_level") or 0),
        str(item.get("name") or ""),
    )


def _existing_items(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    payload = snapshot if isinstance(snapshot, dict) else {}
    return [
        dict(item)
        for section in _SECTIONS
        for item in payload.get(section) or []
        if isinstance(item, dict)
    ]


def build_magic_treasure_database_snapshot(
    runtime: dict[str, Any],
    existing: dict[str, Any] | None = None,
    talisman_knowledge: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not runtime.get("complete") or not runtime.get("items"):
        raise ValueError(str(runtime.get("reason") or "法宝运行态快照不完整"))
    old_items = _existing_items(existing)
    old_by_talisman_id = {
        int(item["talisman_id"]): item
        for item in old_items
        if item.get("talisman_id") not in (None, "")
    }
    old_by_name = {
        str(item.get("name") or "").strip(): item
        for item in old_items
        if str(item.get("name") or "").strip()
    }
    sections: dict[str, list[dict[str, Any]]] = {key: [] for key in _SECTIONS}
    captured_date = str(runtime.get("captured_at") or date.today().isoformat())[:10]
    knowledge_by_id = talisman_knowledge or {}
    runtime_ids = {
        int(item.get("talisman_id") or 0)
        for item in runtime.get("items") or []
        if isinstance(item, dict) and int(item.get("talisman_id") or 0) > 0
    }
    if runtime.get("owned_only"):
        missing_owned_ids = sorted(
            int(item.get("talisman_id") or 0)
            for item in old_items
            if bool(item.get("owned"))
            and int(item.get("talisman_id") or 0) not in runtime_ids
        )
        if missing_owned_ids:
            raise ValueError(f"法宝运行态遗漏既有法宝：{missing_owned_ids[:8]}")
    for raw_item in runtime.get("items") or []:
        item = dict(raw_item)
        talisman_id = int(item.get("talisman_id") or 0)
        prior = old_by_talisman_id.get(int(item.get("talisman_id") or 0)) or old_by_name.get(
            str(item.get("name") or "").strip()
        ) or {}
        item = {**prior, **item}
        for field in _MANUAL_FIELDS:
            if prior.get(field) not in (None, ""):
                item[field] = prior[field]
        if not runtime.get("effects_complete") and prior.get("upgrade_effects"):
            rank = int(item.get("rank") or 0)
            item["upgrade_effects"] = [
                {
                    **dict(effect),
                    "unlocked": int(effect.get("stage") or 0) <= rank,
                    "current": int(effect.get("stage") or 0) == rank,
                }
                for effect in prior.get("upgrade_effects") or []
                if isinstance(effect, dict)
            ]
            if prior.get("original_effect"):
                item["original_effect"] = prior["original_effect"]
        knowledge = knowledge_by_id.get(talisman_id) or {}
        for field in _CATALOG_FIELDS:
            if knowledge.get(field) not in (None, ""):
                item[field] = knowledge[field]
        item["knowledge_source"] = "item_catalog" if knowledge else "runtime_memory"
        item["date"] = str(prior.get("date") or captured_date)
        section = str(item.pop("section_key", "fabao"))
        if section not in sections:
            section = "fabao"
        sections[section].append(item)
    if runtime.get("owned_only"):
        for prior in old_items:
            talisman_id = int(prior.get("talisman_id") or 0)
            if talisman_id in runtime_ids or bool(prior.get("owned")):
                continue
            item = dict(prior)
            category = str(item.get("category") or "法宝")
            section = {
                "法宝": "fabao",
                "先天古宝": "xiantiangubao",
                "后天古宝": "houtiangubao",
            }.get(category, "fabao")
            sections[section].append(item)
    for items in sections.values():
        items.sort(key=_item_sort_key)
    return {
        **sections,
        "runtime_source": str(runtime.get("source") or "runtime_memory"),
        "runtime_complete": True,
        "runtime_error": "",
        "runtime_updated_at": float(runtime.get("captured_timestamp") or time.time()),
        "runtime_item_count": sum(len(items) for items in sections.values()),
        "runtime_debug": {
            **dict(runtime.get("evidence") or {}),
            "elapsed_seconds": float(runtime.get("elapsed_seconds") or 0),
            "runtime_owned_item_count": len(runtime_ids),
            "catalogue_item_count": sum(len(items) for items in sections.values()),
        },
    }


def collect_magic_treasure_snapshot_once() -> dict[str, Any]:
    runtime = read_magic_treasure_hall_runtime()
    if not runtime.get("complete"):
        raise RuntimeError(str(runtime.get("reason") or "法宝动态插桩数据尚未完整加载"))
    knowledge_error = ""
    try:
        talisman_knowledge = load_fanxiu_talisman_item_knowledge(rebuild_missing=False)
    except (FanxiuResourceError, OSError, ValueError) as exc:
        # Static encyclopedia data is enrichment only.  A missing export must
        # not prevent the complete, read-only runtime snapshot from persisting.
        talisman_knowledge = {}
        knowledge_error = f"{type(exc).__name__}: {exc}"
    with Session(engine) as session:
        existing = load_inventory_hall_snapshot(session, MAGIC_TREASURE_HALL_KEY)
        snapshot = build_magic_treasure_database_snapshot(runtime, existing, talisman_knowledge)
        snapshot["runtime_debug"]["item_catalog_talisman_count"] = len(talisman_knowledge)
        snapshot["runtime_debug"]["item_catalog_error"] = knowledge_error
        upsert_inventory_hall_snapshot(
            session,
            MAGIC_TREASURE_HALL_KEY,
            snapshot,
            source_kind="dynamic_instrumentation",
            entity_name="法宝殿",
            require_complete_runtime=True,
        )
    return snapshot
