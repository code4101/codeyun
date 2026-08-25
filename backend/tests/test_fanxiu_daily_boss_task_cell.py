from __future__ import annotations

import threading
from pathlib import Path

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.runner import (
    create_behavior_tree_runtime_runner,
)


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def test_daily_boss_task_cell_leaves_scene_lifecycle_to_business_handler() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_boss")
    assert definition is not None
    calls: list[tuple[dict, dict]] = []

    class Runner:
        def _fanxiu_runtime(self, *_args, **_kwargs):
            raise AssertionError("daily boss wrapper must not repeat scene navigation")

        def _execute_daily_boss_task(self, ctx, _stop_event, payload):
            calls.append((ctx, payload))
            if False:
                yield None
            return "success"

    ctx = {"asset_tree_path": "asset-tree.json"}
    payload = {"post_challenge_timeout_seconds": 900}
    result = _drain(definition.handler(Runner(), ctx, payload, threading.Event()))

    assert result == "success"
    assert calls == [(ctx, payload)]


def test_daily_boss_mozu_world_transition_waits_without_fallback_click(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    scenes = iter(
        [
            (None, 0.0, "mozu-transition", ""),
            (34, 100.0, "world", ""),
        ]
    )
    ensured: list[str] = []

    class Runtime:
        def wait_action_settle(self, _seconds):
            if False:
                yield None

        def clear_frame(self):
            raise AssertionError("transition must finish before goto_view fallback")

        def goto_view(self, _scene_id):
            raise AssertionError("transition must not click or navigate")

    def no_overlay(*_args, **_kwargs):
        if False:
            yield None
        return False

    def ensure_world(*_args, **_kwargs):
        ensured.append("world")
        if False:
            yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: Runtime())
    monkeypatch.setattr(runner, "_fanxiu_runtime_scene_text", lambda *_args, **_kwargs: next(scenes))
    monkeypatch.setattr(runner, "_close_daily_boss_item_detail_if_present", no_overlay)
    monkeypatch.setattr(runner, "_close_daily_boss_storage_bag_if_present", no_overlay)
    monkeypatch.setattr(runner, "_scene_reference_similarity", lambda *_args, **_kwargs: 96.0)
    monkeypatch.setattr(runner, "_ensure_daily_lingzu_outer_world", ensure_world)

    result = _drain(
        runner._return_daily_boss_to_world(
            {"asset_tree_path": Path("asset-tree.json"), "images": {314: {"id": 314}}},
            threading.Event(),
        )
    )

    assert result == "success"
    assert ensured == ["world"]
    assert runner.status()["phase"] == "daily_boss_wait_mozu_world_transition"
