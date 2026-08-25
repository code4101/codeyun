"""Import the current client CampAnswer config into shared quiz knowledge."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.models import FanxiuChoiceKnowledge

from .model import (
    ChoiceContext,
    ChoiceOption,
    ChoiceQuestion,
    choice_text_similarity,
    clean_ocr_choice_text,
    normalize_choice_text,
)
from .store import (
    CONTEXT_ACTIVITY_QUIZ,
    CONTEXT_ACTIVITY_QUIZ_FINAL,
    DOMAIN_QUIZ,
    INTERACTION_CHOICE_CLICK,
    OCR_DERIVED_ACTIVITY_SOURCES,
    apply_question_to_record,
)


REVERSE_SOURCE = "fanxiu_reverse_campanswer"
PROTECTED_GAME_TRUTH_SOURCES = (
    "activity_quiz_native",
    "activity_quiz_runtime",
    "activity_quiz_final_runtime",
    "activity_quiz_capture",
)


@dataclass
class CampAnswerImportStats:
    question_rows: int = 0
    option_rows: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    protected_conflicts: int = 0
    ignored_orphan_option_rows: int = 0
    merged_ocr_records: int = 0
    cleaned_ocr_records: int = 0
    source_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"凡修逆向配置不存在：{path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"凡修逆向配置必须是 rows 数组：{path}")
    return [dict(row) for row in value]


def _source_digest(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _has_protected_game_truth(question: ChoiceQuestion) -> bool:
    return any(
        option.status == 1
        and any(option.source.startswith(prefix) for prefix in PROTECTED_GAME_TRUTH_SOURCES)
        for option in question.options
    )


def _positive_text(question: ChoiceQuestion) -> str:
    positives = [option.text for option in question.options if option.status == 1]
    return positives[0] if len(positives) == 1 else ""


def _ensure_quiz_contexts(question: ChoiceQuestion) -> None:
    # Both clients consume the same CampAnswer table. Ordinary preserves config
    # order; the final client shuffles it every question, so never share the
    # fixed-position assumption across the two presentations.
    question.ensure_context(
        CONTEXT_ACTIVITY_QUIZ,
        interaction_mode=INTERACTION_CHOICE_CLICK,
        group_name="活动_答题",
        options_order_fixed=True,
    )
    question.ensure_context(
        CONTEXT_ACTIVITY_QUIZ_FINAL,
        interaction_mode=INTERACTION_CHOICE_CLICK,
        group_name="活动_答题决赛",
        options_order_fixed=False,
    )


def _is_ocr_record(record: FanxiuChoiceKnowledge) -> bool:
    return any(record.source.startswith(prefix) for prefix in OCR_DERIVED_ACTIVITY_SOURCES)


def _clean_ocr_record_options(record: FanxiuChoiceKnowledge) -> bool:
    changed = False
    cleaned_options: list[dict[str, Any]] = []
    for raw in record.options or []:
        option = ChoiceOption.from_value(raw)
        cleaned_text = clean_ocr_choice_text(option.text)
        cleaned_aliases = [
            cleaned
            for alias in option.aliases
            if (cleaned := clean_ocr_choice_text(alias)) and cleaned != cleaned_text
        ]
        if cleaned_text != option.text or cleaned_aliases != option.aliases:
            changed = True
        option.text = cleaned_text
        option.aliases = list(dict.fromkeys(cleaned_aliases))
        cleaned_options.append(option.to_dict())
    if changed:
        record.options = cleaned_options
        record.updated_at = time.time()
    return changed


def _authoritative_reverse_match(
    record: FanxiuChoiceKnowledge,
    authoritative: list[FanxiuChoiceKnowledge],
) -> FanxiuChoiceKnowledge | None:
    ranked = sorted(
        (
            (choice_text_similarity(record.prompt, candidate.prompt), candidate)
            for candidate in authoritative
            if choice_text_similarity(record.answer, candidate.answer) >= 90.0
        ),
        key=lambda value: value[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 78.0:
        return None
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if ranked[0][0] < 100.0 and ranked[0][0] - second_score < 8.0:
        return None
    return ranked[0][1]


def import_reverse_camp_answer_knowledge(
    session: Session,
    *,
    export_root: str | Path | None = None,
) -> CampAnswerImportStats:
    """Idempotently merge active client questions into the quiz knowledge table.

    Explicit correct/wrong feedback captured from a running game stays above
    reverse static data. AI guesses and legacy OCR knowledge may be corrected by
    the current client config. Orphan CampOptions groups are deliberately ignored.
    """

    root = resolve_fanxiu_export_root(export_root)
    question_path = root / "parsed_configs" / "CampAnswer" / "rows.json"
    option_path = root / "parsed_configs" / "CampOptions" / "rows.json"
    question_rows = _load_rows(question_path)
    option_rows = _load_rows(option_path)
    options_by_id = {int(row["id"]): row for row in option_rows}
    referenced_option_ids: set[int] = set()
    stats = CampAnswerImportStats(
        question_rows=len(question_rows),
        option_rows=len(option_rows),
        source_sha256=_source_digest(question_path, option_path),
    )
    static_updated_at = max(question_path.stat().st_mtime, option_path.stat().st_mtime)

    FanxiuChoiceKnowledge.__table__.create(session.get_bind(), checkfirst=True)
    reverse_records: list[FanxiuChoiceKnowledge] = []
    for order_index, row in enumerate(question_rows):
        prompt = str(row.get("question_plain") or row.get("question") or "").strip()
        normalized_prompt = normalize_choice_text(prompt)
        if not normalized_prompt:
            raise ValueError(f"CampAnswer #{row.get('id')} 缺少题干")
        option_ids = [int(value) for value in row.get("options") or []]
        answer_id = int(row.get("answer") or 0)
        referenced_option_ids.update(option_ids)
        if answer_id not in option_ids:
            raise ValueError(f"CampAnswer #{row.get('id')} 的答案不在选项中：{answer_id}")
        try:
            option_texts = [str(options_by_id[value]["options"]).strip() for value in option_ids]
        except KeyError as exc:
            raise ValueError(f"CampAnswer #{row.get('id')} 引用了缺失选项：{exc.args[0]}") from exc
        if not all(option_texts):
            raise ValueError(f"CampAnswer #{row.get('id')} 存在空选项")

        record = session.exec(
            select(FanxiuChoiceKnowledge).where(
                FanxiuChoiceKnowledge.domain == DOMAIN_QUIZ,
                FanxiuChoiceKnowledge.normalized_prompt == normalized_prompt,
            )
        ).first()
        is_new = record is None
        if record is None:
            record = FanxiuChoiceKnowledge(
                domain=DOMAIN_QUIZ,
                group_name="活动_答题",
                prompt=prompt,
                normalized_prompt=normalized_prompt,
                interaction_mode=INTERACTION_CHOICE_CLICK,
                contexts=[],
                options=[],
                options_complete=False,
                order_index=order_index,
                source=REVERSE_SOURCE,
            )

        before = (
            record.prompt,
            record.group_name,
            record.interaction_mode,
            record.contexts,
            record.options,
            record.options_complete,
            record.order_index,
            record.source,
        )
        question = ChoiceQuestion.from_record(record)
        _ensure_quiz_contexts(question)
        static_answer = option_texts[option_ids.index(answer_id)]
        protected_conflict = (
            _has_protected_game_truth(question)
            and normalize_choice_text(_positive_text(question))
            != normalize_choice_text(static_answer)
        )
        if protected_conflict:
            # The game has already shown a concrete result for this prompt. Keep
            # that evidence intact even if a stale client bundle disagrees.
            stats.protected_conflicts += 1
        else:
            question.prompt = prompt
            question.group_name = "活动_答题"
            question.interaction_mode = INTERACTION_CHOICE_CLICK
            question.options = [
                ChoiceOption(
                    text=text,
                    status=1 if option_id == answer_id else -1,
                    position=position,
                    source=REVERSE_SOURCE,
                    updated_at=static_updated_at,
                )
                for position, (option_id, text) in enumerate(zip(option_ids, option_texts))
            ]
            question.options_complete = True
            question.order_index = order_index
            question.source = REVERSE_SOURCE
        apply_question_to_record(question, record)
        after = (
            record.prompt,
            record.group_name,
            record.interaction_mode,
            record.contexts,
            record.options,
            record.options_complete,
            record.order_index,
            record.source,
        )
        if is_new:
            stats.inserted += 1
            session.add(record)
        elif before != after:
            stats.updated += 1
            session.add(record)
        else:
            stats.unchanged += 1
        if record.source == REVERSE_SOURCE:
            reverse_records.append(record)

    stats.ignored_orphan_option_rows = len(options_by_id) - len(referenced_option_ids)
    session.flush()
    reverse_ids = {record.id for record in reverse_records}
    for record in session.exec(
        select(FanxiuChoiceKnowledge).where(
            FanxiuChoiceKnowledge.domain == DOMAIN_QUIZ,
            FanxiuChoiceKnowledge.group_name == "活动_答题",
        )
    ).all():
        if record.id in reverse_ids or not _is_ocr_record(record):
            continue
        cleaned = _clean_ocr_record_options(record)
        matched = _authoritative_reverse_match(record, reverse_records)
        if matched is not None:
            session.delete(record)
            stats.merged_ocr_records += 1
        elif cleaned:
            session.add(record)
            stats.cleaned_ocr_records += 1
    session.commit()
    from .catalog import choice_knowledge_catalog

    choice_knowledge_catalog.reload(session)
    return stats


__all__ = [
    "CampAnswerImportStats",
    "PROTECTED_GAME_TRUTH_SOURCES",
    "REVERSE_SOURCE",
    "import_reverse_camp_answer_knowledge",
]
