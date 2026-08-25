from __future__ import annotations

import threading
from pathlib import Path

from backend.core.fanxiu.data_annotation.behavior_tree_control import (
    create_behavior_tree_runtime_runner,
)


def _drain(runner, generator):
    return runner._run_direct_runtime_action(
        lambda: generator,
        stop_event=threading.Event(),
        tick_seconds=0.001,
    )


class Runtime:
    def __init__(self, *, start_scene: int) -> None:
        self.scene = start_scene
        self.actions: list[tuple] = []

    def current_scene(self, scene_ids=None, **_kwargs):
        self.actions.append(("current_scene", tuple(scene_ids or ()), self.scene))
        return self.scene, 100.0, f"frame-{self.scene}"

    def ocr_text(self, frame=None, *_args, **_kwargs):
        if frame == "frame-69":
            return "日常 活跃度"
        if frame == "frame-66":
            return "日程"
        if frame == "frame-477":
            return "秘境封魔杀"
        return "世界 储物袋 角色 装备 功法书 大地图"

    def wait_click(self, scene_id, title, **_kwargs):
        self.actions.append(("wait_click", scene_id, title))
        assert scene_id == self.scene
        if (scene_id, title) == (477, "返回"):
            self.scene = 66
        elif (scene_id, title) == (66, "返回"):
            self.scene = 34
        else:
            raise AssertionError(f"unexpected click: {(scene_id, title)}")
        if False:
            yield None
        return self.scene

    def wait_view(self, *scene_ids, **_kwargs):
        self.actions.append(("wait_view", tuple(scene_ids), self.scene))
        assert self.scene in scene_ids
        if False:
            yield None
        return self.scene

    def wait_action_settle(self, *_args, **_kwargs):
        if False:
            yield None
        return None

    def goto_view(self, scene_id):
        self.actions.append(("goto_view", scene_id, self.scene))
        assert self.scene == 34
        assert scene_id == 69
        self.scene = 69
        if False:
            yield None
        return 69


def test_daily_entry_locally_closes_fengmosha_cover_then_reenters_daily(
    monkeypatch,
) -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = Runtime(start_scene=477)
    ctx = {
        "entry": object(),
        "asset_tree_path": Path("asset-tree.json"),
        "images": {34: {"id": 34, "title": "世界", "shapes": []}},
    }
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)

    result = _drain(
        runner,
        runner._enter_daily_from_world_like(
            ctx,
            runtime,
            threading.Event(),
            "frame-477",
            477,
            "秘境封魔杀",
            label="日常_测试",
        ),
    )

    assert result == 69
    assert ctx["_go_scene_known_scene_id"] == 34
    assert [(a[0], *a[1:3]) for a in runtime.actions if a[0] in {"wait_click", "goto_view"}] == [
        ("wait_click", 477, "返回"),
        ("wait_click", 66, "返回"),
        ("goto_view", 69, 34),
    ]
    assert ("wait_view", (66, 34), 66) in runtime.actions
    assert ("wait_view", (34,), 34) in runtime.actions


def test_daily_entry_can_resume_from_schedule_after_cover_was_already_closed(
    monkeypatch,
) -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = Runtime(start_scene=66)
    ctx = {
        "entry": object(),
        "asset_tree_path": Path("asset-tree.json"),
        "images": {34: {"id": 34, "title": "世界", "shapes": []}},
    }
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)

    result = _drain(
        runner,
        runner._enter_daily_from_world_like(
            ctx,
            runtime,
            threading.Event(),
            "frame-66",
            66,
            "日程",
            label="日常_测试",
        ),
    )

    assert result == 69
    assert ("wait_click", 66, "返回") in runtime.actions
    assert ("goto_view", 69, 34) in runtime.actions
