"""双修业务：从日常入口选择秘术、邀请仙缘、完成修炼并返回世界。

本模块持有双修场景与完成规则。识别、Shape 点击和停止检查由宿主执行器
提供，任务入口仍为 _execute_daily_shuangxiu_task，保留 Scheduler 调用契约。
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from pyxllib.autogui import View

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from ..ocr_values import FULLWIDTH_DIGIT_TRANSLATION


class DailyShuangxiuTaskMixin:
    def _execute_daily_shuangxiu_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_双修资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        image69 = images.get(69)
        image215 = images.get(215)
        image216 = images.get(216)
        image217 = images.get(217)
        image218 = images.get(218)
        image219 = images.get(219)
        image221 = images.get(221)

        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([221, 219, 218, 217, 216, 215, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id == 221:
            return (yield from self._click_daily_shuangxiu_continue(ctx, stop_event, payload))
        if scene_id == 219:
            if self._daily_shuangxiu_text_remaining_zero(text):
                return (yield from self._finish_daily_shuangxiu_after_continue(ctx, stop_event, payload))
            return (yield from self._click_daily_shuangxiu_start_training(ctx, stop_event, payload))
        if scene_id == 218:
            return (yield from self._click_daily_shuangxiu_first_partner(ctx, stop_event, payload))
        if scene_id == 217:
            return (yield from self._click_daily_shuangxiu_xianyuan_tab(ctx, stop_event, payload))
        if scene_id == 216:
            return (yield from self._click_daily_shuangxiu_invite(ctx, stop_event, payload))
        if scene_id == 215:
            return (yield from self._click_daily_shuangxiu_first_book(ctx, stop_event, payload, frame=frame))
        if scene_id != 69:
            if scene_id != 34:
                raise RuntimeError("日常_双修：当前不在可识别的世界、日常页或双修秘术页，无法开始")
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label="日常_双修")):
                _wait_scene_match = yield from context.wait_scene([215, 69, 34], wait=5.0, required=False)
                (scene_id, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                if scene_id == 215:
                    return (yield from self._click_daily_shuangxiu_first_book(ctx, stop_event, payload, frame=frame))
            if scene_id != 69:
                yield from self._enter_daily_from_world_like(
                    ctx,
                    context,
                    stop_event,
                    frame,
                    scene_id,
                    text,
                    label="日常_双修",
                )

        daily_status = yield from self._open_daily_entry_from_daily(
            ctx,
            stop_event,
            payload,
            task_label="日常_双修",
            title_pattern=r"完成\s*双\s*人\s*修\s*炼\s*1\s*次|双\s*人\s*修\s*炼|双\s*修",
            progress_can_mark_done=True,
        )
        if daily_status == "done":
            self._record_daily_entry_done(
                payload,
                task_id="legacy-daily-shuangxiu",
                task_type="daily_shuangxiu",
                label="日常_双修",
                message="日常列表显示已完成",
            )
            return "success"
        if daily_status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-shuangxiu",
                task_type="daily_shuangxiu",
                label="日常_双修",
                entry_label="完成双人修炼1次",
            )
            return "skipped"
        context = self._behavior_tree_context(ctx, ctx.get("asset_tree_path") if isinstance(ctx.get("asset_tree_path"), Path) else None, stop_event=stop_event)
        yield from context.wait_scene(
            [215],
            wait=float(payload.get("secret_timeout") or payload.get("post_click_timeout") or 12.0),
            label="日常_双修：等待双修秘术页 #215",
        )
        return (yield from self._click_daily_shuangxiu_first_book(ctx, stop_event, payload))

    def _click_daily_shuangxiu_first_book(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        frame: str | None = None,
    ) -> str:
        self._raise_if_stopped(stop_event)
        image215 = ctx.get("images", {}).get(215)
        if not isinstance(image215, dict):
            raise RuntimeError("日常_双修：缺少 #215「双修秘术」标注，无法点击痴情咒")
        book_shape = self._find_shape(image215, "痴情咒", "shape 2")
        if book_shape is None:
            raise RuntimeError("日常_双修：#215 缺少「痴情咒」点击区域标注")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：点击 #215「痴情咒」",
                phase="daily_shuangxiu_click_first_book",
                current_scene=215,
            )
            self._log_locked("action", "日常_双修：点击 #215「痴情咒」第一本书")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image215,
            book_shape,
            payload,
            label="日常_双修：等待 #215「痴情咒」",
            y_ratio=0.35,
            timeout_key="book_click_timeout",
        )
        yield from self._wait_daily_shuangxiu_detail(ctx, stop_event, payload)
        return (yield from self._click_daily_shuangxiu_invite(ctx, stop_event, payload))

    def _wait_daily_shuangxiu_detail(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from context.wait_scene(
            [216],
            wait=float(payload.get("detail_timeout") or 12.0),
            label="日常_双修：等待痴情咒详情",
        )
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：已进入痴情咒详情",
                phase="daily_shuangxiu_detail_ready",
                current_scene=216,
            )

    def _click_daily_shuangxiu_invite(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ) -> str:
        self._raise_if_stopped(stop_event)
        image216 = ctx.get("images", {}).get(216)
        if not isinstance(image216, dict):
            raise RuntimeError("日常_双修：缺少 #216「双修痴情咒详情」标注，无法点击邀请道友")
        invite_shape = self._find_shape(image216, "邀请道友", "shape 1")
        if invite_shape is None:
            raise RuntimeError("日常_双修：#216 缺少「邀请道友」按钮标注")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：点击 #216「邀请道友」",
                phase="daily_shuangxiu_click_invite",
                current_scene=216,
            )
            self._log_locked("action", "日常_双修：点击 #216「邀请道友」")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image216,
            invite_shape,
            payload,
            label="日常_双修：等待 #216「邀请道友」",
            timeout_key="invite_click_timeout",
        )
        yield from self._wait_daily_shuangxiu_invite(ctx, stop_event, payload)
        return (yield from self._click_daily_shuangxiu_xianyuan_tab(ctx, stop_event, payload))

    def _daily_shuangxiu_text_remaining_zero(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        compact = re.sub(r"\s+", "", normalized).replace("O", "0").replace("o", "0")
        return "今日剩余修炼次数" in compact and bool(re.search(r"今日剩余修炼次数[:：]?0(?:\D|$)", compact))

    def _wait_daily_shuangxiu_invite(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        context = self._behavior_tree_context(ctx, stop_event=stop_event)
        return (yield from context.wait_scene(
            [217],
            wait=float(payload.get("invite_timeout") or 12.0),
            label="日常_双修：等待邀请页 #217",
        ))

    def _click_daily_shuangxiu_xianyuan_tab(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ) -> str:
        self._raise_if_stopped(stop_event)
        image217 = ctx.get("images", {}).get(217)
        if not isinstance(image217, dict):
            raise RuntimeError("日常_双修：缺少 #217「双修邀请」标注，无法点击仙缘页签")
        tab_shape = self._find_shape(image217, "仙缘页签", "shape 1")
        if tab_shape is None:
            raise RuntimeError("日常_双修：#217 缺少「仙缘页签」标注")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：点击 #217「仙缘」",
                phase="daily_shuangxiu_click_xianyuan_tab",
                current_scene=217,
            )
            self._log_locked("action", "日常_双修：点击 #217「仙缘」页签")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image217,
            tab_shape,
            payload,
            label="日常_双修：等待 #217「仙缘」页签",
            timeout_key="xianyuan_tab_click_timeout",
        )
        settle_seconds = float(payload.get("xianyuan_tab_settle_seconds") or 2.0)
        if settle_seconds > 0:
            yield from self._wait_action_settle(ctx, stop_event, seconds=settle_seconds)
        context = self._behavior_tree_context(ctx, stop_event=stop_event)
        yield from context.wait_scene(
            [218],
            wait=float(payload.get("xianyuan_list_timeout") or 12.0),
            label="日常_双修：等待仙缘邀请列表 #218",
        )
        return (yield from self._click_daily_shuangxiu_first_partner(ctx, stop_event, payload))

    def _click_daily_shuangxiu_first_partner(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ) -> str:
        self._raise_if_stopped(stop_event)
        image218 = ctx.get("images", {}).get(218)
        if not isinstance(image218, dict):
            raise RuntimeError("日常_双修：缺少 #218「双修仙缘邀请列表」标注，无法点击邀请按钮")
        invite_shape = self._find_shape(image218, "邀请按钮", "shape 1")
        if invite_shape is None:
            raise RuntimeError("日常_双修：#218 缺少「邀请按钮」浮动标注")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：点击 #218 第一个可用邀请",
                phase="daily_shuangxiu_click_first_partner",
                current_scene=218,
            )
            self._log_locked("action", "日常_双修：点击 #218「邀请按钮」第一个匹配项")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image218,
            invite_shape,
            payload,
            label="日常_双修：等待 #218「邀请按钮」",
            timeout_key="partner_invite_click_timeout",
        )
        settle_seconds = float(payload.get("partner_invite_settle_seconds") or 2.0)
        if settle_seconds > 0:
            yield from self._wait_action_settle(ctx, stop_event, seconds=settle_seconds)
        yield from self._wait_daily_shuangxiu_training_ready(ctx, stop_event, payload)
        return (yield from self._click_daily_shuangxiu_start_training(ctx, stop_event, payload))

    def _wait_daily_shuangxiu_training_ready(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        context = self._behavior_tree_context(ctx, stop_event=stop_event)
        return (yield from context.wait_scene(
            [219],
            wait=float(payload.get("training_ready_timeout") or 12.0),
            label="日常_双修：等待修炼准备页 #219",
        ))

    def _click_daily_shuangxiu_start_training(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ) -> str:
        self._raise_if_stopped(stop_event)
        image219 = ctx.get("images", {}).get(219)
        if not isinstance(image219, dict):
            raise RuntimeError("日常_双修：缺少 #219「双修修炼准备」标注，无法点击前往修炼")
        start_shape = self._find_shape(image219, "前往修炼", "shape 1")
        if start_shape is None:
            raise RuntimeError("日常_双修：#219 缺少「前往修炼」按钮标注")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：点击 #219「前往修炼」",
                phase="daily_shuangxiu_click_start_training",
                current_scene=219,
            )
            self._log_locked("action", "日常_双修：点击 #219「前往修炼」")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image219,
            start_shape,
            payload,
            label="日常_双修：等待 #219「前往修炼」",
            timeout_key="start_training_click_timeout",
        )
        settle_seconds = float(payload.get("start_training_settle_seconds") or 2.0)
        if settle_seconds > 0:
            yield from self._wait_action_settle(ctx, stop_event, seconds=settle_seconds)
        yield from self._wait_daily_shuangxiu_complete(ctx, stop_event, payload)
        return (yield from self._click_daily_shuangxiu_continue(ctx, stop_event, payload))

    def _wait_daily_shuangxiu_complete(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        context = self._behavior_tree_context(ctx, stop_event=stop_event)
        return (yield from context.wait_scene(
            [221],
            wait=float(payload.get("training_complete_timeout") or 18.0),
            label="日常_双修：等待修炼完成页 #221",
        ))

    def _click_daily_shuangxiu_continue(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ) -> str:
        self._raise_if_stopped(stop_event)
        image221 = ctx.get("images", {}).get(221)
        if not isinstance(image221, dict):
            raise RuntimeError("日常_双修：缺少 #221「双修修炼完成」标注，无法点击继续")
        continue_shape = self._find_shape(image221, "点击屏幕继续", "继续", "shape 1")
        if continue_shape is None:
            raise RuntimeError("日常_双修：#221 缺少「点击屏幕继续」按钮标注")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：点击 #221「点击屏幕继续」",
                phase="daily_shuangxiu_click_continue",
                current_scene=221,
            )
            self._log_locked("action", "日常_双修：点击 #221「点击屏幕继续」")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image221,
            continue_shape,
            payload,
            label="日常_双修：等待 #221「点击屏幕继续」",
            timeout_key="complete_continue_click_timeout",
        )
        settle_seconds = float(payload.get("complete_continue_settle_seconds") or 2.0)
        if settle_seconds > 0:
            yield from self._wait_action_settle(ctx, stop_event, seconds=settle_seconds)
        return (yield from self._finish_daily_shuangxiu_after_continue(ctx, stop_event, payload))

    def _finish_daily_shuangxiu_after_continue(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ) -> str:
        context = self._behavior_tree_context(ctx, stop_event=stop_event)
        scene_id = yield from context.wait_scene(
            [34, 219],
            wait=float(payload.get("after_complete_timeout") or 8.0),
            label="日常_双修：等待修炼完成后的正式落点",
        )
        if isinstance(scene_id, View):
            scene_id = scene_id.id
        if scene_id == 34:
            return self._complete_daily_shuangxiu_after_continue(current_scene=34)
        if scene_id == 219:
            yield from self._leave_daily_shuangxiu_training_ready(ctx, stop_event, payload)
        else:
            raise RuntimeError(f"日常_双修：修炼完成后的落点 #{scene_id} 尚未实现")
        yield from context.wait_scene(
            [34],
            wait=float(payload.get("after_leave_world_timeout") or 12.0),
            label="日常_双修：等待离开后回到世界 #34",
        )
        return self._complete_daily_shuangxiu_after_continue(current_scene=34)

    def _leave_daily_shuangxiu_training_ready(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        image219 = ctx.get("images", {}).get(219)
        if not isinstance(image219, dict):
            raise RuntimeError("日常_双修：缺少 #219「双修修炼准备」标注，无法离开")
        leave_shape = self._find_shape(image219, "请离", "离开", "退出", "返回")
        if leave_shape is None:
            raise RuntimeError("日常_双修：#219 缺少「离开」按钮标注，无法完成收尾")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：点击 #219「离开」",
                phase="daily_shuangxiu_click_leave",
                current_scene=219,
            )
            self._log_locked("action", "日常_双修：点击 #219「离开」")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image219,
            leave_shape,
            payload,
            label="日常_双修：等待 #219「离开」",
            timeout_key="leave_click_timeout",
        )
        settle_seconds = float(payload.get("leave_click_settle_seconds") or 1.0)
        if settle_seconds > 0:
            yield from self._wait_action_settle(ctx, stop_event, seconds=settle_seconds)
        context = self._behavior_tree_context(ctx, stop_event=stop_event)
        scene_id = yield from context.wait_scene(
            [34],
            wait=float(payload.get("leave_result_timeout") or 8.0),
            label="日常_双修：等待守护处理离开确认后返回世界",
        )
        if scene_id.id != 34:
            raise RuntimeError(f"日常_双修：离开后未到世界，实际 #{scene_id.id}")

    def _complete_daily_shuangxiu_after_continue(self, *, current_scene: int | None) -> str:
        with self._lock:
            self._set_status_locked(
                "success",
                "日常_双修：已点击修炼完成继续",
                phase="daily_shuangxiu_complete_continued",
                current_scene=current_scene,
            )
            self._log_locked("success", "日常_双修：已点击 #221「点击屏幕继续」")
        return "success"
