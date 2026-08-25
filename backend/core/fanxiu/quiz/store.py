from __future__ import annotations

import time
from typing import Any

from sqlmodel import Session, select

from backend.core.fanxiu.choice_knowledge.store import (
    CONTEXT_LINGQUAN,
    DOMAIN_LINGQUAN,
    INTERACTION_TEXT_INPUT,
    ensure_choice_knowledge_seeds,
    match_choice_knowledge,
    normalize_choice_text,
    normalize_option,
)
from backend.models import FanxiuChoiceKnowledge


def normalize_question(text: str) -> str:
    return normalize_choice_text(text)


def serialize_question(item: FanxiuChoiceKnowledge) -> dict[str, Any]:
    return {
        "id": item.id,
        "group_name": item.group_name,
        "question": item.question,
        "answer": item.answer,
        "options": item.options,
        "options_complete": item.options_complete,
        "interaction_mode": item.interaction_mode,
        "contexts": item.contexts,
        "order_index": item.order_index,
        "source": item.source,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def ensure_lingquan_question_seed(session: Session) -> int:
    """Idempotently seed Lingquan into the shared choice knowledge table."""

    FanxiuChoiceKnowledge.__table__.create(session.get_bind(), checkfirst=True)
    def context_count() -> int:
        return sum(
            1
            for item in session.exec(
                select(FanxiuChoiceKnowledge).where(
                    FanxiuChoiceKnowledge.domain == DOMAIN_LINGQUAN
                )
            ).all()
            if any(
                str(context.get("key") or "") == CONTEXT_LINGQUAN
                for context in item.contexts or []
            )
        )

    before = context_count()
    ensure_choice_knowledge_seeds(session)
    after = context_count()
    return max(0, after - before)


def list_lingquan_questions(
    session: Session,
    *,
    query: str = "",
    group_name: str = "",
) -> list[FanxiuChoiceKnowledge]:
    ensure_lingquan_question_seed(session)
    statement = select(FanxiuChoiceKnowledge).where(
        FanxiuChoiceKnowledge.domain == DOMAIN_LINGQUAN
    )
    if group_name:
        statement = statement.where(FanxiuChoiceKnowledge.group_name == group_name)
    items = list(session.exec(statement).all())
    needle = normalize_question(query).lower()
    if needle:
        items = [
            item for item in items
            if needle in normalize_question(item.question).lower()
            or needle in normalize_question(item.answer).lower()
        ]
    return sorted(items, key=lambda item: (item.group_name, item.order_index, item.created_at))


def match_lingquan_question(session: Session, text: str) -> tuple[FanxiuChoiceKnowledge | None, float]:
    """Fuzzy-match OCR text while tolerating labels around the real question."""

    ensure_lingquan_question_seed(session)
    return match_choice_knowledge(
        session,
        domain=DOMAIN_LINGQUAN,
        observed_prompt=text,
    )


def match_lingquan_question_cached(text: str):
    """Match from the process-local catalog without touching the database."""

    from backend.core.fanxiu.choice_knowledge.catalog import (
        choice_knowledge_catalog,
    )

    return choice_knowledge_catalog.match(
        domain=DOMAIN_LINGQUAN,
        observed_prompt=text,
    )


def match_activity_quiz_question(
    session: Session,
    text: str,
) -> tuple[FanxiuChoiceKnowledge | None, float]:
    """Match 活动_答题 against the same canonical bank used by 灵泉."""

    ensure_lingquan_question_seed(session)
    return match_choice_knowledge(
        session,
        domain=DOMAIN_LINGQUAN,
        observed_prompt=text,
    )


def match_activity_quiz_question_cached(text: str):
    """Latency-sensitive 活动_答题 lookup from the shared memory catalog."""

    from backend.core.fanxiu.choice_knowledge.catalog import choice_knowledge_catalog

    return choice_knowledge_catalog.match(
        domain=DOMAIN_LINGQUAN,
        observed_prompt=text,
    )


def create_lingquan_question(session: Session, payload: dict[str, Any]) -> FanxiuChoiceKnowledge:
    question = str(payload.get("question") or "").strip()
    answer = str(payload.get("answer") or "").strip()
    if not question or not answer:
        raise ValueError("题目和答案不能为空")
    item = FanxiuChoiceKnowledge(
        domain=DOMAIN_LINGQUAN,
        group_name=str(payload.get("group_name") or "玩法知识").strip() or "玩法知识",
        prompt=question,
        normalized_prompt=normalize_question(question),
        interaction_mode=INTERACTION_TEXT_INPUT,
        contexts=[{
            "key": CONTEXT_LINGQUAN,
            "interaction_mode": INTERACTION_TEXT_INPUT,
            "group_name": str(payload.get("group_name") or "玩法知识").strip() or "玩法知识",
            "options_order_fixed": False,
        }],
        options=[
            normalize_option({
                "text": answer,
                "status": 1,
                "source": "manual",
            })
        ],
        options_complete=False,
        order_index=int(payload.get("order_index") or 0),
        source="manual",
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    from backend.core.fanxiu.choice_knowledge.catalog import choice_knowledge_catalog

    choice_knowledge_catalog.upsert(item)
    return item


def update_lingquan_question(
    session: Session,
    item: FanxiuChoiceKnowledge,
    payload: dict[str, Any],
) -> FanxiuChoiceKnowledge:
    if "question" in payload:
        item.prompt = str(payload.get("question") or "").strip()
        item.normalized_prompt = normalize_question(item.prompt)
    if "answer" in payload:
        answer = str(payload.get("answer") or "").strip()
        options = [normalize_option(option) for option in item.options]
        matched_index = next(
            (
                index
                for index, option in enumerate(options)
                if normalize_question(option.get("text")) == normalize_question(answer)
            ),
            None,
        )
        if matched_index is None and answer:
            for option in options:
                if int(option.get("status") or 0) == 1:
                    option["status"] = 0
            options.append(normalize_option({
                "text": answer,
                "status": 1,
                "source": "manual",
            }))
        elif matched_index is not None:
            for option in options:
                if int(option.get("status") or 0) == 1:
                    option["status"] = 0
            options[matched_index]["status"] = 1
        item.options = options
    if not item.prompt or not item.answer:
        raise ValueError("题目和答案不能为空")
    if "group_name" in payload:
        item.group_name = str(payload.get("group_name") or "玩法知识").strip() or "玩法知识"
    if "order_index" in payload:
        item.order_index = int(payload.get("order_index") or 0)
    item.updated_at = time.time()
    session.add(item)
    session.commit()
    session.refresh(item)
    from backend.core.fanxiu.choice_knowledge.catalog import choice_knowledge_catalog

    choice_knowledge_catalog.upsert(item)
    return item
