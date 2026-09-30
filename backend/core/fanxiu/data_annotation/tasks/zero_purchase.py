"""零元购每日返还：逐页领取已有返还，不执行购买。

当前已验收的三页共用 #902；按钮倒计时和返还进度是业务事实，
页签角标用于整轮复核。主题集拥有活动实例、每日幂等键与调度。
"""
from __future__ import annotations

import re
import time

SCENE = 902
TABS = ("时装自选", "月影流光", "轩辕玄霆")


def return_state(button: str, progress: str) -> tuple[str, int, int]:
    """Parse a known return state; missing OCR never means already collected."""
    button = re.sub(r"\s+", "", button)
    match = re.search(r"消耗返还进度[：:]?(\d+)/(\d+)", re.sub(r"\s+", "", progress))
    if not match:
        raise ValueError(f"零元购返还进度未识别：{progress!r}")
    current, total = map(int, match.groups())
    if total <= 0 or not 0 <= current <= total:
        raise ValueError(f"零元购返还进度异常：{progress!r}")
    if current == total:
        return "complete", current, total
    if re.search(r"\d+(?:时|小时).*\d+分.*可领", button):
        return "waiting", current, total
    if button == "领取":
        return "claimable", current, total
    raise ValueError(f"零元购领取状态未识别：{button!r}")


def _read_state(context):
    frame = yield from context.wait_shape(SCENE, "返还说明", timeout=10)
    return return_state(
        context.ocr_text_in_shapes(SCENE, ["领取状态"], padding=0, frame_data_url=frame),
        context.ocr_text_in_shapes(SCENE, ["返还进度"], padding=0, frame_data_url=frame),
    )


def collect_zero_purchase_returns(context):
    """Re-enter from any safe scene and visit every tab; claim at most once per tab.

    Failures preserve the scene and do not write completion. A rerun rereads
    the countdown/progress, so a successful claim is never blindly repeated.
    """
    yield from context.go_scene(34)
    yield from context.wait_click_ocr_text(
        34, "零元购", in_shapes=["左侧菜单"], max_scrolls_per_direction=0,
    )
    yield from context.wait_scene_exact([SCENE], timeout=15)
    claimed = 0
    states = {}
    for tab in TABS:
        yield from context.wait_click(SCENE, tab)
        yield from context.wait_action_settle(0.6)
        yield from context.wait_scene_exact([SCENE], timeout=15)
        state, before, total = yield from _read_state(context)
        if state == "claimable":
            yield from context.wait_click(SCENE, "领取")
            deadline = time.monotonic() + 15
            while True:
                yield from context.wait_scene_exact([SCENE], timeout=10)
                state, current, total = yield from _read_state(context)
                if state in ("waiting", "complete") and current == before + 1:
                    claimed += 1
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"零元购/{tab}：领取后未确认返还进度增加")
                yield from context.wait_action_settle(0.5)
        else:
            current = before
        states[tab] = {"state": state, "returned": current, "total": total}
    yield from context.wait_scene_exact([SCENE], timeout=15)
    badges = context.ocr_text_in_shapes(SCENE, ["页签领取标记"], padding=0, crop=True)
    if "领" in badges:
        raise RuntimeError("零元购：逐页处理后仍有领取角标，保留现场")
    yield from context.wait_click(SCENE, "返回")
    yield from context.wait_scene_exact([34], timeout=15)
    return {"result": "success", "claimed": claimed, "tabs": states, "final_scene": 34}
