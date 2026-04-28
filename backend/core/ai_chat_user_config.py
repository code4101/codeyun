from __future__ import annotations

import base64
import hashlib
import re
import time
import uuid
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlmodel import Session, select

from backend.core.ai_chat import AiProviderConfig, CODEX_CLI_DEFAULT_COMMAND, CODEX_CLI_DEFAULT_MODEL
from backend.core.settings import get_settings
from backend.models import AppSetting, User


class AiChatUserConfigError(RuntimeError):
    """Raised when persisted AI chat user configuration cannot be used safely."""


def build_ai_chat_provider_config_key(user_id: int) -> str:
    return f"user:{user_id}:ai_chat_provider_configs"


CUSTOM_PROVIDER_KIND_OPENAI = "openai_compatible"
CUSTOM_PROVIDER_KIND_CODEX = "codex_cli"
CUSTOM_PROVIDER_VISIBILITY_PRIVATE = "private"
CUSTOM_PROVIDER_VISIBILITY_PUBLIC = "public"


def _normalize_custom_provider_kind(value: str | None) -> str:
    normalized = (value or CUSTOM_PROVIDER_KIND_OPENAI).strip().lower()
    if normalized not in {CUSTOM_PROVIDER_KIND_OPENAI, CUSTOM_PROVIDER_KIND_CODEX}:
        raise AiChatUserConfigError("暂不支持的自定义来源类型")
    return normalized


def _normalize_custom_provider_visibility(value: str | None) -> str:
    normalized = (value or CUSTOM_PROVIDER_VISIBILITY_PRIVATE).strip().lower()
    if normalized not in {CUSTOM_PROVIDER_VISIBILITY_PRIVATE, CUSTOM_PROVIDER_VISIBILITY_PUBLIC}:
        raise AiChatUserConfigError("暂不支持的来源权限模式")
    return normalized


def _build_public_custom_provider_id(owner_user_id: int, local_provider_id: str) -> str:
    return f"shared-u{owner_user_id}-{local_provider_id}"


def _parse_public_custom_provider_id(provider_id: str | None) -> tuple[int, str] | None:
    normalized = (provider_id or "").strip().lower()
    match = re.fullmatch(r"shared-u(\d+)-(.+)", normalized)
    if not match:
        return None
    return int(match.group(1)), match.group(2)


def _extract_user_id_from_setting_key(setting_key: str | None) -> int | None:
    match = re.fullmatch(r"user:(\d+):ai_chat_provider_configs", (setting_key or "").strip())
    if not match:
        return None
    return int(match.group(1))


def _get_custom_provider_capabilities(kind: str) -> dict[str, Any]:
    normalized_kind = _normalize_custom_provider_kind(kind)
    if normalized_kind == CUSTOM_PROVIDER_KIND_CODEX:
        return {
            "supports_stream": False,
            "supports_vision": False,
            "requires_api_key": False,
            "default_base_url": CODEX_CLI_DEFAULT_COMMAND,
            "default_model": CODEX_CLI_DEFAULT_MODEL,
            "timeout_seconds": 600.0,
        }
    return {
        "supports_stream": True,
        "supports_vision": False,
        "requires_api_key": True,
        "default_base_url": "",
        "default_model": "",
        "timeout_seconds": 120.0,
    }


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


