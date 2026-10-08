"""Upgrade owned buildings in the active helper's displayed row/column order."""
from __future__ import annotations

import json
import base64
import re
import time
from collections import defaultdict
from datetime import datetime

from ..instrumentation.xianfu_building_upgrade import read_xianfu_building_upgrade_panel
from ..instrumentation.item_config import read_item_metadata_runtime
from ..instrumentation.item_resources import read_item_available_counts
from backend.core.temp_paths import codeyun_temp_root

BUILDING_SCENE = 931


def _visible_buildings(context, buildings):
    """Split OCR lines by the three annotated columns, including merged lines."""
    frame = context.cur_frame(update=True)
    viewport = context.shape(BUILDING_SCENE, '建筑窗口').box()
    first = context.shape(BUILDING_SCENE, '条目图标模板').box()
    last = context.shape(BUILDING_SCENE, '末列图标模板').box()
    first_x = first['x'] + first['w']/2
    stride = (last['x'] + last['w']/2 - first_x)/2
    groups = defaultdict(list)
    for token in context.full_frame_ocr_tokens(frame):
        x, y = token['x']+token['w']/2, token['y']+token['h']/2
        if not (viewport['x'] <= x <= viewport['x']+viewport['w']
                and viewport['y'] <= y <= viewport['y']+viewport['h']):
            continue
        column = round((x-first_x)/stride)
        if 0 <= column <= 2:
            groups[(token.get('parent_line_id'), column)].append(token)
    found = []
    for (_, column), tokens in groups.items():
        tokens.sort(key=lambda token: token['x'])
        text = ''.join(token['text'] for token in tokens).replace(' ', '')
        for building in buildings:
            match = re.fullmatch(re.escape(building['name']) + r'(\d+)/(\d+)', text)
            if match:
                y = min(t['y'] for t in tokens)
                h = max(t['y']+t['h'] for t in tokens)-y
                found.append({**building, 'column': column, 'x': first_x+column*stride,
                              'label_y': y+h/2, 'level': int(match[1]), 'maximum': int(match[2])})
    # Retain each actually observed viewport, so a missed OCR row is inspectable.
    root = codeyun_temp_root('fanxiu-question-evidence')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    if isinstance(frame, str) and frame.startswith('data:image/'):
        (root/f'xianfu-building-viewport-{stamp}.png').write_bytes(base64.b64decode(frame.split(',', 1)[1]))
    (root/f'xianfu-building-viewport-{stamp}.json').write_text(
        json.dumps({'tokens': context.full_frame_ocr_tokens(frame), 'matched': found},
                   ensure_ascii=False, indent=2), encoding='utf-8')
    return found


