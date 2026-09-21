"""星海入口：等待浮动名称可读，点击一次，再确认指定目标页。

名称、错字容差、搜索范围及固定入口的点击几何由 #259 Shape 持有。
这里只管理动作时序，不预测旋转角度，也不因一帧缺字而推断入口不存在。
真实帧已验证四域名称定位；入口点击与目标页仍须逐项真实验收。
"""
from __future__ import annotations

from collections.abc import Generator, Sequence
from typing import Any
import re
import time
import unicodedata


# 正式场景 #765「星海悟道树」持有节点窗口 shape，viewport 由该 shape 的 box 提供；
# #766「星海悟道节点」持有激活效果/等级标题与「升级」shape。不再手写树窗口 dict。
# 点击关系沿用已在轮回域、淬锋域验证的 label 顶部上移一个文本高度。
XINGHAI_TREE_VIEW = 765
XINGHAI_TREE_DETAIL_VIEW = 766


def fill_current_xinghai_tree_node(context: Any):
    """Spend available tree material on the verified open node, one level at a time."""
    from backend.core.fanxiu.instrumentation.xinghai_tree import read_xinghai_tree
    from backend.core.fanxiu.instrumentation.item_resources import read_item_available_counts
    from .skill_tree import fill_tree_node
    detail = read_xinghai_tree(detail=True)
    counts, _ = read_item_available_counts(detail['costs'], manager_key='xinghai-tree')

    def click_upgrade():
        current = read_xinghai_tree(detail=True)
        if (current['faqi_id'], current['id']) != (detail['faqi_id'], detail['id']):
            raise RuntimeError('悟道树详情已改变')
        yield from context.wait_click(XINGHAI_TREE_DETAIL_VIEW, '升级')

    return (yield from fill_tree_node(context, read_detail=lambda: read_xinghai_tree(detail=True),
                                     click_upgrade=click_upgrade, expected_id=detail['id'], initial_counts=counts))


def enter_first_xinghai_tree_node(context: Any, *, target_id=None):
    """Locate the first unfilled global node and click it once; verify detail."""
    from backend.core.fanxiu.instrumentation.xinghai_tree import read_xinghai_tree
    from .skill_tree import locate_first_tree_node, tree_progress_labels
    tree = read_xinghai_tree()

    node_window = context.view(XINGHAI_TREE_VIEW).get_shape('节点窗口')
    if node_window is None:
        raise RuntimeError('星海悟道树节点窗口 shape 缺失')
    window_box = node_window.box()
    viewport = (window_box['x'], window_box['y'], window_box['w'], window_box['h'])

    def observe():
        read_xinghai_tree()  # Reject a closed/replaced tree before using OCR.
        frame = context.cur_frame(update=True)
        return tree_progress_labels(context.full_frame_ocr_tokens(frame), viewport=viewport)

    def scroll(direction):
        yield from context.scroll_shape_content(context.view(XINGHAI_TREE_VIEW), '节点窗口', direction=direction)

    found = yield from locate_first_tree_node(context, nodes=tree['nodes'], observe_labels=observe, scroll=scroll, target_id=target_id)
    if found is None:
        return {'reason': '全树已满'}
    target, label_box = found['node'], found['label']
    context.click_frame_point(XINGHAI_TREE_VIEW, label_box['x'], label_box['y']-label_box['h'])
    deadline = time.monotonic()+15
    from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
    while time.monotonic() < deadline:
        try:
            detail = read_xinghai_tree(detail=True)
        except FanxiuRuntimeMemoryError:
            yield from context.wait_action_settle(0.3)
            continue
        if (detail['faqi_id'], detail['id']) != (tree['faqi_id'], target['id']):
            raise RuntimeError('悟道树点击落点身份不符，保留现场')
        return detail
    raise TimeoutError('悟道树点击后详情未就绪，保留现场且不重复点击')


