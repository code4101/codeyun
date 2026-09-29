"""论道任务的执行流程；策略选择由 lundao 领域能力提供。

通过执行器组合取得日志、调度和日常导航能力；本模块不反向导入执行器。
入口和完成判据在同一业务模块内，其他玩法无需理解这里的页面细节。
"""
from __future__ import annotations

import re
import threading
import time
from datetime import (
    datetime,
    time as time_cls,
    timedelta,
)
from pathlib import Path
from typing import (
    Any,
    Mapping,
)

from pyxllib.autogui import View

from .daily_observations import observed_scene_id
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_spatial import (
    find_text_matches,
    group_ocr_tokens,
    locate_text_box,
    query_spatial_ocr,
)
from backend.core.fanxiu.runtime_gui import DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD
from backend.core.fanxiu.data_annotation.job_times import clip_daily_retry_to_window
from backend.core.fanxiu.data_annotation.ocr_values import FULLWIDTH_DIGIT_TRANSLATION
from backend.core.fanxiu.data_annotation.tasks.lundao import (
    LUNDAO_CLOSE_TIME,
    LUNDAO_DALUO_ROOM_ID,
    LUNDAO_FIRST_TRIGGER,
    LUNDAO_SANQING_ROOM_ID,
    current_lundao_player_profile,
    evaluate_lundao_room_opportunity,
    lundao_unique_cjk_name,
    lundao_player_profile_from_runtime,
    lundao_purchase_allowed,
    lundao_safety_threshold,
    next_lundao_daily_trigger,
    next_lundao_recheck,
    next_lundao_unseated_retry,
    plan_lundao_strategy,
    refresh_and_select_lundao_kick_target,
)


# Seat facts may precede dialogue/animation completion; entry and recovery share these scenes.
LUNDAO_SETTLEMENT_SCENE_IDS = (303, 318, 375, 295)


# Unknown scene IDs stay empty; never guess routing destinations.
_DAILY_LUNDAO_STABLE_STATE_SCENE_IDS: dict[str, tuple[int, ...]] = {
    "ready": (),
    "in_progress": (304,),
    "kicked": (391,),
    "completed": (),
}


_DAILY_LUNDAO_ENTRY_LAYER0_SCENE_IDS: tuple[int, ...] = (296, 304, 391)


