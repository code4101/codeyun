from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from backend.core.ai_chat import AiProviderConfig, chat_with_provider
from backend.core.hierarchical_reduction import (
    HierarchicalReductionError,
    ReductionChunk,
    ReductionChunkItem,
    ReductionModelResponse,
    ReductionProfile,
    ReductionPrompt,
    ReductionSourceUnit,
    estimate_level_count,
    extract_json_object,
    run_hierarchical_reduction,
)


DOCUMENT_REDUCTION_BRANCH_FACTOR = 8
DOCUMENT_SOURCE_UNIT_MAX_CHARS = 1_800
DOCUMENT_REDUCTION_LEAF_MAX_CHARS = 12_000
DOCUMENT_REDUCTION_REDUCE_MAX_CHARS = 10_000
DOCUMENT_QUERY_MAX_SOURCE_REFS = 8
DOCUMENT_MODEL_TIMEOUT_SECONDS = 300.0
DOCUMENT_JSON_REPAIR_TIMEOUT_SECONDS = 120.0
DOCUMENT_INDEX_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "summary": {"type": "string"},
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
        },
        "possible_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "importance": {
            "type": "string",
            "enum": ["low", "medium", "high"],
        },
        "importance_reason": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": [
        "topic",
        "summary",
        "keywords",
        "possible_questions",
        "importance",
        "importance_reason",
        "reason",
    ],
}
DOCUMENT_QUERY_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "summary": {"type": "string"},
        "matched_node_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "matched_source_refs": {
            "type": "array",
            "items": {"type": "string"},
        },
        "needs_more_context": {"type": "boolean"},
        "follow_up_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "answer",
        "summary",
        "matched_node_ids",
        "matched_source_refs",
        "needs_more_context",
        "follow_up_questions",
    ],
}


class DocumentReductionError(RuntimeError):
    """Raised when document reduction cannot complete safely."""


def build_document_source_units(
    text: str,
    *,
    document_id: str,
    max_chars: int = DOCUMENT_SOURCE_UNIT_MAX_CHARS,
) -> list[ReductionSourceUnit]:
    content = (text or "").strip()
    if not content:
        raise DocumentReductionError("文档内容不能为空")

    paragraphs = _split_text_paragraphs(content)
    units: list[ReductionSourceUnit] = []
    current_parts: list[str] = []
    current_chars = 0
    current_start = 0
    consumed_chars = 0

    def flush() -> None:
        nonlocal current_parts, current_chars, current_start
        if not current_parts:
            return
        chunk_text = "\n\n".join(current_parts).strip()
        if not chunk_text:
            current_parts = []
            current_chars = 0
            return
        unit_index = len(units) + 1
        units.append(
            ReductionSourceUnit(
                unit_id=f"{document_id}:chunk-{unit_index:04d}",
                content=chunk_text,
                metadata={
                    "chunk_index": unit_index,
                    "start_char": current_start,
                    "end_char": current_start + len(chunk_text),
                },
            )
        )
        current_parts = []
        current_chars = 0

    for paragraph in paragraphs:
        normalized = paragraph.strip()
        if not normalized:
            continue
        pieces = _slice_text(normalized, max_chars=max_chars)
        for piece in pieces:
            if not current_parts:
                current_start = consumed_chars
            projected = current_chars + len(piece) + (2 if current_parts else 0)
            if current_parts and projected > max_chars:
                flush()
                current_start = consumed_chars
            current_parts.append(piece)
            current_chars = len("\n\n".join(current_parts))
            consumed_chars += len(piece) + 2
    flush()
    return units


def generate_document_index(
    *,
    document_id: str,
    document_title: str,
    text: str,
    provider_id: str,
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    extra_providers: tuple[AiProviderConfig, ...] = (),
    branch_factor: int = DOCUMENT_REDUCTION_BRANCH_FACTOR,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    try:
        source_units = build_document_source_units(text, document_id=document_id)
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "prepared",
                    "source_unit_count": len(source_units),
                    "estimated_level_count": estimate_level_count(len(source_units), branch_factor=branch_factor),
                }
            )
        profile = _build_document_reduction_profile(
            branch_factor=branch_factor,
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=model,
            extra_providers=extra_providers,
        )
        model_runner = _build_document_model_runner(
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=model,
            extra_providers=extra_providers,
        )
        reduction_result = run_hierarchical_reduction(
            source_units,
            profile=profile,
            model_runner=model_runner,
            root_input_meta={
                "document_id": document_id,
                "document_title": document_title,
            },
            progress_callback=progress_callback,
        )
    except HierarchicalReductionError as exc:
        raise DocumentReductionError(str(exc)) from exc

    return {
        "document_id": document_id,
        "source_units": [
            {
                "unit_id": item.unit_id,
                "content": str(item.content),
                "metadata": dict(item.metadata or {}),
            }
            for item in source_units
        ],
        "source_unit_count": len(source_units),
        "reduction": {
            "run_id": reduction_result.run_id,
            "profile_id": reduction_result.profile_id,
            "level_count": len(reduction_result.levels),
            "node_count": sum(len(level.nodes) for level in reduction_result.levels),
            "levels": [_serialize_level(level) for level in reduction_result.levels],
        },
        "result": dict(reduction_result.final_result or {}),
    }


