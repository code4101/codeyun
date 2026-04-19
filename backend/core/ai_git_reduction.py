from __future__ import annotations

import json
from typing import Any, Callable, Optional

from backend.core.ai_chat import AiProviderConfig, chat_with_provider
from backend.core.ai_git_commit import AiGitCommitError
from backend.core.git_tools import (
    collect_git_reduction_source_units,
    format_git_commit_message,
    normalize_commit_body_lines,
)
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


GIT_REDUCTION_BRANCH_FACTOR = 10
GIT_REDUCTION_LEAF_MAX_CHARS = 12_000
GIT_REDUCTION_REDUCE_MAX_CHARS = 10_000
GIT_MODEL_TIMEOUT_SECONDS = 300.0
GIT_JSON_REPAIR_TIMEOUT_SECONDS = 120.0
GIT_COMMIT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "summary": {"type": "string"},
        "key_points": {
            "type": "array",
            "items": {"type": "string"},
        },
        "risk_points": {
            "type": "array",
            "items": {"type": "string"},
        },
        "candidate_subject": {"type": "string"},
        "candidate_body": {
            "type": "array",
            "items": {"type": "string"},
        },
        "should_split": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": [
        "topic",
        "summary",
        "key_points",
        "risk_points",
        "candidate_subject",
        "candidate_body",
        "should_split",
        "reason",
    ],
}


def generate_ai_git_commit_draft_hierarchical(
    *,
    cwd: Optional[str] = None,
    provider_id: str,
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    style: str,
    include_body: bool,
    extra_providers: tuple[AiProviderConfig, ...] = (),
    branch_factor: int = GIT_REDUCTION_BRANCH_FACTOR,
    reduction_input: Optional[dict[str, Any]] = None,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    try:
        if reduction_input is None:
            if not (cwd or "").strip():
                raise AiGitCommitError("cwd 不能为空")
            reduction_input = collect_git_reduction_source_units(str(cwd))
        source_units = [
            ReductionSourceUnit(
                unit_id=str(item["unit_id"]),
                content=str(item["content"]),
                metadata={
                    "path": str(item["path"]),
                    "group": str(item["group"]),
                    "truncated": bool(item.get("truncated")),
                },
            )
            for item in reduction_input["source_units"]  # type: ignore[index]
            if isinstance(item, dict)
        ]
        if not source_units:
            raise AiGitCommitError("没有可供分层压缩的 Git 变更单元")
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "prepared",
                    "source_unit_count": len(source_units),
                    "estimated_level_count": estimate_level_count(len(source_units), branch_factor=branch_factor),
                    "source_unit_truncated_count": int(reduction_input.get("source_unit_truncated_count") or 0),
                }
            )

        profile = _build_git_commit_reduction_profile(
            style=style,
            include_body=include_body,
            branch_factor=branch_factor,
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=model,
            extra_providers=extra_providers,
        )
        model_runner = _build_git_commit_model_runner(
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
                "cwd": reduction_input["cwd"],
                "repo_root": reduction_input["repo_root"],
                "branch": reduction_input["branch"],
            },
            progress_callback=progress_callback,
        )
    except HierarchicalReductionError as exc:
        raise AiGitCommitError(str(exc)) from exc

    final_result = dict(reduction_result.final_result or {})
    final_result["model"] = str(reduction_result.root_node.model or model or provider_id)
    final_result["raw_content"] = str(reduction_result.root_node.raw_model_output or "")
    final_result["reduction"] = {
        "run_id": reduction_result.run_id,
        "profile_id": reduction_result.profile_id,
        "level_count": len(reduction_result.levels),
        "source_unit_count": len(source_units),
        "source_unit_truncated_count": int(reduction_input.get("source_unit_truncated_count") or 0),
        "node_count": sum(len(level.nodes) for level in reduction_result.levels),
        "leaf_chunk_count": len(reduction_result.levels[0].chunks) if reduction_result.levels else 0,
        "levels": [
            {
                "level": level.level,
                "input_kind": level.input_kind,
                "chunk_count": len(level.chunks),
                "node_count": len(level.nodes),
                "preview_nodes": [
                    {
                        "node_id": node.node_id,
                        "topic": str(node.payload.get("topic") or "").strip(),
                        "summary": str(node.payload.get("summary") or "").strip(),
                        "candidate_subject": str(node.payload.get("candidate_subject") or "").strip(),
                        "source_ref_count": len(node.source_refs),
                    }
                    for node in level.nodes[:3]
                ],
            }
            for level in reduction_result.levels
        ],
    }
    return {
        "inspect": {
            key: value
            for key, value in reduction_input.items()
            if key not in {"source_units", "source_unit_count", "source_unit_truncated_count"}
        },
        **final_result,
    }


