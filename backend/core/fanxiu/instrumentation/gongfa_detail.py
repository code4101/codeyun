from __future__ import annotations

"""Project useful relationship details for one learned GongFa book."""

from functools import lru_cache
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.catalog.hot_update import (
    render_fanxiu_gongfa_homemake_static_detail,
)
from backend.core.fanxiu.catalog.inventory_snapshot_store import (
    load_inventory_hall_snapshot,
)
from backend.core.fanxiu.instrumentation.runtime_memory import as_int
from backend.core.fanxiu.instrumentation.xianyuan_atlas import (
    _item_index,
    _target_support_by_item,
)


_ROLE_NAMES = {
    "main": "主书",
    "xian": "仙书",
    "side": "副书",
    "grid": "心法格",
}
_CATEGORY_NAMES = {
    "gongfa": "神通",
    "xinfa": "心法",
}


@lru_cache(maxsize=256)
def _render_effect_rows(
    book_id: int,
    star: int,
    jie: int,
    pin: int,
) -> tuple[dict[str, Any], ...]:
    try:
        detail = render_fanxiu_gongfa_homemake_static_detail(
            book_id,
            star=max(1, star),
            jie=max(1, jie),
            pin=max(1, pin),
            include_inactive=False,
        )
    except Exception:
        return ()
    return tuple(
        dict(row)
        for row in detail.get("rows") or []
        if isinstance(row, dict) and row.get("plain_text")
    )


def _usage_effect_value(
    book: dict[str, Any],
    usage: dict[str, Any],
    field: str,
) -> str:
    rows = _render_effect_rows(
        as_int(book.get("book_id")) or 0,
        as_int(book.get("star")) or 1,
        as_int(book.get("jie")) or 1,
        as_int(book.get("pin")) or 1,
    )
    effect_id = as_int(usage.get("effect_id"))
    exact = [
        row for row in rows
        if effect_id is not None and as_int(row.get("effect_id")) == effect_id
    ]
    if exact:
        return "\n".join(
            dict.fromkeys(str(row.get(field) or row.get("plain_text") or "") for row in exact)
        )

    role = str(usage.get("role") or "")
    preferred_sections = {
        "main": ("main_effect", "main_description", "skill_description"),
        "xian": ("main_effect", "side_effect", "main_description"),
        "side": ("side_effect",),
        "grid": ("main_effect", "main_description", "skill_description"),
    }.get(role, ())
    for section in preferred_sections:
        match = next((row for row in rows if row.get("section") == section), None)
        if match:
            return str(match.get(field) or match.get("plain_text") or "")
    return ""