def answer_document_question(
    *,
    question: str,
    root_summary: dict[str, Any],
    matched_nodes: list[dict[str, Any]],
    source_units: dict[str, dict[str, Any]],
    provider_id: str,
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    extra_providers: tuple[AiProviderConfig, ...] = (),
) -> dict[str, Any]:
    normalized_question = (question or "").strip()
    if not normalized_question:
        raise DocumentReductionError("问题不能为空")

    prompt = _build_document_query_prompt(
        question=normalized_question,
        root_summary=root_summary,
        matched_nodes=matched_nodes,
        source_units=source_units,
    )
    response = chat_with_provider(
        provider_id=provider_id,
        base_url=base_url,
        api_key=api_key,
        messages=[{"role": "user", "content": prompt}],
        model=(model or "").strip() or None,
        system_prompt=_build_document_query_system_prompt(),
        temperature=0.1,
        response_format=_resolve_document_response_format(provider_id, DOCUMENT_QUERY_JSON_SCHEMA),
        timeout_seconds=DOCUMENT_MODEL_TIMEOUT_SECONDS,
        extra_providers=extra_providers,
    )
    payload = _parse_document_query_payload(
        str(response.get("content") or ""),
        provider_id=provider_id,
        base_url=base_url,
        api_key=api_key,
        model=model,
        extra_providers=extra_providers,
    )
    return {
        "model": str(response.get("model") or model or provider_id),
        "answer": str(payload.get("answer") or "").strip(),
        "summary": str(payload.get("summary") or "").strip(),
        "matched_node_ids": [
            str(item).strip()
            for item in (payload.get("matched_node_ids") or [])
            if str(item).strip()
        ],
        "matched_source_refs": [
            str(item).strip()
            for item in (payload.get("matched_source_refs") or [])
            if str(item).strip()
        ],
        "needs_more_context": bool(payload.get("needs_more_context")),
        "follow_up_questions": [
            str(item).strip()
            for item in (payload.get("follow_up_questions") or [])
            if str(item).strip()
        ][:3],
        "raw_content": str(response.get("content") or ""),
    }


def _build_document_reduction_profile(
    *,
    branch_factor: int,
    provider_id: str,
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    extra_providers: tuple[AiProviderConfig, ...],
) -> ReductionProfile:
    return ReductionProfile(
        profile_id="document_index_hierarchical",
        task_type="document_index",
        branch_factor=branch_factor,
        leaf_prompt_builder=lambda chunk, profile: ReductionPrompt(
            system_prompt=_build_document_leaf_system_prompt(),
            user_prompt=_build_document_leaf_user_prompt(chunk),
        ),
        reduce_prompt_builder=lambda chunk, profile: ReductionPrompt(
            system_prompt=_build_document_reduce_system_prompt(),
            user_prompt=_build_document_reduce_user_prompt(chunk),
        ),
        payload_parser=lambda response, **_: _parse_document_index_payload(
            response.content,
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=model,
            extra_providers=extra_providers,
        ),
        leaf_chunker=_build_document_leaf_chunker(branch_factor=branch_factor),
        reduce_chunker=_build_document_reduce_chunker(branch_factor=branch_factor),
        finalizer=_build_document_finalizer(),
    )


def _build_document_model_runner(
    *,
    provider_id: str,
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    extra_providers: tuple[AiProviderConfig, ...],
):
    def model_runner(prompt: ReductionPrompt, *, chunk: ReductionChunk, profile: ReductionProfile) -> ReductionModelResponse:
        del chunk, profile
        response = chat_with_provider(
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            messages=[{"role": "user", "content": prompt.user_prompt}],
            model=(model or "").strip() or None,
            system_prompt=prompt.system_prompt,
            temperature=0.0,
            response_format=_resolve_document_response_format(provider_id, DOCUMENT_INDEX_JSON_SCHEMA),
            timeout_seconds=DOCUMENT_MODEL_TIMEOUT_SECONDS,
            extra_providers=extra_providers,
        )
        return ReductionModelResponse(
            model=str(response.get("model") or model or provider_id),
            content=str(response.get("content") or ""),
            raw=response,
        )

    return model_runner


