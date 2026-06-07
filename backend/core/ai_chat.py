from __future__ import annotations

import base64
import binascii
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import requests

from backend.core.settings import ROOT_DIR, get_settings


class OllamaClientError(RuntimeError):
    """Raised when an AI provider request cannot be completed."""


CODEX_CLI_DEFAULT_COMMAND = "codex"
CODEX_CLI_DEFAULT_MODEL = "gpt-5.3-codex-spark"
CODEX_CLI_MODELS = (
    CODEX_CLI_DEFAULT_MODEL,
    "gpt-5.5",
)
CODEX_CLI_WORKSPACE_DIRNAME = "codex-cli-workspace"
CODEX_CLI_IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}


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
    sharing_mode: str = "builtin"
    can_manage: bool = False
    workspace_dir: str = ""
    session_id: str = ""


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
        "codex-cli": AiProviderConfig(
            id="codex-cli",
            label="Codex CLI",
            kind="codex_cli",
            base_url=CODEX_CLI_DEFAULT_COMMAND,
            default_model=CODEX_CLI_DEFAULT_MODEL,
            timeout_seconds=900.0,
            api_key="",
            supports_stream=False,
            supports_vision=True,
            requires_api_key=False,
            configured=bool(shutil.which(CODEX_CLI_DEFAULT_COMMAND)),
            models=CODEX_CLI_MODELS,
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
    resolved_base_url = provider.base_url if base_url is None else (
        base_url.strip() if provider.kind == "codex_cli" else base_url.strip().rstrip("/")
    )
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
        "sharing_mode": provider.sharing_mode,
        "can_manage": provider.can_manage,
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
    elif provider.kind == "codex_cli":
        try:
            _probe_codex_cli(provider)
        except OllamaClientError as exc:
            payload["available"] = False
            payload["error"] = str(exc)
            return payload

    payload["available"] = True
    payload["error"] = None
    return payload


def _get_unconfigured_provider_message(provider: AiProviderConfig) -> str:
    if not provider.base_url.strip():
        if provider.kind == "codex_cli":
            return f"{provider.label} 未填写命令"
        return f"{provider.label} 未填写地址"
    if provider.requires_api_key and not provider.api_key.strip():
        if provider.kind == "codex_cli":
            return f"{provider.label} 未填写访问 Token"
        return f"{provider.label} 未填写 API Key"
    return f"{provider.label} 未配置"


def _split_command_line(command_line: str) -> list[str]:
    payload = command_line.strip()
    if not payload:
        return []
    try:
        return shlex.split(payload, posix=os.name != "nt")
    except ValueError as exc:
        raise OllamaClientError(f"命令格式无效：{exc}") from exc


def _codex_tools_node_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "tools" / "node"


def _get_preferred_codex_command_candidates() -> list[Path]:
    return [
        _codex_tools_node_dir() / "codex.cmd",
        Path.home() / "AppData" / "Local" / "OpenAI" / "Codex" / "bin" / "codex.cmd",
        Path.home() / ".cargo" / "bin" / "codex.cmd",
    ]


def _is_usable_preferred_codex_command(candidate: Path) -> bool:
    if not candidate.exists():
        return False

    tools_node_dir = _codex_tools_node_dir()
    try:
        is_repo_tools_node_command = candidate.parent.resolve() == tools_node_dir.resolve()
    except OSError:
        is_repo_tools_node_command = candidate.parent == tools_node_dir

    if is_repo_tools_node_command:
        package_script = tools_node_dir / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        return package_script.exists()

    return True


def _resolve_command_path(command: list[str]) -> list[str]:
    if not command or os.name != "nt":
        return command

    executable = command[0].strip()
    if not executable:
        return command

    if os.path.isabs(executable) and Path(executable).exists():
        return [executable, *command[1:]]

    executable_name = Path(executable).name.lower()
    if executable_name in {"codex", "codex.cmd", "codex.exe", "codex.ps1"}:
        for candidate in _get_preferred_codex_command_candidates():
            if _is_usable_preferred_codex_command(candidate):
                return [os.fspath(candidate), *command[1:]]

    # On Windows, `subprocess.run(["codex", ...])` may hit an extensionless shim
    # before the real `.cmd/.exe` launcher. Resolve once up front to the concrete
    # runnable path that `shutil.which()` selects.
    resolved_executable = shutil.which(executable)
    if resolved_executable:
        return [resolved_executable, *command[1:]]
    return command


def _build_codex_workspace_dir(workspace_dir: str | None = None) -> Path:
    raw_workspace_dir = (workspace_dir or "").strip()
    if raw_workspace_dir:
        path = Path(raw_workspace_dir).expanduser()
        if not path.is_absolute():
            path = (ROOT_DIR / path).resolve()
        if not path.exists():
            raise OllamaClientError(f"Codex CLI 工作目录不存在：{path}")
        if not path.is_dir():
            raise OllamaClientError(f"Codex CLI 工作目录不是目录：{path}")
        return path

    path = get_settings().data_dir / CODEX_CLI_WORKSPACE_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _summarize_process_output(*segments: str) -> str:
    lines: list[str] = []
    for segment in segments:
        for line in segment.splitlines():
            normalized = line.strip()
            if normalized:
                lines.append(normalized)
    if not lines:
        return "没有可用输出"
    for line in reversed(lines):
        if line.startswith("Node.js v") and len(lines) > 1:
            continue
        return line
    return lines[-1]


def _extract_codex_cli_session_id(*segments: str) -> str:
    for segment in segments:
        for line in segment.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            for key in ("thread_id", "session_id"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return ""


def _probe_codex_cli(provider: AiProviderConfig) -> None:
    command = _resolve_command_path(_split_command_line(provider.base_url))
    if not command:
        raise OllamaClientError("Codex CLI 未填写命令")

    try:
        completed = subprocess.run(
            [*command, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=min(15.0, max(1.0, provider.timeout_seconds)),
            check=False,
        )
    except FileNotFoundError as exc:
        raise OllamaClientError(f"未找到 Codex CLI 命令：{command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise OllamaClientError("检测 Codex CLI 超时") from exc
    except OSError as exc:
        raise OllamaClientError(f"启动 Codex CLI 失败：{exc}") from exc

    if completed.returncode != 0:
        detail = _summarize_process_output(completed.stderr, completed.stdout)
        raise OllamaClientError(f"检测 Codex CLI 失败：{detail}")


def _build_codex_cli_prompt(
    *,
    messages: list[dict[str, Any]],
    system_prompt: str | None,
    response_format: Any,
) -> str:
    sections = [
        "你正在通过 CodeYun 调用本机 Codex CLI。",
        "当前工作目录由 CodeYun 通过 --cd 指定；请按用户消息直接处理。",
    ]

    if response_format == "json":
        sections.append("- 最终只输出一个 JSON 对象，不要使用 Markdown 代码块。")
    elif isinstance(response_format, dict):
        sections.append("- 最终只输出一个满足下方 JSON Schema 的 JSON 对象，不要使用 Markdown 代码块。")
        sections.append("")
        sections.append("JSON Schema:")
        sections.append(json.dumps(response_format, ensure_ascii=False, indent=2))

    if system_prompt and system_prompt.strip():
        sections.extend([
            "",
            "系统提示：",
            system_prompt.strip(),
        ])

    sections.extend([
        "",
        "对话历史：",
    ])

    for message in messages:
        role = str(message.get("role") or "").strip().lower() or "user"
        content = str(message.get("content") or "")
        image_count = len(message.get("images") or [])
        if image_count:
            content = (
                f"{content.rstrip()}\n\n"
                f"[本条消息附带 {image_count} 张图片，已随 Codex CLI 调用作为 --image 附件传入。]"
            ).strip()
        sections.extend([
            "",
            f"[{role}]",
            content,
        ])

    sections.extend([
        "",
        "请直接给出最终回答。",
    ])
    return "\n".join(sections).strip()


def _parse_image_payload(value: str, index: int) -> tuple[bytes, str]:
    payload = value.strip()
    mime_type = "image/png"
    if payload.startswith("data:"):
        header, separator, encoded = payload.partition(",")
        if separator:
            media_type = header[5:].split(";", 1)[0].strip().lower()
            if media_type.startswith("image/"):
                mime_type = media_type
            payload = encoded

    try:
        image_bytes = base64.b64decode("".join(payload.split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise OllamaClientError(f"第 {index} 张图片不是有效的 base64 数据") from exc
    if not image_bytes:
        raise OllamaClientError(f"第 {index} 张图片内容为空")
    return image_bytes, mime_type


def _write_codex_cli_image_files(messages: list[dict[str, Any]], temp_dir: Path) -> list[Path]:
    image_paths: list[Path] = []
    for message in messages:
        for image in message.get("images") or []:
            if not isinstance(image, str) or not image.strip():
                continue
            image_bytes, mime_type = _parse_image_payload(image, len(image_paths) + 1)
            suffix = CODEX_CLI_IMAGE_EXTENSIONS.get(mime_type, ".png")
            image_path = temp_dir / f"codex-image-{len(image_paths) + 1}{suffix}"
            image_path.write_bytes(image_bytes)
            image_paths.append(image_path)
    return image_paths


def _chat_with_codex_cli(
    provider: AiProviderConfig,
    *,
    messages: list[dict[str, Any]],
    model: str | None,
    system_prompt: str | None,
    response_format: Any = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    command = _resolve_command_path(_split_command_line(provider.base_url))
    if not command:
        raise OllamaClientError("Codex CLI 未填写命令")

    workspace_dir = _build_codex_workspace_dir(provider.workspace_dir)
    session_id = provider.session_id.strip()
    prompt = _build_codex_cli_prompt(
        messages=messages,
        system_prompt=system_prompt,
        response_format=response_format,
    )
    resolved_model = (model or provider.default_model).strip() or provider.default_model

    with tempfile.TemporaryDirectory(prefix="codeyun-codex-cli-") as temp_dir:
        temp_path = Path(temp_dir)
        output_path = temp_path / "last-message.txt"
        image_paths = _write_codex_cli_image_files(messages, temp_path)
        command_args = [
            *command,
            "exec",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--color",
            "never",
            "--json",
            "--cd",
            os.fspath(workspace_dir),
            "--output-last-message",
            os.fspath(output_path),
        ]
        if resolved_model:
            command_args.extend(["--model", resolved_model])
        for image_path in image_paths:
            command_args.extend(["--image", os.fspath(image_path)])
        if session_id:
            command_args.extend(["resume", session_id])
        command_args.append("-")

        try:
            completed = subprocess.run(
                command_args,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=os.fspath(workspace_dir),
                timeout=timeout_seconds or provider.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise OllamaClientError(f"未找到 Codex CLI 命令：{command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise OllamaClientError(f"Codex CLI 响应超时（>{timeout_seconds or provider.timeout_seconds:.0f}s）") from exc
        except OSError as exc:
            raise OllamaClientError(f"启动 Codex CLI 失败：{exc}") from exc

        content = ""
        if output_path.exists():
            content = output_path.read_text(encoding="utf-8").strip()
        returned_session_id = _extract_codex_cli_session_id(completed.stdout)

    if completed.returncode != 0:
        detail = _summarize_process_output(completed.stderr, completed.stdout, content)
        raise OllamaClientError(f"Codex CLI 调用失败：{detail}")
    if not content:
        detail = _summarize_process_output(completed.stderr, completed.stdout)
        raise OllamaClientError(f"Codex CLI 没有返回有效内容：{detail}")

    return {
        "model": resolved_model,
        "content": content,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "done_reason": "stop",
        "session_id": returned_session_id or session_id or None,
        "prompt_eval_count": None,
        "eval_count": None,
        "total_duration": None,
    }


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


def _iter_sse_data_lines(response: requests.Response) -> Iterator[str]:
    data_lines: list[bytes] = []

    for raw_line in response.iter_lines(decode_unicode=False):
        if raw_line is None:
            continue

        line_bytes = raw_line.encode("utf-8") if isinstance(raw_line, str) else raw_line
        line = line_bytes.rstrip(b"\r")
        if line == b"":
            if data_lines:
                yield b"\n".join(data_lines).decode("utf-8")
                data_lines = []
            continue

        if line.startswith(b":"):
            continue

        field, separator, value = line.partition(b":")
        if separator:
            if value.startswith(b" "):
                value = value[1:]
        else:
            value = b""

        if field == b"data":
            data_lines.append(value)

    if data_lines:
        yield b"\n".join(data_lines).decode("utf-8")


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


def _build_ollama_chat_payload(
    *,
    messages: list[dict[str, Any]],
    model: str | None,
    system_prompt: str | None,
    temperature: float | None,
    response_format: Any,
    default_model: str,
    stream: bool,
) -> tuple[dict[str, Any], str | None]:
    runtime_model, display_model, extra_payload = _resolve_ollama_model_request(
        model,
        default_model,
    )
    payload: dict[str, Any] = {
        "model": runtime_model,
        "messages": _build_ollama_messages(messages, system_prompt),
        "stream": stream,
    }
    if temperature is not None:
        payload["options"] = {"temperature": temperature}
    if response_format is not None:
        payload["format"] = response_format
    payload.update(extra_payload)
    return payload, display_model


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
    if response_format is not None:
        return _collect_chat_with_ollama_stream_response(
            provider,
            messages=messages,
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            response_format=response_format,
            timeout_seconds=timeout_seconds,
        )

    payload, display_model = _build_ollama_chat_payload(
        messages=messages,
        model=model,
        system_prompt=system_prompt,
        temperature=temperature,
        response_format=response_format,
        default_model=provider.default_model,
        stream=False,
    )

    try:
        response = requests.post(
            f"{provider.base_url}/api/chat",
            json=payload,
            timeout=timeout_seconds or provider.timeout_seconds,
        )
    except requests.ReadTimeout:
        return _collect_chat_with_ollama_stream_response(
            provider,
            messages=messages,
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            response_format=response_format,
            timeout_seconds=timeout_seconds,
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
    response_format: Any = None,
    timeout_seconds: float | None = None,
) -> Iterator[dict[str, Any]]:
    payload, display_model = _build_ollama_chat_payload(
        messages=messages,
        model=model,
        system_prompt=system_prompt,
        temperature=temperature,
        response_format=response_format,
        default_model=provider.default_model,
        stream=True,
    )

    try:
        with requests.post(
            f"{provider.base_url}/api/chat",
            json=payload,
            timeout=timeout_seconds or provider.timeout_seconds,
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


def _collect_chat_with_ollama_stream_response(
    provider: AiProviderConfig,
    *,
    messages: list[dict[str, Any]],
    model: str | None,
    system_prompt: str | None,
    temperature: float | None,
    response_format: Any = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    final_payload: dict[str, Any] | None = None
    for event in _stream_chat_with_ollama(
        provider,
        messages=messages,
        model=model,
        system_prompt=system_prompt,
        temperature=temperature,
        response_format=response_format,
        timeout_seconds=timeout_seconds,
    ):
        if event.get("type") == "done":
            final_payload = dict(event)
            final_payload.pop("type", None)

    if final_payload is None:
        raise OllamaClientError("Ollama 流式聊天没有返回完成结果")
    return final_payload


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
    except UnicodeDecodeError as exc:
        raise OllamaClientError(f"{provider.label} 返回了无法解码的流式结果") from exc

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
    response_format: Any = None,
    timeout_seconds: float | None = None,
) -> Iterator[dict[str, Any]]:
    payload: dict[str, Any] = {
        "model": (model or provider.default_model).strip() or provider.default_model,
        "messages": _build_text_messages(messages, system_prompt),
        "stream": True,
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
        with requests.post(
            f"{provider.base_url}/chat/completions",
            json=payload,
            headers=_build_openai_headers(provider),
            timeout=timeout_seconds or provider.timeout_seconds,
            stream=True,
        ) as response:
            if response.status_code >= 400:
                _raise_provider_error(f"{provider.label} 流式聊天请求失败", response)

            content_parts: list[str] = []
            model_name = payload["model"]
            created_at: str | None = None
            done_reason: str | None = None
            usage: dict[str, Any] = {}

            for line in _iter_sse_data_lines(response):
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

    if provider.kind == "codex_cli":
        return _chat_with_codex_cli(
            provider,
            messages=messages,
            model=model,
            system_prompt=system_prompt,
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
    response_format: Any = None,
    timeout_seconds: float | None = None,
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
            response_format=response_format,
            timeout_seconds=timeout_seconds,
        )
        return

    if provider.kind == "openai_compatible":
        yield from _stream_chat_with_openai_compatible(
            provider,
            messages=messages,
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            response_format=response_format,
            timeout_seconds=timeout_seconds,
        )
        return

    if provider.kind == "codex_cli":
        yield {
            "type": "done",
            **_chat_with_codex_cli(
                provider,
                messages=messages,
                model=model,
                system_prompt=system_prompt,
                response_format=response_format,
                timeout_seconds=timeout_seconds,
            ),
        }
        return

    raise OllamaClientError(f"未实现的 AI 来源类型：{provider.kind}")
