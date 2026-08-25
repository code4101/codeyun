"""Codex CLI fallback for previously unseen activity quiz questions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

from backend.core.ai.chat import chat_with_provider


ACTIVITY_QUIZ_AI_MODEL = "gpt-5.3-codex-spark"
ACTIVITY_QUIZ_AI_CHOICE_LABELS = ("A", "B", "C", "D")
ACTIVITY_QUIZ_AI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "choice": {"type": "string", "enum": list(ACTIVITY_QUIZ_AI_CHOICE_LABELS)},
    },
    "required": ["choice"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ActivityQuizAiDecision:
    position: int
    choice: str
    answer: str
    model: str = ACTIVITY_QUIZ_AI_MODEL


def parse_activity_quiz_ai_decision(
    content: str,
    options: Sequence[str],
) -> ActivityQuizAiDecision:
    """Convert the model's A/B/C response directly to a visible option."""

    normalized_options = tuple(str(option or "").strip() for option in options)
    labels = ACTIVITY_QUIZ_AI_CHOICE_LABELS[:len(normalized_options)]
    if len(normalized_options) not in {3, 4} or not all(normalized_options):
        raise ValueError("活动答题 AI 需要三个或四个完整选项")
    payload = json.loads(str(content or "").strip())
    if not isinstance(payload, dict):
        raise ValueError("活动_答题 AI 未返回 JSON 对象")
    choice = str(payload.get("choice") or "").strip().upper()
    if choice not in labels:
        raise ValueError(f"活动答题 AI 未返回有效的 {'/'.join(labels)} 选项")
    position = labels.index(choice)
    return ActivityQuizAiDecision(
        position=position,
        choice=choice,
        answer=normalized_options[position],
    )


def request_activity_quiz_ai_decision(
    prompt: str,
    options: Sequence[str],
    *,
    timeout_seconds: float = 45.0,
) -> ActivityQuizAiDecision:
    """Ask only after all options exist, returning the smallest useful result."""

    normalized_options = tuple(str(option or "").strip() for option in options)
    labels = ACTIVITY_QUIZ_AI_CHOICE_LABELS[:len(normalized_options)]
    if len(normalized_options) not in {3, 4} or not all(normalized_options):
        raise ValueError("活动答题 AI 需要三个或四个完整选项")
    option_lines = "\n".join(
        f"{label}. {option}"
        for label, option in zip(labels, normalized_options, strict=True)
    )
    result = chat_with_provider(
        provider_id="codex-cli",
        model=ACTIVITY_QUIZ_AI_MODEL,
        messages=[{
            "role": "user",
            "content": f"题目：{str(prompt or '').strip()}\n{option_lines}",
        }],
        system_prompt=(
            "你是修仙题材游戏的单选题助手。根据题干和全部可见选项选择最可能的答案。"
            f"不要调用工具，不要解释，只返回结构化字段 choice（{'、'.join(labels)} 之一）。"
        ),
        response_format={
            **ACTIVITY_QUIZ_AI_RESPONSE_SCHEMA,
            "properties": {
                "choice": {"type": "string", "enum": list(labels)},
            },
        },
        timeout_seconds=max(1.0, float(timeout_seconds)),
    )
    return parse_activity_quiz_ai_decision(
        str(result.get("content") or ""),
        normalized_options,
    )


__all__ = [
    "ACTIVITY_QUIZ_AI_CHOICE_LABELS",
    "ACTIVITY_QUIZ_AI_MODEL",
    "ActivityQuizAiDecision",
    "parse_activity_quiz_ai_decision",
    "request_activity_quiz_ai_decision",
]
