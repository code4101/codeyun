"""衣装阁：原生升级/激活/共鸣的每日资源组件。"""
from datetime import datetime

from backend.core.fanxiu.instrumentation.wardrobe_cultivation import (
    read_wardrobe_cultivation, read_wardrobe_resonance,
)

STAGE_ID = 'wardrobe-cultivation'
STAGE_VERSION = '1'
MAIN, DETAIL = 888, 889
RESULT = 890
RESONANCE = 891


def _wait(context, scenes):
    context.clear_frame()
    return (yield from context.wait_scene_exact(scenes, timeout=20))


def settle_wardrobe(context):
    """关闭升级/激活触发的结果弹窗，回到可继续处理的主馆。"""
    for _ in range(8):
        scene = yield from _wait(context, [RESULT, DETAIL, MAIN])
        if scene.scene_id == MAIN:
            return
        yield from context.wait_click(scene.scene_id,
                                      '继续' if scene.scene_id == RESULT else '关闭')
        yield from context.wait_action_settle(2)
        context.clear_frame()
    raise RuntimeError('衣装阁结果弹窗未收敛')


def _budget(stop_at):
    if stop_at is not None and datetime.now() >= stop_at:
        raise TimeoutError('衣装阁研发窗口结束，保留已完成进度，须归还调度权')


