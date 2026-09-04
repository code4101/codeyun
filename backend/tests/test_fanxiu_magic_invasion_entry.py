from __future__ import annotations

from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.tasks.magic_invasion import (
    MAGIC_INVASION_TIANNAN_COMPLETE_SCENE_TITLE,
    _enter_magic_invasion_map,
    _magic_entry_scene_ids,
)


class _EntryContext:
    def __init__(self, scenes: list[int], *, transition_scene_id: int | None = None) -> None:
        self.scenes = list(scenes)
        self.transition_scene_id = transition_scene_id
        self.actions: list[tuple[object, ...]] = []
        self.wait_candidates: list[tuple[int, ...]] = []

    def resolve_view_selector(self, title: str):
        if (
            title == MAGIC_INVASION_TIANNAN_COMPLETE_SCENE_TITLE
            and self.transition_scene_id is not None
        ):
            return SimpleNamespace(id=self.transition_scene_id)
        return None

    def click_shape(self, scene_id: int, title: str) -> None:
        self.actions.append(("click", scene_id, title))

    def click_shape_center(self, scene_id: int, title: str) -> None:
        self.actions.append(("click_center", scene_id, title))

    def wait_scene(self, scenes: list[int], **_options):
        self.wait_candidates.append(tuple(scenes))
        yield "wait"
        return SimpleNamespace(scene_id=self.scenes.pop(0))

    def wait_action_settle(self, seconds: float):
        self.actions.append(("settle", seconds))
        yield "settle"


def test_magic_entry_direct_landing_keeps_optional_handlers_dormant() -> None:
    context = _EntryContext([512], transition_scene_id=700)

    list(_enter_magic_invasion_map(context))

    assert context.actions == [("click", 509, "前往大地图")]
    assert 425 in context.wait_candidates[0]
    assert 700 in context.wait_candidates[0]


def test_magic_entry_consumes_world_map_and_business_layer0_then_resumes() -> None:
    context = _EntryContext([425, 700, 425, 512], transition_scene_id=700)

    list(_enter_magic_invasion_map(context))

    assert context.actions == [
        ("click", 509, "前往大地图"),
        ("click_center", 425, "天南大陆"),
        ("settle", 0.5),
        ("click_center", 700, "背景"),
        ("settle", 0.5),
        ("click_center", 425, "天南大陆"),
        ("settle", 0.5),
    ]


def test_magic_entry_resolves_transition_asset_by_title_not_guessed_id() -> None:
    context = _EntryContext([], transition_scene_id=734)

    assert _magic_entry_scene_ids(context)[-1] == 734

