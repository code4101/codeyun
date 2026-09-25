"""神焰资产适配：批次之间读材料，升阶成功页关闭后重新观察。

点击位置只来自资产树；战斗浮字遮挡时等待，未确认材料变化时不重发。
"""
from __future__ import annotations

import re
import time
from collections import defaultdict

MAIN, SUCCESS, STAGE, STRENGTH = 852, 853, 854, 855


def flame_lines(context):
    groups = defaultdict(list)
    for token in context.full_frame_ocr_tokens(context.cur_frame(update=True)):
        groups[token['parent_line_id']].append(token)
    return [dict(text=''.join(t['text'] for t in sorted(ts, key=lambda t: t['order'])),
                 x=min(t['x'] for t in ts), y=min(t['y'] for t in ts))
            for ts in groups.values()]


def text_in(context, scene, shape, lines):
    box = context.shape(scene, shape).box()
    return ''.join(row['text'] for row in lines
                   if box['x'] <= row['x'] <= box['x']+box['w']
                   and box['y'] <= row['y'] <= box['y']+box['h'])


def observe_flame(context, name, scene, action, *, previous=None):
    """Wait through animation and success overlays without replaying a spend."""
    deadline = time.monotonic()+25
    while time.monotonic() < deadline:
        lines = flame_lines(context)
        if any('点击屏幕继续' in row['text'] for row in lines):
            # The native promotion overlay owns this control; reobserve after close.
            context.click_shape_center_fast(SUCCESS, '继续')
            yield from context.wait_action_settle(2)
            continue
        if name in text_in(context, scene, '神焰名称', lines) and action in text_in(context, scene, action, lines):
            ratio = re.fullmatch(r'(\d+)/(\d+)', text_in(context, scene, '材料比例', lines))
            if ratio:
                owned, cost = map(int, ratio.groups())
                if cost > 0 and (previous is None or owned < previous):
                    return owned, cost
        yield from context.wait_action_settle(.5)
    raise RuntimeError(f'{name} {action} 页面/材料变化未确认，保留现场')


def consume_flame(context, name, scene, action):
    """Half of the affordable count per batch; promotion is one click per overlay."""
    owned, cost = yield from observe_flame(context, name, scene, action)
    initial = owned
    batches = 0
    while owned >= cost:
        # 外层支持一键炼化，一次可能用完；升阶每次产生模态成功页。
        count = max(1, (owned//cost)//2) if scene == STRENGTH else 1
        for _ in range(count):
            context.click_shape_center_fast(scene, action)
            yield from context.wait_action_settle(.4)
        # 升阶先更新材料，再延迟展示成功动画；不能在动画到达前宣告结束。
        yield from context.wait_action_settle(6 if scene != STRENGTH else 1)
        owned, cost = yield from observe_flame(context, name, scene, action, previous=owned)
        batches += 1
        print(f'{name} {action}: {owned}/{cost}', flush=True)
    return dict(name=name, action=action, initial=initial, remaining=owned, cost=cost, batches=batches)
