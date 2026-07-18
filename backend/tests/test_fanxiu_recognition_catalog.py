from backend.core.fanxiu.data_annotation.recognition_catalog import (
    build_recognition_graph_nodes,
    default_recognition_candidate_ids,
    expand_graph_candidate_ids,
)


def _image(scene_id: int, title: str, *, layer: int | None = 1, identity: bool = True) -> dict:
    image = {
        "type": "image",
        "filename": f"{scene_id:04d}.png",
        "title": title,
        "children": [],
    }
    if layer is not None:
        image["layer"] = layer
    if identity:
        image["shapes"] = [{"id": "identity", "isSceneIdentity": True}]
    return image


def test_asset_nesting_is_metadata_not_a_recognition_edge():
    child = _image(266, "法则详情", layer=2)
    parent = _image(265, "法则选择", layer=1)
    parent["children"] = [child]
    tree = [{"type": "folder", "title": "拜谒", "children": [parent]}]
    images = {265: parent, 266: child}

    nodes = {node.scene_id: node for node in build_recognition_graph_nodes(tree, images)}

    assert nodes[265].parent_scene_ids == ()
    assert nodes[266].parent_scene_ids == ()
    assert nodes[266].asset_path == ("拜谒", "法则选择", "法则详情")
    assert default_recognition_candidate_ids(tree, images) == [265, 266]


def test_recognition_parent_builds_graph_relation_without_asset_reparenting():
    parent = _image(265, "法则选择", layer=1)
    child = {**_image(266, "法则详情", layer=2), "recognitionParentId": 265}
    tree = [{"type": "folder", "title": "拜谒", "children": [parent, child]}]
    images = {265: parent, 266: child}

    nodes = {node.scene_id: node for node in build_recognition_graph_nodes(tree, images)}

    assert nodes[266].parent_scene_ids == (265,)
    assert nodes[266].asset_path == ("拜谒", "法则详情")
    assert default_recognition_candidate_ids(tree, images) == [265, 266]
    assert expand_graph_candidate_ids(tree, images, [266]) == [265, 266]


def test_preferred_graph_candidate_expands_to_ancestors_and_descendants():
    parent = _image(265, "法则选择", layer=1)
    detail = {**_image(266, "法则详情", layer=2), "recognitionParentId": 265}
    sibling = {**_image(267, "无关子页", layer=2), "recognitionParentId": 265}
    tree = [parent, detail, sibling]
    images = {265: parent, 266: detail, 267: sibling}

    assert expand_graph_candidate_ids(tree, images, [266]) == [265, 266]
    assert expand_graph_candidate_ids(tree, images, [265]) == [265, 266, 267]


def test_default_candidates_include_all_identity_frames_and_filter_material():
    world = _image(34, "世界", layer=1)
    material = _image(300, "素材", layer=3, identity=False)
    ocr_scene = _image(201, "仙缘挑战提示", layer=None, identity=False)
    ocr_scene["id"] = "frame-201"
    ocr_scene["filename"] = ""
    ocr_scene["shapes"] = [{
        "id": "confirm",
        "isSceneIdentity": True,
        "ocrReg": "继续|是否.*挑战",
    }]
    tree = [world, material, ocr_scene]
    images = {34: world, 300: material, 201: ocr_scene}

    assert default_recognition_candidate_ids(tree, images) == [34, 201]
    assert expand_graph_candidate_ids(tree, images, [201]) == [201]


def test_default_candidates_support_popup_path_filter_without_tree_semantics():
    world = _image(34, "世界", layer=1)
    popup = _image(47, "所有提示窗口", layer=None)
    tree = [
        {"type": "folder", "title": "场景", "children": [world]},
        {"type": "folder", "title": "弹窗", "children": [popup]},
    ]
    images = {34: world, 47: popup}

    assert default_recognition_candidate_ids(tree, images) == [34, 47]
    assert default_recognition_candidate_ids(tree, images, include_popups=True) == [47]
    assert default_recognition_candidate_ids(tree, images, include_popups=False) == [34]
