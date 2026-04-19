from __future__ import annotations

import json
from dataclasses import replace
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.ai_chat import (
    AiProviderConfig,
    OllamaClientError,
    chat_with_provider,
    get_ai_provider,
    get_ai_provider_status,
    get_default_ai_provider_id,
    list_ai_provider_summaries,
    stream_chat_with_provider,
)
from backend.core.codex_access_keys import (
    create_codex_access_key,
    delete_codex_access_key,
    ensure_codex_access_key_allowed,
    list_codex_access_keys,
    reveal_codex_access_key,
)
from backend.core.ai_chat_prompt_cards import (
    list_user_ai_chat_prompt_cards,
    save_user_ai_chat_prompt_cards,
)
from backend.core.ai_chat_session import (
    get_user_ai_chat_sessions,
    save_user_ai_chat_sessions,
)
from backend.core.ollama_access_keys import (
    create_ollama_access_key,
    delete_ollama_access_key,
    ensure_ollama_access_key_allowed,
    list_ollama_access_keys,
    reveal_ollama_access_key,
)
from backend.core.ai_chat_user_config import (
    AiChatUserConfigError,
    activate_user_ai_chat_provider_api_key,
    delete_user_ai_chat_custom_provider,
    delete_user_ai_chat_provider_api_key,
    delete_user_ai_chat_provider_config,
    get_user_ai_chat_provider_runtime_config,
    list_public_ai_chat_custom_provider_configs,
    list_user_ai_chat_custom_provider_configs,
    list_user_ai_chat_provider_configs,
    save_user_ai_chat_custom_provider,
    save_user_ai_chat_provider_config,
)
from backend.core.auth import get_current_active_superuser, get_current_user_from_token, get_optional_current_user_from_token
from backend.core.feature_access_guard import require_feature_access_dependency
from backend.core.settings import get_settings
from backend.db import get_session
from backend.models import User


router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("ai-tools"))],
)
ANONYMOUS_DEFAULT_PROVIDER_ID = "ollama"


class AiChatImageInput(BaseModel):
    name: Optional[str] = None
    mime_type: Optional[str] = None
    data_base64: str


class AiChatMessageInput(BaseModel):
    role: Literal["user", "assistant"]
    content: str = ""
    images: list[AiChatImageInput] = Field(default_factory=list)


class AiChatRequest(BaseModel):
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    messages: list[AiChatMessageInput] = Field(default_factory=list)
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    temperature: Optional[float] = None
    stream: Optional[bool] = None


class AiChatResponse(BaseModel):
    model: str
    content: str
    created_at: Optional[str] = None
    done_reason: Optional[str] = None
    prompt_eval_count: Optional[int] = None
    eval_count: Optional[int] = None
    total_duration: Optional[int] = None


class AiChatStatusResponse(BaseModel):
    provider: str
    label: str
    kind: str
    is_custom: bool
    sharing_mode: str = "builtin"
    can_manage: bool = False
    available: bool
    requires_auth: bool
    configured: bool
    supports_stream: bool
    supports_vision: bool
    requires_api_key: bool
    base_url: str
    default_model: str
    models: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class AiChatProviderSummary(BaseModel):
    id: str
    label: str
    kind: str
    is_custom: bool
    sharing_mode: str = "builtin"
    can_manage: bool = False
    configured: bool
    requires_api_key: bool
    base_url: str
    default_model: str
    models: list[str] = Field(default_factory=list)
    supports_stream: bool
    supports_vision: bool


class AiChatProvidersResponse(BaseModel):
    default_provider: str
    items: list[AiChatProviderSummary] = Field(default_factory=list)


class AiChatStatusRequest(BaseModel):
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class AiChatSavedApiKeySummary(BaseModel):
    id: str
    label: str
    masked_value: str
    is_active: bool
    updated_at: Optional[float] = None


class AiChatSavedProviderConfig(BaseModel):
    provider: str
    base_url: str
    preferred_model: str = ""
    preferred_models: list[str] = Field(default_factory=list)
    has_api_key: bool
    active_key_id: Optional[str] = None
    key_count: int = 0
    keys: list[AiChatSavedApiKeySummary] = Field(default_factory=list)
    updated_at: Optional[float] = None


class AiChatSavedConfigsResponse(BaseModel):
    signed_in: bool
    items: list[AiChatSavedProviderConfig] = Field(default_factory=list)


