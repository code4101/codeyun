from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).with_name("strategy_research_catalog.json")


def load_strategy_research_catalog() -> dict[str, Any]:
    """Load the curated strategy research catalog from the source tree."""
    with CATALOG_PATH.open("r", encoding="utf-8") as file:
        catalog = json.load(file)
    _validate_strategy_research_catalog(catalog)
    return catalog


def list_strategy_research_items(
    *,
    family: str | None = None,
    status: str | None = None,
    market: str | None = None,
    min_priority: int | None = None,
) -> dict[str, Any]:
    catalog = load_strategy_research_catalog()
    normalized_family = _normalize_filter_value(family)
    normalized_status = _normalize_filter_value(status)
    normalized_market = _normalize_filter_value(market)
    priority_floor = int(min_priority) if min_priority is not None else None

    strategies = []
    for item in catalog["strategies"]:
        if normalized_family and normalized_family not in {str(value).lower() for value in item.get("family", [])}:
            continue
        if normalized_status and normalized_status != str(item.get("status", "")).lower():
            continue
        if normalized_market and normalized_market not in {str(value).lower() for value in item.get("market_scope", [])}:
            continue
        if priority_floor is not None and int(item.get("priority", 999)) > priority_floor:
            continue
        strategies.append(item)

    return {
        "schema_version": catalog["schema_version"],
        "updated_at": catalog["updated_at"],
        "purpose": catalog["purpose"],
        "source_groups": catalog["source_groups"],
        "count": len(strategies),
        "items": strategies,
    }


def get_strategy_research_item(strategy_id: str) -> dict[str, Any] | None:
    normalized_id = strategy_id.strip()
    if not normalized_id:
        return None
    catalog = load_strategy_research_catalog()
    return next((item for item in catalog["strategies"] if item.get("id") == normalized_id), None)


def list_strategy_research_backlog(*, max_priority: int = 3) -> dict[str, Any]:
    """Return a compact execution-oriented backlog for the next research jobs."""
    result = list_strategy_research_items(min_priority=max_priority)
    items = []
    for item in sorted(result["items"], key=lambda value: (int(value.get("priority", 999)), str(value.get("id", "")))):
        items.append({
            "id": item["id"],
            "title": item["title"],
            "status": item["status"],
            "priority": item["priority"],
            "family": item.get("family", []),
            "market_scope": item.get("market_scope", []),
            "instrument_scope": item.get("instrument_scope", []),
            "existing_mapping": item.get("existing_mapping", {}),
            "next_validation": item.get("validation_plan", [])[:3],
        })
    return {
        "updated_at": result["updated_at"],
        "max_priority": max_priority,
        "count": len(items),
        "items": items,
    }


def _normalize_filter_value(value: str | None) -> str:
    return (value or "").strip().lower()


def _validate_strategy_research_catalog(catalog: dict[str, Any]) -> None:
    if not isinstance(catalog.get("schema_version"), int):
        raise ValueError("strategy research catalog schema_version must be an integer")
    if not isinstance(catalog.get("source_groups"), list):
        raise ValueError("strategy research catalog source_groups must be a list")
    if not isinstance(catalog.get("strategies"), list):
        raise ValueError("strategy research catalog strategies must be a list")

    source_ids = set()
    for source in catalog["source_groups"]:
        source_id = str(source.get("id") or "").strip()
        if not source_id:
            raise ValueError("strategy research source id is required")
        if source_id in source_ids:
            raise ValueError(f"duplicate strategy research source id: {source_id}")
        source_ids.add(source_id)

    strategy_ids = set()
    for item in catalog["strategies"]:
        strategy_id = str(item.get("id") or "").strip()
        if not strategy_id:
            raise ValueError("strategy research item id is required")
        if strategy_id in strategy_ids:
            raise ValueError(f"duplicate strategy research item id: {strategy_id}")
        strategy_ids.add(strategy_id)
        for key in ("title", "status", "priority", "hypothesis", "rules", "data_requirements", "validation_plan"):
            if key not in item:
                raise ValueError(f"strategy research item {strategy_id} missing {key}")
        for source_id in item.get("sources", []):
            if source_id not in source_ids:
                raise ValueError(f"strategy research item {strategy_id} references unknown source {source_id}")
