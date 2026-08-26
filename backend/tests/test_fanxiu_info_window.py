from __future__ import annotations

import inspect
from types import MethodType

from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner
from backend.core.fanxiu.info_window import (
    FanxiuInfoWindowObserver,
    FanxiuInfoWindowState,
    format_fanxiu_observation_age,
    format_fanxiu_scene_text,
    read_fanxiu_info_window_settings,
    read_fanxiu_info_window_user_settings,
    write_fanxiu_info_window_settings,
    write_fanxiu_info_window_user_settings,
)


def test_fanxiu_info_window_scene_text() -> None:
    assert format_fanxiu_scene_text(510, 89.2, asset_directory="日程/玩法榜/魔道入侵") == (
        "日程/玩法榜/魔道入侵 #510 89%"
    )
    assert format_fanxiu_scene_text(34, 96.42) == "#34 96%"
    assert format_fanxiu_scene_text(None, 0) == "unknown 0%"
    assert format_fanxiu_scene_text(34, 96.42, show_scene_id=False) == "96%"


def test_fanxiu_info_window_observation_age_uses_integer_seconds_and_minutes() -> None:
    assert format_fanxiu_observation_age(100.0, now=100.9) == "0s"
    assert format_fanxiu_observation_age(100.0, now=102.9) == "2s"
    assert format_fanxiu_observation_age(100.0, now=160.0) == "1min"
    assert format_fanxiu_observation_age(100.0, now=221.9) == "2min"
    assert format_fanxiu_observation_age(None, now=221.9) == ""


def test_info_window_settings_are_persisted_and_default_missing_fields(monkeypatch, tmp_path) -> None:
    from backend.core.fanxiu import info_window as info_window_module

    path = tmp_path / "info_window_settings.json"
    monkeypatch.setattr(info_window_module, "fanxiu_info_window_settings_path", lambda: path)

    assert write_fanxiu_info_window_settings({"enabled": False, "show_scene_id": False}) == {
        "enabled": False,
        "active_recognition": False,
        "show_scene_id": False,
        "show_scene_score": True,
        "show_scene_identity_shapes": True,
        "show_all_shapes": False,
    }
    assert read_fanxiu_info_window_settings()["enabled"] is False


def test_info_window_settings_migrate_legacy_inverse_passive_switch(monkeypatch, tmp_path) -> None:
    from backend.core.fanxiu import info_window as info_window_module

    path = tmp_path / "info_window_settings.json"
    monkeypatch.setattr(info_window_module, "fanxiu_info_window_settings_path", lambda: path)

    assert write_fanxiu_info_window_settings({"passive_mode": False})["active_recognition"] is True


def test_info_window_user_preferences_are_isolated_and_migrate_legacy_settings(monkeypatch, tmp_path) -> None:
    from backend.core.fanxiu import info_window as info_window_module

    active_path = tmp_path / "info_window_settings.json"
    user_dir = tmp_path / "info-window-users"
    monkeypatch.setattr(info_window_module, "fanxiu_info_window_settings_path", lambda: active_path)
    monkeypatch.setattr(
        info_window_module,
        "fanxiu_info_window_user_settings_path",
        lambda user_id: user_dir / f"user_{user_id}.json",
    )
    write_fanxiu_info_window_settings({"show_scene_identity_shapes": False})

    assert read_fanxiu_info_window_user_settings(11)["show_scene_identity_shapes"] is False
    assert (user_dir / "user_11.json").is_file()

    write_fanxiu_info_window_user_settings(11, {"show_all_shapes": True})
    write_fanxiu_info_window_user_settings(12, {"show_scene_score": False})
    assert read_fanxiu_info_window_user_settings(11)["show_all_shapes"] is True
    assert read_fanxiu_info_window_user_settings(12)["show_scene_score"] is False
    assert read_fanxiu_info_window_user_settings(12)["show_all_shapes"] is False


def test_info_window_state_keeps_plain_shape_boxes() -> None:
    state = FanxiuInfoWindowState()
    payload = state.publish(
        34,
        100,
        source="runtime",
        scope="decision",
        asset_directory="日常/任务",
        boxes=[{"x": 818.3333, "y": 161.6666, "w": 46.6666, "h": 116.6666}],
        all_shape_boxes=[{"x": 10.1234, "y": 20.5678, "w": 30.9999, "h": 40.1111}],
        frame_width=900,
        frame_height=1600,
        persist=False,
    )

    assert payload["boxes"] == [{"x": 818.333, "y": 161.667, "w": 46.667, "h": 116.667}]
    assert payload["all_shape_boxes"] == [{"x": 10.123, "y": 20.568, "w": 31.0, "h": 40.111}]
    assert payload["asset_directory"] == "日常/任务"
    assert payload["text"] == "日常/任务 #34 100%"
    assert (payload["frame_width"], payload["frame_height"]) == (900, 1600)
    assert payload["scope"] == "decision"
    assert payload["committed"] is True


