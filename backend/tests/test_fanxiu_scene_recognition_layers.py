from __future__ import annotations

import threading
import time

from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner


def _scene(scene_id: int, layer: int) -> dict:
    return {
        "type": "image",
        "title": f"scene-{scene_id}",
        "filename": f"{scene_id:04d}.png",
        "layer": layer,
        "shapes": [{"id": f"identity-{scene_id}", "isSceneIdentity": True}],
        "children": [],
    }


def _context() -> dict:
    layer1 = _scene(101, 1)
    layer2 = _scene(201, 2)
    layer0 = _scene(301, 2)
    layer3 = {
        "type": "image",
        "title": "scene-401",
        "filename": "0401.png",
        "layer": 3,
        "shapes": [],
        "children": [],
    }
    return {
        "asset_tree": [layer1, layer2, layer0, layer3],
        "images": {101: layer1, 201: layer2, 301: layer0, 401: layer3},
    }


def test_layer0_match_short_circuits_layer1_and_layer2(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    calls: list[tuple[str, list[int]]] = []

    def identify(_ctx, _frame, scene_ids, *, layer_label, trace=None):
        calls.append((layer_label, list(scene_ids)))
        return (301, 95.0, "matched")

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify)

    assert runner._identify_scene_number_by_graph(_context(), "frame", [301]) == (301, 95.0, "matched")
    assert calls == [("layer0", [301])]


def test_explicit_layer0_miss_does_not_fall_through_to_default_layers(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    calls: list[tuple[str, list[int]]] = []

    def identify(_ctx, _frame, scene_ids, *, layer_label, trace=None):
        calls.append((layer_label, list(scene_ids)))
        return None, 20.0, "no_match"

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify)

    assert runner._identify_scene_number_by_graph(_context(), "frame", [301]) == (None, 20.0, "no_match")
    assert calls == [("layer0", [301])]


def test_layer1_match_short_circuits_layer2(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    calls: list[tuple[str, list[int]]] = []

    def identify(_ctx, _frame, scene_ids, *, layer_label, trace=None):
        calls.append((layer_label, list(scene_ids)))
        return 101, 92.0, "matched"

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify)

    assert runner._identify_scene_number_by_graph(_context(), "frame") == (101, 92.0, "matched")
    assert calls == [("layer1", [101])]


def test_default_layer1_graph_includes_popup_candidates(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    world = _scene(101, 1)
    normal_layer2 = _scene(201, 2)
    popup = _scene(47, 2)
    popup["shapes"].append({"id": "close-47", "title": "空白"})
    tree = [
        world,
        normal_layer2,
        {"type": "folder", "title": "弹窗", "children": [popup]},
    ]
    ctx = {
        "asset_tree": tree,
        "images": {101: world, 201: normal_layer2, 47: popup},
    }
    calls: list[tuple[str, list[int]]] = []

    def identify(_ctx, _frame, scene_ids, *, layer_label, trace=None):
        calls.append((layer_label, list(scene_ids)))
        return 101, 98.0, "graph_nearest"

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify)

    assert runner._identify_scene_number_by_graph(ctx, "frame") == (101, 98.0, "graph_nearest")
    assert calls == [("layer1", [101, 47])]


def test_layer2_runs_only_after_layer1_whole_layer_misses(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    calls: list[tuple[str, list[int]]] = []

    def identify(_ctx, _frame, scene_ids, *, layer_label, trace=None):
        calls.append((layer_label, list(scene_ids)))
        if layer_label == "layer2":
            return 201, 91.0, "matched"
        return None, 40.0, "no_match"

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify)

    assert runner._identify_scene_number_by_graph(_context(), "frame") == (201, 91.0, "matched")
    assert calls == [
        ("layer1", [101]),
        ("layer2", [201, 301]),
    ]


def test_layer3_similarity_is_auxiliary_after_identity_layers_miss(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    graph_calls: list[str] = []
    layer3_calls: list[list[int]] = []

    def identify_graph(_ctx, _frame, _scene_ids, *, layer_label, trace=None):
        graph_calls.append(layer_label)
        return None, 40.0, "no_match"

    def identify_layer3(_ctx, _frame, scene_ids, *, trace=None):
        layer3_calls.append(list(scene_ids))
        return None, 93.0, "no_match"

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify_graph)
    monkeypatch.setattr(runner, "_identify_scene_number_in_layer3_candidates", identify_layer3)

    assert runner._identify_scene_number_by_graph(_context(), "frame") == (None, 93.0, "no_match")
    assert graph_calls == ["layer1", "layer2"]
    assert layer3_calls == [[401]]


