from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from backend.core.fanxiu.data_annotation.popup_guard import (
    FanxiuEmulatorRestartRequired,
)
from backend.core.fanxiu.data_annotation.unknown_recovery import (
    build_unknown_evidence,
)
from backend.core.fanxiu.instrumentation.capacity_tower import (
    read_capacity_tower_snapshot,
)


LINGTA_DAILY_LEVEL_LIMIT = 20
LINGTA_DAILY_TRIGGER = (7, 0)
LINGTA_LIST_SCENE_ID = 194
LINGTA_OVERVIEW_SCENE_ID = 531
LINGTA_CURRENT_FLOOR_SCENE_ID = 532
LINGTA_DAILY_LIMIT_SCENE_ID = 533
LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID = 534
LINGTA_ORDINARY_RESULT_SCENE_ID = 548
LINGTA_FAILURE_SCENE_ID = 365
LINGTA_CHAIN_START_MARK = "lingta_auto_chain_started"
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_LINGTA_PROGRESS_RE = re.compile(r"已通过[:：]?(\d+)/(\d+)层")
_LINGTA_AUTO_NEXT_RE = re.compile(r"下一层[（(]\d+秒[）)]")


@dataclass(frozen=True, slots=True)
class LingtaProgress:
    """A uniquely recognized current-tower progress line."""

    passed: int
    total: int
    text: str


def parse_lingta_progress_lines(lines: Iterable[str]) -> LingtaProgress | None:
    """Parse exactly one live ``已通过 n/m层`` line.

    Completed and locked neighbouring cards intentionally do not match.  More
    than one distinct numeric progress line is rejected because it no longer
    proves which visible card is current.
    """

    matches: dict[tuple[int, int], LingtaProgress] = {}
    for raw_line in lines:
        text = re.sub(r"\s+", "", str(raw_line or "")).translate(_FULLWIDTH_DIGITS)
        match = _LINGTA_PROGRESS_RE.search(text)
        if match is None:
            continue
        passed, total = (int(value) for value in match.groups())
        if total <= 0 or passed < 0 or passed > total:
            continue
        matches[(passed, total)] = LingtaProgress(passed=passed, total=total, text=text)
    if not matches:
        return None
    if len(matches) != 1:
        raise RuntimeError("灵塔列表区同时识别到多个不同的当前塔进度，拒绝猜测点击目标")
    return next(iter(matches.values()))


def lingta_progress_fragment(
    fragments: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], LingtaProgress] | None:
    """Return the unique positioned current-progress line from a cropped ROI."""

    positioned: list[tuple[dict[str, Any], LingtaProgress]] = []
    for fragment in fragments:
        if not isinstance(fragment, dict):
            continue
        progress = parse_lingta_progress_lines([str(fragment.get("text") or "")])
        if progress is None:
            continue
        if any(float(fragment.get(key) or 0) <= 0 for key in ("w", "h")):
            continue
        positioned.append((fragment, progress))
    distinct = {(item.passed, item.total) for _, item in positioned}
    if not positioned:
        return None
    if len(distinct) != 1:
        raise RuntimeError("灵塔列表区同时识别到多个不同的当前塔进度，拒绝猜测点击目标")
    return positioned[0]


def lingta_current_card_point(fragment: dict[str, Any]) -> tuple[float, float]:
    """Locate the large current-tower body relative to its live progress line.

    The relation is shared by the old left-hand and current right-hand cards.
    Clicking the progress text itself was explicitly tested and does nothing.
    """

    x = float(fragment.get("x") or 0) + float(fragment.get("w") or 0) / 2
    height = float(fragment.get("h") or 0)
    y = float(fragment.get("y") or 0) + height / 2 - 9 * height
    if not (0 < x < 900 and 0 < y < 1600 and height > 0):
        raise RuntimeError("灵塔当前卡片关系点超出真实 900×1600 画面，拒绝点击")
    return x, y


def classify_lingta_settlement_text(text: str) -> str | None:
    """Classify the two visually similar CapacityTower victory branches.

    ``下一层(n秒)`` is the normal automatic continuation.  A static
    ``下一层`` together with the outer ``点击退出`` prompt is emitted when
    the player's level does not satisfy the next floor's gate.  Classification
    alone never authorizes an exit click; the latter still needs a real scene
    and action asset.
    """

    compact = re.sub(r"\s+", "", str(text or "")).translate(_FULLWIDTH_DIGITS)
    if _LINGTA_AUTO_NEXT_RE.search(compact):
        return "auto_next"
    if "点击退出" in compact and "下一层" in compact:
        return "level_gate"
    if all(
        marker in compact
        for marker in ("本轮指导次数", "当前人物境界", "指导推荐境界")
    ):
        return "capacity_failure"
    if "已通关" in compact:
        return "tower_complete"
    return None


def _tomorrow_lingta_trigger(now: datetime | None = None) -> str:
    return next_lingta_challenge_time(now).strftime("%Y-%m-%d %H:%M:%S")


