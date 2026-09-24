"""镇物详情组件：固定祭炼；换装后普通重铸取得更高评分即结束。

由活动_每日清单同步聚合。点击只使用固定 Shape；祭炼以画面分子/
分母为准，不检查红点或 Runtime。未知场景、OCR 不确定或无进展保留现场。
"""
import re
from decimal import Decimal


def _scene(context, scenes):
    match = yield from context.wait_scene(scenes, wait=15)
    if match is None or match.scene_id not in scenes:
        raise RuntimeError(f"镇物预期 {scenes}，实际 {match}")
    return match.scene_id


def _text(context, scene, shape, *, crop=True):
    rows = context.ocr_fragments_in_shapes(
        scene, [shape], padding=0, crop=crop,
        frame_data_url=context.cur_frame(update=True),
    )
    return ''.join(r.get('text', '') for r in rows).replace(' ', '')


def _quantity(context, scene, shape):
    # 祭炼结果会先落回 #817，再渲染材料数字。只对读数不完整重取新帧；
    # 连续读不到合法分子/分母时仍失败关闭，避免凭旧数量再次点击。
    raw = ''
    for attempt in range(4):
        # 真机 #817 的小数字在局部裁剪 OCR 中为空；全帧 OCR 后按 Shape
        # 空间筛选能稳定读到「89/11」，仍只接受完整分子/分母。
        for crop in (False, True):
            raw = _text(context, scene, shape, crop=crop)
            match = re.fullmatch(r'(\d+)/(\d+)', raw)
            if match and int(match[2]) > 0:
                return int(match[1]), int(match[2])
        if attempt < 3:
            yield from context.wait_action_settle(0.7)
    raise RuntimeError(f"镇物材料读数不确定：{raw!r}")


def _score(context, scene, shape):
    raw = _text(context, scene, shape)
    match = re.fullmatch(r'重铸评分[:：](\d+)', raw)
    if not match:
        raise RuntimeError(f"镇物评分读数不确定：{raw!r}")
    return int(match[1])


def finish_domain_equipment(context, *, equipped: bool, recast_required=False,
                            cast_soul=False, log=lambda text: None):
    """从详情/祭炼/重铸页闭环当前镇物并返回 #814。

    equipped 仅由本次真实装配动作决定，不能从历史步骤推测；重入由
    总览当前重铸提示决定 recast_required。重铸最多 20 次，祭炼最多
    500 次。已有候选时先按当前/最新评分裁决，再继续有界重铸。
    """
    scene = yield from _scene(context, [815, 817, 818, 819])
    pending_recast = scene == 819
    if scene not in (817, 819):
        context.click_shape_center(scene, '祭炼' if scene == 815 else '祭炼页签')
        yield from _scene(context, [817])
    previous = None
    clicks = 0
    for _ in range(0 if pending_recast else 501):
        have, need = yield from _quantity(context, 817, '材料数量')
        if have < need:
            log(f'祭炼完成：{have}/{need}，点击 {clicks} 次')
            break
        if previous is not None and have >= previous:
            raise RuntimeError('祭炼材料未减少，保留现场')
        if clicks == 500:
            raise RuntimeError('祭炼达到单次执行界限')
        previous = have
        context.click_shape_center(817, '祭炼')
        clicks += 1
        yield from _scene(context, [817])
    recasts = 0
    if equipped or recast_required or pending_recast:
        if not pending_recast:
            context.click_shape_center(817, '重铸页签')
            scene = yield from _scene(context, [818, 819])
        while True:
            if scene == 819:
                old = _score(context, 819, '当前评分')
                new = _score(context, 819, '最新评分')
                log(f'重铸比较：{old} → {new}')
                if new > old:
                    context.click_shape_center(819, '保留新属性')
                    scene = yield from _scene(context, [818])
                    kept = _score(context, 818, '重铸评分')
                    if kept != new:
                        raise RuntimeError('重铸保留后评分未核实')
                    break
            if recasts >= 20:
                raise RuntimeError('20 次重铸未取得更高评分，保留现场')
            # The material counter is in the same location on both layouts.
            have, need = yield from _quantity(context, scene, '材料数量')
            if have < need:
                raise RuntimeError(f'重铸材料不足：{have}/{need}')
            context.click_shape_center(scene, '重铸')
            recasts += 1
            scene = yield from _scene(context, [819])
    else:
        scene = 817
    soul_clicks = 0
    if cast_soul:
        context.click_shape_center(scene, '铸魂页签')
        yield from _scene(context, [820])
        soul_clicks = yield from upgrade_domain_equipment_soul(context, log=log)
        scene = 820
    context.click_shape_center(scene, '返回')
    yield from _scene(context, [814])
    return {'sacrifice_clicks': clicks, 'recast_clicks': recasts,
            'soul_clicks': soul_clicks, 'scene': 814}


