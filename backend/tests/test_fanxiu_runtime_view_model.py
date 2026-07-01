import threading

import pytest

from backend.core.fanxiu.runtime.behavior_tree import create_fanxiu_runtime_runner, ensure_fanxiu_runtime_jobs_registered
from backend.core.fanxiu.data_annotation.runtime_runner import FanxiuRuntime
from pyxllib.prog import BehaviorTreeStatus
from pyxllib.autogui import (
    MatchRole,
    Shape,
    View,
    flatten_shapes,
    image_number,
    index_images,
    normalize_frame_layer,
    normalize_match_role,
)


def test_match_role_normalizes_legacy_and_new_values():
    assert normalize_match_role("off") is MatchRole.off
    assert normalize_match_role("required") is MatchRole.required
    assert normalize_match_role("decisive") is MatchRole.decisive
    assert normalize_match_role("无") is MatchRole.off
    assert normalize_match_role("必") is MatchRole.required
    assert normalize_match_role("定") is MatchRole.decisive
    assert normalize_match_role(True) is MatchRole.required


def test_view_wraps_existing_asset_tree_shape_data_without_copying():
    image = {
        "type": "image",
        "title": "报名",
        "filename": "0023.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "id": "group",
                "kind": "group",
                "title": "组",
                "children": [
                    {
                        "id": "back",
                        "kind": "shape",
                        "title": "返回",
                        "x": 0.1,
                        "y": 0.2,
                        "w": 0.3,
                        "h": 0.4,
                    }
                ],
            }
        ],
    }

    view = View(image)
    shape = view.get_shape("返回")

    assert view.id == 23
    assert shape is not None
    assert shape.raw is image["shapes"][0]["children"][0]
    assert shape.parent_view == view
    assert shape.box() == {"name": "返回", "x": 90.0, "y": 320.0, "w": 270.0, "h": 640.0}


def test_view_scene_identity_uses_match_role_model():
    image = {
        "type": "image",
        "filename": "0121.png",
        "shapes": [
            {"id": "old", "kind": "shape", "title": "旧标识", "isSceneIdentity": True},
            {"id": "new", "kind": "shape", "title": "新标识", "sceneIdentityRole": "decisive"},
            {"id": "off", "kind": "shape", "title": "普通按钮", "sceneIdentityRole": "off"},
        ],
    }

    identities = [shape for shape in View(image).get_shapes(include_groups=False) if shape.is_scene_identity]

    assert [shape.title for shape in identities] == ["旧标识", "新标识"]
    assert identities[0].scene_identity_role is MatchRole.required
    assert identities[1].scene_identity_role is MatchRole.decisive


def test_view_layer_derives_from_primary_marker_and_scene_identity():
    layer2_image = {
        "type": "image",
        "filename": "0266.png",
        "shapes": [{"id": "local", "title": "拜谒", "isSceneIdentity": True, "sceneIdentityScope": "local"}],
    }
    layer1_image = {
        "type": "image",
        "filename": "0034.png",
        "layer": 1,
        "shapes": [{"id": "global", "title": "世界", "isSceneIdentity": True, "sceneIdentityScope": "global"}],
    }
    default_image = {
        "type": "image",
        "filename": "0001.png",
        "shapes": [{"id": "button", "title": "普通按钮"}],
    }

    assert normalize_frame_layer("layer1") == 1
    assert normalize_frame_layer("2") == 2
    assert View(layer2_image).layer == 2
    assert View(layer1_image).layer == 1
    assert View(default_image).layer == 3


def test_view_close_keeps_action_on_runtime_side():
    image = {
        "type": "image",
        "filename": "0047.png",
        "shapes": [
            {"id": "confirm", "kind": "shape", "title": "确认"},
            {"id": "blank", "kind": "shape", "title": "空白"},
        ],
    }
    calls = []

    class FakeRuntime:
        def click_shape(self, view, shape):
            calls.append((view.id, shape.title))
            return "clicked"

    assert View(image).close(FakeRuntime()) == "clicked"
    assert calls == [(47, "空白")]


def test_asset_tree_helpers_match_runner_compatibility():
    tree = [
        {
            "type": "folder",
            "title": "日常",
            "children": [
                {
                    "type": "image",
                    "filename": "0069.png",
                    "shapes": [{"id": "shape", "title": "日常"}],
                }
            ],
        }
    ]

    runner = create_fanxiu_runtime_runner()

    assert image_number(tree[0]["children"][0]) == 69
    assert index_images(tree) == {69: tree[0]["children"][0]}
    assert flatten_shapes(tree[0]["children"][0]["shapes"]) == [{"id": "shape", "title": "日常"}]
    assert runner._image_number(tree[0]["children"][0]) == 69
    assert runner._index_images(tree) == {69: tree[0]["children"][0]}
    assert runner._flatten_shapes(tree[0]["children"][0]["shapes"]) == [{"id": "shape", "title": "日常"}]


def test_view_close_reports_missing_close_shape():
    with pytest.raises(RuntimeError, match="缺少可关闭标注"):
        View({"type": "image", "filename": "0001.png", "shapes": []}).close(object())


def test_fanxiu_runtime_shape_load_uses_existing_drag_action(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "width": 100,
        "height": 200,
        "shapes": [
            {"id": "list", "kind": "rect", "title": "邮件清单2", "x": 0.1, "y": 0.2, "w": 0.4, "h": 0.5, "contentDirection": "down"}
        ],
    }
    shape = View(image).get_shape("邮件清单2")
    dragged = []
    sleeps = []
    monkeypatch.setattr("pyxllib.autogui.runtime.time.sleep", lambda seconds: sleeps.append(seconds))

    monkeypatch.setattr(
        runner,
        "_drag_frame_point",
        lambda _ctx, _image, sx, sy, ex, ey, **kwargs: dragged.append((_image["title"], sx, sy, ex, ey, kwargs.get("duration_ms"))),
    )

    runtime = runner._fanxiu_runtime({"entry": object()})

    assert shape is not None
    assert list(shape.load(runtime, ratio=0.5, duration=1.5)) == [1]
    assert dragged == [("邮件", 30.0, 115.0, 30.0, 65.0, 1500)]
    assert sleeps == [2.0]
    assert runtime.attrs["load_new"] is True


