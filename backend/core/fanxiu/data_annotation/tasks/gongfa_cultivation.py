"""功法书养成组件：悟境、通玄、法则均消耗已有材料至不足。

业务结果页必须显式继续，不属于弹窗守护。所有动作使用资产 scene/Shape；
只执行现场教学流程：快速融合、快速共鸣、悟境/通玄、法则、返回世界。
不进入单项共鸣详情。秘法按用户要求仅保留函数占位。
"""
from __future__ import annotations

import re

BOOK = 781
# 书页会记住上次页签：从世界入口进入时可能停在自创等其它页签，此时是另一个真实页面，
# 必须先切回功法书页签再执行养成流程。
BOOK_OTHER_TAB = 800
STAGE_ID = "gongfa-cultivation"
STAGE_VERSION = "1"
TABS = ("剑修", "法修", "魔修", "体修", "仙术")
ENLIGHTENMENT = 786
ENLIGHTENMENT_RESULT = 787
TRANSCENDENCE = 788
TRANSCENDENCE_RESULT = 789
LAW_UPGRADE = 790
UPGRADES = {
    ENLIGHTENMENT: ("悟境", ENLIGHTENMENT_RESULT),
    TRANSCENDENCE: ("通玄", TRANSCENDENCE_RESULT),
    LAW_UPGRADE: ("升级", None),
}


def require_scene(context, scene_id):
    """等待动画中的专属身份完整出现，不点击全局回退识别出的通用页。"""
    for attempt in range(3):
        match = yield from context.wait_scene([scene_id], wait=8)
        if match.scene_id == scene_id:
            return match
        if attempt < 2:
            yield from context.wait_action_settle(.5)
            context.cur_frame(update=True)
    raise RuntimeError(f"功法升级：预期 #{scene_id}，实际 #{match.scene_id}")


def complete_current_upgrade(context, scene_id: int, *, max_actions: int = 100):
    """从当前悟境/通玄/法则页整单重入，材料不足才结束；不购买材料。

    每次读取独立数量 Shape，悟境/通玄显式处理结果页，法则原页刷新。
    下一轮核验材料实际减少；无进展或识别不明保留现场报错。
    """
    action, result_scene = UPGRADES[scene_id]
    previous = None
    actions = 0
    while True:
        yield from require_scene(context, scene_id)
        frame = context.cur_frame(update=True)
        if scene_id == ENLIGHTENMENT:
            terminal = context.ocr_text_in_shapes(
                scene_id, ["满级状态"], crop=True, padding=0, frame_data_url=frame,
            )
            if "大圆满" in re.sub(r"\s+", "", terminal):
                return {"upgrade": action, "actions": actions, "outcome": "max_level"}
        text = context.ocr_text_in_shapes(
            scene_id, ["材料数量"], crop=True, padding=0, frame_data_url=frame,
        )
        ratios = re.findall(r"(\d+)\s*[/／]\s*(\d+)", text)
        if len(ratios) != 1 or int(ratios[0][1]) <= 0:
            raise RuntimeError(f"功法{action}材料数量不明确：{text!r}")
        available, cost = map(int, ratios[0])
        if previous is not None and available >= previous:
            raise RuntimeError(f"功法{action}材料未减少：{previous}→{available}")
        if available < cost:
            return {"upgrade": action, "actions": actions,
                    "remaining": available, "required": cost}
        if actions >= max_actions:
            raise RuntimeError(f"功法{action}超过单次动作上限 {max_actions}")
        yield from context.wait_click(scene_id, action)
        if result_scene is not None:
            yield from require_scene(context, result_scene)
            yield from context.wait_click(result_scene, "继续")
        else:
            yield from context.wait_action_settle(1)
        previous = available
        actions += 1


def activate_and_upgrade_secret_art(context):
    """秘法亮起时还有激活、升级操作；当前无候选，尚未进行现场教学。

    用户明确要求保留 pass 占位，后续教学后在此补齐，不自行探索新分支。
    """
    pass


def quick_fusion(context):
    """原生快速融合；确认是业务弹窗，由本流程显式处理。"""
    yield from context.wait_click(BOOK, "快速融合")
    # 空批次提示短暂，先采帧再识别，避免 OCR 延迟错过反馈。
    frames = []
    for _ in range(5):
        yield from context.wait_action_settle(.25)
        frames.append(context.cur_frame(update=True))
    for frame in frames:
        text = context.ocr_text_in_shapes(
            BOOK, ["操作反馈"], crop=True, padding=0, frame_data_url=frame,
        )
        if "暂无可融合功法" in text:
            return {"outcome": "nothing_to_fuse"}
    yield from require_scene(context, 782)
    yield from context.wait_click(782, "确认")
    yield from require_scene(context, 783)
    yield from context.wait_click(783, "继续")
    yield from require_scene(context, BOOK)
    return {"outcome": "fused"}


def quick_resonance(context):
    """只走藏书→快速共鸣→结果继续→返回，不进入各品质的单项共鸣。"""
    yield from context.wait_click(BOOK, "藏书")
    yield from context.wait_click(784, "快速共鸣")
    yield from context.wait_action_settle(1)
    match = yield from context.wait_scene([785, 784], wait=8)
    if match.scene_id == 785:
        yield from context.wait_click(785, "继续")
        outcome = "resonated"
    elif match.scene_id == 784:
        # 原生批量入口在没有共鸣项时留在藏书；不扩展单项共鸣分支。
        outcome = "no_result_page"
    else:
        raise RuntimeError(f"快速共鸣出现未知页面 #{match.scene_id}")
    yield from context.wait_click(784, "返回")
    yield from require_scene(context, BOOK)
    return {"outcome": outcome}


