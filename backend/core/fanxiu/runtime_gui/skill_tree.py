"""技能树公共策略：全树按行优先，局部进度序列定位，单节点点满。

玩法适配器负责提供完整节点事实、窗口 OCR、滚动和点击；本模块不保存
跨 Cell 游标，也不以红点排序替代用户指定的从上到下、从左到右顺序。
"""
from __future__ import annotations

import re
import time
from collections import defaultdict


def ordered_tree_nodes(nodes):
    """Coordinates use screen convention: y increases downward."""
    rows = list(nodes)
    if len({n['id'] for n in rows}) != len(rows):
        raise ValueError('技能树节点 ID 不唯一')
    for n in rows:
        if not 0 <= n['level'] <= n['max_level']:
            raise ValueError('技能树等级不完整或越界')
    return sorted(rows, key=lambda n: (n['y'], n['x']))


def first_unfilled_node(nodes):
    """Position helper only; an incomplete node need not be upgradeable."""
    return next((n for n in ordered_tree_nodes(nodes) if n['level'] < n['max_level']), None)


def first_upgradeable_node(nodes, counts):
    """Choose the first affordable, unlocked node in row order.

    An expensive/locked node never short-circuits the remaining tree. Validate
    all unfinished nodes before returning so incomplete facts cannot masquerade
    as an exhausted tree. Counts and costs use exact native integer units.
    """
    pending = [n for n in ordered_tree_nodes(nodes) if n['level'] < n['max_level']]
    for node in pending:
        costs = node.get('costs')
        if not isinstance(node.get('unlocked'), bool) or not isinstance(costs, dict) or not costs:
            raise ValueError(f"技能树节点 {node['id']} 准入/消耗事实不完整")
        for item, need in costs.items():
            if not isinstance(need, int) or need <= 0 or item not in counts:
                raise ValueError(f"技能树节点 {node['id']} 消耗或库存缺失")
            if not isinstance(counts[item], int) or counts[item] < 0:
                raise ValueError('技能树库存无效')
    return next((n for n in pending if n['unlocked'] and
                 all(counts[item] >= need for item, need in n['costs'].items())), None)


def fill_available_tree(*, read_tree, read_counts, enter_node, fill_node, leave_node):
    """Recompute global eligibility after each verified node transaction.

    Adapters own navigation/identity and supply a complete graph. No persistent
    cursor or blocked-node list is used: newly unlocked nodes are immediately
    reconsidered in row order. Exhaustion means every unfinished node is locked
    or unaffordable, never that the first node is too expensive.
    """
    initial = read_tree()
    receipts = []
    max_actions = sum(n['max_level'] - n['level'] for n in ordered_tree_nodes(initial['nodes']))
    for _ in range(max_actions + 1):
        tree = read_tree()
        nodes = tree['nodes']
        counts = read_counts({item for n in nodes for item in n.get('costs', {})})
        target = first_upgradeable_node(nodes, counts)
        if target is None:
            reason = '全树已满' if first_unfilled_node(nodes) is None else '无可升级节点'
            return {'reason': reason, 'receipts': receipts, 'remaining': counts}
        entered = yield from enter_node(target['id'])
        if entered.get('id') != target['id']:
            raise RuntimeError('技能树候选与实际详情身份不符')
        result = yield from fill_node()
        receipts.append({'node_id': target['id'], 'result': result})
        yield from leave_node()
        after = next((n for n in read_tree()['nodes'] if n['id'] == target['id']), None)
        if after is None or after['level'] <= target['level']:
            raise RuntimeError('技能树可升级候选未取得等级进展，需核对 Runtime/红点，禁止空转')
        if after['level'] != target['level'] + result.get('steps', 0):
            raise RuntimeError('技能树节点等级变化与升级凭证不一致')
    raise RuntimeError('技能树升级超出初始剩余等级上限')


