"""#66 carousel: Runtime task inventory, title OCR and bounded GUI alignment."""
from __future__ import annotations

import re
import time
from typing import Any, Mapping

from backend.core.fanxiu.instrumentation.schedule_cards import read_schedule_card_runtime_snapshot
from backend.core.fanxiu.runtime_gui import GuiCandidate, RuntimeEntity, score_runtime_gui_pair
from backend.core.fanxiu.runtime_gui.text import normalize_ocr_name
from backend.core.fanxiu.data_annotation.schedule_navigation import (
    activity_card_indicator_points, activity_card_selected_indicator,
)
from backend.core.fanxiu.data_annotation.ocr_spatial import query_ocr_lines

TITLE_SHAPE = '活动卡片/标题'


def align_schedule_card_title(
    title: str, snapshot: Mapping[str, Any], *, minimum_score: float = 0.62,
    minimum_margin: float = 0.08,
) -> dict[str, Any]:
    """Match one noisy title against ALL actual carousel tasks, preserving ties.

    OCR whitespace/punctuation and misspellings are tolerated. An explicit
    conflicting cross-server count is not. No date filtering: membership comes
    only from the client carousel. A match is identity evidence, not permission
    to enter a future/closed event.
    """
    result = {'title': title, 'status': 'insufficient', 'task': None, 'candidates': []}
    if snapshot.get('complete') is not True:
        return {**result, 'status': 'incomplete_runtime'}
    if not normalize_ocr_name(title):
        return result
    observed_cross = re.search(r'跨服\s*[\[【（(]?\s*(\d+)', title)
    candidates = []
    for item in snapshot.get('items', []):
        expected = str(item['title'])
        expected_cross = re.search(r'跨服\s*[\[【（(]?\s*(\d+)', expected)
        if observed_cross and expected_cross and observed_cross[1] != expected_cross[1]:
            continue
        if observed_cross and not expected_cross:
            continue
        # Missing OCR qualifiers are missing evidence, not evidence for the
        # unqualified/local version. Give both the same base-title score so
        # adjacent sequence observations can resolve the actual occurrence.
        scoring_name = expected
        if not observed_cross:
            scoring_name = re.sub(r'跨服\s*[\[【（(]?\s*\d+\s*[\]】）)]?', '', scoring_name)
        if not re.search(r'[（(]', title):
            scoring_name = re.sub(r'[（(][^）)]*[）)]', '', scoring_name)
        entity = RuntimeEntity(key=str(item['key']), name=scoring_name)
        evidence = score_runtime_gui_pair(entity, GuiCandidate(key='title', text=title))
        candidates.append({'key': item['key'], 'title': expected, 'score': evidence.score, 'task': item})
    candidates.sort(key=lambda row: row['score'], reverse=True)
    result['candidates'] = [{k: v for k, v in row.items() if k != 'task'} for row in candidates]
    if not candidates or candidates[0]['score'] < minimum_score:
        return result
    margin = candidates[0]['score'] - (candidates[1]['score'] if len(candidates) > 1 else 0)
    result['score'] = candidates[0]['score']
    result['margin'] = margin
    if len(candidates) > 1 and margin < minimum_margin:
        return {**result, 'status': 'ambiguous'}
    return {**result, 'status': 'aligned', 'task': candidates[0]['task']}


def align_schedule_card_neighbors(observations: Mapping[int, str], snapshot: Mapping[str, Any], *,
                                  minimum_score: float = 0.62, minimum_margin: float = 0.08):
    """Resolve the origin from signed left/right offsets in the native sequence.

    Unreadable titles provide no identity evidence. Inferring an unreadable
    origin requires two readable positions; readable contradictions are never
    discarded. Offsets must come from confirmed GUI page transitions.
    """
    result = {'status': 'insufficient', 'task': None, 'candidates': []}
    if snapshot.get('complete') is not True:
        return {**result, 'status': 'incomplete_runtime'}
    items = snapshot.get('items', [])
    if not items or len({offset % len(items) for offset in observations}) != len(observations):
        return result
    scores = {}
    for offset, title in observations.items():
        candidates = align_schedule_card_title(title, snapshot)['candidates']
        if any(row['score'] >= minimum_score for row in candidates):
            scores[offset] = {row['key']: row['score'] for row in candidates}
    if not scores or (0 not in scores and len(scores) < 2):
        return result
    hypotheses = []
    for start, item in enumerate(items):
        pairs = [values.get(items[(start+offset) % len(items)]['key'], 0)
                 for offset, values in scores.items()]
        if min(pairs) >= minimum_score:
            hypotheses.append({'key': item['key'], 'score': sum(pairs), 'task': item})
    hypotheses.sort(key=lambda row: row['score'], reverse=True)
    result['candidates'] = [{k: v for k, v in row.items() if k != 'task'} for row in hypotheses]
    if not hypotheses:
        return result
    margin = hypotheses[0]['score'] - (hypotheses[1]['score'] if len(hypotheses) > 1 else 0)
    if len(hypotheses) > 1 and margin < minimum_margin:
        return {**result, 'status': 'ambiguous', 'margin': margin}
    return {**result, 'status': 'aligned', 'task': hypotheses[0]['task'],
            'score': hypotheses[0]['score']/len(scores), 'margin': margin}


