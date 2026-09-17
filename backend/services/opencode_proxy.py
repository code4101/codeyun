"""Standalone Responses-API proxy for opencode Go.

Codex only speaks the OpenAI Responses API, but opencode Go serves three different
upstream surfaces (Responses, Chat Completions, Anthropic Messages).  This small
service listens on localhost, accepts ``POST /v1/responses`` and routes each model
to the surface that supports it, translating requests and (streaming) responses.

It is deliberately decoupled from the main CodeYun backend: run it as its own
process, e.g. ``python -m backend.services.opencode_proxy``.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from typing import Any, Iterator

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from backend.core.opencode_usage import read_opencode_go_key


OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
SESSION_HEADER = "x-opencode-session"
SESSION_VALUE = "codeyun-opencode-proxy"
UPSTREAM_TIMEOUT_SECONDS = 600.0

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}

# opencode Go endpoint per family (see https://opencode.ai/docs/go/). DeepSeek is
# served over Chat Completions, not Responses.
_RESPONSES_PREFIXES = ("gpt-", "grok-", "muse-")
_MESSAGES_PREFIXES = ("qwen", "minimax", "claude")

app = FastAPI(title="opencode-go Responses proxy", docs_url=None, redoc_url=None)


class ProxyError(RuntimeError):
    pass


def classify_model(model: str) -> str:
    normalized = str(model or "").strip().lower()
    if normalized.startswith(_MESSAGES_PREFIXES):
        return "messages"
    if normalized.startswith(_RESPONSES_PREFIXES):
        return "responses"
    return "chat"


def _upstream_headers(surface: str) -> dict[str, str]:
    key = read_opencode_go_key() or os.environ.get("OPENCODE_GO_API_KEY", "").strip()
    if not key:
        raise ProxyError("未找到 opencode-go 凭证")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "codeyun-opencode-proxy/1.0",
        SESSION_HEADER: SESSION_VALUE,
    }
    if surface == "messages":
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _local_only(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in _LOCAL_HOSTS


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") in {"input_text", "output_text", "text"}:
                    parts.append(str(item.get("text") or ""))
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part for part in parts if part)
    return ""


_ROLE_ALIASES = {"developer": "system", "latest_reminder": "user"}


def _chat_role(role: Any) -> str:
    normalized = str(role or "user").strip().lower()
    if normalized in _ROLE_ALIASES:
        return _ROLE_ALIASES[normalized]
    return normalized if normalized in {"system", "user", "assistant", "tool"} else "user"


def _image_url_of(part: dict[str, Any]) -> str:
    url = part.get("image_url")
    if isinstance(url, dict):
        url = url.get("url")
    if not url:
        url = part.get("url") or part.get("file_url")
    return str(url or "")


def _chat_content(content: Any) -> Any:
    """Convert Responses content (text/image parts) into Chat Completions content."""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[dict[str, Any]] = []
    has_image = False
    for item in content:
        if isinstance(item, str):
            parts.append({"type": "text", "text": item})
            continue
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "input_image" or "image_url" in item:
            url = _image_url_of(item)
            if url:
                parts.append({"type": "image_url", "image_url": {"url": url}})
                has_image = True
        elif kind in {"input_text", "output_text", "text", None}:
            text = str(item.get("text") or item.get("content") or "")
            if text:
                parts.append({"type": "text", "text": text})
    if has_image:
        return parts
    return "".join(part["text"] for part in parts if part.get("type") == "text")


def _tool_content(output: Any) -> Any:
    """Convert a Responses ``function_call_output`` into Chat Completions content.

    ``view_image`` returns a list of content parts whose ``input_image`` holds a
    multi-MB base64 data URL.  ``json.dumps``-ing that list turns the image into
    millions of text tokens and overruns the model context, so images are
    forwarded as ``image_url`` parts and text parts collapse to a string.
    """

    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return _chat_content(output)
    return json.dumps(output, ensure_ascii=False)


def _reasoning_text(item: dict[str, Any]) -> str:
    """Collect a Responses ``reasoning`` item's text for ``reasoning_content``."""

    parts: list[str] = []
    for key in ("summary", "content"):
        for block in item.get(key) or []:
            if isinstance(block, dict):
                text = block.get("text")
                if text:
                    parts.append(str(text))
            elif isinstance(block, str):
                parts.append(block)
    return "".join(parts)


