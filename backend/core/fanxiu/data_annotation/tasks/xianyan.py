from __future__ import annotations

from datetime import datetime, timedelta
import io
import re
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image

XIANYAN_REWARDS_TASK_ID = "xianyan-rewards"
XIANYAN_PARTICIPATION_TASK_ID = "xianyan-participation"
XIANYAN_CLEAN_TASK_ID = "xianyan-host-baihua"
XIANYAN_BANQUET_TYPES = (
    ("百花宴", "百花宴拥有数量", "选择百花宴"),
    ("龙凤宴", "龙凤宴拥有数量", "选择龙凤宴"),
    ("瑶星宴", "瑶星宴拥有数量", "选择瑶星宴"),
)


class XianyanTaskMixin:
    @staticmethod
    def _xianyan_ocr_text(tokens: list[dict[str, Any]]) -> str:
        return "".join(str(item.get("text") or "") for item in tokens).replace(" ", "")

    @classmethod
    def _xianyan_entry_is_visible(cls, context: Any, frame: str) -> bool:
        """Require the live activity label before using the time-varying #20 slot."""

        return "仙园游宴" in cls._xianyan_ocr_text(context.full_frame_ocr_tokens(frame))

    def _open_xianyan_entry(self, context: Any, frame: str):
        """Click the moving #20 activity entry from its live OCR token box."""

        match = context.click_ocr_text(
            20,
            "仙园游宴",
            in_shapes=["仙园游宴"],
            frame_data_url=frame,
            crop=True,
        )
        click_x, click_y = match.point()
        self._log(
            "action",
            f"仙园游宴动态入口：OCR={match.text!r}，click=({click_x:.1f},{click_y:.1f})",
        )
        return (yield from context.wait_scene(
            [630],
            wait=30.0,
            label="仙园游宴：等待活动主页",
        ))

    @staticmethod
    def _xianyan_green_checkbox_ratio(
        image: Image.Image,
        box: tuple[float, float, float, float],
    ) -> float:
        """Return the green-pixel ratio in one evidence-backed normalized checkbox box."""

        rgb = image.convert("RGB")
        width, height = rgb.size
        x, y, w, h = box
        crop = rgb.crop(
            (
                max(0, round(x * width)),
                max(0, round(y * height)),
                min(width, round((x + w) * width)),
                min(height, round((y + h) * height)),
            )
        )
        pixels = list(crop.get_flattened_data())
        if not pixels:
            return 0.0
        green = sum(
            1
            for red, channel_green, blue in pixels
            if channel_green >= 105
            and channel_green >= red * 1.25
            and channel_green >= blue * 1.08
        )
        return green / len(pixels)

    @classmethod
    def _xianyan_checkbox_is_checked(
        cls,
        context: Any,
        frame: str,
        box: tuple[float, float, float, float],
    ) -> bool:
        raw = context.runner._decode_frame_data_url(frame)
        with Image.open(io.BytesIO(raw)) as source:
            return cls._xianyan_green_checkbox_ratio(source, box) >= 0.01

    @staticmethod
    def _xianyan_shape_box(context: Any, scene_id: int, shape_name: str) -> tuple[float, float, float, float]:
        """Read one declared Shape's normalized geometry via the public context API.

        Geometry is owned by the asset tree; callers must not keep a second
        hardcoded copy of the same box.  ``context.shape`` resolves the exact
        Shape and ``shape.raw`` carries normalized x/y/w/h.
        """

        shape = context.shape(scene_id, shape_name)
        raw = getattr(shape, "raw", None)
        if not isinstance(raw, dict):
            raise RuntimeError(f"#{scene_id}「{shape_name}」缺少可读几何")
        try:
            box = (
                float(raw.get("x") or 0.0),
                float(raw.get("y") or 0.0),
                float(raw.get("w") or 0.0),
                float(raw.get("h") or 0.0),
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"#{scene_id}「{shape_name}」几何无效") from exc
        if box[2] <= 0 or box[3] <= 0:
            raise RuntimeError(f"#{scene_id}「{shape_name}」几何为空")
        return box

    @classmethod
    def _xianyan_ensure_shape_checked(
        cls,
        context: Any,
        *,
        scene_id: int,
        toggle_shape: str,
        check_shape: str,
        timeout: float = 10.0,
    ) -> None:
        """Boundedly ensure a declared checkbox Shape is checked.

        The green state is read from the checkbox Shape's own normalized box
        (asset geometry, no duplicated coordinates).  An already-checked box is
        never clicked; otherwise the toggle Shape is clicked once and re-observed.
        """

        check_box = cls._xianyan_shape_box(context, scene_id, check_shape)
        deadline = time.monotonic() + max(0.5, float(timeout))
        clicked = False
        while True:
            yield from cls._wait_xianyan_exact_scene(
                context, scene_id, timeout=8.0,
                label=f"仙宴_勾选：确认 #{scene_id}",
            )
            context.clear_frame()
            frame = context.cur_frame(update=True)
            if cls._xianyan_checkbox_is_checked(context, frame, check_box):
                return
            if not clicked:
                yield from context.wait_click(scene_id, toggle_shape, timeout=8.0)
                clicked = True
                yield from context.wait_action_settle(0.5)
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"仙宴_参与：#{scene_id}「{toggle_shape}」勾选后未出现绿色勾"
                )
            yield from context.wait_action_settle(0.4)

    @staticmethod
    def _xianyan_scene_id(value: Any) -> int | None:
        if value is None:
            return None
        scene_id = getattr(value, "id", value)
        try:
            return int(scene_id)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _wait_xianyan_exact_scene(
        cls,
        context: Any,
        *scene_ids: int,
        timeout: float,
        label: str,
    ) -> int:
        """Bounded fresh observation that only accepts an exact target scene.

        A global fallback scene (for example the unchanged #642) is not proof
        that a transition happened; the real successor may still be loading.
        Observe until the monotonic deadline and raise ``TimeoutError`` on
        timeout instead of returning the fallback or a success conclusion.
        """

        targets = tuple(int(scene_id) for scene_id in scene_ids)
        expected = "/".join(f"#{scene_id}" for scene_id in targets)
        deadline = time.monotonic() + max(0.1, float(timeout))
        last_scene: int | None = None
        while time.monotonic() < deadline:
            # Drop the cached frame so each attempt observes a genuinely new
            # screenshot rather than repeating the same fallback frame.
            context.clear_frame()
            match = yield from context.wait_scene(
                targets,
                wait=max(0.5, min(5.0, deadline - time.monotonic())),
                required=False,
                label=f"{label}：等待 {expected}",
            )
            if match is None:
                yield from context.wait_action_settle(0.3)
                continue
            actual = int(match.scene_id)
            last_scene = actual
            if actual in targets:
                return actual
            # Non-target fallback: settle briefly for a fresh frame and remain
            # interruptible, then keep observing the target set.
            yield from context.wait_action_settle(0.3)
        raise TimeoutError(
            f"{label}：到 {float(timeout):g}s 仍未出现 {expected}"
            f"（最后识别 #{last_scene if last_scene is not None else 'unknown'}）"
        )

    @classmethod
    def _read_xianyan_banquet_counts(cls, context: Any, frame: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for banquet_name, count_shape, _select_shape in XIANYAN_BANQUET_TYPES:
            values, text = context.ocr_numbers_in_shapes(
                649,
                [count_shape],
                frame_data_url=frame,
                crop=True,
            )
            if len(values) != 1 or values[0] < 0:
                raise RuntimeError(f"{banquet_name}库存无法形成唯一非负整数：{text!r}")
            counts[banquet_name] = int(values[0])
        return counts

    @staticmethod
    def _read_xianyan_remaining_seconds(context: Any, frame: str) -> int | None:
        """Read the active banquet countdown from full-frame OCR lines."""

        tokens = context.full_frame_ocr_tokens(frame)
        lines: dict[str, list[dict[str, Any]]] = {}
        for token in tokens:
            line_id = str(token.get("parent_line_id") or "")
            if line_id:
                lines.setdefault(line_id, []).append(token)
        matches: list[int] = []
        for line in lines.values():
            text = "".join(
                str(token.get("text") or "")
                for token in sorted(line, key=lambda token: int(token.get("order") or 0))
            )
            if "剩余时间" not in text:
                continue
            match = re.search(r"(\d{1,2})\s*[:：]\s*(\d{1,2})\s*[:：]\s*(\d{1,2})", text)
            if match:
                hours, minutes, seconds = (int(value) for value in match.groups())
                if minutes < 60 and seconds < 60:
                    matches.append(hours * 3600 + minutes * 60 + seconds)
        if len(matches) > 1:
            raise RuntimeError(f"仙宴剩余时间无法形成唯一倒计时：{matches}")
        return matches[0] if matches else None

    def _schedule_xianyan_rewards_from_frame(
        self,
        context: Any,
        frame: str,
        *,
        settle_padding_seconds: int = 30,
        schedule: bool = True,
    ) -> str | None:
        remaining = self._read_xianyan_remaining_seconds(context, frame)
        if remaining is None:
            if schedule:
                self._persist_scheduler_task_next_time(XIANYAN_REWARDS_TASK_ID, None)
            return None
        next_time = datetime.now() + timedelta(
            seconds=max(0, remaining) + max(10, int(settle_padding_seconds))
        )
        next_time_text = next_time.strftime("%Y-%m-%d %H:%M:%S")
        if schedule:
            self._persist_scheduler_task_next_time(XIANYAN_REWARDS_TASK_ID, next_time_text)
        return next_time_text

    def _claim_available_xianyan_rewards(
        self,
        context: Any,
        stop_event: threading.Event,
        *,
        max_rounds: int,
        settle_seconds: float,
        wait_timeout: float,
    ):
        claimed = 0
        for _round_index in range(max_rounds):
            self._raise_if_stopped(stop_event)
            # #423 is a formal Layer2 result scene, but its click authority is
            # local to this reward transaction.  Keep it in this explicit
            # Layer0 set; default recognition must never turn generic
            # ``点击屏幕继续`` pages into an仙宴 action.
            _wait_scene_match = yield from context.wait_scene([422, 423, 642, 659], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id == 659:
                # A previous attempt may have already consumed the reward but
                # stopped on the full-screen result overlay.  Dismiss it
                # idempotently without counting a second reward.
                yield from context.wait_click(659, "点击屏幕继续")
                yield from context.wait_action_settle(settle_seconds)
                continue
            if scene_id == 423:
                yield from context.wait_click(423, "结算继续")
                # 已经提交过的结果页只关闭；重入不能再次累计领奖数。
                yield from context.wait_action_settle(settle_seconds)
                continue
            if scene_id != 422:
                return claimed, scene_id, frame
            yield from context.wait_click(422, "领取宴席奖励")
            next_scene = yield from context.wait_scene(
                [423,
                642,
                659],
                wait=wait_timeout,
                label="仙宴_获得奖励：等待奖励层或幂等返回",
            )
            next_scene = self._xianyan_scene_id(next_scene)
            if next_scene == 659:
                yield from context.wait_click(659, "点击屏幕继续")
                claimed += 1
                yield from context.wait_action_settle(settle_seconds)
                continue
            if next_scene == 642:
                # The server can consume an already-mature reward and return to
                # the banquet home without rendering #423.  That is a terminal
                # idempotent success, not a reason to retry the same reward.
                # Keep the scene proven by wait_scene: an immediate second
                # recognition can hit the short home-page refresh animation and
                # incorrectly turn this known #642 landing into None.
                claimed += 1
                return claimed, 642, frame
            yield from context.wait_click(423, "结算继续")
            claimed += 1
            yield from context.wait_action_settle(settle_seconds)
        raise RuntimeError(f"仙宴_获得奖励：达到最大轮数 {max_rounds}，仍有奖励可领取")

    def _xianyan_attempt_runtime(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        asset_tree_path = ctx.get("asset_tree_path")
        return self._behavior_tree_context(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )

    @staticmethod
    def _read_xianyan_gift_inventory(item_id: int) -> int:
        """Read one gift's exact stock from the loaded backpack model."""

        from backend.core.fanxiu.instrumentation.backpack import (
            read_backpack_item_counts,
        )

        counts, _state = read_backpack_item_counts(
            [int(item_id)],
            manager_key="xianyan-gifts",
            force_refresh=True,
        )
        return int(counts.get(int(item_id)) or 0)

    def _execute_xianyan_host_baihua_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """Start a new Job attempt and host every available banquet."""

        context = self._xianyan_attempt_runtime(ctx, stop_event)
        yield from context.go_scene(34)
        return (yield from self._execute_xianyan_host_baihua_same_attempt(
            context, stop_event, payload
        ))

    def _execute_xianyan_host_baihua_same_attempt(
        self,
        context: Any,
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """Host all materials inside the caller's current Job attempt."""

        max_rounds = max(1, min(100, int(payload.get("max_rounds") or 50)))
        claimed = 0
        _wait_scene_match = yield from context.wait_scene([422, 423, 642, 649, 650, 659, 660], wait=5.0, required=False)
        (scene_id, score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id == 650:
            raise RuntimeError("百花宴作业从未归属的确认事务启动，拒绝重复确认")
        if scene_id not in {422, 423, 642, 649, 659, 660} or float(score or 0) < 80.0:
            yield from context.go_scene(20)
            _wait_scene_match = yield from context.wait_scene([20], wait=5.0, required=False)
            (entry_scene, entry_score, entry_frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if entry_scene != 20 or float(entry_score or 0.0) < 80.0:
                raise RuntimeError("仙宴举办：未能可靠到达绿瓶 #20，拒绝探测动态活动槽位")
            if not self._xianyan_entry_is_visible(context, entry_frame):
                message = "仙宴举办幂等结束：绿瓶当前没有仙园游宴入口"
                context.set_completion_message(message)
                self._log("success", message)
                return "success"
            yield from self._open_xianyan_entry(context, entry_frame)
            yield from context.wait_click(630, "园中仙宴", timeout=10.0)
            scene_id = yield from context.wait_scene(
                [631, 422, 423, 642], wait=20.0, label="百花宴：等待园中仙宴"
            )
            scene_id = self._xianyan_scene_id(scene_id)
            if scene_id == 631:
                message = "百花宴幂等结束：园中仙宴当前未开放"
                context.set_completion_message(message)
                self._log("success", message)
                return "success"

        if scene_id in {422, 423}:
            claimed, scene_id, _frame = yield from self._claim_available_xianyan_rewards(
                context,
                stop_event,
                max_rounds=100,
                settle_seconds=1.0,
                wait_timeout=15.0,
            )
            if scene_id != 642:
                scene_id = yield from context.wait_scene(
                    [642], wait=15.0, label="百花宴：领奖后等待当前仙宴"
                )
                scene_id = self._xianyan_scene_id(scene_id)

        # 历史宴席结束时，界面可能在仙宴专属结算 #659 和
        # 误点容错弹窗 #660 之间往复；回到 #642 后再点“举办仙宴”
        # 仍可能取出下一份结算。#620 是仙侣结算，虽有相同文案，
        # 不得作为仙宴候选场景。
        for _settle_round in range(100):
            if scene_id == 649:
                break
            if scene_id == 659:
                yield from context.wait_click(659, "点击屏幕继续")
                scene_id = self._xianyan_scene_id(
                    (yield from context.wait_scene(
                        [422, 642, 659, 660],
                        wait=20.0,
                        label="仙宴：关闭圆满奖励后等待落点",
                    ))
                )
                continue
            if scene_id == 660:
                yield from context.wait_click(660, "关闭详情")
                scene_id = self._xianyan_scene_id(
                    (yield from context.wait_scene(
                        [422, 642, 659, 660],
                        wait=20.0,
                        label="仙宴：关闭随礼详情后等待落点",
                    ))
                )
                continue
            if scene_id in {422, 423}:
                claimed_after_result, scene_id, _frame = yield from self._claim_available_xianyan_rewards(
                    context,
                    stop_event,
                    max_rounds=100,
                    settle_seconds=1.0,
                    wait_timeout=15.0,
                )
                claimed += claimed_after_result
                continue
            if scene_id == 642:
                yield from context.wait_click(642, "举办仙宴", timeout=10.0)
                # #642 can be a global fallback during the load; only #649/#659
                # prove the picker or next settlement.  A timeout preserves the
                # scene and raises instead of concluding "no banquet".
                scene_id = yield from self._wait_xianyan_exact_scene(
                    context,
                    659,
                    423,
                    649,
                    timeout=15.0,
                    label="百花宴：等待选择层或下一份结算",
                )
                continue
            raise RuntimeError(f"仙宴结算收口遇到未支持场景 #{scene_id}")
        else:
            raise RuntimeError("仙宴结算链在有界次数内未收敛到宴席选择层")

        hosted = {banquet_name: 0 for banquet_name, _count_shape, _select_shape in XIANYAN_BANQUET_TYPES}
        frame = (yield from context.wait_scene([649], wait=5.0)).frame_data_url
        before = self._read_xianyan_banquet_counts(context, frame)
        if sum(before.values()) == 0:
            yield from context.wait_click_then_scene(
                649, "关闭", 642, timeout=10.0, settle_seconds=1.0,
                label="仙宴举办幂等结束：关闭选择层",
            )
            message = "仙宴举办幂等结束：百花宴、龙凤宴、瑶星宴食材均为 0"
            context.set_completion_message(message)
            self._log("success", message)
            return "success"

        for _round_index in range(max_rounds):
            self._raise_if_stopped(stop_event)
            selected = next(
                item for item in XIANYAN_BANQUET_TYPES if before[item[0]] > 0
            )
            banquet_name, _count_shape, select_shape = selected
            yield from context.wait_click(649, select_shape, timeout=10.0)
            yield from context.wait_scene([650], wait=10.0, label=f"{banquet_name}：等待确认")
            yield from context.wait_click(650, "确认", timeout=10.0)
            yield from context.wait_scene([642], wait=15.0, label=f"{banquet_name}：确认举办成功")
            hosted[banquet_name] += 1
            # Verify the ledger after every confirmed hosting, including the
            # final unit: reopen #649 and require exactly one chosen type to
            # drop by 1 with the other two unchanged.  This replaces the old
            # ``sum == 1`` shortcut that never re-read the last stock.
            yield from context.wait_click(642, "举办仙宴", timeout=10.0)
            yield from context.wait_scene([649], wait=10.0, label="仙宴：重新读取库存")
            frame = (yield from context.wait_scene([649], wait=5.0)).frame_data_url
            after = self._read_xianyan_banquet_counts(context, frame)
            expected = dict(before)
            expected[banquet_name] -= 1
            if after != expected:
                raise RuntimeError(f"仙宴库存增量异常：{before} -> {after}，本轮={banquet_name}")
            before = after
            if sum(before.values()) == 0:
                # Zero stock: close the picker back to #642, then reuse the
                # existing reward/countdown handling.
                yield from context.wait_click_then_scene(
                    649, "关闭", 642, timeout=10.0, settle_seconds=1.0,
                    label="仙宴举办：库存耗尽关闭选择层",
                )
                claimed_after, _scene_id, frame = yield from self._claim_available_xianyan_rewards(
                    context,
                    stop_event,
                    max_rounds=100,
                    settle_seconds=1.0,
                    wait_timeout=15.0,
                )
                claimed += claimed_after
                next_time = (self._schedule_xianyan_rewards_from_frame(context, frame)
                             if payload.get("schedule_rewards", True)
                             and payload.get("schedule", True) else None)
                schedule_text = f"，下次领奖 {next_time}" if next_time else ""
                hosted_text = "、".join(
                    f"{name} {count} 场" for name, count in hosted.items() if count
                )
                message = (
                    f"仙宴举办完整闭环：本次举办 {hosted_text}、领取 {claimed} 轮，"
                    f"三类食材均已耗尽{schedule_text}"
                )
                context.set_completion_message(message)
                self._log("success", message)
                return "success"

        # Bounded budget exhausted while stock remains.
        if bool(payload.get("rnd_bounded")):
            remaining_text = "、".join(
                f"{name} {before[name]}" for name, _c, _s in XIANYAN_BANQUET_TYPES
            )
            message = (
                f"仙宴举办 R&D 有界预算用尽（{max_rounds} 轮），"
                f"剩余库存：{remaining_text}"
            )
            context.set_completion_message(message)
            self._log("warning", message)
            return "pending"
        raise RuntimeError(f"仙宴达到最大轮数 {max_rounds}，库存仍为 {before}")

    def _execute_xianyan_rewards_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """Start a new Job attempt and claim every available banquet reward."""

        context = self._xianyan_attempt_runtime(ctx, stop_event)
        yield from context.go_scene(34)
        return (yield from self._execute_xianyan_rewards_same_attempt(
            context, stop_event, payload
        ))

    def _execute_xianyan_rewards_same_attempt(
        self,
        context: Any,
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """Claim rewards inside the caller's current Job attempt."""

        max_rounds = max(1, min(500, int(payload.get("max_rounds") or 100)))
        settle_seconds = max(0.2, min(5.0, float(payload.get("settle_seconds") or 1.0)))
        wait_timeout = max(2.0, min(60.0, float(payload.get("wait_timeout") or 15.0)))
        _wait_scene_match = yield from context.wait_scene([422, 423, 642, 659], wait=5.0, required=False)
        (scene_id, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id not in {422, 423, 642, 659}:
            yield from context.go_scene(20)
            _wait_scene_match = yield from context.wait_scene([20], wait=5.0, required=False)
            (entry_scene, entry_score, entry_frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if entry_scene != 20 or float(entry_score or 0.0) < 80.0:
                raise RuntimeError("仙宴_获得奖励：未能可靠到达绿瓶 #20，拒绝探测动态活动槽位")
            if not self._xianyan_entry_is_visible(context, entry_frame):
                message = "仙宴_获得奖励幂等结束：绿瓶当前没有仙园游宴入口"
                context.set_completion_message(message)
                self._log("success", message)
                return "success"
            yield from self._open_xianyan_entry(context, entry_frame)
            yield from context.wait_click(630, "园中仙宴", timeout=10.0)
            scene_id = yield from context.wait_scene(
                [631, 422, 423, 642, 659], wait=20.0, label="仙宴_获得奖励：等待园中仙宴"
            )
            scene_id = self._xianyan_scene_id(scene_id)
            if scene_id == 631:
                message = "仙宴_获得奖励幂等结束：园中仙宴当前未开放"
                context.set_completion_message(message)
                self._log("success", message)
                return "success"

        claimed, _scene_id, frame = yield from self._claim_available_xianyan_rewards(
            context,
            stop_event,
            max_rounds=max_rounds,
            settle_seconds=settle_seconds,
            wait_timeout=wait_timeout,
        )
        next_time = self._schedule_xianyan_rewards_from_frame(
            context, frame, schedule=bool(payload.get("schedule", True))
        )
        if claimed:
            message = f"仙宴_获得奖励：完成，共领取 {claimed} 轮"
        else:
            message = "仙宴_获得奖励：当前没有可领取奖励"
        if next_time:
            message += f"，下次 {next_time}"
        context.set_completion_message(message)
        self._log("success", message)
        return "success"

    def _prepare_xianyan_white_gift(self, context: Any, stop_event: threading.Event):
        """Reach the #653 white-gift selection state without submitting.

        Strict, guard-checked preparation chain and the only sanctioned entry
        right now:

            #642 (园中仙宴主页, 校验场景)
              -> wait_click「参与宴会」 -> 严格等 #651
            #651 (参与仙宴列表)
              -> 确保「仅显示接受碧螺春」已勾（已勾只读不点）
              -> wait_click「第一个查看」-> 严格等 #652
            #652 (快捷详情)
              -> wait_click「参与仙宴」-> 严格等 #653
            #653 (礼物选择)
              -> 确保「随礼·白玉酿」勾选、确保「记住选择」勾选（已勾只读不点）

        Returns ``"prepared"``; it never clicks 参与仙宴/确定 on #653, never
        consumes a gift, and never writes next_time.  It starts only from one of
        #642/#651/#652/#653 and fails closed on any other scene; it never routes
        through #20/#34.  #653 is an activity-specific layer and must not be
        generalized to a shared #47/弹窗 handler.
        """

        self._raise_if_stopped(stop_event)
        entry_scene = yield from self._wait_xianyan_exact_scene(
            context, 642, 651, 652, 653, timeout=8.0, label="仙宴_准备：识别起点"
        )
        if entry_scene == 642:
            yield from context.wait_click(642, "参与宴会", timeout=10.0)
            entry_scene = yield from self._wait_xianyan_exact_scene(
                context, 651, timeout=20.0, label="仙宴_准备：进入参与列表"
            )
        if entry_scene == 651:
            # Default filter ON: ensure 仅显示接受碧螺春 is checked; if already
            # checked, only read it (no click).
            yield from self._xianyan_ensure_shape_checked(
                context,
                scene_id=651,
                toggle_shape="仅显示接受碧螺春",
                check_shape="仅显示接受碧螺春",
            )
            yield from context.wait_click(651, "第一个查看", timeout=10.0)
            entry_scene = yield from self._wait_xianyan_exact_scene(
                context, 652, timeout=20.0, label="仙宴_准备：进入快捷详情"
            )
        if entry_scene == 652:
            yield from context.wait_click(652, "参与仙宴", timeout=10.0)
            entry_scene = yield from self._wait_xianyan_exact_scene(
                context, 653, timeout=20.0, label="仙宴_准备：进入礼物选择"
            )
        if entry_scene != 653:
            raise RuntimeError(f"仙宴_准备：未到达 #653 礼物选择层：#{entry_scene}")
        # Check 白玉酿 and 记住选择; already-checked boxes are read-only.  Do not
        # click 参与仙宴/确定 and do not consume anything.
        yield from self._xianyan_ensure_shape_checked(
            context,
            scene_id=653,
            toggle_shape="随礼·白玉酿",
            check_shape="随礼·白玉酿",
        )
        yield from self._xianyan_ensure_shape_checked(
            context,
            scene_id=653,
            toggle_shape="记住选择",
            check_shape="记住选择",
        )
        self._log(
            "success",
            "仙宴_准备：已达 #653，白玉酿与记住选择已勾选；仅准备完成，未送礼",
        )
        return "prepared"

    def _execute_xianyan_participation_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """Start a new Job attempt and drain one same-tier gift round."""

        context = self._xianyan_attempt_runtime(ctx, stop_event)
        yield from context.go_scene(34)
        return (yield from self._execute_xianyan_participation_same_attempt(
            context, stop_event, payload
        ))

    def _execute_xianyan_participation_same_attempt(
        self, context: Any, stop_event: threading.Event, payload: dict[str, Any],
    ):
        """Quality policy is shared with the complete cycle: b before a."""
        from .xianyan_cycle import execute_xianyan_gifts

        result = yield from execute_xianyan_gifts(
            self, context, stop_event,
            max_batches=max(1, min(100, int(payload.get("max_batches") or 60))),
        )
        if result["status"] != "round_complete":
            return "pending"
        yield from context.go_scene(34)
        context.set_completion_message(f"仙宴随礼本轮完成：{result}")
        return "success"

    def _execute_xianyan_clean_task(
        self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any],
    ):
        """One idempotent round; residual stock is valid when no suitable tables remain."""
        from .xianyan_cycle import execute_xianyan_cycle

        context = self._xianyan_attempt_runtime(ctx, stop_event)
        result = yield from execute_xianyan_cycle(
            self, context, stop_event,
            max_batches=max(1, min(100, int(payload.get("max_batches") or 60))),
        )
        context.set_completion_message(f"园中仙宴本轮：{result}")
        return "success" if result["status"] == "round_complete" else "pending"


def execute_xianyan_stage(
    runner: Any,
    context: Any,
    stop_event: threading.Event,
    *,
    stage: str,
    payload: dict[str, Any] | None = None,
):
    """Thin public entry that reuses one existing 仙宴 same-attempt component.

    ``stage`` is one of ``prepare``/``host``/``participate``/``rewards`` and is
    dispatched to the existing helpers.  This wrapper itself adds no new task
    and no new navigation step; it does not prepend ``go_scene(34)``.  The
    reused components keep their own existing behavior, which may still return
    to #34 (for example the participate/rewards flows return through #34), so
    callers must not rely on this wrapper to preserve the frame.  ``prepare``
    only reaches the #653 prepared state and never submits (see
    ``_prepare_xianyan_white_gift``).  When ``payload["rnd_bounded"]`` is set the
    host stage returns ``pending`` (with a completion message naming the
    remaining stock) instead of raising when its bounded budget is exhausted;
    the formal branch keeps the existing raise.
    """

    stage_name = str(stage or "").strip().lower()
    method_names = {
        "host": "_execute_xianyan_host_baihua_same_attempt",
        "participate": "_execute_xianyan_participation_same_attempt",
        "rewards": "_execute_xianyan_rewards_same_attempt",
        "prepare": "_prepare_xianyan_white_gift",
    }
    method_name = method_names.get(stage_name)
    if method_name is None:
        raise ValueError(f"仙宴阶段只允许 prepare/host/participate/rewards：{stage}")
    options = dict(payload or {})
    runner._log("detail", f"仙宴阶段：复用现有组件执行 stage={stage_name}")
    if stage_name == "prepare":
        # Prepare takes no payload; it returns "prepared" and never consumes.
        return (yield from getattr(runner, method_name)(context, stop_event))
    return (yield from getattr(runner, method_name)(context, stop_event, options))


def execute_xianyan_white_gift_round(
    runner: Any, context: Any, stop_event: threading.Event, *, max_actions: int = 10,
):
    """Compatibility R&D entry, now using the sole quality-aware gift component.

    A short round authorizes at most one batch; its first submission verifies
    the remembered gift. Remaining high gifts always run first.
    """
    from .xianyan_cycle import execute_xianyan_gifts

    return (yield from execute_xianyan_gifts(runner, context, stop_event, max_batches=1))


def execute_xianyan_white_gift_preparation(
    runner: Any,
    context: Any,
    stop_event: threading.Event,
):
    """Public entry: reach #653 with 白玉酿 prepared, without submitting.

    Thin wrapper over ``_prepare_xianyan_white_gift``.  It preserves the live
    scene (no #20/#34 routing), returns ``"prepared"``, and never consumes a
    gift nor writes next_time.  See the mixin method for the strict scene chain.
    """

    return (
        yield from execute_xianyan_stage(
            runner, context, stop_event, stage="prepare"
        )
    )
