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


def soul_badge_level(fragments: list[dict[str, Any]]) -> int | None:
    """Read +N inside one soul badge's annotated level region.

    A reached level can still have unfinished 补魂 rows. 圆满 describes
    that row's fill state, so it cannot establish the soul's current level.
    """
    levels = {
        int(str(item["text"])[1:])
        for item in fragments
        if re.fullmatch(r"\+\d{1,3}", str(item.get("text") or ""))
    }
    return next(iter(levels)) if len(levels) == 1 else None


def cast_controls(fragments: list[dict[str, Any]]) -> tuple[str, tuple[int, int] | None, dict[str, Any] | None]:
    actions = [
        item for item in fragments
        if str(item.get("text") or "") in {"前往铸魂", "补魂", "铸魂"}
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
        if not _selected_soul(context, frame, fragments, soul):
            continue
        badge = context.ocr_fragments_in_shapes(
            SCENE, [f"{soul}魂等级"], frame_data_url=frame, padding=0,
        )
        level = soul_badge_level(badge)
        if level is None:
            level = soul_badge_level(context.ocr_fragments_in_shapes(
                SCENE, [f"{soul}魂等级"], frame_data_url=frame, padding=0, crop=True,
            ))
        if level is not None:
            return level
    raise RuntimeError(f"祈愿铸魂：{soul}魂当前等级无法确认")


def _selected_soul(context: Any, frame: str, fragments, soul: str | None) -> bool:
    names = (soul,) if soul else tuple(SOUL_BUTTONS)
    if any(f"{name}魂铸魂" in str(item.get("text") or "") for name in names for item in fragments):
        return True
    # The unlocked next-star state says “N星铸魂至M段” instead of naming
    # the soul. Its independent title remains authoritative in both states.
    title = context.ocr_fragments_in_shapes(
        SCENE, ['当前魂标题'], frame_data_url=frame, padding=0, crop=True,
    )
    return any(str(item.get('text') or '') == f'{name}魂' for name in names for item in title)


def _material(context: Any, soul: str | None):
    for _ in range(12):
        frame = context.cur_frame(update=True)
        fragments = context.ocr_fragments(frame)
        label, amount, action = cast_controls(fragments)
        identity = _selected_soul(context, frame, fragments, soul)
        if label == "前往铸魂" and action is not None:
            if not identity:
                yield from context.wait_action_settle(0.35)
                continue
            _click(context, action)
            yield from context.wait_action_settle(0.8)
            continue
        if identity and label in {"补魂", "铸魂"} and amount is not None and action is not None:
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
    # All three souls share the same casting material. Check that one resource
    # before the more expensive level-based target selection.
    context.click_frame_point(SCENE, *MAIN_TAB)
    yield from context.wait_action_settle(0.5)
    shared_amount, _ = yield from _material(context, None)
    if shared_amount[1] <= 0:
        raise RuntimeError(f"祈愿铸魂：无效材料消耗 {shared_amount}")
    if shared_amount[0] < shared_amount[1]:
        log(f"祈愿铸魂：材料 {shared_amount[0]}/{shared_amount[1]}，无需选择目标")
        return {"soul": None, "spent": 0, "remaining": shared_amount[0], "cost": shared_amount[1]}

    level = yield from _select(context, "天")
    target = "天" if level < MIN_LEVEL else "地"
    selected = "天"
    if target != "天":
        for soul in ("地", "人"):
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
