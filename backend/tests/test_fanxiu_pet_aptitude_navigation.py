from __future__ import annotations

from backend.core.fanxiu.data_annotation.tasks.pet_aptitude_navigation import (
    PET_APTITUDE_SCENE_ID,
    enter_first_growing_pet_aptitude,
)


class _Runtime:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, str]] = []
        self.observations = iter([(547, 100.0, "detail"), (545, 100.0, "aptitude")])

    def click_shape_center(self, scene_id: int, shape: str) -> None:
        self.clicks.append((scene_id, shape))

    def current_scene(self, _candidates, **_kwargs):
        return next(self.observations)


def test_pet_aptitude_navigation_uses_verified_547_detail_scene() -> None:
    runtime = _Runtime()

    assert enter_first_growing_pet_aptitude(runtime) == PET_APTITUDE_SCENE_ID
    assert runtime.clicks == [(483, "第一个成长灵兽"), (547, "资质")]
    assert all(scene_id != 334 for scene_id, _shape in runtime.clicks)