def responses_to_chat(body: dict[str, Any], *, stream: bool = False) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if body.get("instructions"):
        messages.append({"role": "system", "content": str(body["instructions"])})

    # Chat Completions requires every assistant ``tool_calls`` message to be
    # followed *immediately* by a ``tool`` message per call.  Codex emits one
    # Responses ``function_call`` item per call (plus a sibling assistant text
    # item), so consecutive calls must be folded into a single assistant message
    # or a parallel-tool turn is rejected upstream as "insufficient tool messages
    # following tool_calls message".  DeepSeek additionally requires the previous
    # assistant turn's ``reasoning_content`` to be passed back once a thread has
    # used thinking with tools, so the leading ``reasoning`` item is folded into
    # the same assistant message.
    pending_content: str | list[dict[str, Any]] | None = None
    pending_tool_calls: list[dict[str, Any]] = []
    pending_reasoning = ""

    def flush_assistant() -> None:
        nonlocal pending_content, pending_tool_calls, pending_reasoning
        if pending_content is None and not pending_tool_calls:
            return
        message: dict[str, Any] = {"role": "assistant", "content": pending_content}
        if pending_reasoning:
            message["reasoning_content"] = pending_reasoning
        if pending_tool_calls:
            message["tool_calls"] = pending_tool_calls
        messages.append(message)
        pending_content = None
        pending_tool_calls = []
        pending_reasoning = ""

    raw_input = body.get("input")
    if isinstance(raw_input, str):
        messages.append({"role": "user", "content": raw_input})
    elif isinstance(raw_input, list):
        for item in raw_input:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind in {None, "message"}:
                role = _chat_role(item.get("role"))
                if role == "assistant":
                    flush_assistant()
                    pending_content = _chat_content(item.get("content"))
                else:
                    flush_assistant()
                    messages.append({"role": role, "content": _chat_content(item.get("content"))})
            elif kind == "reasoning":
                text = _reasoning_text(item)
                if text:
                    pending_reasoning = f"{pending_reasoning}{text}"
            elif kind == "function_call":
                pending_tool_calls.append({
                    "id": item.get("call_id") or item.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": item.get("name") or "",
                        "arguments": item.get("arguments") or "{}",
                    },
                })
            elif kind == "function_call_output":
                flush_assistant()
                output = item.get("output")
                messages.append({
                    "role": "tool",
                    "tool_call_id": item.get("call_id") or "",
                    "content": _tool_content(output),
                })
    flush_assistant()

    payload: dict[str, Any] = {"model": body.get("model"), "messages": messages}
    if body.get("max_output_tokens"):
        payload["max_tokens"] = body["max_output_tokens"]
    if stream:
        # Without this the upstream stream carries no usage, and Codex records a
        # turn with a null token count (no context-window gauge either).
        payload["stream_options"] = {"include_usage": True}
    if body.get("temperature") is not None:
        payload["temperature"] = body["temperature"]
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        payload["reasoning_effort"] = reasoning["effort"]
    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": item.get("name"),
                    "description": item.get("description") or "",
                    "parameters": item.get("parameters") or {"type": "object", "properties": {}},
                },
            }
            for item in tools
            if isinstance(item, dict) and item.get("type") == "function"
        ]
        if body.get("tool_choice"):
            payload["tool_choice"] = body["tool_choice"]
    return payload


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def chat_to_responses(chat: dict[str, Any], model: str) -> dict[str, Any]:
    choice = (chat.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    output: list[dict[str, Any]] = []
    if message.get("reasoning_content"):
        output.append({
            "type": "reasoning",
            "id": _new_id("rs"),
            "summary": [{"type": "summary_text", "text": message["reasoning_content"]}],
        })
    if message.get("content"):
        output.append({
            "type": "message",
            "id": _new_id("msg"),
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": message["content"], "annotations": []}],
        })
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        output.append({
            "type": "function_call",
            "id": _new_id("fc"),
            "call_id": call.get("id") or _new_id("call"),
            "name": function.get("name") or "",
            "arguments": function.get("arguments") or "{}",
            "status": "completed",
        })
    usage = chat.get("usage") or {}
    return {
        "id": chat.get("id") or _new_id("resp"),
        "object": "response",
        "created_at": chat.get("created") or int(time.time()),
        "status": "completed",
        "model": model,
        "output": output,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    }


def _data_url_image(url: str) -> dict[str, Any] | None:
    if not url.startswith("data:"):
        return None
    header, _, data = url.partition(",")
    media_type = header[5:].split(";", 1)[0] or "image/png"
    if not data:
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def _messages_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    blocks: list[dict[str, Any]] = []
    has_image = False
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "input_image" or "image_url" in item:
            url = _image_url_of(item)
            image = _data_url_image(url) if url else None
            if image is None and url:
                image = {"type": "image", "source": {"type": "url", "url": url}}
            if image:
                blocks.append(image)
                has_image = True
        elif kind in {"input_text", "output_text", "text", None}:
            text = str(item.get("text") or item.get("content") or "")
            if text:
                blocks.append({"type": "text", "text": text})
    if has_image:
        return blocks
    return "".join(block["text"] for block in blocks if block.get("type") == "text")


def _messages_tool_content(output: Any) -> Any:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return _messages_content(output)
    return json.dumps(output, ensure_ascii=False)


def responses_to_messages(body: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    system_extra: list[str] = []
    # Anthropic pairs one assistant message's ``tool_use`` blocks with the
    # ``tool_result`` blocks of the *next* user message.  Codex splits a
    # parallel-tool turn into separate Responses items, so fold consecutive
    # calls (and their outputs) back into single assistant/user messages.
    pending_assistant: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def flush_assistant() -> None:
        nonlocal pending_assistant
        if pending_assistant:
            messages.append({"role": "assistant", "content": pending_assistant})
            pending_assistant = []

    def flush_tool_results() -> None:
        nonlocal pending_tool_results
        if pending_tool_results:
            messages.append({"role": "user", "content": pending_tool_results})
            pending_tool_results = []

    def append_assistant(block: dict[str, Any]) -> None:
        flush_tool_results()
        pending_assistant.append(block)

    def append_user(blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        flush_assistant()
        pending_tool_results.extend(blocks)
        flush_tool_results()

    raw_input = body.get("input")
    if isinstance(raw_input, str):
        append_user([{"type": "text", "text": raw_input}])
    elif isinstance(raw_input, list):
        for item in raw_input:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind in {None, "message"}:
                role_raw = str(item.get("role") or "user").strip().lower()
                if role_raw == "developer":
                    text = _content_text(item.get("content"))
                    if text:
                        system_extra.append(text)
                    continue
                content = _messages_content(item.get("content"))
                if isinstance(content, list):
                    blocks = content
                elif content:
                    blocks = [{"type": "text", "text": content}]
                else:
                    blocks = []
                if role_raw == "assistant":
                    for block in blocks:
                        append_assistant(block)
                else:
                    append_user(blocks)
            elif kind == "function_call":
                append_assistant({
                    "type": "tool_use",
                    "id": item.get("call_id") or item.get("id") or _new_id("call"),
                    "name": item.get("name") or "",
                    "input": _safe_json(item.get("arguments")),
                })
            elif kind == "function_call_output":
                flush_assistant()
                output = item.get("output")
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": item.get("call_id") or "",
                    "content": _messages_tool_content(output),
                })
    flush_assistant()
    flush_tool_results()

    payload: dict[str, Any] = {
        "model": body.get("model"),
        "max_tokens": body.get("max_output_tokens") or 4096,
        "messages": messages,
    }
    system_parts = [str(body["instructions"])] if body.get("instructions") else []
    system_parts.extend(system_extra)
    if system_parts:
        payload["system"] = "\n\n".join(part for part in system_parts if part)
    if body.get("temperature") is not None:
        payload["temperature"] = body["temperature"]
    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        payload["tools"] = [
            {
                "name": item.get("name"),
                "description": item.get("description") or "",
                "input_schema": item.get("parameters") or {"type": "object", "properties": {}},
            }
            for item in tools
            if isinstance(item, dict) and item.get("type") == "function"
        ]
    return payload


def _safe_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


def messages_to_responses(message: dict[str, Any], model: str) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for block in message.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
        elif block.get("type") == "thinking":
            output.append({
                "type": "reasoning",
                "id": _new_id("rs"),
                "summary": [{"type": "summary_text", "text": str(block.get("thinking") or "")}],
            })
        elif block.get("type") == "tool_use":
            output.append({
                "type": "function_call",
                "id": _new_id("fc"),
                "call_id": block.get("id") or _new_id("call"),
                "name": block.get("name") or "",
                "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                "status": "completed",
            })
    if text_parts:
        output.insert(0, {
            "type": "message",
            "id": _new_id("msg"),
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "".join(text_parts), "annotations": []}],
        })
    usage = message.get("usage") or {}
    return {
        "id": message.get("id") or _new_id("resp"),
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": output,
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)),
        },
    }


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream_responses_passthrough(response: requests.Response, model: str) -> Iterator[bytes]:
    # Yield raw bytes: the upstream SSE is UTF-8, and decoding here (requests
    # defaults to ISO-8859-1 when the Content-Type omits a charset) would
    # double-encode non-ASCII text.
    for chunk in response.iter_content(chunk_size=8192):
        if chunk:
            yield chunk


