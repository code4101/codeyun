"""Persistence adapter for the business-neutral Fanxiu choice model."""

from __future__ import annotations

import time
from typing import Any, Sequence

from rapidfuzz import fuzz, process
from sqlmodel import Session, select

from backend.models import FanxiuChoiceKnowledge

from .model import (
    ChoiceOption,
    ChoiceQuestion,
    ChoiceSelection,
    clean_ocr_choice_text,
    choice_text_similarity,
    normalize_choice_text,
)
from .seed import (
    ACTIVITY_QUIZ_OBSERVED_QUESTIONS,
    LILIAN_EVENT_OBSERVED_OPTIONS,
    LILIAN_EVENT_RECOMMENDED_ANSWERS,
)


DOMAIN_QUIZ = "quiz"
# Compatibility name for callers whose presentation is still called 灵泉.
DOMAIN_LINGQUAN = DOMAIN_QUIZ
DOMAIN_LILIAN_EVENT = "lilian_event"
CONTEXT_LINGQUAN = "lingquan"
CONTEXT_ACTIVITY_QUIZ = "activity_quiz"
CONTEXT_ACTIVITY_QUIZ_FINAL = "activity_quiz_final"
INTERACTION_TEXT_INPUT = "text_input"
INTERACTION_CHOICE_CLICK = "choice_click"
OCR_DERIVED_ACTIVITY_SOURCES = (
    "activity_quiz_runtime",
    "activity_quiz_final_runtime",
    "activity_quiz_capture",
    "activity_quiz_ai",
)
AUTHORITATIVE_ACTIVITY_SOURCES = (
    "activity_quiz_native",
    "fanxiu_reverse_campanswer",
)


def _is_ocr_derived_activity_source(source: str) -> bool:
    return any(str(source or "").startswith(prefix) for prefix in OCR_DERIVED_ACTIVITY_SOURCES)


def _activity_option(
    value: str | dict[str, Any] | ChoiceOption,
    *,
    position: int,
    source: str,
) -> ChoiceOption:
    option = ChoiceOption.from_value(value, default_position=position)
    if _is_ocr_derived_activity_source(source):
        option.text = clean_ocr_choice_text(option.text)
        option.aliases = [
            cleaned
            for alias in option.aliases
            if (cleaned := clean_ocr_choice_text(alias)) and cleaned != option.text
        ]
    return option