class LingtaChallengeTaskMixin:
    """Daily CapacityTower progression with a persisted one-click start mark.

    There are two normal completion families: the explicit twenty-floor daily
    limit (#533), and a power-limit loss (#365) after zero or more wins.  Both
    are terminal for today and schedule tomorrow 07:00.  Any future visually
    different loss/summary page must preserve evidence and the start mark;
    it must never authorize a second Challenge click by analogy.
    """

    def _execute_lingta_challenge_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        return self._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="lingta_challenge",
            label="灵塔_挑战",
            flow=self.灵塔挑战流程,
        )

    def apply_lingta_challenge_admission(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        from backend.core.fanxiu.data_annotation import (
            behavior_tree_runtime as _behavior_tree_runtime,
        )

        return self._persist_admission_decision(
            dict(payload or {}),
            lingta_challenge_admission(_behavior_tree_runtime._now()),
        )

    def _read_lingta_challenge_snapshot(self) -> dict[str, Any]:
        return read_capacity_tower_snapshot()

    def _persist_lingta_terminal_mark(
        self,
        task_id: str,
        start_mark: dict[str, Any] | None,
        *,
        scene_id: int,
    ) -> dict[str, Any]:
        """Persist visible daily-limit truth before attempting its exit chain."""

        terminal_mark = {
            **dict(start_mark or {}),
            "terminal_outcome": "daily_limit",
            "terminal_scene_id": int(scene_id),
            "terminal_observed_at": datetime.now().isoformat(timespec="seconds"),
        }
        if not self._set_scheduler_task_payload_flag(
            task_id,
            LINGTA_CHAIN_START_MARK,
            terminal_mark,
        ):
            raise RuntimeError(
                "灵塔_挑战：已确认每日上限终态，但终态防重复标记未确认落盘；"
                "保留现场且不执行退出"
            )
        return terminal_mark

    def _persist_lingta_power_limit_mark(
        self,
        task_id: str,
        start_mark: dict[str, Any] | None,
        *,
        passed: int,
    ) -> dict[str, Any]:
        """Checkpoint the authoritative #365 failure before its exit click."""

        terminal_mark = {
            **dict(start_mark or {}),
            "terminal_outcome": "power_limit",
            "terminal_scene_id": LINGTA_FAILURE_SCENE_ID,
            "terminal_observed_at": datetime.now().isoformat(timespec="seconds"),
            "max_chain_pass_count": max(
                int((start_mark or {}).get("max_chain_pass_count") or 0),
                max(0, int(passed)),
            ),
        }
        if not self._set_scheduler_task_payload_flag(
            task_id,
            LINGTA_CHAIN_START_MARK,
            terminal_mark,
        ):
            raise RuntimeError(
                "灵塔_挑战：已确认 #365 失败终态，但终态防重复标记未确认落盘；"
                "保留现场且不执行退出"
            )
        return terminal_mark

    def _lingta_terminal_result(
        self,
        runtime: Any,
        *,
        outcome: str,
        message: str,
    ) -> dict[str, Any]:
        from backend.core.fanxiu.data_annotation import (
            behavior_tree_runtime as _behavior_tree_runtime,
        )

        task_id = str(runtime.payload.get("__scheduler_task_id") or "lingta-challenge")
        self._persist_scheduler_task_next_time(
            task_id,
            _tomorrow_lingta_trigger(_behavior_tree_runtime._now()),
        )
        runtime.set_completion_message(message)
        return {
            "result": "success",
            "outcome": outcome,
            "message": message,
            "current_scene": 34,
        }

    def _leave_lingta_failure_to_world(self, runtime: Any):
        """Leave the failure result through either observed stable landing."""

        try:
            landing = yield from runtime.wait_click_then_view(
                LINGTA_FAILURE_SCENE_ID,
                "退出",
                [34, LINGTA_CURRENT_FLOOR_SCENE_ID],
                timeout=60,
                max_clicks=1,
                retry_if_source_remains=False,
                label="灵塔_挑战：失败后退出",
            )
        except TimeoutError:
            # #532 contains dynamic floor/reward art and can miss its complete
            # image identity after #365 exits.  Accept it only when Runtime
            # proves Lingta is loaded and the formal #532 Challenge OCR anchor
            # agrees with the GUI; neither source authorizes the click alone.
            if not all(
                hasattr(runtime, name)
                for name in (
                    "cur_frame",
                    "ocr_text_in_shapes",
                    "click_frame_point",
                    "wait_view",
                )
            ):
                raise
            snapshot = self._read_lingta_challenge_snapshot()
            frame = runtime.cur_frame(update=True)
            challenge_text = runtime.ocr_text_in_shapes(
                LINGTA_CURRENT_FLOOR_SCENE_ID,
                ("挑战文字",),
                padding=12,
                frame_data_url=frame,
            )
            if snapshot.get("complete") is not True or "挑战" not in re.sub(
                r"\s+", "", str(challenge_text or "")
            ):
                raise
            self._log(
                "warning",
                "灵塔_挑战：失败退出后由 Runtime 数据与 #532「挑战文字」OCR 联合确认当前层详情",
            )
            runtime.click_frame_point(LINGTA_CURRENT_FLOOR_SCENE_ID, 80, 1480)
            landing = yield from runtime.wait_view(
                LINGTA_LIST_SCENE_ID,
                34,
                timeout=30.0,
                label="灵塔_挑战：动态 #532 返回后等待列表或世界",
            )
        landing_id = getattr(landing, "id", landing)
        if landing_id == LINGTA_LIST_SCENE_ID:
            yield from runtime.goto_view(34)
            return
        if landing_id == LINGTA_CURRENT_FLOOR_SCENE_ID:
            yield from runtime.goto_view(34)
            return
        if landing_id == 34:
            return
        # Lightweight test runtimes return the requested candidate list
        # instead of a concrete View.  Production wait_click_then_view always
        # returns one matched View; keep that test seam without accepting any
        # unrelated runtime landing.
        if isinstance(landing_id, (list, tuple)) and 34 in landing_id:
            return
        raise RuntimeError(
            f"灵塔_挑战：失败退出后落到未验收场景 {landing_id!r}，拒绝假成功"
        )

    def _preserve_lingta_settlement_evidence(
        self,
        runtime: Any,
        frame: str | None,
        *,
        label: str,
    ) -> str:
        """Save one unknown settlement frame without changing game state."""

        if not isinstance(frame, str) or not frame.startswith("data:image"):
            return "；现场帧不可用"
        runtime_snapshot: dict[str, Any] = {}
        try:
            evidence = build_unknown_evidence(
                runtime.runner,
                runtime.ctx,
                frame,
                label=label,
                expected_scene_ids=[
                    LINGTA_FAILURE_SCENE_ID,
                    LINGTA_LIST_SCENE_ID,
                    LINGTA_CURRENT_FLOOR_SCENE_ID,
                    LINGTA_DAILY_LIMIT_SCENE_ID,
                    LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID,
                    LINGTA_ORDINARY_RESULT_SCENE_ID,
                    34,
                ],
                last_scene_id=None,
                last_score=0.0,
            )
        except Exception as exc:
            return f"；现场保存失败={type(exc).__name__}: {exc}"
        parts: list[str] = []
        if evidence.frame_path:
            parts.append(f"截图={evidence.frame_path}")
        if evidence.report_path:
            parts.append(f"证据={evidence.report_path}")
        return f"；{'，'.join(parts)}" if parts else "；现场帧未落盘"

    def _open_lingta_current_floor_detail(self, runtime: Any):
        payload = runtime.payload
        yield from runtime.goto_view(34)
        yield from runtime.wait_click_then_view(
            34,
            "日常",
            69,
            label="灵塔_挑战：进入日常 #69",
        )
        opened = yield from runtime.open_daily_entry(
            label="灵塔_挑战",
            title_pattern=r"挑战或扫荡混沌灵塔|混沌灵塔|灵塔",
            # 日常完成只表示扫荡已经结束，不能阻止挑战新层。
            progress_can_mark_done=False,
            max_scrolls=max(1, int(payload.get("max_scrolls") or 30)),
        )
        if opened != "open":
            raise RuntimeError(f"灵塔_挑战：#69 灵塔行未能打开：{opened}")
        landing = yield from runtime.wait_view(
            193,
            LINGTA_LIST_SCENE_ID,
            # 日常入口后的跨场景加载在真实设备上可超过 30 秒；加载期
            # Layer 0 应保持 unknown，不能因短超时把动画补成业务场景。
            timeout=float(payload.get("lingta_entry_wait_timeout") or 60.0),
            label="灵塔_挑战：等待 #193/#194",
        )
        if getattr(landing, "id", landing) == 193:
            yield from self._open_daily_lingta_main_from_entry(
                runtime.ctx,
                runtime.stop_event or threading.Event(),
            )

        # The entry wait can first match #194 while its card contents are still
        # settling.  An immediate one-frame recheck intermittently falls back
        # to unknown even though the same page becomes a 100% match seconds
        # later.  Keep this as a bounded view wait so OCR never races the
        # transition animation.
        stable_deadline = time.monotonic() + 15.0
        while True:
            scene_id, _score, frame = runtime.current_scene(
                [LINGTA_LIST_SCENE_ID],
                update=True,
            )
            if scene_id == LINGTA_LIST_SCENE_ID:
                break
            if time.monotonic() >= stable_deadline:
                raise RuntimeError("灵塔_挑战：等待列表 #194 稳定超时")
            yield from runtime.wait_action_settle(1.0)
        fragments = runtime.ocr_fragments_in_shapes(
            LINGTA_LIST_SCENE_ID,
            ("当前灵塔信息区",),
            frame_data_url=frame,
            crop=True,
            padding=0,
        )
        evidence = lingta_progress_fragment(fragments)
        if evidence is None:
            raise RuntimeError("灵塔_挑战：#194 未唯一识别当前塔进度")
        fragment, progress = evidence
        runtime.click_frame_point(
            LINGTA_LIST_SCENE_ID,
            *lingta_current_card_point(fragment),
        )
        runtime_snapshot: dict[str, Any] = {}
        try:
            landing = yield from runtime.wait_view(
                LINGTA_OVERVIEW_SCENE_ID,
                LINGTA_CURRENT_FLOOR_SCENE_ID,
                timeout=15,
                label="灵塔_挑战：等待总览 #531 或当前层 #532",
            )
        except TimeoutError:
            # The overview's highest-floor label is dynamic and can make the
            # full #531 identity fail even though the page and its stable
            # action are already present.  Runtime proves that Lingta data is
            # loaded; the following wait_any still requires either the formal
            # #531 action shape or the complete #532 identity before clicking.
            # Neither signal alone authorizes the transition.
            runtime_snapshot = self._read_lingta_challenge_snapshot()
            if runtime_snapshot.get("complete") is not True:
                raise
            self._log(
                "warning",
                "灵塔_挑战：#531 完整身份受动态最高层文字影响，"
                "Runtime 数据已加载，改由正式动作锚点与 #532 联合对齐",
            )
            landing = LINGTA_OVERVIEW_SCENE_ID
        if getattr(landing, "id", landing) == LINGTA_OVERVIEW_SCENE_ID:
            if runtime_snapshot.get("complete") is True:
                challenge_frame = runtime.cur_frame(update=True)
                challenge_text = runtime.ocr_text_in_shapes(
                    LINGTA_CURRENT_FLOOR_SCENE_ID,
                    ("挑战文字",),
                    padding=12,
                    frame_data_url=challenge_frame,
                )
                if "挑战" in re.sub(r"\s+", "", str(challenge_text or "")):
                    self._log(
                        "success",
                        "灵塔_挑战：Runtime 数据与 #532「挑战文字」OCR 对齐，"
                        "确认已在当前层详情",
                    )
                    return progress
            overview_result = yield from runtime.wait_any(
                {
                    "current_floor": runtime.view_visible(LINGTA_CURRENT_FLOOR_SCENE_ID),
                    "jump": runtime.shape_visible(
                        LINGTA_OVERVIEW_SCENE_ID,
                        "前往当前层",
                    ),
                },
                timeout=30,
                label="灵塔_挑战：等待迟到直达 #532 或前往当前层可点击",
            )
            if overview_result == "jump":
                yield from runtime.wait_click_then_view(
                    LINGTA_OVERVIEW_SCENE_ID,
                    "前往当前层",
                    LINGTA_CURRENT_FLOOR_SCENE_ID,
                    timeout=15,
                    label="灵塔_挑战：前往当前层 #532",
                )
        return progress

    def 灵塔挑战流程(self, runtime: Any):
        stop_event = runtime.stop_event or threading.Event()
        payload = runtime.payload
        task_id = str(payload.get("__scheduler_task_id") or "lingta-challenge")
        start_mark = payload.get(LINGTA_CHAIN_START_MARK)
        if start_mark is None:
            start_mark = self._get_scheduler_task_payload_flag(
                task_id,
                LINGTA_CHAIN_START_MARK,
            )
            if start_mark is not None:
                payload[LINGTA_CHAIN_START_MARK] = start_mark
        monitor_timeout = max(60.0, float(payload.get("monitor_timeout_seconds") or 3600.0))
        poll_seconds = max(0.5, float(payload.get("monitor_poll_seconds") or 2.0))
        start_transition_grace = max(
            3.0,
            float(payload.get("start_transition_grace_seconds") or 15.0),
        )

        scene_id, _score, frame = runtime.current_scene(
            [
                34,
                69,
                193,
                LINGTA_LIST_SCENE_ID,
                LINGTA_OVERVIEW_SCENE_ID,
                LINGTA_CURRENT_FLOOR_SCENE_ID,
                LINGTA_DAILY_LIMIT_SCENE_ID,
                LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID,
                LINGTA_ORDINARY_RESULT_SCENE_ID,
                LINGTA_FAILURE_SCENE_ID,
            ],
            update=True,
        )
        if (
            isinstance(start_mark, dict)
            and start_mark.get("terminal_outcome") == "power_limit"
            and scene_id in {34, LINGTA_LIST_SCENE_ID, LINGTA_CURRENT_FLOOR_SCENE_ID}
        ):
            if scene_id != 34:
                yield from runtime.goto_view(34)
            self._clear_scheduler_task_payload_flag(task_id, LINGTA_CHAIN_START_MARK)
            passed = max(0, int(start_mark.get("max_chain_pass_count") or 0))
            return self._lingta_terminal_result(
                runtime,
                outcome="power_limit",
                message=(
                    f"灵塔_挑战：防重复标记已保存上次 #365 失败终态（通过 {passed} 层），"
                    "已幂等返回世界且未重复点击挑战"
                ),
            )
        if start_mark and scene_id == LINGTA_DAILY_LIMIT_SCENE_ID:
            # The game explicitly states that today's twenty-floor limit was
            # reached.  This visible truth is stronger than the client model,
            # whose chain counter may already have been cleared on settlement.
            start_mark = self._persist_lingta_terminal_mark(
                task_id,
                start_mark if isinstance(start_mark, dict) else None,
                scene_id=LINGTA_DAILY_LIMIT_SCENE_ID,
            )
            payload[LINGTA_CHAIN_START_MARK] = start_mark
            landing = yield from runtime.wait_click_then_view(
                LINGTA_DAILY_LIMIT_SCENE_ID,
                "点击退出",
                [34, LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID],
                timeout=60,
                max_clicks=1,
                retry_if_source_remains=False,
                label="灵塔_挑战：每日上限后退出",
            )
            if getattr(landing, "id", landing) == LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID:
                yield from runtime.wait_click_then_view(
                    LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID,
                    "返回灵塔列表",
                    LINGTA_LIST_SCENE_ID,
                    timeout=60,
                    max_clicks=1,
                    retry_if_source_remains=False,
                    label="灵塔_挑战：上限详情返回列表",
                )
                yield from runtime.goto_view(34)
            self._clear_scheduler_task_payload_flag(task_id, LINGTA_CHAIN_START_MARK)
            return self._lingta_terminal_result(
                runtime,
                outcome="daily_limit",
                message="灵塔_挑战：游戏确认今天已挑战 20 层并达到每日上限，已回到世界",
            )
        if start_mark and scene_id == LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID:
            # The animated reward settlement may close by itself before a
            # Scheduler retry.  The detail page then carries the equally
            # authoritative 20/20 text and no Challenge button.
            start_mark = self._persist_lingta_terminal_mark(
                task_id,
                start_mark if isinstance(start_mark, dict) else None,
                scene_id=LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID,
            )
            payload[LINGTA_CHAIN_START_MARK] = start_mark
            yield from runtime.wait_click_then_view(
                LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID,
                "返回灵塔列表",
                LINGTA_LIST_SCENE_ID,
                timeout=60,
                max_clicks=1,
                retry_if_source_remains=False,
                label="灵塔_挑战：上限详情返回列表",
            )
            yield from runtime.goto_view(34)
            self._clear_scheduler_task_payload_flag(task_id, LINGTA_CHAIN_START_MARK)
            return self._lingta_terminal_result(
                runtime,
                outcome="daily_limit",
                message="灵塔_挑战：详情页确认今日层数挑战上限 20/20，已回到世界",
            )
        if start_mark and scene_id in {34, LINGTA_LIST_SCENE_ID}:
            # A previous Cell may have preserved an unknown settlement frame,
            # while the game subsequently finished its own exit before the
            # Scheduler retry.  Finish only from proof that the permitted click
            # really advanced this chain.  Merely finding a stable page is not
            # enough to authorize either a second click or success.
            if (
                isinstance(start_mark, dict)
                and start_mark.get("terminal_outcome") == "daily_limit"
            ):
                if scene_id != 34:
                    yield from runtime.goto_view(34)
                self._clear_scheduler_task_payload_flag(
                    task_id,
                    LINGTA_CHAIN_START_MARK,
                )
                return self._lingta_terminal_result(
                    runtime,
                    outcome="daily_limit",
                    message=(
                        "灵塔_挑战：防重复标记已保存游戏可见的每日 20 层上限终态，"
                        "已从稳定页面幂等收尾回到世界"
                    ),
                )
            recovered_snapshot = self._read_lingta_challenge_snapshot()
            try:
                start_ui_passed = int(
                    start_mark.get("ui_passed")
                    if isinstance(start_mark, dict)
                    else 0
                )
            except (TypeError, ValueError):
                start_ui_passed = 0
            # Runtime's CapacityTower counter can be cleared before the game
            # returns to a stable page.  Re-open the read-only route and compare
            # the authoritative list progress captured before the one permitted
            # Challenge click.  This proves advancement without authorizing a
            # second click and closes interrupted/late-exit attempts idempotently.
            if start_ui_passed > 0:
                recovered_progress = yield from self._open_lingta_current_floor_detail(runtime)
                if recovered_progress.passed > start_ui_passed:
                    ui_advanced = recovered_progress.passed - start_ui_passed
                    yield from runtime.goto_view(34)
                    self._clear_scheduler_task_payload_flag(
                        task_id,
                        LINGTA_CHAIN_START_MARK,
                    )
                    return self._lingta_terminal_result(
                        runtime,
                        outcome="recovered_auto_chain",
                        message=(
                            "灵塔_挑战：自动链已返回稳定界面，列表进度由 "
                            f"{start_ui_passed} 推进到 {recovered_progress.passed} "
                            f"（本轮 {ui_advanced} 层）；已幂等回到世界且未重复点击挑战"
                        ),
                    )
            try:
                start_mark_start_id = int(
                    start_mark.get("start_tower_id")
                    if isinstance(start_mark, dict)
                    else 0
                )
            except (TypeError, ValueError):
                start_mark_start_id = 0
            try:
                start_mark_passed = int(
                    start_mark.get("max_chain_pass_count")
                    if isinstance(start_mark, dict)
                    else 0
                )
            except (TypeError, ValueError):
                start_mark_passed = 0
            try:
                start_mark_last_id = int(
                    start_mark.get("last_tower_id")
                    if isinstance(start_mark, dict)
                    else 0
                )
            except (TypeError, ValueError):
                start_mark_last_id = 0
            recovered_passed = max(
                start_mark_passed,
                int(recovered_snapshot.get("chain_pass_count") or 0),
            )
            recovered_current_id = int(
                recovered_snapshot.get("current_tower_id") or start_mark_last_id or 0
            )
            recovered_advanced = bool(
                start_mark_start_id > 0
                and recovered_current_id > 0
                and recovered_current_id != start_mark_start_id
            )
            if (
                recovered_snapshot.get("ok")
                and recovered_snapshot.get("config_bounds_complete") is True
                and recovered_snapshot.get("has_current_tower_config") is False
                and recovered_advanced
            ):
                if scene_id != 34:
                    yield from runtime.goto_view(34)
                self._clear_scheduler_task_payload_flag(
                    task_id,
                    LINGTA_CHAIN_START_MARK,
                )
                return self._lingta_terminal_result(
                    runtime,
                    outcome="no_next_floor",
                    message=(
                        f"灵塔_挑战幂等结束：当前层已从 {start_mark_start_id} 推进到 "
                        f"{recovered_current_id} 并越过客户端完整配置边界，"
                        "没有下一层可挑战，已清理旧防重复标记并回到世界"
                    ),
                )
            # ``max_chain_pass_count`` is written by this same Cell from a
            # complete external-memory snapshot before preserving an unknown
            # settlement.  It remains authoritative if the game unloads or
            # clears the live model before the Scheduler retry.
            if not (recovered_advanced or recovered_passed > 0):
                raise RuntimeError(
                    "灵塔_挑战：防重复标记后虽已回到稳定界面，但没有确认层数推进；"
                    "保留防重复标记，拒绝重复点击"
                )
            if scene_id != 34:
                yield from runtime.goto_view(34)
            self._clear_scheduler_task_payload_flag(task_id, LINGTA_CHAIN_START_MARK)
            outcome = (
                "daily_limit"
                if recovered_passed >= LINGTA_DAILY_LEVEL_LIMIT
                else "recovered_auto_chain"
            )
            message = (
                f"灵塔_挑战：由当前事实确认自动链已结束，"
                f"本轮计数 {recovered_passed}、当前配置 {recovered_current_id}，"
                "已回到世界且未重复点击挑战"
            )
            return self._lingta_terminal_result(
                runtime,
                outcome=outcome,
                message=message,
            )
        if (
            isinstance(start_mark, dict)
            and start_mark.get("launch_left_detail_at")
            and scene_id == LINGTA_CURRENT_FLOOR_SCENE_ID
        ):
            raise RuntimeError(
                "灵塔_挑战：防重复标记证明上次挑战已经离开 #532，当前又返回 #532；"
                "该事实不证明通关或授权第二次挑战，保留标记等待终态能力修复"
            )
        if start_mark and scene_id not in {LINGTA_FAILURE_SCENE_ID}:
            raise RuntimeError(
                "灵塔_挑战：存在未收口自动链防重复标记，当前又不是已知失败终态；拒绝重复点击"
            )
        if scene_id == LINGTA_FAILURE_SCENE_ID:
            text = runtime.ocr_text(frame)
            if "变强" not in text:
                raise RuntimeError("灵塔_挑战：#365 未确认失败终态文案")
            start_mark = self._persist_lingta_power_limit_mark(
                task_id,
                start_mark if isinstance(start_mark, dict) else None,
                passed=0,
            )
            payload[LINGTA_CHAIN_START_MARK] = start_mark
            yield from self._leave_lingta_failure_to_world(runtime)
            self._clear_scheduler_task_payload_flag(task_id, LINGTA_CHAIN_START_MARK)
            return self._lingta_terminal_result(
                runtime,
                outcome="power_limit",
                message="灵塔_挑战：当前层挑战失败，已达到本日战力极限并回到世界",
            )
        if scene_id is None:
            raise RuntimeError("灵塔_挑战：启动前是未知战斗/加载态，拒绝导航")

        # A prior navigation-only attempt may already have opened #532 before
        # failing to classify the direct landing.  This is still a safe
        # pre-click state: continue from the visible Challenge button instead
        # of navigating away and reopening the same detail.
        progress = (
            None
            if scene_id == LINGTA_CURRENT_FLOOR_SCENE_ID
            else (yield from self._open_lingta_current_floor_detail(runtime))
        )
        snapshot = self._read_lingta_challenge_snapshot()
        if not snapshot.get("ok") and progress is None:
            raise RuntimeError(
                f"灵塔_挑战：当前层详情已打开，但只读层数事实不可用：{snapshot.get('reason') or snapshot}"
            )
        start_tower_id = int(snapshot.get("current_tower_id") or 0)
        start_mark_value = {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "start_tower_id": start_tower_id,
            "ui_passed": progress.passed if progress is not None else None,
            "ui_total": progress.total if progress is not None else None,
            "start_evidence": (
                "runtime_memory"
                if snapshot.get("ok")
                else "unique_ui_progress"
            ),
        }
        if not self._set_scheduler_task_payload_flag(
            task_id,
            LINGTA_CHAIN_START_MARK,
            start_mark_value,
        ):
            raise RuntimeError("灵塔_挑战：启动防重复标记未确认持久化，拒绝点击挑战")
        payload[LINGTA_CHAIN_START_MARK] = start_mark_value
        runtime.click_ocr_text(
            LINGTA_CURRENT_FLOOR_SCENE_ID,
            "挑战",
            in_shapes=("挑战文字",),
            match_mode="exact",
            crop=True,
        )

        clicked_at = time.monotonic()
        deadline = clicked_at + monitor_timeout
        # ``DungeonSceneNengLiTa.Enter`` clears the client counter only after
        # this click.  A value read on #532 can therefore be stale residue from
        # an earlier tower session and must never seed the new run's result.
        last_snapshot = {**snapshot, "chain_pass_count": 0}
        max_chain_pass_count = 0
        settlement_probe_until = 0.0
        last_frame: str | None = None
        last_runtime_report_at = 0.0
        last_reported_chain_pass_count = -1
        launch_left_detail = False
        ordinary_result_latched = False
        while time.monotonic() <= deadline:
            self._raise_if_stopped(stop_event)
            scene_id, _score, frame = runtime.current_scene(
                [
                    LINGTA_FAILURE_SCENE_ID,
                    LINGTA_LIST_SCENE_ID,
                    LINGTA_CURRENT_FLOOR_SCENE_ID,
                    LINGTA_DAILY_LIMIT_SCENE_ID,
                    LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID,
                    LINGTA_ORDINARY_RESULT_SCENE_ID,
                    34,
                ],
                update=True,
            )
            if isinstance(frame, str) and frame:
                last_frame = frame
            if scene_id != LINGTA_ORDINARY_RESULT_SCENE_ID:
                ordinary_result_latched = False
            if scene_id != LINGTA_CURRENT_FLOOR_SCENE_ID:
                launch_left_detail = True
                # Battle/loading has no formal GUI scene.  Persist that the
                # one authorized Challenge click did leave #532, so a later
                # retry cannot misclassify a returned detail page as a click
                # that never launched.  This observation authorizes no second
                # click and proves no victory by itself.
                if (
                    scene_id is None
                    and not start_mark_value.get("launch_left_detail_at")
                ):
                    start_mark_value = {
                        **start_mark_value,
                        "launch_left_detail_at": datetime.now().isoformat(
                            timespec="seconds"
                        ),
                    }
                    if not self._set_scheduler_task_payload_flag(
                        task_id,
                        LINGTA_CHAIN_START_MARK,
                        start_mark_value,
                    ):
                        raise RuntimeError(
                            "灵塔_挑战：已观察到挑战离开 #532，但启动确认未能落盘；"
                            "保留原防重复标记并停止观察"
                        )
                    payload[LINGTA_CHAIN_START_MARK] = start_mark_value
            if scene_id == LINGTA_FAILURE_SCENE_ID:
                text = runtime.ocr_text(frame)
                if "变强" not in text:
                    raise RuntimeError("灵塔_挑战：#365 未确认失败终态文案")
                final_snapshot = self._read_lingta_challenge_snapshot()
                passed = max(
                    max_chain_pass_count,
                    int(final_snapshot.get("chain_pass_count") or 0),
                    int(last_snapshot.get("chain_pass_count") or 0),
                )
                start_mark_value = self._persist_lingta_power_limit_mark(
                    task_id,
                    start_mark_value if isinstance(start_mark_value, dict) else None,
                    passed=passed,
                )
                payload[LINGTA_CHAIN_START_MARK] = start_mark_value
                yield from self._leave_lingta_failure_to_world(runtime)
                self._clear_scheduler_task_payload_flag(task_id, LINGTA_CHAIN_START_MARK)
                return self._lingta_terminal_result(
                    runtime,
                    outcome="power_limit",
                    message=f"灵塔_挑战：连续通过 {max(0, passed)} 层后挑战失败，已达到本日战力极限并回到世界",
                )
            if scene_id == LINGTA_DAILY_LIMIT_SCENE_ID:
                start_mark_value = self._persist_lingta_terminal_mark(
                    task_id,
                    start_mark_value,
                    scene_id=LINGTA_DAILY_LIMIT_SCENE_ID,
                )
                payload[LINGTA_CHAIN_START_MARK] = start_mark_value
                landing = yield from runtime.wait_click_then_view(
                    LINGTA_DAILY_LIMIT_SCENE_ID,
                    "点击退出",
                    [34, LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID],
                    timeout=60,
                    max_clicks=1,
                    retry_if_source_remains=False,
                    label="灵塔_挑战：每日上限后退出",
                )
                if getattr(landing, "id", landing) == LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID:
                    yield from runtime.wait_click_then_view(
                        LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID,
                        "返回灵塔列表",
                        LINGTA_LIST_SCENE_ID,
                        timeout=60,
                        max_clicks=1,
                        retry_if_source_remains=False,
                        label="灵塔_挑战：上限详情返回列表",
                    )
                    yield from runtime.goto_view(34)
                self._clear_scheduler_task_payload_flag(task_id, LINGTA_CHAIN_START_MARK)
                return self._lingta_terminal_result(
                    runtime,
                    outcome="daily_limit",
                    message="灵塔_挑战：游戏确认今天已挑战 20 层并达到每日上限，已回到世界",
                )
            if scene_id == LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID:
                start_mark_value = self._persist_lingta_terminal_mark(
                    task_id,
                    start_mark_value,
                    scene_id=LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID,
                )
                payload[LINGTA_CHAIN_START_MARK] = start_mark_value
                yield from runtime.wait_click_then_view(
                    LINGTA_DAILY_LIMIT_DETAIL_SCENE_ID,
                    "返回灵塔列表",
                    LINGTA_LIST_SCENE_ID,
                    timeout=60,
                    max_clicks=1,
                    retry_if_source_remains=False,
                    label="灵塔_挑战：上限详情返回列表",
                )
                yield from runtime.goto_view(34)
                self._clear_scheduler_task_payload_flag(task_id, LINGTA_CHAIN_START_MARK)
                return self._lingta_terminal_result(
                    runtime,
                    outcome="daily_limit",
                    message="灵塔_挑战：详情页确认今日层数挑战上限 20/20，已回到世界",
                )
            if scene_id in {LINGTA_LIST_SCENE_ID, 34}:
                final_snapshot = self._read_lingta_challenge_snapshot()
                passed = max(
                    max_chain_pass_count,
                    int(final_snapshot.get("chain_pass_count") or 0),
                    int(last_snapshot.get("chain_pass_count") or 0),
                )
                if passed <= 0:
                    if not final_snapshot.get("ok"):
                        raise RuntimeError(
                            "灵塔_挑战：自动链返回稳定界面，但最终层数事实不可用且防重复标记无推进证据"
                        )
                    advanced = int(final_snapshot["current_tower_id"]) != start_tower_id
                    detail = "配置主键已变化但本轮计数已丢失" if advanced else "未观察到推进"
                    raise RuntimeError(
                        f"灵塔_挑战：自动链返回稳定界面但未确认通关层数（{detail}）；防重复标记保留"
                    )
                if scene_id != 34:
                    yield from runtime.goto_view(34)
                self._clear_scheduler_task_payload_flag(task_id, LINGTA_CHAIN_START_MARK)
                outcome = "daily_limit" if passed >= LINGTA_DAILY_LEVEL_LIMIT else "no_next_floor"
                if outcome == "daily_limit":
                    message = (
                        f"灵塔_挑战：自动链正常结束，本轮通过 {passed} 层，"
                        f"已达到每日 {LINGTA_DAILY_LEVEL_LIMIT} 层上限并回到世界"
                    )
                else:
                    message = (
                        f"灵塔_挑战：自动链正常结束，本轮通过 {passed} 层，"
                        "当前没有可继续挑战的下一层并回到世界"
                    )
                return self._lingta_terminal_result(
                    runtime,
                    outcome=outcome,
                    message=message,
                )
            if scene_id == LINGTA_CURRENT_FLOOR_SCENE_ID:
                if (
                    not launch_left_detail
                    and time.monotonic() - clicked_at < start_transition_grace
                ):
                    # The first frames after the one permitted click may still
                    # be the detail page.  Wait for the transition without
                    # ever clicking the entry again.
                    yield from runtime.wait_action_settle(poll_seconds)
                    continue
                if launch_left_detail:
                    evidence_label = "灵塔_挑战_自动链离开后返回532"
                    error_message = (
                        "灵塔_挑战：已确认挑战离开 #532，随后自动链返回 #532；"
                        "当前只读层数事实未证明合法终态，防重复标记保留，拒绝再次点击挑战"
                    )
                else:
                    evidence_label = "灵塔_挑战_点击后仍在532"
                    error_message = (
                        "灵塔_挑战：点击挑战后未观察到离开 #532；"
                        "防重复标记保留，拒绝重试点击"
                    )
                evidence_suffix = self._preserve_lingta_settlement_evidence(
                    runtime,
                    frame,
                    label=evidence_label,
                )
                primary_error = RuntimeError(f"{error_message}{evidence_suffix}")
                try:
                    yield from runtime.goto_view(34)
                except (InterruptedError, GeneratorExit, FanxiuEmulatorRestartRequired):
                    raise
                except Exception as cleanup_error:
                    primary_error.add_note(
                        "灵塔_挑战：点击后仍返回 #532 的局部清场失败："
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
                raise primary_error
            # #548 is the shared, formally identified ordinary victory page.
            # Its required OCR is ``下一层(n秒)`` rather than a static
            # ``下一层``, so this optional latency optimization cannot consume
            # the level-gated terminal branch by analogy.  The click invokes
            # the same native StartNext transition as countdown expiry; if the
            # transient page vanishes before recognition, doing nothing remains
            # correct and the monitor keeps observing the automatic chain.
            if scene_id == LINGTA_ORDINARY_RESULT_SCENE_ID:
                try:
                    yield from runtime.wait_click(
                        LINGTA_ORDINARY_RESULT_SCENE_ID,
                        "下一层",
                        timeout=float(payload.get("next_button_timeout_seconds") or 2.0),
                    )
                    if not ordinary_result_latched:
                        ordinary_result_latched = True
                        max_chain_pass_count += 1
                        start_mark_value = {
                            **start_mark_value,
                            "max_chain_pass_count": max(
                                int(start_mark_value.get("max_chain_pass_count") or 0),
                                max_chain_pass_count,
                            ),
                            "last_observed_at": datetime.now().isoformat(
                                timespec="seconds"
                            ),
                            "progress_evidence": "ordinary_result_next_clicked",
                        }
                        if not self._set_scheduler_task_payload_flag(
                            task_id,
                            LINGTA_CHAIN_START_MARK,
                            start_mark_value,
                        ):
                            raise RuntimeError(
                                "灵塔_挑战：#548 胜利页已确认，但 UI 通关进度未确认落盘；"
                                "保留现场并停止"
                            )
                        payload[LINGTA_CHAIN_START_MARK] = start_mark_value
                        self._log(
                            "detail",
                            f"灵塔_挑战：#548 胜利页确认本轮至少通过 {max_chain_pass_count} 层",
                        )
                except RuntimeError as exc:
                    message = str(exc)
                    if "wait_click #548" not in message or "超时" not in message:
                        raise
                    self._log(
                        "detail",
                        "灵塔_挑战：#548 倒计时页已在可选加速点击前消失，放弃本次点击并重新识别整帧",
                    )
            # 战斗、胜利倒计时和加载帧不需要点击。低频只读事实用于
            # 记录进度，任何不完整结果都不改变控制流。
            current = self._read_lingta_challenge_snapshot()
            if current.get("ok"):
                last_snapshot = current
                observed_chain_pass_count = int(current.get("chain_pass_count") or 0)
                report_now = time.monotonic()
                if (
                    observed_chain_pass_count != last_reported_chain_pass_count
                    or report_now - last_runtime_report_at >= 60.0
                ):
                    elapsed = float(current.get("elapsed_seconds") or 0.0)
                    self._log(
                        "detail",
                        "灵塔_挑战：只读 Runtime 进度 "
                        f"tower={int(current.get('current_tower_id') or 0)} "
                        f"chain_pass={observed_chain_pass_count} "
                        f"probe={elapsed:.2f}s",
                    )
                    last_runtime_report_at = report_now
                    last_reported_chain_pass_count = observed_chain_pass_count
                if observed_chain_pass_count > max_chain_pass_count:
                    # A win has just been observed.  For a short window inspect
                    # the result text so a level-gated static victory page
                    # fails quickly with its evidence intact instead of timing
                    # out after an hour.
                    settlement_probe_until = time.monotonic() + 12.0
                max_chain_pass_count = max(
                    max_chain_pass_count,
                    observed_chain_pass_count,
                )
                if observed_chain_pass_count > int(
                    start_mark_value.get("max_chain_pass_count") or 0
                ):
                    start_mark_value = {
                        **start_mark_value,
                        "max_chain_pass_count": observed_chain_pass_count,
                        "last_tower_id": int(current.get("current_tower_id") or 0),
                        "last_observed_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    if not self._set_scheduler_task_payload_flag(
                        task_id,
                        LINGTA_CHAIN_START_MARK,
                        start_mark_value,
                    ):
                        raise RuntimeError(
                            "灵塔_挑战：已观察到胜利，但防重复标记进度未确认落盘；"
                            "保留启动标记和现场"
                        )
                    payload[LINGTA_CHAIN_START_MARK] = start_mark_value
                if (
                    observed_chain_pass_count > 0
                    and current.get("config_bounds_complete") is True
                    and current.get("has_current_tower_config") is False
                ):
                    evidence_suffix = self._preserve_lingta_settlement_evidence(
                        runtime,
                        frame,
                        label="灵塔_挑战_全塔终态",
                    )
                    raise RuntimeError(
                        "灵塔_挑战：胜利后当前层已越过本地最大灵塔配置，"
                        "确认到达全塔终态；防重复标记和结算现场已保留，"
                        f"需用本帧验证正式退出资产{evidence_suffix}"
                    )
                if frame and time.monotonic() <= settlement_probe_until:
                    settlement = classify_lingta_settlement_text(runtime.ocr_text(frame))
                    if settlement == "level_gate":
                        evidence_suffix = self._preserve_lingta_settlement_evidence(
                            runtime,
                            frame,
                            label="灵塔_挑战_等级门槛结算",
                        )
                        raise RuntimeError(
                            "灵塔_挑战：胜利后停在静态‘下一层/点击退出’等级门槛页；"
                            "防重复标记和现场已保留，需用本帧补正式退出资产"
                            f"{evidence_suffix}"
                        )
                    if settlement == "capacity_failure":
                        evidence_suffix = self._preserve_lingta_settlement_evidence(
                            runtime,
                            frame,
                            label="灵塔_挑战_专用失败汇总",
                        )
                        raise RuntimeError(
                            "灵塔_挑战：连续挑战后进入灵塔专用失败汇总页；"
                            "防重复标记、本轮计数和现场已保留，需用本帧补正式退出资产"
                            f"{evidence_suffix}"
                        )
                    if settlement == "tower_complete":
                        evidence_suffix = self._preserve_lingta_settlement_evidence(
                            runtime,
                            frame,
                            label="灵塔_挑战_已通关结算",
                        )
                        raise RuntimeError(
                            "灵塔_挑战：胜利后显示‘已通关’，但尚缺该终态的真实退出资产；"
                            f"防重复标记和现场已保留{evidence_suffix}"
                        )
            yield from runtime.wait_action_settle(poll_seconds)

        evidence_suffix = self._preserve_lingta_settlement_evidence(
            runtime,
            last_frame,
            label="灵塔_挑战_自动链监控超时",
        )
        raise TimeoutError(
            "灵塔_挑战：自动链监控超时；防重复标记保留，禁止 Scheduler 重试点击"
            f"{evidence_suffix}"
        )


def next_lingta_challenge_time(now: datetime | None = None) -> datetime:
    """Return the next absolute daily 07:00 trigger after ``now``."""

    current = now or datetime.now()
    candidate = current.replace(
        hour=LINGTA_DAILY_TRIGGER[0],
        minute=LINGTA_DAILY_TRIGGER[1],
        second=0,
        microsecond=0,
    )
    if candidate <= current:
        candidate += timedelta(days=1)
    return candidate


def lingta_challenge_admission(
    now: datetime | None = None,
) -> dict[str, object] | None:
    """Reject accidental pre-07:00 dispatches without touching the game."""

    current = now or datetime.now()
    today_trigger = current.replace(
        hour=LINGTA_DAILY_TRIGGER[0],
        minute=LINGTA_DAILY_TRIGGER[1],
        second=0,
        microsecond=0,
    )
    if current >= today_trigger:
        return None
    return {
        "result": "success",
        "message": "灵塔_挑战：尚未到每日 07:00，未读取或操作游戏界面",
        "next_time": today_trigger.strftime("%Y-%m-%d %H:%M:%S"),
        "current_scene": None,
    }


__all__ = [
    "LINGTA_DAILY_LEVEL_LIMIT",
    "LINGTA_DAILY_TRIGGER",
    "LINGTA_CHAIN_START_MARK",
    "LingtaProgress",
    "classify_lingta_settlement_text",
    "lingta_challenge_admission",
    "lingta_current_card_point",
    "lingta_progress_fragment",
    "next_lingta_challenge_time",
    "parse_lingta_progress_lines",
]
