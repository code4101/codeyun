from backend.core.fanxiu.data_annotation.recognition_tree import (
    build_recognition_tree_nodes,
    layer0_recognition_candidate_ids,
    runtime_root_scene_candidate_ids,
)


def _image(
    scene_id: int,
    title: str,
    *,
    layer: int | None = 1,
    identity: bool = True,
    children: list[dict] | None = None,
) -> dict:
    image = {
        "type": "image",
        "filename": f"{scene_id:04d}.png",
        "title": title,
        "children": children or [],
    }
    if layer is not None:
        image["layer"] = layer
    if identity:
        image["shapes"] = [{"id": "identity", "title": f"{title}标识", "isSceneIdentity": True}]
    return image


def test_recognition_tree_keeps_folder_nesting_as_asset_path_not_parent_relation():
    scene = _image(34, "世界", layer=1)
    tree = [{"type": "folder", "title": "世界目录", "children": [scene]}]
    images = {34: scene}

    nodes = build_recognition_tree_nodes(tree, images)

    assert len(nodes) == 1
    assert nodes[0].scene_id == 34
    assert nodes[0].asset_path == ("世界目录", "世界")
    assert nodes[0].parent_scene_ids == ()
    assert runtime_root_scene_candidate_ids(tree, images) == [34]


def test_recognition_tree_does_not_use_image_children_as_recognition_structure():
    child = _image(266, "法则详情", layer=2)
    parent = _image(265, "法则选择", layer=1, children=[child])
    tree = [{"type": "folder", "title": "拜谒", "children": [parent]}]
    images = {265: parent, 266: child}

    nodes = build_recognition_tree_nodes(tree, images)
    by_id = {node.scene_id: node for node in nodes}

    assert by_id[265].parent_scene_ids == ()
    assert by_id[266].parent_scene_ids == ()
    assert by_id[266].asset_path == ("拜谒", "法则选择", "法则详情")
    assert runtime_root_scene_candidate_ids(tree, images) == [265, 266]


def test_recognition_tree_uses_recognition_parent_without_asset_reparenting():
    parent = _image(265, "法则选择", layer=1)
    child = {**_image(266, "法则详情", layer=2), "recognitionParentId": 265}
    tree = [{"type": "folder", "title": "拜谒", "children": [parent, child]}]
    images = {265: parent, 266: child}

    nodes = build_recognition_tree_nodes(tree, images)
    by_id = {node.scene_id: node for node in nodes}

    assert by_id[265].parent_scene_ids == ()
    assert by_id[266].parent_scene_ids == (265,)
    assert by_id[266].asset_path == ("拜谒", "法则详情")
    assert runtime_root_scene_candidate_ids(tree, images) == [265]
    assert layer0_recognition_candidate_ids(tree, images, [266]) == [265, 266]


def test_layer0_candidates_expand_to_parent_and_preferred_subtree():
    detail = {**_image(266, "法则详情", layer=2), "recognitionParentId": 265}
    sibling = {**_image(267, "无关子页", layer=2), "recognitionParentId": 265}
    parent = _image(265, "法则选择", layer=1, children=[detail, sibling])
    world = _image(34, "世界", layer=1)
    tree = [world, parent]
    images = {34: world, 265: parent, 266: detail, 267: sibling}

    assert layer0_recognition_candidate_ids(tree, images, [266]) == [265, 266]
    assert layer0_recognition_candidate_ids(tree, images, [265]) == [265, 266, 267]


def test_runtime_root_scene_candidates_preserve_layer_order_and_popup_filter():
    world = _image(34, "世界", layer=1)
    popup = _image(47, "所有提示窗口", layer=None)
    helper = _image(999, "模板", layer=None, identity=False)
    tree = [
        {"type": "folder", "title": "默认", "children": [helper, world]},
        {"type": "folder", "title": "弹窗", "children": [popup]},
    ]
    images = {34: world, 47: popup, 999: helper}

    assert runtime_root_scene_candidate_ids(tree, images) == [34, 47, 999]
    assert runtime_root_scene_candidate_ids(tree, images, include_popups=True) == [47]
    assert runtime_root_scene_candidate_ids(tree, images, include_popups=False) == [34, 999]


def test_recognition_tree_derives_layer2_from_scene_identity_and_keeps_asset_order():
    first = _image(100, "先出现", layer=None, identity=True)
    second = _image(200, "后出现", layer=None, identity=True)
    helper = _image(300, "素材", layer=2, identity=False)
    tree = [first, helper, second]
    images = {100: first, 200: second, 300: helper}

    nodes = build_recognition_tree_nodes(tree, images)
    by_id = {node.scene_id: node for node in nodes}

    assert by_id[100].layer == 2
    assert by_id[200].layer == 2
    assert by_id[300].layer == 3
    assert runtime_root_scene_candidate_ids(tree, images) == [100, 200, 300]


def test_runtime_root_scene_candidates_skip_explicit_local_only_identity_roots():
    world = _image(34, "世界", layer=1)
    local_overlay = _image(277, "小助手进度", layer=None, identity=False)
    local_overlay["shapes"] = [
        {
            "id": "progress",
            "title": "进度",
            "isSceneIdentity": True,
            "sceneIdentityScope": "local",
        }
    ]
    legacy_identity = _image(100, "旧标注场景", layer=None, identity=True)
    popup = _image(47, "所有提示窗口", layer=None, identity=False)
    popup["shapes"] = [
        {
            "id": "popup",
            "title": "弹窗标识",
            "isSceneIdentity": True,
            "sceneIdentityScope": "local",
        }
    ]
    tree = [
        {"type": "folder", "title": "场景", "children": [world, legacy_identity]},
        {"type": "folder", "title": "日常", "children": [local_overlay]},
        {"type": "folder", "title": "弹窗", "children": [popup]},
    ]
    images = {34: world, 277: local_overlay, 100: legacy_identity, 47: popup}

    assert runtime_root_scene_candidate_ids(tree, images) == [34, 100]
    assert runtime_root_scene_candidate_ids(tree, images, include_popups=True) == [47]
