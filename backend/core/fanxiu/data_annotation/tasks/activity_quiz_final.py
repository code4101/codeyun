"""Prepared core-loop responder for 活动_答题决赛 scene #61.

The contest entry and result pages are intentionally unknown.  This Job only
waits for the already annotated core scene, answers consecutive questions, and
stops after the scene becomes idle or disappears.  It must never invent
navigation around #61.
"""

from __future__ import annotations

import base64
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import cv2
import numpy as np
from sqlmodel import Session

from backend.core.fanxiu.choice_knowledge.model import (
    ChoiceQuestion,
    choice_text_similarity,
    normalize_choice_text,
)
from backend.core.fanxiu.choice_knowledge.activity_quiz_ai import (
    ACTIVITY_QUIZ_AI_MODEL,
    ActivityQuizAiDecision,
    request_activity_quiz_ai_decision,
)
from backend.core.fanxiu.choice_knowledge.store import (
    upsert_activity_quiz_ai_guess,
    upsert_activity_quiz_final_result,
)
from backend.core.fanxiu.quiz.store import match_activity_quiz_question_cached
from backend.core.fanxiu.instrumentation.service import (
    fanxiu_instrumentation_service,
)
from backend.db import engine


FINAL_SCENE_ID = 61
FINAL_OPTION_COUNT = 4
FINAL_AI_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="fanxiu-activity-quiz-final-ai",
)


@dataclass(frozen=True)
class FinalQuizOption:
    text: str
    x: float
    y: float


@dataclass
class FinalQuizQuestionState:
    prompt: str
    options: tuple[FinalQuizOption, ...]
    matched_id: str = ""
    match_score: float = 0.0
    clicked: bool = False
    click_source: str = ""
    settled: bool = False
    correct_position: int | None = None
    persisted: bool = False
    persistence_error: str = ""
    needs_ai_hint: bool = False
    ai_future: Future[ActivityQuizAiDecision] | None = None
    ai_decision: ActivityQuizAiDecision | None = None
    ai_error: str = ""
    ai_retired: bool = False
    ai_persisted: bool = False
    ai_discarded: bool = False
    last_hint_at: float | None = None
    hint_click_count: int = 0
    hint_suppressed: bool = False
    fallback_started: bool = False
    native_rejection_logged: bool = False
    native_quest_id: int | None = None
    native_answer_id: int | None = None
    ai_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    started_at: float = field(default_factory=time.monotonic)
    clicked_at: float | None = None


def _token_center(token: dict[str, Any]) -> tuple[float, float]:
    return (
        float(token.get("x") or 0) + float(token.get("w") or 0) / 2,
        float(token.get("y") or 0) + float(token.get("h") or 0) / 2,
    )


def _join_tokens(tokens: Iterable[dict[str, Any]]) -> str:
    return "".join(
        str(token.get("text") or "").strip()
        for token in sorted(
            (dict(token) for token in tokens if isinstance(token, dict)),
            key=lambda token: (_token_center(token)[1], _token_center(token)[0]),
        )
    )


def parse_final_quiz_options(
    tokens: Iterable[dict[str, Any]],
    *,
    expected_count: int = FINAL_OPTION_COUNT,
) -> tuple[FinalQuizOption, ...]:
    """Group OCR tokens into visual rows and retain each row's real center.

    FINAL RULE: option order/positions are not assumed fixed.  Every question
    must resolve answer text against these freshly observed rows and click the
    corresponding center from the current frame.
    """

    rows: list[list[dict[str, Any]]] = []
    for raw in sorted(
        (dict(token) for token in tokens if isinstance(token, dict)),
        key=lambda token: (_token_center(token)[1], _token_center(token)[0]),
    ):
        _cx, cy = _token_center(raw)
        if not rows:
            rows.append([raw])
            continue
        previous_centers = [_token_center(token)[1] for token in rows[-1]]
        tolerance = max(24.0, float(raw.get("h") or 0) * 0.8)
        if abs(cy - sum(previous_centers) / len(previous_centers)) <= tolerance:
            rows[-1].append(raw)
        else:
            rows.append([raw])

    parsed: list[FinalQuizOption] = []
    for row in rows:
        text = re.sub(r"^[A-DＡ-Ｄ][.、:：]?", "", _join_tokens(row)).strip()
        if not text:
            continue
        left = min(float(token.get("x") or 0) for token in row)
        top = min(float(token.get("y") or 0) for token in row)
        right = max(float(token.get("x") or 0) + float(token.get("w") or 0) for token in row)
        bottom = max(float(token.get("y") or 0) + float(token.get("h") or 0) for token in row)
        parsed.append(FinalQuizOption(text=text, x=(left + right) / 2, y=(top + bottom) / 2))
    return tuple(parsed) if len(parsed) == int(expected_count) else ()


