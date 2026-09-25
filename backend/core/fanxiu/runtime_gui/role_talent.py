"""天赋技能树 GUI 适配：业务身份由 Runtime 证明，坐标由资产和 OCR 定位。"""
from __future__ import annotations

import time
from collections import defaultdict

from ..instrumentation.role_talent import read_role_talent_tree, read_role_talent_popup
from ..instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from .activity_bottom_tab import resolve_vertical_bottom_tab
from .skill_tree import locate_first_tree_node, tree_spatial_progress_candidates, tree_progress_labels

TREE_SCENE = 849
DETAIL_SCENE = 850
CHOICE_SCENE = 851


class TalentPageInterrupted(RuntimeError):
    """A known outside page replaced the tree; resume from current facts."""


def wait_talent_scene(context, scenes):
    match = yield from context.wait_scene(scenes, wait=20)
    if int(match) not in scenes:
        raise TalentPageInterrupted(f'天赋页面被打断，当前 #{int(match)}')
    return int(match)


def role_talent_nodes(tab):
    """Preserve an existing branch; otherwise choose the native leftmost variant."""
    result = []
    for node in tab['nodes']:
        v = next((v for v in node['variants'] if v['active']), node['variants'][0])
        result.append(dict(id=node['id'], x=(node['index']-1)%3,
                           y=(node['index']-1)//3, column=(node['index']-1)%3,
                           level=v['current_level'], max_level=v['max_level'],
                           costs=v['costs'], unlocked=v['unlocked'],
                           talent_id=v['talent_id'], requires_choice=node['requires_choice']))
    return result


def select_role_talent_tab(context, tab):
    """Resolve native visible name inside the bottom-menu asset, including new tabs."""
    yield from wait_talent_scene(context, [TREE_SCENE])
    deadline = time.monotonic()+20
    box = context.shape(TREE_SCENE, '底部页签').box()
    while True:
        grouped = defaultdict(list)
        tokens = context.full_frame_ocr_tokens(context.cur_frame(update=True))
        for token in tokens:
            if box['x'] <= token['x'] and token['x']+token['w'] <= box['x']+box['w'] and box['y'] <= token['y'] <= box['y']+box['h']:
                grouped[token['parent_line_id']].append(token)
        lines = []
        for fragments in grouped.values():
            fragments.sort(key=lambda t:t['order'])
            x, y = min(t['x'] for t in fragments), min(t['y'] for t in fragments)
            lines.append(dict(text=''.join(t['text'] for t in fragments),x=x,y=y,
                              w=max(t['x']+t['w'] for t in fragments)-x,
                              h=max(t['y']+t['h'] for t in fragments)-y))
        try:
            # Stage prefixes (真/圣/妖) and their decorative dot are not needed.
            target = resolve_vertical_bottom_tab(lines, tab_name=tab['name'].split('·')[-1],
                                                  frame_width=900, frame_height=1600)
            break
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            yield from context.wait_action_settle(.5)
    context.click_frame_point(TREE_SCENE,target.x,target.y)
    deadline = time.monotonic()+20
    while True:
        tree = read_role_talent_tree()
        if tree['selected_type'] == tab['type']:
            return tree['tabs'][0]
        if time.monotonic() >= deadline:
            raise RuntimeError('天赋页签未切换成功')
        yield from context.wait_action_settle(.3)


def enter_role_talent_node(context, nodes, target):
    """Use progress sequence and native grid column; reject animation-induced ambiguity."""
    yield from wait_talent_scene(context, [TREE_SCENE])
    box = context.shape(TREE_SCENE,'节点窗口').box()
    viewport = box['x'],box['y'],box['w'],box['h']

    def observe():
        return tree_progress_labels(context.full_frame_ocr_tokens(context.cur_frame(update=True)),
                                    viewport=viewport,include_choice=True)

    def scroll(direction):
        yield from wait_talent_scene(context, [TREE_SCENE])
        yield from context.scroll_shape_content(context.view(TREE_SCENE),'节点窗口',direction=direction)

    columns = {n['id']:n['column'] for n in nodes}
    def match(ordered,labels):
        grid = context.shape(TREE_SCENE,'网格单元').box()
        spatial = [{**n,'y':n['y']*grid['h']/grid['w']} for n in ordered]
        candidates = tree_spatial_progress_candidates(spatial,labels)
        aligned = [mapping for mapping in candidates if all(
            abs(label['x']+label['w']/2-(viewport[0]+viewport[2]*(columns[ident]+.5)/3)) < viewport[2]/6
            for ident,label in mapping.items())]
        if not aligned:
            raise ValueError('天赋进度与原生列位置不符')
        if len(aligned) == 1 and target['id'] not in aligned[0]:
            # A unique full-grid alignment can locate a label covered by combat
            # text. The popup ID remains mandatory before any material is spent.
            mapping = aligned[0]
            by_id = {n['id']:n for n in spatial}
            pairs = [(a,b) for a in mapping for b in mapping
                     if by_id[a]['y']==by_id[b]['y'] and by_id[a]['x']!=by_id[b]['x']]
            if pairs:
                a,b = pairs[0]
                la,lb = mapping[a],mapping[b]
                scale = ((lb['x']+lb['w']/2)-(la['x']+la['w']/2))/(by_id[b]['x']-by_id[a]['x'])
                tx = la['x']+la['w']/2+scale*(by_id[target['id']]['x']-by_id[a]['x'])
                ty = la['y']+la['h']/2+scale*(by_id[target['id']]['y']-by_id[a]['y'])
                if viewport[0]+40 < tx < viewport[0]+viewport[2]-40 and viewport[1]+80 < ty < viewport[1]+viewport[3]-40:
                    mapping[target['id']] = dict(x=tx-la['w']/2,y=ty-la['h']/2,w=la['w'],h=la['h'],projected=True)
        return aligned

    deadline = time.monotonic()+60
    while True:
        try:
            found = yield from locate_first_tree_node(context,nodes=nodes,target_id=target['id'],
                                                       observe_labels=observe,scroll=scroll,match_labels=match)
            break
        except ValueError:
            # Boss cinematics and floating combat text temporarily cover nodes.
            # Reobserve without clicking or inferring that the tree is empty.
            if time.monotonic() >= deadline:
                raise
            yield from wait_talent_scene(context, [TREE_SCENE])
            yield from context.wait_action_settle(.5)
    label = found['label']
    context.click_frame_point(TREE_SCENE,label['x']+label['w']/2,label['y']-label['h'])
    deadline = time.monotonic()+20
    while True:
        try:
            popup = read_role_talent_popup(choice=target['requires_choice'])
            if (target['requires_choice'] and target['talent_id'] in popup['candidates']) or popup['id']==target['talent_id']:
                return popup
        except FanxiuRuntimeMemoryError as exc:
            if exc.code not in {'ui_snapshot_pending','runtime_incomplete'}:
                raise
        if time.monotonic() >= deadline:
            raise RuntimeError('天赋节点详情未就绪，保留现场')
        yield from context.wait_action_settle(.3)
