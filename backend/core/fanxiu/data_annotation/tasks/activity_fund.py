"""活动基金：按 Runtime 页签顺序回访未全领档，不保存完成标记。

当前活动已验证 #904；页面皮肤/入口留给本期活动适配，实时基金状态读取
可复用。状态 1 但当前不可领时留待次日，不能冒充全部完成或反复点购买提示。
"""
import time

from backend.core.fanxiu.instrumentation.activity_fund import read_activity_fund_state


def collect_activity_fund(context, *, scene_id=904, return_scene_id=903):
    """On an opened fund page, claim all currently eligible tabs left to right.

    Claims current resources and returns one level to the activity home.
    Does not activate paid funds, write a database, or cache completion.
    Returns still-pending IDs so the enclosing daily activity can
    revisit them alongside tomorrow's newly opened tab.
    """
    yield from context.wait_scene_exact([scene_id], timeout=15)
    initial = read_activity_fund_state()
    activity_id = initial['activity_id']
    claimed_tabs = []
    for tab in initial['tabs']:
        if tab['state'] != 1 or not tab['claimable']:
            continue
        current = read_activity_fund_state()
        if current['activity_id'] != activity_id:
            raise RuntimeError('活动基金实例已切换')
        selected = next(t for t in current['tabs'] if t['fund_id'] == current['selected_fund'])
        if current['selected_fund'] != tab['fund_id']:
            direction = 'left' if tab['index'] < selected['index'] else 'right'
            yield from context.wait_click_ocr_text(
                scene_id, tab['name'], in_shapes=['基金页签'], search_direction=direction,
                max_scrolls_per_direction=12,
            )
            yield from context.wait_action_settle(0.6)
            yield from context.wait_scene_exact([scene_id], timeout=15)
        before = read_activity_fund_state()
        if before['activity_id'] != activity_id or before['selected_fund'] != tab['fund_id']:
            raise RuntimeError('活动基金页签点击后 Runtime 身份不符')
        fresh = next(t for t in before['tabs'] if t['fund_id'] == tab['fund_id'])
        if not fresh['claimable']:
            continue
        missing = set(fresh['missing_free']), set(fresh['missing_paid'])
        yield from context.wait_click(scene_id, '一键领取')
        deadline = time.monotonic() + 30
        while True:
            yield from context.wait_scene_exact([scene_id], timeout=15)
            after = read_activity_fund_state()
            if after['activity_id'] != activity_id:
                raise RuntimeError('活动基金领取后实例已切换')
            updated = next(t for t in after['tabs'] if t['fund_id'] == tab['fund_id'])
            remaining = set(updated['missing_free']), set(updated['missing_paid'])
            progressed = (remaining[0] < missing[0] or remaining[1] < missing[1])
            if progressed and not updated['claimable']:
                claimed_tabs.append(tab['fund_id'])
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('活动基金一键领取后未确认领取 ID 更新')
            yield from context.wait_action_settle(0.5)
    final = read_activity_fund_state()
    if final['activity_id'] != activity_id or any(t['claimable'] for t in final['tabs'] if t['opened']):
        raise RuntimeError('活动基金仍有可领奖励，不能结束本轮')
    yield from context.wait_click(scene_id, '返回')
    yield from context.wait_scene_exact([return_scene_id], timeout=15)
    return dict(result='success', activity_id=activity_id, claimed_tabs=claimed_tabs,
                pending_funds=[t['fund_id'] for t in final['tabs'] if t['state'] == 1],
                tabs=[{k: t[k] for k in ('fund_id', 'name', 'state')} for t in final['tabs']])