def _build_git_commit_reduction_profile(
    *,
    style: str,
    include_body: bool,
    branch_factor: int,
    provider_id: str,
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    extra_providers: tuple[AiProviderConfig, ...],
) -> ReductionProfile:
    def leaf_prompt_builder(chunk: ReductionChunk, *, profile: ReductionProfile) -> ReductionPrompt:
        del profile
        return ReductionPrompt(
            system_prompt=_build_leaf_system_prompt(style=style, include_body=include_body),
            user_prompt=_build_leaf_user_prompt(chunk=chunk, style=style, include_body=include_body),
        )

    def reduce_prompt_builder(chunk: ReductionChunk, *, profile: ReductionProfile) -> ReductionPrompt:
        del profile
        return ReductionPrompt(
            system_prompt=_build_reduce_system_prompt(style=style, include_body=include_body),
            user_prompt=_build_reduce_user_prompt(chunk=chunk, style=style, include_body=include_body),
        )

    return ReductionProfile(
        profile_id="git_commit_hierarchical",
        task_type="git_commit",
        branch_factor=branch_factor,
        leaf_prompt_builder=leaf_prompt_builder,
        reduce_prompt_builder=reduce_prompt_builder,
        payload_parser=lambda response, **_: _parse_git_commit_payload(
            response.content,
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=model,
            include_body=include_body,
            extra_providers=extra_providers,
        ),
        leaf_chunker=_build_git_leaf_chunker(branch_factor=branch_factor),
        reduce_chunker=_build_git_reduce_chunker(branch_factor=branch_factor),
        finalizer=_build_git_commit_finalizer(include_body=include_body),
    )


def _build_git_commit_model_runner(
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
            response_format=_resolve_git_response_format(provider_id, GIT_COMMIT_JSON_SCHEMA),
            timeout_seconds=GIT_MODEL_TIMEOUT_SECONDS,
            extra_providers=extra_providers,
        )
        return ReductionModelResponse(
            model=str(response.get("model") or model or provider_id),
            content=str(response.get("content") or ""),
            raw=response,
        )

    return model_runner


def _build_leaf_system_prompt(*, style: str, include_body: bool) -> str:
    style_text = (
        "candidate_subject 必须符合 Conventional Commits，description 使用中文。"
        if style == "conventional"
        else "candidate_subject 必须是自然中文总结，不要带 Conventional Commit 前缀。"
    )
    body_text = (
        "candidate_body 必须是 2 到 4 条中文短句数组，每条只描述一个关键变化，不要自己带编号或项目符号前缀，系统会自动格式化成 1、2、3 编号正文。"
        if include_body
        else "candidate_body 必须返回空数组。"
    )
    return (
        "你是一个严谨的 Git 叶子摘要助手。"
        "你只能输出 JSON，不要输出 Markdown、解释、代码块或额外文本。"
        "JSON 结构固定为："
        '{"topic":"", "summary":"", "key_points": [], "risk_points": [], "candidate_subject":"", "candidate_body": [], "should_split": false, "reason": ""}。'
        "topic 和 summary 用中文。"
        "key_points、risk_points 都必须是中文短句数组。"
        f"{style_text}"
        f"{body_text}"
        "如果这批文件内部主题已经明显混杂，则把 should_split 设为 true，并在 reason 中说明。"
    )


def _build_reduce_system_prompt(*, style: str, include_body: bool) -> str:
    style_text = (
        "candidate_subject 必须符合 Conventional Commits，description 使用中文。"
        if style == "conventional"
        else "candidate_subject 必须是自然中文总结，不要带 Conventional Commit 前缀。"
    )
    body_text = (
        "candidate_body 必须是 2 到 4 条中文短句数组，每条只保留归并后的高价值变化，不要自己带编号或项目符号前缀，系统会自动格式化成 1、2、3 编号正文。"
        if include_body
        else "candidate_body 必须返回空数组。"
    )
    return (
        "你是一个严谨的 Git 归并摘要助手。"
        "你会收到多个下层结构化摘要，需要把它们压缩成更高层的统一结果。"
        "你只能输出 JSON，不要输出 Markdown、解释、代码块或额外文本。"
        "JSON 结构固定为："
        '{"topic":"", "summary":"", "key_points": [], "risk_points": [], "candidate_subject":"", "candidate_body": [], "should_split": false, "reason": ""}。'
        "summary 应该是归并后的整体概括，而不是罗列原文。"
        f"{style_text}"
        f"{body_text}"
        "只有在这些下层摘要仍然明显属于多个独立主题时，才把 should_split 设为 true。"
    )


