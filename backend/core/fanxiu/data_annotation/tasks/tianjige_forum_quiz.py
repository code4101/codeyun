from __future__ import annotations

import threading
import time
from datetime import datetime, time as clock_time, timedelta
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.tianjige_forum_quiz import (
    TianjigeQuizProbe,
    probe_tianjige_forum_quiz,
    submit_tianjige_forum_quiz_answer,
)
from backend.models import AppSetting


TIANJIGE_FORUM_QUIZ_TASK_ID = "tianjige-forum-quiz"
TIANJIGE_FORUM_QUIZ_WEEKDAYS = (1, 2, 3)  # Tuesday, Wednesday, Thursday
TIANJIGE_FORUM_QUIZ_START = clock_time(17, 59, 50)
TIANJIGE_FORUM_QUIZ_END = clock_time(19, 0, 0)
TIANJIGE_FORUM_QUIZ_LEDGER_KEY = "fanxiu.tianjige_forum_quiz.submission"


def _now() -> datetime:
    return datetime.now()


def next_tianjige_forum_quiz_trigger_at(current: datetime) -> datetime:
    """返回严格晚于当前时刻的下一个周二、三、四 17:59:50。"""

    for day_offset in range(8):
        day = current.date() + timedelta(days=day_offset)
        if day.weekday() not in TIANJIGE_FORUM_QUIZ_WEEKDAYS:
            continue
        candidate = datetime.combine(day, TIANJIGE_FORUM_QUIZ_START)
        if candidate > current:
            return candidate
    raise RuntimeError("无法计算天机阁有奖竞答下次时间")


def _format_next_time(value: datetime) -> str:
    return value.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _read_submission_ledger(db_bind: Any | None = None) -> dict[str, Any]:
    if db_bind is None:
        from backend.db import engine

        db_bind = engine
    with Session(db_bind) as session:
        row = session.get(AppSetting, TIANJIGE_FORUM_QUIZ_LEDGER_KEY)
        return dict(row.value or {}) if row and isinstance(row.value, dict) else {}


def _write_submission_ledger(value: dict[str, Any], db_bind: Any | None = None) -> None:
    if db_bind is None:
        from backend.db import engine

        db_bind = engine
    with Session(db_bind) as session:
        row = session.get(AppSetting, TIANJIGE_FORUM_QUIZ_LEDGER_KEY)
        if row is None:
            row = AppSetting(key=TIANJIGE_FORUM_QUIZ_LEDGER_KEY)
        row.value = dict(value)
        row.updated_at = time.time()
        session.add(row)
        session.commit()


def _set_next_time(runner: Any, value: datetime) -> str:
    formatted = _format_next_time(value)
    runner._persist_scheduler_task_next_time(TIANJIGE_FORUM_QUIZ_TASK_ID, formatted)
    return formatted


def _poll_next_time(current: datetime, poll_seconds: float) -> datetime:
    window_end = datetime.combine(current.date(), TIANJIGE_FORUM_QUIZ_END)
    candidate = current + timedelta(seconds=max(5.0, float(poll_seconds)))
    if candidate < window_end:
        return candidate
    return next_tianjige_forum_quiz_trigger_at(window_end)


def _is_active_window(current: datetime) -> bool:
    return (
        current.weekday() in TIANJIGE_FORUM_QUIZ_WEEKDAYS
        and TIANJIGE_FORUM_QUIZ_START <= current.time() < TIANJIGE_FORUM_QUIZ_END
    )


def _waiting_result(
    runner: Any,
    current: datetime,
    probe: TianjigeQuizProbe,
    *,
    poll_seconds: float,
    message: str,
) -> dict[str, Any]:
    next_time = _set_next_time(runner, _poll_next_time(current, poll_seconds))
    full_message = f"{message}，下次检查 {next_time}"
    runner._log("info", full_message)
    return {
        "thread_key": probe.thread_key,
        "comment_count": probe.comment_count,
        "message": full_message,
    }


