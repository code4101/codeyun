"""天赋技能树每日使用：既定树优先，树内从上到下、从左到右。

已选分支沿用；未选默认最左。未来显示的新树排在六棵既有树之后，
但未知消耗不自动授权。完成意味着所有显示树均无可用材料能升级的节点。
"""
from __future__ import annotations

import time

from backend.core.fanxiu.instrumentation.item_resources import read_item_available_counts
from backend.core.fanxiu.instrumentation.role_talent import read_role_talent_tree, read_role_talent_popup
from backend.core.fanxiu.behavior_tree.errors import SceneClickMismatch
from backend.core.fanxiu.runtime_gui.role_talent import (
    TREE_SCENE, DETAIL_SCENE, CHOICE_SCENE, TalentPageInterrupted, wait_talent_scene,
    role_talent_nodes, select_role_talent_tab, enter_role_talent_node,
)
from backend.core.fanxiu.runtime_gui.skill_tree import first_upgradeable_node

STAGE_ID = 'role-talent-update'
STAGE_VERSION = '1'
TALENT_PRIORITY = (1, 3, 2, 4, 5, 6)  # 金乌、鲲鹏、月蟾、渊蛟、麒麟、九尾
TALENT_MATERIALS = frozenset({14, 41})  # 道心、乾坤道元；不授权代币或未知材料


def ordered_talent_tabs(tabs):
    priorities = {ident:i for i,ident in enumerate(TALENT_PRIORITY)}
    return sorted(tabs,key=lambda t:(priorities.get(t['type'],len(priorities)),t['type']))


def validate_talent_costs(nodes):
    for node in nodes:
        if set(node['costs']) - TALENT_MATERIALS:
            raise RuntimeError(f"天赋出现未授权消耗：{node['costs']}")


def close_talent_detail(context):
    yield from context.wait_click(DETAIL_SCENE,'返回')
    yield from wait_talent_scene(context,[TREE_SCENE])


def upgrade_talent_node(context, tab_type, target, counts):
    """One click, one native level receipt; uncertain clicks are never replayed."""
    validate_talent_costs([target])
    if not target['unlocked'] or any(counts[k] < need for k,need in target['costs'].items()):
        raise RuntimeError('天赋材料模型不足，禁止点击')
    if target['requires_choice']:
        yield from wait_talent_scene(context,[CHOICE_SCENE])
        before=read_role_talent_popup(choice=True)
        if before['candidates'][0] != target['talent_id'] or target['level'] != 0:
            raise RuntimeError('天赋左侧抉择身份不符')
        if before['selected_index'] != 1:
            yield from context.wait_click(CHOICE_SCENE,'左侧分支')
        selected=read_role_talent_popup(choice=True)
        if selected['selected_index'] != 1 or selected['id'] != target['talent_id'] or selected['can_activate'] is not True:
            raise RuntimeError('天赋左侧分支未就绪')
        # The locked live sample proves selection, not activation. The button
        # is located by its OCR text only once prerequisites make it visible.
        yield from context.wait_click(CHOICE_SCENE,'激活')
    else:
        yield from wait_talent_scene(context,[DETAIL_SCENE])
        before = read_role_talent_popup()
        if before['id'] != target['talent_id'] or before['level'] != target['level']:
            raise RuntimeError('天赋详情与目标等级不符')
        if not before['can_activate'] or not before['can_click'] or before['is_max']:
            raise RuntimeError('天赋详情可升级状态与模型冲突')
        yield from context.wait_click(DETAIL_SCENE,'升级')
    deadline = time.monotonic()+20
    while True:
        tree = read_role_talent_tree()
        if tree['selected_type'] != tab_type:
            raise RuntimeError('升级后天赋页签改变，结果待同步')
        after = next(n for n in role_talent_nodes(tree['tabs'][0]) if n['id']==target['id'])
        if after['talent_id'] != target['talent_id']:
            raise RuntimeError('升级后分支改变')
        if after['level'] == target['level']+1:
            for item,need in target['costs'].items():
                counts[item] -= need
            return tree['tabs'][0],dict(type=tab_type,node_id=target['id'],talent_id=target['talent_id'],
                                      before=target['level'],after=after['level'])
        if after['level'] != target['level'] or time.monotonic() >= deadline:
            raise RuntimeError('天赋升级结果未确认，禁止重复发送')
        yield from context.wait_action_settle(.3)


