from __future__ import annotations

import time
import uuid
from typing import Any

from sqlmodel import Session

from backend.models import AppSetting

AI_CHAT_PROMPT_CARDS_SETTING_KEY_PREFIX = "ai_chat.prompt_cards.user"


def build_ai_chat_prompt_cards_setting_key(user_id: int) -> str:
    return f"{AI_CHAT_PROMPT_CARDS_SETTING_KEY_PREFIX}.{int(user_id)}"


def _normalize_prompt_card_id(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"prompt-{uuid.uuid4().hex[:12]}"


def _normalize_prompt_card_title(value: Any, fallback_index: int) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"提示词 {fallback_index}"


def _normalize_prompt_card_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    return ""


def _normalize_prompt_cards_payload(value: Any) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    raw_items = payload.get("items")
    raw_selected_id = payload.get("selected_id")

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(raw_item, dict):
            continue
        card_id = _normalize_prompt_card_id(raw_item.get("id"))
        if card_id in seen_ids:
            continue
        seen_ids.add(card_id)
        updated_at = raw_item.get("updated_at")
        items.append(
            {
                "id": card_id,
                "title": _normalize_prompt_card_title(raw_item.get("title"), len(items) + 1),
                "content": _normalize_prompt_card_content(raw_item.get("content")),
                "updated_at": float(updated_at) if isinstance(updated_at, (int, float)) else None,
            }
        )

    selected_id = raw_selected_id.strip() if isinstance(raw_selected_id, str) and raw_selected_id.strip() else None
    if selected_id and selected_id not in seen_ids:
        selected_id = None

    return {
        "selected_id": selected_id,
        "items": items,
    }


def list_user_ai_chat_prompt_cards(session: Session, user_id: int) -> dict[str, Any]:
    row = session.get(AppSetting, build_ai_chat_prompt_cards_setting_key(user_id))
    if row is None:
        return {
            "selected_id": None,
            "items": [],
        }
    return _normalize_prompt_cards_payload(row.value)


def save_user_ai_chat_prompt_cards(
    session: Session,
    user_id: int,
    *,
    selected_id: str | None,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = _normalize_prompt_cards_payload(
        {
            "selected_id": selected_id,
            "items": items,
        }
    )
    setting_key = build_ai_chat_prompt_cards_setting_key(user_id)
    row = session.get(AppSetting, setting_key)

    if not normalized["items"] and not normalized["selected_id"]:
        if row is not None:
            session.delete(row)
            session.commit()
        return normalized

    now = time.time()
    if row is None:
        row = AppSetting(key=setting_key)
    row.value = normalized
    row.updated_at = now
    session.add(row)
    session.commit()
    return normalized
