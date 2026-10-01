"""三皇领悟 GUI 动作；资产 #856/#857/#908，原生节点决定动作类型。

普通领悟、最左抉择及共鸣结果页使用各自已标注的动作。
未识别弹窗时保留现场，禁止猜背景坐标或重复发送消耗动作。
"""
import time

from ..instrumentation.three_emperors import read_three_emperors, read_three_emperors_choice

MAIN, CHOICE, RESONANCE_RESULT = 856, 857, 908


class ComprehensionNotConfirmed(RuntimeError):
    """A click timed out; the caller must recheck level and stock before retry."""


def wait_emperors(context, scenes=(MAIN, CHOICE)):
    """Consume the confirmed resonance overlay before observing the next node.

    A node's level can already advance underneath its delayed result overlay.
    Only the uncovered main page is ready for the next spend or safe exit.
    """
    scene = int((yield from context.wait_scene([*scenes, RESONANCE_RESULT], wait=20)))
    if scene == RESONANCE_RESULT:
        yield from context.wait_click_then_scene(RESONANCE_RESULT, '继续', MAIN)
        scene = int((yield from context.wait_scene(list(scenes), wait=20)))
    if scene not in scenes:
        raise RuntimeError(f'三皇页面被打断或出现待标注结果页：#{scene}')
    return scene


def choose_left(context):
    """Only activate a new skill, never pay to replace an existing selection."""
    yield from wait_emperors(context, (CHOICE,))
    before = read_three_emperors_choice()
    if before['nodeState'] != 1 or before['initSelectedSkill'] != 0:
        raise RuntimeError('当前为已有神通切换，不属于每日领悟')
    if before['option_count'] != 2:
        raise RuntimeError('三皇抉择布局变化，需重新验证最左选项 Shape')
    context.click_shape_center_fast(CHOICE, '最左选项')
    deadline = time.monotonic()+10
    while True:
        selected = read_three_emperors_choice()
        if selected['node']['id'] != before['node']['id']:
            raise RuntimeError('三皇抉择节点在选择期间变化')
        if selected['selected_first'] and selected['isCanClick'] is True:
            return selected
        if time.monotonic() >= deadline:
            raise RuntimeError('最左神通未确认选中')
        yield from context.wait_action_settle(.4)


def await_level(context, before):
    """Clear recognized result overlays before confirming the next level."""
    deadline = time.monotonic()+25
    while time.monotonic() < deadline:
        scene = yield from wait_emperors(context, (MAIN, CHOICE, 351))
        if scene == 351:
            yield from context.wait_click(351, '继续')
            yield from context.wait_action_settle(1)
            continue
        if scene == MAIN:
            after = read_three_emperors()
            if after['curLevel'] == before['curLevel']+1:
                return after
            if after['curLevel'] != before['curLevel']:
                raise RuntimeError('三皇等级发生外部变化，材料模型需重建')
        yield from context.wait_action_settle(.4)
    raise ComprehensionNotConfirmed('三皇领悟结果未确认；需核对等级和库存')
