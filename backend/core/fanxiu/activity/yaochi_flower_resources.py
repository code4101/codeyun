from __future__ import annotations

"""Read and persist Yaochi Flower Festival gift-resource inventory."""

import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from backend.core.fanxiu.activity.exchange_event import is_exchange_activity_active
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from backend.models import FanxiuExchangeActivity, FanxiuPacketBusinessRecord


YAOCHI_FLOWER_FESTIVAL_ACTIVITY_TYPE = "yaochi-flower-festival"
FLOWER_RESOURCE_SNAPSHOT_DOMAIN = "yaochi_flower_resource_snapshot"
FLOWER_RESOURCE_SNAPSHOT_KEY = "current"
_FRIENDSHIP_PATTERN = re.compile(r"赠送给仙缘人物可以提升(\d+)点友好度")


class YaochiFlowerResourceItem(BaseModel):
    item_id: int
    item_ids: list[int] = Field(default_factory=list)
    name: str
    icon: str = ""
    small_icon: str = ""
    description: str = ""
    quality: int | None = None
    quality_color: str = ""
    friendship: int
    count: int | None = None
    total_friendship: int | None = None


class YaochiFlowerResourceSnapshot(BaseModel):
    activity_id: str = ""
    captured_at: str = ""
    source_kind: str = ""
    complete: bool = False
    items: list[YaochiFlowerResourceItem] = Field(default_factory=list)
    total_count: int | None = None
    total_friendship: int | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


