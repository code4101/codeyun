from __future__ import annotations

"""Runtime-only normalization for Runtime mail snapshots."""

import json
from pathlib import Path
from typing import Any

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root


def _coerce_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if isinstance(value, float) and value.is_integer() else value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = float(text) if "." in text else int(text)
    except ValueError:
        return None
    return int(number) if isinstance(number, float) and number.is_integer() else number


def _list_items(value: Any) -> list[Any]:
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return list(value["items"])
    return list(value) if isinstance(value, list) else []


def _load_mail_item_name_index(
    *,
    export_root: str | Path | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    del data_dir
    try:
        runtime_index = load_fanxiu_item_runtime_index(
            export_root=export_root,
            rebuild_missing=False,
        )
    except Exception:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item_id, card in (runtime_index.get("cards_by_id") or {}).items():
        if not isinstance(card, dict):
            continue
        name = str(card.get("name") or "").strip()
        if not name:
            continue
        result[str(item_id)] = {
            "name": name,
            "quality": card.get("quality_name") or card.get("quality_tab") or "",
            "type": card.get("type_name") or "",
            "icon": card.get("icon") or "",
            "small_icon": card.get("small_icon") or "",
            "description": card.get("description") or card.get("effect_text") or "",
            "source": "item_catalog",
        }
    return result


def _normalize_mail_reward_item(
    reward: Any,
    export_root: str | Path | None = None,
    item_name_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(reward, dict):
        return None
    source = reward.get("_super") if isinstance(reward.get("_super"), dict) else {}
    source = {**source, **reward}
    item_id = source.get("code") or source.get("baseId") or source.get("itemId") or source.get("item") or source.get("id")
    amount = source.get("amount")
    if amount in (None, ""):
        amount = source.get("num") or source.get("count")
    index = item_name_index if item_name_index is not None else _load_mail_item_name_index(export_root=export_root)
    meta = index.get(str(item_id or ""), {})
    item_name = str(meta.get("name") or "").strip()
    number = _coerce_number(amount)
    result: dict[str, Any] = {
        "item_id": str(item_id or ""),
        "item_name": item_name,
        "amount": number,
        "text": f"{item_name or ('道具 #' + str(item_id or ''))} x{number}" if number is not None else item_name,
    }
    for source_key, target_key in (
        ("quality", "quality"),
        ("type", "item_type"),
        ("icon", "icon"),
        ("small_icon", "small_icon"),
        ("description", "description"),
        ("source", "name_source"),
    ):
        if meta.get(source_key):
            result[target_key] = meta[source_key]
    return result if result["item_id"] or result["item_name"] or number is not None else None


def _normalize_mail_rewards(
    mail_vo: dict[str, Any],
    export_root: str | Path | None = None,
    item_name_index: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    return [
        normalized
        for reward in _list_items(mail_vo.get("rewards"))
        if (normalized := _normalize_mail_reward_item(reward, export_root, item_name_index)) is not None
    ]


def _mail_rewards_summary(rewards: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for reward in rewards:
        item_id = str(reward.get("item_id") or "").strip()
        name = str(reward.get("item_name") or "").strip() or (f"未知道具 #{item_id}" if item_id else "")
        amount = reward.get("amount")
        text = str(reward.get("text") or "").strip()
        parts.append(f"{name} x{amount}" if name and amount is not None else text or name)
    return "，".join(part for part in parts if part)


def load_fanxiu_mail_envelope_titles(
    export_root: str | Path | None = None,
) -> dict[int, dict[str, Any]]:
    path = resolve_fanxiu_export_root(export_root) / "parsed_configs" / "Envelope" / "rows.json"
    if not path.is_file():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    result: dict[int, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            result[int(row.get("id", row.get("_row_key")))] = row
        except (TypeError, ValueError):
            continue
    return result


def _mail_title_from_vo(
    mail_vo: dict[str, Any],
    envelopes: dict[int, dict[str, Any]],
) -> str:
    title = str(mail_vo.get("title") or "").strip()
    if title:
        return title
    try:
        envelope = envelopes.get(int(mail_vo.get("type"))) or {}
    except (TypeError, ValueError):
        return ""
    return str(envelope.get("title_plain") or envelope.get("title") or "").strip()


__all__ = [
    "_load_mail_item_name_index",
    "_mail_rewards_summary",
    "_mail_title_from_vo",
    "_normalize_mail_reward_item",
    "_normalize_mail_rewards",
    "load_fanxiu_mail_envelope_titles",
]
