from __future__ import annotations

import re
from typing import Any

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text


class 日常异火任务Mixin:
    def _daily_yihuo_text_is_xinghai_list(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return "异火" in compact and ("蓝色星海" in compact or "提纯" in compact or "幻灵域" in compact or "淬锋域" in compact)

    def _daily_yihuo_text_is_claimed(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return "已领取" in compact and "次日5点刷新" in compact and "净莲妖火" in compact

    def _daily_yihuo_text_is_detail(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return "异火" in compact and ("升阶" in compact or "次日5点刷新" in compact or "净莲妖火" in compact)

    def _daily_yihuo_return_best_effort(self, context: Any):
        for view_id, shape_title in ((261, "返回"), (260, "返回"), (259, "返回")):
            try:
                context.click_shape_center(view_id, shape_title)
            except Exception:
                continue
            try:
                result = yield from context.wait_any({
                    "已回世界": context.scene_visible(34),
                    "仍在异火详情": context.ocr_matches(self._daily_yihuo_text_is_claimed, label="异火已领取"),
                }, timeout=2.0, label="日常_异火：返回后确认")
            except Exception:
                continue
            if result == "已回世界":
                return

    def 日常异火流程(self, context: Any):
        scene_id, _score, frame = context.sample_scene_once([261, 260, 259, 34], update=True)
        current_text = context.ocr_text(frame)
        if self._daily_yihuo_text_is_claimed(current_text):
            yield from self._daily_yihuo_return_best_effort(context)
            context.set_next_time(self._next_daily_boss_reset_time_text())
            return
        if scene_id == 261 or self._daily_yihuo_text_is_detail(current_text):
            context.click_shape_center(261, "返回")
            yield from context.wait_scene([260], wait=5.0, label="日常_异火：从异火详情返回列表 #260")
            scene_id = 260
        if scene_id not in {259, 260} and not self._daily_yihuo_text_is_xinghai_list(current_text):
            yield from context.go_scene(34)
            yield from context.wait_click(34, "下方菜单/星海")

        if scene_id != 260:
            frame = yield from context.wait_any({
                "星海列表": context.ocr_contains(all_of=("异火",), any_of=("蓝色星海", "提纯", "幻灵域", "淬锋域")),
                "异火入口": context.shape_visible(259, "异火"),
            })
            if frame == "星海列表":
                context.click_shape_center(259, "异火")
            else:
                yield from context.wait_click(259, "异火")
            yield from context.wait_scene([260], wait=18.0, label="日常_异火：等待异火列表 #260")
        context.click_shape_center(260, "净莲")

        箱子状态 = yield from context.wait_any({
            "可领取": context.shape_visible(261, "箱子"),
            "已领取": context.all_of(
                context.shape_visible(261, "返回"),
                context.ocr_contains(all_of=("已领取",), any_of=("次日5点刷新", "净莲妖火")),
            ),
        })
        if 箱子状态 == "可领取":
            yield from context.wait_click(261, "箱子")

        yield from self._daily_yihuo_return_best_effort(context)

        返回状态 = yield from context.wait_any({
            "已回世界": context.scene_visible(34),
            "异火页返回": context.shape_visible(259, "返回"),
        }, timeout=3.0)
        if 返回状态 == "异火页返回":
            context.click_shape_center(259, "返回")
        try:
            yield from context.wait_scene([34], wait=3.0)
        except Exception:
            pass
        context.set_next_time(self._next_daily_boss_reset_time_text())

