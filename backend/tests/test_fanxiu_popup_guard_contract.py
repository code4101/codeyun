from __future__ import annotations

from typing import Any

from backend.core.fanxiu.data_annotation.runner import create_behavior_tree_executor


def test_nested_leave_popup_inherits_parent_background_dismiss_action() -> None:
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
    assert clicks == [(47, "空白")]
    assert runner.status()["last_guard_event"]["action"] == "click:空白"
