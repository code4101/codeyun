from __future__ import annotations

from typing import Any


class SignupMiscTaskMixin:
    def 日常报名流程(self, runtime: Any):
        起点状态 = yield from self._日常报名进入日常页(runtime)
        入口状态 = "报名页" if 起点状态 == "报名页" else (yield from self._日常报名打开活动报名(runtime))
        if 入口状态 == "已完成":
            yield from runtime.goto_view(34)
            return {"result": "success", "claimed": 0, "already_done": True}

        领取数量 = yield from self._日常报名处理报名列(runtime)
        yield from self._日常报名返回日常页(runtime)
        yield from self._日常报名返回世界(runtime)
        if 领取数量 <= 0:
            return {
                "result": "skipped",
                "claimed": 0,
                "message": "日常_报名：未领取任何报名项，不能确认最后两条已处理，稍后重试",
            }
        return {"result": "success", "claimed": 领取数量}

    def _日常报名进入日常页(self, runtime: Any):
        scene_id, _score, frame = runtime.current_scene([69, 34], update=True)
        text = runtime.ocr_text(frame)
        if self._日常报名文本是报名页(text):
            return "报名页"
        if scene_id == 69:
            return "日常页"
        if scene_id == 34 or self._daily_assistant_text_is_world_like(text):
            yield from runtime.wait_click_then_view(34, "日常", label="日常_报名：从世界进入日常 #69")
            return "日常页"
        yield from runtime.goto_view(69)
        return "日常页"

    def _日常报名文本是报名页(self, text: str) -> bool:
        normalized = str(text or "")
        return "报名" in normalized and "活动时间" in normalized and ("已报名" in normalized or "待报名" in normalized)

    def _日常报名打开活动报名(self, runtime: Any) -> str:
        current_text = runtime.ocr_text(runtime.cur_frame(update=True))
        if self._日常报名文本是报名页(current_text):
            return "报名页"
        入口状态 = yield from runtime.wait_any({
            "可领取": runtime.shape_visible(75, "活动报名-领取"),
            "已完成": runtime.all_of(
                runtime.ocr_contains(all_of=("活动报名",)),
            ),
        })
        if 入口状态 == "可领取":
            yield from runtime.wait_click(75, "活动报名")
            yield from runtime.wait_any(
                {
                    "scene": runtime.view_visible(23),
                    "text": runtime.ocr_matches(self._日常报名文本是报名页, label="日常_报名：报名列表 OCR"),
                },
                label="日常_报名：等待报名列表 #23",
            )
        return 入口状态

    def _日常报名处理报名列(self, runtime: Any) -> int:
        领取数量 = 0
        无变化确认次数 = 0
        底部确认轮数 = int(getattr(runtime, "payload", {}).get("signup_bottom_confirmations", 2) or 2)
        while True:
            matches = runtime.ocr_row_clicks_in_shape(
                23,
                "报名列",
                include=("报名",),
                exclude=("已报名",),
            )
            if matches:
                x, y, _text = matches[0]
                runtime.click_frame_point(23, x, y)
                if not (yield from self._日常报名等待领取页(runtime)):
                    continue
                yield from runtime.wait_click(24, "领取")
                领取数量 += 1
                无变化确认次数 = 0
                领取后落点 = yield from self._日常报名等待领取后落点(runtime)
                if 领取后落点 != "报名页":
                    return 领取数量
                continue

            滚动有变化 = yield from runtime.scroll_shape_content(23, "报名列")
            if 滚动有变化:
                无变化确认次数 = 0
                continue
            无变化确认次数 += 1
            if 无变化确认次数 >= max(1, 底部确认轮数):
                break
        return 领取数量

    def _日常报名等待领取页(self, runtime: Any) -> bool:
        try:
            yield from runtime.wait_view(24)
            return True
        except TimeoutError:
            return False

    def _日常报名等待领取后落点(self, runtime: Any) -> str:
        return (
            yield from runtime.wait_any(
                {
                    "报名页": runtime.view_visible(23),
                    "日常页": runtime.view_visible(69),
                    "世界": runtime.view_visible(34),
                    "绿瓶": runtime.view_visible(20),
                    "报名文本": runtime.ocr_matches(self._日常报名文本是报名页, label="日常_报名：领取后报名页 OCR"),
                    "世界文本": runtime.ocr_matches(self._daily_assistant_text_is_world_like, label="日常_报名：领取后世界 OCR"),
                },
                label="日常_报名：等待领取后落点",
            )
        )

    def _日常报名返回日常页(self, runtime: Any):
        scene_id, _score, frame = runtime.current_scene([23, 69, 34], update=True)
        text = runtime.ocr_text(frame)
        if scene_id in (69, 34) or self._daily_assistant_text_is_world_like(text):
            return
        if scene_id != 23 and not self._日常报名文本是报名页(text):
            return
        if all(hasattr(runtime, name) for name in ("click_shape_center", "wait_action_settle")):
            runtime.click_shape_center(23, "返回")
            yield from runtime.wait_action_settle(1.0)
            return
        yield from runtime.wait_click(23, "返回")

    def _日常报名返回世界(self, runtime: Any):
        scene_id, _score, frame = runtime.current_scene([69, 34], update=True)
        text = runtime.ocr_text(frame)
        if scene_id == 34 or self._daily_assistant_text_is_world_like(text):
            return
        if scene_id == 69 or ("日常" in text and "活跃度" in text):
            if all(hasattr(runtime, name) for name in ("click_shape_center", "wait_action_settle")):
                runtime.click_shape_center(69, "退出")
                yield from runtime.wait_action_settle(1.0)
            else:
                yield from runtime.wait_click(69, "退出")
            yield from runtime.wait_any(
                {
                    "scene": runtime.view_visible(34),
                    "text": runtime.ocr_matches(self._daily_assistant_text_is_world_like, label="日常_报名：世界 OCR"),
                },
                label="日常_报名：等待返回世界 #34",
            )
            return
        yield from runtime.goto_view(34)
