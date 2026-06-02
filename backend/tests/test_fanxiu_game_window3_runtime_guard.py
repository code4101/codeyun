import json
import threading
import time

from backend.api.fanxiu import (
    _GameWindow3RuntimeContainer,
    _GameWindow3RuntimeRunner,
    _enqueue_game_window3_manual_job,
    _pop_next_game_window3_manual_job,
    _normalize_game_window3_runtime_guard_items,
)


def _image(title: str, filename: str, shapes: list[dict] | None = None) -> dict:
    return {
        "type": "image",
        "title": title,
        "filename": filename,
        "width": 900,
        "height": 1600,
        "shapes": shapes or [],
    }


def test_auto_close_guard_images_use_only_first_level_popup_group_children():
    runner = _GameWindow3RuntimeRunner()
    top_level = _image("所有提示窗口", "0047.jpg")
    nested = _image("拍卖", "0028.jpg")
    login = _image("修炼", "0019.jpg")

    candidates = runner._auto_close_guard_images([
        {"type": "folder", "title": "遮挡标记", "children": [_image("公告遮挡", "0026.jpg")]},
        {"type": "folder", "title": "登录弹窗", "children": [login]},
        {
            "type": "folder",
            "title": "弹窗",
            "children": [
                top_level,
                {"type": "folder", "title": "所有提示窗口", "children": [nested]},
            ],
        },
    ])

    assert [item["image"]["title"] for item in candidates] == ["所有提示窗口"]


