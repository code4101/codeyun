from copy import deepcopy

import pytest

from scripts import fanxiu_install_modao_assets as installer


def _folder(folder_id: str, title: str, children: list[dict] | None = None) -> dict:
    return {
        "id": folder_id,
        "type": "folder",
        "title": title,
        "children": list(children or []),
        "filename": "",
    }


def _image(image_id: str, filename: str) -> dict:
    return {
        "id": image_id,
        "type": "image",
        "title": filename,
        "filename": filename,
        "children": [],
    }


def test_modao_installer_migrates_legacy_path_and_is_idempotent() -> None:
    existing_target = _folder(
        "target-existing",
        "魔道入侵",
        [_image("target-510", "0510.png")],
    )
    gameplay = _folder("gameplay", "玩法榜", [existing_target])
    schedule = _folder("schedule", "日程", [gameplay])
    legacy = _folder(
        "legacy-modao",
        "魔道入侵",
        [
            _image("legacy-509", "0509.png"),
            _image("legacy-510-duplicate", "0510.png"),
            _folder("legacy-notes", "人工复核"),
        ],
    )
    activity = _folder("activity", "活动", [legacy])
    tree = [
        schedule,
        activity,
        _folder("other", "其它", [_image("wrong-511", "0511.png")]),
    ]
    nodes = [
        _image("new-509", "0509.png"),
        _image("new-510", "0510.png"),
        _image("new-511", "0511.png"),
        _image("new-512", "0512.png"),
    ]

    first = installer.install_nodes_into_tree(tree, nodes)
    target = installer._folder_at_path(tree, installer.TARGET_FOLDER_PATH)

    assert target is existing_target
    assert first["migrated_count"] == 2
    assert first["removed_duplicate_count"] == 1
    assert [node["filename"] for node in first["added_nodes"]] == [
        "0511.png",
        "0512.png",
    ]
    assert {
        node["filename"]
        for node in target["children"]
        if node.get("type") == "image"
    } == {
        "0509.png",
        "0510.png",
        "0511.png",
        "0512.png",
    }
    assert installer._folder_at_path(tree, installer.LEGACY_FOLDER_PATH) is None
    assert any(node.get("id") == "legacy-notes" for node in target["children"])

    second = installer.install_nodes_into_tree(
        tree,
        [_image(f"repeat-{index}", filename) for index, filename in enumerate(
            ("0509.png", "0510.png", "0511.png", "0512.png"),
            start=1,
        )],
    )

    assert second["folder"] is existing_target
    assert second["added_nodes"] == []
    assert second["migrated_count"] == 0
    assert second["removed_duplicate_count"] == 0
    assert second["changed"] is False
    assert len(existing_target["children"]) == 5


def test_modao_authoritative_folder_path_is_locked() -> None:
    assert installer.TARGET_FOLDER_PATH == ("日程", "玩法榜", "魔道入侵")
    assert installer.LEGACY_FOLDER_PATH == ("活动", "魔道入侵")


def test_explore_assets_expose_map_counter_and_non_back_dialog_close() -> None:
    nodes = {node["filename"]: node for node in installer.build_nodes()}
    map_shapes = {shape["title"]: shape for shape in nodes["0512.png"]["shapes"]}
    dialog_shapes = {shape["title"]: shape for shape in nodes["0514.png"]["shapes"]}

    assert map_shapes["可用探查次数"]["ocrMatchMode"] == "regex"
    assert map_shapes["可用探查次数"]["ocrText"] == r"\d+\s*/\s*\d+"
    assert map_shapes["可用探查次数"]["isSceneIdentity"] is False
    assert map_shapes["可用探查次数"]["ocrEnabled"] is True
    assert map_shapes["可用探查次数"]["y"] > 0.6
    assert map_shapes["挑战事件"]["y"] < 0.1
    outside_close = dialog_shapes["返回"]
    assert outside_close["sceneJumpTarget"] == "513"
    assert outside_close["x"] < 0.10
    assert outside_close["y"] > 0.90
    assert "折角装饰" in outside_close["description"]
    assert dialog_shapes["数量滑块游标"]["floating"] is True
    assert dialog_shapes["数量滑块游标"]["imageMatchRole"] == "required"
    assert dialog_shapes["使用数量为1"]["imageMatchRole"] == "required"
    assert dialog_shapes["数量滑轨左端"]["x"] < dialog_shapes["数量滑轨右端"]["x"]


def test_map_entry_confirmation_has_unique_full_identity_and_formal_actions() -> None:
    node = next(node for node in installer.build_nodes() if node["filename"] == "0517.png")
    shapes = {shape["title"]: shape for shape in node["shapes"]}

    assert shapes["确认离开提示"]["ocrText"] == "确认离开当前地图前往沙盘进入魔道入侵玩法"
    assert shapes["确认离开提示"]["isSceneIdentity"] is True
    for title, jump in (("取消", "509"), ("确认", "512")):
        assert shapes[title]["isSceneIdentity"] is False
        assert shapes[title]["ocrMatchRole"] == "required"
        assert shapes[title]["sceneJumpTarget"] == jump


def test_magic_entry_transition_is_read_only_and_waited_out() -> None:
    node = next(node for node in installer.build_nodes() if node["filename"] == "0641.png")
    shapes = {shape["title"]: shape for shape in node["shapes"]}

    assert shapes["情报角色"]["ocrText"] == r"陈巧倩\s*[:：]?"
    assert shapes["魔道情报文本"]["ocrText"] == r"详细的魔道|魔道情报"
    assert shapes["情报角色"]["isSceneIdentity"] is True
    assert shapes["魔道情报文本"]["isSceneIdentity"] is True
    assert "对话" not in shapes


