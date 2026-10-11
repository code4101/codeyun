"""Use current prayer pearls until the displayed balance is below the cost."""

from __future__ import annotations

from typing import Any, Callable


PRAYER_MAIN = 455
PRAYER_WISH = 833
PRAYER_SUCCESS = 834


def prayer_soul_tab(fragments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """祈愿共享壳的仙谕页签；任务页也匹配 #455，须先选择实际主页。"""
    candidates = [row for row in fragments
                  if ''.join(str(row.get('text') or '').split()).endswith('仙谕')
                  and 350 <= float(row.get('x') or 0) <= 530
                  and float(row.get('y') or 0) >= 1200
                  and float(row.get('w') or 0) > 0
                  and float(row.get('h') or 0) > 0]
    return candidates[0] if len(candidates) == 1 else None


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
    maximum_actions: int | None = None, maximum_spend: int | None = None,
):
    """返回#455；默认使用可负担神珠，研究可显式限制动作数和总消耗。

    限额在点击前检查，每次动作后仍核验实际余额下降。限额到达也返回主界面，
    不通过中断generator截断已经消费的动作。
    """
    for name, value in (('maximum_actions', maximum_actions), ('maximum_spend', maximum_spend)):
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError(f'{name}需要正整数或None')

    match = yield from context.wait_scene(
        [PRAYER_MAIN, PRAYER_WISH, PRAYER_SUCCESS], wait=8.0,
        label="祈愿更新：确认祈愿当前场景",
    )
    scene = match.scene_id
    if scene == PRAYER_SUCCESS:
        context.click_shape_center(PRAYER_SUCCESS, "返回")
        yield from context.wait_scene_exact([PRAYER_WISH], timeout=30.0, observation_scenes=[PRAYER_MAIN, PRAYER_SUCCESS], label="祈愿更新：关闭祈愿成功页")
    elif scene == PRAYER_MAIN:
        # #455 是任务、积分获取和仙谕共用壳。祈愿按钮只在仙谕页，
        # 不能将任务页的场景命中当作按钮已可点击。
        from .prayer_daily_resource import fragment_center
        tab = None
        for _ in range(8):
            check_stopped()
            tab = prayer_soul_tab(context.ocr_fragments(context.cur_frame(update=True)))
            if tab is not None:
                break
            yield from context.wait_action_settle(0.35)
        if tab is None:
            raise RuntimeError('祈愿突破：未唯一定位仙谕主页页签')
        context.click_frame_point(PRAYER_MAIN, *fragment_center(tab))
        yield from context.wait_action_settle(1.0)
        # 正式导航统一等待真实落点；固定点击不能表达过渡页处理。
        yield from context.go_scene(PRAYER_WISH)

    breakthroughs = 0
    spent = 0
    previous_balance: int | None = None
    while True:
        check_stopped()
        have, cost = yield from _read_balance(context)
        if cost <= 0:
            raise RuntimeError(f"祈愿突破：消耗值无效 {have}/{cost}")
        if previous_balance is not None and have >= previous_balance:
            raise RuntimeError(f"祈愿突破：突破后神珠未减少 {previous_balance}→{have}")
        if (have < cost or maximum_actions is not None and breakthroughs >= maximum_actions
                or maximum_spend is not None and spent + cost > maximum_spend):
            context.click_shape_center(PRAYER_WISH, "返回")
            yield from context.wait_scene_exact([PRAYER_MAIN], timeout=30.0, observation_scenes=[PRAYER_WISH], label="祈愿更新：突破后返回 #455")
            log(f"祈愿突破：完成 {breakthroughs} 次，神珠 {have}/{cost}")
            return {"breakthroughs": breakthroughs, "remaining": have, "cost": cost, "spent": spent}
        context.click_shape_center(PRAYER_WISH, "突破")
        yield from context.wait_scene_exact([PRAYER_SUCCESS], timeout=30.0, observation_scenes=[PRAYER_WISH], label="祈愿更新：确认突破成功")
        previous_balance = have
        breakthroughs += 1
        spent += cost
        context.click_shape_center(PRAYER_SUCCESS, "返回")
        yield from context.wait_scene_exact([PRAYER_WISH], timeout=30.0, observation_scenes=[PRAYER_MAIN, PRAYER_SUCCESS], label="祈愿更新：继续检查神珠")