class _ChatStreamItem:
    """One ``response.output`` item assembled from Chat Completions deltas.

    Chat Completions streams a single assistant turn as three independent delta
    fields (``reasoning_content`` / ``content`` / ``tool_calls``), while the
    Responses API expects one ``output`` item per field, each finished by an
    ``output_item.done`` carrying the whole item.  The class keeps the accumulated
    text so the ``done`` events can report the real content instead of an empty
    placeholder.
    """

    def __init__(self, kind: str, index: int, item_id: str, *, call_id: str = "", name: str = "") -> None:
        self.kind = kind
        self.index = index
        self.item_id = item_id
        self.call_id = call_id
        self.name = name
        self.parts: list[str] = []

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def as_item(self, status: str) -> dict[str, Any]:
        if self.kind == "reasoning":
            summary = [{"type": "summary_text", "text": self.text}] if self.text else []
            return {"type": "reasoning", "id": self.item_id, "summary": summary}
        if self.kind == "message":
            content = (
                [{"type": "output_text", "text": self.text, "annotations": []}] if self.text else []
            )
            return {
                "type": "message",
                "id": self.item_id,
                "status": status,
                "role": "assistant",
                "content": content,
            }
        return {
            "type": "function_call",
            "id": self.item_id,
            "call_id": self.call_id,
            "name": self.name,
            "arguments": self.text or "{}",
            "status": status,
        }


