from backend.core.fanxiu_behavior_tree import create_fanxiu_runtime_runner
from pyxllib.autogui import ActionPlanner


def _image(width: int = 900, height: int = 1600) -> dict:
    return {"type": "image", "title": "测试", "filename": "0001.png", "width": width, "height": height}


def test_action_planner_builds_shape_box_and_center():
    image = _image()
    shape = {"title": "按钮", "x": 0.5, "y": 0.9, "w": 0.1, "h": 0.06}
    planner = ActionPlanner()

    assert planner.shape_box(image, shape) == {
        "name": "按钮",
        "x": 450.0,
        "y": 1440.0,
        "w": 90.0,
        "h": 96.0,
    }
    assert planner.shape_center(image, shape) == (495.0, 1488.0)


def test_action_planner_click_shape_payload_matches_runtime_protocol():
    image = _image()
    shape = {"title": "按钮", "x": 0.5, "y": 0.9, "w": 0.1, "h": 0.06}

    assert ActionPlanner().click_shape_payload(image, shape) == {
        "x": 495.0,
        "y": 1488.0,
        "mode": "screen",
        "area": "client",
        "rotate": "0",
        "fixed_width": 900,
        "fixed_height": 1600,
        "frame_width": 900,
        "frame_height": 1600,
        "input_backend": "adb",
    }


def test_action_planner_clamps_direct_click_and_drag_points():
    image = _image(width=100, height=200)
    planner = ActionPlanner()

    click_payload = planner.click_point_payload(image, -5, 250)
    drag_payload = planner.drag_point_payload(image, -5, 20, 130, 250, duration_ms=1000)

    assert click_payload["x"] == 0.0
    assert click_payload["y"] == 199.0
    assert drag_payload["start_x"] == 0.0
    assert drag_payload["start_y"] == 20.0
    assert drag_payload["end_x"] == 99.0
    assert drag_payload["end_y"] == 199.0
    assert drag_payload["duration_ms"] == 1000


def test_runner_action_helpers_delegate_to_action_planner():
    runner = create_fanxiu_runtime_runner()
    image = _image()
    shape = {"title": "按钮", "x": 0.5, "y": 0.9, "w": 0.1, "h": 0.06}

    assert runner._box(shape, image) == ActionPlanner().shape_box(image, shape)
    assert runner._shape_center(shape, image) == ActionPlanner().shape_center(image, shape)
