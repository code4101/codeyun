"""资源每日处理中的普通灵根领悟；异灵根明确留待后续研发。"""
from backend.core.fanxiu.instrumentation.spirit_root import read_spirit_root_state

STAGE_ID = 'spirit-root-update'
STAGE_VERSION = '1'
STAGE_LABEL = '灵根领悟'
ROOT = 865
RESULT = 866


def _ready(context):
    for _ in range(5):
        match = yield from context.wait_scene([RESULT, ROOT, 689, 578], wait=5, required=False)
        scene = match.scene_id if match else None
        if scene == ROOT:
            return
        if scene == RESULT:
            yield from context.wait_click(RESULT, '继续', timeout=10)
        elif scene == 689:
            yield from context.wait_click(689, '返回', timeout=10)
        elif scene != 578:
            raise RuntimeError(f'灵根领悟：未知结果场景 #{scene}，保留现场')
        yield from context.wait_action_settle(1)
    raise RuntimeError('灵根领悟：结果页未收敛')


def update_spirit_root(context):
    """#34→角色 #770→普通灵根 #865→领悟→结果 #866→继续→复查。

    只操作中间普通灵根；不点击五行图标和异灵根，不购买或跳转材料获取。
    以 LingGen_6 的已计算叶子判定可升级；材料不足、解锁门槛未满足或满级
    都是普通灵根本轮终态，异灵根角标不影响完成凭证。
    一键领悟沿用用户当前设置：已开启时一次消耗本轮材料，未开启则逐次
    检查服务端等级。一次点击后等待结果并核验等级增加，禁止无变化盲重试。

    真实案例（2026-09-26）：一键领悟消耗 226，七重 124→147 层；点击后
    先出现材料为 0 的 #865 中间帧，延迟才出现 #866。因此必须先等待结果页
    并点击继续，再判断材料不足；不能见 0 就返回，否则会把结果弹窗遗留给
    下个组件。中断在结果页时新 attempt 先关闭结果，再重新读取当前事实。
    当前正例手动已验收；封装后以材料不足状态验证入口、终态及整单幂等。
    跨重/突破、未开启一键的连续点击尚待真实样本；不伪造材料来测试。

    维修入口：状态与门槛查 instrumentation.spirit_root；入口与结果延迟查
    #770/865/866 Shape。新结果布局先考虑复用 #866，再新增场景 ID。
    """
    start = yield from context.wait_scene([RESULT, ROOT, 770, 34], wait=4, required=False)
    scene = start.scene_id if start else None
    if scene not in (ROOT, RESULT):
        if scene != 770:
            yield from context.go_scene(34)
            yield from context.wait_click_then_scene(34, '角色', 770, timeout=20)
        yield from context.wait_click_then_scene(770, '灵根', ROOT, timeout=25)
    yield from _ready(context)
    state = read_spirit_root_state()
    first_level = state['level']
    actions = 0
    while state['can_upgrade']:
        if actions >= 500:
            raise RuntimeError('灵根领悟：本轮超过 500 次，保留现场检查一键设置')
        before = state
        yield from context.wait_click(ROOT, '领悟', timeout=10)
        # 成功动画的底层页面会先更新，不能把它当作结果弹窗已关闭。
        if before['one_key']:
            yield from context.wait_scene_exact([RESULT], timeout=35, label='灵根领悟：等待升级结果')
        else:
            # 客户端只有 oneKey 分支创建汇总结果页，单次领悟直接更新主面板。
            yield from context.wait_action_settle(2)
        yield from _ready(context)
        state = read_spirit_root_state()
        if state['level'] <= before['level']:
            raise RuntimeError('灵根领悟：服务端等级未增长，禁止重复提交')
        actions += 1
    # 回属性再回世界，使用已验证入口，不把底部返回的不同落点当固定事实。
    yield from context.wait_click_then_scene(ROOT, '属性', 770, timeout=20, max_clicks=1)
    yield from context.go_scene(34)
    return {'result': 'success', 'outcome': 'complete', 'actions': actions,
            'level_before': first_level, 'level_after': state['level'],
            'stage': state['stage'], 'enough': state['enough'], 'final_scene': 34,
            'skipped_features': ['异灵根', '五行灵根']}
