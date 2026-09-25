"""炼神 GUI 事务；每次动作先验证当前身份，副作用后验证原生等级。"""
from __future__ import annotations
import time
from ..instrumentation.lianshen_tree import read_lianshen_tree, read_lianshen_choice
from ..instrumentation.item_resources import read_item_available_counts
from ..instrumentation.lianshen_talent import read_lianshen_talent_detail
from ..instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from .skill_tree import (
    first_upgradeable_node, locate_first_tree_node, tree_progress_candidates,
    tree_progress_labels,
)


def activate_lianshen_choice(context):
    """未选分支按用户策略选左；既有分支由角色等级决定，不重选。"""
    yield from context.wait_scene([774], wait=10)
    before = read_lianshen_choice()
    tree = read_lianshen_tree(all_tabs=True)
    tab = next(t for t in tree['tabs'] if t['type'] == before['type'])
    node = next(n for n in tab['nodes'] if [v['talent_id'] for v in n['variants']] == before['candidates'])
    if not node['requires_choice']:
        raise RuntimeError('炼神分支已激活，不能再次选择')
    target = node['variants'][0]
    counts, _ = read_item_available_counts(target['costs'], manager_key='lianshen-choice')
    if not tab['tab_unlocked'] or not target['unlocked'] or any(counts[k] < v for k,v in target['costs'].items()):
        raise RuntimeError('炼神抉择当前不可升级')
    if before['selected_index'] != 1:
        yield from context.wait_click(774, '左侧分支')
    selected = read_lianshen_choice()
    if selected['selected_id'] != target['talent_id'] or selected['can_activate'] is not True:
        raise RuntimeError('炼神左侧抉择未就绪')
    yield from context.wait_click(774, '激活')
    yield from context.wait_scene([771], wait=15)
    after = read_lianshen_tree(all_tabs=True)
    current = next(n for t in after['tabs'] for n in t['nodes'] if n['id'] == node['id'])
    active = [v for v in current['variants'] if v['active']]
    if len(active) != 1 or active[0]['talent_id'] != target['talent_id'] or active[0]['current_level'] != target['current_level'] + 1:
        raise RuntimeError('炼神抉择结果未确认，禁止重复激活')
    return {'tab_type':tab['type'],'node_id':node['id'],'talent_id':target['talent_id'],'steps':1,'level':active[0]['current_level']}


def _node_facts(tab):
    """Adapt native CList order to the shared row-first tree contract."""
    nodes = []
    for node in tab['nodes']:
        variant = next((v for v in node['variants'] if v['active']), node['variants'][0])
        nodes.append({
            'id': node['id'], 'y': node['ordinal'], 'x': 0,
            'column': (node['index']-1) % 3,
            'level': variant['current_level'], 'max_level': variant['max_level'],
            'costs': variant['costs'], 'unlocked': variant['unlocked'],
            'talent_id': variant['talent_id'], 'requires_choice': node['requires_choice'],
        })
    return nodes


def _close_detail(context):
    """Use the confirmed empty background on #773, clear of tree nodes."""
    box = context.shape(773, '返回').box()
    context.click_frame_point(773, box['x']+box['w']/2, box['y']+box['h']/2)
    yield from context.wait_action_settle(2)


def _enter_node(context, nodes, target):
    """Find the first candidate through overlapping views, then verify its ID."""
    # A previous upgrade may leave its detail open while the tree remains
    # visible to Runtime. Reuse that exact popup instead of OCRing the blur.
    if not target['requires_choice']:
        try:
            open_detail = read_lianshen_talent_detail()
        except FanxiuRuntimeMemoryError:
            open_detail = None
        if open_detail is not None:
            if open_detail['id'] == target['talent_id']:
                return 'detail'
            yield from _close_detail(context)
    box = context.shape(771, '节点窗口').box()
    viewport = (box['x'], box['y'], box['w'], box['h'])

    def observe():
        return tree_progress_labels(
            context.full_frame_ocr_tokens(context.cur_frame(update=True)),
            viewport=viewport, include_choice=True,
        )

    def scroll(direction):
        yield from context.scroll_shape_content(
            context.view(771), '节点窗口', direction=direction, ratio=.25,
        )

    columns = {node['id']: node['column'] for node in nodes}

    def match_labels(ordered, labels):
        candidates = tree_progress_candidates(ordered, labels)
        aligned = [
            mapping for mapping in candidates
            if all(abs(label['x']+label['w']/2 -
                       (viewport[0]+viewport[2]*(columns[ident]+.5)/3))
                   < viewport[2]/6 for ident, label in mapping.items())
        ]
        if not aligned:
            raise ValueError('炼神 OCR 序列的列位置与原生格子不符')
        return aligned

    # Combat text can cover the progress labels for longer than the locator's
    # three quick OCR samples. A later clear frame still has an exact Runtime
    # window match; reobserve before any node click or material spend.
    deadline = time.monotonic() + 60
    while True:
        try:
            found = yield from locate_first_tree_node(
                context, nodes=nodes, target_id=target['id'],
                observe_labels=observe, scroll=scroll, match_labels=match_labels,
            )
            break
        except ValueError:
            if time.monotonic() >= deadline:
                raise
            yield from context.wait_action_settle(.5)
    label = found['label']
    context.click_frame_point(771, label['x']+label['w']/2, label['y']-label['h'])
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        try:
            if target['requires_choice']:
                choice = read_lianshen_choice()
                if target['talent_id'] not in choice['candidates']:
                    yield from context.wait_action_settle(.3)
                    continue
                return 'choice'
            detail = read_lianshen_talent_detail()
            if detail['id'] != target['talent_id']:
                # The popup object can briefly retain the preceding talent.
                yield from context.wait_action_settle(.3)
                continue
            return 'detail'
        except FanxiuRuntimeMemoryError:
            yield from context.wait_action_settle(.3)
    raise TimeoutError('炼神节点详情未就绪；点击结果不明，保留现场')


