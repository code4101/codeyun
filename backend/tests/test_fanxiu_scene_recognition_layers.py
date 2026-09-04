from __future__ import annotations

import ast
import inspect
import io
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from backend.core.fanxiu.behavior_tree.kernel_scheduler import create_behavior_tree_executor
from backend.core.fanxiu.data_annotation.behavior_tree_executor import (
    BehaviorTreeContext,
    SceneMatch,
    SceneWaitTimeout,
)


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


def _recognition_tuple(result) -> tuple[int | None, float, str, int | None]:
    return result.scene_id, result.score, result.status, result.matched_layer


def _drain_result(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def _png_frame(runner, *, color=(240, 240, 240)) -> str:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (90, 160), color).save(output, format="PNG")
    return runner._data_url(output.getvalue())


def test_scene_api_has_one_waiting_entry_and_no_legacy_aliases() -> None:
    wait_parameters = inspect.signature(BehaviorTreeContext.wait_scene).parameters
    frame_parameters = inspect.signature(
        BehaviorTreeContext.recognize_scene_in_frame
    ).parameters

    assert list(wait_parameters) == [
        "self",
        "scenes",
        "wait",
        "required",
        "label",
    ]
    assert wait_parameters["scenes"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert wait_parameters["wait"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert wait_parameters["wait"].default == 5.0
    assert list(frame_parameters) == ["self", "views", "frame_data_url"]
    for legacy_name in (
        "recognize_scene",
        "observe_scene",
        "wait_view",
        "wait_view_id",
        "goto_view",
        "sample_scene_once",
    ):
        assert not hasattr(BehaviorTreeContext, legacy_name)


def test_production_data_annotation_has_no_legacy_scene_sampling_calls() -> None:
    production_root = Path(__file__).parents[1] / "core" / "fanxiu" / "data_annotation"
    offenders = [
        path
        for path in production_root.rglob("*.py")
        if any(
            legacy_call in path.read_text(encoding="utf-8-sig")
            for legacy_call in ("sample_scene_once(", "handle_interruptions(")
        )
    ]

    assert offenders == []


def test_same_frame_recognition_cannot_capture_or_refresh_a_frame() -> None:
    production_root = Path(__file__).parents[1] / "core" / "fanxiu" / "data_annotation"
    offenders: list[tuple[Path, int, str]] = []
    for path in production_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "recognize_scene_in_frame"
            ):
                continue
            keywords = {keyword.arg for keyword in node.keywords}
            if "frame_data_url" not in keywords or "update" in keywords:
                offenders.append((path, node.lineno, ",".join(sorted(keywords))))

    assert offenders == []


def test_business_tasks_do_not_call_private_scene_identifiers() -> None:
    tasks_root = Path(__file__).parents[1] / "core" / "fanxiu" / "data_annotation" / "tasks"
    offenders = [
        path
        for path in tasks_root.rglob("*.py")
        if "_identify_scene_number(" in path.read_text(encoding="utf-8-sig")
    ]

    assert offenders == []


def test_current_scene_calls_are_executed_as_behavior_tree_generators() -> None:
    production_root = Path(__file__).parents[1] / "core" / "fanxiu" / "data_annotation"
    offenders: list[tuple[Path, int]] = []
    for path in production_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "current_scene"
            ):
                continue
            cursor: ast.AST | None = parents.get(node)
            consumed = False
            while cursor is not None and not isinstance(cursor, ast.stmt):
                if isinstance(cursor, (ast.YieldFrom, ast.Lambda)):
                    consumed = True
                    break
                if (
                    isinstance(cursor, ast.Call)
                    and isinstance(cursor.func, ast.Attribute)
                    and cursor.func.attr in {"execute", "_run_direct_action"}
                ):
                    consumed = True
                    break
                cursor = parents.get(cursor)
            if not consumed:
                offenders.append((path, node.lineno))

    assert offenders == []


def test_wait_scene_rejects_an_empty_layer0_collection() -> None:
    context = BehaviorTreeContext(create_behavior_tree_executor(), _context())

    try:
        _drain_result(context.wait_scene([]))
    except ValueError as exc:
        assert "scenes 不能为空" in str(exc)
    else:
        raise AssertionError("empty Layer-0 collection must fail before recognition")


