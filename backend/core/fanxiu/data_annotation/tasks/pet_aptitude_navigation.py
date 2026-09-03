from __future__ import annotations

from typing import Any


PET_HOME_SCENE_ID = 483
PET_DETAIL_SCENE_ID = 547
PET_APTITUDE_SCENE_ID = 545


def enter_first_growing_pet_aptitude(context: Any):
    """Open the first growing pet's aptitude page using verified scene assets."""
    context.click_shape_center(PET_HOME_SCENE_ID, "第一个成长灵兽")
    yield from context.wait_scene(PET_DETAIL_SCENE_ID, wait=30.0)
    context.click_shape_center(PET_DETAIL_SCENE_ID, "资质")
    yield from context.wait_scene(PET_APTITUDE_SCENE_ID, wait=30.0)
    return PET_APTITUDE_SCENE_ID
