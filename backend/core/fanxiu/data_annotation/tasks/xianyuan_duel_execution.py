"""仙缘斗法业务：参与时段、目标选择、购买次数、阵容调整与结算推进。

纯目标映射与轮次差异规则归 xianyuan_duel；本模块组合 Runtime 事实和
场景操作，通用执行能力由宿主 Executor 提供。
"""
from __future__ import annotations

from .daily_observations import observed_scene_id
from backend.core.fanxiu.data_annotation.effective_time import job_now
import io
import math
import re
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from backend.core.fanxiu.data_annotation.duel_strategy import XIANYUAN_CAREER_LABELS, best_xianyuan_partner_order, parse_slot_value_title, plan_swaps
from backend.core.fanxiu.data_annotation.arena_schedule import XIANYUAN_DUEL_TASK_ID, next_xianyuan_duel_cycle_trigger_at, next_xianyuan_duel_trigger_at, xianyuan_duel_scheduler_in_window, xianyuan_duel_window_text
from backend.core.fanxiu.data_annotation.storage import data_annotation_entry_image_dir
from backend.core.fanxiu.data_annotation.tasks.xianyuan_duel import choose_xianyuan_duel_target, map_xianyuan_duel_targets_to_slots
from backend.core.fanxiu.catalog.server_relations import classify_fanxiu_target_relation
from typing import TYPE_CHECKING
from .xianyuan_duel import xianyuan_duel_runtime_facts_advanced

if TYPE_CHECKING:
    from ..game_context import BehaviorTreeContext


XIANYUAN_DUEL_ENTRY_NOT_FOUND_DATE_FLAG = "_xianyuan_duel_entry_not_found_date"


def read_xianyuan_duel_runtime_snapshot(
    *,
    include_formations: bool = True,
    self_power_hint: int | float | None = None,
) -> dict[str, Any]:
    from backend.core.fanxiu.instrumentation.arena import read_xianyuan_duel_snapshot

    return read_xianyuan_duel_snapshot(
        include_formations=include_formations,
        self_power_hint=self_power_hint,
    )


