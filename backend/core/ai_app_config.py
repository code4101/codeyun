from __future__ import annotations

import time
from typing import Any

from sqlmodel import Session

from backend.core.ai_chat import AiProviderConfig, get_default_ai_provider_id
from backend.core.ai_chat_user_config import (
    AiChatUserConfigError,
    get_user_ai_chat_provider_runtime_config,
    list_user_ai_chat_custom_provider_configs,
)
from backend.core.ollama_access_keys import ensure_ollama_access_key_allowed
from backend.models import AppSetting, User


AI_APP_NOTE_TAXONOMY = "note-taxonomy"
AI_APP_GIT_COMMIT = "ai-git-commit"
AI_APP_CODEX_DIARY = "codex-diary"
AI_APP_GIT_COMMIT_DEFAULT_PROVIDER = "deepseek"
AI_APP_GIT_COMMIT_DEFAULT_MODEL = "deepseek-v4-flash"
AI_APP_CODEX_DIARY_DEFAULT_PROVIDER = "deepseek"
AI_APP_CODEX_DIARY_DEFAULT_MODEL = "deepseek-v4-pro"
_CODEX_CLI_PROVIDER_ALIASES = {"codex", "codex-cli", "custom-codex-cli"}

AI_APP_CONFIG_SETTING_KEY_PREFIX = "ai_app.config.user"
LEGACY_AI_GIT_COMMIT_CONFIG_SETTING_KEY_PREFIX = "ai_git_commit.config.user"

AI_APP_DEFINITIONS: tuple[dict[str, str], ...] = (
    {
        "id": AI_APP_NOTE_TAXONOMY,
        "label": "笔记分类",
        "description": "仅分析当前标题，并参考已有条目的标题、分类、形态、阶段后回写结果。",
    },
    {
        "id": AI_APP_GIT_COMMIT,
        "label": "AI提交",
        "description": "生成 Git 提交信息，自动 Git 提交和分层归纳提交共用这一组模型配置。",
    },
    {
        "id": AI_APP_CODEX_DIARY,
        "label": "Codex 星图日记",
        "description": "读取 Codex 会话并生成星图日记节点。",
    },
)


class AiAppConfigError(RuntimeError):
    """Raised when persisted AI app configuration cannot be used."""


def build_ai_app_config_setting_key(user_id: int) -> str:
    return f"{AI_APP_CONFIG_SETTING_KEY_PREFIX}.{int(user_id)}"


def build_legacy_ai_git_commit_config_setting_key(user_id: int) -> str:
    return f"{LEGACY_AI_GIT_COMMIT_CONFIG_SETTING_KEY_PREFIX}.{int(user_id)}"


def _known_app_ids() -> set[str]:
    return {item["id"] for item in AI_APP_DEFINITIONS}


def _normalize_app_id(value: str | None) -> str:
    normalized = (value or "").strip()
    if normalized not in _known_app_ids():
        raise AiAppConfigError("未知 AI 功能配置")
    return normalized