def _normalize_provider_base_url_id(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if not normalized:
        raise AiChatUserConfigError("地址标识不能为空")
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


def _pick_default_active_base_url_id(base_urls: dict[str, dict[str, Any]]) -> str:
    if not base_urls:
        return ""

    def sort_key(item: tuple[str, dict[str, Any]]) -> tuple[float, str]:
        base_url_id, payload = item
        updated_at = payload.get("updated_at")
        normalized_updated_at = float(updated_at) if isinstance(updated_at, (int, float)) else 0.0
        return (-normalized_updated_at, base_url_id)

    return sorted(base_urls.items(), key=sort_key)[0][0]


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


def _build_saved_base_url_summary(
    base_url_id: str,
    item: dict[str, Any],
    active_base_url_id: str,
) -> dict[str, Any]:
    return {
        "id": base_url_id,
        "label": item["label"],
        "value": item["value"],
        "is_active": base_url_id == active_base_url_id,
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
            "active_base_url_id": None,
            "base_url_count": 0,
            "base_urls": [],
            "preferred_model": "",
            "preferred_models": [],
            "has_api_key": False,
            "active_key_id": None,
            "key_count": 0,
            "keys": [],
            "updated_at": None,
        }

    active_base_url_id = (
        item["active_base_url_id"]
        if item["active_base_url_id"] in item["base_urls"]
        else _pick_default_active_base_url_id(item["base_urls"])
    )
    active_base_url_item = item["base_urls"].get(active_base_url_id) if active_base_url_id else None
    sorted_base_urls = sorted(
        item["base_urls"].items(),
        key=lambda pair: (
            float(pair[1]["updated_at"]) if isinstance(pair[1]["updated_at"], (int, float)) else 0.0,
            pair[0],
        ),
    )
    base_url_items = [
        _build_saved_base_url_summary(base_url_id, base_url_item, active_base_url_id)
        for base_url_id, base_url_item in sorted_base_urls
    ]
    active_key_id = item["active_key_id"] if item["active_key_id"] in item["api_keys"] else _pick_default_active_key_id(item["api_keys"])
    sorted_keys = sorted(
        item["api_keys"].items(),
        key=lambda pair: (
            float(pair[1]["updated_at"]) if isinstance(pair[1]["updated_at"], (int, float)) else 0.0,
            pair[0],
        ),
    )
    key_items = [
        _build_saved_key_summary(key_id, key_item, active_key_id)
        for key_id, key_item in sorted_keys
    ]
    return {
        "provider": provider_id,
        "base_url": active_base_url_item["value"] if active_base_url_item else "",
        "active_base_url_id": active_base_url_id or None,
        "base_url_count": len(base_url_items),
        "base_urls": base_url_items,
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
        "base_urls": {},
        "active_base_url_id": "",
        "preferred_models": [],
        "api_keys": {},
        "active_key_id": "",
        "updated_at": None,
    }


def _build_legacy_saved_base_url(base_url: str, updated_at: float | None) -> dict[str, dict[str, Any]]:
    return {
        "base-url-legacy": {
            "label": "地址 1",
            "value": base_url.strip(),
            "updated_at": updated_at,
        }
    }


def _build_legacy_saved_key(encrypted_api_key: str, updated_at: float | None) -> dict[str, dict[str, Any]]:
    masked_value = "已保存"
    try:
        masked_value = _mask_secret(_decrypt_secret(encrypted_api_key))
    except AiChatUserConfigError:
        masked_value = "已损坏"

    return {
        "key-legacy": {
            "label": "",
            "api_key_encrypted": encrypted_api_key,
            "masked_value": masked_value,
            "updated_at": updated_at,
        }
    }


def _load_saved_base_urls(raw_item: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str]:
    raw_base_urls = raw_item.get("base_urls")
    base_urls: dict[str, dict[str, Any]] = {}

    if isinstance(raw_base_urls, dict):
        for raw_base_url_id, raw_base_url_item in raw_base_urls.items():
            try:
                base_url_id = _normalize_provider_base_url_id(str(raw_base_url_id))
            except AiChatUserConfigError:
                continue
            if not isinstance(raw_base_url_item, dict):
                continue

            value = raw_base_url_item.get("value")
            if not isinstance(value, str) or not value.strip():
                continue

            label = raw_base_url_item.get("label")
            updated_at = raw_base_url_item.get("updated_at")
            base_urls[base_url_id] = {
                "label": label.strip() if isinstance(label, str) and label.strip() else "未命名地址",
                "value": value.strip(),
                "updated_at": float(updated_at) if isinstance(updated_at, (int, float)) else None,
            }

    legacy_base_url = raw_item.get("base_url")
    legacy_updated_at = raw_item.get("updated_at")
    normalized_legacy_base_url = legacy_base_url.strip() if isinstance(legacy_base_url, str) and legacy_base_url.strip() else ""
    legacy_base_url_id = ""
    if normalized_legacy_base_url:
        for base_url_id, base_url_item in base_urls.items():
            if base_url_item["value"] == normalized_legacy_base_url:
                legacy_base_url_id = base_url_id
                break
        if not legacy_base_url_id:
            legacy_base_url_id = "base-url-legacy"
            if legacy_base_url_id in base_urls:
                legacy_base_url_id = f"base-url-{uuid.uuid4().hex[:12]}"
            base_urls[legacy_base_url_id] = {
                **_build_legacy_saved_base_url(
                    normalized_legacy_base_url,
                    float(legacy_updated_at) if isinstance(legacy_updated_at, (int, float)) else None,
                )["base-url-legacy"],
                "label": _generate_next_base_url_label(base_urls),
            }

    active_base_url_id = raw_item.get("active_base_url_id")
    normalized_active_base_url_id = (
        _normalize_provider_base_url_id(active_base_url_id)
        if isinstance(active_base_url_id, str) and active_base_url_id.strip()
        else ""
    )
    if normalized_active_base_url_id not in base_urls:
        normalized_active_base_url_id = legacy_base_url_id if legacy_base_url_id in base_urls else _pick_default_active_base_url_id(base_urls)

    return base_urls, normalized_active_base_url_id


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
                "label": "",
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


def _normalize_custom_provider_item(provider_id: str, raw_item: dict[str, Any]) -> dict[str, Any] | None:
    label = raw_item.get("label")
    if not isinstance(label, str) or not label.strip():
        return None

    try:
        kind = _normalize_custom_provider_kind(str(raw_item.get("kind") or CUSTOM_PROVIDER_KIND_OPENAI))
        visibility = _normalize_custom_provider_visibility(
            str(raw_item.get("visibility") or raw_item.get("sharing_mode") or CUSTOM_PROVIDER_VISIBILITY_PRIVATE)
        )
    except AiChatUserConfigError:
        return None

    capabilities = _get_custom_provider_capabilities(kind)
    base_url = raw_item.get("base_url")
    default_model = raw_item.get("default_model")
    models = raw_item.get("models")
    updated_at = raw_item.get("updated_at")

    normalized_models = tuple(
        item.strip()
        for item in (models or [])
        if isinstance(item, str) and item.strip()
    )
    resolved_base_url = base_url.strip() if isinstance(base_url, str) else str(capabilities["default_base_url"])
    resolved_default_model = (
        default_model.strip()
        if isinstance(default_model, str) and default_model.strip()
        else str(capabilities["default_model"])
    )

    return {
        "id": provider_id,
        "label": label.strip(),
        "kind": kind,
        "visibility": visibility,
        "base_url": resolved_base_url,
        "default_model": resolved_default_model,
        "models": normalized_models,
        "supports_stream": bool(capabilities["supports_stream"]),
        "supports_vision": bool(capabilities["supports_vision"]),
        "requires_api_key": bool(capabilities["requires_api_key"]),
        "timeout_seconds": float(capabilities["timeout_seconds"]),
        "updated_at": float(updated_at) if isinstance(updated_at, (int, float)) else None,
    }


def _build_custom_provider_summary_item(
    item: dict[str, Any],
    *,
    provider_id: str,
    label: str | None = None,
    can_manage: bool,
    sharing_mode: str,
) -> dict[str, Any]:
    return {
        "id": provider_id,
        "label": label or item["label"],
        "kind": item["kind"],
        "configured": False,
        "base_url": item["base_url"],
        "default_model": item["default_model"],
        "models": list(item["models"]),
        "supports_stream": item["supports_stream"],
        "supports_vision": item["supports_vision"],
        "requires_api_key": item["requires_api_key"],
        "is_custom": True,
        "sharing_mode": sharing_mode,
        "can_manage": can_manage,
        "updated_at": item["updated_at"],
    }


def _build_custom_provider_runtime_config(
    item: dict[str, Any],
    *,
    provider_id: str,
    label: str | None = None,
    can_manage: bool,
    sharing_mode: str,
) -> AiProviderConfig:
    return AiProviderConfig(
        id=provider_id,
        label=label or item["label"],
        kind=item["kind"],
        base_url=item["base_url"],
        default_model=item["default_model"],
        timeout_seconds=item["timeout_seconds"],
        api_key="",
        supports_stream=item["supports_stream"],
        supports_vision=item["supports_vision"],
        requires_api_key=item["requires_api_key"],
        configured=False,
        models=item["models"],
        is_custom=True,
        sharing_mode=sharing_mode,
        can_manage=can_manage,
    )


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

        preferred_model = raw_item.get("preferred_model")
        preferred_models = _normalize_model_list(raw_item.get("preferred_models"), preferred_model if isinstance(preferred_model, str) else None)
        updated_at = raw_item.get("updated_at")
        base_urls, active_base_url_id = _load_saved_base_urls(raw_item)
        active_base_url_item = base_urls.get(active_base_url_id) if active_base_url_id else None
        api_keys, active_key_id = _load_saved_api_keys(raw_item)

        providers[normalized_id] = {
            "base_url": active_base_url_item["value"] if active_base_url_item else "",
            "base_urls": base_urls,
            "active_base_url_id": active_base_url_id,
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

        normalized_item = _normalize_custom_provider_item(normalized_id, raw_item)
        if normalized_item is None:
            continue
        providers[normalized_id] = normalized_item
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


def _generate_next_base_url_label(base_urls: dict[str, dict[str, Any]]) -> str:
    max_index = 0
    for item in base_urls.values():
        label = item.get("label")
        if not isinstance(label, str):
            continue
        match = re.fullmatch(r"地址\s*(\d+)", label.strip(), flags=re.IGNORECASE)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return f"地址 {max_index + 1}"


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
            _build_custom_provider_summary_item(
                item,
                provider_id=item["id"],
                can_manage=True,
                sharing_mode=item["visibility"],
            )
        )
    return items


def list_user_ai_chat_custom_provider_configs(session: Session, user_id: int) -> tuple[AiProviderConfig, ...]:
    providers = _load_custom_provider_map(session, user_id)
    return tuple(
        _build_custom_provider_runtime_config(
            item,
            provider_id=item["id"],
            can_manage=True,
            sharing_mode=item["visibility"],
        )
        for item in providers.values()
    )


def list_public_ai_chat_custom_providers(session: Session, user_id: int) -> list[dict[str, Any]]:
    rows = session.exec(
        select(AppSetting).where(AppSetting.key.like("user:%:ai_chat_provider_configs"))
    ).all()
    items: list[dict[str, Any]] = []
    owner_rows: dict[int, User | None] = {}

    for row in rows:
        owner_user_id = _extract_user_id_from_setting_key(row.key)
        if owner_user_id is None or owner_user_id == user_id or not isinstance(row.value, dict):
            continue

        raw_providers = row.value.get("custom_providers")
        if not isinstance(raw_providers, dict):
            continue

        owner = owner_rows.get(owner_user_id)
        if owner_user_id not in owner_rows:
            owner = session.get(User, owner_user_id)
            owner_rows[owner_user_id] = owner

        owner_label = ""
        if owner is not None:
            owner_label = owner.nickname.strip() or owner.username.strip()

        for provider_id, raw_item in raw_providers.items():
            try:
                local_provider_id = _normalize_provider_id(provider_id)
            except AiChatUserConfigError:
                continue
            if not isinstance(raw_item, dict):
                continue

            item = _normalize_custom_provider_item(local_provider_id, raw_item)
            if item is None or item["visibility"] != CUSTOM_PROVIDER_VISIBILITY_PUBLIC:
                continue
            if item["kind"] == CUSTOM_PROVIDER_KIND_CODEX and (owner is None or not owner.is_superuser):
                continue

            exposed_provider_id = _build_public_custom_provider_id(owner_user_id, local_provider_id)
            label = item["label"] if not owner_label else f"{item['label']} @ {owner_label}"
            items.append(
                _build_custom_provider_summary_item(
                    item,
                    provider_id=exposed_provider_id,
                    label=label,
                    can_manage=False,
                    sharing_mode=CUSTOM_PROVIDER_VISIBILITY_PUBLIC,
                )
            )

    return sorted(items, key=lambda item: (str(item["label"]).lower(), str(item["id"])))


def list_public_ai_chat_custom_provider_configs(session: Session, user_id: int) -> tuple[AiProviderConfig, ...]:
    rows = list_public_ai_chat_custom_providers(session, user_id)
    runtime_configs: list[AiProviderConfig] = []
    for row in rows:
        runtime_configs.append(
            AiProviderConfig(
                id=str(row["id"]),
                label=str(row["label"]),
                kind=str(row["kind"]),
                base_url=str(row["base_url"]),
                default_model=str(row["default_model"]),
                timeout_seconds=600.0 if str(row["kind"]) == CUSTOM_PROVIDER_KIND_CODEX else 120.0,
                api_key="",
                supports_stream=bool(row["supports_stream"]),
                supports_vision=bool(row["supports_vision"]),
                requires_api_key=bool(row["requires_api_key"]),
                configured=False,
                models=tuple(str(item).strip() for item in row["models"] if str(item).strip()),
                is_custom=True,
                sharing_mode=str(row["sharing_mode"]),
                can_manage=bool(row["can_manage"]),
            )
        )
    return tuple(runtime_configs)


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
            "active_base_url_id": None,
            "base_url_count": 0,
            "base_urls": [],
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
    active_base_url_id = (
        item["active_base_url_id"]
        if item["active_base_url_id"] in item["base_urls"]
        else _pick_default_active_base_url_id(item["base_urls"])
    )
    active_base_url = item["base_urls"].get(active_base_url_id) if active_base_url_id else None
    return {
        "provider": normalized_id,
        "base_url": active_base_url["value"] if active_base_url else "",
        "active_base_url_id": active_base_url_id or None,
        "base_url_count": len(item["base_urls"]),
        "base_urls": [
            _build_saved_base_url_summary(base_url_id, base_url_item, active_base_url_id)
            for base_url_id, base_url_item in item["base_urls"].items()
        ],
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
    clear_api_key: bool = False,
) -> dict[str, Any]:
    normalized_id = _normalize_provider_id(provider_id)
    providers = _load_provider_map(session, user_id)
    current = providers.get(normalized_id, _default_provider_item())

    next_base_urls = dict(current["base_urls"])
    next_active_base_url_id = current["active_base_url_id"]
    if base_url is not None:
        normalized_base_url = base_url.strip()
        if normalized_base_url:
            matched_base_url_id = ""
            for base_url_id, base_url_item in next_base_urls.items():
                if base_url_item["value"] == normalized_base_url:
                    matched_base_url_id = base_url_id
                    break
            if not matched_base_url_id:
                matched_base_url_id = f"base-url-{uuid.uuid4().hex[:12]}"
                next_base_urls[matched_base_url_id] = {
                    "label": _generate_next_base_url_label(next_base_urls),
                    "value": normalized_base_url,
                    "updated_at": time.time(),
                }
        else:
            next_base_urls = {}
            next_active_base_url_id = ""

    if next_active_base_url_id not in next_base_urls:
        next_active_base_url_id = _pick_default_active_base_url_id(next_base_urls)
    active_base_url_item = next_base_urls.get(next_active_base_url_id) if next_active_base_url_id else None
    next_base_url = active_base_url_item["value"] if active_base_url_item else ""

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
            "label": "",
            "api_key_encrypted": _encrypt_secret(normalized_api_key),
            "masked_value": _mask_secret(normalized_api_key),
            "updated_at": time.time(),
        }

    if next_active_key_id not in next_api_keys:
        next_active_key_id = _pick_default_active_key_id(next_api_keys)

    updated_at = time.time()
    if not next_base_urls and not next_preferred_models and not next_api_keys:
        providers.pop(normalized_id, None)
        _save_provider_map(session, user_id, providers)
        return _build_provider_config_summary(normalized_id, None)

    providers[normalized_id] = {
        "base_url": next_base_url,
        "base_urls": next_base_urls,
        "active_base_url_id": next_active_base_url_id,
        "preferred_models": next_preferred_models,
        "api_keys": next_api_keys,
        "active_key_id": next_active_key_id,
        "updated_at": updated_at,
    }
    _save_provider_map(session, user_id, providers)

    return _build_provider_config_summary(normalized_id, providers[normalized_id])