def test_fanxiu_runtime_wait_view_uses_scene_recognition(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image121 = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "shapes": [{"id": "identity", "kind": "rect", "title": "邮件标识", "isSceneIdentity": True}],
    }
    ctx = {"entry": object(), "images": {121: image121}}
    preferred_calls = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: 0.0)

    def identify(_ctx, _frame, preferred):
        preferred_calls.append(preferred)
        return 121, 96.0

    monkeypatch.setattr(runner, "_identify_scene_number", identify)

    waiter = runner._fanxiu_runtime(ctx).wait_view(121, timeout=3.0, label="等待邮件")

    assert next(waiter) is BehaviorTreeStatus.RUNNING
    with pytest.raises(StopIteration) as stop:
        next(waiter)
    assert stop.value.value.raw is image121
    assert preferred_calls == [[121]]


def test_fanxiu_runtime_wait_scene_uses_scene_api_source(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image121 = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "shapes": [{"id": "identity", "kind": "rect", "title": "邮件标识", "isSceneIdentity": True}],
    }
    ctx = {"entry": object(), "images": {121: image121}}
    preferred_calls = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: 0.0)

    def identify(_ctx, _frame, preferred):
        preferred_calls.append(preferred)
        return 121, 96.0

    monkeypatch.setattr(runner, "_identify_scene_number", identify)

    runtime = runner._fanxiu_runtime(ctx)
    emitted_actions = []
    monkeypatch.setattr(runtime, "_emit_runtime_action", lambda message, **kwargs: emitted_actions.append((message, kwargs)))

    waiter = runtime.wait_scene(121, timeout=3.0, label="等待邮件")

    assert next(waiter) is BehaviorTreeStatus.RUNNING
    with pytest.raises(StopIteration) as stop:
        next(waiter)
    assert stop.value.value.raw is image121
    assert preferred_calls == [[121]]
    assert emitted_actions[0][1]["phase"] == "runtime_wait_scene"
    assert emitted_actions[0][1]["source_info"]["action"] == "wait_scene"


def test_fanxiu_runtime_wait_scene_without_targets_returns_current_scene(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {"entry": object(), "images": {}}
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, preferred: (34, 91.0))

    waiter = runner._fanxiu_runtime(ctx).wait_scene()

    with pytest.raises(StopIteration) as stop:
        next(waiter)
    assert stop.value.value == (34, 91.0, "frame")


def test_fanxiu_runtime_wait_scene_falls_back_to_global_after_layer0_window(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image121 = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "shapes": [{"id": "identity", "kind": "rect", "title": "邮件标识", "isSceneIdentity": True}],
    }
    ctx = {"entry": object(), "images": {121: image121}}
    preferred_calls = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: 0.0)

    def identify(_ctx, _frame, preferred):
        preferred_calls.append(preferred)
        if preferred is None:
            return 121, 96.0
        return None, 0.0

    monkeypatch.setattr(runner, "_identify_scene_number", identify)
    waiter = runner._fanxiu_runtime(ctx).wait_scene(121, timeout=1.0, layer0_wait_seconds=0, label="等待邮件")

    for _ in range(100):
        try:
            assert next(waiter) is BehaviorTreeStatus.RUNNING
        except StopIteration as stop:
            assert stop.value.raw is image121
            break
    else:
        raise AssertionError("wait_scene did not finish")

    assert None in preferred_calls


def test_fanxiu_runtime_wait_view_prefers_view_identity_match(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image68 = {
        "type": "image",
        "title": "世界-下方动态",
        "filename": "0068.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "id": "mail",
                "kind": "rect",
                "title": "邮件",
                "sceneIdentityRole": "decisive",
                "imageMatchRole": "required",
                "x": 0.38,
                "y": 0.86,
                "w": 0.07,
                "h": 0.04,
            }
        ],
    }
    ctx = {"entry": object(), "images": {68: image68}}

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: runner.overlay_threshold + 1)
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("wait_view should use View.is_match first")),
    )

    waiter = runner._fanxiu_runtime(ctx).wait_view(68, timeout=1.0, label="等待动态栏")

    assert next(waiter) is BehaviorTreeStatus.RUNNING
    with pytest.raises(StopIteration) as stop:
        next(waiter)
    assert stop.value.value.raw is image68


def test_fanxiu_runtime_wait_view_timeout(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {"entry": object(), "images": {}}
    monotonic_values = iter([0.0, 0.2, 0.2, 0.5, 0.5, 0.8, 0.8, 1.2, 1.2])

    def monotonic_time():
        return next(monotonic_values, 1.2)

    monkeypatch.setattr("backend.core.fanxiu.data_annotation.runtime_runner.time.monotonic", monotonic_time)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (None, 0.0))

    waiter = runner._fanxiu_runtime(ctx).wait_view(121, timeout=1.0, label="等待邮件")

    assert next(waiter) is BehaviorTreeStatus.RUNNING
    assert next(waiter) is BehaviorTreeStatus.RUNNING
    with pytest.raises(TimeoutError, match="等待邮件 超时"):
        next(waiter)


def test_fanxiu_runtime_find_view_empty_group_uses_top_level_identity_only(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image34 = {
        "type": "image",
        "title": "世界",
        "filename": "0034.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "id": "panel",
                "kind": "rect",
                "title": "面板",
                "children": [
                    {"id": "nested", "kind": "rect", "title": "子标识", "sceneIdentityRole": "decisive"},
                ],
            }
        ],
    }
    image35 = {
        "type": "image",
        "title": "世界下方菜单",
        "filename": "0035.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "menu", "kind": "rect", "title": "菜单", "sceneIdentityRole": "decisive"}],
    }
    runtime = runner._fanxiu_runtime({"entry": object(), "images": {34: image34, 35: image35}})

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_shape_score", lambda _ctx, _image, shape, *_args, **_kwargs: runner.overlay_threshold + 1 if shape.get("id") in {"nested", "menu"} else 0.0)

    assert View(image34).is_match(runtime) is True
    assert runtime.find_view("").raw is image35


