from __future__ import annotations

import threading
import time

from backend.core.fanxiu.runtime.behavior_tree import create_fanxiu_runtime_runner


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
    layer0 = _scene(301, 3)
    return {
        "asset_tree": [layer1, layer2, layer0],
        "images": {101: layer1, 201: layer2, 301: layer0},
    }


def test_layer0_match_short_circuits_layer1_and_layer2(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    calls: list[tuple[str, list[int]]] = []

    def identify(_ctx, _frame, scene_ids, *, layer_label, trace=None):
        calls.append((layer_label, list(scene_ids)))
        return (301, 95.0, "matched")

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify)

    assert runner._identify_scene_number_by_graph(_context(), "frame", [301]) == (301, 95.0, "matched")
    assert calls == [("layer0", [301])]


def test_layer1_match_short_circuits_layer2(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    calls: list[tuple[str, list[int]]] = []

    def identify(_ctx, _frame, scene_ids, *, layer_label, trace=None):
        calls.append((layer_label, list(scene_ids)))
        if layer_label == "layer0":
            return None, 20.0, "no_match"
        return 101, 92.0, "matched"

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify)

    assert runner._identify_scene_number_by_graph(_context(), "frame", [301]) == (101, 92.0, "matched")
    assert calls == [("layer0", [301]), ("layer1", [101])]


def test_layer2_runs_only_after_layer1_whole_layer_misses(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    calls: list[tuple[str, list[int]]] = []

    def identify(_ctx, _frame, scene_ids, *, layer_label, trace=None):
        calls.append((layer_label, list(scene_ids)))
        if layer_label == "layer2":
            return 201, 91.0, "matched"
        return None, 40.0, "no_match"

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify)

    assert runner._identify_scene_number_by_graph(_context(), "frame", [301]) == (201, 91.0, "matched")
    assert calls == [
        ("layer0", [301]),
        ("layer1", [101]),
        ("layer2", [201]),
    ]


def test_layer1_ambiguity_still_blocks_lower_priority_layer2(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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