def _find_authoritative_ocr_match(
    session: Session,
    *,
    prompt: str,
    answer: str,
    group_name: str,
) -> FanxiuChoiceKnowledge | None:
    candidates = [
        item
        for item in session.exec(
            select(FanxiuChoiceKnowledge).where(
                FanxiuChoiceKnowledge.domain == DOMAIN_QUIZ,
                FanxiuChoiceKnowledge.group_name == group_name,
            )
        ).all()
        if _is_ocr_derived_activity_source(item.source)
        and choice_text_similarity(answer, item.answer) >= 90.0
    ]
    ranked = sorted(
        ((choice_text_similarity(prompt, item.prompt), item) for item in candidates),
        key=lambda value: value[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 78.0:
        return None
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if ranked[0][0] < 100.0 and ranked[0][0] - second_score < 8.0:
        return None
    return ranked[0][1]


def normalize_option(
    option: str | dict[str, Any] | ChoiceOption,
    *,
    default_position: int | None = None,
    default_status: int = 0,
    source: str = "observed",
) -> dict[str, Any]:
    """Compatibility helper returning the persisted option dictionary."""

    return ChoiceOption.from_value(
        option,
        default_position=default_position,
        default_status=default_status,
        source=source,
    ).to_dict()


def question_from_record(record: FanxiuChoiceKnowledge) -> ChoiceQuestion:
    return ChoiceQuestion.from_record(record)


def apply_question_to_record(
    question: ChoiceQuestion,
    record: FanxiuChoiceKnowledge,
) -> FanxiuChoiceKnowledge:
    record.domain = question.domain
    record.group_name = question.group_name
    record.prompt = question.prompt
    record.normalized_prompt = normalize_choice_text(question.prompt)
    record.interaction_mode = question.interaction_mode
    record.contexts = question.to_contexts_payload()
    record.options = question.to_options_payload()
    record.options_complete = question.options_complete
    record.order_index = question.order_index
    record.source = question.source
    record.updated_at = time.time()
    return record


def observe_options(
    knowledge: FanxiuChoiceKnowledge,
    observed_options: Sequence[str | dict[str, Any] | ChoiceOption],
    *,
    match_threshold: float = 72.0,
    observed_at: float | None = None,
) -> FanxiuChoiceKnowledge:
    question = question_from_record(knowledge)
    question.observe_options(
        observed_options,
        match_threshold=match_threshold,
        observed_at=observed_at,
    )
    return apply_question_to_record(question, knowledge)


def select_observed_option(
    knowledge: FanxiuChoiceKnowledge,
    observed_options: Sequence[str | dict[str, Any] | ChoiceOption],
    **kwargs: Any,
) -> ChoiceSelection:
    question = question_from_record(knowledge)
    selection = question.recommend(observed_options, **kwargs)
    apply_question_to_record(question, knowledge)
    return selection


def match_choice_knowledge(
    session: Session,
    *,
    domain: str,
    observed_prompt: str,
    group_name: str = "",
) -> tuple[FanxiuChoiceKnowledge | None, float]:
    statement = select(FanxiuChoiceKnowledge).where(
        FanxiuChoiceKnowledge.domain == domain
    )
    if group_name:
        statement = statement.where(FanxiuChoiceKnowledge.group_name == group_name)
    items = list(session.exec(statement).all())
    normalized = normalize_choice_text(observed_prompt)
    if not normalized or not items:
        return None, 0.0
    matched = process.extractOne(
        normalized,
        [normalize_choice_text(item.prompt) for item in items],
        scorer=lambda observed, expected, **_kwargs: max(
            float(fuzz.ratio(observed, expected)),
            float(fuzz.partial_ratio(observed, expected)),
        ),
    )
    if matched is None:
        return None, 0.0
    _prompt, score, index = matched
    return items[index], float(score)


def update_choice_status(
    session: Session,
    knowledge: FanxiuChoiceKnowledge,
    *,
    observed_options: Sequence[str | dict[str, Any] | ChoiceOption],
    selected_text: str,
    selected_position: int | None,
    status: int,
) -> FanxiuChoiceKnowledge:
    """Merge current options, overwrite one status, and save current state."""

    question = question_from_record(knowledge)
    question.observe_options(observed_options)
    question.update_option_status(
        selected_text=selected_text,
        selected_position=selected_position,
        status=status,
    )
    apply_question_to_record(question, knowledge)
    session.add(knowledge)
    session.commit()
    session.refresh(knowledge)
    from .catalog import choice_knowledge_catalog

    choice_knowledge_catalog.upsert(knowledge)
    return knowledge


record_choice_outcome = update_choice_status


def update_exclusive_choice_outcome(
    session: Session,
    knowledge: FanxiuChoiceKnowledge,
    *,
    observed_options: Sequence[str | dict[str, Any] | ChoiceOption],
    selected_text: str,
    selected_position: int | None,
    success: bool,
) -> FanxiuChoiceKnowledge:
    """Persist one result for a question that has exactly one correct option.

    A failed attempt only invalidates the selected option.  A confirmed
    success also invalidates every other observed option because the business
    rule guarantees a unique correct answer.
    """

    question = question_from_record(knowledge)
    question.observe_options(observed_options)
    selected = question.update_option_status(
        selected_text=selected_text,
        selected_position=selected_position,
        status=1 if success else -1,
    )
    if success:
        now = time.time()
        for option in question.options:
            if option is selected:
                continue
            option.status = -1
            option.updated_at = now
    apply_question_to_record(question, knowledge)
    session.add(knowledge)
    session.commit()
    session.refresh(knowledge)
    from .catalog import choice_knowledge_catalog

    choice_knowledge_catalog.upsert(knowledge)
    return knowledge


def upsert_activity_quiz_result(
    session: Session,
    *,
    observed_prompt: str,
    observed_options: Sequence[str | dict[str, Any] | ChoiceOption],
    correct_position: int,
    knowledge_id: str = "",
    context_key: str = CONTEXT_ACTIVITY_QUIZ,
    group_name: str = "活动_答题",
    options_order_fixed: bool = True,
    source: str = "activity_quiz_runtime",
) -> FanxiuChoiceKnowledge:
    """Persist one 活动_答题 result without fuzzy-merging distinct prompts.

    Timed recognition may pass an id only when #431 already produced a
    reliable match.  Otherwise persistence uses exact normalized identity;
    fuzzy similarity is deliberately forbidden here because questions sharing
    names such as 韩立/黄枫谷 can still be different facts.
    """

    prompt = str(observed_prompt or "").strip()
    if not prompt:
        raise ValueError("活动_答题题目不能为空")
    options = []
    for index, option in enumerate(observed_options):
        normalized_option = _activity_option(option, position=index, source=source)
        if normalized_option.text:
            options.append(normalized_option)
    if not options:
        raise ValueError("活动_答题选项不能为空")
    position = int(correct_position)
    if position < 0 or position >= len(options):
        raise ValueError("活动_答题正确选项位置越界")

    knowledge = session.get(FanxiuChoiceKnowledge, str(knowledge_id)) if knowledge_id else None
    if knowledge is not None and choice_text_similarity(prompt, knowledge.prompt) < 82.0:
        knowledge = None
    normalized = normalize_choice_text(prompt)
    if knowledge is None:
        knowledge = session.exec(
            select(FanxiuChoiceKnowledge).where(
                FanxiuChoiceKnowledge.domain == DOMAIN_QUIZ,
                FanxiuChoiceKnowledge.normalized_prompt == normalized,
            )
        ).first()
    authoritative = any(source.startswith(prefix) for prefix in AUTHORITATIVE_ACTIVITY_SOURCES)
    if knowledge is None and authoritative:
        knowledge = _find_authoritative_ocr_match(
            session,
            prompt=prompt,
            answer=options[position].text,
            group_name=group_name,
        )
    if knowledge is None:
        knowledge = FanxiuChoiceKnowledge(
            domain=DOMAIN_QUIZ,
            group_name=group_name,
            prompt=prompt,
            normalized_prompt=normalized,
            interaction_mode=INTERACTION_CHOICE_CLICK,
            contexts=[],
            options=[],
            options_complete=False,
            source=source,
        )

    question = question_from_record(knowledge)
    if authoritative:
        question.prompt = prompt
        question.group_name = group_name
        question.interaction_mode = INTERACTION_CHOICE_CLICK
        question.options = []
        question.source = source
    question.ensure_context(
        context_key,
        interaction_mode=INTERACTION_CHOICE_CLICK,
        group_name=group_name,
        options_order_fixed=options_order_fixed,
    )
    question.observe_options(options)
    now = time.time()
    for option in question.options:
        if option.position is None:
            continue
        option.status = 1 if int(option.position) == position else -1
        option.source = source
        option.updated_at = now
    question.options_complete = True
    apply_question_to_record(question, knowledge)
    session.add(knowledge)
    session.commit()
    session.refresh(knowledge)
    from .catalog import choice_knowledge_catalog

    choice_knowledge_catalog.upsert(knowledge)
    return knowledge


def upsert_activity_quiz_final_result(
    session: Session,
    *,
    observed_prompt: str,
    observed_options: Sequence[str | dict[str, Any] | ChoiceOption],
    correct_position: int,
    knowledge_id: str = "",
) -> FanxiuChoiceKnowledge:
    """Persist #61 truth while explicitly keeping final-option order variable."""

    return upsert_activity_quiz_result(
        session,
        observed_prompt=observed_prompt,
        observed_options=observed_options,
        correct_position=correct_position,
        knowledge_id=knowledge_id,
        context_key=CONTEXT_ACTIVITY_QUIZ_FINAL,
        group_name="活动_答题决赛",
        options_order_fixed=False,
        source="activity_quiz_final_runtime",
    )


def upsert_activity_quiz_ai_guess(
    session: Session,
    *,
    observed_prompt: str,
    observed_options: Sequence[str | dict[str, Any] | ChoiceOption],
    selected_position: int,
    model: str,
) -> FanxiuChoiceKnowledge:
    """Persist a tentative AI guess without overriding authoritative evidence."""

    prompt = str(observed_prompt or "").strip()
    if not prompt:
        raise ValueError("活动_答题题目不能为空")
    options = [
        _activity_option(option, position=index, source="activity_quiz_ai")
        for index, option in enumerate(observed_options)
    ]
    options = [option for option in options if option.text]
    position = int(selected_position)
    if position < 0 or position >= len(options):
        raise ValueError("活动_答题 AI 选项位置越界")

    normalized = normalize_choice_text(prompt)
    knowledge = session.exec(
        select(FanxiuChoiceKnowledge).where(
            FanxiuChoiceKnowledge.domain == DOMAIN_QUIZ,
            FanxiuChoiceKnowledge.normalized_prompt == normalized,
        )
    ).first()
    if knowledge is None:
        knowledge = FanxiuChoiceKnowledge(
            domain=DOMAIN_QUIZ,
            group_name="活动_答题",
            prompt=prompt,
            normalized_prompt=normalized,
            interaction_mode=INTERACTION_CHOICE_CLICK,
            contexts=[],
            options=[],
            options_complete=False,
            source="activity_quiz_ai",
        )

    question = question_from_record(knowledge)
    authoritative = next(
        (
            option
            for option in question.options
            if option.status == 1 and not option.source.startswith("activity_quiz_ai")
        ),
        None,
    )
    if authoritative is not None:
        return knowledge

    question.ensure_context(
        CONTEXT_ACTIVITY_QUIZ,
        interaction_mode=INTERACTION_CHOICE_CLICK,
        group_name="活动_答题",
        options_order_fixed=True,
    )
    question.observe_options(options)
    now = time.time()
    source = f"activity_quiz_ai:{str(model or '').strip()}".rstrip(":")
    for option in question.options:
        if option.position is None:
            continue
        option.status = 1 if int(option.position) == position else 0
        option.source = source if option.status == 1 else "activity_quiz_observed"
        option.updated_at = now
    question.options_complete = True
    question.source = "activity_quiz_ai"
    apply_question_to_record(question, knowledge)
    session.add(knowledge)
    session.commit()
    session.refresh(knowledge)
    from .catalog import choice_knowledge_catalog

    choice_knowledge_catalog.upsert(knowledge)
    return knowledge


def ensure_choice_knowledge_seeds(session: Session) -> int:
    """Seed Lingquan answers and Lilian recommendations idempotently."""

    from backend.core.fanxiu.quiz.seed import LEGACY_LINGQUAN_QUESTION_GROUPS

    FanxiuChoiceKnowledge.__table__.create(session.get_bind(), checkfirst=True)
    inserted = 0
    observed_seed_updated = False
    order_index = 0
    for group_name, questions in LEGACY_LINGQUAN_QUESTION_GROUPS.items():
        for prompt, answer in questions:
            normalized = normalize_choice_text(prompt)
            existing = session.exec(
                select(FanxiuChoiceKnowledge).where(
                    FanxiuChoiceKnowledge.domain == DOMAIN_LINGQUAN,
                    FanxiuChoiceKnowledge.group_name == group_name,
                    FanxiuChoiceKnowledge.normalized_prompt == normalized,
                )
            ).first()
            if existing is None:
                existing = FanxiuChoiceKnowledge(
                    domain=DOMAIN_LINGQUAN,
                    group_name=group_name,
                    prompt=prompt,
                    normalized_prompt=normalized,
                    interaction_mode=INTERACTION_TEXT_INPUT,
                    contexts=[{
                        "key": CONTEXT_LINGQUAN,
                        "interaction_mode": INTERACTION_TEXT_INPUT,
                        "group_name": group_name,
                        "options_order_fixed": False,
                    }],
                    options=[normalize_option({
                        "text": answer,
                        "status": 1,
                        "source": "legacy_migration",
                    })],
                    options_complete=False,
                    order_index=order_index,
                    source="legacy_migration",
                )
                session.add(existing)
                inserted += 1
            else:
                question = question_from_record(existing)
                if question.context(CONTEXT_LINGQUAN) is None:
                    question.ensure_context(
                        CONTEXT_LINGQUAN,
                        interaction_mode=INTERACTION_TEXT_INPUT,
                        group_name=group_name,
                    )
                    apply_question_to_record(question, existing)
                    session.add(existing)
                    observed_seed_updated = True
            order_index += 1

    for order_index, (prompt, recommended) in enumerate(
        LILIAN_EVENT_RECOMMENDED_ANSWERS
    ):
        normalized = normalize_choice_text(prompt)
        existing = session.exec(
            select(FanxiuChoiceKnowledge).where(
                FanxiuChoiceKnowledge.domain == DOMAIN_LILIAN_EVENT,
                FanxiuChoiceKnowledge.group_name == "",
                FanxiuChoiceKnowledge.normalized_prompt == normalized,
            )
        ).first()
        if existing is None:
            existing = FanxiuChoiceKnowledge(
                domain=DOMAIN_LILIAN_EVENT,
                prompt=prompt,
                normalized_prompt=normalized,
                interaction_mode=INTERACTION_CHOICE_CLICK,
                contexts=[{
                    "key": DOMAIN_LILIAN_EVENT,
                    "interaction_mode": INTERACTION_CHOICE_CLICK,
                    "group_name": "",
                    "options_order_fixed": True,
                }],
                options=[normalize_option({
                    "text": recommended,
                    "status": 1,
                    "source": "user_note",
                })],
                options_complete=False,
                order_index=order_index,
                source="user_note",
            )
            session.add(existing)
            inserted += 1
        lilian_question = question_from_record(existing)
        if lilian_question.context(DOMAIN_LILIAN_EVENT) is None:
            lilian_question.ensure_context(
                DOMAIN_LILIAN_EVENT,
                interaction_mode=INTERACTION_CHOICE_CLICK,
                options_order_fixed=True,
            )
            apply_question_to_record(lilian_question, existing)
            session.add(existing)
            observed_seed_updated = True
        observed_options = LILIAN_EVENT_OBSERVED_OPTIONS.get(prompt)
        if observed_options:
            question = question_from_record(existing)
            current_visible = tuple(
                option.text
                for option in sorted(
                    (
                        option
                        for option in question.options
                        if option.position is not None
                    ),
                    key=lambda option: int(option.position or 0),
                )
            )
            if not question.options_complete or current_visible != observed_options:
                question.observe_options(observed_options)
                apply_question_to_record(question, existing)
                session.add(existing)
                observed_seed_updated = True

    for order_index, (prompt, observed_options, answer) in enumerate(
        ACTIVITY_QUIZ_OBSERVED_QUESTIONS
    ):
        normalized = normalize_choice_text(prompt)
        existing = session.exec(
            select(FanxiuChoiceKnowledge).where(
                FanxiuChoiceKnowledge.domain == DOMAIN_QUIZ,
                FanxiuChoiceKnowledge.normalized_prompt == normalized,
            )
        ).first()
        is_new = existing is None
        if is_new:
            existing = FanxiuChoiceKnowledge(
                domain=DOMAIN_QUIZ,
                group_name="活动_答题",
                prompt=prompt,
                normalized_prompt=normalized,
                interaction_mode=INTERACTION_CHOICE_CLICK,
                contexts=[],
                options=[],
                options_complete=False,
                order_index=order_index,
                source="activity_quiz_capture",
            )
            session.add(existing)
            inserted += 1
        question = question_from_record(existing)
        # Seed only the first activity observation.  Later runtime evidence is
        # authoritative and must not be reset on every process startup.
        activity_context = question.context(CONTEXT_ACTIVITY_QUIZ)
        if is_new or activity_context is None:
            question.ensure_context(
                CONTEXT_ACTIVITY_QUIZ,
                interaction_mode=INTERACTION_CHOICE_CLICK,
                group_name="活动_答题",
                options_order_fixed=True,
            )
            question.observe_options(observed_options)
            for option in question.options:
                option.status = (
                    1
                    if normalize_choice_text(option.text) == normalize_choice_text(answer)
                    else -1
                )
                option.source = "activity_quiz_capture"
            question.options_complete = True
            apply_question_to_record(question, existing)
            session.add(existing)
            observed_seed_updated = True
        elif (
            activity_context.group_name != "活动_答题"
            or not activity_context.options_order_fixed
        ):
            question.ensure_context(
                CONTEXT_ACTIVITY_QUIZ,
                interaction_mode=INTERACTION_CHOICE_CLICK,
                group_name="活动_答题",
                options_order_fixed=True,
            )
            if existing.group_name == "活动答题":
                question.group_name = "活动_答题"
            apply_question_to_record(question, existing)
            session.add(existing)
            observed_seed_updated = True
    if inserted or observed_seed_updated:
        session.commit()
    return inserted


__all__ = [
    "ChoiceOption",
    "CONTEXT_ACTIVITY_QUIZ",
    "CONTEXT_ACTIVITY_QUIZ_FINAL",
    "CONTEXT_LINGQUAN",
    "ChoiceQuestion",
    "ChoiceSelection",
    "DOMAIN_LILIAN_EVENT",
    "DOMAIN_LINGQUAN",
    "DOMAIN_QUIZ",
    "INTERACTION_CHOICE_CLICK",
    "INTERACTION_TEXT_INPUT",
    "apply_question_to_record",
    "choice_text_similarity",
    "ensure_choice_knowledge_seeds",
    "match_choice_knowledge",
    "normalize_choice_text",
    "normalize_option",
    "observe_options",
    "question_from_record",
    "record_choice_outcome",
    "select_observed_option",
    "update_exclusive_choice_outcome",
    "update_choice_status",
    "upsert_activity_quiz_final_result",
    "upsert_activity_quiz_result",
    "upsert_activity_quiz_ai_guess",
]
