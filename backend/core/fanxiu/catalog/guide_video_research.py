from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from backend.core.settings import get_settings


GUIDE_VIDEO_RESEARCH_SCHEMA_VERSION = 1
_PLAY_COUNT_RE = re.compile(r"([\d.]+)")


def guide_video_research_snapshot_path() -> Path:
    return get_settings().data_dir / "fanxiu" / "guide-videos" / "research.json"


def _empty_research_snapshot() -> dict[str, Any]:
    return {
        "schema_version": GUIDE_VIDEO_RESEARCH_SCHEMA_VERSION,
        "status": "idle",
        "target_count": 0,
        "done_count": 0,
        "updated_at": 0.0,
        "error": "",
        "items": [],
    }


def load_guide_video_research_snapshot(path: str | Path | None = None) -> dict[str, Any]:
    snapshot_path = Path(path) if path is not None else guide_video_research_snapshot_path()
    if not snapshot_path.is_file():
        return _empty_research_snapshot()
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_research_snapshot()
    if not isinstance(payload, dict):
        return _empty_research_snapshot()
    snapshot = _empty_research_snapshot()
    snapshot.update(payload)
    snapshot["schema_version"] = GUIDE_VIDEO_RESEARCH_SCHEMA_VERSION
    snapshot["items"] = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    return snapshot


def save_guide_video_research_snapshot(
    snapshot: dict[str, Any], path: str | Path | None = None
) -> Path:
    snapshot_path = Path(path) if path is not None else guide_video_research_snapshot_path()
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _empty_research_snapshot()
    normalized.update(snapshot)
    normalized["schema_version"] = GUIDE_VIDEO_RESEARCH_SCHEMA_VERSION
    normalized["items"] = [item for item in snapshot.get("items") or [] if isinstance(item, dict)]
    normalized["done_count"] = sum(item.get("status") == "done" for item in normalized["items"])
    normalized["target_count"] = max(int(normalized.get("target_count") or 0), len(normalized["items"]))
    normalized["updated_at"] = float(normalized.get("updated_at") or time.time())
    temporary = snapshot_path.with_name(
        f".{snapshot_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temporary.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, snapshot_path)
    return snapshot_path


def parse_guide_video_play_count(value: Any) -> int:
    text = str(value or "").replace(",", "").strip()
    match = _PLAY_COUNT_RE.search(text)
    if not match:
        return 0
    count = float(match.group(1))
    if "万" in text:
        count *= 10_000
    elif "亿" in text:
        count *= 100_000_000
    return int(count)


def guide_video_priority(item: dict[str, Any]) -> tuple[int, int, int, int]:
    role_rank = {"original": 4, "clip": 3, "guide": 2, "official": 1}.get(
        str(item.get("source_role") or ""), 0
    )
    return (
        role_rank,
        1 if item.get("is_pinned") else 0,
        parse_guide_video_play_count(item.get("play_text")),
        int(item.get("published_at") or 0),
    )


def rank_guide_video_research_candidates(
    catalog_items: Iterable[dict[str, Any]],
    research_items: Iterable[dict[str, Any]] = (),
    *,
    limit: int = 20,
    include_roles: Iterable[str] = ("original", "clip", "guide"),
) -> list[dict[str, Any]]:
    completed = {
        str(item.get("item_id") or "")
        for item in research_items
        if str(item.get("status") or "") == "done"
    }
    roles = {str(role) for role in include_roles}
    candidates = [
        item
        for item in catalog_items
        if str(item.get("item_id") or "") not in completed
        and str(item.get("source_role") or "") in roles
    ]
    candidates.sort(key=guide_video_priority, reverse=True)
    return candidates[: max(int(limit), 0)]


def research_by_item_id(snapshot: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    current = snapshot if snapshot is not None else load_guide_video_research_snapshot()
    return {
        str(item.get("item_id") or ""): item
        for item in current.get("items") or []
        if str(item.get("item_id") or "")
    }


def upsert_guide_video_research(
    record: dict[str, Any], path: str | Path | None = None
) -> dict[str, Any]:
    item_id = str(record.get("item_id") or "").strip()
    if not item_id:
        raise ValueError("研究记录缺少 item_id")
    snapshot = load_guide_video_research_snapshot(path)
    by_id = research_by_item_id(snapshot)
    by_id[item_id] = {**by_id.get(item_id, {}), **record, "item_id": item_id}
    items = sorted(
        by_id.values(),
        key=lambda item: (float(item.get("analyzed_at") or 0), item["item_id"]),
        reverse=True,
    )
    status = "error" if any(item.get("status") == "error" for item in items) else "done"
    updated = {**snapshot, "status": status, "updated_at": time.time(), "items": items}
    save_guide_video_research_snapshot(updated, path)
    return updated


def resolve_research_artifact(item_id: str, kind: str) -> Path:
    record = research_by_item_id().get(str(item_id or ""))
    if record is None:
        raise FileNotFoundError(item_id)
    field = {
        "media": "local_video_path",
        "document": "document_path",
        "transcript": "transcript_path",
    }.get(kind)
    if field is None:
        raise ValueError(f"不支持的研究文件类型：{kind}")
    path = Path(str(record.get(field) or ""))
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.resolve()
