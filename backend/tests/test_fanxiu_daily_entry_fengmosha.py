from __future__ import annotations

import threading
from pathlib import Path

from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
    create_behavior_tree_executor,
)


def _drain(runner, generator):
    del runner
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


class Runtime:
    def __init__(self, *, start_scene: int) -> None:
        self.scene = start_scene
        self.actions: list[tuple] = []

    def sample_scene_once(self, scene_ids=None, **_kwargs):
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

    def wait_scene(self, layer0, **_kwargs):
        scene_ids = tuple(layer0)
        self.actions.append(("wait_scene", tuple(scene_ids), self.scene))
        assert self.scene in scene_ids
        if False:
            yield None
        return SceneMatch(self.scene)

    def wait_action_settle(self, *_args, **_kwargs):
        if False:
            yield None
        return None

    def go_scene(self, scene_id):
        self.actions.append(("go_scene", scene_id, self.scene))
        assert scene_id == 69
        assert self.scene in {34, 401}
        self.scene = 69
        if False:
            yield None
        return 69

    def enter_daily_list_direct(self, **kwargs):
        self.actions.append(("enter_daily_list_direct", self.scene, kwargs.get("label")))
        assert self.scene in {34, 661}
        self.scene = 69
        if False:
            yield None
        return {"terminal_scene": 69, "attempts": 1}


class SceneMatch:
    def __init__(self, scene_id: int) -> None:
        self.scene_id = scene_id
        self.score = 100.0
        self.frame_data_url = f"frame-{scene_id}"

    def __int__(self) -> int:
        return self.scene_id


def test_daily_entry_locally_closes_fengmosha_cover_then_reenters_daily(
    monkeypatch,
) -> None:
    runner = create_behavior_tree_executor()
    runtime = Runtime(start_scene=477)
    ctx = {
        "entry": object(),
        "asset_tree_path": Path("asset-tree.json"),
        "images": {34: {"id": 34, "title": "世界", "shapes": []}},
    }
    monkeypatch.setattr(runner, "_behavior_tree_context", lambda *_args, **_kwargs: runtime)

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
    assert [(a[0], *a[1:3]) for a in runtime.actions if a[0] in {"wait_click", "enter_daily_list_direct"}] == [
        ("wait_click", 477, "返回"),
        ("wait_click", 66, "返回"),
        ("enter_daily_list_direct", 34, "日常_测试"),
    ]
    assert ("wait_scene", (66, 34), 66) in runtime.actions
    assert ("wait_scene", (34,), 34) in runtime.actions


def test_daily_entry_can_resume_from_schedule_after_cover_was_already_closed(
    monkeypatch,
) -> None:
    runner = create_behavior_tree_executor()
    runtime = Runtime(start_scene=66)
    ctx = {
        "entry": object(),
        "asset_tree_path": Path("asset-tree.json"),
        "images": {34: {"id": 34, "title": "世界", "shapes": []}},
    }
    monkeypatch.setattr(runner, "_behavior_tree_context", lambda *_args, **_kwargs: runtime)

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
    assert ("enter_daily_list_direct", 34, "日常_测试") in runtime.actions


def test_daily_entry_treats_scene_661_as_passive_overlay(monkeypatch) -> None:
    runner = create_behavior_tree_executor()
    runtime = Runtime(start_scene=661)
    ctx = {
        "entry": object(),
        "asset_tree_path": Path("asset-tree.json"),
        "images": {34: {"id": 34, "title": "世界", "shapes": []}},
    }
    monkeypatch.setattr(runner, "_behavior_tree_context", lambda *_args, **_kwargs: runtime)

    result = _drain(
        runner,
        runner._enter_daily_from_world_like(
            ctx,
            runtime,
            threading.Event(),
            "frame-661",
            661,
            "进入",
            label="洞天_座位",
        ),
    )

    assert result == 69
    assert ("enter_daily_list_direct", 661, "洞天_座位") in runtime.actions
    assert not any(action[0] == "wait_click" for action in runtime.actions)


def test_daily_entry_normalizes_known_non_daily_scene_via_scene_graph(
    monkeypatch,
) -> None:
    runner = create_behavior_tree_executor()
    runtime = Runtime(start_scene=401)
    ctx = {
        "entry": object(),
        "asset_tree_path": Path("asset-tree.json"),
        "images": {34: {"id": 34, "title": "世界", "shapes": []}},
    }
    monkeypatch.setattr(runner, "_behavior_tree_context", lambda *_args, **_kwargs: runtime)

    result = _drain(
        runner,
        runner._enter_daily_from_world_like(
            ctx,
            runtime,
            threading.Event(),
            "frame-401",
            401,
            "魔狱封阵",
            label="日常_首领",
        ),
    )

    assert result == 69
    assert ("go_scene", 69, 401) in runtime.actions
    assert not any(action[0] == "enter_daily_list_direct" for action in runtime.actions)


def test_daily_boss_retries_entry_once_after_popup_returns_to_daily(
    monkeypatch,
) -> None:
    runner = create_behavior_tree_executor()
    calls: list[tuple] = []
    landings = iter(("daily", "success"))

    class DailyEntryContext:
        def open_daily_entry(self, **kwargs):
            calls.append(("open", kwargs["label"], kwargs["max_scrolls"]))
            if False:
                yield None
            return "open"

    context = DailyEntryContext()
    monkeypatch.setattr(runner, "_behavior_tree_context", lambda *_args, **_kwargs: context)

    def fake_wait(_ctx, _stop_event, **kwargs):
        calls.append(("wait", kwargs["return_on_daily_list"]))
        if False:
            yield None
        return next(landings)

    monkeypatch.setattr(runner, "_wait_daily_boss_list", fake_wait)

    result = _drain(
        runner,
        runner._open_daily_boss_list_from_daily(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
            {},
        ),
    )

    assert result == "success"
    assert calls == [
        ("open", "日常_首领", 10),
        ("wait", True),
        ("open", "日常_首领", 10),
        ("wait", False),
    ]
