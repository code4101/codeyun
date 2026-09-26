"""遍历神印左右分组，使用已有材料激活、升级、突破。"""
from backend.core.fanxiu.instrumentation.god_seal import read_god_seal_state

STAGE_ID = 'god-seal-update'
STAGE_VERSION = '1'
STAGE_LABEL = '神印升级'
MAIN, DETAIL = 871, 872


def part_shape(row):
    """原生 initPos 对齐五个已标注槽位；新布局报错后再校准资产。

    parts 编号在不同组不代表同一位置，不能以编号或列表顺序直接点槽。
    号令、鱼际已实测共用五槽布局；隐藏/未解锁部位不点击。
    """
    anchors = [(0, 106.7, '上方部位'), (89, 33, '右上部位'),
               (64.3, -63.9, '右下部位'), (-64.3, -63.9, '左下部位'),
               (-89, 33, '左上部位')]
    x, y = row['position']
    found = [name for px, py, name in anchors if abs(x-px) < .2 and abs(y-py) < .2]
    if len(found) != 1:
        raise RuntimeError(f'神印新布局需标注：{row}')
    return found[0]


def _ready(context, target):
    for _ in range(6):
        scene = yield from context.wait_scene([689, 578, target], wait=5, required=False)
        scene = scene.scene_id if scene else None
        if scene == target:
            return
        if scene == 689:
            yield from context.wait_click(689, '返回', timeout=10)
        elif scene != 578:
            raise RuntimeError(f'神印：未知弹窗 #{scene}，保留现场')
        yield from context.wait_action_settle(2)
    raise RuntimeError('神印弹窗未收敛')


def update_god_seals(context):
    """#34→角色 #770→分组 #871⇄部位 #872，全部结束返回 #34。

    先向左回到第零组，再逐组向右，以 Runtime curSelectIndex/maxIndex
    核验每次切换及终点；箭头不存在时不盲点。所有分组复用 #871，激活、
    升级、突破共用 #872；按钮文案变化不创建重复场景。
    每个已解锁部位持续操作到材料不足/满级，不购买材料，不点锁定部位。
    单次提交后等动画、读服务器 level 增长再继续，失败不重发旧请求。
    新 attempt 从当前等级继续，已消耗材料不会重复计算；通用活动提示
    #689 和奖励遮罩 #578 统一收敛，未知页面保留现场。

    维护线索：GodWrathMainPanel 的左右箭头控制整组，SmallGodWrathInfoPanel
    的 SureBtn 控制激活/升级/突破，baseVo.level 单调记录进展。
    新分组布局由 part_shape 核验；出现新位置应先实测标注再扩展映射。

    2026-09-26 实测两组十个部位，其中五个解锁；盟用已有材料从 0→1→2，
    破 121、诛 82、花 2、仙 2 均材料不足。正例覆盖激活/普通升级；
    完整重跑两组五部位 actions=0，返回 #34，验证材料不足终态幂等。
    满级和跨重突破尚待真实材料样本，动画守卫按原生分支保留。
    """
    start = yield from context.wait_scene([DETAIL, MAIN, 770, 34], wait=5, required=False)
    scene = start.scene_id if start else None
    if scene == DETAIL:
        yield from context.wait_click_then_scene(DETAIL, '返回', MAIN, timeout=25, max_clicks=1)
        scene = MAIN
    if scene != MAIN:
        if scene != 770:
            yield from context.go_scene(34)
            yield from context.wait_click_then_scene(34, '角色', 770, timeout=20)
        yield from context.wait_click_then_scene(770, '神印', MAIN, timeout=30)
    yield from _ready(context, MAIN)
    state = read_god_seal_state()
    maximum = state['max_index']
    while state['index'] > 0:
        previous = state['index']
        yield from context.wait_click(MAIN, '上一组', timeout=10)
        yield from context.wait_action_settle(2)
        state = read_god_seal_state()
        if state['index'] != previous-1:
            raise RuntimeError('神印向左切换未生效')
    changes, inspected = [], []
    for index in range(maximum+1):
        if state['index'] != index or state['max_index'] != maximum or state['moving']:
            raise RuntimeError('神印分组尚未稳定或目录变化')
        for row in state['parts']:
            if not row['open']:
                continue
            yield from context.wait_click_then_scene(MAIN, part_shape(row), DETAIL, timeout=25, max_clicks=1)
            yield from _ready(context, DETAIL)
            detail = read_god_seal_state()['detail']
            key = (row['point_type'], row['part'])
            first, count = detail['level'], 0
            while True:
                if (detail['point_type'], detail['part']) != key:
                    raise RuntimeError('神印点击落到错误部位，禁止升级')
                if detail['animating']:
                    yield from context.wait_action_settle(3)
                    detail = read_god_seal_state()['detail']
                    if detail['animating']:
                        raise RuntimeError('神印突破动画未结束')
                if detail['maximum'] or not detail['enough']:
                    break
                if count >= 500:
                    raise RuntimeError('神印单部位升级超过 500 次，保留现场')
                before = detail['level']
                yield from context.wait_click(DETAIL, '升级', timeout=10)
                yield from context.wait_action_settle(3)
                yield from _ready(context, DETAIL)
                detail = read_god_seal_state()['detail']
                if detail['level'] <= before:
                    raise RuntimeError('神印等级未增长，禁止重复提交')
                count += 1
            record = dict(name=row['name'], point_type=key[0], part=key[1],
                          level_before=first, level_after=detail['level'], actions=count)
            inspected.append(record)
            if count:
                changes.append(record)
            yield from context.wait_click_then_scene(DETAIL, '返回', MAIN, timeout=25, max_clicks=1)
        if index < maximum:
            yield from context.wait_click(MAIN, '下一组', timeout=10)
            yield from context.wait_action_settle(2)
            state = read_god_seal_state()
            if state['index'] != index+1:
                raise RuntimeError('神印向右切换未生效')
    yield from context.wait_click_then_scene(MAIN, '属性', 770, timeout=20, max_clicks=1)
    yield from context.go_scene(34)
    return {'result': 'success', 'outcome': 'complete', 'groups': maximum+1,
            'actions': sum(r['actions'] for r in changes), 'changes': changes,
            'inspected': inspected, 'final_scene': 34}
