from __future__ import annotations

from pathlib import Path

from PIL import Image

from scripts.fanxiu_install_dongtian_friend_swap_scene import (
    SCENE_ID,
    authoritative_next_scene_number,
    build_scene_node,
    install_scene_into_tree,
)


def _write_frame(path: Path) -> None:
    Image.new("RGB", (900, 1600), "white").save(path)


def _tree() -> list[dict]:
    return [
        {
            "id": "daily",
            "type": "folder",
            "title": "日常",
            "children": [
                {
                    "id": "dongtian",
                    "type": "folder",
                    "title": "洞天",
                    "children": [
                        {
                            "id": "old",
                            "type": "image",
                            "title": "旧场景",
                            "filename": "0606.png",
                            "shapes": [],
                            "children": [],
                        }
                    ],
                }
            ],
        }
    ]


def test_friend_swap_scene_uses_authoritative_next_number(tmp_path: Path) -> None:
    frame = tmp_path / "frame.png"
    _write_frame(frame)
    tree = _tree()

    changed, node = install_scene_into_tree(tree, frame)

    assert changed is True
    assert authoritative_next_scene_number(_tree()) == 607
    assert node["filename"] == "0607.png"
    assert node["id"] == SCENE_ID


def test_friend_swap_scene_is_idempotent(tmp_path: Path) -> None:
    frame = tmp_path / "frame.png"
    _write_frame(frame)
    tree = _tree()

    first_changed, first = install_scene_into_tree(tree, frame)
    second_changed, second = install_scene_into_tree(tree, frame)

    assert first_changed is True
    assert second_changed is False
    assert second is first


def test_friend_swap_scene_requires_two_ocr_identities_and_keeps_swap_unarmed(
    tmp_path: Path,
) -> None:
    frame = tmp_path / "frame.png"
    _write_frame(frame)

    node = build_scene_node(607, frame)
    shapes = {shape["title"]: shape for shape in node["shapes"]}

    assert {
        title
        for title, shape in shapes.items()
        if shape["sceneIdentityRole"] == "required"
    } == {"我方当前战力", "互换采气"}
    assert shapes["我方当前战力"]["ocrText"] == "我方当前战力"
    assert shapes["我方当前战力"]["ocrMatchMode"] == "contains"
    assert shapes["互换采气"]["ocrText"] == r"互换采[气无]"
    assert shapes["互换采气"]["sceneJumpTarget"] == ""
    assert "高风险动作" in shapes["互换采气"]["description"]
    assert shapes["返回"]["sceneJumpTarget"] == ""