class LundaoTaskMixin:
    def daily_lundao_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = dict(payload or {})
        now = job_now()
        if lundao_safety_threshold(now) is not None:
            return None
        return self._persist_admission_decision(payload, {
            "result": "success",
            "message": (
                "论道_座位：当前不在 "
                f"{LUNDAO_FIRST_TRIGGER.strftime('%H:%M')}-{LUNDAO_CLOSE_TIME.strftime('%H:%M')} "
                "运行窗口，未执行游戏操作"
            ),
            "next_time": next_lundao_daily_trigger(now).strftime("%Y-%m-%d %H:%M:%S"),
            "current_scene": None,
        })

    def _execute_daily_lundao_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少论道_座位资产树路径，无法执行作业")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)

        scene_match = yield from context.wait_scene([69, 34, 661, 296, 297, 298, 371, 372, 375, 329, 301, 303, 318, 304, 391, 52, 53], wait=5.0)
        (scene_id, _score, frame) = (scene_match.scene_id, scene_match.score, scene_match.frame_data_url)
        # Candidate-set scoring can project a real world frame onto #69 when
        # current-scene closure candidates are included.  Before treating that broad result as
        # authorization to scroll the daily list, arbitrate #69 vs #34 again
        # on the exact same frame.  The list guard remains the final barrier.
        if scene_id == 69:
            anchored_scene_id, anchored_score, _ = context.recognize_scene_in_frame(
                [69, 34],
                frame_data_url=frame,
            )
            if anchored_scene_id in {69, 34}:
                scene_id, _score = anchored_scene_id, anchored_score
        text = context.ocr_text(frame)
        # A room list containing our own seat shows ``离座`` on that row, so
        # the ordinary #297 identity (which expects ``请他让座``) may miss and
        # global recognition can fall through to the background scene (for
        # example #85).  In this branch the visible Lundao roster semantics
        # plus fresh Runtime seat ownership are authoritative; do not route the
        # page through generic world recovery and accidentally click ``离座``.
        if (
            scene_id
            not in {
                296, 297, 298, 371, 372, 375, 329,
                301, 303, 318, 304, 391, 52, 53,
            }
            and self._daily_lundao_is_visible_own_seat_roster(context, frame)
        ):
            current_status = self._daily_lundao_post_seat_status(
                payload,
                reason="daily-lundao-visible-seated-roster",
            )
            if (
                current_status.get("available")
                and current_status.get("complete")
                and current_status.get("seated") is True
                and int(current_status.get("room_id") or 0)
                in {LUNDAO_DALUO_ROOM_ID, LUNDAO_SANQING_ROOM_ID}
            ):
                return (
                    yield from self._finish_daily_lundao_visible_seated_roster(
                        context,
                        payload,
                        current_status,
                    )
                )
            raise RuntimeError(
                "论道_座位：画面为本人已入座名单页，但 Runtime 未确认大罗/三清座位，"
                "已保留现场且未点击离座"
            )
        if scene_id in {34, 661, 69}:
            runtime_guard = yield from self._daily_lundao_world_runtime_guard(
                context,
                payload,
                scene_id=scene_id,
            )
            if runtime_guard is not None:
                return runtime_guard
        if self._daily_lundao_text_is_reward(text):
            scene_id = 52
        if scene_id in {297, 298, 371, 372, 303, 375}:
            result = yield from self._run_daily_lundao_seat_and_leave(context, stop_event, payload=payload)
            return self._finish_daily_lundao_current_scene_action(
                payload,
                result,
                reason=f"从当前中间场景 #{scene_id} 收口完成",
            )
        if scene_id == 318:
            dialogue_text = re.sub(r'\s+', '', text)
            if not (('听道' in dialogue_text or '闻道' in dialogue_text)
                    and self._daily_lundao_runtime_confirms_seated()):
                raise RuntimeError('论道_座位：共享对白 #318 缺少听道语义或实际入座证据，保留现场')
        if scene_id in {329, 301, 303, 318, 52, 53}:
            result = yield from self._complete_daily_lundao_seat_and_leave(context, stop_event, scene_id)
            return self._finish_daily_lundao_current_scene_action(
                payload,
                result,
                reason=f"从当前入座收尾场景 #{scene_id} 收口完成",
            )
        if scene_id == 391:
            entry_result = yield from self._dismiss_daily_lundao_kicked(context)
            scene_id = entry_result.get("scene_id")
        if scene_id == 304:
            return (yield from self._run_daily_lundao_dynamic_strategy_timed(
                context,
                stop_event,
                payload,
                daluo_source_scene_id=304,
            ))
        if scene_id == 296:
            return (yield from self._run_daily_lundao_dynamic_strategy_timed(context, stop_event, payload))
        if scene_id != 69:
            scene_id = yield from self._enter_daily_from_world_like(ctx, context, stop_event, frame, scene_id, text, label="论道_座位")
        if scene_id != 69:
            raise RuntimeError("论道_座位：未能进入 #69 日常列表")
        entry_result = yield from self._enter_daily_lundao_and_route_state(
            ctx,
            stop_event,
            payload,
            context,
        )
        if entry_result["status"] == "not_found":
            return self._finish_daily_lundao_unseated_retry(
                payload,
                reason="#69 暂未找到论道入口，首次落座前立即重试",
            )
        if entry_result["status"] == "kicked":
            entry_result = yield from self._dismiss_daily_lundao_kicked(context)
        scene_id = entry_result.get("scene_id")
        score = float(entry_result.get("score") or 0.0)
        if entry_result["status"] == "in_progress":
            return (yield from self._run_daily_lundao_dynamic_strategy_timed(
                context,
                stop_event,
                payload,
                daluo_source_scene_id=304,
            ))
        if entry_result["status"] == "dojo_selection":
            return (yield from self._run_daily_lundao_dynamic_strategy_timed(context, stop_event, payload))
        if entry_result["status"] in {"unknown", "unimplemented"}:
            raise RuntimeError(
                f"论道_座位：进入后的落点分支尚未实现，当前 "
                f"#{scene_id if scene_id is not None else 'unknown'} {score:.0f}%"
            )
        raise RuntimeError(f"论道_座位：进入论道返回了未处理状态 {entry_result['status']!r}")

    def _enter_daily_lundao_and_route_state(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        """节点 1：从 #69 打开论道并判定四态或道场选择页。

        输入只能是已确认的 #69 日常列表；输出是纯路由结果，不点击道场级别，
        也不执行抢座、结果处理或离场。unknown/未映射落点不得算成功。
        """
        status = yield from self._open_daily_entry_from_daily(
            ctx,
            stop_event,
            payload,
            task_label="论道_座位",
            title_pattern="论道",
            progress_can_mark_done=False,
        )
        if status != "open":
            return {"status": status, "scene_id": None, "score": 0.0}

        yield from context.wait_scene(
            _DAILY_LUNDAO_ENTRY_LAYER0_SCENE_IDS,
            wait=20.0,
            label="论道_座位：等待道场选择/闻道中/被踢状态",
        )
        _wait_scene_match = yield from context.wait_scene(_DAILY_LUNDAO_ENTRY_LAYER0_SCENE_IDS, wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        return self._route_daily_lundao_entry_scene(scene_id, score, frame)

    def _route_daily_lundao_entry_scene(
        self,
        scene_id: int | None,
        score: float,
        frame: str,
    ) -> dict[str, Any]:
        if scene_id in _DAILY_LUNDAO_STABLE_STATE_SCENE_IDS["ready"]:
            return {"status": "ready", "stable_state": "ready", "scene_id": scene_id, "score": float(score)}
        if scene_id in _DAILY_LUNDAO_STABLE_STATE_SCENE_IDS["in_progress"]:
            return {"status": "in_progress", "stable_state": "in_progress", "scene_id": scene_id, "score": float(score)}
        if scene_id in _DAILY_LUNDAO_STABLE_STATE_SCENE_IDS["kicked"]:
            return {"status": "kicked", "stable_state": "kicked", "scene_id": scene_id, "score": float(score)}
        if scene_id == 296:
            return {"status": "dojo_selection", "scene_id": scene_id, "score": float(score)}
        if scene_id is None:
            return {"status": "unknown", "scene_id": None, "score": float(score)}
        # 新的正式落点先显式失败；待用户给出业务语义后再加入四态映射或独立路由。
        return {"status": "unimplemented", "scene_id": scene_id, "score": float(score)}

    def _dismiss_daily_lundao_kicked(self, context: Any) -> dict[str, Any]:
        """确认 #391「被踢了」提示，再按同一组稳定状态重新路由。"""

        post_kick_scene_ids = [296, 304]
        yield from context.wait_click_then_scene(
            391,
            "确认",
            post_kick_scene_ids,
            settle_seconds=1.5,
            timeout=20.0,
        )
        _wait_scene_match = yield from context.wait_scene(post_kick_scene_ids, wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        return self._route_daily_lundao_entry_scene(scene_id, score, frame)

    def _record_daily_lundao_next_time(
        self,
        payload: Mapping[str, Any],
        next_time: str,
        *,
        reason: str,
    ) -> str:
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "daily-lundao-seat"),
            next_time,
        )
        self._log("success", f"论道_座位：{reason}，下次 {next_time}")
        return next_time

    def _finish_daily_lundao_current_scene_action(
        self,
        payload: Mapping[str, Any],
        result: str,
        *,
        reason: str,
    ) -> str:
        """Close Scheduler intent after consuming a provable current scene."""

        status = self._read_daily_lundao_execution_status(payload)
        now = job_now()
        left_time = status.get("current_left_listen_time")
        room_id = status.get("room_id")
        next_at = (
            next_lundao_daily_trigger(now)
            if (
                left_time is not None
                and (
                    int(left_time) <= 0
                    or int(room_id or 0) == LUNDAO_DALUO_ROOM_ID
                )
            )
            else next_lundao_recheck(now)
        )
        room_text = f"，当前道场 room_id={room_id}" if room_id is not None else ""
        self._record_daily_lundao_next_time(
            payload,
            next_at.strftime("%Y-%m-%d %H:%M:%S"),
            reason=f"{reason}{room_text}",
        )
        return result

    def _finish_daily_lundao_unseated_retry(
        self,
        payload: Mapping[str, Any],
        *,
        reason: str,
    ) -> str:
        """Record a business miss as due-now, then finish the trigger normally.

        ``success`` here means the Scheduler-triggered Cell completed its
        contract.  It does not claim that the first Lundao seat was obtained.
        """

        retry_at = next_lundao_unseated_retry(
            job_now()
        ).strftime("%Y-%m-%d %H:%M:%S")
        self._record_daily_lundao_next_time(
            payload,
            retry_at,
            reason=reason,
        )
        return "success"

    def _finish_daily_lundao_changed_sanqing_target(
        self,
        context: Any,
        payload: Mapping[str, Any],
    ):
        """Finish safely when the selected Sanqing occupant moved before click."""

        yield from self._return_daily_lundao_to_selection(context, 297)
        yield from context.go_scene(34)
        return self._finish_daily_lundao_unseated_retry(
            payload,
            reason="三清目标已变化，未执行入座，10分钟后继续检查",
        )

    def _daily_lundao_first_visible_dojo(self, context: Any) -> str:
        frame = context.cur_frame(update=True)
        tokens = context.ocr_tokens_in_shapes(296, ["至尊道场"], frame_data_url=frame)
        fragments = group_ocr_tokens(tokens)
        hits: list[str] = []
        for fragment in fragments:
            text = re.sub(r"\s+", "", _sanitize_ocr_text(fragment.get("text")))
            for name in ("至尊", "大罗", "三清"):
                if name in text:
                    hits.append(name)
        unique = list(dict.fromkeys(hits))
        if len(unique) != 1:
            raise RuntimeError(f"论道_座位：#296 第一行道场 OCR 不唯一，命中={unique or '空'}，已停止且未点击")
        return unique[0]

    def _click_daily_lundao_dojo(
        self,
        context: Any,
        target: str,
        *,
        source_scene_id: int = 296,
    ) -> None:
        if source_scene_id == 304:
            # #304 is already an independently identified Lundao in-progress
            # scene.  Its stylized dojo label is commonly OCR'd as the stable
            # fragment ``大罗`` without the optional ``道场`` suffix.  Keep
            # the action bound to #304 and require exactly one spatial line;
            # the fragment only recovers a candidate and never promotes an
            # unknown frame or permits an ambiguous click.
            stable_target = target.removesuffix("道场") or target
            frame = context.cur_frame(update=True)
            # The dojo tabs are controls, not #304 identity shapes.  Prefer
            # the shared full-frame token cache so candidate collection does
            # not silently exclude them; this does not launch another OCR
            # pass.  Legacy/test adapters fall back to their token method.
            ocr_tokens = getattr(context, "full_frame_ocr_tokens", None)
            if not callable(ocr_tokens):
                ocr_tokens = getattr(context, "ocr_tokens", None)
            if callable(ocr_tokens):
                # Consume the real token boxes so ``大`` and ``罗`` may be
                # reconstructed without assuming OCR returns one full line.
                # Exact token matches also enumerate duplicate labels instead
                # of silently selecting the first one.
                candidates = [
                    {
                        "x": match.x,
                        "y": match.y,
                        "w": match.w,
                        "h": match.h,
                    }
                    for match in find_text_matches(
                        ocr_tokens(frame),
                        stable_target,
                    )
                ]
            else:
                # Test/legacy adapter fallback; production Runtime exposes
                # token-level OCR and therefore takes the branch above.
                lines = context.ocr_lines(frame_data_url=frame)
                candidates = [
                    line
                    for line in lines
                    if stable_target in re.sub(r"\s+", "", _sanitize_ocr_text(line.get("text")))
                    and float(line.get("w") or 0) > 0
                    and float(line.get("h") or 0) > 0
                ]
            if len(candidates) != 1:
                raise RuntimeError(
                    f"论道_座位：#304 未唯一识别「{target}」，"
                    f"候选={len(candidates)}，已停止且未点击"
                )
            line = candidates[0]
            context.click_frame_point(
                304,
                float(line.get("x") or 0) + float(line.get("w") or 0) / 2,
                float(line.get("y") or 0) + float(line.get("h") or 0) / 2,
            )
            return
        if source_scene_id != 296:
            raise RuntimeError(f"论道_座位：不支持从 #{source_scene_id} 选择「{target}」")
        order = ("至尊", "大罗", "三清", "御界")
        first = self._daily_lundao_first_visible_dojo(context)
        slot = order.index(target) - order.index(first) + 1
        slot_shapes = {1: "至尊道场", 2: "大罗道场", 3: "三清道场"}
        shape = slot_shapes.get(slot)
        if shape is None:
            raise RuntimeError(f"论道_座位：#296 当前首行为「{first}」，目标「{target}」不在可见三行，已停止")
        context.click_shape_center(296, shape)

    def _click_daily_lundao_dojo_with_stable_ocr(
        self,
        context: Any,
        stop_event: threading.Event,
        target: str,
        *,
        source_scene_id: int,
        attempts: int = 4,
        retry_seconds: float = 1.0,
    ):
        """Retry only a transient zero-candidate OCR result on identified #304.

        The independently identified scene remains the action boundary.  A
        duplicate candidate is an ambiguity and therefore fails immediately;
        repeated zero-candidate frames fail closed after the bounded local
        retry instead of forcing the Scheduler to restart the whole Job.
        """

        started_at = time.perf_counter()
        bounded_attempts = max(1, min(int(attempts), 8))
        for attempt in range(1, bounded_attempts + 1):
            self._raise_if_stopped(stop_event)
            try:
                self._click_daily_lundao_dojo(
                    context,
                    target,
                    source_scene_id=source_scene_id,
                )
            except RuntimeError as exc:
                if "候选=0" not in str(exc) or attempt >= bounded_attempts:
                    self._log(
                        "detail",
                        "论道_座位：阶段[#304稳定识别大罗]结束 "
                        f"elapsed={time.perf_counter() - started_at:.2f}s "
                        f"attempt={attempt}/{bounded_attempts} result=failed",
                    )
                    raise
                self._log(
                    "wait",
                    "论道_座位：#304 本帧未读到大罗，保持当前场景且不点击，"
                    f"等待新鲜帧 {attempt}/{bounded_attempts}",
                )
                yield from context.wait_action_settle(max(0.0, float(retry_seconds)))
                continue
            self._log(
                "detail",
                "论道_座位：阶段[#304稳定识别大罗]完成 "
                f"elapsed={time.perf_counter() - started_at:.2f}s "
                f"attempt={attempt}/{bounded_attempts}",
            )
            return

    def _refresh_daily_lundao_runtime_facts(self, *, reason: str, room_id: int | None = None) -> dict[str, Any]:
        started_at = time.perf_counter()
        from backend.core.fanxiu.instrumentation.lundao import read_lundao_snapshot

        status = read_lundao_snapshot()
        selected_room_id = int(room_id or status.get("room_id") or 0)
        roster_key = "daluo_roster" if selected_room_id == LUNDAO_DALUO_ROOM_ID else "sanqing_roster"
        roster = status.get(roster_key) if isinstance(status.get(roster_key), dict) else {}
        facts = {"status": status, "roster": roster, "source": "runtime_memory"}
        facts["elapsed_seconds"] = time.perf_counter() - started_at
        self._log(
            "detail",
            f"论道_座位：按需读取 Runtime {reason} 用时 {facts['elapsed_seconds']:.2f}s",
        )
        return facts

    @staticmethod
    def _daily_lundao_runtime_catch_up_status(facts: Mapping[str, Any]) -> str:
        catch_up = facts.get("runtime_catch_up")
        if not isinstance(catch_up, Mapping):
            return "unknown"
        result = catch_up.get("result")
        nested = result.get("result") if isinstance(result, Mapping) else None
        for candidate in (nested, result, catch_up):
            if not isinstance(candidate, Mapping):
                continue
            value = str(candidate.get("status") or candidate.get("reason") or "").strip()
            if value:
                return value
        return "unknown"

    def _wait_daily_lundao_room_facts(
        self,
        context: Any,
        *,
        reason: str,
        room_id: int,
        baseline_key: tuple[Any, ...] = (),
        wait_seconds: float = 120.0,
        settle_retries: int = 15,
    ):
        """等待 Runtime 模型切换到刚点击的论道道场。"""

        facts = self._refresh_daily_lundao_runtime_facts(
            reason=reason,
            room_id=room_id,
        )
        for retry in range(max(0, int(settle_retries)) + 1):
            roster = facts.get("roster") if isinstance(facts.get("roster"), dict) else {}
            roster_key = tuple((roster.get("evidence") or {}).get("order_key") or ())
            if (
                bool(roster.get("available"))
                and bool(roster.get("complete"))
                and int(roster.get("room_id") or 0) == int(room_id)
                and bool(roster_key)
                and (not baseline_key or roster_key > baseline_key)
            ):
                return facts
            if self._daily_lundao_runtime_catch_up_status(facts) == "ingestion_busy":
                # This is explicit back-pressure from the Runtime ingestion
                # owner, not a GUI transition that can settle after another
                # second.  Return the evidence immediately so the caller can
                # preserve the original trigger and let Scheduler retry the
                # whole transaction, instead of burning 15 identical polls.
                return facts
            if retry >= max(0, int(settle_retries)):
                break
            yield from context.wait_action_settle(1.0)
            facts = self._refresh_daily_lundao_runtime_facts(reason=reason, room_id=room_id)
        return facts

    def _daily_lundao_room_available_count(self, status: Mapping[str, Any], room_id: int) -> int | None:
        values = status.get("room_available_counts") if isinstance(status.get("room_available_counts"), Mapping) else {}
        value = values.get(str(room_id))
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _daily_lundao_remaining_attempts(self, context: Any) -> int:
        values, normalized = context.ocr_numbers_in_shapes(296, ["次数"], padding=8)
        if not values:
            raise RuntimeError(f"论道_座位：未能从 #296[次数] 读取首个数值，OCR={normalized[:80]}")
        return values[0]

    def _buy_one_daily_lundao_attempt(
        self,
        context: Any,
        *,
        before: int,
        at: datetime,
    ) -> dict[str, Any]:
        """Buy at most one attempt and verify the #296 counter increased."""

        if before > 0:
            return {"ready": True, "purchased": False, "before": before, "after": before}
        if not lundao_purchase_allowed(at):
            self._log(
                "skip",
                "论道_座位：#296[次数]分子为 0 且已到 21:00，禁止购买次数，本轮不再入座或升级",
            )
            return {
                "ready": False,
                "purchased": False,
                "before": before,
                "after": before,
                "reason": "purchase_cutoff",
            }
        self._log("action", "论道_座位：座位动作已确认且次数为 0，点击 #296[购买]")
        context.click_shape_center(296, "购买")
        yield from context.wait_scene([392], wait=15.0, label="论道_座位：等待购买次数页 #392")
        self._log("action", "论道_座位：只购买 1 次")
        context.click_shape_center(392, "购买")
        yield from context.wait_action_settle(1.5)
        _wait_scene_match = yield from context.wait_scene([392], wait=5.0, required=False)
        (scene_id, score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        scene_id = observed_scene_id(scene_id)
        if scene_id != 392:
            raise RuntimeError(
                f"论道_座位：点击 #392[购买] 后出现未知结果，scene={scene_id} score={float(score):.0f}%，"
                "已停止且不会重复购买"
            )
        context.click_shape_center(392, "返回")
        yield from context.wait_scene([296], wait=15.0, label="论道_座位：购买后返回 #296")
        after = self._daily_lundao_remaining_attempts(context)
        if after <= before:
            self._log(
                "warning",
                f"论道_座位：购买后次数未增加（{before}->{after}），按购买额度不足处理，本轮不再购买",
            )
            return {"ready": False, "purchased": False, "before": before, "after": after, "reason": "purchase_unavailable"}
        self._log("success", f"论道_座位：购买 1 次成功，剩余次数 {before}->{after}")
        return {"ready": True, "purchased": True, "before": before, "after": after}

    def _return_daily_lundao_to_selection(self, context: Any, scene_id: int) -> int:
        # #298 is the empty-seat variant of the same room list; its full-screen
        # reference has no duplicate return annotation, so use the shared #297
        # room-list return control already present at the same stable location.
        context.click_shape_center(297, "返回")
        return (yield from context.wait_scene([296, 304], wait=15.0, label="论道_座位：返回道场选择"))

    def _confirm_daily_lundao_seated_after_return(self, context: Any, initial_scene: int) -> int:
        """Resolve the short #296 -> #304 transition without trusting packet state."""

        scene_id = observed_scene_id(initial_scene)
        if scene_id == 304:
            return 304
        for _attempt in range(3):
            yield from context.wait_action_settle(2.0)
            _wait_scene_match = yield from context.wait_scene([296, 304], wait=5.0, required=False)
            (detected, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            scene_id = observed_scene_id(detected)
            if scene_id == 304:
                return 304
            ocr_text = getattr(context, "ocr_text", None)
            if callable(ocr_text) and self._daily_lundao_text_is_seated(ocr_text(frame)):
                return 304
        return scene_id

    def _run_daily_lundao_room_action(
        self,
        context: Any,
        stop_event: threading.Event,
        *,
        opportunity: Mapping[str, Any],
    ) -> str:
        scene_match = yield from context.wait_scene([297, 298], wait=5.0)
        (scene_id, score, _frame) = (scene_match.scene_id, scene_match.score, scene_match.frame_data_url)
        action = str(opportunity.get("action") or "")
        # The Runtime roster can change between planning and the final fresh
        # layer-0 read.  #298 is itself authoritative evidence that an empty
        # seat is currently available, so it is safe to discard an older kick
        # target and rebuild the action as the target-free empty-seat flow.
        # The inverse is not safe: #297 does not identify a player target, so
        # an older ``empty`` decision must abort this room attempt rather than
        # being upgraded to a kick from stale facts.
        if scene_id == 298 and action in {"empty", "kick"}:
            scene_id, score = yield from self._run_daily_lundao_empty_seat_strategy(context)
        elif scene_id == 297 and action == "empty":
            return "target_changed"
        elif action == "kick" and scene_id == 297:
            target = opportunity.get("target") if isinstance(opportunity.get("target"), Mapping) else None
            if target is None:
                raise RuntimeError("论道_座位：策略要求踢人但没有合法目标，已停止")
            kick_result = yield from self._run_daily_lundao_kick_for_seat_strategy(
                context,
                stop_event,
                target_player=target,
                room_id=int(
                    opportunity.get("room_id") or LUNDAO_DALUO_ROOM_ID
                ),
            )
            if kick_result.get("status") == "target_changed":
                return "target_changed"
            scene_id = int(kick_result.get("scene_id") or 52)
            score = float(kick_result.get("score") or 0.0)
        else:
            raise RuntimeError(f"论道_座位：策略动作 {action!r} 与当前场景 #{scene_id} 不一致，已停止")
        return (yield from self._complete_daily_lundao_seat_and_leave(context, stop_event, scene_id, score))

    def _run_daily_lundao_dynamic_strategy_timed(
        self,
        context: Any,
        stop_event: threading.Event,
        payload: Mapping[str, Any],
        **kwargs: Any,
    ):
        """Record one complete strategy phase, including failed resumptions."""

        started_at = time.perf_counter()
        result = "failed"
        try:
            value = yield from self._run_daily_lundao_dynamic_strategy(
                context,
                stop_event,
                payload,
                **kwargs,
            )
            result = str(value)
            return value
        finally:
            self._log(
                "detail",
                "论道_座位：阶段[动态策略]结束 "
                f"elapsed={time.perf_counter() - started_at:.2f}s result={result}",
            )

    def _rebuild_daily_lundao_strategy_after_attempt_check(
        self,
        context: Any,
        stop_event: threading.Event,
        payload: Mapping[str, Any],
        *,
        source_scene_id: int,
        purchase_used: bool,
    ):
        """Rebuild the roster without losing the actual selection-page kind."""

        if source_scene_id not in {296, 304}:
            raise RuntimeError(
                "论道_座位：确认挑战次数后未回到道场选择/闻道中页面，"
                f"当前 #{source_scene_id}，已停止且未点击"
            )
        return (
            yield from self._run_daily_lundao_dynamic_strategy(
                context,
                stop_event,
                payload,
                attempt_ready=True,
                purchase_used=purchase_used,
                daluo_source_scene_id=source_scene_id,
            )
        )

    def _run_daily_lundao_dynamic_strategy(
        self,
        context: Any,
        stop_event: threading.Event,
        payload: Mapping[str, Any],
        *,
        attempt_ready: bool = False,
        purchase_used: bool = False,
        daluo_source_scene_id: int = 296,
    ) -> str:
        now = job_now()
        from backend.core.fanxiu.instrumentation.lundao import (
            read_lundao_snapshot,
        )

        runtime_override = payload.get("__lundao_runtime_snapshot_override")
        execution_status = (
            dict(runtime_override)
            if isinstance(runtime_override, Mapping)
            else read_lundao_snapshot()
        )
        if execution_status.get("available") and execution_status.get("complete"):
            self._log(
                "detail",
                "论道_座位：已从游戏 Runtime 读取剩余闻道时间、"
                "自身座位与房间空位",
            )
            runtime_decision = plan_lundao_strategy(
                execution_status,
                daluo_opportunity=None,
                at=now,
            )
            if runtime_decision.get("action") in {"done", "stay_daluo"}:
                yield from context.go_scene(34)
                next_time = runtime_decision["next_time"].strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                self._record_daily_lundao_next_time(
                    payload,
                    next_time,
                    reason=(
                        f"Runtime：{runtime_decision.get('reason')}"
                    ),
                )
                return "success"
        else:
            self._log(
                "detail",
                "论道_座位：Runtime 状态尚未加载，先打开道场触发模型初始化，"
                f"reason={execution_status.get('reason') or 'incomplete'}",
            )
        # Opening Daluo is read-only and initializes the Runtime room roster.
        initial = self._refresh_daily_lundao_runtime_facts(
            reason="daily-lundao-before-daluo",
            room_id=LUNDAO_DALUO_ROOM_ID,
        )
        baseline = initial.get("roster") if isinstance(initial.get("roster"), dict) else {}
        baseline_key = tuple((baseline.get("evidence") or {}).get("order_key") or ())
        if daluo_source_scene_id == 304:
            yield from self._click_daily_lundao_dojo_with_stable_ocr(
                context,
                stop_event,
                "大罗道场",
                source_scene_id=304,
                attempts=int(payload.get("scene304_ocr_attempts") or 4),
                retry_seconds=float(payload.get("scene304_ocr_retry_seconds") or 1.0),
            )
        else:
            self._click_daily_lundao_dojo(context, "大罗")
        try:
            yield from context.wait_scene([297, 298], wait=15.0, label="论道_座位：等待大罗座位列表")
        except TimeoutError as exc:
            # A player already seated in Daluo has a `离座` control on their own
            # row, so the ordinary #297 identity (which expects `请他让座`) can
            # be only a partial visual match.  Opening the page is still enough
            # to initialize the Runtime model. Continue read-only and let
            # Runtime decide whether to stay/finish; no seat click is
            # allowed unless the normal actionable list is identified later.
            self._log("warning", f"论道_座位：大罗名单页未完整匹配，继续只读 Runtime 核对：{exc}")
        runtime_after_open = (
            dict(runtime_override)
            if isinstance(runtime_override, Mapping)
            else read_lundao_snapshot()
        )
        runtime_roster = (
            runtime_after_open.get("daluo_roster")
            if isinstance(runtime_after_open.get("daluo_roster"), dict)
            else {}
        )
        runtime_roster_used = bool(
            runtime_after_open.get("available")
            and runtime_after_open.get("complete")
            and runtime_roster.get("available")
            and runtime_roster.get("complete")
        )
        if runtime_roster_used:
            self._log(
                "detail",
                "论道_座位：已从游戏 Runtime 读取完整大罗座位名单，"
                "直接进入决策",
            )
            refreshed = {
                "status": runtime_after_open,
                "roster": runtime_roster,
            }
        else:
            self._log(
                "detail",
                "论道_座位：Runtime 大罗名单尚未加载，等待 Runtime 模型完成，"
                f"reason={runtime_roster.get('reason') or runtime_after_open.get('reason') or 'incomplete'}",
            )
            refreshed = yield from self._wait_daily_lundao_room_facts(
                context,
                reason="daily-lundao-daluo-roster",
                room_id=LUNDAO_DALUO_ROOM_ID,
                baseline_key=baseline_key,
                wait_seconds=120.0,
            )
        status = refreshed.get("status") if isinstance(refreshed.get("status"), dict) else {}
        authoritative_runtime = (
            runtime_after_open
            if runtime_after_open.get("available") and runtime_after_open.get("complete")
            else execution_status
        )
        if authoritative_runtime.get("available") and authoritative_runtime.get("complete"):
            status = {
                **status,
                **{
                    key: authoritative_runtime.get(key)
                    for key in (
                        "available",
                        "complete",
                        "strength",
                        "room_id",
                        "seat_id",
                        "seated",
                        "left_listen_time",
                        "current_left_listen_time",
                        "sit_down_time",
                        "rooms",
                        "room_available_counts",
                        "source",
                        "protocol",
                        "evidence",
                    )
                },
            }
        roster = refreshed.get("roster") if isinstance(refreshed.get("roster"), dict) else {}
        roster_key = tuple((roster.get("evidence") or {}).get("order_key") or ())
        fresh_status_and_roster = (
            bool(status.get("available"))
            and int(roster.get("room_id") or 0) == LUNDAO_DALUO_ROOM_ID
            and bool(roster_key)
            and (
            not baseline_key or roster_key > baseline_key
            )
        )
        if not fresh_status_and_roster:
            return_scene = yield from self._return_daily_lundao_to_selection(context, 297)
            # #296 can be a transient match while the already-seated view is
            # settling to #304. Runtime incompleteness must not gate the
            # independent visual confirmation.
            return_scene = yield from self._confirm_daily_lundao_seated_after_return(context, return_scene)
            yield from context.go_scene(34)
            if return_scene == 304:
                next_at = next_lundao_recheck(job_now())
                next_time = next_at.strftime("%Y-%m-%d %H:%M:%S")
                catch_up_status = self._daily_lundao_runtime_catch_up_status(refreshed)
                self._record_daily_lundao_next_time(
                    payload,
                    next_time,
                    reason=(
                        f"已在闻道中，Runtime暂未追平（{catch_up_status}），"
                        "按正常半小时复查"
                    ),
                )
                return "success"
            catch_up_status = self._daily_lundao_runtime_catch_up_status(refreshed)
            raise RuntimeError(
                "论道_座位：Runtime 状态或座位名单不完整，"
                "保留原触发时间立即整单重试；"
                f"runtime_catch_up={catch_up_status}"
            )

        # Completion and an existing Daluo seat need only fresh status facts;
        # neither branch should be blocked by the seated-row visual variant or
        # by a roster that is irrelevant to a no-op decision.
        status_decision = plan_lundao_strategy(status, daluo_opportunity=None, at=now)
        if status_decision.get("action") in {"done", "stay_daluo"}:
            yield from self._return_daily_lundao_to_selection(context, 297)
            yield from context.go_scene(34)
            next_time = status_decision["next_time"].strftime("%Y-%m-%d %H:%M:%S")
            self._record_daily_lundao_next_time(payload, next_time, reason=str(status_decision.get("reason")))
            return "success"
        if (
            not runtime_roster_used
            and baseline_key
            and roster_key
            and roster_key <= baseline_key
        ):
            raise RuntimeError("论道_座位：进入大罗后未获得更新的座位名单，已停止且未点击座位")
        profile = lundao_player_profile_from_runtime(authoritative_runtime)
        if not profile.get("available"):
            profile = current_lundao_player_profile()
        opportunity = evaluate_lundao_room_opportunity(
            roster,
            player_profile=profile,
            available_count=self._daily_lundao_room_available_count(status, LUNDAO_DALUO_ROOM_ID),
            at=now,
            room_id=LUNDAO_DALUO_ROOM_ID,
            require_safety_threshold=True,
        )
        decision = plan_lundao_strategy(status, daluo_opportunity=opportunity, at=now)
        self._log(
            "info",
            f"论道_座位：大罗 x={opportunity.get('safety_score')}/{opportunity.get('threshold')}，"
            f"空位={opportunity.get('available_count')}，合法目标={opportunity.get('eligible_count')}，决策={decision.get('action')}",
        )
        if not opportunity.get("ok"):
            yield from self._return_daily_lundao_to_selection(context, 297)
            yield from context.go_scene(34)
            raise RuntimeError(
                "论道_座位：大罗事实不足，保留原触发时间立即整单重试，"
                f"reason={opportunity.get('reason') or 'unknown'}"
            )
        if decision.get("action") == "done":
            yield from self._return_daily_lundao_to_selection(context, 297)
            yield from context.go_scene(34)
            next_time = decision["next_time"].strftime("%Y-%m-%d %H:%M:%S")
            self._record_daily_lundao_next_time(payload, next_time, reason=str(decision.get("reason")))
            return "success"
        returned_to_selection = False
        fallback_without_attempt = False
        if decision.get("action") == "seat_daluo":
            if not attempt_ready:
                selection_source_scene_id = yield from self._return_daily_lundao_to_selection(context, 297)
                returned_to_selection = True
                remaining = self._daily_lundao_remaining_attempts(context)
                attempt = {"ready": remaining > 0, "purchased": False, "before": remaining, "after": remaining}
                if remaining <= 0:
                    if purchase_used:
                        attempt = {"ready": False, "reason": "purchase_already_attempted"}
                    else:
                        attempt = yield from self._buy_one_daily_lundao_attempt(context, before=remaining, at=now)
                if not attempt.get("ready"):
                    if attempt.get("reason") == "purchase_cutoff":
                        yield from context.go_scene(34)
                        next_at = next_lundao_daily_trigger(now)
                        self._record_daily_lundao_next_time(
                            payload,
                            next_at.strftime("%Y-%m-%d %H:%M:%S"),
                            reason="21点后免费次数为0，不再购买或争取最后一小时收益",
                        )
                        return "success"
                    # Challenge attempts are required for kicking a player, not
                    # for occupying a visible empty seat. Before the cost cutoff,
                    # a non-cutoff purchase failure may still fall back to a
                    # visible Sanqing empty seat without spending an attempt.
                    fallback_without_attempt = True
                    status = {**status, "seated": False, "room_id": None, "seat_id": None}
                    self._log("warning", "论道_座位：大罗需要挑战次数但当前不可购买，本轮继续检查三清空位")
                else:
                    # Purchasing or merely confirming an available attempt is not
                    # permission to use the old roster. Re-open Daluo and rebuild
                    # the decision from a new packet baseline before any seat click.
                    return (
                        yield from self._rebuild_daily_lundao_strategy_after_attempt_check(
                            context,
                            stop_event,
                            payload,
                            source_scene_id=int(observed_scene_id(selection_source_scene_id)),
                            purchase_used=purchase_used or bool(attempt.get("purchased")),
                        )
                    )
            if attempt_ready:
                result = yield from self._run_daily_lundao_room_action(context, stop_event, opportunity=opportunity)
                if result == "target_changed":
                    # The selected Daluo player disappeared before the click.  No
                    # seat transaction happened, so do not pretend the previous
                    # Sanqing seat still exists and do not end this Job.  Continue
                    # below into the normal Sanqing fallback in the same Cell.
                    status = {**status, "seated": False, "room_id": None, "seat_id": None}
                    self._log("warning", "论道_座位：大罗目标已变化，当前无座，本轮立即降级尝试三清")
                else:
                    after_status = self._daily_lundao_post_seat_status(
                        payload,
                        reason="daily-lundao-after-seat",
                    )
                    self._require_daily_lundao_expected_room(
                        after_status,
                        LUNDAO_DALUO_ROOM_ID,
                        label="大罗入座",
                    )
                    after_left_time = after_status.get("current_left_listen_time")
                    completed_at = job_now()
                    # Daluo is the terminal target seat.  A same-day rerun is
                    # now requested only by a newly observed eviction mail.
                    next_at = next_lundao_daily_trigger(completed_at)
                    self._record_daily_lundao_next_time(payload, next_at.strftime("%Y-%m-%d %H:%M:%S"), reason="已完成大罗入座")
                    return result

        current_room = int(status.get("room_id") or 0)
        if not returned_to_selection:
            yield from self._return_daily_lundao_to_selection(context, 297)
        if current_room == LUNDAO_SANQING_ROOM_ID:
            yield from context.go_scene(34)
            next_at = decision.get("next_time") if isinstance(decision.get("next_time"), datetime) else next_lundao_recheck(now)
            self._record_daily_lundao_next_time(payload, next_at.strftime("%Y-%m-%d %H:%M:%S"), reason="大罗条件不足，保留三清")
            return "success"

        if not attempt_ready and not fallback_without_attempt:
            remaining = self._daily_lundao_remaining_attempts(context)
            attempt = {"ready": remaining > 0, "purchased": False, "before": remaining, "after": remaining}
            if remaining <= 0:
                if purchase_used:
                    attempt = {"ready": False, "reason": "purchase_already_attempted"}
                else:
                    attempt = yield from self._buy_one_daily_lundao_attempt(context, before=remaining, at=now)
            if not attempt.get("ready"):
                yield from context.go_scene(34)
                purchase_cutoff = attempt.get("reason") == "purchase_cutoff"
                next_at = (
                    next_lundao_daily_trigger(now)
                    if purchase_cutoff
                    else datetime.combine(now.date(), time_cls(22, 0))
                )
                self._record_daily_lundao_next_time(
                    payload,
                    next_at.strftime("%Y-%m-%d %H:%M:%S"),
                    reason=(
                        "21点后免费次数为0，禁止购买并放弃本轮三清入座"
                        if purchase_cutoff
                        else "确需三清入座，但今日已无可用次数"
                    ),
                )
                return "success"
            attempt_ready = True

        before_sanqing = self._refresh_daily_lundao_runtime_facts(
            reason="daily-lundao-before-sanqing",
            room_id=LUNDAO_SANQING_ROOM_ID,
        )
        before_sanqing_roster = (
            before_sanqing.get("roster")
            if isinstance(before_sanqing.get("roster"), dict)
            else {}
        )
        before_sanqing_key = tuple(
            (before_sanqing_roster.get("evidence") or {}).get("order_key") or ()
        )
        self._click_daily_lundao_dojo(context, "三清")
        sanqing_match = yield from context.wait_scene(
            [297, 298],
            wait=15.0,
            label="论道_座位：等待三清座位列表",
        )
        sanqing_scene = observed_scene_id(sanqing_match)
        if (
            sanqing_scene not in {297, 298}
            and self._daily_lundao_is_visible_own_seat_roster(
                context,
                sanqing_match.frame_data_url,
            )
        ):
            current_status = self._daily_lundao_post_seat_status(
                payload,
                reason="daily-lundao-sanqing-visible-seated-roster",
            )
            self._require_daily_lundao_expected_room(
                current_status,
                LUNDAO_SANQING_ROOM_ID,
                label="三清本人座位页",
            )
            yield from self._return_daily_lundao_to_selection(context, 297)
            yield from context.go_scene(34)
            next_at = next_lundao_recheck(job_now())
            self._record_confirmed_daily_lundao_seat(
                payload,
                current_status,
                expected_room_id=LUNDAO_SANQING_ROOM_ID,
                next_time=next_at.strftime("%Y-%m-%d %H:%M:%S"),
                label="三清本人座位页",
                reason="三清名单页与 Runtime 已确认当前座位，按正常半小时复查",
            )
            return "success"
        if sanqing_scene == 298:
            result = yield from self._run_daily_lundao_room_action(
                context,
                stop_event,
                opportunity={"action": "empty"},
            )
            after_status = self._daily_lundao_post_seat_status(
                payload,
                reason="daily-lundao-after-sanqing-empty-seat",
            )
            next_at = next_lundao_recheck(job_now())
            self._record_confirmed_daily_lundao_seat(
                payload,
                after_status,
                expected_room_id=LUNDAO_SANQING_ROOM_ID,
                next_time=next_at.strftime("%Y-%m-%d %H:%M:%S"),
                label="三清入座",
                reason="三清画面确认有空位，已直接入座",
            )
            return result
        if fallback_without_attempt:
            yield from self._return_daily_lundao_to_selection(context, 297)
            yield from context.go_scene(34)
            return self._finish_daily_lundao_unseated_retry(
                payload,
                reason="三清当前满座且没有可用挑战次数，10分钟后继续检查空位",
            )
        sanqing_facts = yield from self._wait_daily_lundao_room_facts(
            context,
            reason="daily-lundao-sanqing-roster",
            room_id=LUNDAO_SANQING_ROOM_ID,
            baseline_key=before_sanqing_key,
            wait_seconds=120.0,
        )
        sanqing_status = sanqing_facts.get("status") if isinstance(sanqing_facts.get("status"), dict) else {}
        sanqing_roster = sanqing_facts.get("roster") if isinstance(sanqing_facts.get("roster"), dict) else {}
        sanqing = evaluate_lundao_room_opportunity(
            sanqing_roster,
            player_profile=profile,
            available_count=self._daily_lundao_room_available_count(sanqing_status, LUNDAO_SANQING_ROOM_ID),
            at=now,
            room_id=LUNDAO_SANQING_ROOM_ID,
            require_safety_threshold=False,
        )
        if not sanqing.get("ok"):
            yield from self._return_daily_lundao_to_selection(context, 297)
            yield from context.go_scene(34)
            raise RuntimeError(
                "论道_座位：三清事实不足，不能误判为无座位，保留原触发时间重试，"
                f"reason={sanqing.get('reason') or 'unknown'}"
            )
        if not sanqing.get("actionable"):
            yield from self._return_daily_lundao_to_selection(context, 297)
            yield from context.go_scene(34)
            return self._finish_daily_lundao_unseated_retry(
                payload,
                reason="尚未落座，保持立即到期并继续争取三清座位",
            )
        result = yield from self._run_daily_lundao_room_action(context, stop_event, opportunity=sanqing)
        if result == "target_changed":
            return (
                yield from self._finish_daily_lundao_changed_sanqing_target(
                    context,
                    payload,
                )
            )
        after_status = self._daily_lundao_post_seat_status(
            payload,
            reason="daily-lundao-after-sanqing-seat",
        )
        after_left_time = after_status.get("current_left_listen_time")
        completed_at = job_now()
        next_at = (
            next_lundao_daily_trigger(completed_at)
            if after_left_time is not None and int(after_left_time) <= 0
            else next_lundao_recheck(completed_at)
        )
        self._record_confirmed_daily_lundao_seat(
            payload,
            after_status,
            expected_room_id=LUNDAO_SANQING_ROOM_ID,
            next_time=next_at.strftime("%Y-%m-%d %H:%M:%S"),
            label="三清入座",
            reason="已完成三清入座",
        )
        return result

    def _daily_lundao_post_seat_status(
        self,
        payload: Mapping[str, Any],
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Prefer the live seated model after a successful seat transaction."""

        execution_status = self._read_daily_lundao_execution_status(payload)
        if (
            execution_status.get("available")
            and execution_status.get("complete")
            and execution_status.get("seated") is True
        ):
            self._log(
                "detail",
                "论道_座位：入座后 Runtime 已确认 seated=true 与"
                "剩余闻道时间",
            )
            return execution_status
        self._log("detail", "论道_座位：入座后按需重读 Runtime，" f"reason={reason}")
        after = self._refresh_daily_lundao_runtime_facts(reason=reason)
        return (
            dict(after["status"])
            if isinstance(after.get("status"), dict)
            else {}
        )

    def _require_daily_lundao_expected_room(
        self,
        status: Mapping[str, Any],
        expected_room_id: int,
        *,
        label: str,
    ) -> None:
        actual_room_id = int(status.get("room_id") or 0)
        if not (
            status.get("available")
            and status.get("complete")
            and status.get("seated") is True
            and actual_room_id == int(expected_room_id)
        ):
            raise RuntimeError(
                f"论道_座位：{label}动作结束后 Runtime 未确认目标道场，"
                f"expected_room_id={int(expected_room_id)}，actual_room_id={actual_room_id or 'unknown'}"
            )

    def _record_confirmed_daily_lundao_seat(
        self,
        payload: Mapping[str, Any],
        status: Mapping[str, Any],
        *,
        expected_room_id: int,
        next_time: str,
        label: str,
        reason: str,
    ) -> None:
        """Only persist seat success after Runtime proves the business postcondition."""

        self._require_daily_lundao_expected_room(
            status,
            expected_room_id,
            label=label,
        )
        self._record_daily_lundao_next_time(payload, next_time, reason=reason)

    def _read_daily_lundao_execution_status(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        from backend.core.fanxiu.instrumentation.lundao import (
            read_lundao_snapshot,
        )

        override = payload.get(
            "__lundao_post_seat_runtime_snapshot_override",
            payload.get("__lundao_runtime_snapshot_override"),
        )
        execution_status = (
            dict(override)
            if isinstance(override, Mapping)
            else read_lundao_snapshot()
        )
        return execution_status

    def _daily_lundao_world_runtime_guard(
        self,
        context: Any,
        payload: Mapping[str, Any],
        *,
        scene_id: int,
    ):
        """Skip a vanished daily entry when Runtime already proves the state."""

        execution_status = self._read_daily_lundao_execution_status(payload)
        if not (
            execution_status.get("available")
            and execution_status.get("complete")
        ):
            return None
        current_left = execution_status.get("current_left_listen_time")
        seated = execution_status.get("seated") is True
        if current_left is None or (int(current_left) > 0 and not seated):
            return None
        # 三清已入座只证明当前有座位，不能证明无需升级。继续进入论道
        # 动态策略，刷新大罗名册、检查免费次数并判断是否可以换座。
        if (
            int(current_left) > 0
            and int(execution_status.get("room_id") or 0) != LUNDAO_DALUO_ROOM_ID
        ):
            return None
        if scene_id not in {34, 661}:
            yield from context.go_scene(34)
        now = job_now()
        if int(current_left) <= 0:
            next_at = next_lundao_daily_trigger(now)
            self._record_daily_lundao_next_time(
                payload,
                next_at.strftime("%Y-%m-%d %H:%M:%S"),
                reason="Runtime 已确认今日闻道时间归零",
            )
            return "success"
        next_at = next_lundao_daily_trigger(now)
        self._record_daily_lundao_next_time(
            payload,
            next_at.strftime("%Y-%m-%d %H:%M:%S"),
            reason="Runtime 已确认仍在闻道中",
        )
        self._log(
            "skip",
            "论道_座位：Runtime 已确认 seated=true，跳过已消失的日常入口",
        )
        return "skipped"

    def _select_daily_lundao_dojo_level(self, context: Any, scene_id: int | None) -> dict[str, Any]:
        """节点 2：选择论道道场级别，输出抢座节点的起始场景。

        输入是节点 1 路由出的 #294「准备开始」或 #296 道场选择页。该节点
        只选择“大罗道场”，不得执行请人让座、入座、结果处理或离场。
        已真实确认的边界仅为 #296[大罗道场] -> 等待 5 秒 -> #297；
        #294[大罗道场] 的点击已确认，但真实落点尚未提供，必须显式暂停。

        真实证据（2026-07-18，AI Cell execution_count=13）：点击前 #296 100%，
        点击 #296[大罗道场] 并等待 5 秒后为 #297 85%。这只证明本轮进入
        #297「踢人抢座」入口，不证明 #297 后续动作。
        """
        if scene_id == 294:
            context.click_shape_center(294, "大罗道场")
            return {"status": "target_pending", "source_scene_id": 294, "scene_id": None, "score": 0.0}
        if scene_id != 296:
            return {"status": "unimplemented", "source_scene_id": scene_id, "scene_id": None, "score": 0.0}
        context.click_shape_center(296, "大罗道场")
        yield from context.wait_action_settle(5.0)
        _wait_scene_match = yield from context.wait_scene([297], wait=5.0, required=False)
        (next_scene_id, score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if next_scene_id != 297:
            return {"status": "unknown", "source_scene_id": 296, "scene_id": next_scene_id, "score": float(score)}
        return {"status": "selected", "source_scene_id": 296, "scene_id": 297, "score": float(score)}

    def _continue_after_daily_lundao_dojo_selection(
        self,
        context: Any,
        stop_event: threading.Event,
        selection_result: dict[str, Any],
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        if selection_result.get("status") == "selected" and selection_result.get("scene_id") == 297:
            return (yield from self._run_daily_lundao_seat_and_leave(context, stop_event, payload=payload))
        if selection_result.get("status") == "target_pending":
            raise RuntimeError("论道_座位：#294 已点击「大罗道场」，真实落点与后续路由尚未提供")
        raise RuntimeError(
            f"论道_座位：选择道场级别后未到达抢座起点 #297，"
            f"当前 #{selection_result.get('scene_id') if selection_result.get('scene_id') is not None else 'unknown'} "
            f"{float(selection_result.get('score') or 0.0):.0f}%"
        )

    def _run_daily_lundao_seat_and_leave(
        self,
        context: Any,
        stop_event: threading.Event,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        """节点 3：从道场选择落点开始，完成一轮抢座、结果处理与离场。

        输入是节点 2 完成选择后的真实画面。进入节点后先用精确 Layer 0
        区分 #297「满座」与 #298「有空位」；输出只能是完整闭环后的 success
        或明确失败。该节点不得返回 #69 查入口，也不得重新选择道场级别。
        尚未给全的抢座分支继续显式失败，不能猜流程或提前报成功。

        #297 进入“踢人抢座”策略，#298 进入“直接坐空位”策略。两条策略
        只负责推进到共同后续场景，随后统一交给确认入座、离场和完成收尾，
        禁止各自复制一套离场逻辑。
        """
        # A generic #295 victory overlay is not a valid top-level entry: it may
        # belong to the previously running job.  #295 is accepted only inside
        # _advance_daily_lundao_kick_dialogue after this job has initiated its
        # own kick/battle transaction.
        _wait_scene_match = yield from context.wait_scene([297, 298, 371, 372, 303, 375], wait=5.0, required=False)
        (scene_id, score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id == 297:
            selection = self._select_daily_lundao_kick_target()
            if not selection.get("ok"):
                raise RuntimeError(
                    f"论道_座位：读取大罗座位或自身法则失败，"
                    f"status={selection.get('status')} reason={selection.get('reason')}"
                )
            target = selection.get("target") if isinstance(selection.get("target"), dict) else None
            if target is None:
                yield from context.go_scene(34)
                next_time = self._schedule_daily_lundao_next_check(
                    dict(payload or {}),
                    message="大罗满座且没有可击败的非友军",
                )
                rejected = selection.get("rejected") if isinstance(selection.get("rejected"), dict) else {}
                self._log("skip", f"论道_座位：无可用目标，已返回 #34，{next_time} 重试，排除={rejected}")
                return "success"
            self._log(
                "info",
                f"论道_座位：选择非友军「{target.get('name')}」，"
                f"{target.get('faze_cross')}跨，战力 {float(target.get('battle_score') or 0):.3e}",
            )
            kick_result = yield from self._run_daily_lundao_kick_for_seat_strategy(
                context,
                stop_event,
                target_player=target,
            )
            if kick_result.get("status") == "target_changed":
                yield from context.go_scene(34)
                next_time = self._schedule_daily_lundao_next_check(
                    dict(payload or {}),
                    message="大罗目标已变化，保持当前座位",
                )
                self._log("skip", f"论道_座位：目标变化，未点击，{next_time} 复查")
                return "success"
            if kick_result.get("status") == "prerequisite_required":
                raise RuntimeError(
                    "论道_座位：#297 动态条目按钮返回法则前置缺失，"
                    "已安全返回 #34；法则邮件补偿尚未执行"
                )
            scene_id = int(kick_result.get("scene_id") or 52)
            score = float(kick_result.get("score") or 0.0)
        elif scene_id == 298:
            scene_id, score = yield from self._run_daily_lundao_empty_seat_strategy(context)
        elif scene_id == 371:
            dialogue_result = yield from self._confirm_daily_lundao_kick_request(context, start_scene=371)
            scene_id = int(dialogue_result.get("scene_id") or 52)
            score = float(dialogue_result.get("score") or 0.0)
        elif scene_id == 372:
            dialogue_result = yield from self._confirm_daily_lundao_kick_request(context, start_scene=372)
            scene_id = int(dialogue_result.get("scene_id") or 52)
            score = float(dialogue_result.get("score") or 0.0)
        elif scene_id == 303:
            dialogue_result = yield from self._advance_daily_lundao_kick_dialogue(context, start_scene=303)
            scene_id = int(dialogue_result.get("scene_id") or 52)
            score = float(dialogue_result.get("score") or 0.0)
        elif scene_id == 375:
            dialogue_result = yield from self._advance_daily_lundao_kick_dialogue(context, start_scene=375)
            scene_id = int(dialogue_result.get("scene_id") or 52)
            score = float(dialogue_result.get("score") or 0.0)
        else:
            raise RuntimeError(
                f"论道_座位：抢座节点入口只接受 #297/#298/#371/#372/#303/#375，当前 "
                f"#{scene_id if scene_id is not None else 'unknown'} {score:.0f}%"
            )
        return (yield from self._complete_daily_lundao_seat_and_leave(context, stop_event, scene_id, score))

    def _leave_daily_lundao_rule_block_to_world(self, context: Any) -> dict[str, Any]:
        """Close the rule-prerequisite notice and suspend Lundao at world #34."""

        _wait_scene_match = yield from context.wait_scene([564], wait=5.0, required=False)
        (scene_id, score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id != 564:
            raise RuntimeError(
                "论道_座位：法则前置缺失退出只接受已确认的 #564，"
                f"当前 #{scene_id if scene_id is not None else 'unknown'} {score:.0f}%"
            )
        self._log("action", "论道_座位：#564 仅作为法则前置缺失信号，关闭提示后退回世界")
        landed = yield from context.wait_click_then_scene(
            564,
            "确认",
            297,
            timeout=12.0,
            label="论道_座位：关闭法则提示后等待座位列表 #297",
        )
        if int(getattr(landed, "scene_id", getattr(landed, "id", landed))) != 297:
            raise RuntimeError("论道_座位：关闭 #564 后未可靠回到座位列表 #297")
        yield from context.go_scene(34)
        _wait_scene_match = yield from context.wait_scene([34], wait=5.0, required=False)
        (final_scene, final_score, _final_frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if final_scene != 34:
            raise RuntimeError(
                "论道_座位：法则前置缺失退出后未确认世界 #34，"
                f"当前 #{final_scene if final_scene is not None else 'unknown'} {final_score:.0f}%"
            )
        self._log("success", "论道_座位：已从 #564 安全退出论道并确认世界 #34")
        return {
            "status": "prerequisite_required",
            "prerequisite": "law_mail",
            "source_scene_id": 564,
            "scene_id": 34,
            "score": float(final_score or 0.0),
        }

    def _select_daily_lundao_kick_target(self) -> dict[str, Any]:
        """Refresh the current Daluo roster and apply the shared relation policy."""

        return refresh_and_select_lundao_kick_target()

    def _schedule_daily_lundao_next_check(
        self,
        payload: dict[str, Any],
        *,
        message: str,
        seconds: int | None = None,
    ) -> str:
        now = job_now()
        next_at = (
            next_lundao_unseated_retry(now)
            if seconds is None
            else now + timedelta(seconds=max(60, int(seconds)))
        )
        next_at = clip_daily_retry_to_window(
            next_at,
            now=now,
            start=LUNDAO_FIRST_TRIGGER,
            end=LUNDAO_CLOSE_TIME,
        )
        next_time = next_at.strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "daily-lundao-seat"),
            next_time,
        )
        self._log("skip", f"论道_座位：{message}，{next_time} 重试")
        return next_time

    def _run_daily_lundao_kick_for_seat_strategy(
        self,
        context: Any,
        stop_event: threading.Event,
        *,
        target_player: Mapping[str, Any] | None = None,
        room_id: int = LUNDAO_DALUO_ROOM_ID,
    ):
        """#297 满座策略：只对上游明确指定的目标玩家执行同条目让座。

        `target_player` 是策略必需输入，由上游给出稳定玩家 ID 和/或精确玩家名；
        正式链路由前置 Runtime 座位清单与选人策略生成该结构，稳定 ID/seat_id 是
        身份依据，name 只用于界面 OCR 匹配与校验；同盟排除也由上游完成。
        本节点禁止读取手工固定姓名、自行排序、猜测或改选目标。必须先限定在
        正式 `#297[窗口]` 内定位目标姓名所属动态条目，再通过空间关联找到
        同一行/同一卡片的 `[请他让座]`，绝不能点击固定全局坐标、窗口外按钮
        或其它玩家条目的同名按钮。目标缺失、条目不唯一、按钮关联不唯一或
        任一证据不足时均须在点击前停止，不得误点，也不得算任务成功。

        定位使用 floating `[模板]` 的 `[区服姓名]` 与 `[按钮]` 正式标注；顶层
        `#297[请他让座]` 只作视觉/场景身份参考，不作为动态条目的点击坐标。
        """
        self._raise_if_stopped(stop_event)
        target = dict(target_player or {})
        target_name = _sanitize_ocr_text(target.get("name"))
        target_id = str(target.get("seat_id") or target.get("id") or "").strip()
        cjk_name = str(target.get('ocr_cjk_name') or '')
        if not target_id:
            raise RuntimeError("论道_座位：#297 踢人抢座缺少上游确定的目标玩家，已停止且未点击")
        if not target_name:
            raise RuntimeError("论道_座位：#297 目标缺少用于界面校验的玩家姓名，已停止且未点击")
        if bool(target.get("excluded") or target.get("is_ally")):
            raise RuntimeError("论道_座位：#297 上游目标已标记为排除/同盟，已停止且未点击")

        window = context.shape(297, "窗口")
        if str(window.raw.get("loadDirection") or "").strip().lower() != "down":
            raise RuntimeError("论道_座位：#297[窗口] loadDirection 不是 down，已停止且未点击")

        def request_kick(item: Any):
            if not context.floating_item_is_fully_inside(item, "窗口"):
                raise RuntimeError(f"论道_座位：目标「{target_name}」模板实例位于窗口裁剪边缘，已停止且未点击")
            if not context.floating_item_field_is_inside(item, "按钮", "窗口"):
                raise RuntimeError(f"论道_座位：目标「{target_name}」预测按钮中心不在窗口内，已停止且未点击")
            protect_end_time = int(target.get("protect_end_time") or 0)
            if protect_end_time > int(time.time() * 1000):
                raise RuntimeError(f"论道_座位：目标「{target_name}」仍在保护时间，已停止且未点击")
            role_id = int(target.get("role_id") or 0)
            if role_id:
                latest = self._refresh_daily_lundao_runtime_facts(
                    reason="daily-lundao-before-kick",
                    room_id=int(room_id),
                )
                latest_roster = latest.get("roster") if isinstance(latest.get("roster"), dict) else {}
                if cjk_name and lundao_unique_cjk_name(target, latest_roster) != cjk_name:
                    raise RuntimeError('论道_座位：中文姓名与当前完整名单不再唯一绑定，未点击')
                target_seat_id = int(target.get("seat_id") or target.get("id") or 0)
                still_present = any(
                    isinstance(seat, dict)
                    and int(seat.get("seat_id") or 0) == target_seat_id
                    and isinstance(seat.get("owner"), dict)
                    and int(seat["owner"].get("role_id") or 0) == role_id
                    for seat in latest_roster.get("seats") or []
                )
                if not still_present:
                    self._log(
                        "skip",
                        f"论道_座位：目标「{target_name}」已不在原座位，未点击并延后复查",
                    )
                    return {
                        "status": "target_changed",
                        "target": {"id": target_id, "name": target_name},
                        "scene_id": 297,
                        "score": 0.0,
                    }
            context.click_floating_item_field(item, "按钮")
            dialogue_result = yield from self._confirm_daily_lundao_kick_request(context)
            if dialogue_result.get("status") == "prerequisite_required":
                return dialogue_result
            return {
                "status": "request_sent",
                "target": {"id": target_id, "name": target_name},
                "scene_id": dialogue_result.get("scene_id"),
                "score": dialogue_result.get("score"),
            }

        fallback_text = ""
        fallback_similarity = -1.0
        for _index in range(31):
            self._raise_if_stopped(stop_event)
            frame = context.cur_frame(update=True)
            items = context.find_floating_items_by_anchor_text(
                297,
                "模板",
                "区服姓名",
                cjk_name or target_name,
                container_shape="窗口",
                frame_data_url=frame,
                match_mode="name",
            )
            if items:
                best = items[0]
                if best.name_similarity > fallback_similarity:
                    fallback_text = best.text
                    fallback_similarity = best.name_similarity
                if best.name_similarity >= DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD:
                    if (
                        context.floating_item_is_fully_inside(best, "窗口")
                        and context.floating_item_field_is_inside(best, "按钮", "窗口")
                    ):
                        return (yield from request_kick(best))
                    self._log(
                        "detail",
                        f"论道_座位：目标「{target_name}」当前贴近窗口裁剪边缘，继续滚动后重新定位",
                    )
            changed = yield from context.scroll_shape_content(297, "窗口")
            if not changed:
                break
        if not fallback_text:
            raise RuntimeError(f"论道_座位：滚动完整个 #297[窗口] 后没有可用 OCR 姓名，已停止且未点击")
        raise RuntimeError(
            f"论道_座位：全列表最高相似度 OCR「{fallback_text}」仅 "
            f"{fallback_similarity:.0%}，低于姓名可信阈值 "
            f"{DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD:.0%}，已停止且未点击"
        )

    def _confirm_daily_lundao_kick_request(self, context: Any, *, start_scene: int | None = None):
        """忽略过渡帧，依次在 #371 发起请离、在 #372 确认。"""
        if start_scene is None:
            start_scene = yield from self._wait_daily_lundao_kick_request_result(context)
        if start_scene == 564:
            return (yield from self._leave_daily_lundao_rule_block_to_world(context))
        if start_scene == 371:
            # 这类人物选项偶尔会吞掉第一次点击。点击后必须同时观察来源页与
            # 目标页；只等待 #372 会把仍然可靠存在的 #371 错报成 unknown，
            # 然后无意义地盲等到超时。
            for attempt in range(1, 4):
                frame = context.cur_frame(update=True)
                tokens = context.ocr_tokens_in_shapes(371, ["请他让座"], frame_data_url=frame)
                candidates: list[dict[str, float]] = []
                for fragment in group_ocr_tokens(tokens):
                    text = re.sub(r"\s+", "", _sanitize_ocr_text(fragment.get("text")))
                    if text.count("请他让座") != 1:
                        continue
                    fragment_tokens = query_spatial_ocr(tokens, fragment)["tokens"]
                    box = locate_text_box(fragment_tokens, "请他让座")
                    if box is not None:
                        candidates.append(box)
                if len(candidates) != 1:
                    raise RuntimeError(f"论道_座位：#371 未唯一定位「请他让座」文字行，候选={len(candidates)}，已停止且未点击")
                box = candidates[0]
                context.click_frame_point(371, float(box["x"]) + float(box["w"]) / 2, float(box["y"]) + float(box["h"]) / 2)
                yield from context.wait_action_settle(1.5)
                observed = yield from context.wait_scene(
                    [371,
                    372,
                    303,
                    375,
                    295,
                    52],
                    wait=8.0,
                    label=f"论道_座位：第 {attempt}/3 次点击请他让座后确认来源或落点",
                )
                start_scene = observed_scene_id(observed)
                if start_scene != 371:
                    break
                self._log("warning", f"论道_座位：第 {attempt}/3 次点击「请他让座」未离开 #371，重新定位后重试")
            if start_scene == 371:
                raise RuntimeError("论道_座位：连续 3 次点击「请他让座」仍停留 #371，已停止避免无限重试")
            if start_scene in {303, 375, 295, 52}:
                return (yield from self._advance_daily_lundao_kick_dialogue(context, start_scene=start_scene))
        if start_scene != 372:
            raise RuntimeError(f"论道_座位：请离确认节点只接受 #371/#372，当前 #{start_scene}")
        context.click_shape_center(372, "确定")
        # #372 后会进入若干段同坐标对话。无需把每一段都识别成 #303；
        # 先给首段画面稳定时间，随后只以正式终点 #375 是否出现作为循环条件。
        yield from context.wait_action_settle(1.5)
        return (yield from self._advance_daily_lundao_kick_dialogue(context))

    def _wait_daily_lundao_kick_request_result(
        self,
        context: Any,
        *,
        timeout: float = 180.0,
    ):
        """Wait through the dojo auto-route for the row button outcome."""

        scene = yield from context.wait_scene(
            [371,
            564],
            wait=timeout,
            label="论道_座位：等待 #297 动态条目按钮的局部结果 #371/#564",
        )
        return observed_scene_id(scene)

    def _advance_daily_lundao_kick_dialogue(
        self,
        context: Any,
        *,
        start_scene: int | None = None,
    ) -> dict[str, Any]:
        """共用 #303 推进人物对话；实际胜利页和入座页决定流程，不猜对话阶段。"""
        from backend.core.fanxiu.behavior_tree.errors import SceneClickMismatch

        terminal_scenes = (52, 53, 186, 329, 301)
        candidates = [*LUNDAO_SETTLEMENT_SCENE_IDS, *terminal_scenes]
        scene_id = start_scene
        clicks = 0
        for _cycle in range(8):
            if scene_id is None:
                scene_id = observed_scene_id((yield from context.wait_scene(
                    candidates, wait=180.0, label="论道_座位：等待对话/胜利/入座",
                )))
            if scene_id in terminal_scenes:
                return {"status": "dialogue_finished", "clicks": clicks,
                        "scene_id": int(scene_id), "score": 100.0}
            if scene_id == 318:
                yield from context.wait_click(318, '确认')
                clicks += 1
                yield from context.wait_action_settle(1.0)
            elif scene_id == 303:
                clicks += (yield from context.advance_dialogue(
                    303, "对话", label="论道_座位：推进人物对话",
                ))
            elif scene_id in {375, 295}:
                try:
                    yield from context.wait_click(scene_id, "关闭")
                except SceneClickMismatch as exc:
                    # 结算层可能自行切换；未发出点击时只重新观察已知后继，
                    # 不沿用旧弹窗坐标，也不把未知页面当作可重试成功。
                    if exc.actual_scene_id not in candidates:
                        raise
                    scene_id = None
                    continue
                yield from context.wait_action_settle(1.5)
            else:
                raise RuntimeError(f"论道_座位：对话/战斗链出现未声明落点 #{scene_id}")
            scene_id = None
        raise RuntimeError("论道_座位：对话/战斗超过 8 段，未确认入座落点")

    def _run_daily_lundao_empty_seat_strategy(
        self,
        context: Any,
        *,
        transition_timeout: float = 45.0,
    ):
        """#298 空位策略；复用既有入座及后续确认落点，不复制收尾。"""
        yield from context.wait_click(298, "入座")
        yield from context.wait_action_settle(1.5)
        transition_timeout = max(20.0, min(120.0, float(transition_timeout)))
        deadline = time.monotonic() + transition_timeout
        last_text = ""
        while time.monotonic() < deadline:
            scene_match = yield from context.wait_scene([329, 301, 303], label='论道_座位：识别入座后场景', wait=5.0)
            (scene_id, score, frame) = (scene_match.scene_id, scene_match.score, scene_match.frame_data_url)
            last_text = context.ocr_text(frame)
            if scene_id in {329, 301, 303}:
                return scene_id, score
            yield from context.wait_action_settle(1.0)
        raise TimeoutError(
            f"论道_座位：点击 #298「入座」后 {transition_timeout:g} 秒内未确认空位链 "
            "#329/#301/#303（#300/#302 由 Layer 0 处理），"
            f"OCR={last_text[:160]}"
        )

    def _advance_daily_lundao_dojo_travel_confirmation(
        self,
        context: Any,
        scene_id: int | None,
    ):
        """Consume the equivalent #300/#329 prompt without stale-scene races."""

        prompt_ids = (329,)
        # Do not accept #186 immediately after confirming the room.  The old
        # seated page remains visible briefly underneath the transition and can
        # match #186 before the new #301 seat-choice prompt appears.  #186 is a
        # valid terminal only after the explicit #301/#302 confirmation chain.
        # #53 has a broad identity shared by the stable seated page and the
        # dynamic auto-navigation transition.  Keep it inside this local
        # transaction's observation domain, but accept it only after both the
        # transition text has disappeared and Runtime proves ``seated``.
        target_ids = (301, 303, 52, 53, 34, 69)
        preferred_prompt_id = 329
        last_scene_id = scene_id
        last_score = 0.0
        last_text = ""
        deadline = time.monotonic() + 60.0
        confirm_attempts = 0
        while time.monotonic() < deadline:
            scene_match = yield from context.wait_scene([*prompt_ids, *target_ids], wait=5.0)
            (detected, score, frame) = (scene_match.scene_id, scene_match.score, scene_match.frame_data_url)
            last_scene_id, last_score = detected, float(score or 0.0)
            last_text = context.ocr_text(frame)
            if detected == 53:
                if (
                    self._daily_lundao_text_is_seated(last_text)
                    and self._daily_lundao_runtime_confirms_seated()
                ):
                    return 53, float(score or 0.0)
                yield from context.wait_action_settle(1.0)
                continue
            if detected in target_ids:
                return int(detected), float(score or 0.0)
            if detected in prompt_ids:
                preferred_prompt_id = int(detected)
            elif not self._daily_lundao_text_is_dojo_travel_prompt(last_text):
                yield from context.wait_action_settle(1.0)
                continue

            if confirm_attempts >= 3:
                yield from context.wait_action_settle(1.0)
                continue
            confirm_attempts += 1
            self._log(
                "action",
                f"论道_座位：确认前往道场弹窗 #{preferred_prompt_id}",
            )
            context.click_shape_center(preferred_prompt_id, "确认")
            yield from context.wait_action_settle(1.5)
            remaining = max(0.1, deadline - time.monotonic())
            try:
                landed = yield from context.wait_scene(
                    [*prompt_ids,
                    *target_ids],
                    wait=min(20.0, remaining),
                    label="论道_座位：确认前往道场后等待入座链",
                )
            except TimeoutError:
                continue
            landed = observed_scene_id(landed)
            if landed in target_ids and landed != 53:
                return int(landed), 100.0
            if landed in prompt_ids:
                preferred_prompt_id = int(landed)
                continue
        raise RuntimeError(
            "论道_座位：前往道场确认弹窗连续处理后仍未进入入座链，"
            f"最后 #{last_scene_id if last_scene_id is not None else 'unknown'} "
            f"{last_score:.0f}%，OCR={last_text[:160]}"
        )

    def _prefer_daily_lundao_seat_choice_scene(
        self,
        context: Any,
        scene_id: int | None,
        score: float = 0.0,
        *,
        frame_data_url: str | None = None,
    ) -> tuple[int | None, float]:
        """Disambiguate broad #53 with the official #301[入座] shape."""

        if scene_id != 53:
            return scene_id, float(score or 0.0)
        frame = frame_data_url or context.cur_frame(update=True)
        seat_score = float(context.shape_score(301, "入座", frame_data_url=frame) or 0.0)
        if seat_score >= 80.0:
            self._log(
                "detail",
                "论道_座位：宽泛 #53 同帧命中正式 #301[入座]，"
                "按尚未入座的座位确认链继续",
            )
            return 301, seat_score
        return 53, float(score or 0.0)

    def _complete_daily_lundao_seat_and_leave(
        self,
        context: Any,
        stop_event: threading.Event,
        scene_id: int | None,
        score: float = 0.0,
    ) -> str:
        """两种抢座策略共用的确认入座、结果处理、离场和闭环收尾。"""
        # A fresh full Job Cell may start while a kick transaction is already
        # visible.  Keep this closure idempotent instead of requiring its caller
        # to normalize every dialogue/battle scene first.
        if scene_id in {371, 372}:
            dialogue_result = yield from self._confirm_daily_lundao_kick_request(
                context,
                start_scene=scene_id,
            )
            scene_id = int(dialogue_result.get("scene_id") or 52)
            score = float(dialogue_result.get("score") or 0.0)
        elif scene_id in LUNDAO_SETTLEMENT_SCENE_IDS:
            dialogue_result = yield from self._advance_daily_lundao_kick_dialogue(
                context,
                start_scene=scene_id,
            )
            scene_id = int(dialogue_result.get("scene_id") or 52)
            score = float(dialogue_result.get("score") or 0.0)
        scene_id, score = self._prefer_daily_lundao_seat_choice_scene(
            context,
            scene_id,
            score,
        )
        if scene_id == 329:
            scene_id, score = yield from self._advance_daily_lundao_dojo_travel_confirmation(
                context,
                scene_id,
            )
            scene_id, score = self._prefer_daily_lundao_seat_choice_scene(
                context,
                scene_id,
                score,
            )
        if scene_id == 301:
            scene_id, score = yield from self._advance_daily_lundao_seat_confirmation(context, stop_event, scene_id)
        if scene_id in {303}:
            scene_id, score = yield from self._advance_daily_lundao_post_seat_dialogue(
                context,
                scene_id,
            )
        # #53 identifies the dojo, not a completed seating transaction.  It
        # also appears briefly between confirmation and the entry dialogue.
        # wait_scene's timeout is a maximum, not a minimum stable duration:
        # a matching background can return immediately.  Wait for the actual
        # business result and advance any delayed dialogue before leaving.
        if scene_id in {53, 186}:
            from backend.core.fanxiu.instrumentation.lundao import read_lundao_snapshot

            deadline = time.monotonic() + 15.0
            while scene_id in {53, 186}:
                self._raise_if_stopped(stop_event)
                facts = read_lundao_snapshot()
                if (facts.get("available") and facts.get("complete")
                        and (facts.get("seated") is True or facts.get("completed") is True)):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("论道_座位：等待入座结果 15 秒仍未成立；保留现场")
                yield from context.wait_action_settle(0.5)
                pending = yield from context.wait_scene(
                    [*LUNDAO_SETTLEMENT_SCENE_IDS, 52, 53, 186], wait=5.0,
                    label="论道_座位：等待入座对话或实际入座结果",
                )
                scene_id, score = pending.scene_id, pending.score
                if scene_id in LUNDAO_SETTLEMENT_SCENE_IDS:
                    scene_id, score = yield from self._advance_daily_lundao_post_seat_dialogue(
                        context, scene_id,
                    )
        if scene_id in {53, 186, 69, 34}:
            self._require_daily_lundao_seated_or_completed()
        if scene_id == 52:
            scene_id, score, text_after = yield from self._confirm_daily_lundao_reward_scene(context)
            if self._daily_lundao_text_is_seated(text_after):
                if scene_id == 53:
                    yield from self._leave_daily_lundao_seated_for_daily_entry(context, 53)
                    self._log("success", "论道_座位：已确认听道收益并退出回世界")
                    return "success"
                if scene_id == 186:
                    from backend.core.fanxiu.instrumentation.lundao import (
                        read_lundao_snapshot,
                    )

                    seated_status = read_lundao_snapshot()
                    if (
                        seated_status.get("available")
                        and seated_status.get("complete")
                        and seated_status.get("seated") is True
                    ):
                        yield from self._leave_daily_lundao_seated_for_daily_entry(context, 186)
                        self._log(
                            "success",
                            "论道_座位：OCR 与 Runtime 均确认已入座，"
                            "已从共享 #186 点击「离开」并退出回世界",
                        )
                        return "success"
                    raise RuntimeError(
                        "论道_座位：OCR 显示闻道中且当前为共享 #186，"
                        "但 Runtime 未确认 seated=true，已停止避免误点离开"
                    )
                raise RuntimeError(
                    f"论道_座位：OCR 已确认闻道中，但图模型未识别到正式闻道场景 #53，"
                    f"当前 #{scene_id if scene_id is not None else 'unknown'}，已停止避免借用其它场景坐标"
                )
        if scene_id in {69, 34}:
            self._log("success", f"论道_座位：已确认听道收益并返回 #{scene_id}")
            return "success"
        if scene_id == 53:
            yield from self._leave_daily_lundao_seated_for_daily_entry(context, 53)
            self._log("success", "论道_座位：已完成听道并退出回世界")
            return "success"
        if scene_id == 186:
            yield from self._leave_daily_lundao_seated_for_daily_entry(context, 186)
            self._log("success", "论道_座位：已从 #186 点击「离开」并退出回世界")
            return "success"
        raise RuntimeError(f"论道_座位：抢座或收尾落点尚未实现，当前 #{scene_id if scene_id is not None else 'unknown'} {score:.0f}%")

    def _advance_daily_lundao_post_seat_dialogue(
        self,
        context: Any,
        start_scene: int,
    ) -> tuple[int, float]:
        result = yield from self._advance_daily_lundao_kick_dialogue(
            context, start_scene=start_scene,
        )
        return int(result["scene_id"]), float(result["score"])

    def _finish_daily_lundao_in_progress(self, context: Any, *, continue_to_selection: bool = False) -> str | int:
        """Leave #304; dynamic strategy may continue on the dojo selection page."""
        context.click_shape_center(304, "返回")
        if continue_to_selection:
            next_scene = yield from context.wait_scene([296, 34, 69], wait=15.0, label="论道_座位：#304 返回后等待道场选择")
            if isinstance(next_scene, View):
                if next_scene.id is None:
                    raise RuntimeError("论道_座位：#304 返回后的 View 缺少场景编号")
                return int(next_scene.id)
            return int(next_scene)
        yield from context.wait_action_settle(1.5)
        self._log("success", "论道_座位：#304 论道中，点击「返回」")
        return "success"

    def _daily_lundao_text_is_reward(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return (
            ("听道收益" in compact and ("今日闻道剩余" in compact or "基础保护时间" in compact))
            or ("本次论道主题" in compact and "今日闻道剩余" in compact and "基础保护时间" in compact)
        )

    def _daily_lundao_text_is_seated(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        if "自动寻路中" in compact:
            return False
        return (
            ("闻道剩余时间" in compact and ("道场闻道收益" in compact or "累积获得" in compact))
            or (
                "闻道感悟" in compact
                and "剩余座位" in compact
                and ("离座" in compact or "离开" in compact)
            )
        )

    @staticmethod
    def _daily_lundao_full_frame_text(
        context: Any,
        frame_data_url: str,
    ) -> str:
        """Compose the shared full-frame OCR tokens for roster-page semantics."""

        return "".join(
            str(token.get("text") or "")
            for token in context.full_frame_ocr_tokens(frame_data_url)
            if isinstance(token, Mapping)
        )

    def _daily_lundao_is_visible_own_seat_roster(
        self,
        context: Any,
        frame_data_url: str,
    ) -> bool:
        """Recognize our seated row when #297 falls through to a background scene.

        ``离座`` is the direct action exposed only on our own Lundao row.  The
        full roster header is preferred when OCR finds it, while the annotated
        scene text remains the bounded fallback for transient full-frame OCR
        misses.  Callers still require fresh Runtime ownership before treating
        this visual fact as a business completion state.
        """

        annotated_text = context.ocr_text(frame_data_url)
        compact = re.sub(
            r"\s+",
            "",
            _sanitize_ocr_text(annotated_text),
        ).translate(FULLWIDTH_DIGIT_TRANSLATION)
        if "离座" in compact:
            return True
        return self._daily_lundao_text_is_seated(
            self._daily_lundao_full_frame_text(context, frame_data_url)
        )

    def _finish_daily_lundao_visible_seated_roster(
        self,
        context: Any,
        payload: Mapping[str, Any],
        status: Mapping[str, Any],
    ):
        """Consume an already-seated roster page without replaying a seat action."""

        room_id = int(status.get("room_id") or 0)
        self._require_daily_lundao_expected_room(
            status,
            room_id,
            label="本人座位页",
        )
        yield from self._return_daily_lundao_to_selection(context, 297)
        yield from context.go_scene(34)
        completed_at = job_now()
        left_time = status.get("current_left_listen_time")
        next_at = (
            next_lundao_daily_trigger(completed_at)
            if room_id == LUNDAO_DALUO_ROOM_ID
            or (left_time is not None and int(left_time) <= 0)
            else next_lundao_recheck(completed_at)
        )
        room_label = "大罗" if room_id == LUNDAO_DALUO_ROOM_ID else "三清"
        self._record_confirmed_daily_lundao_seat(
            payload,
            status,
            expected_room_id=room_id,
            next_time=next_at.strftime("%Y-%m-%d %H:%M:%S"),
            label=f"{room_label}本人座位页",
            reason=f"{room_label}名单页与 Runtime 已确认当前座位",
        )
        return "success"

    def _daily_lundao_runtime_confirms_seated(self) -> bool:
        from backend.core.fanxiu.instrumentation.lundao import read_lundao_snapshot

        status = read_lundao_snapshot()
        return bool(
            status.get("available")
            and status.get("complete")
            and status.get("seated") is True
        )

    def _daily_lundao_text_is_dojo_travel_prompt(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return (
            "道场" in compact
            and ("前往" in compact or "是否要前往" in compact)
            and "确认" in compact
            and ("取消" in compact or "是否" in compact)
        )

    def _daily_lundao_text_is_seat_choice_prompt(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return (
            ("听道座位" in compact and "入座" in compact)
            or "再看看别的座位" in compact
            or "座位甚佳" in compact
        )

    def _daily_lundao_text_is_seat_confirm_prompt(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return (
            ("是否在该空位入座" in compact or ("是否" in compact and "入座" in compact))
            and (
                "当前道场" in compact
                or "论道收益" in compact
                or "剩余时间" in compact
                # 真实的简版确认框只显示这句正文和「确定」。场景模型已
                # 独立命中 #302 时，这两个语义足以确认，不能强求详情字段。
                or ("是否在该空位入座" in compact and "确定" in compact)
            )
        )

    def _require_daily_lundao_seated_or_completed(self) -> None:
        """Runtime 事实门禁：必须已入座或今日听道已完成，否则保留现场。"""
        from backend.core.fanxiu.instrumentation.lundao import read_lundao_snapshot

        facts = read_lundao_snapshot()
        if not (facts.get("available") and facts.get("complete")
                and (facts.get("seated") is True or facts.get("completed") is True)):
            raise RuntimeError(
                "论道_座位：入座结果未成立，Runtime 未确认已入座或今日听道完成；保留现场"
            )

    def _confirm_daily_lundao_reward_scene(self, context: Any):
        """复用 #52 听道收益确认：点击「确认」后按 Runtime 事实门禁验收。"""
        yield from context.wait_click_then_scene(52, "确认", wait_leave=True)
        match = yield from context.wait_scene(
            [53, 69, 34, 85, 186, 52], wait=5.0, required=False,
        )
        (scene_id, score, frame_after) = (
            (match.scene_id, match.score, match.frame_data_url)
            if match is not None else (None, 0.0, context.frame_data_url or "")
        )
        self._require_daily_lundao_seated_or_completed()
        return scene_id, float(score or 0.0), context.ocr_text(frame_after)

    def _leave_daily_lundao_seated_for_daily_entry(self, context: Any, scene_id: int | None):
        """从已确认入座页离场；业务事实与延迟结算界面分别验收。"""
        from backend.core.fanxiu.behavior_tree.errors import SceneClickMismatch

        if scene_id not in {53, 186}:
            raise RuntimeError(
                f"论道闻道中只接受正式场景 #53/#186，当前 #{scene_id if scene_id is not None else 'unknown'}，"
                "禁止借用其它场景的「离开」坐标"
            )
        for attempt in range(3):
            try:
                return (yield from self._leave_shared_scene_186_to_world(
                    context, label="论道_座位", include_lundao_scene=True,
                    source_scene_id=scene_id,
                ))
            except SceneClickMismatch as exc:
                # Runtime 入座成立不代表 GUI 结算已结束。延迟胜利页与
                # 对白、收益确认属于同一结算链，必须复用正常流程消费。
                recovery_scenes = [*LUNDAO_SETTLEMENT_SCENE_IDS, 52, 53, 186, 69, 34]
                if exc.actual_scene_id not in recovery_scenes:
                    raise
                self._log("detail", f"论道_座位：离场前出现 #{exc.actual_scene_id}，重新确认并收尾")
                match = yield from context.wait_scene(
                    recovery_scenes, wait=5.0, label="论道_座位：重新识别延迟结算或离场落点",
                )
                if match.scene_id in LUNDAO_SETTLEMENT_SCENE_IDS:
                    result = yield from self._advance_daily_lundao_kick_dialogue(
                        context, start_scene=match.scene_id,
                    )
                    landed = int(result['scene_id'])
                elif match.scene_id in {52, 53, 186, 69, 34}:
                    landed = match.scene_id
                else:
                    raise
                if landed == 52:
                    landed, _score, _text = yield from self._confirm_daily_lundao_reward_scene(context)
                if landed in {53, 186, 69, 34}:
                    self._require_daily_lundao_seated_or_completed()
                if landed in {53, 186}:
                    scene_id = landed
                    continue
                if landed in {69, 34}:
                    return "success"
                raise RuntimeError(
                    f"论道_座位：延迟结算后未回到正式闻道页或离场终点，"
                    f"当前 #{landed if landed is not None else 'unknown'}，保留现场"
                )
        raise RuntimeError('论道_座位：离场前延迟结算有界重试耗尽，保留现场')

    def _advance_daily_lundao_seat_confirmation(
        self,
        context: Any,
        stop_event: threading.Event,
        scene_id: int | None,
    ):
        last_scene_id = scene_id
        last_score = 0.0
        for _index in range(4):
            self._raise_if_stopped(stop_event)
            if scene_id == 329:
                scene_id, last_score = yield from self._advance_daily_lundao_dojo_travel_confirmation(
                    context,
                    scene_id,
                )
            if scene_id == 301:
                # #301[入座] itself carries the required OCR constraint.  Do
                # not add a second ad-hoc scene probe.  ``wait_click`` itself
                # re-enters the mandatory Layer-0 popup guard.  The
                # #302 action itself is intentionally unconstrained; current
                # game versions may say either “空位入座” or “更换到该座位”,
                # so an old #302 identity string must not become a new gate.
                yield from context.wait_click(301, "入座")
                yield from context.wait_action_settle(2.0)
                # Preserve the actual confirmation before generic popup handling.
                # A #47 dismissal can otherwise erase why a room switch failed.
                confirmation_frame = context.cur_frame(update=True)
                self._log("detail", "论道_座位：入座后确认文案：" + context.ocr_text(confirmation_frame))
                # The declared #302 confirmation is consumed inside Layer 0
                # on the next scene observation.
            scene_match = yield from context.wait_scene([303, 301, 329, 52, 53, 186, 237, 18, 14, 69, 34], wait=5.0)
            (scene_id, score, _frame) = (scene_match.scene_id, scene_match.score, scene_match.frame_data_url)
            last_scene_id, last_score = scene_id, float(score)
            current_text = context.ocr_text(_frame)
            if self._daily_lundao_text_is_seated(current_text):
                if scene_id in {53, 186}:
                    return scene_id, float(score)
                if scene_id is None:
                    from backend.core.fanxiu.instrumentation.lundao import (
                        read_lundao_snapshot,
                    )

                    seated_status = read_lundao_snapshot()
                    if (
                        seated_status.get("available")
                        and seated_status.get("complete")
                        and seated_status.get("seated") is True
                    ):
                        self._log(
                            "info",
                            "论道_座位：图模型未稳定识别 #186，但 OCR 与 Runtime "
                            "均确认已入座，按共享 #186 的正式离场链收尾",
                        )
                        return 186, float(score)
                    self._log(
                        "detail",
                        "论道_座位：OCR 已显示闻道中但 #186 身份尚未稳定，"
                        "继续等待，不点击通用返回",
                    )
                    yield from context.wait_action_settle(1.0)
                    continue
                raise RuntimeError(
                    f"论道_座位：OCR 已确认闻道中，但图模型落点为 "
                    f"#{scene_id}，禁止映射成其它场景"
                )
            scene_id, score = self._prefer_daily_lundao_seat_choice_scene(
                context,
                scene_id,
                score,
                frame_data_url=_frame,
            )
            last_scene_id, last_score = scene_id, float(score)
            if scene_id in {303, 52, 53}:
                return scene_id, float(score)
            if scene_id in {237, 18, 14}:
                raise RuntimeError(f"论道_座位：入座确认后落到非论道页面 #{scene_id}，已停止避免误点")
            if scene_id is None:
                if self._daily_lundao_text_is_seat_choice_prompt(current_text):
                    scene_id = 301
                    continue
                if self._daily_lundao_text_is_seat_confirm_prompt(current_text):
                    scene_id = 302
                    continue
                yield from context.wait_action_settle(1.0)
                continue
            if scene_id == 301:
                continue
            if scene_id == 302:
                continue
            if scene_id == 329:
                continue
            return scene_id, float(score)
        raise RuntimeError(f"论道_座位：#301/#302 入座确认循环超过上限，最后 #{last_scene_id if last_scene_id is not None else 'unknown'} {last_score:.0f}%")
