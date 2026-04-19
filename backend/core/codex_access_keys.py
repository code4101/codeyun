from __future__ import annotations

import secrets
import time
import uuid
from typing import Any

from sqlmodel import Session

from backend.core.ai_chat_user_config import AiChatUserConfigError, _decrypt_secret, _encrypt_secret, _mask_secret
from backend.models import AppSetting


CODEX_ACCESS_KEYS_SETTING_KEY = "system:ai_chat:codex_access_tokens"
CODEX_ACCESS_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
CODEX_ACCESS_KEY_LABEL_PREFIX = "访问 Token"


def _normalize_access_key_id(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if not normalized:
        raise AiChatUserConfigError("访问 Token 标识不能为空")
    return normalized


def _generate_access_key_label(items: dict[str, dict[str, Any]]) -> str:
    max_index = 0
    for item in items.values():
        label = str(item.get("label") or "").strip()
        if not label.startswith(f"{CODEX_ACCESS_KEY_LABEL_PREFIX} "):
            continue
        suffix = label[len(CODEX_ACCESS_KEY_LABEL_PREFIX) + 1:]
        if suffix.isdigit():
            max_index = max(max_index, int(suffix))
    return f"{CODEX_ACCESS_KEY_LABEL_PREFIX} {max_index + 1}"


def _generate_access_key_plaintext() -> str:
    chars = [secrets.choice(CODEX_ACCESS_KEY_ALPHABET) for _ in range(24)]
    groups = ["".join(chars[index:index + 4]) for index in range(0, len(chars), 4)]
    return f"ctk-{'-'.join(groups)}"


def _load_codex_access_key_map(session: Session) -> dict[str, dict[str, Any]]:
    row = session.get(AppSetting, CODEX_ACCESS_KEYS_SETTING_KEY)
    if not row or not isinstance(row.value, dict):
        return {}

    raw_items = row.value.get("keys")
    if not isinstance(raw_items, dict):
        return {}

    items: dict[str, dict[str, Any]] = {}
    for raw_key_id, raw_item in raw_items.items():
        try:
            key_id = _normalize_access_key_id(str(raw_key_id))
        except AiChatUserConfigError:
            continue
        if not isinstance(raw_item, dict):
            continue

        encrypted_value = raw_item.get("secret_encrypted")
        if not isinstance(encrypted_value, str) or not encrypted_value.strip():
            continue

        label = str(raw_item.get("label") or "").strip() or "未命名访问 Token"
        masked_value = str(raw_item.get("masked_value") or "").strip()
        if not masked_value:
            try:
                masked_value = _mask_secret(_decrypt_secret(encrypted_value))
            except AiChatUserConfigError:
                masked_value = "已损坏"

        items[key_id] = {
            "label": label,
            "secret_encrypted": encrypted_value.strip(),
            "masked_value": masked_value,
            "created_at": float(raw_item.get("created_at")) if isinstance(raw_item.get("created_at"), (int, float)) else None,
            "updated_at": float(raw_item.get("updated_at")) if isinstance(raw_item.get("updated_at"), (int, float)) else None,
            "created_by_user_id": int(raw_item.get("created_by_user_id")) if isinstance(raw_item.get("created_by_user_id"), int) else None,
        }
    return items


def _save_codex_access_key_map(session: Session, items: dict[str, dict[str, Any]]) -> None:
    row = session.get(AppSetting, CODEX_ACCESS_KEYS_SETTING_KEY)
    if not items:
        if row is not None:
            session.delete(row)
            session.commit()
        return

    payload = {"keys": items}
    now = time.time()
    if row is None:
        row = AppSetting(key=CODEX_ACCESS_KEYS_SETTING_KEY, value=payload, updated_at=now)
    else:
        row.value = payload
        row.updated_at = now
    session.add(row)
    session.commit()


def _serialize_access_key_summary(key_id: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": key_id,
        "label": str(item.get("label") or "").strip() or "未命名访问 Token",
        "masked_value": str(item.get("masked_value") or "").strip() or "已保存",
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "created_by_user_id": item.get("created_by_user_id"),
    }


def list_codex_access_keys(session: Session) -> list[dict[str, Any]]:
    items = _load_codex_access_key_map(session)
    return [
        _serialize_access_key_summary(key_id, item)
        for key_id, item in sorted(
            items.items(),
            key=lambda pair: (
                -(float(pair[1].get("updated_at")) if isinstance(pair[1].get("updated_at"), (int, float)) else 0.0),
                pair[0],
            ),
        )
    ]


def create_codex_access_key(
    session: Session,
    *,
    created_by_user_id: int | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    items = _load_codex_access_key_map(session)
    plaintext_value = _generate_access_key_plaintext()
    now = time.time()
    key_id = f"ctk-{uuid.uuid4().hex[:12]}"
    items[key_id] = {
        "label": (label or "").strip() or _generate_access_key_label(items),
        "secret_encrypted": _encrypt_secret(plaintext_value),
        "masked_value": _mask_secret(plaintext_value),
        "created_at": now,
        "updated_at": now,
        "created_by_user_id": created_by_user_id,
    }
    _save_codex_access_key_map(session, items)
    return {
        **_serialize_access_key_summary(key_id, items[key_id]),
        "plaintext_value": plaintext_value,
    }


def reveal_codex_access_key(session: Session, key_id: str | None) -> dict[str, Any]:
    normalized_key_id = _normalize_access_key_id(key_id)
    items = _load_codex_access_key_map(session)
    item = items.get(normalized_key_id)
    if not item:
        raise AiChatUserConfigError("指定的 Codex 访问 Token 不存在")
    return {
        **_serialize_access_key_summary(normalized_key_id, item),
        "plaintext_value": _decrypt_secret(str(item.get("secret_encrypted") or "")),
    }


def delete_codex_access_key(session: Session, key_id: str | None) -> None:
    normalized_key_id = _normalize_access_key_id(key_id)
    items = _load_codex_access_key_map(session)
    if normalized_key_id not in items:
        raise AiChatUserConfigError("指定的 Codex 访问 Token 不存在")
    items.pop(normalized_key_id, None)
    _save_codex_access_key_map(session, items)


def ensure_codex_access_key_allowed(session: Session, api_key: str | None) -> None:
    items = _load_codex_access_key_map(session)
    if not items:
        raise AiChatUserConfigError("当前还没有配置可分发的 Codex 访问 Token，请联系管理员先生成")

    normalized_api_key = (api_key or "").strip()
    if not normalized_api_key:
        raise AiChatUserConfigError("Codex 需要访问 Token，请填写管理员分发的 CodeYun 访问 Token")

    for item in items.values():
        encrypted_secret = str(item.get("secret_encrypted") or "").strip()
        if not encrypted_secret:
            continue
        try:
            decrypted_secret = _decrypt_secret(encrypted_secret)
        except AiChatUserConfigError:
            continue
        if secrets.compare_digest(decrypted_secret, normalized_api_key):
            return

    raise AiChatUserConfigError("Codex 访问 Token 无效，请检查后重试")