def _build_git_json_repair_system_prompt() -> str:
    return (
        "你是一个 Git 提交 JSON 修复助手。"
        "你会收到一段模型输出，请将其整理为一个合法的 JSON 对象。"
        "不能输出 Markdown、解释、代码块或额外文本。"
        "如果原内容字段缺失，只能用空字符串、空数组或 false 补齐，不要编造事实。"
    )


def _build_git_json_schema_prompt(*, include_body: bool) -> str:
    body_instruction = '"candidate_body": []' if not include_body else '"candidate_body": [""]'
    return (
        "目标 JSON 结构固定为："
        '{"topic":"", "summary":"", "key_points": [], "risk_points": [], '
        f'"candidate_subject":"", {body_instruction}, "should_split": false, "reason": ""}}。'
    )


def _parse_git_commit_payload(
    raw_content: Any,
    *,
    provider_id: str,
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    include_body: bool,
    extra_providers: tuple[AiProviderConfig, ...],
) -> dict[str, Any]:
    payload = _extract_or_repair_git_payload(
        raw_content,
        provider_id=provider_id,
        base_url=base_url,
        api_key=api_key,
        model=model,
        include_body=include_body,
        extra_providers=extra_providers,
    )
    return {
        "topic": str(payload.get("topic") or "").strip(),
        "summary": str(payload.get("summary") or "").strip(),
        "key_points": _normalize_string_list(payload.get("key_points"), limit=8),
        "risk_points": _normalize_string_list(payload.get("risk_points"), limit=8),
        "candidate_subject": str(payload.get("candidate_subject") or "").strip(),
        "candidate_body": (
            _normalize_string_list(payload.get("candidate_body"), limit=4)
            if include_body
            else []
        ),
        "should_split": bool(payload.get("should_split")),
        "reason": str(payload.get("reason") or "").strip(),
    }


def _extract_or_repair_git_payload(
    raw_content: Any,
    *,
    provider_id: str,
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    include_body: bool,
    extra_providers: tuple[AiProviderConfig, ...],
) -> dict[str, Any]:
    try:
        return extract_json_object(raw_content)
    except HierarchicalReductionError as first_error:
        repaired_content = _repair_git_json_output(
            raw_content,
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=model,
            include_body=include_body,
            extra_providers=extra_providers,
        )
        try:
            return extract_json_object(repaired_content)
        except HierarchicalReductionError as repair_error:
            raise AiGitCommitError(
                f"{first_error}；修复后仍无法解析：{repair_error}"
            ) from repair_error


def _repair_git_json_output(
    raw_content: Any,
    *,
    provider_id: str,
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    include_body: bool,
    extra_providers: tuple[AiProviderConfig, ...],
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
                        _build_git_json_schema_prompt(include_body=include_body),
                        "请把下面内容修复成严格合法的 JSON 对象，只输出 JSON。",
                        "",
                        "原始内容：",
                        str(raw_content or ""),
                    ]
                ).strip(),
            }
        ],
        model=(model or "").strip() or None,
        system_prompt=_build_git_json_repair_system_prompt(),
        temperature=0.0,
        response_format=_resolve_git_response_format(provider_id, "json"),
        timeout_seconds=GIT_JSON_REPAIR_TIMEOUT_SECONDS,
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


def _resolve_git_response_format(provider_id: str, schema: Any) -> Any:
    normalized = (provider_id or "").strip().lower()
    if normalized == "ollama":
        return schema
    return None


def _build_leaf_user_prompt(*, chunk: ReductionChunk, style: str, include_body: bool) -> str:
    style_label = "Conventional Commit" if style == "conventional" else "中文总结"
    body_label = "需要正文" if include_body else "只需要标题"
    sections = [
        "请基于下面这一小组 Git 文件变更，生成结构化叶子摘要。",
        f"- 风格: {style_label}",
        f"- 正文: {body_label}",
        "- 目标: 忠实提炼这组文件在做什么，并给出这组文件对应的候选提交标题/正文",
        "",
    ]
    for index, item in enumerate(chunk.items, start=1):
        sections.append(f"### 变更单元 {index}")
        sections.append(str(item.content))
        sections.append("")
    return "\n".join(sections).strip()


