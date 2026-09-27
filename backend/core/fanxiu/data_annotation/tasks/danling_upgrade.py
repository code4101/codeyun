"""丹灵升级：遍历客户端开放目录，各自升阶/激活，再升级至原生门槛。

由资源_每日处理聚合，每日完成凭证只在完整遍历后提交。材料不足、满级、
阶数未达升级门槛都是正常终态；未知页面、目标错位或无进展必须保留现场。
"""
from backend.core.fanxiu.instrumentation.danling import (
    read_danling_state, read_danling_result_window, read_danling_bloodline,
)

STAGE_ID = 'danling-upgrade'
STAGE_VERSION = '1'
MAIN, STAGE, LEVEL, STAGE_RESULT = 878, 879, 880, 881
BLOOD = 882


def _wait(context, scenes, *, sources=()):
    return (yield from context.wait_scene_exact(
        scenes, timeout=25, observation_scenes=sources, label='丹灵升级：等待页面',
    ))


def _open(context):
    """从当前稳定业务边界进入；横向菜单必须按实时文字定位。"""
    window = read_danling_result_window()
    if window == 'MedicalelfActivateSuitView':
        yield from context.wait_shape(STAGE_RESULT, '继续', timeout=15)
        context.click_shape_center(STAGE_RESULT, '继续')
        yield from _wait(context, [BLOOD])
    elif window:
        if window == 'MedicalelfActiveView':
            context.click_shape_center(STAGE_RESULT, '返回背景')
        else:
            yield from context.wait_shape(STAGE_RESULT, '继续', timeout=15)
            context.click_shape_center(STAGE_RESULT, '继续')
        yield from _wait(context, [STAGE, LEVEL], sources=[STAGE_RESULT])
        return
    scene = yield from context.wait_scene([STAGE, LEVEL, MAIN, STAGE_RESULT, BLOOD, 624, 20], wait=5)
    scene = scene.scene_id
    if scene == BLOOD:
        if read_danling_bloodline()['popup']:
            context.click_shape_center(BLOOD, '关闭节点')
            yield from context.wait_action_settle(1)
        context.click_shape_center(BLOOD, '丹灵')
        yield from _wait(context, [MAIN], sources=[BLOOD])
        scene = MAIN
    if scene == STAGE_RESULT:
        context.click_shape_center(STAGE_RESULT, '继续')
        yield from _wait(context, [STAGE, LEVEL], sources=[STAGE_RESULT])
        return
    if scene in (STAGE, LEVEL):
        return
    if scene != MAIN:
        if scene != 624:
            yield from context.go_scene(20)
            yield from _wait(context, [20])
            found = False
            for direction in ('right', 'left'):
                for _ in range(12):
                    match = context.find_ocr_text(20, '炼丹', in_shapes=['菜单'], crop=True)
                    if match:
                        context.click_frame_point(20, *match.point())
                        found = True
                        break
                    context.drag_shape_content(20, '菜单', direction=direction)
                    yield from context.wait_action_settle(2)
                    yield from _wait(context, [20])
                if found:
                    break
            if not found:
                raise RuntimeError('丹灵升级：绿瓶菜单未找到炼丹入口')
            yield from _wait(context, [624], sources=[20])
        context.click_shape_center(624, '丹灵')
        yield from _wait(context, [MAIN], sources=[624])
    # 原生首个卡片用于打开详情，随后遍历 ShowVoList 全部开放丹灵。
    context.click_shape_center(MAIN, '首个丹灵')
    yield from _wait(context, [STAGE], sources=[MAIN])


def _settled_state(context):
    for _ in range(20):
        state = read_danling_state()
        if not state.get('animating'):
            return state
        yield from context.wait_action_settle(.5)
    raise RuntimeError('丹灵升级：升级动画未结束，保留现场')


