"""Persist the cumulative storage-bag atlas from complete Runtime UI snapshots."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping

from backend.core.settings import get_settings


_SCHEMA_VERSION = 2
_STORE_LOCK = threading.RLock()
_CATALOG_FIELDS = (
    "id", "item_id", "name", "quality", "quality_name", "quality_color",
    "quality_icon", "type", "type_name", "icon", "small_icon", "description",
    "effect_description", "effect_detail_preview", "can_use", "sort", "stone_value",
)


def storage_bag_atlas_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    return get_settings().data_dir / "fanxiu" / "wiki" / "storage_bag_atlas.json"


def _catalog_card(cards_by_id: Mapping[Any, Any], base_id: int) -> dict[str, Any] | None:
    raw = cards_by_id.get(str(base_id), cards_by_id.get(base_id))
    if not isinstance(raw, Mapping):
        return None
    return {field: raw.get(field) for field in _CATALOG_FIELDS if raw.get(field) is not None}


def _read_store(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": _SCHEMA_VERSION, "updated_at": None, "items": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"储物袋图鉴文件不可读：{exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("储物袋图鉴文件结构无效")
    payload["schema_version"] = _SCHEMA_VERSION
    for raw_row in payload["items"]:
        if isinstance(raw_row, dict):
            # Average yield is a database projection over verified immutable
            # opening events. The cumulative atlas owns item discovery only.
            raw_row.pop("average_yield", None)
    return payload


def _write_store(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def build_storage_bag_catalog_snapshot(
    runtime_snapshot: Mapping[str, Any],
    cards_by_id: Mapping[Any, Any],
    *,
    captured_at: str,
) -> dict[str, Any]:
    """Aggregate complete live UI rows by base ID while preserving first UI order."""

    if not runtime_snapshot.get("complete"):
        raise ValueError("储物袋 Runtime 快照不完整")
    if runtime_snapshot.get("source") != "active_backpack_panel_item_info_list":
        raise ValueError("储物袋 Runtime 快照来源不是活动面板清单")
    raw_items = runtime_snapshot.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("储物袋 Runtime 快照缺少 items")

    current_by_id: dict[int, dict[str, Any]] = {}
    previous_ui_index = -1
    for raw in raw_items:
        if not isinstance(raw, Mapping) or raw.get("is_padding"):
            continue
        ui_index = raw.get("ui_index")
        base_id = raw.get("base_id")
        instance_id = raw.get("instance_id")
        num = raw.get("num")
        if (
            not isinstance(ui_index, int)
            or ui_index <= previous_ui_index
            or not isinstance(base_id, int)
            or not str(instance_id or "").isdigit()
            or not isinstance(num, int)
        ):
            raise ValueError("储物袋 Runtime 物品行缺少有序 ui_index/id/base_id/num")
        previous_ui_index = ui_index
        current = current_by_id.get(base_id)
        if current is None:
            current_by_id[base_id] = {
                "runtime_order": ui_index + 1,
                "base_id": base_id,
                "num": num,
                "instance_count": 1,
                "item": _catalog_card(cards_by_id, base_id),
            }
        else:
            current["num"] += num
            current["instance_count"] += 1

    items = list(current_by_id.values())
    unresolved_item_ids = [row["base_id"] for row in items if row["item"] is None]
    return {
        "source": runtime_snapshot.get("source"),
        "complete": True,
        "captured_at": captured_at,
        "tab": runtime_snapshot.get("tab"),
        "stack_count": sum(row["instance_count"] for row in items),
        "current_type_count": len(items),
        "declared_slot_count": runtime_snapshot.get("declared_slot_count"),
        "trailing_missing_indices": runtime_snapshot.get("trailing_missing_indices") or [],
        "unresolved_catalog_count": len(unresolved_item_ids),
        "unresolved_item_ids": sorted(unresolved_item_ids),
        "items": items,
        "evidence": runtime_snapshot.get("evidence"),
        "performance": runtime_snapshot.get("performance"),
    }


def _atlas_projection(store: Mapping[str, Any], *, runtime_available: bool, reason: str = "") -> dict[str, Any]:
    items = sorted(
        (dict(row) for row in store.get("items") or [] if isinstance(row, Mapping)),
        key=lambda row: (int(row.get("atlas_order") or 0), int(row.get("base_id") or 0)),
    )
    return {
        "source": "storage_bag_runtime_atlas",
        "complete": True,
        "runtime_available": runtime_available,
        "runtime_reason": reason,
        "captured_at": store.get("updated_at"),
        "stack_count": store.get("stack_count", 0),
        "current_type_count": sum(1 for row in items if int(row.get("num") or 0) > 0),
        "zero_count": sum(1 for row in items if int(row.get("num") or 0) == 0),
        "atlas_count": len(items),
        "declared_slot_count": store.get("declared_slot_count"),
        "unresolved_catalog_count": sum(1 for row in items if not isinstance(row.get("item"), Mapping)),
        "items": items,
        "evidence": store.get("evidence"),
    }


def sync_storage_bag_atlas(
    runtime_snapshot: Mapping[str, Any],
    cards_by_id: Mapping[Any, Any],
    *,
    captured_at: str,
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Merge a complete live snapshot into the cumulative atlas and persist it atomically."""

    live = build_storage_bag_catalog_snapshot(runtime_snapshot, cards_by_id, captured_at=captured_at)
    resolved_path = storage_bag_atlas_path(path)
    with _STORE_LOCK:
        store = _read_store(resolved_path)
        existing_rows = [dict(row) for row in store.get("items") or [] if isinstance(row, Mapping)]
        by_id = {int(row["base_id"]): row for row in existing_rows if str(row.get("base_id") or "").isdigit()}
        next_order = max((int(row.get("atlas_order") or 0) for row in existing_rows), default=0)
        for row in existing_rows:
            row.update({"num": 0, "runtime_order": None, "instance_count": 0, "present": False})

        for live_row in live["items"]:
            base_id = int(live_row["base_id"])
            row = by_id.get(base_id)
            if row is None:
                next_order += 1
                row = {
                    "atlas_order": next_order,
                    "base_id": base_id,
                    "first_seen_at": captured_at,
                }
                existing_rows.append(row)
                by_id[base_id] = row
            row.update(
                {
                    "num": int(live_row["num"]),
                    "runtime_order": int(live_row["runtime_order"]),
                    "instance_count": int(live_row["instance_count"]),
                    "present": True,
                    "last_seen_at": captured_at,
                }
            )
            if live_row.get("item") is not None:
                row["item"] = live_row["item"]

        payload = {
            "schema_version": _SCHEMA_VERSION,
            "updated_at": captured_at,
            "stack_count": live["stack_count"],
            "declared_slot_count": live.get("declared_slot_count"),
            "evidence": live.get("evidence"),
            "items": sorted(existing_rows, key=lambda row: int(row.get("atlas_order") or 0)),
        }
        _write_store(resolved_path, payload)
        return _atlas_projection(payload, runtime_available=True)


def load_storage_bag_atlas(
    *,
    path: str | Path | None = None,
    runtime_available: bool = False,
    reason: str = "",
) -> dict[str, Any] | None:
    with _STORE_LOCK:
        store = _read_store(storage_bag_atlas_path(path))
        if not store.get("items"):
            return None
        return _atlas_projection(store, runtime_available=runtime_available, reason=reason)


def delete_storage_bag_atlas_item(base_id: int, *, path: str | Path | None = None) -> dict[str, Any]:
    """Delete an absent atlas type; a future real appearance will register it again."""

    resolved_path = storage_bag_atlas_path(path)
    with _STORE_LOCK:
        store = _read_store(resolved_path)
        rows = [dict(row) for row in store.get("items") or [] if isinstance(row, Mapping)]
        target = next((row for row in rows if int(row.get("base_id") or 0) == base_id), None)
        if target is None:
            raise KeyError(base_id)
        if int(target.get("num") or 0) > 0:
            raise ValueError("当前仍持有该物品，不能从图鉴删除")
        store["items"] = [row for row in rows if int(row.get("base_id") or 0) != base_id]
        _write_store(resolved_path, store)
        return {"deleted": True, "base_id": base_id, "atlas_count": len(store["items"])}