def fill_current_xinghai_tree(context: Any):
    """Upgrade all affordable nodes; a costly first node never stops the tree."""
    from backend.core.fanxiu.instrumentation.xinghai_tree import read_xinghai_tree
    from backend.core.fanxiu.instrumentation.item_resources import read_item_available_counts
    from .skill_tree import fill_available_tree
    yield from context.wait_scene([XINGHAI_TREE_VIEW], wait=10)
    initial = read_xinghai_tree()
    faqi_id = initial['faqi_id']

    def read_tree():
        current = read_xinghai_tree()
        if not current.get('complete') or current['faqi_id'] != faqi_id:
            raise RuntimeError('星海悟道树身份或完整性改变，保留现场')
        return current

    def counts(items):
        return read_item_available_counts(items, manager_key='xinghai-tree')[0] if items else {}

    def leave_node():
        yield from context.wait_click(XINGHAI_TREE_DETAIL_VIEW, '关闭')
        yield from context.wait_scene([XINGHAI_TREE_VIEW], wait=10)

    result = yield from fill_available_tree(
        read_tree=read_tree, read_counts=counts,
        enter_node=lambda ident: enter_first_xinghai_tree_node(context, target_id=ident),
        fill_node=lambda: fill_current_xinghai_tree_node(context), leave_node=leave_node,
    )
    return {**result, 'faqi_id': faqi_id}


def click_pending_xinghai_entry(
    context: Any,
    pending_targets: Sequence[str],
    *,
    timeout: float = 120.0,
) -> Generator[Any, None, dict[str, Any]]:
    """Click one readable pending entry on #259, using the same frame's box.

    Runtime supplies an unordered eligibility set. Each guarded frame tests
    all eligible Shapes; visible matches are ordered top-to-bottom then
    left-to-right, never by Runtime order. No match means observe a new frame,
    not wait for one preselected domain. Shape owns OCR tolerance and geometry.
    This action returns only a click receipt: the caller must validate the
    destination before doing business there. It never repeats a sent click.
    """
    pending = set(pending_targets)
    if pending - {'淬灵域', '淬锋域', '幻灵域', '轮回域'}:
        raise ValueError(f'未知星海入口：{pending}')
    if timeout <= 0:
        raise ValueError('等待预算必须大于 0')
    if not pending:
        return {'clicked': False, 'reason': '无待处理入口'}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        match = yield from context.wait_scene([259], wait=min(10.0, max(0.1, deadline - time.monotonic())))
        frame = match.frame_data_url
        visible = []
        for name in sorted(pending):
            shape = context.shape(259, f'旋转区域/{name}')
            evidence = context.shape_matches(259, shape, frame_data_url=frame)
            if evidence is None:
                continue
            box = evidence.get('ocr_box') or evidence.get('resolved_box')
            if not isinstance(box, dict) or not all(k in box for k in ('x', 'y', 'w', 'h')):
                raise RuntimeError(f'星海 {name} 命中但缺少当前 OCR 框')
            visible.append((float(box['y']), float(box['x']), name, shape, evidence))
        if visible:
            visible.sort(key=lambda row: (row[0], row[1], row[2]))
            _, _, name, shape, evidence = visible[0]
            context.click_shape(259, shape, frame_data_url=frame, match_result=evidence)
            return {'clicked': True, 'target': name,
                    'visible_pending': [row[2] for row in visible],
                    'ocr_text': evidence.get('ocr_text'),
                    'box': evidence.get('ocr_box') or evidence.get('resolved_box')}
        yield from context.wait_action_settle(0.2)
    raise TimeoutError(f'星海待处理入口在 {timeout:g} 秒内未识别：{sorted(pending)}')


def open_xinghai_entry(
    context: Any,
    target: str,
    *,
    target_scenes: Sequence[int],
    search_timeout: float = 120.0,
    landing_timeout: float = 15.0,
) -> Generator[Any, None, Any]:
    """在 #259 打开指定入口，并返回已确认的目标场景。

    120 秒是沿用拜谒的初始有界等待预算，不是旋转周期测量结果。
    wait_click 复用场景守护、父区域、浮动 OCR 唯一性和本帧落点。
    命中缺失或歧义时继续采样；超时抛错并保留现场。动作只发一次，
    随后仅观察调用方声明的目标页，不把“离开源页”当作正确到达。
    不能声明目标页时，应先进行逐步研发与标注，不调用本入口。
    """
    rotating = {"淬锋域", "淬灵域", "幻灵域", "轮回域"}
    if target not in rotating | {"提纯", "异火"}:
        raise ValueError(f"未知星海入口：{target}")
    destinations = list(dict.fromkeys(int(scene) for scene in target_scenes))
    if not destinations or any(scene <= 0 or scene == 259 for scene in destinations):
        raise ValueError("必须声明有效的星海入口目标场景，且不能包含源场景 #259")
    if search_timeout <= 0 or landing_timeout <= 0:
        raise ValueError("等待预算必须大于 0")
    shape = f"旋转区域/{target}" if target in rotating else target
    with context.expect_views(*destinations):
        yield from context.wait_click(259, shape, timeout=search_timeout)
        return (yield from context.wait_scene(
            destinations, wait=landing_timeout, label=f"星海：确认进入{target}",
        ))


