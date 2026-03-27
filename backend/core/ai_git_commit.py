from __future__ import annotations

import json
import re
from typing import Optional

from sqlmodel import Session

from backend.core.ai_chat import (
    AiProviderConfig,
    chat_with_provider,
    get_default_ai_provider_id,
)
from backend.core.ai_chat_user_config import (
    get_user_ai_chat_provider_runtime_config,
    list_user_ai_chat_custom_provider_configs,
)
from backend.core.git_tools import format_git_commit_message
from backend.models import User


class AiGitCommitError(RuntimeError):
    """Raised when the AI commit draft cannot be generated or parsed."""


def resolve_ai_runtime_config(
    *,
    session: Session,
    current_user: Optional[User],
    provider: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
) -> tuple[str, Optional[str], Optional[str], tuple[AiProviderConfig, ...]]:
    resolved_provider = (provider or get_default_ai_provider_id()).strip().lower() or get_default_ai_provider_id()
    resolved_base_url = (base_url or "").strip()
    resolved_api_key = (api_key or "").strip()
    extra_providers: tuple[AiProviderConfig, ...] = ()

    if current_user is not None:
        extra_providers = tuple(list_user_ai_chat_custom_provider_configs(session, current_user.id))
        saved_config = get_user_ai_chat_provider_runtime_config(session, current_user.id, resolved_provider)
        if not resolved_base_url:
            resolved_base_url = str(saved_config.get("base_url") or "").strip()
        if not resolved_api_key:
            resolved_api_key = str(saved_config.get("api_key") or "").strip()

    return (
        resolved_provider,
        resolved_base_url or None,
        resolved_api_key or None,
        extra_providers,
    )


def _build_system_prompt(style: str, include_body: bool) -> str:
    style_text = (
        "提交标题必须符合 Conventional Commits，格式为 type(scope): description 或 type: description，description 用中文。"
        if style == "conventional"
        else "提交标题必须是自然中文总结，不要使用 Conventional Commit 前缀。"
    )
    body_text = (
        "body 必须是 2 到 4 条中文短句数组，每条只描述一个关键变化，不要带项目符号前缀。"
        if include_body
        else "body 必须返回空数组。"
    )
    return (
        "你是一个严谨的 Git 提交信息助手。"
        "你只能输出 JSON，不要输出 Markdown、解释、代码块或额外文本。"
        "JSON 结构固定为："
        '{"subject":"", "body": [], "needs_split": false, "reason": ""}。'
        f"{style_text}"
        "subject 使用中文，尽量不超过 50 个汉字或等价长度。"
        f"{body_text}"
        "如果变更明显混杂、应该拆成多个提交，或上下文已经明确提示规模过大，则把 needs_split 设为 true，并在 reason 里简短说明。"
    )


def _build_user_prompt(
    *,
    context_text: str,
    style: str,
    include_body: bool,
) -> str:
    style_label = "Conventional Commit" if style == "conventional" else "中文总结"
    body_label = "需要正文" if include_body else "只需要标题"
    return (
        "请基于下面的 Git 变更上下文生成一次提交草稿。\n"
        f"- 风格: {style_label}\n"
        f"- 正文: {body_label}\n"
        "- 输出必须是 JSON\n\n"
        f"{context_text}"
    )


def _extract_json_payload(raw_content: str) -> dict[str, object]:
    content = raw_content.strip()
    if content.startswith("```"):
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if fence_match:
            content = fence_match.group(1).strip()

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\})", content, re.DOTALL)
        if not match:
            raise AiGitCommitError("AI 没有返回可解析的 JSON")
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise AiGitCommitError("AI 返回的 JSON 格式无效") from exc

    if not isinstance(parsed, dict):
        raise AiGitCommitError("AI 返回的提交结果不是 JSON 对象")
    return parsed


def _normalize_body_lines(value: object, *, include_body: bool) -> list[str]:
    if not include_body:
        return []
    if not isinstance(value, list):
        return []

    lines: list[str] = []
    for item in value:
        line = str(item or "").strip()
        if not line:
            continue
        if line.startswith(("-", "*", "•")):
            line = line[1:].strip()
        lines.append(line)
    return lines[:4]


def generate_ai_git_commit_draft(
    *,
    context_text: str,
    provider_id: str,
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    style: str,
    include_body: bool,
    force_split_reason: Optional[str] = None,
    extra_providers: tuple[AiProviderConfig, ...] = (),
) -> dict[str, object]:
    response = chat_with_provider(
        provider_id=provider_id,
        base_url=base_url,
        api_key=api_key,
        messages=[
            {
                "role": "user",
                "content": _build_user_prompt(
                    context_text=context_text,
                    style=style,
                    include_body=include_body,
                ),
            }
        ],
        model=(model or "").strip() or None,
        system_prompt=_build_system_prompt(style, include_body),
        temperature=0.2,
        extra_providers=extra_providers,
    )

    payload = _extract_json_payload(str(response.get("content") or ""))
    subject = str(payload.get("subject") or "").strip()
    if not subject:
        raise AiGitCommitError("AI 没有生成有效的提交标题")

    body = _normalize_body_lines(payload.get("body"), include_body=include_body)
    full_message = format_git_commit_message(subject, body)
    model_needs_split = bool(payload.get("needs_split"))
    final_reason = str(payload.get("reason") or "").strip()
    if force_split_reason and not final_reason:
        final_reason = force_split_reason
    return {
        "subject": subject,
        "body": body,
        "full_message": full_message,
        "needs_split": model_needs_split or bool(force_split_reason),
        "reason": final_reason,
        "model": str(response.get("model") or model or provider_id),
        "raw_content": str(response.get("content") or ""),
    }