def test_current_scene_delegates_to_non_required_wait_scene(monkeypatch) -> None:
    context = BehaviorTreeContext(create_behavior_tree_executor(), _context())
    match = SceneMatch(
        301,
        score=96.0,
        matched_layer=0,
        scope="business",
        status="matched",
        frame_data_url="current-frame",
    )
    calls: list[tuple[object, dict[str, object]]] = []

    def wait_scene(scenes=None, **options):
        calls.append((scenes, options))
        if False:
            yield None
        return match

    monkeypatch.setattr(context, "wait_scene", wait_scene)

    assert _drain_result(context.current_scene([301], label="current")) == (
        301,
        96.0,
        "current-frame",
    )
    assert calls == [
        (
            [301],
            {"wait": 0.0, "required": False, "label": "current"},
        )
    ]


def test_scene_match_is_an_id_with_explicit_recognition_facts() -> None:
    match = SceneMatch(
        382,
        score=96.5,
        matched_layer=2,
        scope="global",
        status="matched",
        frame_data_url="frame-382",
    )

    assert match == 382
    assert match.scene_id == 382
    assert match.id == 382
    assert match.frame_data_url == "frame-382"
    assert match.as_dict() == {
        "scene_id": 382,
        "score": 96.5,
        "matched_layer": 2,
        "scope": "global",
        "status": "matched",
    }


def test_layered_wait_reports_actual_business_layer_not_asset_layer(monkeypatch) -> None:
    runner = create_behavior_tree_executor()
    raw_context = _context()
    raw_context["_fanxiu_scene_observation_probe"] = True
    context = BehaviorTreeContext(runner, raw_context)
    monkeypatch.setattr(context, "cur_frame", lambda update=False: "frame")
    monkeypatch.setattr(
        runner,
        "_scene_score",
        lambda _ctx, image, _frame: 95.0 if image["filename"] == "0301.png" else 0.0,
    )

    match, score, frame = _drain_result(context._recognize_scene_layers([301], wait=0))

    assert match.scene_id == 301
    assert match.matched_layer == 0
    assert match.scope == "business"
    assert score == 95.0
    assert frame == "frame"


def test_popup_layer0_cannot_be_reclassified_as_a_business_scene(monkeypatch) -> None:
    runner = create_behavior_tree_executor()
    popup = _scene(313, 2)
    popup["shapes"].append({"id": "close-313", "title": "空白"})
    business = _scene(301, 2)
    raw_context = {
        "asset_tree": [
            {"type": "folder", "title": "弹窗", "children": [popup]},
            business,
        ],
        "images": {313: popup, 301: business},
        "_fanxiu_scene_observation_probe": True,
    }
    context = BehaviorTreeContext(runner, raw_context)
    monkeypatch.setattr(context, "cur_frame", lambda update=False: "frame")
    recognized = iter((313, 301))
    monkeypatch.setattr(
        runner,
        "_identify_scene_number_by_graph",
        lambda *_args, **_kwargs: SimpleNamespace(
            scene_id=next(recognized),
            score=99.0,
            status="matched",
            matched_layer=0,
        ),
    )
    handled: list[int] = []
    monkeypatch.setattr(
        runner,
        "_handle_recognized_popup_candidate",
        lambda _context, candidate, **_kwargs: handled.append(
            runner._image_number(candidate["image"])
        ) or True,
    )

    match, score, frame = _drain_result(
        context._recognize_scene_layers([313, 301], wait=0)
    )

    assert handled == [313]
    assert match.scene_id == 301
    assert match.scope == "business"
    assert score == 99.0
    assert frame == "frame"


def test_layered_wait_reports_global_layer2_fallback(monkeypatch) -> None:
    runner = create_behavior_tree_executor()
    raw_context = _context()
    raw_context["_fanxiu_scene_observation_probe"] = True
    context = BehaviorTreeContext(runner, raw_context)
    monkeypatch.setattr(context, "cur_frame", lambda update=False: "frame")
    monkeypatch.setattr(
        runner,
        "_scene_score",
        lambda _ctx, image, _frame: 95.0 if image["filename"] == "0201.png" else 0.0,
    )

    match, score, frame = _drain_result(context._recognize_scene_layers([301], wait=0))

    assert match.scene_id == 201
    assert match.matched_layer == 2
    assert match.scope == "global"
    assert score == 95.0
    assert frame == "frame"


