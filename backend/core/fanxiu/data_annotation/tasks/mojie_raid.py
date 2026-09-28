"""奇袭魔界：准入、队伍、进攻、周结算与复核。

执行器提供通用场景操作；本模块拥有业务完成判据和后续调度。
"""
from __future__ import annotations
from backend.core.fanxiu.data_annotation.effective_time import job_now
import re
import threading
from datetime import datetime, time as time_cls, timedelta
from pathlib import Path
from typing import Any
from pyxllib.autogui import Shape
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.ocr_values import FULLWIDTH_DIGIT_TRANSLATION


class MojieRaidTaskMixin:
    def daily_mojie_raid_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Skip the weekly closed interval; Monday's first run is at 13:00."""
        now = job_now()
        if now.weekday() == 0 and now.time() < time_cls(13, 0):
            return self._persist_admission_decision(dict(payload or {}), {
                "result": "success",
                "message": "日常_奇袭魔界：周一首次运行时间为 13:00，未执行游戏操作",
                "next_time": now.replace(hour=13, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S"),
                "current_scene": None,
            })
        if now.weekday() != 6 or now.time() < time_cls(22, 0):
            return None
        return self._persist_admission_decision(dict(payload or {}), {
            "result": "success",
            "message": "日常_奇袭魔界：周日 22:00 窗口已关闭，直接顺延下周一，未执行游戏操作",
            "next_time": self._next_mojie_raid_week_start_time_text(now),
            "current_scene": None,
        })

    def _execute_daily_mojie_raid_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})

        def terminal(message: str) -> str:
            set_completion_message = getattr(context, "set_completion_message", None)
            if callable(set_completion_message):
                set_completion_message(str(message))
            return "success"

        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_奇袭魔界资产树路径，无法执行作业")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        raid_scenes = {319, 320, 321, 322, 323, 324}
        settlement_only = self._mojie_raid_settlement_only()
        joined_existing_team = False
        _wait_scene_match = yield from context.wait_scene([331, 330, *sorted(raid_scenes), 69, 34, 20], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id == 331:
            if settlement_only:
                next_time = self._schedule_mojie_raid_settled_week(payload)
                yield from context.go_scene(34)
                return terminal(f"周日自动挑战已结束，已加入页面收尾完成；下次 {next_time}")
            # #331 is the already-joined team page.  Whether it was reached after
            # a committed #324 transaction or after replaying #320 against an
            # already-changed game state, the current trigger must not attack
            # again.  Persist the next legal trigger before low-risk cleanup and
            # report success: the business effect for this trigger already exists.
            next_time = self._schedule_next_mojie_raid_trigger(
                payload,
                reason="起点已处于 #331「已加入」状态，本轮业务已完成",
            )
            yield from context.go_scene(34)
            return terminal(f"#331 已确认入队，本轮幂等完成；下次 {next_time}")
        if scene_id == 330:
            scene_id = yield from self._confirm_daily_mojie_raid_reward_confirmation(context)
        if scene_id not in {69, *raid_scenes}:
            scene_id = yield from self._enter_daily_from_world_like(ctx, context, stop_event, frame, scene_id, text, label="日常_奇袭魔界")
        if scene_id not in {69, *raid_scenes}:
            raise RuntimeError("日常_奇袭魔界：未能进入 #69 日常列表")
        if scene_id == 69:
            debug_payload = payload.get("debug") if isinstance(payload.get("debug"), dict) else {}
            stop_after_daily_entry = bool(
                payload.get("stop_after_daily_entry")
                or payload.get("pause_after_daily_entry")
                or debug_payload.get("stop_after_daily_entry")
                or debug_payload.get("pause_after_daily_entry")
            )
            status = yield from context.open_daily_entry(
                label="日常_奇袭魔界",
                title_pattern=r"参与.{0,4}奇|奇.{0,4}魔|魔界",
                progress_can_mark_done=False,
                max_scrolls=int(payload.get("max_scrolls") or 30),
            )
            if status == "not_found":
                if settlement_only:
                    next_time = self._schedule_mojie_raid_settled_week(payload)
                    return terminal(f"周日自动挑战已结束，日常入口已消失；下次 {next_time}")
                self._record_daily_entry_not_found_retry(
                    payload,
                    task_id="legacy-daily-mojie-raid",
                    task_type="daily_mojie_raid",
                    label="日常_奇袭魔界",
                    entry_label="魔界",
                )
                return "skipped"
            if status == "done":
                raise RuntimeError("日常_奇袭魔界：入口行完成态不能作为奇袭魔界完成判据")
            if stop_after_daily_entry:
                yield from context.wait_action_settle(float(payload.get("entry_pause_settle_seconds") or 1.5))
                _wait_scene_match = yield from context.wait_scene(wait=5.0, required=False)
                (current_scene_id, score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
                scene_label = f"#{current_scene_id}" if current_scene_id is not None else "unknown"
                self._log(
                    "warning",
                    f"日常_奇袭魔界：已点击日常入口，按调试要求暂停；当前 {scene_label} {score:.0f}%，OCR={text[:120]}",
                )
                return "skipped"
            try:
                waited = yield from context.wait_scene([319, 330], label="日常_奇袭魔界：等待奇袭魔界 #319")
                if getattr(waited, "id", waited) == 330:
                    scene_id = yield from self._confirm_daily_mojie_raid_reward_confirmation(context)
                else:
                    scene_id = 319
            except TimeoutError as exc:
                yield from self._handle_daily_mojie_raid_open_blocker_placeholder(context, payload)
                raise RuntimeError("日常_奇袭魔界：入口点击后未到达 #319，疑似遇到未实现的特殊弹窗") from exc
        if settlement_only:
            # 周日 13:00 是最后报名机会，21:30 自动挑战；此后仅收尾，
            # 即使页面仍显示剩余次数，也不能重新提交参与进攻。
            next_time = self._schedule_mojie_raid_settled_week(payload)
            yield from context.go_scene(34)
            return terminal(f"周日自动挑战已结束，已完成页面收尾；下次 {next_time}")
        if scene_id == 319:
            self._log("success", "日常_奇袭魔界：已到达 #319")
            shape_matches = getattr(context, "shape_matches", None)
            numbers, text = context.ocr_numbers_in_shapes(
                319,
                ("剩余次数",),
                padding=int(payload.get("mojie_raid_remaining_padding") or 16),
                max_attempts=1,
            )
            if not numbers and "确定" in str(text or ""):
                self._log(
                    "info",
                    "日常_奇袭魔界：底层 #319 被前置「确定」浮层覆盖；"
                    "先完成既有 #330 确认闭环，再重新检查「我的队伍」",
                )
                scene_id = yield from self._confirm_daily_mojie_raid_reward_confirmation(context)
                if scene_id == 319:
                    numbers, text = context.ocr_numbers_in_shapes(
                        319,
                        ("剩余次数",),
                        padding=int(payload.get("mojie_raid_remaining_padding") or 16),
                        max_attempts=1,
                    )
            remaining, text = self.read_daily_mojie_raid_remaining(
                context, payload, initial_ocr=(numbers, text),
            )
            self._log("detail", f"日常_奇袭魔界：剩余次数 {remaining}，OCR={text[:80]}")
            if remaining <= 0:
                # 单帧 OCR 的 0 会直接把整个作业推进到下周，代价过高。
                # 等待后重新取一张真实帧确认；若第二帧恢复为正数，继续本周流程。
                confirm_seconds = float(payload.get("mojie_raid_zero_confirm_seconds") or 2.0)
                self._log("warning", "日常_奇袭魔界：首次读到剩余次数 0，等待新帧复核后再结束本周")
                yield from context.wait_action_settle(confirm_seconds)
                context.clear_frame()
                remaining, confirm_text = self.read_daily_mojie_raid_remaining(
                    context, payload,
                )
                self._log("detail", f"日常_奇袭魔界：剩余次数复核 {remaining}，OCR={confirm_text[:80]}")
            if remaining <= 0:
                confirmed_at = job_now()
                if not self._mojie_raid_completion_window_open(confirmed_at):
                    next_time = self._schedule_mojie_raid_thursday_verification(
                        payload,
                        reason="两次有效新帧确认剩余次数为 0，但尚未到周四，不能判定本周完成",
                        now=confirmed_at,
                    )
                    message = f"两次有效新帧确认剩余次数为 0，但未到周四；复核时间 {next_time}"
                else:
                    next_time = self._schedule_next_mojie_raid_week(
                        payload,
                        reason="周四起两次有效新帧确认剩余次数为 0，本周已完成",
                        confirmed_remaining=remaining,
                        confirmed_at=confirmed_at,
                    )
                    message = f"周四起两次有效新帧确认剩余次数为 0，本周完成；下次 {next_time}"
                yield from context.wait_click(319, "返回")
                return terminal(message)
            existing_team_match = shape_matches(319, "队伍") if callable(shape_matches) else None
            if existing_team_match:
                # 剩余次数大于 0 时，「我的队伍」OCR 才是本轮已配置的幂等事实。
                next_time = self._schedule_next_mojie_raid_trigger(
                    payload,
                    reason="#319 已显示「我的队伍」，本轮业务已完成",
                )
                yield from context.wait_click_then_scene(319, "返回", 34)
                return terminal(f"#319 OCR 已确认「我的队伍」，本轮幂等完成；下次 {next_time}")
            yield from context.wait_click_then_scene(319, "参与进攻", 320)
            scene_id = 320
        else:
            self._log("detail", f"日常_奇袭魔界：从 #{scene_id} 恢复后续流程")
        if scene_id == 320:
            # 「进攻倒计时」只限制战斗结算，不限制提前选择据点和配置队伍。
            # 本轮幂等事实仍然必须来自 #319「队伍」OCR，或建队/入队后到达
            # #324/#331；不能因为倒计时大于 0 就把 Job 延后并冒充完成。
            countdown_text = context.ocr_text_in_shapes(
                320,
                ("进攻倒计时标识",),
                padding=int(payload.get("mojie_raid_attack_countdown_padding") or 12),
            )
            countdown_seconds = self._daily_mojie_raid_attack_countdown_seconds(countdown_text)
            if countdown_seconds is not None and countdown_seconds > 0:
                self._log(
                    "detail",
                    "日常_奇袭魔界：#320 仍有进攻倒计时，继续进入据点配置队伍，"
                    f"OCR={countdown_text[:120]}",
                )
            scene_id = yield from self._click_daily_mojie_raid_top_attack_target(context, payload)
            if scene_id == 331:
                # A previous/current join can make the #320 target click land
                # directly on the already-joined page, skipping #321..#324.
                # This direct transition is authoritative transaction-local
                # evidence.  Commit scheduling before cleanup so a navigation
                # failure cannot make the non-replayable action due again.
                next_time = self._schedule_next_mojie_raid_trigger(
                    payload,
                    reason="点击据点后已处于 #331「已加入」状态，本轮业务已完成",
                )
                yield from context.click_shape_center_then_scene(331, "返回", 320)
                yield from context.wait_click(320, "返回")
                yield from context.wait_click_then_scene(319, "返回", 34)
                return terminal(f"点击据点后 #331 已确认入队，本轮幂等完成；下次 {next_time}")
        if scene_id == 321:
            # #322 is only a confirmation popup, but a persistent #321 can mean
            # the server-side weekly attack allowance is already exhausted or
            # another business guard rejected the request.  Re-clicking cannot
            # distinguish those states and may duplicate a delayed UI action.
            yield from context.wait_click_then_scene(321, "创建队伍", 322, max_clicks=1)
            scene_id = 322
        if scene_id == 322:
            # 业务语义：奇袭魔界的建队额度由整个同盟共享，并非每个玩家各有
            # 3 个名额；每个触发周期同盟最多只能建立 3 支队伍。作业触发较晚
            # 时，盟友可能已经把额度用完，因此 #322 显示 3/3 是“本周期同盟
            # 建队名额已满”，不是「确定」按钮失效，也不应靠重复点击恢复。
            # 此时不能再创建队伍，但仍可加入本联盟已经创建且未满员的队伍。
            # 队伍卡片的位置、队长名和人数都会变化；#321 的“加入”状态只会
            # 出现在本联盟且可加入的 roleItem 上，因此用它定位动态卡片，再按
            # 资产模板记录的相对位移点击真正绑定事件的整个人物卡片。
            team_numbers, team_text = context.ocr_numbers_in_shapes(
                322,
                ("队伍额度",),
                padding=int(payload.get("mojie_raid_team_count_padding") or 12),
            )
            team_count: int | None = None
            team_limit: int | None = None
            if len(team_numbers) >= 2:
                team_count, team_limit = int(team_numbers[0]), int(team_numbers[1])
            else:
                fraction = parse_ocr_values(team_text, expected_count=2, allow_extra_numbers=True)
                if fraction is not None:
                    team_count, team_limit = fraction
            self._log(
                "detail",
                f"日常_奇袭魔界：#322 队伍数 {team_count if team_count is not None else '?'}"
                f"/{team_limit if team_limit is not None else '?'}，OCR={str(team_text or '')[:80]}",
            )
            if team_count is not None and team_limit is not None and team_limit > 0 and team_count >= team_limit:
                self._log(
                    "action",
                    f"日常_奇袭魔界：#322 建队额度已满 {team_count}/{team_limit}，返回 #321 加入友方队伍",
                )
                yield from context.wait_click_then_scene(322, "返回", 321)
                joined_scene = yield from self._join_daily_mojie_raid_friendly_team(context, payload)
                if joined_scene is None:
                    yield from context.click_shape_center_then_scene(321, "返回", 320)
                    yield from context.wait_click(320, "返回")
                    yield from context.wait_click_then_scene(319, "返回", 34)
                    next_time = self._schedule_next_mojie_raid_trigger(
                        payload,
                        reason=f"#322 建队额度已满 {team_count}/{team_limit}，#321 暂无可加入友方队伍",
                    )
                    return terminal(
                        f"建队额度已满 {team_count}/{team_limit} 且暂无可加入队伍；下次 {next_time}"
                    )
                scene_id = joined_scene
                joined_existing_team = True
            else:
                yield from context.wait_click_then_scene(322, "下拉选项", 323)
                scene_id = 323
        if scene_id == 323:
            yield from context.wait_click_then_scene(323, "开启", 322)
            # 创建队伍的“确定”输入历史上经常不生效；#322 仍被可靠识别时，
            # 继续补点同一已标注按钮。重试必须有界，且一旦离开 #322 或
            # 进入 unknown，wait_click_then_scene 会立即停止，不能盲目连点。
            yield from context.wait_click_then_scene(
                322,
                "确定",
                324,
                timeout=float(payload.get("mojie_raid_confirm_wait_timeout") or 8),
                max_clicks=int(payload.get("mojie_raid_confirm_max_clicks") or 6),
            )
            scene_id = 324
        if scene_id == 324:
            # #324 is the authoritative commit evidence: the join/create action
            # has succeeded and the player is on the team page.  Persist the
            # next legal trigger before any cleanup.  A later return/navigation
            # failure must never make this non-replayable transaction due again.
            committed_reason = (
                "已确认加入友方队伍并进入 #324"
                if joined_existing_team
                else "已确认建队成功并进入 #324"
            )
            next_time = self._schedule_next_mojie_raid_trigger(payload, reason=committed_reason)
            terminal_message = f"{committed_reason}，本轮幂等完成；下次 {next_time}"
            # 建队成功后的队伍页会在短暂动画结束后让 #324 的「鼓舞」
            # 图像身份失效；此时再用 wait_click 会永远等不到源场景。
            # 「返回」本身是固定标注坐标，直接点击，并兼容实机可能跳到
            # 中间页 #331 或直接回到世界 #34 两种落点。
            landed = yield from context.click_shape_center_then_scene(324, "返回", 331, 34)
            scene_id = int(getattr(landed, "id", landed))
            if scene_id == 34:
                return terminal(terminal_message)
        if scene_id == 331:
            yield from context.click_shape_center_then_scene(331, "返回", 320)
            yield from context.wait_click(320, "返回")
            yield from context.wait_click_then_scene(319, "返回", 34)
        if "terminal_message" not in locals():
            raise RuntimeError(f"奇袭魔界未获得建队或入队完成证据，实际场景 #{scene_id}")
        return terminal(terminal_message)

    def _daily_mojie_raid_join_click_delta(
        self,
        context: BehaviorTreeContext,
    ) -> tuple[float, float]:
        """Read the roleItem click offset from #321 assets instead of hard-coding pixels."""

        anchor = context.shape(321, "友方加入锚点")
        click_target = context.shape(321, "友方人物点击点")
        view = context.view(321)
        width, height = context.runner._frame_size(view.raw)

        def center(shape: Shape) -> tuple[float, float]:
            raw = shape.raw
            return (
                (float(raw.get("x") or 0) + float(raw.get("w") or 0) / 2) * width,
                (float(raw.get("y") or 0) + float(raw.get("h") or 0) / 2) * height,
            )

        anchor_x, anchor_y = center(anchor)
        click_x, click_y = center(click_target)
        return click_x - anchor_x, click_y - anchor_y

    def _join_daily_mojie_raid_friendly_team(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
    ):
        """Join one visible friendly corps, horizontally loading #321 when needed.

        Static client logic (`UnionDemonBossTeamItem`) proves that “加入” is
        rendered only for our cross-union/club when the player has no team and
        the team is not full.  The text itself has no click handler: the handler
        belongs to `roleItem`, so the click point is derived from the two #321
        template shapes and translated to each current OCR anchor.
        """

        max_scrolls = max(0, int(payload.get("mojie_raid_join_max_scrolls") or 8))
        max_candidates = max(1, int(payload.get("mojie_raid_join_max_candidates") or 6))
        settle_seconds = float(payload.get("mojie_raid_join_scroll_settle_seconds") or 2.0)
        dx, dy = self._daily_mojie_raid_join_click_delta(context)
        attempted = 0
        seen_pages: set[tuple[str, ...]] = set()

        for scroll_index in range(max_scrolls + 1):
            frame = context.cur_frame(update=True)
            # Restrict both candidate discovery and page identity to the
            # annotated horizontal team window.  A tuple of only ``3/5`` /
            # ``5/5`` counters is not a page identity: different pages often
            # have the same member-count distribution and would stop scanning
            # too early.
            fragments = context.ocr_fragments_in_shapes(
                321,
                ("队伍窗口",),
                frame_data_url=frame,
                padding=0,
            )
            markers = [
                item
                for item in fragments
                if re.search(r"加[入人]", re.sub(r"\s+", "", str(item.get("text") or "")))
            ]
            markers.sort(key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0)))
            page_key = tuple(
                sorted(
                    re.sub(r"\s+", "", str(item.get("text") or ""))
                    for item in fragments
                    if re.sub(r"\s+", "", str(item.get("text") or ""))
                )
            )
            self._log(
                "detail",
                f"日常_奇袭魔界：#321 横向页 {scroll_index + 1} 发现 {len(markers)} 个友方加入标识，人数键={page_key}",
            )

            for marker in markers:
                if attempted >= max_candidates:
                    break
                x = float(marker.get("x") or 0) + float(marker.get("w") or 0) / 2 + dx
                y = float(marker.get("y") or 0) + float(marker.get("h") or 0) / 2 + dy
                attempted += 1
                self._log(
                    "action",
                    f"日常_奇袭魔界：按第 {attempted} 个“加入”锚点定位并点击友方人物卡片",
                )
                context.click_frame_point(321, x, y)
                try:
                    # #508 is the formally annotated "是否加入该队伍" business
                    # scene.  It must be the Layer-0 target of this transition;
                    # a one-shot full-frame OCR probe can miss the animated popup
                    # and used to leave it open while the task kept scrolling #321.
                    yield from context.wait_scene(
                        [508],
                        wait=float(payload.get("mojie_raid_join_popup_timeout_seconds") or 8.0),
                        label="日常_奇袭魔界：等待加入队伍确认 #508",
                    )
                except RuntimeError:
                    self._log(
                        "warning",
                        "日常_奇袭魔界：人物卡片点击后未识别到 #508 入队确认，尝试下一候选",
                    )
                    continue

                context.click_shape_center(508, "确认")
                try:
                    yield from context.wait_scene(
                        [324],
                        wait=float(payload.get("mojie_raid_join_result_timeout_seconds") or 12.0),
                        label="日常_奇袭魔界：确认加入后等待队伍页 #324",
                    )
                except RuntimeError as exc:
                    raise RuntimeError("日常_奇袭魔界：确认加入后未识别到队伍页 #324") from exc
                self._log("success", "日常_奇袭魔界：已加入友方队伍并进入队伍页 #324")
                return 324

            if attempted >= max_candidates or scroll_index >= max_scrolls:
                break
            if page_key and page_key in seen_pages:
                self._log("detail", "日常_奇袭魔界：#321 横向内容已重复，停止继续加载")
                break
            if page_key:
                seen_pages.add(page_key)
            changed = yield from context.scroll_shape_content(
                321,
                "队伍窗口",
                direction="right",
                ratio=float(payload.get("mojie_raid_join_scroll_ratio") or 0.5),
                duration=float(payload.get("mojie_raid_join_scroll_duration") or 1.2),
                settle_seconds=settle_seconds,
                stable_sample_count=1,
                unchanged_confirmations=2,
            )
            if not changed:
                break
        return None

    def _click_daily_mojie_raid_top_attack_target(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
    ):
        target_shape = str(payload.get("mojie_raid_target_shape") or "检索区域/修罗").strip()
        match_timeout = float(
            payload.get("mojie_raid_target_match_timeout")
            or context.default_wait_click_timeout
        )
        wait_timeout = float(
            payload.get("mojie_raid_target_wait_timeout")
            or context.default_wait_condition_timeout
        )
        settle_seconds = float(payload.get("mojie_raid_target_click_settle_seconds") or 1.5)
        max_clicks = max(1, int(payload.get("mojie_raid_target_max_clicks") or 2))
        click_x_ratio = float(payload.get("mojie_raid_target_click_x_ratio") or 1.21875)
        click_y_ratio = float(payload.get("mojie_raid_target_click_y_ratio") or (5.0 / 3.0))
        last_error: TimeoutError | None = None

        for attempt in range(1, max_clicks + 1):
            self._log(
                "action",
                f"日常_奇袭魔界：定位并点击 #320「{target_shape}」 {attempt}/{max_clicks}",
            )
            yield from context.wait_click(
                320,
                target_shape,
                timeout=match_timeout,
                x_ratio=click_x_ratio,
                y_ratio=click_y_ratio,
            )
            yield from context.wait_action_settle(settle_seconds)
            try:
                waited = yield from context.wait_scene(
                    [321,
                    331],
                    wait=wait_timeout,
                    label="日常_奇袭魔界：点击 #320 修罗据点后等待 #321/#331",
                )
                landed = int(getattr(waited, "id", waited) or 0)
                if landed in (321, 331):
                    return landed
                # wait_scene 可返回全局识别的源页面，它不是业务落点成功。
                raise TimeoutError(f"点击修罗据点后未进入 #321/#331，实际 #{landed}")
            except TimeoutError as exc:
                last_error = exc
                _wait_scene_match = yield from context.wait_scene([320, 321, 331], wait=5.0, required=False)
                (scene_id, score, _frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                if scene_id in (321, 331):
                    return scene_id
                if scene_id != 320 or attempt >= max_clicks:
                    raise
                self._log(
                    "warning",
                    f"日常_奇袭魔界：点击修罗据点后仍在 #320 {score:.0f}%，重新定位后重试",
                )

        raise last_error or TimeoutError("日常_奇袭魔界：点击 #320 修罗据点后未进入 #321")

    def read_daily_mojie_raid_remaining(
        self, context: Any, payload: dict[str, Any] | None = None, *, initial_ocr=None,
    ):
        """次数语义锚定留在业务；无数值时换帧重试由通用 OCR 接口负责。"""
        payload = payload or {}

        def parse_remaining(text):
            anchored = self._daily_mojie_raid_remaining_ocr_fallback(text)
            values = parse_ocr_values(text)
            if anchored is not None:
                return anchored
            return values[0] if values else None

        if initial_ocr is not None:
            _numbers, initial_text = initial_ocr
            remaining = parse_remaining(initial_text)
            if remaining is not None:
                return remaining, initial_text
        remaining, text = context.ocr_value_in_shapes(
            319, ("剩余次数",), parse_value=parse_remaining,
            padding=int(payload.get("mojie_raid_remaining_padding") or 16),
            max_attempts=max(1, int(payload.get("mojie_raid_remaining_ocr_attempts") or 5)),
            retry_interval=max(0.1, float(payload.get("mojie_raid_remaining_ocr_interval") or 2.0)),
        )
        if remaining is None:
            raise RuntimeError(f"日常_奇袭魔界：多次 OCR 未能读取 #319「剩余次数」，最后 OCR={text[:120]}")
        return remaining, text

    def _daily_mojie_raid_remaining_ocr_fallback(self, text: str) -> int | None:
        normalized = str(text or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
        match = re.search(
            r"(?:本周)?剩余(?:进攻)?次数\s*[:：]?\s*([0-9]+|[BO])(?:\D|$)",
            normalized,
            re.IGNORECASE,
        )
        if not match:
            return None
        token = str(match.group(1) or "").upper()
        if token == "B":
            return 8
        if token == "O":
            return 0
        return int(token)

    def _daily_mojie_raid_attack_countdown_seconds(self, text: str) -> int | None:
        """Parse the #320 pre-attack countdown without treating it as a click failure."""

        normalized = str(text or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
        matches = re.findall(
            r"进攻倒计时\s*[:：]?\s*(-?)\s*([0-9]{1,3})\s*[:：]\s*([0-9]{1,2})\s*[:：]\s*([0-9]{1,2})",
            normalized,
        )
        if len(matches) != 1:
            return None
        sign, hours_text, minutes_text, seconds_text = matches[0]
        hours, minutes, seconds = (
            int(hours_text),
            int(minutes_text),
            int(seconds_text),
        )
        if minutes >= 60 or seconds >= 60:
            return None
        if sign == "-":
            # The game keeps counting below zero after the attack window opens.
            # A valid negative countdown therefore means "open now", not an
            # unreadable timer and not a future delay.
            return 0
        return hours * 3600 + minutes * 60 + seconds

    def _next_mojie_raid_week_start_time_text(
        self,
        now: datetime | None = None,
    ) -> str:
        now = now or job_now()
        days_until_next_monday = (7 - now.weekday()) % 7
        if days_until_next_monday == 0:
            days_until_next_monday = 7
        next_monday = now + timedelta(days=days_until_next_monday)
        return next_monday.replace(
            hour=13,
            minute=0,
            second=0,
            microsecond=0,
        ).strftime("%Y-%m-%d %H:%M:%S")

    def _mojie_raid_completion_window_open(
        self,
        now: datetime | None = None,
    ) -> bool:
        """Only Thursday and later may close the current raid week."""

        current = now or job_now()
        return current.weekday() >= 3

    def _next_mojie_raid_thursday_verification_time_text(
        self,
        now: datetime | None = None,
    ) -> str:
        """Return this week's Thursday 10:00 verification boundary."""

        current = now or job_now()
        days_until_thursday = 3 - current.weekday()
        if days_until_thursday <= 0:
            raise ValueError("奇袭魔界周四复核时间只适用于周一至周三")
        return (current + timedelta(days=days_until_thursday)).replace(
            hour=10,
            minute=0,
            second=0,
            microsecond=0,
        ).strftime("%Y-%m-%d %H:%M:%S")

    def _next_mojie_raid_followup_time_text(
        self,
        now: datetime | None = None,
    ) -> str:
        """Check at 13:00 and midnight, skipping Monday midnight."""

        current = now or job_now()
        if self._mojie_raid_settlement_only(current):
            return self._next_mojie_raid_week_start_time_text(current)
        candidate = current.replace(hour=13, minute=0, second=0, microsecond=0)
        if candidate > current:
            return candidate.strftime("%Y-%m-%d %H:%M:%S")
        if current.weekday() == 6:
            return self._next_mojie_raid_week_start_time_text(current)
        return (
            current + timedelta(days=1)
        ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        ).strftime("%Y-%m-%d %H:%M:%S")

    def _mojie_raid_settlement_only(self, now: datetime | None = None) -> bool:
        """Sunday 21:30 automatic battle ends the week's registration cycle."""
        current = now or job_now()
        return current.weekday() == 6 and current.time() >= time_cls(21, 30)

    def _schedule_mojie_raid_settled_week(self, payload: dict[str, Any]) -> str:
        """Close by the weekly settlement boundary, without fabricating zero attempts."""
        if not self._mojie_raid_settlement_only():
            raise ValueError("奇袭魔界日历收尾仅适用于周日 21:30 后")
        next_time = self._next_mojie_raid_week_start_time_text()
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-mojie-raid"),
            next_time,
        )
        self._log("success", f"日常_奇袭魔界：周日自动挑战已结束，下次 {next_time}")
        return next_time

    def _schedule_next_mojie_raid_week(
        self,
        payload: dict[str, Any],
        *,
        reason: str,
        confirmed_remaining: int,
        confirmed_at: datetime | None = None,
    ) -> str:
        current = confirmed_at or job_now()
        if int(confirmed_remaining) != 0:
            raise ValueError("奇袭魔界只有明确确认剩余次数为 0 才能推进到下周")
        if not self._mojie_raid_completion_window_open(current):
            raise ValueError("奇袭魔界只有周四起才能判定本周完成")
        next_time = self._next_mojie_raid_week_start_time_text(current)
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-mojie-raid"),
            next_time,
        )
        self._log("success", f"日常_奇袭魔界：{reason}，下次 {next_time}")
        return next_time

    def _schedule_mojie_raid_thursday_verification(
        self,
        payload: dict[str, Any],
        *,
        reason: str,
        now: datetime | None = None,
    ) -> str:
        next_time = self._next_mojie_raid_thursday_verification_time_text(now)
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-mojie-raid"),
            next_time,
        )
        self._log("success", f"日常_奇袭魔界：{reason}，本周周四复核 {next_time}")
        return next_time

    def _schedule_next_mojie_raid_trigger(
        self,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> str:
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-mojie-raid")
        next_time = self._next_mojie_raid_followup_time_text()
        self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
        self._log("success", f"日常_奇袭魔界：{reason}，本周仍需继续，下次 {next_time}")
        return next_time

    def _handle_daily_mojie_raid_open_blocker_placeholder(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
    ):
        del context, payload
        self._log("warning", "日常_奇袭魔界：特殊弹窗处理占位，等待后续补标/补流程")
        if False:
            yield None
        return "not_implemented"

    def _confirm_daily_mojie_raid_reward_confirmation(
        self,
        context: BehaviorTreeContext,
    ):
        self._log("action", "日常_奇袭魔界：检测到 #330 前置奖励确认，点击「确定」后继续等待 #319")
        waited = yield from context.wait_click_then_scene(
            330,
            "确定",
            [319],
            settle_seconds=1.5,
            timeout=20.0,
        )
        scene_id = getattr(waited, "id", waited)
        if scene_id != 319:
            raise RuntimeError(
                "日常_奇袭魔界：#330 点击「确定」后未确认到 #319，"
                f"实际 #{scene_id if scene_id is not None else 'unknown'}"
            )
        return scene_id