def upgrade_danling(context):
    """完成全部已开放丹灵的现有材料培养，返回世界页和逐项结果。

每次只提交一个原生按钮动作，服务器 stage 或 (level, exp) 增长后才
继续。不会点击获取材料/购买，也不会把“前往升阶”当升级按钮反复点。
新调用重新读取目录和当前等级，旧 Cell 的循环游标不参与恢复。
"""
    yield from _open(context)
    state = yield from _settled_state(context)
    ids = [r['id'] for r in state['spirits']]
    while state['index'] > 0:
        scene = STAGE if state['tab'] == 0 else LEVEL
        previous = state['index']
        context.click_shape_center(scene, '上一个')
        yield from context.wait_action_settle(1)
        state = yield from _settled_state(context)
        if state['index'] != previous-1:
            raise RuntimeError('丹灵升级：向前切换未生效')
    inspected = []
    for index, ident in enumerate(ids):
        if state['current']['id'] != ident or state['index'] != index:
            raise RuntimeError('丹灵升级：丹灵目录与当前身份不一致')
        before = dict(state['current'])
        actions = 0
        stops = {}
        for tab, scene in ((0, STAGE), (1, LEVEL)):
            if state['tab'] != tab:
                source = STAGE if state['tab'] == 0 else LEVEL
                context.click_shape_center(source, '升阶页签' if tab == 0 else '升级页签')
                yield from _wait(context, [scene], sources=[source])
                state = yield from _settled_state(context)
            for _ in range(500):
                if state['current']['id'] != ident or state['tab'] != tab:
                    raise RuntimeError('丹灵升级：动作目标错位')
                if not state['actionable']:
                    stops['stage' if tab == 0 else 'level'] = {
                        'maximum': state['maximum'], 'owned': state['owned'], 'cost': state['cost'],
                        'reason': '满级' if state['maximum'] else '材料不足或解锁门槛',
                    }
                    break
                _require_material(state)
                old = dict(state['current'])
                context.click_shape_center(scene, '升阶操作' if tab == 0 else '升级操作')
                yield from context.wait_action_settle(3)
                yield from _finish_action(context, tab, old)
                state = yield from _settled_state(context)
                changed = (state['current']['stage'] > old['stage'] if tab == 0 else
                           (state['current']['level'], state['current']['exp']) > (old['level'], old['exp']))
                if state['current']['id'] != ident or not changed:
                    raise RuntimeError('丹灵升级：服务器数据未增长，禁止重复提交')
                actions += 1
            else:
                raise RuntimeError('丹灵升级：单个页签超过 500 次动作，保留现场')
        inspected.append(dict(before=before, after=dict(state['current']), actions=actions, stops=stops))
        if index+1 < len(ids):
            context.click_shape_center(LEVEL, '下一个')
            yield from context.wait_action_settle(1)
            state = yield from _settled_state(context)
            if [r['id'] for r in state['spirits']] != ids:
                raise RuntimeError('丹灵升级：目录在执行中发生变化')
    context.click_shape_center(LEVEL, '返回')
    yield from _wait(context, [MAIN], sources=[LEVEL])
    bloodline = yield from _upgrade_bloodline(context)
    context.click_shape_center(MAIN, '返回')
    yield from _wait(context, [624], sources=[MAIN])
    yield from context.go_scene(34)
    yield from _wait(context, [34])
    return dict(result='success', outcome='complete', inspected=inspected, bloodline=bloodline,
                actions=sum(row['actions'] for row in inspected) + sum(row['actions'] for row in bloodline),
                final_scene=34)


def _upgrade_bloodline(context):
    """品质血脉为免费激活；每次以服务端 effects 增长验收，门槛不足即结束。"""
    context.click_shape_center(MAIN, '血脉')
    yield from _wait(context, [BLOOD], sources=[MAIN])
    state = read_danling_bloodline()
    if state['tabs'] != ['绝品', '仙品', '神品']:
        raise RuntimeError('丹灵血脉：品质目录变化，需补充页面适配')
    inspected = []
    for index, name in enumerate(state['tabs']):
        if state['tab'] != index:
            if state['popup']:
                context.click_shape_center(BLOOD, '关闭节点')
                yield from context.wait_action_settle(1)
            context.click_shape_center(BLOOD, name)
            yield from context.wait_action_settle(2)
            state = read_danling_bloodline()
        if state['tab'] != index:
            raise RuntimeError(f'丹灵血脉：{name}页签未就绪')
        before = state['active_id']
        actions = 0
        for _ in range(500):
            if not state['actionable']:
                break
            target = state['id']
            if state['popup_id'] != target:
                if state['popup']:
                    context.click_shape_center(BLOOD, '关闭节点')
                    yield from context.wait_action_settle(1)
                yield from open_danling_bloodline_node(context, state)
                state = read_danling_bloodline()
                if state['popup_id'] != target or not state['actionable']:
                    raise RuntimeError('丹灵血脉：打开的节点与待激活目标不符')
            context.click_shape_center(BLOOD, '激活')
            yield from context.wait_action_settle(3)
            if read_danling_result_window() != 'MedicalelfActivateSuitView':
                raise RuntimeError('丹灵血脉：未取得激活结果，保留现场')
            yield from context.wait_shape(STAGE_RESULT, '继续', timeout=15)
            context.click_shape_center(STAGE_RESULT, '继续')
            yield from _wait(context, [BLOOD], sources=[STAGE_RESULT])
            state = read_danling_bloodline()
            if state['tab'] != index or state['active_id'] != target:
                raise RuntimeError('丹灵血脉：服务端激活节点不符，禁止重复提交')
            actions += 1
        else:
            raise RuntimeError('丹灵血脉：超过 500 次激活')
        inspected.append(dict(name=name, before=before, after=state['active_id'], actions=actions,
                              next_condition=state['condition'], totals=state['totals']))
    if state['popup']:
        context.click_shape_center(BLOOD, '关闭节点')
        yield from context.wait_action_settle(1)
    context.click_shape_center(BLOOD, '丹灵')
    yield from _wait(context, [MAIN], sources=[BLOOD])
    return inspected