def test_wait_scene_returns_global_layer2_and_keeps_its_raw_frame(monkeypatch, tmp_path) -> None:
    runner = create_behavior_tree_executor()
    context = BehaviorTreeContext(runner, _context())
    context.attrs["payload"] = {"__scheduler_task_id": "daily-test"}
    frame = _png_frame(runner)
    match = SceneMatch(
        201,
        score=91.0,
        matched_layer=2,
        scope="global",
        status="matched",
        frame_data_url=frame,
    )

    def recognize(*_args, **_kwargs):
        if False:
            yield None
        return match, 91.0, frame

    monkeypatch.setattr(context, "_recognize_scene_layers", recognize)
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.scene_diagnostics._diagnostic_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.behavior_tree_executor.escalate_persistent_scene_unknown",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("Layer 2 match is not an escalation condition")),
    )

    result = _drain_result(context.wait_scene([301], wait=0))

    assert result is match
    assert result.matched_layer == 2
    assert result.evidence_frame_path
    assert Path(result.evidence_frame_path).read_bytes() == runner._decode_frame_data_url(frame)


def test_wait_scene_retries_the_complete_flow_after_an_all_layer_miss(monkeypatch) -> None:
    runner = create_behavior_tree_executor()
    context = BehaviorTreeContext(runner, _context())
    frame = _png_frame(runner)
    recovered = SceneMatch(
        101,
        score=94.0,
        matched_layer=1,
        scope="global",
        status="matched",
        frame_data_url=frame,
    )
    results = [(None, 0.0, frame), (recovered, 94.0, frame)]
    waits: list[float] = []

    def recognize(_scenes, wait, **_kwargs):
        waits.append(wait)
        if False:
            yield None
        return results.pop(0)

    monkeypatch.setattr(context, "_recognize_scene_layers", recognize)

    result = _drain_result(context.wait_scene([301], wait=5))

    assert result is recovered
    assert waits == [5.0, 0.0]
    assert result.evidence_frame_path is None


def test_wait_scene_raises_typed_timeout_with_raw_evidence_after_guard(monkeypatch, tmp_path) -> None:
    runner = create_behavior_tree_executor()
    context = BehaviorTreeContext(runner, _context())
    context.scene_unmatched_guard_seconds = 2.0
    frame = _png_frame(runner)
    calls = 0

    def recognize(_scenes, wait, **_kwargs):
        nonlocal calls
        calls += 1
        if False:
            yield None
        return None, 12.0, frame

    clock = iter((0.0, 0.0, 0.0, 0.0, 3.0))
    monkeypatch.setattr(context, "_recognize_scene_layers", recognize)
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.behavior_tree_executor.time.monotonic",
        lambda: next(clock),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.scene_diagnostics._diagnostic_root",
        lambda: tmp_path,
    )

    try:
        _drain_result(context.wait_scene([301], wait=0))
    except SceneWaitTimeout as exc:
        assert exc.expected_scene_ids == (301,)
        assert exc.last_match is None
        assert exc.frame_data_url == frame
        assert exc.evidence_frame_path
        assert Path(exc.evidence_frame_path).is_file()
        assert "持续未匹配" in str(exc)
    else:
        raise AssertionError("all-layer misses must raise SceneWaitTimeout after the guard")
    assert calls == 2


def test_scheduled_wait_scene_timeout_escalates_and_exposes_the_dispatch(monkeypatch, tmp_path) -> None:
    runner = create_behavior_tree_executor()
    context = BehaviorTreeContext(runner, _context())
    context.scene_unmatched_guard_seconds = 0.0
    context.attrs["payload"] = {"__scheduler_task_id": "daily-test"}
    frame = _png_frame(runner)
    captured = {}

    def recognize(_scenes, wait, **_kwargs):
        del wait
        if False:
            yield None
        return None, 0.0, frame

    def escalate(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            dispatch_id="dispatch-1",
            request_path=str(tmp_path / "request.json"),
        )

    monkeypatch.setattr(context, "_recognize_scene_layers", recognize)
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.scene_diagnostics._diagnostic_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.behavior_tree_executor.escalate_persistent_scene_unknown",
        escalate,
    )

    try:
        _drain_result(context.wait_scene([301], wait=0, label="scheduled-scene"))
    except SceneWaitTimeout as exc:
        assert exc.codex_dispatch_id == "dispatch-1"
        assert exc.codex_request_path == str(tmp_path / "request.json")
        assert exc.codex_escalation_error is None
        assert "Codex投递=dispatch-1" in str(exc)
    else:
        raise AssertionError("scheduled persistent unknown must end the old attempt")
    assert captured["task_id"] == "daily-test"
    assert captured["expected_scene_ids"] == [301]


def test_layer0_match_short_circuits_layer1_and_layer2(monkeypatch):
    runner = create_behavior_tree_executor()
    calls: list[tuple[str, list[int]]] = []

    def identify(_ctx, _frame, scene_ids, *, layer_label, trace=None):
        calls.append((layer_label, list(scene_ids)))
        return (301, 95.0, "matched")

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify)

    assert _recognition_tuple(runner._identify_scene_number_by_graph(_context(), "frame", [301])) == (
        301, 95.0, "matched", 0,
    )
    assert calls == [("layer0", [301])]