# 正式资产 #764 是淬锋域「淬炼升级」与悟道树共用的升级页；required 身份为
# 「淬炼升级」和「悟道树标题」，材料数量与等级 ROI 由该页 Shape 提供。
XINGHAI_UPGRADE_VIEW = 764

# 正式场景 #763 是四域域内展示页：#764 升级与 #765 悟道树都要先经它的中央
# 法器。wake 域会先落到 #767 觉醒页，再点「淬炼页签」回到共用的 #764。
XINGHAI_DOMAIN_VIEW = 763
XINGHAI_WAKE_VIEW = 767


def fill_current_xinghai_upgrade(context: Any, *, max_steps: int = 256):
    """在 #764 共用升级页逐级消耗材料；淬锋域连续升级至材料不足已真实验收。

    每轮先用 Runtime 身份定基（``read_xinghai_upgrade_state``）：id 不一致即报错，
    满级即返回。材料由同帧 OCR「材料数量」严格解析分子/正分母；Runtime
    ``can_upgrade`` 必须与 ``分子>=分母`` 一致，矛盾时只做有界重取（绝不在矛盾时点击）。
    分子不足返回材料不足事实。够用时只调用一次 ``wait_click(764, '淬炼升级')``，随后
    最多 15 秒轮询确认同 id 且等级恰 +1；跳级、身份改变或超时立即报错，绝不重发。
    达到 ``max_steps`` 报错，不长按、不猜成功。

    只读身份/等级并只发送每轮一次的升级点击；不读成本配置、不引入 wallet 依赖。
    ``remaining`` 为 OCR 分子（当前已有），``cost`` 为 OCR 分母（本轮需求）。
    """
    from backend.core.fanxiu.instrumentation.xinghai_runtime import (
        read_xinghai_upgrade_state,
    )

    if type(max_steps) is not int or max_steps <= 0:
        raise ValueError('max_steps 必须为正整数')

    expected_id = read_xinghai_upgrade_state()['id']
    receipts: list[dict[str, Any]] = []
    steps = 0
    while steps < max_steps:
        state = read_xinghai_upgrade_state()
        if state['id'] != expected_id:
            raise RuntimeError('星海升级页身份改变，保留现场')
        if state['is_max_level']:
            return {'reason': '已满级', 'id': expected_id, 'level': state['level'],
                    'steps': steps, 'receipts': receipts}

        numerator = denominator = None
        agreed = False
        for _attempt in range(3):  # 1 次观测 + 有界重取 2 次，矛盾期间不发点击
            match = yield from context.wait_scene([XINGHAI_UPGRADE_VIEW], wait=10)
            text = context.ocr_text_in_shapes(
                XINGHAI_UPGRADE_VIEW, ['材料数量'],
                frame_data_url=match.frame_data_url, padding=0, crop=True)
            normalized = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text or '')))
            fraction = re.fullmatch(r'([0-9]+)/([0-9]+)', normalized)
            if fraction is None:
                raise RuntimeError(f'星海升级页材料数量无法识别：{text!r}')
            numerator, denominator = int(fraction.group(1)), int(fraction.group(2))
            if denominator <= 0:
                raise RuntimeError(f'星海升级页材料数量分母无效：{text!r}')
            state = read_xinghai_upgrade_state()
            if state['id'] != expected_id:
                raise RuntimeError('星海升级页身份改变，保留现场')
            if state['is_max_level'] or state['can_upgrade'] == (numerator >= denominator):
                agreed = True
                break
        if not agreed:
            raise RuntimeError('星海升级页 Runtime can_upgrade 与材料 OCR 矛盾，保留现场')
        if state['is_max_level']:
            return {'reason': '已满级', 'id': expected_id, 'level': state['level'],
                    'steps': steps, 'receipts': receipts}
        if numerator < denominator:
            return {'reason': '材料不足', 'id': expected_id, 'level': state['level'],
                    'steps': steps, 'remaining': numerator, 'cost': denominator,
                    'receipts': receipts}

        level_before = state['level']
        yield from context.wait_click(XINGHAI_UPGRADE_VIEW, '淬炼升级')
        deadline = time.monotonic() + 15
        while True:
            after = read_xinghai_upgrade_state()
            if after['id'] != expected_id:
                raise RuntimeError('星海升级页加点后身份改变，保留现场')
            if after['level'] == level_before + 1:
                break
            if after['level'] != level_before:
                raise RuntimeError('星海升级页等级跳变，禁止重复点击')
            if time.monotonic() >= deadline:
                raise RuntimeError('星海升级页加点结果未确认，禁止重复发送')
            yield from context.wait_action_settle(0.3)
        receipts.append({'level_before': level_before, 'level_after': after['level'],
                         'quantity_before': numerator, 'cost': denominator})
        steps += 1
    raise RuntimeError('星海升级达到诊断上限')


