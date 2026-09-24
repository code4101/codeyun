"""洗灵祈愿周的灵器背包快捷分解；只使用已验证的场景与 Shape。"""
from __future__ import annotations

from backend.core.fanxiu.prayer_cycle import current_prayer_cycle
from backend.core.fanxiu.instrumentation.spirit_artifact_equipped import (
    read_spirit_artifact_owned_runtime,
)


QUALITY_LABEL = '绝品及以下'
QUALITY_LIMIT = 5  # SpiritWareBagView 下拉配置中的绝品；真实背包 #823 已核对。


def _eligible_items(owned):
    inventory, equipped = owned['inventory'], owned['equipped']
    identity = (inventory.get('pid'), inventory.get('process_start_ticks'))
    if (inventory.get('complete') is not True or equipped.get('complete') is not True
            or not all(identity)
            or identity != (equipped.get('pid'), equipped.get('process_start_ticks'))):
        raise RuntimeError('灵器快捷分解需要同进程完整库存和装配引用')
    equipped_ids = {row['item_id'] for row in equipped['items']}
    rows = inventory['items']
    if len({row['item_id'] for row in rows}) != len(rows):
        raise RuntimeError('灵器库存实例 ID 重复')
    return {row['item_id'] for row in rows
            if row['item_id'] not in equipped_ids and row['quality'] <= QUALITY_LIMIT}


def _verify_decomposition(before, after, expected):
    old = {row['item_id']: row for row in before['inventory']['items']}
    new = {row['item_id']: row for row in after['inventory']['items']}
    if ((before['inventory']['pid'], before['inventory']['process_start_ticks']) !=
            (after['inventory']['pid'], after['inventory']['process_start_ticks'])
            or after['inventory'].get('complete') is not True
            or after['equipped'].get('complete') is not True):
        raise RuntimeError('快捷分解前后游戏进程或库存完整性变化')
    if set(old) - set(new) != expected or set(new) - set(old):
        raise RuntimeError('快捷分解移除的本体与绝品及以下未装配候选不一致')
    if before['equipped']['items'] != after['equipped']['items']:
        raise RuntimeError('快捷分解改变了已装配部件')
    if any(old[uid] != row for uid, row in new.items()):
        raise RuntimeError('快捷分解改变了保留部件的属性')


def decompose_spirit_artifact_bag_during_prayer(context):
    """本周非洗灵则 pass；洗灵周每次进入背包触发快捷分解。

    客户端只分解未装配件，选项明确为“绝品及以下”。确认前以 Runtime
    再读一次库存；动作后只允许该批候选消失。空批次仍真实点击一次，
    以库存和装备均未变化证明幂等完成。
    """
    cycle = current_prayer_cycle()
    if cycle != '洗灵':
        return {'status': 'pass', 'prayer_cycle': cycle, 'decomposed': 0}

    if (yield from context.wait_scene([666], wait=8)).scene_id != 666:
        raise RuntimeError('洗灵周背包分解要求灵器总览 #666')
    context.click_shape_center(666, '背包页签')
    if (yield from context.wait_scene([823], wait=15)).scene_id != 823:
        raise RuntimeError('灵器背包 #823 未就绪')

    def selected_quality():
        frame = context.cur_frame(update=True)
        return context.ocr_text_in_shapes(
            823, ['分解档位下拉'], crop=True, padding=3,
            frame_data_url=frame,
        ).strip().replace(' ', '')

    if QUALITY_LABEL not in selected_quality():
        context.click_shape_center(823, '分解档位下拉')
        if (yield from context.wait_scene([824], wait=10)).scene_id != 824:
            raise RuntimeError('灵器分解档位列表 #824 未就绪')
        frame = context.cur_frame(update=True)
        option = context.ocr_text_in_shapes(
            824, [QUALITY_LABEL], crop=True, padding=3,
            frame_data_url=frame,
        ).strip().replace(' ', '')
        if QUALITY_LABEL not in option:
            raise RuntimeError(f'分解档位未识别出{QUALITY_LABEL}：{option!r}')
        context.click_shape_center(824, QUALITY_LABEL)
        if (yield from context.wait_scene([823], wait=10)).scene_id != 823:
            raise RuntimeError('选择绝品及以下后未返回灵器背包')
    if QUALITY_LABEL not in selected_quality():
        raise RuntimeError('灵器背包当前分解档位不是绝品及以下')

    before = read_spirit_artifact_owned_runtime()
    expected = _eligible_items(before)
    context.click_shape_center(823, '执行快捷分解')
    if expected:
        if (yield from context.wait_scene([825], wait=12)).scene_id != 825:
            raise RuntimeError('存在可分解本体，但未出现快捷分解确认窗')
        frame = context.cur_frame(update=True)
        prompt = context.ocr_text_in_shapes(
            825, ['分解档位提示'], crop=True, padding=3,
            frame_data_url=frame,
        ).replace(' ', '')
        if QUALITY_LABEL not in prompt or current_prayer_cycle() != '洗灵':
            raise RuntimeError(f'分解确认内容或祈愿时机已变化：{prompt!r}')
        fresh = read_spirit_artifact_owned_runtime()
        if (fresh['inventory']['items'] != before['inventory']['items']
                or fresh['equipped']['items'] != before['equipped']['items']):
            raise RuntimeError('分解确认前灵器库存或装配已变化')
        context.click_shape_center(825, '确认')
        if (yield from context.wait_scene([823], wait=15)).scene_id != 823:
            raise RuntimeError('确认分解后未返回灵器背包')
    else:
        yield from context.wait_action_settle(1)
        if (yield from context.wait_scene([823, 825], wait=8)).scene_id != 823:
            raise RuntimeError('无候选却出现分解确认窗，保留现场')

    after = read_spirit_artifact_owned_runtime()
    _verify_decomposition(before, after, expected)
    context.click_shape_center(823, '返回')
    if (yield from context.wait_scene([34, 661], wait=15)).scene_id not in (34, 661):
        raise RuntimeError('灵器背包分解后未返回世界')
    return {'status': 'complete', 'prayer_cycle': cycle,
            'decomposed': len(expected)}
