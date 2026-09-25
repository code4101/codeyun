"""洞天任务的执行流程；策略选择由 dongtian 领域能力提供。

通过执行器组合取得日志、调度和日常导航能力；本模块不反向导入执行器。
入口和完成判据在同一业务模块内，其他玩法无需理解这里的页面细节。
"""
from __future__ import annotations

import threading
import time
from datetime import time as time_cls
from pathlib import Path
from typing import (
    Any,
    Callable,
    Mapping,
)

from pyxllib.prog import BehaviorTreeStatus

from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_spatial import locate_text_box
from backend.core.fanxiu.runtime_gui import rank_ocr_name_matches
from backend.core.fanxiu.data_annotation.job_times import next_business_time


class DongtianTaskMixin:
    def _daily_dongtian_text_is_home(self, text: Any) -> bool:
        compact = _sanitize_ocr_text(text)
        return bool("洞天福地" in compact and ("我的编队" in compact or "收益" in compact or "联盟占领" in compact))

    def _wait_daily_dongtian_home(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        task_label: str,
        allow_claim_page: bool = False,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        timeout = float(payload.get("dongtian_home_timeout") or 25.0)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            scene_candidates = [284, 279] if allow_claim_page else [279]
            _wait_scene_match = yield from context.wait_scene(scene_candidates, wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, float(score), text
            if allow_claim_page and scene_id == 284:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{task_label}：已直接到收益领取页",
                        phase="daily_dongtian_claim",
                        current_scene=284,
                    )
                    self._log_locked("success", f"{task_label}：入口直接落到 #284 {score:.0f}%，跳过 #279「收益」")
                return 284
            if scene_id == 279 or self._daily_dongtian_text_is_home(text):
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{task_label}：已到洞天福地主页",
                        phase="daily_dongtian_home",
                        current_scene=279,
                    )
                    self._log_locked("success", f"{task_label}：识别 #279 {score:.0f}%，OCR={text[:120]}")
                return 279
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"{task_label}：等待 #279 洞天福地主页超时，最后 {scene_text} {last_score:.0f}% OCR={last_text[:180]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{task_label}：等待洞天福地主页，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_dongtian_wait_home",
                    current_scene=scene_id,
                )

    def _record_daily_dongtian_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = (
            next_business_time(("14:00",))
        )
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-dongtian"),
            next_time,
        )
        self._log("success", f"洞天_领取：{message}，下次 {next_time}")
        return next_time

    def _daily_dongtian_has_shape(self, ctx: dict[str, Any], scene_id: int, title: str) -> bool:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image = images.get(scene_id)
        if not isinstance(image, dict):
            return False
        return any(str(shape.get("title") or "") == title for shape in image.get("shapes") or [])

    def _claim_daily_dongtian_profit(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        task_label: str,
        start_scene_id: int | None = None,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if not isinstance(images.get(284), dict):
            raise RuntimeError(f"{task_label}：缺少 #284 收益领取页标注，无法把 #279「收益」后的下一步作为场景锚点")
        if not self._daily_dongtian_has_shape(ctx, 284, "领取"):
            raise RuntimeError(f"{task_label}：缺少 #284「领取」shape 标注，无法执行下一步领取")

        if start_scene_id is None:
            _wait_scene_match = yield from context.wait_scene([284, 279], wait=5.0, required=False)
            (start_scene_id, _score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
        claimed = start_scene_id == 284
        if start_scene_id == 279:
            outcome = yield from context.wait_click_then_any(
                279,
                "收益",
                {
                    "claim_page": context.scene_visible(284),
                    "no_reward": context.scene_visible(279),
                },
                label=f"{task_label}：点击 #279「收益」后识别领取页或无收益主页",
                settle_seconds=float(payload.get("dongtian_profit_settle_seconds") or 2.0),
            )
            claimed = outcome == "claim_page"
            if not claimed:
                self._log(
                    "success",
                    f"{task_label}：#279「收益」点击后仍为可靠洞天主页，当前没有待领取收益",
                )
        elif start_scene_id == 284:
            self._log("detail", f"{task_label}：当前已在 #284 收益领取页，直接领取，不重复点击 #279「收益」")
        else:
            raise RuntimeError(f"{task_label}：领取收益前应在 #279/#284，实际 #{start_scene_id if start_scene_id is not None else 'unknown'}")
        if claimed:
            yield from context.wait_click_then_scene(
                284,
                "领取",
                279,
                label=f"{task_label}：点击 #284「领取」后等待 #279 洞天主页",
                settle_seconds=max(2.0, float(payload.get("dongtian_claim_settle_seconds") or 2.0)),
            )
            _wait_scene_match = yield from context.wait_scene([279], wait=5.0, required=False)
            (scene_after, score_after, frame_after) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text_after = context.ocr_text(frame_after)
            self._log("success", f"{task_label}：已点击 #284「领取」并回到 #279，当前 #{scene_after if scene_after is not None else 'unknown'} {score_after:.0f}%，OCR={text_after[:160]}")
        return_landing = yield from context.wait_click_then_scene(
            279,
            "返回",
            34,
            20,
            label=f"{task_label}：点击 #279「返回」后等待 #34 世界或 #20 绿瓶",
            settle_seconds=float(payload.get("dongtian_return_settle_seconds") or 2.0),
        )
        if int(getattr(return_landing, "id", return_landing)) == 20:
            self._log(
                "detail",
                f"{task_label}：#279「返回」落到 #20，沿正式场景图继续返回世界",
            )
            result = context.go_scene(34)
            if hasattr(result, "send"):
                yield from result
        _wait_scene_match = yield from context.wait_scene([34], wait=5.0, required=False)
        (scene_return, score_return, frame_return) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_return != 34 or score_return < 90:
            raise RuntimeError(
                f"{task_label}：离开洞天后未到可靠 #34，当前 "
                f"#{scene_return if scene_return is not None else 'unknown'} {score_return:.0f}%"
            )
        text_return = context.ocr_text(frame_return)
        self._log("success", f"{task_label}：已从 #279 返回 #34，当前 #{scene_return} {score_return:.0f}%，OCR={text_return[:160]}")
        return claimed

    def daily_dongtian_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = dict(payload or {})
        decision = self._daily_window_admission(
            now=job_now(),
            trigger=time_cls(14, 0),
            cutoff=time_cls(22, 0),
            label="洞天_领取",
            window_text="14:00-22:00",
        )
        return self._persist_admission_decision(payload, decision)

    def _execute_daily_dongtian_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = {"max_scrolls": 24, **dict(payload or {})}
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_洞天福地资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if not isinstance(images.get(279), dict):
            raise RuntimeError("缺少 #279「洞天福地」标注，无法确认洞天主页")

        task_label = "洞天_领取"
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        # Reward-memory discovery can require a full LuaJIT heap scan when its
        # cached roots have drifted.  That snapshot is only an optional GUI
        # short-circuit for this claim job, so do not let it block the normal,
        # idempotent #279/#284 flow for minutes.  Explicit overrides remain
        # available to tests and callers that already hold a fresh snapshot.
        if isinstance(payload.get("__dongtian_runtime_snapshot_override"), dict):
            reward_snapshot = self._daily_dongtian_runtime_snapshot(payload)
        else:
            reward_snapshot = {}
            self._log(
                "detail",
                "洞天_领取：跳过可能触发全量内存扫描的收益预判，直接执行 GUI 幂等领取流程",
            )
        reward_available = reward_snapshot.get("reward_available")
        if reward_available is False and bool(reward_snapshot.get("complete")):
            self._record_daily_dongtian_done(
                payload,
                message="Runtime 已确认当前没有待领取的洞天收益",
            )
            with self._lock:
                self._set_status_locked(
                    "success",
                    "洞天_领取：Runtime 已确认当前没有待领取收益",
                    phase="daily_dongtian_done",
                )
            return "success"
        if reward_available is True and bool(reward_snapshot.get("complete")):
            self._log(
                "success",
                "洞天_领取：Runtime 已确认存在待领取收益，继续执行 GUI 领取动作",
            )
        else:
            self._log(
                "detail",
                "洞天_领取：Runtime 收益状态不可用，保留原 GUI 流程兜底",
            )
        _wait_scene_match = yield from context.wait_scene([284, 279, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id == 284:
            claimed = yield from self._claim_daily_dongtian_profit(ctx, stop_event, payload, task_label=task_label, start_scene_id=284)
            self._record_daily_dongtian_done(payload, message="已领取洞天福地收益" if claimed is not False else "当前没有待领取的洞天收益")
            with self._lock:
                self._set_status_locked("success", "洞天_领取：已领取洞天福地收益" if claimed is not False else "洞天_领取：当前没有待领取收益", phase="daily_dongtian_done", current_scene=279)
            return "success"
        if scene_id == 279 or self._daily_dongtian_text_is_home(text):
            claimed = yield from self._claim_daily_dongtian_profit(ctx, stop_event, payload, task_label=task_label, start_scene_id=279)
            self._record_daily_dongtian_done(payload, message="已领取洞天福地收益" if claimed is not False else "当前没有待领取的洞天收益")
            with self._lock:
                self._set_status_locked("success", "洞天_领取：已领取洞天福地收益" if claimed is not False else "洞天_领取：当前没有待领取收益", phase="daily_dongtian_done", current_scene=279)
            return "success"

        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                _wait_scene_match = yield from context.wait_scene([284, 279, 69, 34], wait=5.0, required=False)
                (scene_id, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
                if scene_id == 284:
                    claimed = yield from self._claim_daily_dongtian_profit(ctx, stop_event, payload, task_label=task_label, start_scene_id=284)
                    self._record_daily_dongtian_done(payload, message="已领取洞天福地收益" if claimed is not False else "当前没有待领取的洞天收益")
                    with self._lock:
                        self._set_status_locked("success", "洞天_领取：已领取洞天福地收益" if claimed is not False else "洞天_领取：当前没有待领取收益", phase="daily_dongtian_done", current_scene=279)
                    return "success"
                if scene_id == 279 or self._daily_dongtian_text_is_home(text):
                    claimed = yield from self._claim_daily_dongtian_profit(ctx, stop_event, payload, task_label=task_label, start_scene_id=279)
                    self._record_daily_dongtian_done(payload, message="已领取洞天福地收益" if claimed is not False else "当前没有待领取的洞天收益")
                    with self._lock:
                        self._set_status_locked("success", "洞天_领取：已领取洞天福地收益" if claimed is not False else "洞天_领取：当前没有待领取收益", phase="daily_dongtian_done", current_scene=279)
                    return "success"
            if scene_id != 69:
                scene_id = yield from self._enter_daily_from_world_like(
                    ctx,
                    context,
                    stop_event,
                    frame,
                    scene_id,
                    text,
                    label=task_label,
                )

        daily_status = yield from self._open_daily_entry_from_daily(
            ctx,
            stop_event,
            payload,
            task_label=task_label,
            title_pattern=r"收取\s*两?万\s*九|九曜\s*玄墨",
            progress_can_mark_done=False,
        )
        if daily_status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-dongtian",
                task_type="daily_dongtian",
                label=task_label,
                entry_label="收取两万九曜玄墨",
            )
            return "skipped"
        landing_scene_id = yield from self._wait_daily_dongtian_home(
            ctx,
            stop_event,
            payload,
            task_label=task_label,
            allow_claim_page=True,
        )
        claimed = yield from self._claim_daily_dongtian_profit(
            ctx,
            stop_event,
            payload,
            task_label=task_label,
            start_scene_id=int(landing_scene_id),
        )
        self._record_daily_dongtian_done(
            payload,
            message=(
                "已从日常进入洞天福地并领取收益"
                if claimed is not False
                else "已从日常进入洞天福地，当前没有待领取收益"
            ),
        )
        with self._lock:
            self._set_status_locked("success", "洞天_领取：已进入洞天福地并领取收益" if claimed is not False else "洞天_领取：已进入洞天福地，当前没有待领取收益", phase="daily_dongtian_done", current_scene=279)
        return "success"

    def daily_dongtian_clear_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = dict(payload or {})
        if bool(payload.get("ignore_schedule_window")):
            return None
        decision = self._daily_window_admission(
            now=job_now(),
            trigger=time_cls(21, 0),
            cutoff=time_cls(22, 0),
            label="洞天_行动力",
            window_text="21:00-22:00",
        )
        return self._persist_admission_decision(payload, decision)

    def _execute_daily_dongtian_clear_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """执行“洞天_行动力”的业务部分。

        完整工程作业由 ``run_task('daily_dongtian_clear')`` 组织成原子闭环：
        外层先用通用 ``go_scene(34)`` 归一到世界，本函数从 #34 进入日常和
        洞天并清理行动力；本函数返回 ``success`` 后，外层再用同一个通用
        ``go_scene(34)`` 按 ``#341 -> #279 -> #34`` 等真实落点动态收尾。
        因而这里不应复制一条洞天专用返回链。

        内层也允许从 #279 洞天主页或 #341 地点详情直接开始，供同一 Cell 内
        的连续流程和 AI 开发调试复用；这不代表工程 Scheduler 可以跨 Cell
        续接业务进度。新一轮正式作业仍必须从稳定起点 #34 整单执行。
        """
        payload = {"max_scrolls": 24, **dict(payload or {})}
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少洞天_行动力资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if not isinstance(images.get(279), dict):
            raise RuntimeError("洞天_行动力：缺少 #279「洞天福地」标注，无法确认洞天主页")

        task_label = "洞天_行动力"
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([341, 279, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id == 341:
            return (yield from self._daily_dongtian_clear_action_power_loop(context, stop_event, payload))
        if scene_id == 279 or self._daily_dongtian_text_is_home(text):
            return (yield from self._continue_daily_dongtian_clear_from_home(context, stop_event, payload))

        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                _wait_scene_match = yield from context.wait_scene([279, 69, 34], wait=5.0, required=False)
                (scene_id, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
                if scene_id == 279 or self._daily_dongtian_text_is_home(text):
                    return (yield from self._continue_daily_dongtian_clear_from_home(context, stop_event, payload))
            if scene_id != 69:
                scene_id = yield from self._enter_daily_from_world_like(
                    ctx,
                    context,
                    stop_event,
                    frame,
                    scene_id,
                    text,
                    label=task_label,
                )

        daily_status = yield from self._open_daily_entry_from_daily(
            ctx,
            stop_event,
            payload,
            task_label=task_label,
            title_pattern=r"收取\s*两?万\s*九|九曜\s*玄墨",
            progress_can_mark_done=False,
        )
        if daily_status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-dongtian-clear",
                task_type="daily_dongtian_clear",
                label=task_label,
                entry_label="收取两万九曜玄墨",
                daily_start_time=time_cls(21, 0),
                daily_end_time=time_cls(22, 0),
            )
            return "skipped"
        yield from self._wait_daily_dongtian_home(ctx, stop_event, payload, task_label=task_label)
        return (yield from self._continue_daily_dongtian_clear_from_home(context, stop_event, payload))

    def _continue_daily_dongtian_clear_from_home(
        self,
        context: Any,
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        return (yield from self._daily_dongtian_clear_action_power_loop(context, stop_event, payload))

    def _daily_dongtian_runtime_snapshot(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        override = payload.get("__dongtian_runtime_snapshot_override")
        if isinstance(override, dict):
            snapshot = dict(override)
        else:
            from backend.core.fanxiu.instrumentation.dongtian import (
                read_dongtian_clear_plan_snapshot,
            )

            snapshot = read_dongtian_clear_plan_snapshot()
        payload["__dongtian_runtime_snapshot"] = snapshot
        evidence = snapshot.get("evidence") if isinstance(snapshot.get("evidence"), dict) else {}
        self._log(
            "detail",
            "洞天_行动力：Runtime 清理计划快照 "
            f"available={bool(snapshot.get('available'))}，"
            f"elapsed={float(snapshot.get('elapsed_seconds') or 0):.3f}s，"
            f"mines_root_cache_hit={evidence.get('mines_root_cache_hit')}，"
            f"club_root_cache_hit={evidence.get('club_root_cache_hit')}，"
            f"phase_timings={evidence.get('phase_timings_seconds')}"
        )
        return snapshot

    def _daily_dongtian_action_power(
        self,
        context: Any,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, str]:
        """Read authoritative action power from game memory only."""

        _ = context
        if not isinstance(payload, dict):
            raise RuntimeError("洞天_行动力：缺少 Runtime payload，拒绝降级 OCR")
        initial_plan = payload.get("__dongtian_runtime_snapshot")
        initial_consumed = bool(payload.get("__dongtian_initial_action_power_consumed"))
        override = payload.get("__dongtian_runtime_snapshot_override")
        if isinstance(initial_plan, dict) and not initial_consumed:
            snapshot = initial_plan
            payload["__dongtian_initial_action_power_consumed"] = True
        elif isinstance(override, dict):
            snapshot = dict(override)
        else:
            from backend.core.fanxiu.instrumentation.dongtian import (
                read_dongtian_action_power_snapshot,
            )

            snapshot = read_dongtian_action_power_snapshot()
        payload["__dongtian_action_power_snapshot"] = snapshot
        evidence = snapshot.get("evidence") if isinstance(snapshot.get("evidence"), dict) else {}
        self._log(
            "detail",
            "洞天_行动力：Runtime 行动力快照 "
            f"available={bool(snapshot.get('available'))}，"
            f"elapsed={float(snapshot.get('elapsed_seconds') or 0):.3f}s，"
            f"mines_root_cache_hit={evidence.get('mines_root_cache_hit')}，"
            f"phase_timings={evidence.get('phase_timings_seconds')}",
        )
        runtime_action_power = snapshot.get("action_power")
        if (
            snapshot.get("available")
            and isinstance(runtime_action_power, int)
            and runtime_action_power >= 0
        ):
            return runtime_action_power, "runtime:XianLvMinesMgr.Model.Data.V_AttackFatigueValue"
        reason = str(snapshot.get("reason") or "V_AttackFatigueValue 缺失")
        raise RuntimeError(f"洞天_行动力：Runtime 行动力不可用，拒绝降级 OCR：{reason}")

    def _daily_dongtian_complete_from_runtime_if_proven(
        self,
        payload: dict[str, Any],
    ) -> str | None:
        """Short-circuit a retry when authoritative action power proves completion."""

        try:
            action_power, _source = self._daily_dongtian_action_power(None, payload)
        except RuntimeError:
            return None
        self._log(
            "detail",
            "洞天_行动力：启动前 Runtime "
            f"行动力={action_power}，来源='runtime:XianLvMinesMgr.Model.Data.V_AttackFatigueValue'",
        )
        if action_power >= 100:
            return None
        self._log("success", f"洞天_行动力：启动前已确认行动力低于 100（当前 {action_power}）")
        return self._complete_daily_clear_task(
            payload,
            task_id="legacy-daily-dongtian-clear",
            label="洞天_行动力",
        )

    def _daily_dongtian_clear_action_power_loop(
        self,
        context: Any,
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """持续执行“洞天挑战一次”，直到行动力不足 100。

        一轮挑战不以胜负作为完成条件：挑战失败通常消耗 100 点并回到 #341，
        挑战成功通常消耗 20 点并可能回到 #279。两种结果都达成“消耗行动力”
        的业务目标，因此每轮只重新识别真实落点和 HUD 行动力：

        - 落在 #341 且行动力仍不少于 100：直接挑战当前地点下一次；
        - 落在 #279 且行动力仍不少于 100：重新从最新 Runtime 快照选择敌对地点；
        - 任一场景识别到行动力小于 100：清理完成，返回 ``success``；
        - 其它落点、Runtime 字段缺失或超过安全轮数：失败并保留明确证据。

        ``max_action_power_rounds`` 只是防止识别异常导致无限循环的安全上限，
        不是业务次数；业务终止条件始终是行动力 ``< 100``。
        """
        rounds = 0
        max_rounds = max(1, int(payload.get("max_action_power_rounds") or 100))
        while rounds < max_rounds:
            _wait_scene_match = yield from context.wait_scene([341, 279], wait=5.0, required=False)
            (scene_id, score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id not in {341, 279}:
                raise RuntimeError(f"洞天_行动力：循环只接受 #341/#279，当前 #{scene_id} {score:.0f}%")

            # #279 needs the ownership plan.  Read it once before action power
            # so the same narrow snapshot supplies both the initial scalar and
            # the stable enemy-place list.  Later rounds read only the scalar.
            if scene_id == 279 and not isinstance(
                payload.get("__dongtian_runtime_snapshot"),
                dict,
            ):
                self._daily_dongtian_runtime_snapshot(payload)
            action_power, evidence = self._daily_dongtian_action_power(context, payload)
            self._log("detail", f"洞天_行动力：当前 #{scene_id} 行动力={action_power}，来源={evidence!r}")
            if action_power < 100:
                self._log("success", f"洞天_行动力：行动力已低于 100（当前 {action_power}），共挑战 {rounds} 次")
                return self._complete_daily_clear_task(
                    payload,
                    task_id="legacy-daily-dongtian-clear",
                    label="洞天_行动力",
                )

            if scene_id == 279:
                enemy_places = [str(item).strip() for item in payload.get("enemy_places") or [] if str(item).strip()]
                if not enemy_places:
                    enemy_places = self._daily_dongtian_enemy_places_from_runtime(payload)
                if not enemy_places:
                    raise RuntimeError("洞天_行动力：Runtime 未解析出敌对地点")
                clicked_place = yield from self._daily_dongtian_click_first_enemy_place(
                    context,
                    stop_event,
                    enemy_places,
                    max_scrolls=max(0, int(payload.get("place_max_scrolls") or 12)),
                )
                yield from self._daily_dongtian_validate_enemy_detail(context, clicked_place, payload)
                if bool(payload.get("pause_after_enemy_place_click")):
                    self._log("success", f"洞天_行动力：已点击敌对地点「{clicked_place}」，按调试参数暂停")
                    return "manual_check_pending"

            yield from self._daily_dongtian_continue_enemy_occupation(context)
            rounds += 1
            remaining, evidence = self._daily_dongtian_action_power(context, payload)
            if remaining >= action_power:
                raise RuntimeError(
                    f"洞天_行动力：占领流程返回但行动力未下降（{action_power}->{remaining}），保留现场"
                )
            if remaining < 100:
                self._log("success", f"洞天_行动力：Runtime 确认行动力 {action_power}->{remaining}，共挑战 {rounds} 次")
                return self._complete_daily_clear_task(
                    payload,
                    task_id="legacy-daily-dongtian-clear",
                    label="洞天_行动力",
                )

        raise RuntimeError(f"洞天_行动力：挑战达到安全上限 {max_rounds} 次，行动力仍未低于 100")

    def _daily_dongtian_enemy_places_from_runtime(
        self,
        payload: dict[str, Any],
    ) -> list[str]:
        snapshot = payload.get("__dongtian_runtime_snapshot")
        if not isinstance(snapshot, dict):
            snapshot = self._daily_dongtian_runtime_snapshot(payload)
        mines = snapshot.get("mines")
        own_union_id = int(snapshot.get("own_union_id") or 0)
        own_union_name = str(snapshot.get("own_union_name") or "").strip()
        if (
            not snapshot.get("available")
            or not snapshot.get("complete")
            or not isinstance(mines, list)
            or not mines
            or (own_union_id <= 0 and not own_union_name)
        ):
            reason = str(snapshot.get("reason") or "Runtime 洞天字段不完整")
            raise RuntimeError(f"洞天_行动力：Runtime 快照不可用，等待模型修复：{reason}")

        # Runtime already joins the sparse MinesPlace ID to its canonical
        # name. A screen-order index is not a mine ID (9 is followed by 19).
        enemies: list[str] = []
        union_summary: list[tuple[int, str, str]] = []
        for mine in mines:
            if not isinstance(mine, dict):
                continue
            mine_id = int(mine.get("id") or 0)
            union_id = int(mine.get("cross_union_id") or 0)
            union_name = str(mine.get("cross_union_name") or "").strip()
            place = str(mine.get("config_name") or mine.get("name") or "").strip()
            if not place:
                raise RuntimeError(f"洞天_行动力：地点 {mine_id} 缺少 Runtime 配置名称")
            union_summary.append((mine_id, place, union_name))
            if not place or (union_id <= 0 and not union_name):
                continue
            if own_union_id > 0 and union_id == own_union_id:
                continue
            if own_union_name and union_name == own_union_name:
                continue
            enemies.append(place)
        self._log(
            "detail",
            f"洞天_行动力：Runtime 解析敌对地点 {enemies}，"
            f"已解码 {len(mines)} 个，unions={union_summary}",
        )
        # Runtime 已按地图从上到下排序。只过滤归属，不按地点名称
        # 添加例外；点不进地点应修复入口定位，不能更改敌对目标优先级。
        return enemies

    def _daily_dongtian_validate_enemy_detail(
        self,
        context: Any,
        clicked_place: str,
        payload: dict[str, Any],
    ):
        expected_place = self._daily_dongtian_normalize_place_name(clicked_place)
        from backend.core.fanxiu.instrumentation.dongtian import read_dongtian_place_catalog
        if expected_place not in {
            self._daily_dongtian_normalize_place_name(item["name"])
            for item in read_dongtian_place_catalog()["places"]
            if item["special_mines"] == 0
        }:
            raise RuntimeError(f"洞天_行动力：Runtime 授权了未知地点 {clicked_place!r}")
        landing = yield from context.wait_scene(
            [341,
            342],
            label=f"洞天_行动力：核对地点「{clicked_place}」详情",
        )
        landed_scene = int(getattr(landing, "id", landing) or 341)
        if landed_scene == 342:
            # Some place cards expose the first position's occupation dialog
            # directly.  The #279 click was already bound to the exact OCR
            # title selected from the authoritative Runtime enemy list, and
            # #342 is the declared direct child of #341[位置1].
            self._log(
                "detail",
                f"洞天_行动力：地点「{expected_place}」直接进入 #342 占领弹窗，"
                "跳过重复的 #341[位置1] 点击",
            )
            return expected_place
        title_shape = context.shape(341, "地点名称")
        if title_shape is None:
            raise RuntimeError("洞天_行动力：缺少 #341「地点名称」区域，无法核对点击落点")

        frame = context.cur_frame(update=True)
        title_fragments = context.ocr_fragments_in_shapes(
            341,
            ["地点名称"],
            frame_data_url=frame,
        )
        observed_titles = [
            self._daily_dongtian_normalize_place_name(fragment.get("text"))
            for fragment in title_fragments
            if self._daily_dongtian_normalize_place_name(fragment.get("text"))
        ]
        ranked = rank_ocr_name_matches(expected_place, observed_titles)
        best = ranked[0] if ranked else None
        if best is not None and best.passed_threshold:
            self._log(
                "detail",
                f"洞天_行动力：#341 顶部地点标题已对齐"
                f"「{expected_place}」~「{best.observed}」({best.similarity:.2f})",
            )
            # Return the Runtime-authorized canonical name.  OCR is evidence
            # for the landing, not a second business identity; callers must
            # not fail again merely because the accepted glyph rendering or
            # whitespace differs from the canonical label.
            return expected_place

        observed = "、".join(observed_titles) or "<空>"
        self._log(
            "warning",
            f"洞天_行动力：点击后安全核验失败（顶部地点标题不一致），立即返回 #279；"
            f"expected={expected_place!r}, observed={observed!r}",
        )
        yield from context.wait_click_then_scene(341, "返回", 279)
        raise RuntimeError("洞天_行动力：敌方地点安全核验失败（顶部地点标题不一致），已返回洞天主页")

    def _daily_dongtian_continue_enemy_occupation(self, context: Any):
        _wait_scene_match = yield from context.wait_scene([341, 342], wait=5.0, required=False)
        (scene_id, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id == 341:
            yield from context.wait_click_then_scene(341, "位置1", 342)
        elif scene_id != 342:
            raise RuntimeError(
                f"洞天_行动力：占领流程要求 #341/#342，当前 #{scene_id or 'unknown'}"
            )
        yield from context.wait_click_then_scene(342, "占领", 343)
        yield from context.wait_click(343, "占领")
        yield from context.wait_action_settle(0.3)
        _wait_scene_match = yield from context.wait_scene([344, 343], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id == 343:
            # The transition can begin a few frames after the click.  One
            # bounded second sample distinguishes a delayed transition from
            # an inert button without requiring the transient battle frame to
            # have a stable scene identity.
            yield from context.wait_action_settle(0.8)
            _wait_scene_match = yield from context.wait_scene([344, 343], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
        if scene_id == 344:
            context.click_shape(344, "战斗", frame_data_url=frame)
            context.clear_frame()
        elif scene_id == 343:
            raise RuntimeError("洞天_行动力：点击 #343「占领」后仍停在队伍确认页")
        # Any other reliable observation means #343 has been left and the
        # direct battle transition is already in flight.  The battle finisher
        # owns the next stable business anchors.
        yield from self._daily_dongtian_finish_battle(context)

    def _daily_dongtian_finish_battle(
        self,
        context: Any,
        *,
        tick_seconds: float = 1.0,
        max_ticks: int = 180,
    ):
        """等待洞天战斗结束，并消费可选的跳过页和最终继续页。

        #345/#346 可能被直接返回主页的流程跳过。正常业务落点只结束等待，
        调用方必须以新鲜 Runtime 行动力下降证明本轮实际产生进展。
        """
        skip_clicked = False
        for _tick in range(max(1, int(max_ticks))):
            yield from context.wait_action_settle(max(0.1, float(tick_seconds)))
            _wait_scene_match = yield from context.wait_scene([345, 346, 341, 279], wait=5.0, required=False)
            (scene_id, _score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id in {341, 279}:
                return
            if scene_id == 345:
                if not skip_clicked:
                    yield from context.wait_click(345, "跳过")
                    skip_clicked = True
                continue
            if scene_id == 346:
                yield from context.wait_click(346, "继续")
                # 继续后通常回到当前地点详情 #341，也可能直接回洞天主页 #279；
                # 两者都表示本轮战斗闭环完成，不把其中一个误设成唯一成功终点。
                yield from context.wait_scene([341, 279], label="洞天_行动力：确认战斗后的正常落点")
                return
        raise RuntimeError("洞天_行动力：战斗等待超时，未出现最终场景 #346「继续」")

    def _daily_dongtian_click_first_enemy_place(
        self,
        context: Any,
        stop_event: threading.Event,
        enemy_places: list[str],
        *,
        max_scrolls: int,
    ):
        """从当前可见且入口可点击的敌对地点中，优先选最上方一个。"""
        if not enemy_places:
            raise RuntimeError("洞天_行动力：没有敌对地点可定位")
        # 全部敌对地点均参与当前帧匹配：如名单 abde、OCR 看见 bde，
        # 直接选 b。只有当前帧没有可安全点击的敌对地点时才滚动。
        return (yield from self._daily_dongtian_click_place(
            context,
            stop_event,
            enemy_places,
            max_scrolls=max_scrolls,
            scroll_directions=("down", "up"),
            task_label="洞天_行动力",
        ))

    def _daily_dongtian_click_place(
        self,
        context: Any,
        stop_event: threading.Event,
        place_names: list[str],
        *,
        max_scrolls: int,
        scroll_directions: tuple[str, ...] = ("down", "up"),
        task_label: str = "洞天_地点定位",
    ):
        """Locate one caller-selected #279 place from the scroll window.

        A search may start at any prior scroll offset.  It therefore walks to
        one boundary and, if necessary, reverses toward the other boundary.
        This low-level locator grants no seating authority.  OCR is used only
        for a canonical place identity (unique OCR edits also require current
        geometry support); #341 remains the independent
        post-click assertion performed by the caller.
        """
        from backend.core.fanxiu.data_annotation.dongtian_place_geometry import (
            dongtian_geometry_scroll_direction,
            estimate_dongtian_target_center,
            dongtian_label_positions,
        )
        from backend.core.fanxiu.instrumentation.dongtian import _mines_place_static_config

        view279 = context.view(279)
        window_shape = context.shape(279, "窗口")
        if window_shape is None:
            raise RuntimeError("洞天_行动力：缺少 #279「窗口」标注，无法查找敌对地点")
        window_box = window_shape.box()

        roster_shape = context.shape(279, "我的编队")
        if roster_shape is None:
            raise RuntimeError("洞天_行动力：缺少 #279「我的编队」禁点区标注，拒绝点击地点")
        roster_box = roster_shape.box()

        def point_in_box(x: float, y: float, box: dict[str, Any]) -> bool:
            left = float(box.get("x") or 0)
            top = float(box.get("y") or 0)
            return left <= x <= left + float(box.get("w") or 0) and top <= y <= top + float(box.get("h") or 0)

        normalized_targets = {
            self._daily_dongtian_normalize_place_name(item): item
            for item in place_names
            if self._daily_dongtian_normalize_place_name(item)
        }
        place_configs, _config_hash = _mines_place_static_config()
        place_positions = dongtian_label_positions(list(place_configs.values()))
        # MinesPlace is the single source of actual locations. The historical
        # OCR-anchor tuple omits legitimate sites such as 莲舟矶 and must not
        # reject a Runtime-selected mine before the shared locator can run.
        unknown = sorted(set(normalized_targets) - set(place_positions))
        if unknown:
            raise RuntimeError(f"{task_label}：调用方传入未知地点 {unknown}")

        directions = tuple(dict.fromkeys(scroll_directions))
        if not directions or any(item not in {"up", "down"} for item in directions):
            raise ValueError(f"洞天地点滚动方向非法：{scroll_directions!r}")

        last_geometry_y: float | None = None
        unchanged_geometry_steps = 0
        for direction_index, direction in enumerate(directions):
            for scroll_index in range(max_scrolls + 1):
                self._raise_if_stopped(stop_event)
                yield from context.wait_scene([279], label=f"{task_label}：等待 #279 洞天福地")
                frame = context.cur_frame(update=True)
                lines = context.ocr_fragments_in_shapes(279, ["窗口"], frame_data_url=frame)
                tokens = (
                    context.ocr_tokens_in_shapes(279, ["窗口"], frame_data_url=frame)
                    if hasattr(context, "ocr_tokens_in_shapes")
                    else []
                )
                matches: list[tuple[float, float, str, dict[str, Any], float, float, float, float]] = []
                geometry_lines = [line for line in lines if not point_in_box(
                    float(line.get("x") or 0) + float(line.get("w") or 0) / 2,
                    float(line.get("y") or 0) + float(line.get("h") or 0) / 2, roster_box)]
                for line in lines:
                    for normalized, original in normalized_targets.items():
                        location_box = self._daily_dongtian_location_box(line, tokens, normalized)
                        if location_box is None:
                            continue

                        location_center_x = float(location_box.get("x") or 0) + float(location_box.get("w") or 0) * 0.5
                        location_center_y = float(location_box.get("y") or 0) + float(location_box.get("h") or 0) * 0.5
                        if self._daily_dongtian_normalize_place_name(line.get("text")) != normalized:
                            predicted = estimate_dongtian_target_center(normalized, geometry_lines, place_positions, self._daily_dongtian_normalize_place_name)
                            if predicted is None or max(abs(predicted[0] - location_center_x), abs(predicted[1] - location_center_y)) > 35:
                                continue
                        click_point = self._daily_dongtian_location_click_point(location_box, window_box)
                        if click_point is None:
                            # 名称和它上方的地点入口都必须完整露在窗口内。
                            continue
                        click_x, click_y = click_point
                        if point_in_box(location_center_x, location_center_y, roster_box) or point_in_box(click_x, click_y, roster_box):
                            continue
                        # 排序依据实际地点名称框，而非可能合并其它文字的 OCR 行框。
                        matches.append((location_center_y, location_center_x, original, line, click_x, click_y, location_center_x, location_center_y))
                        break
                if matches:
                    _y, _x, place, line, click_x, click_y, location_center_x, location_center_y = min(matches, key=lambda item: (item[0], item[1]))
                    normalized_clicked = self._daily_dongtian_normalize_place_name(place)
                    if normalized_clicked not in normalized_targets or normalized_targets[normalized_clicked] != place:
                        raise RuntimeError(f"{task_label}：地点名称内部一致性校验失败：place={place!r}")
                    if click_x <= 0 or click_y <= 0:
                        raise RuntimeError(f"{task_label}：地点「{place}」 OCR 坐标无效，line={line}")
                    # OCR 名称只用于定位；实际入口位于名称上方，必须使用
                    # 定位函数已校验的落点，不能重新覆盖成文字中心。
                    click_candidates = [(click_x, click_y)]
                    successor_wait_seconds = float(
                        context.payload.get("place_click_successor_wait_seconds") or 20.0
                    )
                    for attempt, (candidate_x, candidate_y) in enumerate(click_candidates, start=1):
                        if point_in_box(candidate_x, candidate_y, roster_box):
                            continue
                        self._log(
                            "click",
                            f"{task_label}：调用方目标地点「{place}」，"
                            f"点击名称上方地点入口=({candidate_x:.0f},{candidate_y:.0f})"
                            f"，尝试 {attempt}/{len(click_candidates)}",
                        )
                        context.click_frame_point(279, candidate_x, candidate_y)
                        try:
                            landing = yield from context.wait_scene(
                                [341,
                                342],
                                wait=successor_wait_seconds,
                                label=f"{task_label}：等待地点「{place}」详情或占领弹窗",
                            )
                        except TimeoutError:
                            landing = None
                        landed_scene = int(getattr(landing, "id", landing) or 0)
                        if landed_scene in {341, 342}:
                            return place
                        _wait_scene_match = yield from context.wait_scene([279, 341, 342], wait=5.0, required=False)
                        (landed_scene, _score, _frame) = (
                            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                        )
                        if landed_scene in {341, 342}:
                            return place
                        if landed_scene != 279:
                            raise RuntimeError(
                                f"{task_label}：点击地点「{place}」后落点不可靠："
                                f"scene={landed_scene or 'unknown'}"
                            )
                        if attempt < len(click_candidates):
                            self._log(
                                "warning",
                                f"{task_label}：地点「{place}」等待 {successor_wait_seconds:.0f}s "
                                "后仍可靠停在 #279，"
                                "改用同一地点的备用热区重试",
                            )
                    raise RuntimeError(f"{task_label}：地点「{place}」点击重试后仍停在 #279")
                if scroll_index >= max_scrolls:
                    break
                # The fixed roster repeats occupied place names. Those are
                # NOT map anchors: mixing them into the fit invalidates its
                # residuals and previously degraded navigation to blind scans.
                geometry_lines = [
                    line for line in lines
                    if not point_in_box(
                        float(line.get("x") or 0) + float(line.get("w") or 0) / 2,
                        float(line.get("y") or 0) + float(line.get("h") or 0) / 2,
                        roster_box,
                    )
                ]
                estimates = [
                    estimate_dongtian_target_center(name, geometry_lines, place_positions, self._daily_dongtian_normalize_place_name)
                    for name in normalized_targets.values()
                ]
                estimates = [point for point in estimates if point is not None]
                scroll_direction = direction
                if len(estimates) == 1:
                    predicted_x, predicted_y = estimates[0]
                    if last_geometry_y is not None and abs(predicted_y - last_geometry_y) < 18:
                        unchanged_geometry_steps += 1
                    else:
                        unchanged_geometry_steps = 0
                    if unchanged_geometry_steps >= 2:
                        raise RuntimeError(f"{task_label}：地图连续滚动后目标几何位置未变化，停止盲滚")
                    guided = dongtian_geometry_scroll_direction(estimates[0], window_box, roster_box)
                    scroll_direction = guided or direction
                    if unchanged_geometry_steps == 1:
                        scroll_direction = "up" if scroll_direction == "down" else "down"
                    last_geometry_y = predicted_y
                    self._log(
                        "detail",
                        f"{task_label}：当前帧几何推断目标=({predicted_x:.0f},{predicted_y:.0f})，"
                        f"OCR 未安全露出；只用于调整滚动，不授权点击",
                    )
                direction_text = "向下" if scroll_direction == "down" else "向上"
                self._log("action", f"{task_label}：当前窗口未安全识别目标，{direction_text}小幅滚动 {scroll_index + 1}/{max_scrolls}")
                # 统一使用框架默认滚动手势（ratio 0.5、duration 1.5s）；几何推断只
                # 用于选择方向，不参与换算滚动步长。
                changed = yield from context.scroll_shape_content(view279, window_shape, direction=scroll_direction)
                if not changed:
                    break
            if direction_index + 1 < len(directions):
                self._log("detail", f"{task_label}：已到{direction}方向边界，反向继续查找")
        raise RuntimeError(f"{task_label}：#279 窗口未找到地点，candidates={place_names}")

    def _daily_dongtian_click_seating_target(
        self,
        context: Any,
        stop_event: threading.Event,
        authorization: Mapping[str, Any],
        *,
        max_scrolls: int,
        probe_reader: Callable[..., Mapping[str, Any]] | None = None,
    ):
        """Click one friendly seating place only after a fresh Runtime gate."""

        from backend.core.fanxiu.data_annotation.dongtian_seating_click import (
            validate_dongtian_seating_place_authorization,
        )
        from backend.core.fanxiu.instrumentation.dongtian import (
            read_dongtian_seating_probe,
        )

        if not isinstance(authorization, Mapping):
            raise RuntimeError("洞天_座位研究：裸地点名不能作为 Runtime 上座授权")
        excluded_mine_ids = {
            int(item)
            for item in authorization.get("excluded_mine_ids") or []
            if not isinstance(item, bool) and str(item).isdigit() and int(item) > 0
        }
        reader = probe_reader or read_dongtian_seating_probe
        fresh_probe = reader(excluded_mine_ids=excluded_mine_ids)
        gate = validate_dongtian_seating_place_authorization(
            authorization,
            fresh_probe,
        )
        if not gate.get("ok"):
            raise RuntimeError(
                "洞天_座位研究：点击前 Runtime 授权失败，"
                f"reason={gate.get('reason') or 'unknown'}"
            )
        self._log(
            "detail",
            "洞天_座位研究：fresh Runtime 已授权友军地点"
            f" mine_id={gate['mine_id']} place={gate['place_name']!r}",
        )
        return (yield from self._daily_dongtian_click_place(
            context,
            stop_event,
            [str(gate["place_name"])],
            max_scrolls=max_scrolls,
            scroll_directions=("down", "up"),
            task_label="洞天_座位研究",
        ))

    @staticmethod
    def _daily_dongtian_normalize_place_name(value: Any) -> str:
        from backend.core.fanxiu.data_annotation.dongtian_place_geometry import normalize_dongtian_place_name
        return normalize_dongtian_place_name(_sanitize_ocr_text(value))

    @staticmethod
    def _daily_dongtian_location_click_point(
        location_box: Mapping[str, Any],
        window_box: Mapping[str, Any],
    ) -> tuple[float, float] | None:
        """名称是 OCR 锚点；地点入口为 (x + w/2, y - 2h)。

        名称完整可见且上方入口位于地图窗口内才返回落点。入口被顶部
        遮挡时返回 None，让调用方滚动露出，不能退回点击名称。
        """

        x = float(location_box.get("x") or 0)
        y = float(location_box.get("y") or 0)
        width = float(location_box.get("w") or 0)
        height = float(location_box.get("h") or 0)
        if width <= 0 or height <= 0:
            return None
        click_x = x + width * 0.5
        click_y = y - height * 2
        left = float(window_box.get("x") or 0)
        top = float(window_box.get("y") or 0)
        right = left + float(window_box.get("w") or 0)
        bottom = top + float(window_box.get("h") or 0)
        if not (left <= x and x + width <= right
                and top <= click_y < y and y + height <= bottom):
            return None
        return click_x, click_y

    def _daily_dongtian_location_box(
        self,
        line: dict[str, Any],
        tokens: list[dict[str, Any]],
        target: str,
    ) -> dict[str, float] | None:
        """Resolve one place inside one authoritative Paddle line.

        The line decides object identity. Linked word boxes only refine a
        substring within that same line; tokens from neighboring UI objects are
        never concatenated or searched together.
        """

        compact_location = self._daily_dongtian_normalize_place_name(line.get("text"))
        compact_target = self._daily_dongtian_normalize_place_name(target)
        if not compact_location or not compact_target:
            return None

        if compact_location != compact_target:
            from backend.core.fanxiu.data_annotation.dongtian_place_geometry import resolve_dongtian_ocr_name
            from backend.core.fanxiu.instrumentation.dongtian import read_dongtian_place_catalog
            names = [self._daily_dongtian_normalize_place_name(p["name"]) for p in read_dongtian_place_catalog()["places"]]
            if resolve_dongtian_ocr_name(compact_location, names) != compact_target:
                return None
        matched_text = compact_location

        line_id = line.get("line_id")
        line_tokens = [token for token in tokens if line_id is not None and token.get("parent_line_id") == line_id]
        token_box = locate_text_box(line_tokens, matched_text)
        if token_box is not None:
            return token_box

        # Without linked tokens only an exact/partial standalone native line is
        # safe. A line containing suffix/prefix text cannot be proportionally
        # sliced because that would recreate the discarded legacy heuristic.
        if compact_location == matched_text:
            return {
                "x": float(line.get("x") or 0),
                "y": float(line.get("y") or 0),
                "w": float(line.get("w") or 0),
                "h": float(line.get("h") or 0),
            }
        return None
