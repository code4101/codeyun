from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any


DAOZU_DAILY_LEVEL_LIMIT = 20
DAOZU_CHAIN_START_MARK = "daozu_auto_chain_started_at"
DAOZU_DAILY_TRIGGER = (7, 0)
DAOZU_ORDINARY_RESULT_SCENE_ID = 548
DAOZU_DAILY_LIMIT_RESULT_SCENE_ID = 533


def next_daozu_challenge_time(now: datetime | None = None) -> datetime:
    current = now or datetime.now()
    candidate = current.replace(
        hour=DAOZU_DAILY_TRIGGER[0],
        minute=DAOZU_DAILY_TRIGGER[1],
        second=0,
        microsecond=0,
    )
    return candidate if candidate > current else candidate + timedelta(days=1)


def _daozu_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _daozu_state_failure_detail(state: dict[str, Any]) -> str:
    source = str(state.get("source") or "unknown")
    reason = str(state.get("reason") or "unavailable")
    return f"source={source}, reason={reason}"


class DaozuChallengeTaskMixin:
    _DAOZU_CONFIGURED_DAILY_LIMIT = DAOZU_DAILY_LEVEL_LIMIT

    @staticmethod
    def _daozu_realm_locked_score(runtime: Any, frame: str | None) -> float:
        """Return the scoped #251 unlock-copy score; this is action eligibility, not quota."""

        if not frame:
            return 0.0
        return float(
            runtime.shape_score(251, "境界未解锁", frame_data_url=frame) or 0.0
        )

    def _finish_daozu_challenge(
        self,
        runtime: Any,
        *,
        task_id: str,
        next_time: str,
        message: str,
    ) -> None:
        self._persist_scheduler_task_next_time(task_id, next_time)
        runtime.set_completion_message(message)

    def _execute_daozu_challenge_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        return self._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="daozu_challenge",
            label="道祖_挑战",
            flow=self.道祖挑战流程,
        )

    def _read_daozu_challenge_state(self) -> dict[str, Any]:
        from backend.core.fanxiu.instrumentation.daozu_road import (
            read_daozu_road_snapshot,
        )

        state = read_daozu_road_snapshot()
        if state.get("available"):
            if state.get("complete"):
                return {
                    "ok": True,
                    "available": True,
                    "passCount": state.get("daily_pass_count"),
                    "limit": state.get("daily_limit"),
                    "remaining": state.get("daily_remaining"),
                    "source": "runtime_memory",
                    "runtime": state,
                }

            pass_count = _daozu_int(state.get("daily_pass_count"))
            limit = int(self._DAOZU_CONFIGURED_DAILY_LIMIT)
            if pass_count is not None and 0 <= pass_count <= limit:
                return {
                    "ok": True,
                    "available": True,
                    "passCount": pass_count,
                    "limit": limit,
                    "remaining": limit - pass_count,
                    "source": "runtime_memory_with_configured_limit",
                    "runtime": state,
                }

            return {
                "ok": False,
                "available": False,
                "source": "runtime_memory",
                "reason": "runtime_incomplete_daily_pass_count_invalid_or_missing",
                "runtime": state,
            }

        return {
            "ok": False,
            "available": False,
            "source": "runtime_memory",
            "reason": str(state.get("reason") or "runtime_unavailable"),
            "runtime": state,
        }

    def 道祖挑战流程(self, runtime: Any):
        from backend.core.fanxiu.data_annotation import (
            behavior_tree_runtime as _behavior_tree_runtime,
        )

        stop_event = runtime.stop_event or threading.Event()
        payload = runtime.payload
        task_id = str(payload.get("__scheduler_task_id") or "daozu-challenge")
        timeout = max(30.0, float(payload.get("monitor_timeout") or 1800.0))
        poll_interval = max(0.1, float(payload.get("monitor_poll_interval") or 1.0))
        next_time = next_daozu_challenge_time(
            _behavior_tree_runtime._now()
        ).strftime("%Y-%m-%d %H:%M:%S")

        result_scene_ids = [DAOZU_ORDINARY_RESULT_SCENE_ID, DAOZU_DAILY_LIMIT_RESULT_SCENE_ID]
        scene_id, _score, frame = runtime.current_scene([34, 251, *result_scene_ids], update=True)
        chain_started = bool(payload.get(DAOZU_CHAIN_START_MARK))
        if scene_id is None and not chain_started:
            raise RuntimeError("道祖_挑战：当前为未知战斗/加载场景，拒绝导航或重复点击")

        state = self._read_daozu_challenge_state()
        if scene_id == 34:
            if not state.get("ok") and not payload.get(DAOZU_CHAIN_START_MARK):
                raise RuntimeError(
                    "道祖_挑战：缺少短时新鲜事实，拒绝进入路线；"
                    f"{_daozu_state_failure_detail(state)}"
                )
            if state.get("ok") and int(state["remaining"]) <= 0:
                self._finish_daozu_challenge(
                    runtime,
                    task_id=task_id,
                    next_time=next_time,
                    message="道祖_挑战结束，事实显示今日剩余 0/20，未执行挑战",
                )
                return
            frame = runtime.cur_frame(update=True)
            panel_lines = runtime.ocr_fragments_in_shapes(
                34,
                ["任务组队面板"],
                frame_data_url=frame,
            )
            panel_text = re.sub(
                r"\s+",
                "",
                " ".join(str(item.get("text") or "") for item in panel_lines),
            )
            if re.search(r"创建队伍|加入队伍", panel_text):
                yield from runtime.wait_click(34, "任务")
                yield from runtime.wait_action_settle(0.8)
            elif "任务" not in panel_text:
                yield from runtime.wait_click(34, "展开任务组队面板")
                yield from runtime.wait_action_settle(0.8)
                yield from runtime.wait_click(34, "任务")
                yield from runtime.wait_action_settle(0.8)
            yield from runtime.wait_click(34, "主线")
            yield from runtime.wait_view(251, timeout=30.0, label="道祖_挑战：等待路线 #251")
            scene_id = 251

        if scene_id == 251:
            _sid, _score, frame = runtime.current_scene([251], update=True)
            if _sid != 251:
                raise RuntimeError("道祖_挑战：启动前未识别到 #251，拒绝点击")
            realm_locked_score = self._daozu_realm_locked_score(runtime, frame)
            if realm_locked_score >= 55.0:
                self._clear_scheduler_task_payload_flag(task_id, DAOZU_CHAIN_START_MARK)
                payload.pop(DAOZU_CHAIN_START_MARK, None)
                yield from runtime.goto_view(34)
                self._finish_daozu_challenge(
                    runtime,
                    task_id=task_id,
                    next_time=next_time,
                    message="道祖_挑战幂等结束：#251 确认境界未达到解锁要求，当前无可执行挑战，已返回世界",
                )
                return
            if state.get("ok") and int(state["remaining"]) <= 0:
                self._clear_scheduler_task_payload_flag(task_id, DAOZU_CHAIN_START_MARK)
                yield from runtime.goto_view(34)
                self._finish_daozu_challenge(
                    runtime,
                    task_id=task_id,
                    next_time=next_time,
                    message="道祖_挑战结束，运行态显示今日剩余 0/20，已回到世界",
                )
                return
            if payload.get(DAOZU_CHAIN_START_MARK):
                raise RuntimeError(
                    "道祖_挑战：#251 未证明境界锁定或今日完成，保留未收口防重复标记并拒绝重复点击"
                )
            if not state.get("ok"):
                raise RuntimeError(
                    "道祖_挑战：#251 非终止态但缺少短时新鲜 remaining>0 事实，拒绝点击；"
                    f"{_daozu_state_failure_detail(state)}"
                )
            start_mark = datetime.now().isoformat(timespec="seconds")
            start_mark_persisted = self._set_scheduler_task_payload_flag(
                task_id,
                DAOZU_CHAIN_START_MARK,
                start_mark,
            )
            if not start_mark_persisted:
                raise RuntimeError("道祖_挑战：启动防重复标记未确认持久化，拒绝点击挑战")
            payload[DAOZU_CHAIN_START_MARK] = start_mark
            # The real #251 frame splits 挑/战 into two OCR tokens.  Use the
            # formal button Shape; it has no visual constraint, so Layer 0
            # performs no redundant full-frame precheck before this action.
            yield from runtime.wait_click(251, "挑战")
        elif scene_id is None and chain_started:
            # The start mark proves that the single start click already happened.
            # Battle/loading frames intentionally have no GUI scene identity;
            # attach to observation without ever clicking #251 again.
            pass
        elif scene_id not in result_scene_ids:
            raise RuntimeError(f"道祖_挑战：当前场景 #{scene_id} 不允许启动或接管自动链")

        deadline = time.monotonic() + timeout
        while time.monotonic() <= deadline:
            self._raise_if_stopped(stop_event)
            scene_id, _score, frame = runtime.current_scene([251, *result_scene_ids], update=True)
            if scene_id == DAOZU_DAILY_LIMIT_RESULT_SCENE_ID:
                yield from runtime.wait_click(DAOZU_DAILY_LIMIT_RESULT_SCENE_ID, "点击退出")
                yield from runtime.wait_view(
                    251,
                    timeout=30.0,
                    label="道祖_挑战：终局退出后等待路线 #251",
                )
                terminal_state = self._read_daozu_challenge_state()
                if not terminal_state.get("ok") or _daozu_int(terminal_state.get("remaining")) != 0:
                    raise RuntimeError("道祖_挑战：终局退出后未取得 remaining=0 的权威运行态")
                self._clear_scheduler_task_payload_flag(task_id, DAOZU_CHAIN_START_MARK)
                yield from runtime.goto_view(34)
                self._finish_daozu_challenge(
                    runtime,
                    task_id=task_id,
                    next_time=next_time,
                    message="道祖_挑战结束，已完成每日20层并从终局返回世界",
                )
                return
            if scene_id == 251:
                route_state = self._read_daozu_challenge_state()
                if route_state.get("ok") and _daozu_int(route_state.get("remaining")) == 0:
                    self._clear_scheduler_task_payload_flag(task_id, DAOZU_CHAIN_START_MARK)
                    yield from runtime.goto_view(34)
                    self._finish_daozu_challenge(
                        runtime,
                        task_id=task_id,
                        next_time=next_time,
                        message="道祖_挑战结束，路线运行态确认每日20层已完成并返回世界",
                    )
                    return
                # The first fresh frame after the start click may still be the
                # launch page while the native dungeon is loading. Keep
                # observing; the persisted start mark prevents a second click.
                yield from runtime.wait_action_settle(poll_interval)
                continue
            # The button and the countdown execute the same native transition.
            # It is an optional latency optimization: never click the generic
            # exit settlement and never make progress depend on this click.
            if scene_id == DAOZU_ORDINARY_RESULT_SCENE_ID:
                yield from runtime.wait_click(DAOZU_ORDINARY_RESULT_SCENE_ID, "下一层")
            yield from runtime.wait_action_settle(poll_interval)

        raise TimeoutError("道祖_挑战：自动链监控超时；防重复标记保留，禁止 Scheduler 重试点击")