def align_schedule_card_sequence(titles: list[str], snapshot: Mapping[str, Any], **kwargs):
    """Resolve the FIRST card from a contiguous forward sequence, with wrap."""
    return {**align_schedule_card_neighbors(dict(enumerate(titles)), snapshot, **kwargs),
            'titles': titles}


def read_schedule_card(context, snapshot: Mapping[str, Any], *, frame_data_url: str | None = None):
    """Observe only the annotated title ROI; caller owns #66 readiness."""
    frame = frame_data_url or context.cur_frame(update=True)
    # The legacy OCR selector accepts leaf names only. Resolve the precise
    # nested Shape through the public geometry API, then use shared OCR's
    # spatial query to avoid both ambiguous leaf names and a second OCR pass.
    lines = query_ocr_lines(
        context.ocr_fragments_in_shapes(66, ['活动卡片'], frame_data_url=frame, crop=True),
        context.shape_box(66, TITLE_SHAPE),
    )
    # Floating stat gains are independent overlays, not pieces of a title.
    lines = [row for row in lines if not re.search(r'[+＋]\s*\d', str(row.get('text') or ''))]
    lines = sorted(lines, key=lambda row: float(row.get('x', 0)))
    title = ''.join(str(row.get('text') or '') for row in lines)
    return {**align_schedule_card_title(title, snapshot), 'title_lines': lines,
            'pager_index': activity_card_selected_indicator(context, frame)}


def wait_schedule_card(context, snapshot: Mapping[str, Any], *, expected_key: str | None = None,
                       max_samples: int = 8):
    """Wait for two matching fresh title observations after a page transition."""
    previous = None
    for _ in range(max_samples):
        yield from context.wait_action_settle(0.35)
        observed = read_schedule_card(context, snapshot)
        key = observed['task']['key'] if observed['status'] == 'aligned' else None
        # OCR may omit a trailing glyph between fresh frames while both
        # observations still uniquely identify the same Runtime card.  Use
        # that resolved identity when available; ambiguous titles must still
        # agree textually before sequence disambiguation.
        identity = (
            ('task', key) if key is not None else ('title', normalize_ocr_name(observed['title'])),
            observed['pager_index'],
        )
        if observed['pager_index'] is not None and identity == previous and (expected_key is None or key == expected_key):
            return observed
        previous = identity
    raise RuntimeError(f'#66 卡片标题未稳定对齐：{observed}')


def resolve_schedule_card_ambiguity(context, snapshot: Mapping[str, Any], current: dict, *,
                                   minimum_observations: int = 1):
    """Read left/right neighbors until their native order uniquely locates current.

    Each step is confirmed by the GUI selected dot, so repeated title 'a' does
    not look like a failed page change. Restore the original page before return.
    """
    if (current['status'] == 'aligned' and minimum_observations <= 1
            and current.get('score', 0) >= 0.9
            and current['task']['index'] == current['pager_index']):
        return current
    points = activity_card_indicator_points(context, context.cur_frame(update=True))
    original = current['pager_index']
    if original is None or len(points) != snapshot['count']:
        raise RuntimeError('#66 无法确认相邻页位置，不能使用序列对齐')
    titles = {0: current['title']}
    alignment = align_schedule_card_neighbors(titles, snapshot)
    offsets = sorted(range(1, len(points)), key=lambda i: min(i, len(points)-i))
    for offset in offsets:
        index = (original + offset) % len(points)
        context.click_frame_point(66, *points[index])
        yield from context.wait_action_settle(0.8)
        following = yield from wait_schedule_card(context, snapshot)
        if following['pager_index'] != index:
            raise RuntimeError('#66 相邻页未到达，拒绝把重复标题当作新页')
        titles[offset] = following['title']
        alignment = align_schedule_card_neighbors(titles, snapshot)
        if alignment['status'] == 'aligned' and len(titles) >= minimum_observations:
            break
    context.click_frame_point(66, *points[original])
    yield from context.wait_action_settle(0.8)
    restored = yield from wait_schedule_card(context, snapshot)
    if restored['pager_index'] != original:
        raise RuntimeError('#66 序列观察后未恢复原卡片')
    fresh = read_schedule_card_runtime_snapshot()
    if not fresh.get('complete') or fresh['fingerprint'] != snapshot['fingerprint']:
        raise RuntimeError('#66 邻接观察期间卡片清单变化，需重新定位')
    if alignment['status'] != 'aligned':
        raise RuntimeError(f'#66 完整一轮序列仍有歧义：{alignment}；observations={titles}')
    readable = {row['key'] for row in restored['candidates'] if row['score'] >= 0.62}
    if readable and alignment['task']['key'] not in readable:
        raise RuntimeError('#66 恢复页标题与序列推断冲突')
    return {**restored, **{k: alignment[k] for k in ('status', 'task', 'score', 'margin')},
            'sequence_titles': titles}


