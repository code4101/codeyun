"""法宝养成的 GUI 组件；以用户现场教学和真实页面为契约。

升级、共鸣使用原生批量入口；神炼在法宝、古宝背包分别处理置顶候选。
未升一阶的候选即使显示可神炼也必须跳过。升级候选由已有组件负责，
本组件补齐共鸣和神炼；Runtime 提供已拥有本体及材料事实，GUI 核对名称。
"""

from __future__ import annotations

import re
import time

TALISMAN_BAG = 554
RESONANCE = 775
ANCIENT_BAG = 776
REFINEMENT = 777
RESULT = 839  # 法宝升阶与共鸣共享“点击屏幕继续”结果布局。
RESONANCE_PROGRESS = 779
STAGE_ID = 'talisman-cultivation'
STAGE_VERSION = '1'


def complete_talisman_cultivation(context):
    """每日升阶之后处理共鸣、法宝及古宝神炼，最后返回世界。

    每次重入重新取候选，不续跑旧详情。只消费现有专属材料，不购材；
    零阶对象由 Runtime 排除。未定位到授权候选即报错，不跳过后记完成。
    """
    from backend.core.fanxiu.instrumentation.magic_treasure import read_talisman_refinement_candidates
    from .world_menu_navigation import open_world_menu_function

    match = yield from context.wait_scene([RESULT, 777, 778, 779, 775, 776, 554, 553, 34], wait=8)
    recovered_result = match.scene_id == RESULT
    if recovered_result:
        yield from context.wait_click(RESULT, '继续')
        match = yield from context.wait_scene_exact([775, 554], timeout=12)
    if match.scene_id in (777, 778, 779, 775):
        yield from context.wait_click(match.scene_id, '返回')
    yield from context.go_scene(34)
    yield from open_world_menu_function(context, 5000, expected_scene_ids=(553,), timeout_seconds=30)
    yield from context.go_scene(TALISMAN_BAG)
    yield from context.wait_click(TALISMAN_BAG, '法宝共鸣入口')
    yield from context.wait_click(RESONANCE, '快速共鸣')
    yield from context.wait_action_settle(2)
    match = yield from context.wait_scene_exact([RESULT, RESONANCE], timeout=15)
    for _ in range(3):
        if match.scene_id == RESULT:
            break
        yield from context.wait_action_settle(1)
        match = yield from context.wait_scene_exact([RESULT, RESONANCE], timeout=15)
    resonated = recovered_result or match.scene_id == RESULT
    if match.scene_id == RESULT:
        yield from context.wait_click(RESULT, '继续')
    yield from require_scene(context, RESONANCE)
    yield from context.wait_click(RESONANCE, '返回')
    resonance = yield from verify_resonance_idle(context)

    receipts = []
    for _ in range(100):
        snapshot = read_talisman_refinement_candidates()
        if not snapshot.get('complete'):
            raise RuntimeError(f"法宝神炼候选不完整：{snapshot.get('reason')}")
        candidates = snapshot['candidates']
        if not candidates:
            yield from context.go_scene(34)
            yield from require_scene(context, 34)
            return {'result': 'success', 'outcome': 'complete',
                    'resonated': resonated, 'resonance': resonance,
                    'refinements': receipts, 'remaining_candidates': 0, 'final_scene': 34}
        target_bag = candidates[0]['bag']
        match = yield from context.wait_scene_exact([TALISMAN_BAG, ANCIENT_BAG], timeout=10)
        scene = TALISMAN_BAG if target_bag == '法宝' else ANCIENT_BAG
        if match.scene_id != scene:
            yield from context.wait_click(match.scene_id, target_bag)
        yield from require_scene(context, scene)
        yield from context.wait_action_settle(1)
        frame = context.cur_frame(update=True)
        selected = None
        for shape in ('首排一', '首排二', '首排三'):
            text = ''.join(context.ocr_text_in_shapes(scene, [shape], frame_data_url=frame).split())
            matches = [c for c in candidates if c['bag'] == target_bag and c['name'] in text]
            if len(matches) == 1:
                selected = matches[0]
                yield from context.wait_click(scene, shape)
                break
        if selected is None:
            raise RuntimeError(f'法宝神炼：置顶候选未定位，保留现场：{candidates}')
        match = yield from context.wait_scene_exact([777, 778], timeout=12)
        if match.scene_id == 778:
            yield from context.wait_click(778, '神炼页签')
        yield from require_scene(context, REFINEMENT)
        text = ''.join(context.ocr_text_in_shapes(REFINEMENT, ['道具名称']).split())
        if selected['name'] not in text:
            raise RuntimeError(f"法宝神炼详情身份不符：{selected['name']} / {text}")
        result = yield from complete_current_refinement(context)
        if not result['actions'] and not result['promotions']:
            raise RuntimeError(f"法宝神炼候选无进展：{selected['name']}")
        receipts.append({'talisman_id': selected['talisman_id'], 'name': selected['name'], **result})
        yield from context.wait_click(REFINEMENT, '返回')
        yield from context.wait_scene_exact([TALISMAN_BAG, ANCIENT_BAG], timeout=12)
    raise RuntimeError('法宝神炼候选超过保护上限，不能标记完成')


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
