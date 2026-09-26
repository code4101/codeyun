"""成长基金领取：不新购，按本周祈愿保护资源，到账后才推进。"""
from backend.core.fanxiu.instrumentation.growth_fund import read_growth_fund_state
from backend.core.fanxiu.prayer_cycle import current_prayer_cycle, prayer_cycle_week_start
from backend.core.fanxiu.runtime_gui.activity_menu import ActivityMenuGrid
from backend.core.fanxiu.runtime_gui.text import normalize_ocr_name
from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens
from .activity_menu_navigation import open_loaded_activity_menu_item
from .growth_fund_policy import plan_growth_funds

STAGE_ID = 'growth-fund-claim'
STAGE_VERSION = '1'
STAGE_LABEL = '成长基金领取'
SCENE = 864


def _ready(context):
    """复用奖励守卫；#578 等待消失，#689 关闭通知，不点击前往讨伐。"""
    for _ in range(5):
        match = yield from context.wait_scene([689, SCENE, 578], wait=3, required=False)
        scene = match.scene_id if match else None
        if scene == SCENE:
            return
        if scene == 689:
            yield from context.wait_click(689, '返回', timeout=8)
            yield from context.wait_action_settle(1)
            continue
        if scene == 578:
            # 原生领取飘屏会自行消失，不能拿已过期结果帧的坐标点击底层。
            yield from context.wait_action_settle(1)
            continue
        raise RuntimeError(f'成长基金：待补充弹窗或场景 #{scene}，保留现场')
    raise RuntimeError('成长基金：弹窗未收敛')


def _enter(context):
    match = yield from context.wait_scene([689, SCENE, 578, 403, 34], wait=3, required=False)
    scene = match.scene_id if match else None
    if scene in (689, SCENE, 578):
        yield from _ready(context)
        return
    if scene != 403:
        yield from context.go_scene(34)
        yield from open_loaded_activity_menu_item(context, 110001,
            kind='world_left', source_scene_id=34, ocr_shape_names=('左侧菜单',),
            fallback_ocr_shape_names=('特惠文字观察',), expected_scene_ids=(403,),
            target_gui_name='特惠', grid=ActivityMenuGrid(columns=1, click_offset_heights=.5))
    yield from open_loaded_activity_menu_item(context, '成长基金', kind='group_popup',
        source_scene_id=403, ocr_shape_names=('特惠活动网格',), expected_scene_ids=(SCENE,))
    yield from _ready(context)


def _select_text(context, name, shape, *, direction):
    """只在正式菜单 Shape 内寻找 Runtime 名称；滚动后重新 OCR，禁用固定序号。"""
    previous = None
    for _ in range(15):
        yield from _ready(context)
        frame = context.cur_frame(update=True)
        lines = group_ocr_tokens(context.ocr_tokens_in_shapes(SCENE, [shape], frame_data_url=frame))
        target = normalize_ocr_name(name)
        candidates = [r for r in lines if normalize_ocr_name(r['text']) == target]
        if len(candidates) > 1:
            raise RuntimeError(f'成长基金菜单不唯一：{name}')
        if candidates:
            row = candidates[0]
            context.click_frame_point(SCENE, row['x'] + row['w']/2, row['y'] + row['h']/2)
            yield from context.wait_action_settle(1)
            yield from _ready(context)
            return
        signature = tuple(sorted(normalize_ocr_name(r['text']) for r in lines))
        if previous == signature:
            break
        previous = signature
        yield from context.scroll_shape_content(SCENE, shape, direction=direction)
    raise RuntimeError(f'成长基金菜单未定位：{name}')


def _select(context, state, fund):
    if state['selected_type'] != fund['type']:
        categories = state['categories']
        positions = {r['id']: i for i, r in enumerate(categories)}
        name = next(r['name'] for r in categories if r['id'] == fund['type'])
        # 菜单滚动位置可独立于选中项；先按目标方向找，走到边界再反向。
        direction = 'right' if positions[fund['type']] > positions[state['selected_type']] else 'left'
        try:
            yield from _select_text(context, name, '基金分类', direction=direction)
        except RuntimeError as exc:
            if '菜单未定位' not in str(exc):
                raise
            yield from _select_text(context, name, '基金分类', direction='left' if direction == 'right' else 'right')
        state = read_growth_fund_state()
        if state['selected_type'] != fund['type']:
            raise RuntimeError('成长基金：分类点击后 Runtime 身份不符')
    if state['selected_fund'] != fund['fundId']:
        selected = next(r for r in state['funds'] if r['fundId'] == state['selected_fund'])
        direction = 'right' if fund['sort'] > selected['sort'] else 'left'
        yield from _select_text(context, fund['name'], '基金档位', direction=direction)
        state = read_growth_fund_state()
    if state['selected_fund'] != fund['fundId']:
        raise RuntimeError('成长基金：档位点击后 Runtime 身份不符')
    return state


