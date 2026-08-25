from __future__ import annotations

from scripts.fanxiu_layer1_contract import (
    FOLLOW_UP_SCENES,
    LAYER1_SCENE_IDS,
    SCENE_DESTINATIONS,
    refactor_layer1_tree,
    validate_layer1_contract,
)


def _image(scene_id: int, *, layer: int = 1, shapes: list[dict] | None = None) -> dict:
    return {
        "id": f"image-{scene_id}",
        "type": "image",
        "title": f"scene-{scene_id}",
        "filename": f"{scene_id:04d}.png",
        "layer": layer,
        "shapes": shapes or [],
        "children": [],
    }


def _folder(title: str, *children: dict) -> dict:
    return {
        "id": f"folder-{title}",
        "type": "folder",
        "title": title,
        "filename": "",
        "children": list(children),
    }


def _tree() -> list[dict]:
    scene_ids = sorted({scene_id for values in SCENE_DESTINATIONS.values() for scene_id in values})
    world = _image(
        20,
        shapes=[{"id": "shape-20-xianyan", "title": "仙园游宴", "sceneJumpTarget": "624(16),630(7)"}],
    )
    hubs = [_image(scene_id) for scene_id in sorted(LAYER1_SCENE_IDS - {20})]
    polluted = [_image(scene_id) for scene_id in scene_ids]
    node_269 = _image(
        269,
        layer=2,
        shapes=[{"id": "shape-269-back", "title": "返回", "sceneJumpTarget": "645(1),34"}],
    )
    node_340 = _image(
        340,
        layer=2,
        shapes=[{"id": "shape-340-back", "title": "返回", "sceneJumpTarget": "304(18),631(1),279(1)"}],
    )
    return [
        _folder("场景", *hubs, world, *polluted),
        _folder(
            "日常",
            _folder("仙府"),
            _folder("仙侣历练"),
            node_269,
            node_340,
        ),
        _folder("日程", _folder("资源榜"), _folder("仙宴")),
        _folder("活动"),
    ]


def _find_scene(tree: list[dict], scene_id: int) -> dict:
    stack = list(tree)
    while stack:
        node = stack.pop()
        if node.get("filename") == f"{scene_id:04d}.png":
            return node
        stack.extend(node.get("children") or [])
    raise AssertionError(scene_id)


def test_refactor_keeps_only_stable_hubs_in_layer1_and_moves_business_pages() -> None:
    before = _tree()
    before_shapes = {
        scene_id: _find_scene(before, scene_id)["shapes"]
        for values in SCENE_DESTINATIONS.values()
        for scene_id in values
    }

    migrated, report = refactor_layer1_tree(before)

    assert report["layer1"] == sorted(LAYER1_SCENE_IDS)
    assert validate_layer1_contract(migrated)["layer1"] == sorted(LAYER1_SCENE_IDS)
    assert all(_find_scene(migrated, scene_id)["layer"] == 2 for scene_id in before_shapes)
    assert all(_find_scene(migrated, scene_id)["shapes"] == shapes for scene_id, shapes in before_shapes.items())
    assert _find_scene(migrated, 20)["shapes"][0]["sceneJumpTarget"] == "624(16),630(7)"
    assert _find_scene(migrated, 269)["shapes"][0]["sceneJumpTarget"] == "34"
    assert _find_scene(migrated, 340)["shapes"][0]["sceneJumpTarget"] == "304(18),279(1)"
    assert report["follow_up"] == FOLLOW_UP_SCENES


def test_refactor_is_idempotent() -> None:
    once, _report = refactor_layer1_tree(_tree())
    twice, second_report = refactor_layer1_tree(once)

    assert twice == once
    assert second_report["layer1"] == sorted(LAYER1_SCENE_IDS)
