from __future__ import annotations

import time

import threading
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from types import GeneratorType
from typing import Any, Callable

from backend.core.fanxiu.gift_code_crawler import crawl_weekly_gift_codes


WEEKLY_GIFT_CODE_TASK_ID = "gift-code-weekly"
WEEKLY_GIFT_CODE_WEEKDAY = 1  # Python weekday: Tuesday; allow time for codes to be published.
WEEKLY_GIFT_CODE_TRIGGER_TIME = dt_time(0, 5)


def _now() -> datetime:
    return datetime.now()


def next_weekly_gift_code_trigger_at(now: datetime) -> datetime:
    """计算本次正常完成后的下周二 00:05。

    同一周周二无论几点完成，都推进到七天后的周二；其它日期推进到紧接着的
    下一个周二。

    :param datetime now: 本次 Job 正常完成时间。
    :return datetime: 下一周期的绝对触发时间。
    """

    days_until_trigger = (WEEKLY_GIFT_CODE_WEEKDAY - now.weekday()) % 7
    if days_until_trigger == 0:
        days_until_trigger = 7
    return datetime.combine(
        now.date() + timedelta(days=days_until_trigger),
        WEEKLY_GIFT_CODE_TRIGGER_TIME,
    )


class GiftCodeTaskMixin:
    """执行“每周_礼包码”的调度外壳与游戏兑换动作。"""

    @staticmethod
    def _gift_page_text_ready(text: str) -> bool:
        """用兑换窗口自身文案确认页面，避免被通用提示场景抢先识别。"""

        normalized = "".join(str(text or "").split())
        return (
            ("点击输入兑换码" in normalized or "请输入正确的兑换码" in normalized)
            and "兑换" in normalized
        )

    @staticmethod
    def _gift_input_confirm_point(
        fragments: list[dict[str, Any]],
        *,
        frame_width: float,
        frame_height: float,
    ) -> tuple[float, float]:
        """定位文本输入覆盖层右下方唯一的“确定”按钮。"""

        candidates: list[tuple[float, float]] = []
        for item in fragments:
            text = "".join(str(item.get("text") or "").split())
            x = float(item.get("x") or 0)
            y = float(item.get("y") or 0)
            w = float(item.get("w") or 0)
            h = float(item.get("h") or 0)
            center_x = x + w / 2
            center_y = y + h / 2
            if (
                text == "确定"
                and center_x >= frame_width * 0.7
                and center_y >= frame_height * 0.85
            ):
                candidates.append((center_x, center_y))
        if len(candidates) != 1:
            raise RuntimeError(f"礼包码输入：右下角‘确定’匹配到 {len(candidates)} 项，停止点击")
        return candidates[0]

    def _record_weekly_gift_code_done(
        self,
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> str:
        next_time = next_weekly_gift_code_trigger_at(now or _now()).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or WEEKLY_GIFT_CODE_TASK_ID),
            next_time,
        )
        return next_time

    def _fetch_weekly_gift_codes(self, stop_event: threading.Event) -> list[str]:
        result = crawl_weekly_gift_codes(
            check_cancel=lambda: self._raise_if_stopped(stop_event),
        )
        self._log(
            "info",
            f"每周_礼包码：论坛正文 {result.text_length} 字，解析到 {len(result.codes)} 个兑换码",
        )
        return list(result.codes)

    def _execute_weekly_gift_code_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """从论坛获取本周礼包码，依次兑换并推进到下周二 00:05。"""

        payload = dict(payload or {})
        raw_codes = payload.get("codes")
        codes: list[str] = []
        seen: set[str] = set()
        if isinstance(raw_codes, list):
            for raw_code in raw_codes:
                code = str(raw_code or "").strip()
                if code and code not in seen:
                    codes.append(code)
                    seen.add(code)
        if not codes:
            codes = self._fetch_weekly_gift_codes(stop_event)
        if not codes:
            raise RuntimeError("每周_礼包码：论坛未返回任何兑换码，不能按成功完成")

        completion: dict[str, str] = {}

        def record_codes_processed() -> str:
            """Persist the business completion time once, before best-effort departure."""

            if "next_time" not in completion:
                completion["next_time"] = self._record_weekly_gift_code_done(payload)
            return completion["next_time"]

        redeem_flow = self._execute_gift_code_task(
            ctx,
            codes,
            stop_event,
            on_codes_processed=record_codes_processed,
        )
        redeem_result: dict[str, Any] = {}
        if isinstance(redeem_flow, GeneratorType):
            returned = yield from redeem_flow
            if isinstance(returned, dict):
                redeem_result = returned
        elif isinstance(redeem_flow, dict):
            redeem_result = redeem_flow

        # Compatibility for a custom executor that returns success without
        # invoking the completion callback. The production executor records it
        # immediately after the last code, before attempting to leave #49.
        next_time = record_codes_processed()
        departure_warning = str(redeem_result.get("departure_warning") or "").strip()
        message = f"每周_礼包码：已完成 {len(codes)} 个兑换码，下次 {next_time}"
        if departure_warning:
            message = f"{message}；{departure_warning}"
        self._log("success", message)
        return {
            "result": "success",
            "message": message,
            "current_scene": int(redeem_result.get("current_scene") or 34),
            "code_count": len(codes),
            **({"departure_warning": departure_warning} if departure_warning else {}),
        }

    def _execute_gift_code_task(
        self,
        ctx: dict[str, Any],
        codes: list[str],
        stop_event: threading.Event,
        *,
        on_codes_processed: Callable[[], str] | None = None,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少每周_礼包码资产树路径，无法打开设置页")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "对齐 #49 设置页", phase="align_settings")
        yield from self._open_settings_page(context)
        return (
            yield from self._redeem_gift_codes_from_settings(
                ctx,
                context,
                codes,
                stop_event,
                on_codes_processed=on_codes_processed,
            )
        )

    def _redeem_gift_codes_from_settings(
        self,
        ctx: dict[str, Any],
        context: Any,
        codes: list[str],
        stop_event: threading.Event,
        *,
        on_codes_processed: Callable[[], str] | None = None,
    ) -> dict[str, Any]:
        """从已确认的 #49 兑换一批礼包码，再尽力安全回到 #34。"""

        for index, code in enumerate(codes):
            self._raise_if_stopped(stop_event)
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"处理第 {index + 1}/{len(codes)} 个：{code}",
                    current_index=index,
                    current_code=code,
                    phase="process_code",
                )
                self._log_locked("action", f"开始兑换：{code}")
            self._process_code(ctx, code, index == len(codes) - 1, stop_event)

        # From this point onward every code has already been submitted. Persist
        # the next business trigger before the unrelated departure side effect,
        # otherwise a missing #49 -> #34 route would make Scheduler repeat the
        # whole batch.
        if on_codes_processed is not None:
            on_codes_processed()
        with self._lock:
            self._set_status_locked("running", "礼包码处理完成，返回 #34", phase="finish_back")
        try:
            yield from self._leave_settings_page(context)
        except InterruptedError:
            # Cell cancellation is control flow, not a recoverable business
            # departure failure. GeneratorExit/KeyboardInterrupt likewise do
            # not derive from Exception and continue to propagate naturally.
            raise
        except Exception as exc:
            if on_codes_processed is None:
                raise
            warning = f"兑换已完成，但离场失败，最后确认场景 #49：{type(exc).__name__}: {exc}"
            with self._lock:
                self._log_locked("warning", warning)
            return {
                "result": "success",
                "message": f"已处理 {len(codes)} 个礼包码；{warning}",
                "current_scene": 49,
                "code_count": len(codes),
                "departure_warning": warning,
            }
        return {
            "result": "success",
            "message": f"已处理 {len(codes)} 个礼包码并返回世界",
            "current_scene": 34,
            "code_count": len(codes),
        }


    def _align_settings(self, ctx: dict[str, Any], stop_event: threading.Event) -> None:
        for attempt in range(12):
            frame = self._screencap(ctx)
            key, score = self._identify_scene(ctx, frame, ["settings", "gift", "duplicated", "reward", "world_menu", "world"])
            matched = key if self._scene_matches(key, score) else ""
            self._log("detail", f"对齐 #49：当前 {matched or 'unknown'} {score:.0f}%")
            if matched:
                with self._lock:
                    scene_id = self.scene_ids.get(matched)
                    self._status.update({"current_scene": scene_id, "updated_at": time.time()})
            if matched == "settings":
                return
            if matched == "reward":
                self._log("detail", "对齐 #49：检测到 #81 过渡奖励，等待回到设置页")
                self._clear_tick_frame(ctx)
                time.sleep(1.0)
                continue
            if matched in {"gift", "duplicated"}:
                close_shape = self._find_shape(self._image(ctx, "gift"), "关闭窗口")
                if close_shape is None:
                    close_shape = self._find_shape(self._image(ctx, "gift"), "关闭", contains=True)
                if close_shape:
                    self._log("detail", f"对齐 #49：检测到 #{self.scene_ids.get(matched)}，点击关闭窗口")
                    self._click_shape(ctx, self._image(ctx, "gift"), close_shape, frame)
                    time.sleep(0.9)
                    continue
            if matched == "world_menu":
                settings_shape = self._find_shape(self._image(ctx, "world_menu"), "设置")
                if not settings_shape:
                    raise RuntimeError("#35 缺少「设置」标注")
                self._log("detail", "对齐 #49：确认 #35 后匹配点击浮动「设置」")
                self._click_shape(ctx, self._image(ctx, "world_menu"), settings_shape, frame)
                time.sleep(1.0)
                continue
            if matched == "world":
                open_shape = self._find_shape(self._image(ctx, "world"), "打开下方菜单")
                if not open_shape:
                    raise RuntimeError("#34 缺少「打开下方菜单」标注")
                self._log("detail", "对齐 #49：确认 #34 后点击打开下方菜单")
                self._click_shape(ctx, self._image(ctx, "world"), open_shape, frame)
                time.sleep(0.8)
                continue
            if attempt >= 3:
                self._log("detail", "对齐 #49：未知场景，保留现场等待可靠识别")
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            time.sleep(0.8)
        raise RuntimeError("无法对齐到 #49 设置页")


    def _open_gift(self, ctx: dict[str, Any], stop_event: threading.Event) -> None:
        frame = self._screencap(ctx)
        image = self._image(ctx, "settings")
        shape = self._find_shape(image, "兑换礼包")
        if not image or not shape:
            raise RuntimeError("#49 缺少「兑换礼包」标注")
        box = self._box(shape, image)
        _width, height = self._frame_size(image)
        self._click_frame_point(
            ctx,
            image,
            float(box.get("x") or 0) + float(box.get("w") or 0) / 2,
            float(box.get("y") or 0) - height * 0.02,
        )
        deadline = time.monotonic() + 10.0
        poll_count = 0
        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            if self._is_gift_page_ready(ctx, frame):
                self._log("success", "兑换礼包窗口已就绪")
                return
            poll_count += 1
            if poll_count in {4, 8}:
                key, score = self._identify_scene(ctx, frame, ["settings"])
                if key == "settings" and self._scene_matches(key, score):
                    self._log("detail", f"兑换礼包入口点击未生效，仍在 #49，安全重试 {poll_count // 4}/2")
                    self._click_frame_point(
                        ctx,
                        image,
                        float(box.get("x") or 0) + float(box.get("w") or 0) / 2,
                        float(box.get("y") or 0) - height * 0.02,
                    )
            self._clear_tick_frame(ctx)
            time.sleep(0.5)
        raise RuntimeError("点击兑换礼包后未检测到兑换窗口文案")


    def _is_gift_page_ready(self, ctx: dict[str, Any], frame: str) -> bool:
        text = self._recognized_scene_ocr_text(ctx, frame, [self.scene_ids["gift"]])
        return self._gift_page_text_ready(text)


    def _clear_and_type(self, ctx: dict[str, Any], code: str, stop_event: threading.Event) -> None:
        image = self._image(ctx, "gift")
        shape = self._find_shape(image, "输入兑换码")
        if not image or not shape:
            raise RuntimeError("#78 缺少「输入兑换码」标注")
        self._click_shape(ctx, image, shape)
        time.sleep(0.25)
        self._keyevents(ctx, ["KEYCODE_MOVE_END", *["KEYCODE_DEL" for _ in range(40)]])
        time.sleep(0.25)
        self._raise_if_stopped(stop_event)
        self._text(ctx, code)
        time.sleep(0.35)
        # Input transport success is not business evidence.  Android's stock
        # ``input text`` can return zero while silently dropping Chinese, so
        # require the exact code on a fresh frame before closing the editor.
        normalized_code = "".join(str(code or "").split())
        input_deadline = time.monotonic() + 3.0
        frame = ""
        observed_text = ""
        while time.monotonic() < input_deadline:
            frame = self._screencap(ctx)
            observed_text = self._recognized_scene_ocr_text(
                ctx,
                frame,
                [self.scene_ids["gift"]],
            )
            if normalized_code and normalized_code in "".join(observed_text.split()):
                break
            self._clear_tick_frame(ctx)
            time.sleep(0.25)
        else:
            raise RuntimeError(
                f"礼包码输入后未回读到原码，拒绝提交：expected={code} OCR={observed_text[:120]}"
            )
        # 输入覆盖层仍处于编辑态；按本帧 OCR 的唯一“确定”实框完成输入，
        # 否则随后点击“兑换”只会收起覆盖层。
        width, height = self._frame_size(image)
        confirm_shape = self._find_shape(image, "输入确定")
        if confirm_shape is None:
            raise RuntimeError("#78 缺少「输入确定」标注")
        confirm_deadline = time.monotonic() + 3.0
        while True:
            try:
                confirm_x, confirm_y = self._gift_input_confirm_point(
                    self._ocr_fragments_in_shapes(
                        frame,
                        image,
                        ["输入确定"],
                        padding=8,
                        ctx=ctx,
                    ),
                    frame_width=width,
                    frame_height=height,
                )
                break
            except RuntimeError as exc:
                if "匹配到 0 项" not in str(exc) or time.monotonic() >= confirm_deadline:
                    raise
            self._clear_tick_frame(ctx)
            time.sleep(0.25)
            frame = self._screencap(ctx)
        self._click_frame_point(ctx, image, confirm_x, confirm_y)
        time.sleep(0.5)


    def _submit_code(self, ctx: dict[str, Any], code: str) -> None:
        image = self._image(ctx, "gift")
        shape = self._find_shape(image, "兑换")
        if not image or not shape:
            raise RuntimeError("#78 缺少「兑换」按钮标注")
        self._click_shape(ctx, image, shape)
        self._log("action", f"已提交：{code}")


    def _settle_after_submit(self, ctx: dict[str, Any], code: str, is_last: bool, stop_event: threading.Event) -> None:
        deadline = time.time() + 16.0
        plain_gift_since = 0.0
        last_seen = ""
        accepted_result_seen = False
        while time.time() < deadline:
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            overlay = self._detect_overlay(ctx, frame)
            if overlay == "duplicated":
                if is_last:
                    self._log("info", f"{code}：检测到 #82 已领取，关闭窗口")
                    self._close_gift_to_settings(ctx, stop_event)
                else:
                    self._log("info", f"{code}：检测到 #82 已领取，继续下一个")
                return
            if overlay == "reward":
                last_seen = "reward"
                accepted_result_seen = True
                self._clear_tick_frame(ctx)
                time.sleep(0.8)
                continue

            key, score = self._identify_scene(ctx, frame, ["settings", "gift"])
            if key == "settings" and self._scene_matches(key, score):
                self._log("info", f"{code}：已回到 #49")
                return
            if (
                key == "gift" and self._scene_matches(key, score)
            ) or self._is_gift_page_ready(ctx, frame):
                last_seen = "gift"
                if plain_gift_since <= 0:
                    plain_gift_since = time.time()
                if time.time() - plain_gift_since >= 4.0:
                    if not accepted_result_seen:
                        raise RuntimeError(
                            f"{code}：提交后仅停留 #78，未检测到奖励或已领取结果，拒绝计为成功"
                        )
                    if is_last:
                        self._log("info", f"{code}：奖励结果已确认，回到 #78 后关闭窗口")
                        self._close_gift_to_settings(ctx, stop_event)
                    else:
                        self._log("info", f"{code}：奖励结果已确认，回到 #78 后继续下一个")
                    return
            else:
                plain_gift_since = 0.0
                last_seen = key or last_seen
            self._clear_tick_frame(ctx)
            time.sleep(0.8)

        if accepted_result_seen and is_last:
            self._log("info", f"{code}：等待结果超时，尝试对齐 #49")
            self._align_settings(ctx, stop_event)
            return
        if accepted_result_seen:
            self._log("info", f"{code}：奖励结果已确认，等待窗口归位超时，继续下一个")
            return
        raise RuntimeError(
            f"{code}：等待兑换结果超时，未检测到奖励或已领取结果（最后看到 {last_seen or 'unknown'}）"
        )


    def _detect_overlay(self, ctx: dict[str, Any], frame: str) -> str:
        duplicated = self._image(ctx, "duplicated")
        if duplicated:
            for title in ("礼包已被领取", "已被领取"):
                shape = self._find_shape(duplicated, title, contains=True)
                if shape and self._shape_score(ctx, duplicated, shape, frame) >= self.overlay_threshold:
                    return "duplicated"
        reward = self._image(ctx, "reward")
        if reward:
            for title in ("恭喜获得", "点击继续", "奖品"):
                shape = self._find_shape(reward, title, contains=True)
                if shape and self._shape_score(ctx, reward, shape, frame) >= 65:
                    return "reward"
        return ""


    def _process_code(self, ctx: dict[str, Any], code: str, is_last: bool, stop_event: threading.Event) -> None:
        # This is a synchronous operation consumed synchronously by the batch.
        # Capture the current gift/settings scene; no unrelated Task wait belongs here.
        frame = self._capture_frame(ctx)
        key, score = self._identify_scene(ctx, frame, ["settings", "gift"])
        if key == "settings" and self._scene_matches(key, score):
            with self._lock:
                self._set_status_locked("running", f"进入 #78 填写：{code}", phase="open_gift", current_scene=49)
            self._open_gift(ctx, stop_event)
        elif not (
            key == "gift" and self._scene_matches(key, score)
        ) and not self._is_gift_page_ready(ctx, frame):
            with self._lock:
                self._set_status_locked("running", f"重新对齐后填写：{code}", phase="align_settings")
            self._align_settings(ctx, stop_event)
            self._open_gift(ctx, stop_event)
        with self._lock:
            self._set_status_locked("running", f"输入礼包码：{code}", phase="type_code", current_scene=78)
        self._clear_and_type(ctx, code, stop_event)
        with self._lock:
            self._set_status_locked("running", f"提交礼包码：{code}", phase="submit_code")
        self._submit_code(ctx, code)
        with self._lock:
            self._set_status_locked("running", f"等待兑换结果：{code}", phase="wait_result")
        self._settle_after_submit(ctx, code, is_last, stop_event)


    def _close_gift_to_settings(self, ctx: dict[str, Any], stop_event: threading.Event) -> None:
        image = self._image(ctx, "gift")
        shape = self._find_shape(image, "关闭窗口")
        if not image or not shape:
            raise RuntimeError("#78 缺少「关闭窗口」标注")
        self._click_shape(ctx, image, shape)
        key, score, _frame = self._wait_for_scene(ctx, stop_event, ["settings"], 2.5, interval=0.25)
        if key == "settings" and self._scene_matches(key, score):
            with self._lock:
                self._status.update({"current_scene": 49, "updated_at": time.time()})


    def _finish_from_settings(self, ctx: dict[str, Any], stop_event: threading.Event) -> None:
        image = self._image(ctx, "settings")
        shape = self._find_shape(image, "回退")
        if not image or not shape:
            raise RuntimeError("#49 缺少「回退」标注")
        with self._lock:
            self._status.update({"current_scene": 49, "updated_at": time.time()})
        self._click_shape(ctx, image, shape)
        key, score, _frame = self._wait_for_scene(ctx, stop_event, ["world", "world_menu", "settings"], 2.5, interval=0.25)
        if key and self._scene_matches(key, score):
            with self._lock:
                self._status.update({"current_scene": self.scene_ids.get(key), "updated_at": time.time()})
