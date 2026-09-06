from __future__ import annotations

from typing import Any

from backend.core.fanxiu.data_annotation.runner import create_behavior_tree_executor
from pyxllib.autogui import Shape, View


def test_unsolicited_nested_leave_popup_uses_own_cancel_not_parent_background() -> None:
    runner = create_behavior_tree_executor()
    parent = {
        "type": "image",
        "id": 47,
        "title": "所有提示窗口",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "提示标题", "isSceneIdentity": True},
            {
                "title": "空白",
                "description": "点击背景空白可以直接关闭",
                "x": 0.88,
                "y": 0.18,
                "w": 0.08,
                "h": 0.05,
            },
        ],
        "children": [
            {
                "type": "image",
                "id": 86,
                "title": "离开场景",
                "width": 900,
                "height": 1600,
                "shapes": [
                    {"title": "离开场景标识", "isSceneIdentity": True},
                    {"title": "取消", "x": 0.20, "y": 0.64, "w": 0.28, "h": 0.05},
                    {"title": "确认", "x": 0.62, "y": 0.64, "w": 0.12, "h": 0.04},
                ],
            }
        ],
    }

    candidates = runner._index_guard_candidates(
        [{"type": "folder", "title": "弹窗", "children": [parent]}]
    )
    candidate = next(item for item in candidates if item["image"]["id"] == 86)

    assert candidate["action_shape"]["title"] == "空白"
    assert candidate["action_view"]["id"] == 47

    clicks: list[tuple[int, str]] = []

    class Context:
        def cur_frame(self) -> str:
            return "frame"

        def click_shape(self, view: Any, shape: Any, **_options: Any) -> None:
            clicks.append((int(view.id), str(shape.title)))

    assert runner._handle_recognized_popup_candidate(Context(), candidate, score=99.0)
    assert clicks == [(86, "取消")]
    assert runner.status()["last_guard_event"]["action"] == "click:取消"


def test_unsolicited_leave_popup_without_own_cancel_fails_closed() -> None:
    runner = create_behavior_tree_executor()
    popup = {
        "type": "image",
        "id": 86,
        "title": "离开场景",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "离开场景标识", "isSceneIdentity": True},
            {"title": "确认", "x": 0.62, "y": 0.64, "w": 0.12, "h": 0.04},
        ],
    }
    clicks: list[tuple[int, str]] = []

    class Context:
        ctx = {"asset_tree": [popup]}
        last_clicked_shape = None
        last_clicked_at = 0.0

        def cur_frame(self) -> str:
            return "frame"

        def click_shape(self, view: Any, shape: Any, **_options: Any) -> None:
            clicks.append((int(view.id), str(shape.title)))

    candidate = {
        "image": popup,
        "action_shape": {"title": "空白"},
        "action_view": {"type": "image", "id": 47, "title": "所有提示窗口"},
    }

    assert runner._handle_recognized_popup_candidate(Context(), candidate, score=99.0)
    assert clicks == []
    assert runner.status()["last_guard_event"]["action"] == "missing_cancel"


def test_popup_group_node_can_bind_explicit_recovery_action() -> None:
    runner = create_behavior_tree_executor()
    popup = {
        "type": "image",
        "id": 696,
        "title": "断线重连",
        "width": 900,
        "height": 1600,
        "behaviorTreeInterruptionAction": "重连",
        "shapes": [
            {"title": "当前网络已断开", "isSceneIdentity": True},
            {"title": "重连", "x": 0.62, "y": 0.64, "w": 0.13, "h": 0.06},
        ],
    }

    candidates = runner._index_guard_candidates(
        [{"type": "folder", "title": "弹窗", "children": [popup]}]
    )
    candidate = next(item for item in candidates if item["image"]["id"] == 696)

    assert candidate["action_shape"]["title"] == "重连"
    assert candidate["action_view"]["id"] == 696

    clicks: list[tuple[int, str]] = []

    class Context:
        last_clicked_shape = None
        last_clicked_at = 0.0
        ctx = {"asset_tree": [popup]}

        def cur_frame(self) -> str:
            return "frame"

        def click_shape(self, view: Any, shape: Any, **_options: Any) -> None:
            clicks.append((int(view.id), str(shape.title)))

    assert runner._handle_recognized_popup_candidate(Context(), candidate, score=99.0)
    assert clicks == [(696, "重连")]
    assert runner.status()["last_guard_event"]["action"] == "click:重连"


