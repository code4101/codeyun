"""Business-neutral domain model for Fanxiu questions and event choices."""

from __future__ import annotations

import random
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Sequence

from rapidfuzz import fuzz


VALID_OPTION_STATUSES = {-1, 0, 1}


def clean_ocr_choice_text(text: Any) -> str:
    """Remove OCR punctuation while preserving answer letters and numbers."""

    return "".join(
        character
        for character in str(text or "").strip()
        if not unicodedata.category(character).startswith("P")
    ).strip()


def normalize_choice_text(text: Any) -> str:
    """Normalize OCR text without guessing damaged characters."""

    return re.sub(
        r"[\s:：,，。.!！?？\"'“”‘’《》【】（）()]+",
        "",
        str(text or ""),
    ).strip().lower()


def choice_text_similarity(observed: Any, expected: Any) -> float:
    """Return wrapper-tolerant text similarity from 0 to 100."""

    left = normalize_choice_text(observed)
    right = normalize_choice_text(expected)
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return 100.0
    return max(float(fuzz.ratio(left, right)), float(fuzz.partial_ratio(left, right)))


def _normalize_status(value: Any) -> int:
    try:
        status = int(value)
    except (TypeError, ValueError):
        return 0
    return status if status in VALID_OPTION_STATUSES else 0


