from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from backend.core.ai.chat_user_config import (
    AiChatUserConfigError,
    get_user_ai_chat_provider_runtime_config,
    list_user_ai_chat_custom_provider_configs,
)
from backend.models import User


SYSTEM_AI_RESOURCE_ACCOUNT_USERNAME = "code4101"


class SystemAiResourceError(RuntimeError):
    """Raised when a system-owned AI resource cannot be resolved."""


def resolve_system_ai_resource(
    *,
    session: Session,
    provider_id: str,
) -> dict[str, Any]:
    """Resolve a system AI resource without exposing its backing account to callers."""
    normalized_provider = provider_id.strip().lower()
    if not normalized_provider:
        raise SystemAiResourceError("系统 AI 来源不能为空")

    resource_account = session.exec(
        select(User).where(User.username == SYSTEM_AI_RESOURCE_ACCOUNT_USERNAME)
    ).first()
    if resource_account is None or not resource_account.is_active:
        raise SystemAiResourceError("系统 AI 资源不可用")

    try:
        saved_config = get_user_ai_chat_provider_runtime_config(
            session,
            resource_account.id,
            normalized_provider,
        )
        return {
            "system_resource_id": f"ai.provider.{normalized_provider}",
            "provider": normalized_provider,
            "base_url": str(saved_config.get("base_url") or "").strip() or None,
            "api_key": str(saved_config.get("api_key") or "").strip() or None,
            "extra_providers": tuple(
                list_user_ai_chat_custom_provider_configs(
                    session,
                    resource_account.id,
                )
            ),
        }
    except AiChatUserConfigError as exc:
        raise SystemAiResourceError("系统 AI 资源不可用") from exc