def activate_user_ai_chat_provider_base_url(
    session: Session,
    user_id: int,
    provider_id: str | None,
    base_url_id: str | None,
) -> dict[str, Any]:
    normalized_id = _normalize_provider_id(provider_id)
    normalized_base_url_id = _normalize_provider_base_url_id(base_url_id)
    providers = _load_provider_map(session, user_id)
    current = providers.get(normalized_id)
    if not current:
        raise AiChatUserConfigError("当前来源还没有账号保存的连接配置")
    if normalized_base_url_id not in current["base_urls"]:
        raise AiChatUserConfigError("指定的地址不存在")

    current["active_base_url_id"] = normalized_base_url_id
    current["base_url"] = current["base_urls"][normalized_base_url_id]["value"]
    current["updated_at"] = time.time()
    providers[normalized_id] = current
    _save_provider_map(session, user_id, providers)
    return _build_provider_config_summary(normalized_id, current)


def update_user_ai_chat_provider_base_url(
    session: Session,
    user_id: int,
    provider_id: str | None,
    base_url_id: str | None,
    *,
    base_url: str | None,
) -> dict[str, Any]:
    normalized_id = _normalize_provider_id(provider_id)
    normalized_base_url_id = _normalize_provider_base_url_id(base_url_id)
    normalized_base_url = (base_url or "").strip()
    if not normalized_base_url:
        raise AiChatUserConfigError("地址不能为空")

    providers = _load_provider_map(session, user_id)
    current = providers.get(normalized_id)
    if not current:
        raise AiChatUserConfigError("当前来源还没有账号保存的连接配置")
    if normalized_base_url_id not in current["base_urls"]:
        raise AiChatUserConfigError("指定的地址不存在")
    for item_id, item in current["base_urls"].items():
        if item_id != normalized_base_url_id and item["value"] == normalized_base_url:
            raise AiChatUserConfigError("这个地址已经存在")

    if current["base_urls"][normalized_base_url_id]["value"] == normalized_base_url:
        return _build_provider_config_summary(normalized_id, current)

    current["base_urls"][normalized_base_url_id] = {
        **current["base_urls"][normalized_base_url_id],
        "value": normalized_base_url,
    }
    if current["active_base_url_id"] == normalized_base_url_id:
        current["base_url"] = normalized_base_url
    current["updated_at"] = time.time()
    providers[normalized_id] = current
    _save_provider_map(session, user_id, providers)
    return _build_provider_config_summary(normalized_id, current)


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