def tree_progress_labels(tokens, *, viewport):
    """Reassemble native OCR lines, then cluster rows despite small y jitter."""
    lines = defaultdict(list)
    x, y, w, h = viewport
    for token in tokens:
        if x <= token['x'] and y <= token['y'] and token['x'] + token['w'] <= x+w and token['y']+token['h'] <= y+h:
            lines[token['parent_line_id']].append(token)
    labels = []
    for fragments in lines.values():
        fragments.sort(key=lambda t: t['x'])
        text = ''.join(t['text'] for t in fragments).replace(' ', '')
        if not re.fullmatch(r'满|\d+/\d+', text):
            continue
        left, top = min(t['x'] for t in fragments), min(t['y'] for t in fragments)
        right = max(t['x']+t['w'] for t in fragments)
        bottom = max(t['y']+t['h'] for t in fragments)
        labels.append({'text': text, 'x': left, 'y': top, 'w': right-left, 'h': bottom-top})
    result = []
    while labels:
        top = min(t['y'] for t in labels)
        row = [t for t in labels if abs(t['y']-top) <= t['h'] / 2]
        result.extend(sorted(row, key=lambda t: t['x']))
        labels = [t for t in labels if t not in row]
    return result


def node_progress_matches(text, node):
    """A single progress label must match this node's exact level facts.

    '满' proves only a maxed node. A canonical N/M must equal both the level
    and the max level, so numeric full levels keep their denominator instead
    of collapsing every maxed numeric node into '满'. Unknown text matches
    nothing.
    """
    if text == '满':
        return node['level'] == node['max_level']
    matched = re.fullmatch(r'(\d+)/(\d+)', text)
    if matched is None:
        return False
    return int(matched.group(1)) == node['level'] and int(matched.group(2)) == node['max_level']


def tree_progress_candidates(nodes, labels):
    """Return every contiguous window matching the observed progress.

    Repeated 0/20 labels alone cannot prove identity; full nodes and partial
    levels provide anchors. Matching is per node, so a maxed node may show
    either '满' or its own N/M such as '5/5'. A missed OCR node or a window
    shorter than two labels is rejected instead of guessing an index. Each
    returned candidate maps node id to the label observed at that node.
    """
    ordered = ordered_tree_nodes(nodes)
    observed = [label['text'] for label in labels]
    if len(observed) < 2:
        raise ValueError('技能树窗口缺少足够进度锚点')
    starts = [
        start
        for start in range(len(ordered)-len(observed)+1)
        if all(node_progress_matches(observed[i], ordered[start+i]) for i in range(len(observed)))
    ]
    if not starts:
        raise ValueError('技能树窗口序列无匹配')
    return [{ordered[start+i]['id']: label for i, label in enumerate(labels)} for start in starts]


def tree_progress_direction(target_index, candidates, indexes):
    """Scroll direction when every candidate window lies on one side of target.

    'up' means the target is above the whole window and 'down' below it. A
    candidate set that contains or straddles the target is ambiguous: repeated
    levels cannot tell which occurrence holds the node, so raise instead.
    """
    spans = [[indexes[node_id] for node_id in candidate] for candidate in candidates]
    if all(min(span) > target_index for span in spans):
        return 'up'
    if all(max(span) < target_index for span in spans):
        return 'down'
    raise ValueError('技能树候选窗口跨越目标，无法确定滚动方向')


def align_tree_progress(nodes, labels):
    """Align a contiguous visible progress sequence, rejecting ambiguous views.

    Kept for callers that need a single confident mapping; repeated-window
    ambiguity raises, while locate_first_tree_node resolves ambiguity by
    candidate direction instead.
    """
    candidates = tree_progress_candidates(nodes, labels)
    if len(candidates) != 1:
        raise ValueError(f'技能树窗口序列无法唯一对齐：{len(candidates)} 个候选')
    return candidates[0]