def upgrade_xianfu_building_list(context):
    """No purchases or acceleration; each upgrade proves exactly one level.

    A failed attempt retains no cursor or consumption intent. Reentry selects
    the current live building and costs again. Aggregate completion receipts
    suppress a second completed invocation in the same business day.
    """
    yield from context.go_scene(171)
    yield from context.wait_click(171, '仙府管家图标')
    yield from context.wait_scene([BUILDING_SCENE], wait=15)
    initial = read_xianfu_building_upgrade_panel()
    buildings = initial['buildings']
    # The game automatically jumps to its first affordable building. Restore
    # the actual first row before imposing the owner's priority.
    for _ in range(20):
        visible = _visible_buildings(context, buildings)
        if any(b['id'] == buildings[0]['id'] for b in visible):
            break
        yield from context.scroll_shape_content(context.view(BUILDING_SCENE), '建筑窗口', direction='up')
    else:
        raise RuntimeError('仙府升级列表顶部未能实机定位')
    results = []
    receipts = []
    verified_cost_items = set()
    for building in buildings:
        for _ in range(20):
            visible = _visible_buildings(context, buildings)
            candidates = [b for b in visible if b['id'] == building['id']]
            if len(candidates) == 1:
                target = candidates[0]
                label = context.shape(BUILDING_SCENE, '条目名称模板').box()
                icon = context.shape(BUILDING_SCENE, '条目图标模板').box()
                viewport = context.shape(BUILDING_SCENE, '建筑窗口').box()
                candidate_y = target['label_y'] + icon['y']+icon['h']/2-label['y']-label['h']/2
                if viewport['y']+icon['h']/2 <= candidate_y <= viewport['y']+viewport['h']-icon['h']/2:
                    break
                yield from context.scroll_shape_content(context.view(BUILDING_SCENE), '建筑窗口',
                    direction='up' if candidate_y < viewport['y']+icon['h']/2 else 'down')
                continue
            if len(candidates) > 1:
                raise RuntimeError('仙府升级建筑当前 OCR 身份重复')
            yield from context.scroll_shape_content(context.view(BUILDING_SCENE), '建筑窗口', direction='down')
        else:
            raise RuntimeError(f"仙府建筑未能完整定位：{building['name']}")
        # The icon/name relationship is read from the accepted real asset,
        # not a saved drag offset. Selection never consumes resources.
        label = context.shape(BUILDING_SCENE, '条目名称模板').box()
        icon = context.shape(BUILDING_SCENE, '条目图标模板').box()
        click_y = target['label_y'] + icon['y']+icon['h']/2-label['y']-label['h']/2
        viewport = context.shape(BUILDING_SCENE, '建筑窗口').box()
        if not viewport['y']+icon['h']/2 <= click_y <= viewport['y']+viewport['h']-icon['h']/2:
            raise RuntimeError(f"仙府建筑图标未完整露出：{building['name']}")
        context.click_frame_point(BUILDING_SCENE, target['x'], click_y)
        yield from context.wait_action_settle(.4)
        current = read_xianfu_building_upgrade_panel(buildings=buildings)['selected']
        if (current['id'], current['name'], current['level'], current['maximum']) != (
                building['id'], building['name'], target['level'], target['maximum']):
            raise RuntimeError(f"仙府建筑详情与真实列表不符：{building['name']}")
        before_level = current['level']
        for _ in range(current['maximum']-current['level']+1):
            if not current['can_upgrade']:
                break
            if current['costs']:
                # Item names are immutable within this attempt; balances and
                # the selected level/cost remain fresh consumption guards.
                unknown_items = set(current['costs']) - verified_cost_items
                if unknown_items:
                    metadata = read_item_metadata_runtime(unknown_items)
                    if not metadata['complete'] or any(not r.get('item_name') for r in metadata['items_by_id'].values()):
                        raise RuntimeError(f"仙府建筑费用名称尚未确认：{current}")
                    verified_cost_items.update(unknown_items)
                counts, _ = read_item_available_counts(current['costs'], manager_key='xianfu-building-upgrade')
            elif current.get('cost_kind') == 'free':
                counts = {}
            else:
                raise RuntimeError(f"仙府建筑未明确费用类型，禁止把空费用当免费：{current}")
            if any(counts[item] < amount for item, amount in current['costs'].items()):
                break
            guard = read_xianfu_building_upgrade_panel(buildings=buildings)['selected']
            if guard != current:
                raise RuntimeError('仙府建筑点击前身份/费用/等级发生变化')
            yield from context.wait_click(BUILDING_SCENE, '升级按钮')
            deadline = time.monotonic()+20
            while True:
                after = read_xianfu_building_upgrade_panel(buildings=buildings)['selected']
                if after['id'] != current['id']:
                    raise RuntimeError('仙府升级后建筑身份改变，禁止继续消费')
                if after['level'] == current['level']+1:
                    break
                if after['level'] != current['level'] or time.monotonic() >= deadline:
                    raise RuntimeError('仙府升级未取得唯一等级变化，保留现场不重复点击')
                yield from context.wait_action_settle(.3)
            receipts.append({'id': current['id'], 'name': current['name'],
                             'before_level': current['level'], 'after_level': after['level'],
                             'costs': current['costs'], 'captured_at': datetime.now().astimezone().isoformat()})
            (codeyun_temp_root('fanxiu-question-evidence')/'xianfu-building-live-upgrade-receipts.json').write_text(
                json.dumps(receipts, ensure_ascii=False, indent=2), encoding='utf-8')
            current = after
        results.append({'id': building['id'], 'name': building['name'],
                        'before_level': before_level, 'after_level': current['level'],
                        'reason': 'max_level' if current['level']==current['maximum'] else 'materials_or_prerequisite'})
    if read_xianfu_building_upgrade_panel()['buildings'] != buildings:
        raise RuntimeError('仙府升级后完整建筑列表改变，须从当前事实重新整单扫描')
    yield from context.go_scene(34)
    yield from context.wait_scene([34], wait=20)
    return {'result': 'success', 'outcome': 'complete', 'buildings': results, 'receipts': receipts}
