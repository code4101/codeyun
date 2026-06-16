from __future__ import annotations

from typing import Any


class SignupMiscTaskMixin:
    def 日常报名流程(self, runtime: Any):
        yield from runtime.goto_view(69)
        入口状态 = yield from self._日常报名打开活动报名(runtime)
        if 入口状态 == "已完成":
            yield from runtime.goto_view(34)
            return

        yield from self._日常报名处理报名列(runtime)
        yield from runtime.wait_click(23, "返回")
        yield from runtime.goto_view(34)

    def _日常报名打开活动报名(self, runtime: Any) -> str:
        入口状态 = yield from runtime.wait_any({
            "可领取": runtime.shape_visible(75, "活动报名-领取"),
            "已完成": runtime.all_of(
                runtime.ocr_contains(all_of=("活动报名",)),
            ),
        })
        if 入口状态 == "可领取":
            yield from runtime.wait_click(75, "活动报名")
            yield from runtime.wait_view(23)
        return 入口状态

    def _日常报名处理报名列(self, runtime: Any) -> int:
        领取数量 = 0
        while True:
            matches = runtime.ocr_row_clicks_in_shape(
                23,
                "报名列",
                include=("报名",),
                exclude=("已报名",),
            )
            if matches:
                x, y, text = matches[0]
                runtime.click_frame_point(23, x, y)
                try:
                    yield from runtime.wait_view(24)
                except TimeoutError:
                    yield from runtime.scroll_shape_content(23, "报名列")
                    continue
                yield from runtime.wait_click(24, "领取")
                yield from runtime.wait_view(23)
                领取数量 += 1
                continue

            滚动有变化 = yield from runtime.scroll_shape_content(23, "报名列")
            if not 滚动有变化:
                break
        return 领取数量
