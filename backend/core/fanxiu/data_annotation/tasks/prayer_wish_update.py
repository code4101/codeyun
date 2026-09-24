"""Use current prayer pearls until the displayed balance is below the cost."""

from __future__ import annotations

from typing import Any, Callable


PRAYER_MAIN = 455
PRAYER_WISH = 833
PRAYER_SUCCESS = 834


def wish_balance(tokens: list[dict[str, Any]]) -> tuple[int, int] | None:
    """Parse the single `balance / cost` row from its annotated Shape."""

    values = [str(token.get("text") or "").strip() for token in tokens]
    if len(values) == 3 and values[0].isdigit() and values[1] == "/" and values[2].isdigit():
        return int(values[0]), int(values[2])
    return None


def _read_balance(context: Any):
    for _ in range(8):
        frame = context.cur_frame(update=True)
        amount = wish_balance(context.ocr_tokens_in_shapes(
            PRAYER_WISH, ("神珠数量",), frame_data_url=frame,
        ))
        if amount is not None:
            return amount
        yield from context.wait_action_settle(0.35)
    raise RuntimeError("祈愿突破：神珠余额和消耗未识别")


def use_prayer_pearls(
    context: Any, *, check_stopped: Callable[[], None], log: Callable[[str], None],
):
    """Resume from #455/#833/#834; return to #455 after using affordable pearls."""

    match = yield from context.wait_scene(
        [PRAYER_MAIN, PRAYER_WISH, PRAYER_SUCCESS], wait=8.0,
        label="祈愿更新：确认祈愿当前场景",
    )
    scene = match.scene_id
    if scene == PRAYER_SUCCESS:
        context.click_shape_center(PRAYER_SUCCESS, "返回")
        yield from context.wait_scene([PRAYER_WISH], wait=12.0, label="祈愿更新：关闭祈愿成功页")
    elif scene == PRAYER_MAIN:
        context.click_shape_center(PRAYER_MAIN, "祈愿")
        yield from context.wait_scene([PRAYER_WISH], wait=12.0, label="祈愿更新：进入祈愿突破页")

    breakthroughs = 0
    previous_balance: int | None = None
    while True:
        check_stopped()
        have, cost = yield from _read_balance(context)
        if cost <= 0:
            raise RuntimeError(f"祈愿突破：消耗值无效 {have}/{cost}")
        if previous_balance is not None and have >= previous_balance:
            raise RuntimeError(f"祈愿突破：突破后神珠未减少 {previous_balance}→{have}")
        if have < cost:
            context.click_shape_center(PRAYER_WISH, "返回")
            yield from context.wait_scene([PRAYER_MAIN], wait=12.0, label="祈愿更新：突破后返回 #455")
            log(f"祈愿突破：完成 {breakthroughs} 次，神珠 {have}/{cost}")
            return {"breakthroughs": breakthroughs, "remaining": have, "cost": cost}
        context.click_shape_center(PRAYER_WISH, "突破")
        yield from context.wait_scene([PRAYER_SUCCESS], wait=12.0, label="祈愿更新：确认突破成功")
        previous_balance = have
        breakthroughs += 1
        context.click_shape_center(PRAYER_SUCCESS, "返回")
        yield from context.wait_scene([PRAYER_WISH], wait=12.0, label="祈愿更新：继续检查神珠")
