"""镇物详情组件：固定祭炼；换装后普通重铸取得更高评分即结束。

研发组件，尚未接入每日任务。点击只使用固定 Shape；祭炼以画面分子/
分母为准，不检查红点或 Runtime。未知场景、OCR 不确定或无进展保留现场。
"""
import re


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
    raw = _text(context, scene, shape)
    match = re.fullmatch(r'(\d+)/(\d+)', raw)
    if not match or int(match[2]) <= 0:
        raise RuntimeError(f"镇物材料读数不确定：{raw!r}")
    return int(match[1]), int(match[2])


def _score(context, scene, shape):
    raw = _text(context, scene, shape)
    match = re.fullmatch(r'重铸评分[:：](\d+)', raw)
    if not match:
        raise RuntimeError(f"镇物评分读数不确定：{raw!r}")
    return int(match[1])


def finish_domain_equipment(context, *, equipped: bool, cast_soul=False, log=lambda text: None):
    """从详情/祭炼/重铸页闭环当前镇物并返回 #814。

    equipped 由本次真实装配动作决定，不能从历史步骤推测。已有待保留
    页面仍比较左右分数；只有更高分才保留。重铸最多 20 次，祭炼最多
    500 次，达到界限报错，不将未完成伪装成成功。
    """
    scene = yield from _scene(context, [815, 817, 818, 819])
    if scene == 819:
        raise RuntimeError('已有重铸候选，请先处理候选，保留现场')
    if scene != 817:
        context.click_shape_center(scene, '祭炼' if scene == 815 else '祭炼页签')
        yield from _scene(context, [817])
    previous = None
    clicks = 0
    for _ in range(501):
        have, need = _quantity(context, 817, '材料数量')
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
    if equipped:
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
            have, need = _quantity(context, scene, '材料数量')
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
        have, need = _quantity(context, 820, '材料数量')
        if have < need:
            log(f'铸魂完成：{have}/{need}，点击 {clicks} 次')
            return clicks
        if previous is not None and have >= previous:
            raise RuntimeError('铸魂材料未减少，保留现场')
        if clicks == 500:
            raise RuntimeError('铸魂达到单次执行界限')
        previous = have
        context.click_shape_center(820, '神铸')
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
            context, equipped=equipped, cast_soul='铸魂' in selected['reasons'], log=log)
        results.append({'slot': selected['shape'], **result})
    raise RuntimeError('镇物总览处理达到单次执行界限')