def fill_tree_node(context, *, read_detail, click_upgrade, expected_id, initial_counts, max_steps=256):
    """Confirm each new level before spending again; retain modeled inventory.

    read_detail returns id/level/max_level/unlocked/costs. GUI adapters own
    click geometry. A sent action with no confirmed level change stops instead
    of retrying. Known fixed costs are deducted after success, not reread.
    """
    counts = dict(initial_counts)
    steps = 0

    def observe(deadline):
        from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
        while True:
            try:
                return read_detail()
            except FanxiuRuntimeMemoryError as exc:
                if exc.code not in {'ui_snapshot_pending', 'runtime_incomplete', 'memory_read_failed', 'memory_address_unmapped'} or time.monotonic() >= deadline:
                    raise
                # Pool membership can disappear while activation refreshes the
                # panel. Re-observe only; never resend an uncertain upgrade.
                yield from context.wait_action_settle(0.3)

    while steps < max_steps:
        before = yield from observe(time.monotonic()+15)
        if before['id'] != expected_id:
            raise RuntimeError('技能树详情身份不符，保留现场')
        if before['level'] == before['max_level']:
            return {'reason': '节点已满', 'steps': steps, 'level': before['level'], 'remaining': counts}
        if not before['unlocked']:
            return {'reason': '前置节点未满', 'steps': steps, 'level': before['level'], 'remaining': counts}
        costs = before['costs']
        if not costs or any(item not in counts for item in costs):
            raise RuntimeError('技能树消耗或材料模型不完整')
        if any(counts[item] < need for item, need in costs.items()):
            return {'reason': '材料不足', 'steps': steps, 'level': before['level'], 'remaining': counts}
        yield from click_upgrade()
        deadline = time.monotonic()+15
        while True:
            after = yield from observe(deadline)
            if after['id'] != expected_id:
                raise RuntimeError('加点后节点身份改变，保留现场')
            if after['level'] == before['level']+1:
                for item, need in costs.items():
                    counts[item] -= need
                steps += 1
                break
            if time.monotonic() >= deadline or after['level'] != before['level']:
                raise RuntimeError('加点结果未确认，禁止重复发送')
            yield from context.wait_action_settle(0.3)
    raise RuntimeError('技能树加点达到诊断上限')


def _label_points(labels):
    points = []
    for index, label in enumerate(labels):
        points.append({
            'index': index,
            'text': label['text'],
            'cx': label['x'] + label['w'] / 2,
            'cy': label['y'] + label['h'] / 2,
            'tol': label['h'] / 2,
            'label': label,
        })
    return points


def _cluster_rows(items, key, tolerance):
    rows = []
    for item in sorted(items, key=key):
        if rows and abs(key(item) - key(rows[-1][-1])) <= tolerance:
            rows[-1].append(item)
        else:
            rows.append([item])
    return rows


def _node_row_tolerance(nodes):
    ys = sorted(node['y'] for node in nodes)
    gaps = [b-a for a, b in zip(ys, ys[1:]) if b-a > 1e-9]
    return min(gaps) * 0.25 if gaps else 0.0


def _min_node_distance(nodes):
    best = None
    for i in range(len(nodes)):
        for j in range(i+1, len(nodes)):
            dx = nodes[i]['x'] - nodes[j]['x']
            dy = nodes[i]['y'] - nodes[j]['y']
            distance = (dx*dx + dy*dy) ** 0.5
            if best is None or distance < best:
                best = distance
    return best if best is not None else 0.0


def _spatial_mapping(nodes, points, scale, offset_x, offset_y):
    mapping = []
    used_ids = set()
    for point in points:
        hit = None
        for node in nodes:
            if abs(scale*node['x'] + offset_x - point['cx']) > point['tol']:
                continue
            if abs(scale*node['y'] + offset_y - point['cy']) > point['tol']:
                continue
            if not node_progress_matches(point['text'], node):
                continue
            if hit is not None:
                return None
            hit = node
        if hit is None or hit['id'] in used_ids:
            return None
        used_ids.add(hit['id'])
        mapping.append((hit['id'], point['index'], point['label']))
    return mapping