def _project_usage(book: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    usage = dict(source)
    category = str(usage.get("category") or "")
    role = str(usage.get("role") or "")
    slot = as_int(usage.get("slot")) or 0
    grid = as_int(usage.get("grid"))
    equipped_name = str(usage.get("equipped_name") or "").strip()
    category_name = _CATEGORY_NAMES.get(category, category or "搭配")
    role_name = _ROLE_NAMES.get(role, role or "来源书")
    if role == "grid" and grid is not None:
        role_name = f"第 {grid} 格"
    usage.update({
        "category_name": category_name,
        "role_name": role_name,
        "location_name": equipped_name or f"{category_name}第 {slot} 栏",
        "effect_text": _usage_effect_value(book, usage, "plain_text"),
        "effect_rich_text": _usage_effect_value(book, usage, "rich_text"),
    })
    return usage


def _book_usages(book: dict[str, Any]) -> list[dict[str, Any]]:
    raw = [
        dict(usage)
        for usage in book.get("upgrade_usages") or []
        if isinstance(usage, dict)
    ]
    if not raw and isinstance(book.get("upgrade_first_usage"), dict):
        raw = [dict(book["upgrade_first_usage"])]
    merged: dict[tuple[str, int, str, int | None, str], dict[str, Any]] = {}
    for source in raw:
        usage = _project_usage(book, source)
        key = (
            str(usage.get("category") or ""),
            as_int(usage.get("slot")) or 0,
            str(usage.get("role") or ""),
            as_int(usage.get("grid")),
            str(usage.get("location_name") or ""),
        )
        existing = merged.get(key)
        if existing is None:
            usage["effect_ids"] = [
                effect_id
                for effect_id in [as_int(usage.get("effect_id"))]
                if effect_id is not None
            ]
            merged[key] = usage
            continue
        effect_id = as_int(usage.get("effect_id"))
        if effect_id is not None and effect_id not in existing["effect_ids"]:
            existing["effect_ids"].append(effect_id)
        texts = [
            text
            for text in (existing.get("effect_text"), usage.get("effect_text"))
            if text
        ]
        existing["effect_text"] = "\n".join(dict.fromkeys(texts))
        rich_texts = [
            text
            for text in (existing.get("effect_rich_text"), usage.get("effect_rich_text"))
            if text
        ]
        existing["effect_rich_text"] = "\n".join(dict.fromkeys(rich_texts))
    return list(merged.values())


def _exchange_channels(book: dict[str, Any]) -> list[dict[str, Any]]:
    support = _target_support_by_item(book)
    items = _item_index()
    channels: list[dict[str, Any]] = []
    for item_id, support_entry in support.items():
        item = items.get(item_id) or {}
        name = str(item.get("name_plain") or item.get("name") or "")
        source = ""
        if name == "悟境残页":
            source = "仙市·真悟阁"
        elif name.startswith("功法残篇"):
            source = "仙市·琅琊阁"
        elif support_entry.get("mode") == "合成" and "通玄" in name:
            source = "通玄合成"
        if not source:
            continue
        detail = {
            "仙市·真悟阁": f"使用{name}兑换本功法真悟手记",
            "仙市·琅琊阁": f"使用{name}兑换本功法重数",
            "通玄合成": f"合成本功法的{name}",
        }[source]
        channels.append({
            "kind": str(support_entry.get("kind") or ""),
            "source": source,
            "title": name,
            "detail": detail,
            "mode": str(support_entry.get("mode") or ""),
            "item_id": item_id,
        })
    return channels


def _xianyuan_channels(
    book: dict[str, Any],
    people: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    support = _target_support_by_item(book)
    channels: list[dict[str, Any]] = []
    for person in people:
        if not isinstance(person, dict):
            continue
        person_name = str(person.get("name") or person.get("npc_id") or "未知人物")
        for reward in person.get("rewards") or []:
            if not isinstance(reward, dict):
                continue
            item_id = as_int(reward.get("item_id")) or 0
            support_entry = support.get(item_id)
            if not support_entry:
                continue
            level = as_int(reward.get("level"))
            count = as_int(reward.get("count")) or 1
            reward_name = str(reward.get("name") or item_id)
            level_text = f"好感 {level} 级" if level is not None else "好感奖励"
            channels.append({
                "kind": str(support_entry.get("kind") or ""),
                "source": "仙缘送礼",
                "title": person_name,
                "detail": (
                    f"{level_text}：{reward_name} ×{count}"
                    f"（{support_entry.get('mode') or '直接'}）"
                ),
                "mode": str(support_entry.get("mode") or ""),
                "item_id": item_id,
                "npc_id": as_int(person.get("npc_id")),
                "level": level,
            })
    return channels


def build_gongfa_book_detail(
    session: Session,
    book: dict[str, Any],
) -> dict[str, Any]:
    xianyuan = load_inventory_hall_snapshot(session, "xianyuan_atlas") or {}
    people = [
        dict(person)
        for person in xianyuan.get("people") or []
        if isinstance(person, dict)
    ]
    channels = [*_exchange_channels(book), *_xianyuan_channels(book, people)]
    channels.sort(key=lambda row: (
        {"融合": 0, "悟境": 1, "通玄": 2}.get(str(row.get("kind") or ""), 9),
        str(row.get("source") or ""),
        str(row.get("title") or ""),
        as_int(row.get("level")) or 0,
    ))
    return {
        "book_id": as_int(book.get("book_id")) or 0,
        "usages": _book_usages(book),
        "acquisition_channels": channels,
        "xianyuan_snapshot_available": bool(xianyuan.get("runtime_complete")),
    }
