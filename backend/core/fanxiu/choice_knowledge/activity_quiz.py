"""Latency-critical helpers for 活动_答题.

The hot path is intentionally pure and has no logging, database access, JSON
serialization, or evidence writes.  Callers may persist results only after the
click has already been sent.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np

from .model import ChoiceQuestion, choice_text_similarity
from .store import CONTEXT_ACTIVITY_QUIZ


OPTION_PANEL_ROI = (0.295, 0.630, 0.805, 0.785)
OPTION_CENTER_X_RATIO = 0.50
OPTION_CENTER_Y_RATIOS = (0.662, 0.710, 0.758)


@dataclass(frozen=True)
class ActivityQuizTarget:
    position: int
    answer: str
    reason: str
    score: float = 100.0


def option_panel_metrics(frame_data_url: str) -> tuple[float, float, float]:
    """Return stddev, edge energy, and dark ratio for the fixed option ROI."""

    encoded = str(frame_data_url or "").split(",", 1)[-1]
    frame = cv2.imdecode(
        np.frombuffer(base64.b64decode(encoded), dtype=np.uint8),
        cv2.IMREAD_GRAYSCALE,
    )
    if frame is None or frame.size == 0:
        return 0.0, 0.0, 0.0
    height, width = frame.shape[:2]
    left, top, right, bottom = OPTION_PANEL_ROI
    crop = frame[
        int(top * height):int(bottom * height),
        int(left * width):int(right * width),
    ]
    if crop.size == 0:
        return 0.0, 0.0, 0.0
    edge = float(
        np.abs(np.diff(crop.astype(np.float32), axis=1)).mean()
        + np.abs(np.diff(crop.astype(np.float32), axis=0)).mean()
    )
    return float(crop.std()), edge, float((crop < 130).mean())


def option_panel_visible(frame_data_url: str) -> bool:
    """Detect #432 buttons without OCR after #431 has armed the responder."""

    stddev, edge, dark_ratio = option_panel_metrics(frame_data_url)
    return stddev >= 15.0 and edge >= 2.0 and dark_ratio >= 0.004


def fixed_option_click_point(
    position: int,
    *,
    width: int = 900,
    height: int = 1600,
) -> tuple[float, float]:
    index = int(position)
    if index < 0 or index >= len(OPTION_CENTER_Y_RATIOS):
        raise ValueError("活动_答题选项位置必须是 0、1 或 2")
    return (
        OPTION_CENTER_X_RATIO * int(width),
        OPTION_CENTER_Y_RATIOS[index] * int(height),
    )


def resolve_activity_quiz_target(
    question: ChoiceQuestion | None,
    observed_options: Sequence[str] = (),
    *,
    option_match_threshold: float = 72.0,
) -> ActivityQuizTarget | None:
    """Resolve a safe click target, preferring the learned fixed position."""

    if question is None or not question.answer:
        return None
    context = question.context(CONTEXT_ACTIVITY_QUIZ)
    recommended = question.current_recommended_option
    if (
        context is not None
        and context.options_order_fixed
        and recommended is not None
        and recommended.position is not None
    ):
        return ActivityQuizTarget(
            position=int(recommended.position),
            answer=recommended.text,
            reason="fixed_position",
        )

    ranked = sorted(
        (
            (choice_text_similarity(question.answer, option), index, option)
            for index, option in enumerate(observed_options)
            if str(option or "").strip()
        ),
        reverse=True,
    )
    if not ranked:
        return None
    best_score, position, option = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score < option_match_threshold or (
        best_score < 100.0 and best_score - second_score < 8.0
    ):
        return None
    return ActivityQuizTarget(
        position=int(position),
        answer=str(option),
        reason="observed_option_match",
        score=float(best_score),
    )


__all__ = [
    "ActivityQuizTarget",
    "fixed_option_click_point",
    "option_panel_metrics",
    "option_panel_visible",
    "resolve_activity_quiz_target",
]
