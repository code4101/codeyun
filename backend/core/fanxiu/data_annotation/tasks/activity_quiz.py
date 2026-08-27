"""Latency-sensitive, idempotent 15-question job for 活动_答题.

This is a daily one-shot activity.  Agents must read
``docs/domains/fanxiu/jobs/凡修活动答题作业.md`` before starting it: an explicit user command is
required, and heavyweight screenshots/diagnostics are forbidden while the
countdown is running.
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from sqlmodel import Session

from backend.core.fanxiu.choice_knowledge.activity_quiz import (
    ActivityQuizTarget,
    fixed_option_click_point,
    option_panel_visible,
    resolve_activity_quiz_target,
)
from backend.core.fanxiu.choice_knowledge.activity_quiz_ai import (
    ACTIVITY_QUIZ_AI_MODEL,
    ActivityQuizAiDecision,
    request_activity_quiz_ai_decision,
)
from backend.core.fanxiu.choice_knowledge.model import (
    ChoiceQuestion,
    choice_text_similarity,
)
from backend.core.fanxiu.choice_knowledge.store import (
    upsert_activity_quiz_ai_guess,
    upsert_activity_quiz_result,
)
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.instrumentation.service import (
    fanxiu_instrumentation_service,
)
from backend.core.fanxiu.quiz.store import match_activity_quiz_question_cached
from backend.db import engine


TOTAL_QUESTIONS = 15
BATCH_SIZE = 5
QUESTION_SCENE_ID = 431
OPTION_SCENE_ID = 432
START_SCENE_ID = 430
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
RESULT_MARKERS = ("正确", "错误", "答对", "答错")
AI_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fanxiu-activity-quiz-ai")


@dataclass
class ActivityQuizQuestionState:
    number: int
    total: int
    prompt: str
    matched_id: str = ""
    match_score: float = 0.0
    target_position: int | None = None
    target_source: str = ""
    native_config_id: int | None = None
    native_prompt: str = ""
    native_options: list[str] = field(default_factory=list)
    observed_options: list[str] = field(default_factory=list)
    clicked: bool = False
    click_source: str = ""
    settled: bool = False
    correct_position: int | None = None
    needs_ai: bool = False
    ai_future: Future[ActivityQuizAiDecision] | None = None
    ai_decision: ActivityQuizAiDecision | None = None
    ai_error: str = ""
    ai_retired: bool = False
    ai_persisting: bool = False
    ai_persisted: bool = False
    ai_discarded: bool = False
    ai_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def batch(self) -> int:
        return (self.number - 1) // BATCH_SIZE + 1


@dataclass
class ActivityQuizRunState:
    """Monotonic question state; result pages can never re-arm a question."""

    total: int = TOTAL_QUESTIONS
    current: ActivityQuizQuestionState | None = None
    highest_number: int = 0
    completed_numbers: set[int] = field(default_factory=set)
    missed_result_numbers: set[int] = field(default_factory=set)
    retired_questions: list[ActivityQuizQuestionState] = field(default_factory=list)
    questions: list[ActivityQuizQuestionState] = field(default_factory=list)
    answer_sources: dict[int, str] = field(default_factory=dict)

    def observe_question(
        self,
        number: int,
        total: int,
        prompt: str,
    ) -> ActivityQuizQuestionState | None:
        number = int(number)
        total = int(total)
        prompt = str(prompt or "").strip()
        if total != self.total or not (1 <= number <= self.total) or not prompt:
            return None
        if number in self.completed_numbers:
            return None
        if self.current is not None and number == self.current.number:
            if not self.current.prompt and prompt:
                self.current.prompt = prompt
            return self.current
        if number <= self.highest_number:
            return None
        if self.current is not None and not self.current.settled:
            self.missed_result_numbers.add(self.current.number)
        if self.current is not None:
            self.current.ai_retired = True
            self.retired_questions.append(self.current)
        self.current = ActivityQuizQuestionState(number, total, prompt)
        self.questions.append(self.current)
        self.highest_number = number
        return self.current

    def mark_clicked(self, number: int, source: str = "knowledge") -> bool:
        if self.current is None or self.current.number != int(number):
            return False
        if self.current.clicked or self.current.settled:
            return False
        self.current.clicked = True
        self.current.click_source = str(source or "knowledge")
        return True

    def settle(self, number: int, correct_position: int) -> bool:
        if self.current is None or self.current.number != int(number):
            return False
        if self.current.settled or int(number) in self.completed_numbers:
            return False
        self.current.correct_position = int(correct_position)
        self.current.settled = True
        self.current.ai_retired = True
        self.completed_numbers.add(int(number))
        self.answer_sources[int(number)] = self.current.click_source or "external"
        self.missed_result_numbers.discard(int(number))
        return True

    def close_batch_without_result(self) -> None:
        if self.current is None or self.current.settled:
            return
        if self.current.number % BATCH_SIZE == 0:
            self.missed_result_numbers.add(self.current.number)

    @property
    def finished(self) -> bool:
        return self.total in self.completed_numbers


def parse_question_number(text: str) -> tuple[int, int] | None:
    """Read the first two integers; slash and surrounding OCR are irrelevant."""

    numbers = parse_ocr_values(str(text or "").translate(FULLWIDTH_DIGITS))
    if numbers is None or len(numbers) < 2:
        return None
    return numbers[0], numbers[1]


def _token_text(tokens: Iterable[dict[str, Any]]) -> str:
    values = sorted(
        (dict(token) for token in tokens if isinstance(token, dict)),
        key=lambda token: (float(token.get("y") or 0), float(token.get("x") or 0)),
    )
    return "".join(str(token.get("text") or "").strip() for token in values)


def _clean_option_text(text: str) -> str:
    value = str(text or "")
    for marker in RESULT_MARKERS:
        value = value.replace(marker, "")
    return value.replace("√", "").replace("×", "").strip(" ：:、")


def parse_option_rows(tokens: Iterable[dict[str, Any]]) -> tuple[list[str], int | None]:
    """Map OCR tokens to the three fixed rows and locate the marked answer."""

    rows: list[list[dict[str, Any]]] = [[], [], []]
    centers = (1059.0, 1136.0, 1213.0)
    for raw in tokens:
        if not isinstance(raw, dict):
            continue
        token = dict(raw)
        cy = float(token.get("y") or 0) + float(token.get("h") or 0) / 2
        position = min(range(3), key=lambda index: abs(cy - centers[index]))
        if abs(cy - centers[position]) <= 55:
            rows[position].append(token)

    options: list[str] = []
    correct_position: int | None = None
    for position, row in enumerate(rows):
        raw_text = _token_text(row)
        options.append(_clean_option_text(raw_text))
        if "正确" in raw_text or "答对" in raw_text or "√" in raw_text:
            correct_position = position
    return options, correct_position


def _ocr_tokens(runtime: Any, scene_id: int, shapes: tuple[str, ...], frame: str) -> list[dict[str, Any]]:
    return runtime.ocr_tokens_in_shapes(
        scene_id,
        shapes,
        padding=8,
        frame_data_url=frame,
        crop=True,
    )


def _read_question(runtime: Any, frame: str) -> tuple[int, int, str] | None:
    number_text = _token_text(_ocr_tokens(runtime, QUESTION_SCENE_ID, ("编号",), frame))
    number = parse_question_number(number_text)
    if number is None:
        return None
    prompt = _token_text(_ocr_tokens(runtime, QUESTION_SCENE_ID, ("题目",), frame))
    prompt = re.sub(r"^(题目|阅读题目)[:：]?", "", prompt).strip()
    return number[0], number[1], prompt


def _start_button_visible(runtime: Any, frame: str) -> bool:
    text = _token_text(_ocr_tokens(runtime, START_SCENE_ID, ("开始",), frame))
    return "开始" in text


def _resolve_known_target(question: ActivityQuizQuestionState, threshold: float) -> ChoiceQuestion | None:
    matched, score = match_activity_quiz_question_cached(question.prompt)
    if matched is None or float(score) < float(threshold):
        return None
    question.matched_id = matched.id
    question.match_score = float(score)
    target = resolve_activity_quiz_target(matched)
    if target is not None:
        question.target_position = target.position
        question.target_source = "knowledge"
    return matched


def _native_question_record(
    snapshot: dict[str, Any],
    number: int,
) -> dict[str, Any] | None:
    questions = snapshot.get("questions")
    if not isinstance(questions, list):
        return None
    return next(
        (
            item
            for item in questions
            if isinstance(item, dict) and item.get("index") == int(number)
        ),
        None,
    )


def _remember_native_question(
    question: ActivityQuizQuestionState,
    snapshot: dict[str, Any],
) -> None:
    record = _native_question_record(snapshot, question.number)
    if record is None or record.get("config_id") != question.native_config_id:
        return
    options = sorted(
        (dict(item) for item in record.get("options") or [] if isinstance(item, dict)),
        key=lambda item: int(item.get("position") or 0),
    )
    texts = [str(item.get("text") or "").strip() for item in options]
    prompt = str(record.get("question") or "").strip()
    if prompt and len(texts) == 3 and all(texts):
        question.native_prompt = prompt
        question.native_options = texts


def resolve_activity_quiz_native_target(
    snapshot: dict[str, Any],
    number: int,
    prompt: str,
    *,
    prompt_match_threshold: float = 82.0,
) -> tuple[ActivityQuizTarget | None, int | None]:
    """Resolve one OCR question against the game-native 15-question plan."""

    if not snapshot.get("available") or not snapshot.get("fresh"):
        return None, None
    record = _native_question_record(snapshot, number)
    if record is None:
        return None, None
    native_prompt = str(record.get("question") or "").strip()
    score = choice_text_similarity(prompt, native_prompt)
    position = record.get("correct_position")
    if (
        not native_prompt
        or score < prompt_match_threshold
        or not isinstance(position, int)
        or not 0 <= position < 3
    ):
        return None, None
    return (
        ActivityQuizTarget(
            position=position,
            answer=str(record.get("answer") or ""),
            reason="native_config_plan",
            score=float(score),
        ),
        int(record["config_id"]),
    )


def _persist_results(results: list[ActivityQuizQuestionState]) -> None:
    if not results:
        return
    with Session(engine) as session:
        for question in results:
            if question.correct_position is None or not any(question.observed_options):
                continue
            native_available = bool(question.native_prompt and question.native_options)
            upsert_activity_quiz_result(
                session,
                observed_prompt=question.native_prompt if native_available else question.prompt,
                observed_options=question.native_options if native_available else question.observed_options,
                correct_position=question.correct_position,
                knowledge_id=question.matched_id,
                source="activity_quiz_native" if native_available else "activity_quiz_runtime",
            )
    results.clear()


def _persist_ai_guess_once(question: ActivityQuizQuestionState) -> bool:
    with question.ai_lock:
        decision = question.ai_decision
        if (
            decision is None
            or question.settled
            or question.ai_persisting
            or question.ai_persisted
        ):
            return False
        question.ai_persisting = True
    try:
        with Session(engine) as session:
            upsert_activity_quiz_ai_guess(
                session,
                observed_prompt=question.prompt,
                observed_options=question.observed_options,
                selected_position=decision.position,
                model=decision.model,
            )
    except Exception as exc:  # noqa: BLE001 - AI fallback must not fail the Job
        with question.ai_lock:
            question.ai_persisting = False
            question.ai_error = str(exc)
        return False
    with question.ai_lock:
        question.ai_persisting = False
        question.ai_persisted = True
    return True


def _publish_ai_decision(
    question: ActivityQuizQuestionState,
    future: Future[ActivityQuizAiDecision],
) -> None:
    try:
        decision = future.result()
    except Exception as exc:  # noqa: BLE001 - background failures are non-fatal
        with question.ai_lock:
            question.ai_error = str(exc)
        return
    with question.ai_lock:
        if question.settled:
            question.ai_discarded = True
            return
        question.ai_decision = decision
        should_persist = question.ai_retired
    if should_persist:
        _persist_ai_guess_once(question)


def _start_ai_request(
    question: ActivityQuizQuestionState,
    *,
    timeout_seconds: float,
) -> bool:
    with question.ai_lock:
        if question.ai_future is not None or question.settled:
            return False
        if len(question.observed_options) != 3 or not all(question.observed_options):
            return False
        future = AI_EXECUTOR.submit(
            request_activity_quiz_ai_decision,
            question.prompt,
            tuple(question.observed_options),
            timeout_seconds=timeout_seconds,
        )
        question.ai_future = future
    future.add_done_callback(lambda item: _publish_ai_decision(question, item))
    return True


def _claim_ai_decision(question: ActivityQuizQuestionState) -> ActivityQuizAiDecision | None:
    with question.ai_lock:
        if question.settled or question.clicked or question.ai_retired:
            return None
        return question.ai_decision


def execute_activity_quiz_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> dict[str, Any]:
    """Answer 1..15 in one Cell, persisting only between five-question batches."""

    asset_tree_path = ctx.get("asset_tree_path")
    if not isinstance(asset_tree_path, Path):
        raise RuntimeError("活动_答题：缺少资产树路径")
    runtime = runner._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
    state = ActivityQuizRunState()
    pending_results: list[ActivityQuizQuestionState] = []
    deadline = time.monotonic() + float(payload.get("max_runtime_seconds") or 240.0)
    match_threshold = float(payload.get("match_score_threshold") or 82.0)
    native_max_age = float(payload.get("native_snapshot_max_age_seconds", 2.0))
    native_prompt_threshold = float(
        payload.get("native_prompt_match_threshold", 82.0)
    )
    ai_timeout_seconds = float(payload.get("ai_timeout_seconds") or 45.0)
    start_needed = True
    native_snapshot_reads = 0
    native_hits = 0
    log = getattr(runner, "_log", lambda _kind, _message: None)

    while time.monotonic() < deadline:
        if stop_event.is_set():
            raise InterruptedError("活动_答题：Cell 已停止")
        # A simulator restart invalidates cached Lua addresses.  Warm the
        # read-only CampAnswer question-plan snapshot before #431 appears, so
        # the cold scan does not consume the first three-second reading phase.
        if state.current is None:
            fanxiu_instrumentation_service.camp_answer_snapshot(
                max_age_seconds=native_max_age
            )
        frame = runtime.cur_frame(update=True)

        # After a click, the only latency-sensitive fact is the marked result.
        # Do not spend another two OCR calls rereading the same number/prompt.
        current = state.current
        if (
            current is not None
            and current.clicked
            and not current.settled
            and option_panel_visible(frame)
        ):
            option_tokens = _ocr_tokens(runtime, OPTION_SCENE_ID, ("选项",), frame)
            options, correct_position = parse_option_rows(option_tokens)
            if any(options):
                current.observed_options = options
            if correct_position is not None and state.settle(current.number, correct_position):
                log(
                    "success",
                    f"活动_答题：第{current.number}题确认游戏真值，来源={state.answer_sources[current.number]}",
                )
                pending_results.append(current)
                if current.number % BATCH_SIZE == 0:
                    start_needed = current.number < TOTAL_QUESTIONS
                if state.finished:
                    break
            continue

        # Critical fast path: #431 already read and matched the question during
        # the three-second reading phase.  As soon as the option pixels appear,
        # click the cached fixed position before doing any more question/number
        # OCR.  Repeating those two OCR calls here previously consumed much of
        # the ten-second answer window and forced the user to answer manually.
        if current is not None and not current.clicked and option_panel_visible(frame):
            if current.target_position is None:
                native_snapshot = fanxiu_instrumentation_service.camp_answer_snapshot(
                    max_age_seconds=native_max_age
                )
                native_snapshot_reads += 1
                native_target, config_id = resolve_activity_quiz_native_target(
                    native_snapshot,
                    current.number,
                    current.prompt,
                    prompt_match_threshold=native_prompt_threshold,
                )
                if native_target is not None:
                    current.target_position = native_target.position
                    current.target_source = "native"
                    current.native_config_id = config_id
                    _remember_native_question(current, native_snapshot)
                    current.needs_ai = False
                    native_hits += 1
                    log(
                        "info",
                        f"活动_答题：第{current.number}题动态插桩命中"
                        f" configId={config_id}，正确位置={native_target.position + 1}",
                    )
            if current.target_position is not None:
                if state.mark_clicked(
                    current.number,
                    current.target_source or "knowledge",
                ):
                    width = int(runtime.view(OPTION_SCENE_ID).raw.get("width") or 900)
                    height = int(runtime.view(OPTION_SCENE_ID).raw.get("height") or 1600)
                    x, y = fixed_option_click_point(
                        current.target_position,
                        width=width,
                        height=height,
                    )
                    runtime.click_frame_point_fast(OPTION_SCENE_ID, x, y)
                continue

            if not all(current.observed_options):
                option_tokens = _ocr_tokens(runtime, OPTION_SCENE_ID, ("选项",), frame)
                options, correct_position = parse_option_rows(option_tokens)
                if any(options):
                    current.observed_options = options
                if correct_position is not None:
                    if state.settle(current.number, correct_position):
                        log(
                            "success",
                            f"活动_答题：第{current.number}题确认游戏真值，来源=external",
                        )
                        pending_results.append(current)
                        if current.number % BATCH_SIZE == 0:
                            start_needed = current.number < TOTAL_QUESTIONS
                        if state.finished:
                            break
                    continue

            matched, score = match_activity_quiz_question_cached(current.prompt)
            if matched is not None and float(score) >= match_threshold:
                target = resolve_activity_quiz_target(matched, current.observed_options)
                if target is not None and state.mark_clicked(current.number, "knowledge"):
                    width = int(runtime.view(OPTION_SCENE_ID).raw.get("width") or 900)
                    height = int(runtime.view(OPTION_SCENE_ID).raw.get("height") or 1600)
                    x, y = fixed_option_click_point(target.position, width=width, height=height)
                    runtime.click_frame_point_fast(OPTION_SCENE_ID, x, y)
                    continue

            # Reading time is used only to decide whether the local bank knows
            # the question.  An unknown-question request cannot be useful until
            # the three visible choices exist, so submit the smallest A/B/C job
            # here and keep polling frames without waiting for its response.
            if current.needs_ai and all(current.observed_options):
                started = _start_ai_request(
                    current,
                    timeout_seconds=ai_timeout_seconds,
                )
                if started:
                    log(
                        "info",
                        f"活动_答题：第{current.number}题选项已出现，异步请求 {ACTIVITY_QUIZ_AI_MODEL} 返回 A/B/C",
                    )

            ai_decision = _claim_ai_decision(current)
            if ai_decision is not None and state.mark_clicked(current.number, "ai"):
                width = int(runtime.view(OPTION_SCENE_ID).raw.get("width") or 900)
                height = int(runtime.view(OPTION_SCENE_ID).raw.get("height") or 1600)
                x, y = fixed_option_click_point(
                    ai_decision.position,
                    width=width,
                    height=height,
                )
                runtime.click_frame_point_fast(OPTION_SCENE_ID, x, y)
                log(
                    "action",
                    f"活动_答题：第{current.number}题采用 AI 暂定选项 {ai_decision.position + 1}",
                )
            continue

        question_data = _read_question(runtime, frame)
        if question_data is not None:
            number, total, prompt = question_data
            question = state.observe_question(number, total, prompt)
            for retired in state.retired_questions:
                _persist_ai_guess_once(retired)
            if question is not None and not question.clicked and not question.settled:
                native_snapshot = fanxiu_instrumentation_service.camp_answer_snapshot(
                    max_age_seconds=native_max_age
                )
                native_snapshot_reads += 1
                native_target, config_id = resolve_activity_quiz_native_target(
                    native_snapshot,
                    question.number,
                    question.prompt,
                    prompt_match_threshold=native_prompt_threshold,
                )
                if native_target is not None:
                    if question.target_source != "native":
                        native_hits += 1
                        log(
                            "info",
                            f"活动_答题：第{question.number}题动态插桩命中"
                            f" configId={config_id}，正确位置={native_target.position + 1}",
                        )
                    question.target_position = native_target.position
                    question.target_source = "native"
                    question.native_config_id = config_id
                    _remember_native_question(question, native_snapshot)
                    matched = None
                else:
                    matched = _resolve_known_target(question, match_threshold)
                # Known/unknown can be decided during the three-second reading
                # phase, but the AI call deliberately waits for visible choices.
                # Known questions never spend a model request.
                question.needs_ai = (
                    question.target_position is None
                    and (matched is None or not matched.answer)
                )
                continue

        missed_batch_transition = bool(
            state.current is not None
            and state.current.clicked
            and not state.current.settled
            and state.current.number % BATCH_SIZE == 0
            and state.current.number < TOTAL_QUESTIONS
        )
        if (start_needed or missed_batch_transition) and _start_button_visible(runtime, frame):
            state.close_batch_without_result()
            _persist_results(pending_results)
            runtime.click_shape_center(START_SCENE_ID, "开始")
            start_needed = False
            continue

        if state.finished:
            break

    _persist_results(pending_results)
    if state.current is not None and not state.current.settled:
        state.current.ai_retired = True
        _persist_ai_guess_once(state.current)
    if not state.finished:
        missing = sorted(state.missed_result_numbers)
        suffix = f"；未采集结果题号 {missing}" if missing else ""
        raise TimeoutError(f"活动_答题：未确认第15题结果{suffix}")
    if state.missed_result_numbers:
        raise RuntimeError(
            f"活动_答题：15题已结束，但结果采集不完整：{sorted(state.missed_result_numbers)}"
        )
    ai_requests = sum(question.ai_future is not None for question in state.questions)
    ai_errors = sum(bool(question.ai_error) for question in state.questions)
    ai_pending = sum(
        question.ai_future is not None and not question.ai_future.done()
        for question in state.questions
    )
    return {
        "result": "success",
        "message": "活动_答题：15题完成，三批题目与答案已更新",
        "answered": len(state.completed_numbers),
        "batches": 3,
        "answer_sources": {
            "native": sum(source == "native" for source in state.answer_sources.values()),
            "knowledge": sum(source == "knowledge" for source in state.answer_sources.values()),
            "ai": sum(source == "ai" for source in state.answer_sources.values()),
            "external": sum(source == "external" for source in state.answer_sources.values()),
        },
        "ai_requests": ai_requests,
        "ai_errors": ai_errors,
        "ai_pending": ai_pending,
        "ai_guesses_persisted": sum(question.ai_persisted for question in state.questions),
        "ai_results_discarded": sum(question.ai_discarded for question in state.questions),
        "native_snapshot_reads": native_snapshot_reads,
        "native_hits": native_hits,
    }


__all__ = [
    "ActivityQuizQuestionState",
    "ActivityQuizRunState",
    "execute_activity_quiz_task",
    "parse_option_rows",
    "parse_question_number",
    "resolve_activity_quiz_native_target",
]
