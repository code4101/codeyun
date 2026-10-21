from __future__ import annotations

"""周常_万仙伐劫：每周日 05:00 执行一次的活动闭环。

业务骨架（真实运行带跑确认）：
    #34 世界 → #69 日常任务块「挑战1次万仙伐劫」→ #748 万仙伐劫主页
    →「前往挑战」→ #85 战斗中（实测约 60~90 秒）→ #346 结算页 →「继续」→ #748
    →「任务」页签 → #749 任务页 → 领取置顶任务奖励 →「返回」→ #34

资产契约由本任务拥有（页面适配，不是通用领奖布局）：
    #69  任务块模板/标题：标题锚点别名「万仙」「伐劫」，次数=当前任务行进度
    #748 身份「万仙伐劫」；动作「前往挑战」「任务」「返回」
    #749 身份「任务」；动作「首条任务进度区」、观察「首条任务标题」、「返回」
    #346 共用结算页身份「继续」

三条必须保留的现场事实：
    1. 锚点不能用正则：``locate_text_box`` 只做精确子串，regex 命中后取不到锚点框，
       条目会被静默丢弃（实测「万仙|伐劫」返回 0 项）。标题 OCR 可能被富文本
       ``<size=64>万`` 的字号差切分，所以这里用别名逐个 ``contains`` 再合并去重。
    2. #749 已领取的行沉到列表尾部并显示「已完成」，顶部是可领取行；因此观察区取
       首行标题，不能照搬兽渊的第三行观察区（推进时第三行可能不变）。
    3. 点未达成/已领取行只弹「尚未满足完成条件」且不跳转，属于安全点击型，
       不需要 ``progress_shape`` 门禁。
"""

import re
import time
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.tasks.task_reward_rows import claim_task_rows_by_ocr


WEEKLY_WANXIAN_WEEKDAY = 6  # Python weekday：周日
WEEKLY_WANXIAN_TRIGGER_TIME = dt_time(5, 0)

WEEKLY_WANXIAN_WORLD_SCENE = 34
WEEKLY_WANXIAN_DAILY_SCENE = 69
WEEKLY_WANXIAN_HOME_SCENE = 748
WEEKLY_WANXIAN_TASK_SCENE = 749
WEEKLY_WANXIAN_SETTLE_SCENE = 346

# 任务块标题的 OCR 别名；逐个 contains 再合并，等价于正则「万仙|伐劫」但走受支持的模式。
WEEKLY_WANXIAN_BLOCK_ANCHORS = ("万仙", "伐劫")

_CHALLENGE_PROGRESS_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


def next_weekly_wanxian_trigger(now: datetime) -> datetime:
    """下一次周日 05:00；触发时间已到则顺延到下周。"""

    for day_offset in range(8):
        candidate_date = now.date() + timedelta(days=day_offset)
        if candidate_date.weekday() != WEEKLY_WANXIAN_WEEKDAY:
            continue
        candidate = datetime.combine(candidate_date, WEEKLY_WANXIAN_TRIGGER_TIME)
        if candidate > now:
            return candidate
    raise RuntimeError("无法计算周常_万仙下次触发时间")


def weekly_wanxian_challenge_finished(progress_text: str) -> bool | None:
    """读「次数」进度判定当前任务行进度是否已完成：True 已完成，None 表示读不清。"""

    normalized = re.sub(r"\s+", "", str(progress_text or ""))
    match = _CHALLENGE_PROGRESS_RE.search(normalized)
    if match is None:
        return None
    done, total = int(match.group(1)), int(match.group(2))
    if total <= 0:
        return None
    return done >= total


