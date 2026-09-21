"""功法书养成组件：悟境、通玄、法则均消耗已有材料至不足。

业务结果页必须显式继续，不属于弹窗守护。所有动作使用资产 scene/Shape；
本组件负责已打开的升级页，列表扫描、快速融合及藏书共鸣由上层组合。
"""
from __future__ import annotations

import re

BOOK = 781
ENLIGHTENMENT = 786
ENLIGHTENMENT_RESULT = 787
TRANSCENDENCE = 788
TRANSCENDENCE_RESULT = 789
LAW_UPGRADE = 790
UPGRADES = {
    ENLIGHTENMENT: ("悟境", ENLIGHTENMENT_RESULT),
    TRANSCENDENCE: ("通玄", TRANSCENDENCE_RESULT),
    LAW_UPGRADE: ("升级", None),
}


def require_scene(context, scene_id):
    """等待动画中的专属身份完整出现，不点击全局回退识别出的通用页。"""
    for attempt in range(3):
        match = yield from context.wait_scene([scene_id], wait=8)
        if match.scene_id == scene_id:
            return match
        if attempt < 2:
            yield from context.wait_action_settle(.5)
            context.cur_frame(update=True)
    raise RuntimeError(f"功法升级：预期 #{scene_id}，实际 #{match.scene_id}")


def complete_current_upgrade(context, scene_id: int, *, max_actions: int = 100):
    """从当前悟境/通玄/法则页整单重入，材料不足才结束；不购买材料。

    每次读取独立数量 Shape，悟境/通玄显式处理结果页，法则原页刷新。
    下一轮核验材料实际减少；无进展或识别不明保留现场报错。
    """
    action, result_scene = UPGRADES[scene_id]
    previous = None
    actions = 0
    while True:
        yield from require_scene(context, scene_id)
        frame = context.cur_frame(update=True)
        text = context.ocr_text_in_shapes(
            scene_id, ["材料数量"], crop=True, padding=0, frame_data_url=frame,
        )
        ratios = re.findall(r"(\d+)\s*[/／]\s*(\d+)", text)
        if len(ratios) != 1 or int(ratios[0][1]) <= 0:
            raise RuntimeError(f"功法{action}材料数量不明确：{text!r}")
        available, cost = map(int, ratios[0])
        if previous is not None and available >= previous:
            raise RuntimeError(f"功法{action}材料未减少：{previous}→{available}")
        if available < cost:
            return {"upgrade": action, "actions": actions,
                    "remaining": available, "required": cost}
        if actions >= max_actions:
            raise RuntimeError(f"功法{action}超过单次动作上限 {max_actions}")
        yield from context.wait_click(scene_id, action)
        if result_scene is not None:
            yield from require_scene(context, result_scene)
            yield from context.wait_click(result_scene, "继续")
        else:
            yield from context.wait_action_settle(1)
        previous = available
        actions += 1