def open_danling_bloodline_node(context, state):
    """节点文字来自原生 pointName；图标位于该标签正上方，打开后复核 ID。"""
    match = context.find_ocr_text(BLOOD, state['node_name'],
                                  in_shapes=['节点列表'], crop=True)
    if not match:
        raise RuntimeError('丹灵血脉：下一节点未在当前可见列表中，保留现场')
    x, y = match.point()
    # 900×1600 参考画面中，小/大节点图标到文字的间距分别为 60/105。
    context.click_frame_point(BLOOD, x, y - (105 if state['big_point'] else 60))
    yield from context.wait_action_settle(2)


def _require_material(state):
    """培养只消费丹灵专用材料；未知消耗不随资源授权扩大到代币。"""
    from backend.core.fanxiu.instrumentation.item_config import read_item_metadata_runtime
    item = state['cost_item_id']
    if item is None:
        # 原生升阶配置的空 consume 代表免费激活/升阶。
        if state['tab'] == 0 and state['owned'] is None and state['cost'] is None:
            return
        raise RuntimeError('丹灵升级：消耗身份缺失')
    metadata = read_item_metadata_runtime([item])
    row = metadata.get('items_by_id', {}).get(item)
    kind = (row.get('item_type_id'), row.get('item_sub_type_id')) if row else None
    allowed = (kind is not None and
               ((state['tab'] == 0 and (kind[0] == 22 or kind == (1, 102))) or
                (state['tab'] == 1 and kind[0] == 103)))
    if not allowed:
        raise RuntimeError(f'丹灵升级：未确认的培养材料 {item}')


def _finish_action(context, tab, old):
    scene = STAGE if tab == 0 else LEVEL
    # Material can add partial experience without opening a level-up overlay.
    # A native result Window is also the ready adapter for shared reward art.
    for _ in range(12):
        window = read_danling_result_window()
        if window:
            expected = ('MedicalelfActiveView' if old['stage'] == 0 else
                        'MedicalelfActiveNewTipsView') if tab == 0 else 'MedicalelfUpLevelView'
            if window != expected:
                raise RuntimeError(f'丹灵升级：结果窗口不符 {window}')
            if window == 'MedicalelfActiveView':
                # Native Root closes this whole activation overlay. This branch
                # requires a future first-activation sample for visual acceptance.
                context.click_shape_center(STAGE_RESULT, '返回背景')
            else:
                yield from context.wait_shape(STAGE_RESULT, '继续', timeout=15)
                context.click_shape_center(STAGE_RESULT, '继续')
            yield from _wait(context, [scene], sources=[STAGE_RESULT])
            return
        state = read_danling_state()
        if state['current']['id'] != old['id']:
            raise RuntimeError('丹灵升级：等待结果时目标变化')
        # A level increase must display its result before the next action;
        # an exp-only increment remains on the same page.
        if tab == 1 and state['current']['level'] == old['level'] and state['current']['exp'] > old['exp']:
            yield from _wait(context, [LEVEL])
            return
        yield from context.wait_action_settle(.5)
    raise RuntimeError('丹灵升级：未取得培养结果，保留现场')
