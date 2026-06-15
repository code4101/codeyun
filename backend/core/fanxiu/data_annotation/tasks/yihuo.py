from __future__ import annotations

from typing import Any


class 日常异火任务Mixin:
    def 日常异火流程(self, runtime: Any):
        yield from runtime.goto_view(34)
        yield from runtime.wait_clicks([
            (34, "下方菜单/星海"),
            (259, "异火"),
            (260, "净莲"),
        ])

        箱子状态 = yield from runtime.wait_any({
            "可领取": runtime.shape_visible(261, "箱子"),
            "已领取": runtime.all_of(
                runtime.shape_visible(261, "返回"),
                runtime.ocr_contains(all_of=("已领取",), any_of=("次日5点刷新", "净莲妖火")),
            ),
        })
        if 箱子状态 == "可领取":
            yield from runtime.wait_click(261, "箱子")

        yield from runtime.wait_clicks([
            (261, "返回"),
            (260, "返回"),
        ])

        返回状态 = yield from runtime.wait_any({
            "已回世界": runtime.view_visible(34),
            "异火页返回": runtime.shape_visible(259, "返回"),
        })
        if 返回状态 == "异火页返回":
            yield from runtime.wait_click(259, "返回")
        yield from runtime.wait_view(34)