def resolve_final_quiz_target(
    question: ChoiceQuestion | None,
    options: Sequence[FinalQuizOption],
    *,
    option_match_threshold: float = 72.0,
) -> int | None:
    """Resolve by current option text only; saved/fixed positions are forbidden."""

    if question is None:
        return None
    return resolve_final_quiz_answer_target(
        question.answer,
        options,
        option_match_threshold=option_match_threshold,
    )


def resolve_final_quiz_answer_target(
    answer: str,
    options: Sequence[FinalQuizOption],
    *,
    option_match_threshold: float = 72.0,
) -> int | None:
    """Map an answer text to one unique row observed in the current frame."""

    if not answer:
        return None
    ranked = sorted(
        (
            (choice_text_similarity(answer, option.text), index)
            for index, option in enumerate(options)
            if option.text
        ),
        reverse=True,
    )
    if not ranked:
        return None
    best_score, position = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score < option_match_threshold or (
        best_score < 100.0 and best_score - second_score < 8.0
    ):
        return None
    return int(position)


def resolve_final_quiz_native_target(
    snapshot: dict[str, Any],
    prompt: str,
    options: Sequence[FinalQuizOption],
    *,
    prompt_match_threshold: float = 82.0,
) -> tuple[int | None, str]:
    """Validate a native answer against the visible question before clicking."""

    if not snapshot.get("available") or not snapshot.get("fresh"):
        return None, str(snapshot.get("reason") or "Runtime 快照不可用")
    native_prompt = str(snapshot.get("question") or "").strip()
    prompt_score = choice_text_similarity(prompt, native_prompt)
    if not native_prompt or prompt_score < prompt_match_threshold:
        return None, f"题面不一致({prompt_score:.1f})"
    native_options = snapshot.get("options")
    answer_id = snapshot.get("correct_option_id")
    if not isinstance(native_options, list) or len(native_options) != FINAL_OPTION_COUNT:
        return None, "Runtime 选项不完整"
    if not any(
        isinstance(item, dict) and item.get("id") == answer_id
        for item in native_options
    ):
        return None, "Runtime 正确选项 ID 不在本题选项中"
    answer = str(snapshot.get("correct_answer") or "").strip()
    position = resolve_final_quiz_answer_target(answer, options)
    if position is None:
        return None, "Runtime 答案无法唯一映射到当前选项行"
    return position, ""


def is_authoritative_final_quiz_question(question: ChoiceQuestion | None) -> bool:
    """Exclude every AI-tentative answer from final-round click authority."""

    if question is None or not question.answer:
        return False
    recommended = question.current_recommended_option
    if recommended is None:
        return False
    return not (
        str(question.source or "").startswith("activity_quiz_ai")
        or str(recommended.source or "").startswith("activity_quiz_ai")
    )