class WeeklyWanxianTaskMixin:
    """执行「周常_万仙」的完整闭环。"""

    def _record_weekly_wanxian_done(self, payload: dict[str, Any], *, now: datetime | None = None) -> str:
        next_time = next_weekly_wanxian_trigger(now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "weekly-wanxian"),
            next_time,
        )
        return next_time

    def _open_weekly_wanxian_block(
        self,
        context: Any,
        *,
        max_scrolls: int,
        transition_timeout: float,
    ):
        """在 #69 找到万仙伐劫任务块，返回「次数」文本并点击进入 #748。"""

        yield from context.go_scene(WEEKLY_WANXIAN_DAILY_SCENE)
        from backend.core.fanxiu.activity.wanxian_search import (
            wanxian_search_directions, wanxian_search_direction_finished,
            record_wanxian_search_evidence, wanxian_semantic_unchanged,
        )
        directions = wanxian_search_directions(context.shape(
            WEEKLY_WANXIAN_DAILY_SCENE, "滚动窗口").load_direction or "down")
        limit = max(0, min(30, int(max_scrolls)))
        deadline = time.monotonic() + 120.0
        observations: list[dict] = []
        frame_paths: list[str | None] = []

        def capture(frame, label):
            # Copy only frames already used by this search, never a fresh GUI read.
            try:
                from backend.core.fanxiu.data_annotation.scene_diagnostics import save_scene_diagnostic_frame
                frame_paths.append(save_scene_diagnostic_frame(self, frame,
                    kind="wanxian_search", label=label, expected_scene_ids=[69]))
            except Exception:
                frame_paths.append(None)

        def persist(outcome):
            try:
                path = record_wanxian_search_evidence(observations,
                    outcome=outcome, frame_paths=frame_paths)
                self._log("info", f"万仙任务块搜索诊断：{outcome}，原尝试证据={path}")
            except Exception as exc:
                try:
                    self._log("warning", f"万仙搜索诊断未持久化：{type(exc).__name__}")
                except Exception:
                    pass  # Diagnostics must not change admission or replay a click.

        # Reopening #69 does not prove a start cursor. A single direction cannot
        # cover rows above an inherited position. Search before every boundary
        # decision so even the final unchanged scroll's frame is inspected.
        for direction in directions:
            previous_changed = None
            previous_texts = None
            semantic_unchanged = 0
            # A noisy first direction cannot consume the opposite direction's
            # entire budget. A timeout remains unknown, never absence proof.
            direction_deadline = min(deadline, time.monotonic() + 60.0)
            for scroll_index in range(limit + 1):
                frame = context.cur_frame(update=True)
                visible_texts = [str(row.get('text') or '') for row in
                    context.ocr_fragments_in_shapes(WEEKLY_WANXIAN_DAILY_SCENE,
                        ['滚动窗口'], frame_data_url=frame, crop=True)
                    if str(row.get('text') or '').strip()]
                semantic_unchanged = wanxian_semantic_unchanged(
                    previous_texts, visible_texts, semantic_unchanged)
                items = []
                # Two aliases ("万仙"/"伐劫") of the same row return the same
                # floating item, so de-duplicate by the full item_box geometry
                # rather than the per-anchor box: shared rows differ only by the
                # anchor offset and would otherwise look like two distinct blocks.
                seen_items: set[tuple[float, float, float, float]] = set()
                for anchor in WEEKLY_WANXIAN_BLOCK_ANCHORS:
                    for item in context.find_floating_items_by_anchor_text(
                        WEEKLY_WANXIAN_DAILY_SCENE,
                        "任务块模板",
                        "标题",
                        anchor,
                        container_shape="滚动窗口",
                        frame_data_url=frame,
                        match_mode="contains",
                    ):
                        item_box = item.item_box if isinstance(item.item_box, dict) else {}
                        key = (
                            round(float(item_box.get("x") or 0.0), 1),
                            round(float(item_box.get("y") or 0.0), 1),
                            round(float(item_box.get("w") or 0.0), 1),
                            round(float(item_box.get("h") or 0.0), 1),
                        )
                        if key in seen_items:
                            continue
                        seen_items.add(key)
                        items.append(item)
                observations.append(dict(direction=direction, scroll_index=scroll_index,
                    previous_scroll_changed=previous_changed, observed_at=time.time(),
                    visible_texts=visible_texts, semantic_unchanged=semantic_unchanged,
                    matched_item_boxes=[item.item_box for item in items]))
                if len(observations) == 1:
                    capture(frame, 'initial')
                if len(items) > 1:
                    capture(frame, 'ambiguous')
                    persist('ambiguous')
                    raise RuntimeError("周常_万仙：#69 中万仙伐劫任务块不唯一，停止点击")
                if len(items) == 1:
                    item = items[0]
                    if not context.floating_item_is_fully_inside(item, "滚动窗口"):
                        capture(frame, 'clipped_item')
                        persist('clipped_item_unconfirmed')
                        raise RuntimeError("周常_万仙：#69 万仙伐劫任务块位于滚动窗口边缘，停止点击")
                    if not context.floating_item_field_is_inside(item, "标题", "滚动窗口"):
                        capture(frame, 'unsafe_field')
                        persist('unsafe_field_unconfirmed')
                        raise RuntimeError("周常_万仙：#69 万仙伐劫任务块的点击字段不在滚动窗口内，停止点击")
                    capture(frame, 'matched')
                    persist('matched_before_click')
                    progress = context.read_floating_item_field(item, "次数", frame_data_url=frame)
                    context.click_floating_item_field(item, "标题")
                    yield from context.wait_scene(
                        [WEEKLY_WANXIAN_HOME_SCENE],
                        wait=transition_timeout,
                        label="周常_万仙：等待活动主页 #748",
                    )
                    return progress
                if time.monotonic() >= deadline:
                    capture(frame, 'deadline')
                    persist('deadline_unconfirmed')
                    raise RuntimeError("周常_万仙：#69 双向搜索超时，入口仍未确认；不得记周常完成")
                if wanxian_search_direction_finished(scroll_index=scroll_index,
                        limit=limit, previous_scroll_changed=(
                            False if semantic_unchanged >= 2 else None)):
                    capture(frame, f'{direction}_boundary')
                    break
                if time.monotonic() >= direction_deadline:
                    capture(frame, f'{direction}_budget')
                    break
                if not visible_texts:
                    # A blank loading list cannot authorize a scroll or prove
                    # a boundary. Observe within the same finite budget.
                    previous_texts = None
                    yield from context.wait_action_settle(1.0)
                    continue
                previous_texts = visible_texts
                previous_changed = yield from context.scroll_shape_content(
                    WEEKLY_WANXIAN_DAILY_SCENE, "滚动窗口", direction=direction,
                    unchanged_confirmations=2,
                )
        persist('not_found_unconfirmed')
        raise RuntimeError("周常_万仙：滚动 #69 后仍未找到万仙伐劫任务块")

    def _run_weekly_wanxian_challenge(
        self,
        context: Any,
        *,
        challenge_wait_seconds: float,
        transition_timeout: float,
    ):
        """从 #748 参战，等共用结算页出现，点继续回到 #748。"""

        yield from context.wait_click(WEEKLY_WANXIAN_HOME_SCENE, "前往挑战")
        self._log(
            "action",
            f"周常_万仙：已点击前往挑战，最多等待 {challenge_wait_seconds:.0f} 秒结算页",
        )
        settled = yield from context.wait_scene(
            [WEEKLY_WANXIAN_SETTLE_SCENE],
            wait=challenge_wait_seconds,
            required=False,
            label="周常_万仙：等待结算页 #346",
        )
        if settled is None or int(getattr(settled, "scene_id", 0) or 0) != WEEKLY_WANXIAN_SETTLE_SCENE:
            raise TimeoutError(f"周常_万仙：{challenge_wait_seconds:.0f} 秒内未出现结算页 #346")
        yield from context.wait_click_then_scene(
            WEEKLY_WANXIAN_SETTLE_SCENE,
            "继续",
            (WEEKLY_WANXIAN_HOME_SCENE,),
            timeout=transition_timeout,
            label="周常_万仙：结算页继续并回到活动主页 #748",
        )

    def _claim_weekly_wanxian_task_rewards(
        self,
        context: Any,
        *,
        click_settle_seconds: float,
        no_change_confirmations: int,
        max_clicks: int,
        transition_timeout: float,
    ):
        """进 #749 领奖，再从 #749 返回世界 #34。"""

        yield from context.click_shape_center_then_scene(
            WEEKLY_WANXIAN_HOME_SCENE,
            "任务",
            WEEKLY_WANXIAN_TASK_SCENE,
            timeout=transition_timeout,
            label="周常_万仙：进入任务页 #749",
        )
        def observe_rewards(phase, ocr_result=None):
            # These are diagnostic observations, not authorization to claim
            # a native tier or a substitute for panel/cycle membership.
            from backend.core.fanxiu.activity.wanxian_reward_evidence import record_wanxian_reward_observation
            from backend.core.fanxiu.data_annotation.scene_diagnostics import save_scene_diagnostic_frame
            from backend.core.fanxiu.instrumentation.wanxian_task_binding import (
                read_wanxian_task_binding_candidates, read_wanxian_revenue_task_candidates,
            )
            runtime = {'available': False, 'reason': 'observation_failed'}
            frame_path = None
            try:
                runtime = {
                    'native_revenue_family_candidate': read_wanxian_revenue_task_candidates(),
                    'binding_candidate': read_wanxian_task_binding_candidates(),
                    'native_activity_identity_verified': False,
                }
            except Exception as exc:
                runtime['error_type'] = type(exc).__name__
                self._log('warning', f'万仙领奖原生观察缺失：{type(exc).__name__}')
            try:
                frame_path = save_scene_diagnostic_frame(self, context.cur_frame(update=True),
                    kind='wanxian_rewards', label=phase, expected_scene_ids=[749])
            except Exception as exc:
                self._log('warning', f'万仙领奖画面诊断缺失：{type(exc).__name__}')
            try:
                return record_wanxian_reward_observation(phase, runtime=runtime,
                    frame_path=frame_path, ocr_result=ocr_result)
            except Exception as exc:
                self._log('warning', f'万仙领奖诊断未落盘：{type(exc).__name__}')
                return None

        before_receipt = observe_rewards('before')
        result = yield from claim_task_rows_by_ocr(
            context,
            scene_id=WEEKLY_WANXIAN_TASK_SCENE,
            first_row_shape="首条任务进度区",
            observer_shape="首条任务标题",
            label="周常_万仙：任务奖励",
            click_settle_seconds=click_settle_seconds,
            no_change_confirmations=no_change_confirmations,
            max_clicks=max_clicks,
        )
        after_receipt = observe_rewards('after', result)
        result = dict(result, evidence_scope='ocr_title_transitions_only',
                      native_claimed_tier_count=None,
                      native_activity_identity_verified=False,
                      observation_receipts={'before': before_receipt, 'after': after_receipt})
        yield from context.click_shape_center_then_scene(
            WEEKLY_WANXIAN_TASK_SCENE,
            "返回",
            (WEEKLY_WANXIAN_WORLD_SCENE,),
            timeout=transition_timeout,
            label="周常_万仙：返回世界 #34",
        )
        return result

    def _execute_weekly_wanxian_task(
        self,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """整单幂等：从世界入口重跑，按当前次数进度决定是否参战，最后必定领奖并回世界。"""

        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少周常_万仙资产树路径，无法执行作业")

        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        transition_timeout = float(payload.get("transition_timeout_seconds") or 20.0)
        max_scrolls = max(0, int(payload.get("max_daily_scrolls") or 30))
        challenge_wait_seconds = max(60.0, float(payload.get("challenge_wait_seconds") or 180.0))
        click_settle_seconds = max(1.0, float(payload.get("click_settle_seconds") or 3.0))
        confirmations = max(1, int(payload.get("no_change_confirmations") or 3))
        max_clicks = max(confirmations, int(payload.get("max_claim_clicks") or 30))

        yield from context.go_scene(WEEKLY_WANXIAN_WORLD_SCENE)
        progress = yield from self._open_weekly_wanxian_block(
            context,
            max_scrolls=max_scrolls,
            transition_timeout=transition_timeout,
        )
        finished = weekly_wanxian_challenge_finished(progress)
        if finished is None:
            raise RuntimeError(f"周常_万仙：#69 次数进度 OCR 无法判定：{progress!r}")

        challenged = False
        if not finished:
            yield from self._run_weekly_wanxian_challenge(
                context,
                challenge_wait_seconds=challenge_wait_seconds,
                transition_timeout=transition_timeout,
            )
            challenged = True
        else:
            self._log("skip", f"周常_万仙：#69 次数进度为 {progress}，当前任务行进度已完成，只补领任务奖励")

        rewards = yield from self._claim_weekly_wanxian_task_rewards(
            context,
            click_settle_seconds=click_settle_seconds,
            no_change_confirmations=confirmations,
            max_clicks=max_clicks,
            transition_timeout=transition_timeout,
        )
        next_time = self._record_weekly_wanxian_done(payload)
        message = (
            f"周常_万仙：{'已参战' if challenged else '当前任务行进度已完成'}，"
            f"任务标题变化 {rewards.get('detected_advances', 0)} 次（原生领取档数未核验）"
            f"（{rewards.get('clicks', 0)} 次点击，{rewards.get('reason')}），已返回世界，下次 {next_time}"
        )
        self._log("success", message)
        return {
            "result": "success",
            "message": message,
            "current_scene": WEEKLY_WANXIAN_WORLD_SCENE,
            "daily_progress": progress,
            "challenged": challenged,
            "task_rewards": rewards,
            "next_time": next_time,
        }


__all__ = [
    "WEEKLY_WANXIAN_BLOCK_ANCHORS",
    "WEEKLY_WANXIAN_DAILY_SCENE",
    "WEEKLY_WANXIAN_HOME_SCENE",
    "WEEKLY_WANXIAN_SETTLE_SCENE",
    "WEEKLY_WANXIAN_TASK_SCENE",
    "WEEKLY_WANXIAN_TRIGGER_TIME",
    "WEEKLY_WANXIAN_WEEKDAY",
    "WEEKLY_WANXIAN_WORLD_SCENE",
    "WeeklyWanxianTaskMixin",
    "next_weekly_wanxian_trigger",
    "weekly_wanxian_challenge_finished",
]