def _chat_usage(usage: dict[str, Any]) -> dict[str, Any]:
    """Map Chat Completions usage onto the Responses usage shape Codex reads."""

    prompt_details = usage.get("prompt_tokens_details")
    completion_details = usage.get("completion_tokens_details")
    cached = int(prompt_details.get("cached_tokens") or 0) if isinstance(prompt_details, dict) else 0
    reasoning = (
        int(completion_details.get("reasoning_tokens") or 0)
        if isinstance(completion_details, dict)
        else 0
    )

    return {
        "input_tokens": int(usage.get("prompt_tokens") or 0),
        "input_tokens_details": {"cached_tokens": cached},
        "output_tokens": int(usage.get("completion_tokens") or 0),
        "output_tokens_details": {"reasoning_tokens": reasoning},
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def _stream_chat_as_responses(response: requests.Response, model: str) -> Iterator[str]:
    """Translate a Chat Completions SSE stream into Responses SSE events.

    Tool calls arrive as fragments keyed by ``index`` and have to be re-assembled
    here.  Dropping them is not a cosmetic loss: a turn where the model answers by
    calling a tool then reaches Codex as a *successful but empty* answer, which
    looks exactly like the model having said nothing at all.
    """

    response_id = _new_id("resp")
    created = int(time.time())
    reasoning: _ChatStreamItem | None = None
    message: _ChatStreamItem | None = None
    tool_items: dict[int, _ChatStreamItem] = {}
    next_output_index = 0
    usage: dict[str, Any] = {}

    def take_index() -> int:
        nonlocal next_output_index
        index = next_output_index
        next_output_index += 1
        return index

    yield _sse("response.created", {"type": "response.created", "response": {
        "id": response_id, "object": "response", "created_at": created,
        "status": "in_progress", "model": model, "output": [],
    }})

    for line in response.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except ValueError:
            continue
        if isinstance(chunk.get("usage"), dict):
            usage = chunk["usage"]
        delta = ((chunk.get("choices") or [{}])[0].get("delta") or {})

        reasoning_delta = delta.get("reasoning_content")
        if reasoning_delta:
            if reasoning is None:
                reasoning = _ChatStreamItem("reasoning", take_index(), _new_id("rs"))
                yield _sse("response.output_item.added", {"type": "response.output_item.added", "output_index": reasoning.index, "item": reasoning.as_item("in_progress")})
                yield _sse("response.reasoning_summary_part.added", {"type": "response.reasoning_summary_part.added", "item_id": reasoning.item_id, "output_index": reasoning.index, "summary_index": 0, "part": {"type": "summary_text", "text": ""}})
            reasoning.parts.append(str(reasoning_delta))
            yield _sse("response.reasoning_summary_text.delta", {"type": "response.reasoning_summary_text.delta", "item_id": reasoning.item_id, "output_index": reasoning.index, "summary_index": 0, "delta": reasoning_delta})

        text = delta.get("content")
        if text:
            if message is None:
                message = _ChatStreamItem("message", take_index(), _new_id("msg"))
                yield _sse("response.output_item.added", {"type": "response.output_item.added", "output_index": message.index, "item": message.as_item("in_progress")})
                yield _sse("response.content_part.added", {"type": "response.content_part.added", "item_id": message.item_id, "output_index": message.index, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}})
            message.parts.append(str(text))
            yield _sse("response.output_text.delta", {"type": "response.output_text.delta", "item_id": message.item_id, "output_index": message.index, "content_index": 0, "delta": text})

        for call in delta.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            try:
                call_index = int(call.get("index") or 0)
            except (TypeError, ValueError):
                call_index = 0
            item = tool_items.get(call_index)
            if item is None:
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                item = _ChatStreamItem(
                    "function_call",
                    take_index(),
                    _new_id("fc"),
                    call_id=str(call.get("id") or _new_id("call")),
                    name=str(function.get("name") or ""),
                )
                tool_items[call_index] = item
                yield _sse("response.output_item.added", {"type": "response.output_item.added", "output_index": item.index, "item": item.as_item("in_progress")})
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            if function.get("name") and not item.name:
                item.name = str(function["name"])
            arguments = function.get("arguments")
            if arguments:
                item.parts.append(str(arguments))
                yield _sse("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta", "item_id": item.item_id, "output_index": item.index, "delta": arguments})

    if reasoning is not None:
        yield _sse("response.reasoning_summary_text.done", {"type": "response.reasoning_summary_text.done", "item_id": reasoning.item_id, "output_index": reasoning.index, "summary_index": 0})
        yield _sse("response.output_item.done", {"type": "response.output_item.done", "output_index": reasoning.index, "item": reasoning.as_item("completed")})
    if message is not None:
        yield _sse("response.output_text.done", {"type": "response.output_text.done", "item_id": message.item_id, "output_index": message.index, "content_index": 0})
        yield _sse("response.output_item.done", {"type": "response.output_item.done", "output_index": message.index, "item": message.as_item("completed")})
    for call_index in sorted(tool_items):
        item = tool_items[call_index]
        yield _sse("response.function_call_arguments.done", {"type": "response.function_call_arguments.done", "item_id": item.item_id, "output_index": item.index, "arguments": item.text or "{}"})
        yield _sse("response.output_item.done", {"type": "response.output_item.done", "output_index": item.index, "item": item.as_item("completed")})

    items = [item for item in (reasoning, message) if item is not None]
    items.extend(tool_items[index] for index in sorted(tool_items))
    completed: dict[str, Any] = {
        "id": response_id, "object": "response", "created_at": created,
        "status": "completed", "model": model,
        "output": [item.as_item("completed") for item in sorted(items, key=lambda item: item.index)],
    }
    if usage:
        completed["usage"] = _chat_usage(usage)
    yield _sse("response.completed", {"type": "response.completed", "response": completed})