class AiChatOllamaAccessKeySummary(BaseModel):
    id: str
    label: str
    masked_value: str
    created_at: Optional[float] = None
    updated_at: Optional[float] = None
    created_by_user_id: Optional[int] = None


class AiChatOllamaAccessKeysResponse(BaseModel):
    items: list[AiChatOllamaAccessKeySummary] = Field(default_factory=list)


class AiChatCreateOllamaAccessKeyRequest(BaseModel):
    label: Optional[str] = None


class AiChatOllamaAccessKeyDetail(AiChatOllamaAccessKeySummary):
    plaintext_value: str


class AiChatCodexAccessKeySummary(BaseModel):
    id: str
    label: str
    masked_value: str
    created_at: Optional[float] = None
    updated_at: Optional[float] = None
    created_by_user_id: Optional[int] = None


class AiChatCodexAccessKeysResponse(BaseModel):
    items: list[AiChatCodexAccessKeySummary] = Field(default_factory=list)


class AiChatCreateCodexAccessKeyRequest(BaseModel):
    label: Optional[str] = None


class AiChatCodexAccessKeyDetail(AiChatCodexAccessKeySummary):
    plaintext_value: str


class AiChatPromptCard(BaseModel):
    id: str
    title: str
    content: str
    updated_at: Optional[float] = None


class AiChatPromptCardsResponse(BaseModel):
    signed_in: bool
    selected_id: Optional[str] = None
    items: list[AiChatPromptCard] = Field(default_factory=list)


class AiChatPromptCardsUpdateRequest(BaseModel):
    selected_id: Optional[str] = None
    items: list[AiChatPromptCard] = Field(default_factory=list)


class AiChatSessionImage(BaseModel):
    id: str
    name: str = ""
    mime_type: str = ""
    data_base64: str


class AiChatSessionMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str = ""
    images: list[AiChatSessionImage] = Field(default_factory=list)
    target_model_option_ids: list[str] = Field(default_factory=list)
    provider_id: str = ""
    model_option_id: str = ""
    model: str = ""
    display_model: str = ""
    created_at: Optional[str] = None
    total_duration: Optional[float] = None
    error: bool = False


class AiChatSessionItem(BaseModel):
    id: str
    title: str = ""
    preview: str = ""
    provider_id: str = ""
    model: str = ""
    selected_model_option_ids: list[str] = Field(default_factory=list)
    selected_assistant_message_id: Optional[str] = None
    draft: str = ""
    messages: list[AiChatSessionMessage] = Field(default_factory=list)
    updated_at: Optional[float] = None


class AiChatSessionsResponse(BaseModel):
    signed_in: bool
    active_session_id: Optional[str] = None
    items: list[AiChatSessionItem] = Field(default_factory=list)


class AiChatSessionsUpdateRequest(BaseModel):
    active_session_id: Optional[str] = None
    items: list[AiChatSessionItem] = Field(default_factory=list)


class AiChatSaveProviderConfigRequest(BaseModel):
    base_url: Optional[str] = None
    preferred_model: Optional[str] = None
    preferred_models: Optional[list[str]] = None
    api_key: Optional[str] = None
    api_key_label: Optional[str] = None
    clear_api_key: bool = False


class AiChatDeleteSavedConfigResponse(BaseModel):
    success: bool = True


class AiChatCreateCustomProviderRequest(BaseModel):
    label: str
    kind: Literal["openai_compatible", "codex_cli"] = "openai_compatible"
    visibility: Literal["private", "public"] = "private"
    base_url: str
    default_model: Optional[str] = None
    models: list[str] = Field(default_factory=list)


def _ensure_ai_chat_access(current_user: Optional[User]) -> None:
    settings = get_settings()
    if settings.is_production and current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="AI聊天在生产环境下仅对登录用户开放",
        )


def _normalize_chat_request(payload: AiChatRequest) -> list[dict[str, object]]:
    if not payload.messages:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="至少需要一条聊天消息")

    if payload.temperature is not None and not 0 <= payload.temperature <= 2:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="temperature 需在 0 到 2 之间")

    messages: list[dict[str, object]] = []
    has_effective_input = False
    for message in payload.messages:
        image_payloads = [item.data_base64 for item in message.images if item.data_base64.strip()]
        if image_payloads and message.role != "user":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="只有用户消息可以附带图片")

        if message.content.strip() or image_payloads:
            has_effective_input = True

        messages.append(
            {
                "role": message.role,
                "content": message.content,
                "images": image_payloads,
            }
        )

    if not has_effective_input and not (payload.system_prompt or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="消息内容不能为空")

    return messages