def test_layer3_reports_strongest_reference_without_producing_scene_id(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    first = {
        "type": "image",
        "title": "first",
        "filename": "0401.png",
        "layer": 3,
        "shapes": [],
    }
    second = {
        "type": "image",
        "title": "second",
        "filename": "0402.png",
        "layer": 3,
        "shapes": [],
    }
    ctx = {"images": {401: first, 402: second}}
    similarities = {401: 79.0, 402: 78.0}

    monkeypatch.setattr(
        runner,
        "_scene_reference_similarity",
        lambda _ctx, image, _frame: similarities[int(image["filename"].split(".")[0])],
    )
    monkeypatch.setattr(
        runner,
        "_layer3_match_threshold",
        lambda image: 80.0 if image is first else 75.0,
    )

    assert runner._identify_scene_number_in_layer3_candidates(
        ctx,
        "frame",
        [401, 402],
    ) == (None, 79.0, "no_match")
    assert ctx["_last_layer3_auxiliary"] == {
        "reference_id": 401,
        "score": 79.0,
        "threshold": 80.0,
        "above_threshold": False,
    }


def test_layer1_ambiguity_still_blocks_lower_priority_layer2(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    calls: list[str] = []

    def identify(_ctx, _frame, _scene_ids, *, layer_label, trace=None):
        calls.append(layer_label)
        if layer_label == "layer1":
            return None, 94.0, "ambiguous"
        raise AssertionError("Layer2 must not run after Layer1 has matching candidates")

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify)

    assert runner._identify_scene_number_by_graph(_context(), "frame") == (None, 94.0, "ambiguous")
    assert calls == ["layer1"]


def test_candidates_inside_one_layer_are_scored_in_parallel(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    images = {scene_id: _scene(scene_id, 1) for scene_id in range(1, 7)}
    active = 0
    peak_active = 0
    lock = threading.Lock()

    def score(_ctx, image, _frame):
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return float(int(image["filename"].split(".")[0]))

    monkeypatch.setattr(runner, "_scene_score", score)

    scores = runner._scene_candidate_scores_parallel({}, images, list(images), "frame")

    assert scores == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert peak_active > 1


def test_parallel_layer_candidates_share_one_ocr_fill(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    images = {scene_id: _scene(scene_id, 1) for scene_id in range(1, 7)}
    ocr_calls = 0
    lock = threading.Lock()

    def ocr(_frame, options=None):
        nonlocal ocr_calls
        with lock:
            ocr_calls += 1
        time.sleep(0.03)
        return {"tokens": [{"text": "论道", "x": 0, "y": 0, "w": 10, "h": 10}]}

    def score(ctx, _image, frame):
        runner._shared_spatial_ocr_result(ctx, frame)
        return 90.0

    monkeypatch.setattr(runner, "_ocr_frame", ocr)
    monkeypatch.setattr(runner, "_scene_score", score)
    ctx: dict = {}

    assert runner._scene_candidate_scores_parallel(ctx, images, list(images), "frame") == [90.0] * 6
    assert ocr_calls == 1


def test_recognition_graph_reuses_static_pair_relations(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    images = {scene_id: _scene(scene_id, 2) for scene_id in (201, 202)}
    ctx = {"images": images}
    calls: list[tuple[int, int]] = []

    def match(_ctx, reference_id, fact_id):
        calls.append((reference_id, fact_id))
        return {
            "s": reference_id,
            "x": fact_id,
            "score": 95.0,
            "threshold": 80.0,
            "matched": reference_id == 201,
        }

    monkeypatch.setattr(runner, "match_scene_frame", match)

    first = runner._scene_match_edges_for_candidates(ctx, [201, 202])
    second = runner._scene_match_edges_for_candidates(ctx, [201, 202])

    assert [(edge["s"], edge["x"]) for edge in first] == [(201, 202)]
    assert second == first
    assert calls == [(201, 202), (202, 201)]
