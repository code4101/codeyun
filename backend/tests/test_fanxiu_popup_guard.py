from __future__ import annotations

from pyxllib.autogui import View
import pytest

from backend.core.fanxiu.data_annotation import behavior_tree_runtime as behavior_tree_runtime_core
from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner


def test_scene_interruption_handles_scene_393_by_clicking_avatar():
    runner = create_behavior_tree_runtime_runner()
    view = View({
        "type": "image",
        "title": "0393.png",
        "filename": "0393.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "kind": "shape",
                "title": "分身",
                "isSceneIdentity": True,
                "sceneIdentityRole": "required",
                "ocrMatchRole": "required",
                "ocrText": "分身",
                "x": 0.23,
                "y": 0.66,
                "w": 0.18,
                "h": 0.04,
            }
        ],
    })
    clicks: list[tuple[int | None, str, str]] = []

    class Runtime:
        attrs: dict[str, object] = {}

        def cur_frame(self, **_kwargs):
            return "frame-393"

        def click_shape(self, target_view, shape, *, frame_data_url=None):
            clicks.append((target_view.id, shape.title, frame_data_url))

    handled = runner._handle_recognized_popup_candidate(
        Runtime(),
        {"image": view.raw, "folder_path": "弹窗", "action_shape": None},
        score=100.0,
    )

    assert handled is True
    assert clicks == [(393, "分身", "frame-393")]
    assert runner.status()["last_guard_event"]["action"] == "click:分身"
    assert runner.status()["current_scene"] == 393


def test_scene_interruption_scene_433_clicks_safe_return():
    runner = create_behavior_tree_runtime_runner()
    popup = _scene(433, "游戏更新提示", [
        {
            "id": "restart",
            "title": "重启",
            "isSceneIdentity": True,
            "sceneIdentityRole": "required",
            "ocrMatchRole": "required",
            "ocrText": "重启",
        },
        {"id": "return", "title": "返回"},
    ])
    clicks: list[tuple[int | None, str, str]] = []

    class Runtime:
        def cur_frame(self, **_kwargs):
            return "frame-433"

        def click_shape(self, target_view, shape, *, frame_data_url=None):
            clicks.append((target_view.id, shape.title, frame_data_url))

    candidates = runner._auto_close_guard_images([
        {"type": "folder", "title": "弹窗", "children": [popup]},
    ])

    assert len(candidates) == 1
    assert candidates[0]["action_shape"]["title"] == "返回"

    handled = runner._handle_recognized_popup_candidate(
        Runtime(),
        candidates[0],
        score=100.0,
    )

    assert handled is True
    assert clicks == [(433, "返回", "frame-433")]
    assert runner.status()["last_guard_event"]["action"] == "click:返回"
    assert runner.status()["current_scene"] == 433


def test_scene_433_without_safe_action_is_not_an_interruption_candidate():
    runner = create_behavior_tree_runtime_runner()
    popup = _scene(433, "游戏更新提示", [{
        "id": "restart",
        "title": "重启",
        "isSceneIdentity": True,
        "sceneIdentityRole": "required",
        "ocrMatchRole": "required",
        "ocrText": "重启",
    }])

    candidates = runner._auto_close_guard_images([
        {"type": "folder", "title": "弹窗", "children": [popup]},
    ])

    assert candidates == []


def test_scene_546_can_opt_into_global_interruption_without_close_action():
    runner = create_behavior_tree_runtime_runner()
    maintenance = _scene(546, "登录维护提示", [{
        "id": "maintenance-message",
        "title": "维护正文",
        "isSceneIdentity": True,
        "sceneIdentityRole": "required",
        "ocrMatchRole": "required",
        "ocrText": "停更码字中，敬请期待更新",
    }])
    maintenance["runtimeInterruption"] = True

    candidates = runner._auto_close_guard_images([
        {"type": "folder", "title": "日常", "children": [maintenance]},
    ])

    assert [runner._image_number(item["image"]) for item in candidates] == [546]
    assert candidates[0]["action_shape"] is None


def _scene(scene_id: int, title: str, shapes: list[dict]) -> dict:
    return {
        "type": "image",
        "title": title,
        "filename": f"{scene_id:04d}.png",
        "width": 900,
        "height": 1600,
        "shapes": shapes,
    }


def test_current_scene_handles_popup_then_repeats_same_business_layer0(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    popup = _scene(47, "所有提示窗口", [
        {"id": "popup-id", "isSceneIdentity": True},
        {"id": "blank", "title": "空白"},
    ])
    world = _scene(34, "世界", [{"id": "world-id", "isSceneIdentity": True}])
    runtime = runner._fanxiu_runtime(
        {"entry": object(), "images": {34: world, 47: popup}},
        frame_data_url="frame-popup",
    )
    runtime.candidates = [{"image": popup, "folder_path": "弹窗", "action_shape": popup["shapes"][1]}]
    results = iter([(47, 100.0), (34, 100.0)])
    preferred_calls: list[list[int] | None] = []
    handled: list[int] = []

    def identify(_ctx, _frame, preferred=None):
        preferred_calls.append(preferred)
        return next(results)

    monkeypatch.setattr(runner, "_identify_scene_number", identify)
    monkeypatch.setattr(
        runner,
        "_handle_recognized_popup_candidate",
        lambda _runtime, candidate, **_kwargs: handled.append(runner._image_number(candidate["image"])) or True,
    )
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame-business")
    monkeypatch.setattr(behavior_tree_runtime_core.time, "sleep", lambda _seconds: None)

    assert runtime.current_scene([34], handle_interruptions=True)[:2] == (34, 100.0)
    assert handled == [47]
    assert preferred_calls == [[34, 47], [34, 47]]


def test_current_scene_leaves_explicit_business_popup_to_business_state_machine(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    popup = _scene(47, "所有提示窗口", [
        {"id": "popup-id", "isSceneIdentity": True},
        {"id": "blank", "title": "空白"},
    ])
    runtime = runner._fanxiu_runtime(
        {"entry": object(), "images": {47: popup}},
        frame_data_url="frame-popup",
    )
    runtime.candidates = [{"image": popup, "folder_path": "弹窗", "action_shape": popup["shapes"][1]}]
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (47, 100.0))
    monkeypatch.setattr(
        runner,
        "_handle_recognized_popup_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("业务显式等待的弹窗不得被通用中断动作消费")
        ),
    )

    assert runtime.current_scene([47], handle_interruptions=True)[:2] == (47, 100.0)


def test_actionless_popup_scene_is_not_an_interruption_candidate():
    runner = create_behavior_tree_runtime_runner()
    actionless = _scene(54, "退出道场", [{"id": "identity", "isSceneIdentity": True}])
    executable = _scene(47, "所有提示窗口", [
        {"id": "identity", "isSceneIdentity": True},
        {"id": "blank", "title": "空白"},
    ])

    candidates = runner._auto_close_guard_images([
        {"type": "folder", "title": "弹窗", "children": [actionless, executable]},
    ])

    assert [runner._image_number(item["image"]) for item in candidates] == [47]