def _build_reduce_user_prompt(*, chunk: ReductionChunk, style: str, include_body: bool) -> str:
    style_label = "Conventional Commit" if style == "conventional" else "中文总结"
    body_label = "需要正文" if include_body else "只需要标题"
    sections = [
        "请基于下面多个下层摘要继续归并，形成更高层的 Git 归并摘要。",
        f"- 风格: {style_label}",
        f"- 正文: {body_label}",
        "- 目标: 合并重复主题，保留高价值差异，并给出更高层的候选提交标题/正文",
        "",
    ]
    for index, item in enumerate(chunk.items, start=1):
        sections.append(f"### 子摘要 {index}")
        source_refs = item.metadata.get("source_refs") or []
        if isinstance(source_refs, list) and source_refs:
            sections.append(f"来源单元: {', '.join(str(value) for value in source_refs)}")
        sections.append(json.dumps(item.content, ensure_ascii=False, indent=2))
        sections.append("")
    return "\n".join(sections).strip()


def _build_git_leaf_chunker(*, branch_factor: int):
    def chunker(items, *, level, input_kind, profile):
        del input_kind, profile
        chunks: list[ReductionChunk] = []
        current_items = []
        current_chars = 0
        current_groups: set[str] = set()
        max_items = max(2, branch_factor)

        sorted_items = sorted(
            items,
            key=lambda item: (
                str(item.metadata.get("group") if isinstance(item, ReductionSourceUnit) else "").strip(),
                str(item.unit_id if isinstance(item, ReductionSourceUnit) else item.node_id),
            ),
        )
        for item in sorted_items:
            if not isinstance(item, ReductionSourceUnit):
                continue
            item_chars = len(str(item.content or ""))
            item_group = str(item.metadata.get("group") or "").strip()
            should_flush = bool(
                current_items and (
                    len(current_items) >= max_items
                    or current_chars + item_chars > GIT_REDUCTION_LEAF_MAX_CHARS
                    or (len(current_groups) >= 2 and item_group and item_group not in current_groups)
                )
            )
            if should_flush:
                chunks.append(_build_chunk_from_sources(current_items, level=level))
                current_items = []
                current_chars = 0
                current_groups = set()
            current_items.append(item)
            current_chars += item_chars
            if item_group:
                current_groups.add(item_group)

        if current_items:
            chunks.append(_build_chunk_from_sources(current_items, level=level))
        return chunks

    return chunker


def _build_git_reduce_chunker(*, branch_factor: int):
    def chunker(items, *, level, input_kind, profile):
        del input_kind, profile
        chunks: list[ReductionChunk] = []
        current_items = []
        current_chars = 0
        max_items = max(2, branch_factor)

        for item in items:
            payload_text = json.dumps(item.payload, ensure_ascii=False, sort_keys=True) if hasattr(item, "payload") else ""
            item_chars = len(payload_text)
            if current_items and (len(current_items) >= max_items or current_chars + item_chars > GIT_REDUCTION_REDUCE_MAX_CHARS):
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
        chunk_id=f"git_commit_hierarchical:L{level}:C{items[0].unit_id}",
        level=level,
        task_type="git_commit",
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
        chunk_id=f"git_commit_hierarchical:L{level}:C{items[0].node_id}",
        level=level,
        task_type="git_commit",
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


def _build_git_commit_finalizer(*, include_body: bool):
    def finalizer(root_node, *, levels, profile):
        del levels, profile
        payload = dict(root_node.payload or {})
        subject = str(payload.get("candidate_subject") or payload.get("topic") or "").strip()
        if not subject:
            raise AiGitCommitError("分层压缩没有生成有效的提交标题")

        raw_body = payload.get("candidate_body")
        if include_body and isinstance(raw_body, list):
            body = normalize_commit_body_lines([str(item) for item in raw_body])
        else:
            body = []
        full_message = format_git_commit_message(subject, body)
        reason = str(payload.get("reason") or "").strip()
        return {
            "subject": subject,
            "body": body[:4],
            "full_message": full_message,
            "needs_split": bool(payload.get("should_split")),
            "reason": reason,
            "topic": str(payload.get("topic") or "").strip(),
            "summary": str(payload.get("summary") or "").strip(),
            "key_points": [str(item).strip() for item in (payload.get("key_points") or []) if str(item).strip()],
            "risk_points": [str(item).strip() for item in (payload.get("risk_points") or []) if str(item).strip()],
        }

    return finalizer
