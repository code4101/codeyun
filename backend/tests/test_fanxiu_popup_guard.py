from __future__ import annotations

from types import SimpleNamespace

from pyxllib.autogui import View

from backend.core.fanxiu.runtime.behavior_tree import create_fanxiu_runtime_runner


def test_popup_guard_handles_scene_393_by_clicking_avatar(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
        matched_view = SimpleNamespace(
            score=100.0,
            folder_path="弹窗",
            action_shape=None,
        )

        def find_view(self, title):
            assert title == "弹窗"
            return view

        def cur_frame(self, **_kwargs):
            return "frame-393"

        def click_shape(self, target_view, shape, *, frame_data_url=None):
            clicks.append((target_view.id, shape.title, frame_data_url))

    monkeypatch.setattr(runner, "_handle_disconnect_reconnect_popup", lambda _runtime: False)
    monkeypatch.setattr(runner, "_skip_popup_guard_on_login_or_maintenance", lambda _runtime: False)

    handled = runner._auto_close_popup_guard_step(Runtime(), during_task=True)

    assert handled is True
    assert clicks == [(393, "分身", "frame-393")]
    assert runner.status()["last_guard_event"]["action"] == "click:分身"
    assert runner.status()["current_scene"] == 393
