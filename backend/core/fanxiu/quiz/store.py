from __future__ import annotations

import re
import time
from typing import Any

from rapidfuzz import fuzz, process
from sqlmodel import Session, select

from backend.models import FanxiuLingquanQuestion

from .seed import LEGACY_LINGQUAN_QUESTION_GROUPS


def normalize_question(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def serialize_question(item: FanxiuLingquanQuestion) -> dict[str, Any]:
    return {
        "id": item.id,
        "group_name": item.group_name,
        "question": item.question,
        "answer": item.answer,
        "enabled": item.enabled,
        "order_index": item.order_index,
        "source": item.source,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def ensure_lingquan_question_seed(session: Session) -> int:
    """Perform the legacy import once, when the dedicated table is empty."""

    if session.exec(select(FanxiuLingquanQuestion.id).limit(1)).first() is not None:
        return 0
    existing: set[tuple[str, str]] = set()
    inserted = 0
    order_index = 0
    for group_name, questions in LEGACY_LINGQUAN_QUESTION_GROUPS.items():
        for question, answer in questions:
            normalized = normalize_question(question)
            if (group_name, normalized) not in existing:
                session.add(FanxiuLingquanQuestion(
                    group_name=group_name,
                    question=question,
                    normalized_question=normalized,
                    answer=answer,
                    enabled=True,
                    order_index=order_index,
                    source="legacy_migration",
                ))
                existing.add((group_name, normalized))
                inserted += 1
            order_index += 1
    if inserted:
        session.commit()
    return inserted


def list_lingquan_questions(
    session: Session,
    *,
    query: str = "",
    group_name: str = "",
    enabled_only: bool = False,
) -> list[FanxiuLingquanQuestion]:
    ensure_lingquan_question_seed(session)
    statement = select(FanxiuLingquanQuestion)
    if group_name:
        statement = statement.where(FanxiuLingquanQuestion.group_name == group_name)
    if enabled_only:
        statement = statement.where(FanxiuLingquanQuestion.enabled == True)  # noqa: E712
    items = list(session.exec(statement).all())
    needle = normalize_question(query).lower()
    if needle:
        items = [
            item for item in items
            if needle in normalize_question(item.question).lower()
            or needle in normalize_question(item.answer).lower()
        ]
    return sorted(items, key=lambda item: (item.group_name, item.order_index, item.created_at))


def match_lingquan_question(session: Session, text: str) -> tuple[FanxiuLingquanQuestion | None, float]:
    """Reuse the legacy RapidFuzz ratio matcher over enabled database rows."""

    normalized = normalize_question(text)
    items = list_lingquan_questions(session, enabled_only=True)
    if not normalized or not items:
        return None, 0.0
    matched = process.extractOne(normalized, [item.normalized_question for item in items], scorer=fuzz.ratio)
    if matched is None:
        return None, 0.0
    _question, score, index = matched
    return items[index], float(score)


def create_lingquan_question(session: Session, payload: dict[str, Any]) -> FanxiuLingquanQuestion:
    question = str(payload.get("question") or "").strip()
    answer = str(payload.get("answer") or "").strip()
    if not question or not answer:
        raise ValueError("题目和答案不能为空")
    item = FanxiuLingquanQuestion(
        group_name=str(payload.get("group_name") or "玩法知识").strip() or "玩法知识",
        question=question,
        normalized_question=normalize_question(question),
        answer=answer,
        enabled=bool(payload.get("enabled", True)),
        order_index=int(payload.get("order_index") or 0),
        source="manual",
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def update_lingquan_question(session: Session, item: FanxiuLingquanQuestion, payload: dict[str, Any]) -> FanxiuLingquanQuestion:
    if "question" in payload:
        item.question = str(payload.get("question") or "").strip()
        item.normalized_question = normalize_question(item.question)
    if "answer" in payload:
        item.answer = str(payload.get("answer") or "").strip()
    if not item.question or not item.answer:
        raise ValueError("题目和答案不能为空")
    if "group_name" in payload:
        item.group_name = str(payload.get("group_name") or "玩法知识").strip() or "玩法知识"
    if "enabled" in payload:
        item.enabled = bool(payload["enabled"])
    if "order_index" in payload:
        item.order_index = int(payload.get("order_index") or 0)
    item.updated_at = time.time()
    session.add(item)
    session.commit()
    session.refresh(item)
    return item
