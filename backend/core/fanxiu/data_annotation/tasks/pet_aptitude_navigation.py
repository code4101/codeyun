from __future__ import annotations

from typing import Any


PET_HOME_SCENE_ID = 483
PET_DETAIL_SCENE_ID = 547
PET_APTITUDE_SCENE_ID = 545


def enter_first_growing_pet_aptitude(runtime: Any) -> int:
    """Open the first growing pet's aptitude page using verified scene assets."""
    runtime.click_shape_center(PET_HOME_SCENE_ID, "第一个成长灵兽")
    scene_id, score, _ = runtime.current_scene(
        [PET_DETAIL_SCENE_ID], update=True, timeout=30.0
    )
    if int(scene_id or 0) != PET_DETAIL_SCENE_ID or float(score or 0) < 80.0:
        raise RuntimeError(
            f"点击第一个成长灵兽后未到 #{PET_DETAIL_SCENE_ID}："
            f"scene={scene_id}, score={float(score or 0):.1f}"
        )
    runtime.click_shape_center(PET_DETAIL_SCENE_ID, "资质")
    scene_id, score, _ = runtime.current_scene(
        [PET_APTITUDE_SCENE_ID], update=True, timeout=30.0
    )
    if int(scene_id or 0) != PET_APTITUDE_SCENE_ID or float(score or 0) < 80.0:
        raise RuntimeError(
            f"点击灵兽详情资质后未到 #{PET_APTITUDE_SCENE_ID}："
            f"scene={scene_id}, score={float(score or 0):.1f}"
        )
    return PET_APTITUDE_SCENE_ID
