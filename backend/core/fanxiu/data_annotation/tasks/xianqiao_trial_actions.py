"""仙窍试炼的观察、配置与挑战流程。

公共动作保留原有 generator 契约；输入、终态和实测资产约束见各方法。
本模块拥有玩法策略与流程，通用 BehaviorTreeContext 只组合这些能力，
不再在识别/点击/导航实现之间夹入试炼业务。这里不持有调度运行权，
不自行派发作业；正式 Task 决定准入与 next_time。
"""
from __future__ import annotations

import re
import time
from typing import Any, Literal
from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens
from backend.core.fanxiu.data_annotation.trial_difficulty import (
    TRIAL_DIFFICULTY_AXES,
    TRIAL_DIFFICULTY_BASE_LEVEL,
    TRIAL_DIFFICULTY_MIN_LEVEL,
    ObservedTrialDifficulty,
    build_even_trial_difficulty_plan,
    find_current_trial_difficulty,
    next_configurable_trial_difficulty,
)
from backend.core.fanxiu.data_annotation.trial_purchase import (
    XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES,
    normalize_xianqiao_trial_purchase_target,
    purchases_completed_before_price,
)
from backend.core.fanxiu.data_annotation.trial_progression import (
    ObservedTrialAttempts,
    ObservedTrialHomeState,
    parse_xianqiao_trial_attempts,
)
from backend.core.fanxiu.data_annotation.trial_strategy import choose_xianqiao_trial_sweep_track
from pyxllib.autogui import Shape, View


XIANQIAO_TRIAL_TRACK_SHAPES = {
    "higher": "切换至较高级试炼",
    "lower": "切换至较低级试炼",
}

XIANQIAO_TRIAL_TRACK_ENEMIES = {
    "higher": "黑凤王",
    "lower": "血光",
}

XIANQIAO_TRIAL_KNOWN_ENEMIES = (
    "血光",
    "黑凤王",
    "黑枭王",
    "天雷圣尊",
    "九幽古魔",
    "死苦魇妖",
)

def normalize_xianqiao_trial_track(value: Any) -> Literal["higher", "lower"]:
    """Normalize the public two-track trial selector without leaking UI labels."""

    normalized = str(value or "").strip().lower()
    aliases = {
        "higher": "higher",
        "high": "higher",
        "a": "higher",
        "较高级": "higher",
        "高级": "higher",
        "lower": "lower",
        "low": "lower",
        "b": "lower",
        "较低级": "lower",
        "低级": "lower",
    }
    track = aliases.get(normalized)
    if track is None:
        raise ValueError(f"未知仙窍试炼线路：{value!r}")
    return track


