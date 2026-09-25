from __future__ import annotations

import threading
import time
from datetime import datetime, time as clock_time, timedelta
from typing import Any

from backend.core.fanxiu.tianjige_forum_quiz import (
    TianjigeForumQuizPreSubmitError,
    TianjigeQuizProbe,
    probe_tianjige_forum_quiz,
    submit_tianjige_forum_quiz_answer,
)


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
    from sqlmodel import Session
    from backend.models import AppSetting

    if db_bind is None:
        from backend.db import engine

        db_bind = engine
    with Session(db_bind) as session:
        row = session.get(AppSetting, TIANJIGE_FORUM_QUIZ_LEDGER_KEY)
        return dict(row.value or {}) if row and isinstance(row.value, dict) else {}


def _write_submission_ledger(value: dict[str, Any], db_bind: Any | None = None) -> None:
    from sqlmodel import Session
    from backend.models import AppSetting

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


def recover_tianjige_unsent_nickname_intent() -> bool:
    """Release only the proven pre-send nickname failure from today's formal attempt.

    The scheduler's terminal message proves the nickname guard fired before the
    send click. Other ``submitting`` records remain protected against duplicates.
    """
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_scheduler_tasks

    task = next((item for item in read_scheduler_tasks() if item.get("id") == TIANJIGE_FORUM_QUIZ_TASK_ID), None)
    if not task or "无法确认当前天机阁登录昵称，拒绝发送" not in str(task.get("last_message") or ""):
        return False
    if str(task.get("last_run_at") or "")[:10] != _now().strftime("%Y-%m-%d"):
        return False
    ledger = _read_submission_ledger()
    if str(ledger.get("state") or "") != "submitting" or not ledger.get("thread_key"):
        return False
    _write_submission_ledger({**ledger, "state": "pre_submit_failed", "updated_at": _now().timestamp()})
    return True


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


def record_tianjige_missing_thread_review(activity_date: str, evidence: str) -> None:
    """Record an Agent's completed daily check, without claiming no activity.

    Suppresses duplicate repair dispatches; regular low-frequency post checks
    continue through the activity window, so a late post can still be handled.
    """
    day = datetime.strptime(activity_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    if not str(evidence).strip():
        raise ValueError("AI 核查必须提供实际证据")
    ledger = _read_submission_ledger()
    ledger.update(missing_thread_review_date=day, missing_thread_review_evidence=evidence)
    _write_submission_ledger(ledger)


def _request_missing_thread_review(runner: Any, current: datetime, probe: TianjigeQuizProbe) -> None:
    """Request one review per day; the receiving Agent alone changes ownership.

    Successful dispatch is persisted to prevent a new Agent every poll. Failed
    launch remains retryable on the next slow poll, without disabling engineering.
    """
    from backend.core.codex import CodexEscalationRequest, escalate_to_codex
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_scheduler_settings

    if not read_scheduler_settings().get("job_group_enabled", True):
        runner._log("warning", "天机阁_有奖竞答：18:10 后仍未找到当天帖子，交由当前接管 AI 核查")
        return
    ledger = _read_submission_ledger()
    day = current.strftime("%Y-%m-%d")
    if ledger.get("missing_thread_review_date") == day:
        return
    request = CodexEscalationRequest(
        title=f"天机阁竞答 {day} 超过 18:10 仍未找到当天帖子",
        problem=f"读取 {probe.profile_thread_count} 条动态仍未匹配当天竞答；不能据此断言今天没有活动。",
        objective="核实官方发布、页面加载、日期解析与筛选，修复真实问题并通过正式入口验证，避免高频无效轮询。",
        evidence=(probe.profile_excerpt or "本次未记录页面正文",),
        recovery_instructions=(
            "实际接管时先调用 backend.core.fanxiu.data_annotation.kernel_scheduler_control.take_ai_control "
            "取得凡修运行权，再使用唯一 Kernel 内的论坛接口核查。工程没有替你切换模式。"
            "按当前事实完成本日处理；不得把未找到写成确认没有活动。完成后归还工程运行权。"
        ),
    )
    try:
        dispatch = escalate_to_codex(request)
    except Exception as exc:
        runner._log("warning", f"天机阁_有奖竞答：AI 请求失败，保留工程模式，稍后重试：{exc}")
        return
    ledger.update(missing_thread_review_date=day, missing_thread_review_dispatch_id=dispatch.dispatch_id)
    _write_submission_ledger(ledger)
    runner._log("warning", f"天机阁_有奖竞答：已请求 AI 核查 {dispatch.dispatch_id}，等待 AI 实际接管")


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
        if completed_at.time() >= clock_time(18, 10):
            _request_missing_thread_review(runner, completed_at, probe)
            poll_seconds = max(poll_seconds, 300.0)
        return _waiting_result(
            runner,
            completed_at,
            probe,
            poll_seconds=poll_seconds,
            message=(
                f"天机阁_有奖竞答：等待页面 {probe.elapsed_seconds:.1f} 秒并读取"
                f" {probe.profile_thread_count} 条动态后，尚未找到当天帖子（不代表未发布）"
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
    except TianjigeForumQuizPreSubmitError as exc:
        _write_submission_ledger({**_read_submission_ledger(), "state": "pre_submit_failed", "updated_at": _now().timestamp()})
        completed_at = _now()
        next_time = _set_next_time(runner, _poll_next_time(completed_at, max(poll_seconds, 60)))
        message = f"天机阁_有奖竞答：发送前检查未通过，尚未回帖：{exc}；下次 {next_time}"
        runner._log("warning", message)
        return {"thread_key": probe.thread_key, "answer": answer_text, "message": message}
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
