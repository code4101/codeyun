from __future__ import annotations

import time
import uuid
from typing import Any

from sqlmodel import Session

from backend.models import AppSetting

AI_CHAT_SESSIONS_SETTING_KEY_PREFIX = "ai_chat.sessions.user"
AI_CHAT_LEGACY_SESSION_SETTING_KEY_PREFIX = "ai_chat.session.user"


def build_ai_chat_sessions_setting_key(user_id: int) -> str:
    return f"{AI_CHAT_SESSIONS_SETTING_KEY_PREFIX}.{int(user_id)}"


def build_ai_chat_legacy_session_setting_key(user_id: int) -> str:
    return f"{AI_CHAT_LEGACY_SESSION_SETTING_KEY_PREFIX}.{int(user_id)}"


def _normalize_non_empty_str(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _normalize_optional_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return ""


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized_item = _normalize_non_empty_str(item)
        if not normalized_item or normalized_item in seen:
            continue
        seen.add(normalized_item)
        normalized.append(normalized_item)
    return normalized


def _normalize_image_item(value: Any, fallback_index: int) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    data_base64 = _normalize_non_empty_str(value.get("data_base64"))
    if not data_base64:
        return None

    image_id = _normalize_non_empty_str(value.get("id")) or f"image-{fallback_index}"
    return {
        "id": image_id,
        "name": _normalize_optional_text(value.get("name")),
        "mime_type": _normalize_optional_text(value.get("mime_type")),
        "data_base64": data_base64,
    }


def _normalize_message_item(value: Any, fallback_index: int) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    role = _normalize_non_empty_str(value.get("role"))
    if role not in {"user", "assistant"}:
        return None

    images: list[dict[str, Any]] = []
    for image_index, image_item in enumerate(value.get("images") or [], start=1):
        normalized_image = _normalize_image_item(image_item, image_index)
        if normalized_image is not None:
            images.append(normalized_image)

    total_duration = value.get("total_duration")
    normalized_total_duration = float(total_duration) if isinstance(total_duration, (int, float)) else None

    return {
        "id": _normalize_non_empty_str(value.get("id")) or f"{role}-{fallback_index}",
        "role": role,
        "content": _normalize_optional_text(value.get("content")),
        "images": images,
        "target_model_option_ids": _normalize_string_list(value.get("target_model_option_ids")),
        "provider_id": _normalize_optional_text(value.get("provider_id")),
        "model_option_id": _normalize_optional_text(value.get("model_option_id")),
        "model": _normalize_optional_text(value.get("model")),
        "display_model": _normalize_optional_text(value.get("display_model")),
        "created_at": _normalize_optional_text(value.get("created_at")) or None,
        "total_duration": normalized_total_duration,
        "error": bool(value.get("error")) if role == "assistant" else False,
    }


def _derive_session_title(messages: list[dict[str, Any]], fallback: str = "新会话") -> str:
    for message in messages:
        if message.get("role") != "user":
            continue
        content = _normalize_optional_text(message.get("content")).strip()
        if content:
            single_line = " ".join(content.split())
            return single_line[:40]
        if message.get("images"):
            return "图片对话"
    return fallback


def _derive_session_preview(messages: list[dict[str, Any]], draft: str) -> str:
    for message in reversed(messages):
        content = _normalize_optional_text(message.get("content")).strip()
        if content:
            return " ".join(content.split())[:80]
        if message.get("images"):
            return "包含图片"
    draft_text = _normalize_optional_text(draft).strip()
    if draft_text:
        return f"草稿：{' '.join(draft_text.split())[:76]}"
    return ""


def _empty_sessions_payload() -> dict[str, Any]:
    return {
        "active_session_id": None,
        "items": [],
    }


def _normalize_session_item(value: Any, fallback_index: int, fallback_updated_at: float | None = None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    messages: list[dict[str, Any]] = []
    assistant_message_ids: set[str] = set()
    for message_index, message_item in enumerate(value.get("messages") or [], start=1):
        normalized_message = _normalize_message_item(message_item, message_index)
        if normalized_message is None:
            continue
        messages.append(normalized_message)
        if normalized_message["role"] == "assistant":
            assistant_message_ids.add(normalized_message["id"])

    draft = _normalize_optional_text(value.get("draft"))
    if not messages and not draft.strip():
        return None

    selected_assistant_message_id = _normalize_non_empty_str(value.get("selected_assistant_message_id")) or None
    if selected_assistant_message_id and selected_assistant_message_id not in assistant_message_ids:
        selected_assistant_message_id = None

    updated_at = value.get("updated_at")
    normalized_updated_at = (
        float(updated_at)
        if isinstance(updated_at, (int, float))
        else (fallback_updated_at if isinstance(fallback_updated_at, (int, float)) else time.time())
    )

    title = _normalize_non_empty_str(value.get("title")) or _derive_session_title(messages)
    preview = _normalize_optional_text(value.get("preview")) or _derive_session_preview(messages, draft)

    return {
        "id": _normalize_non_empty_str(value.get("id")) or f"session-{uuid.uuid4().hex[:12]}",
        "title": title,
        "preview": preview,
        "provider_id": _normalize_optional_text(value.get("provider_id")),
        "model": _normalize_optional_text(value.get("model")),
        "selected_model_option_ids": _normalize_string_list(value.get("selected_model_option_ids")),
        "selected_assistant_message_id": selected_assistant_message_id,
        "draft": draft,
        "messages": messages,
        "updated_at": normalized_updated_at,
    }


def _normalize_legacy_session_payload(value: Any, fallback_updated_at: float | None = None) -> dict[str, Any]:
    item = _normalize_session_item(value, 1, fallback_updated_at)
    if item is None:
        return _empty_sessions_payload()
    return {
        "active_session_id": item["id"],
        "items": [item],
    }


def _normalize_sessions_payload(value: Any, fallback_updated_at: float | None = None) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    if "items" not in payload:
        return _normalize_legacy_session_payload(payload, fallback_updated_at)

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item_index, raw_item in enumerate(payload.get("items") or [], start=1):
        normalized_item = _normalize_session_item(raw_item, item_index, fallback_updated_at)
        if normalized_item is None:
            continue
        if normalized_item["id"] in seen_ids:
            continue
        seen_ids.add(normalized_item["id"])
        items.append(normalized_item)

    items.sort(key=lambda item: (-float(item["updated_at"]), item["id"]))

    active_session_id = _normalize_non_empty_str(payload.get("active_session_id")) or None
    if active_session_id and active_session_id not in seen_ids:
        active_session_id = None
    if active_session_id is None and items:
        active_session_id = items[0]["id"]

    return {
        "active_session_id": active_session_id,
        "items": items,
    }


def get_user_ai_chat_sessions(session: Session, user_id: int) -> dict[str, Any]:
    row = session.get(AppSetting, build_ai_chat_sessions_setting_key(user_id))
    if row is not None:
        return _normalize_sessions_payload(row.value, row.updated_at)

    legacy_row = session.get(AppSetting, build_ai_chat_legacy_session_setting_key(user_id))
    if legacy_row is not None:
        return _normalize_sessions_payload(legacy_row.value, legacy_row.updated_at)

    return _empty_sessions_payload()


def save_user_ai_chat_sessions(
    session: Session,
    user_id: int,
    *,
    active_session_id: str | None,
    items: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    normalized = _normalize_sessions_payload(
        {
            "active_session_id": active_session_id,
            "items": list(items or []),
        }
    )
    setting_key = build_ai_chat_sessions_setting_key(user_id)
    row = session.get(AppSetting, setting_key)
    legacy_row = session.get(AppSetting, build_ai_chat_legacy_session_setting_key(user_id))

    if not normalized["items"]:
        if row is not None:
            session.delete(row)
        if legacy_row is not None:
            session.delete(legacy_row)
        session.commit()
        return _empty_sessions_payload()

    now = time.time()
    active_id = normalized["active_session_id"]

    persisted_items: list[dict[str, Any]] = []
    for item in normalized["items"]:
        persisted_item = dict(item)
        if item["id"] == active_id:
            persisted_item["updated_at"] = now
        else:
            persisted_item["updated_at"] = float(item["updated_at"]) if isinstance(item.get("updated_at"), (int, float)) else now
        persisted_items.append(persisted_item)
    persisted_items.sort(key=lambda item: (-float(item["updated_at"]), item["id"]))

    if active_id not in {item["id"] for item in persisted_items}:
        active_id = persisted_items[0]["id"] if persisted_items else None

    if row is None:
        row = AppSetting(key=setting_key)
    row.value = {
        "active_session_id": active_id,
        "items": persisted_items,
    }
    row.updated_at = now
    session.add(row)
    if legacy_row is not None:
        session.delete(legacy_row)
    session.commit()

    return {
        "active_session_id": active_id,
        "items": persisted_items,
    }