class XianqiaoTrialActions:
    """仙窍试炼能力；由执行上下文提供观察、交互和中断接口。"""

    def read_current_trial_difficulty(
        self,
        view: View | int | str,
        *,
        frame_data_url: str | None = None,
    ) -> ObservedTrialDifficulty:
        """从 #358 当前真实帧读取“当前难度为 N 级”。

        该函数只建立本轮 ``当前+1`` 的难度起点，不推断任何滑杆值。每根
        滑杆仍必须由 ``set_slider_value`` 读取自己的标题百分比并复核。
        """

        self.view(view)  # Fail early when the requested asset is unavailable.
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=True)
        cached_ocr = self.runner._shared_spatial_ocr_result(self.ctx, frame)
        tokens = cached_ocr.get("tokens") if isinstance(cached_ocr.get("tokens"), list) else []
        observation = find_current_trial_difficulty(group_ocr_tokens(tokens))
        if observation is None:
            # The saved #358 failure frame visibly contains the complete
            # label, while the shared full-frame OCR omitted it. Re-read only
            # the existing formal「当前难度」Shape; do not broaden the OCR
            # region or infer a level from slider positions.
            bounded_lines = self.ocr_fragments_in_shapes(
                view,
                ("当前难度",),
                padding=12,
                frame_data_url=frame,
                crop=True,
            )
            bounded_text = self.runner._ocr_text(bounded_lines)
            observation = find_current_trial_difficulty([{"text": bounded_text}])
        if observation is None:
            raise RuntimeError("未从当前画面读取到“当前难度为 N 级”")
        return observation

    def wait_current_trial_difficulty(
        self,
        view: View | int | str = 358,
        *,
        timeout_seconds: float = 30.0,
        settle_seconds: float = 0.6,
    ):
        """Wait for a visible level through queued battle-result banners.

        Six cached/fast OCR attempts can finish while the same banner still
        covers the label. Bound wall-clock time instead, taking a fresh frame
        for each observation; never infer the hidden level from slider state.
        """

        last_error: RuntimeError | None = None
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            try:
                return self.read_current_trial_difficulty(view)
            except RuntimeError as exc:
                last_error = exc
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                yield from self.wait_action_settle(min(max(0.2, settle_seconds), remaining))
        raise RuntimeError(
            f"等待 {timeout_seconds:g} 秒仍未读到仙窍试炼当前难度"
        ) from last_error

    def configure_even_trial_difficulty(
        self,
        view: View | int | str,
        target_level: int,
        *,
        scroll_shape: Shape | str = "难度窗口",
        track_shape: Shape | str = "伤害降低",
        title_anchor_shape: Shape | str = "难度条",
        max_scrolls_per_axis: int = 3,
        settle_seconds: float = 0.8,
    ):
        """按均匀模型配置五根难度滑杆并复核最终显示等级。

        函数职责仅是业务编排：计算计划、按标题滚动到可见行、逐根调用
        ``set_slider_value``，最后确认界面显示的当前难度。单根滑杆的坐标
        换算、拖拽和百分比校准不在这里重复实现。

        #358 当前资产中两个历史 shape 名称与视觉语义相反：``伤害降低``
        标的是第一根滑轨，``难度条`` 标的是第一行标题区域。本函数通过参数
        显式保留这个事实，若以后重命名标注，只需调整调用参数。

        :param int target_level: 最终目标等级，例如 26。
        :return dict: 纯业务计划、五根滑杆结果和最终显示等级。
        """

        target_view = self.view(view)
        plan = build_even_trial_difficulty_plan(target_level)
        max_scrolls_per_axis = max(0, int(max_scrolls_per_axis))

        def label_visible(label: str) -> bool:
            frame = self.cur_frame(update=True)
            cached_ocr = self.runner._shared_spatial_ocr_result(self.ctx, frame)
            tokens = cached_ocr.get("tokens") if isinstance(cached_ocr.get("tokens"), list) else []
            return any(label in re.sub(r"\s+", "", str(fragment.get("text") or "")) for fragment in group_ocr_tokens(tokens))

        def ensure_axis_visible(label: str, direction: str):
            for scroll_index in range(max_scrolls_per_axis + 1):
                if label_visible(label):
                    return
                if scroll_index >= max_scrolls_per_axis:
                    break
                # This container is filled with slider tracks.  A center drag
                # can be consumed by the slider underneath and leave the list
                # stationary, so scroll through the right-side blank band of
                # the already annotated container.
                self.drag_shape_content(
                    target_view,
                    scroll_shape,
                    direction=direction,
                    ratio=0.65,
                    duration=1.0,
                    cross_axis_ratio=0.92,
                )
                yield from self.wait_action_settle(settle_seconds)
            raise RuntimeError(f"滚动难度窗口后仍未看到“{label}”")

        results: list[dict[str, Any]] = []
        for index, (axis, target_value) in enumerate(zip(TRIAL_DIFFICULTY_AXES, plan.values)):
            # Start from the first row and then move monotonically toward the
            # lower rows.  This keeps scroll behavior deterministic regardless
            # of where the previous Cell left the list.
            direction = "up" if index < 3 else "down"
            yield from ensure_axis_visible(axis.label, direction)
            result = yield from self.set_slider_value(
                target_view,
                axis.label,
                target_value,
                track=track_shape,
                anchor=title_anchor_shape,
                minimum=axis.minimum,
                maximum=axis.maximum,
                step=axis.step,
                settle_seconds=settle_seconds,
            )
            results.append(result)

        # A newly unlocked trial reports a level below 6 even though each
        # slider row already renders its first non-zero label. Merely observing
        # those labels as equal to the level-6 plan performs no gesture, so the
        # game never activates that axis. Nudge every axis up one step and back
        # only for this proven bootstrap state.
        bootstrap_observation: ObservedTrialDifficulty | None = None
        if plan.level == TRIAL_DIFFICULTY_MIN_LEVEL:
            try:
                bootstrap_observation = self.read_current_trial_difficulty(target_view)
            except RuntimeError:
                bootstrap_observation = None
        if (
            bootstrap_observation is not None
            and TRIAL_DIFFICULTY_BASE_LEVEL <= bootstrap_observation.level < TRIAL_DIFFICULTY_MIN_LEVEL
        ):
            for index, (axis, target_value) in enumerate(zip(TRIAL_DIFFICULTY_AXES, plan.values)):
                yield from ensure_axis_visible(axis.label, "up" if index < 3 else "down")
                nudge_value = axis.value_at(2)
                for value in (nudge_value, target_value):
                    results.append((yield from self.set_slider_value(
                        target_view,
                        axis.label,
                        value,
                        track=track_shape,
                        anchor=title_anchor_shape,
                        minimum=axis.minimum,
                        maximum=axis.maximum,
                        step=axis.step,
                        settle_seconds=settle_seconds,
                    )))
            self.runner._log(
                "detail",
                "仙窍试炼难度：已通过逐轴往返激活新解锁线路的最低 6 级配置",
            )

        final_observation: ObservedTrialDifficulty | None = None
        last_read_error: RuntimeError | None = None
        # A global scrolling announcement can cross the ``当前难度`` label for
        # several seconds while leaving #358 otherwise fully interactive.  The
        # field is authoritative, so wait for an unobscured frame instead of
        # turning a transient overlay into a technical Scheduler retry.
        read_attempts = 8
        read_retry_seconds = max(2.0, float(settle_seconds))
        for verification in range(read_attempts):
            try:
                final_observation = self.read_current_trial_difficulty(target_view)
                last_read_error = None
                if final_observation.level == plan.level:
                    break
            except RuntimeError as exc:
                # The difficulty label is animated after the final slider drag.
                # A single OCR frame can therefore be empty even though the
                # settled frame immediately afterwards is authoritative.
                last_read_error = exc
            if verification + 1 < read_attempts:
                yield from self.wait_action_settle(read_retry_seconds)
        if final_observation is None and last_read_error is not None:
            raise RuntimeError(
                f"难度配置后连续{read_attempts}次未读到当前难度"
            ) from last_read_error
        if final_observation is None or final_observation.level != plan.level:
            actual = final_observation.level if final_observation is not None else None
            raise RuntimeError(f"难度配置后显示等级不符：目标 {plan.level}，实际 {actual}")

        return {
            "plan": {
                "level": plan.level,
                "positions": list(plan.positions),
                "values": list(plan.values),
            },
            "sliders": results,
            "final_level": final_observation.level,
            "final_text": final_observation.text,
        }

    def prepare_xianqiao_trial_settings(
        self,
        view: View | int | str = 358,
        *,
        target_level: int | None = None,
        difficulty_increment: int = 1,
        settle_seconds: float = 0.8,
    ):
        """按仙窍试炼业务顺序准备 #358 的全部开战参数。

        固定顺序是：

        1. 先按当前体系已穿戴仙纹统计金/水/火，选择数量最少者作为掉落元素；
        2. 再把五行增益点数按累计投入均衡分完；
        3. 从实时画面读取当前难度；
        3. 优先使用显式 ``target_level``；否则以
           ``当前难度 + difficulty_increment`` 生成均匀难度计划；
        4. 配置五根滑杆并复核最终显示等级。

        五行增益和难度滑杆是两套独立资源模型。五行点数不参与难度等级
        公式，但必须先完成，避免后续流程在 #358 留下未消费资源。

        ``target_level`` 保留给人工调试、纠偏和指定等级测试。正式每日推进
        不保存外部等级，只根据 #357 的“开启扫荡”状态使用相对 ``+1/-1``。
        """

        drop_element = yield from self.configure_xianqiao_trial_drop_element(
            view,
            settle_seconds=settle_seconds,
        )
        five_elements = yield from self.allocate_balanced_points(
            view,
            points_shape="五行点数",
            first_value_shape="当前攻击",
            second_value_shape="当前伤害",
            first_increase_shape="增加攻击",
            second_increase_shape="增加伤害",
            first_label="攻击",
            second_label="伤害",
            minimum=10,
            step=10,
            settle_seconds=settle_seconds,
        )
        current: ObservedTrialDifficulty | None = None
        last_current_error: RuntimeError | None = None
        read_attempts = 8
        read_retry_seconds = max(2.0, float(settle_seconds))
        for attempt in range(read_attempts):
            try:
                current = self.read_current_trial_difficulty(view)
                last_current_error = None
                break
            except RuntimeError as exc:
                # #358 is already transactionally established and the five-
                # element allocation has just animated the page.  A missing
                # difficulty label on one fresh OCR frame is therefore a
                # transient field read, not a reason to discard the whole
                # navigation and wait for the Scheduler's technical retry.
                last_current_error = exc
                if attempt + 1 < read_attempts:
                    self.runner._log(
                        "warning",
                        (
                            "仙窍_试炼：当前难度被动态公告遮挡或首帧暂未读到，"
                            f"原地刷新重试 {attempt + 2}/{read_attempts}"
                        ),
                    )
                    yield from self.wait_action_settle(read_retry_seconds)
        if current is None:
            raise RuntimeError(
                f"仙窍_试炼：连续{read_attempts}次未读到当前难度"
            ) from last_current_error
        resolved_target_level = (
            next_configurable_trial_difficulty(current.level, difficulty_increment)
            if target_level is None
            else int(target_level)
        )
        difficulty = yield from self.configure_even_trial_difficulty(
            view,
            resolved_target_level,
            settle_seconds=settle_seconds,
        )
        return {
            "drop_element": drop_element,
            "five_elements": five_elements,
            "current_level": current.level,
            "target_level": resolved_target_level,
            "difficulty": difficulty,
        }

    def configure_xianqiao_trial_drop_element(
        self,
        view: View | int | str = 358,
        *,
        selector_shape: Shape | str = "shape 12",
        settle_seconds: float = 0.8,
    ):
        """Set #358's trial drop element to the least represented 金/水/火.

        Business selection comes exclusively from the read-only
        ``ImmHoleData.GetElementLvDic(type)`` equivalent. OCR only locates the
        already-decided label in the dropdown; it must never choose the element.
        If the context model or final visual verification is unavailable, fail
        closed instead of silently keeping the game's default 金.
        """

        from backend.core.fanxiu.instrumentation.xianqiao import (
            read_xianqiao_snapshot,
            select_xianqiao_trial_drop_element,
        )

        snapshot = read_xianqiao_snapshot()
        if not snapshot.get("complete"):
            raise RuntimeError(
                "仙窍已穿戴五行数据不完整，拒绝沿用默认掉落元素："
                f"{snapshot.get('reason') or 'context snapshot incomplete'}"
            )
        decision = select_xianqiao_trial_drop_element(
            snapshot.get("element_counts_by_id") or {}
        )
        target = str(decision["element"])
        self.runner._log(
            "detail",
            (
                "仙窍试炼掉落元素决策：当前已穿戴仙纹 "
                f"金{decision['desired_counts']['金']}、"
                f"水{decision['desired_counts']['水']}、"
                f"火{decision['desired_counts']['火']}，选择最少的「{target}」"
            ),
        )
        target_view = self.view(view)
        shape_title = selector_shape.title if isinstance(selector_shape, Shape) else str(selector_shape)

        current = self.find_ocr_text(
            target_view,
            target,
            in_shapes=[shape_title],
            padding=8,
        )
        changed = current is None
        if changed:
            self.click_shape(target_view, selector_shape)
            yield from self.wait_action_settle(settle_seconds)
            # The popup is attached below the selector. Padding intentionally
            # covers the five short rows while excluding the rest of #358.
            option = self.find_ocr_text(
                target_view,
                target,
                in_shapes=[shape_title],
                padding=260,
            )
            if option is None:
                raise RuntimeError(f"#358 掉落元素下拉框未识别到目标「{target}」")
            click_x, click_y = option.point()
            self.click_frame_point(target_view, click_x, click_y)
            yield from self.wait_action_settle(settle_seconds)

        verified = self.find_ocr_text(
            target_view,
            target,
            in_shapes=[shape_title],
            padding=8,
        )
        if verified is None:
            raise RuntimeError(f"#358 掉落元素配置复核失败：目标「{target}」")
        self.runner._log(
            "success",
            (
                f"#358 掉落元素已复核为「{target}」"
                f"（{'已切换' if changed else '原设置已正确'}）"
            ),
        )
        return {
            **decision,
            "active_system_type": snapshot.get("active_system_type"),
            "worn_parts": snapshot.get("worn_parts"),
            "changed": changed,
        }

    def read_xianqiao_trial_attempts(
        self,
        view: View | int | str = 357,
        *,
        attempts_shape: Shape | str = "次数",
        frame_data_url: str | None = None,
    ) -> ObservedTrialAttempts:
        """从 #357 实时读取剩余奖励次数。

        逐级探测只在 ``remaining > 0`` 时允许发起下一场。次数必须从当前
        画面读取，不能用购买次数或本轮循环次数推导，因为购买、成功、失败
        是否消耗次数属于游戏实时状态。
        """

        shape_title = attempts_shape.title if isinstance(attempts_shape, Shape) else str(attempts_shape)
        text = self.ocr_text_in_shapes(
            view,
            [shape_title],
            padding=16,
            frame_data_url=frame_data_url,
        )
        return parse_xianqiao_trial_attempts(text)

    def observe_xianqiao_trial_home(
        self,
        view: View | int | str = 357,
        *,
        attempts_shape: Shape | str = "次数",
        sweep_shape: Shape | str = "开启扫荡",
        threshold: float | None = None,
    ) -> ObservedTrialHomeState:
        """从 #357 的同一帧读取次数和“开启扫荡”状态。

        “开启扫荡”是游戏对当前难度是否已经通关的权威标志。出现时，下一场
        应先把难度加1；未出现时，当前难度本身就是尚未通过的越级难度，应
        直接挑战，不能再加1。这里不读取或持久化昨天的成功等级。
        """

        frame = self.cur_frame(update=True)
        attempts = self.read_xianqiao_trial_attempts(
            view,
            attempts_shape=attempts_shape,
            frame_data_url=frame,
        )
        score = self.shape_score(view, sweep_shape, frame_data_url=frame)
        min_score = self.runner.overlay_threshold if threshold is None else float(threshold)
        return ObservedTrialHomeState(
            attempts=attempts,
            sweep_available=score >= float(min_score),
            sweep_score=score,
        )

    def select_xianqiao_trial_track(
        self,
        track: Literal["higher", "lower"] | str,
        *,
        home_view: View | int | str = 357,
        settle_seconds: float = 0.8,
    ):
        """Select the exact 黑凤王 (A) or 血光 (B) track and remain on #357.

        #357 contains more tracks on both sides, including locked tracks. To
        avoid treating one relative arrow click as an identity, selection is
        anchored at the leftmost supported track (血光). A is then exactly one
        step to its right. Every endpoint is verified from the bounded
        ``首领名称`` OCR region.
        """

        normalized = normalize_xianqiao_trial_track(track)
        home_id = int(self.view(home_view).id)
        yield from self.wait_scene(
            [home_view],
            wait=15.0,
            label=f"选择仙窍试炼线路前确认主页 #{home_id}",
        )
        observations: list[dict[str, Any]] = []
        for _index in range(len(XIANQIAO_TRIAL_KNOWN_ENEMIES)):
            observation = self.observe_xianqiao_trial_track(home_view)
            observations.append(observation)
            if observation["enemy"] == XIANQIAO_TRIAL_TRACK_ENEMIES["lower"]:
                break
            self.click_shape_center(home_view, XIANQIAO_TRIAL_TRACK_SHAPES["lower"])
            yield from self.wait_action_settle(settle_seconds)
            yield from self.wait_scene(
                [home_view],
                wait=15.0,
                label="向左查找血光试炼后确认 #357",
            )
        else:
            raise RuntimeError("连续向左查找后仍未定位到血光试炼")

        action_shape = None
        if normalized == "higher":
            action_shape = XIANQIAO_TRIAL_TRACK_SHAPES["higher"]
            self.click_shape_center(home_view, action_shape)
            yield from self.wait_action_settle(settle_seconds)
            yield from self.wait_scene(
                [home_view],
                wait=15.0,
                label="从血光切换黑凤王后确认 #357",
            )
        final_observation = self.observe_xianqiao_trial_track(home_view)
        expected_enemy = XIANQIAO_TRIAL_TRACK_ENEMIES[normalized]
        if final_observation["enemy"] != expected_enemy:
            raise RuntimeError(
                f"仙窍试炼线路选择不符：目标 {expected_enemy}，实际 {final_observation['enemy']}"
            )
        return {
            "track": normalized,
            "enemy": expected_enemy,
            "action_shape": action_shape,
            "terminal_scene": home_id,
            "observations": observations,
        }

    def observe_xianqiao_trial_track(
        self,
        home_view: View | int | str = 357,
        *,
        enemy_shape: Shape | str = "首领名称",
        frame_data_url: str | None = None,
    ) -> dict[str, Any]:
        """Read the current #357 trial identity from its bounded name region."""

        frame = (
            frame_data_url
            if isinstance(frame_data_url, str) and frame_data_url
            else self.cur_frame(update=True)
        )
        shape_title = enemy_shape.title if isinstance(enemy_shape, Shape) else str(enemy_shape)
        text = self.ocr_text_in_shapes(
            home_view,
            (shape_title,),
            padding=0,
            frame_data_url=frame,
            crop=True,
        )
        compact = re.sub(r"\s+", "", text)
        enemies = [enemy for enemy in XIANQIAO_TRIAL_KNOWN_ENEMIES if enemy in compact]
        if len(enemies) != 1:
            raise RuntimeError(f"#357 未唯一识别当前试炼首领：{text!r}")
        enemy = enemies[0]
        track = next(
            (key for key, value in XIANQIAO_TRIAL_TRACK_ENEMIES.items() if value == enemy),
            None,
        )
        return {"track": track, "enemy": enemy, "text": text}

    def configure_xianqiao_trial_level(
        self,
        target_level: int,
        *,
        home_view: View | int | str = 357,
        settings_view: View | int | str = 358,
        settle_seconds: float = 0.8,
    ):
        """从 #357 进入设置页并配置一个绝对难度后返回 #357。

        这是人工调试和纠偏接口；正式每日流程使用
        :meth:`adjust_xianqiao_trial_level`，避免依赖外部保存的等级。
        """

        self.click_shape_center(home_view, "设置难度")
        yield from self.wait_action_settle(settle_seconds)
        yield from self.wait_scene(
            [settings_view],
            wait=15.0,
            label="进入仙窍试炼设置页",
        )
        settings = yield from self.prepare_xianqiao_trial_settings(
            settings_view,
            target_level=int(target_level),
            settle_seconds=settle_seconds,
        )
        self.click_shape_center(settings_view, "返回")
        yield from self.wait_action_settle(settle_seconds)
        yield from self.wait_scene(
            [home_view],
            wait=15.0,
            label=f"仙窍试炼{int(target_level)}级设置后返回主页",
        )
        return settings

    def inspect_xianqiao_trial_track_state(
        self,
        *,
        home_view: View | int | str = 357,
        settings_view: View | int | str = 358,
        settle_seconds: float = 0.8,
    ):
        """Read one track's identity, current level and sweep eligibility.

        The settings page is opened only to read the game's authoritative
        current level.  No slider or drop-element control is changed.  A level
        is exposed as ``sweepable_level`` only when #357 simultaneously shows
        ``开启扫荡``.
        """

        track = self.observe_xianqiao_trial_track(home_view)
        home = self.observe_xianqiao_trial_home(home_view)
        self.click_shape_center(home_view, "设置难度")
        yield from self.wait_action_settle(settle_seconds)
        yield from self.wait_scene(
            [int(self.view(settings_view).id)],
            wait=15.0,
            label="仙窍试炼：只读当前线路难度",
        )
        current = yield from self.wait_current_trial_difficulty(
            settings_view,
            settle_seconds=min(0.8, max(0.2, settle_seconds)),
        )
        self.click_shape_center(settings_view, "返回")
        yield from self.wait_action_settle(settle_seconds)
        yield from self.wait_scene(
            [int(self.view(home_view).id)],
            wait=15.0,
            label="仙窍试炼：只读难度后返回主页",
        )
        return {
            **track,
            "current_level": int(current.level),
            "sweep_available": bool(home.sweep_available),
            "sweepable_level": int(current.level) if home.sweep_available else None,
            "attempts": {
                "remaining": int(home.attempts.remaining),
                "capacity": int(home.attempts.capacity),
                "text": home.attempts.text,
            },
        }

    def adjust_xianqiao_trial_level(
        self,
        difficulty_increment: int,
        *,
        home_view: View | int | str = 357,
        settings_view: View | int | str = 358,
        settle_seconds: float = 0.8,
    ):
        """从 #357 进入设置页，把实时难度相对调整后返回 #357。

        日常无状态推进只使用 ``+1``（已出现“开启扫荡”）和 ``-1``（首次
        失败后回退到已证明可通过的难度）。具体等级由 #358 的实时文字读取，
        不从外部保存的等级推导。
        """

        increment = int(difficulty_increment)
        if increment == 0:
            raise ValueError("仙窍试炼相对难度调整不能为0")
        self.click_shape_center(home_view, "设置难度")
        yield from self.wait_action_settle(settle_seconds)
        yield from self.wait_scene(
            [settings_view],
            wait=15.0,
            label="进入仙窍试炼设置页",
        )
        if increment < 0:
            current = yield from self.wait_current_trial_difficulty(
                settings_view,
                settle_seconds=min(0.8, max(0.2, settle_seconds)),
            )
            if int(current.level) <= TRIAL_DIFFICULTY_MIN_LEVEL:
                self.click_shape_center(settings_view, "返回")
                yield from self.wait_action_settle(settle_seconds)
                yield from self.wait_scene(
                    [int(self.view(home_view).id)],
                    wait=15.0,
                    label="仙窍试炼特殊低等级无需回退",
                )
                return {
                    "current_level": int(current.level),
                    "target_level": None,
                    "skipped": True,
                    "reason": "no_configurable_predecessor",
                }
        settings = yield from self.prepare_xianqiao_trial_settings(
            settings_view,
            difficulty_increment=increment,
            settle_seconds=settle_seconds,
        )
        self.click_shape_center(settings_view, "返回")
        yield from self.wait_action_settle(settle_seconds)
        yield from self.wait_scene(
            [home_view],
            wait=15.0,
            label=f"仙窍试炼难度{increment:+d}后返回主页",
        )
        return settings

    def start_xianqiao_trial_challenge(
        self,
        *,
        challenge_view: View | int | str = 357,
        start_confirm_view: View | int | str = 359,
        continue_confirm_view: View | int | str = 360,
        sweep_confirm_view: View | int | str = 366,
        max_polls: int = 30,
        stable_departure_polls: int = 5,
        max_action_attempts: int = 3,
        action_retry_polls: int = 3,
        settle_seconds: float = 0.8,
        sweep_result_delay: float = 5.0,
        sweep_return_timeout: float = 15.0,
    ):
        """按当前实际场景推进仙窍试炼的开战确认链。

        这是从试炼主页进入战斗的一组标准动作。函数每轮只观察当前场景并
        响应它实际看到的按钮，不使用“是否加过难度”等历史变量推导弹窗：

        - 遇到 #357，点击“挑战”；
        - 遇到 #359，点击“开始挑战”；
        - 遇到 #360，点击“继续挑战”。
        - 遇到 #366，点击“开启扫荡”。

        #359/#360/#366 都只是点击 #357“挑战”后可能实际出现的候选，不由
        调用方根据难度历史预选分支。因此函数也可从任一确认页恢复执行。
        若游戏没有展示某个确认场景，该步骤会自然跳过；连续若干轮离开已知场景后，视为已经进入加载
        或战斗。每个已处理场景最多点击一次，避免界面切换延迟造成重复操作。

        游戏可能走 ``#366 -> #367 -> #357``，也可能把通用奖励页 #227
        插在其中，而且一次扫荡可能连续出现多页 #227。#367 会自动消失；
        #227 必须点击已有的“继续”动作。因此点击扫荡后会在限定时间内按
        当前真实场景逐页收口，直到稳定回到 #357。

        :param challenge_view: 试炼主页及“挑战”动作所在 View，默认 #357。
        :param start_confirm_view: “开始挑战”确认 View，默认 #359。
        :param continue_confirm_view: “继续挑战”确认 View，默认 #360。
        :param sweep_confirm_view: “开启扫荡”确认 View，默认 #366。
        :param int max_polls: 整个确认链允许的最大观察轮数。
        :param int stable_departure_polls: 离开已知场景后判定进入战斗所需连续轮数。
        :param float settle_seconds: 点击或观察之间的稳定等待秒数。
        :param float sweep_result_delay: 扫荡后等待瞬时奖励层消失的秒数。
        :param float sweep_return_timeout: 奖励层消失后等待 #357 的超时时间。
        :return dict: 实际执行的动作及离开确认链的原因。
        """

        views = {
            int(self.view(challenge_view).id): "挑战",
            int(self.view(start_confirm_view).id): "开始挑战",
            int(self.view(continue_confirm_view).id): "继续挑战",
            int(self.view(sweep_confirm_view).id): "开启扫荡",
        }
        challenge_id = int(self.view(challenge_view).id)
        continue_id = int(self.view(continue_confirm_view).id)
        sweep_id = int(self.view(sweep_confirm_view).id)
        terminal_confirmation_ids = {continue_id}
        max_polls = max(1, int(max_polls))
        stable_departure_polls = max(1, int(stable_departure_polls))
        max_action_attempts = max(1, int(max_action_attempts))
        action_retry_polls = max(1, int(action_retry_polls))
        handled: set[int] = set()
        action_attempts: dict[int, int] = {}
        last_action_poll: dict[int, int] = {}
        actions: list[dict[str, Any]] = []
        absent_polls = 0
        last_scene_id: int | None = None

        business_foreground_ids = tuple(
            scene_id for scene_id in views if scene_id != challenge_id
        ) + (227, 367)
        with self.expect_views(business_foreground_ids):
            for poll_index in range(max_polls):
                _wait_scene_match = yield from self.wait_scene(list(views), wait=5.0, required=False)
                (scene_id, score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, self.frame_data_url or "")
                )
                last_scene_id = scene_id
                if scene_id in views:
                    absent_polls = 0
                    attempts = int(action_attempts.get(scene_id) or 0)
                    retry_ready = (
                        attempts == 0
                        or poll_index - int(last_action_poll.get(scene_id) or 0) >= action_retry_polls
                    )
                    if attempts < max_action_attempts and retry_ready:
                        shape = views[scene_id]
                        click_ratios = (0.5, 0.72, 0.28)
                        x_ratio = click_ratios[min(attempts, len(click_ratios) - 1)]
                        if attempts:
                            self.runner._log(
                                "warning",
                                (
                                    f"仙窍试炼：#{scene_id}「{shape}」点击后仍在原场景，"
                                    f"框内换点重试 {attempts + 1}/{max_action_attempts}"
                                ),
                            )
                        self.click_shape(
                            scene_id,
                            shape,
                            frame_data_url=frame,
                            x_ratio=x_ratio,
                        )
                        handled.add(scene_id)
                        action_attempts[scene_id] = attempts + 1
                        last_action_poll[scene_id] = poll_index
                        actions.append({
                            "scene": scene_id,
                            "shape": shape,
                            "score": float(score),
                            "attempt": attempts + 1,
                            "x_ratio": x_ratio,
                        })
                        if scene_id == sweep_id:
                            yield from self.wait_action_settle(sweep_result_delay)
                            return_deadline = time.monotonic() + max(1.0, float(sweep_return_timeout))
                            while True:
                                remaining = return_deadline - time.monotonic()
                                if remaining <= 0:
                                    raise TimeoutError("仙窍试炼扫荡奖励收口超时，未返回 #357")
                                landed = yield from self.wait_scene(
                                    [challenge_view,
                                    227,
                                    367],
                                    wait=remaining,
                                    label="仙窍试炼扫荡奖励收口并返回主页",
                                )
                                landed_id = int(landed.id)
                                if landed_id == challenge_id:
                                    break
                                if landed_id == 227:
                                    # Reward layers can disappear between the
                                    # wait result and the click.  Reconfirm a
                                    # stable #227 so a late ``继续`` cannot
                                    # penetrate into #357's purchase button.
                                    yield from self.wait_action_settle(0.35)
                                    _wait_scene_match = yield from self.wait_scene([227, challenge_id, 367], wait=5.0, required=False)
                                    (stable_id, _stable_score, frame) = (
                                        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                                        if _wait_scene_match is not None else (None, 0.0, self.frame_data_url or "")
                                    )
                                    if stable_id == challenge_id:
                                        break
                                    if stable_id != 227:
                                        yield from self.wait_action_settle(settle_seconds)
                                        continue
                                    self.click_shape(227, "继续", frame_data_url=frame)
                                    actions.append({
                                        "scene": 227,
                                        "shape": "继续",
                                        "score": 100.0,
                                    })
                                yield from self.wait_action_settle(settle_seconds)
                            return {
                                "actions": actions,
                                "exit_reason": "sweep_completed",
                                "last_scene": challenge_id,
                            }
                        yield from self.wait_action_settle(settle_seconds)
                        if scene_id in terminal_confirmation_ids:
                            return {
                                "actions": actions,
                                "exit_reason": "continue_confirmed",
                                "last_scene": scene_id,
                            }
                        continue
                elif handled:
                    absent_polls += 1
                    if absent_polls >= stable_departure_polls:
                        return {
                            "actions": actions,
                            "exit_reason": "left_confirmation_chain",
                            "last_scene": scene_id,
                        }
                elif poll_index + 1 >= stable_departure_polls:
                    raise RuntimeError("未遇到 #357/#359/#360/#366，无法开始仙窍试炼挑战")

                yield from self.wait_action_settle(settle_seconds)

            pending = (
                f"#{last_scene_id}"
                if last_scene_id is not None
                else "unknown"
            )
            if challenge_id in handled:
                raise TimeoutError(f"仙窍试炼开战确认链超时，最后场景 {pending}")
            raise RuntimeError(f"未能开始仙窍试炼挑战，最后场景 {pending}")

    def wait_xianqiao_trial_result(
        self,
        *,
        battle_view: View | int | str = 362,
        success_view: View | int | str = 361,
        failure_view: View | int | str = 365,
        world_view: View | int | str = 34,
        battle_entry_timeout: float = 30.0,
        battle_timeout: float = 360.0,
        result_settle_seconds: float = 0.2,
        result_confirmation_seconds: float = 3.0,
    ):
        """识别 #362 战斗中状态并等待仙窍试炼结算画面。

        #362 是持续数分钟的战斗中间态，不是异常，也不是挑战完成。函数可
        以从 #362 等待到结算，也可在 Cell 恢复时直接接管 #361/#365。
        两种结算都包含“退出”，但成功/失败必须由场景身份区分，不能由共用
        按钮反推结果。

        业务上 #361/#365 若约1分钟无人操作，大概率会自行消失并回到 #34。
        一旦回到世界页，胜负状态和可点击现场都已丢失，重新进入仙窍的恢复
        成本很高。因此等待候选显式包含 #34：命中时返回 ``result_expired``，
        绝不把它猜成成功或失败；正常命中结算后只等待极短稳定时间，交由上
        层立即记录结果并点击“退出”。工程调度不应在这个窗口执行其它工作。

        本函数只观察结果，不点击“退出”。需要结算后自动返回 #357 时使用
        :meth:`complete_xianqiao_trial_challenge`。

        :param battle_view: “战斗中”场景，默认 #362。
        :param success_view: 成功结算及“退出”动作所在 View，默认 #361。
        :param failure_view: 失败结算及“退出”动作所在 View，默认 #365。
        :param world_view: 结算自动消失后的世界页，默认 #34。
        :param float battle_entry_timeout: 开战确认后等待进入 #362 的秒数。
        :param float battle_timeout: 在 #362 中等待战斗结束的最大秒数。
        :return dict: ``success``、``failure`` 或 ``result_expired`` 及 OCR 证据。
        """

        battle = self.view(battle_view)
        success = self.view(success_view)
        failure = self.view(failure_view)
        world = self.view(world_view)
        # A chat window can cover an in-progress battle (observed #30 over
        # #362 on 2026-09-24). Recognize it in Layer 0 so the battle result
        # watcher can close it before the short result page expires.
        chat_id, chat_list_id = 30, 332
        candidate_ids = [int(battle.id), int(success.id), int(failure.id), int(world.id), chat_id]
        result_ids = {int(success.id), int(failure.id)}

        def resolved_id(value: View | int) -> int:
            return int(value.id) if isinstance(value, View) else int(value)

        result_view = yield from self.wait_scene(
            candidate_ids,
            wait=battle_entry_timeout,
            label="等待仙窍试炼战斗或直接结算",
        )
        deadline = time.monotonic() + max(1.0, float(battle_timeout))
        chat_dismissals = 0
        while True:
            result_id = resolved_id(result_view)
            if result_id == chat_id:
                chat_dismissals += 1
                if chat_dismissals > 2:
                    raise RuntimeError("仙窍试炼战斗期间聊天窗反复遮挡，保留现场")
                self.runner._log("warning", "仙窍试炼战斗期间 #30 聊天窗遮挡，点击标注返回后继续观察")
                self.click_shape_center(chat_id, "返回")
                yield from self.wait_action_settle(0.4)
                result_view = yield from self.wait_scene(
                    [int(battle.id), int(success.id), int(failure.id), int(world.id), chat_list_id, chat_id],
                    wait=15.0,
                    label="仙窍试炼关闭聊天窗后的实际落点",
                )
                if resolved_id(result_view) == chat_list_id:
                    self.click_shape_center(chat_list_id, "返回")
                    yield from self.wait_action_settle(0.4)
                    result_view = yield from self.wait_scene(
                        [int(battle.id), int(success.id), int(failure.id), int(world.id), chat_id],
                        wait=15.0,
                        label="仙窍试炼关闭聊天列表后的实际落点",
                    )
                continue
            if result_id == int(battle.id):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("等待仙窍试炼最终结算超时")
                result_view = yield from self.wait_scene(
                    [int(success.id), int(failure.id), int(world.id), chat_id],
                    wait=remaining,
                    label="等待仙窍试炼成功、失败或结算过期",
                )
                continue
            if result_id not in result_ids:
                break
            if float(result_confirmation_seconds) <= 0:
                break
            # Multi-wave battles briefly reuse #361 after an intermediate
            # wave.  Only a result layer that remains present is terminal;
            # #361 -> #362 means the battle is still running.
            yield from self.wait_action_settle(result_confirmation_seconds)
            _wait_scene_match = yield from self.wait_scene(candidate_ids, wait=5.0, required=False)
            (stable_id, _stable_score, _stable_frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, self.frame_data_url or "")
            )
            if stable_id == result_id:
                break
            if stable_id == int(battle.id):
                self.runner._log(
                    "detail",
                    f"仙窍试炼 #{result_id} 为中途结算层，已回 #362，继续等待",
                )
                result_view = int(battle.id)
                continue
            if stable_id in result_ids or stable_id in {int(world.id), chat_id}:
                result_view = int(stable_id)
                continue
            raise RuntimeError(
                f"仙窍试炼结算确认后进入未知场景 #{stable_id}"
            )

        result_id = resolved_id(result_view)
        if result_id == int(world.id):
            return {
                "outcome": "result_expired",
                "battle_scene": int(battle.id),
                "result_scene": int(world.id),
                "ocr_text": "",
                "_frame_data_url": None,
            }
        yield from self.wait_action_settle(result_settle_seconds)
        frame = self.cur_frame(update=True)
        outcome_by_scene = {
            int(success.id): "success",
            int(failure.id): "failure",
        }
        outcome = outcome_by_scene.get(result_id)
        if outcome is None:
            raise RuntimeError(f"仙窍试炼结算命中了非候选场景 #{result_id}")
        return {
            "outcome": outcome,
            "battle_scene": int(battle.id),
            "result_scene": result_id,
            "ocr_text": self.ocr_text(update=False),
            "_frame_data_url": frame,
        }

    def complete_xianqiao_trial_challenge(
        self,
        *,
        track: Literal["higher", "lower"] | str | None = None,
        home_view: View | int | str = 357,
        success_view: View | int | str = 361,
        battle_view: View | int | str = 362,
        failure_view: View | int | str = 365,
        world_view: View | int | str = 34,
        battle_entry_timeout: float = 30.0,
        battle_timeout: float = 360.0,
        settle_seconds: float = 0.8,
    ):
        """发起仙窍试炼、等待 #362 战斗结束并安全处理已知成功页。

        这是业务调用方最常用的完整挑战接口。它组合开战确认链与战斗结果
        等待，明确区分 #361 成功和 #365 失败；两种已知结算都会立即记录并
        点击各自“退出”，避免约1分钟后自动回 #34。结算页退出或自动消失后
        既可能回 #357，也可能直接回 #34；命中 #34 时复用正式入口重新进入
        #357。若结算未捕捉，本函数只恢复主页，不猜测胜负，交由逐级探测根
        据战后“开启扫荡”状态判定本轮结果。

        行为树执行器会在点击前写入明确结果日志并把结构化结果返回。无需持久化
        最高成功或首次失败等级；返回 #357 后由游戏按钮继续提供权威状态。

        :return dict: 开战动作、结算识别结果，以及是否已返回主页。
        """

        original_track = normalize_xianqiao_trial_track(track) if track is not None else None

        started = yield from self.start_xianqiao_trial_challenge(
            settle_seconds=settle_seconds,
        )
        if started.get("exit_reason") == "sweep_completed":
            return {
                "started": started,
                "result": {
                    "outcome": "sweep",
                    "result_scene": int(self.view(home_view).id),
                },
                "returned_home": True,
            }
        result = yield from self.wait_xianqiao_trial_result(
            battle_view=battle_view,
            success_view=success_view,
            failure_view=failure_view,
            world_view=world_view,
            battle_entry_timeout=battle_entry_timeout,
            battle_timeout=battle_timeout,
            result_settle_seconds=settle_seconds,
        )
        frame = result.pop("_frame_data_url", None)
        if result["outcome"] == "result_expired":
            self.runner._log(
                "warning",
                "仙窍试炼结算未捕捉且已回到 #34，重新进入 #357 复核当前关卡通关状态",
            )
            reentry = yield from self.enter_xianqiao_trial(
                trial_view=home_view,
                settle_seconds=settle_seconds,
            )
            if original_track is not None:
                yield from self.select_xianqiao_trial_track(
                    original_track,
                    home_view=home_view,
                    settle_seconds=settle_seconds,
                )
            return {
                "started": started,
                "result": result,
                "returned_home": True,
                "landing_scene": int(self.view(world_view).id),
                "reentered_from_world": True,
                "reentry": reentry,
                "original_track": original_track,
            }

        self.runner._log(
            "success" if result["outcome"] == "success" else "warning",
            (
                f"仙窍试炼结算已确认：{result['outcome']} "
                f"#{result['result_scene']}，立即退出以避免弹窗自动消失"
            ),
        )
        self.click_shape_center(result["result_scene"], "退出")
        yield from self.wait_action_settle(settle_seconds)
        landing_view = yield from self.wait_scene(
            [home_view,
            world_view],
            wait=15.0,
            label="仙窍试炼结算退出后的实际落点",
        )
        landing_id = int(landing_view.id) if isinstance(landing_view, View) else int(landing_view)
        reentry = None
        if landing_id == int(self.view(world_view).id):
            self.runner._log(
                "action",
                "仙窍试炼结算退出后直接回到 #34，重新进入 #357 继续本轮任务",
            )
            reentry = yield from self.enter_xianqiao_trial(
                trial_view=home_view,
                settle_seconds=settle_seconds,
            )
            if original_track is not None:
                yield from self.select_xianqiao_trial_track(
                    original_track,
                    home_view=home_view,
                    settle_seconds=settle_seconds,
                )
        return {
            "started": started,
            "result": result,
            "returned_home": True,
            "landing_scene": landing_id,
            "reentered_from_world": reentry is not None,
            "reentry": reentry,
            "original_track": original_track,
        }

    def probe_xianqiao_trial_until_failure(
        self,
        *,
        home_view: View | int | str = 357,
        settings_view: View | int | str = 358,
        max_challenges: int = 20,
        settle_seconds: float = 0.8,
        battle_timeout: float = 360.0,
        track: Literal["higher", "lower"] | str | None = None,
    ):
        """完全依赖 #357 实时状态逐级挑战，直到次数耗尽或首次失败。

        每轮先观察同一帧中的剩余次数和“开启扫荡”：若按钮存在，说明当前
        难度已通关，先相对加1再挑战；若按钮不存在，说明当前已经处于越级
        状态，直接挑战。成功后回到 #357 再观察，绝不在内存中推算下一等级。

        首次失败后立即把当前难度相对减1，恢复到刚刚已经证明能通过的难度。
        若仍有次数，返回 ``sweep_required=True``，留在 #357 等待后续扫荡
        逻辑消费；扫荡动作尚未纳入本函数。

        #361/#365 约1分钟无人操作会自动回 #34，点击结算页“退出”也可能直
        接落到 #34。完整挑战函数会重新进入 #357。若结算窗口未捕捉，则用
        战后主页的“开启扫荡”恢复本轮结果：出现表示刚挑战的当前关已通关，
        未出现表示刚挑战失败。这个规则只用于同一轮挑战后的复核，不能与初
        次进入时“无扫荡则允许挑战一次”的规则混用。

        当前接口故意不自动购买次数，也不在失败后扫荡。每日入口应先调用
        :meth:`purchase_xianqiao_trial_attempts` 买到目标档位，再调用本函数。
        :param int max_challenges: 单次调用最多实际挑战次数，防止无界循环。
        :return dict: 剩余次数、是否需要扫荡、逐轮证据和停止原因。
        """

        home_id = int(self.view(home_view).id)
        _wait_scene_match = yield from self.wait_scene([home_id], wait=5.0, required=False)
        (scene_id, score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, self.frame_data_url or "")
        )
        if scene_id != home_id:
            raise RuntimeError(
                f"逐级探测必须从仙窍试炼主页 #{home_id} 开始，"
                f"实际 #{scene_id} ({float(score):.0f}%)"
            )

        trials: list[dict[str, Any]] = []
        last_observation: ObservedTrialHomeState | None = None

        def finish(
            exit_reason: str,
            remaining_attempts: int | None,
            *,
            rollback_settings: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            sweepable_level = None
            if isinstance(rollback_settings, dict) and not rollback_settings.get("skipped"):
                target_level = rollback_settings.get("target_level")
                if target_level is not None:
                    sweepable_level = int(target_level)
            return {
                "exit_reason": exit_reason,
                "remaining_attempts": remaining_attempts,
                "sweep_required": bool(
                    exit_reason == "failure_found"
                    and remaining_attempts is not None
                    and remaining_attempts > 0
                ),
                "rollback_settings": rollback_settings,
                "sweepable_level": sweepable_level,
                "trials": trials,
            }

        for _index in range(max(1, int(max_challenges))):
            observation = self.observe_xianqiao_trial_home(home_view)
            last_observation = observation
            attempts = observation.attempts
            if attempts.remaining <= 0:
                return finish("attempts_exhausted", attempts.remaining)

            settings = None
            mode = "challenge_existing_overlevel"
            if observation.sweep_available:
                settings = yield from self.adjust_xianqiao_trial_level(
                    1,
                    home_view=home_view,
                    settings_view=settings_view,
                    settle_seconds=settle_seconds,
                )
                mode = "incremented_from_sweep"
            challenge = yield from self.complete_xianqiao_trial_challenge(
                track=track,
                home_view=home_view,
                battle_timeout=battle_timeout,
                settle_seconds=settle_seconds,
            )
            outcome = str(challenge["result"]["outcome"])
            outcome_source = "result_scene"
            recovery_observation = None
            if outcome == "result_expired" and challenge.get("returned_home"):
                recovery_observation = self.observe_xianqiao_trial_home(home_view)
                outcome = "success" if recovery_observation.sweep_available else "failure"
                outcome_source = "post_challenge_sweep"
                self.runner._log(
                    "success" if outcome == "success" else "warning",
                    (
                        "仙窍试炼结算未捕捉，重新进入后"
                        f"{'检测到' if recovery_observation.sweep_available else '未检测到'}"
                        f"“开启扫荡”，判定本轮{outcome}"
                    ),
                )
            attempts_after = (
                recovery_observation.attempts
                if recovery_observation is not None
                else (
                    self.read_xianqiao_trial_attempts(home_view)
                    if challenge.get("returned_home")
                    else None
                )
            )
            record = {
                "mode": mode,
                "resolved_outcome": outcome,
                "outcome_source": outcome_source,
                "sweep_score_before": observation.sweep_score,
                "sweep_score_after": (
                    recovery_observation.sweep_score
                    if recovery_observation is not None
                    else None
                ),
                "attempts_before": {
                    "remaining": attempts.remaining,
                    "capacity": attempts.capacity,
                    "text": attempts.text,
                },
                "attempts_after": (
                    {
                        "remaining": attempts_after.remaining,
                        "capacity": attempts_after.capacity,
                        "text": attempts_after.text,
                    }
                    if attempts_after is not None
                    else None
                ),
                "settings": settings,
                "challenge": challenge,
            }
            trials.append(record)

            if outcome == "success":
                if attempts_after is None:
                    return finish("result_expired", None)
                continue
            if outcome == "failure":
                remaining = attempts_after.remaining if attempts_after is not None else None
                if attempts_after is None:
                    return finish("result_expired", None)
                rollback = yield from self.adjust_xianqiao_trial_level(
                    -1,
                    home_view=home_view,
                    settings_view=settings_view,
                    settle_seconds=settle_seconds,
                )
                return finish(
                    "failure_found",
                    remaining,
                    rollback_settings=rollback,
                )
            return finish("result_expired", None)

        remaining = (
            last_observation.attempts.remaining
            if last_observation is not None
            else None
        )
        return finish("max_challenges_reached", remaining)

    def purchase_xianqiao_trial_attempts(
        self,
        target_daily_purchases: int = 3,
        *,
        home_view: View | int | str = 357,
        purchase_view: View | int | str = 363,
        exhausted_view: View | int | str = 364,
        wait_timeout: float = 15.0,
        settle_seconds: float = 0.8,
        max_transitions: int = 12,
    ):
        """把仙窍试炼当天累计购买次数补到目标档位并返回 #357。

        ``target_daily_purchases`` 表示当天累计购买目标，不是本次额外购买数。
        默认 3 即清空当天三个购买档位；传 2 时只购买 100、150 灵石两档，
        在 #363 看到 200 灵石后主动点击“返回”，不会继续消费。

        函数完全根据实时场景推进，不预设点击 #357“购买”后一定出现哪个页：

        - 遇到 #357：点击“购买”，等待 #363 或 #364；
        - 遇到 #363：OCR 当前价格并反推当天已买次数。未到目标则购买，已到
          目标则点击 #363“返回”；
        - 遇到 #364：说明当天购买次数已达上限，点击 #364“返回”。

        因此它既可从 #357 开始，也可从已打开的 #363/#364 恢复；每次购买
        后同样重新识别实际场景。未知价格会立即停止，宁可保留现场也不误购。

        :param int target_daily_purchases: 当天累计希望购买的次数，合法值 0..3。
        :param home_view: 试炼主页及“购买”入口所在 View，默认 #357。
        :param purchase_view: 价格和“购买并使用”所在 View，默认 #363。
        :param exhausted_view: 当天购买次数耗尽提示 View，默认 #364。
        :return dict: 目标、开始/结束档位、本次实际购买价格和结束原因。
        """

        target = normalize_xianqiao_trial_purchase_target(target_daily_purchases)
        home_id = int(self.view(home_view).id)
        purchase_id = int(self.view(purchase_view).id)
        exhausted_id = int(self.view(exhausted_view).id)
        candidate_ids = (home_id, purchase_id, exhausted_id)
        purchases_now: list[int] = []
        purchased_before: int | None = None
        purchased_after: int | None = None
        actions: list[dict[str, Any]] = []
        pending_price: tuple[int, str, str] | None = None

        def waited_view_id(waited: View | int) -> int:
            if isinstance(waited, View):
                if waited.id is None:
                    raise RuntimeError("等待结果缺少 View 编号")
                return int(waited.id)
            return int(waited)

        def read_purchase_price(frame: str | None = None) -> tuple[int, str, str]:
            current_frame = frame if isinstance(frame, str) and frame else self.cur_frame(update=True)
            numbers, ocr_text = self.ocr_numbers_in_shapes(
                purchase_view,
                ["价格"],
                frame_data_url=current_frame,
            )
            known_prices = [
                number
                for number in numbers
                if number in XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES
            ]
            if len(set(known_prices)) != 1:
                raise RuntimeError(f"#363 无法唯一识别购买价格：{ocr_text!r}")
            return known_prices[0], ocr_text, current_frame

        _wait_scene_match = yield from self.wait_scene(candidate_ids, wait=5.0, required=False)
        (scene_id, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, self.frame_data_url or "")
        )
        if scene_id not in candidate_ids:
            raise RuntimeError("仙窍试炼购买流程必须从 #357/#363/#364 之一开始")

        for _transition in range(max(1, int(max_transitions))):
            if scene_id == home_id:
                self.click_shape_center(home_view, "购买")
                actions.append({"scene": home_id, "shape": "购买"})
                yield from self.wait_action_settle(settle_seconds)
                waited = yield from self.wait_scene(
                    [purchase_view,
                    exhausted_view],
                    wait=wait_timeout,
                    label="等待仙窍试炼购买结果",
                )
                scene_id = waited_view_id(waited)
                continue

            if scene_id == exhausted_id:
                purchased_before = (
                    len(XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES)
                    if purchased_before is None
                    else purchased_before
                )
                purchased_after = len(XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES)
                self.click_shape_center(exhausted_view, "返回")
                actions.append({"scene": exhausted_id, "shape": "返回"})
                yield from self.wait_action_settle(settle_seconds)
                yield from self.wait_scene(
                    [home_view],
                    wait=wait_timeout,
                    label="等待返回仙窍试炼主页",
                )
                return {
                    "target_daily_purchases": target,
                    "purchased_before": purchased_before,
                    "purchases_now": purchases_now,
                    "purchased_after": purchased_after,
                    "actions": actions,
                    "exit_reason": "daily_limit_reached",
                    "terminal_scene": home_id,
                }

            if pending_price is not None:
                price, ocr_text, frame = pending_price
                pending_price = None
            else:
                price, ocr_text, frame = read_purchase_price()
            completed = purchases_completed_before_price(price)
            purchased_before = completed if purchased_before is None else purchased_before
            purchased_after = completed

            if completed >= target:
                self.click_shape_center(purchase_view, "返回")
                actions.append({"scene": purchase_id, "shape": "返回", "price": price})
                yield from self.wait_action_settle(settle_seconds)
                yield from self.wait_scene(
                    [home_view],
                    wait=wait_timeout,
                    label="等待返回仙窍试炼主页",
                )
                return {
                    "target_daily_purchases": target,
                    "purchased_before": purchased_before,
                    "purchases_now": purchases_now,
                    "purchased_after": purchased_after,
                    "actions": actions,
                    "exit_reason": "target_reached",
                    "terminal_scene": home_id,
                }

            self.click_shape(
                purchase_view,
                "购买并使用",
                frame_data_url=frame,
            )
            purchases_now.append(price)
            purchased_after = completed + 1
            actions.append({"scene": purchase_id, "shape": "购买并使用", "price": price})
            yield from self.wait_action_settle(settle_seconds)
            waited = yield from self.wait_scene(
                [home_view,
                purchase_view,
                exhausted_view],
                wait=wait_timeout,
                label="等待仙窍试炼购买后的实际场景",
            )
            scene_id = waited_view_id(waited)
            if scene_id != purchase_id:
                continue

            # #363 本身没有离开时，必须等价格从本次档位前进到下一档。
            # wait_scene 可能在购买动画尚未刷新时立即命中旧 #363；若直接进入
            # 下一轮，会把旧价格当成仍可购买并造成重复消费。
            expected_index = completed + 1
            if expected_index >= len(XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES):
                expected_price = None
            else:
                expected_price = XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES[expected_index]
            refresh_deadline = time.monotonic() + max(1.0, float(wait_timeout))
            while scene_id == purchase_id:
                try:
                    next_price, next_text, next_frame = read_purchase_price()
                except RuntimeError:
                    next_price = None
                    next_text = ""
                    next_frame = ""
                if expected_price is not None and next_price == expected_price:
                    pending_price = (next_price, next_text, next_frame)
                    break
                if next_price not in (None, price):
                    raise RuntimeError(
                        f"#363 购买 {price} 后价格未按档位前进："
                        f"预期 {expected_price}，实际 {next_price}"
                    )
                if time.monotonic() >= refresh_deadline:
                    raise TimeoutError(f"#363 购买 {price} 后价格/场景未在限时内刷新")
                yield from self.wait_action_settle(min(0.4, settle_seconds or 0.4))
                _wait_scene_match = yield from self.wait_scene(candidate_ids, wait=5.0, required=False)
                (scene_id, _score, _frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, self.frame_data_url or "")
                )

        raise TimeoutError(
            f"仙窍试炼购买流程超过 {max_transitions} 次场景转换；"
            f"已购买价格 {purchases_now}，最后场景 #{scene_id}"
        )

    def enter_xianqiao_trial(
        self,
        *,
        daily_view: View | int | str = 69,
        category_view: View | int | str = 356,
        trial_view: View | int | str = 357,
        purchase_view: View | int | str = 363,
        purchase_exhausted_view: View | int | str = 364,
        max_daily_scrolls: int = 30,
        settle_seconds: float = 0.8,
        trial_entry_timeout: float = 120.0,
    ):
        """从稳定世界锚点进入仙窍试炼主页 #357。

        正式任务由框架先归一到 #34。本函数用专用直达动作进入 #69，在日常
        列表实时查找“仙窍”，等待 #356 后，再在“试炼”区域内用全局空间
        OCR 找到唯一“真仙”并点击。标题文字坐标不是固定 shape，因此必须
        使用本轮 OCR 坐标；无法唯一命中时保留现场并停止，不能猜位置。
        """

        retained = yield from self.wait_scene([34, 357, 363, 364], wait=5.0, required=False)
        if retained is not None and retained.scene_id in {363, 364}:
            # An interrupted zero-attempt sweep can retain the purchase panel.
            # Close it through the same verified, non-purchasing terminal path.
            yield from self.leave_xianqiao_trial(settle_seconds=settle_seconds)
        daily_entry = yield from self.enter_daily_list_direct(
            daily_view=daily_view,
            settle_seconds=settle_seconds,
        )
        status = yield from self.open_daily_entry(
            label="仙窍_试炼",
            title_pattern=r"仙\s*窍",
            progress_can_mark_done=False,
            max_scrolls=max(0, int(max_daily_scrolls)),
            # 首条任务会被世界公告/飘字短暂遮挡；先保持列表静止复识别，
            # 不要因单帧 OCR 漏字立即把入口滚出当前画面。
            initial_checks=3,
        )
        if status != "open":
            raise RuntimeError(f"仙窍_试炼：#69 未能打开仙窍入口，状态 {status!r}")
        landed = yield from self.wait_scene(
            [
                int(self.view(category_view).id),
                int(self.view(trial_view).id),
                int(self.view(purchase_view).id),
                int(self.view(purchase_exhausted_view).id),
            ],
            wait=15.0,
            label="仙窍_试炼：等待分类页、主页或次数购买页",
        )
        landed_id = int(landed.id) if isinstance(landed, View) else int(landed)
        if landed_id in {
            int(self.view(purchase_view).id),
            int(self.view(purchase_exhausted_view).id),
        }:
            self.runner._log(
                "detail",
                f"仙窍_试炼：日常入口因次数状态直达 #{landed_id}，关闭购买页",
            )
            self.click_shape_center(landed_id, "返回")
            yield from self.wait_action_settle(settle_seconds)
            purchase_return = yield from self.wait_scene(
                [int(self.view(trial_view).id), int(self.view(daily_view).id)],
                wait=15.0,
                label="仙窍_试炼：关闭购买页后确认实际落点",
            )
            purchase_return_id = (
                int(purchase_return.id)
                if isinstance(purchase_return, View)
                else int(purchase_return)
            )
            if purchase_return_id == int(self.view(daily_view).id):
                self.click_shape_center(daily_view, "退出")
                yield from self.wait_action_settle(settle_seconds)
                yield from self.wait_scene(
                    [34],
                    wait=15.0,
                    label="仙窍_试炼：次数耗尽后返回世界",
                )
                return {
                    "daily_list": daily_entry,
                    "daily_entry": status,
                    "category_scene": None,
                    "entry_landing_scene": landed_id,
                    "already_exhausted": True,
                    "terminal_scene": 34,
                }
            return {
                "daily_list": daily_entry,
                "daily_entry": status,
                "category_scene": None,
                "entry_landing_scene": landed_id,
                "already_exhausted": False,
                "terminal_scene": 357,
            }
        if landed_id == int(self.view(trial_view).id):
            return {
                "daily_list": daily_entry,
                "daily_entry": status,
                "category_scene": None,
                "entry_landing_scene": landed_id,
                "already_exhausted": False,
                "terminal_scene": 357,
            }
        frame = self.cur_frame(update=True)
        matches = self.ocr_centers_in_shape(
            category_view,
            "试炼",
            include=("真仙",),
            frame_data_url=frame,
        )
        if len(matches) != 1:
            visible = [text for _x, _y, text in matches]
            raise RuntimeError(
                f"仙窍_试炼：#356「试炼」区域无法唯一定位“真仙”，命中 {visible}"
            )
        x, y, text = matches[0]
        self.runner._log("action", f"仙窍_试炼：#356 点击 OCR「{text}」进入真仙试炼")
        self.click_frame_point(category_view, x, y)
        yield from self.wait_action_settle(settle_seconds)
        yield from self.wait_scene(
            [trial_view],
            wait=max(15.0, float(trial_entry_timeout)),
            label="仙窍_试炼：等待试炼主页 #357",
        )
        return {
            "daily_list": daily_entry,
            "daily_entry": status,
            "category_scene": 356,
            "entry_landing_scene": 356,
            "already_exhausted": False,
            "terminal_scene": 357,
        }

    def sweep_remaining_xianqiao_trial_attempts(
        self,
        *,
        home_view: View | int | str = 357,
        max_sweeps: int = 10,
        settle_seconds: float = 0.8,
    ):
        """失败回退一级后，把 #357 实时剩余次数全部扫荡完。

        每轮重新读取次数和“开启扫荡”，再复用标准挑战入口。扫荡后必须看到
        次数严格减少；否则立即停止，避免输入未生效时重复消费或无限循环。
        """

        sweeps: list[dict[str, Any]] = []
        for _index in range(max(1, int(max_sweeps))):
            observation = self.observe_xianqiao_trial_home(home_view)
            before = observation.attempts
            if before.remaining <= 0:
                return {
                    "exit_reason": "attempts_exhausted",
                    "remaining_attempts": 0,
                    "sweeps": sweeps,
                }
            if not observation.sweep_available:
                raise RuntimeError(
                    "仙窍_试炼：失败回退后仍有次数，但 #357 未识别到“开启扫荡”"
                )
            challenge = yield from self.complete_xianqiao_trial_challenge(
                home_view=home_view,
                settle_seconds=settle_seconds,
            )
            outcome = str(challenge.get("result", {}).get("outcome") or "")
            if outcome != "sweep" or not challenge.get("returned_home"):
                raise RuntimeError(f"仙窍_试炼：剩余次数扫荡未稳定返回 #357，结果 {outcome!r}")
            after = self.read_xianqiao_trial_attempts(home_view)
            if after.remaining >= before.remaining:
                raise RuntimeError(
                    f"仙窍_试炼：扫荡后次数未减少，之前 {before.remaining}，之后 {after.remaining}"
                )
            sweeps.append({
                "attempts_before": before.remaining,
                "attempts_after": after.remaining,
                "challenge": challenge,
            })
            if after.remaining <= 0:
                return {
                    "exit_reason": "attempts_exhausted",
                    "remaining_attempts": 0,
                    "sweeps": sweeps,
                }
        raise RuntimeError(f"仙窍_试炼：超过 {max_sweeps} 次扫荡仍未耗尽次数")

    def leave_xianqiao_trial(
        self,
        *,
        home_view: View | int | str = 357,
        world_view: View | int | str = 34,
        settle_seconds: float = 0.8,
    ):
        """关闭零次数购买提示后离开主页，确认稳定世界；不购买次数。"""

        home_id = int(self.view(home_view).id)
        _wait_scene_match = yield from self.wait_scene([home_id, 363, 364], wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, self.frame_data_url or "")
        )
        if scene_id in {363, 364}:
            self.click_shape_center(scene_id, "返回")
            landing = yield from self.wait_scene([home_id], wait=15.0,
                                                label="仙窍_试炼：关闭次数提示后复核主页")
            scene_id, frame = landing.scene_id, landing.frame_data_url
            if self.read_xianqiao_trial_attempts(home_view).remaining != 0:
                raise RuntimeError("仙窍_试炼：次数提示关闭后仍有剩余次数，保留现场")
        if scene_id != home_id:
            raise RuntimeError(
                f"仙窍_试炼收尾预期 #357，实际 #{scene_id} ({float(score):.0f}%)"
            )
        self.click_shape(home_view, "返回", frame_data_url=frame)
        yield from self.wait_action_settle(settle_seconds)
        yield from self.wait_scene(
            [world_view],
            wait=15.0,
            label="仙窍_试炼：等待返回世界 #34",
        )
        return {"from_scene": home_id, "terminal_scene": int(self.view(world_view).id)}

    def run_xianqiao_trial_daily(
        self,
        *,
        target_daily_purchases: int = 0,
        max_challenges: int = 20,
        settle_seconds: float = 0.8,
        battle_timeout: float = 360.0,
    ):
        """Probe A then B, and spend remaining attempts by weighted value.

        A is the higher regular track (黑凤王); B is the lower track (血光).
        Each track is pushed independently to its first failure while attempts
        remain.  Only after both frontiers are known are remaining attempts
        swept: B wins exactly when ``2 * B_level > A_level``; ties select A.
        Levels without a verified sweep button are ineligible.
        """

        purchase_target = normalize_xianqiao_trial_purchase_target(target_daily_purchases)
        if purchase_target:
            purchase = yield from self.purchase_xianqiao_trial_attempts(
                purchase_target,
                settle_seconds=settle_seconds,
            )
        else:
            purchase = {
                "target_daily_purchases": 0,
                "purchased_before": None,
                "purchases_now": [],
                "purchased_after": None,
                "actions": [],
                "exit_reason": "purchase_disabled",
                "terminal_scene": 357,
            }
        tracks: dict[str, Any] = {}
        remaining_attempts: int | None = None
        for track in ("higher", "lower"):
            selected = yield from self.select_xianqiao_trial_track(
                track,
                settle_seconds=settle_seconds,
            )
            state = yield from self.inspect_xianqiao_trial_track_state(
                settle_seconds=settle_seconds,
            )
            remaining_attempts = int(state["attempts"]["remaining"])
            progression = None
            sweepable_level = state.get("sweepable_level")
            if remaining_attempts > 0:
                progression = yield from self.probe_xianqiao_trial_until_failure(
                    track=track,
                    max_challenges=max_challenges,
                    settle_seconds=settle_seconds,
                    battle_timeout=battle_timeout,
                )
                exit_reason = str(progression.get("exit_reason") or "")
                if exit_reason not in {"attempts_exhausted", "failure_found"}:
                    raise RuntimeError(
                        f"仙窍_试炼：{track} 逐级探测未正常结束，原因 {exit_reason!r}"
                    )
                remaining_attempts = progression.get("remaining_attempts")
                if progression.get("sweepable_level") is not None:
                    sweepable_level = int(progression["sweepable_level"])
                elif exit_reason == "failure_found":
                    sweepable_level = None
            tracks[track] = {
                "selection": selected,
                "state_before": state,
                "progression": progression,
                "sweepable_level": sweepable_level,
            }
            if remaining_attempts is not None and int(remaining_attempts) <= 0:
                break

        sweep = None
        sweep_decision = None
        if remaining_attempts is not None and int(remaining_attempts) > 0:
            higher_level = tracks.get("higher", {}).get("sweepable_level")
            lower_level = tracks.get("lower", {}).get("sweepable_level")
            if higher_level is not None or lower_level is not None:
                sweep_decision = choose_xianqiao_trial_sweep_track(
                    higher_level=higher_level,
                    lower_level=lower_level,
                )
                yield from self.select_xianqiao_trial_track(
                    str(sweep_decision["track"]),
                    settle_seconds=settle_seconds,
                )
                sweep = yield from self.sweep_remaining_xianqiao_trial_attempts(
                    max_sweeps=max_challenges,
                    settle_seconds=settle_seconds,
                )
        leave = yield from self.leave_xianqiao_trial(settle_seconds=settle_seconds)
        return {
            "purchase": purchase,
            "tracks": tracks,
            "sweep_decision": sweep_decision,
            "sweep": sweep,
            "leave": leave,
            "result": "success",
            "message": "仙窍_试炼完成，已回到世界 #34",
            "current_scene": 34,
        }
