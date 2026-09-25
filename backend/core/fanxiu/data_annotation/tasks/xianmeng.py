"""仙盟挑战：准入、目标选择、进攻选项、奖励与业务调度。

由执行器组合提供场景、Runtime 与调度能力；仅导入模块不会执行游戏动作。
"""
from __future__ import annotations
from backend.core.fanxiu.activity.xianmeng_targets import (
    describe_xianmeng_attackable_targets,
    plan_xianmeng_targets,
)
import io
import math
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any
from pyxllib.prog import BehaviorTreeStatus
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.evidence_retention import prune_fanxiu_evidence
from backend.core.temp_paths import codeyun_temp_root
from backend.core.fanxiu.data_annotation.ocr_spatial import find_fuzzy_text_matches, find_text_matches, select_fuzzy_text_match, select_text_match
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.ocr_values import FULLWIDTH_DIGIT_TRANSLATION


class DailyXianmengTaskMixin:
    def daily_xianmeng_admission(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = dict(payload or {})
        now = job_now()
        end_text = str(payload.get("daily_end_time") or "22:00")
        try:
            end_clock = datetime.strptime(end_text, "%H:%M").time()
        except ValueError:
            end_clock = datetime.strptime("22:00", "%H:%M").time()
        close_at = now.replace(
            hour=end_clock.hour,
            minute=end_clock.minute,
            second=end_clock.second,
            microsecond=0,
        )
        if now < close_at:
            return None
        return self._persist_admission_decision(payload, {
            "result": "success",
            "message": f"仙盟_挑战：当前已到或超过 {end_text}，活动窗口结束，未执行游戏操作",
            "next_time": None,
            "current_scene": None,
        })

    def _execute_daily_xianmeng_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = payload or {}
        self._daily_xianmeng_excluded_target_ids = set()
        self._daily_xianmeng_fallback_cooldowns = []
        target_rounds = int(payload.get("rounds") or payload.get("max_rounds") or 0)
        context = self._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
        attacks = 0
        remaining_attack_count: int | None = None
        swallowed_clicks = 0
        triple_attack_enabled = False
        attack_options_initialized = False
        single_attacks_since_option_probe = 0
        next_triple_probe_after = 1
        reward_score_checked = False
        reward_score_probe_failures = 0
        entry_scene = yield from self._enter_daily_xianmeng_attack_view(
            context,
            payload,
        )
        if entry_scene is None:
            return "skipped"
        scene_id = int(entry_scene)
        while True:
            if target_rounds > 0 and attacks >= target_rounds:
                yield from self._return_daily_xianmeng_to_world(context)
                self._record_daily_xianmeng_done(
                    payload,
                    message=f"已完成 {attacks} 轮攻击并返回 #34",
                )
                return "success"
            if scene_id == 317:
                yield from self._record_daily_xianmeng_immunity_cd(context, payload)
                return "skipped"

            if scene_id in (294, 295):
                # 临近结束只清体力；积分不再决定选敌或开启三连，无需读战报 OCR。
                if (scene_id == 294 and not reward_score_checked
                        and self._daily_xianmeng_triple_window_open(payload)):
                    reward_points, reward_text = context.ocr_value_in_shapes(
                        294, ("个人积分",),
                        padding=int(payload.get("reward_score_ocr_padding") or 12),
                        crop=True,
                        parse_value=lambda raw: self._parse_daily_xianmeng_personal_scores(raw) or None,
                    )
                    if reward_points:
                        reward_score_checked = True
                        average_score = sum(reward_points) / len(reward_points)
                        self._daily_xianmeng_recent_average_score = average_score
                        minimum_score = float(
                            payload.get("minimum_average_personal_score") or 200
                        )
                        self._log(
                            "detail",
                            f"日常_仙盟：首份战报个人积分 {reward_points}，平均 {average_score:.1f}",
                        )
                        if average_score < minimum_score:
                            try:
                                count_snapshot = self._read_daily_xianmeng_count_snapshot()
                            except Exception as exc:
                                count_snapshot = {
                                    "ok": False,
                                    "complete": False,
                                    "reason": str(exc),
                                }
                            continue_sweep, runtime_remaining = (
                                self._daily_xianmeng_should_continue_low_score_sweep(
                                    count_snapshot,
                                    payload,
                                    now=job_now(),
                                )
                            )
                            if continue_sweep:
                                self._log(
                                    "warning",
                                    f"日常_仙盟：首份战报平均个人积分 {average_score:.1f} "
                                    f"低于 {minimum_score:g}，但 Runtime 剩余体力 "
                                    f"{runtime_remaining}，本 Cell 继续批量清扫；"
                                    "后续只按本地单次/三连成本扣减，不逐轮复查积分",
                                )
                            else:
                                self._schedule_daily_xianmeng_retry(
                                    payload,
                                    seconds=int(payload.get("low_score_retry_seconds") or 1200),
                                    message=(
                                        f"首份战报平均个人积分 {average_score:.1f} "
                                        f"低于 {minimum_score:g}，"
                                        "等待队友处理防护机制"
                                    ),
                                )
                                yield from self._return_daily_xianmeng_to_world(context)
                                return "skipped"
                    else:
                        reward_score_probe_failures += 1
                        if reward_score_probe_failures >= 2:
                            reward_score_checked = True
                            self._log(
                                "warning",
                                "日常_仙盟：连续两份战报未识别到个人积分，"
                                "本目标阶段不再重复 OCR，继续攻击",
                            )
                # The control can be visually recognizable before the result
                # popup has finished enabling input.
                yield from context.wait_action_settle(
                    float(payload.get("result_click_ready_seconds") or 0.8)
                )
                context.click_shape_center_fast(scene_id, "确定" if scene_id == 294 else "关闭")
                yield from context.wait_action_settle(float(payload.get("result_close_settle_seconds") or 0.35))
                scene_id = yield from self._wait_daily_xianmeng_fast_attack_scene(
                    context,
                    payload,
                    result_min_elapsed=float(payload.get("result_close_retry_seconds") or 2.0),
                )
                continue

            if scene_id == 293:
                if not attack_options_initialized:
                    triple_attack_enabled = yield from self._ensure_daily_xianmeng_attack_options(
                        context,
                        payload,
                    )
                    remaining_attack_count = yield from self._read_daily_xianmeng_attack_count_once(
                        context,
                        payload,
                    )
                    attack_options_initialized = True
                    self._log(
                        "success",
                        "日常_仙盟：攻击阶段初始化完成，"
                        f"Runtime 剩余体力 {remaining_attack_count}，三连={triple_attack_enabled}",
                    )
                    next_triple_probe_after = self._daily_xianmeng_next_triple_probe_after(
                        getattr(self, "_daily_xianmeng_last_option_snapshot", {}),
                        average_score=getattr(self, "_daily_xianmeng_recent_average_score", None),
                    )
                elif (
                    (triple_attack_enabled and not self._daily_xianmeng_triple_window_open(payload))
                    or (not triple_attack_enabled
                        and self._daily_xianmeng_triple_window_open(payload)
                        and single_attacks_since_option_probe >= next_triple_probe_after)
                ):
                    # Runtime is authoritative, but reading it after every
                    # attack is wasteful.  Probe near the estimated crossing
                    # point, then recompute from the fresh score.
                    triple_attack_enabled = yield from self._ensure_daily_xianmeng_attack_options(
                        context,
                        payload,
                    )
                    single_attacks_since_option_probe = 0
                    next_triple_probe_after = self._daily_xianmeng_next_triple_probe_after(
                        getattr(self, "_daily_xianmeng_last_option_snapshot", {}),
                        average_score=getattr(self, "_daily_xianmeng_recent_average_score", None),
                    )
                    if triple_attack_enabled:
                        self._log(
                            "success",
                            "日常_仙盟：Runtime 确认积分达到三连阈值，已切换并复验三连",
                        )

                minimum_batch = 3 if self._daily_xianmeng_should_preserve_tail_before_triple_disable() else 1
                if remaining_attack_count is not None and remaining_attack_count < minimum_batch:
                        # Local hot-loop bookkeeping is only an estimate. A
                        # toast or overlay can hide the attack control without
                        # consuming stamina; completion needs authoritative data.
                        count_snapshot = self._read_daily_xianmeng_count_snapshot()
                        actual_count = count_snapshot.get("attack_count")
                        if not count_snapshot.get("ok") or not count_snapshot.get("complete") or not isinstance(actual_count, int):
                            raise RuntimeError("日常_仙盟：清扫终点无法核实体力，保留现场")
                        if actual_count >= minimum_batch:
                            remaining_attack_count = actual_count
                            self._log("warning", f"日常_仙盟：Runtime 校正仍余 {actual_count} 体力，继续清扫")
                            continue
                        yield from self._return_daily_xianmeng_to_cover(context)
                        claimed = yield from self._claim_daily_xianmeng_task_rewards(
                            context,
                            payload,
                        )
                        if claimed > 0:
                            self._log(
                                "action",
                                f"日常_仙盟：攻击次数耗尽后又领取 {claimed} 档任务奖励，"
                                "重新进入战场清空新增体力",
                            )
                            next_scene = yield from self._enter_daily_xianmeng_attack_view(
                                context,
                                payload,
                            )
                            if next_scene is None:
                                return "skipped"
                            scene_id = int(next_scene)
                            attack_options_initialized = False
                            remaining_attack_count = None
                            reward_score_checked = False
                            reward_score_probe_failures = 0
                            continue
                        yield from self._return_daily_xianmeng_to_world(context)
                        self._record_daily_xianmeng_done(
                            payload,
                            message=f"任务已无可领且 Runtime 攻击体力 {actual_count} 小于本阶段阈值 {minimum_batch}，已返回 #34",
                        )
                        return "success"

                required_attempts = self._daily_xianmeng_required_attempts(triple_attack_enabled)
                if (
                    triple_attack_enabled
                    and remaining_attack_count is not None
                    and 0 < remaining_attack_count < required_attempts
                ):
                    # Before the autonomous sweep retain the triple tail;
                    # afterwards switch to singles when the full Task owns
                    # clearing the final 1-2 stamina.
                    if self._daily_xianmeng_should_preserve_tail_before_triple_disable():
                        next_time = self._daily_xianmeng_event_tail_next_time(payload)
                        if next_time:
                            yield from self._return_daily_xianmeng_to_world(context)
                            payload["_xianmeng_next_time"] = next_time
                            self._log(
                                "success",
                                f"日常_仙盟：剩余 {remaining_attack_count} 次不足三连，"
                                f"保留到下一活动尾程，下次 {next_time}",
                            )
                            return "skipped"
                    triple_attack_enabled = yield from self._disable_daily_xianmeng_triple_for_tail(
                        context,
                        payload,
                        remaining_attempts=remaining_attack_count,
                    )
                    required_attempts = self._daily_xianmeng_required_attempts(triple_attack_enabled)
                context.click_shape_center_fast(293, "攻击")
                yield from context.wait_action_settle(float(payload.get("attack_click_settle_seconds") or 0.25))
                departed = yield from self._wait_daily_xianmeng_attack_departure(context, payload)
                if not departed:
                    swallowed_clicks += 1
                    if swallowed_clicks >= 3:
                        # This is an exceptional checkpoint, not part of the hot
                        # loop. The old OCR immunity branch is intentionally paid
                        # only after repeated attack-state non-transitions.
                        frame = context.cur_frame(update=True)
                        immunity_text = context.ocr_text_in_shapes(
                            317,
                            ("免战",),
                            padding=20,
                            frame_data_url=frame,
                            crop=True,
                        )
                        if "免战" in str(immunity_text or ""):
                            yield from self._record_daily_xianmeng_immunity_cd(context, payload)
                            return "skipped"
                        raise RuntimeError(
                            "日常_仙盟：攻击按钮连续三次未推进，结果未确认；"
                            "保留现场，不退出世界、不安排业务重试"
                        )
                    self._log("warning", f"日常_仙盟：攻击按钮未触发状态迁移，重试 {swallowed_clicks}/3")
                    scene_id = 293
                    continue
                swallowed_clicks = 0
                # Disappearance alone is not battle evidence: toasts and global
                # overlays can cover the control. Require a real result before
                # recording a consumed round, never accept #293 here.
                scene_id = yield from self._wait_daily_xianmeng_fast_attack_scene(
                    context, payload, accept_attack=False,
                )
                attacks += 1
                if remaining_attack_count is not None:
                    remaining_attack_count = max(0, remaining_attack_count - required_attempts)
                if not triple_attack_enabled:
                    single_attacks_since_option_probe += required_attempts
                if attacks == 1 or attacks % 10 == 0 or remaining_attack_count == 0:
                    self._log(
                        "success",
                        "日常_仙盟：已确认攻击结果，"
                        f"完成 {attacks} 轮，按本地计数剩余 {remaining_attack_count}",
                    )
                continue

            scene_id = yield from self._wait_daily_xianmeng_fast_attack_scene(context, payload)

    def _wait_daily_xianmeng_attack_departure(self, context: Any, payload: dict[str, Any]):
        """Confirm an attack from the attack control disappearing, not a guessed next popup."""

        # The victory animation can keep the old attack ROI visually intact
        # for more than four seconds even though the server has accepted the
        # attack. Keep this cheaper shape probe, but cover the observed
        # animation window before declaring a swallowed click.
        timeout = float(payload.get("attack_departure_timeout_seconds") or 8.0)
        threshold = float(payload.get("fast_attack_shape_threshold") or self.overlay_threshold)
        start = time.monotonic()
        while True:
            self._raise_if_stopped(context.stop_event or threading.Event())
            self._clear_tick_frame(context.ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = context.cur_frame(update=True)
            if context.shape_score(293, "攻击", frame_data_url=frame) < threshold:
                return True
            if time.monotonic() - start >= timeout:
                return False
            yield from context.wait_action_settle(0.15)

    def _read_daily_xianmeng_attack_count_once(self, context: Any, payload: dict[str, Any]):
        """Read the attack count once per attack stage; never per battle."""
        yield from ()  # Preserve the generator API without issuing a GUI action.
        # The GUI also displays clone_count beside attack_count. OCR numeric
        # order is not resource identity; use the typed Runtime field first.
        snapshot = self._read_daily_xianmeng_count_snapshot()
        if snapshot.get("ok") and snapshot.get("complete") and isinstance(snapshot.get("attack_count"), int):
            return int(snapshot["attack_count"])
        raise RuntimeError("日常_仙盟：Runtime 无法确认攻击体力，保留现场，不用相邻分身次数代替")

    @staticmethod
    def _parse_daily_xianmeng_personal_scores(text: str) -> list[int]:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return [
            int(value)
            for value in re.findall(r"个人积分\D{0,12}([0-9]{2,4})", normalized)
        ]

    def _wait_daily_xianmeng_fast_attack_scene(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        accept_results: bool = True,
        accept_attack: bool = True,
        result_min_elapsed: float = 0.0,
        attack_min_elapsed: float = 0.0,
    ):
        """Recognize the attack loop using only the mapped scene image ROIs."""

        timeout = float(payload.get("fast_attack_scene_timeout_seconds") or 20.0)
        threshold = float(payload.get("fast_attack_shape_threshold") or self.overlay_threshold)
        start = time.monotonic()
        layered_probe_done = False
        while True:
            self._raise_if_stopped(context.stop_event or threading.Event())
            self._clear_tick_frame(context.ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = context.cur_frame(update=True)
            # 关战报后先看攻击按钮；攻击后只看结果。每帧命中即返回，
            # 不为已确认状态继续识别其它页面的 Shape/OCR。
            elapsed = time.monotonic() - start
            scores = {}
            if accept_attack and elapsed >= max(0.0, float(attack_min_elapsed)):
                scores[293] = context.shape_score(293, "攻击", frame_data_url=frame)
                if scores[293] >= threshold:
                    return 293
            if accept_results and elapsed >= max(0.0, float(result_min_elapsed)):
                scores[295] = context.shape_score(295, "关闭", frame_data_url=frame)
                if scores[295] >= threshold:
                    return 295
                report_confirm_score = context.shape_score(294, "确定", frame_data_url=frame)
                report_title_score = (
                    context.shape_score(294, "挑战成功", frame_data_url=frame)
                    if report_confirm_score >= threshold else 0.0
                )
                scores[294] = min(report_confirm_score, report_title_score)
                if scores[294] >= threshold:
                    return 294
            # The reward rows vary, so their broad body anchor is deliberately
            # weaker than a normal control. Pair it with the exact 100% report
            # button; the network warning negative frame scores 0%/2% here.
            # Some multi-battle reports are valid #294 scenes but use a
            # different report body/button skin, so the two cheap ROIs above
            # both score 0%. Pay for one complete layered recognition only
            # after the normal animation window; this keeps the hot path cheap
            # while accepting the observed report variant.
            if not layered_probe_done and elapsed >= 6.0:
                layered_probe_done = True
                layered = yield from context.wait_scene(
                    [294, 295, 293],
                    wait=0.0,
                    required=False,
                    label="日常_仙盟：轻量战报变体复核",
                )
                layered_scene = int(getattr(layered, "scene_id", 0) or 0) if layered is not None else 0
                if accept_results and layered_scene in {294, 295}:
                    return layered_scene
                if accept_attack and layered_scene == 293:
                    return 293
            if elapsed >= timeout:
                raise TimeoutError(
                    "日常_仙盟：轻量攻击循环等待超时，"
                    + ", ".join(f"#{key}={value:.0f}%" for key, value in scores.items())
                )
            yield from context.wait_action_settle(0.25)

    def _read_daily_xianmeng_command_target_snapshot(self) -> dict[str, Any]:
        from backend.core.fanxiu.instrumentation.landcontend import (
            read_landcontend_command_target_snapshot,
        )

        return read_landcontend_command_target_snapshot()

    def _read_daily_xianmeng_immunity_snapshot(self) -> dict[str, Any]:
        from backend.core.fanxiu.instrumentation.landcontend import (
            read_landcontend_immunity_snapshot,
        )

        return read_landcontend_immunity_snapshot()

    def _read_daily_xianmeng_count_snapshot(self) -> dict[str, Any]:
        from backend.core.fanxiu.instrumentation.landcontend import (
            read_landcontend_count_snapshot,
        )

        return read_landcontend_count_snapshot()

    def _click_daily_xianmeng_ocr(
        self,
        context: Any,
        text: str,
        *,
        timeout: float,
        fuzzy: bool = False,
        occurrence: int | None = None,
        max_center_y: float | None = None,
    ):
        """Click a navigation label on the current full frame.

        #66 is used only as the 900x1600 coordinate canvas after leaving that
        page. Business decisions (command target and cooldown) never come from
        this OCR helper.  Every branch locates the label through full-frame OCR
        tokens plus the public ocr_spatial match/select contract; the declared
        shape token view can clearly show a label (e.g. 「跳转」) yet miss it.
        """

        deadline = time.monotonic() + max(0.1, float(timeout))
        last_error: Exception | None = None
        while True:
            self._raise_if_stopped(context.stop_event or threading.Event())
            try:
                frame = context.cur_frame(update=True)
                tokens = context.full_frame_ocr_tokens(frame)
                if fuzzy:
                    matches = [
                        item
                        for item in find_fuzzy_text_matches(
                            tokens,
                            text,
                            min_score=72.0,
                        )
                        if max_center_y is None
                        or item.y + item.h / 2 <= float(max_center_y)
                    ]
                    match = select_fuzzy_text_match(
                        matches,
                        text,
                        occurrence=occurrence,
                        ambiguity_margin=8.0,
                    )
                else:
                    matches = [
                        item
                        for item in find_text_matches(tokens, text)
                        if max_center_y is None
                        or item.y + item.h / 2 <= float(max_center_y)
                    ]
                    match = select_text_match(
                        matches,
                        text,
                        occurrence=occurrence,
                    )
                if match is None:
                    raise RuntimeError(f"「{text}」未唯一匹配，拒绝点击")
                context.click_frame_point(66, *match.point())
                self._log("action", f"日常_仙盟：点击「{text}」")
                return match
            except RuntimeError as exc:
                last_error = exc
            if time.monotonic() >= deadline:
                raise TimeoutError(f"日常_仙盟：等待并点击「{text}」超时：{last_error}")
            self._clear_tick_frame(context.ctx)
            yield BehaviorTreeStatus.RUNNING
            if (context.stop_event or threading.Event()).wait(1.0):
                self._raise_if_stopped(context.stop_event or threading.Event())

    @staticmethod
    def _daily_xianmeng_claim_markers(context: Any) -> list[dict[str, Any]]:
        frame = context.cur_frame(update=True)
        return [
            item
            for item in context.ocr_tokens(frame)
            if str(item.get("text") or "").strip() == "领"
        ]

    @staticmethod
    def _daily_xianmeng_first_task_fingerprint(context: Any) -> str:
        frame = context.cur_frame(update=True)
        tokens = [
            item
            for item in context.ocr_tokens(frame)
            if 60.0 <= float(item.get("x") or 0.0) <= 500.0
            and 275.0 <= float(item.get("y") or 0.0) <= 455.0
        ]
        return "".join(str(item.get("text") or "").strip() for item in tokens)

    @staticmethod
    def _daily_xianmeng_transient_popup_metrics(context: Any, frame: str) -> dict[str, Any]:
        """Recognize the broken dark business layer without inventing a scene.

        The 2026-08-16 production frame had no normal page chrome, an almost
        completely black body, and one bright in-game close control at the
        upper right.  This predicate deliberately requires both facts and is
        only used inside the Xianmeng transaction, where closing the layer is
        a reversible recovery action.
        """

        from PIL import Image

        raw = context.runner._decode_frame_data_url(frame)
        with Image.open(io.BytesIO(raw)) as source:
            image = source.convert("L")
            width, height = image.size
            body = image.crop((0, int(height * 0.12), width, int(height * 0.95)))
            close_roi = image.crop(
                (
                    int(width * 0.80),
                    int(height * 0.045),
                    int(width * 0.94),
                    int(height * 0.13),
                )
            )
            body_histogram = body.histogram()
            close_histogram = close_roi.histogram()
            body_dark_ratio = sum(body_histogram[:24]) / max(1, body.width * body.height)
            close_bright_ratio = sum(close_histogram[170:]) / max(
                1, close_roi.width * close_roi.height
            )
        return {
            "matched": body_dark_ratio >= 0.97 and close_bright_ratio >= 0.012,
            "width": width,
            "height": height,
            "body_dark_ratio": body_dark_ratio,
            "close_bright_ratio": close_bright_ratio,
        }

    def _close_daily_xianmeng_transient_popup(self, context: Any):
        """Close and verify the high-confidence transient Xianmeng business layer."""

        try:
            frame = context.cur_frame(update=True)
            metrics = self._daily_xianmeng_transient_popup_metrics(context, frame)
        except Exception as exc:
            self._log("detail", f"日常_仙盟：异常业务层帧判定失败：{exc}")
            return 0
        if not metrics["matched"]:
            return 0

        evidence_root = codeyun_temp_root("fanxiu-evidence", "xianmeng-transient-popup")
        evidence_path = evidence_root / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
        evidence_path.write_bytes(context.runner._decode_frame_data_url(frame))
        prune_fanxiu_evidence(evidence_root.parent)
        self._log(
            "action",
            (
                "日常_仙盟：识别到异常黑色业务层，优先点击游戏内关闭；"
                f"dark={metrics['body_dark_ratio']:.3f}，"
                f"close={metrics['close_bright_ratio']:.3f}，证据={evidence_path}"
            ),
        )
        # #473 is only the canonical 900x1600 coordinate canvas here.  The
        # click is authorized by the current-frame predicate above, not by a
        # claim that the broken layer itself is scene #473.
        context.click_frame_point(473, 790.0, 130.0)
        yield from context.wait_action_settle(0.8)
        recovered_scene = yield from self._wait_daily_xianmeng_exact_view(
            context,
            473,
            474,
            timeout=8.0,
        )
        self._log("success", f"日常_仙盟：异常业务层已关闭，恢复到 #{recovered_scene}")
        return recovered_scene

    def _claim_daily_xianmeng_task_rewards(
        self,
        context: Any,
        payload: dict[str, Any],
    ):
        """Claim the Xianmeng/cultivation ladders without touching item icons."""

        yield from self._wait_daily_xianmeng_exact_view(context, 473, timeout=12.0)
        cover_markers = self._daily_xianmeng_claim_markers(context)
        if not any(float(item.get("y") or 0.0) >= 1200.0 for item in cover_markers):
            return 0

        context.click_shape_center(473, "任务")
        yield from context.wait_action_settle(
            float(payload.get("task_settle_seconds") or 1.0)
        )
        try:
            yield from self._wait_daily_xianmeng_exact_view(context, 474, timeout=12.0)
        except TimeoutError:
            # The task tab can remain unavailable during an otherwise active
            # qualifying battlefield.  Reward collection is optional and
            # must not block the challenge itself.  Only continue when a
            # fresh exact read proves the ignored tap left us on the cover;
            # any other landing remains a hard failure.
            yield from self._wait_daily_xianmeng_exact_view(context, 473, timeout=3.0)
            self._log(
                "skip",
                "日常_仙盟：任务入口未响应且仍在封面 #473，跳过可选领奖并继续挑战",
            )
            return 0

        claimed = 0
        max_claims = max(1, int(payload.get("max_task_claims_per_tab") or 24))
        tabs = (
            ("仙盟页签", 140.0, 260.0),
            ("修为页签", 300.0, 415.0),
        )
        for tab_shape, min_x, max_x in tabs:
            markers = self._daily_xianmeng_claim_markers(context)
            if not any(
                float(item.get("y") or 0.0) < 300.0
                and min_x <= float(item.get("x") or 0.0) <= max_x
                for item in markers
            ):
                continue
            context.click_shape_center(474, tab_shape)
            yield from context.wait_action_settle(0.8)
            for _index in range(max_claims):
                markers = self._daily_xianmeng_claim_markers(context)
                if not any(
                    float(item.get("y") or 0.0) < 300.0
                    and min_x <= float(item.get("x") or 0.0) <= max_x
                    for item in markers
                ):
                    break
                before = self._daily_xianmeng_first_task_fingerprint(context)
                if not before:
                    break
                # The left text/progress area claims the completed first row.
                # The right reward icons only open #250 item details and are a
                # deliberate no-click region in both code and assets.
                context.click_shape_center(474, "首条任务领取区")
                yield from context.wait_action_settle(0.9)
                recovered_scene = yield from self._close_daily_xianmeng_transient_popup(context)
                if recovered_scene == 473:
                    self._log(
                        "skip",
                        "日常_仙盟：领奖触发异常业务层并已恢复封面，停止可选领奖",
                    )
                    return claimed
                after = self._daily_xianmeng_first_task_fingerprint(context)
                if not after or after == before:
                    break
                claimed += 1
                self._log("detail", f"日常_仙盟：已领取第 {claimed} 档任务奖励")

        markers = self._daily_xianmeng_claim_markers(context)
        remaining_top_markers = [
            item
            for item in markers
            if float(item.get("y") or 0.0) < 300.0
        ]
        if remaining_top_markers:
            self._log(
                "skip",
                "日常_仙盟：任务页仍有领取标记但首行状态不再变化，"
                "已安全停止，拒绝点击奖励图标",
            )
        context.click_shape_center(474, "仙盟争霸")
        yield from context.wait_action_settle(0.8)
        yield from self._wait_daily_xianmeng_exact_view(context, 473, timeout=12.0)
        if claimed:
            self._log("success", f"日常_仙盟：已领取 {claimed} 档任务奖励并返回封面")
        return claimed

    def _return_daily_xianmeng_to_world(self, context: Any):
        self._log("action", "日常_仙盟：返回世界 #34")
        # Each return click must prove its real successor scene before the next
        # step; a hard ``scene_id = <next>`` assignment is not arrival evidence.
        # The routes below are exactly the pre-existing paths (317 still reuses
        # #293「返回」).  No new navigation is introduced here.
        route: dict[int, tuple[int, str, tuple[int, ...]]] = {
            294: (294, "确定", (293,)),
            295: (295, "关闭", (293,)),
            293: (293, "返回", (475,)),
            317: (293, "返回", (475,)),
            471: (471, "返回", (475,)),
            474: (474, "返回", (473,)),
            473: (473, "返回", (34,)),
            475: (475, "离开", (34,)),
        }
        scene_id = 0
        if isinstance(getattr(context, "ctx", None), dict):
            try:
                scene_id = yield from self._wait_daily_xianmeng_exact_view(
                    context, 293, 317, 294, 295, 471, 475, 474, 473, 34, timeout=2.0
                )
            except TimeoutError:
                scene_id = 0
        for _step in range(len(route) + 1):
            if int(scene_id) == 34:
                break
            step = route.get(int(scene_id))
            if step is None:
                break
            click_scene, shape, successors = step
            if int(scene_id) == 475:
                # Click 「离开」 only once the collapsed menu is really present:
                # wait_click proves the #475 pre-click scene, then #34 is
                # strictly re-verified.  A bare settle+click fired too early and
                # the UI had not collapsed yet.
                yield from context.wait_click(
                    click_scene,
                    shape,
                    timeout=8.0,
                )
            else:
                context.click_shape_center(click_scene, shape)
            yield from context.wait_action_settle(0.8)
            successor_timeout = 30.0 if 34 in successors else 8.0
            try:
                scene_id = yield from self._wait_daily_xianmeng_exact_view(
                    context, *successors, timeout=successor_timeout
                )
            except TimeoutError as exc:
                # Preserve the real failure scene (the helper reports its last
                # observation) and stop here; never fall through to navigation
                # on a guessed successor.
                self._log(
                    "warning",
                    f"日常_仙盟：点击 #{click_scene}「{shape}」后未确认后继场景，{exc}",
                )
                raise
        yield from context.go_scene(34)
        # Strict world acceptance: an exact #34 observation, never a global
        # fallback accepted by a plain wait_scene.
        yield from self._wait_daily_xianmeng_exact_view(context, 34, timeout=30.0)
        return "success"

    def _return_daily_xianmeng_to_cover(self, context: Any):
        """Return from any Xianmeng subview to the activity cover (#473)."""
        yield from self._return_daily_xianmeng_to_world(context)
        return (yield from self._enter_daily_xianmeng_cover(context))

    def _enter_daily_xianmeng_cover(self, context: Any):
        recovered_scene = yield from self._close_daily_xianmeng_transient_popup(context)
        if recovered_scene == 473:
            return 473
        try:
            yield from self._wait_daily_xianmeng_exact_view(context, 473, timeout=2.0)
            return 473
        except TimeoutError:
            pass
        yield from context.go_scene(34)
        yield from context.wait_scene([34], wait=30.0, label="日常_仙盟：确认世界 #34")
        yield from context.go_scene(66)
        yield from context.wait_scene([66], wait=30.0, label="日常_仙盟：等待仙盟列表 #66")
        yield from self._click_daily_xianmeng_ocr(
            context,
            "仙盟争霸",
            timeout=20.0,
            max_center_y=800.0,
        )
        yield from context.wait_action_settle(2.0)
        yield from self._wait_daily_xianmeng_exact_view(context, 473, timeout=20.0)
        return 473

    def _advance_daily_xianmeng_after_immunity(
        self,
        context: Any,
        payload: dict[str, Any],
    ):
        """Try the next damaged non-friendly pillar after a target-specific CD."""

        selection = getattr(self, "_daily_xianmeng_target_selection", {})
        if selection.get("mode") != "non-friendly-fallback":
            yield from self._record_daily_xianmeng_immunity_cd(context, payload)
            return None

        target = selection.get("target") if isinstance(selection.get("target"), dict) else {}
        target_id = int(target.get("id") or 0)
        excluded = set(getattr(self, "_daily_xianmeng_excluded_target_ids", set()))
        if target_id > 0:
            excluded.add(target_id)
        self._daily_xianmeng_excluded_target_ids = excluded

        try:
            snapshot = self._read_daily_xianmeng_immunity_snapshot()
        except Exception as exc:
            snapshot = {"ok": False, "complete": False, "reason": str(exc)}
        cooldowns = list(getattr(self, "_daily_xianmeng_fallback_cooldowns", []))
        if snapshot.get("ok") and snapshot.get("complete"):
            cooldowns.append(
                {
                    "target_id": target_id,
                    "target_name": str(target.get("name") or ""),
                    "seconds": max(0, int(snapshot.get("cooldown_seconds") or 0)),
                }
            )
        self._daily_xianmeng_fallback_cooldowns = cooldowns

        remaining = [
            item
            for item in selection.get("candidates", [])
            if isinstance(item, dict) and int(item.get("id") or 0) not in excluded
        ]
        if remaining:
            self._log(
                "skip",
                f"日常_仙盟：{target.get('name') or target_id} 处于免战，"
                f"顺延尝试下一根非友军柱子 {remaining[0].get('name')}",
            )
            yield from self._return_daily_xianmeng_to_world(context)
            return (yield from self._enter_daily_xianmeng_attack_view(context, payload))

        if cooldowns:
            retry_seconds = min(int(item["seconds"]) for item in cooldowns)
            message = f"全部候选均免战，最短动态 CD 剩余 {max(0, retry_seconds)} 秒"
        else:
            retry_seconds = int(payload.get("immunity_probe_retry_seconds") or 300)
            message = "全部候选均不可攻击且动态 CD 暂不可用，按 5 分钟安全复查"
        next_time = self._schedule_daily_xianmeng_retry(
            payload,
            seconds=max(5, retry_seconds),
            message=message,
        )
        yield from self._return_daily_xianmeng_to_world(context)
        self._log(
            "success",
            f"日常_仙盟：降级候选已全部顺延检查，工程调度时间 {next_time}",
        )
        return None

    def _schedule_daily_xianmeng_retry(
        self,
        payload: dict[str, Any],
        *,
        seconds: int,
        message: str,
    ) -> str | None:
        now = job_now()
        retry_at = now + timedelta(seconds=max(0, int(seconds)))
        end_text = str(payload.get("daily_end_time") or "22:00")
        try:
            end_clock = datetime.strptime(end_text, "%H:%M").time()
        except ValueError:
            end_clock = datetime.strptime("22:00", "%H:%M").time()
        close_at = now.replace(
            hour=end_clock.hour,
            minute=end_clock.minute,
            second=end_clock.second,
            microsecond=0,
        )
        next_time = (
            retry_at.strftime("%Y-%m-%d %H:%M:%S")
            if now < close_at and retry_at < close_at
            else None
        )
        payload["_xianmeng_next_time"] = next_time
        if next_time is None:
            self._log(
                "skip",
                f"日常_仙盟：{message}；重试将达到或超过 {end_text}，活动结束，不再调度",
            )
        else:
            self._log("skip", f"日常_仙盟：{message}，下次 {next_time}")
        return next_time

    def read_xianmeng_attackable_targets(self) -> dict[str, Any]:
        """查询已加载战场的目标资格；规则由领域接口统一提供。"""
        return describe_xianmeng_attackable_targets(self._read_daily_xianmeng_command_target_snapshot())

    def _wait_daily_xianmeng_command_target(
        self,
        context: Any,
        payload: dict[str, Any],
    ):
        self._raise_if_stopped(context.stop_event or threading.Event())
        yield BehaviorTreeStatus.RUNNING
        try:
            snapshot = self.read_xianmeng_attackable_targets()
        except Exception as exc:
            snapshot = {"ok": False, "complete": False, "reason": str(exc)}
        if not snapshot.get("ok") or not snapshot.get("complete"):
            raise RuntimeError(
                "日常_仙盟：指挥/阵营事实未完整加载，不能当作没有指挥目标："
                f"{snapshot.get('reason') or 'incomplete Runtime snapshot'}"
            )
        if snapshot["fallback_plan"]["all_opponents_defeated"]:
            payload["_xianmeng_day_complete_reason"] = "所有非友军阵柱积分为0，当日无对手"
            payload["_xianmeng_next_time"] = None
            self._log("success", "日常_仙盟：当日无对手，今日完成；不再复查体力")
            return None
        target = snapshot.get("target")
        eligible_ids = {int(row["id"]) for row in snapshot["fallback_plan"]["candidates"]}
        if (
            snapshot.get("ok")
            and snapshot.get("complete")
            and int(snapshot.get("command_count") or 0) == 1
            and isinstance(target, dict)
            and int(target.get("id") or 0) > 0
            and int(target.get("id") or 0) in eligible_ids
            and str(target.get("name") or "").strip()
        ):
            current_hp = target.get("pillar_cur_hp")
            if (
                not isinstance(current_hp, (int, float))
                or float(current_hp) > 0
            ):
                self._daily_xianmeng_target_selection = {
                    "mode": "command",
                    "camp_count": int(snapshot.get("camp_count") or 0),
                    "target": target,
                    "candidates": [target],
                }
                evidence = (
                    snapshot.get("evidence")
                    if isinstance(snapshot.get("evidence"), dict)
                    else {}
                )
                self._log(
                    "success",
                    (
                        f"日常_仙盟：Runtime 唯一指挥目标 {target['name']}"
                        f"（slot={int(target.get('slot') or 0)}），"
                        f"耗时 {float(snapshot.get('elapsed_seconds') or 0.0):.2f}s，"
                        f"resolver={evidence.get('manager_resolver') or 'unknown'}"
                    ),
                )
                return target
            self._log(
                "skip",
                f"日常_仙盟：指挥目标 {target['name']} 的阵柱已被打碎，等待新指挥目标",
            )
        fallback = snapshot["fallback_plan"]
        excluded = set(getattr(self, "_daily_xianmeng_excluded_target_ids", set()))
        candidates = [
            item
            for item in fallback.get("candidates", [])
            if int(item.get("id") or 0) not in excluded
        ]
        sweep_allowed = self._daily_xianmeng_stamina_sweep_allowed()
        sweep_start_text = self._daily_xianmeng_autonomous_sweep_start_text()
        fallback_allowed = bool(fallback.get("own_pillar_destroyed")) or sweep_allowed
        if fallback_allowed and candidates:
            target = candidates[0]
            self._daily_xianmeng_target_selection = {
                "mode": "non-friendly-fallback",
                "camp_count": int(snapshot.get("camp_count") or 0),
                "target": target,
                "candidates": candidates,
            }
            reason = (
                "我方柱子已爆且无法再设置指挥目标"
                if fallback.get("own_pillar_destroyed")
                else f"已进入 {sweep_start_text} 后体力清扫且当前没有唯一指挥目标"
            )
            self._log(
                "action",
                f"日常_仙盟：{reason}，"
                f"降级选择非友军中柱子损坏最多的 {target['name']}"
                f"（剩余 {float(target['pillar_ratio']) * 100:.1f}%）",
            )
            return target
        own_rows = fallback.get("own_camps") if isinstance(fallback.get("own_camps"), list) else []
        own_hp = [
            f"{row.get('name') or row.get('id')}={float(row['pillar_cur_hp']):.0f}/{float(row['pillar_max_hp']):.0f}"
            for row in own_rows
            if isinstance(row.get("pillar_cur_hp"), (int, float))
            and isinstance(row.get("pillar_max_hp"), (int, float))
        ]
        self._log(
            "skip",
            "日常_仙盟：当前没有可攻击的唯一指挥目标，"
            f"command_count={int(snapshot.get('command_count') or 0)}，"
            f"我方阵柱={','.join(own_hp) or 'unknown'}；"
            + (
                "已允许自主选敌但当前没有可攻击的非友军；等待下次同步"
                if fallback_allowed
                else f"{sweep_start_text} 前保持等待大师兄设置目标"
            ),
        )
        return None

    @staticmethod
    def _daily_xianmeng_stamina_sweep_allowed(now: datetime | None = None) -> bool:
        """Allow autonomous target selection from the configured sweep start."""

        from backend.core.fanxiu.activity.daily_activity_job_registry import (
            XIANMENG_AUTONOMOUS_SWEEP_START,
        )

        current = now or job_now()
        return (current.hour, current.minute) >= XIANMENG_AUTONOMOUS_SWEEP_START

    @staticmethod
    def _daily_xianmeng_autonomous_sweep_start_text() -> str:
        """Format the autonomous sweep start clock from its single constant."""

        from backend.core.fanxiu.activity.daily_activity_job_registry import (
            XIANMENG_AUTONOMOUS_SWEEP_START,
        )

        hour, minute = XIANMENG_AUTONOMOUS_SWEEP_START
        return f"{hour:02d}:{minute:02d}"

    @staticmethod
    def _daily_xianmeng_should_preserve_tail_before_triple_disable(
        now: datetime | None = None,
    ) -> bool:
        """Daytime uses batches >=3; the authorized 21:50 tail drains singles."""
        current = now or job_now()
        return (current.hour, current.minute) < (21, 50)

    _daily_xianmeng_fallback_candidates = staticmethod(plan_xianmeng_targets)

    def _enter_daily_xianmeng_attack_view(
        self,
        context: Any,
        payload: dict[str, Any],
    ):
        current_scene = 0
        try:
            current_scene = yield from self._wait_daily_xianmeng_exact_view(
                context,
                317,
                293,
                295,
                294,
                471,
                475,
                473,
                timeout=2.0,
            )
        except TimeoutError:
            pass
        if current_scene in (317, 293, 295, 294):
            return current_scene

        if current_scene not in (471, 475):
            if current_scene != 473:
                yield from self._enter_daily_xianmeng_cover(context)

            # Rewards can directly replenish challenge stamina and clone counts.
            # Therefore every cover entry claims both ladders before evaluating or
            # consuming the battlefield resource.
            yield from self._claim_daily_xianmeng_task_rewards(context, payload)
            entry_attempts = max(1, int(payload.get("battlefield_entry_attempts") or 3))
            for entry_attempt in range(1, entry_attempts + 1):
                context.click_shape_center(473, "前往战场")
                yield from context.wait_action_settle(
                    float(payload.get("entry_settle_seconds") or 2.0)
                )
                # #475 is the real battlefield page and this is a load-timing
                # wait, not a new navigation route: a global fallback (#34) can
                # appear during the ~60s loading window and must not be read as
                # arrival.  Only a fresh exact #475 succeeds; a bounded probe
                # timeout is treated as "cover never departed" and re-verified
                # below before any bounded retry.
                try:
                    current_scene = yield from self._wait_daily_xianmeng_exact_view(
                        context,
                        475,
                        timeout=float(payload.get("battlefield_entry_probe_timeout") or 90.0),
                    )
                except TimeoutError:
                    current_scene = None
                if current_scene == 475:
                    break
                # Fresh observation only: never assume the cover or blindly click
                # again.  Confirm #473 is currently visible; any other scene is a
                # preserved failure state.
                try:
                    reentered = yield from self._wait_daily_xianmeng_exact_view(
                        context,
                        473,
                        timeout=float(payload.get("battlefield_cover_recheck_timeout") or 5.0),
                    )
                except TimeoutError as exc:
                    raise TimeoutError(
                        "日常_仙盟：前往战场后未出现 #475，且未确认仍在封面 #473，"
                        "保留现场"
                    ) from exc
                current_scene = reentered
                self._log(
                    "warning",
                    f"日常_仙盟：前往战场点击未生效，仍在 #473，重试 {entry_attempt}/{entry_attempts}",
                )
            if current_scene != 475:
                raise TimeoutError(
                    f"日常_仙盟：前往战场连续 {entry_attempts} 次未生效，"
                    f"最终场景 #{current_scene}"
                )
        if current_scene == 475:
            context.click_shape_center(475, "战场地图")
            yield from context.wait_action_settle(
                float(payload.get("map_settle_seconds") or 2.0)
            )
            yield from self._wait_daily_xianmeng_exact_view(context, 471, timeout=20.0)

        target = yield from self._wait_daily_xianmeng_command_target(context, payload)
        if target is None:
            yield from self._return_daily_xianmeng_to_world(context)
            if payload.get("_xianmeng_day_complete_reason"):
                return None
            self._schedule_daily_xianmeng_retry(
                payload,
                seconds=int(payload.get("no_command_retry_seconds") or 1800),
                message=(
                    f"{self._daily_xianmeng_autonomous_sweep_start_text()} 后自主清扫：当前没有可攻击的非友军阵柱，体力未清空，已返回 #34 待复查"
                    if self._daily_xianmeng_stamina_sweep_allowed()
                    else "未等到唯一指挥目标，已返回 #34"
                ),
            )
            return None

        target_id = int(target["id"])
        target_name = str(target["name"])
        target_slot = int(target.get("slot") or 0)
        if target_slot not in range(1, 9):
            raise RuntimeError(f"日常_仙盟：动态目标 {target_name} 缺少有效阵营槽位")
        camp_count = int(self._daily_xianmeng_target_selection.get("camp_count") or 0)
        # Client GetCurMatchType selects a different CampPosition table for
        # <=6 camps. A runtime list index alone does not identify a GUI point.
        if not 1 <= camp_count <= 8 or target_slot > camp_count:
            raise RuntimeError(f"日常_仙盟：无法对齐阵营布局 count={camp_count}, slot={target_slot}")
        slot_shape = f"六阵营槽位{target_slot}" if camp_count <= 6 else f"阵营槽位{target_slot}"
        context.click_shape_center(471, slot_shape)
        self._log(
            "action",
            f"日常_仙盟：按动态 slot={target_slot} 选择 {target_name}({target_id})",
        )
        yield from context.wait_action_settle(float(payload.get("target_settle_seconds") or 1.0))
        # 目标详情弹窗跟随柱子位置浮动，不能用一个固定 #472 坐标点击。
        # 目标决策仍完全来自动态 camp id/slot；OCR 只定位已打开弹窗的动作按钮。
        yield from self._click_daily_xianmeng_ocr(context, "跳转", timeout=10.0)
        self._log("success", f"日常_仙盟：目标详情已打开并跳转 {target_name}({target_id})")
        yield from context.wait_action_settle(float(payload.get("jump_settle_seconds") or 2.0))
        attack_scene = yield from self._wait_daily_xianmeng_exact_view(
            context, 317, 293, timeout=30.0,
        )
        focused = self._read_daily_xianmeng_command_target_snapshot()
        if not focused.get("complete") or int(focused.get("focus_camp_id") or 0) != target_id:
            raise RuntimeError(
                f"日常_仙盟：跳转目标不一致，期望 {target_id}，"
                f"实际 {focused.get('focus_camp_id')}，停止攻击"
            )
        return attack_scene

    def _read_daily_xianmeng_attack_options_snapshot(self) -> dict[str, Any]:
        from backend.core.fanxiu.instrumentation.landcontend import (
            read_landcontend_attack_options_snapshot,
        )

        return read_landcontend_attack_options_snapshot()

    @staticmethod
    def _daily_xianmeng_option_cycle_key(snapshot: dict[str, Any]) -> str:
        captured_at = str(snapshot.get("captured_at") or "")
        day = (
            captured_at[:10]
            if len(captured_at) >= 10
            else datetime.now().astimezone().date().isoformat()
        )
        stage = int(snapshot.get("stage") or 0)
        return f"{day}:stage-{stage}"

    @staticmethod
    def _daily_xianmeng_required_attempts(triple_checked: bool) -> int:
        return 3 if triple_checked else 1

    @staticmethod
    def _daily_xianmeng_next_triple_probe_after(
        snapshot: dict[str, Any],
        *,
        average_score: float | None,
    ) -> int:
        """Estimate a bounded single-attack interval before the next Runtime probe."""

        if bool(snapshot.get("triple_checked")):
            return 1
        score = max(0, int(snapshot.get("score") or 0))
        threshold = max(1, int(snapshot.get("triple_score_threshold") or 15_000))
        gap = max(0, threshold - score)
        if gap <= 0:
            return 1
        estimated_gain = max(50.0, float(average_score or 200.0))
        estimated_rounds = max(1, math.ceil(gap / estimated_gain))
        # Recheck a little before the estimate, but never burn more than ten
        # single attacks without refreshing the authoritative score.
        return max(1, min(10, estimated_rounds - 2))

    @staticmethod
    def _daily_xianmeng_should_continue_low_score_sweep(
        snapshot: dict[str, Any],
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> tuple[bool, int | None]:
        """After autonomous sweep starts, spend stamina on weak-score targets.

        A complete Runtime count is still required: a low score is a quality
        signal, not an attack failure, and must not strand usable stamina.
        """

        remaining = snapshot.get("attack_count")
        if (
            not snapshot.get("ok")
            or not snapshot.get("complete")
            or not isinstance(remaining, int)
        ):
            return False, None
        current = now or job_now()
        from backend.core.fanxiu.activity.daily_activity_job_registry import (
            XIANMENG_AUTONOMOUS_SWEEP_START,
        )
        return (current.hour, current.minute) >= XIANMENG_AUTONOMOUS_SWEEP_START and remaining > 0, remaining

    @staticmethod
    def _daily_xianmeng_attempts_exhausted(numbers: list[int]) -> bool:
        """Fail closed when the stylized zero is not recognized as a digit."""

        return not numbers or min(numbers) <= 0

    @staticmethod
    def _daily_xianmeng_triple_window_open(
        payload: dict[str, Any], *, now: datetime | None = None,
    ) -> bool:
        """游戏在战场结束前 30 分钟禁止三倍挑战；积分达标也不能开启。"""
        current = now or datetime.now().astimezone()
        end_clock = datetime.strptime(str(payload.get("daily_end_time") or "22:00"), "%H:%M").time()
        closes = current.replace(hour=end_clock.hour, minute=end_clock.minute, second=0, microsecond=0)
        return current < closes - timedelta(minutes=30)

    def _ensure_daily_xianmeng_attack_options(
        self,
        context: Any,
        payload: dict[str, Any],
    ):
        """Synchronize #293 toggles from Runtime facts before an attack.

        The in-memory verification cache prevents repeated probes after both options
        have been verified for the current activity day/stage. A new process
        or next-day run safely performs one fresh verification.
        """

        now_day = datetime.now().astimezone().date().isoformat()
        triple_window_open = self._daily_xianmeng_triple_window_open(payload)
        verification_cache = getattr(self, "_daily_xianmeng_option_verification_cache", None)
        if (isinstance(verification_cache, dict) and verification_cache.get("day") == now_day
                and verification_cache.get("triple_window_open") == triple_window_open):
            return bool(verification_cache.get("triple_checked"))

        snapshot = self._read_daily_xianmeng_attack_options_snapshot()
        if not snapshot.get("ok") or not snapshot.get("complete"):
            raise RuntimeError("日常_仙盟：动态插桩未返回完整攻击配置")

        score = int(snapshot["score"])
        skip_threshold = int(snapshot.get("skip_score_threshold") or 1_000)
        triple_threshold = int(snapshot.get("triple_score_threshold") or 15_000)
        desired_skip = score >= skip_threshold
        desired_triple = score >= triple_threshold and triple_window_open
        changed: list[str] = []

        if bool(snapshot.get("skip_checked")) != desired_skip:
            context.click_shape_center(293, "跳过")
            changed.append("跳过")
            yield from context.wait_action_settle(
                float(payload.get("option_settle_seconds") or 0.35)
            )
        if bool(snapshot.get("triple_checked")) != desired_triple:
            context.click_shape_center(293, "三连")
            changed.append("三连")
            yield from context.wait_action_settle(
                float(payload.get("option_settle_seconds") or 0.35)
            )

        verified = (
            self._read_daily_xianmeng_attack_options_snapshot()
            if changed
            else snapshot
        )
        if not verified.get("ok") or not verified.get("complete"):
            raise RuntimeError("日常_仙盟：按钮配置后的动态插桩复验不完整")
        if bool(verified.get("skip_checked")) != desired_skip:
            raise RuntimeError("日常_仙盟：#293[跳过] 状态复验失败，拒绝继续攻击")
        if bool(verified.get("triple_checked")) != desired_triple:
            raise RuntimeError("日常_仙盟：#293[三连] 状态复验失败，拒绝继续攻击")

        self._daily_xianmeng_last_option_snapshot = dict(verified)

        cycle_key = self._daily_xianmeng_option_cycle_key(verified)
        if desired_triple or not triple_window_open:
            self._daily_xianmeng_option_verification_cache = {
                "day": cycle_key[:10],
                "cycle_key": cycle_key,
                "triple_checked": desired_triple,
                "triple_window_open": triple_window_open,
            }
        if not triple_window_open:
            self._log("detail", "日常_仙盟：战场结束前30分钟禁用三连，已确认单攻配置")
        if changed:
            self._log(
                "success",
                f"日常_仙盟：积分 {score}，已配置并复验 {'、'.join(changed)}",
            )
        return desired_triple

    def _disable_daily_xianmeng_triple_for_tail(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        remaining_attempts: int,
    ):
        """Turn triple attack off so the final one or two attempts are consumed."""

        context.click_shape_center(293, "三连")
        yield from context.wait_action_settle(
            float(payload.get("option_settle_seconds") or 0.35)
        )
        verified = self._read_daily_xianmeng_attack_options_snapshot()
        if not verified.get("ok") or not verified.get("complete"):
            raise RuntimeError("日常_仙盟：尾数切换后的动态插桩复验不完整")
        if bool(verified.get("triple_checked")):
            raise RuntimeError("日常_仙盟：尾数不足三连时关闭[三连]失败")
        cycle_key = self._daily_xianmeng_option_cycle_key(verified)
        self._daily_xianmeng_option_verification_cache = {
            "day": cycle_key[:10],
            "cycle_key": cycle_key,
            "triple_checked": False,
            "triple_window_open": self._daily_xianmeng_triple_window_open(payload),
        }
        self._log(
            "success",
            f"日常_仙盟：剩余 {remaining_attempts} 次，已关闭并复验三连，改用单攻清零",
        )
        return False

    def _record_daily_xianmeng_done(self, payload: dict[str, Any], *, message: str) -> str | None:
        from backend.core.fanxiu.activity.daily_activity_job_registry import next_xianmeng_stamina_review

        current = job_now()
        tail = self._daily_xianmeng_event_tail_next_time(payload)
        tail_at = datetime.fromisoformat(tail).replace(tzinfo=current.tzinfo) if tail else None
        review_at = next_xianmeng_stamina_review(current, tail_at=tail_at)
        next_time = review_at.strftime("%Y-%m-%d %H:%M:%S") if review_at else None
        payload["_xianmeng_next_time"] = next_time
        suffix = f"，体力复查下次 {next_time}" if next_time else "，未安排后续触发"
        self._log("success", f"日常_仙盟：{message}{suffix}")
        return next_time

    @staticmethod
    def _daily_xianmeng_event_tail_next_time(
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> str | None:
        """Resolve the authorized event-day final sweep or a legacy override."""

        current = now or job_now()
        if bool(payload.get("schedule_tail_from_daily_activity_list")):
            from backend.core.fanxiu.activity.daily_activity_discovery import (
                read_daily_activity_discovery_plan,
            )
            from backend.core.fanxiu.activity.daily_activity_job_registry import (
                next_xianmeng_challenge_tail_time,
            )

            try:
                plan = read_daily_activity_discovery_plan(
                    target_date=current.date(),
                    allow_discovery=False,
                    force_refresh=False,
                )
                return next_xianmeng_challenge_tail_time(plan, now=current)
            except (OSError, RuntimeError, ValueError):
                return None

        event_date = str(payload.get("event_tail_date") or "").strip()
        raw_times = payload.get("event_tail_times")
        if not event_date or not isinstance(raw_times, list):
            return None
        if event_date != current.date().isoformat():
            return None
        close_at = current.replace(hour=22, minute=0, second=0, microsecond=0)
        candidates: list[datetime] = []
        for raw_time in raw_times:
            try:
                hour, minute = (int(part) for part in str(raw_time).split(":", 1))
                candidates.append(
                    current.replace(hour=hour, minute=minute, second=0, microsecond=0)
                )
            except (TypeError, ValueError):
                continue
        future = sorted(item for item in candidates if current < item < close_at)
        return future[0].strftime("%Y-%m-%d %H:%M:%S") if future else None

    def _record_daily_xianmeng_immunity_cd(self, context: Any, payload: dict[str, Any]):
        try:
            snapshot = self._read_daily_xianmeng_immunity_snapshot()
        except Exception as exc:
            snapshot = {"ok": False, "complete": False, "reason": str(exc)}
        if snapshot.get("ok") and snapshot.get("complete"):
            cd_seconds = int(snapshot.get("cooldown_seconds") or 0)
            retry_seconds = max(0, cd_seconds)
            message = f"动态免战 CD 剩余 {cd_seconds} 秒"
        else:
            retry_seconds = int(payload.get("immunity_probe_retry_seconds") or 300)
            message = "动态免战 CD 暂不可用，按 5 分钟安全复查"
        next_time = self._schedule_daily_xianmeng_retry(
            payload,
            seconds=retry_seconds,
            message=message,
        )
        yield from self._return_daily_xianmeng_to_world(context)
        self._log("success", f"日常_仙盟：免战分支已回到 #34，工程调度时间 {next_time}")
        return next_time

    def _wait_daily_xianmeng_exact_view(self, context: Any, *scene_ids: int, timeout: float) -> int:
        """复用正式严格落点等待，其他全局场景不能证明页面已就绪。"""
        match = yield from context.wait_scene_exact(
            scene_ids, timeout=max(0.1, float(timeout)), label="日常_仙盟：等待目标页面",
        )
        return int(match.scene_id)