def upgrade_current_category(context, *, stop_at=None):
    """清空当前分类的可升级/可激活项；动作后按原生等级和拥有状态验收。

    列表会在操作后自动重排，必须重新定位文字，禁止复用旧位置。
    未定位的 Runtime 候选不当成完成；扫描窗口保留重叠区。
    """
    completed = []
    for _ in range(150):
        _budget(stop_at)
        yield from _wait(context, [MAIN])
        before = read_wardrobe_cultivation()
        pending = [r for r in before['items'] if r['can_activate'] or r['can_upgrade']]
        if not pending:
            return completed
        match = None
        for direction in ('down', 'up'):
            # direction 表示加载方向，down 对应手指向上拖动。
            for scan in range(max(3, len(before['items']) // 4 + 2)):
                _budget(stop_at)
                for text in ('启动', '激活', '升级'):
                    match = context.find_ocr_text(MAIN, text, in_shapes=['条目列表'],
                                                  crop=True, occurrence=0)
                    if match:
                        break
                if match:
                    break
                context.drag_shape_content(MAIN, '条目列表', direction=direction)
                yield from context.wait_action_settle(1.5)
                yield from _wait(context, [MAIN])
            if match:
                break
        if match is None:
            raise RuntimeError(f'衣装阁提示候选未能定位：{pending}')
        context.click_frame_point(MAIN, *match.point())
        yield from context.wait_action_settle(2)
        # 第一次点选可直接激活；升级则选中，再点同项才打开详情。
        state = read_wardrobe_cultivation()
        current = state['selected']
        if current is None:
            raise RuntimeError('衣装阁点选后没有所选身份')
        old = next((r for r in before['items'] if r['id'] == current['id']), None)
        if old is None:
            raise RuntimeError('衣装阁点选目标不在观察目录')
        if not old['owned'] and current['owned']:
            completed.append(dict(action='activate', id=current['id']))
            yield from settle_wardrobe(context)
            continue
        if state['detail'] is None:
            if not current['can_upgrade']:
                raise RuntimeError(f'衣装阁提示与所选门槛冲突：{current}')
            # 点选没有改变目录；仍需重新定位当前升级提示。
            m = context.find_ocr_text(MAIN, '升级', in_shapes=['条目列表'], crop=True, occurrence=0)
            if m is None:
                raise RuntimeError('衣装阁所选升级提示消失')
            context.click_frame_point(MAIN, *m.point())
        yield from _wait(context, [DETAIL])
        state = read_wardrobe_cultivation()
        detail = state['detail']
        if detail is None or detail['id'] != current['id']:
            raise RuntimeError('衣装升级详情身份不符')
        for _ in range(100):
            _budget(stop_at)
            if not detail['actionable']:
                break
            if detail['action_type'] != 2 or not detail['owned']:
                raise RuntimeError(f'衣装阁未知消费类型：{detail}')
            text = '一键升级' if detail['batch'] else '升级'
            m = context.find_ocr_text(DETAIL, text, in_shapes=['操作区'], crop=True)
            if m is None:
                raise RuntimeError(f'衣装阁缺少原生按钮：{text}')
            context.click_frame_point(DETAIL, *m.point())
            for poll in range(12):
                yield from context.wait_action_settle(1)
                updated = read_wardrobe_cultivation()['detail']
                if updated and updated['id'] == detail['id'] and updated['level'] > detail['level']:
                    break
            else:
                raise RuntimeError(f'衣装升级无进展：{detail}')
            completed.append(dict(action='upgrade', id=detail['id'],
                                  before=detail['level'], after=updated['level']))
            print(f"衣装阁 #{detail['id']}：{detail['level']} → {updated['level']}", flush=True)
            detail = updated
            landed = yield from _wait(context, [RESULT, DETAIL])
            if landed.scene_id == RESULT:
                yield from context.wait_click(RESULT, '继续')
                yield from _wait(context, [DETAIL])
        else:
            raise RuntimeError('衣装升级超过动作上限')
        yield from context.wait_click(DETAIL, '关闭')
    raise RuntimeError('衣装分类超过动作上限')


def complete_current_resonance(context, *, stop_at=None):
    """激活所有已满足条件的仙语阁档位，以服务器阶数增长确认进展。"""
    yield from _wait(context, [RESONANCE])
    state = read_wardrobe_resonance()
    initial = state['stage']
    for _ in range(1000):
        _budget(stop_at)
        if state['current']['id'] != state['selected']['id']:
            raise RuntimeError('仙语阁未选中当前应激活档位')
        if not state['actionable'] or state['already_active']:
            return dict(before=initial, after=state['stage'], next_condition=state['current'])
        context.click_shape_center(RESONANCE, '激活')
        for poll in range(12):
            yield from context.wait_action_settle(1)
            updated = read_wardrobe_resonance()
            if updated['stage'] > state['stage']:
                break
        else:
            raise RuntimeError(f'仙语阁激活无进展：{state}')
        state = updated
    raise RuntimeError('仙语阁激活超过保护上限')


def dismiss_suit_notices(context, *, stop_at=None):
    """查看已生效的新仙语；客户端入场自动滚到第一个新提示所在视窗。

    原生 ShowItem(index) 定位目标；末页可能不能顶齐，故最多检查该视窗
    八个卡片。仅浏览，不点击穿戴。以 newFashionSuitRedList 缩减验收。
    """
    cleared = 0
    for _ in range(100):
        _budget(stop_at)
        before = read_wardrobe_cultivation(include_items=False)['new_suits']
        if not before:
            return cleared
        yield from context.wait_click(MAIN, '时装')
        yield from context.wait_action_settle(1)
        yield from context.wait_click(MAIN, '仙语')
        yield from context.wait_action_settle(2)
        yield from _wait(context, [MAIN])
        for index in range(1, 9):
            _budget(stop_at)
            context.click_shape_center(MAIN, f'卡片{index}')
            yield from context.wait_action_settle(1)
            after = read_wardrobe_cultivation(include_items=False)['new_suits']
            if len(after) < len(before):
                cleared += len(before) - len(after)
                break
        else:
            raise RuntimeError(f'新仙语视窗查看无进展：{before}')
    raise RuntimeError('新仙语提示超过保护上限')


def complete_wardrobe(context, *, stop_at=None):
    """从稳定入口处理形象、装扮、仙语共鸣及新提示，返回世界页。

    仅使用游戏原生可升级/启动按钮和已满足的共鸣条件，不购买材料，
    不点击穿戴。材料不足、满级和条件未满足为正常终态；身份冲突、
    动作无进展、未知弹窗或未处理提示均失败关闭，不提交每日完成凭证。
    唱片集只有播放功能，不属于资源培养。
    """
    _budget(stop_at)
    scene = yield from context.wait_scene([RESONANCE, RESULT, DETAIL, MAIN, 892, 20, 34], wait=5)
    if scene.scene_id == RESONANCE:
        yield from context.wait_click(RESONANCE, '返回')
        yield from context.wait_action_settle(2)
    elif scene.scene_id == 892:
        yield from context.wait_click(892, '形象')
        yield from context.wait_action_settle(2)
    elif scene.scene_id not in (RESULT, DETAIL, MAIN):
        yield from context.go_scene(20)
        yield from _wait(context, [20])
        found = False
        for direction in ('right', 'left'):
            for _ in range(12):
                _budget(stop_at)
                target = context.find_ocr_text(20, '衣装阁', in_shapes=['菜单'], crop=True)
                if target:
                    context.click_frame_point(20, *target.point())
                    found = True
                    break
                context.drag_shape_content(20, '菜单', direction=direction)
                yield from context.wait_action_settle(2)
                yield from _wait(context, [20])
            if found:
                break
        if not found:
            raise RuntimeError('绿瓶菜单未找到衣装阁')
    yield from settle_wardrobe(context)
    actions = {}
    for group, tabs in [('形象', ('时装', '武器', '环身', '背饰', '御器')),
                        ('装扮', ('头像', '头像框', '聊天框'))]:
        yield from context.wait_click(MAIN, group)
        yield from context.wait_action_settle(2)
        for tab in tabs:
            _budget(stop_at)
            yield from context.wait_click(MAIN, tab)
            yield from context.wait_action_settle(2)
            actions[tab] = yield from upgrade_current_category(context, stop_at=stop_at)
    yield from context.wait_click(MAIN, '形象')
    yield from context.wait_action_settle(2)
    yield from context.wait_click(MAIN, '仙语阁')
    resonance = yield from complete_current_resonance(context, stop_at=stop_at)
    yield from context.wait_click(RESONANCE, '返回')
    yield from context.wait_action_settle(2)
    yield from _wait(context, [MAIN])
    notices = yield from dismiss_suit_notices(context, stop_at=stop_at)
    final = read_wardrobe_cultivation(include_items=False)
    if final['new_ids'] or final['new_suits']:
        raise RuntimeError(f"衣装阁仍有未处理提示：{final['new_ids']}, {final['new_suits']}")
    yield from context.wait_click(MAIN, '返回')
    yield from _wait(context, [20])
    yield from context.go_scene(34)
    yield from _wait(context, [34])
    return dict(complete=True, actions=actions, resonance=resonance, notices=notices)
