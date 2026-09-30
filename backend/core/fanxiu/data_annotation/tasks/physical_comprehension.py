"""第一本炼体功法：未领悟时先激活，再感悟；不执行升阶、淬炼、秘躯。"""
import re

from backend.core.fanxiu.instrumentation.physical_comprehension import read_physical_comprehension_state
from backend.core.fanxiu.instrumentation.item_batch_use_dialog import read_item_batch_use_dialog_snapshot

STAGE_ID = 'physical-comprehension'
STAGE_VERSION = '1'
STAGE_LABEL = '炼体感悟'
UNLEARNED = 899
LEARN_RESULT = 900


def learn_current_physical_book(context):
    """消费当前书页明确显示的已有材料，领悟一次并等待专属结果。

    2026-09-30 长生天王经实测：3/1 → 领悟炼体神通 → 继续 → 升阶。
    结果不明时不重发；新 attempt 通过结果页或已领悟详情自然重入。
    """
    yield from context.wait_scene_exact([UNLEARNED], timeout=10)
    text = context.ocr_text_in_shapes(
        UNLEARNED, ['材料数量'], crop=True, padding=0,
        frame_data_url=context.cur_frame(update=True),
    )
    ratios = re.findall(r'(\d+)\s*[/／]\s*(\d+)', text)
    if len(ratios) != 1 or int(ratios[0][1]) <= 0:
        raise RuntimeError(f'炼体领悟：材料数量不明确：{text!r}')
    available, cost = map(int, ratios[0])
    if available < cost:
        return False
    yield from context.wait_click_then_scene(
        UNLEARNED, '领悟', LEARN_RESULT, timeout=30, max_clicks=1,
    )
    yield from context.wait_click_then_scene(
        LEARN_RESULT, '继续', 868, timeout=20, max_clicks=1,
    )
    return True


def comprehend_first_physical_book(context):
    """取本次进入“全部”页的第一本，整轮固定 baseId，不追逐排序变化。

    路径：角色 #770→炼体 #867→第一本；未领悟 #899 先领悟并消费
    结果 #900，再从升阶 #868（若出现）进入淬体 #869。
    只点右侧感悟丹；底部“淬炼”消耗另一类资源，不能因其红点而点击。
    canGanWu/maxGanWu 是当前客户端计算的准入，服务端 comprehensionLv
    是进展事实。多材料复用 #584 并校验道具与当前数量；单材料直接出结果。
    结果 #870 可能晚于底层等级更新，必须等结果、继续后再判定本轮终态。

    2026-09-26 实测：默认万劫修罗体 910610，百脉宝魄 5050002×4，
    13→17 级；批量默认最大值。完整重跑 actions=0，等级保持 17 并返回
    #34。单材料、满级待真实样本，不伪造成功。
    中断恢复先关闭结果/取消尚未提交的通用弹窗，再重新选第一本；已提交
    由服务器等级和材料承接，不重放旧点击。未知弹窗保留现场报错。
    维修时先查本文件流程、instrumentation 同名状态接口及 #867–870/#899–900。
    """
    start = yield from context.wait_scene([UNLEARNED, LEARN_RESULT, 870, 584, 869, 868, 867, 770, 34], wait=5, required=False)
    scene = start.scene_id if start else None
    if scene == LEARN_RESULT:
        yield from context.wait_click_then_scene(LEARN_RESULT, '继续', 868, timeout=20, max_clicks=1)
        scene = 868
    if scene == 870:
        yield from context.wait_click_then_scene(870, '继续', 869, timeout=20, max_clicks=1)
        scene = 869
    if scene == 584:
        yield from context.wait_click_then_scene(584, '外侧空白', 869, timeout=20, max_clicks=1)
        scene = 869
    if scene in (868, 869):
        yield from context.wait_click_then_scene(scene, '返回', 867, timeout=20, max_clicks=1)
        scene = 867
    if scene not in (867, UNLEARNED):
        if scene != 770:
            yield from context.go_scene(34)
            yield from context.wait_click_then_scene(34, '角色', 770, timeout=20)
        yield from context.wait_click_then_scene(770, '炼体', 867, timeout=30)
    if scene != UNLEARNED:
        yield from context.wait_click(867, '第一本', timeout=10)
    detail = yield from context.wait_scene_exact([UNLEARNED, 868, 869], timeout=30)
    if detail.scene_id == UNLEARNED:
        learned = yield from learn_current_physical_book(context)
        if not learned:
            yield from context.wait_click_then_scene(UNLEARNED, '返回', 867, timeout=20, max_clicks=1)
            yield from context.wait_click_then_scene(867, '属性', 770, timeout=20, max_clicks=1)
            yield from context.go_scene(34)
            return {'result': 'success', 'outcome': 'insufficient_learning_material',
                    'actions': 0, 'final_scene': 34}
        detail = yield from context.wait_scene_exact([868], timeout=20)
    if detail.scene_id == 868:
        yield from context.wait_click_then_scene(868, '淬体', 869, timeout=20)
    state = read_physical_comprehension_state()
    base_id, first_level, actions = state['base_id'], state['level'], 0
    while state['can_upgrade']:
        if actions >= 100 or state['base_id'] != base_id:
            raise RuntimeError('炼体感悟：次数异常或功法发生变化')
        before = state['level']
        yield from context.wait_click(869, '感悟丹', timeout=10)
        result = yield from context.wait_scene_exact([584, 870], timeout=40)
        if result.scene_id == 584:
            batch = read_item_batch_use_dialog_snapshot(expected_item_id=state['item_id'])
            if batch['current'] != batch['single_use_maximum']:
                raise RuntimeError('炼体感悟：批量数量非默认最大值，保留弹窗')
            yield from context.wait_click(584, '使用', timeout=10)
            yield from context.wait_scene_exact([870], timeout=40)
        yield from context.wait_click_then_scene(870, '继续', 869, timeout=20, max_clicks=1)
        state = read_physical_comprehension_state()
        if state['base_id'] != base_id or state['level'] <= before:
            raise RuntimeError('炼体感悟：等级未增长或功法变化，禁止重复提交')
        actions += 1
    yield from context.wait_click_then_scene(869, '返回', 867, timeout=20, max_clicks=1)
    yield from context.wait_click_then_scene(867, '属性', 770, timeout=20, max_clicks=1)
    yield from context.go_scene(34)
    return {'result': 'success', 'outcome': 'complete', 'actions': actions, 'base_id': base_id,
            'level_before': first_level, 'level_after': state['level'], 'final_scene': 34}
