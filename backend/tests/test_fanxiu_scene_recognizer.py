from pyxllib.autogui import SceneRecognizer


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
