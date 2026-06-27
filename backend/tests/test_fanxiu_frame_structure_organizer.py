from backend.core.fanxiu.data_annotation.frame_structure_organizer import organize_frame_structure_in_tree


def _shape(title: str, *, identity: bool = True) -> dict:
    return {
        "id": f"shape-{title}",
        "title": title,
        "isSceneIdentity": identity,
        "sceneIdentityRole": "required" if identity else "off",
        "sceneIdentityScope": "local" if identity else "none",
        "imageMatchRole": "required" if identity else "off",
        "x": 0,
        "y": 0,
        "w": 0.1,
        "h": 0.1,
    }


def _image(number: int, title: str, shapes: list[dict], *, layer: int = 2) -> dict:
    return {
        "type": "image",
        "title": title,
        "filename": f"{number:04d}.png",
        "width": 900,
        "height": 1600,
        "layer": layer,
        "shapes": shapes,
        "children": [],
    }


def test_frame_structure_organizer_adopts_children_by_shared_identity_anchor():
    tree = [
        {
            "type": "folder",
            "title": "邮件",
            "children": [
                _image(121, "邮件", [_shape("A"), _shape("B")]),
                _image(122, "邮件内容", [_shape("C")]),
                _image(123, "邮件内容", [_shape("D")]),
            ],
        }
    ]
    scores = {
        (121, "A", 122): 96,
        (121, "A", 123): 96,
        (121, "B", 122): 0,
        (121, "B", 123): 0,
    }

    def score_shape(parent, shape, child):
        return scores.get((int(parent["filename"][:4]), shape["title"], int(child["filename"][:4])), 0)

    organized, stats = organize_frame_structure_in_tree(tree, score_shape=score_shape)
    folder_children = organized[0]["children"]
    parent = folder_children[0]

    assert [item["filename"] for item in folder_children] == ["0121.png"]
    assert [item["filename"] for item in parent["children"]] == ["0122.png", "0123.png"]
    assert stats.adoption_count == 2
    assert [(item.parent_id, item.child_id) for item in stats.adoptions] == [(121, 122), (121, 123)]
    assert parent["shapes"][0]["sceneIdentityRole"] == "required"
    assert parent["shapes"][1]["sceneIdentityRole"] == "off"
    assert stats.demoted_identities == [{"image_id": 121, "shape_title": "B"}]


def test_frame_structure_organizer_does_not_use_jump_targets_or_titles():
    tree = [
        {
            "type": "folder",
            "title": "邮件",
            "children": [
                _image(121, "父", [_shape("A")]),
                {**_image(122, "完全不同标题", [_shape("C")]), "shapes": [_shape("C"), {**_shape("返回", identity=False), "sceneJumpTarget": "121(1)"}]},
            ],
        }
    ]

    def score_shape(parent, shape, child):
        return 0

    organized, stats = organize_frame_structure_in_tree(tree, score_shape=score_shape)

    assert [item["filename"] for item in organized[0]["children"]] == ["0121.png", "0122.png"]
    assert stats.adoption_count == 0


def test_frame_structure_organizer_is_idempotent_for_existing_subframes():
    tree = [
        {
            "type": "folder",
            "title": "邮件",
            "children": [
                {
                    **_image(121, "邮件", [_shape("A")]),
                    "children": [
                        _image(122, "邮件内容", [_shape("C")]),
                        _image(123, "邮件内容", [_shape("D")]),
                    ],
                }
            ],
        }
    ]

    def score_shape(parent, shape, child):
        return 100

    organized, stats = organize_frame_structure_in_tree(tree, score_shape=score_shape)

    assert organized == tree
    assert stats.adoption_count == 0


def test_frame_structure_organizer_adopts_layer_roots_across_business_folders():
    tree = [
        {
            "type": "folder",
            "title": "默认分组",
            "children": [_image(71, "修仙传游历", [_shape("修仙传")])],
        },
        {
            "type": "folder",
            "title": "日常",
            "children": [
                {
                    "type": "folder",
                    "title": "供奉",
                    "children": [_image(251, "供奉", [_shape("供奉")])],
                }
            ],
        },
    ]

    def score_shape(parent, shape, child):
        key = (int(parent["filename"][:4]), shape["title"], int(child["filename"][:4]))
        return 96 if key == (71, "修仙传", 251) else 0

    organized, stats = organize_frame_structure_in_tree(tree, score_shape=score_shape, scope="layer")
    parent = organized[0]["children"][0]

    assert [item["filename"] for item in organized[1]["children"][0]["children"]] == []
    assert [item["filename"] for item in parent["children"]] == ["0251.png"]
    assert stats.adoption_count == 1
    assert [(item.parent_id, item.child_id) for item in stats.adoptions] == [(71, 251)]


def test_frame_structure_organizer_sibling_scope_does_not_cross_business_folders():
    tree = [
        {"type": "folder", "title": "默认分组", "children": [_image(71, "修仙传游历", [_shape("修仙传")])]},
        {"type": "folder", "title": "日常", "children": [_image(251, "供奉", [_shape("供奉")])]},
    ]

    def score_shape(parent, shape, child):
        return 100

    organized, stats = organize_frame_structure_in_tree(tree, score_shape=score_shape, scope="sibling")

    assert organized == tree
    assert stats.adoption_count == 0


def test_frame_structure_organizer_normalizes_layers_by_scene_identity_for_all_frames():
    tree = [
        {
            "type": "folder",
            "title": "日常",
            "children": [
                _image(250, "宝匣", [_shape("宝匣")], layer=3),
                _image(251, "无标识", [_shape("领取", identity=False)], layer=2),
                {
                    **_image(252, "父场景", [_shape("父")], layer=1),
                    "children": [
                        _image(253, "子场景", [_shape("子")], layer=3),
                        _image(254, "子素材", [], layer=2),
                    ],
                },
            ],
        }
    ]

    def score_shape(parent, shape, child):
        return 0

    organized, stats = organize_frame_structure_in_tree(tree, score_shape=score_shape)
    children = organized[0]["children"]

    assert children[0]["layer"] == 2
    assert children[1]["layer"] == 3
    assert children[2]["layer"] == 1
    assert children[2]["children"][0]["layer"] == 2
    assert children[2]["children"][1]["layer"] == 3
    assert stats.layer_update_count == 4
    assert {(item["image_id"], item["from_layer"], item["to_layer"]) for item in stats.layer_updates} == {
        (250, 3, 2),
        (251, 2, 3),
        (253, 3, 2),
        (254, 2, 3),
    }


def test_frame_structure_organizer_downgrades_parent_after_identity_demotion():
    tree = [
        {
            "type": "folder",
            "title": "邮件",
            "children": [
                _image(121, "邮件", [_shape("A"), _shape("B")], layer=2),
                _image(122, "邮件内容", [_shape("C")], layer=2),
            ],
        }
    ]

    def score_shape(parent, shape, child):
        return 96 if shape["title"] == "A" else 0

    organized, stats = organize_frame_structure_in_tree(tree, score_shape=score_shape)
    parent = organized[0]["children"][0]

    assert parent["layer"] == 2
    assert parent["shapes"][0]["sceneIdentityRole"] == "required"
    assert parent["shapes"][1]["sceneIdentityRole"] == "off"
    assert stats.layer_update_count == 0