def reveal_user_ai_chat_provider_api_key(
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
    key_item = current["api_keys"].get(normalized_key_id)
    if key_item is None:
        raise AiChatUserConfigError("指定的 API Key 不存在")

    active_key_id = current["active_key_id"] if current["active_key_id"] in current["api_keys"] else _pick_default_active_key_id(current["api_keys"])
    return {
        **_build_saved_key_summary(normalized_key_id, key_item, active_key_id),
        "plaintext_value": _decrypt_secret(key_item["api_key_encrypted"]),
    }


def update_user_ai_chat_provider_api_key(
    session: Session,
    user_id: int,
    provider_id: str | None,
    key_id: str | None,
    *,
    api_key: str | None,
) -> dict[str, Any]:
    normalized_id = _normalize_provider_id(provider_id)
    normalized_key_id = _normalize_provider_key_id(key_id)
    normalized_api_key = (api_key or "").strip()
    if not normalized_api_key:
        raise AiChatUserConfigError("API Key 不能为空")

    providers = _load_provider_map(session, user_id)
    current = providers.get(normalized_id)
    if not current:
        raise AiChatUserConfigError("当前来源还没有账号保存的连接配置")
    key_item = current["api_keys"].get(normalized_key_id)
    if key_item is None:
        raise AiChatUserConfigError("指定的 API Key 不存在")

    if _decrypt_secret(key_item["api_key_encrypted"]) == normalized_api_key:
        return _build_provider_config_summary(normalized_id, current)

    current["api_keys"][normalized_key_id] = {
        **key_item,
        "api_key_encrypted": _encrypt_secret(normalized_api_key),
        "masked_value": _mask_secret(normalized_api_key),
    }
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

    if not current["base_urls"] and not current["preferred_models"] and not next_api_keys:
        providers.pop(normalized_id, None)
        _save_provider_map(session, user_id, providers)
        return _build_provider_config_summary(normalized_id, None)

    current["api_keys"] = next_api_keys
    current["active_key_id"] = next_active_key_id
    current["updated_at"] = time.time()
    providers[normalized_id] = current
    _save_provider_map(session, user_id, providers)
    return _build_provider_config_summary(normalized_id, current)


def delete_user_ai_chat_provider_base_url(
    session: Session,
    user_id: int,
    provider_id: str | None,
    base_url_id: str | None,
) -> dict[str, Any]:
    normalized_id = _normalize_provider_id(provider_id)
    normalized_base_url_id = _normalize_provider_base_url_id(base_url_id)
    providers = _load_provider_map(session, user_id)
    current = providers.get(normalized_id)
    if not current:
        raise AiChatUserConfigError("当前来源还没有账号保存的连接配置")
    if normalized_base_url_id not in current["base_urls"]:
        raise AiChatUserConfigError("指定的地址不存在")

    next_base_urls = dict(current["base_urls"])
    next_base_urls.pop(normalized_base_url_id, None)
    next_active_base_url_id = current["active_base_url_id"]
    if next_active_base_url_id == normalized_base_url_id:
        next_active_base_url_id = _pick_default_active_base_url_id(next_base_urls)
    active_base_url_item = next_base_urls.get(next_active_base_url_id) if next_active_base_url_id else None

    if not next_base_urls and not current["preferred_models"] and not current["api_keys"]:
        providers.pop(normalized_id, None)
        _save_provider_map(session, user_id, providers)
        return _build_provider_config_summary(normalized_id, None)

    current["base_urls"] = next_base_urls
    current["active_base_url_id"] = next_active_base_url_id
    current["base_url"] = active_base_url_item["value"] if active_base_url_item else ""
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
    kind: str | None,
    visibility: str | None,
    base_url: str,
    default_model: str | None = None,
    models: list[str] | None = None,
) -> dict[str, Any]:
    normalized_label = label.strip()
    normalized_kind = _normalize_custom_provider_kind(kind)
    normalized_visibility = _normalize_custom_provider_visibility(visibility)
    capabilities = _get_custom_provider_capabilities(normalized_kind)
    normalized_base_url = base_url.strip().rstrip("/") if normalized_kind != CUSTOM_PROVIDER_KIND_CODEX else base_url.strip()
    normalized_default_model = (default_model or "").strip() or str(capabilities["default_model"])
    normalized_models = [
        item.strip()
        for item in (models or [])
        if isinstance(item, str) and item.strip()
    ]
    if not normalized_label:
        raise AiChatUserConfigError("自定义来源名称不能为空")
    if not normalized_base_url:
        raise AiChatUserConfigError("自定义来源连接信息不能为空")

    providers = _load_provider_map(session, user_id)
    custom_providers = _load_custom_provider_map(session, user_id)
    provider_id = f"custom-{_slugify_custom_provider_label(normalized_label)}"
    while provider_id in custom_providers:
        provider_id = f"custom-{_slugify_custom_provider_label(normalized_label)}-{uuid.uuid4().hex[:4]}"

    updated_at = time.time()
    custom_providers[provider_id] = {
        "id": provider_id,
        "label": normalized_label,
        "kind": normalized_kind,
        "visibility": normalized_visibility,
        "base_url": normalized_base_url,
        "default_model": normalized_default_model,
        "models": tuple(normalized_models),
        "updated_at": updated_at,
    }
    _save_all_maps(session, user_id, providers, custom_providers)
    normalized_item = _normalize_custom_provider_item(provider_id, custom_providers[provider_id])
    if normalized_item is None:
        raise AiChatUserConfigError("自定义来源保存失败")
    return _build_custom_provider_summary_item(
        normalized_item,
        provider_id=provider_id,
        can_manage=True,
        sharing_mode=normalized_visibility,
    )


def delete_user_ai_chat_custom_provider(session: Session, user_id: int, provider_id: str | None) -> None:
    if _parse_public_custom_provider_id(provider_id):
        raise AiChatUserConfigError("共享来源不能在这里删除，请删除你自己的原始来源")
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