def test_info_window_age_uses_frame_capture_time_not_commit_time() -> None:
    state = FanxiuInfoWindowState()
    payload = state.publish(
        414,
        100,
        source="go_scene",
        scope="decision",
        captured_at=100.0,
        committed_at=109.0,
        persist=False,
    )

    assert payload["captured_at"] == 100.0
    assert payload["committed_at"] == 109.0
    assert payload["observed_at"] == 100.0
    assert format_fanxiu_observation_age(payload["captured_at"], now=110.0) == "10s"


def test_local_probe_miss_does_not_replace_committed_scene() -> None:
    state = FanxiuInfoWindowState()
    committed = state.publish(
        414,
        100,
        source="go_scene",
        scope="decision",
        persist=False,
    )

    probe = state.publish(
        None,
        0,
        source="wait_view",
        scope="probe",
        persist=False,
    )

    assert probe["committed"] is False
    assert probe["revision"] == committed["revision"]
    assert state.read()["scene_id"] == 414
    assert state.read()["source"] == "go_scene"


def test_local_probe_miss_without_committed_scene_stays_unpublished(monkeypatch, tmp_path) -> None:
    from backend.core.fanxiu import info_window as info_window_module

    monkeypatch.setattr(
        info_window_module,
        "fanxiu_info_window_state_path",
        lambda: tmp_path / "missing-info-window-state.json",
    )
    state = FanxiuInfoWindowState()

    probe = state.publish(
        None,
        0,
        source="wait_view",
        scope="probe",
        persist=False,
    )

    assert probe["committed"] is False
    assert state.read() == {}


def test_navigation_decision_can_update_committed_projection() -> None:
    state = FanxiuInfoWindowState()

    payload = state.publish(
        414,
        100,
        source="go_scene",
        scope="decision",
        persist=False,
    )

    assert payload["committed"] is True
    assert state.read()["scene_id"] == 414
    assert state.read()["scope"] == "decision"


def test_runtime_commits_final_scene_but_not_scoped_miss_or_unaccepted_hit(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = runner._fanxiu_runtime({"entry": object(), "images": {34: {}, 414: {}}})
    results = iter(((414, 100.0), (None, 0.0), (34, 100.0)))
    commits: list[tuple[int | None, float, str]] = []
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: next(results))
    monkeypatch.setattr(
        runner,
        "_commit_scene_observation",
        lambda _ctx, frame, scene_id, score: commits.append((scene_id, score, frame)),
    )

    assert runtime.current_scene(frame_data_url="global-frame", handle_interruptions=False)[:2] == (414, 100.0)
    assert runtime.current_scene([34], frame_data_url="scoped-miss", handle_interruptions=False)[:2] == (None, 0.0)
    with runner._scene_observation_probe(runtime.ctx):
        assert runtime.current_scene(
            [34],
            frame_data_url="unaccepted-hit",
            handle_interruptions=False,
        )[:2] == (34, 100.0)

    assert commits == [(414, 100.0, "global-frame")]


def test_tick_frame_keeps_one_capture_time_across_same_frame_layers(monkeypatch) -> None:
    from backend.core.fanxiu.data_annotation import behavior_tree_runtime as behavior_tree_runtime_module

    runner = create_behavior_tree_runtime_runner()
    ctx: dict = {}
    now = {"value": 100.0}
    monkeypatch.setattr(behavior_tree_runtime_module.time, "time", lambda: now["value"])

    runner._set_tick_frame(ctx, "frame-a")
    now["value"] = 109.0
    runner._set_tick_frame(ctx, "frame-a")
    assert ctx["_tick_frame_captured_at"] == 100.0

    runner._clear_tick_frame(ctx)
    runner._set_tick_frame(ctx, "frame-a")
    assert ctx["_tick_frame_captured_at"] == 109.0


def test_info_window_observer_is_strictly_read_only_when_active_recognition_is_off() -> None:
    class FailingLock:
        def acquire(self, **_kwargs):
            raise AssertionError("inactive recognition must not touch the execution lock")

    observer = FanxiuInfoWindowObserver(
        execution_lock=FailingLock(),
        recognize=lambda: (_ for _ in ()).throw(AssertionError("inactive observer must not recognize")),
        settings_reader=lambda: {"enabled": True, "active_recognition": False},
    )

    assert observer.tick(now=10.0) == "inactive"


def test_info_window_observer_active_recognition_reuses_results_until_they_are_five_seconds_old() -> None:
    class Lock:
        def __init__(self) -> None:
            self.released = False

        def acquire(self, **_kwargs):
            return True

        def release(self):
            self.released = True

    class Renderer:
        def available(self, **_kwargs):
            return True

    lock = Lock()
    calls: list[str] = []
    latest = {"observed_at": 8.0}
    recognition_times = iter((13.0, 18.0))

    def recognize() -> None:
        calls.append("recognized")
        latest["observed_at"] = next(recognition_times)

    observer = FanxiuInfoWindowObserver(
        execution_lock=lock,
        recognize=recognize,
        settings_reader=lambda: {"enabled": True, "active_recognition": True},
        state_reader=lambda: latest,
        windows_client=Renderer(),
    )

    assert observer.tick(now=10.0) == "fresh"
    assert observer.tick(now=12.999) == "fresh"
    assert observer.tick(now=13.0) == "recognized"
    assert observer.tick(now=17.999) == "fresh"
    assert observer.tick(now=18.0) == "recognized"
    assert observer.tick(now=18.5) == "fresh"
    assert latest["observed_at"] == 18.0
    assert calls == ["recognized", "recognized"]
    assert lock.released is True


