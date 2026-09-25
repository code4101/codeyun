"""三皇灵威领悟：足额使用材料，未选神通默认最左。

独立每日组件；已实测左侧神通激活、连续领悟、材料不足及返回世界。
节点与余额由只读 Runtime 提供，点击复用 #856/#857 固定 Shape。
"""
from ...instrumentation.item_resources import read_item_available_counts
from ...instrumentation.three_emperors import read_three_emperors
from ...runtime_gui.role_menu import enter_role_feature
from ...runtime_gui.three_emperors import (
    MAIN, CHOICE, wait_emperors, choose_left, await_level, ComprehensionNotConfirmed,
)

STAGE_ID = 'three-emperors-comprehend'
STAGE_VERSION = '1'


def comprehend_three_emperors(context):
    """Resume by current page, keep costs in memory, verify final stock fresh."""
    scene = int((yield from context.wait_scene([MAIN, CHOICE, 770, 34, 661], wait=20)))
    if scene not in (MAIN, CHOICE):
        yield from enter_role_feature(context, '三皇灵威')
        scene = yield from wait_emperors(context, (MAIN,))
    stock = read_item_available_counts([36], manager_key='three-emperors')[0][36]
    initial = stock
    upgrades = 0
    retries = 0
    state = read_three_emperors()
    while True:
        if state['isMax'] is True:
            break
        if scene == CHOICE:
            selected = yield from choose_left(context)
            cost = selected['cost']
            enough = selected['isEnough']
        else:
            cost, enough = state['cost'], state['isEnough']
        if stock < cost:
            fresh = read_item_available_counts([36], manager_key='three-emperors')[0][36]
            if fresh >= cost:
                stock = fresh
                continue
            stock = fresh
            break
        if scene == MAIN and state['node']['skillGroup'] > 0:
            # This opens the choice page; it is not yet a material spend.
            context.click_shape_center_fast(MAIN, '领悟')
            scene = yield from wait_emperors(context, (CHOICE,))
            continue
        if enough is not True:
            raise RuntimeError('三皇库存与原生可领悟状态冲突')
        context.click_shape_center_fast(scene, '激活' if scene == CHOICE else '领悟')
        yield from context.wait_action_settle(1 if state['node']['nodeType'] == 1 else 4)
        try:
            state = yield from await_level(context, state)
        except ComprehensionNotConfirmed:
            # Animation may swallow a tap. Retry once only after proving that
            # neither the node nor the resource balance changed.
            yield from wait_emperors(context, (MAIN,))
            fresh_state = read_three_emperors()
            fresh_stock = read_item_available_counts([36], manager_key='three-emperors')[0][36]
            if retries or fresh_state['curLevel'] != state['curLevel'] or fresh_stock != stock:
                raise
            retries += 1
            state, scene = fresh_state, MAIN
            continue
        stock -= cost
        upgrades += 1
        retries = 0
        yield from context.wait_action_settle(1)
        scene = MAIN
    if scene == CHOICE:
        context.click_shape_center(CHOICE, '返回背景')
        yield from wait_emperors(context, (MAIN,))
    yield from context.wait_click(MAIN, '返回')
    yield from context.wait_click(770, '返回')
    world = int((yield from context.wait_scene([34, 661], wait=20)))
    if world not in (34, 661):
        raise RuntimeError('三皇领悟未返回世界')
    return dict(result='success', outcome='complete', initial=initial, remaining=stock,
                upgrades=upgrades, level=state['curLevel'], next_cost=state.get('cost'))