def _fill_detail(context, talent_id, *, max_clicks=100):
    """Read current detail and stock before each click; accept native level jumps."""
    def completed_in_tree():
        tab = read_lianshen_tree()
        variant = next((v for node in tab['nodes'] for v in node['variants']
                        if v['talent_id'] == talent_id), None)
        return variant is not None and variant['current_level'] == variant['max_level']

    clicks = 0
    while clicks < max_clicks:
        try:
            before = read_lianshen_talent_detail()
        except FanxiuRuntimeMemoryError:
            if completed_in_tree():
                return {'clicks': clicks, 'reason': '满级且详情已关闭'}
            raise
        if before['id'] != talent_id:
            raise RuntimeError('炼神详情身份改变，保留现场')
        if before['level'] == before['max_level']:
            return {'clicks': clicks, 'level': before['level'], 'reason': '满级'}
        counts, _ = read_item_available_counts(before['costs'], manager_key='lianshen')
        if any(counts[item] < need for item, need in before['costs'].items()):
            return {'clicks': clicks, 'level': before['level'], 'reason': '材料不足'}
        if not before['can_click'] or not before['can_activate']:
            raise RuntimeError('炼神详情与库存的可升级状态冲突，保留现场')
        # Runtime proves the popup identity; the asset supplies fixed button geometry.
        context.click_shape_center_fast(773, '升级')
        clicks += 1
        deadline = time.monotonic()+15
        while True:
            try:
                after = read_lianshen_talent_detail()
            except FanxiuRuntimeMemoryError:
                if completed_in_tree():
                    return {'clicks': clicks, 'reason': '满级且详情已关闭'}
                if time.monotonic() >= deadline:
                    raise
                yield from context.wait_action_settle(.3)
                continue
            if after['id'] != talent_id:
                raise RuntimeError('炼神升级后详情身份改变，保留现场')
            if after['level'] > before['level']:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('炼神升级结果未确认，禁止重复点击')
            yield from context.wait_action_settle(.3)
    raise RuntimeError('炼神单节点超过升级次数上限')


def fill_current_lianshen_tab(context, *, expected_type=3, initial_tab=None):
    """Spend available material in native row-first order without saved cursor."""
    receipts = []
    tab = initial_tab
    for _ in range(256):
        if tab is None:
            tab = read_lianshen_tree()
        if tab['type'] != expected_type or not tab['tab_unlocked']:
            raise RuntimeError('炼神页签身份或解锁状态改变')
        nodes = _node_facts(tab)
        item_ids = {item for n in nodes for item in n['costs']}
        counts, _ = read_item_available_counts(item_ids, manager_key='lianshen')
        target = first_upgradeable_node(nodes, counts)
        if target is None:
            return {'type': expected_type, 'receipts': receipts, 'remaining': counts,
                    'reason': '无可升级节点'}
        kind = yield from _enter_node(context, nodes, target)
        if kind == 'choice':
            result = yield from activate_lianshen_choice(context)
        else:
            result = yield from _fill_detail(context, target['talent_id'])
            try:
                detail_still_open = read_lianshen_talent_detail()
            except FanxiuRuntimeMemoryError:
                detail_still_open = None
            if detail_still_open is not None:
                if detail_still_open['id'] != target['talent_id']:
                    raise RuntimeError('炼神结束节点身份改变，保留现场')
                yield from _close_detail(context)
        receipts.append({'node_id': target['id'], 'talent_id': target['talent_id'],
                         'result': result})
        after = read_lianshen_tree()
        current = next((n for n in _node_facts(after) if n['id'] == target['id']), None)
        if current is None or current['level'] <= target['level']:
            raise RuntimeError('炼神节点未取得升级进展，保留现场')
        tab = after
    raise RuntimeError('炼神升级超过树节点预算')