def test_mail_cleanup_entry_prefers_view68_mail(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    image34 = {
        "type": "image",
        "title": "世界",
        "filename": "0034.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "open", "kind": "rect", "title": "打开下方菜单", "x": 0.8, "y": 0.9, "w": 0.1, "h": 0.05}],
    }
    image68 = {
        "type": "image",
        "title": "世界-下方动态",
        "filename": "0068.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "mail68", "kind": "rect", "title": "邮件", "x": 0.3, "y": 0.8, "w": 0.1, "h": 0.05}],
    }
    image121 = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "identity", "kind": "rect", "title": "邮件标识", "sceneIdentityRole": "decisive"}],
    }
    ctx = {"entry": object(), "asset_tree_path": tmp_path / "asset_tree.json", "images": {34: image34, 68: image68, 121: image121}}
    clicked = []

    monkeypatch.setattr(runner, "_go_scene_task", lambda _ctx, _path, scene_id, _stop: "success" if scene_id == 34 else "unexpected")
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_match_shape", lambda *_args, **_kwargs: {"matched": True, "similarity": 100, "matches": []})
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, *_args, **_kwargs: clicked.append((image["title"], shape["title"])))
    monkeypatch.setattr(runner, "_close_mail_wait_popup_once", lambda *_args, **_kwargs: False)

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    runtime = runner._fanxiu_runtime(ctx, ctx["asset_tree_path"])
    result = runner._run_direct_runtime_action(
        lambda: runner._open_mail_cleanup_entry(runtime),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [("世界-下方动态", "邮件")]


def test_mail_cleanup_entry_falls_back_to_view35_mail(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    image34 = {
        "type": "image",
        "title": "世界",
        "filename": "0034.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "open", "kind": "rect", "title": "打开下方菜单", "x": 0.8, "y": 0.9, "w": 0.1, "h": 0.05}],
    }
    image68 = {
        "type": "image",
        "title": "世界-下方动态",
        "filename": "0068.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "mail68", "kind": "rect", "title": "邮件", "x": 0.3, "y": 0.8, "w": 0.1, "h": 0.05}],
    }
    image35 = {
        "type": "image",
        "title": "世界下方菜单",
        "filename": "0035.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "id": "mail35",
                "kind": "rect",
                "title": "邮件",
                "floating": True,
                "imageMatchRole": "optional",
                "ocrEnabled": True,
                "ocrText": "邮",
                "ocrMatchRole": "optional",
                "ocrMatchMode": "contains",
                "x": 0.5,
                "y": 0.8,
                "w": 0.1,
                "h": 0.08,
            }
        ],
    }
    image121 = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "identity", "kind": "rect", "title": "邮件标识", "sceneIdentityRole": "decisive"}],
    }
    ctx = {"entry": object(), "asset_tree_path": tmp_path / "asset_tree.json", "images": {34: image34, 68: image68, 35: image35, 121: image121}}
    clicked = []

    monkeypatch.setattr(runner, "_go_scene_task", lambda _ctx, _path, scene_id, _stop: "success" if scene_id == 34 else "unexpected")
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")

    def match_shape(_ctx, _image, shape, *_args, **_kwargs):
        return {"matched": shape.get("id") != "mail68", "similarity": 0 if shape.get("id") == "mail68" else 100, "matches": []}

    monkeypatch.setattr(runner, "_match_shape", match_shape)
    monkeypatch.setattr(runner, "_shape_score", lambda _ctx, _image, shape, *_args, **_kwargs: 0.0 if shape.get("id") == "mail68" else runner.scene_threshold + 1)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, *_args, **_kwargs: clicked.append((image["title"], shape["title"])))
    monkeypatch.setattr(runner, "_close_mail_wait_popup_once", lambda *_args, **_kwargs: False)

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    runtime = runner._fanxiu_runtime(ctx, ctx["asset_tree_path"])
    result = runner._run_direct_runtime_action(
        lambda: runner._open_mail_cleanup_entry(runtime),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [("世界下方菜单", "邮件")]


def test_mail_cleanup_entry_refreshes_frame_after_opening_world_menu(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    image34 = {
        "type": "image",
        "title": "世界",
        "filename": "0034.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "open", "kind": "rect", "title": "打开下方菜单", "x": 0.8, "y": 0.9, "w": 0.1, "h": 0.05}],
    }
    image68 = {
        "type": "image",
        "title": "世界-下方动态",
        "filename": "0068.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "mail68", "kind": "rect", "title": "邮件", "x": 0.3, "y": 0.8, "w": 0.1, "h": 0.05}],
    }
    image35 = {
        "type": "image",
        "title": "世界下方菜单",
        "filename": "0035.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "id": "mail35",
                "kind": "rect",
                "title": "邮件",
                "floating": True,
                "imageMatchRole": "optional",
                "ocrEnabled": True,
                "ocrText": "邮",
                "ocrMatchRole": "optional",
                "ocrMatchMode": "contains",
                "x": 0.5,
                "y": 0.8,
                "w": 0.1,
                "h": 0.08,
            }
        ],
    }
    image121 = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "identity", "kind": "rect", "title": "邮件标识", "sceneIdentityRole": "decisive"}],
    }
    ctx = {"entry": object(), "asset_tree_path": tmp_path / "asset_tree.json", "images": {34: image34, 68: image68, 35: image35, 121: image121}}
    frames = []

    monkeypatch.setattr(runner, "_go_scene_task", lambda _ctx, _path, scene_id, _stop: "success" if scene_id == 34 else "unexpected")
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: f"frame{len(frames)}")

    def match_shape(_ctx, _image, shape, frame, *, condition="auto"):
        frames.append((shape.get("title"), frame, condition))
        if shape.get("title") == "邮件标识":
            return {"matched": True, "similarity": 100, "matches": []}
        return {
            "matched": shape.get("title") == "邮件" and frame != "frame0" and condition == "ocr",
            "similarity": 100 if frame != "frame0" and condition == "ocr" else 0,
            "matches": [{"text": "邮"}] if frame != "frame0" and condition == "ocr" else [],
            "fixed_box": {"name": shape.get("title"), "x": 450, "y": 1450, "w": 90, "h": 110},
            "resolved_box": {"name": shape.get("title"), "x": 450, "y": 1450, "w": 90, "h": 110},
        }

    monkeypatch.setattr(runner, "_match_shape", match_shape)
    monkeypatch.setattr(runner, "_click_shape", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (121, 95.0))
    monkeypatch.setattr(runner, "_close_mail_wait_popup_once", lambda *_args, **_kwargs: False)

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    runtime = runner._fanxiu_runtime(ctx, ctx["asset_tree_path"])
    result = runner._run_direct_runtime_action(
        lambda: runner._open_mail_cleanup_entry(runtime),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert any(title == "邮件" and frame != "frame0" for title, frame, _condition in frames)


def test_mail_cleanup_entry_fixed_clicks_mail_after_ocr_timeout(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    image34 = {
        "type": "image",
        "title": "世界",
        "filename": "0034.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "open", "kind": "rect", "title": "打开下方菜单", "x": 0.8, "y": 0.9, "w": 0.1, "h": 0.05}],
    }
    image68 = {"type": "image", "title": "世界-下方动态", "filename": "0068.png", "width": 900, "height": 1600, "shapes": []}
    image35 = {
        "type": "image",
        "title": "世界下方菜单",
        "filename": "0035.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "id": "mail35",
                "kind": "rect",
                "title": "邮件",
                "floating": True,
                "sceneJumpTarget": "121(1)",
                "imageMatchRole": "optional",
                "ocrEnabled": True,
                "ocrText": "邮",
                "ocrMatchRole": "optional",
                "ocrMatchMode": "contains",
                "x": 0.5,
                "y": 0.9,
                "w": 0.1,
                "h": 0.08,
            }
        ],
    }
    image121 = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "identity", "kind": "rect", "title": "邮件标识", "sceneIdentityRole": "decisive"}],
    }
    ctx = {"entry": object(), "asset_tree_path": tmp_path / "asset_tree.json", "images": {34: image34, 68: image68, 35: image35, 121: image121}}
    fixed_clicks = []

    def timeout_wait_click(self, view, shape, *, timeout=None, interval_action=1):
        assert timeout == 8.0
        raise TimeoutError("等待点击超时：邮件")
        yield

    monkeypatch.setattr(FanxiuRuntime, "wait_click_shape", timeout_wait_click)
    monkeypatch.setattr(runner, "_go_scene_task", lambda _ctx, _path, scene_id, _stop: "success" if scene_id == 34 else "unexpected")
    def fixed_match_shape(_ctx, _image, shape, *_args, **_kwargs):
        if shape.get("title") == "邮件标识":
            return {"matched": True, "similarity": 100, "matches": []}
        return {"matched": False, "similarity": 0, "matches": []}

    monkeypatch.setattr(runner, "_match_shape", fixed_match_shape)
    monkeypatch.setattr(runner, "_click_shape", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, image, x, y: fixed_clicks.append((image["title"], x, y)))
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (121, 95.0))
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(runner, "_close_mail_wait_popup_once", lambda *_args, **_kwargs: False)

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

        def set(self):
            return None

    runtime = runner._fanxiu_runtime(ctx, ctx["asset_tree_path"])
    result = runner._run_direct_runtime_action(
        lambda: runner._open_mail_cleanup_entry(runtime),
        stop_event=FakeStopEvent(),
        max_runtime_seconds=15,
        tick_seconds=0.01,
    )

    assert result == "success"
    assert fixed_clicks == [("世界下方菜单", 495.0, 1539.84)]
    assert any("#35「邮件」未命中" in log["message"] for log in runner.status()["logs"])


