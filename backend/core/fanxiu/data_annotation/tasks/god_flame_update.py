"""神焰每日处理：右、中、左各先强化后升阶，最后外层炼化。"""
from backend.core.fanxiu.runtime_gui.god_flame import (
    MAIN, STRENGTH, STAGE, SUCCESS, consume_flame, flame_lines, text_in,
)

STAGE_ID = 'god-flame-update'
STAGE_VERSION = '1'
FLAMES = (('右侧类型', '太阳真火'), ('中间类型', '紫罗极火'), ('左侧类型', '修罗圣火'))


def require_main(context):
    """Use the main-page label, rather than accepting wait_scene's unrelated result."""
    for _ in range(20):
        rows = flame_lines(context)
        if '炼化进度' in text_in(context, MAIN, '炼化进度', rows):
            return
        if any('点击屏幕继续' in row['text'] for row in rows):
            context.click_shape_center_fast(SUCCESS, '继续')
        yield from context.wait_action_settle(.5)
    raise RuntimeError('神焰主页面未就绪')


def update_god_flames(context):
    """Rebuild from visible state; retries never rely on a previous tab cursor."""
    scene = int((yield from context.wait_scene([MAIN, STAGE, STRENGTH, SUCCESS, 770, 34, 661], wait=20)))
    if scene in (STAGE, STRENGTH):
        context.click_shape_center(scene, '返回背景')
        yield from context.wait_action_settle(2)
    elif scene == SUCCESS:
        context.click_shape_center(SUCCESS, '继续')
        yield from context.wait_action_settle(2)
        scene = int((yield from context.wait_scene([MAIN, STAGE, STRENGTH], wait=10)))
        if scene in (STAGE, STRENGTH):
            context.click_shape_center(scene, '返回背景')
            yield from context.wait_action_settle(2)
    elif scene != MAIN:
        yield from context.go_scene(MAIN)
    yield from require_main(context)
    receipts = []
    for entry, name in FLAMES:
        yield from require_main(context)
        context.click_shape_center(MAIN, entry)
        yield from context.wait_action_settle(2)
        receipts.append((yield from consume_flame(context, name, STRENGTH, '炼化')))
        context.click_shape_center(STRENGTH, '升阶页签')
        yield from context.wait_action_settle(2)
        receipts.append((yield from consume_flame(context, name, STAGE, '升阶')))
        context.click_shape_center(STAGE, '返回背景')
        yield from context.wait_action_settle(2)
    yield from require_main(context)
    receipts.append((yield from consume_flame(context, '乾蓝冰焰', MAIN, '炼化')))
    context.click_shape_center(MAIN, '返回')
    yield from context.wait_click(770, '返回')
    scene = int((yield from context.wait_scene([34, 661], wait=20)))
    if scene not in (34, 661):
        raise RuntimeError(f'神焰处理后未回到世界：#{scene}')
    return dict(result='success', outcome='complete', receipts=receipts)