def test_popup_group_missing_explicit_recovery_action_fails_closed() -> None:
    runner = create_behavior_tree_executor()
    popup = {
        "type": "image",
        "id": 696,
        "title": "断线重连",
        "behaviorTreeInterruptionAction": "重连",
        "shapes": [
            {"title": "当前网络已断开", "isSceneIdentity": True},
            {"title": "关闭", "x": 0.8, "y": 0.2, "w": 0.1, "h": 0.1},
        ],
    }

    candidates = runner._index_guard_candidates(
        [{"type": "folder", "title": "弹窗", "children": [popup]}]
    )
    candidate = next(item for item in candidates if item["image"]["id"] == 696)

    assert candidate["action_shape"] is None
    assert candidate["action_view"]["id"] == 696


def test_declared_leave_action_is_confirmed_inside_layer0_guard() -> None:
    runner = create_behavior_tree_executor()
    popup = {
        "type": "image",
        "filename": "0086.png",
        "title": "离开场景",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "离开场景标识", "isSceneIdentity": True},
            {"title": "确认", "x": 0.62, "y": 0.64, "w": 0.12, "h": 0.04},
        ],
    }
    source = View(
        {
            "type": "image",
            "filename": "0085.png",
            "title": "区域内页",
            "shapes": [],
        }
    )
    pending = Shape(
        {"title": "离开", "sceneJumpTarget": "86"},
        parent_view=source,
    )
    clicks: list[tuple[int, str]] = []

    class Context:
        ctx = {"asset_tree": [source.raw, popup]}
        last_clicked_shape = pending
        last_clicked_at = __import__("time").monotonic()

        def cur_frame(self) -> str:
            return "frame"

        def click_shape(self, view: Any, shape: Any, **_options: Any) -> None:
            clicks.append((int(view.id), str(shape.title)))

    candidate = {
        "image": popup,
        "action_shape": {"title": "空白"},
        "action_view": {
            "type": "image",
            "filename": "0047.png",
            "title": "所有提示窗口",
        },
    }

    assert runner._handle_recognized_popup_candidate(Context(), candidate, score=99.0)
    assert clicks == [(86, "确认")]
    assert runner.status()["last_guard_event"]["action"] == "click:确认"


def test_immediate_leave_confirmation_does_not_require_a_declared_popup_edge() -> None:
    runner = create_behavior_tree_executor()
    popup = {
        "type": "image",
        "filename": "0086.png",
        "title": "离开场景",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "离开场景标识", "isSceneIdentity": True},
            {"title": "确认", "x": 0.62, "y": 0.64, "w": 0.12, "h": 0.04},
        ],
    }
    source = View({"type": "image", "filename": "0171.png", "shapes": []})
    pending = Shape({"title": "离开"}, parent_view=source)
    clicks: list[tuple[int, str]] = []

    class Context:
        ctx = {"asset_tree": [source.raw, popup]}
        last_clicked_shape = pending
        last_clicked_at = __import__("time").monotonic()

        def cur_frame(self) -> str:
            return "frame"

        def click_shape(self, view: Any, shape: Any, **_options: Any) -> None:
            clicks.append((int(view.id), str(shape.title)))

    candidate = {
        "image": popup,
        "action_shape": {"title": "空白"},
        "action_view": {"type": "image", "filename": "0047.png"},
    }

    assert runner._handle_recognized_popup_candidate(Context(), candidate, score=99.0)
    assert clicks == [(86, "确认")]
    assert runner.status()["last_guard_event"]["action"] == "click:确认"