def detect_final_quiz_correct_position(
    frame_data_url: str,
    options: Sequence[FinalQuizOption],
) -> int | None:
    """Locate the game's green result mark beside a current option row."""

    encoded = str(frame_data_url or "").split(",", 1)[-1]
    try:
        frame = cv2.imdecode(
            np.frombuffer(base64.b64decode(encoded), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
    except Exception:  # noqa: BLE001 - an unusable frame is simply no result
        return None
    if frame is None or frame.size == 0:
        return None
    height, width = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    left = int(width * 0.58)
    right = int(width * 0.80)
    half_height = max(24, int(height * 0.028))
    scores: list[float] = []
    for option in options:
        top = max(0, int(option.y) - half_height)
        bottom = min(height, int(option.y) + half_height)
        crop = hsv[top:bottom, left:right]
        if crop.size == 0:
            scores.append(0.0)
            continue
        green = cv2.inRange(crop, (35, 35, 25), (100, 255, 230))
        scores.append(float((green > 0).mean()))
    if not scores:
        return None
    position = int(np.argmax(scores))
    best = scores[position]
    second = sorted(scores, reverse=True)[1] if len(scores) > 1 else 0.0
    if best < 0.01 or best - second < 0.008:
        return None
    return position


def _same_question(left: str, right: str) -> bool:
    left_normalized = normalize_choice_text(left)
    right_normalized = normalize_choice_text(right)
    if not left_normalized or not right_normalized:
        return False
    return choice_text_similarity(left_normalized, right_normalized) >= 92.0


def _persist_confirmed_answer(question: FinalQuizQuestionState) -> bool:
    if question.persisted or question.correct_position is None:
        return question.persisted
    try:
        with Session(engine) as session:
            upsert_activity_quiz_final_result(
                session,
                observed_prompt=question.prompt,
                observed_options=[option.text for option in question.options],
                correct_position=question.correct_position,
                knowledge_id=question.matched_id,
            )
    except Exception as exc:  # noqa: BLE001 - persistence cannot stop live answering
        question.persistence_error = str(exc)
        return False
    question.persisted = True
    return True


def _persist_final_ai_guess_once(question: FinalQuizQuestionState) -> bool:
    """Save an unconfirmed hint only when no game truth superseded it."""

    with question.ai_lock:
        decision = question.ai_decision
        if decision is None or question.settled or question.ai_persisted:
            return False
        question.ai_persisted = True
    try:
        with Session(engine) as session:
            upsert_activity_quiz_ai_guess(
                session,
                observed_prompt=question.prompt,
                observed_options=[option.text for option in question.options],
                selected_position=decision.position,
                model=decision.model,
            )
    except Exception as exc:  # noqa: BLE001 - hint persistence cannot stop the loop
        with question.ai_lock:
            question.ai_persisted = False
            question.ai_error = str(exc)
        return False
    return True


def _publish_final_ai_decision(
    question: FinalQuizQuestionState,
    future: Future[ActivityQuizAiDecision],
) -> None:
    try:
        decision = future.result()
    except Exception as exc:  # noqa: BLE001 - a failed hint must remain non-fatal
        with question.ai_lock:
            question.ai_error = str(exc)
        return
    with question.ai_lock:
        if question.settled:
            question.ai_discarded = True
            return
        question.ai_decision = decision
        retired = question.ai_retired
    if retired:
        _persist_final_ai_guess_once(question)


def _start_final_ai_request(
    question: FinalQuizQuestionState,
    *,
    timeout_seconds: float,
) -> bool:
    with question.ai_lock:
        if question.ai_future is not None or question.settled:
            return False
        if len(question.options) != FINAL_OPTION_COUNT or not all(
            option.text for option in question.options
        ):
            return False
        future = FINAL_AI_EXECUTOR.submit(
            request_activity_quiz_ai_decision,
            question.prompt,
            tuple(option.text for option in question.options),
            timeout_seconds=timeout_seconds,
        )
        question.ai_future = future
    future.add_done_callback(lambda item: _publish_final_ai_decision(question, item))
    return True


def _retire_final_ai_hint(question: FinalQuizQuestionState | None) -> None:
    if question is None:
        return
    with question.ai_lock:
        question.ai_retired = True
        should_persist = question.ai_decision is not None and not question.settled
    if should_persist:
        _persist_final_ai_guess_once(question)


def final_quiz_hint_point(
    runtime: Any,
    option: FinalQuizOption,
) -> tuple[float, float] | None:
    """Project a live option row into the user-annotated #61[外框] hint lane."""

    view = runtime.view(FINAL_SCENE_ID)
    outer = runtime.shape(FINAL_SCENE_ID, "外框")
    width = float(view.raw.get("width") or 0)
    height = float(view.raw.get("height") or 0)
    if width <= 0 or height <= 0:
        return None
    raw = outer.raw
    left = float(raw.get("x") or 0) * width
    top = float(raw.get("y") or 0) * height
    right = (float(raw.get("x") or 0) + float(raw.get("w") or 0)) * width
    bottom = (float(raw.get("y") or 0) + float(raw.get("h") or 0)) * height
    if right <= left or bottom <= top or not (top <= option.y <= bottom):
        return None
    return ((left + right) / 2, option.y)


def execute_activity_quiz_final_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> dict[str, Any]:
    """Wait for #61 and answer every newly observed question in one Cell."""

    asset_tree_path = ctx.get("asset_tree_path")
    if not isinstance(asset_tree_path, Path):
        raise RuntimeError("活动_答题决赛：缺少资产树路径")
    runtime = runner._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
    max_runtime = float(payload.get("max_runtime_seconds") or 900.0)
    start_wait = float(payload.get("start_wait_seconds") or 180.0)
    idle_after_click = float(payload.get("idle_after_click_seconds") or 15.0)
    scene_exit_grace = float(payload.get("scene_exit_grace_seconds") or 8.0)
    poll_seconds = float(payload.get("poll_seconds") or 0.12)
    ai_timeout_seconds = float(payload.get("ai_timeout_seconds") or 45.0)
    hint_interval = float(payload.get("ai_hint_interval_seconds") or 1.0)
    hint_max_clicks = max(1, int(payload.get("ai_hint_max_clicks") or 3))
    native_max_age = float(payload.get("native_snapshot_max_age_seconds", 1.0))
    native_wait_seconds = max(0.0, float(payload.get("native_wait_seconds", 1.2)))
    native_prompt_threshold = float(
        payload.get("native_prompt_match_threshold") or 82.0
    )
    # OCR is noisy, so question identity remains fuzzy.  The safety boundary is
    # not exact text: it is that the match resolves to an existing authoritative
    # bank record and that its answer uniquely maps to one current visual row.
    match_threshold = float(payload.get("match_score_threshold") or 82.0)
    deadline = time.monotonic() + max_runtime
    start_deadline = time.monotonic() + min(start_wait, max_runtime)
    current: FinalQuizQuestionState | None = None
    scene_seen = False
    last_complete_observation: float | None = None
    click_attempts = 0
    ai_requests = 0
    hint_clicks = 0
    hinted_questions: set[str] = set()
    confirmed_answers = 0
    answer_sources = {"native": 0, "knowledge": 0, "external": 0}
    native_snapshot_reads = 0
    native_clicks = 0
    native_fallbacks = 0
    log = getattr(runner, "_log", lambda _kind, _message: None)

    while time.monotonic() < deadline:
        if stop_event.is_set():
            raise InterruptedError("活动_答题决赛：Cell 已停止")
        if not scene_seen:
            # A simulator restart invalidates every cached Lua address.  Start
            # rediscovery while the Job is still waiting for #61, instead of
            # spending the first live question's short answer window on the
            # cold memory scan.  This call is cache-backed and never blocks the
            # frame loop; it has no click/network side effect.
            fanxiu_instrumentation_service.final_camp_answer_snapshot(
                max_age_seconds=native_max_age
            )
        now = time.monotonic()
        frame = runtime.cur_frame(update=True)
        prompt_tokens = runtime.ocr_tokens_in_shapes(
            FINAL_SCENE_ID,
            ("题目",),
            padding=8,
            frame_data_url=frame,
            crop=True,
        )
        prompt = _join_tokens(prompt_tokens).strip()

        if current is not None and current.clicked and _same_question(prompt, current.prompt):
            last_complete_observation = now
            correct_position = detect_final_quiz_correct_position(frame, current.options)
            if correct_position is not None and not current.settled:
                current.correct_position = correct_position
                current.settled = True
                _retire_final_ai_hint(current)
                persisted = _persist_confirmed_answer(current)
                confirmed_answers += 1
                answer_sources[current.click_source or "external"] += 1
                log(
                    "success",
                    f"活动_答题决赛：游戏确认正确答案为第 {correct_position + 1} 项"
                    + ("，已写入题库" if persisted else "，题库写入失败"),
                )
            if current.clicked_at is not None and now - current.clicked_at >= idle_after_click:
                break
            time.sleep(max(0.02, poll_seconds))
            continue

        option_tokens = runtime.ocr_tokens_in_shapes(
            FINAL_SCENE_ID,
            ("选项",),
            padding=8,
            frame_data_url=frame,
            crop=True,
        )
        options = parse_final_quiz_options(option_tokens)
        if not prompt or not options:
            # A missing prompt can be the short result/closing window.  Never
            # reuse the previous question's AI row after that boundary, even if
            # OCR later happens to recover stale-looking text.
            if current is not None and not prompt:
                current.hint_suppressed = True
            if not scene_seen and now >= start_deadline:
                raise TimeoutError("活动_答题决赛：等待核心场景 #61 超时，未执行点击")
            if scene_seen and last_complete_observation is not None and now - last_complete_observation >= scene_exit_grace:
                break
            time.sleep(max(0.02, poll_seconds))
            continue

        scene_seen = True
        last_complete_observation = now
        if current is not None and _same_question(prompt, current.prompt):
            current.options = options
            correct_position = detect_final_quiz_correct_position(frame, options)
            if correct_position is not None and not current.settled:
                current.correct_position = correct_position
                current.settled = True
                current.click_source = current.click_source or "external"
                _retire_final_ai_hint(current)
                persisted = _persist_confirmed_answer(current)
                confirmed_answers += 1
                answer_sources[current.click_source] += 1
                log(
                    "success",
                    f"活动_答题决赛：采集游戏真值，第 {correct_position + 1} 项"
                    + ("，已写入题库" if persisted else "，题库写入失败"),
                )
                continue
        if current is None or not _same_question(prompt, current.prompt):
            _retire_final_ai_hint(current)
            current = FinalQuizQuestionState(prompt=prompt, options=options)
            correct_position = detect_final_quiz_correct_position(frame, options)
            if correct_position is not None:
                current.correct_position = correct_position
                current.settled = True
                current.click_source = "external"
                _retire_final_ai_hint(current)
                persisted = _persist_confirmed_answer(current)
                confirmed_answers += 1
                answer_sources["external"] += 1
                log(
                    "success",
                    f"活动_答题决赛：采集人工作答后的游戏真值，第 {correct_position + 1} 项"
                    + ("，已写入题库" if persisted else "，题库写入失败"),
                )
                continue

        # Native Runtime is the first answer source, but it never gets blind
        # coordinate authority.  The memory question must match the visible
        # OCR prompt and its answer text must uniquely map to one freshly read
        # option row.  This protects users from stale cache and shuffled rows.
        if not current.clicked and not current.settled and not current.fallback_started:
            native_snapshot = fanxiu_instrumentation_service.final_camp_answer_snapshot(
                max_age_seconds=native_max_age
            )
            native_snapshot_reads += 1
            native_position, native_reason = resolve_final_quiz_native_target(
                native_snapshot,
                prompt,
                current.options,
                prompt_match_threshold=native_prompt_threshold,
            )
            if native_position is not None:
                target = current.options[native_position]
                runtime.click_frame_point_fast(FINAL_SCENE_ID, target.x, target.y)
                current.clicked = True
                current.click_source = "native"
                current.clicked_at = time.monotonic()
                current.native_quest_id = int(native_snapshot["quest_id"])
                current.native_answer_id = int(native_snapshot["correct_option_id"])
                click_attempts += 1
                native_clicks += 1
                log(
                    "action",
                    "活动_答题决赛：动态插桩命中"
                    f" questId={current.native_quest_id}"
                    f" progress={native_snapshot.get('progress')}"
                    f" answerId={current.native_answer_id}，"
                    f"按当前 OCR 行点击第 {native_position + 1} 项"
                    f"（快照 age={native_snapshot.get('cache_age_seconds')}s）",
                )
                continue

            if time.monotonic() - current.started_at < native_wait_seconds:
                time.sleep(max(0.02, poll_seconds))
                continue

            current.fallback_started = True
            native_fallbacks += 1
            if not current.native_rejection_logged:
                current.native_rejection_logged = True
                log("info", f"活动_答题决赛：动态插桩未获可点击答案，降级：{native_reason}")

            matched, score = match_activity_quiz_question_cached(prompt)
            current.match_score = float(score)
            target_position = None
            if (
                matched is not None
                and float(score) >= match_threshold
                and is_authoritative_final_quiz_question(matched)
            ):
                current.matched_id = matched.id
                target_position = resolve_final_quiz_target(matched, current.options)
            if target_position is not None:
                target = current.options[target_position]
                runtime.click_frame_point_fast(FINAL_SCENE_ID, target.x, target.y)
                current.clicked = True
                current.click_source = "knowledge"
                current.clicked_at = time.monotonic()
                click_attempts += 1
                log("action", f"活动_答题决赛：题库降级命中，按本题实时选项点击第 {target_position + 1} 项")
                continue

            # AI remains a temporary advisory-only fallback until native
            # instrumentation has passed the next real event-window audit.
            current.needs_ai_hint = True
            if _start_final_ai_request(current, timeout_seconds=ai_timeout_seconds):
                ai_requests += 1
                log(
                    "info",
                    f"活动_答题决赛：题库也未可靠命中，异步请求 {ACTIVITY_QUIZ_AI_MODEL}，仅在[外框]提示",
                )

        prompt_is_current = _same_question(prompt, current.prompt)
        if (
            current.needs_ai_hint
            and not current.settled
            and not current.hint_suppressed
            and prompt_is_current
            and current.hint_click_count < hint_max_clicks
        ):
            with current.ai_lock:
                decision = current.ai_decision
            if (
                decision is not None
                and 0 <= decision.position < len(current.options)
                and (
                    current.last_hint_at is None
                    or now - current.last_hint_at >= max(0.1, hint_interval)
                )
            ):
                hint_point = final_quiz_hint_point(
                    runtime,
                    current.options[decision.position],
                )
                if hint_point is not None:
                    runtime.click_frame_point_fast(FINAL_SCENE_ID, *hint_point)
                    current.last_hint_at = time.monotonic()
                    current.hint_click_count += 1
                    hint_clicks += 1
                    if current.prompt not in hinted_questions:
                        hinted_questions.add(current.prompt)
                        log(
                            "action",
                            f"活动_答题决赛：AI 仅提示第 {decision.position + 1} 行，每秒点击[外框]对应高度",
                        )
        time.sleep(max(0.02, poll_seconds))

    _retire_final_ai_hint(current)
    if not scene_seen:
        raise TimeoutError("活动_答题决赛：运行结束前未识别到核心场景 #61")
    return {
        "result": "success",
        "message": f"活动_答题决赛：核心循环结束，确认并保存 {confirmed_answers} 题",
        "answered": confirmed_answers,
        "confirmed_answers": confirmed_answers,
        "click_attempts": click_attempts,
        "ai_requests": ai_requests,
        "hint_clicks": hint_clicks,
        "hinted_questions": len(hinted_questions),
        "answer_sources": answer_sources,
        "native_snapshot_reads": native_snapshot_reads,
        "native_clicks": native_clicks,
        "native_fallbacks": native_fallbacks,
        "current_scene": FINAL_SCENE_ID,
    }


__all__ = [
    "FINAL_SCENE_ID",
    "FinalQuizOption",
    "FinalQuizQuestionState",
    "execute_activity_quiz_final_task",
    "detect_final_quiz_correct_position",
    "final_quiz_hint_point",
    "is_authoritative_final_quiz_question",
    "parse_final_quiz_options",
    "resolve_final_quiz_answer_target",
    "resolve_final_quiz_native_target",
    "resolve_final_quiz_target",
]
