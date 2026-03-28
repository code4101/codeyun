from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterator

import requests

from backend.core.settings import get_settings


class OllamaClientError(RuntimeError):
    """Raised when an AI provider request cannot be completed."""


@dataclass(frozen=True)
class AiProviderConfig:
    id: str
    label: str
    kind: str
    base_url: str
    default_model: str
    timeout_seconds: float
    api_key: str
    supports_stream: bool
    supports_vision: bool
    requires_api_key: bool
    configured: bool
    models: tuple[str, ...]
    is_custom: bool = False


OLLAMA_MODEL_ALIASES: dict[str, dict[str, Any]] = {
    "qwen3.5:4b-instruct": {
        "runtime_model": "qwen3.5:4b",
        "think": False,
    },
}


def _build_provider_map(
    extra_providers: tuple[AiProviderConfig, ...] = (),
) -> dict[str, AiProviderConfig]:
    settings = get_settings()
    providers = {
        "ollama": AiProviderConfig(
            id="ollama",
            label="Ollama",
            kind="ollama",
            base_url=settings.ollama_base_url.rstrip("/"),
            default_model=settings.ollama_default_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            api_key="",
            supports_stream=True,
            supports_vision=True,
            requires_api_key=False,
            configured=bool(settings.ollama_base_url.strip()),
            models=(),
            is_custom=False,
        ),
        "deepseek": AiProviderConfig(
            id="deepseek",
            label="DeepSeek",
            kind="openai_compatible",
            base_url=settings.deepseek_base_url.rstrip("/"),
            default_model=settings.deepseek_default_model,
            timeout_seconds=settings.deepseek_timeout_seconds,
            api_key=settings.deepseek_api_key,
            supports_stream=True,
            supports_vision=False,
            requires_api_key=True,
            configured=bool(settings.deepseek_base_url.strip() and settings.deepseek_api_key.strip()),
            models=settings.deepseek_models,
            is_custom=False,
        ),
        "302ai": AiProviderConfig(
            id="302ai",
            label="302AI",
            kind="openai_compatible",
            base_url="https://api.302.ai/v1",
            default_model="",
            timeout_seconds=120.0,
            api_key="",
            supports_stream=True,
            supports_vision=False,
            requires_api_key=True,
            configured=False,
            models=(),
            is_custom=False,
        ),
        "aihubmix": AiProviderConfig(
            id="aihubmix",
            label="AIHubMix",
            kind="openai_compatible",
            base_url="https://api.aihubmix.com/v1",
            default_model="",
            timeout_seconds=120.0,
            api_key="",
            supports_stream=True,
            supports_vision=False,
            requires_api_key=True,
            configured=False,
            models=(),
            is_custom=False,
        ),
        "openrouter": AiProviderConfig(
            id="openrouter",
            label="OpenRouter",
            kind="openai_compatible",
            base_url="https://openrouter.ai/api/v1",
            default_model="",
            timeout_seconds=120.0,
            api_key="",
            supports_stream=True,
            supports_vision=False,
            requires_api_key=True,
            configured=False,
            models=(),
            is_custom=False,
        ),
    }
    for provider in extra_providers:
        providers[provider.id] = provider
    return providers


def _apply_provider_overrides(
    provider: AiProviderConfig,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
) -> AiProviderConfig:
    resolved_base_url = provider.base_url if base_url is None else base_url.strip().rstrip("/")
    resolved_api_key = provider.api_key if api_key is None else api_key.strip()
    configured = bool(resolved_base_url) and (not provider.requires_api_key or bool(resolved_api_key))
    return replace(
        provider,
        base_url=resolved_base_url,
        api_key=resolved_api_key,
        configured=configured,
    )


def get_ai_provider(
    provider_id: str | None = None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    extra_providers: tuple[AiProviderConfig, ...] = (),
) -> AiProviderConfig:
    providers = _build_provider_map(extra_providers)
    resolved_id = (provider_id or get_default_ai_provider_id()).strip().lower()
    provider = providers.get(resolved_id)
    if provider is None:
        raise OllamaClientError(f"未知 AI 来源：{resolved_id}")
    return _apply_provider_overrides(provider, base_url=base_url, api_key=api_key)


def get_default_ai_provider_id() -> str:
    settings = get_settings()
    providers = _build_provider_map()
    preferred = settings.ai_default_provider
    if preferred in providers and providers[preferred].configured:
        return preferred

    for provider in providers.values():
        if provider.configured:
            return provider.id

    return preferred if preferred in providers else "ollama"


