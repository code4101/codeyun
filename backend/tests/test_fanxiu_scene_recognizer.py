from pyxllib.autogui import SceneRecognizer
from pyxllib.autogui.matching import SceneScorer


def test_scene_recognizer_uses_best_preferred_candidate():
    ctx = {"images": {34: {"title": "#34"}, 66: {"title": "#66"}}}
    recognizer = SceneRecognizer(
        score_image=lambda _ctx, image, _frame: {"#34": 90.0, "#66": 80.0}[image["title"]],
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_number(ctx, "frame", preferred_scene_ids=[66, 34]) == (34, 90.0)


def test_scene_recognizer_returns_none_when_best_score_below_threshold():
    ctx = {"images": {34: {"title": "#34"}, 66: {"title": "#66"}}}
    recognizer = SceneRecognizer(
        score_image=lambda _ctx, image, _frame: {"#34": 79.0, "#66": 60.0}[image["title"]],
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_number(ctx, "frame") == (None, 79.0)


def test_scene_recognizer_prefers_smaller_scene_id_when_scores_tie():
    ctx = {"images": {66: {"title": "#66"}, 34: {"title": "#34"}}}
    recognizer = SceneRecognizer(
        score_image=lambda *_args: 90.0,
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_number(ctx, "frame") == (34, 90.0)


def test_scene_recognizer_identifies_key_by_score_then_priority():
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
        key_priorities={"world": 0, "settings": 3, "gift": 9},
    )

    assert recognizer.identify_scene_key({}, "frame", keys=["world", "settings", "gift"]) == ("gift", 90.0)
    assert recognizer.scene_matches_key("settings", 80.0) is True


def test_scene_recognizer_returns_deepest_scene_tree_match():
    parent = {
        "type": "image",
        "filename": "0265.png",
        "title": "法则之主选择页",
        "sceneIdentityLevel": 2,
        "children": [
            {
                "type": "image",
                "filename": "0266.png",
                "title": "法则之主拜谒详情",
                "sceneIdentityLevel": 1,
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
        "sceneIdentityLevel": 2,
        "children": [
            {
                "type": "image",
                "filename": "0266.png",
                "title": "法则之主拜谒详情",
                "sceneIdentityLevel": 1,
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


def test_scene_recognizer_ignores_non_scene_frames_without_candidates():
    scene = {"type": "image", "filename": "0034.png", "title": "世界", "sceneIdentityLevel": 2}
    helper = {"type": "image", "filename": "0999.png", "title": "普通模板", "sceneIdentityLevel": 0}
    ctx = {"asset_tree": [scene, helper], "images": {34: scene, 999: helper}}
    recognizer = SceneRecognizer(
        score_image=lambda _ctx, image, _frame: 100.0 if image["title"] == "普通模板" else 85.0,
        threshold_for_scene_id=lambda _scene_id: 80.0,
    )

    assert recognizer.identify_scene_tree_number(ctx, "frame") == (34, 85.0)


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
        "sceneIdentityLevel": 2,
        "children": [
            {
                "type": "image",
                "filename": "0266.png",
                "title": "法则之主拜谒详情",
                "sceneIdentityLevel": 1,
            },
            {
                "type": "image",
                "filename": "0267.png",
                "title": "无关子页",
                "sceneIdentityLevel": 1,
            },
        ],
    }
    other = {"type": "image", "filename": "0034.png", "title": "世界", "sceneIdentityLevel": 2}
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
