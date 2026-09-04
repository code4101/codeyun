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
DAOZU_STARTUP_WAIT_SECONDS = 60.0


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
    def _daozu_realm_locked_score(context: Any, frame: str | None) -> float:
        """Return the scoped #251 unlock-copy score; this is action eligibility, not quota."""

        if not frame:
            return 0.0
        return float(
            context.shape_score(251, "境界未解锁", frame_data_url=frame) or 0.0
        )

    def _finish_daozu_challenge(
        self,
        context: Any,
        *,
        task_id: str,
        next_time: str,
        message: str,
    ) -> None:
        self._persist_scheduler_task_next_time(task_id, next_time)
        context.set_completion_message(message)

    def _execute_daozu_challenge_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        return self._execute_daily_task(
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
                    "context": state,
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
                    "context": state,
                }

            return {
                "ok": False,
                "available": False,
                "source": "runtime_memory",
                "reason": "runtime_incomplete_daily_pass_count_invalid_or_missing",
                "context": state,
            }

        return {
            "ok": False,
            "available": False,
            "source": "runtime_memory",
            "reason": str(state.get("reason") or "runtime_unavailable"),
            "context": state,
        }

    def 道祖挑战流程(self, context: Any):
        from backend.core.fanxiu.data_annotation import (
            behavior_tree_executor as _behavior_tree_executor,
        )

        stop_event = context.stop_event or threading.Event()
        payload = context.payload
        task_id = str(payload.get("__scheduler_task_id") or "daozu-challenge")
        timeout = max(30.0, float(payload.get("monitor_timeout") or 1800.0))
        poll_interval = max(0.1, float(payload.get("monitor_poll_interval") or 1.0))
        next_time = next_daozu_challenge_time(
            _behavior_tree_executor._now()
        ).strftime("%Y-%m-%d %H:%M:%S")

        result_scene_ids = [DAOZU_ORDINARY_RESULT_SCENE_ID, DAOZU_DAILY_LIMIT_RESULT_SCENE_ID]
        chain_started = bool(payload.get(DAOZU_CHAIN_START_MARK))
        startup_layer0_wait_seconds = max(
            5.0,
            float(
                payload.get("startup_layer0_wait_seconds")
                or DAOZU_STARTUP_WAIT_SECONDS
            ),
        )
        match = None
        try:
            match = yield from context.wait_scene(
                [34, 251, *result_scene_ids],
                wait=startup_layer0_wait_seconds,
                label="道祖_挑战：等待可接管起始场景",
            )
            scene_id = int(match)
        except TimeoutError as exc:
            # A persisted start mark proves that the only irreversible click
            # already happened. An unidentified battle/loading frame is then
            # a legal intermediate state and the monitor keeps waiting without
            # clicking #251 again. Before the chain starts, a global match is
            # diagnostic evidence for safe recovery only.
            last_match = getattr(exc, "last_match", None)
            scene_id = None if chain_started else (
                int(getattr(last_match, "scene_id"))
                if getattr(last_match, "scene_id", None) is not None
                else None
            )
        frame = getattr(match, "frame_data_url", None) or context.cur_frame(update=True)
        if scene_id is None and not chain_started:
            raise RuntimeError(
                "道祖_挑战：wait_scene 等待后仍未出现指定 Layer 0 场景，"
                "拒绝导航或重复点击"
            )

        if not chain_started and scene_id not in {34, 251, *result_scene_ids}:
            if scene_id == 15:
                raise RuntimeError(
                    "道祖_挑战：当前为 #15 账号登录页，凭据与登录确认只能由用户处理"
                )
            # A preceding job can fail after leaving a normal, identified
            # business page (for example #358).  Its safe navigation edges are
            # the authoritative way back to the stable world entry.
            yield from context.go_scene(34)
            scene_id = 34
            frame = context.cur_frame(update=True)

        state = self._read_daozu_challenge_state()
        if scene_id == 34:
            if not state.get("ok") and not payload.get(DAOZU_CHAIN_START_MARK):
                raise RuntimeError(
                    "道祖_挑战：缺少短时新鲜事实，拒绝进入路线；"
                    f"{_daozu_state_failure_detail(state)}"
                )
            if state.get("ok") and int(state["remaining"]) <= 0:
                self._finish_daozu_challenge(
                    context,
                    task_id=task_id,
                    next_time=next_time,
                    message="道祖_挑战结束，事实显示今日剩余 0/20，未执行挑战",
                )
                return
            frame = context.cur_frame(update=True)
            panel_lines = context.ocr_fragments_in_shapes(
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
                yield from context.wait_click(34, "任务")
                yield from context.wait_action_settle(0.8)
            elif "任务" not in panel_text:
                yield from context.wait_click(34, "展开任务组队面板")
                yield from context.wait_action_settle(0.8)
                yield from context.wait_click(34, "任务")
                yield from context.wait_action_settle(0.8)
            yield from context.wait_click(34, "主线")
            yield from context.wait_scene([251], wait=30.0, label="道祖_挑战：等待路线 #251")
            scene_id = 251

        started_now = False
        if scene_id == 251:
            realm_locked_score = self._daozu_realm_locked_score(context, frame)
            if realm_locked_score >= 55.0:
                self._clear_scheduler_task_payload_flag(task_id, DAOZU_CHAIN_START_MARK)
                payload.pop(DAOZU_CHAIN_START_MARK, None)
                yield from context.go_scene(34)
                self._finish_daozu_challenge(
                    context,
                    task_id=task_id,
                    next_time=next_time,
                    message="道祖_挑战幂等结束：#251 确认境界未达到解锁要求，当前无可执行挑战，已返回世界",
                )
                return
            if state.get("ok") and int(state["remaining"]) <= 0:
                self._clear_scheduler_task_payload_flag(task_id, DAOZU_CHAIN_START_MARK)
                yield from context.go_scene(34)
                self._finish_daozu_challenge(
                    context,
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
            yield from context.wait_click(251, "挑战")
            started_now = True
        elif scene_id is None and chain_started:
            # The start mark proves that the single start click already happened.
            # Battle/loading frames intentionally have no GUI scene identity;
            # attach to observation without ever clicking #251 again.
            pass
        elif scene_id not in result_scene_ids:
            raise RuntimeError(f"道祖_挑战：当前场景 #{scene_id} 不允许启动或接管自动链")

        pending_scene_id = None if started_now or scene_id is None else scene_id
        route_terminal_allowed = not started_now
        deadline = time.monotonic() + timeout
        while time.monotonic() <= deadline:
            self._raise_if_stopped(stop_event)
            if pending_scene_id is not None:
                scene_id = pending_scene_id
                pending_scene_id = None
            else:
                remaining_wait = max(0.0, deadline - time.monotonic())
                try:
                    observed = yield from context.wait_scene(
                        [251, *result_scene_ids],
                        wait=min(60.0, remaining_wait),
                        label="道祖_挑战：等待路线或自动链结算场景",
                    )
                    scene_id = int(observed)
                except TimeoutError:
                    continue
            if scene_id == DAOZU_DAILY_LIMIT_RESULT_SCENE_ID:
                yield from context.wait_click(DAOZU_DAILY_LIMIT_RESULT_SCENE_ID, "点击退出")
                yield from context.wait_scene(
                    [251],
                    wait=30.0,
                    label="道祖_挑战：终局退出后等待路线 #251",
                )
                terminal_state = self._read_daozu_challenge_state()
                if not terminal_state.get("ok") or _daozu_int(terminal_state.get("remaining")) != 0:
                    raise RuntimeError("道祖_挑战：终局退出后未取得 remaining=0 的权威运行态")
                self._clear_scheduler_task_payload_flag(task_id, DAOZU_CHAIN_START_MARK)
                yield from context.go_scene(34)
                self._finish_daozu_challenge(
                    context,
                    task_id=task_id,
                    next_time=next_time,
                    message="道祖_挑战结束，已完成每日20层并从终局返回世界",
                )
                return
            if scene_id == 251:
                if route_terminal_allowed:
                    route_state = self._read_daozu_challenge_state()
                    if (
                        route_state.get("ok")
                        and _daozu_int(route_state.get("remaining")) == 0
                    ):
                        self._clear_scheduler_task_payload_flag(task_id, DAOZU_CHAIN_START_MARK)
                        yield from context.go_scene(34)
                        self._finish_daozu_challenge(
                            context,
                            task_id=task_id,
                            next_time=next_time,
                            message="道祖_挑战结束，路线运行态确认每日20层已完成并返回世界",
                        )
                        return
                # The first fresh frame after the start click may still be the
                # launch page while the native dungeon is loading. Keep
                # observing; the persisted start mark prevents a second click.
                yield from context.wait_action_settle(poll_interval)
                continue
            # The button and the countdown execute the same native transition.
            # It is an optional latency optimization: never click the generic
            # exit settlement and never make progress depend on this click.
            if scene_id == DAOZU_ORDINARY_RESULT_SCENE_ID:
                yield from context.wait_click(DAOZU_ORDINARY_RESULT_SCENE_ID, "下一层")
                route_terminal_allowed = True
            yield from context.wait_action_settle(poll_interval)

        raise TimeoutError("道祖_挑战：自动链监控超时；防重复标记保留，禁止 Scheduler 重试点击")