def test_scene_score_requires_explicit_scene_identity_shape(monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    normal_shape = {"id": "signup", "kind": "rect", "title": "报名", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}
    image = _image("报名", "0023.jpg", [normal_shape])
    calls: list[str] = []

    monkeypatch.setattr(runner, "_shape_score", lambda _ctx, _image, shape, _frame: calls.append(shape["title"]) or 90)

    assert runner._scene_score({"entry": object()}, image, "frame") == 0
    assert calls == []


def test_popup_score_can_fallback_to_plain_shapes(monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    blank_shape = {"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}
    image = _image("所有提示窗口", "0047.jpg", [blank_shape])

    monkeypatch.setattr(runner, "_shape_score", lambda _ctx, _image, _shape, _frame: 80)

    assert runner._popup_score({"entry": object()}, image, "frame") == 80


def test_auto_close_guard_tick_clicks_first_matching_blank_shape(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    blank_shape = {"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1}
    nested_shape = {"id": "nested", "kind": "rect", "title": "空白", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.1}
    tree = [
        {
            "type": "folder",
            "title": "弹窗",
            "children": [
                _image("所有提示窗口", "0047.jpg", [blank_shape]),
                {
                    "type": "folder",
                    "title": "所有提示窗口",
                    "children": [_image("拍卖", "0028.jpg", [nested_shape])],
                },
            ],
        },
    ]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree), encoding="utf-8")

    clicked: list[tuple[str, str]] = []
    monkeypatch.setattr(runner, "_popup_score", lambda _ctx, image, _frame: 70 if image["title"] == "所有提示窗口" else 0)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, _frame=None: clicked.append((image["title"], shape["title"])))

    assert runner._auto_close_popup_guard_step({"entry": object()}, path, "data:image/png;base64,frame")
    assert clicked == [("所有提示窗口", "空白")]


def test_auto_close_guard_candidates_are_cached_by_asset_tree_signature(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps([{"type": "folder", "title": "弹窗", "children": [_image("图1", "0001.jpg")]}]), encoding="utf-8")
    load_count = 0
    original_load = runner._load_asset_tree

    def load(path_arg):
        nonlocal load_count
        load_count += 1
        return original_load(path_arg)

    monkeypatch.setattr(runner, "_load_asset_tree", load)

    first = runner._auto_close_guard_candidates_for_path(path)
    second = runner._auto_close_guard_candidates_for_path(path)

    assert first == second
    assert load_count == 1


def test_auto_close_guard_popup_47_child_84_clicks_no_more_prompt_only_when_unchecked(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    blank_shape = {"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1}
    no_more_prompt_shape = {"id": "no-more", "kind": "rect", "title": "不再提示", "x": 0.3, "y": 0.5, "w": 0.1, "h": 0.1}
    confirm_shape = {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1}
    child_84 = _image("自动切磋", "0084.png", [
        {"id": "identity", "kind": "rect", "title": "切磋已满30次", "isSceneIdentity": True, "x": 0.2, "y": 0.4, "w": 0.5, "h": 0.1},
        no_more_prompt_shape,
        confirm_shape,
    ])
    popup_47 = _image("所有提示窗口", "0047.jpg", [blank_shape])
    popup_47["children"] = [child_84]
    tree = [{"type": "folder", "title": "弹窗", "children": [popup_47]}]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree), encoding="utf-8")

    clicked: list[tuple[str, str]] = []

    def scene_score(_ctx, image, _frame):
        return 70 if image["title"] in {"所有提示窗口", "自动切磋"} else 0

    def shape_score(_ctx, _image, shape, _frame):
        return 0 if shape["title"] == "不再提示" else 70

    monkeypatch.setattr(runner, "_popup_score", scene_score)
    monkeypatch.setattr(runner, "_shape_score", shape_score)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, _frame=None: clicked.append((image["title"], shape["title"])))

    assert runner._auto_close_popup_guard_step({"entry": object()}, path, "data:image/png;base64,frame")
    assert clicked == [("自动切磋", "不再提示")]


def test_auto_close_guard_popup_47_child_84_checked_clicks_confirm_only(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    no_more_prompt_shape = {"id": "no-more", "kind": "rect", "title": "不再提示", "x": 0.3, "y": 0.5, "w": 0.1, "h": 0.1}
    confirm_shape = {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1}
    child_84 = _image("自动切磋", "0084.png", [
        {"id": "identity", "kind": "rect", "title": "切磋已满30次", "isSceneIdentity": True, "x": 0.2, "y": 0.4, "w": 0.5, "h": 0.1},
        no_more_prompt_shape,
        confirm_shape,
    ])
    popup_47 = _image("所有提示窗口", "0047.jpg", [{"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1}])
    popup_47["children"] = [child_84]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps([{"type": "folder", "title": "弹窗", "children": [popup_47]}]), encoding="utf-8")

    clicked: list[tuple[str, str]] = []
    monkeypatch.setattr(runner, "_popup_score", lambda _ctx, image, _frame: 70 if image["title"] in {"所有提示窗口", "自动切磋"} else 0)
    monkeypatch.setattr(runner, "_shape_score", lambda _ctx, _image, _shape, _frame: 70)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, _frame=None: clicked.append((image["title"], shape["title"])))
    monkeypatch.setattr("backend.api.fanxiu.time.sleep", lambda _seconds: None)

    assert runner._auto_close_popup_guard_step({"entry": object()}, path, "data:image/png;base64,frame")
    assert clicked == [("自动切磋", "确认")]


def test_auto_close_guard_popup_47_child_86_clicks_confirm(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    confirm_shape = {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1}
    child_86 = _image("离开场景", "0086.png", [
        {"id": "identity", "kind": "rect", "title": "离开场景", "isSceneIdentity": True, "x": 0.2, "y": 0.4, "w": 0.5, "h": 0.1},
        confirm_shape,
    ])
    popup_47 = _image("所有提示窗口", "0047.jpg", [{"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1}])
    popup_47["children"] = [child_86]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps([{"type": "folder", "title": "弹窗", "children": [popup_47]}]), encoding="utf-8")

    clicked: list[tuple[str, str]] = []
    monkeypatch.setattr(runner, "_popup_score", lambda _ctx, image, _frame: 70 if image["title"] in {"所有提示窗口", "离开场景"} else 0)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, _frame=None: clicked.append((image["title"], shape["title"])))

    assert runner._auto_close_popup_guard_step({"entry": object()}, path, "data:image/png;base64,frame")
    assert clicked == [("离开场景", "确认")]


def test_runtime_behavior_tree_popup_84_uses_separate_ticks_for_checkbox_and_confirm(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    no_more_prompt_shape = {"id": "no-more", "kind": "rect", "title": "不再提示", "x": 0.3, "y": 0.5, "w": 0.1, "h": 0.1}
    confirm_shape = {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1}
    child_84 = _image("自动切磋", "0084.png", [
        {"id": "identity", "kind": "rect", "title": "切磋已满30次", "isSceneIdentity": True, "x": 0.2, "y": 0.4, "w": 0.5, "h": 0.1},
        no_more_prompt_shape,
        confirm_shape,
    ])
    popup_47 = _image("所有提示窗口", "0047.jpg", [{"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1}])
    popup_47["children"] = [child_84]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps([{"type": "folder", "title": "弹窗", "children": [popup_47]}]), encoding="utf-8")
    ctx = {"entry": object()}
    captured: list[str] = []
    clicked: list[tuple[str, str, str]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    with runner._lock:
        runner._guard_enabled = True

    def capture_frame(_ctx):
        frame = f"frame{len(captured)}"
        captured.append(frame)
        return frame

    def scene_score(_ctx, image, _frame):
        if len(clicked) >= 2:
            return 0
        return 70 if image["title"] in {"所有提示窗口", "自动切磋"} else 0

    def shape_score(_ctx, _image, shape, _frame):
        return 0 if shape["title"] == "不再提示" and not clicked else 70

    def click_shape(_ctx, image, shape, frame=None):
        clicked.append((image["title"], shape["title"], str(frame or "")))

    monkeypatch.setattr(runner, "_capture_frame", capture_frame)
    monkeypatch.setattr(runner, "_popup_score", scene_score)
    monkeypatch.setattr(runner, "_shape_score", shape_score)
    monkeypatch.setattr(runner, "_click_shape", click_shape)
    monkeypatch.setattr(runner, "_persist_status", lambda: None)
    monkeypatch.setattr("backend.api.fanxiu.time.sleep", lambda _seconds: (_ for _ in ()).throw(AssertionError("guard must yield instead of sleeping")))

    result = runner._run_runtime_behavior_tree(
        runtime_ctx=ctx,
        asset_tree_path=path,
        stop_event=FakeStopEvent(),
        action=lambda: runner._screencap(ctx),
        label="测试作业",
    )

    assert result == "frame2"
    assert clicked == [
        ("自动切磋", "不再提示", "frame0"),
        ("自动切磋", "确认", ""),
    ]
    assert captured == ["frame0", "frame1", "frame2"]


def test_runtime_behavior_tree_reuses_guard_frame_for_job_when_guard_skips(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"entry": object()}
    captured: list[str] = []
    job_frames: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    with runner._lock:
        runner._guard_enabled = True

    def capture_frame(_ctx):
        frame = f"data:image/png;base64,frame{len(captured)}"
        captured.append(frame)
        return frame

    monkeypatch.setattr(runner, "_capture_frame", capture_frame)
    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", lambda _ctx, _path, _frame: False)

    result = runner._run_runtime_behavior_tree(
        runtime_ctx=ctx,
        asset_tree_path=path,
        stop_event=FakeStopEvent(),
        action=lambda: job_frames.append(runner._screencap(ctx)) or "done",
        label="测试作业",
    )

    assert result == "done"
    assert job_frames == ["data:image/png;base64,frame0"]
    assert captured == ["data:image/png;base64,frame0"]


def test_runtime_behavior_tree_runs_guard_before_job_and_skips_job_when_handled(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"entry": object()}
    captured: list[str] = []
    events: list[tuple[str, str]] = []
    handled_results = [True, False]

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    with runner._lock:
        runner._guard_enabled = True

    def capture_frame(_ctx):
        frame = f"data:image/png;base64,frame{len(captured)}"
        captured.append(frame)
        return frame

    def guard_tick(_ctx, _path, frame):
        events.append(("guard", frame))
        return handled_results.pop(0)

    def job_action():
        frame = runner._screencap(ctx)
        events.append(("job", frame))
        return "done"

    monkeypatch.setattr(runner, "_capture_frame", capture_frame)
    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", guard_tick)
    monkeypatch.setattr(runner, "_persist_status", lambda: None)

    result = runner._run_runtime_behavior_tree(
        runtime_ctx=ctx,
        asset_tree_path=path,
        stop_event=FakeStopEvent(),
        action=job_action,
        label="测试作业",
    )

    assert result == "done"
    assert events == [
        ("guard", "data:image/png;base64,frame0"),
        ("guard", "data:image/png;base64,frame1"),
        ("job", "data:image/png;base64,frame1"),
    ]
    assert captured == ["data:image/png;base64,frame0", "data:image/png;base64,frame1"]


def test_popup_guard_restarts_scan_each_tick_so_actual_popup_order_can_differ(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    shapes = {
        title: {"id": title, "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1}
        for title in ("图1", "图2", "图3", "图4", "图5")
    }
    tree = [{"type": "folder", "title": "弹窗", "children": [_image(title, f"{index:04d}.jpg", [shapes[title]]) for index, title in enumerate(shapes, start=1)]}]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree), encoding="utf-8")
    ctx = {"entry": object()}
    actual_order = ["图3", "图2", "图4", "图1"]
    clicked: list[str] = []
    captured: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    with runner._lock:
        runner._guard_enabled = True

    def capture_frame(_ctx):
        frame = f"frame{len(captured)}"
        captured.append(frame)
        return frame

    def scene_score(_ctx, image, _frame):
        if len(clicked) >= len(actual_order):
            return 0
        return 70 if image["title"] == actual_order[len(clicked)] else 0

    monkeypatch.setattr(runner, "_capture_frame", capture_frame)
    monkeypatch.setattr(runner, "_popup_score", scene_score)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, _shape, _frame=None: clicked.append(image["title"]))
    monkeypatch.setattr(runner, "_persist_status", lambda: None)

    result = runner._run_runtime_behavior_tree(
        runtime_ctx=ctx,
        asset_tree_path=path,
        stop_event=FakeStopEvent(),
        action=lambda: "job-started",
        label="测试作业",
    )

    assert clicked == actual_order
    assert result == "job-started"
    assert captured == ["frame0", "frame1", "frame2", "frame3", "frame4"]


def test_popup_guard_parallel_scores_still_choose_first_matching_candidate(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    tree = [{
        "type": "folder",
        "title": "弹窗",
        "children": [
            _image("图1", "0001.jpg", [{"id": "s1", "kind": "rect", "title": "空白", "x": 0, "y": 0, "w": 1, "h": 1}]),
            _image("图2", "0002.jpg", [{"id": "s2", "kind": "rect", "title": "空白", "x": 0, "y": 0, "w": 1, "h": 1}]),
            _image("图3", "0003.jpg", [{"id": "s3", "kind": "rect", "title": "空白", "x": 0, "y": 0, "w": 1, "h": 1}]),
        ],
    }]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree), encoding="utf-8")
    clicked: list[str] = []

    def score(_ctx, image, _frame):
        return {"图1": 10, "图2": 75, "图3": 90}[image["title"]]

    monkeypatch.setattr(runner, "_popup_score", score)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, _shape, _frame=None: clicked.append(image["title"]))

    assert runner._auto_close_popup_guard_step({"entry": object()}, path, "frame")
    assert clicked == ["图2"]


def test_popup_guard_parallel_scoring_checks_all_candidates_in_one_tick(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    candidates = [{"image": _image(f"图{index}", f"{index:04d}.jpg")} for index in range(12)]
    seen: list[str] = []

    def score(_ctx, image, _frame):
        seen.append(image["title"])
        return 70 if image["title"] == "图1" else 0

    monkeypatch.setattr(runner, "_popup_score", score)

    candidate, score_value = runner._auto_close_popup_first_match({"entry": object()}, candidates, "frame")

    assert candidate is not None
    assert candidate["image"]["title"] == "图1"
    assert score_value == 70
    assert set(seen) == {f"图{index}" for index in range(12)}


def test_popup_guard_parallel_scoring_is_faster_than_serial_for_independent_candidates(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    candidates = [{"image": _image(f"图{index}", f"{index:04d}.jpg")} for index in range(12)]
    ctx = {"entry": object()}
    score_delay = 0.015
    rounds = 5

    def score(_ctx, image, _frame):
        time.sleep(score_delay)
        return 0 if image["title"] != "图11" else 70

    monkeypatch.setattr(runner, "_popup_score", score)

    start = time.perf_counter()
    for _ in range(rounds):
        serial_scores = runner._auto_close_popup_candidate_scores_serial(ctx, candidates, "frame")
    serial_elapsed = time.perf_counter() - start

    start = time.perf_counter()
    for _ in range(rounds):
        parallel_scores = runner._auto_close_popup_candidate_scores_parallel(ctx, candidates, "frame")
    parallel_elapsed = time.perf_counter() - start

    assert serial_scores == parallel_scores
    assert parallel_elapsed < serial_elapsed * 0.75


def test_runtime_container_reads_guard_whitelist_from_backend_state(tmp_path):
    runner = _GameWindow3RuntimeRunner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")

    with runner._lock:
        runner._guard_enabled = True
        runner._guard_items["wanling_invite"] = {"enabled": False}

    container = _GameWindow3RuntimeContainer(
        runner,
        runtime_ctx={"entry": object()},
        asset_tree_path=path,
        stop_event=threading.Event(),
    )

    specs = {spec.node_id: spec.enabled for spec in container.guard_specs()}
    assert specs == {"close_popups": True, "wanling_invite": False}


def test_runtime_container_groups_are_prioritized_and_non_preemptive(tmp_path):
    runner = _GameWindow3RuntimeRunner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")

    container = _GameWindow3RuntimeContainer(
        runner,
        runtime_ctx={"entry": object()},
        asset_tree_path=path,
        stop_event=threading.Event(),
    )

    groups = container.group_specs()
    assert [(item.group_id, item.priority, item.preempt_same_group) for item in groups] == [
        ("guard", 10, False),
        ("manual_job", 50, False),
        ("job", 100, False),
    ]


def test_manual_jobs_are_persisted_as_middle_priority_group(tmp_path, monkeypatch):
    path = tmp_path / "manual_jobs.json"
    monkeypatch.setattr("backend.api.fanxiu._game_window3_manual_job_state_path", lambda: path)

    job = _enqueue_game_window3_manual_job("detect_scene", {"x": 1}, label="单步识别")
    popped = _pop_next_game_window3_manual_job()

    assert job["group"] == "manual_job"
    assert job["priority"] == 50
    assert popped is not None
    assert popped["id"] == job["id"]
    assert popped["task_type"] == "detect_scene"
    assert popped["payload"] == {"x": 1}
    assert _pop_next_game_window3_manual_job() is None


def test_runtime_container_skips_disabled_guards(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"entry": object()}
    events: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    with runner._lock:
        runner._guard_enabled = False

    monkeypatch.setattr(runner, "_capture_frame", lambda _ctx: events.append("capture") or "frame")
    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", lambda _ctx, _path, _frame: events.append("guard") or False)

    result = runner._run_runtime_behavior_tree(
        runtime_ctx=ctx,
        asset_tree_path=path,
        stop_event=FakeStopEvent(),
        action=lambda: events.append("job") or "done",
        label="测试作业",
    )

    assert result == "done"
    assert events == ["job"]


def test_go_scene_moves_by_scene_jump_and_records_frequency(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    jump_shape = {"id": "jump", "kind": "rect", "title": "去二", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "sceneJumpTarget": "2"}
    tree = [
        _image("一", "0001.jpg", [jump_shape]),
        _image("二", "0002.jpg", [{"id": "id2", "kind": "rect", "title": "二标识", "isSceneIdentity": True, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}]),
    ]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree), encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": runner._index_images(tree)}
    scene_state = {"value": 1}

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_capture_frame", lambda _ctx: f"scene{scene_state['value']}")
    monkeypatch.setattr(runner, "_scene_score", lambda _ctx, image, frame: 80 if str(runner._image_number(image)) in str(frame) else 0)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, _shape, _frame=None: (scene_state.update({"value": 2}), runner._clear_tick_frame(_ctx)))

    result = runner._run_runtime_behavior_tree(
        runtime_ctx=ctx,
        asset_tree_path=path,
        stop_event=FakeStopEvent(),
        action=lambda: runner._execute_runtime_task(ctx, "go_scene", {"target_scene_id": 2}, FakeStopEvent()),
        label="到场景",
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert result == "success"
    assert saved[0]["shapes"][0]["sceneJumpTarget"] == "2(1)"


def test_go_scene_records_unexpected_result_after_timeout_and_replans(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    first_shape = {"id": "jump12", "kind": "rect", "title": "随机跳", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "sceneJumpTarget": "3"}
    second_shape = {"id": "jump23", "kind": "rect", "title": "去三", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "sceneJumpTarget": "3"}
    tree = [
        _image("一", "0001.jpg", [first_shape]),
        _image("二", "0002.jpg", [second_shape]),
        _image("三", "0003.jpg", [{"id": "id3", "kind": "rect", "title": "三标识", "isSceneIdentity": True, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}]),
    ]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree), encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": runner._index_images(tree)}
    scene_state = {"value": 1}
    monotonic_values = iter([0.0, 61.0, 100.0, 101.0])

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def click_shape(_ctx, image, _shape, _frame=None):
        scene_state["value"] = 2 if runner._image_number(image) == 1 else 3
        runner._clear_tick_frame(_ctx)

    monkeypatch.setattr("backend.api.fanxiu.time.monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(runner, "_capture_frame", lambda _ctx: f"scene{scene_state['value']}")
    monkeypatch.setattr(runner, "_scene_score", lambda _ctx, image, frame: 80 if str(runner._image_number(image)) in str(frame) else 0)
    monkeypatch.setattr(runner, "_click_shape", click_shape)

    result = runner._run_runtime_behavior_tree(
        runtime_ctx=ctx,
        asset_tree_path=path,
        stop_event=FakeStopEvent(),
        action=lambda: runner._execute_runtime_task(ctx, "go_scene", {"target_scene_id": 3}, FakeStopEvent()),
        label="到场景",
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert result == "success"
    assert saved[0]["shapes"][0]["sceneJumpTarget"] == "2(1),3"
    assert saved[1]["shapes"][0]["sceneJumpTarget"] == "3(1)"


def test_unknown_scene_frame_creates_note_only_placeholder_shape(tmp_path, monkeypatch):
    runner = _GameWindow3RuntimeRunner()
    tree: list[dict] = []
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")

    class Entry:
        mode = "local"

    monkeypatch.setattr(
        "backend.api.fanxiu._save_game_window2_service",
        lambda _payload: {"filename": "9999.png", "width": 900, "height": 1600},
    )

    runner._save_unknown_scene_frame(
        {"entry": Entry()},
        path,
        tree,
        "data:image/png;base64,frame",
        target_scene_id=34,
        current_scene_id=12,
        action_shape={"title": "返回"},
        elapsed_seconds=60.0,
        history=["60.0s unknown 0%"],
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    shape = saved[0]["children"][0]["shapes"][0]
    assert saved[0]["title"] == "未知场景"
    assert shape["title"] == "未知场景备注"
    assert "description" in shape
    assert "isSceneIdentity" not in shape
    assert "sceneIdentityRole" not in shape
    assert "sceneJumpTarget" not in shape


def test_runtime_logs_include_current_scope_and_item_id():
    runner = _GameWindow3RuntimeRunner()

    previous = runner._set_log_context("job", "weekly_gift_codes")
    try:
        with runner._lock:
            runner._log_locked("action", "开始兑换")
    finally:
        runner._restore_log_context(previous)

    previous = runner._set_log_context("guard", "close_popups")
    try:
        with runner._lock:
            runner._log_locked("guardClick", "关闭弹窗")
    finally:
        runner._restore_log_context(previous)

    logs = runner.status()["logs"]
    assert logs[0]["scope"] == "job"
    assert logs[0]["item_id"] == "weekly_gift_codes"
    assert logs[1]["scope"] == "guard"
    assert logs[1]["item_id"] == "close_popups"


def test_manual_job_logs_use_group_item_id_and_keep_instance_id_in_message():
    runner = _GameWindow3RuntimeRunner()

    with runner._lock:
        runner._log_locked(
            "info",
            runner._manual_job_log_message("manual-1", "手动作业已启动：单步识别"),
            scope="manual_job",
            item_id="manual_job",
        )

    log = runner.status()["logs"][0]
    assert log["scope"] == "manual_job"
    assert log["item_id"] == "manual_job"
    assert log["message"].startswith("[manual-1] ")


def test_noop_guard_records_enabled_state_without_starting_popup_thread(tmp_path):
    runner = _GameWindow3RuntimeRunner()

    status = runner.set_guard(
        entry=object(),
        entry_id="codepc_mf",
        guard_id="wanling_invite",
        enabled=True,
        interval_seconds=2,
        asset_tree_path=tmp_path / "missing.json",
    )

    assert status["guard_items"]["wanling_invite"]["enabled"] is True
    assert status["guard_enabled"] is False
    assert status["guard_running"] is False
    assert status["logs"][-1]["scope"] == "guard"
    assert status["logs"][-1]["item_id"] == "wanling_invite"


def test_runtime_status_normalizes_guard_items_from_backend_definitions():
    status = {
        "guard_enabled": False,
        "guard_running": False,
        "guard_entry_id": "",
        "guard_items": {},
    }

    _normalize_game_window3_runtime_guard_items(status)

    assert set(status["guard_items"]) == {"close_popups", "wanling_invite"}
    assert status["guard_items"]["close_popups"]["label"] == "关闭弹窗"
    assert status["guard_items"]["wanling_invite"]["label"] == "万灵切磋邀请"