def load_yaochi_flower_resource_definitions(
    *,
    export_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load universal friendship gifts accepted by the Yaochi gift screen."""

    root = resolve_fanxiu_export_root(export_root)
    path = root / "parsed_configs" / "Item" / "rows.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("无法读取仙花物品配置") from exc
    rows = payload if isinstance(payload, list) else payload.get("rows", payload)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise ValueError("仙花物品配置格式无效")

    quality_colors: dict[int, str] = {}
    quality_path = root / "parsed_configs" / "Quality" / "rows.json"
    try:
        quality_payload = json.loads(quality_path.read_text(encoding="utf-8"))
        quality_rows = (
            list(quality_payload.values())
            if isinstance(quality_payload, dict)
            else quality_payload
        )
        if isinstance(quality_rows, list):
            quality_colors = {
                int(row.get("id") or 0): str(row.get("color") or "").lstrip("#")
                for row in quality_rows
                if isinstance(row, dict) and int(row.get("id") or 0) > 0
            }
    except (OSError, ValueError, TypeError):
        quality_colors = {}

    definitions: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        description = str(row.get("descript_plain") or row.get("descript") or "")
        match = _FRIENDSHIP_PATTERN.search(description)
        if (
            match is None
            or int(row.get("type") or 0) != 20
            or int(row.get("subType") or 0) != 16
        ):
            continue
        item_id = int(row.get("id") or 0)
        if item_id <= 0:
            continue
        quality = int(row.get("quality") or 0) or None
        definitions.append(
            {
                "item_id": item_id,
                "name": str(row.get("name_plain") or row.get("name") or item_id),
                "icon": str(row.get("icon") or ""),
                "small_icon": str(row.get("smallIcon") or ""),
                "description": description,
                "quality": quality,
                "quality_color": quality_colors.get(quality or 0, ""),
                "friendship": int(match.group(1)),
            }
        )
    definitions.sort(
        key=lambda item: (
            -(int(item.get("quality") or 0)),
            -int(item["friendship"]),
            int(item["item_id"]),
        )
    )
    if not definitions:
        raise ValueError("未找到瑶池花会可用赠礼配置")
    return definitions


def _snapshot_from_definitions(
    definitions: list[dict[str, Any]],
    *,
    counts: dict[int, int] | None = None,
    activity_id: str = "",
    captured_at: str = "",
    evidence: dict[str, Any] | None = None,
) -> YaochiFlowerResourceSnapshot:
    grouped: dict[tuple[str, str, int | None, int], dict[str, Any]] = {}
    for definition in definitions:
        item_id = int(definition["item_id"])
        key = (
            str(definition["name"]),
            str(definition.get("icon") or definition.get("small_icon") or ""),
            definition.get("quality"),
            int(definition["friendship"]),
        )
        group = grouped.setdefault(
            key,
            {**definition, "item_id": item_id, "item_ids": [], "count": 0},
        )
        group["item_ids"].append(item_id)
        if counts is not None:
            group["count"] += counts.get(item_id, 0)

    items = []
    for group in grouped.values():
        count = int(group.pop("count")) if counts is not None else None
        items.append(
            YaochiFlowerResourceItem(
                **group,
                count=count,
                total_friendship=(count * int(group["friendship"]) if count is not None else None),
            )
        )
    return YaochiFlowerResourceSnapshot(
        activity_id=activity_id,
        captured_at=captured_at,
        source_kind="read_only_runtime_memory" if counts is not None else "",
        complete=counts is not None,
        items=items,
        total_count=(sum(item.count or 0 for item in items) if counts is not None else None),
        total_friendship=(
            sum(item.total_friendship or 0 for item in items) if counts is not None else None
        ),
        evidence=dict(evidence or {}),
    )


def _normalize_snapshot_items(
    snapshot: YaochiFlowerResourceSnapshot,
    *,
    definitions: list[dict[str, Any]] | None = None,
) -> YaochiFlowerResourceSnapshot:
    definition_by_id = {
        int(item["item_id"]): item for item in (definitions or [])
    }
    grouped: dict[tuple[str, str, int | None, int], YaochiFlowerResourceItem] = {}
    for item in snapshot.items:
        definition = definition_by_id.get(item.item_id, {})
        icon = item.icon or str(definition.get("icon") or "")
        key = (item.name, icon or item.small_icon, item.quality, item.friendship)
        source_ids = list(dict.fromkeys(item.item_ids or [item.item_id]))
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = item.model_copy(
                update={
                    "item_ids": source_ids,
                    "icon": icon,
                    "quality_color": (
                        item.quality_color or str(definition.get("quality_color") or "")
                    ),
                }
            )
            continue
        existing.item_ids = list(dict.fromkeys([*existing.item_ids, *source_ids]))
        if existing.count is None or item.count is None:
            existing.count = None
            existing.total_friendship = None
        else:
            existing.count += item.count
            existing.total_friendship = existing.count * existing.friendship

    items = list(grouped.values())
    return snapshot.model_copy(update={"items": items})


def read_yaochi_flower_resources_runtime_snapshot(
    *,
    export_root: str | Path | None = None,
) -> YaochiFlowerResourceSnapshot:
    definitions = load_yaochi_flower_resource_definitions(export_root=export_root)
    counts, evidence = read_backpack_item_counts(
        (item["item_id"] for item in definitions),
        manager_key="yaochi-flower-backpack",
    )
    return _snapshot_from_definitions(
        definitions,
        counts=counts,
        captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        evidence=evidence,
    )


def load_yaochi_flower_resource_snapshot(
    session: Session,
) -> YaochiFlowerResourceSnapshot:
    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == FLOWER_RESOURCE_SNAPSHOT_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == FLOWER_RESOURCE_SNAPSHOT_KEY,
        )
    ).first()
    if row is not None and isinstance(row.payload, dict):
        snapshot = YaochiFlowerResourceSnapshot.model_validate(row.payload)
        return _normalize_snapshot_items(
            snapshot,
            definitions=load_yaochi_flower_resource_definitions(),
        )
    return _snapshot_from_definitions(load_yaochi_flower_resource_definitions())


def collect_and_store_yaochi_flower_resource_snapshot(
    session: Session,
    *,
    activity_id: str,
    today: date | None = None,
    observed_snapshot: YaochiFlowerResourceSnapshot | dict[str, Any] | None = None,
) -> YaochiFlowerResourceSnapshot:
    activity = session.get(FanxiuExchangeActivity, activity_id)
    if activity is None or activity.activity_type != YAOCHI_FLOWER_FESTIVAL_ACTIVITY_TYPE:
        raise ValueError("瑶池花会活动不存在")
    current_day = today or datetime.now().astimezone().date()
    if not is_exchange_activity_active(activity, today=current_day):
        raise ValueError("瑶池花会活动不在有效日期内")

    try:
        snapshot = (
            read_yaochi_flower_resources_runtime_snapshot()
            if observed_snapshot is None
            else YaochiFlowerResourceSnapshot.model_validate(observed_snapshot)
        )
    except FanxiuRuntimeMemoryError as exc:
        raise ValueError(str(exc)) from exc
    snapshot.activity_id = activity_id
    snapshot = _normalize_snapshot_items(
        snapshot,
        definitions=load_yaochi_flower_resource_definitions(),
    )
    if not snapshot.complete or any(item.count is None for item in snapshot.items):
        raise ValueError("仙花资源运行态数据不完整，已保留上次快照")

    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == FLOWER_RESOURCE_SNAPSHOT_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == FLOWER_RESOURCE_SNAPSHOT_KEY,
        )
    ).first()
    now = time.time()
    if row is None:
        row = FanxiuPacketBusinessRecord(
            domain=FLOWER_RESOURCE_SNAPSHOT_DOMAIN,
            record_key=FLOWER_RESOURCE_SNAPSHOT_KEY,
            source_kind=snapshot.source_kind,
            entity_name="瑶池花会仙花资源",
            captured_at=snapshot.captured_at,
            captured_date=snapshot.captured_at[:10],
            payload=snapshot.model_dump(mode="json"),
            evidence=dict(snapshot.evidence),
            created_at=now,
            updated_at=now,
        )
    else:
        row.source_kind = snapshot.source_kind
        row.captured_at = snapshot.captured_at
        row.captured_date = snapshot.captured_at[:10]
        row.payload = snapshot.model_dump(mode="json")
        row.evidence = dict(snapshot.evidence)
        row.updated_at = now
    session.add(row)
    session.commit()
    return snapshot
