"""炼神 GUI 事务；每次动作先验证当前身份，副作用后验证原生等级。"""
from __future__ import annotations
import time
from ..instrumentation.lianshen_tree import read_lianshen_tree, read_lianshen_choice
from ..instrumentation.item_resources import read_item_available_counts


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
