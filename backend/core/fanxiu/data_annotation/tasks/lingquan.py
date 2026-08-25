from __future__ import annotations

import time
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.schedule_navigation import (
    select_schedule_activity,
)
from backend.core.fanxiu.runtime.mumu_control import text_mumu_adb


LINGQUAN_TRIGGER_TIME = dt_time(20, 30)
LINGQUAN_QUESTION_START_TIME = dt_time(20, 33)
LINGQUAN_QUESTION_CUTOFF = dt_time(20, 41)
LINGQUAN_EXIT_TIME = dt_time(20, 43)


class _LingquanWindowExpired(RuntimeError):
    """Internal control flow: the current day's Lingquan window has ended."""


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

    def daily_lingquan_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = dict(payload or {})
        now = _now()
        if LINGQUAN_TRIGGER_TIME <= now.time() < LINGQUAN_QUESTION_CUTOFF:
            return None
        return self._persist_admission_decision(payload, {
            "result": "success",
            "message": "日常_灵泉：当前不在 20:30:00-20:40:59 入场/答题窗口，未执行游戏操作",
            "next_time": _next_trigger(now).strftime("%Y-%m-%d %H:%M:%S"),
            "current_scene": None,
        })

    def _wait_lingquan_until(self, runtime: Any, deadline: datetime, *, poll_seconds: float = 1.0):
        while True:
            remaining = (deadline - _now()).total_seconds()
            if remaining <= 0:
                return
            yield from runtime.wait_action_settle(min(max(0.1, poll_seconds), remaining))

    @staticmethod
    def _lingquan_timeout(deadline: datetime, requested: float) -> float:
        remaining = (deadline - _now()).total_seconds()
        if remaining <= 0:
            raise _LingquanWindowExpired("日常_灵泉：本日窗口已结束")
        return min(max(0.1, float(requested)), remaining)

    @staticmethod
    def _lingquan_window_timeout(deadline: datetime) -> float:
        remaining = (deadline - _now()).total_seconds()
        if remaining <= 0:
            raise _LingquanWindowExpired("日常_灵泉：本日窗口已结束")
        return max(0.1, remaining)

    @staticmethod
    def _lingquan_question_page_timeout(deadline: datetime, requested: float) -> float:
        """Wait through the pre-question holding screen instead of failing at 20 seconds."""
        now = _now()
        ready_at = _deadline(now, LINGQUAN_QUESTION_START_TIME)
        target = now + timedelta(seconds=max(0.1, float(requested)))
        if now < ready_at:
            target = max(target, ready_at + timedelta(seconds=max(0.1, float(requested))))
        return LingquanTaskMixin._lingquan_window_timeout(min(deadline, target))

    @staticmethod
    def _view_id(view: Any) -> int | None:
        value = getattr(view, "id", view)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _enter_lingquan(self, runtime: Any, *, transition_timeout: float, deadline: datetime):
        scene_id, _score, _frame = runtime.current_scene([389, 388, 387, 386, 66, 34], update=True)
        if scene_id is None:
            self._log("info", "日常_灵泉：当前为过渡/未知画面，等待进入稳定业务场景")
            waited = yield from runtime.wait_view(
                389, 388, 387, 386,
                timeout=self._lingquan_window_timeout(deadline),
                label="日常_灵泉：等待过渡结束并进入稳定业务场景",
            )
            scene_id = self._view_id(waited)
        if scene_id == 389:
            self._log("info", "日常_灵泉：已在答题页 #389，直接继续本轮答题")
            return
        if scene_id == 388:
            timeout = self._lingquan_question_page_timeout(deadline, transition_timeout)
            yield from runtime.wait_click_then_view(388, "进入问答", 389, timeout=timeout)
            return
        if scene_id == 34:
            self._log("info", "日常_灵泉：未识别到 #386，使用 #34 → #66[前往] 活动入口")
            self._lingquan_timeout(deadline, transition_timeout)
            yield from runtime.goto_view(66)
            scene_id = 66
        elif scene_id not in {66, 386, 387}:
            self._log("info", "日常_灵泉：当前不在活动入口，使用 #34 → #66[前往] 活动入口")
            yield from runtime.goto_view(34)
            self._lingquan_timeout(deadline, transition_timeout)
            yield from runtime.goto_view(66)
            scene_id = 66
        if scene_id == 66:
            settle_seconds = self._lingquan_timeout(deadline, 3.0)
            yield from runtime.wait_action_settle(settle_seconds)
            timeout = self._lingquan_timeout(deadline, transition_timeout)
            yield from select_schedule_activity(
                runtime,
                r"灵泉",
                enter=True,
                settle_seconds=min(0.8, settle_seconds),
            )
            yield from runtime.wait_view(
                386,
                timeout=timeout,
                label="日常_灵泉：等待已校验的活动卡片进入 #386",
            )
            scene_id = 386
        if scene_id == 386:
            landed = yield from runtime.wait_click_then_view(
                386, "前往", [387, 388, 389],
                timeout=self._lingquan_window_timeout(deadline),
                label="日常_灵泉：等待过渡结束并进入 #387/#388/#389",
            )
            scene_id = self._view_id(landed)
        if scene_id == 389:
            return
        if scene_id == 388:
            timeout = self._lingquan_question_page_timeout(deadline, transition_timeout)
            yield from runtime.wait_click_then_view(388, "进入问答", 389, timeout=timeout)
            return
        if scene_id != 387:
            raise RuntimeError(f"日常_灵泉：过渡结束后落点无效：{scene_id!r}")
        timeout = self._lingquan_timeout(deadline, transition_timeout)
        yield from runtime.wait_click_then_view(387, "灵泉", 303, timeout=timeout)
        yield from runtime.advance_dialogue(303, "对话", label="日常_灵泉：推进管事对话")
        timeout = self._lingquan_timeout(deadline, transition_timeout)
        yield from runtime.wait_view(388, timeout=timeout, label="日常_灵泉：等待准备页 #388")
        timeout = self._lingquan_question_page_timeout(deadline, transition_timeout)
        yield from runtime.wait_click_then_view(388, "进入问答", 389, timeout=timeout)

    def _ensure_lingquan_question_scene(
        self,
        runtime: Any,
        *,
        cutoff: datetime,
        transition_timeout: float,
    ):
        """Keep the active quiz window anchored at #389."""
        scene_id, _score, _frame = runtime.current_scene([389, 388, 387, 386, 66, 34], update=True)
        if scene_id == 389:
            return
        self._log(
            "warning",
            f"日常_灵泉：答题窗口内发现当前不在 #389（#{scene_id or 'unknown'}），自动恢复答题页",
        )
        yield from self._enter_lingquan(
            runtime,
            transition_timeout=transition_timeout,
            deadline=cutoff,
        )
        scene_id, _score, _frame = runtime.current_scene([389], update=True)
        if scene_id != 389:
            raise TimeoutError("日常_灵泉：窗口内恢复后仍未确认到 #389")

    def _answer_lingquan_question(
        self,
        runtime: Any,
        *,
        frame_data_url: str | None,
        transition_timeout: float,
        score_threshold: float,
        question_text: str | None = None,
        previous_matched_question: str = "",
    ):
        started_at = time.perf_counter()
        from backend.core.fanxiu.quiz.store import (
            match_lingquan_question_cached,
        )

        question_text = str(
            question_text
            if question_text is not None
            else runtime.ocr_text_in_shapes(389, ("题目",), frame_data_url=frame_data_url)
        ).strip()
        matched, score = match_lingquan_question_cached(question_text)
        if matched is None or score <= score_threshold:
            self._log("warning", f"日常_灵泉：题库未可靠匹配（{score:.1f}），跳过：{question_text!r}")
            return {"answered": False, "question": question_text, "score": score}
        if previous_matched_question and matched.question == previous_matched_question:
            return {
                "answered": False,
                "duplicate": True,
                "question": question_text,
                "matched_question": matched.question,
                "score": score,
            }

        self._log("info", f"日常_灵泉：匹配 {score:.1f}%，答案：{matched.answer}")
        # 打开的是 MuMu/Android 系统输入法；键盘会改变画面底部，不能把
        # “必须识别成 #390”作为继续输入的前置条件。#390 只提供发送按钮
        # 的宿主 Shape 配置，实际画面和坐标仍以当前业务宿主为准。
        yield from runtime.wait_click(389, "输入")
        yield from runtime.wait_action_settle(0.5)
        input_ready_at = time.perf_counter()
        text_mumu_adb(matched.answer)
        yield from runtime.wait_action_settle(0.5)
        text_ready_at = time.perf_counter()
        # 第一击只关闭输入法弹窗；强制间隔两秒后第二击才真正发送。
        # 当前业务状态与按钮宿主已由 #389/#390 明确，限时路径不能再为
        # 每一击重复取帧、OCR「发送」并保存动作前截图。坐标仍从正式
        # #390[发送] 标注解析，只跳过这两类昂贵的重复证据。
        runtime.click_shape_center_fast(390, "发送")
        first_click_at = time.perf_counter()
        yield from runtime.wait_action_settle(2.0)
        runtime.click_shape_center_fast(390, "发送")
        second_click_at = time.perf_counter()
        self._log(
            "detail",
            (
                "日常_灵泉：答题关键路径耗时 "
                f"输入框={input_ready_at - started_at:.2f}s，"
                f"文本输入={text_ready_at - input_ready_at:.2f}s，"
                f"首击={first_click_at - text_ready_at:.2f}s，"
                f"双击完成={second_click_at - started_at:.2f}s"
            ),
        )
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
        previous_matched_question = ""
        while _now() < cutoff:
            from backend.core.fanxiu.instrumentation import (
                fanxiu_instrumentation_service,
            )

            runtime_question = (
                fanxiu_instrumentation_service.lingquan_question_snapshot(
                    max_age_seconds=2.0,
                )
            )
            use_runtime_question = bool(
                runtime_question.get("available")
                and runtime_question.get("fresh")
                and runtime_question.get("phase") == "question"
                and str(runtime_question.get("question") or "").strip()
            )
            frame: str | None = None
            if use_runtime_question:
                question_text = str(runtime_question["question"]).strip()
                countdown = int(runtime_question.get("remaining_seconds") or 0)
                countdown_text = f"Runtime：{countdown}秒"
                self._log(
                    "info",
                    "日常_灵泉：动态插桩读取到"
                    f"第{runtime_question.get('question_index') or '?'}题，"
                    f"剩余 {countdown} 秒",
                )
            else:
                frame = runtime.cur_frame(update=True)
                question_text = str(
                    runtime.ocr_text_in_shapes(
                        389,
                        ("题目",),
                        frame_data_url=frame,
                    )
                    or ""
                ).strip()
                countdown = 0
                countdown_text = ""
            if not question_text:
                self._log("debug", "日常_灵泉：等待 #389 出现题目文本")
                # 正常答题过程始终停留在 #389。先读当前题面，只有题面与
                # Runtime 快照都缺失时才付出整场景识别/恢复成本；此前每题
                # 开头无条件识别 #389，会在 OCR 退化时额外消耗 7-27 秒。
                yield from self._ensure_lingquan_question_scene(
                    runtime,
                    cutoff=cutoff,
                    transition_timeout=transition_timeout,
                )
                yield from self._wait_lingquan_until(
                    runtime, min(cutoff, _now() + timedelta(seconds=poll_seconds)), poll_seconds=poll_seconds,
                )
                continue
            if not use_runtime_question:
                numbers, countdown_text = runtime.ocr_numbers_in_shapes(
                    389,
                    ("倒计时",),
                    frame_data_url=frame,
                )
                countdown = numbers[0] if numbers else 0
            # 题目文本决定是否作答；倒计时只用于控制答完后的刷新等待，
            # OCR 失败时退化成短轮询，不能因此跳过整轮答题。
            detected_at = _now()
            refresh_deadline = detected_at + timedelta(seconds=countdown if countdown > 0 else poll_seconds)
            self._log(
                "info",
                f"日常_灵泉：识别到题目，倒计时 {countdown_text!r}，刷新截止 {refresh_deadline:%H:%M:%S}",
            )
            result = yield from self._answer_lingquan_question(
                runtime,
                frame_data_url=frame,
                transition_timeout=transition_timeout,
                score_threshold=score_threshold,
                question_text=question_text,
                previous_matched_question=previous_matched_question,
            )
            answers += int(bool(result.get("answered")))
            if result.get("answered"):
                previous_matched_question = str(result.get("matched_question") or "")
            yield from self._wait_lingquan_until(runtime, min(refresh_deadline, cutoff), poll_seconds=poll_seconds)
        return answers

    def _exit_lingquan_to_world(self, runtime: Any, *, timeout: float):
        """Consume every nested leave layer until the real world scene is reached."""
        deadline = time.monotonic() + max(1.0, float(timeout))
        scene_id, _score, _frame = runtime.current_scene(
            [34, 388, 186, 86],
            update=True,
        )

        # 灵泉结束后可能叠着多层内部场景：活动专用 #388、通用离开层
        # #186，以及每次离开产生的 #86 确认层。不能把第一次点击后出现
        # #186 当成异常，也不能只消费一次确认；每次动作后都重新识别，
        # 直到真实命中 #34。动作次数与总时间同时有界，避免异常画面狂点。
        for _step in range(8):
            # wait_view / wait_click_then_view 已经用真实帧确认落到 #34 时，
            # 即使动作恰好耗尽总预算也应以业务成功为准。否则下一轮先算
            # remaining 会把“截止瞬间已回世界”误写成离场超时。
            if scene_id == 34:
                self._log("success", "日常_灵泉：已离场并回到 #34 世界")
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("日常_灵泉：多层离场超时，尚未回到 #34")
            if scene_id is None:
                waited = yield from runtime.wait_view(
                    34,
                    388,
                    186,
                    86,
                    timeout=remaining,
                    label="日常_灵泉：重新识别多层离场上下文",
                )
                scene_id = self._view_id(waited)
                continue
            if scene_id in {388, 186}:
                self._log("action", f"日常_灵泉：点击 #{scene_id}「离开」")
                runtime.click_shape(scene_id, "离开")
                yield from runtime.wait_action_settle(2.0)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("日常_灵泉：点击离开后等待落点超时")
                landed = yield from runtime.wait_view(
                    86,
                    34,
                    388,
                    186,
                    timeout=remaining,
                    label="日常_灵泉：点击离开后重新识别落点",
                )
                scene_id = self._view_id(landed)
                continue
            if scene_id == 86:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("日常_灵泉：离场确认前已超时")
                landed = yield from runtime.wait_click_then_view(
                    86,
                    "确认",
                    [34, 388, 186, 86],
                    timeout=remaining,
                )
                scene_id = self._view_id(landed)
                continue
            raise RuntimeError(f"日常_灵泉：多层离场落点异常：#{scene_id or 'unknown'}")

        raise RuntimeError(f"日常_灵泉：多层离场动作次数耗尽，当前 #{scene_id or 'unknown'}")

    def _execute_daily_lingquan_task(
        self,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(payload or {})
        now = _now()
        next_time = _next_trigger(now).strftime("%Y-%m-%d %H:%M:%S")

        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_灵泉资产树路径，无法执行作业")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        transition_timeout = float(payload.get("transition_timeout_seconds") or 20.0)
        poll_seconds = max(0.2, float(payload.get("poll_seconds") or 1.0))
        score_threshold = float(payload.get("match_score_threshold") or 90.0)
        question_start = _deadline(now, LINGQUAN_QUESTION_START_TIME)
        cutoff = _deadline(now, LINGQUAN_QUESTION_CUTOFF)
        exit_time = _deadline(now, LINGQUAN_EXIT_TIME)

        answers = 0
        exiting = False
        # 窗口后补跑只负责离场；窗口内入场超时则不能假装已经进入过。
        entered_question_scene = now >= cutoff
        try:
            from sqlmodel import Session

            from backend.core.fanxiu.choice_knowledge.catalog import (
                load_choice_knowledge_catalog,
            )
            from backend.db import engine

            with Session(engine) as session:
                load_choice_knowledge_catalog(session)
            # 第一次 LuaJIT 管理器定位可能较慢，提前在后台预热；查询本身
            # 不阻塞行为树，预热未完成时答题循环仍使用原有 OCR。
            from backend.core.fanxiu.instrumentation import (
                fanxiu_instrumentation_service,
            )

            fanxiu_instrumentation_service.lingquan_question_snapshot(
                max_age_seconds=2.0,
            )
            # 20:30 入场后，问答页可能要到 20:33 才出现稳定的 #389
            # 身份。窗口内的视觉等待超时只代表需要重新识别/恢复，不能
            # 抛给通用工程失败收尾，否则收尾会点击 #389[返回] 离开答题页。
            while _now() < cutoff:
                try:
                    yield from self._enter_lingquan(
                        runtime,
                        transition_timeout=transition_timeout,
                        deadline=cutoff,
                    )
                    entered_question_scene = True
                    yield from self._wait_lingquan_until(runtime, question_start, poll_seconds=poll_seconds)
                    answers += yield from self._run_lingquan_question_loop(
                        runtime,
                        cutoff=cutoff,
                        transition_timeout=transition_timeout,
                        score_threshold=score_threshold,
                        poll_seconds=poll_seconds,
                    )
                    break
                except TimeoutError as exc:
                    if _now() >= cutoff:
                        break
                    self._log(
                        "warning",
                        f"日常_灵泉：答题窗口内等待超时，保留活动现场并自动恢复 #389：{exc}",
                    )
                    yield from runtime.wait_action_settle(min(1.0, (cutoff - _now()).total_seconds()))
            if not entered_question_scene:
                raise _LingquanWindowExpired("日常_灵泉：入场等待已到答题截止")
            timeout = self._lingquan_timeout(exit_time, transition_timeout)
            yield from runtime.wait_click_then_view(389, "返回", 388, timeout=timeout)
            yield from self._wait_lingquan_until(runtime, exit_time, poll_seconds=poll_seconds)
            exiting = True
            yield from self._exit_lingquan_to_world(
                runtime,
                timeout=float(payload.get("exit_timeout_seconds") or 30.0),
            )
        except _LingquanWindowExpired:
            pass
        except TimeoutError:
            # 20:41 后已没有答题价值；即使尚未到 20:43，也必须按本日
            # 完成收束，不能让 Scheduler 把入口/返回超时当失败反复重跑。
            # 进入离场阶段后则只有真实到达 #34 才能成功；多层离场超时
            # 不能再被窗口收束逻辑吞掉。
            if exiting or _now() < cutoff:
                raise

        # 20:43 开始通过当前场景已标注的「离开」动作返回世界。答题窗口
        # 结束后不再重试入口或题目，但离场仍必须完成，不能把活动场景
        # 留给 #424 通用左下角返回兜底。
        # 灵泉是窗口作业：窗口内能回答多少题就回答多少题。
        # 到达硬截止并正常推进到次日触发即为成功，答题数量只作统计，
        # 不能把 0 题改写成失败并让已结束的窗口继续重试。
        self._log("success", f"日常_灵泉：本日窗口结束，完成 {answers} 道题并已离场")
        runtime.set_next_time(next_time)
        return {
            "result": "success",
            "message": f"日常_灵泉本日窗口已结束，共回答 {answers} 道题；等待明日触发",
            "current_scene": None,
            "answers": answers,
        }