def test_runtime_view_open_world_menu_falls_back_to_fixed_click(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image34 = {
        "type": "image",
        "title": "世界",
        "filename": "0034.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "open", "kind": "rect", "title": "打开下方菜单", "x": 0.8, "y": 0.9, "w": 0.1, "h": 0.05}],
    }
    clicked = []

    def click_shape(*_args, **_kwargs):
        raise RuntimeError("未能按图像定位浮动按钮「打开下方菜单」")

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_click_shape", click_shape)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, image, x, y: clicked.append((image["title"], x, y)))

    runtime = runner._fanxiu_runtime({"entry": type("Entry", (), {"mode": "local"})(), "images": {34: image34}})
    View(image34).get_shape("打开下方菜单").click(runtime)

    assert clicked == [("世界", 765.0, 1480.0)]
    assert runtime.cur_frame() == "frame"


def test_runtime_view_mail_delete_falls_back_to_fixed_click(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image121 = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "delete-read", "kind": "rect", "title": "一键删除", "x": 0.2, "y": 0.8, "w": 0.2, "h": 0.08}],
    }
    clicked = []

    def click_shape(*_args, **_kwargs):
        raise RuntimeError("未能按图像定位浮动按钮「一键删除」")

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_click_shape", click_shape)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, image, x, y: clicked.append((image["title"], x, y)))

    runtime = runner._fanxiu_runtime({"entry": type("Entry", (), {"mode": "local"})(), "images": {121: image121}})
    View(image121).get_shape("一键删除").click(runtime)

    assert clicked == [("邮件", 270.0, 1344.0)]
    assert any("#121「一键删除」图像定位失败，改按固定标注点击" in log["message"] for log in runner.status()["logs"])


