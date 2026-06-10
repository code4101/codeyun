from pyxllib.autogui import SceneNavigator


def _image(title: str, filename: str, shapes: list[dict] | None = None) -> dict:
    return {
        "type": "image",
        "title": title,
        "filename": filename,
        "width": 900,
        "height": 1600,
        "shapes": shapes or [],
    }


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


def test_scene_navigator_resolves_numeric_image_and_folder_targets():
    tree = [
        _image("世界", "0034.jpg"),
        {
            "type": "folder",
            "title": "日常",
            "children": [
                _image("日常", "0069.png"),
                _image("报名", "0023.png"),
            ],
        },
    ]
    navigator = SceneNavigator(tree)

    assert navigator.resolve_scene_jump_label("#34") == [34]
    assert navigator.resolve_scene_jump_label("报名") == [23]
    assert navigator.resolve_scene_jump_label("日常") == [69, 23, 69]
    assert navigator.scene_jump_target_ids({"sceneJumpTarget": "日常,#34,报名"}) == [69, 23, 34]


def test_scene_navigator_infers_nested_leave_returns_to_parent_scene():
    leave_shape = {"id": "leave", "kind": "rect", "title": "离开"}
    tree = [
        _image("世界", "0034.jpg", []),
        {
            "id": "folder-world",
            "type": "folder",
            "title": "世界",
            "children": [
                _image("某区域内部", "0085.png", [leave_shape]),
            ],
        },
    ]
    navigator = SceneNavigator(tree)

    edges = navigator.scene_jump_edges()

    assert 85 in edges
    assert edges[85][0]["shape"] is leave_shape
    assert edges[85][0]["target_ids"] == [34]
    assert navigator.find_scene_route(85, 34) == [edges[85][0]]


def test_scene_navigator_infers_world_menu_close_returns_to_world():
    close_shape = {"id": "close-menu", "kind": "rect", "title": "关闭下方菜单"}
    tree = [
        {
            "id": "folder-world",
            "type": "folder",
            "title": "世界",
            "children": [
                _image("世界", "0034.jpg", []),
                _image("世界下方菜单", "0035.png", [close_shape]),
            ],
        },
    ]
    navigator = SceneNavigator(tree)

    edges = navigator.scene_jump_edges()

    assert edges[35][0]["target_ids"] == [34]
    assert navigator.find_scene_route(35, 34) == [edges[35][0]]


def test_scene_navigator_finds_multi_step_route_and_ignores_independent_exit():
    edge12 = {"id": "edge12", "kind": "rect", "title": "去二", "sceneJumpTarget": "#2"}
    edge23 = {"id": "edge23", "kind": "rect", "title": "去三", "sceneJumpTarget": "#3"}
    ignored = {"id": "exit", "kind": "rect", "title": "空白", "sceneJumpTarget": "-1"}
    tree = [
        _image("一", "0001.jpg", [edge12, ignored]),
        _image("二", "0002.jpg", [edge23]),
        _image("三", "0003.jpg", []),
    ]
    navigator = SceneNavigator(tree)

    route = navigator.find_scene_route(1, 3)

    assert route is not None
    assert [edge["shape"]["id"] for edge in route] == ["edge12", "edge23"]
    assert navigator.scene_jump_edges()[1][0]["shape"] is edge12