def active_book_tabs(context):
    frame = context.cur_frame(update=True)
    return [tab for tab in TABS if context.shape_matches(
        BOOK, tab + "激活点", frame_data_url=frame,
    )]


def visible_upgrade_candidate(context, *, frame_data_url=None):
    """在标注的左右列中解析重复 Shape；同屏多个提示时取一个完整实例。"""
    frame = frame_data_url or context.cur_frame(update=True)
    for title, scene in (("可悟境", ENLIGHTENMENT), ("可通玄", TRANSCENDENCE)):
        for column in ("左列", "右列"):
            container = "功法列表/" + column
            template = container + "/" + title
            items = context.find_floating_items_by_anchor_text(
                BOOK, template, "提示", title, container_shape=container,
                frame_data_url=frame, match_mode="contains", crop=True,
            )
            for item in items:
                if context.floating_item_field_is_fully_inside(item, "提示", container):
                    return item, scene
    return None


def find_upgrade_candidate(context):
    """使用连续列表的默认重叠滚动；先覆盖当前位置上方，再向下扫描。"""
    for direction in ("up", "down"):
        for _ in range(30):
            yield from require_scene(context, BOOK)
            candidate = visible_upgrade_candidate(context)
            if candidate is not None:
                return candidate
            changed = yield from context.scroll_shape_content(
                BOOK, "功法列表", direction=direction,
            )
            if not changed:
                break
        else:
            raise RuntimeError("功法列表滚动超过上限，未确认边界")
    return visible_upgrade_candidate(context)


def complete_book_tabs(context):
    results = []
    for tab in TABS:
        if tab not in active_book_tabs(context):
            continue
        yield from context.wait_click(BOOK, tab)
        for _ in range(100):
            yield from require_scene(context, BOOK)
            if tab not in active_book_tabs(context):
                break
            candidate = yield from find_upgrade_candidate(context)
            if candidate is None:
                raise RuntimeError(f"{tab}红点未消失，但列表未找到悟境/通玄候选")
            yield from require_scene(context, BOOK)
            candidate = visible_upgrade_candidate(context)
            if candidate is None:
                raise RuntimeError("功法升级候选在点击前发生变化")
            item, scene = candidate
            context.click_floating_item_field(item, "提示")
            result = yield from complete_current_upgrade(context, scene)
            if not result["actions"]:
                raise RuntimeError(f"{tab}可升级提示与材料数量不一致")
            results.append({"tab": tab, **result})
            yield from context.wait_click(scene, "返回")
        else:
            raise RuntimeError(f"{tab}升级循环超过上限")
    if active_book_tabs(context):
        raise RuntimeError("功法分类仍有激活点")
    return results


def upgrade_law(context):
    """当前已教学的红点对应鸿蒙时间法则，消耗已有材料升级。"""
    yield from require_scene(context, BOOK)
    if not context.shape_matches(BOOK, "法则激活点", frame_data_url=context.cur_frame(update=True)):
        return {"outcome": "no_upgrade_indicator"}
    yield from context.wait_click(BOOK, "法则")
    yield from context.wait_click(256, "鸿蒙时间法则")
    result = yield from complete_current_upgrade(context, LAW_UPGRADE)
    yield from context.wait_click(LAW_UPGRADE, "返回")
    yield from context.wait_click(256, "功法书")
    yield from require_scene(context, BOOK)
    if context.shape_matches(BOOK, "法则激活点", frame_data_url=context.cur_frame(update=True)):
        raise RuntimeError("法则升级后仍有红点，保留现场")
    return result


def upgrade_gongfa_book(context):
    yield from enter_book(context)
    fusion = yield from quick_fusion(context)
    resonance = yield from quick_resonance(context)
    books = yield from complete_book_tabs(context)
    law = yield from upgrade_law(context)
    activate_and_upgrade_secret_art(context)
    yield from context.wait_click(BOOK, "返回")
    world = yield from context.wait_scene([34, 661], wait=8)
    if world.scene_id not in (34, 661):
        raise RuntimeError(f"升级功法书结束未回世界：#{world.scene_id}")
    return {"result": "success", "outcome": "complete", "fusion": fusion,
            "resonance": resonance, "books": books, "law": law,
            "secret_art": "reserved_by_user", "final_scene": world.scene_id}


def enter_book(context):
    """进入功法书页并停在该页；从已知页面重入，书页停在其它页签时先切回。

    过渡帧上分层识别可能给出园区其它已知场景，点击前的场景守护会拒绝落点；
    这种情况按识别波动重试，只有持续无法识别才交由未匹配守护上报。
    """
    from backend.core.fanxiu.behavior_tree.errors import SceneClickMismatch

    known = [BOOK, BOOK_OTHER_TAB, 782, 783, 784, 785, 786, 787, 788, 789, 790, 256, 34, 661]
    for attempt in range(8):
        match = yield from context.wait_scene(known, wait=8, required=(attempt == 0))
        if match is None:
            yield from context.wait_action_settle(1.0)
            continue
        scene = int(match.scene_id)
        if scene == BOOK:
            return match
        if scene == BOOK_OTHER_TAB:
            shape = "功法书页签"
        elif scene in (34, 661):
            shape = "功法书入口"
        elif scene in (783, 785, 787, 789):
            shape = "继续"
        elif scene == 782:
            shape = "取消"
        elif scene in (784, 786, 788, 790, 256):
            shape = "返回"
        else:
            raise RuntimeError(f"升级功法书遇到未知起始页面 #{scene}")
        try:
            yield from context.wait_click(scene, shape)
        except SceneClickMismatch:
            yield from context.wait_action_settle(1.0)
    raise RuntimeError("未进入功法书")
