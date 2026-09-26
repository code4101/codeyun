"""领取试炼手册：免费激活 → 普通奖励批领 → 底部循环奖励 → 世界。

每次整单重入都以游戏当前状态为准；不把点击次数、文案阴性或本地历史
作为完成凭证。未激活/循环箱正例待真实状态验收，未知页面保留现场。
"""
from backend.core.fanxiu.instrumentation.trial_manual import read_trial_manual_state
from backend.core.fanxiu.runtime_gui.trial_manual import plan_trial_reward_click
from backend.core.fanxiu.runtime_gui.activity_menu import ActivityMenuGrid
from .activity_menu_navigation import open_loaded_activity_menu_item

STAGE_ID = 'trial-manual-claim'
STAGE_VERSION = '1'
STAGE_LABEL = '领取试炼手册'
SCENE = 863


def _scene(match):
    return int(getattr(match, 'scene_id', getattr(match, 'id', match)))


def _ready(context):
    match = yield from context.wait_scene([581, SCENE], wait=15)
    if _scene(match) == 581:
        # Reuse the free-track result layer, gated by the manual-specific text.
        # This also recovers an interruption after activation already succeeded.
        yield from context.wait_shape(581, '手册激活确认', timeout=8)
        yield from context.wait_click_then_scene(581, '继续', SCENE, timeout=20)
        match = yield from context.wait_scene([SCENE], wait=10)
    if _scene(match) != SCENE:
        raise RuntimeError(f'试炼手册：未回到手册页 #{_scene(match)}，保留现场')


def _updated(context, before, predicate):
    """Wait for one submitted action; never click again after ambiguous results."""
    for _ in range(3):
        yield from context.wait_action_settle(1)
        yield from _ready(context)
        after = read_trial_manual_state()
        if (after['activity_id'], after['cycle']) != (before['activity_id'], before['cycle']):
            raise RuntimeError('试炼手册：操作期间活动换期，停止本次动作')
        if predicate(after):
            return after
    raise RuntimeError('试炼手册：动作后未取得状态变化，不重复提交')


def _enter(context):
    start = yield from context.wait_scene([581, SCENE, 403, 34], wait=10)
    scene = _scene(start)
    if scene == 581:
        yield from _ready(context)
        scene = SCENE
    if scene not in {403, SCENE}:
        yield from context.go_scene(34)
        yield from open_loaded_activity_menu_item(
            context, 110001, kind='world_left', source_scene_id=34,
            ocr_shape_names=('左侧菜单',), fallback_ocr_shape_names=('特惠文字观察',),
            expected_scene_ids=(403,), target_gui_name='特惠',
            grid=ActivityMenuGrid(columns=1, click_offset_heights=0.5))
        scene = 403
    if scene == 403:
        yield from open_loaded_activity_menu_item(
            context, '试炼手册', kind='group_popup', source_scene_id=403,
            ocr_shape_names=('特惠活动网格',), expected_scene_ids=(SCENE,))
    yield from _ready(context)