def inspect_schedule_cards(context):
    """Return the Runtime inventory and current GUI task without navigating."""
    yield from context.wait_scene([66], wait=5)
    snapshot = read_schedule_card_runtime_snapshot()
    if not snapshot.get('complete'):
        raise RuntimeError(f'#66 卡片 Runtime 不完整：{snapshot}')
    current = yield from wait_schedule_card(context, snapshot)
    current = yield from resolve_schedule_card_ambiguity(context, snapshot, current)
    return {'runtime': snapshot, 'current': current}


def select_schedule_card(context, runtime_key: str, *, state: Mapping[str, Any] | None = None):
    """Locate a Runtime task in #66 and stop on its verified card.

    Uses the observed anchor and native relative order to choose the dot.
    Fresh title or neighbor evidence must confirm the landing. Does not enter the event or test its
    business availability; even expired/future cards can be selected.
    """
    state = state if state is not None else (yield from inspect_schedule_cards(context))
    snapshot = state['runtime']
    matches = [row for row in snapshot['items'] if row['key'] == runtime_key]
    if len(matches) != 1:
        raise ValueError(f'#66 轮播清单中目标不唯一或不存在：{runtime_key}')
    if state['current']['task']['key'] == runtime_key:
        return state['current']
    points = activity_card_indicator_points(context, context.cur_frame(update=True))
    if len(points) != snapshot['count']:
        raise RuntimeError('#66 页点数与 Runtime 卡片数量不符')
    current_index = state['current']['pager_index']
    if current_index is None:
        raise RuntimeError('#66 当前页点未知，不能从邻接关系推导目标位置')
    offset = matches[0]['index'] - state['current']['task']['index']
    target_index = (current_index + offset) % len(points)
    x, y = points[target_index]
    context.click_frame_point(66, x, y)
    yield from context.wait_action_settle(0.8)
    current = yield from wait_schedule_card(context, snapshot)
    current = yield from resolve_schedule_card_ambiguity(context, snapshot, current)
    if current['pager_index'] != target_index or current['task']['key'] != runtime_key:
        raise RuntimeError(f'#66 卡片落点与目标不符：{current}')
    return current


def prepare_schedule_card_for_activity_ids(context, activity_ids):
    """Select one open Runtime card from IDs declared by a navigation Shape.

    The scene graph says where Forward may land. This function chooses the
    currently open card and confirms its GUI title before that Shape is clicked.
    One Runtime inventory read is reused through selection; title and pager
    feedback are GUI observations.
    """
    try:
        activity_ids = {int(value) for value in activity_ids if int(value) > 0}
    except (TypeError, ValueError) as exc:
        raise ValueError('场景跳转 Shape 的 navigationRuntimeActivityIds 必须是活动 ID 列表') from exc
    if not activity_ids:
        raise ValueError('场景跳转 Shape 缺少有效 navigationRuntimeActivityIds')
    state = yield from inspect_schedule_cards(context)
    now_ms = time.time() * 1000
    candidates = [
        item for item in state['runtime']['items']
        if int(item.get('activity_id') or 0) in activity_ids
        and float(item.get('start_time') or 0) <= now_ms <= float(item.get('end_time') or 0)
    ]
    if len(candidates) != 1:
        raise RuntimeError(f'#66 所选活动身份 {sorted(activity_ids)} 的有效卡片不唯一：{candidates}')
    selected = yield from select_schedule_card(
        context, str(candidates[0]['key']), state=state,
    )
    if str(selected['task']['key']) != str(candidates[0]['key']):
        raise RuntimeError('#66 活动卡片前置条件未满足')
    return selected


def verify_schedule_card_carousel(context):
    """Visit every detected pager dot, recording title-to-task alignment.

    Dot positions are navigation hints, never identity. Success requires all
    Runtime keys exactly once. Leaves #66 open and does not click Forward.
    """
    state = yield from inspect_schedule_cards(context)
    snapshot = state['runtime']
    points = activity_card_indicator_points(context, context.cur_frame(update=True))
    if len(points) != snapshot['count']:
        raise RuntimeError(f'#66 页点数 {len(points)} 与 Runtime 卡片数 {snapshot["count"]} 不一致')
    observations = []
    for index, (x, y) in enumerate(points):
        yield from context.wait_scene([66], wait=5)
        context.click_frame_point(66, x, y)
        yield from context.wait_action_settle(0.8)
        current = yield from wait_schedule_card(context, snapshot)
        current = yield from resolve_schedule_card_ambiguity(context, snapshot, current)
        observations.append({'dot_index': index, 'title': current['title'],
                             'task': current['task'], 'score': current['score']})
    keys = [row['task']['key'] for row in observations]
    complete = len(set(keys)) == len(keys) and set(keys) == {row['key'] for row in snapshot['items']}
    return {'complete': complete, 'runtime': snapshot, 'observations': observations}