def _stream_messages_as_responses(response: requests.Response, model: str) -> Iterator[str]:
    for line in response.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        try:
            event = json.loads(data)
        except ValueError:
            continue
        delta = event.get("delta") or {}
        if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            yield _sse("response.output_text.delta", {"type": "response.output_text.delta", "delta": delta.get("text") or ""})
        elif event.get("type") == "message_stop":
            yield _sse("response.completed", {"type": "response.completed", "response": {
                "id": _new_id("resp"), "object": "response", "created_at": int(time.time()),
                "status": "completed", "model": model, "output": [],
            }})


def _forward(surface: str, payload: dict[str, Any], stream: bool) -> requests.Response:
    url = f"{OPENCODE_GO_BASE_URL}/{surface}"
    response = requests.post(
        url,
        headers=_upstream_headers(surface),
        json=payload,
        stream=stream,
        timeout=UPSTREAM_TIMEOUT_SECONDS,
    )
    # Upstream JSON/SSE is UTF-8; pin the encoding so iter_lines()/json() do not
    # fall back to ISO-8859-1 and mangle non-ASCII text.
    response.encoding = "utf-8"
    return response


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "opencode-proxy"}


@app.get("/v1/models")
def list_models(request: Request):
    if not _local_only(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        response = requests.get(
            f"{OPENCODE_GO_BASE_URL}/models",
            headers=_upstream_headers("responses"),
            timeout=30,
        )
        return JSONResponse(response.json(), status_code=response.status_code)
    except (ProxyError, requests.RequestException, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


def _json_or_text(response: requests.Response) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError:
        return {"error": response.text[:2000]}


@app.post("/v1/responses")
async def create_response(request: Request):
    if not _local_only(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid json"}, status_code=400)

    model = str(body.get("model") or "")
    surface = classify_model(model)
    stream = bool(body.get("stream"))
    upstream_path = {
        "responses": "responses",
        "chat": "chat/completions",
        "messages": "messages",
    }[surface]
    if surface == "responses":
        payload = body
    elif surface == "chat":
        payload = responses_to_chat(body, stream=stream)
        payload["stream"] = stream
    else:
        payload = responses_to_messages(body)
        payload["stream"] = stream

    try:
        upstream = await run_in_threadpool(_forward, upstream_path, payload, stream)
    except ProxyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except requests.RequestException as exc:
        return JSONResponse({"error": f"upstream request failed: {exc}"}, status_code=502)

    if upstream.status_code >= 400:
        return JSONResponse(_json_or_text(upstream), status_code=upstream.status_code)

    if stream:
        if surface == "responses":
            generator = _stream_responses_passthrough(upstream, model)
        elif surface == "chat":
            generator = _stream_chat_as_responses(upstream, model)
        else:
            generator = _stream_messages_as_responses(upstream, model)
        return StreamingResponse(generator, media_type="text/event-stream")

    data = _json_or_text(upstream)
    if surface == "chat":
        return JSONResponse(chat_to_responses(data, model))
    if surface == "messages":
        return JSONResponse(messages_to_responses(data, model))
    return JSONResponse(data)


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="opencode Go Responses proxy")
    parser.add_argument("--host", default=os.environ.get("OPENCODE_PROXY_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("OPENCODE_PROXY_PORT", DEFAULT_PORT)))
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