def test_existing_scene_is_upgraded_by_filename_and_second_merge_is_idempotent() -> None:
    scene = _image("stable-scene-id", "0512.png")
    scene["title"] = "旧魔道地图"
    scene["width"] = 900
    scene["height"] = 1600
    scene["shapes"] = [{"id": "keep-shape-id", "title": "探查", "x": 0.1}]
    target = _folder("target", "魔道入侵", [scene])
    tree = [_folder("schedule", "日程", [_folder("gameplay", "玩法榜", [target])])]
    desired = next(
        node for node in installer.build_nodes() if node["filename"] == "0512.png"
    )

    first = installer.install_nodes_into_tree(tree, [desired])

    assert first["changed"] is True
    assert first["added_nodes"] == []
    assert first["updated_nodes"] == [scene]
    assert scene["id"] == "stable-scene-id"
    assert next(shape for shape in scene["shapes"] if shape["title"] == "探查")["id"] == "keep-shape-id"
    scene.pop("_source")

    second_desired = next(
        node for node in installer.build_nodes() if node["filename"] == "0512.png"
    )
    second = installer.install_nodes_into_tree(tree, [second_desired])

    assert second["changed"] is False
    assert second["updated_nodes"] == []


def test_new_read_only_scenes_stay_in_authoritative_folder_without_mutating_509() -> None:
    original_shapes = [
        {"id": "old-identity", "title": "活动结束倒计时", "sceneJumpTarget": ""},
        {"id": "old-map", "title": "前往大地图", "sceneJumpTarget": "512(3)"},
    ]
    scene_509 = _image("existing-509", "0509.png")
    scene_509["shapes"] = deepcopy(original_shapes)
    target = _folder("target", "魔道入侵", [scene_509])
    tree = [_folder("schedule", "日程", [_folder("gameplay", "玩法榜", [target])])]
    new_nodes = [
        {
            **_image(f"scene-{number}", f"0{number}.png"),
            "shapes": [
                {"title": "返回", "ocrText": "", "sceneJumpTarget": "509"},
                {"title": "稳定身份", "ocrText": "魔道入侵", "sceneJumpTarget": ""},
            ],
        }
        for number in (519, 520, 521)
    ]

    installer.install_nodes_into_tree(tree, new_nodes)

    assert scene_509["shapes"] == original_shapes
    assert installer._folder_at_path(tree, installer.TARGET_FOLDER_PATH) is target
    assert {item["filename"] for item in target["children"]} == {
        "0509.png",
        "0519.png",
        "0520.png",
        "0521.png",
    }
    assert all(
        installer._folder_at_path(tree, path) is None
        for path in (("活动", "魔道入侵"), ("日程", "魔道入侵"))
    )


def test_read_only_scenes_reject_purchase_exchange_and_reward_actions() -> None:
    target = _folder("target", "魔道入侵")
    tree = [_folder("schedule", "日程", [_folder("gameplay", "玩法榜", [target])])]
    unsafe = {
        **_image("scene-519", "0519.png"),
        "shapes": [
            {"title": "查看奖励", "ocrText": "查看奖励", "sceneJumpTarget": "522"},
        ],
    }

    with pytest.raises(RuntimeError, match="禁止安装购买/兑换/奖励动作"):
        installer.install_nodes_into_tree(tree, [unsafe])


def test_exchange_scene_accepts_both_wallet_label_variants() -> None:
    nodes = {node["filename"]: node for node in installer.build_nodes()}
    wallet = next(
        shape
        for shape in nodes["0519.png"]["shapes"]
        if shape["title"] == "当前拥有魔晶"
    )

    assert wallet["ocrMatchMode"] == "regex"
    assert wallet["ocrText"] == r"当前拥有(?:位面)?魔晶"
    assert wallet["isSceneIdentity"] is True
    assert wallet["sceneIdentityRole"] == "required"


def test_exchange_scene_reuses_proven_five_row_slot_geometry() -> None:
    nodes = {node["filename"]: node for node in installer.build_nodes()}
    shapes = {shape["title"]: shape for shape in nodes["0519.png"]["shapes"]}

    assert shapes["商品列表"]["sceneJumpTarget"] == ""
    rows = [shapes[f"商品行{index}"] for index in range(1, 6)]
    assert [row["y"] for row in rows] == [0.205, 0.3175, 0.43, 0.5425, 0.655]
    assert all(row["x"] == 0.06 and row["w"] == 0.87 for row in rows)
    assert all(row["sceneJumpTarget"] == "" for row in rows)


def test_rank_scene_image_discriminators_exclude_tab_text() -> None:
    nodes = {node["filename"]: node for node in installer.build_nodes()}

    for filename, title in (("0520.png", "个人选中态"), ("0521.png", "位面选中态")):
        shape = next(shape for shape in nodes[filename]["shapes"] if shape["title"] == title)
        assert shape["isSceneIdentity"] is True
        assert shape["imageMatchRole"] == "required"
        assert shape["ocrMatchRole"] == "off"
        assert shape["ocrText"] == ""
        assert shape["y"] >= 0.1575
        assert shape["h"] <= 0.012