def _build_document_leaf_system_prompt() -> str:
    return (
        "你是一个文档索引助手。"
        "你会收到一小段文档原文，需要提炼便于后续检索和问答的结构化信息。"
        "你只能输出 JSON，不要输出 Markdown、解释、代码块或额外文本。"
        "JSON 结构固定为："
        '{"topic":"", "summary":"", "keywords": [], "possible_questions": [], "importance":"medium", "importance_reason":"", "reason": ""}。'
        "topic 和 summary 使用中文。"
        "keywords 返回 3 到 8 个短词。"
        "possible_questions 返回 2 到 5 个用户可能会问的中文问题。"
        'importance 只能是 "low"、"medium"、"high"。'
    )


def _build_document_reduce_system_prompt() -> str:
    return (
        "你是一个文档归并索引助手。"
        "你会收到多个下层结构化摘要，需要继续归并，但要保留后续检索需要的主题、关键词和潜在问题。"
        "你只能输出 JSON，不要输出 Markdown、解释、代码块或额外文本。"
        "JSON 结构固定为："
        '{"topic":"", "summary":"", "keywords": [], "possible_questions": [], "importance":"medium", "importance_reason":"", "reason": ""}。'
        "summary 要体现更高层概括，不要只是机械拼接。"
    )


def _build_document_leaf_user_prompt(chunk: ReductionChunk) -> str:
    sections = [
        "请对下面这一小组文档片段做结构化索引。",
        "- 目标: 便于后续的模糊提问、检索、回查和摘要",
        "",
    ]
    for index, item in enumerate(chunk.items, start=1):
        sections.append(f"### 片段 {index}")
        sections.append(str(item.content))
        sections.append("")
    return "\n".join(sections).strip()


def _build_document_reduce_user_prompt(chunk: ReductionChunk) -> str:
    sections = [
        "请继续归并下面多个下层索引摘要。",
        "- 目标: 形成更高层的主题索引，并保留关键词、潜在提问和重要性判断",
        "",
    ]
    for index, item in enumerate(chunk.items, start=1):
        sections.append(f"### 子摘要 {index}")
        source_refs = item.metadata.get("source_refs") or []
        if isinstance(source_refs, list) and source_refs:
            sections.append(f"来源片段: {', '.join(str(value) for value in source_refs)}")
        sections.append(json.dumps(item.content, ensure_ascii=False, indent=2))
        sections.append("")
    return "\n".join(sections).strip()


def _build_document_query_system_prompt() -> str:
    return (
        "你是一个文档问答助手。"
        "你只能根据提供的文档索引摘要和原文片段回答。"
        "如果上下文不足，要明确说明并返回 needs_more_context=true。"
        "你只能输出 JSON，不要输出 Markdown、解释、代码块或额外文本。"
        "JSON 结构固定为："
        '{"answer":"", "summary":"", "matched_node_ids": [], "matched_source_refs": [], "needs_more_context": false, "follow_up_questions": []}。'
        "answer 和 summary 使用中文。"
    )


def _build_document_json_repair_system_prompt() -> str:
    return (
        "你是一个 JSON 修复助手。"
        "你会收到一段模型输出，请将其整理为一个合法的 JSON 对象。"
        "不能输出 Markdown、解释、代码块或额外文本。"
        "如果原内容字段缺失，只能用空字符串、空数组或 false 补齐，不要编造事实。"
    )


def _build_document_index_json_schema_prompt() -> str:
    return (
        '目标 JSON 结构固定为：'
        '{"topic":"", "summary":"", "keywords": [], "possible_questions": [], '
        '"importance":"medium", "importance_reason":"", "reason": ""}。'
    )


def _build_document_query_json_schema_prompt() -> str:
    return (
        '目标 JSON 结构固定为：'
        '{"answer":"", "summary":"", "matched_node_ids": [], "matched_source_refs": [], '
        '"needs_more_context": false, "follow_up_questions": []}。'
    )


