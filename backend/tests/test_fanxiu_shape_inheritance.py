import copy
import json

import pytest
from pyxllib.autogui import View

from backend.core.fanxiu.data_annotation.behavior_tree_runtime import BehaviorTreeRuntimeRunner
from backend.core.fanxiu.data_annotation.shape_inheritance import (
    INHERITANCE_HOST_SCENE_ID,
    INHERITANCE_SOURCE_SCENE_ID,
    ShapeInheritanceError,
    find_raw_shape_for_effective,
    resolve_shape_inheritance,
)


def _image(scene_id: int, shapes: list[dict], *, parents: str = "", layer: int = 2) -> dict:
    image = {
        "id": f"scene-{scene_id}",
        "type": "image",
        "title": f"场景 {scene_id}",
        "filename": f"{scene_id:04d}.png",
        "width": 900,
        "height": 1600,
        "layer": layer,
        "shapes": shapes,
        "children": [],
    }
    if parents:
        image["parentSceneIds"] = parents
    return image


def _shape(shape_id: str, title: str, **extra) -> dict:
    return {
        "id": shape_id,
        "title": title,
        "x": 0.1,
        "y": 0.2,
        "w": 0.3,
        "h": 0.1,
        **extra,
    }


def test_multiple_inheritance_keeps_declared_order_and_child_host_image():
    tree = [
        _image(10, [_shape("a", "A", imageMatchRole="required")]),
        _image(20, [_shape("b", "B", ocrText="按钮", ocrMatchRole="required")]),
        _image(30, [_shape("c", "C")], parents="10, 20"),
    ]
    raw_before = copy.deepcopy(tree)

    resolution = resolve_shape_inheritance(tree)
    child = resolution.images[30]

    assert [shape["id"] for shape in child["shapes"]] == ["a", "b", "c"]
    assert child["filename"] == "0030.png"
    assert child["width"] == 900
    assert [shape[INHERITANCE_SOURCE_SCENE_ID] for shape in child["shapes"]] == [10, 20, 30]
    assert all(shape[INHERITANCE_HOST_SCENE_ID] == 30 for shape in child["shapes"])
    assert View(child).get_shape("A").parent_view.raw is child
    assert tree == raw_before


def test_inheritance_only_resolves_shapes_and_preserves_host_layer_annotation():
    identity = _shape(
        "identity",
        "场景标识",
        isSceneIdentity=True,
        sceneIdentityRole="required",
        imageMatchRole="required",
    )
    resolution = resolve_shape_inheritance([
        _image(10, [identity]),
        _image(20, [], parents="10"),
        _image(30, [], layer=1),
    ])

    assert View(resolution.images[20]).layer == 2
    assert View(resolution.images[30]).layer == 1
    assert resolution.images[30]["layer"] == 1


def test_diamond_inheritance_deduplicates_the_same_source_shape():
    tree = [
        _image(1, [_shape("shared", "返回")]),
        _image(2, [], parents="1"),
        _image(3, [], parents="1"),
        _image(4, [], parents="2, 3"),
    ]

    resolution = resolve_shape_inheritance(tree)

    assert [shape["id"] for shape in resolution.images[4]["shapes"]] == ["shared"]
    assert resolution.images[4]["shapes"][0][INHERITANCE_SOURCE_SCENE_ID] == 1


@pytest.mark.parametrize(
    ("tree", "message"),
    [
        ([_image(1, [], parents="2")], "不存在的父场景 #2"),
        ([_image(1, [], parents="2"), _image(2, [], parents="1")], "#1 -> #2 -> #1"),
        ([_image(1, [], parents="1")], "不能继承自身"),
    ],
)
def test_invalid_inheritance_fails_with_actionable_error(tree, message):
    with pytest.raises(ShapeInheritanceError, match=message):
        resolve_shape_inheritance(tree)


def test_effective_shape_maps_back_to_raw_annotation_owner():
    parent_shape = _shape("shared", "返回", sceneJumpTarget="34")
    tree = [_image(1, [parent_shape]), _image(2, [], parents="1")]
    resolution = resolve_shape_inheritance(tree)
    effective = resolution.images[2]["shapes"][0]

    raw = find_raw_shape_for_effective(resolution.raw_images, effective)

    assert raw is parent_shape


def test_runtime_graph_derives_child_jump_edge_from_effective_shapes():
    runner = BehaviorTreeRuntimeRunner()
    tree = [
        _image(1, [_shape("go-home", "返回", sceneJumpTarget="34")]),
        _image(2, [], parents="1"),
        _image(34, [_shape("home-id", "场景标识", isSceneIdentity=True)]),
    ]

    edges = runner._scene_jump_edges(tree)

    inherited_edge = next(edge for edge in edges[2] if edge["shape"]["id"] == "go-home")
    assert inherited_edge["source_id"] == 2
    assert inherited_edge["target_ids"] == [34]
    assert inherited_edge["image"]["filename"] == "0002.png"


def test_runtime_records_inherited_jump_history_on_raw_source(tmp_path):
    runner = BehaviorTreeRuntimeRunner()
    parent_shape = _shape("go-home", "返回", sceneJumpTarget="34")
    tree = [_image(1, [parent_shape]), _image(2, [], parents="1"), _image(34, [])]
    path = tmp_path / "asset-tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    effective_shape = runner._index_images(tree)[2]["shapes"][0]
    ctx = {"asset_tree": tree, "images": runner._index_images(tree)}

    runner._record_scene_jump_landing(
        ctx,
        path,
        tree,
        effective_shape,
        34,
        reason="test",
    )

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written[0]["shapes"][0]["sceneJumpTarget"] == "34(1)"
    assert written[1].get("shapes") == []
    assert not any(key.startswith("_inheritance") for key in written[0]["shapes"][0])
