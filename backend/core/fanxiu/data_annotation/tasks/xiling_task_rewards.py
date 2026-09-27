"""洗灵证武领奖适配：复用共享逐档事务，以 QuestMgr 已领取状态验收。

首行进度会被世界公告遮挡，不能用 OCR 阴性判断领完。页面只负责
定位首行安全区；每档点击后必须验证对应 taskId 的领取状态迁移。
"""
from datetime import datetime

from ...instrumentation.xiling_task_rewards import discover_xiling_task_spec
from ...instrumentation.daily_task_rewards import read_task_reward_spec_fast_snapshot
from .gameplay_rank_task_rewards import (
    GameplayRankTaskAssets, GameplayRankTaskTab, claim_gameplay_rank_task_tabs,
)
from .resource_rank_daily_gift import (
    RESOURCE_RANK_GIFT_ADAPTERS, open_resource_rank_activity_page,
)


def claim_xiling_task_rewards(context, *, activity_id: int):
    """领取指定本期已达成档位并返回榜页；重入跳过已领取档位。

    调用者持有 Kernel 独占权。只领奖，不洗炼、不使用奖励物品。
    2026-09-27 的 #877 任务页提供首行安全领取区与返回榜页入口。
    """
    current = yield from context.wait_scene([733, 877], wait=5, required=False)
    if current is not None and current.scene_id == 877:
        yield from context.wait_click_then_scene(
            877, '榜单', [733], timeout=20, label='洗灵领奖：返回榜页',
        )
    adapter = next(a for a in RESOURCE_RANK_GIFT_ADAPTERS if a.key == 'xiling-zhengwu')
    yield from open_resource_rank_activity_page(
        context, adapter, activity_id=activity_id, now=datetime.now().astimezone(),
    )
    spec = discover_xiling_task_spec(activity_id)

    def reader(**kwargs):
        value = read_task_reward_spec_fast_snapshot(spec, **kwargs)
        value['task_subtypes'] = {task_id: 1 for task_id in spec.task_ids}
        return value

    result = yield from claim_gameplay_rank_task_tabs(
        context,
        assets=GameplayRankTaskAssets(
            activity_label='洗灵证武', home_scene_id=733, home_shape='榜单',
            tabs=(GameplayRankTaskTab('洗灵', 1, 877, '洗灵页签'),),
        ),
        reader=reader,
    )
    yield from context.wait_scene([733], wait=15)
    return result