class XianyuanDuelTaskMixin:
    def _execute_daily_xianyuan_duel_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        scheduler_task_id = str(payload.get("__scheduler_task_id") or XIANYUAN_DUEL_TASK_ID)
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少仙缘斗法资产树路径，无法执行作业")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([308, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id not in {308, 69}:
            scene_id = yield from self._enter_daily_from_world_like(ctx, context, stop_event, frame, scene_id, text, label="仙缘斗法")
        if scene_id not in {308, 69}:
            raise RuntimeError("仙缘斗法：未能进入 #69 日常列表")
        if scene_id == 69:
            status = yield from context.open_daily_entry(
                label="仙缘斗法",
                title_pattern=r"斗\s*法",
                progress_can_mark_done=False,
                max_scrolls=int(payload.get("max_scrolls") or 30),
            )
            if status == "not_found":
                return self._handle_daily_xianyuan_duel_entry_not_found(
                    payload,
                    scheduler_task_id=scheduler_task_id,
                )
            self._clear_scheduler_task_payload_flag(
                scheduler_task_id,
                XIANYUAN_DUEL_ENTRY_NOT_FOUND_DATE_FLAG,
            )
        if not bool(payload.get("skip_purchase")):
            yield from self._prepare_daily_xianyuan_duel_purchases(context, payload)
        max_runs = int(payload.get("max_runs") or 7)
        max_no_effect_retries = max(1, int(payload.get("max_no_effect_retries") or 3))
        no_effect_retries = 0
        refresh_used = False
        completed = 0
        pending_facts: dict[str, Any] | None = None
        stable_self_power: int | None = None
        for probe_index in range(max_runs + max_no_effect_retries + 2):
            facts = pending_facts
            try:
                remaining = yield from self._read_daily_xianyuan_duel_remaining(context, payload)
            except RuntimeError as exc:
                if "无法从 #308[次数] 识别剩余次数" not in str(exc):
                    raise
                facts = facts or (yield from self._wait_current_daily_xianyuan_duel_facts(
                    context,
                    payload,
                    reason=f"round-{completed + 1}-ocr-fallback",
                    self_power_hint=stable_self_power,
                ))
                if facts is None:
                    return (yield from self._defer_daily_xianyuan_duel_runtime(
                        context,
                        payload,
                        scheduler_task_id=scheduler_task_id,
                        reason="次数 OCR 与 Runtime 均未在等待窗口内就绪",
                    ))
                if stable_self_power is None:
                    stable_self_power = int(facts["self_power"])
                remaining = int(facts["remaining_challenges"])
                self._log(
                    "warning",
                    "仙缘斗法：#308[次数] 有界 OCR 仍为空，"
                    f"使用同页 Runtime 剩余次数 {remaining} 兜底",
                )
            if remaining <= 0:
                next_time = next_xianyuan_duel_cycle_trigger_at(job_now()).strftime("%Y-%m-%d %H:%M:%S")
                self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
                self._log(
                    "success",
                    f"仙缘斗法：#308[次数] 已确认剩余为 0，本周期完成，下次 {next_time or '按既有日程'}",
                )
                return "success"
            facts = facts or (yield from self._wait_current_daily_xianyuan_duel_facts(
                context,
                payload,
                reason=f"round-{completed + 1}",
                self_power_hint=stable_self_power,
            ))
            if facts is None:
                return (yield from self._defer_daily_xianyuan_duel_runtime(
                    context,
                    payload,
                    scheduler_task_id=scheduler_task_id,
                    reason="Runtime 未在等待窗口内取得完整当前事实",
                ))
            if stable_self_power is None:
                stable_self_power = int(facts["self_power"])
            facts["self_power"] = stable_self_power
            pending_facts = None
            runtime_remaining = int(facts["remaining_challenges"])
            if runtime_remaining != remaining:
                self._log(
                    "detail",
                    "仙缘斗法：#308[次数] 与 Runtime 次数不同，"
                    f"UI={remaining}、Runtime={runtime_remaining}；完成判据以 UI 为准",
                )
            if completed >= max_runs:
                break

            mapped = yield from self._map_daily_xianyuan_duel_targets(context, facts, payload)
            chosen = choose_xianyuan_duel_target(mapped["targets"], self_power=int(facts["self_power"]))
            if chosen is None:
                refreshes = int(facts.get("remaining_refreshes") or 0)
                if refreshes > 0 and not refresh_used:
                    self._log("action", "仙缘斗法：3 个候选均无法稳妥挑战，使用今日唯一一次刷新")
                    yield from context.wait_click(308, "刷新")
                    refreshed = yield from self._wait_current_daily_xianyuan_duel_facts(
                        context,
                        payload,
                        reason="after-refresh",
                        previous=facts,
                        self_power_hint=stable_self_power,
                    )
                    if refreshed is None:
                        return (yield from self._defer_daily_xianyuan_duel_runtime(
                            context,
                            payload,
                            scheduler_task_id=scheduler_task_id,
                            reason="刷新后 Runtime 候选未在等待窗口内推进",
                        ))
                    refresh_used = True
                    pending_facts = refreshed
                    continue
                chosen = choose_xianyuan_duel_target(
                    mapped["targets"],
                    self_power=int(facts["self_power"]),
                    allow_unbeatable_fallback=True,
                )
                if chosen is None:
                    raise RuntimeError("仙缘斗法：三个候选均缺少有效仙侣战力，不能可靠选择挑战目标")
                self._log(
                    "action",
                    "仙缘斗法：刷新已用尽且三人均强于我方，"
                    f"改为挑战仙侣战力最低的「{chosen['name']}」",
                )

            self._log(
                "action",
                "仙缘斗法：选择 "
                f"{chosen['challenge_shape']}「{chosen['name']}」，积分 {chosen['score']}，"
                f"仙侣战力 {chosen['team_power']}，关系 {chosen['relation_label']}，"
                f"映射 {mapped['method']}",
            )
            yield from context.wait_click_then_scene(308, str(chosen["challenge_shape"]), 309)
            formation_payload = {
                **payload,
                "__xianyuan_duel_facts": facts,
                "__xianyuan_duel_target": chosen,
            }
            yield from self._optimize_daily_xianyuan_duel_formation(context, formation_payload)
            # #308 is the preparation/opponent page, not a valid battle
            # landing.  It may flash during the transition, so the local
            # transaction must wait for the optional battle layer #345 or the
            # layer-0 result page #310.  Only clicking #310 may return to #308.
            try:
                view_after_start = yield from context.wait_click_then_scene(
                    309,
                    "开始挑战",
                    [345, 310],
                    timeout=float(payload.get("battle_result_timeout") or 60.0),
                )
            except TimeoutError:
                _wait_scene_match = yield from context.wait_scene([308], wait=5.0, required=False)
                (recovery_scene_id, recovery_score, _recovery_frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                if recovery_scene_id != 308:
                    raise
                recovery_remaining = yield from self._read_daily_xianyuan_duel_remaining(
                    context,
                    payload,
                )
                if recovery_remaining >= remaining:
                    no_effect_retries += 1
                    if no_effect_retries > max_no_effect_retries:
                        raise RuntimeError(
                            "仙缘斗法：挑战后持续停在 #308 且剩余次数未变化，"
                            f"已重试 {max_no_effect_retries} 次"
                        )
                    self._log(
                        "warning",
                        "仙缘斗法：挑战后观察 60 秒仍在 #308，"
                        f"剩余次数仍为 {recovery_remaining}，判定本次点击未生效并重试 "
                        f"{no_effect_retries}/{max_no_effect_retries}",
                    )
                    continue
                completed += max(1, remaining - recovery_remaining)
                self._log(
                    "warning",
                    "仙缘斗法：未观察到 #310，但 #308 剩余次数已从 "
                    f"{remaining} 降为 {recovery_remaining}，按本轮已生效继续处理",
                )
                pending_facts = yield from self._wait_current_daily_xianyuan_duel_facts(
                    context,
                    payload,
                    reason=f"after-recovered-round-{completed}",
                    previous=facts,
                    self_power_hint=stable_self_power,
                )
                if pending_facts is None:
                    return (yield from self._defer_daily_xianyuan_duel_runtime(
                        context,
                        payload,
                        scheduler_task_id=scheduler_task_id,
                        reason=f"恢复第 {completed} 轮后 Runtime 未在等待窗口内推进",
                    ))
                continue
            if observed_scene_id(view_after_start) == 345:
                view_after_start = yield from context.wait_click_then_scene(345, "跳过", 310)
            yield from context.wait_click_then_scene(310, "点击继续", 308)
            completed += 1
            pending_facts = yield from self._wait_current_daily_xianyuan_duel_facts(
                context,
                payload,
                reason=f"after-round-{completed}",
                previous=facts,
                self_power_hint=stable_self_power,
            )
            if pending_facts is None:
                return (yield from self._defer_daily_xianyuan_duel_runtime(
                    context,
                    payload,
                    scheduler_task_id=scheduler_task_id,
                    reason=f"第 {completed} 轮后 Runtime 未在等待窗口内推进",
                ))
        raise RuntimeError(
            f"仙缘斗法：达到单轮安全上限 {max_runs}，已挑战 {completed} 次但 #308[次数] 仍大于 0"
        )

    def daily_xianyuan_duel_admission(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = dict(payload or {})
        task_id = str(payload.get("__scheduler_task_id") or "")
        if task_id != XIANYUAN_DUEL_TASK_ID:
            return None
        now = job_now()
        if xianyuan_duel_scheduler_in_window(now):
            return None
        window_text = xianyuan_duel_window_text(now)
        next_time = next_xianyuan_duel_trigger_at(now).strftime("%Y-%m-%d %H:%M:%S")
        return self._persist_admission_decision(payload, {
            "result": "success",
            "message": (
                f"仙缘斗法：当前不在 {window_text} 窗口，"
                "错过的场次作废，未执行游戏操作"
            ),
            "next_time": next_time,
            "current_scene": None,
            "scheduler_incident": {
                "kind": "window_expired",
                "cycle_kind": "daily",
                "window": window_text,
                "reason": "该日窗口已结束，禁止跨日补跑",
            },
        })

    def _handle_daily_xianyuan_duel_entry_not_found(
        self,
        payload: dict[str, Any],
        *,
        scheduler_task_id: str,
    ) -> str:
        """Require two same-day complete #69 traversals before closing the cycle."""

        now = job_now()
        today = now.date().isoformat()
        previous_not_found_date = str(
            self._get_scheduler_task_payload_flag(
                scheduler_task_id,
                XIANYUAN_DUEL_ENTRY_NOT_FOUND_DATE_FLAG,
            )
            or ""
        )
        if previous_not_found_date == today:
            next_time = next_xianyuan_duel_cycle_trigger_at(now).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            # Preserve the conservative two-scan evidence if the scheduling
            # write fails.  A later retry can then close the same cycle instead
            # of silently falling back to a new "first" miss.
            self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
            self._clear_scheduler_task_payload_flag(
                scheduler_task_id,
                XIANYUAN_DUEL_ENTRY_NOT_FOUND_DATE_FLAG,
            )
            self._log(
                "success",
                "仙缘斗法：同一日连续两轮归一并扫描完整日常列表仍无入口，"
                f"按本周期无可执行入口收口，下次 {next_time}",
            )
            return "skipped"

        if not self._set_scheduler_task_payload_flag(
            scheduler_task_id,
            XIANYUAN_DUEL_ENTRY_NOT_FOUND_DATE_FLAG,
            today,
        ):
            raise RuntimeError("仙缘斗法：首次未找到入口，但未能持久化复查标记")
        self._record_daily_entry_not_found_retry(
            payload,
            task_id=scheduler_task_id,
            task_type="daily_xianyuan_duel",
            label="仙缘斗法",
            entry_label="斗法",
            seconds=int(payload.get("retry_seconds") or 60),
        )
        return "skipped"

    def _read_current_daily_xianyuan_duel_facts(
        self,
        payload: dict[str, Any],
        *,
        reason: str,
        self_power_hint: int | float | None = None,
    ) -> dict[str, Any]:
        # Target selection only needs the authoritative totals and three target
        # summaries.  Decoding all 20 partner rows on every round used to cost
        # tens of seconds even when the 2x power rule immediately skipped
        # formation changes.  The detailed formation is loaded later only when
        # that rule actually needs it.
        runtime_facts = read_xianyuan_duel_runtime_snapshot(
            include_formations=False,
            self_power_hint=self_power_hint,
        )
        if (
            runtime_facts.get("available")
            and runtime_facts.get("complete")
            and len(runtime_facts.get("targets") or []) == 3
        ):
            self._log("detail", f"仙缘斗法：{reason} 已从游戏 Runtime 常驻模型取得当前事实")
            return runtime_facts

        del payload
        raise RuntimeError(
            "仙缘斗法：Runtime 当前事实不完整，等待模型加载；"
            f"reason={runtime_facts.get('reason') or 'runtime_incomplete'}"
        )

    def _wait_current_daily_xianyuan_duel_facts(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
        *,
        reason: str,
        previous: dict[str, Any] | None = None,
        self_power_hint: int | float | None = None,
    ):
        timeout = max(0.0, float(payload.get("runtime_ready_timeout") or 45.0))
        poll_seconds = max(0.2, float(payload.get("runtime_ready_poll_seconds") or 2.0))
        started = time.monotonic()
        last_reason = "runtime_incomplete"
        while True:
            try:
                facts = self._read_current_daily_xianyuan_duel_facts(
                    payload,
                    reason=reason,
                    self_power_hint=self_power_hint,
                )
                if previous is None or xianyuan_duel_runtime_facts_advanced(previous, facts):
                    if self_power_hint is not None:
                        facts["self_power"] = self_power_hint
                    return facts
                last_reason = "动态事实尚未推进"
            except RuntimeError as exc:
                last_reason = str(exc)
            elapsed = time.monotonic() - started
            if elapsed >= timeout:
                return None
            self._log(
                "wait",
                f"仙缘斗法：{reason} 等待 Runtime {elapsed:.1f}/{timeout:.0f}s，{last_reason}",
            )
            yield from context.wait_action_settle(
                min(poll_seconds, max(0.2, timeout - elapsed))
            )

    def _defer_daily_xianyuan_duel_runtime(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
        *,
        scheduler_task_id: str,
        reason: str,
    ):
        retry_seconds = max(60, int(payload.get("retry_seconds") or 60))
        next_time = (job_now() + timedelta(seconds=retry_seconds)).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
        self._log("skip", f"仙缘斗法：{reason}，{next_time} 安全复查")
        try:
            yield from context.go_scene(34)
        except (InterruptedError, GeneratorExit):
            raise
        except Exception as exc:
            self._log("warning", f"仙缘斗法：已保存安全复查时间，但返回世界未完成：{exc}")
        return "skipped"

    def _map_daily_xianyuan_duel_targets(
        self,
        context: BehaviorTreeContext,
        facts: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _wait_scene_match = yield from context.wait_scene([308], wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id != 308:
            raise RuntimeError(f"仙缘斗法：选人要求当前为 #308，实际 #{scene_id or 'unknown'} {score:.0f}%")
        ocr_names = [
            context.ocr_text_in_shapes(
                308,
                (f"姓名{slot}",),
                padding=int(payload.get("target_name_ocr_padding") or 4),
                frame_data_url=frame,
            )
            for slot in range(1, 4)
        ]
        targets: list[dict[str, Any]] = []
        for value in facts.get("targets") or []:
            item = dict(value)
            relation = classify_fanxiu_target_relation(
                is_npc=not bool(item.get("player")),
                server_id=item.get("server_id"),
            )
            item["camp"] = str(relation.get("camp") or "non_friendly")
            item["relation"] = str(relation.get("relation") or "")
            item["relation_label"] = str(relation.get("relation_label") or "未知关系")
            targets.append(item)
        mapped = map_xianyuan_duel_targets_to_slots(
            targets,
            ocr_names,
            minimum_pair_score=float(payload.get("target_name_min_similarity") or 0.35),
            minimum_assignment_margin=float(payload.get("target_name_min_margin") or 0.08),
        )
        if not mapped.get("ok"):
            raise RuntimeError(f"仙缘斗法：无法可靠映射候选姓名到 UI，{mapped.get('reason') or '匹配失败'}")
        return mapped

    def _read_daily_xianyuan_duel_remaining(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
    ):
        max_attempts = max(1, int(payload.get("remaining_ocr_attempts") or 6))
        padding = max(0, int(payload.get("remaining_ocr_padding") or 8))
        retry_seconds = max(0.2, float(payload.get("remaining_ocr_retry_seconds") or 1.0))
        max_remaining = max(0, int(payload.get("max_runs") or 7))
        last_text = ""
        for attempt in range(max_attempts):
            numbers, last_text = context.ocr_numbers_in_shapes(308, ("次数",), padding=padding, max_attempts=1)
            if not numbers:
                crop_numbers, crop_text = context.ocr_numbers_in_shapes(
                    308,
                    ("次数",),
                    padding=padding,
                    crop=True,
                    max_attempts=1,
                )
                if crop_numbers:
                    numbers, last_text = crop_numbers, crop_text
            if numbers and int(numbers[0]) >= 0:
                remaining = int(numbers[0])
                if remaining > max_remaining:
                    compact = re.sub(r"\s+", "", str(last_text or ""))
                    duplicated_digit = re.search(
                        r"剩余挑战次数[:：]?([0-9])\1\+?$",
                        compact,
                    )
                    if duplicated_digit and int(duplicated_digit.group(1)) <= max_remaining:
                        corrected = int(duplicated_digit.group(1))
                        self._log(
                            "warning",
                            "仙缘斗法：#308[次数] OCR 将加号误读成重复数字，"
                            f"{remaining}→{corrected}，OCR={last_text[:80]}",
                        )
                        remaining = corrected
                    else:
                        if attempt + 1 < max_attempts:
                            yield from context.wait_action_settle(0.8)
                            continue
                        raise RuntimeError(
                            "仙缘斗法：#308[次数] OCR 超出单周期安全上限，"
                            f"识别={remaining}、上限={max_remaining}、OCR={last_text[:120]}"
                        )
                self._log("detail", f"仙缘斗法：#308[次数] 剩余 {remaining}，OCR={last_text[:80]}")
                return remaining
            if attempt + 1 < max_attempts:
                yield from context.wait_action_settle(retry_seconds)
        raise RuntimeError(f"仙缘斗法：无法从 #308[次数] 识别剩余次数，最后 OCR={last_text[:120]}")

    def _open_daily_xianyuan_duel_purchase(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
        *,
        reason: str,
    ):
        attempts = max(1, int(payload.get("purchase_open_attempts") or 2))
        timeout = float(payload.get("purchase_open_timeout") or 12.0)
        last_scene: tuple[int | None, float, str] | None = None
        for attempt in range(1, attempts + 1):
            wait_error: TimeoutError | None = None
            try:
                landing = yield from context.wait_click_then_scene(
                    308,
                    "购买",
                    311,
                    timeout=timeout,
                    max_clicks=1,
                )
            except TimeoutError as exc:
                wait_error = exc
            else:
                if int(getattr(landing, "id", landing) or 0) == 311:
                    return

            # wait_click_then_scene may return a newly recognized non-target
            # scene instead of raising. Re-sample before deciding whether the
            # purchase sheet opened; never treat underlying #308 as #311.
            _wait_scene_match = yield from context.wait_scene([311, 308], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            last_scene = (scene_id, score, context.ocr_text(frame))
            if scene_id == 311:
                return
            if scene_id != 308:
                if wait_error is not None:
                    raise wait_error
                raise RuntimeError(
                    f"仙缘斗法：{reason}后购买入口落到未知场景 "
                    f"#{scene_id or 'unknown'} {score:.0f}%"
                )
            if attempt >= attempts:
                text = last_scene[2]
                raise RuntimeError(
                    f"仙缘斗法：{reason}后 #308 购买入口仍未打开，"
                    "无法证明当日 100/200 灵石档已购。必须进入 #311 并确认"
                    "下一档价格为 300 灵石后才能幂等继续；"
                    f"当前 #308 {score:.0f}%，OCR={text[:80]}"
                ) from wait_error
            self._log(
                "warning",
                f"仙缘斗法：{reason}后等待 #311 未命中，但新帧仍确认 #308；"
                f"稳定等待后重试购买入口 {attempt + 1}/{attempts}",
            )
            yield from context.wait_action_settle(
                float(payload.get("purchase_open_retry_settle_seconds") or 1.0)
            )

    def _prepare_daily_xianyuan_duel_purchases(self, context: BehaviorTreeContext, payload: dict[str, Any]):
        yield from self._open_daily_xianyuan_duel_purchase(
            context,
            payload,
            reason="首次检查购买档位",
        )
        max_attempts = int(payload.get("purchase_max_attempts") or 6)
        stop_price = max(
            0,
            int(
                payload.get("purchase_max_price")
                or payload.get("purchase_price_limit")
                or 300
            ),
        )
        expected_purchase_prices = {100, 200}
        last_purchased_price: int | None = None
        for _index in range(max_attempts):
            for _retry in range(3):
                numbers, text = context.ocr_numbers_in_shapes(311, ("价格",), padding=16)
                if numbers:
                    break
                yield from context.wait_action_settle(0.8)
            if not numbers:
                continue
            price = numbers[0]
            if last_purchased_price is not None and price <= last_purchased_price:
                raise RuntimeError(
                    "仙缘斗法：购买动作后价格未向下一档推进，"
                    f"上一档 {last_purchased_price}，当前 {price}，拒绝重放灵石购买"
                )
            if price == stop_price:
                yield from context.wait_click_then_scene(311, "返回", 308)
                return
            if price not in expected_purchase_prices:
                raise RuntimeError(
                    "仙缘斗法：购买页价格不属于 100/200 可购档或 "
                    f"{stop_price} 灵石幂等停止档，当前价格 {price}"
                )
            self._log("action", f"仙缘斗法：购买斗法次数，价格 {price}")
            yield from context.wait_click(311, "购买")
            last_purchased_price = int(price)
            yield from context.wait_action_settle(1.0)
            _wait_scene_match = yield from context.wait_scene([311, 308], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id == 308:
                text = context.ocr_text(frame)
                self._log(
                    "detail",
                    "仙缘斗法：购买后入口关闭并返回 #308，"
                    f"重新打开购买页确认下一档为 {stop_price} 灵石；"
                    f"当前 #308 {score:.0f}%，OCR={text[:80]}",
                )
                yield from self._open_daily_xianyuan_duel_purchase(
                    context,
                    payload,
                    reason="购买后重新确认下一档",
                )
                continue
            if scene_id != 311:
                raise RuntimeError(
                    "仙缘斗法：购买后未停留在 #311 或返回 #308，"
                    f"实际 #{scene_id or 'unknown'} {score:.0f}%"
                )
        raise RuntimeError(f"仙缘斗法：购买页价格识别失败或未达到停止价格，最后识别文本：{text if 'text' in locals() else ''}")

    def _optimize_daily_xianyuan_duel_formation(self, context: BehaviorTreeContext, payload: dict[str, Any]):
        if bool(payload.get("skip_formation_optimize")):
            return
        facts = payload.get("__xianyuan_duel_facts")
        target = payload.get("__xianyuan_duel_target")
        if not isinstance(facts, dict) or not isinstance(target, dict):
            raise RuntimeError("仙缘斗法：缺少本轮结构化敌我事实，拒绝使用图片猜测阵容")
        self_power = int(facts.get("self_power") or 0)
        target_power = int(target.get("team_power") or 0)
        skip_ratio = float(payload.get("formation_skip_power_ratio") or 2.0)
        if target_power <= 0 or self_power <= 0:
            raise RuntimeError("仙缘斗法：敌我仙侣总战力不完整，无法执行 2 倍战力规则")
        power_ratio = self_power / target_power
        if power_ratio >= skip_ratio:
            self._log(
                "action",
                "仙缘斗法：我方仙侣战力 "
                f"{self_power} 为「{target.get('name') or '目标'}」{target_power} 的 {power_ratio:.2f} 倍，"
                f"达到 {skip_ratio:g} 倍，跳过阵容调整直接挑战",
            )
            return

        self_team = facts.get("self_team") if isinstance(facts.get("self_team"), dict) else {}
        enemy_team = target.get("team") if isinstance(target.get("team"), dict) else {}
        if not self_team.get("formation_complete") or not enemy_team.get("formation_complete"):
            detailed = read_xianyuan_duel_runtime_snapshot(include_formations=True)
            if not detailed.get("available") or not detailed.get("complete"):
                raise RuntimeError(
                    "仙缘斗法：摘要判断需要换阵，但完整 Runtime 阵容读取失败，"
                    f"reason={detailed.get('reason') or 'unknown'}"
                )
            target_id = target.get("target_id")
            detailed_target = next(
                (
                    item
                    for item in detailed.get("targets") or []
                    if isinstance(item, dict)
                    and item.get("target_id") == target_id
                ),
                None,
            )
            if not isinstance(detailed_target, dict):
                raise RuntimeError(
                    "仙缘斗法：完整 Runtime 阵容已刷新，无法按 target_id 对齐当前挑战对象"
                )
            if (
                str(detailed_target.get("name") or "") != str(target.get("name") or "")
                or int(detailed_target.get("team_power") or 0) != target_power
            ):
                raise RuntimeError(
                    "仙缘斗法：完整 Runtime 阵容与选人摘要不一致，拒绝使用跨版本阵容"
                )
            facts = detailed
            target = detailed_target
            self_team = facts.get("self_team") if isinstance(facts.get("self_team"), dict) else {}
            enemy_team = target.get("team") if isinstance(target.get("team"), dict) else {}

        start_ts = time.monotonic()
        _wait_scene_match = yield from context.wait_scene([309], wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id != 309:
            raise RuntimeError(f"仙缘斗法：阵容优化要求当前为 #309，实际为 #{scene_id or 'unknown'} {score:.0f}%")
        if not self_team.get("formation_complete") or not enemy_team.get("formation_complete"):
            raise RuntimeError("仙缘斗法：敌我五个位置的结构化阵容不完整，拒绝退回 OCR 或图片相似匹配")
        my_partner_ids = [int(value) for value in self_team.get("partner_ids") or []]
        enemy_partner_ids = [int(value) for value in enemy_team.get("partner_ids") or []]
        try:
            best = best_xianyuan_partner_order(
                my_partner_ids,
                enemy_partner_ids,
                decay=float(payload.get("formation_decay") or 0.5),
            )
        except ValueError as exc:
            raise RuntimeError(f"仙缘斗法：结构化阵容无法计算，{exc}") from exc
        swaps = plan_swaps(my_partner_ids, best["partner_ids"])
        max_swaps = int(payload.get("formation_final_max_swaps") or 4)
        if len(swaps) > max_swaps:
            raise RuntimeError(f"仙缘斗法：结构化换位需要 {len(swaps)} 次，超过安全上限 {max_swaps}")
        settle_seconds = float(payload.get("formation_drag_settle_seconds") or 1.8)
        drag_duration = float(payload.get("formation_drag_duration_seconds") or 2.0)
        for start_slot, end_slot in swaps:
            context.drag_shape_to_shape(
                309,
                f"拖拽锚点{start_slot}",
                f"拖拽锚点{end_slot}",
                duration=drag_duration,
                frame_data_url=frame,
            )
            yield from context.wait_action_settle(settle_seconds)
        elapsed = time.monotonic() - start_ts
        my_labels = [XIANYUAN_CAREER_LABELS[int(value)] for value in best["careers"]]
        enemy_labels = [XIANYUAN_CAREER_LABELS[int(value)] for value in best["enemy_careers"]]
        self._log(
            "action",
            "仙缘斗法：按结构化阵容完成优化，"
            f"敌方={'/'.join(enemy_labels)}，我方={'/'.join(my_labels)}，"
            f"调整{len(swaps)}次，耗时{elapsed:.1f}s",
        )

    def _read_daily_xianyuan_duel_formation_state(self, context: BehaviorTreeContext) -> dict[str, Any]:
        from PIL import Image, ImageChops, ImageStat

        _wait_scene_match = yield from context.wait_scene([309], wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id != 309:
            raise RuntimeError(f"仙缘斗法：阵容优化要求当前为 #309，实际为 #{scene_id or 'unknown'} {score:.0f}%")
        image309 = context.view(309).raw
        entry = context.ctx.get("entry") if isinstance(context.ctx, dict) else None
        entry_id = str(getattr(entry, "entry_id", "") or "")
        filename = str(image309.get("filename") or "")
        if not entry_id or not filename:
            raise RuntimeError("仙缘斗法：缺少 #309 参考图，无法识别阵容")
        ref_path = data_annotation_entry_image_dir(entry_id) / filename
        ref_image = Image.open(ref_path).convert("RGB")
        cur_image = Image.open(io.BytesIO(context.runner._decode_frame_data_url(frame))).convert("RGB")

        def crop(pil_image: Any, shape: dict[str, Any], *, pad: int = 1):
            width, height = pil_image.size
            x = float(shape.get("x") or 0) * width
            y = float(shape.get("y") or 0) * height
            w = float(shape.get("w") or 0) * width
            h = float(shape.get("h") or 0) * height
            return pil_image.crop((
                max(0, int(round(x - pad))),
                max(0, int(round(y - pad))),
                min(width, int(round(x + w + pad))),
                min(height, int(round(y + h + pad))),
            ))

        def similarity(left: Any, right: Any) -> float:
            right = right.resize(left.size)
            stat = ImageStat.Stat(ImageChops.difference(left, right))
            rmse = math.sqrt(sum(value * value for value in stat.rms) / len(stat.rms))
            return max(0.0, 100.0 * (1.0 - rmse / 255.0))

        career_shapes: list[tuple[int, str, dict[str, Any]]] = []
        for shape in image309.get("shapes") or []:
            if str(shape.get("title") or "") != "我方职业":
                continue
            for child in shape.get("children") or []:
                parsed = parse_slot_value_title(str(child.get("title") or ""), "职业")
                if parsed:
                    career_shapes.append((parsed[0], parsed[1], child))
        career_shapes.sort(key=lambda item: item[0])
        state_shapes: list[tuple[int, int, dict[str, Any]]] = []
        for shape in image309.get("shapes") or []:
            parsed = parse_slot_value_title(str(shape.get("title") or ""), "克制")
            if parsed:
                state_shapes.append((parsed[0], int(parsed[1]), shape))
        state_shapes.sort(key=lambda item: item[0])
        if len(career_shapes) != 5 or len(state_shapes) != 5:
            raise RuntimeError("仙缘斗法：#309 缺少职业或克制三态标注")

        career_templates: dict[str, list[Any]] = {}
        for _slot, career, shape in career_shapes:
            career_templates.setdefault(career, []).append(crop(ref_image, shape))
        state_templates: dict[int, list[Any]] = {}
        for _slot, state, shape in state_shapes:
            state_templates.setdefault(state, []).append(crop(ref_image, shape, pad=0))

        my_order: list[str] = []
        for _slot, _career, shape in career_shapes:
            slot_crop = crop(cur_image, shape)
            scores = {
                career: max(similarity(slot_crop, template) for template in templates)
                for career, templates in career_templates.items()
            }
            my_order.append(max(scores, key=scores.get))
        states: list[int] = []
        for _slot, _state, shape in state_shapes:
            slot_crop = crop(cur_image, shape, pad=0)
            scores = {
                state: max(similarity(slot_crop, template) for template in templates)
                for state, templates in state_templates.items()
            }
            states.append(int(max(scores, key=scores.get)))
        return {"frame": frame, "my_order": my_order, "states": states}
