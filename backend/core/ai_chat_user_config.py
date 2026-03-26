from __future__ import annotations

import base64
import hashlib
import re
import time
import uuid
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlmodel import Session

from backend.core.ai_chat import AiProviderConfig
from backend.core.settings import get_settings
from backend.models import AppSetting


class AiChatUserConfigError(RuntimeError):
    """Raised when persisted AI chat user configuration cannot be used safely."""


def build_ai_chat_provider_config_key(user_id: int) -> str:
    return f"user:{user_id}:ai_chat_provider_configs"


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    secret_key = get_settings().secret_key.encode("utf-8")
    digest = hashlib.sha256(secret_key).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt_secret(value: str) -> str:
    payload = value.strip()
    if not payload:
        return ""
    return _get_fernet().encrypt(payload.encode("utf-8")).decode("utf-8")


def _decrypt_secret(value: str) -> str:
    payload = value.strip()
    if not payload:
        return ""
    try:
        return _get_fernet().decrypt(payload.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise AiChatUserConfigError("已保存的 API Key 无法解密，请重新保存") from exc


def _normalize_provider_id(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if not normalized:
        raise AiChatUserConfigError("AI 来源不能为空")
    return normalized


def _normalize_provider_key_id(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if not normalized:
        raise AiChatUserConfigError("API Key 标识不能为空")
    return normalized


def _normalize_model_list(values: Any, fallback_single: str | None = None) -> list[str]:
    raw_items: list[Any]
    if isinstance(values, list):
        raw_items = values
    elif isinstance(values, tuple):
        raw_items = list(values)
    else:
        raw_items = []
    if not raw_items and isinstance(fallback_single, str) and fallback_single.strip():
        raw_items = [fallback_single]

    normalized: list[str] = []
    seen = set()
    for item in raw_items:
        if not isinstance(item, str):
            continue
        model_name = item.strip()
        if not model_name or model_name in seen:
            continue
        seen.add(model_name)
        normalized.append(model_name)
    return normalized


def _mask_secret(value: str) -> str:
    payload = value.strip()
    if not payload:
        return ""
    if len(payload) <= 8:
        return f"{payload[:2]}***{payload[-2:]}" if len(payload) > 4 else "*" * len(payload)
    return f"{payload[:4]}...{payload[-4:]}"


def _pick_default_active_key_id(api_keys: dict[str, dict[str, Any]]) -> str:
    if not api_keys:
        return ""

    def sort_key(item: tuple[str, dict[str, Any]]) -> tuple[float, str]:
        key_id, payload = item
        updated_at = payload.get("updated_at")
        normalized_updated_at = float(updated_at) if isinstance(updated_at, (int, float)) else 0.0
        return (-normalized_updated_at, key_id)

    return sorted(api_keys.items(), key=sort_key)[0][0]


def _build_saved_key_summary(
    key_id: str,
    item: dict[str, Any],
    active_key_id: str,
) -> dict[str, Any]:
    return {
        "id": key_id,
        "label": item["label"],
        "masked_value": item["masked_value"],
        "is_active": key_id == active_key_id,
        "updated_at": item["updated_at"],
    }


def _build_provider_config_summary(
    provider_id: str,
    item: dict[str, Any] | None,
) -> dict[str, Any]:
    if not item:
        return {
            "provider": provider_id,
            "base_url": "",
            "preferred_model": "",
            "preferred_models": [],
            "has_api_key": False,
            "active_key_id": None,
            "key_count": 0,
            "keys": [],
            "updated_at": None,
        }

    active_key_id = item["active_key_id"] if item["active_key_id"] in item["api_keys"] else ""
    sorted_keys = sorted(
        item["api_keys"].items(),
        key=lambda pair: (
            0 if pair[0] == active_key_id else 1,
            -(float(pair[1]["updated_at"]) if isinstance(pair[1]["updated_at"], (int, float)) else 0.0),
            pair[0],
        ),
    )
    key_items = [
        _build_saved_key_summary(key_id, key_item, active_key_id)
        for key_id, key_item in sorted_keys
    ]
    return {
        "provider": provider_id,
        "base_url": item["base_url"],
        "preferred_model": item["preferred_models"][0] if item["preferred_models"] else "",
        "preferred_models": list(item["preferred_models"]),
        "has_api_key": bool(key_items),
        "active_key_id": active_key_id or None,
        "key_count": len(key_items),
        "keys": key_items,
        "updated_at": item["updated_at"],
    }


def _default_provider_item() -> dict[str, Any]:
    return {
        "base_url": "",
        "preferred_models": [],
        "api_keys": {},
        "active_key_id": "",
        "updated_at": None,
    }


def _build_legacy_saved_key(encrypted_api_key: str, updated_at: float | None) -> dict[str, dict[str, Any]]:
    masked_value = "已保存"
    try:
        masked_value = _mask_secret(_decrypt_secret(encrypted_api_key))
    except AiChatUserConfigError:
        masked_value = "已损坏"

    return {
        "key-legacy": {
            "label": "Key 1",
            "api_key_encrypted": encrypted_api_key,
            "masked_value": masked_value,
            "updated_at": updated_at,
        }
    }


def _load_saved_api_keys(raw_item: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str]:
    raw_api_keys = raw_item.get("api_keys")
    api_keys: dict[str, dict[str, Any]] = {}

    if isinstance(raw_api_keys, dict):
        for raw_key_id, raw_key_item in raw_api_keys.items():
            try:
                key_id = _normalize_provider_key_id(str(raw_key_id))
            except AiChatUserConfigError:
                continue
            if not isinstance(raw_key_item, dict):
                continue

            encrypted_api_key = raw_key_item.get("api_key_encrypted")
            if not isinstance(encrypted_api_key, str) or not encrypted_api_key.strip():
                continue

            label = raw_key_item.get("label")
            masked_value = raw_key_item.get("masked_value")
            updated_at = raw_key_item.get("updated_at")
            if not isinstance(masked_value, str) or not masked_value.strip():
                try:
                    masked_value = _mask_secret(_decrypt_secret(encrypted_api_key))
                except AiChatUserConfigError:
                    masked_value = "已损坏"

            api_keys[key_id] = {
                "label": label.strip() if isinstance(label, str) and label.strip() else "未命名 Key",
                "api_key_encrypted": encrypted_api_key.strip(),
                "masked_value": masked_value.strip(),
                "updated_at": float(updated_at) if isinstance(updated_at, (int, float)) else None,
            }

    if not api_keys:
        legacy_encrypted_api_key = raw_item.get("api_key_encrypted")
        updated_at = raw_item.get("updated_at")
        if isinstance(legacy_encrypted_api_key, str) and legacy_encrypted_api_key.strip():
            api_keys = _build_legacy_saved_key(
                legacy_encrypted_api_key.strip(),
                float(updated_at) if isinstance(updated_at, (int, float)) else None,
            )

    active_key_id = raw_item.get("active_key_id")
    normalized_active_key_id = (
        _normalize_provider_key_id(active_key_id)
        if isinstance(active_key_id, str) and active_key_id.strip()
        else ""
    )
    if normalized_active_key_id not in api_keys:
        normalized_active_key_id = _pick_default_active_key_id(api_keys)

    return api_keys, normalized_active_key_id


def _load_provider_map(session: Session, user_id: int) -> dict[str, dict[str, Any]]:
    row = session.get(AppSetting, build_ai_chat_provider_config_key(user_id))
    if not row or not isinstance(row.value, dict):
        return {}

    raw_providers = row.value.get("providers")
    if not isinstance(raw_providers, dict):
        return {}

    providers: dict[str, dict[str, Any]] = {}
    for provider_id, raw_item in raw_providers.items():
        try:
            normalized_id = _normalize_provider_id(provider_id)
        except AiChatUserConfigError:
            continue
        if not isinstance(raw_item, dict):
            continue

        base_url = raw_item.get("base_url")
        preferred_model = raw_item.get("preferred_model")
        preferred_models = _normalize_model_list(raw_item.get("preferred_models"), preferred_model if isinstance(preferred_model, str) else None)
        updated_at = raw_item.get("updated_at")
        api_keys, active_key_id = _load_saved_api_keys(raw_item)

        providers[normalized_id] = {
            "base_url": base_url.strip() if isinstance(base_url, str) else "",
            "preferred_models": preferred_models,
            "api_keys": api_keys,
            "active_key_id": active_key_id,
            "updated_at": float(updated_at) if isinstance(updated_at, (int, float)) else None,
        }
    return providers


def _load_custom_provider_map(session: Session, user_id: int) -> dict[str, dict[str, Any]]:
    row = session.get(AppSetting, build_ai_chat_provider_config_key(user_id))
    if not row or not isinstance(row.value, dict):
        return {}

    raw_providers = row.value.get("custom_providers")
    if not isinstance(raw_providers, dict):
        return {}

    providers: dict[str, dict[str, Any]] = {}
    for provider_id, raw_item in raw_providers.items():
        try:
            normalized_id = _normalize_provider_id(provider_id)
        except AiChatUserConfigError:
            continue
        if not isinstance(raw_item, dict):
            continue

        label = raw_item.get("label")
        base_url = raw_item.get("base_url")
        default_model = raw_item.get("default_model")
        models = raw_item.get("models")
        updated_at = raw_item.get("updated_at")
        if not isinstance(label, str) or not label.strip():
            continue

        normalized_models = tuple(
            item.strip()
            for item in (models or [])
            if isinstance(item, str) and item.strip()
        )
        providers[normalized_id] = {
            "id": normalized_id,
            "label": label.strip(),
            "kind": "openai_compatible",
            "base_url": base_url.strip() if isinstance(base_url, str) else "",
            "default_model": default_model.strip() if isinstance(default_model, str) else "",
            "models": normalized_models,
            "supports_stream": True,
            "supports_vision": False,
            "requires_api_key": True,
            "updated_at": float(updated_at) if isinstance(updated_at, (int, float)) else None,
        }
    return providers


def _save_provider_map(session: Session, user_id: int, providers: dict[str, dict[str, Any]]) -> None:
    setting_key = build_ai_chat_provider_config_key(user_id)
    row = session.get(AppSetting, setting_key)
    custom_providers = _load_custom_provider_map(session, user_id)

    if not providers and not custom_providers:
        if row:
            session.delete(row)
            session.commit()
        return

    payload = {
        "providers": providers,
        "custom_providers": custom_providers,
    }
    now = time.time()
    if row is None:
        row = AppSetting(key=setting_key, value=payload, updated_at=now)
    else:
        row.value = payload
        row.updated_at = now

    session.add(row)
    session.commit()


def _save_all_maps(
    session: Session,
    user_id: int,
    providers: dict[str, dict[str, Any]],
    custom_providers: dict[str, dict[str, Any]],
) -> None:
    setting_key = build_ai_chat_provider_config_key(user_id)
    row = session.get(AppSetting, setting_key)

    if not providers and not custom_providers:
        if row:
            session.delete(row)
            session.commit()
        return

    payload = {
        "providers": providers,
        "custom_providers": custom_providers,
    }
    now = time.time()
    if row is None:
        row = AppSetting(key=setting_key, value=payload, updated_at=now)
    else:
        row.value = payload
        row.updated_at = now

    session.add(row)
    session.commit()


def _generate_next_api_key_label(api_keys: dict[str, dict[str, Any]]) -> str:
    max_index = 0
    for item in api_keys.values():
        label = item.get("label")
        if not isinstance(label, str):
            continue
        match = re.fullmatch(r"Key\s+(\d+)", label.strip(), flags=re.IGNORECASE)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return f"Key {max_index + 1}"


def list_user_ai_chat_provider_configs(session: Session, user_id: int) -> list[dict[str, Any]]:
    providers = _load_provider_map(session, user_id)
    return [
        _build_provider_config_summary(provider_id, providers[provider_id])
        for provider_id in sorted(providers)
    ]


def list_user_ai_chat_custom_providers(session: Session, user_id: int) -> list[dict[str, Any]]:
    providers = _load_custom_provider_map(session, user_id)
    items: list[dict[str, Any]] = []
    for provider_id in sorted(providers):
        item = providers[provider_id]
        items.append(
            {
                "id": item["id"],
                "label": item["label"],
                "kind": item["kind"],
                "configured": False,
                "base_url": item["base_url"],
                "default_model": item["default_model"],
                "models": list(item["models"]),
                "supports_stream": item["supports_stream"],
                "supports_vision": item["supports_vision"],
                "requires_api_key": item["requires_api_key"],
                "is_custom": True,
                "updated_at": item["updated_at"],
            }
        )
    return items


def list_user_ai_chat_custom_provider_configs(session: Session, user_id: int) -> tuple[AiProviderConfig, ...]:
    providers = _load_custom_provider_map(session, user_id)
    return tuple(
        AiProviderConfig(
            id=item["id"],
            label=item["label"],
            kind=item["kind"],
            base_url=item["base_url"],
            default_model=item["default_model"],
            timeout_seconds=120.0,
            api_key="",
            supports_stream=item["supports_stream"],
            supports_vision=item["supports_vision"],
            requires_api_key=item["requires_api_key"],
            configured=False,
            models=item["models"],
            is_custom=True,
        )
        for item in providers.values()
    )


def get_user_ai_chat_provider_runtime_config(
    session: Session,
    user_id: int,
    provider_id: str | None,
) -> dict[str, Any]:
    normalized_id = _normalize_provider_id(provider_id)
    providers = _load_provider_map(session, user_id)
    item = providers.get(normalized_id)
    if not item:
        return {
            "provider": normalized_id,
            "base_url": "",
            "preferred_model": "",
            "preferred_models": [],
            "api_key": "",
            "has_api_key": False,
            "active_key_id": None,
            "updated_at": None,
        }

    active_key_id = item["active_key_id"] if item["active_key_id"] in item["api_keys"] else _pick_default_active_key_id(item["api_keys"])
    active_key = item["api_keys"].get(active_key_id) if active_key_id else None
    encrypted_api_key = active_key["api_key_encrypted"] if active_key else ""
    return {
        "provider": normalized_id,
        "base_url": item["base_url"],
        "preferred_model": item["preferred_models"][0] if item["preferred_models"] else "",
        "preferred_models": list(item["preferred_models"]),
        "api_key": _decrypt_secret(encrypted_api_key) if encrypted_api_key else "",
        "has_api_key": bool(item["api_keys"]),
        "active_key_id": active_key_id or None,
        "updated_at": item["updated_at"],
    }


def save_user_ai_chat_provider_config(
    session: Session,
    user_id: int,
    provider_id: str | None,
    *,
    base_url: str | None = None,
    preferred_model: str | None = None,
    preferred_models: list[str] | tuple[str, ...] | None = None,
    api_key: str | None = None,
    api_key_label: str | None = None,
    clear_api_key: bool = False,
) -> dict[str, Any]:
    normalized_id = _normalize_provider_id(provider_id)
    providers = _load_provider_map(session, user_id)
    current = providers.get(normalized_id, _default_provider_item())

    next_base_url = current["base_url"] if base_url is None else base_url.strip()
    if preferred_models is None:
        next_preferred_models = list(current["preferred_models"]) if preferred_model is None else _normalize_model_list([], preferred_model)
    else:
        next_preferred_models = _normalize_model_list(preferred_models, preferred_model)
    next_api_keys = dict(current["api_keys"])
    next_active_key_id = current["active_key_id"]

    if clear_api_key:
        next_api_keys = {}
        next_active_key_id = ""

    normalized_api_key = (api_key or "").strip()
    if normalized_api_key:
        new_key_id = f"key-{uuid.uuid4().hex[:12]}"
        next_api_keys[new_key_id] = {
            "label": (api_key_label or "").strip() or _generate_next_api_key_label(next_api_keys),
            "api_key_encrypted": _encrypt_secret(normalized_api_key),
            "masked_value": _mask_secret(normalized_api_key),
            "updated_at": time.time(),
        }
        next_active_key_id = new_key_id

    if next_active_key_id not in next_api_keys:
        next_active_key_id = _pick_default_active_key_id(next_api_keys)

    updated_at = time.time()
    if not next_base_url and not next_preferred_models and not next_api_keys:
        providers.pop(normalized_id, None)
        _save_provider_map(session, user_id, providers)
        return _build_provider_config_summary(normalized_id, None)

    providers[normalized_id] = {
        "base_url": next_base_url,
        "preferred_models": next_preferred_models,
        "api_keys": next_api_keys,
        "active_key_id": next_active_key_id,
        "updated_at": updated_at,
    }
    _save_provider_map(session, user_id, providers)

    return _build_provider_config_summary(normalized_id, providers[normalized_id])


def activate_user_ai_chat_provider_api_key(
    session: Session,
    user_id: int,
    provider_id: str | None,
    key_id: str | None,
) -> dict[str, Any]:
    normalized_id = _normalize_provider_id(provider_id)
    normalized_key_id = _normalize_provider_key_id(key_id)
    providers = _load_provider_map(session, user_id)
    current = providers.get(normalized_id)
    if not current:
        raise AiChatUserConfigError("当前来源还没有账号保存的连接配置")
    if normalized_key_id not in current["api_keys"]:
        raise AiChatUserConfigError("指定的 API Key 不存在")

    current["active_key_id"] = normalized_key_id
    current["updated_at"] = time.time()
    providers[normalized_id] = current
    _save_provider_map(session, user_id, providers)
    return _build_provider_config_summary(normalized_id, current)


def delete_user_ai_chat_provider_api_key(
    session: Session,
    user_id: int,
    provider_id: str | None,
    key_id: str | None,
) -> dict[str, Any]:
    normalized_id = _normalize_provider_id(provider_id)
    normalized_key_id = _normalize_provider_key_id(key_id)
    providers = _load_provider_map(session, user_id)
    current = providers.get(normalized_id)
    if not current:
        raise AiChatUserConfigError("当前来源还没有账号保存的连接配置")
    if normalized_key_id not in current["api_keys"]:
        raise AiChatUserConfigError("指定的 API Key 不存在")

    next_api_keys = dict(current["api_keys"])
    next_api_keys.pop(normalized_key_id, None)
    next_active_key_id = current["active_key_id"]
    if next_active_key_id == normalized_key_id:
        next_active_key_id = _pick_default_active_key_id(next_api_keys)

    if not current["base_url"] and not current["preferred_models"] and not next_api_keys:
        providers.pop(normalized_id, None)
        _save_provider_map(session, user_id, providers)
        return _build_provider_config_summary(normalized_id, None)

    current["api_keys"] = next_api_keys
    current["active_key_id"] = next_active_key_id
    current["updated_at"] = time.time()
    providers[normalized_id] = current
    _save_provider_map(session, user_id, providers)
    return _build_provider_config_summary(normalized_id, current)


def delete_user_ai_chat_provider_config(session: Session, user_id: int, provider_id: str | None) -> None:
    normalized_id = _normalize_provider_id(provider_id)
    providers = _load_provider_map(session, user_id)
    if normalized_id not in providers:
        return
    providers.pop(normalized_id, None)
    _save_provider_map(session, user_id, providers)


def _slugify_custom_provider_label(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.strip().lower())
    slug = slug.strip("-")
    if not slug:
        slug = uuid.uuid4().hex[:8]
    return slug[:40]


def save_user_ai_chat_custom_provider(
    session: Session,
    user_id: int,
    *,
    label: str,
    base_url: str,
    default_model: str | None = None,
    models: list[str] | None = None,
) -> dict[str, Any]:
    normalized_label = label.strip()
    normalized_base_url = base_url.strip().rstrip("/")
    normalized_default_model = (default_model or "").strip()
    normalized_models = [
        item.strip()
        for item in (models or [])
        if isinstance(item, str) and item.strip()
    ]
    if not normalized_label:
        raise AiChatUserConfigError("自定义来源名称不能为空")
    if not normalized_base_url:
        raise AiChatUserConfigError("自定义来源地址不能为空")

    providers = _load_provider_map(session, user_id)
    custom_providers = _load_custom_provider_map(session, user_id)
    provider_id = f"custom-{_slugify_custom_provider_label(normalized_label)}"
    while provider_id in custom_providers:
        provider_id = f"custom-{_slugify_custom_provider_label(normalized_label)}-{uuid.uuid4().hex[:4]}"

    updated_at = time.time()
    custom_providers[provider_id] = {
        "id": provider_id,
        "label": normalized_label,
        "kind": "openai_compatible",
        "base_url": normalized_base_url,
        "default_model": normalized_default_model,
        "models": tuple(normalized_models),
        "supports_stream": True,
        "supports_vision": False,
        "requires_api_key": True,
        "updated_at": updated_at,
    }
    _save_all_maps(session, user_id, providers, custom_providers)
    return {
        "id": provider_id,
        "label": normalized_label,
        "kind": "openai_compatible",
        "configured": False,
        "base_url": normalized_base_url,
        "default_model": normalized_default_model,
        "models": normalized_models,
        "supports_stream": True,
        "supports_vision": False,
        "requires_api_key": True,
        "is_custom": True,
        "updated_at": updated_at,
    }


def delete_user_ai_chat_custom_provider(session: Session, user_id: int, provider_id: str | None) -> None:
    normalized_id = _normalize_provider_id(provider_id)
    providers = _load_provider_map(session, user_id)
    custom_providers = _load_custom_provider_map(session, user_id)
    changed = False
    if normalized_id in custom_providers:
        custom_providers.pop(normalized_id, None)
        changed = True
    if normalized_id in providers:
        providers.pop(normalized_id, None)
        changed = True
    if changed:
        _save_all_maps(session, user_id, providers, custom_providers)