def claim_growth_fund(context, *, moment=None):
    """领取所有当前可领且策略允许的档位，成功返回世界 #34。

    调度：资源_每日处理按冻结业务时间每周一调用；函数本身支持显式研发验收。
    全目录由 Runtime 遍历，已领/未达标档无需逐页空点；待领分类和档位仍按
    游戏从左到右顺序定位。当前配置每档资源同类；热更出现混合祈愿档时
    明确停止并研发单项领取，不能为了清空普通资源而批领受保护附件。
    每次重入重读服务器 bought/claimed，不把本地历史或红点消失当到账证明。
    只点击领奖按钮，永不点击付费激活。未购买的右列不在计划内；已购右列
    与免费左列同样检查祈愿资源。宝匣依据已加载道具描述识别内含祈愿材料。

    维修顺序：进不去查 #34→#403 菜单对齐；换分类/档位失败查 #864 两个
    滚动 Shape 和 Runtime 身份；领取未到账查 FundMgr.FoundationInfo 两列
    ID 集合。继续领取在无可领且未购买时会弹购买推荐，故必须在点击前再次
    验证 Foundation_Reward 叶子为真。出现推荐页应补取消 Shape，不可购买。
    奖励通用弹窗由场景守卫处理；讨伐通知复用 #689 返回。弹窗清理后重读
    已领记录，禁止只因返回主页就签发成功。新布局先考虑复用 #864，再新建。

    2026-09-26 实机：6 档新增 21 个免费奖励 ID，购买集合保持原 10 档；
    5 个非洗灵周档位留存。#578 过场、#689 通知均已处理；从 #34 整单重跑
    actions=[] 并回 #34。已购付费列本次无可领正例；混合档策略仅有纯算法
    测试，若未来出现真实混合配置按上述单项领取边界继续研发。
    """
    prayer = current_prayer_cycle(moment)
    yield from _enter(context)
    state = read_growth_fund_state()
    bought = state['bought']
    actions = []
    for _ in range(len(state['funds']) + 1):
        if current_prayer_cycle() != prayer:
            raise RuntimeError('成长基金：计划祈愿周与当前周不一致，停止领取')
        plan = plan_growth_funds(state, prayer)
        if plan['mixed']:
            raise RuntimeError('成长基金：发现混合祈愿档，需逐项领取，禁止批领')
        if not plan['claim']:
            break
        fund = plan['claim'][0]
        state = yield from _select(context, state, fund)
        fresh_plan = plan_growth_funds(state, prayer)
        if fund['fundId'] not in {r['fundId'] for r in fresh_plan['claim']}:
            raise RuntimeError('成长基金：动作前可领状态变化')
        before = state
        yield from context.wait_click(SCENE, '继续领取', timeout=8)
        for retry in range(3):
            yield from context.wait_action_settle(1)
            yield from _ready(context)
            state = read_growth_fund_state()
            if state['bought'] != bought:
                raise RuntimeError('成长基金：购买记录意外变化')
            if state['selected_fund'] != fund['fundId'] or any(
                    not set(before['claimed_' + track]) <= set(state['claimed_' + track])
                    for track in ('free', 'paid')):
                raise RuntimeError('成长基金：操作期间档位或服务器记录身份变化')
            deltas = {track: sorted(set(state['claimed_' + track]) - set(before['claimed_' + track]))
                      for track in ('free', 'paid')}
            if any(deltas.values()):
                allowed = {(d['track'], d['reward_id']) for d in fund['decisions'] if d['allowed']}
                if any((track, ident) not in allowed for track, values in deltas.items() for ident in values):
                    raise RuntimeError('成长基金：到账超出授权奖励集合')
                actions.append({'fund_id': fund['fundId'], **deltas})
                break
        else:
            raise RuntimeError('成长基金：点击后无到账证据，禁止重复提交')
    else:
        raise RuntimeError('成长基金：领取循环未收敛')
    plan = plan_growth_funds(state, prayer)
    if plan['claim'] or plan['mixed']:
        raise RuntimeError('成长基金：仍有本周待领奖励')
    yield from context.wait_click_then_scene(SCENE, '返回', 34, timeout=20, max_clicks=1)
    return {'result': 'success', 'outcome': 'complete', 'prayer': prayer,
            'week': prayer_cycle_week_start(moment).date().isoformat(), 'actions': actions,
            'deferred_funds': [r['fundId'] for r in plan['deferred']],
            'claimed_free': state['claimed_free'], 'claimed_paid': state['claimed_paid'],
            'bought': state['bought'], 'final_scene': 34}
