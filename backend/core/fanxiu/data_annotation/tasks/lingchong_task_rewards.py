"""Occurrence-bound preliminary pet rewards; QuestMgr proves all claims.

The rank entry is verified #933. The task list must positively match the
existing #747 layout and the active panel must still expose the requested
activity ID. A different layout stops before any reward click for annotation.
"""
from datetime import datetime


def claim_lingchong_preliminary_rewards(context, *, activity_id: int):
    from backend.core.fanxiu.activity.lingchong_jingwu import read_lingchong_task_milestones
    from backend.core.fanxiu.instrumentation.activity_rank_page import read_activity_rank_page_snapshot
    from .resource_rank_page import LINGCHONG_PRELIMINARY_RANK_ADAPTER
    from .resource_rank_daily_gift import open_resource_rank_activity_page
    from .shequn_lingchong_task_rewards import claim_shequn_lingchong_task_rewards
    from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens

    if activity_id != 1042911:
        raise ValueError('灵宠任务奖励当前仅绑定同服预赛')
    snapshot = read_lingchong_task_milestones(activity_id)
    state = snapshot.get('reward_state') or {}
    ids = set(snapshot.get('task_ids') or [])
    if not (snapshot.get('ok') and snapshot.get('complete') and state.get('complete') and len(ids) == 14):
        raise RuntimeError('灵宠14档任务奖励事实不完整')
    if snapshot.get('progress', 0) < snapshot.get('target', 6000):
        raise RuntimeError('灵宠最高任务档尚未达成')
    if ids <= set(state.get('claimed_task_ids') or []):
        # Claims can be committed before departure fails. An idempotent retry
        # still owns safe cleanup; already_claimed cannot hide the old page.
        yield from context.go_scene(34)
        return {'status': 'completed', 'claimed_count': 0, 'already_claimed': True, 'total_claimed': 14}
    current = yield from context.wait_scene([747], wait=3, required=False)
    page = read_activity_rank_page_snapshot() if current is not None else {}
    task_page_open = (current is not None and page.get('complete')
                      and page.get('activity_id') == activity_id and page.get('tab_index') == 2)
    if task_page_open:
        result = yield from claim_shequn_lingchong_task_rewards(context, activity_id=activity_id, scene_id=747)
        yield from context.go_scene(34)
        return {'status': 'completed', 'total_claimed': 14, 'rewards': result}
    scene = int((yield from open_resource_rank_activity_page(
        context, LINGCHONG_PRELIMINARY_RANK_ADAPTER,
        activity_id=activity_id, now=datetime.now().astimezone())))
    page = read_activity_rank_page_snapshot()
    if scene != 933 or not page.get('complete') or page.get('activity_id') != activity_id:
        raise RuntimeError('灵宠领奖入口实例身份不符')
    frame = context.cur_frame(update=True)
    targets = [r for r in group_ocr_tokens(context.full_frame_ocr_tokens(frame))
               if str(r.get('text') or '').replace(' ', '').replace('\n', '') == '任务'
               and float(r.get('y') or 0) >= float(context.view(933).raw.get('height') or 1600) * 0.85]
    if len(targets) != 1:
        raise RuntimeError('灵宠任务页入口文字未唯一定位，保留现场')
    row = targets[0]
    if not all(k in row for k in ('x', 'y', 'w', 'h')):
        raise RuntimeError('灵宠任务入口缺少真实OCR坐标')
    context.click_frame_point(933, row['x'] + row['w']/2, row['y'] + row['h']/2)
    yield from context.wait_scene([747], wait=20, label='灵宠预赛任务列表正向布局验收')
    page = read_activity_rank_page_snapshot()
    if not page.get('complete') or page.get('activity_id') != activity_id or page.get('tab_index') != 2:
        raise RuntimeError('灵宠任务页实例或页签身份不符，禁止领奖')
    # This provider accepts explicit activity/scene bindings; its Runtime gate
    # and safe left-row region are shared only after the positive layout check.
    result = yield from claim_shequn_lingchong_task_rewards(
        context, activity_id=activity_id, scene_id=747)
    after = read_lingchong_task_milestones(activity_id)
    rewards = after.get('reward_state') or {}
    if not (after.get('complete') and rewards.get('complete')
            and ids <= set(rewards.get('claimed_task_ids') or [])
            and not rewards.get('claimable_task_ids')):
        raise RuntimeError('灵宠任务奖励领取未收敛')
    yield from context.go_scene(34)
    return {'status': 'completed', 'total_claimed': 14, 'rewards': result}


def execute_lingchong_task_rewards_cell(runner, ctx, payload, stop_event):
    context = runner._behavior_tree_context(ctx, ctx.get('asset_tree_path'), stop_event=stop_event)
    return (yield from claim_lingchong_preliminary_rewards(
        context, activity_id=int(payload.get('activity_id') or 0)))
