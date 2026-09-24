"""洗灵更新：处理灵器提示；洗灵祈愿周顺带清理背包，再返回世界页。

资源_每日处理以每日完成凭证调用本组件。箭头只用于定位；实际装配、
效果已读、升阶和悟境由 prompt_scan 的逐轮业务判据决定。
"""
from __future__ import annotations

import time

from .spirit_artifact_prompt_scan import spirit_artifact_prompt_update_steps
from .spirit_artifact_bag_decompose import decompose_spirit_artifact_bag_during_prayer
from .world_menu_navigation import open_world_menu_function


STAGE_ID = 'spirit-artifact-prompt-update'
STAGE_VERSION = '2'
STAGE_LABEL = '洗灵更新'


def update_spirit_artifact_prompts(context, *, max_seconds: int = 1800):
    """每日组件；未清尽的箭头或中断不能签发完成凭证。"""
    if max_seconds <= 0:
        raise ValueError('洗灵更新需要有效执行时限')
    stop_at = time.time() + max_seconds
    yield from open_world_menu_function(
        context, 1190001, expected_scene_ids=(666,), timeout_seconds=30,
    )
    outcome = yield from spirit_artifact_prompt_update_steps(
        context, stop_at=stop_at,
    )
    if outcome['status'] != 'complete':
        raise RuntimeError(f'洗灵更新尚未清尽提示：{outcome}')
    bag = yield from decompose_spirit_artifact_bag_during_prayer(context)
    if bag['status'] == 'pass':
        context.click_shape_center(666, '返回')
        match = yield from context.wait_scene([34, 661], wait=15)
        if match.scene_id not in (34, 661):
            raise RuntimeError(f'洗灵更新未返回世界：#{match.scene_id}')
    return {'result': 'success', 'outcome': 'complete',
            'actions': outcome['actions'], 'red_ware_ids': [], 'bag': bag}
