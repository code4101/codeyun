from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.quiz.store import match_lingquan_question
from backend.core.fanxiu.runtime.mumu_control import text_mumu_adb
from backend.db import engine


LINGQUAN_TRIGGER_TIME = dt_time(20, 30)
LINGQUAN_QUESTION_CUTOFF = dt_time(20, 41)
LINGQUAN_EXIT_TIME = dt_time(20, 43)


def _now() -> datetime:
    return datetime.now()


def _next_trigger(now: datetime) -> datetime:
    candidate = datetime.combine(now.date(), LINGQUAN_TRIGGER_TIME)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _deadline(now: datetime, value: dt_time) -> datetime:
    return datetime.combine(now.date(), value)


class LingquanTaskMixin:
    """Daily Lingquan entry, timed quiz loop, and timed scene exit."""

    def _wait_lingquan_until(self, runtime: Any, deadline: datetime, *, poll_seconds: float = 1.0):
        while True:
            remaining = (deadline - _now()).total_seconds()
            if remaining <= 0:
                return
            yield from runtime.wait_action_settle(min(max(0.1, poll_seconds), remaining))

    def _enter_lingquan(self, runtime: Any, *, transition_timeout: float):
        scene_id, _score, _frame = runtime.current_scene([386], update=True)
        if scene_id != 386:
            self._log("info", "日常_灵泉：未识别到 #386，使用 #34 → #66[前往] 活动入口")
            yield from runtime.goto_view(34)
            yield from runtime.goto_view(66)
            yield from runtime.wait_click_then_view(
                66, "前往", 386, timeout=transition_timeout,
                label="日常_灵泉：#66[前往] 等待活动入口 #386",
            )
        yield from runtime.wait_click_then_view(386, "前往", 387, timeout=transition_timeout)
        yield from runtime.wait_click_then_view(387, "灵泉", 303, timeout=transition_timeout)
        yield from runtime.advance_dialogue(303, "对话", label="日常_灵泉：推进管事对话")
        yield from runtime.wait_view(388, timeout=transition_timeout, label="日常_灵泉：等待准备页 #388")
        yield from runtime.wait_click_then_view(388, "进入问答", 389, timeout=transition_timeout)

    def _answer_lingquan_question(
        self,
        runtime: Any,
        *,
        frame_data_url: str,
        transition_timeout: float,
        score_threshold: float,
    ):
        question_text = runtime.ocr_text_in_shapes(389, ("题目",), frame_data_url=frame_data_url)
        with Session(engine) as session:
            matched, score = match_lingquan_question(session, question_text)
        if matched is None or score <= score_threshold:
            self._log("warning", f"日常_灵泉：题库未可靠匹配（{score:.1f}），跳过：{question_text!r}")
            return {"answered": False, "question": question_text, "score": score}

        self._log("info", f"日常_灵泉：匹配 {score:.1f}%，答案：{matched.answer}")
        yield from runtime.wait_click_then_view(
            389, "输入", 390, settle_seconds=0.5, timeout=transition_timeout,
            label="日常_灵泉：打开答案输入框 #390",
        )
        text_mumu_adb(matched.answer)
        yield from runtime.wait_action_settle(0.5)
        # 第一击只关闭输入法弹窗；强制间隔两秒后第二击才真正发送。
        yield from runtime.wait_click(390, "发送")
        yield from runtime.wait_action_settle(2.0)
        yield from runtime.wait_click(390, "发送")
        yield from runtime.wait_view(389, timeout=transition_timeout, label="日常_灵泉：等待下一题页 #389")
        return {
            "answered": True,
            "question": question_text,
            "matched_question": matched.question,
            "answer": matched.answer,
            "score": score,
        }

    def _run_lingquan_question_loop(
        self,
        runtime: Any,
        *,
        cutoff: datetime,
        transition_timeout: float,
        score_threshold: float,
        poll_seconds: float,
    ):
        answers = 0
        while _now() < cutoff:
            frame = runtime.cur_frame(update=True)
            numbers, countdown_text = runtime.ocr_numbers_in_shapes(
                389, ("倒计时",), frame_data_url=frame,
            )
            countdown = numbers[0] if numbers else 0
            if countdown <= 20:
                self._log("debug", f"日常_灵泉：等待新题倒计时 >20，当前 {countdown_text!r}")
                yield from self._wait_lingquan_until(
                    runtime, min(cutoff, _now() + timedelta(seconds=poll_seconds)), poll_seconds=poll_seconds,
                )
                continue

            # 新题的强制 CD 从识别倒计时这一刻开始，与识题/回答耗时无关。
            detected_at = _now()
            refresh_deadline = detected_at + timedelta(seconds=countdown)
            self._log("info", f"日常_灵泉：识别新题倒计时 {countdown} 秒，刷新截止 {refresh_deadline:%H:%M:%S}")
            result = yield from self._answer_lingquan_question(
                runtime,
                frame_data_url=frame,
                transition_timeout=transition_timeout,
                score_threshold=score_threshold,
            )
            answers += int(bool(result.get("answered")))
            yield from self._wait_lingquan_until(runtime, min(refresh_deadline, cutoff), poll_seconds=poll_seconds)
        return answers

    def _execute_daily_lingquan_task(
        self,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(payload or {})
        now = _now()
        next_time = _next_trigger(now).strftime("%Y-%m-%d %H:%M:%S")
        if not (LINGQUAN_TRIGGER_TIME <= now.time() < LINGQUAN_QUESTION_CUTOFF):
            return {
                "result": "success",
                "message": "日常_灵泉：当前不在 20:30:00-20:40:59 入场/答题窗口，未执行游戏操作",
                "next_time": next_time,
                "current_scene": None,
            }

        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_灵泉资产树路径，无法执行作业")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        transition_timeout = float(payload.get("transition_timeout_seconds") or 20.0)
        poll_seconds = max(0.2, float(payload.get("poll_seconds") or 1.0))
        score_threshold = float(payload.get("match_score_threshold") or 90.0)
        cutoff = _deadline(now, LINGQUAN_QUESTION_CUTOFF)
        exit_time = _deadline(now, LINGQUAN_EXIT_TIME)

        yield from self._enter_lingquan(runtime, transition_timeout=transition_timeout)
        answers = yield from self._run_lingquan_question_loop(
            runtime,
            cutoff=cutoff,
            transition_timeout=transition_timeout,
            score_threshold=score_threshold,
            poll_seconds=poll_seconds,
        )
        yield from runtime.wait_click_then_view(389, "返回", 388, timeout=transition_timeout)
        yield from self._wait_lingquan_until(runtime, exit_time, poll_seconds=poll_seconds)
        # #388 的场景身份可匹配通用区域内部 #85；从其 [离开] 返回世界。
        yield from runtime.wait_view(85, timeout=transition_timeout, label="日常_灵泉：等待可离场身份 #85")
        yield from runtime.wait_click_then_view(85, "离开", 34, timeout=transition_timeout)
        self._log("success", f"日常_灵泉：完成 {answers} 道题并于 20:43 离场")
        return {
            "result": "success",
            "message": f"日常_灵泉完成，共回答 {answers} 道题，已于 20:43 返回世界",
            "next_time": next_time,
            "current_scene": 34,
            "answers": answers,
        }