def test_explicit_layer0_miss_does_not_fall_through_to_default_layers(monkeypatch):
    runner = create_behavior_tree_executor()
    calls: list[tuple[str, list[int]]] = []

    def identify(_ctx, _frame, scene_ids, *, layer_label, trace=None):
        calls.append((layer_label, list(scene_ids)))
        return None, 20.0, "no_match"

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify)

    assert _recognition_tuple(runner._identify_scene_number_by_graph(_context(), "frame", [301])) == (
        None, 20.0, "no_match", None,
    )
    assert calls == [("layer0", [301])]


def test_layer1_match_short_circuits_layer2(monkeypatch):
    runner = create_behavior_tree_executor()
    calls: list[tuple[str, list[int]]] = []

    def identify(_ctx, _frame, scene_ids, *, layer_label, trace=None):
        calls.append((layer_label, list(scene_ids)))
        return 101, 92.0, "matched"

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify)

    assert _recognition_tuple(runner._identify_scene_number_by_graph(_context(), "frame")) == (
        101, 92.0, "matched", 1,
    )
    assert calls == [("layer1", [101])]


def test_default_layer1_graph_includes_popup_candidates(monkeypatch):
    runner = create_behavior_tree_executor()
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

    assert _recognition_tuple(runner._identify_scene_number_by_graph(ctx, "frame")) == (
        101, 98.0, "graph_nearest", 1,
    )
    assert calls == [("layer1", [101, 47])]


def test_canonical_global_layers_exclude_popup_candidates(monkeypatch):
    runner = create_behavior_tree_executor()
    world = _scene(101, 1)
    popup = _scene(47, 2)
    tree = [
        world,
        {"type": "folder", "title": "弹窗", "children": [popup]},
    ]
    ctx = {
        "asset_tree": tree,
        "images": {101: world, 47: popup},
    }
    calls: list[tuple[str, list[int]]] = []

    def identify(_ctx, _frame, scene_ids, *, layer_label, trace=None):
        calls.append((layer_label, list(scene_ids)))
        return 101, 98.0, "graph_nearest"

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify)

    assert _recognition_tuple(runner._identify_scene_number_by_graph(
        ctx,
        "frame",
        include_default_popup_candidates=False,
    )) == (101, 98.0, "graph_nearest", 1)
    assert calls == [("layer1", [101])]


def test_layer2_runs_only_after_layer1_whole_layer_misses(monkeypatch):
    runner = create_behavior_tree_executor()
    calls: list[tuple[str, list[int]]] = []

    def identify(_ctx, _frame, scene_ids, *, layer_label, trace=None):
        calls.append((layer_label, list(scene_ids)))
        if layer_label == "layer2":
            return 201, 91.0, "matched"
        return None, 40.0, "no_match"

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify)

    assert _recognition_tuple(runner._identify_scene_number_by_graph(_context(), "frame")) == (
        201, 91.0, "matched", 2,
    )
    assert calls == [
        ("layer1", [101]),
        ("layer2", [201, 301]),
    ]


def test_layer3_similarity_is_auxiliary_after_identity_layers_miss(monkeypatch):
    runner = create_behavior_tree_executor()
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

    assert _recognition_tuple(runner._identify_scene_number_by_graph(_context(), "frame")) == (
        None, 93.0, "no_match", None,
    )
    assert graph_calls == ["layer1", "layer2"]
    assert layer3_calls == [[401]]


def test_layer3_reports_strongest_reference_without_producing_scene_id(monkeypatch):
    runner = create_behavior_tree_executor()
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
    runner = create_behavior_tree_executor()
    calls: list[str] = []

    def identify(_ctx, _frame, _scene_ids, *, layer_label, trace=None):
        calls.append(layer_label)
        if layer_label == "layer1":
            return None, 94.0, "ambiguous"
        raise AssertionError("Layer2 must not run after Layer1 has matching candidates")

    monkeypatch.setattr(runner, "_identify_scene_number_in_graph_candidates", identify)

    assert _recognition_tuple(runner._identify_scene_number_by_graph(_context(), "frame")) == (
        None, 94.0, "ambiguous", 1,
    )
    assert calls == ["layer1"]


def test_candidates_inside_one_layer_are_scored_in_parallel(monkeypatch):
    runner = create_behavior_tree_executor()
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
    runner = create_behavior_tree_executor()
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
    runner = create_behavior_tree_executor()
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