def test_fanxiu_runtime_wait_click_tries_ocr_after_image_miss(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image35 = {
        "type": "image",
        "title": "世界下方菜单",
        "filename": "0035.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "id": "mail35",
                "kind": "rect",
                "title": "邮件",
                "floating": True,
                "imageMatchRole": "decisive",
                "ocrEnabled": True,
                "ocrText": "邮",
                "ocrMatchRole": "decisive",
                "x": 0.5,
                "y": 0.9,
                "w": 0.1,
                "h": 0.08,
            }
        ],
    }
    shape = View(image35).get_shape("邮件")
    ctx = {"entry": type("Entry", (), {"mode": "local"})(), "images": {35: image35}}
    calls = []
    clicked = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")

    def match_shape(_ctx, _image, _shape, _frame, *, condition="auto"):
        calls.append(condition)
        if condition == "ocr":
            return {
                "matched": True,
                "similarity": 91,
                "ocr_text": "邮",
                "matches": [{"text": "邮"}],
                "fixed_box": {"name": "邮件", "x": 450, "y": 1450, "w": 90, "h": 110},
                "resolved_box": {"name": "邮件", "x": 450, "y": 1450, "w": 90, "h": 110},
            }
        return {
            "matched": False,
            "similarity": 0,
            "matches": [],
            "fixed_box": {"name": "邮件", "x": 450, "y": 1450, "w": 90, "h": 110},
        }

    monkeypatch.setattr(runner, "_match_shape", match_shape)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, _frame, **kwargs: clicked.append((image["title"], shape["title"], kwargs.get("match_result"))))

    runtime = runner._fanxiu_runtime(ctx)
    waiter = shape.wait_click(runtime)

    with pytest.raises(StopIteration):
        next(waiter)

    assert calls == ["image", "ocr"]
    assert clicked[0][0:2] == ("世界下方菜单", "邮件")
    assert clicked[0][2]["ocr_text"] == "邮"


def test_mail_entry_wait_click_prefers_ocr_before_image(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image35 = {
        "type": "image",
        "title": "世界下方菜单",
        "filename": "0035.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "id": "mail35",
                "kind": "rect",
                "title": "邮件",
                "floating": True,
                "sceneJumpTarget": "121(1)",
                "imageMatchRole": "optional",
                "ocrEnabled": True,
                "ocrText": "邮",
                "ocrMatchRole": "optional",
                "ocrMatchMode": "contains",
                "x": 0.5,
                "y": 0.9,
                "w": 0.1,
                "h": 0.08,
            }
        ],
    }
    shape = View(image35).get_shape("邮件")
    ctx = {"entry": type("Entry", (), {"mode": "local"})(), "images": {35: image35}}
    calls = []
    clicked = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")

    def match_shape(_ctx, _image, _shape, _frame, *, condition="auto"):
        calls.append(condition)
        return {
            "matched": condition == "ocr",
            "similarity": 100 if condition == "ocr" else 90,
            "ocr_text": "邮" if condition == "ocr" else "",
            "matches": [{"text": "邮"}] if condition == "ocr" else [],
            "fixed_box": {"name": "ocr", "x": 320, "y": 1380, "w": 80, "h": 50},
            "resolved_box": {"name": "ocr", "x": 320, "y": 1380, "w": 80, "h": 50},
        }

    monkeypatch.setattr(runner, "_match_shape", match_shape)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, _frame, **kwargs: clicked.append((image["title"], shape["title"], kwargs.get("match_result"))))

    runtime = runner._fanxiu_runtime(ctx)
    waiter = shape.wait_click(runtime)

    with pytest.raises(StopIteration):
        next(waiter)

    assert calls == ["ocr"]
    assert clicked[0][2]["ocr_text"] == "邮"


def test_shape_match_uses_full_frame_ocr_overlap_after_crop_ocr_miss(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image35 = {
        "type": "image",
        "title": "世界下方菜单",
        "filename": "0035.png",
        "width": 900,
        "height": 1600,
    }
    shape = {
        "id": "mail35",
        "kind": "rect",
        "title": "邮件",
        "floating": True,
        "imageMatchRole": "optional",
        "ocrEnabled": True,
        "ocrText": "邮",
        "ocrMatchRole": "optional",
        "x": 0.499,
        "y": 0.908,
        "w": 0.1,
        "h": 0.069,
    }
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}

    def run_match(_ctx, _image, _shape, _frame, *, scan=False, match_strategy="auto", ocr_enabled=False):
        return {
            "similarity": 0,
            "matches": [],
            "fixed_box": {"name": "邮件", "x": 449, "y": 1453, "w": 90, "h": 111},
        }

    monkeypatch.setattr(runner, "_run_match", run_match)
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
        lambda _frame: [{"text": "止清羊驼仙缘邮件设置", "x": 87, "y": 1526, "w": 560, "h": 43}],
    )

    result = runner._match_shape(ctx, image35, shape, "frame", condition="ocr")

    assert result["matched"] is True
    assert result["resolved_box"] == {"name": "邮件", "x": 449, "y": 1453, "w": 90, "h": 111}
    assert result["ocr_text"] == "止清羊驼仙缘邮件设置"


def test_shape_match_accepts_ocr_contains_even_when_similarity_is_low(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image35 = {
        "type": "image",
        "title": "世界下方菜单",
        "filename": "0035.png",
        "width": 900,
        "height": 1600,
    }
    shape = {
        "id": "mail35",
        "kind": "rect",
        "title": "邮件",
        "floating": True,
        "imageMatchRole": "optional",
        "ocrEnabled": True,
        "ocrText": "邮",
        "ocrMatchRole": "optional",
        "ocrMatchMode": "contains",
        "x": 0.499,
        "y": 0.908,
        "w": 0.1,
        "h": 0.069,
    }
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}

    def run_match(_ctx, _image, _shape, _frame, *, scan=False, match_strategy="auto", ocr_enabled=False):
        assert ocr_enabled is True
        return {
            "similarity": 63,
            "matches": [{"text": "邮传", "x": 459, "y": 1527, "w": 57, "h": 30}],
            "fixed_box": {"name": "ocr", "x": 459, "y": 1527, "w": 57, "h": 30},
        }

    monkeypatch.setattr(runner, "_run_match", run_match)

    result = runner._match_shape(ctx, image35, shape, "frame", condition="ocr")

    assert result["matched"] is True
    assert result["ocr_text"] == "邮传"
    assert result["resolved_box"] == {"name": "ocr", "x": 459, "y": 1527, "w": 57, "h": 30}