def open_current_xinghai_upgrade(
    context: Any,
    expected_id: int,
    *,
    ready_timeout: float = 15.0,
):
    """从 #763 域内展示页点一次中央法器，确认落点并返回 #764 升级事实。

    先用 ``read_xinghai_domain_identity`` 核对当前域 id 与 ``expected_id`` 一致，
    不一致即报错保留现场，随后只发送一次 ``wait_click(763, '中央法器')``。
    点击后 ``read_xinghai_central_tab`` 可能仍在初始化，``FanxiuRuntimeMemoryError``
    只做有界重取（``ready_timeout`` 秒内重新读取，绝不重发点击）；ADB 等其它
    异常不吞。``level`` 页签直接等 #764；``wake`` 页签等 #767、点「淬炼页签」、
    再等 #764；``stage`` 或未知页签报错。最终 ``read_xinghai_upgrade_state`` 的
    id 必须仍等于 ``expected_id``，返回该升级事实；不构造 stage 场景。
    """
    from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
    from backend.core.fanxiu.instrumentation.xinghai_runtime import (
        read_xinghai_central_tab,
        read_xinghai_domain_identity,
        read_xinghai_upgrade_state,
    )

    if type(expected_id) is not int or expected_id <= 0:
        raise ValueError('expected_id 必须为正整数')
    if ready_timeout <= 0:
        raise ValueError('等待预算必须大于 0')

    yield from context.wait_scene([XINGHAI_DOMAIN_VIEW], wait=10, label='星海：确认域内展示页')
    identity = read_xinghai_domain_identity()
    if identity.get('id') != expected_id:
        raise RuntimeError(f"星海域内身份不符：{identity.get('id')} != {expected_id}")

    yield from context.wait_click(XINGHAI_DOMAIN_VIEW, '中央法器')

    deadline = time.monotonic() + ready_timeout
    while True:
        try:
            tab = read_xinghai_central_tab()
            break
        except FanxiuRuntimeMemoryError:
            if time.monotonic() >= deadline:
                raise
            yield from context.wait_action_settle(0.3)
    if tab.get('id') != expected_id:
        raise RuntimeError(f"星海中央法器身份不符：{tab.get('id')} != {expected_id}")

    kind = tab.get('tab')
    if kind == 'level':
        yield from context.wait_scene([XINGHAI_UPGRADE_VIEW], wait=10, label='星海：确认淬炼升级页')
    elif kind == 'wake':
        yield from context.wait_scene([XINGHAI_WAKE_VIEW], wait=10, label='星海：确认觉醒页')
        yield from context.wait_click(XINGHAI_WAKE_VIEW, '淬炼页签')
        yield from context.wait_scene([XINGHAI_UPGRADE_VIEW], wait=10, label='星海：确认淬炼升级页')
    else:
        raise RuntimeError(f'星海中央法器落点页签不支持：{kind!r}')

    state = read_xinghai_upgrade_state()
    if state.get('id') != expected_id:
        raise RuntimeError(f"星海升级页身份不符：{state.get('id')} != {expected_id}")
    return state
