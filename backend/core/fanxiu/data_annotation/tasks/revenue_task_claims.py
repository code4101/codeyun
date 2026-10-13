"""Claim an already-bound RevenueTask tab from native ordered row authority."""
from backend.core.fanxiu.instrumentation.revenue_task_page import read_revenue_task_page_snapshot


def claim_open_revenue_task_rewards(context, *, activity_id: int,
                                   expected_subtype: int, scene_id: int,
                                   first_row_shape: str, max_claims: int = 100):
    """Only collect currently claimable rewards; never jump unfinished tasks.

    The owning caller must bind the exact live occurrence before opening the
    tab. This transaction rechecks native activity/subtype, current first-row
    controller and durable finished IDs around every one-row click. A failed
    observation stops immediately; restarting reads remaining authority anew.
    """
    claimed = []
    for _ in range(max_claims):
        yield from context.wait_scene_exact([scene_id], timeout=25)
        before = read_revenue_task_page_snapshot(activity_id)
        if not before.get('complete') or before.get('subtype') != expected_subtype:
            raise RuntimeError(f'任务页原生身份未对齐：{before.get("reason")}')
        authorized = before['authorized_claim_task_ids']
        if not authorized:
            return dict(complete=True, claimed_task_ids=claimed, after_snapshot=before)
        first = before.get('first_row')
        if (not before['at_top'] or not first or not first['claimable']
                or first['task_id'] != authorized[0]):
            raise RuntimeError('任务可领首行与可见控制器未对齐，禁止点击')
        expected = first['task_id']
        context.click_shape_center(scene_id, first_row_shape)
        yield from context.wait_action_settle(1.5)
        after = read_revenue_task_page_snapshot(activity_id, expected_task_id=expected)
        if (not after.get('complete') or after.get('subtype') != expected_subtype
                or after.get('expected_task_claimed') is not True
                or expected not in after['claimed_task_ids']
                or expected in after['authorized_claim_task_ids']):
            raise RuntimeError(f'任务{expected}领取效果尚未准确验收，禁止重放点击')
        claimed.append(expected)
        context.report_progress(f'已核验领取任务{expected}，本批{len(claimed)}项')
    raise RuntimeError('任务领取超过有界次数，保留现场')


def claim_open_activity_round_reward(context, *, activity_id: int,
                                     expected_reward_id: int, scene_id: int,
                                     round_reward_shape: str):
    """Collect exactly one earned round chest, confirmed by native box IDs.

    The caller binds the exact occurrence and supplies a visually accepted
    summary-background Shape. This component never uses a task-row Shape,
    progresses unfinished conditions, or retries an unconfirmed reward click.
    """
    yield from context.wait_scene_exact([scene_id], timeout=25)
    before = read_revenue_task_page_snapshot(activity_id, include_round_rewards=True)
    reward = before.get('round_reward') or {}
    if (not before.get('complete') or before.get('subtype') != 999
            or expected_reward_id not in reward.get('progress_reward_ids', [])):
        raise RuntimeError('轮次总奖页面或配置身份未对齐')
    if expected_reward_id in reward['received_reward_ids']:
        return dict(result='success', claimed_reward_ids=[], boundary='already_claimed')
    if not reward['can_claim'] or reward['authorized_reward_id'] != expected_reward_id:
        raise RuntimeError('指定轮次总奖当前不可领，禁止点击')
    context.click_shape_center(scene_id, round_reward_shape)
    # A server/UI refresh can follow the animation. Observe only; never click
    # again if the first read is not yet conclusive.
    for _ in range(3):
        yield from context.wait_action_settle(1.5)
        after = read_revenue_task_page_snapshot(activity_id, include_round_rewards=True)
        new_reward = after.get('round_reward') or {}
        if (after.get('complete') and after.get('subtype') == 999
                and after.get('evidence') == before.get('evidence')
                and set(new_reward.get('received_reward_ids', [])) - set(reward['received_reward_ids']) == {expected_reward_id}
                and set(reward['received_reward_ids']).issubset(new_reward.get('received_reward_ids', []))
                and (new_reward.get('current_round') == reward['current_round'] + 1
                     or (reward['current_round'] == reward['round_count'] and new_reward.get('native_state') == 3))):
            return dict(result='success', claimed_reward_ids=[expected_reward_id],
                        before_round=reward['current_round'],
                        after_round=new_reward['current_round'], after_snapshot=after)
    raise RuntimeError('轮次总奖点击后的原生领取与换轮尚未确认，禁止重放点击')
