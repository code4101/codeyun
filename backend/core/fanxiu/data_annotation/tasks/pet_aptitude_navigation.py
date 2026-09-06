from __future__ import annotations

from typing import Any


PET_HOME_SCENE_ID = 483
PET_DETAIL_SCENE_ID = 547
PET_APTITUDE_SCENE_ID = 545


def prepare_pet_resource_page(context: Any):
    """灵兽资源使用第一步：主页出现可升级时，先完成快速吞噬。"""
    from backend.core.fanxiu.data_annotation.tasks.resource_auto_use import complete_pet_quick_swallow_on_current_page

    yield from context.wait_scene([PET_HOME_SCENE_ID], wait=15)
    frame = context.cur_frame(update=True)
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
    yield from context.wait_scene([PET_DETAIL_SCENE_ID], wait=30.0)
    context.click_shape_center(PET_DETAIL_SCENE_ID, "资质")
    yield from context.wait_scene([PET_APTITUDE_SCENE_ID], wait=30.0)
    return PET_APTITUDE_SCENE_ID