def test_info_window_runtime_has_no_adb_bridge() -> None:
    from backend.core.fanxiu import info_window as info_window_module

    source = inspect.getsource(info_window_module)

    assert "FanxiuAndroidInfoWindowClient" not in source
    assert "android_proxy" not in source
    assert "screencap" not in source


def test_runtime_scene_recognition_publishes_existing_result(monkeypatch) -> None:
    from backend.core.fanxiu.data_annotation import behavior_tree_runtime as behavior_tree_runtime_module

    runner = object.__new__(behavior_tree_runtime_module.BehaviorTreeRuntimeRunner)
    runner._identify_scene_number_by_graph = MethodType(
        lambda _self, _ctx, _frame, _preferred, trace=None: (34, 98.5, "matched"),
        runner,
    )
    runner._frame_size = MethodType(lambda _self, _image: (900, 1600), runner)
    runner._scene_identity_shapes = MethodType(
        lambda _self, _image: [{"title": "任务"}],
        runner,
    )
    runner._all_scene_shapes = MethodType(
        lambda _self, _image: [{"title": "任务"}, {"title": "日常"}],
        runner,
    )
    runner._box = MethodType(
        lambda _self, shape, _image: {
            "x": 818.0 if shape["title"] == "任务" else 50.0,
            "y": 161.0,
            "w": 47.0,
            "h": 117.0,
        },
        runner,
    )
    published: list[dict] = []
    monkeypatch.setattr(
        behavior_tree_runtime_module,
        "publish_fanxiu_scene_recognition",
        lambda scene_id, score, **kwargs: published.append({
            "scene_id": scene_id,
            "score": score,
            **kwargs,
        }),
    )
    monkeypatch.setattr(behavior_tree_runtime_module.time, "time", lambda: 123.0)

    ctx = {
            "_fanxiu_scene_observation_source": "engineering_cell",
            "entry_id": "fanxiu-entry",
            "asset_tree_generation": 7,
            "asset_tree": [
                {
                    "type": "group",
                    "title": "日程",
                    "children": [{
                        "type": "group",
                        "title": "玩法榜",
                        "children": [{
                            "type": "image",
                            "title": "魔道入侵",
                            "filename": "0034.png",
                        }],
                    }],
                },
            ],
            "images": {34: {}},
        }
    scene_id, score = runner._identify_scene_number(ctx, "frame")
    runner._commit_scene_observation(ctx, "frame", scene_id, score)
    assert (scene_id, score) == (34, 98.5)
    assert published == [{
        "scene_id": 34,
        "score": 98.5,
        "scope": "decision",
        "source": "engineering_cell",
        "entry_id": "fanxiu-entry",
        "asset_generation": 7,
        "frame_id": "9dff50df08c63581",
        "asset_directory": "日程/玩法榜",
        "boxes": [{"x": 818.0, "y": 161.0, "w": 47.0, "h": 117.0}],
        "all_shape_boxes": [
            {"x": 818.0, "y": 161.0, "w": 47.0, "h": 117.0},
            {"x": 50.0, "y": 161.0, "w": 47.0, "h": 117.0},
        ],
        "frame_width": 900,
        "frame_height": 1600,
        "captured_at": 123.0,
        "committed_at": 123.0,
    }]


def test_runtime_scene_recognition_does_not_override_graph_with_unscoped_ocr(monkeypatch) -> None:
    from backend.core.fanxiu.data_annotation import behavior_tree_runtime as behavior_tree_runtime_module

    runner = object.__new__(behavior_tree_runtime_module.BehaviorTreeRuntimeRunner)
    runner._identify_scene_number_by_graph = MethodType(
        lambda _self, _ctx, _frame, _preferred, trace=None: (47, 88.0, "matched"),
        runner,
    )
    runner._cached_ocr_fragments = MethodType(
        lambda _self, _ctx, _frame: [{"text": "停更码字中，敬请期待更新"}],
        runner,
    )
    runner._frame_size = MethodType(lambda _self, _image: (900, 1600), runner)
    runner._scene_identity_shapes = MethodType(lambda _self, _image: [], runner)
    runner._all_scene_shapes = MethodType(lambda _self, _image: [], runner)
    published: list[dict] = []
    monkeypatch.setattr(
        behavior_tree_runtime_module,
        "publish_fanxiu_scene_recognition",
        lambda scene_id, score, **kwargs: published.append({
            "scene_id": scene_id,
            "score": score,
            **kwargs,
        }),
    )

    ctx = {
        "_fanxiu_scene_observation_source": "manual_cell",
        "images": {47: {}, 415: {}},
    }
    assert runner._identify_scene_number(ctx, "frame") == (47, 88.0)
    assert ctx["_last_scene_recognition_status"] == "matched"
    assert published == []