def execute_tianjige_forum_quiz_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> dict[str, Any]:
    """短 Cell 检查当日论坛竞答，必要时只发送一次最高置信回复。"""

    del ctx
    current = _now()
    if not _is_active_window(current):
        next_time = _set_next_time(runner, next_tianjige_forum_quiz_trigger_at(current))
        message = f"天机阁_有奖竞答：当前不在活动窗口，下次 {next_time}"
        runner._log("info", message)
        return {"message": message}

    runner._raise_if_stopped(stop_event)
    poll_seconds = max(5.0, float(payload.get("poll_seconds") or 10))
    minimum_score = max(1, int(payload.get("minimum_answer_score") or 2))
    def log_waiting_progress(probe: TianjigeQuizProbe) -> None:
        if probe.status == "waiting_thread":
            runner._log(
                "info",
                f"天机阁_有奖竞答：同一标签页已等待 {probe.elapsed_seconds:.1f} 秒，"
                f"已读取 {probe.profile_thread_count} 条动态，继续刷新等待当天帖子",
            )
        else:
            score = probe.answer.score if probe.answer else 0
            runner._log(
                "info",
                f"天机阁_有奖竞答：同一帖子已见 {probe.comment_count} 条评论，"
                f"最高候选权重 {score}/{minimum_score}，继续刷新等待",
            )

    probe = probe_tianjige_forum_quiz(
        current.strftime("%Y-%m-%d"),
        timeout_seconds=max(5.0, float(payload.get("page_timeout_seconds") or 15)),
        # Scheduler Cell must stay short.  A missing post/answer is represented by
        # ``next_time`` below, rather than monopolising the only Kernel until the
        # whole activity window closes.
        overall_timeout_seconds=None,
        poll_seconds=poll_seconds,
        minimum_answer_score=minimum_score,
        progress_callback=log_waiting_progress,
        check_cancel=lambda: runner._raise_if_stopped(stop_event),
    )
    if probe.status == "waiting_thread":
        completed_at = _now()
        return _waiting_result(
            runner,
            completed_at,
            probe,
            poll_seconds=poll_seconds,
            message=(
                f"天机阁_有奖竞答：等待页面 {probe.elapsed_seconds:.1f} 秒并读取"
                f" {probe.profile_thread_count} 条动态后，确认当天帖子尚未发布"
            ),
        )

    if probe.answer is None or probe.answer.score < minimum_score:
        score = probe.answer.score if probe.answer else 0
        completed_at = _now()
        return _waiting_result(
            runner,
            completed_at,
            probe,
            poll_seconds=poll_seconds,
            message=(
                f"天机阁_有奖竞答：已见 {probe.comment_count} 条评论，"
                f"最高候选权重 {score}/{minimum_score}，继续等待"
            ),
        )

    answer_text = probe.answer.text
    ledger = _read_submission_ledger()
    if (
        str(ledger.get("thread_key") or "") == probe.thread_key
        and str(ledger.get("state") or "") in {"submitting", "submitted"}
    ):
        next_time = _set_next_time(runner, next_tianjige_forum_quiz_trigger_at(current))
        state = str(ledger.get("state") or "")
        message = f"天机阁_有奖竞答：本帖已有 {state} 记录，不重复回复；下次 {next_time}"
        runner._log("info", message)
        return {
            "thread_key": probe.thread_key,
            "answer": answer_text,
            "message": message,
        }

    if not bool(payload.get("submit_enabled", True)):
        return _waiting_result(
            runner,
            current,
            probe,
            poll_seconds=poll_seconds,
            message=f"天机阁_有奖竞答：只读模式已选出答案 {answer_text!r}",
        )

    # 在真实发送前持久化意图。发送后的任何不确定错误都不再自动重试，
    # 以“宁可漏答一次，也不重复回帖”为外部写入边界。
    _write_submission_ledger(
        {
            "thread_key": probe.thread_key,
            "thread_url": probe.thread_url,
            "answer": answer_text,
            "line_scores": list(probe.answer.line_scores),
            "line_votes": list(probe.answer.line_votes),
            "state": "submitting",
            "updated_at": current.timestamp(),
        }
    )
    try:
        verified = submit_tianjige_forum_quiz_answer(
            probe.thread_url,
            answer_text,
            timeout_seconds=max(5.0, float(payload.get("submit_timeout_seconds") or 15)),
            check_cancel=lambda: runner._raise_if_stopped(stop_event),
        )
    except Exception as exc:
        if bool(getattr(stop_event, "is_set", lambda: False)()):
            raise
        next_time = _set_next_time(runner, next_tianjige_forum_quiz_trigger_at(current))
        message = f"天机阁_有奖竞答：发送结果不确定，为避免重复不再重试：{exc}"
        runner._log("warning", message)
        return {
            "thread_key": probe.thread_key,
            "answer": answer_text,
            "message": message,
        }

    if not verified:
        next_time = _set_next_time(runner, next_tianjige_forum_quiz_trigger_at(current))
        message = "天机阁_有奖竞答：发送后未在评论区确认，为避免重复不再重试"
        runner._log("warning", message)
        return {
            "thread_key": probe.thread_key,
            "answer": answer_text,
            "message": message,
        }

    _write_submission_ledger(
        {
            "thread_key": probe.thread_key,
            "thread_url": probe.thread_url,
            "answer": answer_text,
            "line_scores": list(probe.answer.line_scores),
            "line_votes": list(probe.answer.line_votes),
            "state": "submitted",
            "updated_at": _now().timestamp(),
        }
    )
    next_time = _set_next_time(runner, next_tianjige_forum_quiz_trigger_at(current))
    message = (
        f"天机阁_有奖竞答：已回复逐题权重 {probe.answer.line_scores}、"
        f"逐题票数 {probe.answer.line_votes} 的组合答案；下次 {next_time}"
    )
    runner._log("success", message)
    return {
        "thread_key": probe.thread_key,
        "answer": answer_text,
        "score": probe.answer.score,
        "votes": probe.answer.votes,
        "line_scores": list(probe.answer.line_scores),
        "line_votes": list(probe.answer.line_votes),
        "message": message,
    }


__all__ = [
    "TIANJIGE_FORUM_QUIZ_END",
    "TIANJIGE_FORUM_QUIZ_START",
    "TIANJIGE_FORUM_QUIZ_TASK_ID",
    "TIANJIGE_FORUM_QUIZ_WEEKDAYS",
    "execute_tianjige_forum_quiz_task",
    "next_tianjige_forum_quiz_trigger_at",
]
