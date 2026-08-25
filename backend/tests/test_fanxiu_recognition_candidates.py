from pyxllib.autogui import View

from backend.core.fanxiu.data_annotation.recognition_candidates import (
    default_recognition_candidate_ids,
    default_recognition_candidate_layers,
    layer3_recognition_candidate_ids,
    recognition_candidate_ids_by_layer,
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


def test_asset_nesting_does_not_change_default_candidate_enumeration():
    child = _image(266, "法则详情", layer=2)
    parent = _image(265, "法则选择", layer=1)
    parent["children"] = [child]
    tree = [{"type": "folder", "title": "拜谒", "children": [parent]}]
    images = {265: parent, 266: child}

    assert default_recognition_candidate_ids(tree, images) == [265, 266]


def test_default_identity_candidates_and_layer3_candidates_are_separate():
    layer1 = _image(265, "法则选择", layer=1)
    layer2 = _image(266, "法则详情", layer=2)
    layer3 = _image(267, "法则素材", layer=3, identity=False)
    tree = [layer1, layer2, layer3]
    images = {265: layer1, 266: layer2, 267: layer3}

    assert default_recognition_candidate_ids(tree, images) == [265, 266]
    assert layer3_recognition_candidate_ids(tree, images) == [267]


def test_assets_without_scene_identity_fall_back_to_layer3():
    explicit_layer1_material = _image(900, "动作素材一", layer=1, identity=False)
    explicit_layer2_material = _image(901, "动作素材二", layer=2, identity=False)
    explicit_layer3_material = _image(902, "动作素材三", layer=3, identity=False)
    tree = [explicit_layer1_material, explicit_layer2_material, explicit_layer3_material]
    images = {
        900: explicit_layer1_material,
        901: explicit_layer2_material,
        902: explicit_layer3_material,
    }

    assert recognition_candidate_ids_by_layer(tree, images, 1) == []
    assert recognition_candidate_ids_by_layer(tree, images, 2) == []
    assert default_recognition_candidate_layers(tree, images) == [(1, []), (2, [])]
    assert default_recognition_candidate_ids(tree, images) == []
    assert layer3_recognition_candidate_ids(tree, images) == [900, 901, 902]


def test_default_candidates_keep_layer1_and_layer2_as_separate_passes():
    layer2_first = _image(266, "法则详情", layer=2)
    layer1_second = _image(265, "法则选择", layer=1)
    tree = [layer2_first, layer1_second]
    images = {265: layer1_second, 266: layer2_first}

    assert recognition_candidate_ids_by_layer(tree, images, 1) == [265]
    assert recognition_candidate_ids_by_layer(tree, images, 2) == [266]
    assert default_recognition_candidate_layers(tree, images) == [
        (1, [265]),
        (2, [266]),
    ]


def test_default_candidates_include_all_identity_frames_and_filter_material():
    world = _image(34, "世界", layer=1)
    material = _image(300, "素材", layer=3, identity=False)
    ocr_scenes = []
    for scene_id, title in ((201, "仙缘挑战提示"), (202, "仙缘挑战结果"), (203, "仙缘离开确认")):
        image = _image(scene_id, title, layer=None, identity=False)
        image["id"] = f"frame-{scene_id}"
        image["filename"] = ""
        image["shapes"] = [{
            "id": "identity",
            "isSceneIdentity": True,
            "ocrReg": title,
        }]
        ocr_scenes.append(image)
    tree = [world, material, *ocr_scenes]
    images = {34: world, 300: material, **{201 + index: image for index, image in enumerate(ocr_scenes)}}

    assert [View(image).layer for image in ocr_scenes] == [2, 2, 2]
    assert default_recognition_candidate_ids(tree, images) == [34, 201, 202, 203]


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


def test_default_candidates_exclude_floating_overlay_identity_frame():
    world = _image(34, "世界", layer=1)
    overlay = _image(421, "气泡", layer=2, identity=False)
    overlay["shapes"] = [{
        "id": "bubble",
        "title": "气泡",
        "isSceneIdentity": True,
        "floating": True,
    }]
    tree = [world, overlay]
    images = {34: world, 421: overlay}

    assert default_recognition_candidate_ids(tree, images) == [34]