def _build_document_query_prompt(
    *,
    question: str,
    root_summary: dict[str, Any],
    matched_nodes: list[dict[str, Any]],
    source_units: dict[str, dict[str, Any]],
) -> str:
    sections = [
        f"用户问题: {question}",
        "",
        "## 文档总览",
        json.dumps(root_summary, ensure_ascii=False, indent=2),
        "",
        "## 命中节点",
    ]
    for index, node in enumerate(matched_nodes, start=1):
        sections.append(f"### 节点 {index}")
        sections.append(json.dumps(node.get("payload") or node, ensure_ascii=False, indent=2))
        sections.append("")

    sections.append("## 原文片段")
    seen_refs: set[str] = set()
    appended = 0
    for node in matched_nodes:
        for ref in node.get("source_refs") or []:
            ref_id = str(ref).strip()
            if not ref_id or ref_id in seen_refs:
                continue
            source_unit = source_units.get(ref_id)
            if not source_unit:
                continue
            seen_refs.add(ref_id)
            appended += 1
            sections.append(f"### 片段 {ref_id}")
            sections.append(str(source_unit.get("content") or ""))
            sections.append("")
            if appended >= DOCUMENT_QUERY_MAX_SOURCE_REFS:
                break
        if appended >= DOCUMENT_QUERY_MAX_SOURCE_REFS:
            break

    return "\n".join(sections).strip()


def _parse_document_index_payload(
    raw_content: Any,
    *,
    provider_id: str,
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    extra_providers: tuple[AiProviderConfig, ...],
) -> dict[str, Any]:
    payload = _extract_or_repair_document_payload(
        raw_content,
        provider_id=provider_id,
        base_url=base_url,
        api_key=api_key,
        model=model,
        extra_providers=extra_providers,
        schema_prompt=_build_document_index_json_schema_prompt(),
    )
    return {
        "topic": str(payload.get("topic") or "").strip(),
        "summary": str(payload.get("summary") or "").strip(),
        "keywords": _normalize_string_list(payload.get("keywords"), limit=8),
        "possible_questions": _normalize_string_list(payload.get("possible_questions"), limit=5),
        "importance": _normalize_importance(payload.get("importance")),
        "importance_reason": str(payload.get("importance_reason") or "").strip(),
        "reason": str(payload.get("reason") or "").strip(),
    }


def _parse_document_query_payload(
    raw_content: Any,
    *,
    provider_id: str,
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    extra_providers: tuple[AiProviderConfig, ...],
) -> dict[str, Any]:
    payload = _extract_or_repair_document_payload(
        raw_content,
        provider_id=provider_id,
        base_url=base_url,
        api_key=api_key,
        model=model,
        extra_providers=extra_providers,
        schema_prompt=_build_document_query_json_schema_prompt(),
    )
    return {
        "answer": str(payload.get("answer") or "").strip(),
        "summary": str(payload.get("summary") or "").strip(),
        "matched_node_ids": _normalize_string_list(payload.get("matched_node_ids"), limit=12),
        "matched_source_refs": _normalize_string_list(payload.get("matched_source_refs"), limit=16),
        "needs_more_context": bool(payload.get("needs_more_context")),
        "follow_up_questions": _normalize_string_list(payload.get("follow_up_questions"), limit=3),
    }


def _extract_or_repair_document_payload(
    raw_content: Any,
    *,
    provider_id: str,
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    extra_providers: tuple[AiProviderConfig, ...],
    schema_prompt: str,
) -> dict[str, Any]:
    try:
        return extract_json_object(raw_content)
    except HierarchicalReductionError as first_error:
        repaired_content = _repair_document_json_output(
            raw_content,
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=model,
            extra_providers=extra_providers,
            schema_prompt=schema_prompt,
        )
        try:
            return extract_json_object(repaired_content)
        except HierarchicalReductionError as repair_error:
            raise DocumentReductionError(
                f"{first_error}；修复后仍无法解析：{repair_error}"
            ) from repair_error


def _repair_document_json_output(
    raw_content: Any,
    *,
    provider_id: str,
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    extra_providers: tuple[AiProviderConfig, ...],
    schema_prompt: str,
) -> str:
    response = chat_with_provider(
        provider_id=provider_id,
        base_url=base_url,
        api_key=api_key,
        messages=[
            {
                "role": "user",
                "content": "\n".join(
                    [
                        schema_prompt,
                        "请把下面内容修复成严格合法的 JSON 对象，只输出 JSON。",
                        "",
                        "原始内容：",
                        str(raw_content or ""),
                    ]
                ).strip(),
            }
        ],
        model=(model or "").strip() or None,
        system_prompt=_build_document_json_repair_system_prompt(),
        temperature=0.0,
        response_format=_resolve_document_response_format(provider_id, "json"),
        timeout_seconds=DOCUMENT_JSON_REPAIR_TIMEOUT_SECONDS,
        extra_providers=extra_providers,
    )
    return str(response.get("content") or "")


def _normalize_string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        items.append(text)
        if len(items) >= limit:
            break
    return items


def _normalize_importance(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"low", "medium", "high"}:
        return normalized
    return "medium"