def upgrade_domain_equipment_soul(context, *, log=lambda text: None):
    """在 #820 逐次神铸，局部材料 OCR 分子小于分母即停，保留当前页。"""
    yield from _scene(context, [820])
    previous = None
    for clicks in range(501):
        have, need = yield from _quantity(context, 820, '材料数量')
        if have < need:
            log(f'铸魂完成：{have}/{need}，点击 {clicks} 次')
            return clicks
        if previous is not None and have >= previous:
            raise RuntimeError('铸魂材料未减少，保留现场')
        if clicks == 500:
            raise RuntimeError('铸魂达到单次执行界限')
        previous = have
        context.click_shape_center(820, '神铸')
        # The underlying panel updates before the delayed result animation.
        # Do not recognize through that overlay and click its background.
        match = yield from context.wait_scene([821], wait=60)
        if match is None or match.scene_id != 821:
            raise RuntimeError('铸魂结果未出现，保留现场')
        context.click_shape_center(821, '点击屏幕继续')
        yield from _scene(context, [820])


def run_domain_equipment_overview(context, *, log=lambda text: None):
    """每轮重新获取总览状态，按几何顺序处理下一件；不保存半成品游标。"""
    from backend.core.fanxiu.instrumentation.domain_equipment import read_domain_equipment_activation
    results = []
    previous = None
    for _ in range(33):
        yield from _scene(context, [814])
        snapshot = read_domain_equipment_activation()
        active = [slot for slot in snapshot['slots'] if slot['active']]
        if not active:
            return {'status': 'completed', 'items': results, 'scene': 814}
        selected = active[0]
        identity = (selected['native_index'], tuple(selected['reasons']))
        if identity == previous:
            raise RuntimeError(f"镇物操作后激活态未改变：{selected['shape']}")
        previous = identity
        log(f"镇物：{selected['shape']}，{selected['reasons']}")
        context.click_shape_center(814, selected['shape'])
        yield from _scene(context, [815])
        equipped = bool({'装配', '空位'} & set(selected['reasons']))
        if equipped:
            context.click_shape_center(815, '道具替换')
            yield from _scene(context, [816])
            context.click_shape_center(816, '首行装配')
            yield from _scene(context, [815])
        result = yield from finish_domain_equipment(
            context, equipped=equipped, recast_required='重铸' in selected['reasons'],
            cast_soul='铸魂' in selected['reasons'], log=log)
        results.append({'slot': selected['shape'], **result})
    raise RuntimeError('镇物总览处理达到单次执行界限')


def _domain_fraction(text):
    """Parse the displayed available/required pair, including compact units."""
    match = re.search(r'(\d+(?:\.\d+)?)([万亿]?)/(\d+(?:\.\d+)?)([万亿]?)', text)
    if not match:
        return None
    factors = {'': 1, '万': 10000, '亿': 100000000}
    return (Decimal(match[1]) * factors[match[2]],
            Decimal(match[3]) * factors[match[4]])


def run_domain_daily_flow(context):
    """从稳定入口处理凝道/破境、镇物；仅在真实返回 #34 后成功。

    法身尚未研发，不自动操作。每次从实时页面重新判定资源，不持久化
    中途步骤。动画最多等待一分钟；未知材料读数有界复核后报错保留现场。
    """
    scene = yield from _scene(context, [34, 20, 812, 814, 815, 817, 818, 819])
    if scene in (815, 817, 818, 819):
        yield from finish_domain_equipment(
            context, equipped=False, recast_required=True,
        )
        scene = 814
    if scene == 34:
        yield from context.go_scene(20, known_paths_only=True)
        scene = yield from _scene(context, [20])
    if scene == 20:
        context.click_ocr_text(20, '领域', in_shapes=['菜单'], match_mode='exact')
        scene = yield from _scene(context, [812])
    progress_clicks = 0
    if scene == 812:
        for _ in range(501):
            breaking = _domain_fraction(_text(context, 812, '破境数量'))
            if breaking is not None:
                have, need = breaking
                action = '破境'
            else:
                fraction = _domain_fraction(_text(context, 812, '凝道修为'))
                if fraction is None:
                    raise RuntimeError('领域凝道/破境材料未识别，保留现场')
                have, need = fraction
                action = '凝道'
            if need <= 0:
                raise RuntimeError('领域所需材料无效')
            if have < need:
                break
            if progress_clicks >= 500:
                raise RuntimeError('领域升级达到单次执行界限')
            context.click_shape_center(812, action)
            progress_clicks += 1
            if action == '破境':
                match = yield from context.wait_scene([813], wait=60)
                if match is None or match.scene_id != 813:
                    raise RuntimeError('领域破境结果未出现，保留现场')
                context.click_shape_center(813, '点击屏幕继续')
            yield from _scene(context, [812])
        context.click_shape_center(812, '镇物')
        yield from _scene(context, [814])
    equipment = yield from run_domain_equipment_overview(context)
    context.click_shape_center(814, '返回')
    yield from _scene(context, [812])
    context.click_shape_center(812, '返回')
    scene = yield from _scene(context, [20, 34])
    if scene == 20:
        yield from context.go_scene(34, known_paths_only=True)
    yield from _scene(context, [34])
    return {'status': 'completed', 'current_scene': 34,
            'progress_clicks': progress_clicks, 'equipment': equipment}
