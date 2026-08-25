from __future__ import annotations

from datetime import datetime, timedelta
import io
import re
import threading
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
XIANYAN_MATCHING_GIFTS = {
    "百花宴": "随礼·碧螺春",
    "龙凤仙宴": "随礼·白玉酿",
    "瑶星宴": "随礼·醉仙酿",
}


class XianyanTaskMixin:
    @staticmethod
    def _xianyan_ocr_text(tokens: list[dict[str, Any]]) -> str:
        return "".join(str(item.get("text") or "") for item in tokens).replace(" ", "")

    @classmethod
    def _xianyan_entry_is_visible(cls, runtime: Any, frame: str) -> bool:
        """Require the live activity label before using the time-varying #20 slot."""

        return "仙园游宴" in cls._xianyan_ocr_text(runtime.full_frame_ocr_tokens(frame))

    def _open_xianyan_entry(self, runtime: Any, frame: str):
        """Click the moving #20 activity entry from its live OCR token box."""

        match = runtime.click_ocr_text(
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
        return (yield from runtime.wait_scene(
            630,
            timeout=30.0,
            label="仙园游宴：等待活动主页",
        ))

    @staticmethod
    def _xianyan_detail_state(text: str) -> dict[str, int | str | None]:
        """Parse the shortcut detail without inferring a spend from a transition."""

        normalized = str(text or "").replace(" ", "")
        remaining_match = re.search(r"剩余(\d+)位道友", normalized)
        white_match = re.search(r"白玉酿[（(](\d+)[）)]", normalized)
        if "已赴宴" in normalized:
            state = "attended"
        elif "赴宴礼物" in normalized and "白玉酿" in normalized:
            state = "white_ready"
        elif "拥有可用礼物" in normalized:
            state = "picker_required"
        else:
            state = "unknown"
        return {
            "state": state,
            "white_count": int(white_match.group(1)) if white_match else None,
            "remaining": int(remaining_match.group(1)) if remaining_match else None,
        }

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
        runtime: Any,
        frame: str,
        box: tuple[float, float, float, float],
    ) -> bool:
        raw = runtime.runner._decode_frame_data_url(frame)
        with Image.open(io.BytesIO(raw)) as source:
            return cls._xianyan_green_checkbox_ratio(source, box) >= 0.01

    @staticmethod
    def _xianyan_scene_id(value: Any) -> int | None:
        if value is None:
            return None
        scene_id = getattr(value, "id", value)
        try:
            return int(scene_id)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _matching_xianyan_gift(banquet_name: str) -> str:
        """Return the only same-quality gift allowed by the user's banquet policy."""

        normalized = str(banquet_name or "").strip()
        try:
            return XIANYAN_MATCHING_GIFTS[normalized]
        except KeyError as exc:
            raise RuntimeError(f"未知仙宴品质，拒绝猜测随礼：{banquet_name!r}") from exc

    @classmethod
    def _matching_xianyan_gift_is_allowed(
        cls,
        banquet_name: str,
        gift_rows: dict[str, bool],
    ) -> bool:
        """Fail closed unless the host explicitly accepts the same-quality gift."""

        matching_gift = cls._matching_xianyan_gift(banquet_name)
        return gift_rows.get(matching_gift) is True

    @staticmethod
    def _xianyan_participation_candidates(
        fragments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Parse visible banquet cards while preserving each 查看 OCR occurrence."""

        views = [item for item in fragments if "查看" in str(item.get("text") or "")]
        candidates: list[dict[str, Any]] = []
        for occurrence, view in enumerate(views):
            center_y = float(view.get("y") or 0) + float(view.get("h") or 0) / 2
            row = [
                item
                for item in fragments
                if abs(
                    float(item.get("y") or 0)
                    + float(item.get("h") or 0) / 2
                    - center_y
                )
                <= 90
            ]
            text = "".join(str(item.get("text") or "") for item in row)
            banquet_name = next(
                (name for name in XIANYAN_MATCHING_GIFTS if name in text),
                "",
            )
            guest_match = re.search(r"(?:宾客数|与宴人数)\s*[:：]?\s*(\d)\s*/\s*5", text)
            stable_text = re.sub(r"\d{1,2}\s*[:：]\s*\d{1,2}\s*[:：]\s*\d{1,2}", "", text)
            stable_text = re.sub(r"(?:宾客数|与宴人数)\s*[:：]?\s*\d\s*/\s*5", "", stable_text)
            stable_text = re.sub(r"\s+", "", stable_text)
            candidates.append({
                "occurrence": occurrence,
                "banquet_name": banquet_name,
                "guest_count": int(guest_match.group(1)) if guest_match else None,
                "ended": "已经结束" in text,
                "text": text,
                "stable_key": stable_text,
            })
        return candidates

    @staticmethod
    def _xianyan_gift_rows_from_fragments(
        fragments: list[dict[str, Any]],
    ) -> dict[str, bool]:
        lines: list[list[dict[str, Any]]] = []
        for item in sorted(fragments, key=lambda value: float(value.get("y") or 0)):
            center_y = float(item.get("y") or 0) + float(item.get("h") or 0) / 2
            line = next(
                (
                    row for row in lines
                    if abs(
                        sum(float(token.get("y") or 0) + float(token.get("h") or 0) / 2 for token in row)
                        / len(row)
                        - center_y
                    ) <= 45
                ),
                None,
            )
            if line is None:
                lines.append([item])
            else:
                line.append(item)
        result = {gift_name: False for gift_name in XIANYAN_MATCHING_GIFTS.values()}
        for row in lines:
            text = "".join(str(item.get("text") or "") for item in row).replace("·", "")
            for gift_name in result:
                if gift_name.replace("·", "") in text:
                    result[gift_name] = "不允许" not in text
        return result

    @staticmethod
    def _read_baihua_banquet_count(runtime: Any, frame: str) -> int:
        values, text = runtime.ocr_numbers_in_shapes(
            649,
            ["百花宴拥有数量"],
            frame_data_url=frame,
            crop=True,
        )
        if len(values) != 1 or values[0] < 0:
            raise RuntimeError(f"百花宴库存无法形成唯一非负整数：{text!r}")
        return int(values[0])

    @classmethod
    def _read_xianyan_banquet_counts(cls, runtime: Any, frame: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for banquet_name, count_shape, _select_shape in XIANYAN_BANQUET_TYPES:
            values, text = runtime.ocr_numbers_in_shapes(
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
    def _read_xianyan_remaining_seconds(runtime: Any, frame: str) -> int | None:
        """Read the active banquet countdown from full-frame OCR lines."""

        tokens = runtime.full_frame_ocr_tokens(frame)
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
        runtime: Any,
        frame: str,
        *,
        settle_padding_seconds: int = 30,
    ) -> str | None:
        remaining = self._read_xianyan_remaining_seconds(runtime, frame)
        if remaining is None:
            self._persist_scheduler_task_next_time(XIANYAN_REWARDS_TASK_ID, None)
            return None
        next_time = datetime.now() + timedelta(
            seconds=max(0, remaining) + max(10, int(settle_padding_seconds))
        )
        next_time_text = next_time.strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(XIANYAN_REWARDS_TASK_ID, next_time_text)
        return next_time_text

    def _claim_available_xianyan_rewards(
        self,
        runtime: Any,
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
            scene_id, _score, frame = runtime.current_scene([422, 423, 642, 659], update=True)
            if scene_id == 659:
                # A previous attempt may have already consumed the reward but
                # stopped on the full-screen result overlay.  Dismiss it
                # idempotently without counting a second reward.
                yield from runtime.wait_click(659, "点击屏幕继续")
                yield from runtime.wait_action_settle(settle_seconds)
                continue
            if scene_id == 423:
                yield from runtime.wait_click(423, "继续")
                claimed += 1
                yield from runtime.wait_action_settle(settle_seconds)
                continue
            if scene_id != 422:
                return claimed, scene_id, frame
            yield from runtime.wait_click(422, "获得奖励")
            next_scene = yield from runtime.wait_view(
                423,
                642,
                659,
                timeout=wait_timeout,
                label="仙宴_获得奖励：等待奖励层或幂等返回",
            )
            next_scene = self._xianyan_scene_id(next_scene)
            if next_scene == 659:
                yield from runtime.wait_click(659, "点击屏幕继续")
                claimed += 1
                yield from runtime.wait_action_settle(settle_seconds)
                continue
            if next_scene == 642:
                # The server can consume an already-mature reward and return to
                # the banquet home without rendering #423.  That is a terminal
                # idempotent success, not a reason to retry the same reward.
                # Keep the scene proven by wait_view: an immediate second
                # recognition can hit the short home-page refresh animation and
                # incorrectly turn this known #642 landing into None.
                claimed += 1
                return claimed, 642, frame
            yield from runtime.wait_click(423, "继续")
            claimed += 1
            yield from runtime.wait_action_settle(settle_seconds)
        raise RuntimeError(f"仙宴_获得奖励：达到最大轮数 {max_rounds}，仍有奖励可领取")

    def _xianyan_attempt_runtime(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        asset_tree_path = ctx.get("asset_tree_path")
        return self._fanxiu_runtime(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )

    def _execute_xianyan_host_baihua_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """Start a new Job attempt and host every available banquet."""

        runtime = self._xianyan_attempt_runtime(ctx, stop_event)
        yield from runtime.go_scene(34)
        return (yield from self._execute_xianyan_host_baihua_same_attempt(
            runtime, stop_event, payload
        ))

    def _execute_xianyan_host_baihua_same_attempt(
        self,
        runtime: Any,
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """Host all materials inside the caller's current Job attempt."""

        max_rounds = max(1, min(100, int(payload.get("max_rounds") or 50)))
        claimed = 0
        scene_id, score, _frame = runtime.current_scene(
            [422, 423, 642, 649, 650, 659, 660], update=True
        )
        if scene_id == 650:
            raise RuntimeError("百花宴作业从未归属的确认事务启动，拒绝重复确认")
        if scene_id not in {422, 423, 642, 649, 659, 660} or float(score or 0) < 80.0:
            yield from runtime.go_scene(20)
            entry_scene, entry_score, entry_frame = runtime.current_scene([20], update=True)
            if entry_scene != 20 or float(entry_score or 0.0) < 80.0:
                raise RuntimeError("仙宴举办：未能可靠到达绿瓶 #20，拒绝探测动态活动槽位")
            if not self._xianyan_entry_is_visible(runtime, entry_frame):
                message = "仙宴举办幂等结束：绿瓶当前没有仙园游宴入口"
                runtime.set_completion_message(message)
                self._log("success", message)
                return "success"
            yield from self._open_xianyan_entry(runtime, entry_frame)
            yield from runtime.wait_click(630, "园中仙宴", timeout=10.0)
            scene_id = yield from runtime.wait_view(
                631, 422, 423, 642, timeout=20.0, label="百花宴：等待园中仙宴"
            )
            scene_id = self._xianyan_scene_id(scene_id)
            if scene_id == 631:
                message = "百花宴幂等结束：园中仙宴当前未开放"
                runtime.set_completion_message(message)
                self._log("success", message)
                return "success"

        if scene_id in {422, 423}:
            claimed, scene_id, _frame = yield from self._claim_available_xianyan_rewards(
                runtime,
                stop_event,
                max_rounds=100,
                settle_seconds=1.0,
                wait_timeout=15.0,
            )
            if scene_id != 642:
                scene_id = yield from runtime.wait_view(
                    642, timeout=15.0, label="百花宴：领奖后等待当前仙宴"
                )
                scene_id = self._xianyan_scene_id(scene_id)

        # 历史宴席结束时，界面可能在仙宴专属结算 #659 和
        # 误点容错弹窗 #660 之间往复；回到 #642 后再点“举办仙宴”
        # 仍可能取出下一份结算。#620 是仙侣结算，虽有相同文案，
        # 不得作为仙宴候选场景。
        for _settle_round in range(30):
            if scene_id == 649:
                break
            if scene_id == 659:
                yield from runtime.wait_click(659, "点击屏幕继续")
                scene_id = self._xianyan_scene_id(
                    (yield from runtime.wait_view(
                        422, 642, 659, 660,
                        timeout=20.0,
                        label="仙宴：关闭圆满奖励后等待落点",
                    ))
                )
                continue
            if scene_id == 660:
                yield from runtime.wait_click(660, "关闭详情")
                scene_id = self._xianyan_scene_id(
                    (yield from runtime.wait_view(
                        422, 642, 659, 660,
                        timeout=20.0,
                        label="仙宴：关闭随礼详情后等待落点",
                    ))
                )
                continue
            if scene_id in {422, 423}:
                claimed_after_result, scene_id, _frame = yield from self._claim_available_xianyan_rewards(
                    runtime,
                    stop_event,
                    max_rounds=100,
                    settle_seconds=1.0,
                    wait_timeout=15.0,
                )
                claimed += claimed_after_result
                continue
            if scene_id == 642:
                yield from runtime.wait_click(642, "举办仙宴", timeout=10.0)
                scene_id = self._xianyan_scene_id(
                    (yield from runtime.wait_view(
                        659, 649, 642,
                        timeout=15.0,
                        label="百花宴：等待选择层或下一份结算",
                    ))
                )
                if scene_id == 642:
                    message = (
                        "仙宴举办幂等结束：举办入口未打开选择层，"
                        "活动已结束或当前没有可举办宴席"
                    )
                    runtime.set_completion_message(message)
                    self._log("success", message)
                    return "success"
                continue
            raise RuntimeError(f"仙宴结算收口遇到未支持场景 #{scene_id}")
        else:
            raise RuntimeError("仙宴结算链在有界次数内未收敛到宴席选择层")

        hosted = {banquet_name: 0 for banquet_name, _count_shape, _select_shape in XIANYAN_BANQUET_TYPES}
        frame = runtime.current_scene([649], update=True, handle_interruptions=False)[2]
        before = self._read_xianyan_banquet_counts(runtime, frame)
        if sum(before.values()) == 0:
            yield from runtime.wait_click_then_view(
                649, "关闭", 642, timeout=10.0, settle_seconds=1.0,
                label="仙宴举办幂等结束：关闭选择层",
            )
            message = "仙宴举办幂等结束：百花宴、龙凤宴、瑶星宴食材均为 0"
            runtime.set_completion_message(message)
            self._log("success", message)
            return "success"

        for _round_index in range(max_rounds):
            self._raise_if_stopped(stop_event)
            selected = next(
                item for item in XIANYAN_BANQUET_TYPES if before[item[0]] > 0
            )
            banquet_name, _count_shape, select_shape = selected
            yield from runtime.wait_click(649, select_shape, timeout=10.0)
            yield from runtime.wait_view(650, timeout=10.0, label=f"{banquet_name}：等待确认")
            yield from runtime.wait_click(650, "确认", timeout=10.0)
            yield from runtime.wait_view(642, timeout=15.0, label=f"{banquet_name}：确认举办成功")
            hosted[banquet_name] += 1
            if sum(before.values()) == 1:
                claimed_after, _scene_id, frame = yield from self._claim_available_xianyan_rewards(
                    runtime,
                    stop_event,
                    max_rounds=100,
                    settle_seconds=1.0,
                    wait_timeout=15.0,
                )
                claimed += claimed_after
                next_time = self._schedule_xianyan_rewards_from_frame(runtime, frame)
                schedule_text = f"，下次领奖 {next_time}" if next_time else ""
                hosted_text = "、".join(
                    f"{name} {count} 场" for name, count in hosted.items() if count
                )
                message = (
                    f"仙宴举办完整闭环：本次举办 {hosted_text}、领取 {claimed} 轮，"
                    f"三类食材均已耗尽{schedule_text}"
                )
                runtime.set_completion_message(message)
                self._log("success", message)
                return "success"

            yield from runtime.wait_click(642, "举办仙宴", timeout=10.0)
            yield from runtime.wait_view(649, timeout=10.0, label="仙宴：重新读取库存")
            frame = runtime.current_scene([649], update=True, handle_interruptions=False)[2]
            after = self._read_xianyan_banquet_counts(runtime, frame)
            expected = dict(before)
            expected[banquet_name] -= 1
            if after != expected:
                raise RuntimeError(f"仙宴库存增量异常：{before} -> {after}，本轮={banquet_name}")
            before = after

        raise RuntimeError(f"仙宴达到最大轮数 {max_rounds}，库存仍为 {before}")

    def _execute_xianyan_rewards_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """Start a new Job attempt and claim every available banquet reward."""

        runtime = self._xianyan_attempt_runtime(ctx, stop_event)
        yield from runtime.go_scene(34)
        return (yield from self._execute_xianyan_rewards_same_attempt(
            runtime, stop_event, payload
        ))

    def _execute_xianyan_rewards_same_attempt(
        self,
        runtime: Any,
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """Claim rewards inside the caller's current Job attempt."""

        max_rounds = max(1, min(500, int(payload.get("max_rounds") or 100)))
        settle_seconds = max(0.2, min(5.0, float(payload.get("settle_seconds") or 1.0)))
        wait_timeout = max(2.0, min(60.0, float(payload.get("wait_timeout") or 15.0)))
        scene_id, _score, _frame = runtime.current_scene([422, 423, 642, 659], update=True)
        if scene_id not in {422, 423, 642, 659}:
            yield from runtime.go_scene(20)
            entry_scene, entry_score, entry_frame = runtime.current_scene([20], update=True)
            if entry_scene != 20 or float(entry_score or 0.0) < 80.0:
                raise RuntimeError("仙宴_获得奖励：未能可靠到达绿瓶 #20，拒绝探测动态活动槽位")
            if not self._xianyan_entry_is_visible(runtime, entry_frame):
                message = "仙宴_获得奖励幂等结束：绿瓶当前没有仙园游宴入口"
                runtime.set_completion_message(message)
                self._log("success", message)
                return "success"
            yield from self._open_xianyan_entry(runtime, entry_frame)
            yield from runtime.wait_click(630, "园中仙宴", timeout=10.0)
            scene_id = yield from runtime.wait_view(
                631, 422, 423, 642, 659, timeout=20.0, label="仙宴_获得奖励：等待园中仙宴"
            )
            scene_id = self._xianyan_scene_id(scene_id)
            if scene_id == 631:
                message = "仙宴_获得奖励幂等结束：园中仙宴当前未开放"
                runtime.set_completion_message(message)
                self._log("success", message)
                return "success"

        claimed, _scene_id, frame = yield from self._claim_available_xianyan_rewards(
            runtime,
            stop_event,
            max_rounds=max_rounds,
            settle_seconds=settle_seconds,
            wait_timeout=wait_timeout,
        )
        next_time = self._schedule_xianyan_rewards_from_frame(runtime, frame)
        if claimed:
            message = f"仙宴_获得奖励：完成，共领取 {claimed} 轮"
        else:
            message = "仙宴_获得奖励：当前没有可领取奖励"
        if next_time:
            message += f"，下次 {next_time}"
        runtime.set_completion_message(message)
        self._log("success", message)
        return "success"

    def _execute_xianyan_participation_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """Start a new Job attempt and drain one same-tier gift round."""

        runtime = self._xianyan_attempt_runtime(ctx, stop_event)
        yield from runtime.go_scene(34)
        return (yield from self._execute_xianyan_participation_same_attempt(
            runtime, stop_event, payload
        ))

    def _execute_xianyan_participation_same_attempt(
        self,
        runtime: Any,
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """Drain one gift round inside the caller's current Job attempt."""

        max_rounds = max(1, min(500, int(payload.get("max_rounds") or 100)))
        joined = 0
        viewed = 0
        pending_white_before: int | None = None
        scene_id, _score, _frame = runtime.current_scene([422, 423, 642, 651, 652, 653], update=True)
        if scene_id not in {422, 423, 642, 651, 652, 653}:
            yield from runtime.go_scene(20)
            entry_scene, entry_score, entry_frame = runtime.current_scene([20], update=True)
            if entry_scene != 20 or float(entry_score or 0.0) < 80.0:
                raise RuntimeError("仙宴_参与：未能可靠到达绿瓶 #20，拒绝探测动态活动槽位")
            if not self._xianyan_entry_is_visible(runtime, entry_frame):
                message = "仙宴_参与幂等结束：绿瓶当前没有仙园游宴入口"
                runtime.set_completion_message(message)
                self._log("success", message)
                return "success"
            yield from self._open_xianyan_entry(runtime, entry_frame)
            yield from runtime.wait_click(630, "园中仙宴", timeout=10.0)
            scene_id = yield from runtime.wait_view(
                631, 422, 423, 642, timeout=20.0, label="仙宴_参与：等待园中仙宴"
            )
            scene_id = self._xianyan_scene_id(scene_id)
            if scene_id == 631:
                message = "仙宴_参与幂等结束：园中仙宴当前未开放"
                runtime.set_completion_message(message)
                self._log("success", message)
                return "success"
        if scene_id in {422, 423}:
            _claimed, scene_id, _frame = yield from self._claim_available_xianyan_rewards(
                runtime, stop_event, max_rounds=100, settle_seconds=1.0, wait_timeout=15.0
            )
        if scene_id == 642:
            yield from runtime.wait_click_then_view(
                642, "参与宴会", 651, timeout=15.0, settle_seconds=1.0,
                label="仙宴_参与：等待宴会列表",
            )

        for _round_index in range(max_rounds):
            self._raise_if_stopped(stop_event)
            scene_id, _score, frame = runtime.current_scene([651, 652, 653], update=True)
            if scene_id == 651:
                filter_box = (0.24, 0.76, 0.08, 0.06)
                if not self._xianyan_checkbox_is_checked(runtime, frame, filter_box):
                    runtime.click_shape_center_fast(651, "仅显示接受碧螺春")
                    yield from runtime.wait_action_settle(1.0)
                    _scene_id, _score, frame = runtime.current_scene([651], update=True)
                    if not self._xianyan_checkbox_is_checked(runtime, frame, filter_box):
                        raise RuntimeError("仙宴_参与：最低礼物筛选勾选后未出现绿色勾")
                text = self._xianyan_ocr_text(runtime.full_frame_ocr_tokens(frame))
                if "查看" not in text:
                    break
                runtime.click_ocr_text(651, "查看", frame_data_url=frame, occurrence=0)
                yield from runtime.wait_view(652, timeout=15.0, label="仙宴_参与：等待快捷详情")
                continue

            if scene_id == 653:
                gift_text = self._xianyan_ocr_text(runtime.full_frame_ocr_tokens(frame))
                if "白玉酿" not in gift_text:
                    raise RuntimeError("仙宴_参与：礼物层未识别到白玉酿，拒绝猜测")
                if not self._xianyan_checkbox_is_checked(runtime, frame, (0.75, 0.395, 0.10, 0.075)):
                    runtime.click_shape_center_fast(653, "随礼·白玉酿")
                if not self._xianyan_checkbox_is_checked(runtime, frame, (0.82, 0.675, 0.08, 0.075)):
                    runtime.click_shape_center_fast(653, "记住选择")
                runtime.click_shape_center_fast(653, "参与仙宴")
                yield from runtime.wait_action_settle(1.0)
                _scene_id, _score, confirm_frame = runtime.current_scene([653], update=True)
                confirm_text = self._xianyan_ocr_text(runtime.full_frame_ocr_tokens(confirm_frame))
                if "白玉酿" not in confirm_text or "确定" not in confirm_text:
                    raise RuntimeError("仙宴_参与：未验证到白玉酿确认框，拒绝继续")
                runtime.click_shape_center_fast(653, "本次登录不再提醒")
                runtime.click_shape_center_fast(653, "确定使用白玉酿")
                yield from runtime.wait_action_settle(3.0)
                continue

            if scene_id != 652:
                raise RuntimeError(f"仙宴_参与：意外场景 {scene_id!r}")
            detail_text = self._xianyan_ocr_text(runtime.full_frame_ocr_tokens(frame))
            detail = self._xianyan_detail_state(detail_text)
            state = detail["state"]
            white_count = detail["white_count"]
            if pending_white_before is not None and isinstance(white_count, int):
                if white_count != pending_white_before - 1:
                    raise RuntimeError(
                        f"仙宴_参与：白玉酿数量未精确减一（{pending_white_before} -> {white_count}）"
                    )
                joined += 1
                pending_white_before = None
            if state == "attended":
                runtime.click_shape_center_fast(652, "查看下一个")
                viewed += 1
                yield from runtime.wait_action_settle(1.0)
                continue
            if state == "white_ready" and isinstance(white_count, int) and white_count > 0:
                pending_white_before = white_count
                runtime.click_shape_center_fast(652, "参与仙宴")
                yield from runtime.wait_action_settle(1.5)
                continue
            if state == "picker_required":
                runtime.click_shape_center_fast(652, "参与仙宴")
                yield from runtime.wait_view(653, timeout=10.0, label="仙宴_参与：首次选择白玉酿")
                continue
            if state == "white_ready" and white_count == 0:
                break
            raise RuntimeError(f"仙宴_参与：无法判定快捷详情状态：{detail_text!r}")

        scene_id, _score, _frame = runtime.current_scene([651], update=True)
        if scene_id == 651:
            yield from runtime.wait_click_then_view(
                651, "遮罩关闭", 642, timeout=10.0, settle_seconds=1.0,
                label="仙宴_参与：完成后关闭列表",
            )
            yield from runtime.go_scene(34)
        next_time = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=30)
        self._persist_scheduler_task_next_time(XIANYAN_CLEAN_TASK_ID, next_time)
        message = (
            f"仙宴_参与本轮完成：白玉酿 {joined} 份，查看 {viewed} 席；"
            f"当前轮次已无可查看宴席，下次 {next_time:%Y-%m-%d %H:%M:%S}"
        )
        runtime.set_completion_message(message)
        self._log("success", message)
        return "success"

    def _execute_xianyan_clean_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """Drain all currently actionable banquet rewards, materials and same-tier gifts."""

        runtime = self._xianyan_attempt_runtime(ctx, stop_event)
        yield from runtime.go_scene(34)
        yield from self._execute_xianyan_host_baihua_same_attempt(
            runtime, stop_event, payload
        )
        yield from self._execute_xianyan_participation_same_attempt(
            runtime, stop_event, payload
        )
        yield from self._execute_xianyan_rewards_same_attempt(
            runtime, stop_event, payload
        )
        message = "仙宴_清理完成：当前奖励、宴席材料和可同档随礼项均已处理干净"
        self._log("success", message)
        return "success"