def test_mail_cleanup_uses_runtime_view_shape_flow(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    image121 = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"id": "list", "kind": "rect", "title": "邮件清单2", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6, "contentDirection": "down"}
        ],
    }
    image122 = {
        "type": "image",
        "title": "邮件详情",
        "filename": "0122.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "claim", "kind": "rect", "title": "领取", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.08}],
    }
    ctx = {
        "entry": object(),
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {121: image121, 122: image122},
    }
    clicked = []
    updates = []

    monkeypatch.setattr("backend.core.fanxiu.data_annotation.runtime_runner.ensure_fanxiu_mail_table", lambda: None)

    def fake_open_entry(_runtime):
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_open_mail_cleanup_entry", fake_open_entry)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")

    def identify_scene_number(_ctx, _frame, keys=None, *_args, **_kwargs):
        if keys and 122 in keys:
            return (122, 96.0)
        return (121, 95.0)

    monkeypatch.setattr(runner, "_identify_scene_number", identify_scene_number)
    monkeypatch.setattr(
        runner,
        "_recognize_visible_mail_rows",
        lambda *_args, **_kwargs: [{"title": "潜修心得", "time_text": "06-09 12:00", "status": "无", "x": 300.0, "y": 500.0}],
    )
    monkeypatch.setattr(runner, "_prepare_mail_row_policy", lambda row, **_kwargs: row.update({"policy": "claim", "mail_key": "mail-1"}))
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, *_args, **_kwargs: clicked.append((image["title"], shape["title"])))
    monkeypatch.setattr(runner, "_update_packet_mail_action_for_row", lambda row, **kwargs: updates.append((row["title"], kwargs["status"])))
    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", lambda _runtime: False)

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_mail_cleanup_task(ctx, FakeStopEvent(), {"max_actions": 1}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [("邮件", "潜修心得"), ("邮件详情", "领取")]
    assert updates == [("潜修心得", "claim_requested")]


def test_mail_cleanup_deletes_read_only_after_scanned_to_end(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    image121 = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"id": "list", "kind": "rect", "title": "邮件清单2", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6, "contentDirection": "down"},
            {"id": "delete-read", "kind": "rect", "title": "一键删除", "x": 0.2, "y": 0.8, "w": 0.2, "h": 0.08},
        ],
    }
    image210 = {
        "type": "image",
        "title": "一键删除确认",
        "filename": "0210.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "confirm", "kind": "rect", "title": "确认", "x": 0.55, "y": 0.64, "w": 0.28, "h": 0.08}],
    }
    ctx = {
        "entry": object(),
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {121: image121, 210: image210},
    }
    actions = []
    wait_view_calls = {"count": 0}

    monkeypatch.setattr("backend.core.fanxiu.data_annotation.runtime_runner.ensure_fanxiu_mail_table", lambda: None)

    def fake_open_entry(_runtime):
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_open_mail_cleanup_entry", fake_open_entry)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_runtime_mail_rows_from_frame", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", lambda _runtime: False)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (121, 95.0))
    monkeypatch.setattr(Shape, "click", lambda shape, _runtime: actions.append(("click", shape.parent_view.raw["title"], shape.title)))

    def fake_scroll(self, *_args, **_kwargs):
        if False:
            yield None
        return False

    def fake_wait_view(self, *view_ids, **kwargs):
        actions.append(("wait_view", view_ids, kwargs))
        wait_view_calls["count"] += 1
        if False:
            yield None
        if wait_view_calls["count"] == 1:
            assert view_ids == (210, 278, 121)
            return View(image210)
        assert view_ids == (121, 34)
        return View(image121)

    def fake_click_shape(self, view_id, shape_title, **kwargs):
        actions.append(("click_shape", view_id, shape_title, kwargs))
        return "success"

    monkeypatch.setattr(FanxiuRuntime, "scroll_shape_content", fake_scroll)
    monkeypatch.setattr(FanxiuRuntime, "wait_view", fake_wait_view)
    monkeypatch.setattr(FanxiuRuntime, "click_shape", fake_click_shape)

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_mail_cleanup_task(ctx, FakeStopEvent(), {"max_actions": 5}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert actions == [
        ("click", "邮件", "一键删除"),
        ("wait_view", (210, 278, 121), {"timeout": 12.0, "label": "邮件_清理：一键删除后等待确认弹窗"}),
        ("click_shape", 210, "确认", {"frame_data_url": None}),
        ("wait_view", (121, 34), {"timeout": 12.0, "label": "邮件_清理：确认一键删除后等待邮件页或世界页"}),
    ]


def test_mail_cleanup_confirms_one_key_delete_prompt(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    image121 = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"id": "list", "kind": "rect", "title": "邮件清单2", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6, "contentDirection": "down"},
            {"id": "delete-read", "kind": "rect", "title": "一键删除", "x": 0.2, "y": 0.8, "w": 0.2, "h": 0.08},
        ],
    }
    image210 = {
        "type": "image",
        "title": "一键删除确认",
        "filename": "0210.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "confirm", "kind": "rect", "title": "确认", "x": 0.55, "y": 0.64, "w": 0.28, "h": 0.08}],
    }
    ctx = {
        "entry": object(),
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {121: image121, 210: image210},
    }
    actions: list[tuple] = []
    wait_view_calls = {"count": 0}

    monkeypatch.setattr("backend.core.fanxiu.data_annotation.runtime_runner.ensure_fanxiu_mail_table", lambda: None)

    def fake_open_entry(_runtime):
        if False:
            yield None
        return "success"

    def fake_scroll(self, *_args, **_kwargs):
        if False:
            yield None
        return False

    def fake_wait_view(self, *view_ids, **kwargs):
        actions.append(("wait_view", view_ids, kwargs))
        wait_view_calls["count"] += 1
        if False:
            yield None
        if wait_view_calls["count"] == 1:
            assert view_ids == (210, 278, 121)
            image278 = {"type": "image", "title": "邮件删除确认", "filename": "0278.png", "width": 900, "height": 1600}
            return View(image278)
        assert view_ids == (121, 34)
        return View(image121)

    def fake_click_shape(self, view_id, shape_title, **kwargs):
        actions.append(("click_shape", view_id, shape_title, kwargs))
        return "success"

    monkeypatch.setattr(runner, "_open_mail_cleanup_entry", fake_open_entry)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_runtime_mail_rows_from_frame", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", lambda _runtime: False)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (121, 95.0))
    monkeypatch.setattr(Shape, "click", lambda shape, _runtime: actions.append(("click", shape.parent_view.raw["title"], shape.title)))
    monkeypatch.setattr(FanxiuRuntime, "scroll_shape_content", fake_scroll)
    monkeypatch.setattr(FanxiuRuntime, "wait_view", fake_wait_view)
    monkeypatch.setattr(FanxiuRuntime, "click_shape", fake_click_shape)

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_mail_cleanup_task(ctx, FakeStopEvent(), {"max_actions": 5}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert actions == [
        ("click", "邮件", "一键删除"),
        ("wait_view", (210, 278, 121), {"timeout": 12.0, "label": "邮件_清理：一键删除后等待确认弹窗"}),
        ("click_shape", 278, "确认", {"frame_data_url": None}),
        ("wait_view", (121, 34), {"timeout": 12.0, "label": "邮件_清理：确认一键删除后等待邮件页或世界页"}),
    ]


def test_mail_cleanup_uses_runtime_default_scroll_parameters(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    image121 = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"id": "list", "kind": "rect", "title": "邮件清单2", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6, "contentDirection": "down"},
            {"id": "delete-read", "kind": "rect", "title": "一键删除", "x": 0.2, "y": 0.8, "w": 0.2, "h": 0.08},
        ],
    }
    image210 = {
        "type": "image",
        "title": "一键删除确认",
        "filename": "0210.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "confirm", "kind": "rect", "title": "确认", "x": 0.55, "y": 0.64, "w": 0.28, "h": 0.08}],
    }
    ctx = {
        "entry": object(),
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {121: image121, 210: image210},
    }
    scroll_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    monkeypatch.setattr("backend.core.fanxiu.data_annotation.runtime_runner.ensure_fanxiu_mail_table", lambda: None)

    def fake_open_entry(_runtime):
        if False:
            yield None
        return "success"

    def fake_scroll(self, *args, **kwargs):
        scroll_calls.append((args, kwargs))
        if False:
            yield None
        return False

    monkeypatch.setattr(runner, "_open_mail_cleanup_entry", fake_open_entry)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_runtime_mail_rows_from_frame", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", lambda _runtime: False)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (121, 95.0))
    monkeypatch.setattr(Shape, "click", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(FanxiuRuntime, "scroll_shape_content", fake_scroll)

    wait_view_calls = {"count": 0}

    def fake_wait_view(self, *view_ids, **_kwargs):
        wait_view_calls["count"] += 1
        if False:
            yield None
        if wait_view_calls["count"] == 1:
            assert view_ids == (210, 278, 121)
            return View(image210)
        assert view_ids == (121, 34)
        return View(image121)

    def fake_click_shape(self, view_id, shape_title, **_kwargs):
        assert (view_id, shape_title) == (210, "确认")
        return "success"

    monkeypatch.setattr(FanxiuRuntime, "wait_view", fake_wait_view)
    monkeypatch.setattr(FanxiuRuntime, "click_shape", fake_click_shape)

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_mail_cleanup_task(ctx, FakeStopEvent(), {"max_actions": 5}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert len(scroll_calls) == 1
    assert scroll_calls[0][1] == {}


def test_mail_cleanup_deletes_read_after_scroll_limit(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    image121 = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"id": "list", "kind": "rect", "title": "邮件清单2", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6, "contentDirection": "down"},
            {"id": "delete-read", "kind": "rect", "title": "一键删除", "x": 0.2, "y": 0.8, "w": 0.2, "h": 0.08},
        ],
    }
    image210 = {
        "type": "image",
        "title": "一键删除确认",
        "filename": "0210.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "confirm", "kind": "rect", "title": "确认", "x": 0.55, "y": 0.64, "w": 0.28, "h": 0.08}],
    }
    ctx = {
        "entry": object(),
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {121: image121, 210: image210},
    }
    actions = []
    wait_view_calls = {"count": 0}

    monkeypatch.setattr("backend.core.fanxiu.data_annotation.runtime_runner.ensure_fanxiu_mail_table", lambda: None)

    def fake_open_entry(_runtime):
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_open_mail_cleanup_entry", fake_open_entry)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_runtime_mail_rows_from_frame", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", lambda _runtime: False)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (121, 95.0))
    monkeypatch.setattr(Shape, "click", lambda shape, _runtime: actions.append(("click", shape.parent_view.raw["title"], shape.title)))

    def fake_scroll(self, *_args, **_kwargs):
        if False:
            yield None
        return True

    monkeypatch.setattr(FanxiuRuntime, "scroll_shape_content", fake_scroll)

    def fake_wait_view(self, *view_ids, **kwargs):
        actions.append(("wait_view", view_ids, kwargs))
        wait_view_calls["count"] += 1
        if False:
            yield None
        if wait_view_calls["count"] == 1:
            assert view_ids == (210, 278, 121)
            return View(image210)
        assert view_ids == (121, 34)
        return View(image121)

    def fake_click_shape(self, view_id, shape_title, **kwargs):
        actions.append(("click_shape", view_id, shape_title, kwargs))
        return "success"

    monkeypatch.setattr(FanxiuRuntime, "wait_view", fake_wait_view)
    monkeypatch.setattr(FanxiuRuntime, "click_shape", fake_click_shape)

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_mail_cleanup_task(ctx, FakeStopEvent(), {"max_actions": 5, "max_scrolls": 1}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert actions == [
        ("click", "邮件", "一键删除"),
        ("wait_view", (210, 278, 121), {"timeout": 12.0, "label": "邮件_清理：一键删除后等待确认弹窗"}),
        ("click_shape", 210, "确认", {"frame_data_url": None}),
        ("wait_view", (121, 34), {"timeout": 12.0, "label": "邮件_清理：确认一键删除后等待邮件页或世界页"}),
    ]
    assert any("仍未确认到底，继续一键删除已阅" in log["message"] for log in runner.status()["logs"])


def test_legacy_mail_claim_check_task_type_runs_mail_cleanup(monkeypatch, tmp_path):
    ensure_fanxiu_runtime_jobs_registered()
    runner = create_fanxiu_runtime_runner()
    image121 = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "list", "kind": "rect", "title": "邮件清单2", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6}],
    }
    ctx = {
        "entry": object(),
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {121: image121},
    }
    called = []

    monkeypatch.setattr("backend.core.fanxiu.data_annotation.runtime_runner.ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(runner, "_execute_mail_cleanup_task", lambda *_args, **_kwargs: called.append("cleanup") or "success")

    result = runner._execute_runtime_task(ctx, "mail_claim_check", {}, threading.Event())

    assert result == "success"
    assert called == ["cleanup"]


def test_xianfu_continue_visit_popup_continues_half_price_then_closes(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image175 = {
        "type": "image",
        "title": "继续寻访",
        "filename": "0175.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"id": "close", "kind": "shape", "title": "关闭", "x": 0.2, "y": 0.8, "w": 0.1, "h": 0.03},
            {"id": "half", "kind": "shape", "title": "半价", "x": 0.5, "y": 0.75, "w": 0.1, "h": 0.03},
            {"id": "continue", "kind": "shape", "title": "继续", "x": 0.6, "y": 0.8, "w": 0.1, "h": 0.03},
        ],
    }
    image174 = {"type": "image", "title": "绝品仙侣", "filename": "0174.png", "width": 900, "height": 1600, "shapes": []}
    clicked: list[str] = []
    ocr_values = iter(["50半价", "100半价"])

    class FakeRuntime:
        def __init__(self):
            self.scene_id = 174

        def get_view(self, view_id):
            if view_id == 175:
                return View(image175)
            if view_id == 174:
                return View(image174)
            return None

        def wait_view(self, *views, timeout=None, label=""):
            del timeout, label
            if False:
                yield BehaviorTreeStatus.RUNNING
            return self.get_view(int(views[0]))

        def cur_frame(self, update=False):
            del update
            return "frame"

        def current_scene(self, view_ids=None, **kwargs):
            del view_ids, kwargs
            return self.scene_id, 100.0, "frame"

        def ocr_text(self, _frame):
            return "绝品仙侣 免费抽取"

        def wait_action_settle(self, _seconds):
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "settled"

        def click_shape(self, _view, shape):
            clicked.append(shape.title)

    def fake_ocr_lines_in_shapes(_frame, _image, shape_titles, **_kwargs):
        if tuple(shape_titles) == ("半价",):
            return [{"text": next(ocr_values), "x": 0, "y": 0, "w": 10, "h": 10}]
        return []

    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", fake_ocr_lines_in_shapes)

    result = runner._run_direct_runtime_action(
        lambda: runner._handle_xianfu_continue_visit_popup(FakeRuntime(), max_continue=20),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == ["继续", "关闭"]


def test_xianfu_continue_visit_popup_retries_close_when_popup_remains(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image175 = {
        "type": "image",
        "title": "继续寻访",
        "filename": "0175.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"id": "close", "kind": "shape", "title": "关闭", "x": 0.2, "y": 0.8, "w": 0.1, "h": 0.03},
            {"id": "half", "kind": "shape", "title": "半价", "x": 0.5, "y": 0.75, "w": 0.1, "h": 0.03},
        ],
    }
    image174 = {"type": "image", "title": "绝品仙侣", "filename": "0174.png", "width": 900, "height": 1600, "shapes": []}
    clicked: list[str] = []

    class FakeRuntime:
        def __init__(self):
            self.close_count = 0

        def get_view(self, view_id):
            if view_id == 175:
                return View(image175)
            if view_id == 174:
                return View(image174)
            return None

        def wait_view(self, *views, timeout=None, label=""):
            del timeout, label
            if False:
                yield BehaviorTreeStatus.RUNNING
            return self.get_view(int(views[0]))

        def cur_frame(self, update=False):
            del update
            return "frame"

        def current_scene(self, view_ids=None, **kwargs):
            del view_ids, kwargs
            scene_id = 175 if self.close_count < 2 else 174
            return scene_id, 100.0, "frame"

        def ocr_text(self, _frame):
            return "马师叔100关闭继续寻访" if self.close_count < 2 else "绝品仙侣 免费抽取"

        def wait_action_settle(self, _seconds):
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "settled"

        def click_shape(self, _view, shape):
            clicked.append(shape.title)
            if shape.title == "关闭":
                self.close_count += 1

    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", lambda *_args, **_kwargs: [{"text": "100半价"}])

    result = runner._run_direct_runtime_action(
        lambda: runner._handle_xianfu_continue_visit_popup(FakeRuntime(), max_continue=20),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == ["关闭", "关闭"]

