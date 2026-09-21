"""玄机阁 GUI 适配：配置顺序、当前 OCR、详情身份三者互证后加点。"""
from __future__ import annotations

import time

from ..instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from ..instrumentation.xianfu_science import read_xianfu_science
from ..instrumentation.item_resources import read_item_available_counts
from .skill_tree import fill_available_tree, fill_tree_node, locate_first_tree_node, tree_progress_labels, tree_spatial_progress_candidates


def enter_first_xianfu_science_node(context, *, target_id=None):
    """Find the global first incomplete node; inspect before spending anything."""
    yield from context.wait_scene([768], wait=10)
    tree = read_xianfu_science()
    box = context.shape(768, '节点窗口').box()
    viewport = tuple(box[k] for k in ('x', 'y', 'w', 'h'))

    def observe():
        frame = context.cur_frame(update=True)
        return tree_progress_labels(context.full_frame_ocr_tokens(frame), viewport=viewport)

    def scroll(direction):
        yield from context.wait_scene([768], wait=10)
        yield from context.scroll_shape_content(context.view(768), '节点窗口', direction=direction)

    found = yield from locate_first_tree_node(context, nodes=tree['nodes'], observe_labels=observe,
        scroll=scroll, match_labels=tree_spatial_progress_candidates, target_id=target_id)
    if found is None:
        return {'reason': '全树已满'}
    label = found['label']
    icon = context.shape(768, '节点模板/图标').box()
    progress = context.shape(768, '节点模板/进度').box()
    # Both reference boxes belong to the same observed node template. The
    # actual label is reacquired after every scroll; no scroll offset persists.
    x = label['x']+label['w']/2 + icon['x']+icon['w']/2-progress['x']-progress['w']/2
    y = label['y']+label['h']/2 + icon['y']+icon['h']/2-progress['y']-progress['h']/2
    if not box['x'] <= x <= box['x']+box['w'] or not box['y'] <= y <= box['y']+box['h']:
        raise RuntimeError('玄机阁目标图标不在可点击窗口')
    context.click_frame_point(768, x, y)
    yield from context.wait_scene([769], wait=10)
    deadline = time.monotonic()+15
    while True:
        try:
            detail = read_xianfu_science(detail=True)
        except FanxiuRuntimeMemoryError:
            if time.monotonic() >= deadline:
                raise
            yield from context.wait_action_settle(0.3)
            continue
        if detail['id'] != found['node']['id']:
            raise RuntimeError('玄机阁详情身份不符，禁止升级')
        return detail


def fill_current_xianfu_science_node(context):
    """Use owned material on the verified node, proving each level change."""
    yield from context.wait_scene([769], wait=10)
    detail = read_xianfu_science(detail=True)
    counts, _ = read_item_available_counts(detail['costs'], manager_key='xianfu-science')

    def click_upgrade():
        current = read_xianfu_science(detail=True)
        if current['id'] != detail['id'] or current['can_activate'] is not True or not current['unlocked']:
            raise RuntimeError('玄机阁升级状态改变，保留现场')
        yield from context.wait_click(769, '升级')

    return (yield from fill_tree_node(context, read_detail=lambda: read_xianfu_science(detail=True),
        click_upgrade=click_upgrade, expected_id=detail['id'], initial_counts=counts))


def fill_xianfu_science_tree(context):
    """Upgrade every currently affordable node, prioritizing row/column order."""
    yield from context.wait_scene([768], wait=10)

    def counts(items):
        return read_item_available_counts(items, manager_key='xianfu-science-tree')[0] if items else {}

    def leave_node():
        yield from context.wait_click(769, '关闭')
        yield from context.wait_scene([768], wait=10)

    return (yield from fill_available_tree(
        read_tree=read_xianfu_science, read_counts=counts,
        enter_node=lambda ident: enter_first_xianfu_science_node(context, target_id=ident),
        fill_node=lambda: fill_current_xianfu_science_node(context), leave_node=leave_node,
    ))
