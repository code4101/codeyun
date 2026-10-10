from __future__ import annotations

from types import SimpleNamespace
import pytest

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

    assert context.actions == [("settle", 3.0)]
    assert 425 in context.wait_candidates[0]
    assert 700 in context.wait_candidates[0]


def test_magic_entry_consumes_world_map_and_business_layer0_then_resumes() -> None:
    context = _EntryContext([425, 700, 425, 512], transition_scene_id=700)

    list(_enter_magic_invasion_map(context))

    assert context.actions == [
        ("settle", 3.0),
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


def test_magic_entry_preserves_successful_slow_world_navigation(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import magic_invasion as driver
    clock = [0.0]
    monkeypatch.setattr(driver.time, "monotonic", lambda: clock[0])
    context = _EntryContext([509, 517, 34, 425, 512])

    def go_scene(scene):
        assert scene == 425
        clock[0] += 100.0
        context.actions.append(("go", scene))
        yield "go"

    context.go_scene = go_scene
    list(driver.enter_magic_invasion_challenge_page(context))
    assert ("go", 425) in context.actions
    assert ("click_center", 425, "天南大陆") in context.actions


@pytest.mark.parametrize("changing", [False, True])
def test_magic_entry_bounds_stalls_and_changing_cover_cycles(monkeypatch, changing):
    from backend.core.fanxiu.data_annotation.tasks import magic_invasion as driver
    clock = [0.0]
    monkeypatch.setattr(driver.time, "monotonic", lambda: clock[0])
    context = _EntryContext([509, 517] * 20 if changing else [509] * 40)
    original_wait = context.wait_scene

    def wait_scene(*args, **kwargs):
        clock[0] += 30.0
        return (yield from original_wait(*args, **kwargs))

    context.wait_scene = wait_scene
    with pytest.raises(RuntimeError, match="可选页面超时"):
        list(driver.enter_magic_invasion_challenge_page(context))
    assert clock[0] <= (270.0 if changing else 120.0)

