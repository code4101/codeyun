"""法身提升是养成集的内部组件；无独立 Job、采购或材料获取动作。"""
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.core.fanxiu.instrumentation.field_transform import (
    read_field_transform_state, read_field_transform_selection,
)
from backend.core.fanxiu.runtime_gui.field_transform import field_transform_model_points

STAGE_ID = 'field-transform-upgrade'
STAGE_VERSION = '1'
STAGE_LABEL = '法身提升'
OVERVIEW, DETAIL, RESULT = 914, 915, 916
# 真机配置 cleanseItem 和详情材料对应的法身专用道具。新增消耗必须先验收。
DEDICATED_ITEMS = {4001: 391100001, 5001: 391100002, 5002: 391100003,
                   6001: 391100004, 7001: 391100006}


def actionable_rows(rows, counts):
    """完整目录中只接受免费提升或已验收的专用材料；未知配置失败关闭。"""
    pending = []
    for row in rows:
        if row['maximum']:
            continue
        if row['cost_item_id'] != DEDICATED_ITEMS.get(row['transform_id']) or row['cost_count'] <= 0:
            raise RuntimeError(f"法身 {row['transform_id']} 消耗未经确认，保留现场")
        if row['free'] or counts[row['cost_item_id']] >= row['cost_count']:
            pending.append(row)
    return pending


def _overview(context):
    """中断重入只清理结果/详情，再基于当前服务器事实决定新动作。"""
    match = yield from context.wait_scene([RESULT, DETAIL, OVERVIEW, 812, 20, 34], wait=5, required=False)
    scene = match.scene_id if match else None
    if scene == RESULT:
        yield from context.wait_click_then_scene(RESULT, '点击屏幕继续', DETAIL, timeout=30, max_clicks=1)
        scene = DETAIL
    if scene == DETAIL:
        yield from context.wait_click_then_scene(DETAIL, '返回', OVERVIEW, timeout=20, max_clicks=1)
        scene = OVERVIEW
    if scene == OVERVIEW:
        return
    if scene not in (812, 20):
        yield from context.go_scene(34)
        yield from context.go_scene(20, known_paths_only=True)
        scene = 20
    if scene == 20:
        yield from context.wait_click_ocr_text(20, '领域', in_shapes=['菜单'], match_mode='exact',
            crop_fallback=True, timeout_seconds=120, max_scrolls_per_direction=12)
        yield from context.wait_scene([812], wait=20)
    yield from context.wait_click_then_scene(812, '法身', OVERVIEW, timeout=25, max_clicks=1)


def _select_pending(context, pending):
    """模型列表只有查看动作；误选详情立即返回，身份吻合才允许业务提交。

    先用真机验收的默认自动定位，再按资产容器有界扫描。列表可同时有多个
    材料可用项及衣装免费项，因此不能把默认当前法身等同于本轮唯一目标。
    """
    ids = {r['transform_id'] for r in pending}
    levels = {r['level'] for r in pending}
    viewport = context.shape(OVERVIEW, '法身列表').box()
    left = context.shape(OVERVIEW, '左法身横向定位').box()
    right = context.shape(OVERVIEW, '右法身横向定位').box()
    gap = context.shape(OVERVIEW, '等级至模型间距').box()['h']
    for direction in ('down', 'up'):
        for _ in range(12):
            yield from context.wait_scene([OVERVIEW], wait=15)
            points = field_transform_model_points(
                context.full_frame_ocr_tokens(context.cur_frame(update=True)), levels=levels,
                viewport=viewport, left=left, right=right, gap=gap)
            for x, y in points:
                context.click_frame_point(OVERVIEW, x, y)
                yield from context.wait_action_settle(1.5)
                match = yield from context.wait_scene([DETAIL, OVERVIEW], wait=5)
                if match.scene_id == DETAIL:
                    selected = read_field_transform_selection()
                    if selected in ids:
                        return selected
                    yield from context.wait_click_then_scene(DETAIL, '返回', OVERVIEW, timeout=20, max_clicks=1)
            changed = yield from context.scroll_shape_content(OVERVIEW, '法身列表', direction=direction)
            if not changed:
                break
    raise RuntimeError('法身可提升目标未在有界列表中定位，保留现场，不写完成凭证')


def upgrade_field_transforms(context):
    """#34→#20 领域→#812 法身→#914→#915→#916→关闭→复核→#34。

    整个目录达到满级或免费/专用资源不足才完成。每次提交后等待结果页并
    复核服务器等级恰好 +1；无结果或无进展禁止重发。材料初始读一次，确认
    成功后按已验证消耗扣减，下一 attempt 重读；不把缓存余额当持久事实。
    2026-10-03 真机：乾罡巨灵 11→12，消耗专用道具 391100001 一份；结果
    展示后关闭回详情、总览、领域和世界，其他四个法身没有可提升资源。
    """
    yield from _overview(context)
    state = read_field_transform_state(page='overview')
    counts, _ = read_backpack_item_counts(list(DEDICATED_ITEMS.values()), manager_key='field-transform')
    counts = dict(counts)
    initial = {r['transform_id']: r['level'] for r in state['rows']}
    actions = 0
    while True:
        pending = actionable_rows(state['rows'], counts)
        if not pending:
            break
        if actions >= 500:
            raise RuntimeError('法身本轮超过 500 次，保留现场')
        target = yield from _select_pending(context, pending)
        before = next(r for r in pending if r['transform_id'] == target)
        detail = read_field_transform_state(page='detail')['detail']
        if detail is None or any(detail[k] != before[k] for k in
                ('transform_id', 'level', 'maximum', 'free', 'cost_item_id', 'cost_count')):
            raise RuntimeError('法身详情与新鲜目录身份/消耗不一致，禁止点击提升')
        yield from context.wait_click(DETAIL, '提升', timeout=10)
        yield from context.wait_scene_exact([RESULT], timeout=45, label='法身提升：等待结果')
        yield from context.wait_click_then_scene(RESULT, '点击屏幕继续', DETAIL, timeout=30, max_clicks=1)
        after = read_field_transform_state(page='detail')
        actual = next(r for r in after['rows'] if r['transform_id'] == target)
        if after['detail'] is None or after['detail']['transform_id'] != target or actual['level'] != before['level'] + 1:
            raise RuntimeError('法身服务器等级未恰好增长一阶，禁止重复提交')
        if not before['free']:
            counts[before['cost_item_id']] -= before['cost_count']
        actions += 1
        state = after
        yield from context.wait_click_then_scene(DETAIL, '返回', OVERVIEW, timeout=20, max_clicks=1)
    yield from context.wait_click_then_scene(OVERVIEW, '返回', 812, timeout=20, max_clicks=1)
    yield from context.go_scene(34)
    return dict(result='success', outcome='complete', actions=actions, final_scene=34,
                levels_before=initial, levels_after={r['transform_id']: r['level'] for r in state['rows']},
                remaining_dedicated_items=counts)
