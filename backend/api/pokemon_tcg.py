from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from backend.core.settings import get_settings


DATASET_ID = "childhood_base_jungle_fossil_rocket"
router = APIRouter()


def _dataset_root() -> Path:
    return get_settings().data_dir / "pokemon_tcg" / DATASET_ID


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"数据快照损坏：{path.name}") from exc


def _cards() -> list[dict[str, Any]]:
    data = _read_json(_dataset_root() / "raw_cards.json", [])
    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail="raw_cards.json 不是列表")
    return [item for item in data if isinstance(item, dict)]


def _card_matches(card: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        str(card.get(key) or "")
        for key in (
            "official_name",
            "display_title",
            "pokemon_species",
            "set_name",
            "official_id",
            "attacks_text",
            "flavor_text",
        )
    ).lower()
    return query.lower() in haystack


@router.get("/meta")
def get_pokemon_tcg_meta() -> dict[str, Any]:
    root = _dataset_root()
    manifest = _read_json(root / "manifest.json", {})
    progress = _read_json(root / "progress.json", {})
    cards = _cards()
    set_counts: dict[str, int] = {}
    for card in cards:
        set_slug = str(card.get("set_slug") or "unknown")
        set_counts[set_slug] = set_counts.get(set_slug, 0) + 1
    return {
        "dataset_id": DATASET_ID,
        "root": str(root),
        "manifest": manifest,
        "progress": progress,
        "card_count": len(cards),
        "set_counts": set_counts,
    }


@router.get("/cards")
def list_pokemon_tcg_cards(
    q: str = "",
    set_slug: str = Query("", alias="set"),
    page: int = Query(1, ge=1),
    page_size: int = Query(60, ge=1, le=240),
) -> dict[str, Any]:
    cards = _cards()
    filtered = [
        card
        for card in cards
        if (not set_slug or card.get("set_slug") == set_slug)
        and _card_matches(card, q.strip())
    ]
    filtered.sort(
        key=lambda card: (
            str(card.get("set_slug") or ""),
            int(card.get("official_number")) if str(card.get("official_number") or "").isdigit() else 999,
            str(card.get("source_card_slug") or ""),
        )
    )
    start = (page - 1) * page_size
    items = filtered[start:start + page_size]
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": len(filtered),
    }


@router.get("/images/{relative_path:path}")
def get_pokemon_tcg_image(relative_path: str) -> FileResponse:
    root = (_dataset_root() / "images").resolve(strict=False)
    target = (root / relative_path).resolve(strict=False)
    if root not in target.parents and target != root:
        raise HTTPException(status_code=400, detail="图片路径非法")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(target)