def test_expected_leave_popup_is_confirmed_after_click_context_was_consumed() -> None:
    runner = create_behavior_tree_executor()
    popup = {
        "type": "image",
        "filename": "0086.png",
        "title": "离开场景",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "离开场景标识", "isSceneIdentity": True},
            {"title": "确认", "x": 0.62, "y": 0.64, "w": 0.12, "h": 0.04},
        ],
    }
    clicks: list[tuple[int, str]] = []

    class Context:
        ctx = {"asset_tree": [popup]}
        last_clicked_shape = None
        last_clicked_at = 0.0

        def cur_frame(self) -> str:
            return "frame"

        def click_shape(self, view: Any, shape: Any, **_options: Any) -> None:
            clicks.append((int(view.id), str(shape.title)))

    candidate = {
        "image": popup,
        "action_shape": {"title": "空白"},
        "action_view": {"type": "image", "filename": "0047.png"},
    }

    assert runner._handle_recognized_popup_candidate(
        Context(), candidate, score=99.0, expected_scene_ids={171, 86, 34}
    )
    assert clicks == [(86, "确认")]


def test_declared_business_popup_is_continued_inside_layer0_guard() -> None:
    runner = create_behavior_tree_executor()
    popup = {
        "type": "image",
        "filename": "0191.png",
        "title": "剑灵扫荡确认",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "扫荡确认标识", "isSceneIdentity": True},
            {"title": "进行扫荡", "sceneJumpTarget": "192", "x": 0.6, "y": 0.7, "w": 0.2, "h": 0.1},
            {"title": "取消", "sceneJumpTarget": "190", "x": 0.2, "y": 0.7, "w": 0.2, "h": 0.1},
        ],
    }
    source = View(
        {
            "type": "image",
            "filename": "0190.png",
            "title": "剑灵",
            "shapes": [],
        }
    )
    pending = Shape(
        {"title": "扫荡", "sceneJumpTarget": "191"},
        parent_view=source,
    )
    parent = {
        "type": "image",
        "filename": "0047.png",
        "title": "所有提示窗口",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "提示标题", "isSceneIdentity": True},
            {"title": "空白", "x": 0.88, "y": 0.18, "w": 0.08, "h": 0.05},
        ],
        "children": [popup],
    }
    candidates = runner._index_guard_candidates(
        [{"type": "folder", "title": "弹窗", "children": [parent]}]
    )
    candidate = next(item for item in candidates if item["image"]["filename"] == "0191.png")
    clicks: list[tuple[int, str]] = []

    class Context:
        ctx = {"asset_tree": [source.raw, popup]}
        last_clicked_shape = pending
        last_clicked_at = __import__("time").monotonic()

        def cur_frame(self) -> str:
            return "frame"

        def click_shape(self, view: Any, shape: Any, **_options: Any) -> None:
            clicks.append((int(view.id), str(shape.title)))

    assert runner._handle_recognized_popup_candidate(Context(), candidate, score=98.0)
    assert clicks == [(191, "进行扫荡")]
    assert runner.status()["last_guard_event"]["action"] == "click:进行扫荡"


def test_popup_asset_description_can_bind_missing_jump_annotation() -> None:
    runner = create_behavior_tree_executor()
    popup = {
        "type": "image",
        "filename": "0278.png",
        "title": "邮件删除确认",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "邮件", "isSceneIdentity": True},
            {
                "title": "确认",
                "sceneJumpTarget": "121",
                "description": "邮件一键删除业务专用确认动作",
                "x": 0.6,
                "y": 0.7,
                "w": 0.2,
                "h": 0.1,
            },
        ],
    }
    source = View({"type": "image", "filename": "0121.png", "shapes": []})
    pending = Shape({"title": "一键删除"}, parent_view=source)
    candidate = {
        "image": popup,
        "action_shape": {"title": "空白"},
        "action_view": {"type": "image", "filename": "0047.png"},
        "intended_action_shape": popup["shapes"][1],
    }
    clicks: list[tuple[int, str]] = []

    class Context:
        ctx = {"asset_tree": [source.raw, popup]}
        last_clicked_shape = pending
        last_clicked_at = __import__("time").monotonic()

        def cur_frame(self) -> str:
            return "frame"

        def click_shape(self, view: Any, shape: Any, **_options: Any) -> None:
            clicks.append((int(view.id), str(shape.title)))

    assert runner._handle_recognized_popup_candidate(Context(), candidate, score=97.0)
    assert clicks == [(278, "确认")]