def _serialize_provider(provider: AiProviderConfig) -> dict[str, Any]:
    models = list(provider.models)
    if provider.default_model and provider.default_model not in models:
        models.insert(0, provider.default_model)

    return {
        "id": provider.id,
        "label": provider.label,
        "kind": provider.kind,
        "configured": provider.configured,
        "base_url": provider.base_url,
        "default_model": provider.default_model,
        "models": models,
        "supports_stream": provider.supports_stream,
        "supports_vision": provider.supports_vision,
        "requires_api_key": provider.requires_api_key,
        "is_custom": provider.is_custom,
    }


def list_ai_provider_summaries(
    *,
    extra_providers: tuple[AiProviderConfig, ...] = (),
) -> list[dict[str, Any]]:
    return [_serialize_provider(provider) for provider in _build_provider_map(extra_providers).values()]


def get_ai_provider_status(
    provider_id: str | None = None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    extra_providers: tuple[AiProviderConfig, ...] = (),
) -> dict[str, Any]:
    provider = get_ai_provider(
        provider_id,
        base_url=base_url,
        api_key=api_key,
        extra_providers=extra_providers,
    )
    payload = _serialize_provider(provider)

    if not provider.configured:
        payload["available"] = False
        payload["error"] = _get_unconfigured_provider_message(provider)
        return payload

    if provider.kind == "ollama":
        try:
            payload["models"] = list_ollama_models(provider)
        except OllamaClientError as exc:
            payload["available"] = False
            payload["error"] = str(exc)
            return payload

    payload["available"] = True
    payload["error"] = None
    return payload


def _get_unconfigured_provider_message(provider: AiProviderConfig) -> str:
    if not provider.base_url.strip():
        return f"{provider.label} 未填写地址"
    if provider.requires_api_key and not provider.api_key.strip():
        return f"{provider.label} 未填写 API Key"
    return f"{provider.label} 未配置"


def _normalize_base64_image(value: str) -> str:
    payload = value.strip()
    if payload.startswith("data:"):
        _, separator, encoded = payload.partition(",")
        payload = encoded if separator else payload
    return "".join(payload.split())