def tree_spatial_progress_candidates(nodes, labels):
    """Co-register configured node coordinates onto OCR label centers.

    An opt-in alternative to the contiguous matcher. A uniform positive scale
    plus translation (no rotation) maps every configured (x, y) onto an observed
    label center, so an entire missed node or row is tolerated: only labels that
    were actually observed constrain the mapping, and missing nodes are never
    guessed. Scale/translation are derived from two different-x labels in one
    observed row and a matching node pair in one configured row, then anchored
    in y; every label must land inside its own half-height tolerance on exactly
    one progress-matching node. Geometry is validated against all labels, so a
    wrong progress or a non-uniform layout yields no candidate and raises.
    Repeated patterns return every distinct id->label mapping and never a best
    guess; callers own disambiguation.
    """
    ordered = ordered_tree_nodes(nodes)
    if len(labels) < 3:
        raise ValueError('技能树空间配准缺少足够进度锚点')
    points = _label_points(labels)
    node_gap = _min_node_distance(ordered)
    if node_gap <= 0:
        raise ValueError('技能树节点坐标不唯一')
    max_tolerance = max(point['tol'] for point in points)
    if max_tolerance <= 0:
        raise ValueError('技能树空间容差必须为正')

    label_rows = _cluster_rows(points, lambda p: p['cy'], max_tolerance)
    label_columns = _cluster_rows(points, lambda p: p['cx'], max_tolerance)
    if len(label_rows) < 2 or len(label_columns) < 2:
        raise ValueError('技能树观察标签未覆盖两行两列')
    node_rows = _cluster_rows(ordered, lambda n: n['y'], _node_row_tolerance(ordered))

    results = []
    seen = set()
    for label_row in label_rows:
        label_row = sorted(label_row, key=lambda p: p['cx'])
        for node_row in node_rows:
            node_row = sorted(node_row, key=lambda n: n['x'])
            for left in range(len(label_row)):
                for right in range(left+1, len(label_row)):
                    point_left, point_right = label_row[left], label_row[right]
                    if point_left['cx'] == point_right['cx']:
                        continue
                    for low in range(len(node_row)):
                        for high in range(low+1, len(node_row)):
                            node_low, node_high = node_row[low], node_row[high]
                            if node_low['x'] == node_high['x']:
                                continue
                            if not node_progress_matches(point_left['text'], node_low):
                                continue
                            if not node_progress_matches(point_right['text'], node_high):
                                continue
                            scale = (point_right['cx']-point_left['cx']) / (node_high['x']-node_low['x'])
                            if scale <= 0:
                                continue
                            # OCR tolerance is measured in screen pixels; the
                            # configuration gap must be projected to that same
                            # space before comparing. Each tolerance is a box,
                            # so account for its diagonal as well.
                            if 2**0.5 * max_tolerance * 2 >= scale * node_gap:
                                continue
                            offset_x = point_left['cx'] - scale * node_low['x']
                            for anchor_point in points:
                                for anchor_node in ordered:
                                    if not node_progress_matches(anchor_point['text'], anchor_node):
                                        continue
                                    offset_y = anchor_point['cy'] - scale * anchor_node['y']
                                    mapping = _spatial_mapping(ordered, points, scale, offset_x, offset_y)
                                    if mapping is None:
                                        continue
                                    key = tuple(sorted((node_id, index) for node_id, index, _ in mapping))
                                    if key in seen:
                                        continue
                                    seen.add(key)
                                    results.append({node_id: label for node_id, _, label in mapping})
    if not results:
        raise ValueError('技能树空间序列无匹配')
    return results


def locate_first_tree_node(context, *, nodes, observe_labels, scroll, max_scrolls=24, target_id=None,
                           match_labels=tree_progress_candidates):
    """Locate the globally first incomplete node through overlapping windows.

    Repeated 0/20 runs yield several candidate windows. When every candidate
    lies on one side of the target the direction is certain, so scroll there
    instead of failing alignment. Only a candidate set that contains or
    straddles the target stays ambiguous. scroll receives 'up'/'down' in
    content-search terms and owns the engine's default half-window gesture.
    No coordinate/index survives a scroll.
    """
    ordered = ordered_tree_nodes(nodes)
    target = (first_unfilled_node(ordered) if target_id is None else
              next((n for n in ordered if n['id'] == target_id), None))
    if target_id is not None and (target is None or target['level'] >= target['max_level']):
        raise ValueError('技能树指定候选不存在或已经满级')
    if target is None:
        return None
    indexes = {node['id']: i for i, node in enumerate(ordered)}
    target_index = indexes[target['id']]
    last_window = None
    for attempt in range(max_scrolls+1):
        # A transient OCR miss can be retried, but never used as a scroll anchor.
        candidates = None
        for observation in range(3):
            try:
                candidates = match_labels(ordered, observe_labels())
                break
            except ValueError:
                if observation == 2:
                    raise
                yield from context.wait_action_settle(0.3)
        if len(candidates) == 1 and target['id'] in candidates[0]:
            return {'node': target, 'label': candidates[0][target['id']]}
        if any(target['id'] in candidate for candidate in candidates):
            raise ValueError('技能树窗口候选不唯一且包含目标，拒绝猜测')
        direction = tree_progress_direction(target_index, candidates, indexes)
        if len(candidates) == 1:
            window = tuple(candidates[0])
            if window == last_window:
                raise RuntimeError('技能树滚动未取得新窗口或达到上限')
            last_window = window
        else:
            # Repeated 0 windows actually move; never treat an identical
            # candidate id set as proof of no progress.
            last_window = None
        if attempt == max_scrolls:
            raise RuntimeError('技能树滚动未取得新窗口或达到上限')
        yield from scroll(direction)
    raise RuntimeError('技能树目标未定位')