@dataclass
class ChoiceOption:
    """One candidate answer and its mutable current judgment."""

    text: str
    status: int = 0
    position: int | None = None
    aliases: list[str] = field(default_factory=list)
    source: str = "observed"
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def from_value(
        cls,
        value: str | dict[str, Any] | "ChoiceOption",
        *,
        default_position: int | None = None,
        default_status: int = 0,
        source: str = "observed",
    ) -> "ChoiceOption":
        if isinstance(value, cls):
            return cls(**value.to_dict())
        raw = {"text": value} if isinstance(value, str) else dict(value or {})
        text = str(raw.get("text") or "").strip()
        position = raw.get("position", default_position)
        try:
            position = int(position) if position is not None else None
        except (TypeError, ValueError):
            position = default_position
        aliases = [
            str(alias).strip()
            for alias in raw.get("aliases") or []
            if str(alias).strip() and str(alias).strip() != text
        ]
        return cls(
            text=text,
            status=_normalize_status(raw.get("status", default_status)),
            position=position,
            aliases=list(dict.fromkeys(aliases)),
            source=str(raw.get("source") or source),
            updated_at=float(raw.get("updated_at") or time.time()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "aliases": list(self.aliases),
            "position": self.position,
            "status": self.status,
            "source": self.source,
            "updated_at": self.updated_at,
        }

    def match_score(self, observed_text: str) -> float:
        return max(
            (
                choice_text_similarity(observed_text, candidate)
                for candidate in [self.text, *self.aliases]
                if candidate
            ),
            default=0.0,
        )


@dataclass(frozen=True)
class ChoiceSelection:
    """The next visible option recommended by the generic algorithm."""

    text: str
    position: int
    status: int
    score: float
    reason: str


@dataclass(frozen=True)
class ChoiceContext:
    """One presentation of shared knowledge in a concrete game activity."""

    key: str
    interaction_mode: str
    group_name: str = ""
    options_order_fixed: bool = False

    @classmethod
    def from_value(cls, value: dict[str, Any] | "ChoiceContext") -> "ChoiceContext":
        if isinstance(value, cls):
            return value
        raw = dict(value or {})
        return cls(
            key=str(raw.get("key") or "").strip(),
            interaction_mode=str(raw.get("interaction_mode") or "choice_click").strip(),
            group_name=str(raw.get("group_name") or "").strip(),
            options_order_fixed=bool(raw.get("options_order_fixed", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "interaction_mode": self.interaction_mode,
            "group_name": self.group_name,
            "options_order_fixed": self.options_order_fixed,
        }


@dataclass
class ChoiceQuestion:
    """Extensible question/event object independent from OCR and result rules."""

    domain: str
    prompt: str
    options: list[ChoiceOption] = field(default_factory=list)
    id: str = ""
    group_name: str = ""
    interaction_mode: str = "choice_click"
    contexts: list[ChoiceContext] = field(default_factory=list)
    options_complete: bool = False
    order_index: int = 0
    source: str = "manual"

    @classmethod
    def from_record(cls, record: Any) -> "ChoiceQuestion":
        return cls(
            id=str(getattr(record, "id", "") or ""),
            domain=str(getattr(record, "domain", "") or ""),
            group_name=str(getattr(record, "group_name", "") or ""),
            prompt=str(getattr(record, "prompt", "") or ""),
            interaction_mode=str(
                getattr(record, "interaction_mode", "choice_click")
                or "choice_click"
            ),
            contexts=[
                ChoiceContext.from_value(context)
                for context in getattr(record, "contexts", []) or []
            ],
            options=[
                ChoiceOption.from_value(option)
                for option in getattr(record, "options", []) or []
            ],
            options_complete=bool(getattr(record, "options_complete", False)),
            order_index=int(getattr(record, "order_index", 0) or 0),
            source=str(getattr(record, "source", "manual") or "manual"),
        )

    @property
    def recommended_options(self) -> list[ChoiceOption]:
        """Return every option currently marked as a positive candidate."""

        return [option for option in self.options if option.status == 1]

    @property
    def question(self) -> str:
        """Legacy-friendly name for text-question callers."""

        return self.prompt

    @property
    def answer(self) -> str:
        """Return the first positive text answer for fill-in interaction."""

        positives = self.recommended_options
        return positives[0].text if positives else ""

    @property
    def current_recommended_option(self) -> ChoiceOption | None:
        """Return the sole positive option, otherwise no unique recommendation."""

        positives = self.recommended_options
        return positives[0] if len(positives) == 1 else None

    def to_options_payload(self) -> list[dict[str, Any]]:
        return [option.to_dict() for option in self.options]

    def to_contexts_payload(self) -> list[dict[str, Any]]:
        return [context.to_dict() for context in self.contexts]

    def context(self, key: str) -> ChoiceContext | None:
        normalized = str(key or "").strip()
        return next((item for item in self.contexts if item.key == normalized), None)

    def ensure_context(
        self,
        key: str,
        *,
        interaction_mode: str,
        group_name: str = "",
        options_order_fixed: bool = False,
    ) -> ChoiceContext:
        """Add or refresh one usage without duplicating the shared answer."""

        value = ChoiceContext(
            key=str(key or "").strip(),
            interaction_mode=str(interaction_mode or "choice_click").strip(),
            group_name=str(group_name or "").strip(),
            options_order_fixed=bool(options_order_fixed),
        )
        self.contexts = [item for item in self.contexts if item.key != value.key]
        self.contexts.append(value)
        return value

    def observe_options(
        self,
        observed_options: Sequence[str | dict[str, Any] | ChoiceOption],
        *,
        match_threshold: float = 72.0,
        observed_at: float | None = None,
    ) -> None:
        """Merge ordered visible options while preserving their current states."""

        now = float(observed_at or time.time())
        stored = [ChoiceOption.from_value(option) for option in self.options]
        merged: list[ChoiceOption] = []
        used_indexes: set[int] = set()

        for position, raw_observed in enumerate(observed_options):
            observed = ChoiceOption.from_value(
                raw_observed,
                default_position=position,
                source="observed",
            )
            if not observed.text:
                continue
            ranked = sorted(
                (
                    (candidate.match_score(observed.text), index)
                    for index, candidate in enumerate(stored)
                    if index not in used_indexes
                ),
                reverse=True,
            )
            best_score, best_index = ranked[0] if ranked else (0.0, -1)
            second_score = ranked[1][0] if len(ranked) > 1 else 0.0
            if best_score >= match_threshold and (
                best_score == 100.0 or best_score - second_score >= 8.0
            ):
                current = stored[best_index]
                used_indexes.add(best_index)
                old_text = current.text
                if old_text and old_text != observed.text:
                    current.aliases = list(dict.fromkeys([
                        *current.aliases,
                        old_text,
                    ]))
                current.text = observed.text
                current.aliases = [
                    alias for alias in current.aliases
                    if alias != current.text
                ]
                current.position = position
                current.updated_at = now
                merged.append(current)
            else:
                observed.updated_at = now
                merged.append(observed)

        for index, option in enumerate(stored):
            if index not in used_indexes:
                option.position = None
                merged.append(option)

        self.options = merged
        self.options_complete = bool(merged) and all(
            option.position is not None for option in merged
        )

    def _visible_by_status(
        self,
        visible: Sequence[ChoiceOption],
        status: int,
        match_threshold: float,
    ) -> list[tuple[ChoiceOption, ChoiceOption, float]]:
        candidates: list[tuple[ChoiceOption, ChoiceOption, float]] = []
        for stored in self.options:
            if stored.status != status:
                continue
            ranked = sorted(
                ((stored.match_score(item.text), item) for item in visible),
                key=lambda item: item[0],
                reverse=True,
            )
            if not ranked:
                continue
            best_score, best = ranked[0]
            second_score = ranked[1][0] if len(ranked) > 1 else 0.0
            if best_score >= match_threshold and (
                best_score == 100.0 or best_score - second_score >= 8.0
            ):
                candidates.append((stored, best, best_score))
        return candidates

    def recommend(
        self,
        observed_options: Sequence[str | dict[str, Any] | ChoiceOption],
        *,
        rng: random.Random | random.SystemRandom | None = None,
        match_threshold: float = 72.0,
    ) -> ChoiceSelection:
        """Choose from any current state and learn previously unseen options."""

        if not observed_options:
            raise ValueError("当前没有可选择的画面选项")
        self.observe_options(observed_options, match_threshold=match_threshold)
        normalized_visible = [
            ChoiceOption.from_value(option, default_position=index)
            for index, option in enumerate(observed_options)
        ]
        visible = sorted(
            [option for option in normalized_visible if option.text],
            key=lambda option: int(option.position or 0),
        )
        chooser = rng or random.SystemRandom()

        positives = self._visible_by_status(visible, 1, match_threshold)
        if positives:
            _stored, selected, score = chooser.choice(positives)
            return ChoiceSelection(
                selected.text,
                int(selected.position or 0),
                1,
                score,
                "positive_random",
            )

        unknowns = self._visible_by_status(visible, 0, match_threshold)
        if unknowns:
            _stored, selected, score = min(
                unknowns,
                key=lambda item: int(item[1].position or 0),
            )
            return ChoiceSelection(
                selected.text,
                int(selected.position or 0),
                0,
                score,
                "unknown_top_down",
            )

        negatives = self._visible_by_status(visible, -1, match_threshold)
        if negatives:
            _stored, selected, score = min(
                negatives,
                key=lambda item: int(item[1].position or 0),
            )
            return ChoiceSelection(
                selected.text,
                int(selected.position or 0),
                -1,
                score,
                "retry_negative_top_down",
            )

        selected = visible[0]
        return ChoiceSelection(
            selected.text,
            int(selected.position or 0),
            0,
            0.0,
            "new_observation_top_down",
        )

    def update_option_status(
        self,
        *,
        selected_text: str,
        selected_position: int | None,
        status: int,
        updated_at: float | None = None,
    ) -> ChoiceOption:
        """Overwrite one option's current status without retaining history."""

        normalized_status = _normalize_status(status)
        if int(status) not in VALID_OPTION_STATUSES:
            raise ValueError("选项状态必须是 -1、0 或 1")
        ranked = sorted(
            (
                (
                    max(
                        option.match_score(selected_text),
                        100.0
                        if selected_position is not None
                        and option.position == selected_position
                        else 0.0,
                    ),
                    index,
                )
                for index, option in enumerate(self.options)
            ),
            reverse=True,
        )
        if not ranked or ranked[0][0] < 72.0:
            raise ValueError("实际选择无法对应到当前选项")
        option = self.options[ranked[0][1]]
        option.status = normalized_status
        option.updated_at = float(updated_at or time.time())
        return option
