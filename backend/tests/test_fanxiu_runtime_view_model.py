import pytest

from backend.core.fanxiu_behavior_tree import create_fanxiu_runtime_runner
from pyxllib.prog import BehaviorTreeStatus
from pyxllib.autogui import (
    MatchRole,
    View,
    flatten_shapes,
    image_number,
    index_images,
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

    monkeypatch.setattr(
        runner,
        "_drag_frame_point",
        lambda _ctx, _image, sx, sy, ex, ey, **kwargs: dragged.append((_image["title"], sx, sy, ex, ey, kwargs.get("duration_ms"))),
    )

    runtime = runner._fanxiu_runtime({"entry": object()})

    assert shape is not None
    assert list(shape.load(runtime, ratio=0.5, duration=1.5)) == [1]
    assert dragged == [("邮件", 30.0, 115.0, 30.0, 65.0, 1500)]
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
    monotonic_values = iter([0.0, 0.5, 1.2])

    monkeypatch.setattr("backend.core.fanxiu_data_annotation_runtime_runner.time.monotonic", lambda: next(monotonic_values))
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


def test_mail_claim_check_v2_entry_prefers_view68_mail(monkeypatch, tmp_path):
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
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: runner.overlay_threshold + 1)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, *_args, **_kwargs: clicked.append((image["title"], shape["title"])))

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    runtime = runner._fanxiu_runtime(ctx, ctx["asset_tree_path"])
    result = runner._run_direct_runtime_action(
        lambda: runner._open_mail_claim_check_v2_entry(runtime),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [("世界-下方动态", "邮件")]


def test_mail_claim_check_v2_entry_falls_back_to_view35_mail(monkeypatch, tmp_path):
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
        "shapes": [{"id": "mail35", "kind": "rect", "title": "邮件", "x": 0.5, "y": 0.8, "w": 0.1, "h": 0.08}],
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

    def shape_score(_ctx, _image, shape, *_args, **_kwargs):
        return 0.0 if shape.get("id") == "mail68" else runner.overlay_threshold + 1

    monkeypatch.setattr(runner, "_shape_score", shape_score)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, *_args, **_kwargs: clicked.append((image["title"], shape["title"])))

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    runtime = runner._fanxiu_runtime(ctx, ctx["asset_tree_path"])
    result = runner._run_direct_runtime_action(
        lambda: runner._open_mail_claim_check_v2_entry(runtime),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [("世界", "打开下方菜单"), ("世界下方菜单", "邮件")]


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


def test_mail_claim_check_v2_uses_runtime_view_shape_flow(monkeypatch, tmp_path):
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
    identify_results = iter([(122, 96.0), (121, 95.0)])

    monkeypatch.setattr("backend.core.fanxiu_data_annotation_runtime_runner.ensure_fanxiu_mail_table", lambda: None)

    def fake_open_entry(_runtime):
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_open_mail_claim_check_v2_entry", fake_open_entry)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: next(identify_results))
    monkeypatch.setattr(
        runner,
        "_recognize_visible_mail_rows",
        lambda *_args, **_kwargs: [{"title": "潜修心得", "time_text": "06-09 12:00", "status": "无", "x": 300.0, "y": 500.0}],
    )
    monkeypatch.setattr(runner, "_prepare_mail_row_policy", lambda row, **_kwargs: row.update({"policy": "claim", "mail_key": "mail-1"}))
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, *_args, **_kwargs: clicked.append((image["title"], shape["title"])))
    monkeypatch.setattr(runner, "_update_packet_mail_action_for_row", lambda row, **kwargs: updates.append((row["title"], kwargs["status"])))

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_mail_claim_check_v2_task(ctx, FakeStopEvent(), {"max_actions": 1}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [("邮件", "潜修心得"), ("邮件详情", "领取")]
    assert updates == [("潜修心得", "claim_requested")]