def _normalize_app_config_item(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    updated_at = item.get("updated_at")
    return {
        "enabled": item.get("enabled") is not False,
        "provider": str(item.get("provider") or item.get("provider_id") or "").strip().lower(),
        "model": str(item.get("model") or "").strip(),
        "updated_at": float(updated_at) if isinstance(updated_at, (int, float)) else None,
    }


def _is_codex_cli_provider(value: Any) -> bool:
    normalized = str(value or "").strip().lower().replace("_", "-")
    return normalized in _CODEX_CLI_PROVIDER_ALIASES or normalized.endswith("-codex-cli")


def _coerce_app_config_for_app(app_id: str, item: dict[str, Any]) -> dict[str, Any]:
    if app_id == AI_APP_GIT_COMMIT and _is_codex_cli_provider(item.get("provider")):
        return {
            **item,
            "provider": AI_APP_GIT_COMMIT_DEFAULT_PROVIDER,
            "model": AI_APP_GIT_COMMIT_DEFAULT_MODEL,
        }
    if app_id == AI_APP_CODEX_DIARY:
        if _is_codex_cli_provider(item.get("provider")):
            return {
                **item,
                "provider": AI_APP_CODEX_DIARY_DEFAULT_PROVIDER,
                "model": AI_APP_CODEX_DIARY_DEFAULT_MODEL,
            }
        if item.get("provider") == AI_APP_CODEX_DIARY_DEFAULT_PROVIDER and not item.get("model"):
            return {
                **item,
                "model": AI_APP_CODEX_DIARY_DEFAULT_MODEL,
            }
    return item


def _default_app_config(app_id: str) -> dict[str, Any]:
    if app_id == AI_APP_GIT_COMMIT:
        provider = AI_APP_GIT_COMMIT_DEFAULT_PROVIDER
        model = AI_APP_GIT_COMMIT_DEFAULT_MODEL
    elif app_id == AI_APP_CODEX_DIARY:
        provider = AI_APP_CODEX_DIARY_DEFAULT_PROVIDER
        model = AI_APP_CODEX_DIARY_DEFAULT_MODEL
    else:
        provider = ""
        model = ""
    return {
        "enabled": True,
        "provider": provider,
        "model": model,
        "updated_at": None,
    }


def _load_app_config_map(session: Session, user_id: int) -> dict[str, dict[str, Any]]:
    row = session.get(AppSetting, build_ai_app_config_setting_key(user_id))
    if row is None or not isinstance(row.value, dict):
        return {}

    raw_apps = row.value.get("apps")
    if not isinstance(raw_apps, dict):
        return {}

    apps: dict[str, dict[str, Any]] = {}
    for app_id, raw_item in raw_apps.items():
        try:
            normalized_app_id = _normalize_app_id(str(app_id))
        except AiAppConfigError:
            continue
        apps[normalized_app_id] = _normalize_app_config_item(raw_item)
    return apps


def _save_app_config_map(session: Session, user_id: int, apps: dict[str, dict[str, Any]]) -> None:
    setting_key = build_ai_app_config_setting_key(user_id)
    row = session.get(AppSetting, setting_key)
    payload = {"apps": apps}
    now = time.time()
    if row is None:
        row = AppSetting(key=setting_key, value=payload, updated_at=now)
    else:
        row.value = payload
        row.updated_at = now
    session.add(row)
    session.commit()


def _load_legacy_ai_git_commit_config(session: Session, user_id: int) -> dict[str, Any] | None:
    row = session.get(AppSetting, build_legacy_ai_git_commit_config_setting_key(user_id))
    if row is None or not isinstance(row.value, dict):
        return None
    provider = str(row.value.get("provider_id") or row.value.get("provider") or "").strip().lower()
    model = str(row.value.get("model") or "").strip()
    if not provider and not model:
        return None
    return {
        "enabled": True,
        "provider": provider,
        "model": model,
        "updated_at": float(row.updated_at) if isinstance(row.updated_at, (int, float)) else None,
    }


def get_user_ai_app_config(session: Session, user_id: int, app_id: str) -> dict[str, Any]:
    normalized_app_id = _normalize_app_id(app_id)
    apps = _load_app_config_map(session, user_id)
    item = apps.get(normalized_app_id)
    if item is None and normalized_app_id == AI_APP_GIT_COMMIT:
        item = _load_legacy_ai_git_commit_config(session, user_id)
    if item is None:
        item = _default_app_config(normalized_app_id)
    item = _coerce_app_config_for_app(normalized_app_id, item)
    return {
        "id": normalized_app_id,
        **item,
    }


def list_user_ai_app_configs(session: Session, user_id: int) -> list[dict[str, Any]]:
    return [
        {
            **definition,
            **get_user_ai_app_config(session, user_id, definition["id"]),
        }
        for definition in AI_APP_DEFINITIONS
    ]


def save_user_ai_app_config(
    session: Session,
    user_id: int,
    app_id: str,
    *,
    enabled: bool = True,
    provider: str = "",
    model: str = "",
) -> dict[str, Any]:
    normalized_app_id = _normalize_app_id(app_id)
    apps = _load_app_config_map(session, user_id)
    now = time.time()
    apps[normalized_app_id] = {
        "enabled": bool(enabled),
        "provider": provider.strip().lower(),
        "model": model.strip(),
        "updated_at": now,
    }
    apps[normalized_app_id] = _coerce_app_config_for_app(normalized_app_id, apps[normalized_app_id])
    _save_app_config_map(session, user_id, apps)
    return {
        "id": normalized_app_id,
        **apps[normalized_app_id],
    }


def resolve_ai_app_runtime_config(
    *,
    session: Session,
    current_user: User | None,
    app_id: str,
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    try:
        app_config = (
            get_user_ai_app_config(session, current_user.id, app_id)
            if current_user is not None
            else {"provider": "", "model": "", "enabled": True}
        )
        configured_provider = str(app_config.get("provider") or "").strip().lower()
        resolved_provider = (provider or configured_provider or get_default_ai_provider_id()).strip().lower()
        if not resolved_provider:
            resolved_provider = get_default_ai_provider_id()

        resolved_base_url = (base_url or "").strip()
        resolved_api_key = (api_key or "").strip()
        resolved_model = (model or "").strip() or str(app_config.get("model") or "").strip()
        extra_providers: tuple[AiProviderConfig, ...] = ()

        if current_user is not None:
            extra_providers = tuple(list_user_ai_chat_custom_provider_configs(session, current_user.id))
            saved_config = get_user_ai_chat_provider_runtime_config(session, current_user.id, resolved_provider)
            if not resolved_base_url:
                resolved_base_url = str(saved_config.get("base_url") or "").strip()
            if not resolved_api_key:
                resolved_api_key = str(saved_config.get("api_key") or "").strip()
            if not resolved_model:
                preferred_models = saved_config.get("preferred_models")
                if isinstance(preferred_models, list):
                    resolved_model = next(
                        (str(item).strip() for item in preferred_models if str(item).strip()),
                        "",
                    )
                if not resolved_model:
                    resolved_model = str(saved_config.get("preferred_model") or "").strip()

        if resolved_provider == "ollama":
            ensure_ollama_access_key_allowed(session, resolved_api_key)

        return {
            "app": app_id,
            "provider": resolved_provider,
            "base_url": resolved_base_url or None,
            "api_key": resolved_api_key or None,
            "model": resolved_model or None,
            "extra_providers": extra_providers,
        }
    except AiChatUserConfigError as exc:
        raise AiAppConfigError(str(exc)) from exc
