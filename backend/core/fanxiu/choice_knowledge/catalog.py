"""Process-local in-memory index for latency-sensitive Fanxiu answering."""

from __future__ import annotations

import threading
from typing import Iterable

from rapidfuzz import fuzz, process
from sqlmodel import Session, select

from backend.models import FanxiuChoiceKnowledge

from .model import ChoiceQuestion, normalize_choice_text


class ChoiceKnowledgeCatalog:
    """Small full-memory catalog rebuilt outside the timed answer path."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, ChoiceQuestion] = {}
        self._by_domain: dict[str, tuple[ChoiceQuestion, ...]] = {}
        self._search_by_domain: dict[
            str,
            tuple[tuple[ChoiceQuestion, str], ...],
        ] = {}
        self._loaded = False

    @property
    def loaded(self) -> bool:
        with self._lock:
            return self._loaded

    def replace(self, records: Iterable[FanxiuChoiceKnowledge]) -> int:
        items = {
            str(record.id): ChoiceQuestion.from_record(record)
            for record in records
        }
        by_domain: dict[str, list[ChoiceQuestion]] = {}
        for item in items.values():
            by_domain.setdefault(item.domain, []).append(item)
        with self._lock:
            self._items = items
            self._by_domain = {
                domain: tuple(sorted(
                    values,
                    key=lambda item: (
                        item.group_name,
                        item.order_index,
                        item.prompt,
                    ),
                ))
                for domain, values in by_domain.items()
            }
            self._search_by_domain = {
                domain: tuple(
                    (question, normalize_choice_text(question.prompt))
                    for question in questions
                )
                for domain, questions in self._by_domain.items()
            }
            self._loaded = True
        return len(items)

    def reload(self, session: Session) -> int:
        records = list(session.exec(select(FanxiuChoiceKnowledge)).all())
        return self.replace(records)

    def upsert(self, record: FanxiuChoiceKnowledge) -> None:
        question = ChoiceQuestion.from_record(record)
        with self._lock:
            if not self._loaded:
                return
            items = dict(self._items)
            items[str(record.id)] = question
        self.replace_records(items.values())

    def replace_records(self, questions: Iterable[ChoiceQuestion]) -> int:
        values = list(questions)
        by_domain: dict[str, list[ChoiceQuestion]] = {}
        for question in values:
            by_domain.setdefault(question.domain, []).append(question)
        with self._lock:
            self._items = {question.id: question for question in values}
            self._by_domain = {
                domain: tuple(sorted(
                    domain_items,
                    key=lambda item: (
                        item.group_name,
                        item.order_index,
                        item.prompt,
                    ),
                ))
                for domain, domain_items in by_domain.items()
            }
            self._search_by_domain = {
                domain: tuple(
                    (question, normalize_choice_text(question.prompt))
                    for question in questions
                )
                for domain, questions in self._by_domain.items()
            }
            self._loaded = True
        return len(values)

    def remove(self, knowledge_id: str) -> None:
        with self._lock:
            if not self._loaded:
                return
            items = dict(self._items)
            items.pop(str(knowledge_id), None)
        self.replace_records(items.values())

    def get(self, knowledge_id: str) -> ChoiceQuestion | None:
        with self._lock:
            return self._items.get(str(knowledge_id))

    def match(
        self,
        *,
        domain: str,
        observed_prompt: str,
        group_name: str = "",
    ) -> tuple[ChoiceQuestion | None, float]:
        normalized = normalize_choice_text(observed_prompt)
        with self._lock:
            indexed = tuple(
                item
                for item in self._search_by_domain.get(str(domain), ())
                if not group_name or item[0].group_name == group_name
            )
        if not normalized or not indexed:
            return None, 0.0
        matched = process.extractOne(
            normalized,
            [normalized_prompt for _item, normalized_prompt in indexed],
            scorer=lambda observed, expected, **_kwargs: max(
                float(fuzz.ratio(observed, expected)),
                float(fuzz.partial_ratio(observed, expected)),
            ),
        )
        if matched is None:
            return None, 0.0
        _prompt, score, index = matched
        return indexed[index][0], float(score)


choice_knowledge_catalog = ChoiceKnowledgeCatalog()


def load_choice_knowledge_catalog(session: Session) -> int:
    """Seed once, then build the complete in-memory lookup index."""

    from .store import ensure_choice_knowledge_seeds

    ensure_choice_knowledge_seeds(session)
    return choice_knowledge_catalog.reload(session)