def _encode_stream_event(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _get_extra_providers(current_user: Optional[User], session: Session) -> tuple[AiProviderConfig, ...]:
    if current_user is None:
        return ()
    items: list[AiProviderConfig] = []
    for provider in (
        *list_user_ai_chat_custom_provider_configs(session, current_user.id),
        *list_public_ai_chat_custom_provider_configs(session, current_user.id),
    ):
        if provider.kind == "codex_cli":
            if provider.can_manage and not current_user.is_superuser:
                continue
            if not current_user.is_superuser and not provider.can_manage and provider.sharing_mode == "public":
                provider = replace(provider, requires_api_key=True, configured=False)
        items.append(provider)
    return tuple(items)


def _ollama_requires_access_key(provider_id: Optional[str]) -> bool:
    return (provider_id or "").strip().lower() == "ollama"


def _apply_ollama_access_policy_to_provider_item(item: dict[str, object]) -> dict[str, object]:
    normalized_id = str(item.get("id") or "").strip().lower()
    if normalized_id != "ollama":
        return item

    next_item = dict(item)
    next_item["requires_api_key"] = True
    return next_item


def _apply_anonymous_provider_policy(item: dict[str, object]) -> dict[str, object]:
    normalized_id = str(item.get("id") or "").strip().lower()
    if normalized_id == "ollama":
        return item
    if not bool(item.get("requires_api_key")):
        return item

    next_item = dict(item)
    next_item["configured"] = False
    return next_item


def _get_default_provider_id_for_request(
    current_user: Optional[User],
    provider_items: list[dict[str, object]],
) -> str:
    provider_ids = [
        str(item.get("id") or "").strip().lower()
        for item in provider_items
        if str(item.get("id") or "").strip()
    ]

    if current_user is None and ANONYMOUS_DEFAULT_PROVIDER_ID in provider_ids:
        return ANONYMOUS_DEFAULT_PROVIDER_ID

    preferred = get_default_ai_provider_id().strip().lower()
    if preferred in provider_ids:
        preferred_item = next(
            (item for item in provider_items if str(item.get("id") or "").strip().lower() == preferred),
            None,
        )
        if preferred_item and bool(preferred_item.get("configured")):
            return preferred

    for item in provider_items:
        provider_id = str(item.get("id") or "").strip().lower()
        if provider_id and bool(item.get("configured")):
            return provider_id

    if preferred in provider_ids:
        return preferred
    if provider_ids:
        return provider_ids[0]
    return ANONYMOUS_DEFAULT_PROVIDER_ID


def _resolve_runtime_provider_config(
    *,
    provider: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
    current_user: Optional[User],
    session: Session,
    extra_providers: tuple[AiProviderConfig, ...],
) -> tuple[str, Optional[str], Optional[str]]:
    resolved_provider = (provider or "").strip().lower()
    if not resolved_provider:
        resolved_provider = (
            ANONYMOUS_DEFAULT_PROVIDER_ID
            if current_user is None
            else get_default_ai_provider_id().strip().lower()
        ) or ANONYMOUS_DEFAULT_PROVIDER_ID
    resolved_base_url = (base_url or "").strip()
    resolved_api_key = (api_key or "").strip()
    provider_config = get_ai_provider(resolved_provider, extra_providers=extra_providers)

    if current_user is None:
        if _ollama_requires_access_key(resolved_provider):
            ensure_ollama_access_key_allowed(session, resolved_api_key)
            return resolved_provider, resolved_base_url or None, resolved_api_key or None
        if provider_config.kind == "codex_cli" and provider_config.requires_api_key:
            ensure_codex_access_key_allowed(session, resolved_api_key)
        return resolved_provider, resolved_base_url or None, resolved_api_key

    saved_config = get_user_ai_chat_provider_runtime_config(session, current_user.id, resolved_provider)
    resolved_api_key = resolved_api_key or saved_config["api_key"] or None
    if _ollama_requires_access_key(resolved_provider):
        ensure_ollama_access_key_allowed(session, resolved_api_key)
    if provider_config.kind == "codex_cli" and provider_config.requires_api_key:
        ensure_codex_access_key_allowed(session, resolved_api_key)
    if provider_config.kind == "codex_cli" and not provider_config.can_manage:
        resolved_base_url = provider_config.base_url
    return (
        resolved_provider,
        resolved_base_url or saved_config["base_url"] or None,
        resolved_api_key,
    )


def _build_ai_chat_status_response(
    *,
    provider: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
    settings,
    current_user: Optional[User],
    session: Session,
) -> AiChatStatusResponse:
    saved_runtime_config: dict[str, object] | None = None
    extra_providers = _get_extra_providers(current_user, session)
    try:
        resolved_provider, resolved_base_url, resolved_api_key = _resolve_runtime_provider_config(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            current_user=current_user,
            session=session,
            extra_providers=extra_providers,
        )
        provider_status = get_ai_provider_status(
            provider_id=resolved_provider,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            extra_providers=extra_providers,
        )
        if current_user is not None:
            saved_runtime_config = get_user_ai_chat_provider_runtime_config(session, current_user.id, resolved_provider)
    except (OllamaClientError, AiChatUserConfigError) as exc:
        fallback_provider: AiProviderConfig | None = None
        try:
            fallback_provider = get_ai_provider(provider, extra_providers=extra_providers)
        except OllamaClientError:
            fallback_provider = None
        return AiChatStatusResponse(
            provider=fallback_provider.id if fallback_provider is not None else (provider or get_default_ai_provider_id()),
            label=fallback_provider.label if fallback_provider is not None else (provider or get_default_ai_provider_id()),
            kind=fallback_provider.kind if fallback_provider is not None else "unknown",
            is_custom=fallback_provider.is_custom if fallback_provider is not None else False,
            sharing_mode=fallback_provider.sharing_mode if fallback_provider is not None else "builtin",
            can_manage=fallback_provider.can_manage if fallback_provider is not None else False,
            available=False,
            requires_auth=settings.is_production,
            configured=False,
            supports_stream=fallback_provider.supports_stream if fallback_provider is not None else True,
            supports_vision=fallback_provider.supports_vision if fallback_provider is not None else False,
            requires_api_key=(
                True
                if _ollama_requires_access_key(
                    fallback_provider.id if fallback_provider is not None else (provider or get_default_ai_provider_id())
                )
                else (fallback_provider.requires_api_key if fallback_provider is not None else False)
            ),
            base_url=base_url or (fallback_provider.base_url if fallback_provider is not None else ""),
            default_model=fallback_provider.default_model if fallback_provider is not None else "",
            models=list(fallback_provider.models) if fallback_provider is not None else [],
            error=str(exc),
        )

    models = list(provider_status["models"])
    preferred_models: list[str] = []
    if isinstance(saved_runtime_config, dict):
        raw_preferred_models = saved_runtime_config.get("preferred_models")
        if isinstance(raw_preferred_models, list):
            preferred_models = [item.strip() for item in raw_preferred_models if isinstance(item, str) and item.strip()]
        if not preferred_models:
            raw_preferred_model = saved_runtime_config.get("preferred_model")
            if isinstance(raw_preferred_model, str) and raw_preferred_model.strip():
                preferred_models = [raw_preferred_model.strip()]
    if preferred_models:
        for model_name in reversed(preferred_models):
            if model_name in models:
                models.remove(model_name)
            models.insert(0, model_name)
        provider_status["default_model"] = preferred_models[0]

    return AiChatStatusResponse(
        provider=provider_status["id"],
        label=provider_status["label"],
        kind=provider_status["kind"],
        is_custom=provider_status["is_custom"],
        sharing_mode=str(provider_status.get("sharing_mode") or "builtin"),
        can_manage=bool(provider_status.get("can_manage")),
        available=provider_status["available"],
        requires_auth=settings.is_production,
        configured=provider_status["configured"],
        supports_stream=provider_status["supports_stream"],
        supports_vision=provider_status["supports_vision"],
        requires_api_key=True if _ollama_requires_access_key(provider_status["id"]) else provider_status["requires_api_key"],
        base_url=provider_status["base_url"],
        default_model=provider_status["default_model"],
        models=models,
        error=provider_status["error"],
    )


@router.get("/status", response_model=AiChatStatusResponse)
def get_ai_chat_status(
    provider: Optional[str] = None,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    settings = get_settings()
    return _build_ai_chat_status_response(
        provider=provider,
        base_url=None,
        api_key=None,
        settings=settings,
        current_user=current_user,
        session=session,
    )


@router.post("/status", response_model=AiChatStatusResponse)
def post_ai_chat_status(
    payload: AiChatStatusRequest,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    settings = get_settings()
    return _build_ai_chat_status_response(
        provider=payload.provider,
        base_url=payload.base_url,
        api_key=payload.api_key,
        settings=settings,
        current_user=current_user,
        session=session,
    )


@router.get("/providers", response_model=AiChatProvidersResponse)
def get_ai_chat_providers(
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    extra_providers = _get_extra_providers(current_user, session)
    provider_items = [
        _apply_ollama_access_policy_to_provider_item(item)
        for item in list_ai_provider_summaries(extra_providers=extra_providers)
    ]
    if current_user is None:
        provider_items = [_apply_anonymous_provider_policy(item) for item in provider_items]

    return AiChatProvidersResponse(
        default_provider=_get_default_provider_id_for_request(current_user, provider_items),
        items=[AiChatProviderSummary.model_validate(item) for item in provider_items],
    )


@router.get("/saved-configs", response_model=AiChatSavedConfigsResponse)
def get_ai_chat_saved_configs(
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    if current_user is None:
        return AiChatSavedConfigsResponse(signed_in=False, items=[])

    return AiChatSavedConfigsResponse(
        signed_in=True,
        items=[
            AiChatSavedProviderConfig.model_validate(item)
            for item in list_user_ai_chat_provider_configs(session, current_user.id)
        ],
    )


@router.get("/ollama-access-keys", response_model=AiChatOllamaAccessKeysResponse)
def get_ai_chat_ollama_access_keys(
    current_user: User = Depends(get_current_active_superuser),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    return AiChatOllamaAccessKeysResponse(
        items=[
            AiChatOllamaAccessKeySummary.model_validate(item)
            for item in list_ollama_access_keys(session)
        ],
    )


@router.post("/ollama-access-keys", response_model=AiChatOllamaAccessKeyDetail)
def post_ai_chat_ollama_access_key(
    payload: AiChatCreateOllamaAccessKeyRequest,
    current_user: User = Depends(get_current_active_superuser),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    try:
        created = create_ollama_access_key(
            session,
            created_by_user_id=current_user.id,
            label=payload.label,
        )
    except (AiChatUserConfigError, OllamaClientError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AiChatOllamaAccessKeyDetail.model_validate(created)


@router.get("/ollama-access-keys/{key_id}", response_model=AiChatOllamaAccessKeyDetail)
def get_ai_chat_ollama_access_key_detail(
    key_id: str,
    current_user: User = Depends(get_current_active_superuser),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    try:
        item = reveal_ollama_access_key(session, key_id)
    except AiChatUserConfigError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return AiChatOllamaAccessKeyDetail.model_validate(item)


@router.delete("/ollama-access-keys/{key_id}", response_model=AiChatDeleteSavedConfigResponse)
def delete_ai_chat_ollama_access_key(
    key_id: str,
    current_user: User = Depends(get_current_active_superuser),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    try:
        delete_ollama_access_key(session, key_id)
    except AiChatUserConfigError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return AiChatDeleteSavedConfigResponse()


@router.get("/prompt-cards", response_model=AiChatPromptCardsResponse)
def get_ai_chat_prompt_cards(
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    if current_user is None:
        return AiChatPromptCardsResponse(signed_in=False, selected_id=None, items=[])

    payload = list_user_ai_chat_prompt_cards(session, current_user.id)
    return AiChatPromptCardsResponse(
        signed_in=True,
        selected_id=payload["selected_id"],
        items=[AiChatPromptCard.model_validate(item) for item in payload["items"]],
    )


@router.put("/prompt-cards", response_model=AiChatPromptCardsResponse)
def put_ai_chat_prompt_cards(
    payload: AiChatPromptCardsUpdateRequest,
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    saved = save_user_ai_chat_prompt_cards(
        session,
        current_user.id,
        selected_id=payload.selected_id,
        items=[item.model_dump() for item in payload.items],
    )
    return AiChatPromptCardsResponse(
        signed_in=True,
        selected_id=saved["selected_id"],
        items=[AiChatPromptCard.model_validate(item) for item in saved["items"]],
    )


@router.get("/sessions", response_model=AiChatSessionsResponse)
def get_ai_chat_sessions(
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    if current_user is None:
        return AiChatSessionsResponse(signed_in=False)

    payload = get_user_ai_chat_sessions(session, current_user.id)
    return AiChatSessionsResponse(
        signed_in=True,
        active_session_id=payload["active_session_id"],
        items=[AiChatSessionItem.model_validate(item) for item in payload["items"]],
    )


@router.put("/sessions", response_model=AiChatSessionsResponse)
def put_ai_chat_sessions(
    payload: AiChatSessionsUpdateRequest,
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    saved = save_user_ai_chat_sessions(
        session,
        current_user.id,
        active_session_id=payload.active_session_id,
        items=[item.model_dump() for item in payload.items],
    )
    return AiChatSessionsResponse(
        signed_in=True,
        active_session_id=saved["active_session_id"],
        items=[AiChatSessionItem.model_validate(item) for item in saved["items"]],
    )


@router.post("/custom-providers", response_model=AiChatProviderSummary)
def post_ai_chat_custom_provider(
    payload: AiChatCreateCustomProviderRequest,
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    if payload.kind == "codex_cli" and not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有管理员可以新增 Codex CLI 来源")
    try:
        custom_provider = save_user_ai_chat_custom_provider(
            session,
            current_user.id,
            label=payload.label,
            kind=payload.kind,
            visibility=payload.visibility,
            base_url=payload.base_url,
            default_model=payload.default_model,
            models=payload.models,
        )
    except (AiChatUserConfigError, OllamaClientError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return AiChatProviderSummary.model_validate(custom_provider)


@router.delete("/custom-providers/{provider_id}", response_model=AiChatDeleteSavedConfigResponse)
def delete_ai_chat_custom_provider(
    provider_id: str,
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    try:
        delete_user_ai_chat_custom_provider(session, current_user.id, provider_id)
    except AiChatUserConfigError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AiChatDeleteSavedConfigResponse()


@router.put("/saved-configs/{provider_id}", response_model=AiChatSavedProviderConfig)
def put_ai_chat_saved_config(
    provider_id: str,
    payload: AiChatSaveProviderConfigRequest,
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    try:
        extra_providers = _get_extra_providers(current_user, session)
        provider_config = get_ai_provider(provider_id, extra_providers=extra_providers)
        saved_config = save_user_ai_chat_provider_config(
            session,
            current_user.id,
            provider_id,
            base_url="" if provider_config.kind == "codex_cli" and not provider_config.can_manage else payload.base_url,
            preferred_model=payload.preferred_model,
            preferred_models=payload.preferred_models,
            api_key=payload.api_key,
            api_key_label=payload.api_key_label,
            clear_api_key=payload.clear_api_key,
        )
    except AiChatUserConfigError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return AiChatSavedProviderConfig.model_validate(saved_config)


@router.get("/codex-access-keys", response_model=AiChatCodexAccessKeysResponse)
def get_ai_chat_codex_access_keys(
    current_user: User = Depends(get_current_active_superuser),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    return AiChatCodexAccessKeysResponse(
        items=[
            AiChatCodexAccessKeySummary.model_validate(item)
            for item in list_codex_access_keys(session)
        ],
    )


@router.post("/codex-access-keys", response_model=AiChatCodexAccessKeyDetail)
def post_ai_chat_codex_access_key(
    payload: AiChatCreateCodexAccessKeyRequest,
    current_user: User = Depends(get_current_active_superuser),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    try:
        created = create_codex_access_key(
            session,
            created_by_user_id=current_user.id,
            label=payload.label,
        )
    except AiChatUserConfigError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AiChatCodexAccessKeyDetail.model_validate(created)


@router.get("/codex-access-keys/{key_id}", response_model=AiChatCodexAccessKeyDetail)
def get_ai_chat_codex_access_key_detail(
    key_id: str,
    current_user: User = Depends(get_current_active_superuser),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    try:
        item = reveal_codex_access_key(session, key_id)
    except AiChatUserConfigError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return AiChatCodexAccessKeyDetail.model_validate(item)


@router.delete("/codex-access-keys/{key_id}", response_model=AiChatDeleteSavedConfigResponse)
def delete_ai_chat_codex_access_key(
    key_id: str,
    current_user: User = Depends(get_current_active_superuser),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    try:
        delete_codex_access_key(session, key_id)
    except AiChatUserConfigError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return AiChatDeleteSavedConfigResponse()


@router.post("/saved-configs/{provider_id}/keys/{key_id}/activate", response_model=AiChatSavedProviderConfig)
def post_ai_chat_saved_config_activate_key(
    provider_id: str,
    key_id: str,
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    try:
        saved_config = activate_user_ai_chat_provider_api_key(
            session,
            current_user.id,
            provider_id,
            key_id,
        )
    except AiChatUserConfigError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return AiChatSavedProviderConfig.model_validate(saved_config)


@router.delete("/saved-configs/{provider_id}/keys/{key_id}", response_model=AiChatSavedProviderConfig)
def delete_ai_chat_saved_config_key(
    provider_id: str,
    key_id: str,
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    try:
        saved_config = delete_user_ai_chat_provider_api_key(
            session,
            current_user.id,
            provider_id,
            key_id,
        )
    except AiChatUserConfigError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return AiChatSavedProviderConfig.model_validate(saved_config)


@router.delete("/saved-configs/{provider_id}", response_model=AiChatDeleteSavedConfigResponse)
def delete_ai_chat_saved_config(
    provider_id: str,
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    delete_user_ai_chat_provider_config(session, current_user.id, provider_id)
    return AiChatDeleteSavedConfigResponse()


@router.post("/chat", response_model=AiChatResponse)
def post_ai_chat(
    payload: AiChatRequest,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    messages = _normalize_chat_request(payload)

    try:
        extra_providers = _get_extra_providers(current_user, session)
        resolved_provider, resolved_base_url, resolved_api_key = _resolve_runtime_provider_config(
            provider=payload.provider,
            base_url=payload.base_url,
            api_key=payload.api_key,
            current_user=current_user,
            session=session,
            extra_providers=extra_providers,
        )
        resolved_model = (payload.model or "").strip()
        if not resolved_model and current_user is not None:
            saved_runtime_config = get_user_ai_chat_provider_runtime_config(session, current_user.id, resolved_provider)
            raw_preferred_models = saved_runtime_config.get("preferred_models")
            if isinstance(raw_preferred_models, list):
                resolved_model = next(
                    (item.strip() for item in raw_preferred_models if isinstance(item, str) and item.strip()),
                    "",
                )
            if not resolved_model:
                resolved_model = str(saved_runtime_config.get("preferred_model") or "").strip()
        response = chat_with_provider(
            provider_id=resolved_provider,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            extra_providers=extra_providers,
            messages=messages,
            model=resolved_model or None,
            system_prompt=payload.system_prompt,
            temperature=payload.temperature,
        )
    except (OllamaClientError, AiChatUserConfigError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return AiChatResponse.model_validate(response)


@router.post("/chat-stream")
def post_ai_chat_stream(
    payload: AiChatRequest,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    _ensure_ai_chat_access(current_user)
    messages = _normalize_chat_request(payload)
    try:
        extra_providers = _get_extra_providers(current_user, session)
        resolved_provider, resolved_base_url, resolved_api_key = _resolve_runtime_provider_config(
            provider=payload.provider,
            base_url=payload.base_url,
            api_key=payload.api_key,
            current_user=current_user,
            session=session,
            extra_providers=extra_providers,
        )
        resolved_model = (payload.model or "").strip()
        if not resolved_model and current_user is not None:
            saved_runtime_config = get_user_ai_chat_provider_runtime_config(session, current_user.id, resolved_provider)
            raw_preferred_models = saved_runtime_config.get("preferred_models")
            if isinstance(raw_preferred_models, list):
                resolved_model = next(
                    (item.strip() for item in raw_preferred_models if isinstance(item, str) and item.strip()),
                    "",
                )
            if not resolved_model:
                resolved_model = str(saved_runtime_config.get("preferred_model") or "").strip()
    except AiChatUserConfigError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    def event_generator():
        try:
            for event in stream_chat_with_provider(
                provider_id=resolved_provider,
                base_url=resolved_base_url,
                api_key=resolved_api_key,
                extra_providers=extra_providers,
                messages=messages,
                model=resolved_model or None,
                system_prompt=payload.system_prompt,
                temperature=payload.temperature,
            ):
                yield _encode_stream_event(event)
        except (OllamaClientError, AiChatUserConfigError) as exc:
            yield _encode_stream_event(
                {
                    "type": "error",
                    "detail": str(exc),
                }
            )

    return StreamingResponse(
        event_generator(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
