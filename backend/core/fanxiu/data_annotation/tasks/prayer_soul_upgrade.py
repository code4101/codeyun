"""Scene #455 prayer soul casting: inspect the current screen before each action.

Each run is independent. A click is complete only after the displayed material
count falls by the displayed cost; retries resume from the game's current UI.
"""

from __future__ import annotations

import re
from typing import Any, Callable


SCENE = 455
MAIN_TAB = (449.0, 1430.0)
SOUL_BUTTONS = {"天": (680.0, 190.0), "地": (780.0, 340.0), "人": (780.0, 530.0)}
MIN_LEVEL = 29


def completed_level(fragments: list[dict[str, Any]]) -> int | None:
    """The +N row aligned with 圆满 is the selected soul's present level."""

    complete = [
        item for item in fragments
        if "圆满" in str(item.get("text") or "")
        and float(item.get("x") or 0) >= 600
        and 900 <= float(item.get("y") or 0) <= 1150
    ]
    if len(complete) != 1:
        return None
    row_y = float(complete[0]["y"])
    levels = {
        int(str(item["text"])[1:])
        for item in fragments
        if re.fullmatch(r"\+\d{1,3}", str(item.get("text") or ""))
        and float(item.get("x") or 0) < 150
        and abs(float(item.get("y") or 0) - row_y) <= 18
    }
    return next(iter(levels)) if len(levels) == 1 else None


def cast_controls(fragments: list[dict[str, Any]]) -> tuple[str, tuple[int, int] | None, dict[str, Any] | None]:
    actions = [
        item for item in fragments
        if str(item.get("text") or "") in {"前往铸魂", "补魂"}
        and 340 <= float(item.get("x") or 0) <= 550
        and 1200 <= float(item.get("y") or 0) <= 1320
    ]
    counts = [
        item for item in fragments
        if re.fullmatch(r"\d+/\d+", str(item.get("text") or ""))
        and 450 <= float(item.get("x") or 0) <= 620
        and 1150 <= float(item.get("y") or 0) <= 1250
    ]
    action = actions[0] if len(actions) == 1 else None
    amount = tuple(map(int, str(counts[0]["text"]).split("/"))) if len(counts) == 1 else None
    return str(action.get("text") or "") if action else "", amount, action


def _click(context: Any, action: dict[str, Any]) -> None:
    context.click_frame_point(
        SCENE,
        float(action["x"]) + float(action["w"]) / 2,
        float(action["y"]) + float(action["h"]) / 2,
    )


def _select(context: Any, soul: str):
    context.click_frame_point(SCENE, *MAIN_TAB)
    yield from context.wait_action_settle(0.5)
    context.click_frame_point(SCENE, *SOUL_BUTTONS[soul])
    for _ in range(12):
        yield from context.wait_action_settle(0.4)
        frame = context.cur_frame(update=True)
        fragments = context.ocr_fragments(frame)
        if not any(f"{soul}魂铸魂" in str(item.get("text") or "") for item in fragments):
            continue
        level = completed_level(fragments)
        if level is not None:
            return level
    raise RuntimeError(f"祈愿铸魂：{soul}魂当前等级无法确认")


def _material(context: Any, soul: str):
    for _ in range(12):
        frame = context.cur_frame(update=True)
        fragments = context.ocr_fragments(frame)
        label, amount, action = cast_controls(fragments)
        if label == "前往铸魂" and action is not None:
            if not any(f"{soul}魂铸魂" in str(item.get("text") or "") for item in fragments):
                yield from context.wait_action_settle(0.35)
                continue
            _click(context, action)
            yield from context.wait_action_settle(0.8)
            continue
        if label == "补魂" and amount is not None and action is not None:
            return amount, action
        yield from context.wait_action_settle(0.35)
    raise RuntimeError(f"祈愿铸魂：{soul}魂材料或补魂按钮未就绪")


def _spend_one(context: Any, soul: str, amount: tuple[int, int], action: dict[str, Any]):
    have, cost = amount
    if cost <= 0 or have < cost:
        raise RuntimeError(f"祈愿铸魂：{soul}魂当前不可补魂 {amount}")
    _click(context, action)
    for _ in range(16):
        yield from context.wait_action_settle(0.35)
        frame = context.cur_frame(update=True)
        _, next_amount, _ = cast_controls(context.ocr_fragments(frame))
        if next_amount == (have - cost, cost):
            return
        if next_amount is not None and next_amount != amount:
            raise RuntimeError(f"祈愿铸魂：材料变化异常 {amount}→{next_amount}")
    raise RuntimeError(f"祈愿铸魂：{soul}魂补魂后材料未减少，停止重复点击")


def upgrade_prayer_souls(
    context: Any, *, check_stopped: Callable[[], None], log: Callable[[str], None],
):
    """Choose one soul from current levels, then exhaust material on it."""

    spent = 0
    target = "地"
    selected = ""
    for soul in ("天", "地", "人"):
        check_stopped()
        level = yield from _select(context, soul)
        selected = soul
        if level < MIN_LEVEL:
            target = soul
            break
    if selected != target:
        yield from _select(context, target)
    log(f"祈愿铸魂：本周期选择{target}魂")

    while True:
        check_stopped()
        amount, action = yield from _material(context, target)
        have, cost = amount
        if cost <= 0:
            raise RuntimeError(f"祈愿铸魂：无效材料消耗 {amount}")
        if have < cost:
            log(f"祈愿铸魂：{target}魂材料 {have}/{cost}，本次已消耗 {spent}")
            return {"soul": target, "spent": spent, "remaining": have, "cost": cost}
        yield from _spend_one(context, target, amount, action)
        spent += cost
        log(f"祈愿铸魂：{target}魂补魂一次，材料 {have}→{have-cost}")
