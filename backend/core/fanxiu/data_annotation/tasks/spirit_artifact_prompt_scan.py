"""灵器红箭头的当前画面扫描；只提供待处理位置，不推断提示原因。

总览箭头仅筛选应进入的灵器。进入后必须分别检查装配、升阶、升品
的提示与业务事实，完成动作后重新扫描；红箭头本身不是成功判据。
"""
from __future__ import annotations

import base64


def red_arrow_centers(frame_data_url: str, *, surface: str) -> tuple[tuple[float, float], ...]:
    """从已确认的总览卡片顶缘或页签顶缘识别红底上箭头。"""
    import cv2
    import numpy as np

    if surface not in ('overview', 'tabs'):
        raise ValueError('红箭头扫描位置必须是 overview 或 tabs')
    payload = frame_data_url.partition(',')[2]
    if not frame_data_url.startswith('data:image/') or not payload:
        raise ValueError('红箭头扫描需要当前帧 data URL')
    frame = cv2.imdecode(np.frombuffer(base64.b64decode(payload), np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError('当前帧图片无法解码')
    height, width = frame.shape[:2]
    top, bottom = ((.16, .22) if surface == 'overview' else (.85, .93))
    start, end = int(height * top), int(height * bottom)
    hsv = cv2.cvtColor(frame[start:end], cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv, (0, 70, 70), (12, 255, 255))
    red |= cv2.inRange(hsv, (170, 70, 70), (179, 255, 255))
    contours, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    scale = width / 900.0
    centers = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if (27 * scale <= w <= 38 * scale and 27 * scale <= h <= 38 * scale
                and .85 <= w / h <= 1.15 and area >= 500 * scale * scale):
            centers.append((x + w / 2, start + y + h / 2))
    return tuple(sorted(centers))


def scan_spirit_artifact_overview_arrows(context, execute) -> dict:
    return execute(spirit_artifact_overview_scan_steps(context))


def spirit_artifact_overview_scan_steps(context):
    """从任意滚动位置扫全馆；无箭头画面不做 OCR。

    半屏滚动保证相邻画面重叠，首尾用 OCR 校验已跨越完整列表；
    只有出现箭头的帧才 OCR 名称并做同帧归属。
    """
    from ...catalog.spirit_artifact_identity import load_spirit_artifact_templates
    from ...instrumentation.spirit_artifact_ui import locate_spirit_artifact_name

    templates = load_spirit_artifact_templates()
    ordered = sorted(templates)
    if (yield from context.wait_scene([666], wait=8)).scene_id != 666:
        raise RuntimeError('灵器提示扫描要求总览 #666')

    gallery = context.shape(666, '灵器列表')

    def observe(frame, *, endpoint=None):
        arrows = red_arrow_centers(frame, surface='overview')
        if not arrows and endpoint is None:
            return [], True
        tokens = context.ocr_tokens_in_shapes(
            666, ['灵器列表'], crop=True, padding=0, frame_data_url=frame,
            options={'ocr_version': 'PP-OCRv5'})
        visible = {}
        for ware in ordered:
            name = templates[ware][0]
            names = (name, name.replace('摩诃', '摩河'), name.replace('干天', '千天'))
            point = locate_spirit_artifact_name(tokens, names)
            if point is not None:
                visible[ware] = (name, point[0])
        red = []
        for x, _ in arrows:
            matches = [ware for ware, (_, name_x) in visible.items()
                       if abs(x - name_x) <= 54]
            if len(matches) != 1:
                raise RuntimeError(f'总览箭头未能唯一归属灵器：x={x}, matches={matches}')
            red.append(matches[0])
        return red, endpoint is None or endpoint in visible

    def scroll(direction):
        return (yield from context.scroll_shape_content(
            gallery, direction=direction, ratio=.5, duration=.7,
            settle_seconds=.7, stable_sample_count=1))

    # 扫描前先回左边界；无需识别途中每个无提示的卡片。
    for _ in range(len(ordered) + 1):
        if not (yield from scroll('left')):
            left_red, at_start = observe(
                context.cur_frame(update=True), endpoint=ordered[0])
            if at_start:
                break
    else:
        raise RuntimeError('未能回到灵器列表起点')

    red = set(left_red)
    for _ in range(len(ordered) + 1):
        changed = yield from scroll('right')
        found, at_end = observe(
            context.cur_frame(update=True),
            endpoint=ordered[-1] if not changed else None)
        red.update(found)
        if not changed and at_end:
            break
    else:
        raise RuntimeError('未能到达灵器列表末尾')
    return {'visible_ware_ids': ordered, 'red_ware_ids': sorted(red),
            'final_scene_id': 666}


def run_spirit_artifact_prompt_round(context, execute, *, stop_at: float,
                                     max_cycles: int = 3) -> dict:
    return execute(spirit_artifact_prompt_update_steps(
        context, stop_at=stop_at, max_cycles=max_cycles))


def spirit_artifact_prompt_update_steps(context, *, stop_at: float,
                                        max_cycles: int = 3):
    """处理当前灵器提示，逐轮重扫，而不把红箭头等同于某一种原因。

    总览红箭头只负责定位：装配页的可用一键装配交给客户端选择部件，
    灵器效果列表负责清除已激活效果的未读标记；升阶和悟境由 Runtime
    候选与材料保护规则逐笔执行。每轮都从总览事实重新开始。未知原因保留
    在 unresolved_ware_ids，调用方不能把它当成完成或盲目重复点击。
    """
    import time

    from ...catalog.spirit_artifact_identity import load_spirit_artifact_templates
    from .spirit_artifact_upgrade_all import spirit_artifact_safe_upgrade_steps
    from .spirit_artifact_upgrade_count import open_artifact_for_upgrade_steps

    if stop_at <= time.time() or max_cycles <= 0:
        raise ValueError('灵器提示处理需要有效截止时间与轮数')
    templates = load_spirit_artifact_templates()
    actions = []
    for cycle in range(max_cycles):
        if time.time() >= stop_at:
            return {'status': 'paused', 'reason': 'deadline', 'actions': actions}
        before = yield from spirit_artifact_overview_scan_steps(context)
        if not before['red_ware_ids']:
            return {'status': 'complete', 'actions': actions,
                    'visible_ware_ids': before['visible_ware_ids'],
                    'red_ware_ids': [], 'final_scene_id': 666}
        upgrades = yield from spirit_artifact_safe_upgrade_steps(
            context, stop_at=stop_at)
        actions.extend({'kind': 'upgrade', **item} for item in upgrades['actions'])
        if upgrades['status'] != 'complete':
            return {'status': 'paused', 'reason': upgrades['reason'], 'actions': actions}
        # GUI 升阶会改变箭头。仅发生实际升级时重扫一次，否则复用新鲜的
        # 本轮总览观察，避免为没有变化的画面再横扫整条列表。
        if upgrades['actions']:
            before = yield from spirit_artifact_overview_scan_steps(context)
            if not before['red_ware_ids']:
                return {'status': 'complete', 'actions': actions,
                        'visible_ware_ids': before['visible_ware_ids'],
                        'red_ware_ids': [], 'final_scene_id': 666}
        for ware_id in before['red_ware_ids']:
            if time.time() >= stop_at:
                return {'status': 'paused', 'reason': 'deadline', 'actions': actions}
            yield from open_artifact_for_upgrade_steps(context, templates[ware_id][0])
            if (yield from context.wait_scene([667], wait=10)).scene_id != 667:
                raise RuntimeError(f'灵器 {ware_id} 装配页未就绪')
            frame = context.cur_frame(update=True)
            tabs = red_arrow_centers(frame, surface='tabs')
            equip_prompt = any(x < context.shape_box(667, '洗炼')['x']
                               for x, _ in tabs)
            if equip_prompt:
                tokens = context.ocr_tokens_in_shapes(
                    667, ['一键装配'], crop=True, padding=0,
                    frame_data_url=frame, options={'ocr_version': 'PP-OCRv5'})
                if '一键装配' in ''.join(str(t.get('text', '')) for t in tokens):
                    context.click_shape_center(667, '一键装配')
                    yield from context.wait_action_settle(1.2)
                    if (yield from context.wait_scene([667], wait=8)).scene_id != 667:
                        raise RuntimeError(f'灵器 {ware_id} 一键装配后场景异常')
                    actions.append({'kind': 'one_click_equip_attempt', 'ware_id': ware_id})
                # 效果列表可重复查看；只在装配提示存在时打开，并以新场景
                # 身份和关闭 Shape 验证。若箭头来自其他原因，后续重扫会保留。
                context.click_shape_center(667, '灵器效果标题')
                if (yield from context.wait_scene([822], wait=10)).scene_id != 822:
                    raise RuntimeError(f'灵器 {ware_id} 效果列表未就绪')
                context.click_shape_center(822, '暗幕关闭效果')
                if (yield from context.wait_scene([667], wait=10)).scene_id != 667:
                    raise RuntimeError(f'灵器 {ware_id} 效果列表关闭失败')
                actions.append({'kind': 'read_effects', 'ware_id': ware_id})
            context.click_shape_center(667, '背景返回封面')
            if (yield from context.wait_scene([666], wait=10)).scene_id != 666:
                raise RuntimeError(f'灵器 {ware_id} 未返回总览')

        after = yield from spirit_artifact_overview_scan_steps(context)
        if not after['red_ware_ids']:
            return {'status': 'complete', 'actions': actions,
                    'visible_ware_ids': after['visible_ware_ids'],
                    'red_ware_ids': [], 'final_scene_id': 666}
        if after['red_ware_ids'] == before['red_ware_ids']:
            return {'status': 'unresolved', 'actions': actions,
                    'unresolved_ware_ids': after['red_ware_ids'],
                    'final_scene_id': 666}
    final = yield from spirit_artifact_overview_scan_steps(context)
    return {'status': 'paused', 'reason': 'cycle_limit', 'actions': actions,
            'red_ware_ids': final['red_ware_ids'], 'final_scene_id': 666}
