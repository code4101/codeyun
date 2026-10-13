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
