from __future__ import annotations

from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.tasks.world_menu_navigation import (
    open_world_menu_function,
)
from backend.core.fanxiu.instrumentation.world_menu import (
    WorldMenuItem,
    WorldMenuReadTimings,
    WorldMenuSnapshot,
)


class _Runtime:
    def __init__(self):
        self.clicks = []

    def go_scene(self, scene):
        yield ("go", scene)

    def wait_click(self, scene, shape):
        yield ("click", scene, shape)

    def wait_scene(self, *scenes, **kwargs):
        yield ("wait", scenes, kwargs)
        return scenes[0]

    def cur_frame(self, *, update):
        return "frame"

    def current_scene(self, **kwargs):
        return 35, 100.0, "frame"

    def ocr_tokens_in_shapes(self, *_args, **_kwargs):
        return [
            {"text": "灵", "x": 703, "y": 1424, "w": 35, "h": 37, "parent_line_id": "l", "order": 0},
            {"text": "兽", "x": 732, "y": 1424, "w": 36, "h": 37, "parent_line_id": "l", "order": 1},
        ]

    def click_frame_point(self, scene, x, y):
        self.clicks.append((scene, x, y))


def test_standard_world_menu_navigation_uses_runtime_identity_and_verifies_successor(
    monkeypatch,
) -> None:
    snapshot = WorldMenuSnapshot(
        complete=True,
        items=(WorldMenuItem(index=12, function_id=4000, name="灵兽"),),
        pid=1,
        process_start_ticks=2,
        fingerprint="f",
        timings=WorldMenuReadTimings(1, 1, 1, 3, "hot"),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.world_menu_navigation.read_world_menu_snapshot",
        lambda: snapshot,
    )
    runtime = _Runtime()

    result = None
    generator = open_world_menu_function(
        runtime, 4000, expected_scene_ids=(483,), timeout_seconds=20
    )
    try:
        while True:
            next(generator)
    except StopIteration as done:
        result = done.value

    assert result == 483
    assert runtime.clicks == [(35, 735.5, 1387.0)]