def update_role_talents(context):
    """Retry only proven pre-click interruptions; never replay an uncertain spend."""
    for attempt in range(4):
        try:
            return (yield from _update_role_talents(context))
        except (TalentPageInterrupted, SceneClickMismatch):
            if attempt==3:
                raise
            # Native guards handle recognized battle settlement popups. The
            # next attempt reconstructs all levels and counts, not a cursor.
            yield from context.wait_action_settle(1)
    raise RuntimeError('天赋页面连续被打断')


def _update_role_talents(context):
    """Resume from real tree/stock, spend affordable resources, return to world.

    Each node is filled before choosing another row-first candidate. Completed
    tabs are not clicked. Battle interruptions before spending can restart from
    world; an uncertain sent upgrade raises without a second click.
    """
    scene = yield from wait_talent_scene(context,[TREE_SCENE,DETAIL_SCENE,CHOICE_SCENE,34,661,770])
    if scene==DETAIL_SCENE:
        # Confirm this is the role popup, not a visually similar 炼神 popup.
        read_role_talent_popup()
        yield from close_talent_detail(context)
    elif scene==CHOICE_SCENE:
        read_role_talent_popup(choice=True)
        yield from context.wait_click(CHOICE_SCENE,'返回')
        yield from wait_talent_scene(context,[TREE_SCENE])
    elif scene!=TREE_SCENE:
        from backend.core.fanxiu.runtime_gui.role_menu import enter_role_feature
        yield from enter_role_feature(context, '天赋')
        yield from wait_talent_scene(context,[TREE_SCENE])
    initial = read_role_talent_tree(all_tabs=True)
    counts = read_item_available_counts(TALENT_MATERIALS,manager_key='role-talent')[0]
    receipts, tabs = [], []
    for info in ordered_talent_tabs(initial['tabs']):
        tab = info
        validate_talent_costs(role_talent_nodes(tab))
        if first_upgradeable_node(role_talent_nodes(tab),counts) is None:
            tabs.append(dict(type=tab['type'],name=tab['name'],outcome='无可升级节点'))
            continue
        if read_role_talent_tree()['selected_type'] != tab['type']:
            tab = yield from select_role_talent_tab(context,tab)
        for _ in range(2048):
            nodes = role_talent_nodes(tab)
            validate_talent_costs(nodes)
            target = first_upgradeable_node(nodes,counts)
            if target is None:
                break
            yield from enter_role_talent_node(context,nodes,target)
            while True:
                tab,receipt = yield from upgrade_talent_node(context,tab['type'],target,counts)
                receipts.append(receipt)
                print(f"天赋 {tab['name']} #{receipt['talent_id']} {receipt['before']}→{receipt['after']}",flush=True)
                current = next(n for n in role_talent_nodes(tab) if n['id']==target['id'])
                validate_talent_costs([current])
                if current['level']==current['max_level'] or not current['unlocked'] or any(counts[k]<v for k,v in current['costs'].items()):
                    break
                if target['requires_choice']:
                    yield from enter_role_talent_node(context,role_talent_nodes(tab),current)
                target=current
            scene=yield from wait_talent_scene(context,[TREE_SCENE,DETAIL_SCENE])
            if scene==DETAIL_SCENE:
                yield from close_talent_detail(context)
        else:
            raise RuntimeError('天赋超过单树升级预算')
        tabs.append(dict(type=tab['type'],name=tab['name'],outcome='无可升级节点'))
    # Fresh terminal observation includes boss rewards/external resource gains.
    final = read_role_talent_tree(all_tabs=True)
    fresh_counts = read_item_available_counts(TALENT_MATERIALS,manager_key='role-talent')[0]
    pending=[]
    for tab in ordered_talent_tabs(final['tabs']):
        nodes=role_talent_nodes(tab)
        validate_talent_costs(nodes)
        if first_upgradeable_node(nodes,fresh_counts) is not None:
            pending.append(tab['type'])
    if pending:
        raise RuntimeError(f'天赋期间资源发生变化，仍有可升级树：{pending}')
    yield from context.wait_click(TREE_SCENE,'返回')
    yield from wait_talent_scene(context,[770])
    yield from context.wait_click(770,'返回')
    yield from wait_talent_scene(context,[34,661])
    return dict(result='success',outcome='complete',tabs=tabs,upgrades=receipts,remaining=fresh_counts)
