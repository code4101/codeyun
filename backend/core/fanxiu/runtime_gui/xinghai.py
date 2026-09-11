"""星海入口：等待浮动名称可读，点击一次，再确认指定目标页。

名称、错字容差、搜索范围及固定入口的点击几何由 #259 Shape 持有。
这里只管理动作时序，不预测旋转角度，也不因一帧缺字而推断入口不存在。
真实帧已验证四域名称定位；入口点击与目标页仍须逐项真实验收。
"""
from __future__ import annotations

from collections.abc import Generator, Sequence
from typing import Any


def open_xinghai_entry(
    context: Any,
    target: str,
    *,
    target_scenes: Sequence[int],
    search_timeout: float = 120.0,
    landing_timeout: float = 15.0,
) -> Generator[Any, None, Any]:
    """在 #259 打开指定入口，并返回已确认的目标场景。

    120 秒是沿用拜谒的初始有界等待预算，不是旋转周期测量结果。
    wait_click 复用场景守护、父区域、浮动 OCR 唯一性和本帧落点。
    命中缺失或歧义时继续采样；超时抛错并保留现场。动作只发一次，
    随后仅观察调用方声明的目标页，不把“离开源页”当作正确到达。
    不能声明目标页时，应先进行逐步研发与标注，不调用本入口。
    """
    rotating = {"淬锋域", "淬灵域", "幻灵域", "轮回域"}
    if target not in rotating | {"提纯", "异火"}:
        raise ValueError(f"未知星海入口：{target}")
    destinations = list(dict.fromkeys(int(scene) for scene in target_scenes))
    if not destinations or any(scene <= 0 or scene == 259 for scene in destinations):
        raise ValueError("必须声明有效的星海入口目标场景，且不能包含源场景 #259")
    if search_timeout <= 0 or landing_timeout <= 0:
        raise ValueError("等待预算必须大于 0")
    shape = f"旋转区域/{target}" if target in rotating else target
    with context.expect_views(*destinations):
        yield from context.wait_click(259, shape, timeout=search_timeout)
        return (yield from context.wait_scene(
            destinations, wait=landing_timeout, label=f"星海：确认进入{target}",
        ))
