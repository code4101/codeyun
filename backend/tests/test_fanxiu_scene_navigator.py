from pyxllib.autogui import SceneNavigator


def test_scene_navigator_parses_and_updates_jump_target_counts():
    shape = {"title": "进入", "sceneJumpTarget": "#34(2),报名, #69"}
    navigator = SceneNavigator([])

    assert navigator.parse_scene_jump_entries(shape["sceneJumpTarget"]) == [
        {"label": "#34", "count": 2},
        {"label": "报名", "count": 0},
        {"label": "#69", "count": 0},
    ]
    assert navigator.increment_scene_jump_target(shape, 34) is True
    assert shape["sceneJumpTarget"] == "#34(3),报名,#69"
    assert navigator.increment_scene_jump_target({"sceneJumpTarget": "-1"}, 34) is False