def _normalize_created_at(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return str(value)
    return None


def _build_text_messages(
    messages: list[dict[str, Any]],
    system_prompt: str | None,
) -> list[dict[str, Any]]:
    normalized_messages: list[dict[str, Any]] = []
    if system_prompt and system_prompt.strip():
        normalized_messages.append(
            {
                "role": "system",
                "content": system_prompt.strip(),
            }
        )

    for message in messages:
        normalized_messages.append(
            {
                "role": (message.get("role") or "").strip(),
                "content": message.get("content") or "",
            }
        )

    return normalized_messages


def _build_ollama_messages(
    messages: list[dict[str, Any]],
    system_prompt: str | None,
) -> list[dict[str, Any]]:
    ollama_messages: list[dict[str, Any]] = []

    if system_prompt and system_prompt.strip():
        ollama_messages.append(
            {
                "role": "system",
                "content": system_prompt.strip(),
            }
        )

    for message in messages:
        entry: dict[str, Any] = {
            "role": (message.get("role") or "").strip(),
            "content": message.get("content") or "",
        }
        images = [
            _normalize_base64_image(image)
            for image in message.get("images") or []
            if isinstance(image, str) and image.strip()
        ]
        if images:
            entry["images"] = images
        ollama_messages.append(entry)

    return ollama_messages


def _raise_provider_error(prefix: str, response: requests.Response) -> None:
    detail = None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("error") or payload.get("message")
        if isinstance(detail, dict):
            detail = detail.get("message") or json.dumps(detail, ensure_ascii=False)
    if not detail:
        detail = response.text.strip() or f"HTTP {response.status_code}"
    raise OllamaClientError(f"{prefix}: {detail}")


def list_ollama_models(provider: AiProviderConfig | None = None) -> list[str]:
    target = provider or get_ai_provider("ollama")
    try:
        response = requests.get(
            f"{target.base_url}/api/tags",
            timeout=target.timeout_seconds,
        )
    except requests.RequestException as exc:
        raise OllamaClientError(f"连接 Ollama 失败：{exc}") from exc

    if response.status_code >= 400:
        _raise_provider_error("读取模型列表失败", response)

    try:
        payload = response.json()
    except ValueError as exc:
        raise OllamaClientError("Ollama 返回了无法解析的模型列表") from exc
    if not isinstance(payload, dict):
        raise OllamaClientError("Ollama 返回了异常的模型列表格式")

    names: list[str] = []
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("model") or item.get("name")
        if isinstance(name, str) and name.strip():
            names.append(name.strip())

    return _extend_ollama_model_names(sorted(set(names)))


def _extract_ollama_chat_response(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get("message")
    if not isinstance(message, dict):
        raise OllamaClientError("Ollama 返回结果里缺少 message 字段")

    content = message.get("content")
    if not isinstance(content, str):
        raise OllamaClientError("Ollama 返回结果里缺少文本内容")

    return {
        "model": payload.get("model") or message.get("model") or "",
        "content": content,
        "created_at": _normalize_created_at(payload.get("created_at")),
        "done_reason": payload.get("done_reason"),
        "prompt_eval_count": payload.get("prompt_eval_count"),
        "eval_count": payload.get("eval_count"),
        "total_duration": payload.get("total_duration"),
    }


def _extend_ollama_model_names(names: list[str]) -> list[str]:
    extended = [item.strip() for item in names if isinstance(item, str) and item.strip()]
    for alias, config in OLLAMA_MODEL_ALIASES.items():
        runtime_model = str(config.get("runtime_model") or "").strip()
        if not runtime_model or runtime_model not in extended:
            continue
        if alias in extended:
            continue
        insert_index = extended.index(runtime_model)
        extended.insert(insert_index, alias)
    return extended


def _resolve_ollama_model_request(
    requested_model: str | None,
    default_model: str,
) -> tuple[str, str | None, dict[str, Any]]:
    display_model = (requested_model or "").strip() or None
    runtime_model = display_model or default_model
    extra_payload: dict[str, Any] = {}

    alias_config = OLLAMA_MODEL_ALIASES.get(runtime_model.casefold())
    if alias_config is None:
        return runtime_model, display_model, extra_payload

    runtime_override = str(alias_config.get("runtime_model") or "").strip()
    if runtime_override:
        runtime_model = runtime_override
    if "think" in alias_config:
        extra_payload["think"] = bool(alias_config["think"])
    return runtime_model, display_model, extra_payload


def _chat_with_ollama(
    provider: AiProviderConfig,
    *,
    messages: list[dict[str, Any]],
    model: str | None,
    system_prompt: str | None,
    temperature: float | None,
    response_format: Any = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    runtime_model, display_model, extra_payload = _resolve_ollama_model_request(
        model,
        provider.default_model,
    )
    payload: dict[str, Any] = {
        "model": runtime_model,
        "messages": _build_ollama_messages(messages, system_prompt),
        "stream": False,
    }
    if temperature is not None:
        payload["options"] = {"temperature": temperature}
    if response_format is not None:
        payload["format"] = response_format
    payload.update(extra_payload)

    try:
        response = requests.post(
            f"{provider.base_url}/api/chat",
            json=payload,
            timeout=timeout_seconds or provider.timeout_seconds,
        )
    except requests.RequestException as exc:
        raise OllamaClientError(f"请求 Ollama 失败：{exc}") from exc

    if response.status_code >= 400:
        _raise_provider_error("Ollama 聊天请求失败", response)

    try:
        response_payload = response.json()
    except ValueError as exc:
        raise OllamaClientError("Ollama 返回了无法解析的聊天结果") from exc
    if not isinstance(response_payload, dict):
        raise OllamaClientError("Ollama 返回了异常的聊天结果格式")

    extracted = _extract_ollama_chat_response(response_payload)
    if display_model:
        extracted["model"] = display_model
    return extracted


def _stream_chat_with_ollama(
    provider: AiProviderConfig,
    *,
    messages: list[dict[str, Any]],
    model: str | None,
    system_prompt: str | None,
    temperature: float | None,
) -> Iterator[dict[str, Any]]:
    runtime_model, display_model, extra_payload = _resolve_ollama_model_request(
        model,
        provider.default_model,
    )
    payload: dict[str, Any] = {
        "model": runtime_model,
        "messages": _build_ollama_messages(messages, system_prompt),
        "stream": True,
    }
    if temperature is not None:
        payload["options"] = {"temperature": temperature}
    payload.update(extra_payload)

    try:
        with requests.post(
            f"{provider.base_url}/api/chat",
            json=payload,
            timeout=provider.timeout_seconds,
            stream=True,
        ) as response:
            if response.status_code >= 400:
                _raise_provider_error("Ollama 流式聊天请求失败", response)

            content_parts: list[str] = []
            seen_done = False
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue

                try:
                    item = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise OllamaClientError("Ollama 返回了无法解析的流式结果") from exc

                if not isinstance(item, dict):
                    raise OllamaClientError("Ollama 返回了异常的流式结果格式")

                message = item.get("message")
                delta = ""
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        delta = content

                if delta:
                    content_parts.append(delta)
                    yield {
                        "type": "delta",
                        "delta": delta,
                        "model": display_model or item.get("model") or payload["model"],
                        "created_at": _normalize_created_at(item.get("created_at")),
                    }

                if item.get("done"):
                    seen_done = True
                    final_payload = _extract_ollama_chat_response(item)
                    if display_model:
                        final_payload["model"] = display_model
                    if not final_payload["content"]:
                        final_payload["content"] = "".join(content_parts)
                    yield {
                        "type": "done",
                        **final_payload,
                    }
                    break

            if not seen_done:
                raise OllamaClientError("Ollama 流式响应提前结束")
    except requests.RequestException as exc:
        raise OllamaClientError(f"请求 Ollama 失败：{exc}") from exc


def _build_openai_headers(provider: AiProviderConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }


def _extract_openai_chat_response(payload: dict[str, Any], provider: AiProviderConfig) -> dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OllamaClientError(f"{provider.label} 返回结果里缺少 choices 字段")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise OllamaClientError(f"{provider.label} 返回了异常的 choices 格式")

    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise OllamaClientError(f"{provider.label} 返回结果里缺少 message 字段")

    content = message.get("content")
    if not isinstance(content, str):
        raise OllamaClientError(f"{provider.label} 返回结果里缺少文本内容")

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return {
        "model": payload.get("model") or provider.default_model,
        "content": content,
        "created_at": _normalize_created_at(payload.get("created") or payload.get("created_at")),
        "done_reason": first_choice.get("finish_reason"),
        "prompt_eval_count": usage.get("prompt_tokens"),
        "eval_count": usage.get("completion_tokens"),
        "total_duration": None,
    }


def _chat_with_openai_compatible(
    provider: AiProviderConfig,
    *,
    messages: list[dict[str, Any]],
    model: str | None,
    system_prompt: str | None,
    temperature: float | None,
    response_format: Any = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": (model or provider.default_model).strip() or provider.default_model,
        "messages": _build_text_messages(messages, system_prompt),
        "stream": False,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if response_format is not None:
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        elif isinstance(response_format, dict):
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "schema": response_format,
                },
            }

    try:
        response = requests.post(
            f"{provider.base_url}/chat/completions",
            json=payload,
            headers=_build_openai_headers(provider),
            timeout=timeout_seconds or provider.timeout_seconds,
        )
    except requests.RequestException as exc:
        raise OllamaClientError(f"请求 {provider.label} 失败：{exc}") from exc

    if response.status_code >= 400:
        _raise_provider_error(f"{provider.label} 聊天请求失败", response)

    try:
        response_payload = response.json()
    except ValueError as exc:
        raise OllamaClientError(f"{provider.label} 返回了无法解析的聊天结果") from exc
    if not isinstance(response_payload, dict):
        raise OllamaClientError(f"{provider.label} 返回了异常的聊天结果格式")

    return _extract_openai_chat_response(response_payload, provider)


def _stream_chat_with_openai_compatible(
    provider: AiProviderConfig,
    *,
    messages: list[dict[str, Any]],
    model: str | None,
    system_prompt: str | None,
    temperature: float | None,
) -> Iterator[dict[str, Any]]:
    payload: dict[str, Any] = {
        "model": (model or provider.default_model).strip() or provider.default_model,
        "messages": _build_text_messages(messages, system_prompt),
        "stream": True,
    }
    if temperature is not None:
        payload["temperature"] = temperature

    try:
        with requests.post(
            f"{provider.base_url}/chat/completions",
            json=payload,
            headers=_build_openai_headers(provider),
            timeout=provider.timeout_seconds,
            stream=True,
        ) as response:
            if response.status_code >= 400:
                _raise_provider_error(f"{provider.label} 流式聊天请求失败", response)

            content_parts: list[str] = []
            model_name = payload["model"]
            created_at: str | None = None
            done_reason: str | None = None
            usage: dict[str, Any] = {}

            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue

                line = raw_line.strip()
                if line.startswith("data:"):
                    line = line[5:].strip()

                if line == "[DONE]":
                    break

                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise OllamaClientError(f"{provider.label} 返回了无法解析的流式结果") from exc

                if not isinstance(item, dict):
                    raise OllamaClientError(f"{provider.label} 返回了异常的流式结果格式")

                if item.get("error"):
                    detail = item["error"]
                    if isinstance(detail, dict):
                        detail = detail.get("message") or json.dumps(detail, ensure_ascii=False)
                    raise OllamaClientError(f"{provider.label} 流式聊天请求失败: {detail}")

                model_name = item.get("model") or model_name
                created_at = _normalize_created_at(item.get("created") or item.get("created_at")) or created_at
                usage = item.get("usage") if isinstance(item.get("usage"), dict) else usage

                choices = item.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue

                choice = choices[0]
                if not isinstance(choice, dict):
                    continue

                delta_payload = choice.get("delta")
                delta = ""
                if isinstance(delta_payload, dict):
                    content = delta_payload.get("content")
                    if isinstance(content, str):
                        delta = content

                if delta:
                    content_parts.append(delta)
                    yield {
                        "type": "delta",
                        "delta": delta,
                        "model": model_name,
                        "created_at": created_at,
                    }

                if choice.get("finish_reason") is not None:
                    done_reason = choice.get("finish_reason")

            yield {
                "type": "done",
                "model": model_name,
                "content": "".join(content_parts),
                "created_at": created_at,
                "done_reason": done_reason,
                "prompt_eval_count": usage.get("prompt_tokens"),
                "eval_count": usage.get("completion_tokens"),
                "total_duration": None,
            }
    except requests.RequestException as exc:
        raise OllamaClientError(f"请求 {provider.label} 失败：{exc}") from exc


def _ensure_provider_can_handle_messages(
    provider: AiProviderConfig,
    messages: list[dict[str, Any]],
) -> None:
    if provider.supports_vision:
        return

    for message in messages:
        if message.get("images"):
            raise OllamaClientError(f"{provider.label} 当前版本不支持图片输入")


def chat_with_provider(
    *,
    provider_id: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    messages: list[dict[str, Any]],
    model: str | None = None,
    system_prompt: str | None = None,
    temperature: float | None = None,
    response_format: Any = None,
    timeout_seconds: float | None = None,
    extra_providers: tuple[AiProviderConfig, ...] = (),
) -> dict[str, Any]:
    provider = get_ai_provider(
        provider_id,
        base_url=base_url,
        api_key=api_key,
        extra_providers=extra_providers,
    )
    if not provider.configured:
        raise OllamaClientError(_get_unconfigured_provider_message(provider))

    _ensure_provider_can_handle_messages(provider, messages)

    if provider.kind == "ollama":
        return _chat_with_ollama(
            provider,
            messages=messages,
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            response_format=response_format,
            timeout_seconds=timeout_seconds,
        )

    if provider.kind == "openai_compatible":
        return _chat_with_openai_compatible(
            provider,
            messages=messages,
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            response_format=response_format,
            timeout_seconds=timeout_seconds,
        )

    raise OllamaClientError(f"未实现的 AI 来源类型：{provider.kind}")


def stream_chat_with_provider(
    *,
    provider_id: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    messages: list[dict[str, Any]],
    model: str | None = None,
    system_prompt: str | None = None,
    temperature: float | None = None,
    extra_providers: tuple[AiProviderConfig, ...] = (),
) -> Iterator[dict[str, Any]]:
    provider = get_ai_provider(
        provider_id,
        base_url=base_url,
        api_key=api_key,
        extra_providers=extra_providers,
    )
    if not provider.configured:
        raise OllamaClientError(_get_unconfigured_provider_message(provider))

    _ensure_provider_can_handle_messages(provider, messages)

    if provider.kind == "ollama":
        yield from _stream_chat_with_ollama(
            provider,
            messages=messages,
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
        )
        return

    if provider.kind == "openai_compatible":
        yield from _stream_chat_with_openai_compatible(
            provider,
            messages=messages,
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
        )
        return

    raise OllamaClientError(f"未实现的 AI 来源类型：{provider.kind}")