def claim_trial_manual(context):
    """领取当前可领的免费手册奖励；资源_每日处理的独立、可重入组件。

    业务约定：未激活先免费激活；点击左列任一可领普通奖励触发游戏批领；
    普通奖励清空后检查底部循环奖励。未达标是合法终态，无需等整本满级。
    不激活付费档；游戏批领若顺带发放已拥有的付费档奖励，属于原生行为。
    成功须有完整 Runtime 终态并返回 #34；聚合凭证由调用方持久化。

    验收边界（2026-09-26）：真实点击 5400 档，一并领取 5200/5400/5600；
    已领数从 25 到 28。随后三次整单重跑均零领取动作，其中两次从世界进入。
    未激活、激活确认层重入、底部循环箱可领、离屏后重定位及换期分支，
    依据客户端逻辑实现，尚未逐项真实验收；不能用离线测试替代这些证据。

    排查顺序与修复位置：
    1. 先切 AI 调度保留现场，查本次 Cell 日志和实际落点；调用方超时不代表
       Cell 已停止，先查 Kernel 状态，避免叠加动作。
    2. 进不去：检查 #34→#403→#863 的菜单 Runtime 身份、当前 OCR 和 Shape，
       修 activity_menu_navigation 或资产；入口位置会变，不固定菜单序号。
    3. 状态缺失/冲突：查 instrumentation.trial_manual.read_trial_manual_state；
       先核对 NewbattlepassMgr、完整奖励周期与已领记录，再查页面同步。
       不把异常改为空列表、未激活或已完成，也不只凭“已激活”OCR 阴性点按钮。
    4. 有待领奖励却找不到位置：查 runtime_gui.trial_manual 的分数对齐，
       对照 #863 奖励分数/普通奖励列 Shape；“当前”下面是总积分，不是档位。
    5. 激活后卡住：检查真实结果层是否适合继续复用 #581，及“免费手册已激活”
       和“继续”的 Shape；布局不同则保存真实帧新建场景并接回 #863。
    6. 底部不领或重复领：核对 box.claimable、claimed_count 与底部奖励 Shape。
       “10000 可领取”是门槛说明，不能据此认定当前可领。次数未增长时停止，
       不反复点；本轮未清空时也不能签发完成凭证。

    修复后从当前中间态整单重跑，再从 #34 重跑；确认已成功的副作用被跳过。
    只有完成判据改变才升级 STAGE_VERSION，避免无故废弃当日聚合完成凭证。
    """
    yield from _enter(context)
    state = read_trial_manual_state()
    actions = []
    if not state['activated']:
        yield from context.wait_click(SCENE, '免费激活', timeout=8)
        # Activation has a persistent confirmation overlay, unlike the normal
        # claim's transient reward bullets. Do not read through its background.
        match = yield from context.wait_scene([581], wait=20)
        if _scene(match) != 581:
            raise RuntimeError('试炼手册：免费激活结果层未确认，不重复激活')
        yield from _ready(context)
        state = yield from _updated(context, state, lambda after: after['activated'])
        actions.append('activate')

    reopened = False
    for _ in range(3):
        pending = [r for r in state['rewards'] if r['claimable']]
        if not pending:
            break
        frame = context.cur_frame(update=True)
        tokens = context.ocr_tokens_in_shapes(SCENE, ['奖励分数'], frame_data_url=frame)
        plan = plan_trial_reward_click(state['rewards'], tokens, context.shape_box(SCENE, '普通奖励列'))
        if plan is None:
            # Reopening invokes the game's own scroll-to-first-pending logic.
            # Do not guess a row or mark an offscreen reward as completed.
            if reopened:
                raise RuntimeError('试炼手册：可领取奖励未与当前行唯一对齐，保留现场')
            yield from context.wait_click_then_scene(SCENE, '返回', 34, timeout=20)
            yield from _enter(context)
            fresh = read_trial_manual_state()
            if (fresh['activity_id'], fresh['cycle']) != (state['activity_id'], state['cycle']):
                raise RuntimeError('试炼手册：重新定位期间活动换期')
            state = fresh
            reopened = True
            continue
        context.click_frame_point(SCENE, *plan['point'])
        pending_ids = {r['reward_id'] for r in pending}
        # 确认本轮待领 ID 进入已领集合后才允许下一轮；动画/点击返回不算成功。
        state = yield from _updated(context, state,
            lambda after: bool(pending_ids & set(after['claimed_ids'])))
        actions.append('claim_normal')
    if any(r['claimable'] for r in state['rewards']):
        raise RuntimeError('试炼手册：普通奖励尚未收敛')
    if state['box']['claimable']:
        # 固定底部位置会随进度显示不同奖励；只有 Runtime 循环箱可领才点。
        # 原生 SendBoxGet 批量领完当前次数，故完成后仍可领应报错调查。
        count = state['box']['claimed_count']
        yield from context.wait_click(SCENE, '底部奖励', timeout=8)
        state = yield from _updated(context, state,
            lambda after: after['box']['claimed_count'] > count)
        actions.append('claim_box')
    if not state['activated'] or state['box']['claimable'] or any(r['claimable'] for r in state['rewards']):
        raise RuntimeError('试炼手册：还有可领取奖励，不能签发完成凭证')
    yield from context.wait_click_then_scene(SCENE, '返回', 34, timeout=20)
    return {'result': 'success', 'outcome': 'complete', 'actions': actions,
            'activity_id': state['activity_id'], 'claimed_ids': state['claimed_ids'],
            'box': state['box'], 'final_scene': 34}