def _resolve_document_response_format(provider_id: str, schema: Any) -> Any:
    normalized = (provider_id or "").strip().lower()
    if normalized == "ollama":
        return schema
    return None


def _build_document_leaf_chunker(*, branch_factor: int):
    def chunker(items, *, level, input_kind, profile):
        del input_kind, profile
        chunks: list[ReductionChunk] = []
        current_items = []
        current_chars = 0
        max_items = max(2, branch_factor)

        for item in items:
            item_chars = len(str(item.content or ""))
            if current_items and (len(current_items) >= max_items or current_chars + item_chars > DOCUMENT_REDUCTION_LEAF_MAX_CHARS):
                chunks.append(_build_chunk_from_sources(current_items, level=level))
                current_items = []
                current_chars = 0
            current_items.append(item)
            current_chars += item_chars

        if current_items:
            chunks.append(_build_chunk_from_sources(current_items, level=level))
        return chunks

    return chunker


def _build_document_reduce_chunker(*, branch_factor: int):
    def chunker(items, *, level, input_kind, profile):
        del input_kind, profile
        chunks: list[ReductionChunk] = []
        current_items = []
        current_chars = 0
        max_items = max(2, branch_factor)

        for item in items:
            payload_text = json.dumps(item.payload, ensure_ascii=False, sort_keys=True)
            item_chars = len(payload_text)
            if current_items and (len(current_items) >= max_items or current_chars + item_chars > DOCUMENT_REDUCTION_REDUCE_MAX_CHARS):
                chunks.append(_build_chunk_from_nodes(current_items, level=level))
                current_items = []
                current_chars = 0
            current_items.append(item)
            current_chars += item_chars

        if current_items:
            chunks.append(_build_chunk_from_nodes(current_items, level=level))
        return chunks

    return chunker


def _build_chunk_from_sources(items: list[ReductionSourceUnit], *, level: int) -> ReductionChunk:
    return ReductionChunk(
        chunk_id=f"document_index_hierarchical:L{level}:C{items[0].unit_id}",
        level=level,
        task_type="document_index",
        input_kind="source",
        items=[
            ReductionChunkItem(
                ref_id=item.unit_id,
                kind="source",
                content=item.content,
                metadata=dict(item.metadata or {}),
            )
            for item in items
        ],
        metadata={"item_count": len(items)},
    )


def _build_chunk_from_nodes(items, *, level: int) -> ReductionChunk:
    return ReductionChunk(
        chunk_id=f"document_index_hierarchical:L{level}:C{items[0].node_id}",
        level=level,
        task_type="document_index",
        input_kind="summary",
        items=[
            ReductionChunkItem(
                ref_id=item.node_id,
                kind="summary",
                content=dict(item.payload or {}),
                metadata={
                    **dict(item.metadata or {}),
                    "source_refs": list(item.source_refs),
                    "child_node_ids": list(item.child_node_ids),
                },
            )
            for item in items
        ],
        metadata={"item_count": len(items)},
    )


def _build_document_finalizer():
    def finalizer(root_node, *, levels, profile):
        del levels, profile
        payload = dict(root_node.payload or {})
        return {
            "topic": str(payload.get("topic") or "").strip(),
            "summary": str(payload.get("summary") or "").strip(),
            "keywords": [str(item).strip() for item in (payload.get("keywords") or []) if str(item).strip()][:8],
            "possible_questions": [
                str(item).strip()
                for item in (payload.get("possible_questions") or [])
                if str(item).strip()
            ][:5],
            "importance": str(payload.get("importance") or "medium").strip() or "medium",
            "importance_reason": str(payload.get("importance_reason") or "").strip(),
            "reason": str(payload.get("reason") or "").strip(),
        }

    return finalizer


def _serialize_level(level) -> dict[str, Any]:
    return {
        "level": level.level,
        "input_kind": level.input_kind,
        "chunk_count": len(level.chunks),
        "node_count": len(level.nodes),
        "nodes": [
            {
                "node_id": node.node_id,
                "level": node.level,
                "chunk_id": node.chunk_id,
                "payload": dict(node.payload or {}),
                "source_refs": list(node.source_refs),
                "child_node_ids": list(node.child_node_ids),
                "model": node.model,
                "metadata": dict(node.metadata or {}),
            }
            for node in level.nodes
        ],
    }


def _split_text_paragraphs(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = re.split(r"\n\s*\n+", normalized)
    return [item.strip() for item in paragraphs if item.strip()]


def _slice_text(text: str, *, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    slices: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        cut = remaining.rfind(" ", 0, max_chars)
        if cut < max_chars // 2:
            cut = max_chars
        slices.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        slices.append(remaining)
    return [item for item in slices if item]
