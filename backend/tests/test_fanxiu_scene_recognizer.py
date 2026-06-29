from pyxllib.autogui import SceneNavigator, SceneRecognizer
from pyxllib.autogui.matching import SceneScorer


def test_scene_recognizer_uses_first_matching_preferred_candidate():
    ctx = {"images": {34: {"title": "#34"}, 66: {"title": "#66"}}}
    recognizer = SceneRecognizer(
        score_image=lambda _ctx, image, _frame: {"#34": 90.0, "#66": 80.0}[image["title"]],
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_number(ctx, "frame", preferred_scene_ids=[66, 34]) == (66, 80.0)


def test_scene_recognizer_returns_none_when_best_score_below_threshold():
    ctx = {"images": {34: {"title": "#34"}, 66: {"title": "#66"}}}
    recognizer = SceneRecognizer(
        score_image=lambda _ctx, image, _frame: {"#34": 79.0, "#66": 60.0}[image["title"]],
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_number(ctx, "frame") == (None, 79.0)


def test_scene_recognizer_uses_input_order_when_scores_tie():
    ctx = {"images": {66: {"title": "#66"}, 34: {"title": "#34"}}}
    recognizer = SceneRecognizer(
        score_image=lambda *_args: 90.0,
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_number(ctx, "frame") == (66, 90.0)


def test_scene_recognizer_identifies_key_by_input_order():
    images = {
        "world": {"title": "world"},
        "gift": {"title": "gift"},
        "settings": {"title": "settings"},
    }
    recognizer = SceneRecognizer(
        score_image=lambda _ctx, image, _frame: 90.0 if image["title"] in {"world", "gift"} else 80.0,
        threshold_for_scene_id=lambda _scene_id: 80.0,
        image_for_key=lambda _ctx, key: images.get(key),
        threshold_for_key=lambda _key: 80.0,
    )

    assert recognizer.identify_scene_key({}, "frame", keys=["world", "settings", "gift"]) == ("world", 90.0)
    assert recognizer.scene_matches_key("settings", 80.0) is True


def test_scene_recognizer_returns_deepest_scene_tree_match():
    parent = {
        "type": "image",
        "filename": "0265.png",
        "title": "法则之主选择页",
        "layer": 1,
        "children": [
            {
                "type": "image",
                "filename": "0266.png",
                "title": "法则之主拜谒详情",
                "layer": 2,
            }
        ],
    }
    ctx = {"asset_tree": [parent], "images": {265: parent, 266: parent["children"][0]}}
    scores = {"法则之主选择页": 92.0, "法则之主拜谒详情": 90.0}
    recognizer = SceneRecognizer(
        score_image=lambda _ctx, image, _frame: scores[image["title"]],
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_tree_number(ctx, "frame") == (266, 90.0)


def test_scene_recognizer_keeps_parent_when_child_evidence_fails():
    parent = {
        "type": "image",
        "filename": "0265.png",
        "title": "法则之主选择页",
        "layer": 1,
        "children": [
            {
                "type": "image",
                "filename": "0266.png",
                "title": "法则之主拜谒详情",
                "layer": 2,
            }
        ],
    }
    ctx = {"asset_tree": [parent], "images": {265: parent, 266: parent["children"][0]}}
    scores = {"法则之主选择页": 92.0, "法则之主拜谒详情": 40.0}
    recognizer = SceneRecognizer(
        score_image=lambda _ctx, image, _frame: scores[image["title"]],
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_tree_number(ctx, "frame") == (265, 92.0)


def test_scene_recognizer_ignores_layer3_frames_without_candidates():
    scene = {"type": "image", "filename": "0034.png", "title": "世界", "layer": 1}
    helper = {"type": "image", "filename": "0999.png", "title": "普通模板", "layer": 3}
    ctx = {"asset_tree": [scene, helper], "images": {34: scene, 999: helper}}
    recognizer = SceneRecognizer(
        score_image=lambda _ctx, image, _frame: 100.0 if image["title"] == "普通模板" else 85.0,
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_tree_number(ctx, "frame") == (34, 85.0)


def test_scene_recognizer_uses_best_score_for_layer3_weak_similarity_fallback():
    scene = {"type": "image", "filename": "0034.png", "title": "世界", "layer": 1}
    lower = {"type": "image", "filename": "0901.png", "title": "弱相似低分", "layer": 3}
    higher = {"type": "image", "filename": "0902.png", "title": "弱相似高分", "layer": 3}
    ctx = {"asset_tree": [scene, lower, higher], "images": {34: scene, 901: lower, 902: higher}}
    scores = {"世界": 40.0, "弱相似低分": 86.0, "弱相似高分": 93.0}
    recognizer = SceneRecognizer(
        score_image=lambda _ctx, image, _frame: scores[image["title"]],
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_tree_number(ctx, "frame") == (902, 93.0)


def test_scene_recognizer_prefers_explicit_child_identity_before_weak_layer3_fallback():
    parent = {
        "type": "image",
        "filename": "0047.png",
        "title": "所有提示窗口",
        "layer": 2,
        "shapes": [{"id": "line", "title": "分割线", "sceneIdentityRole": "required", "imageMatchRole": "required"}],
        "children": [
            {"type": "image", "filename": "0060.png", "title": "封魔杀", "layer": 3},
            {
                "type": "image",
                "filename": "0278.png",
                "title": "邮件删除确认",
                "layer": 3,
                "shapes": [{"id": "mail", "title": "邮件", "sceneIdentityRole": "required", "ocrMatchRole": "required"}],
            },
        ],
    }
    ctx = {"asset_tree": [parent], "images": {47: parent, 60: parent["children"][0], 278: parent["children"][1]}}
    scores = {"所有提示窗口": 95.0, "封魔杀": 93.0, "邮件删除确认": 100.0}
    recognizer = SceneRecognizer(
        score_image=lambda _ctx, image, _frame: scores[image["title"]],
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_tree_number(ctx, "frame") == (278, 95.0)


def test_scene_recognizer_keeps_parent_when_explicit_child_fails_even_if_weak_child_matches():
    parent = {
        "type": "image",
        "filename": "0047.png",
        "title": "所有提示窗口",
        "layer": 2,
        "shapes": [{"id": "line", "title": "分割线", "sceneIdentityRole": "required", "imageMatchRole": "required"}],
        "children": [
            {"type": "image", "filename": "0060.png", "title": "封魔杀", "layer": 3},
            {
                "type": "image",
                "filename": "0278.png",
                "title": "邮件删除确认",
                "layer": 3,
                "shapes": [{"id": "mail", "title": "邮件", "sceneIdentityRole": "required", "ocrMatchRole": "required"}],
            },
        ],
    }
    ctx = {"asset_tree": [parent], "images": {47: parent, 60: parent["children"][0], 278: parent["children"][1]}}
    scores = {"所有提示窗口": 95.0, "封魔杀": 93.0, "邮件删除确认": 0.0}
    recognizer = SceneRecognizer(
        score_image=lambda _ctx, image, _frame: scores[image["title"]],
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_tree_number(ctx, "frame") == (47, 95.0)


def test_scene_recognizer_trace_records_root_and_child_refinement():
    parent = {
        "type": "image",
        "filename": "0047.png",
        "title": "所有提示窗口",
        "layer": 2,
        "shapes": [{"id": "line", "title": "分割线", "sceneIdentityRole": "required", "imageMatchRole": "required"}],
        "children": [
            {
                "type": "image",
                "filename": "0278.png",
                "title": "邮件删除确认",
                "layer": 3,
                "shapes": [{"id": "mail", "title": "邮件", "sceneIdentityRole": "required", "ocrMatchRole": "required"}],
            },
        ],
    }
    ctx = {"asset_tree": [parent], "images": {47: parent, 278: parent["children"][0]}}
    scores = {"所有提示窗口": 95.0, "邮件删除确认": 99.0}
    recognizer = SceneRecognizer(
        score_image=lambda _ctx, image, _frame: scores[image["title"]],
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )
    trace: list[dict] = []

    assert recognizer.identify_scene_tree_number(ctx, "frame", trace=trace) == (278, 95.0)
    assert trace[0]["event"] == "root_layer_queue"
    assert any(item["event"] == "candidate_group" and item["stage"] == "layer2" and item["selected_ids"] == [47] for item in trace)
    assert any(item["event"] == "candidate_group" and item["stage"] == "children" and item["parent_id"] == 47 and item["selected_ids"] == [278] for item in trace)
    assert trace[-1]["event"] == "final"
    assert trace[-1]["scene_id"] == 278


def test_scene_recognizer_trace_records_batched_stage_timing():
    roots = [
        {"type": "image", "filename": f"{scene_id:04d}.png", "title": f"root-{scene_id}", "layer": 2}
        for scene_id in (101, 102, 103, 104, 105)
    ]
    ctx = {"asset_tree": roots, "images": {101: roots[0], 102: roots[1], 103: roots[2], 104: roots[3], 105: roots[4]}}
    seen: list[int] = []

    def score_image(_ctx, image, _frame):
        scene_id = int(image["filename"].split(".")[0])
        seen.append(scene_id)
        return 90.0 if scene_id == 103 else 40.0

    recognizer = SceneRecognizer(
        score_image=score_image,
        threshold_for_scene_id=lambda _scene_id: 80.0,
        max_parallel_workers=1,
        max_candidate_batch_size=2,
    )
    trace: list[dict] = []

    assert recognizer.identify_scene_tree_number(ctx, "frame", trace=trace) == (103, 90.0)
    batches = [item for item in trace if item["event"] == "candidate_batch" and item["stage"] == "layer2"]
    assert [batch["candidate_ids"] for batch in batches] == [[101, 102], [103, 104]]
    assert all("elapsed_seconds" in batch and "elapsed_text" in batch for batch in batches)
    group = next(item for item in trace if item["event"] == "candidate_group" and item["stage"] == "layer2")
    assert group["batch_size"] == 2
    assert group["batch_count"] == 3
    assert group["processed_count"] == 4
    assert group["stopped_early"] is True
    assert "elapsed_seconds" in group and "elapsed_text" in group
    assert seen == [101, 102, 103, 104]


def test_scene_recognizer_stops_root_layer_scan_after_layer2_popup_match():
    world = {"type": "image", "filename": "0034.png", "title": "世界", "layer": 1}
    popup = {
        "type": "image",
        "filename": "0047.png",
        "title": "所有提示窗口",
        "layer": 2,
        "shapes": [{"id": "line", "title": "分割线", "sceneIdentityRole": "required", "imageMatchRole": "required"}],
        "children": [
            {
                "type": "image",
                "filename": "0278.png",
                "title": "邮件删除确认",
                "layer": 3,
                "shapes": [{"id": "mail", "title": "邮件", "sceneIdentityRole": "required", "ocrMatchRole": "required"}],
            }
        ],
    }
    global_layer3 = {"type": "image", "filename": "0999.png", "title": "全局弱素材", "layer": 3}
    ctx = {"asset_tree": [world, popup, global_layer3], "images": {34: world, 47: popup, 278: popup["children"][0], 999: global_layer3}}
    seen: list[str] = []
    scores = {"世界": 40.0, "所有提示窗口": 95.0, "邮件删除确认": 100.0}

    def score_image(_ctx, image, _frame):
        seen.append(image["title"])
        if image["title"] == "全局弱素材":
            raise AssertionError("layer2 命中后不应继续检测全局 layer3 root")
        return scores[image["title"]]

    recognizer = SceneRecognizer(
        score_image=score_image,
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_tree_number(ctx, "frame") == (278, 95.0)
    assert seen == ["世界", "所有提示窗口", "邮件删除确认"]


def test_scene_recognizer_ignores_layer_when_refining_subframes():
    parent = {
        "type": "image",
        "filename": "0047.png",
        "title": "所有提示窗口",
        "layer": 2,
        "shapes": [{"id": "line", "title": "分割线", "sceneIdentityRole": "required", "imageMatchRole": "required"}],
        "children": [
            {
                "type": "image",
                "filename": "0210.png",
                "title": "二层确认弹窗",
                "layer": 2,
                "shapes": [{"id": "confirm", "title": "确认", "sceneIdentityRole": "required"}],
            },
            {
                "type": "image",
                "filename": "0060.png",
                "title": "三层明确子帧",
                "layer": 3,
                "shapes": [{"id": "child-id", "title": "子帧标识", "sceneIdentityRole": "required"}],
            },
        ],
    }
    ctx = {"asset_tree": [parent], "images": {47: parent, 210: parent["children"][0], 60: parent["children"][1]}}
    seen: list[str] = []

    def score_image(_ctx, image, _frame):
        seen.append(image["title"])
        return {"所有提示窗口": 95.0, "二层确认弹窗": 40.0, "三层明确子帧": 90.0}[image["title"]]

    recognizer = SceneRecognizer(
        score_image=score_image,
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_tree_number(ctx, "frame") == (60, 90.0)
    assert seen == ["所有提示窗口", "二层确认弹窗", "三层明确子帧"]


def test_scene_recognizer_keeps_parent_when_no_direct_child_matches():
    parent = {
        "type": "image",
        "filename": "0047.png",
        "title": "所有提示窗口",
        "layer": 2,
        "shapes": [{"id": "line", "title": "分割线", "sceneIdentityRole": "required", "imageMatchRole": "required"}],
        "children": [
            {
                "type": "image",
                "filename": "0210.png",
                "title": "二层确认弹窗",
                "layer": 2,
                "shapes": [{"id": "confirm", "title": "确认", "sceneIdentityRole": "required"}],
            },
            {
                "type": "image",
                "filename": "0278.png",
                "title": "三层邮件确认",
                "layer": 3,
                "shapes": [{"id": "mail", "title": "邮件", "sceneIdentityRole": "required", "ocrMatchRole": "required"}],
            },
        ],
    }
    ctx = {"asset_tree": [parent], "images": {47: parent, 210: parent["children"][0], 278: parent["children"][1]}}
    seen: list[str] = []

    def score_image(_ctx, image, _frame):
        seen.append(image["title"])
        return {"所有提示窗口": 95.0, "二层确认弹窗": 40.0, "三层邮件确认": 40.0}[image["title"]]

    recognizer = SceneRecognizer(
        score_image=score_image,
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_tree_number(ctx, "frame") == (47, 95.0)
    assert seen == ["所有提示窗口", "二层确认弹窗", "三层邮件确认"]


def test_scene_recognizer_uses_layer_order_within_same_layer():
    first_in_tree = {"type": "image", "filename": "0100.png", "title": "低优先", "layer": 1, "layerOrder": 20}
    second_in_tree = {"type": "image", "filename": "0200.png", "title": "高优先", "layer": 1, "layerOrder": 10}
    ctx = {"asset_tree": [first_in_tree, second_in_tree], "images": {100: first_in_tree, 200: second_in_tree}}
    recognizer = SceneRecognizer(
        score_image=lambda *_args: 100.0,
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_tree_number(ctx, "frame") == (200, 100.0)


def test_scene_recognizer_treats_layer3_frame_as_transparent_parent():
    helper = {
        "type": "image",
        "filename": "0999.png",
        "title": "Layer3素材组",
        "layer": 3,
        "children": [
            {
                "type": "image",
                "filename": "0034.png",
                "title": "世界",
                "layer": 1,
            }
        ],
    }
    ctx = {"asset_tree": [helper], "images": {999: helper, 34: helper["children"][0]}}
    recognizer = SceneRecognizer(
        score_image=lambda _ctx, image, _frame: 100.0 if image["title"] == "Layer3素材组" else 88.0,
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_tree_number(ctx, "frame") == (34, 88.0)


def test_scene_navigator_treats_layer3_frame_as_transparent_parent():
    parent = {
        "type": "image",
        "filename": "0100.png",
        "title": "父场景",
        "layer": 1,
        "children": [
            {
                "type": "image",
                "filename": "0999.png",
                "title": "Layer3素材",
                "layer": 3,
                "shapes": [
                    {"id": "helper-action", "title": "返回", "sceneJumpTarget": "100"},
                ],
                "children": [
                    {
                        "type": "image",
                        "filename": "0101.png",
                        "title": "子场景",
                        "layer": 2,
                        "shapes": [
                            {"id": "return", "title": "返回"},
                        ],
                    }
                ],
            }
        ],
    }

    edges = SceneNavigator([parent]).scene_jump_edges()

    assert 999 not in edges
    assert edges[101][0]["target_ids"] == [100]


def test_scene_navigator_resolves_folder_to_scene_frames_only():
    tree = [
        {
            "type": "folder",
            "title": "候选组",
            "children": [
                {"type": "image", "filename": "0999.png", "title": "素材", "layer": 3},
                {"type": "image", "filename": "0100.png", "title": "场景", "layer": 1},
            ],
        }
    ]

    assert SceneNavigator(tree).resolve_scene_jump_label("候选组") == [100]


def test_scene_scorer_requires_all_scene_identity_shapes():
    image = {
        "type": "image",
        "filename": "0266.png",
        "shapes": [
            {"id": "domain", "title": "法则之主", "sceneIdentityRole": "required", "imageMatchRole": "required"},
            {"id": "detail", "title": "拜谒", "sceneIdentityRole": "required", "imageMatchRole": "required"},
        ],
    }
    scorer = SceneScorer(
        shape_score=lambda _ctx, _image, shape, _frame: 95.0 if shape["id"] == "domain" else 40.0,
        shape_ocr_score=lambda *_args: 0.0,
        threshold=80.0,
    )

    assert scorer.scene_score({}, image, "frame") == 0.0


def test_scene_recognizer_uses_preferred_subtree_without_context_filter():
    parent = {
        "type": "image",
        "filename": "0265.png",
        "title": "法则之主选择页",
        "layer": 1,
        "children": [
            {
                "type": "image",
                "filename": "0266.png",
                "title": "法则之主拜谒详情",
                "layer": 2,
            },
            {
                "type": "image",
                "filename": "0267.png",
                "title": "无关子页",
                "layer": 2,
            },
        ],
    }
    other = {"type": "image", "filename": "0034.png", "title": "世界", "layer": 1}
    ctx = {"asset_tree": [other, parent], "images": {34: other, 265: parent, 266: parent["children"][0], 267: parent["children"][1]}}
    calls = []

    def score_image(_ctx, image, _frame):
        calls.append(image["title"])
        return {"世界": 100.0, "法则之主选择页": 91.0, "法则之主拜谒详情": 93.0, "无关子页": 40.0}[image["title"]]

    recognizer = SceneRecognizer(
        score_image=score_image,
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_tree_number(ctx, "frame", preferred_scene_ids=[266]) == (266, 91.0)
    assert calls == ["法则之主选择页", "法则之主拜谒详情"]


def test_scene_recognizer_selects_best_child_score_when_siblings_match():
    parent = {
        "type": "image",
        "filename": "0299.png",
        "title": "论道",
        "layer": 1,
        "children": [
            {"type": "image", "filename": "0297.png", "title": "三清道场让座"},
            {"type": "image", "filename": "0298.png", "title": "三清道场空位"},
        ],
    }
    ctx = {"asset_tree": [parent], "images": {299: parent, 297: parent["children"][0], 298: parent["children"][1]}}
    calls: list[str] = []

    def score_image(_ctx, image, _frame):
        calls.append(image["title"])
        return {"论道": 95.0, "三清道场让座": 80.0, "三清道场空位": 100.0}[image["title"]]

    recognizer = SceneRecognizer(
        score_image=score_image,
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_tree_number(ctx, "frame", preferred_scene_ids=[299]) == (298, 95.0)
    assert calls == ["论道", "三清道场让座", "三清道场空位"]


def test_scene_recognizer_falls_back_to_default_layers_after_preferred_layer0_misses():
    parent = {
        "type": "image",
        "filename": "0265.png",
        "title": "法则之主选择页",
        "layer": 1,
        "children": [
            {
                "type": "image",
                "filename": "0266.png",
                "title": "法则之主拜谒详情",
                "layer": 2,
            }
        ],
    }
    world = {"type": "image", "filename": "0034.png", "title": "世界", "layer": 1}
    ctx = {"asset_tree": [world, parent], "images": {34: world, 265: parent, 266: parent["children"][0]}}
    calls: list[str] = []

    def score_image(_ctx, image, _frame):
        calls.append(image["title"])
        return {"世界": 90.0, "法则之主选择页": 40.0, "法则之主拜谒详情": 100.0}[image["title"]]

    recognizer = SceneRecognizer(
        score_image=score_image,
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_tree_number(ctx, "frame", preferred_scene_ids=[266]) == (34, 90.0)
    assert calls == ["法则之主选择页", "世界"]
