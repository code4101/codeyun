"""法宝养成的 GUI 组件；以用户现场教学和真实页面为契约。

升级、共鸣使用原生批量入口；神炼在法宝、古宝背包分别处理置顶候选。
未升一阶的候选即使显示可神炼也必须跳过。这里不读取库存 Runtime，
不因静态法宝配置缺项阻塞游戏原生功能。尚未验证的页面分支不猜测执行。
"""

from __future__ import annotations

import re
import time

TALISMAN_BAG = 554
RESONANCE = 775
ANCIENT_BAG = 776
REFINEMENT = 777
RESULT = 544
RESONANCE_PROGRESS = 779


def require_scene(context, scene_id):
    """等待真实目标；旧框架可能返回全局回退结果，不能仅凭正常返回算成功。"""
    match = yield from context.wait_scene([scene_id], wait=8)
    if match.scene_id != scene_id:
        raise RuntimeError(f'预期 #{scene_id}，实际 #{match.scene_id}')
    return match


def verify_quick_upgrade_idle(context):
    """通过原生无候选提示证明升级终态；不会自动确认未知确认框。"""
    yield from require_scene(context, TALISMAN_BAG)
    context.click_shape_center_fast(TALISMAN_BAG, '快速升级')
    # 先保留短暂反馈帧，再识别；同 tick 必须显式取新帧，不能读截图缓存。
    frames = []
    for _ in range(5):
        if context.stop_event is not None and context.stop_event.wait(.2):
            raise InterruptedError('升级终态采样中断')
        if context.stop_event is None:
            time.sleep(.2)
        frames.append(context.cur_frame(update=True))
    for frame in frames:
        if context.shape_matches(TALISMAN_BAG, '无可升级提示', frame_data_url=frame):
            return {'status': 'nothing_to_upgrade', 'upgrades': 0}
    raise RuntimeError('未取得“暂无可升级法宝”终态；保留现场，不能按无变化判成功')


def verify_resonance_idle(context):
    """共鸣空批次无可见回执；用两个品质页下一阶条件验收，不反复发请求。"""
    yield from require_scene(context, TALISMAN_BAG)
    yield from context.wait_click(TALISMAN_BAG, '法宝共鸣入口')
    progress = {}
    for quality in ('珍品法宝', '绝品法宝'):
        yield from require_scene(context, RESONANCE)
        yield from context.wait_click(RESONANCE, quality)
        yield from require_scene(context, RESONANCE_PROGRESS)
        text = context.ocr_text_in_shapes(RESONANCE_PROGRESS, ['共鸣条件'])
        current, required = material_ratio(text)
        if current >= required:
            raise RuntimeError(f'{quality}仍可共鸣：{current}/{required}')
        progress[quality] = {'current': current, 'required': required}
        yield from context.wait_click(RESONANCE_PROGRESS, '返回')
    yield from require_scene(context, RESONANCE)
    yield from context.wait_click(RESONANCE, '返回')
    yield from require_scene(context, TALISMAN_BAG)
    return {'status': 'requirements_not_met', 'resonances': 0, 'progress': progress}


def verify_talisman_cultivation_idle(context):
    """整套养成的真实幂等验收入口，留在法宝背包。

    本函数验证已完成后的空操作分支，不冒充有新资源时的完整执行 Task。
    升级与共鸣不读 Runtime；神炼候选只观察一次，0 阶无本体排除。
    """
    from backend.core.fanxiu.instrumentation.magic_treasure import read_talisman_refinement_candidates
    upgrade = yield from verify_quick_upgrade_idle(context)
    resonance = yield from verify_resonance_idle(context)
    refinement = read_talisman_refinement_candidates()
    if not refinement.get('complete'):
        raise RuntimeError(f'神炼候选核验不完整：{refinement.get("reason")}')
    if refinement['candidates']:
        raise RuntimeError(f'仍有可执行神炼：{refinement["candidates"]}')
    return {'status': 'idempotent', 'upgrade': upgrade, 'resonance': resonance,
        'refinement': {'actions': 0, 'eligible_candidates': 0, 'zero_stage_policy': 'skip'}}


def material_ratio(text: str) -> tuple[int, int]:
    """只解析专用数量 Shape 的持有/消耗比；不把识别失败当作零。"""
    values = re.findall(r"(\d+)\s*[/／]\s*(\d+)", text)
    if len(values) != 1 or int(values[0][1]) <= 0:
        raise ValueError(f"神炼材料数量不明确：{text!r}")
    return tuple(map(int, values[0]))


def complete_current_refinement(context, *, max_actions: int = 64):
    """已进入且已证明拥有本体的神炼页，消耗可用专属材料至不足。

    首次及批次结束读取数量；批内沿已知消耗递减，不反复检查背包。
    晋升是免费中间步骤，优先于材料不足终态；晋升后重读新阶段消耗。
    不会购买材料或自动升级未拥有本体。
    返回后仍留在详情页；背包扫描与安全离场由调用者组合。
    """
    actions = 0
    remaining = None
    cost = None
    promotions = 0
    while True:
        match = yield from context.wait_scene([REFINEMENT], wait=8)
        if match.scene_id != REFINEMENT:
            raise RuntimeError(f"神炼动作后页面未就绪：{match.scene_id}")
        frame = context.cur_frame()
        if context.shape_matches(REFINEMENT, '晋升', frame_data_url=frame):
            if promotions >= 16:
                raise RuntimeError('晋升分支未收敛')
            yield from context.wait_click(REFINEMENT, '晋升')
            yield from context.wait_action_settle(2)
            promotions += 1
            remaining = None
            continue
        if remaining is None or remaining < cost:
            # 复用场景识别已有 OCR，仅提取数量框；极窄裁图会漏检 1/1。
            text = context.ocr_text_in_shapes(REFINEMENT, ['材料数量'], frame_data_url=frame, padding=4)
            available, cost = material_ratio(text)
            if remaining is not None and available != remaining:
                raise RuntimeError(f'神炼批次结果与已知消耗不符：预计 {remaining}，实际 {available}')
            remaining = available
            if remaining < cost:
                return {'status': 'materials_exhausted', 'actions': actions, 'promotions': promotions, 'remaining': remaining}
        if actions >= max_actions:
            raise RuntimeError('神炼达到单次安全上限，尚未完成')
        yield from context.wait_click(REFINEMENT, '神炼')
        yield from context.wait_action_settle(2)
        remaining -= cost
        actions += 1
