from __future__ import annotations

from typing import Any


PET_HOME_SCENE_ID = 483
PET_DETAIL_SCENE_ID = 547
PET_APTITUDE_SCENE_ID = 545
PET_PILL_DIALOG_SCENE_ID = 706


def open_pet_aptitude_pill(context: Any, *, item_name: str, item_id: int, max_scrolls: int = 6,
                           initial_scene=None):
    """按名称查找并核对 Runtime 身份；initial_scene 复用紧邻调用的识别帧。

    提供 initial_scene 后到调用之间不得操作页面；滚动后总是重新识别。
    """
    from backend.core.fanxiu.instrumentation.item_batch_use_dialog import read_item_batch_use_dialog_snapshot

    if not item_name.strip() or item_id <= 0 or max_scrolls < 0:
        raise ValueError("需要有效道具名称、ID 和滚动次数")
    direction = "down"
    for index in range(2 * max_scrolls + 2):
        scene = initial_scene if index == 0 and initial_scene is not None else (
            yield from context.wait_scene([PET_APTITUDE_SCENE_ID], wait=15)
        )
        if int(scene) != PET_APTITUDE_SCENE_ID:
            raise RuntimeError("当前不是灵兽资质页")
        frame = scene.frame_data_url
        rows = context.ocr_fragments_in_shapes(PET_APTITUDE_SCENE_ID, ["资质丹"], frame_data_url=frame)
        matches = [row for row in rows if row.get("text", "").replace(" ", "") == item_name.replace(" ", "")]
        if len(matches) > 1:
            raise RuntimeError("当前可见道具名称不唯一")
        if matches:
            row = matches[0]
            context.click_frame_point(PET_APTITUDE_SCENE_ID, row["x"] + row["w"] / 2, row["y"] + row["h"] / 2)
            landed = yield from context.wait_scene([PET_PILL_DIALOG_SCENE_ID], wait=15)
            if int(landed) != PET_PILL_DIALOG_SCENE_ID:
                raise RuntimeError("点击道具后未进入使用弹窗")
            return read_item_batch_use_dialog_snapshot(expected_item_id=item_id)
        if index == max_scrolls:
            direction = "up"
        changed = yield from context.scroll_shape_content(PET_APTITUDE_SCENE_ID, "资质丹", direction=direction, ratio=0.5)
        if not changed:
            if direction == "up":
                break
            direction = "up"
    raise RuntimeError(f"当前列表未找到{item_name}")


def prepare_pet_pill_quantity(context: Any, *, item_id: int, quantity: int,
                              initial_snapshot: dict | None = None):
    """Runtime 只核对首尾身份与数量；滑条反馈读取局部数量 OCR。

    initial_snapshot 可复用刚打开该弹窗的回执，其间不得操作或切换页面。
    返回值是最终 Runtime 核对结果，可供紧接着的使用动作直接消费。
    """
    from backend.core.fanxiu.instrumentation.item_batch_use_dialog import read_item_batch_use_dialog_snapshot
    from backend.core.fanxiu.data_annotation.tasks.integer_count_control import IntegerSliderAssets, set_verified_integer_slider_count

    if initial_snapshot is None:
        scene = yield from context.wait_scene([PET_PILL_DIALOG_SCENE_ID], wait=15)
        if int(scene) != PET_PILL_DIALOG_SCENE_ID:
            raise RuntimeError("当前不是道具使用弹窗")
    def read_count():
        return read_item_batch_use_dialog_snapshot(expected_item_id=item_id)
    before = dict(initial_snapshot) if initial_snapshot is not None else read_count()
    if before.get("item_id") != item_id:
        raise RuntimeError("初始使用弹窗道具身份不符")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= before["single_use_maximum"]:
        raise ValueError("使用数量超出当前道具允许范围")
    assets = IntegerSliderAssets(settings_scene_id=PET_PILL_DIALOG_SCENE_ID, count_region="数量",
                                count_decrease="减少", count_increase="增加",
                                count_slider_thumb="滑块游标", count_slider_track="滑条",
                                count_slider_left_anchor="滑轨左端", count_slider_right_anchor="滑轨右端")
    control = yield from set_verified_integer_slider_count(context, assets, quantity,
                                                maximum=before["slider_maximum"], max_adjustments=10,
                                                count_label="灵兽资质丹数量",
                                                initial_count=before["current"], runtime_count_reader=None)
    # already_exact performs no GUI action; the fresh initial snapshot is
    # still the final fact. Only an actual adjustment needs another read.
    after = before if control.get("phase") == "already_exact" else read_count()
    if (after["current"] != quantity or after["owned_count"] != before["owned_count"]
            or after.get("pet_id") != before.get("pet_id")):
        raise RuntimeError("使用前数量或库存发生变化")
    after["quantity_control"] = control
    return after


def prepare_pet_resource_page(context: Any):
    """灵兽资源使用第一步：主页出现可升级时，先完成快速吞噬。"""
    from backend.core.fanxiu.data_annotation.tasks.resource_auto_use import complete_pet_quick_swallow_on_current_page

    scene = yield from context.wait_scene([PET_HOME_SCENE_ID], wait=15)
    if int(scene) != PET_HOME_SCENE_ID:
        raise RuntimeError("当前不是灵兽主页")
    frame = scene.frame_data_url
    lines = context.ocr_fragments_in_shapes(PET_HOME_SCENE_ID, ["灵兽升级提示区域"], frame_data_url=frame)
    if not any("可升级" in row.get("text", "") for row in lines):
        return {"status": "no_visible_upgrade", "upgraded": False}
    result = yield from complete_pet_quick_swallow_on_current_page(context)
    frame = context.cur_frame(update=True)
    lines = context.ocr_fragments_in_shapes(PET_HOME_SCENE_ID, ["灵兽升级提示区域"], frame_data_url=frame)
    if any("可升级" in row.get("text", "") for row in lines):
        raise RuntimeError("快速吞噬后仍有可升级提示，需要重新核对")
    return {**result, "status": "prepared", "upgraded": result.get("status") != "nothing_to_upgrade"}


def enter_first_growing_pet_aptitude(context: Any):
    """Open the first growing pet's aptitude page using verified scene assets."""
    context.click_shape_center(PET_HOME_SCENE_ID, "第一个成长灵兽")
    scene = yield from context.wait_scene([PET_DETAIL_SCENE_ID], wait=30.0)
    if int(scene) != PET_DETAIL_SCENE_ID:
        raise RuntimeError("未进入成长灵兽详情")
    context.click_shape_center(PET_DETAIL_SCENE_ID, "资质")
    scene = yield from context.wait_scene([PET_APTITUDE_SCENE_ID], wait=30.0)
    if int(scene) != PET_APTITUDE_SCENE_ID:
        raise RuntimeError("未进入灵兽资质页")
    return PET_APTITUDE_SCENE_ID

