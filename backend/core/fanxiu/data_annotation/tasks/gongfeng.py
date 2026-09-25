"""供奉领取、法则升级与界面收尾。

由执行器组合提供场景、Runtime 与调度能力；仅导入模块不会执行游戏动作。
"""
from __future__ import annotations
import re
import threading
from typing import Any
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.ocr_values import FULLWIDTH_DIGIT_TRANSLATION


class DailyGongfengTaskMixin:
    def _execute_daily_gongfeng_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        return self._execute_daily_task(
            ctx,
            stop_event,
            payload,
            task_type="daily_gongfeng",
            label="日常_供奉",
            flow=self.日常供奉流程,
        )

    def 日常供奉流程(self, context: Any):
        task_label = "日常_供奉"
        ctx = context.ctx
        stop_event = context.stop_event or threading.Event()
        payload = context.payload

        image252 = context.view(252).raw
        image254 = context.view(254).raw
        image255 = context.view(255).raw
        image256 = context.view(256).raw
        image257 = context.view(257).raw

        _wait_scene_match = yield from context.wait_scene([34, 251, 252, 255, 256, 257], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        accepted = 0
        upgraded = 0
        if scene_id == 255:
            yield from self._close_daily_gongfeng_item_detail_if_present(ctx, stop_event, image255, context)
            scene_id = 254
        if scene_id == 257:
            yield from context.wait_click(257, "空白")
            yield from context.wait_action_settle(1.2)
            scene_id = 252
        if scene_id == 256:
            yield from context.wait_click(256, "返回")
            _wait_scene_match = yield from context.wait_scene([34, 252], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )

        if self._daily_gongfeng_upgrade_page_visible(context, frame):
            upgraded = yield from self._upgrade_daily_gongfeng_until_insufficient(ctx, stop_event, payload, image254, image255, context)
            yield from self._close_daily_gongfeng_upgrade_pages(ctx, stop_event, payload, image254, image256, context)
        else:
            if scene_id is None:
                yield from context.go_scene(34)
                scene_id = 34
            if scene_id == 34:
                yield from context.wait_click(34, "主线")
                scene_id = 251
            if scene_id == 251:
                yield from context.wait_click(251, "供奉")
                scene_id = 252
            if scene_id == 252:
                accepted = yield from self._accept_daily_gongfeng_until_done(ctx, stop_event, payload, image252, context)
                yield from self._claim_daily_gongfeng_extra_reward(ctx, stop_event, payload, image252, image257, context)
                upgraded = yield from self._upgrade_daily_gongfeng_law(ctx, stop_event, payload, image252, image254, image255, image256, context)
        yield from context.go_scene(34)
        context.set_next_time(self._next_daily_boss_reset_time_text())
        context.set_completion_message(f"{task_label}完成，接受 {accepted} 次，升级 {upgraded} 次，已回到世界")

    def _accept_daily_gongfeng_until_done(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image252: dict[str, Any],
        context: BehaviorTreeContext,
    ):
        accepted = 0
        max_accept = max(0, int(payload.get("max_accept") or 20))
        max_read = max(1, int(payload.get("gongfeng_count_read_retries") or 8))
        for index in range(max_accept):
            self._raise_if_stopped(stop_event)
            yield from context.wait_scene([252], label="日常_供奉：等待供奉页 #252")
            remaining = None
            last_text = ""
            for read_index in range(max_read):
                numbers, last_text = context.ocr_numbers_in_shapes(252, ("次数",), padding=16, max_attempts=1)
                self._log("detail", f"日常_供奉：读取次数 {index + 1}.{read_index + 1} OCR={last_text} nums={numbers}")
                if numbers:
                    remaining = numbers[0]
                    break
                yield from context.wait_action_settle(0.8)
            if remaining is None:
                raise RuntimeError(f"日常_供奉：#252「次数」未识别到整数，OCR={last_text[:120]}")
            if remaining <= 0:
                self._log("success", f"日常_供奉：供奉次数已为 0，接受 {accepted} 次")
                return accepted
            with self._lock:
                self._set_status_locked("running", f"日常_供奉：接受供奉 {accepted + 1}", phase="daily_gongfeng_accept")
                self._log_locked("action", "日常_供奉：点击 #252「接受供奉」")
            yield from context.wait_click(252, "接受供奉")
            accepted += 1
            yield from context.wait_action_settle(2.0)
        raise RuntimeError(f"日常_供奉：达到最大接受次数 {max_accept} 仍未到 0，已暂停")

    def _claim_daily_gongfeng_extra_reward(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image252: dict[str, Any],
        image257: dict[str, Any],
        context: BehaviorTreeContext,
    ):
        shape = self._find_shape(image252, "额外奖励")
        if shape is None:
            raise RuntimeError("日常_供奉：缺少 #252「额外奖励」标注")
        yield from context.wait_scene([252], label="日常_供奉：等待供奉页 #252")
        with self._lock:
            self._set_status_locked("running", "日常_供奉：点击 #252「额外奖励」", phase="daily_gongfeng_extra_reward", current_scene=252)
            self._log_locked("action", "日常_供奉：点击 #252「额外奖励」")
        yield from context.wait_click(252, "额外奖励")
        yield from context.wait_action_settle(float(payload.get("gongfeng_extra_settle_seconds") or 2.0))
        result = yield from context.wait_scene(
            [257, 252],
            label="日常_供奉：等待额外奖励结果（弹窗由 Layer 0 守护清理）",
        )
        result_id = int(getattr(result, "id", result))
        if result_id == 257:
            yield from context.wait_click(257, "空白")
            yield from context.wait_scene([252], label="日常_供奉：等待回到 #252")
            return "closed_257"
        self._log("success", "日常_供奉：额外奖励已领取或未弹出详情")
        return "no_popup"

    def _upgrade_daily_gongfeng_law(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image252: dict[str, Any],
        image254: dict[str, Any],
        image255: dict[str, Any],
        image256: dict[str, Any],
        context: BehaviorTreeContext,
    ) -> int:
        yield from context.wait_scene([252], label="日常_供奉：等待供奉页 #252")
        yield from context.wait_click(252, "升级法则")
        yield from self._wait_daily_gongfeng_upgrade_page(context)
        upgraded = yield from self._upgrade_daily_gongfeng_until_insufficient(ctx, stop_event, payload, image254, image255, context)
        yield from self._close_daily_gongfeng_upgrade_pages(ctx, stop_event, payload, image254, image256, context)
        return upgraded

    def _wait_daily_gongfeng_upgrade_page(self, context: Any):
        return (yield from context.wait_any(
            {
                "升级页": context.ocr_contains(
                    all_of=("技能描述",),
                    any_of=("升级", "时间道蕴", "仙法"),
                    label="日常_供奉：#254 升级页 OCR",
                ),
            },
            label="日常_供奉：等待 #254 升级页",
        ))

    def _daily_gongfeng_upgrade_page_visible(
        self,
        context: Any,
        frame: str | None = None,
    ) -> bool:
        try:
            score = context.shape_score(254, "升级", frame_data_url=frame)
        except Exception:
            return False
        return score >= float(self.overlay_threshold)

    def _upgrade_daily_gongfeng_until_insufficient(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image254: dict[str, Any],
        image255: dict[str, Any],
        context: Any,
    ) -> int:
        upgraded = 0
        max_upgrade = max(0, int(payload.get("max_upgrade") or 50))
        read_retries = max(1, int(payload.get("gongfeng_law_read_retries") or 12))
        for loop_index in range(max_upgrade + 1):
            self._raise_if_stopped(stop_event)
            yield from self._close_daily_gongfeng_item_detail_if_present(ctx, stop_event, image255, context)
            parsed: tuple[int, int] | None = None
            last_text = ""
            for read_index in range(read_retries):
                self._raise_if_stopped(stop_event)
                try:
                    yield from self._wait_daily_gongfeng_upgrade_page(context)
                except TimeoutError:
                    _wait_scene_match = yield from context.wait_scene([34, 252, 256], wait=5.0, required=False)
                    (scene_id, score, _frame) = (
                        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                    )
                    if scene_id in {34, 252, 256}:
                        self._log("success", f"日常_供奉：升级后已离开 #254，到达 #{scene_id} {score:.0f}%，升级 {upgraded} 次")
                        return upgraded
                    raise
                nums, last_text = context.ocr_numbers_in_shapes(254, ("数值",), padding=26, max_attempts=1)
                self._log("detail", f"日常_供奉：读取法则数值 {loop_index + 1}.{read_index + 1} OCR={last_text} nums={nums}")
                parsed = self._parse_daily_gongfeng_law_progress(last_text)
                if parsed is not None:
                    break
                if (yield from self._close_daily_gongfeng_item_detail_if_present(ctx, stop_event, image255, context)):
                    continue
                yield from context.wait_action_settle(0.8)
            if parsed is None:
                raise RuntimeError(f"日常_供奉：#254「数值」未识别到两个整数，OCR={last_text[:120]}")
            current, required = parsed
            if current < required:
                self._log("success", f"日常_供奉：法则资源不足 {current}/{required}，升级 {upgraded} 次")
                return upgraded
            with self._lock:
                self._set_status_locked("running", f"日常_供奉：升级法则 {upgraded + 1}，{current}/{required}", phase="daily_gongfeng_upgrade_law", current_scene=254)
            context.click_shape_center(254, "升级")
            upgraded += 1
            yield from context.wait_action_settle(1.5)
        raise RuntimeError(f"日常_供奉：升级超过 {max_upgrade} 次仍未到资源不足，已暂停")

    def _parse_daily_gongfeng_law_progress(self, text: Any) -> tuple[int, int] | None:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = normalized.replace("：", ":").replace("O", "0").replace("o", "0")
        fraction = parse_ocr_values(normalized, expected_count=2, allow_extra_numbers=True)
        if fraction is None:
            return None
        current, required = fraction
        current_text, required_text = str(current), str(required)
        if required > 0 and current > required and len(current_text) > len(required_text):
            if current_text.startswith(required_text):
                current = int(current_text[len(required_text):].lstrip("0") or "0")
            elif len(current_text) >= len(required_text) * 2:
                suffix = int(current_text[-len(required_text):])
                if suffix <= required:
                    current = suffix
        return (current, required) if required > 0 else None

    def _close_daily_gongfeng_item_detail_if_present(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image255: dict[str, Any],
        context: Any,
    ):
        del ctx, stop_event, image255
        _wait_scene_match = yield from context.wait_scene([255], wait=5.0, required=False)
        (scene_id, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id != 255:
            return False
        self._log("action", "日常_供奉：关闭 #255 物品详情")
        yield from context.wait_click(255, "空白")
        yield from context.wait_action_settle(1.2)
        return True

    def _close_daily_gongfeng_upgrade_pages(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image254: dict[str, Any],
        image256: dict[str, Any],
        context: BehaviorTreeContext,
    ):
        del image254
        _wait_scene_match = yield from context.wait_scene([34, 252, 254, 256], wait=5.0, required=False)
        (scene_id, score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id in {34, 252}:
            self._log("success", f"日常_供奉：已在收尾场景 #{scene_id} {score:.0f}%，无需关闭 #254")
            return "success"
        if scene_id == 256:
            yield from context.wait_click(256, "返回")
            yield from context.wait_scene([252, 34], label="日常_供奉：等待回到 #252/#34")
            return "success"
        try:
            yield from self._wait_daily_gongfeng_upgrade_page(context)
        except TimeoutError:
            _wait_scene_match = yield from context.wait_scene([34, 252, 256], wait=5.0, required=False)
            (scene_id, score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id in {34, 252}:
                self._log("success", f"日常_供奉：未再检测到 #254，当前 #{scene_id} {score:.0f}%，按已收尾处理")
                return "success"
            if scene_id == 256:
                yield from context.wait_click(256, "返回")
                yield from context.wait_scene([252, 34], label="日常_供奉：等待回到 #252/#34")
                return "success"
            raise
        self._log("action", "日常_供奉：点击 #254「空白」")
        yield from context.wait_click(254, "空白")
        yield from context.wait_action_settle(1.2)
        yield from context.wait_click(256, "返回")
        try:
            yield from context.wait_scene([252, 34], label="日常_供奉：等待回到 #252/#34")
        except Exception as exc:
            self._log("warning", f"日常_供奉：#256 返回后未确认 #252/#34：{exc}")
        return "success"

    def _daily_gongfeng_numbers(self, text: str) -> list[int]:
        normalized = str(text or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
        return list(parse_ocr_values(normalized) or ())

    def _daily_gongfeng_text_is_page(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "供奉总览" in normalized and ("供奉奖励" in normalized or "接受供奉" in normalized or "每日登陆可领取奖励" in normalized)

    def _daily_gongfeng_remaining(self, text: str) -> int | None:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        match = re.search(r"今日接受供奉次数[:：]?\s*(\d+)", normalized)
        if not match:
            return None
        raw = match.group(1)
        if len(raw) > 1 and raw.endswith("5"):
            raw = raw[:-1]
        try:
            return int(raw)
        except ValueError:
            return None
