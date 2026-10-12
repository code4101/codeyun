"""Free Tianhe activity-task claims through the shared verified row workflow."""
from .daily_task_rewards import claim_first_row_until_clear
from .tianhe_navigation import enter_tianhe_activity_home
from backend.core.fanxiu.instrumentation.revenue_task_page import read_revenue_task_page_snapshot
from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
    read_scheduler_research_batch_admission, read_scheduler_batch_admission,
)


def execute_tianhe_task_rewards(runner, ctx, payload, stop_event):
    """Claim completed active tasks only, in bounded engineering idle windows.

    Native task identity/readiness plus QuestMgr.finishTasks verify each click.
    Unfinished conditions are never clicked. Ticket rewards accumulate after
    the first grand prize; this Task never calls a draw or gifting action.
    """
    activity_id = payload.get('activity_id')
    if type(activity_id) is not int or activity_id <= 0:
        raise ValueError('天河任务领奖需要精确活动ID')
    limit = payload.get('max_claims', 2)
    if type(limit) is not int or not 1 <= limit <= 2:
        raise ValueError('当前批次最多领取2项任务')
    context = runner._behavior_tree_context(ctx, ctx.get('asset_tree_path'), stop_event=stop_event)
    parent_task_id = str(payload.get('__scheduler_task_id') or '')
    admission = (read_scheduler_batch_admission(
        task_id=parent_task_id, batch_seconds=180, release_seconds=120, protect_all=True,
    ) if parent_task_id else read_scheduler_research_batch_admission(
        batch_seconds=180, release_seconds=120,
    ))
    if not admission.get('admitted'):
        yield from context.go_scene(34)
        return {'status': 'pending', 'reason': 'engineering_duty', 'admission': admission}
    initial = read_revenue_task_page_snapshot(activity_id)
    if not initial.get('complete'):
        yield from enter_tianhe_activity_home(context, activity_id=activity_id)
        yield from context.wait_click(498, '任务')
        yield from context.wait_scene_exact([500], timeout=20, observation_scenes=[498])
        initial = read_revenue_task_page_snapshot(activity_id)
    if not initial.get('complete') or initial.get('subtype') != 2:
        raise RuntimeError('天河活跃任务页未就绪：' + str(initial.get('reason')))
    identity = initial['evidence']

    def reader(_domain, *, expected_claimed_task_id=None):
        result = read_revenue_task_page_snapshot(activity_id, expected_task_id=expected_claimed_task_id)
        if not result.get('complete'):
            raise RuntimeError('领取后任务事实读取不完整：' + str(result.get('reason')))
        if result.get('evidence') != identity:
            raise RuntimeError('领取前后任务进程身份改变，保留现场')
        return result

    result = yield from claim_first_row_until_clear(
        domain='tianhe', scene_id=500, context=context, reader=reader,
        initial_snapshot=initial, click_x=470, click_y=365, max_claims=limit,
    )
    bounded = str(result.get('reason', '')).startswith('达到单域领取上限')
    if not result.get('ok') and not bounded:
        raise RuntimeError('天河任务领奖尚未核验：' + str(result.get('reason')))
    yield from context.go_scene(34)
    return {'status': 'pending' if bounded else 'success', **result}
