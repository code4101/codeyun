"""炼神资源日常使用：实时页签、原生等级和库存决定每次动作。"""
from __future__ import annotations

import time

from backend.core.fanxiu.instrumentation.lianshen_tree import read_lianshen_tree
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from backend.core.fanxiu.runtime_gui.lianshen import fill_current_lianshen_tab

STAGE_ID = 'lianshen-update'
STAGE_VERSION = '1'
_TABS = {1: '阳神页签', 2: '阴神页签', 3: '元神页签', 4: '玉神页签'}


def _selected_tab():
    try:
        return read_lianshen_tree()
    except FanxiuRuntimeMemoryError:
        return None


def update_lianshen(context, *, tab_types=(1, 2, 3, 4)):
    """Visit selected tabs once, spend affordable material, return to world.

    The tab list is a business policy. Inside a tab there is no stored progress:
    the current native tree and wallet determine the next row-first node.
    """
    if not tab_types or any(t not in _TABS for t in tab_types):
        raise ValueError('炼神页签范围无效')
    tab = _selected_tab()
    if tab is None:
        from backend.core.fanxiu.runtime_gui.role_menu import enter_role_feature
        yield from enter_role_feature(context, '炼神')
        tab = read_lianshen_tree()
    receipts = []
    for tab_type in tab_types:
        if tab['type'] != tab_type:
            context.click_shape_center_fast(771, _TABS[tab_type])
            deadline = time.monotonic()+10
            while True:
                tab = read_lianshen_tree()
                if tab['type'] == tab_type:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'炼神页签 {tab_type} 未切换成功')
                yield from context.wait_action_settle(.3)
        receipts.append((yield from fill_current_lianshen_tab(
            context, expected_type=tab_type, initial_tab=tab,
        )))
    context.click_shape_center_fast(771, '返回')
    yield from context.wait_scene([770], wait=10)
    yield from context.wait_click(770, '返回')
    yield from context.wait_scene([34, 661], wait=10)
    return {'result': 'success', 'outcome': 'complete', 'tabs': receipts}
