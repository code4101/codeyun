import os
import tempfile

os.environ["CODEYUN_DATA_DIR"] = os.path.join(tempfile.gettempdir(), "codeyun", "pytest_fanxiu_runtime_guard")

import json
import threading
import time
import base64
import io
from contextlib import contextmanager, nullcontext
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from backend.api import fanxiu as fanxiu_api
from backend.api.fanxiu import (
    BehaviorTreeStatus,
    _BehaviorTreeRuntimeContainer,
    _default_data_annotation_scheduler_tasks,
    _data_annotation_task_supported,
    _normalize_behavior_tree_runtime_guard_items,
    _record_data_annotation_scheduler_task_fact,
    _read_data_annotation_world_facts,
    _write_data_annotation_world_facts,
)
from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner as _create_behavior_tree_runtime_runner
from backend.core.fanxiu.behavior_tree import runtime as fanxiu_behavior_tree_core
from backend.core.fanxiu.data_annotation import behavior_tree_control as behavior_tree_control_core
from backend.core.fanxiu.data_annotation import behavior_tree_runtime as behavior_tree_runtime_core
from backend.core.fanxiu.data_annotation import popup_guard as popup_guard_core
from backend.core.fanxiu.data_annotation.ocr_spatial import OcrTextMatch
from backend.core.fanxiu.data_annotation.tasks import daily_challenge as daily_challenge_module
from backend.core.fanxiu.data_annotation.scheduler import (
    data_annotation_scheduler_time_order_key,
    repair_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.state import normalize_behavior_tree_runtime_display
from backend.core.access.service_tokens import SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL, create_service_access_token
from backend.core.fanxiu.runtime.mumu_control import _compare_frame_crops
from backend.models import FanxiuMailRecord, User, UserDevice
from backend.core.fanxiu.runtime import mumu_control as mumu_control


def create_behavior_tree_runtime_runner():
    runner = _create_behavior_tree_runtime_runner()
    runner._navigation_random.choices = lambda population, *, weights, k: [
        max(zip(population, weights), key=lambda item: item[1])[0]
    ]
    return runner


def test_capture_frame_retries_transient_black_frame_in_same_transaction(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    calls = {"capture": 0}
    waits: list[float] = []

    def capture():
        calls["capture"] += 1
        if calls["capture"] == 1:
            raise RuntimeError("MuMu ADB截图疑似黑屏")
        return SimpleNamespace(body=b"fresh-png")

    monkeypatch.setattr(behavior_tree_runtime_core, "_screencap_game_window2_service", capture)
    monkeypatch.setattr(
        behavior_tree_runtime_core,
        "record_mumu_adb_failure",
        lambda *_args, **_kwargs: {
            "status": "suspect",
            "recovered": False,
            "recovery_deferred": "frame_unusable_observation_window",
            "frame_unusable_elapsed_seconds": 0.0,
            "frame_unusable_recovery_seconds": 30.0,
        },
    )
    monkeypatch.setattr(behavior_tree_runtime_core.time, "sleep", lambda seconds: waits.append(seconds))

    frame = runner._capture_frame({"entry": SimpleNamespace(mode="local")})

    assert base64.b64decode(frame.split(",", 1)[1]) == b"fresh-png"
    assert calls["capture"] == 2
    assert waits == [2.0]


def test_capture_frame_invalidates_gui_transaction_after_mumu_recovery(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    calls = {"capture": 0}

    def capture():
        calls["capture"] += 1
        raise RuntimeError("MuMu ADB截图疑似黑屏")

    recovered_state = {
        "status": "healthy",
        "recovered": True,
        "recovery_count": 1,
    }
    monkeypatch.setattr(behavior_tree_runtime_core, "_screencap_game_window2_service", capture)
    monkeypatch.setattr(
        behavior_tree_runtime_core,
        "record_mumu_adb_failure",
        lambda *_args, **_kwargs: recovered_state,
    )

    with pytest.raises(popup_guard_core.FanxiuEmulatorRestartRequired) as captured:
        runner._capture_frame({"entry": SimpleNamespace(mode="local")})

    assert captured.value.recovery_succeeded is True
    assert captured.value.evidence["reason"] == "adb_frame_recovery"
    assert captured.value.evidence["device_health"] == recovered_state
    assert calls["capture"] == 1


def _png_data_url(width: int = 900, height: int = 1600) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _image(title: str, filename: str, shapes: list[dict] | None = None) -> dict:
    return {
        "type": "image",
        "title": title,
        "filename": filename,
        "width": 900,
        "height": 1600,
        "shapes": shapes or [],
    }


def _scene_image(
    title: str,
    filename: str,
    shapes: list[dict] | None = None,
    *,
    layer: int = 2,
) -> dict:
    image = _image(title, filename, shapes)
    image["layer"] = layer
    return image


def _popup_domain(popup: dict, *specific_nodes: dict) -> list[dict]:
    return [{"type": "folder", "title": "弹窗", "children": [popup, *specific_nodes]}]


def _drain_generator(result):
    while True:
        try:
            next(result)
        except StopIteration as stop:
            return stop.value


def _assert_daily_assistant_unverified_error(exc_info, *expected_fragments: str):
    message = str(exc_info.value)
    assert "日常_助手：不能把未确认执行结果标记为成功" in message
    for fragment in expected_fragments:
        assert fragment in message


@pytest.fixture(autouse=True)
def _isolate_fanxiu_runtime_state_paths(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "fanxiu" / "data-annotation" / "runtime"
    entry_dir = tmp_path / "fanxiu" / "data-annotation" / "entries"

    def runtime_file(name: str) -> Path:
        return runtime_dir / name

    def asset_tree_path(entry_id: str) -> Path:
        return entry_dir / entry_id / "asset-tree.json"

    monkeypatch.setattr(fanxiu_api, "_BEHAVIOR_TREE_RUNTIME_RUNNER", create_behavior_tree_runtime_runner())
    monkeypatch.setattr(fanxiu_api, "_behavior_tree_runtime_state_path", lambda: runtime_file("runtime_state.json"))
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: runtime_file("world_facts.json"))
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_state_path", lambda: runtime_file("scheduler_tasks.json"))
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_settings_path", lambda: runtime_file("scheduler_settings.json"))
    monkeypatch.setattr(fanxiu_api, "_data_annotation_mail_scan_state_path", lambda: runtime_file("mail_scan_state.json"))
    monkeypatch.setattr(fanxiu_api, "_data_annotation_asset_tree_path", asset_tree_path)

    monkeypatch.setattr(behavior_tree_runtime_core, "_behavior_tree_runtime_state_path", lambda: runtime_file("runtime_state.json"))
    monkeypatch.setattr(behavior_tree_runtime_core, "_data_annotation_world_facts_path", lambda: runtime_file("world_facts.json"))
    monkeypatch.setattr(behavior_tree_runtime_core, "_data_annotation_scheduler_state_path", lambda: runtime_file("scheduler_tasks.json"))
    monkeypatch.setattr(behavior_tree_runtime_core, "_data_annotation_scheduler_settings_path", lambda: runtime_file("scheduler_settings.json"))
    monkeypatch.setattr(behavior_tree_runtime_core, "_data_annotation_mail_scan_state_path", lambda: runtime_file("mail_scan_state.json"))
    monkeypatch.setattr(behavior_tree_runtime_core, "_data_annotation_asset_tree_path", asset_tree_path)

    monkeypatch.setattr(behavior_tree_control_core, "fanxiu_behavior_tree_runtime_state_path", lambda: runtime_file("runtime_state.json"))
    monkeypatch.setattr(behavior_tree_control_core, "fanxiu_data_annotation_world_facts_path", lambda: runtime_file("world_facts.json"))
    monkeypatch.setattr(behavior_tree_control_core, "fanxiu_data_annotation_scheduler_state_path", lambda: runtime_file("scheduler_tasks.json"))
    monkeypatch.setattr(behavior_tree_control_core, "fanxiu_data_annotation_scheduler_settings_path", lambda: runtime_file("scheduler_settings.json"))

    monkeypatch.setattr(fanxiu_behavior_tree_core, "_BEHAVIOR_TREE_RUNTIME_RUNNER", None)
    monkeypatch.setattr(fanxiu_behavior_tree_core, "fanxiu_behavior_tree_runtime_state_path", lambda: runtime_file("runtime_state.json"))
    monkeypatch.setattr(fanxiu_behavior_tree_core, "fanxiu_data_annotation_world_facts_path", lambda: runtime_file("world_facts.json"))
    monkeypatch.setattr(fanxiu_behavior_tree_core, "fanxiu_data_annotation_scheduler_state_path", lambda: runtime_file("scheduler_tasks.json"))
    monkeypatch.setattr(fanxiu_behavior_tree_core, "fanxiu_data_annotation_scheduler_settings_path", lambda: runtime_file("scheduler_settings.json"))
    monkeypatch.setattr(fanxiu_behavior_tree_core, "fanxiu_data_annotation_mail_scan_state_path", lambda: runtime_file("mail_scan_state.json"))
    monkeypatch.setattr(fanxiu_behavior_tree_core, "data_annotation_asset_tree_path", asset_tree_path)


def test_runtime_wait_click_then_shape_closes_click_with_target_probe(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, {"images": {}}, stop_event=threading.Event())
    actions: list[tuple] = []

    def fake_wait_click(frame, shape, **kwargs):
        actions.append(("wait_click", frame, shape, kwargs))
        if False:
            yield None
        return "clicked"

    def fake_wait_action_settle(seconds=1.0):
        actions.append(("settle", seconds))
        if False:
            yield None
        return None

    def fake_wait_shape(frame, shape, **kwargs):
        actions.append(("wait_shape", frame, shape, kwargs))
        if False:
            yield None
        return "target-frame"

    monkeypatch.setattr(runtime, "wait_click", fake_wait_click)
    monkeypatch.setattr(runtime, "wait_action_settle", fake_wait_action_settle)
    monkeypatch.setattr(runtime, "wait_shape", fake_wait_shape)

    result = _drain_generator(runtime.wait_click_then_shape(
        34,
        "仙市",
        247,
        "秘藏阁",
        timeout=7,
        settle_seconds=1.25,
        label="等待仙市入口页",
    ))

    assert result == "target-frame"
    assert actions == [
        ("wait_click", 34, "仙市", {}),
        ("settle", 1.25),
        ("wait_shape", 247, "秘藏阁", {"timeout": 7.0, "label": "等待仙市入口页"}),
    ]


def test_runtime_wait_click_unconstrained_shape_skips_all_visual_prechecks(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image302 = _scene_image("论道入座确认", "0302.png", [{
        "id": "confirm",
        "kind": "rect",
        "title": "确定",
        "imageMatchRole": "off",
        "ocrMatchRole": "off",
        "x": 0.6,
        "y": 0.6,
        "w": 0.2,
        "h": 0.1,
    }])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {302: image302}},
        stop_event=threading.Event(),
    )
    clicked: list[tuple[float, float]] = []

    monkeypatch.setattr(
        runtime,
        "wait_view",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("wait_click must not re-recognize the source scene")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_wait_shape_match",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an unconstrained Shape must not invoke matching")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_click_frame_point",
        lambda _ctx, _image, x, y: clicked.append((x, y)),
    )

    assert _drain_generator(runtime.wait_click(302, "确定")) is None
    assert clicked == [(630.0, 1040.0)]


def test_runtime_wait_click_constrained_shape_uses_only_local_shape_match(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    action_shape = {
        "id": "seat",
        "kind": "rect",
        "title": "入座",
        "imageMatchRole": "off",
        "ocrMatchRole": "required",
        "ocrText": "入座",
        "x": 0.6,
        "y": 0.6,
        "w": 0.2,
        "h": 0.1,
    }
    image301 = _scene_image("论道座位", "0301.png", [action_shape])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {301: image301}},
        stop_event=threading.Event(),
    )
    matched: list[tuple[dict, dict]] = []
    clicked: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        runtime,
        "wait_view",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("wait_click must not run whole-scene recognition")
        ),
    )

    def fake_wait_shape_match(_ctx, _stop_event, image, shape, **_kwargs):
        matched.append((image, shape))
        if False:
            yield None
        return "local-frame", {"matched": True, "similarity": 100.0}

    monkeypatch.setattr(runner, "_wait_shape_match", fake_wait_shape_match)
    monkeypatch.setattr(
        runner,
        "_click_shape",
        lambda _ctx, _image, _shape, frame, match_result=None: clicked.append((frame, match_result)),
    )

    assert _drain_generator(runtime.wait_click(301, "入座")) is None
    assert len(matched) == 1
    assert matched[0][0] is image301
    assert matched[0][1]["id"] == "seat"
    assert clicked == [("local-frame", {"matched": True, "similarity": 100.0})]


def test_runtime_wait_shape_does_not_invoke_scene_or_popup_recognition(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image302 = _scene_image("论道入座确认", "0302.png", [{
        "id": "prompt",
        "kind": "rect",
        "title": "是否入座",
        "imageMatchRole": "off",
        "ocrMatchRole": "required",
        "ocrText": "空位入座",
        "x": 0.2,
        "y": 0.3,
        "w": 0.4,
        "h": 0.1,
    }])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {302: image302}},
        stop_event=threading.Event(),
    )
    calls: list[str] = []

    monkeypatch.setattr(
        runtime,
        "current_scene",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("wait_shape must not inject #47 through scene recognition")
        ),
    )

    def fake_wait_shape_match(*_args, **_kwargs):
        calls.append("shape")
        if False:
            yield None
        return "frame302", {"matched": True, "similarity": 99.0}

    monkeypatch.setattr(runner, "_wait_shape_match", fake_wait_shape_match)

    assert _drain_generator(runtime.wait_shape(302, "是否入座")) == "frame302"
    assert calls == ["shape"]


def test_runtime_wait_shape_unconstrained_shape_returns_without_matching(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image302 = _scene_image("论道入座确认", "0302.png", [{
        "id": "confirm",
        "kind": "rect",
        "title": "确定",
        "imageMatchRole": "off",
        "ocrMatchRole": "off",
        "x": 0.6,
        "y": 0.6,
        "w": 0.2,
        "h": 0.1,
    }])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {302: image302}},
        stop_event=threading.Event(),
    )
    runtime.frame_data_url = "already-observed-business-frame"

    monkeypatch.setattr(
        runtime,
        "current_scene",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an unconstrained Shape must not recognize a scene")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_wait_shape_match",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an unconstrained Shape must not invoke image/OCR matching")
        ),
    )

    assert _drain_generator(runtime.wait_shape(302, "确定")) == "already-observed-business-frame"


def test_runtime_advance_dialogue_requires_five_second_quiet_window(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image373 = _scene_image("论道对话", "0373.png")
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {373: image373}},
        stop_event=threading.Event(),
    )
    scenes = iter([(373, 100.0, "first"), (373, 100.0, "second"), *[(None, 0.0, "transition")] * 10])
    actions: list[tuple] = []
    clock = [0.0]

    monkeypatch.setattr(behavior_tree_runtime_core.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime, "current_scene", lambda *_args, **_kwargs: next(scenes))
    monkeypatch.setattr(runtime, "click_shape_center", lambda *args, **kwargs: actions.append(("click", args, kwargs)))

    def fake_wait_action_settle(seconds):
        actions.append(("settle", seconds))
        clock[0] += seconds
        yield BehaviorTreeStatus.RUNNING

    monkeypatch.setattr(runtime, "wait_action_settle", fake_wait_action_settle)

    result = _drain_generator(runtime.advance_dialogue(373, "聊天按钮"))

    assert result == 2
    click_actions = [action for action in actions if action[0] == "click"]
    assert [(action[1][0].id, action[1][1]) for action in click_actions] == [
        (373, "聊天按钮"),
        (373, "聊天按钮"),
    ]
    assert [action[1] for action in actions if action[0] == "settle"][-10:] == [0.5] * 10


def test_runtime_advance_dialogue_stops_after_bounded_clicks(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {373: _scene_image("论道对话", "0373.png")}},
        stop_event=threading.Event(),
    )
    clicks: list[tuple] = []
    clock = [0.0]

    monkeypatch.setattr(behavior_tree_runtime_core.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime, "current_scene", lambda *_args, **_kwargs: (373, 100.0, "dialogue"))
    monkeypatch.setattr(runtime, "click_shape_center", lambda *args, **kwargs: clicks.append((args, kwargs)))

    def fake_wait_action_settle(seconds):
        clock[0] += seconds
        yield BehaviorTreeStatus.RUNNING

    monkeypatch.setattr(runtime, "wait_action_settle", fake_wait_action_settle)

    with pytest.raises(RuntimeError, match="连续推进 3 次"):
        _drain_generator(runtime.advance_dialogue(373, "聊天按钮", max_clicks=3))
    assert len(clicks) == 3


def test_runtime_current_scene_returns_explicit_business_candidate_before_popup_handling(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    popup302 = _scene_image("论道入座确认", "0302.png")
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {302: popup302}, "asset_tree": []},
    )
    runtime.candidates = [{"image": popup302, "folder_path": "弹窗"}]

    monkeypatch.setattr(runner, "_handle_disconnect_reconnect_popup", lambda _runtime: False)
    monkeypatch.setattr(runner, "_skip_popup_guard_on_login_or_maintenance", lambda _runtime: False)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (302, 100.0))
    monkeypatch.setattr(
        runner,
        "_handle_recognized_popup_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("业务候选不应执行弹窗中断动作")),
    )

    assert runtime.current_scene([302], frame_data_url="frame") == (302, 100.0, "frame")


def test_runtime_current_scene_promotes_explicit_maintenance_scene_before_popup(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    popup546 = _scene_image("登录维护提示", "0546.png")
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {546: popup546}, "asset_tree": []},
    )
    runtime.candidates = [{"image": popup546, "folder_path": "弹窗"}]
    calls: list[tuple[int, dict[str, object]]] = []

    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (546, 100.0))
    monkeypatch.setattr(
        runner,
        "_handle_recognized_popup_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("维护提示不得当通用弹窗点击")
        ),
    )

    def raise_maintenance(*, scene_id, evidence: dict[str, object]):
        calls.append((scene_id, dict(evidence)))
        raise RuntimeError("maintenance-promoted")

    monkeypatch.setattr(runner, "_raise_game_maintenance", raise_maintenance)

    with pytest.raises(RuntimeError, match="maintenance-promoted"):
        runtime.current_scene([34], frame_data_url="maintenance-frame")
    assert calls == [(546, {
        "stage": "maintenance_scene",
        "recognized_scene_id": 546,
    })]



def test_go_scene_payload_normalizes_layer0_wait_aliases():
    from backend.core.fanxiu.data_annotation.jobs import normalize_data_annotation_go_scene_payload

    assert normalize_data_annotation_go_scene_payload({"target_scene_id": "#171", "wait_seconds": 60}) == {
        "target_scene_id": 171,
        "wait_seconds": 60,
        "layer0_wait_seconds": 60.0,
    }
    assert normalize_data_annotation_go_scene_payload({"target": 34, "wait_time": 5})["layer0_wait_seconds"] == 5.0
    assert normalize_data_annotation_go_scene_payload({"target": 34, "layer0_wait_seconds": -1})["layer0_wait_seconds"] == 0.0


def test_go_scene_default_layer0_wait_seconds_is_high_confidence_window():
    assert behavior_tree_runtime_core.DEFAULT_LAYER0_WAIT_SECONDS == 30.0


def test_go_scene_default_unknown_requires_one_continuous_minute():
    assert behavior_tree_runtime_core.DEFAULT_GO_SCENE_CONTINUOUS_UNKNOWN_SECONDS == 60.0
    assert behavior_tree_runtime_core.DEFAULT_GO_SCENE_OBSERVATION_TIMEOUT_SECONDS == 60.0
    assert behavior_tree_runtime_core.DEFAULT_SCENE_RECOGNITION_POLL_SECONDS == 1.0


def test_offline_cultivation_landing_keeps_20_and_34_layer0_alive_for_reward_banners():
    runner = create_behavior_tree_runtime_runner()
    confirm = {"title": "确定", "sceneJumpTarget": "34(2),20(1)"}

    assert runner._scene_jump_preferred_wait_seconds(
        source_scene_id=609,
        target_scene_id=34,
        expected_ids=[34, 20],
        shape=confirm,
        requested_wait_seconds=None,
    ) == behavior_tree_runtime_core.OFFLINE_CULTIVATION_SETTLE_WAIT_SECONDS
    assert runner._scene_jump_preferred_wait_seconds(
        source_scene_id=608,
        target_scene_id=34,
        expected_ids=[34, 20],
        shape=confirm,
        requested_wait_seconds=None,
    ) == behavior_tree_runtime_core.DEFAULT_LAYER0_WAIT_SECONDS


def test_offline_cultivation_transition_waits_past_default_timeout_for_scene20(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    confirm = {
        "id": "confirm",
        "kind": "rect",
        "title": "确定",
        "sceneJumpTarget": "34(2),20(1)",
    }
    image609 = _scene_image("离线修炼结算", "0609.png", [confirm])
    image20 = _scene_image("绿瓶", "0020.png", [], layer=1)
    image34 = _scene_image("世界", "0034.png", [], layer=1)
    tree = [image609, image20, image34]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {
        "entry": object(),
        "asset_tree": tree,
        "asset_tree_path": path,
        "images": runner._index_images(tree),
    }
    clock = {"value": 0.0}
    recorded: list[int] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def current_scene(
        _runtime,
        views=None,
        *,
        frame_data_url=None,
        update=False,
        include_popup_candidates=True,
    ):
        del views, update, include_popup_candidates
        if clock["value"] >= 90.0:
            return 20, 100.0, frame_data_url
        return None, 0.0, frame_data_url

    def settle(_ctx, _stop_event, *, seconds):
        del seconds
        clock["value"] += 31.0
        if False:
            yield BehaviorTreeStatus.RUNNING

    monkeypatch.setattr(behavior_tree_runtime_core.time, "monotonic", lambda: clock["value"])
    monkeypatch.setattr(behavior_tree_runtime_core.BehaviorTreeRuntime, "current_scene", current_scene)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "offline-reward-banner")
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", settle)
    monkeypatch.setattr(runner, "_identify_scene_number_for_route", lambda *_args, **_kwargs: (None, 0.0))
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (None, 0.0))
    monkeypatch.setattr(
        runner,
        "_save_unknown_scene_frame",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must keep waiting")),
    )
    monkeypatch.setattr(
        runner,
        "_record_scene_jump_landing",
        lambda _ctx, _path, _tree, _shape, scene_id, **_kwargs: recorded.append(scene_id),
    )

    result = _drain_generator(runner._wait_scene_jump_result(
        ctx,
        path,
        tree,
        source_scene_id=609,
        target_scene_id=34,
        edge={"source_id": 609, "image": image609, "shape": confirm, "target_ids": [34, 20]},
        stop_event=FakeStopEvent(),
    ))

    assert result == 20
    assert clock["value"] == 93.0
    assert recorded == [20]


def test_scene_jump_layer0_wait_explicitly_sleeps_before_fresh_frame(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    entry_shape = {
        "id": "xianshi",
        "kind": "rect",
        "title": "仙市",
        "sceneJumpTarget": "247",
    }


    image34 = _scene_image("世界", "0034.png", [entry_shape], layer=1)
    image247 = _scene_image("仙市入口", "0247.png", [], layer=2)
    tree = [image34, image247]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {
        "entry": object(),
        "asset_tree": tree,
        "asset_tree_path": path,
        "images": {34: image34, 247: image247},
    }
    state = {"frame": "transition"}
    sleeps: list[float] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def current_scene(
        _runtime,
        views=None,
        *,
        frame_data_url=None,
        update=False,
        include_popup_candidates=True,
    ):
        del views, update, include_popup_candidates
        if frame_data_url == "scene-247":
            return 247, 100.0, frame_data_url
        return None, 0.0, frame_data_url

    def settle(_ctx, _stop_event, *, seconds):
        sleeps.append(float(seconds))
        state["frame"] = "scene-247"
        if False:
            yield BehaviorTreeStatus.RUNNING

    monkeypatch.setattr(behavior_tree_runtime_core.BehaviorTreeRuntime, "current_scene", current_scene)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: state["frame"])
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", settle)
    monkeypatch.setattr(runner, "_record_scene_jump_landing", lambda *_args, **_kwargs: None)

    result = _drain_generator(runner._wait_scene_jump_result(
        ctx,
        path,
        tree,
        source_scene_id=34,
        target_scene_id=247,
        edge={"source_id": 34, "image": image34, "shape": entry_shape, "target_ids": [247]},
        stop_event=FakeStopEvent(),
        layer0_wait_seconds=30.0,
    ))

    assert result == 247
    assert sleeps == [
        pytest.approx(
            behavior_tree_runtime_core.DEFAULT_SCENE_RECOGNITION_POLL_SECONDS
        )
    ]


def test_scene_jump_accepts_route_capable_unexpected_landing_without_waiting_layer0(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    entry_shape = {
        "id": "schedule",
        "kind": "rect",
        "title": "日程",
        "sceneJumpTarget": "3",
    }
    return_shape = {
        "id": "return",
        "kind": "rect",
        "title": "返回",
        "sceneJumpTarget": "3",
    }
    image1 = _scene_image("世界", "0001.png", [entry_shape], layer=1)
    image2 = _scene_image("活动硬打断", "0002.png", [return_shape], layer=1)
    image3 = _scene_image("日程", "0003.png", [], layer=1)
    tree = [image1, image2, image3]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {
        "entry": object(),
        "asset_tree": tree,
        "asset_tree_path": path,
        "images": runner._index_images(tree),
    }
    sleeps: list[float] = []
    recorded: list[int] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def current_scene(_runtime, views=None, *, frame_data_url=None, update=False, **_kwargs):
        del views, update
        return None, 0.0, frame_data_url

    def settle(_ctx, _stop_event, *, seconds):
        sleeps.append(float(seconds))
        if False:
            yield BehaviorTreeStatus.RUNNING

    monkeypatch.setattr(behavior_tree_runtime_core.BehaviorTreeRuntime, "current_scene", current_scene)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "scene-2")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (2, 100.0))
    monkeypatch.setattr(runner, "_identify_scene_number_for_route", lambda *_args, **_kwargs: (2, 100.0))
    monkeypatch.setattr(runner, "_navigation_scene_id", lambda _ctx, scene_id, _frame: scene_id)
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", settle)
    monkeypatch.setattr(
        runner,
        "_record_scene_jump_landing",
        lambda _ctx, _path, _tree, _shape, scene_id, **_kwargs: recorded.append(scene_id),
    )

    result = _drain_generator(runner._wait_scene_jump_result(
        ctx,
        path,
        tree,
        source_scene_id=1,
        target_scene_id=3,
        edge={"source_id": 1, "image": image1, "shape": entry_shape, "target_ids": [3]},
        stop_event=FakeStopEvent(),
        layer0_wait_seconds=30.0,
    ))

    assert result == 2
    assert sleeps == []
    assert recorded == [2]


def test_scene_jump_scoped_world_miss_commits_only_final_414_landing(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    source_shape = {
        "id": "open",
        "kind": "rect",
        "title": "打开",
        "sceneJumpTarget": "34",
    }
    return_shape = {
        "id": "return",
        "kind": "rect",
        "title": "返回",
        "sceneJumpTarget": "34",
    }
    image171 = _scene_image("仙府", "0171.png", [source_shape], layer=1)
    image34 = _scene_image("世界", "0034.png", [], layer=1)
    image414 = _scene_image("潜修真悟", "0414.png", [return_shape], layer=2)
    tree = [image171, image34, image414]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {
        "entry": object(),
        "asset_tree": tree,
        "asset_tree_path": path,
        "images": runner._index_images(tree),
    }
    commits: list[tuple[int | None, float, str]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def current_scene(_runtime, views=None, *, frame_data_url=None, **_kwargs):
        assert views == [34]
        return None, 0.0, frame_data_url

    monkeypatch.setattr(behavior_tree_runtime_core.BehaviorTreeRuntime, "current_scene", current_scene)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "same-frame")
    monkeypatch.setattr(runner, "_identify_scene_number_for_route", lambda *_args, **_kwargs: (414, 100.0))
    monkeypatch.setattr(runner, "_record_scene_jump_landing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner,
        "_commit_scene_observation",
        lambda _ctx, frame, scene_id, score: commits.append((scene_id, score, frame)),
    )

    result = _drain_generator(runner._wait_scene_jump_result(
        ctx,
        path,
        tree,
        source_scene_id=171,
        target_scene_id=34,
        edge={"source_id": 171, "image": image171, "shape": source_shape, "target_ids": [34]},
        stop_event=FakeStopEvent(),
        layer0_wait_seconds=30.0,
    ))

    assert result == 414
    assert commits == [(414, 100.0, "same-frame")]


def test_go_scene_rejects_stale_target_hit_when_fresh_frame_is_another_scene(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _scene_image("世界", "0034.png", [], layer=1)
    image66 = _scene_image("日程", "0066.png", [], layer=1)
    tree = [image34, image66]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {
        "entry": object(),
        "asset_tree": tree,
        "asset_tree_path": path,
        "images": runner._index_images(tree),
    }

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    class ReplannedFromWorld(RuntimeError):
        pass

    def recognize_stale_target(*_args, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return 66, 100.0, "stale-66", "matched"

    def identify(_ctx, frame, preferred_scene_ids=None, **_kwargs):
        if frame == "fresh-34":
            if preferred_scene_ids == [66]:
                return None, 0.0
            return 34, 100.0
        return 66, 100.0

    def settle(_ctx, _stop_event, *, seconds):
        assert seconds == pytest.approx(behavior_tree_runtime_core.DEFAULT_SCENE_RECOGNITION_POLL_SECONDS)
        if False:
            yield BehaviorTreeStatus.RUNNING

    def select_edge(_tree, current_scene_id, target_scene_id, **_kwargs):
        assert current_scene_id == 34
        assert target_scene_id == 66
        raise ReplannedFromWorld

    frames = iter(["initial", "fresh-34", "next-loop"])
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(runner, "_wait_for_go_scene_recognition", recognize_stale_target)
    monkeypatch.setattr(runner, "_identify_scene_number", identify)
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", settle)
    monkeypatch.setattr(runner, "_select_scene_next_edge", select_edge)

    with pytest.raises(ReplannedFromWorld):
        _drain_generator(runner._go_scene_task(
            ctx,
            path,
            66,
            FakeStopEvent(),
        ))


def test_go_scene_continuous_unknown_is_cancelled_by_any_known_scene(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    ctx: dict[str, object] = {}
    state = {"now": 100.0, "frame": "transition-0"}

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def current_scene(self, views=None, *, frame_data_url=None, update=False, **_kwargs):
            del views, update
            if frame_data_url == "scene-185":
                return 185, 100.0, frame_data_url
            return None, 80.0, frame_data_url

    def settle(_ctx, _stop_event, *, seconds):
        state["now"] += float(seconds)
        state["frame"] = (
            "scene-185"
            if state["now"] >= 159.5
            else f"transition-{state['now']:.1f}"
        )
        if False:
            yield BehaviorTreeStatus.RUNNING

    monkeypatch.setattr(behavior_tree_runtime_core.time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: state["frame"])
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", settle)
    monkeypatch.setattr(runner, "_scene_route_candidate_ids", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "_identify_scene_number_for_route", lambda *_args, **_kwargs: (None, 0.0))
    monkeypatch.setattr(
        behavior_tree_runtime_core,
        "_image_similarity_percent",
        lambda _runner, left, right: 100.0 if left == right else 0.0,
    )

    result = _drain_generator(runner._wait_for_go_scene_recognition(
        ctx,
        FakeRuntime(),
        [],
        171,
        FakeStopEvent(),
        state["frame"],
    ))

    assert result[:2] == (185, 100.0)
    assert state["now"] == pytest.approx(160.0)


def test_go_scene_continuous_unknown_includes_initial_recognition_time(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    ctx: dict[str, object] = {}
    state = {"now": 100.0, "first": True, "frame": "unknown"}

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def current_scene(self, views=None, *, frame_data_url=None, update=False, **_kwargs):
            del views, update
            if state["first"]:
                state["first"] = False
                state["now"] += 2.0
            ctx["_last_scene_recognition_status"] = "no_match"
            ctx["_last_layer3_auxiliary"] = {
                "reference_id": 370,
                "score": 82.7,
                "threshold": 90.0,
                "above_threshold": False,
            }
            return None, 82.7, frame_data_url

    def settle(_ctx, _stop_event, *, seconds):
        state["now"] += float(seconds)
        if False:
            yield BehaviorTreeStatus.RUNNING

    monkeypatch.setattr(behavior_tree_runtime_core.time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: state["frame"])
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", settle)
    monkeypatch.setattr(runner, "_scene_route_candidate_ids", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "_identify_scene_number_for_route", lambda *_args, **_kwargs: (None, 0.0))

    result = _drain_generator(runner._wait_for_go_scene_recognition(
        ctx,
        FakeRuntime(),
        [],
        34,
        FakeStopEvent(),
        state["frame"],
    ))

    assert result == (None, 82.7, "unknown", "continuous_unknown")
    assert state["now"] == pytest.approx(160.0)
    assert ctx["_last_go_scene_recognition_evidence"] == {
        "attempts": 59,
        "continuous_unknown_seconds": 60.0,
        "best_score": 82.7,
        "best_layer3_auxiliary": {
            "reference_id": 370,
            "score": 82.7,
            "threshold": 90.0,
            "above_threshold": False,
        },
        "last_unresolved_status": "no_match",
    }


def test_identify_scene_number_returns_graph_ambiguity_without_legacy_fallback(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    monkeypatch.setattr(
        runner,
        "_identify_scene_number_by_graph",
        lambda *_args, **_kwargs: (None, 100.0, "ambiguous"),
    )

    trace: list[dict] = []
    scene_id, score = runner._identify_scene_number({}, _png_data_url(), trace=trace)

    assert (scene_id, score) == (None, 100.0)
    assert not any("tree" in str(item).lower() or "fallback" in str(item).lower() for item in trace)


def test_identify_scene_number_returns_graph_match_directly(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    calls: list[list[int] | None] = []

    def identify(_ctx, _frame, preferred_scene_ids=None, trace=None):
        calls.append(preferred_scene_ids)
        return 276, 100.0, "graph_nearest"

    monkeypatch.setattr(runner, "_identify_scene_number_by_graph", identify)
    scene_id, score = runner._identify_scene_number({}, _png_data_url(), [276])

    assert (scene_id, score) == (276, 100.0)
    assert calls == [[276]]


def test_runtime_goto_view_forwards_layer0_wait_seconds(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    ctx = {"images": {}}
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, asset_tree_path=tmp_path / "asset_tree.json", stop_event=threading.Event())
    captured: dict[str, object] = {}

    def fake_go_scene(_ctx, _asset_tree_path, target_scene_id, _stop_event, **kwargs):
        captured.update({"target_scene_id": target_scene_id, **kwargs})
        return "success"

    monkeypatch.setattr(runner, "_go_scene_task", fake_go_scene)

    assert _drain_generator(runtime.goto_view(171, wait_seconds=60)) == "success"
    assert captured == {"target_scene_id": 171, "layer0_wait_seconds": 60}


def test_runtime_go_scene_forwards_to_scene_movement(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, {"images": {}}, asset_tree_path=tmp_path / "asset_tree.json", stop_event=threading.Event())
    captured: dict[str, object] = {}
    emitted_actions = []

    def fake_go_scene(_ctx, _asset_tree_path, target_scene_id, _stop_event, **kwargs):
        captured.update({"target_scene_id": target_scene_id, **kwargs})
        return "success"

    monkeypatch.setattr(runner, "_go_scene_task", fake_go_scene)
    monkeypatch.setattr(runtime, "_emit_runtime_action", lambda message, **kwargs: emitted_actions.append((message, kwargs)))

    assert _drain_generator(runtime.go_scene(171, layer0_wait_seconds=45)) == "success"
    assert captured == {"target_scene_id": 171, "layer0_wait_seconds": 45}
    assert emitted_actions[0][1]["phase"] == "runtime_go_scene"
    assert emitted_actions[0][1]["source_info"]["action"] == "go_scene"


def test_runtime_shape_lookup_uses_declared_shape_parent_and_child_host():
    runner = create_behavior_tree_runtime_runner()
    parent = _scene_image(
        "日常",
        "0069.png",
        [{"id": "daily-exit", "title": "退出", "x": 0.1, "y": 0.2, "w": 0.1, "h": 0.1}],
        layer=1,
    )
    child = _scene_image(
        "活动报名",
        "0075.png",
        [{"id": "signup", "title": "报名", "x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1}],
        layer=1,
    )
    child["parentSceneIds"] = "69"
    parent["children"] = [child]
    tree = [parent]
    ctx = {"asset_tree": tree, "images": runner._index_images(tree), "attrs": {}}
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx)

    inherited = runtime.shape(75, "退出")

    assert inherited.raw["id"] == "daily-exit"
    assert inherited.parent_view.id == 75


def test_runtime_physical_asset_parent_does_not_inherit_shapes():
    runner = create_behavior_tree_runtime_runner()
    parent = _scene_image(
        "日常",
        "0069.png",
        [{"id": "parent-daily", "title": "日常", "x": 0.1, "y": 0.2, "w": 0.1, "h": 0.1}],
        layer=1,
    )
    child = _scene_image(
        "活动报名",
        "0075.png",
        [{"id": "child-daily", "title": "日常", "x": 0.3, "y": 0.4, "w": 0.1, "h": 0.1}],
        layer=1,
    )
    parent["children"] = [child]
    tree = [parent]
    ctx = {"asset_tree": tree, "images": runner._index_images(tree), "attrs": {}}
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx)

    found = runtime.shape(75, "日常")

    assert found.raw["id"] == "child-daily"
    assert found.parent_view.id == 75


def test_runtime_ocr_region_does_not_inherit_from_physical_asset_parent():
    runner = create_behavior_tree_runtime_runner()
    parent = _scene_image(
        "日常",
        "0069.png",
        [{"id": "parent-window", "title": "滚动窗口", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6}],
        layer=1,
    )
    child = _scene_image("活动报名", "0075.png", [], layer=1)
    parent["children"] = [child]
    tree = [parent]
    ctx = {"asset_tree": tree, "images": runner._index_images(tree), "attrs": {}}

    assert runner._query_box_for_shapes(
        ctx["images"][75],
        ("滚动窗口",),
        ctx=ctx,
    ) is None


def test_runtime_ocr_region_uses_explicit_shape_parent():
    runner = create_behavior_tree_runtime_runner()
    parent = _scene_image(
        "日常",
        "0069.png",
        [{"id": "parent-window", "title": "滚动窗口", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6}],
        layer=1,
    )
    child = _scene_image("活动报名", "0075.png", [], layer=1)
    child["parentSceneIds"] = "69"
    parent["children"] = [child]
    tree = [parent]
    ctx = {"asset_tree": tree, "images": runner._index_images(tree), "attrs": {}}

    assert runner._query_box_for_shapes(
        ctx["images"][75],
        ("滚动窗口",),
        padding=0,
        ctx=ctx,
    ) == {"x": 90.0, "y": 320.0, "w": 720.0, "h": 960.0}


def test_runtime_drag_shape_to_frame_edge_uses_shape_start_and_screen_edge(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    scene = _scene_image(
        "快速探索",
        "0314.png",
        [{"id": "amount-slider", "title": "滚动条", "x": 0.25, "y": 0.5, "w": 0.5, "h": 0.04}],
    )
    tree = [scene]
    ctx = {"asset_tree": tree, "images": runner._index_images(tree), "attrs": {}}
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx)
    drags: list[tuple[float, float, float, float, int]] = []

    monkeypatch.setattr(
        runner,
        "_drag_frame_point",
        lambda _ctx, _image, sx, sy, ex, ey, duration_ms=300: drags.append((sx, sy, ex, ey, duration_ms)),
    )

    runtime.drag_shape_to_frame_edge(314, "滚动条", direction="right", duration=0.6)

    assert drags == [(243.0, 832.0, 882.0, 832.0, 600)]


def test_daily_scroll_safe_viewport_keeps_visible_first_row_and_excludes_bottom_navigation():
    runner = create_behavior_tree_runtime_runner()
    daily = _scene_image(
        "日常",
        "0069.png",
        [
            {"id": "daily-list", "title": "滚动窗口", "x": 0.08, "y": 0.2, "w": 0.88, "h": 0.6},
            {"id": "daily-row", "title": "任务块模板", "x": 0.42, "y": 0.26, "w": 0.53, "h": 0.12},
        ],
        layer=1,
    )
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"asset_tree": [daily], "images": runner._index_images([daily]), "attrs": {}},
    )
    view = runtime.view(69)

    safe = runtime._daily_scroll_safe_shape(view, runtime.shape(view, "滚动窗口"))
    box = runner._box(safe.raw, view.raw)

    assert box["y"] == pytest.approx((0.2 + 0.6 * 0.08) * 1600)
    assert box["y"] < (0.26 + 0.12 / 2) * 1600
    assert box["y"] + box["h"] <= (0.2 + 0.6 * 0.78) * 1600 + 1


def test_daily_scroll_safe_viewport_does_not_hide_real_lundao_row():
    runner = create_behavior_tree_runtime_runner()
    daily = _scene_image(
        "日常",
        "0069.png",
        [
            {"id": "daily-list", "title": "滚动窗口", "x": 70 / 900, "y": 410 / 1600, "w": 790 / 900, "h": 1046 / 1600},
            {"id": "daily-row", "title": "任务块模板", "x": 0.42, "y": 0.26, "w": 0.53, "h": 0.115},
        ],
        layer=1,
    )
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"asset_tree": [daily], "images": runner._index_images([daily]), "attrs": {}},
    )
    view = runtime.view(69)
    safe = runtime._daily_scroll_safe_shape(view, runtime.shape(view, "滚动窗口"))
    lines = [{"text": "参与论道1小时", "x": 405, "y": 551, "w": 288, "h": 44}]

    searchable = runtime._daily_lines_in_shape(lines, safe)
    matches = runtime._daily_entry_matches(searchable, view, title_pattern="论道")

    assert matches == [(549.0, 573.0, "参与论道1小时")]


def test_open_daily_entry_keeps_scrolling_when_ocr_rows_move(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    daily = _scene_image(
        "日常",
        "0069.png",
        [{"id": "daily-list", "title": "滚动窗口", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6}],
        layer=1,
    )
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"asset_tree": [daily], "images": runner._index_images([daily]), "attrs": {}},
    )
    scroll_calls: list[str] = []

    frame_index = {"value": 0}

    def fake_cur_frame(update=False):
        frame_index["value"] += 1
        return f"frame-{frame_index['value']}"

    def fake_ocr(_ctx, frame):
        index = int(str(frame).rsplit("-", 1)[-1])
        offset = min(index, 6) * 20
        return [
            {"text": "日常 活跃度100", "x": 80, "y": 250, "w": 740, "h": 70},
            {"text": "接受韩立指导", "x": 410, "y": 800 - offset, "w": 220, "h": 44},
        ]

    monkeypatch.setattr(runtime, "cur_frame", fake_cur_frame)
    monkeypatch.setattr(runner, "_ocr_fragments_in_scene_shapes", lambda ctx, frame, _image: fake_ocr(ctx, frame))
    monkeypatch.setattr(runtime, "_ensure_daily_list_frame", lambda frame, lines, label: None)
    monkeypatch.setattr(runtime, "_daily_entry_matches", lambda lines, view, title_pattern, exclude_pattern=None: [])
    monkeypatch.setattr(runtime, "_emit_runtime_action", lambda *args, **kwargs: None)

    def fake_scroll(view, shape, *, direction=None, recognition_shape=None):
        scroll_calls.append(str(direction))
        assert recognition_shape is not None
        if False:
            yield BehaviorTreeStatus.RUNNING
        return False

    monkeypatch.setattr(runtime, "scroll_shape_content", fake_scroll)

    result = _drain_generator(runtime.open_daily_entry(
        label="日常_测试",
        title_pattern=r"不存在",
        max_scrolls=5,
    ))

    assert result == "not_found"
    assert scroll_calls == ["down", "down", "down", "down", "down"]
    # Initial page plus one fresh frame per scroll.  The post-scroll OCR is
    # reused by the next search iteration instead of recognizing the same
    # unchanged page twice.
    assert frame_index["value"] == 6


def test_open_daily_entry_rechecks_first_screen_before_scrolling(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    daily = _scene_image(
        "日常",
        "0069.png",
        [{"id": "daily-list", "title": "滚动窗口", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6}],
        layer=1,
    )
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"asset_tree": [daily], "images": runner._index_images([daily]), "attrs": {}},
    )
    frames: list[str] = []
    clicks: list[tuple[float, float]] = []
    scrolls: list[str] = []

    def fake_cur_frame(update=False):
        frame = f"frame-{len(frames) + 1}"
        frames.append(frame)
        return frame

    monkeypatch.setattr(runtime, "cur_frame", fake_cur_frame)
    monkeypatch.setattr(runner, "_cached_ocr_fragments", lambda _ctx, _frame: [{"text": "日常 活跃度", "x": 80, "y": 250, "w": 740, "h": 70}])
    monkeypatch.setattr(runtime, "_ensure_daily_list_frame", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runtime,
        "_daily_entry_matches",
        lambda *_args, **_kwargs: [(450.0, 620.0, "参与灵脉争夺1小时")] if len(frames) == 4 else [],
    )
    monkeypatch.setattr(runtime, "_daily_entry_row_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_emit_runtime_action", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "click_frame_point", lambda _view, x, y: clicks.append((x, y)))

    def fake_wait(*_args, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING

    def fake_scroll(_view, _shape, *, direction=None):
        scrolls.append(str(direction))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return True

    monkeypatch.setattr(runtime, "wait_action_settle", fake_wait)
    monkeypatch.setattr(runtime, "scroll_shape_content", fake_scroll)

    result = _drain_generator(runtime.open_daily_entry(
        label="灵脉_座位",
        title_pattern=r"灵脉",
        max_scrolls=2,
        initial_checks=10,
    ))

    assert result == "open"
    assert frames == ["frame-1", "frame-2", "frame-3", "frame-4"]
    assert scrolls == []
    assert clicks == [(450.0, 620.0)]


def test_open_daily_entry_rechecks_occluded_top_row_without_scrolling(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    daily = _scene_image(
        "日常",
        "0069.png",
        [{"id": "daily-list", "title": "滚动窗口", "x": 70 / 900, "y": 410 / 1600, "w": 790 / 900, "h": 1046 / 1600}],
        layer=1,
    )
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"asset_tree": [daily], "images": runner._index_images([daily]), "attrs": {}},
    )
    frame_index = {"value": 0}
    clicks: list[tuple[float, float]] = []

    def fake_cur_frame(update=False):
        frame_index["value"] += 1
        return f"frame-{frame_index['value']}"

    def fake_ocr(_ctx, frame):
        index = int(str(frame).rsplit("-", 1)[-1])
        if index >= 2:
            return [{"text": "完成仙窍试炼", "x": 430, "y": 420, "w": 245, "h": 45}]
        return [{"text": "世界公告遮挡", "x": 200, "y": 420, "w": 300, "h": 45}]

    monkeypatch.setattr(runtime, "cur_frame", fake_cur_frame)
    monkeypatch.setattr(runner, "_ocr_fragments_in_scene_shapes", lambda ctx, frame, _image: fake_ocr(ctx, frame))
    monkeypatch.setattr(runtime, "_ensure_daily_list_frame", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_daily_entry_row_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_emit_runtime_action", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "click_frame_point", lambda _view, x, y: clicks.append((x, y)))

    def fake_wait(*_args, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING

    def fake_scroll(_view, _shape, *, direction=None, recognition_shape=None):
        raise AssertionError("起始屏复识别不应触发滚动")

    monkeypatch.setattr(runtime, "wait_action_settle", fake_wait)
    monkeypatch.setattr(runtime, "scroll_shape_content", fake_scroll)

    result = _drain_generator(runtime.open_daily_entry(
        label="仙窍_试炼",
        title_pattern=r"仙\s*窍",
        max_scrolls=0,
        initial_checks=2,
    ))

    assert result == "open"
    assert clicks == [(552.5, 442.5)]


def test_runtime_click_inherited_shape_uses_child_host_scene(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    parent = _scene_image(
        "日常",
        "0069.png",
        [{"id": "daily-exit", "title": "退出", "x": 0.1, "y": 0.2, "w": 0.1, "h": 0.1}],
        layer=1,
    )
    child = _scene_image("活动报名", "0075.png", [], layer=1)
    child["parentSceneIds"] = "69"
    parent["children"] = [child]
    tree = [parent]
    ctx = {"asset_tree": tree, "images": runner._index_images(tree), "attrs": {}}
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx)
    clicked: list[tuple[int | None, str]] = []

    monkeypatch.setattr(runner, "_shape_click_needs_frame", lambda _shape: False)
    monkeypatch.setattr(
        runner,
        "_click_shape",
        lambda _ctx, image, shape, *_args, **_kwargs: clicked.append((runner._image_number(image), shape["id"])),
    )

    runtime.click_shape(75, "退出")

    assert clicked == [(75, "daily-exit")]




def test_runtime_wait_click_then_any_closes_click_with_branch_probe(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, {"images": {}}, stop_event=threading.Event())
    actions: list[tuple] = []
    conditions = {"purchase": object(), "empty": object()}

    def fake_wait_click(frame, shape, **kwargs):
        actions.append(("wait_click", frame, shape, kwargs))
        if False:
            yield None
        return "clicked"

    def fake_wait_action_settle(seconds=1.0):
        actions.append(("settle", seconds))
        if False:
            yield None
        return None

    def fake_wait_any(actual_conditions, **kwargs):
        actions.append(("wait_any", actual_conditions, kwargs))
        if False:
            yield None
        return "empty"

    monkeypatch.setattr(runtime, "wait_click", fake_wait_click)
    monkeypatch.setattr(runtime, "wait_action_settle", fake_wait_action_settle)
    monkeypatch.setattr(runtime, "wait_any", fake_wait_any)

    result = _drain_generator(runtime.wait_click_then_any(
        228,
        "购买",
        conditions,
        timeout=9,
        settle_seconds=1.5,
        label="等待购买体力结果",
    ))

    assert result == "empty"
    assert actions == [
        ("wait_click", 228, "购买", {}),
        ("settle", 1.5),
        ("wait_any", conditions, {"timeout": 9, "label": "等待购买体力结果"}),
    ]


def test_runtime_wait_click_then_view_infers_scene_jump_target(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image69 = _image("日常", "0069.png", [{"title": "拜谒", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1, "sceneJumpTarget": "264"}])
    image264 = _image("拜谒", "0264.png", [])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {69: image69, 264: image264}, "asset_tree": [image69, image264]},
        stop_event=threading.Event(),
    )
    actions: list[tuple] = []

    def fake_wait_click(frame, shape, **kwargs):
        actions.append(("wait_click", getattr(frame, "id", frame), getattr(shape, "title", shape), kwargs))
        if False:
            yield None
        return None

    def fake_wait_action_settle(seconds=1.0):
        actions.append(("settle", seconds))
        if False:
            yield None
        return None

    def fake_wait_view(*views, **kwargs):
        actions.append(("wait_view", views, kwargs))
        if False:
            yield None
        return runtime.view(264)

    monkeypatch.setattr(runtime, "wait_click", fake_wait_click)
    monkeypatch.setattr(runtime, "wait_action_settle", fake_wait_action_settle)
    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)

    result = _drain_generator(runtime.wait_click_then_view(69, "拜谒", timeout=12.0, settle_seconds=1.25))

    assert result.id == 264
    assert actions == [
        ("wait_click", 69, "拜谒", {}),
        ("settle", 1.25),
        ("wait_view", (264,), {"timeout": 12.0, "label": "点击后等待目标场景 #264"}),
    ]


def test_runtime_open_sdk_bubble_menu_uses_live_shape_and_requires_popup_590(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image421 = _image("气泡", "0421.png", [{"title": "气泡", "floating": True}])
    image590 = _image("37手游弹窗", "0590.png", [])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {421: image421, 590: image590}, "asset_tree": [image421, image590]},
        stop_event=threading.Event(),
    )
    calls: list[tuple] = []

    boxes = iter([
        {"matched": True, "unique_match": True, "resolved_box": {"x": 810, "y": 446, "w": 90, "h": 92}},
        {"matched": True, "unique_match": True, "resolved_box": {"x": 0, "y": 634, "w": 90, "h": 92}},
    ])

    def fake_wait_view(target, **options):
        calls.append(("wait_view", target, options))
        if False:
            yield None
        return runtime.view(590)

    monkeypatch.setattr(
        runtime,
        "shape_matches",
        lambda _view, shape, **_kwargs: None if shape == "奖励浮层" else next(boxes),
    )
    monkeypatch.setattr(runtime, "drag_frame_point", lambda *args, **kwargs: calls.append(("drag", args, kwargs)))
    monkeypatch.setattr(runtime, "click_frame_point", lambda *args, **kwargs: calls.append(("click", args, kwargs)))
    monkeypatch.setattr(runtime, "wait_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)

    result = _drain_generator(runtime.open_sdk_bubble_menu(timeout=9.0, settle_seconds=0.75))

    assert result.id == 590
    assert calls[0][0] == "drag"
    assert calls[0][1][3:5] == (45.0, 680.0)
    assert calls[1][0] == "click"
    assert calls[2] == (
        "wait_view",
        590,
        {"timeout": 8.0, "label": "安全区点击气泡后等待37手游弹窗#590"},
    )


def test_runtime_open_sdk_bubble_menu_moves_low_left_dock_before_click(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image421 = _image("气泡", "0421.png", [{"title": "气泡", "floating": True}])
    image590 = _image("37手游弹窗", "0590.png", [])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {421: image421, 590: image590}, "asset_tree": [image421, image590]},
        stop_event=threading.Event(),
    )
    calls: list[tuple] = []
    boxes = iter([
        {"matched": True, "unique_match": True, "resolved_box": {"x": 17, "y": 845, "w": 57, "h": 67}},
        {"matched": True, "unique_match": True, "resolved_box": {"x": 17, "y": 647, "w": 57, "h": 67}},
    ])

    monkeypatch.setattr(
        runtime,
        "shape_matches",
        lambda _view, shape, **_kwargs: None if shape == "奖励浮层" else next(boxes),
    )
    monkeypatch.setattr(runtime, "drag_frame_point", lambda *args, **kwargs: calls.append(("drag", args, kwargs)))
    monkeypatch.setattr(runtime, "click_frame_point", lambda *args, **kwargs: calls.append(("click", args, kwargs)))
    monkeypatch.setattr(runtime, "wait_action_settle", lambda *_args, **_kwargs: iter(()))
    def fake_wait_view(*_args, **_kwargs):
        if False:
            yield None
        return runtime.view(590)

    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)

    result = _drain_generator(runtime.open_sdk_bubble_menu(timeout=9.0))

    assert result.id == 590
    assert calls[0][0] == "drag"
    assert calls[0][1][1:5] == (45.5, 878.5, 45.0, 680.0)
    assert calls[1][0] == "click"


def test_runtime_shape_matches_can_reuse_one_captured_frame(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image421 = _image(
        "气泡",
        "0421.png",
        [{
            "title": "气泡",
            "ocrMatchRole": "required",
            "ocrText": "气泡",
            "x": 0.0,
            "y": 0.5,
            "w": 0.1,
            "h": 0.1,
        }],
    )
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {421: image421}, "asset_tree": [image421]},
        stop_event=threading.Event(),
    )
    seen: list[str] = []

    monkeypatch.setattr(
        runtime,
        "cur_frame",
        lambda **_kwargs: pytest.fail("explicit frame must not trigger another screencap"),
    )

    def fake_match_shape(_ctx, _view, _shape, frame, **_kwargs):
        seen.append(frame)
        return {"matched": True, "resolved_box": {"x": 1, "y": 2, "w": 3, "h": 4}}

    monkeypatch.setattr(runner, "_match_shape", fake_match_shape)

    result = runtime.shape_matches(421, "气泡", frame_data_url="captured-frame")

    assert result is not None
    assert seen == ["captured-frame"]


def test_runtime_open_sdk_bubble_menu_never_retries_a_penetrating_click(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image421 = _image("气泡", "0421.png", [{"title": "气泡", "floating": True}])
    image590 = _image("37手游弹窗", "0590.png", [])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {421: image421, 590: image590}, "asset_tree": [image421, image590]},
        stop_event=threading.Event(),
    )
    calls = {"click": 0}
    safe_match = {
        "matched": True,
        "unique_match": True,
        "resolved_box": {"x": 17, "y": 650, "w": 57, "h": 67},
    }
    monkeypatch.setattr(
        runtime,
        "shape_matches",
        lambda _view, shape, **_kwargs: None if shape == "奖励浮层" else safe_match,
    )
    monkeypatch.setattr(runtime, "click_frame_point", lambda *_args, **_kwargs: calls.__setitem__("click", calls["click"] + 1))
    monkeypatch.setattr(runtime, "wait_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runtime, "wait_view", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("penetrated")))
    monkeypatch.setattr(runtime, "match_view", lambda *_args, **_kwargs: (False, 0.0, "frame"))

    with pytest.raises(TimeoutError, match="禁止原地重试"):
        _drain_generator(runtime.open_sdk_bubble_menu(timeout=9.0))

    assert calls == {"click": 1}


def test_runtime_wait_click_then_view_retries_by_default_when_source_remains(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image343 = _image(
        "占领确认",
        "0343.png",
        [{"title": "占领", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1, "sceneJumpTarget": "344"}],
    )
    image344 = _image("战斗确认", "0344.png", [])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {343: image343, 344: image344}, "asset_tree": [image343, image344]},
        stop_event=threading.Event(),
    )
    actions: list[tuple] = []
    wait_view_calls = {"count": 0}

    def fake_wait_click(frame, shape, **kwargs):
        actions.append(("wait_click", getattr(frame, "id", frame), getattr(shape, "title", shape), kwargs))
        if False:
            yield None

    def fake_wait_action_settle(seconds=1.0):
        actions.append(("settle", seconds))
        if False:
            yield None

    def fake_wait_view(*views, **kwargs):
        wait_view_calls["count"] += 1
        assert runtime.active_business_view_ids() == (344,)
        actions.append(("wait_view", views, kwargs))
        if False:
            yield None
        if wait_view_calls["count"] == 1:
            raise TimeoutError("首次点击未生效")
        return runtime.view(344)

    def fake_current_scene(*args, **kwargs):
        actions.append(("current_scene", args, kwargs))
        return 343, 100.0, "frame"

    monkeypatch.setattr(runtime, "wait_click", fake_wait_click)
    monkeypatch.setattr(runtime, "wait_action_settle", fake_wait_action_settle)
    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)
    monkeypatch.setattr(runtime, "current_scene", fake_current_scene)

    result = _drain_generator(runtime.wait_click_then_view(343, "占领", 344))

    assert result.id == 344
    assert "business_view_claims" not in runtime.attrs
    assert [action[0] for action in actions] == [
        "wait_click",
        "settle",
        "wait_view",
        "current_scene",
        "wait_click",
        "settle",
        "wait_view",
    ]


def test_runtime_business_view_claims_are_nested_and_exception_safe():
    runner = create_behavior_tree_runtime_runner()
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, {"images": {}, "asset_tree": []})

    with pytest.raises(RuntimeError, match="stop"):
        with runtime.expect_views(276, [277, 276]):
            assert runtime.active_business_view_ids() == (276, 277)
            with runtime.expect_views("#275"):
                assert runtime.active_business_view_ids() == (276, 277, 275)
            assert runtime.active_business_view_ids() == (276, 277)
            raise RuntimeError("stop")

    assert runtime.active_business_view_ids() == ()
    assert "business_view_claims" not in runtime.attrs


@pytest.mark.parametrize("first_scene", [344, None])
def test_daily_dongtian_accepts_confirm_overlay_or_direct_battle_transition(monkeypatch, first_scene):
    runner = create_behavior_tree_runtime_runner()
    actions: list[tuple] = []

    class FakeRuntime:
        def wait_click_then_view(self, source, shape, target, **kwargs):
            actions.append(("wait_click_then_view", source, shape, target, kwargs))
            if False:
                yield None
            return object()

        def wait_click(self, source, shape, **kwargs):
            actions.append(("wait_click", source, shape, kwargs))
            if False:
                yield None

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield None

        def current_scene(self, *_args, **_kwargs):
            actions.append(("current_scene",))
            return first_scene, 100.0 if first_scene else 0.0, "frame"

        def click_shape(self, view, shape, **kwargs):
            actions.append(("click_shape", view, shape, kwargs))

        def clear_frame(self):
            actions.append(("clear_frame",))

    def fake_finish(_runtime):
        actions.append(("finish_battle",))
        if False:
            yield None

    monkeypatch.setattr(runner, "_daily_dongtian_finish_battle", fake_finish)

    _drain_generator(runner._daily_dongtian_continue_enemy_occupation(FakeRuntime()))

    assert actions == [
        ("wait_click_then_view", 341, "位置1", 342, {}),
        ("wait_click_then_view", 342, "占领", 343, {}),
        ("wait_click", 343, "占领", {}),
        ("settle", 0.3),
        ("current_scene",),
        *(
            [("click_shape", 344, "战斗", {"frame_data_url": "frame"}), ("clear_frame",)]
            if first_scene == 344
            else []
        ),
        ("finish_battle",),
    ]


def test_runtime_wait_click_then_shape_retries_when_source_remains(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [{"title": "仙市", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1}])
    image247 = _image("仙市", "0247.png", [{"title": "秘藏阁", "x": 0.2, "y": 0.8, "w": 0.2, "h": 0.1}])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {34: image34, 247: image247}, "asset_tree": [image34, image247]},
        stop_event=threading.Event(),
    )
    actions: list[tuple] = []
    wait_shape_calls = {"count": 0}

    def fake_wait_click(frame, shape, **kwargs):
        actions.append(("wait_click", getattr(frame, "id", frame), getattr(shape, "title", shape), kwargs))
        if False:
            yield None
        return None

    def fake_wait_action_settle(seconds=1.0):
        actions.append(("settle", seconds))
        if False:
            yield None
        return None

    def fake_wait_shape(frame, shape, **kwargs):
        wait_shape_calls["count"] += 1
        actions.append(("wait_shape", getattr(frame, "id", frame), getattr(shape, "title", shape), kwargs))
        if False:
            yield None
        if wait_shape_calls["count"] == 1:
            raise TimeoutError("秘藏阁未出现")
        return "frame"

    def fake_current_scene(*args, **kwargs):
        actions.append(("current_scene", args, kwargs))
        return 34, 100.0, "frame"

    monkeypatch.setattr(runtime, "wait_click", fake_wait_click)
    monkeypatch.setattr(runtime, "wait_action_settle", fake_wait_action_settle)
    monkeypatch.setattr(runtime, "wait_shape", fake_wait_shape)
    monkeypatch.setattr(runtime, "current_scene", fake_current_scene)

    result = _drain_generator(
        runtime.wait_click_then_shape(
            34,
            "仙市",
            247,
            "秘藏阁",
            timeout=6.0,
            settle_seconds=2.0,
            retry_if_source_remains=True,
            max_clicks=2,
            label="等待仙市入口页",
        )
    )

    assert result == "frame"
    assert [action[0] for action in actions] == [
        "wait_click",
        "settle",
        "wait_shape",
        "current_scene",
        "wait_click",
        "settle",
        "wait_shape",
    ]


def test_runtime_wait_click_then_view_records_landing_frequency(monkeypatch, tmp_path):
    runner = create_behavior_tree_runtime_runner()
    baiye_shape = {"title": "拜谒", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1, "sceneJumpTarget": "264"}
    image69 = _image("日常", "0069.png", [baiye_shape])
    image264 = _image("拜谒", "0264.png", [])
    tree = [image69, image264]
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {69: image69, 264: image264}, "asset_tree": tree, "asset_tree_path": tmp_path / "asset-tree.json"},
        stop_event=threading.Event(),
    )
    writes: list[list[dict]] = []

    def fake_wait_click(_frame, _shape, **_kwargs):
        if False:
            yield None
        return None

    def fake_wait_action_settle(_seconds=1.0):
        if False:
            yield None
        return None

    def fake_wait_view(*_views, **_kwargs):
        if False:
            yield None
        return runtime.view(264)

    monkeypatch.setattr(runtime, "wait_click", fake_wait_click)
    monkeypatch.setattr(runtime, "wait_action_settle", fake_wait_action_settle)
    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)
    monkeypatch.setattr(
        behavior_tree_runtime_core,
        "update_data_annotation_asset_tree",
        lambda _path, _update: writes.append(tree)
        or SimpleNamespace(tree=tree, revision="test-revision"),
    )

    result = _drain_generator(runtime.wait_click_then_view(69, "拜谒", 264))

    assert result.id == 264
    assert baiye_shape["sceneJumpTarget"] == "264(1)"
    assert writes == [tree]


def test_runtime_wait_click_then_wait_view_records_declared_landing_once(monkeypatch, tmp_path):
    runner = create_behavior_tree_runtime_runner()
    jump_shape = {"title": "拜谒", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1, "sceneJumpTarget": "264"}
    image69 = _image("日常", "0069.png", [jump_shape])
    image264 = _image("拜谒", "0264.png", [])
    tree = [image69, image264]
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {
            "images": {69: image69, 264: image264},
            "asset_tree": tree,
            "asset_tree_path": tmp_path / "asset-tree.json",
        },
        stop_event=threading.Event(),
    )
    writes: list[list[dict]] = []
    scenes = iter([(69, 100.0), (264, 100.0), (264, 100.0)])

    monkeypatch.setattr(
        behavior_tree_runtime_core,
        "update_data_annotation_asset_tree",
        lambda _path, _update: writes.append(tree)
        or SimpleNamespace(tree=tree, revision="test-revision"),
    )
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: next(scenes))
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: None)

    _drain_generator(runtime.wait_click(69, "拜谒"))
    first = _drain_generator(runtime.wait_view(264))
    second = _drain_generator(runtime.wait_view(264))

    assert first.id == 264
    assert second.id == 264
    assert jump_shape["sceneJumpTarget"] == "264(1)"
    assert writes == [tree]


def test_runtime_wait_click_then_wait_view_does_not_create_missing_jump_target(monkeypatch, tmp_path):
    runner = create_behavior_tree_runtime_runner()
    plain_shape = {"title": "攻击", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1}
    image293 = _image("仙盟", "0293.png", [plain_shape])
    image294 = _image("确认", "0294.png", [])
    tree = [image293, image294]
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {
            "images": {293: image293, 294: image294},
            "asset_tree": tree,
            "asset_tree_path": tmp_path / "asset-tree.json",
        },
        stop_event=threading.Event(),
    )
    writes: list[list[dict]] = []
    scenes = iter([(293, 100.0), (294, 100.0)])

    monkeypatch.setattr(
        behavior_tree_runtime_core,
        "update_data_annotation_asset_tree",
        lambda _path, _update: writes.append(tree)
        or SimpleNamespace(tree=tree, revision="test-revision"),
    )
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: next(scenes))
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: None)

    _drain_generator(runtime.wait_click(293, "攻击"))
    result = _drain_generator(runtime.wait_view(294))

    assert result.id == 294
    assert "sceneJumpTarget" not in plain_shape
    assert writes == []


def test_runtime_scene_jump_target_adds_new_landing_and_sorts_counts_descending():
    runner = create_behavior_tree_runtime_runner()
    shape = {"title": "随机跳", "sceneJumpTarget": "277(3),85(2),186(5)"}

    assert runner._increment_scene_jump_target(shape, 1) is True

    assert shape["sceneJumpTarget"] == "186(5),277(3),85(2),1(1)"


def test_runtime_wait_click_then_view_accepts_target_list(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image293 = _image("仙盟", "0293.png", [{"title": "攻击", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1}])
    image294 = _image("确认", "0294.png", [])
    image295 = _image("备用确认", "0295.png", [])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {293: image293, 294: image294, 295: image295}, "asset_tree": [image293, image294, image295]},
        stop_event=threading.Event(),
    )
    actions: list[tuple] = []

    def fake_wait_click(frame, shape, **kwargs):
        actions.append(("wait_click", getattr(frame, "id", frame), getattr(shape, "title", shape), kwargs))
        if False:
            yield None
        return None

    def fake_wait_action_settle(seconds=1.0):
        actions.append(("settle", seconds))
        if False:
            yield None
        return None

    def fake_wait_view(*views, **kwargs):
        actions.append(("wait_view", views, kwargs))
        if False:
            yield None
        return runtime.view(294)

    monkeypatch.setattr(runtime, "wait_click", fake_wait_click)
    monkeypatch.setattr(runtime, "wait_action_settle", fake_wait_action_settle)
    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)

    result = _drain_generator(runtime.wait_click_then_view(293, "攻击", [294, 295]))

    assert result.id == 294
    assert actions == [
        ("wait_click", 293, "攻击", {}),
        ("settle", 1.0),
        ("wait_view", (294, 295), {"timeout": 12.0, "label": "点击后等待目标场景 #294,#295"}),
    ]


def test_runtime_wait_click_then_view_retries_once_when_declared_jump_stays_on_source(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image285 = _image(
        "造化灵脉",
        "0285.png",
        [{"title": "空位", "x": 0.7, "y": 0.5, "w": 0.2, "h": 0.1, "sceneJumpTarget": "286(3)"}],
    )
    image286 = _image("选择空位", "0286.png", [])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {285: image285, 286: image286}, "asset_tree": [image285, image286]},
        stop_event=threading.Event(),
    )
    actions: list[tuple] = []
    wait_calls = {"count": 0}

    def fake_wait_click(frame, shape, **kwargs):
        actions.append(("wait_click", getattr(frame, "id", frame), getattr(shape, "title", shape), kwargs))
        if False:
            yield None

    def fake_wait_action_settle(seconds=1.0):
        actions.append(("settle", seconds))
        if False:
            yield None

    def fake_wait_view(*views, **kwargs):
        wait_calls["count"] += 1
        actions.append(("wait_view", views, kwargs))
        if False:
            yield None
        if wait_calls["count"] == 1:
            raise TimeoutError("仍未到达 #286")
        return runtime.view(286)

    def fake_current_scene(*args, **kwargs):
        actions.append(("current_scene", args, kwargs))
        return 285, 100.0, "frame285"

    monkeypatch.setattr(runtime, "wait_click", fake_wait_click)
    monkeypatch.setattr(runtime, "wait_action_settle", fake_wait_action_settle)
    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)
    monkeypatch.setattr(runtime, "current_scene", fake_current_scene)

    result = _drain_generator(runtime.wait_click_then_view(285, "空位", 286, timeout=3.0))

    assert result.id == 286
    assert [action[0] for action in actions] == [
        "wait_click",
        "settle",
        "wait_view",
        "current_scene",
        "wait_click",
        "settle",
        "wait_view",
    ]
    assert actions[3] == (
        "current_scene",
        (),
        {"update": True, "handle_interruptions": True},
    )


def test_runtime_wait_click_then_view_does_not_retry_without_declared_jump(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image293 = _image("仙盟", "0293.png", [{"title": "攻击", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1}])
    image294 = _image("确认", "0294.png", [])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {293: image293, 294: image294}, "asset_tree": [image293, image294]},
        stop_event=threading.Event(),
    )
    clicks: list[tuple] = []

    def fake_wait_click(frame, shape, **kwargs):
        clicks.append((getattr(frame, "id", frame), getattr(shape, "title", shape), kwargs))
        if False:
            yield None

    def fake_wait_action_settle(_seconds=1.0):
        if False:
            yield None

    def fake_wait_view(*_views, **_kwargs):
        if False:
            yield None
        raise TimeoutError("未到达 #294")

    monkeypatch.setattr(runtime, "wait_click", fake_wait_click)
    monkeypatch.setattr(runtime, "wait_action_settle", fake_wait_action_settle)
    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)
    monkeypatch.setattr(runtime, "current_scene", lambda *_args, **_kwargs: pytest.fail("无跳转标记不得复核补点"))

    with pytest.raises(TimeoutError, match="已点击 1 次"):
        _drain_generator(runtime.wait_click_then_view(293, "攻击", 294, timeout=3.0))

    assert clicks == [(293, "攻击", {})]


def test_runtime_wait_click_then_view_can_wait_leave_without_target(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image293 = _image("仙盟", "0293.png", [{"title": "攻击", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1}])
    image294 = _image("确认", "0294.png", [])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {293: image293, 294: image294}, "asset_tree": [image293, image294]},
        stop_event=threading.Event(),
    )
    actions: list[tuple] = []
    scenes = iter([(293, 98.0, "frame-a"), (294, 99.0, "frame-b")])

    def fake_wait_click(frame, shape, **kwargs):
        actions.append(("wait_click", getattr(frame, "id", frame), getattr(shape, "title", shape), kwargs))
        if False:
            yield None
        return None

    def fake_wait_action_settle(seconds=1.0):
        actions.append(("settle", seconds))
        if False:
            yield None
        return None

    def fake_current_scene(*_args, **_kwargs):
        return next(scenes)

    monkeypatch.setattr(runtime, "wait_click", fake_wait_click)
    monkeypatch.setattr(runtime, "wait_action_settle", fake_wait_action_settle)
    monkeypatch.setattr(runtime, "current_scene", fake_current_scene)

    result = _drain_generator(runtime.wait_click_then_view(293, "攻击", wait_leave=True, timeout=3.0))

    assert result.id == 294
    assert actions == [
        ("wait_click", 293, "攻击", {}),
        ("settle", 1.0),
    ]


def test_runtime_wait_click_then_view_requires_target_or_jump_target():
    runner = create_behavior_tree_runtime_runner()
    image69 = _image("日常", "0069.png", [{"title": "无目标", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1}])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {69: image69}, "asset_tree": [image69]},
        stop_event=threading.Event(),
    )

    with pytest.raises(RuntimeError, match="缺少目标场景"):
        _drain_generator(runtime.wait_click_then_view(69, "无目标"))


def test_runtime_wait_click_then_view_timeout_reports_source_and_declared_target(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image69 = _image("日常", "0069.png", [{"title": "拜谒", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1, "sceneJumpTarget": "264"}])
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {69: image69}, "asset_tree": [image69]},
        stop_event=threading.Event(),
    )

    def fake_wait_click(*_args, **_kwargs):
        if False:
            yield None
        return None

    def fake_wait_action_settle(*_args, **_kwargs):
        if False:
            yield None
        return None

    def fake_wait_view(*_args, **_kwargs):
        if False:
            yield None
        raise TimeoutError("未检测到 #264，最后 unknown 0%")

    monkeypatch.setattr(runtime, "wait_click", fake_wait_click)
    monkeypatch.setattr(runtime, "wait_action_settle", fake_wait_action_settle)
    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)

    with pytest.raises(TimeoutError) as exc_info:
        _drain_generator(runtime.wait_click_then_view(69, "拜谒", timeout=3.0, retry_if_source_remains=False))

    message = str(exc_info.value)
    assert "源场景=#69" in message
    assert "shape=[拜谒]" in message
    assert "期望目标=#264" in message
    assert "sceneJumpTarget=264" in message
    assert "unknown 0%" in message


def test_runtime_ocr_matches_wraps_predicate(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, {"images": {}}, stop_event=threading.Event())
    monkeypatch.setattr(runtime, "ocr_text", lambda _frame: "修仙传 游历 人界")
    monkeypatch.setattr(runner, "_ocr_text", lambda lines: " ".join(str(line.get("text") or "") for line in lines))

    condition = runtime.ocr_matches(lambda text: "修仙传" in text and "游历" in text, label="游历首页 OCR")
    result = condition.check(runtime, "frame")

    assert result.matched is True
    assert "游历首页 OCR 命中" in result.detail


def test_runtime_wait_view_or_ocr_returns_branch_and_scene(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, {"images": {}}, stop_event=threading.Event())
    actions: list[tuple] = []

    def fake_view_visible(view, **kwargs):
        actions.append(("view_visible", view, kwargs))
        return ("view_visible", view)

    def fake_ocr_matches(predicate, **kwargs):
        actions.append(("ocr_matches", predicate("修仙传 游历"), kwargs))
        return ("ocr_matches", kwargs)

    def fake_wait_any(conditions, **kwargs):
        actions.append(("wait_any", conditions, kwargs))
        if False:
            yield None
        return "text"

    monkeypatch.setattr(runtime, "view_visible", fake_view_visible)
    monkeypatch.setattr(runtime, "ocr_matches", fake_ocr_matches)
    monkeypatch.setattr(runtime, "wait_any", fake_wait_any)
    monkeypatch.setattr(runtime, "current_scene", lambda views: (None, 0.0, "frame"))

    result = _drain_generator(runtime.wait_view_or_ocr(
        228,
        lambda text: "游历" in text,
        view_threshold=95.0,
        timeout=7,
        label="等待游历首页",
    ))

    assert result == ("text", 228, 0.0)
    assert actions == [
        ("view_visible", 228, {"threshold": 95.0}),
        ("ocr_matches", True, {"label": "等待游历首页 OCR"}),
        (
            "wait_any",
            {
                "scene": ("view_visible", 228),
                "text": ("ocr_matches", {"label": "等待游历首页 OCR"}),
            },
            {"timeout": 7, "label": "等待游历首页"},
        ),
    ]


def _build_service_client(session: Session) -> TestClient:
    app = FastAPI()
    app.include_router(fanxiu_api.service_router, prefix="/api/fanxiu")

    def override_get_session():
        yield session

    app.dependency_overrides[fanxiu_api.get_session] = override_get_session
    return TestClient(app)


def _build_service_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_runtime_cell_logs_prefers_persisted_cell_and_falls_back_to_behavior_tree_logs(monkeypatch):
    session = _build_service_session()
    app = FastAPI()
    app.include_router(fanxiu_api.router, prefix="/api/fanxiu")
    current_user = User(id=1, username="alice", hashed_password="x", is_active=True)

    def override_get_session():
        yield session

    app.dependency_overrides[fanxiu_api.get_session] = override_get_session
    app.dependency_overrides[fanxiu_api.get_current_active_user] = lambda: current_user
    monkeypatch.setattr(fanxiu_api, "ensure_feature_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fanxiu_api, "_sync_behavior_tree_runtime_runner_to_core", lambda: None)
    monkeypatch.setattr(fanxiu_api, "_behavior_tree_runtime_state_path", lambda: Path("runtime-state.json"))
    monkeypatch.setattr(
        fanxiu_api,
        "_read_behavior_tree_runtime_status",
        lambda: {
            "cell_logs": [
                {
                    "id": "cell-new",
                    "title": "任务 cell：detect_scene",
                    "source_kind": "command",
                    "source": json.dumps({"code": "run_task_cell('detect_scene', {})"}, ensure_ascii=False),
                    "started_at": "10:00:01",
                    "ended_at": "10:00:02",
                    "entries": [
                        {
                            "id": "runtime-1",
                            "time": "10:00:01",
                            "kind": "info",
                            "scope": "cell",
                            "item_id": "framework",
                            "message": "提交 cell",
                            "action": "",
                            "source_file": "",
                            "source_path": "",
                            "source_line": None,
                            "source_expr": "",
                            "ts": "1",
                        }
                    ],
                }
            ]
        },
    )
    monkeypatch.setattr(
        fanxiu_api,
        "_core_behavior_tree_runtime_logs",
        lambda **_kwargs: [
            {"time": "09:00:01", "kind": "info", "scope": "guard", "item_id": "popup", "message": "守护完成", "ts": "1"}
        ],
    )

    response = TestClient(app).get("/api/fanxiu/data-annotation/runtime/cell-logs?limit=2")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["cells"]) == 2
    assert payload["cells"][0]["id"] == "cell-new"
    assert payload["cells"][1]["id"].startswith("cell-")
    assert payload["cells"][0]["title"] == "任务 cell：detect_scene"
    assert payload["cells"][0]["source"] == "run_task_cell('detect_scene', {})"
    assert payload["cells"][0]["entries"][0]["message"] == "提交 cell"
    assert payload["cells"][1]["title"] == "守护 cell"
    assert payload["cells"][1]["source"].startswith("# 历史运行日志回放")


def test_runtime_status_can_skip_cell_logs(monkeypatch):
    monkeypatch.setattr(fanxiu_api, "ensure_feature_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        fanxiu_api,
        "_behavior_tree_runtime_status",
        lambda **_kwargs: {
            "ok": True,
            "status": "idle",
            "message": "ready",
            "cell_logs": [{"id": "cell-1", "title": "历史 cell", "entries": [{"message": "huge"}]}],
        },
    )
    current_user = User(id=1, username="tester", hashed_password="x", is_active=True)

    response = fanxiu_api.get_fanxiu_behavior_tree_runtime_status(
        entry_id="",
        include_cell_logs=False,
        current_user=current_user,
        session=None,
    )

    assert response.message == "ready"
    assert response.cell_logs == []


def test_runtime_status_can_skip_logs(monkeypatch):
    monkeypatch.setattr(fanxiu_api, "ensure_feature_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        fanxiu_api,
        "_behavior_tree_runtime_status",
        lambda **_kwargs: {
            "ok": True,
            "status": "idle",
            "message": "ready",
            "logs": [{"message": "huge"}],
        },
    )
    current_user = User(id=1, username="tester", hashed_password="x", is_active=True)

    response = fanxiu_api.get_fanxiu_behavior_tree_runtime_status(
        entry_id="",
        include_logs=False,
        current_user=current_user,
        session=None,
    )

    assert response.message == "ready"
    assert response.logs == []


def test_cross_process_runtime_status_prefers_newer_kernel_snapshot_without_writing(tmp_path, monkeypatch):
    from backend.core.fanxiu.behavior_tree import jupyter_kernel as jupyter_kernel_core

    runtime_state = tmp_path / "runtime_state.json"
    world_facts = tmp_path / "world_facts.json"
    stale_backend_status = {
        "ok": True,
        "running": False,
        "status": "success",
        "phase": "daily_redruntime_done",
        "task_type": "daily_redruntime",
        "current_task": "红包",
        "current_task_id": "daily_redruntime",
        "message": "红包完成",
        "logs": [{"kind": "success", "message": "红包完成"}],
        "cell_logs": [],
        "updated_at": 100.0,
    }
    kernel_status = {
        **stale_backend_status,
        "running": True,
        "status": "running",
        "phase": "mail_scan",
        "task_type": "mail_selective_claim",
        "current_task": "邮件",
        "current_task_id": "mail_selective_claim",
        "message": "正在扫描邮件",
        "logs": [{"kind": "action", "message": "正在扫描邮件"}],
        "updated_at": 200.0,
    }
    runtime_state.write_text(json.dumps(kernel_status, ensure_ascii=False), encoding="utf-8")

    class StaleBackendRunner:
        guard_definitions = {}

        def status(self, *, include_cell_logs=True):
            payload = json.loads(json.dumps(stale_backend_status, ensure_ascii=False))
            if not include_cell_logs:
                payload.pop("cell_logs", None)
            return payload

    monkeypatch.setattr(fanxiu_behavior_tree_core, "get_behavior_tree_runtime_runner", lambda: StaleBackendRunner())
    monkeypatch.setattr(behavior_tree_control_core, "behavior_tree_runtime_runner_status", lambda: dict(stale_backend_status))
    monkeypatch.setattr(
        jupyter_kernel_core,
        "fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "busy"},
    )

    before = runtime_state.read_text(encoding="utf-8")
    framework_status = fanxiu_behavior_tree_core.fanxiu_behavior_tree_runtime_status(
        runtime_state_path=runtime_state,
        world_facts_path=world_facts,
    )
    control_status = behavior_tree_control_core.behavior_tree_runtime_status(
        runtime_state_path=runtime_state,
        world_facts_path=world_facts,
    )

    assert framework_status["running"] is True
    assert framework_status["current_task_id"] == "mail_selective_claim"
    assert control_status["running"] is True
    assert control_status["current_task_id"] == "mail_selective_claim"
    assert runtime_state.read_text(encoding="utf-8") == before
    assert not world_facts.exists()


def test_behavior_tree_logs_use_newest_snapshot_without_querying_kernel(tmp_path, monkeypatch):
    from backend.core.fanxiu.behavior_tree import jupyter_kernel as jupyter_kernel_core

    runtime_state = tmp_path / "runtime_state.json"
    runtime_state.write_text(
        json.dumps(
            {
                "updated_at": 200.0,
                "logs": [{"kind": "info", "message": "持久化日志", "ts": "2"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class StaleBackendRunner:
        def status(self, *, include_cell_logs=True):
            return {
                "updated_at": 100.0,
                "logs": [{"kind": "info", "message": "旧内存日志", "ts": "1"}],
            }

    monkeypatch.setattr(fanxiu_behavior_tree_core, "get_behavior_tree_runtime_runner", lambda: StaleBackendRunner())
    monkeypatch.setattr(
        jupyter_kernel_core,
        "fanxiu_kernel_manager_status",
        lambda: pytest.fail("reading logs must not query kernel state"),
    )

    logs = fanxiu_behavior_tree_core.fanxiu_behavior_tree_runtime_logs(
        runtime_state_path=runtime_state,
        limit=20,
    )

    assert [item["message"] for item in logs] == ["持久化日志"]


def test_running_behavior_tree_logs_publish_throttled_progress_to_shared_state():
    runner = create_behavior_tree_runtime_runner()
    with runner._lock:
        runner._status.update({
            "running": True,
            "status": "running",
            "current_task_id": "mail_selective_claim",
            "updated_at": time.time(),
        })

    runner._log("action", "正在扫描邮件第 2 页")

    persisted = behavior_tree_control_core.read_behavior_tree_runtime_status()
    assert persisted["running"] is True
    assert persisted["current_task_id"] == "mail_selective_claim"
    assert persisted["logs"][-1]["message"] == "正在扫描邮件第 2 页"


def test_ocr_row_clicks_in_shape_uses_shape_center_x_and_filters_text():
    runner = create_behavior_tree_runtime_runner()
    image = _image(
        "报名页",
        "signup.png",
        [
            {"title": "报名列", "type": "rectangle", "x": 100 / 900, "y": 200 / 1600, "w": 300 / 900, "h": 500 / 1600},
        ],
    )
    lines = [
        {"text": "已报名", "x": 120, "y": 230, "w": 120, "h": 30},
        {"text": "报名", "x": 390, "y": 300, "w": 40, "h": 30},
        {"text": "报名", "x": 390, "y": 760, "w": 40, "h": 30},
        {"text": "立即报名", "x": 360, "y": 520, "w": 60, "h": 40},
    ]

    matches = runner._ocr_row_clicks_in_shape(lines, image, "报名列", include=("报名",), exclude=("已报名",))

    assert matches == [
        (250.0, 315.0, "报名"),
        (250.0, 540.0, "立即报名"),
    ]


def test_daily_dungeon_purchase_remaining_count_parses_ocr_variants():
    runner = create_behavior_tree_runtime_runner()

    assert runner._daily_dungeon_purchase_remaining_count("剩余限购次数：3") == 3
    assert runner._daily_dungeon_purchase_remaining_count("破界符 拥有：0/1 剩余限购次数: １") == 1
    assert runner._daily_dungeon_purchase_remaining_count("剩余限购次数：O 购买并使用") == 0
    assert runner._daily_dungeon_purchase_remaining_count("购买并使用 价格：100") is None


def test_daily_dungeon_recommend_completed_state_skips_purchase(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    ctx = {"asset_tree_path": Path("asset-tree.json"), "images": {}}
    stop_event = threading.Event()
    actions: list[tuple] = []

    class FakeRuntime:
        def wait_view(self, *views, **kwargs):
            actions.append(("wait_view", views, kwargs))
            if False:
                yield None
            return views[0]

        def ocr_text(self, frame_data_url=None, *, update: bool = False):
            actions.append(("ocr_text", update))
            return "当前模式：修罗 已完成所有挑战 今日可挑战次数：6/6 组队扫荡"

    def fake_recommend(*_args, **_kwargs):
        actions.append(("recommend",))
        if False:
            yield None
        return "success"

    def fake_cleanup(callback, **kwargs):
        actions.append(("cleanup", kwargs))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_click_daily_dungeon_recommend", fake_recommend)
    monkeypatch.setattr(runner, "_click_daily_dungeon_buy", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not buy")))
    monkeypatch.setattr(runner, "_record_daily_dungeon_done", lambda _payload, *, message: actions.append(("done", message)) or "2026-06-22 05:00:00")
    monkeypatch.setattr(runner, "_safe_daily_done_cleanup", fake_cleanup)

    result = _drain_generator(
        runner._click_daily_dungeon_recommend_and_buy(
            ctx,
            stop_event,
            {},
            {"id": 222},
            {"id": 223},
            {"id": 224},
            {"id": 225},
            task_label="日常_每日副本",
        )
    )

    assert result == "success"
    assert actions == [
        ("recommend",),
        ("wait_view", (223,), {"timeout": 18.0, "label": "日常_每日副本：等待副本挑战 #223"}),
        ("ocr_text", True),
        ("done", "副本挑战已完成"),
        ("cleanup", {"label": "日常_每日副本", "repeat_risk": "重复扫荡"}),
    ]


def test_daily_dungeon_three_of_three_completed_state_is_not_done():
    runner = create_behavior_tree_runtime_runner()

    assert runner._daily_dungeon_text_is_completed(
        "当前模式：修罗 已完成所有挑战 今日可挑战次数:0/3 组队扫荡"
    ) is False
    assert runner._daily_dungeon_text_is_completed(
        "当前模式：修罗 已完成所有挑战 今日可挑战次数:3/3 组队扫荡"
    ) is False
    assert runner._daily_dungeon_text_is_completed(
        "当前模式：修罗 已完成所有挑战 今日可挑战次数：6/6 组队扫荡"
    ) is True


def test_daily_dungeon_purchase_clicks_until_unavailable_without_waiting_view(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image224 = _image("购买破界符", "0224.png", [
        {"id": "use", "kind": "rect", "title": "购买并使用", "x": 0.35, "y": 0.75, "w": 0.3, "h": 0.06},
    ])
    image225 = _image("购买次数不足", "0225.png", [
        {"id": "blank", "kind": "rect", "title": "空白", "x": 0.05, "y": 0.9, "w": 0.2, "h": 0.06},
    ])
    ctx = {"entry": object(), "asset_tree_path": Path("asset-tree.json"), "images": {224: image224, 225: image225}}
    stop_event = threading.Event()
    clicks: list[str] = []
    closed: list[str] = []
    waited_views: list[int] = []

    class FakeRuntime:
        wait_any_calls = 0

        def cur_frame(self, update: bool = False):
            return "frame"

        def ocr_text(self, frame_data_url=None, *, update: bool = False):
            if len(clicks) >= 2:
                return "破界符 持有数量 0 每日限购 增加购买次数"
            return "破界符 拥有：0/1 剩余限购次数：1 价格：200 购买并使用"

        def wait_action_settle(self, seconds: float = 1.0):
            if False:
                yield None
            return None

        def wait_view(self, *views, **kwargs):
            waited_views.extend(int(view) for view in views)
            if False:
                yield None
            return views[0]

        def wait_any(self, *args, **kwargs):
            self.wait_any_calls += 1
            if False:
                yield None
            return "purchase"

    fake_runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: fake_runtime)

    def fake_click(_ctx, _stop_event, _image, shape, _payload, **_kwargs):
        clicks.append(str(shape.get("title") or ""))
        if False:
            yield None
        return True

    def fake_close(_ctx, _stop_event, _image):
        closed.append("空白")
        if False:
            yield None
        return True

    monkeypatch.setattr(runner, "_click_shape_respecting_conditions", fake_click)
    monkeypatch.setattr(runner, "_close_daily_dungeon_purchase_unavailable", fake_close)

    result = runner._run_direct_runtime_action(
        lambda: runner._click_daily_dungeon_purchase_uses(
            ctx,
            stop_event,
            {"max_purchase_uses": 3, "purchase_click_settle_seconds": 0.01},
            image224,
            image225,
            task_label="日常_每日副本",
        ),
        stop_event=stop_event,
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicks == ["购买并使用", "购买并使用"]
    assert closed == ["空白"]
    assert waited_views == [223]
    assert fake_runtime.wait_any_calls == 0


def test_daily_dungeon_wait_purchase_result_accepts_purchase_ocr_when_scene_unknown(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    ctx = {"asset_tree_path": Path("asset-tree.json")}
    stop_event = threading.Event()

    class FakeRuntime:
        def current_scene(self, candidates, *, update: bool = False):
            assert candidates == [224, 225, 223]
            return None, 0.0, "frame"

        def ocr_text(self, frame):
            assert frame == "frame"
            return "破界符 价格：100 拥有：255320 购买并使用"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: FakeRuntime())

    result = _drain_generator(
        runner._wait_daily_dungeon_purchase_result(
            ctx,
            stop_event,
            {"id": 223},
            {"id": 224},
            {"id": 225},
            timeout=1.0,
            label="日常_每日副本：等待购买结果",
        )
    )

    assert result == 224


def test_scene_route_click_falls_back_for_world_open_menu(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    shape = {"id": "open-menu", "kind": "rect", "title": "打开下方菜单", "x": 0.4, "y": 0.9, "w": 0.1, "h": 0.06}
    image34 = _image("世界", "0034.jpg", [shape])
    clicked: list[tuple[float, float]] = []

    def fake_click_shape(*_args, **_kwargs):
        raise RuntimeError("未能按图像定位浮动按钮「打开下方菜单」")

    monkeypatch.setattr(runner, "_click_shape", fake_click_shape)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))

    runner._click_scene_route_shape({"entry": object()}, image34, shape, "frame")

    assert clicked == [(405.0, 1488.0)]
    assert any("改按固定标注点击" in log["message"] for log in runner.status()["logs"])


def test_navigation_retry_jitter_radius_doubles_then_grows_by_at_most_fifty():
    runner = create_behavior_tree_runtime_runner()

    radii = [runner._navigation_retry_jitter_radius(count) for count in range(11)]

    assert radii == [0, 1, 2, 4, 8, 16, 32, 64, 114, 164, 214]


def test_navigation_retry_jitter_stays_inside_game_frame(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    monkeypatch.setattr(behavior_tree_runtime_core.random, "randint", lambda _low, high: high)

    point = runner._randomly_perturb_click_point(image34, 898.0, 1598.0, radius=10)

    assert point == (899.0, 1599.0)


def test_scene_route_click_forwards_retry_jitter_to_shape_click(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    shape = {
        "id": "continue",
        "kind": "rect",
        "title": "继续",
        "sceneJumpTarget": "482",
        "x": 0.4,
        "y": 0.8,
        "w": 0.2,
        "h": 0.1,
    }
    image413 = _image("修炼结算", "0413.png", [shape])
    radii: list[int] = []

    def click_shape(_ctx, _image, _shape, _frame, *, jitter_radius=0):
        radii.append(jitter_radius)

    monkeypatch.setattr(runner, "_click_shape", click_shape)

    runner._click_scene_route_shape(
        {"entry": object()},
        image413,
        shape,
        "frame",
        jitter_radius=8,
    )

    assert radii == [8]


def test_scene_route_click_falls_back_for_jump_target_shape(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    shape = {
        "id": "daily",
        "kind": "rect",
        "title": "日常",
        "sceneJumpTarget": "69(149)",
        "x": 0.03944444444444445,
        "y": 0.2070833333333333,
        "w": 0.08666666666666667,
        "h": 0.07708333333333334,
    }
    image34 = _image("世界", "0034.png", [shape])
    clicked: list[tuple[float, float]] = []

    def fake_click_shape(*_args, **_kwargs):
        raise RuntimeError("未能按 OCR 定位浮动按钮「日常」：目标 日|常")

    monkeypatch.setattr(runner, "_click_shape", fake_click_shape)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))

    runner._click_scene_route_shape({"entry": object()}, image34, shape, "frame")

    assert clicked == [(pytest.approx(74.5), pytest.approx(392.99999999999994))]


def test_scene_route_click_does_not_fall_back_for_one_key_claim(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    shape = {
        "id": "claim-all",
        "kind": "rect",
        "title": "一键领取",
        "sceneJumpTarget": "121",
        "x": 0.4,
        "y": 0.8,
        "w": 0.2,
        "h": 0.1,
    }
    image121 = _image("邮件", "0121.png", [shape])

    def fake_click_shape(*_args, **_kwargs):
        raise RuntimeError("未能按 OCR 定位浮动按钮「一键领取」")

    monkeypatch.setattr(runner, "_click_shape", fake_click_shape)
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args: pytest.fail("一键领取不能固定 fallback 点击"))

    with pytest.raises(RuntimeError, match="一键领取"):
        runner._click_scene_route_shape({"entry": object()}, image121, shape, "frame")


def test_click_frame_point_saves_action_trace_before_click(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}
    clicked: list[dict] = []

    def fake_temp_root(*parts, create=True):
        path = tmp_path.joinpath(*parts)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(behavior_tree_runtime_core, "codeyun_temp_root", fake_temp_root)
    monkeypatch.setattr(runner, "_capture_frame", lambda _ctx: _png_data_url())
    monkeypatch.setenv("CODEYUN_FANXIU_ACTION_TRACE_MAX_FILES", "10000")
    monkeypatch.setattr(behavior_tree_runtime_core, "_click_game_window2_service", lambda payload: clicked.append(payload) or {"ok": True})

    runner._click_frame_point(ctx, image34, 123.0, 456.0)

    trace_dir = tmp_path / "fanxiu_action_trace"
    before_files = list(trace_dir.glob("*_before.png"))
    marked_files = list(trace_dir.glob("*_marked.png"))
    index_path = trace_dir / "index.jsonl"
    assert clicked and clicked[0]["x"] == 123.0
    assert clicked[0]["input_backend"] == "adb"
    assert before_files
    assert marked_files
    record = json.loads(index_path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["kind"] == "click"
    assert record["action"]["point"] == [123.0, 456.0]
    assert Path(record["before"]).is_file()
    assert Path(record["marked"]).is_file()


def test_runtime_local_click_overrides_action_planner_input_backend(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}
    clicked: list[dict] = []

    class FakeActionPlanner:
        def click_point_payload(self, _image, x, y):
            return {"x": x, "y": y, "input_backend": "desktop"}

    monkeypatch.setattr(behavior_tree_runtime_core, "ActionPlanner", FakeActionPlanner)
    monkeypatch.setattr(runner, "_save_action_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_runtime_core, "_click_game_window2_service", lambda payload: clicked.append(payload) or {"ok": True})

    runner._click_frame_point(ctx, image34, 123.0, 456.0)

    assert clicked and clicked[0]["input_backend"] == "adb"


def test_runtime_local_shape_click_overrides_action_planner_input_backend(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    shape = {"id": "main", "kind": "rect", "title": "主线", "x": 0.4, "y": 0.5, "w": 0.2, "h": 0.1}
    image34 = _image("世界", "0034.png", [shape])
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}
    clicked: list[dict] = []

    class FakeActionPlanner:
        def shape_center(self, _image, _shape):
            return (450.0, 880.0)

        def click_shape_payload(self, _image, _shape):
            return {"x": 450.0, "y": 880.0, "input_backend": "desktop"}

    monkeypatch.setattr(behavior_tree_runtime_core, "ActionPlanner", FakeActionPlanner)
    monkeypatch.setattr(runner, "_save_action_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_runtime_core, "_click_game_window2_service", lambda payload: clicked.append(payload) or {"ok": True})

    runner._click_shape(ctx, image34, shape)

    assert clicked and clicked[0]["input_backend"] == "adb"


def test_runtime_shape_click_uses_parent_shape_center_when_ocr_resolves_sub_box(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    shape = {
        "id": "daily",
        "kind": "rect",
        "title": "日常",
        "x": 0.4,
        "y": 0.2,
        "w": 0.2,
        "h": 0.1,
        "ocrMatchRole": "required",
        "ocrText": "日|常",
    }
    image34 = _image("世界", "0034.png", [shape])
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}
    clicked: list[dict] = []
    match_result = {
        "matched": True,
        "similarity": 100,
        "ocr_text": "日常",
        "fixed_box": {"x": 100, "y": 1200, "w": 80, "h": 36},
        "resolved_box": {"x": 110, "y": 1204, "w": 60, "h": 32},
    }

    monkeypatch.setattr(runner, "_save_action_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_runtime_core, "_click_game_window2_service", lambda payload: clicked.append(payload) or {"ok": True})

    runner._click_shape(ctx, image34, shape, frame_data_url="frame", match_result=match_result)

    assert clicked and clicked[0]["x"] == 450.0
    assert clicked[0]["y"] == 400.0
    assert any("ocr=日常" in log["message"] and "click=(450.0,400.0)" in log["message"] for log in runner.status()["logs"])


def test_runtime_local_drag_overrides_action_planner_input_backend(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}
    dragged: list[dict] = []

    class FakeActionPlanner:
        def drag_point_payload(self, _image, start_x, start_y, end_x, end_y, *, duration_ms=300):
            return {
                "start_x": start_x,
                "start_y": start_y,
                "end_x": end_x,
                "end_y": end_y,
                "duration_ms": duration_ms,
                "input_backend": "desktop",
            }

    monkeypatch.setattr(behavior_tree_runtime_core, "ActionPlanner", FakeActionPlanner)
    monkeypatch.setattr(runner, "_save_action_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_runtime_core, "_drag_game_window2_service", lambda payload: dragged.append(payload) or {"ok": True})

    runner._drag_frame_point(ctx, image34, 100.0, 900.0, 100.0, 400.0, duration_ms=600)

    assert dragged and dragged[0]["input_backend"] == "adb"
    assert dragged[0]["duration_ms"] == 600


def test_runtime_drag_shape_to_shape_uses_runtime_drag(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    icon = {"id": "icon", "kind": "rect", "title": "图标", "x": 0.1, "y": 0.2, "w": 0.1, "h": 0.1}
    target = {"id": "target", "kind": "rect", "title": "隐藏区", "x": 0.8, "y": 0.7, "w": 0.1, "h": 0.1}
    image58 = _image("隐藏浮动窗", "0058.png", [icon, target])
    ctx = {"entry": type("Entry", (), {"mode": "local"})(), "images": {58: image58}}
    dragged: list[tuple[float, float, float, float, int]] = []

    monkeypatch.setattr(
        runner,
        "_drag_frame_point",
        lambda _ctx, _image, sx, sy, ex, ey, duration_ms=300: dragged.append((sx, sy, ex, ey, duration_ms)),
    )

    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx)
    runtime.drag_shape_to_shape(58, "图标", "隐藏区", duration=0.35, frame_data_url=_png_data_url())

    assert dragged == [(135.0, 400.0, 765.0, 1200.0, 350)]


def test_runtime_drag_floating_shape_uses_resolved_current_position(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    icon = {
        "id": "icon",
        "kind": "rect",
        "title": "气泡",
        "x": 0.02,
        "y": 0.63,
        "w": 0.06,
        "h": 0.04,
        "floating": True,
        "imageMatchRole": "required",
    }
    target = {"id": "target", "kind": "rect", "title": "拖拽隐藏", "x": 0.2, "y": 0.85, "w": 0.6, "h": 0.13}
    image421 = _image("气泡", "0421.png", [icon, target])
    ctx = {"entry": type("Entry", (), {"mode": "local"})(), "images": {421: image421}}
    dragged: list[tuple[float, float, float, float, int]] = []

    monkeypatch.setattr(
        runner,
        "_match_shape",
        lambda *_args, **_kwargs: {
            "matched": True,
            "box": {"x": 18.0, "y": 1008.0, "w": 54.0, "h": 64.0},
            "resolved_box": {"x": 18.0, "y": 760.0, "w": 54.0, "h": 64.0},
        },
    )
    monkeypatch.setattr(
        runner,
        "_drag_frame_point",
        lambda _ctx, _image, sx, sy, ex, ey, duration_ms=300: dragged.append((sx, sy, ex, ey, duration_ms)),
    )

    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx)
    runtime.drag_shape_to_shape(421, "气泡", "拖拽隐藏", duration=0.35, frame_data_url=_png_data_url())

    assert dragged == [(45.0, 792.0, 450.0, 1464.0, 350)]


def test_runtime_remote_click_uses_remote_service(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    entry = type("Entry", (), {"mode": "remote"})()
    ctx = {"entry": entry}
    remote_clicks: list[dict] = []

    monkeypatch.setattr(behavior_tree_runtime_core, "_click_remote_game_window2", lambda _entry, payload: remote_clicks.append(payload) or {"ok": True})
    monkeypatch.setattr(behavior_tree_runtime_core, "_click_game_window2_service", lambda _payload: pytest.fail("remote click should not call local window service"))

    runner._click_frame_point(ctx, image34, 123.0, 456.0)

    assert remote_clicks and remote_clicks[0]["x"] == 123.0


def test_scene_route_click_falls_back_for_daily_exit(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    shape = {"id": "exit", "kind": "rect", "title": "退出", "x": 0.05, "y": 0.9364583333333333, "w": 0.07407407407407407, "h": 0.03645833333333337}
    image69 = _image("日常", "0069.png", [shape])
    clicked: list[tuple[float, float]] = []

    def fake_click_shape(*_args, **_kwargs):
        raise RuntimeError("未能按图像定位浮动按钮「退出」")

    monkeypatch.setattr(runner, "_click_shape", fake_click_shape)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))

    runner._click_scene_route_shape({"entry": object()}, image69, shape, "frame")

    assert clicked == [(pytest.approx(78.33333333333334), 1527.5)]
    assert any("#69「退出」图像定位失败，改按固定标注点击" in log["message"] for log in runner.status()["logs"])


def test_scene_route_click_keeps_strict_matching_for_other_shapes(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    shape = {"id": "mail", "kind": "rect", "title": "邮件", "x": 0.4, "y": 0.9, "w": 0.1, "h": 0.06}
    image35 = _image("世界下方菜单", "0035.png", [shape])
    clicked: list[tuple[float, float]] = []

    def fake_click_shape(*_args, **_kwargs):
        raise RuntimeError("未能按图像定位按钮「邮件」")

    monkeypatch.setattr(runner, "_click_shape", fake_click_shape)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))

    try:
        runner._click_scene_route_shape({"entry": object()}, image35, shape, "frame")
    except RuntimeError as exc:
        assert "邮件" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert clicked == []


def test_scene_score_uses_discriminator_shapes_with_same_box(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    world_identity = {
        "id": "world",
        "kind": "rect",
        "title": "大地图",
        "isSceneIdentity": True,
        "sceneIdentityRole": "required",
        "imageMatchRole": "required",
        "x": 0.1,
        "y": 0.1,
        "w": 0.1,
        "h": 0.1,
    }
    open_menu = {
        "id": "open-menu",
        "kind": "rect",
        "title": "打开下方菜单",
        "discriminatorEnabled": True,
        "discriminatorGroupId": "group-a",
        "x": 0.89,
        "y": 0.91,
        "w": 0.06,
        "h": 0.03,
    }
    close_menu = {
        "id": "close-menu",
        "kind": "rect",
        "title": "关闭下方菜单",
        "isSceneIdentity": True,
        "sceneIdentityRole": "required",
        "imageMatchRole": "required",
        "discriminatorEnabled": True,
        "discriminatorGroupId": "group-b",
        "x": 0.89,
        "y": 0.91,
        "w": 0.06,
        "h": 0.03,
    }
    image34 = _image("世界", "0034.jpg", [world_identity, open_menu])
    image35 = _image("世界下方菜单", "0035.png", [close_menu])
    ctx = {"images": {34: image34, 35: image35}}
    scores = {
        "大地图": 82.0,
        "打开下方菜单": 96.0,
        "关闭下方菜单": 88.0,
    }

    monkeypatch.setattr(runner, "_shape_score", lambda _ctx, _image, shape, _frame, **_kwargs: scores[str(shape.get("title"))])

    assert runner._scene_score(ctx, image34, "frame") == 96.0
    assert runner._scene_score(ctx, image35, "frame") == 0.0
    assert any("场景区分：#35 被 #34" in log["message"] for log in runner.status()["logs"])


def test_scene_route_candidates_use_full_frame_similarity_when_graph_ties(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image34 = _scene_image("世界", "0034.jpg", [
        {"id": "world-id", "kind": "rect", "title": "世界标识", "isSceneIdentity": True},
        {"id": "open", "kind": "rect", "title": "打开下方菜单", "sceneJumpTarget": "35"}
    ])
    image35 = _scene_image("世界下方菜单", "0035.png", [
        {"id": "menu-id", "kind": "rect", "title": "菜单标识", "isSceneIdentity": True},
        {"id": "mail", "kind": "rect", "title": "邮件", "sceneJumpTarget": "121"}
    ])
    image121 = _scene_image(
        "邮件",
        "0121.png",
        [{"id": "mail-id", "kind": "rect", "title": "邮件标识", "isSceneIdentity": True}],
    )
    tree = [image34, image35, image121]
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": {34: image34, 35: image35, 121: image121}}

    frame_data_url = _png_data_url()
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: frame_data_url)
    monkeypatch.setattr(
        runner,
        "_scene_score",
        lambda _ctx, image, _frame: 100.0 if image.get("filename") in {"0034.jpg", "0035.png"} else 0.0,
    )
    monkeypatch.setattr(
        runner,
        "_scene_reference_similarity",
        lambda _ctx, image, _frame: 99.0 if image.get("filename") == "0035.png" else 80.0,
    )

    candidates = runner._scene_route_candidate_ids(tree, 121)

    assert candidates[:3] == [121, 35, 34]
    assert runner._identify_scene_number(ctx, "frame", candidates) == (35, 100.0)


def test_route_candidate_prefers_direct_world_over_false_high_score_selection(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _scene_image("世界", "0034.png", [
        {"id": "world-identity", "kind": "rect", "title": "世界身份", "x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1, "isSceneIdentity": True},
        {"id": "daily", "kind": "rect", "title": "日常", "sceneJumpTarget": "69"},
    ], layer=1)
    image69 = _scene_image("日常", "0069.png", [], layer=1)
    image18 = _scene_image("游戏封面", "0018.png", [
        {"id": "cover-identity", "kind": "rect", "title": "封面身份", "x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1, "isSceneIdentity": True},
        {"id": "back", "kind": "rect", "title": "返回", "sceneJumpTarget": "20,34"},
    ], layer=1)
    image20 = _scene_image("绿瓶", "0020.png", [
        {"id": "world", "kind": "rect", "title": "回到世界", "sceneJumpTarget": "34"},
    ], layer=1)
    tree = [image20, image34, image69, image18]
    ctx = {"images": {18: image18, 20: image20, 34: image34, 69: image69}}
    scores = {
        18: 100.0,
        20: 0.0,
        34: 80.0,
        69: 0.0,
    }
    monkeypatch.setattr(runner, "_scene_score", lambda _ctx, image, _frame: scores[runner._image_number(image)])

    scene_id, score = runner._identify_scene_number_for_route(ctx, "frame", tree, 69, [69, 34, 18, 20])

    assert (scene_id, score) == (34, 80.0)


def test_scene_jump_to_world_stops_on_unified_scene_identity_before_unknown_recovery(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    back_shape = {"id": "back", "kind": "rect", "title": "返回", "sceneJumpTarget": "34"}
    image319 = _scene_image("奇袭魔界", "0319.png", [back_shape])
    image34 = _scene_image("世界", "0034.png", [])
    tree = [image319, image34]
    path = tmp_path / "asset-tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {"asset_tree": tree, "asset_tree_path": path, "images": {319: image319, 34: image34}}

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "world-frame")
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda *_args, **_kwargs: (34, 100.0),
    )
    result = _drain_generator(runner._wait_scene_jump_result(
        ctx,
        path,
        tree,
        source_scene_id=319,
        target_scene_id=34,
        edge={"source_id": 319, "image": image319, "shape": back_shape, "target_ids": [34]},
        stop_event=threading.Event(),
    ))

    assert result == 34


def test_go_scene_to_world_rechecks_fresh_frame_before_clicking_false_scene(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    exit_shape = {"id": "exit", "kind": "rect", "title": "退出", "sceneJumpTarget": "34"}
    image69 = _scene_image("日常", "0069.png", [exit_shape])
    image34 = _scene_image("世界", "0034.png", [])
    tree = [image69, image34]
    path = tmp_path / "asset-tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {"asset_tree": tree, "asset_tree_path": path, "images": {34: image34, 69: image69}}
    frames = ["false-daily-frame", "fresh-world-frame"]

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: frames.pop(0) if len(frames) > 1 else frames[0])
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, frame: (34, 100.0) if frame == "fresh-world-frame" else (69, 100.0),
    )
    monkeypatch.setattr(
        runner,
        "_click_scene_route_shape",
        lambda *_args, **_kwargs: pytest.fail("新帧已确认世界，不得继续点击错误 #69[退出]"),
    )

    result = _drain_generator(runner._go_scene_task(ctx, path, 34, threading.Event()))

    assert result == "success"


def test_compare_frame_crops_resizes_current_crop_without_mask():
    reference = np.full((45, 48, 3), 120, dtype=np.uint8)
    current = np.full((36, 38, 3), 120, dtype=np.uint8)

    similarity, score = _compare_frame_crops(reference, current, pixel_tolerance=0)

    assert similarity == 100
    assert score == 1.0


def test_mumu_adb_serial_candidates_use_local_ports_without_proxy_devices_by_default(monkeypatch):
    for key in mumu_control.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(mumu_control.MUMU_ADB_ALLOW_PROXY_DEVICES_ENV, raising=False)
    mumu_control._MUMU_ADB_SESSION.clear()
    monkeypatch.setattr(mumu_control.fanxiu_adb_device_service, "devices", lambda: ["192.168.31.181:5555"])
    monkeypatch.setattr(mumu_control, "_mumu_manager_adb_serial_candidates", lambda: [])

    assert mumu_control._mumu_adb_serial_candidates() == [
        "127.0.0.1:7555",
        "127.0.0.1:16416",
        "127.0.0.1:5555",
    ]


def test_mumu_adb_serial_candidates_allow_proxy_devices_when_explicit(monkeypatch):
    for key in mumu_control.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(mumu_control.MUMU_ADB_ALLOW_PROXY_DEVICES_ENV, "1")
    mumu_control._MUMU_ADB_SESSION.clear()
    monkeypatch.setattr(mumu_control.fanxiu_adb_device_service, "devices", lambda: ["192.168.31.181:5555"])
    monkeypatch.setattr(mumu_control, "_mumu_manager_adb_serial_candidates", lambda: [])

    assert mumu_control._mumu_adb_serial_candidates() == [
        "127.0.0.1:7555",
        "127.0.0.1:16416",
        "127.0.0.1:5555",
        "192.168.31.181:5555",
    ]


def test_mumu_adb_serial_candidates_keep_default_ports_only_without_devices(monkeypatch):
    for key in mumu_control.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(mumu_control.MUMU_ADB_ALLOW_PROXY_DEVICES_ENV, raising=False)
    mumu_control._MUMU_ADB_SESSION.clear()
    monkeypatch.setattr(mumu_control.fanxiu_adb_device_service, "devices", lambda: [])
    monkeypatch.setattr(mumu_control, "_mumu_manager_adb_serial_candidates", lambda: [])

    assert mumu_control._mumu_adb_serial_candidates() == [
        "127.0.0.1:7555",
        "127.0.0.1:16416",
        "127.0.0.1:5555",
    ]


def test_mumu_adb_serial_candidates_include_mumu_manager_devices_by_default(monkeypatch):
    for key in mumu_control.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(mumu_control.MUMU_ADB_ALLOW_PROXY_DEVICES_ENV, raising=False)
    mumu_control._MUMU_ADB_SESSION.clear()
    monkeypatch.setattr(mumu_control.fanxiu_adb_device_service, "devices", lambda: [])
    monkeypatch.setattr(mumu_control, "_mumu_manager_adb_serial_candidates", lambda: ["192.168.31.181:5555"])

    assert mumu_control._mumu_adb_serial_candidates() == [
        "127.0.0.1:7555",
        "127.0.0.1:16416",
        "127.0.0.1:5555",
        "192.168.31.181:5555",
    ]


def test_mumu_adb_input_uses_adb_cli_with_online_serial(monkeypatch):
    for key in mumu_control.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    mumu_control._MUMU_ADB_SESSION.clear()
    calls: list[tuple[tuple[str, ...], int | float | None]] = []

    monkeypatch.setattr(mumu_control, "_ensure_mumu_adb_port_available", lambda: None)
    monkeypatch.setenv("FANXIU_MUMU_ADB_SERIAL", "192.168.31.181:5555")
    monkeypatch.setattr(mumu_control.fanxiu_adb_device_service, "devices", lambda: [])
    monkeypatch.setattr(mumu_control.fanxiu_adb_device_service, "adb_path", lambda: Path("D:/adb.exe"))

    def fake_run(command, **kwargs):
        calls.append((tuple(str(item) for item in command), kwargs.get("timeout")))
        if len(command) >= 2 and command[1] == "connect":
            return mumu_control.subprocess.CompletedProcess(command, 0, "connected\n", "")
        if command[-1] == "wm size":
            return mumu_control.subprocess.CompletedProcess(command, 0, "Physical size: 900x1600\n", "")
        if command[-1] == "input tap 77 396":
            return mumu_control.subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"unexpected adb command: {command}")

    monkeypatch.setattr(mumu_control.subprocess, "run", fake_run)

    result = mumu_control._run_mumu_adb_input("input tap 77 396", timeout_s=7)

    assert result["input"] == "adb-cli"
    assert result["adb_serial"] == "192.168.31.181:5555"
    assert result["adb_size"] == "Physical size: 900x1600"
    adb_path = str(Path("D:/adb.exe"))
    assert calls == [
        ((adb_path, "connect", "192.168.31.181:5555"), 3),
        ((adb_path, "-s", "192.168.31.181:5555", "shell", "wm size"), 5),
        ((adb_path, "-s", "192.168.31.181:5555", "shell", "input tap 77 396"), 7),
    ]


def test_mumu_adb_input_reports_failed_process_output(monkeypatch):
    for key in mumu_control.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    mumu_control._MUMU_ADB_SESSION.clear()

    monkeypatch.setattr(mumu_control, "_ensure_mumu_adb_port_available", lambda: None)
    monkeypatch.setenv("FANXIU_MUMU_ADB_SERIAL", "192.168.31.181:5555")
    monkeypatch.setattr(mumu_control.fanxiu_adb_device_service, "adb_path", lambda: Path("D:/adb.exe"))

    def fake_run(command, **_kwargs):
        if command[-1] == "wm size":
            return mumu_control.subprocess.CompletedProcess(command, 0, "Physical size: 900x1600\n", "")
        return mumu_control.subprocess.CompletedProcess(command, 1, "", "input failed\n")

    monkeypatch.setattr(mumu_control.subprocess, "run", fake_run)

    try:
        mumu_control._run_mumu_adb_input("input swipe 1 2 3 4 1000", timeout_s=7)
    except RuntimeError as exc:
        assert "input failed" in str(exc)
    else:
        raise AssertionError("expected adb failure")


def test_click_shape_uses_shape_center_after_ocr_match(monkeypatch):
    monkeypatch.setenv("CODEYUN_FANXIU_ACTION_TRACE", "0")
    runner = create_behavior_tree_runtime_runner()
    image = _image("世界下方菜单", "0035.png")
    shape = {
        "id": "mail",
        "kind": "rect",
        "title": "邮件",
        "floating": True,
        "ocrText": "邮件",
        "ocrMatchRole": "required",
        "x": 0.5,
        "y": 0.9,
        "w": 0.1,
        "h": 0.06,
    }
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}
    clicked: list[dict] = []

    monkeypatch.setattr(
        runner,
        "_ocr_frame",
        lambda _frame, **_kwargs: {"tokens": _ocr_tokens("邮件", x=450, y=1440, w=90, h=96)},
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.behavior_tree_runtime._click_game_window2_service",
        lambda payload: clicked.append(payload) or {"ok": True},
    )

    runner._click_shape(ctx, image, shape, "frame")

    assert clicked[0]["x"] == 495.0
    assert clicked[0]["y"] == 1488.0
    assert clicked[0]["input_backend"] == "adb"


@pytest.mark.parametrize(
    ("counter_text", "message"),
    [
        ("剩余次数：0/2", "分子为0"),
        ("剩余次数：0", "完整分子/分母"),
        ("剩余次数：3/2", "数值异常"),
    ],
)
def test_xuanhuang_forward_click_guard_refuses_without_positive_fraction(monkeypatch, counter_text, message):
    runner = create_behavior_tree_runtime_runner()
    image = _image("玄荒挑战", "0418.png")
    shape = {"id": "forward", "title": "前往"}
    ctx: dict = {}

    monkeypatch.setattr(runner, "_clear_tick_frame", lambda _ctx: None)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "fresh-frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (418, 100.0))
    monkeypatch.setattr(runner, "_scene_matches_id", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        runner,
        "_ocr_fragments_in_shapes",
        lambda *_args, **_kwargs: [{"text": counter_text}],
    )

    with pytest.raises(RuntimeError, match=message):
        runner._guard_xuanhuang_forward_click(ctx, image, shape, "stale-frame")


def test_xuanhuang_forward_click_guard_allows_positive_fraction_with_bad_separator(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("玄荒挑战", "0418.png")
    shape = {"id": "forward", "title": "前往"}
    ctx: dict = {}

    monkeypatch.setattr(runner, "_clear_tick_frame", lambda _ctx: None)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "fresh-frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (418, 100.0))
    monkeypatch.setattr(runner, "_scene_matches_id", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        runner,
        "_ocr_fragments_in_shapes",
        lambda *_args, **_kwargs: [{"text": "剩余次数：1丨2"}],
    )

    assert runner._guard_xuanhuang_forward_click(ctx, image, shape, "stale-frame") == "fresh-frame"


def test_shape_ocr_reuses_existing_full_frame_cache(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("世界下方菜单", "0035.png")
    shape = {
        "id": "mail",
        "kind": "rect",
        "title": "邮件",
        "imageMatchRole": "off",
        "ocrText": "邮件",
        "ocrMatchRole": "required",
        "x": 0.1,
        "y": 0.1,
        "w": 0.2,
        "h": 0.1,
    }
    ctx = {
        "entry": type("Entry", (), {"mode": "local"})(),
            "_ocr_tokens_cache": {
                "frame": "frame",
                "version": 4,
                "options_key": '{"return_word_box": true}',
                "lines": [{"line_id": "line-0", "order": 0, "text": "邮件", "x": 100, "y": 170, "w": 80, "h": 30, "source": "paddle"}],
                "tokens": _ocr_tokens("邮件", x=100, y=170, w=80, h=30),
            },
    }

    monkeypatch.setattr(runner, "_run_match", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should reuse cached OCR")))
    monkeypatch.setattr(runner, "_ocr_fragments", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not run full-frame OCR")))

    result = runner._match_shape(ctx, image, shape, "frame", condition="ocr")

    assert result["matched"] is True
    assert result["similarity"] == 100
    assert result["ocr_text"] == "邮件"
    assert result["reason"] == "cached_frame_ocr"


def test_shape_ocr_cache_miss_does_not_repeat_ocr(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("世界下方菜单", "0035.png")
    shape = {
        "id": "mail",
        "kind": "rect",
        "title": "邮件",
        "imageMatchRole": "off",
        "ocrText": "邮件",
        "ocrMatchRole": "required",
        "x": 0.1,
        "y": 0.1,
        "w": 0.2,
        "h": 0.1,
    }
    ctx = {
        "entry": type("Entry", (), {"mode": "local"})(),
        "_ocr_tokens_cache": {
            "frame": "frame",
            "version": 3,
            "options_key": '{"return_word_box": true}',
            "tokens": _ocr_tokens("邮件", x=700, y=1400, w=80, h=30),
        },
    }

    monkeypatch.setattr(runner, "_run_match", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache is decisive for this frame")))
    monkeypatch.setattr(runner, "_ocr_fragments", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not run full-frame OCR")))

    result = runner._match_shape(ctx, image, shape, "frame", condition="ocr")

    assert result["matched"] is False
    assert result["similarity"] == 0
    assert result["matches"] == []
    assert result["reason"] == "cached_frame_ocr"


def test_shape_ocr_cache_miss_is_decisive_without_repeating_crop_ocr(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("某区域内部", "0085.png")
    shape = {
        "id": "leave",
        "kind": "rect",
        "title": "离开",
        "imageMatchRole": "optional",
        "ocrText": "离|开",
        "ocrMatchMode": "regex",
        "ocrMatchRole": "optional",
        "x": 0.85,
        "y": 0.48,
        "w": 0.1,
        "h": 0.05,
    }
    ctx = {
        "entry": type("Entry", (), {"mode": "local"})(),
        "_ocr_tokens_cache": {
            "frame": "frame",
            "version": 3,
            "options_key": '{"return_word_box": true}',
            "tokens": _ocr_tokens("其它文字", x=100, y=170, w=80, h=30),
        },
    }
    calls: list[dict] = []

    def fake_run_match(*_args, **kwargs):
        calls.append(kwargs)
        return {
            "similarity": 0,
            "matches": [{"ocr_text": "+离开", "x": 770, "y": 768, "w": 100, "h": 80}],
            "fixed_box": {"x": 770, "y": 768, "w": 100, "h": 80},
        }

    monkeypatch.setattr(runner, "_run_match", fake_run_match)

    result = runner._match_shape(ctx, image, shape, "frame", condition="ocr")

    assert calls == []
    assert result["matched"] is False
    assert result["ocr_text"] == ""


def test_click_floating_required_ocr_shape_does_not_fallback_to_raw_box(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("世界下方菜单", "0035.png")
    shape = {
        "id": "mail",
        "kind": "rect",
        "title": "邮件",
        "floating": True,
        "imageMatchRole": "off",
        "ocrText": "邮件",
        "ocrMatchRole": "required",
        "x": 0.5,
        "y": 0.9,
        "w": 0.1,
        "h": 0.06,
    }
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}
    clicked: list[dict] = []

    monkeypatch.setattr(
        runner,
        "_run_match",
        lambda *_args, **_kwargs: {"similarity": 0, "matches": [], "fixed_box": {"x": 450, "y": 1440, "w": 80, "h": 40}},
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.behavior_tree_runtime._click_game_window2_service",
        lambda payload: clicked.append(payload) or {"ok": True},
    )

    try:
        runner._click_shape(ctx, image, shape, "frame")
    except RuntimeError as exc:
        assert "未能按 OCR 定位浮动按钮" in str(exc)
    else:
        raise AssertionError("expected required OCR miss to abort click")
    assert clicked == []


def test_floating_ocr_shape_scans_full_frame_and_resolves_live_token_box():
    runner = create_behavior_tree_runtime_runner()
    image = _image("气泡菜单", "0590.png")
    shape = {
        "id": "voucher",
        "title": "代金券",
        "floating": True,
        "imageMatchRole": "off",
        "ocrText": "代金券",
        "ocrMatchRole": "required",
        "ocrMatchMode": "contains",
        "x": 0.1,
        "y": 0.1,
        "w": 0.1,
        "h": 0.04,
    }
    ctx = {
        "_ocr_tokens_cache": {
            "frame": "fresh-frame",
            "tokens": [
                {"text": "代金券", "x": 702.0, "y": 911.0, "w": 96.0, "h": 31.0, "parent_line_id": "voucher"},
            ],
        },
    }

    result = runner._shape_cached_frame_ocr_match(ctx, image, shape, "fresh-frame")

    assert result["matched"] is True
    assert result["floating_ocr"] is True
    assert result["fixed_box"] != result["resolved_box"]
    assert result["resolved_box"] == {"x": 702.0, "y": 911.0, "w": 96.0, "h": 31.0}
    assert runner._shape_match_resolved_click_point(image, shape, result) == (750.0, 926.5)


def test_floating_image_match_adapts_tolerance_until_candidate_is_unique(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("气泡", "0421.png")
    shape = {
        "title": "气泡",
        "floating": True,
        "imageMatchRole": "required",
        "pixelTolerance": 20,
    }
    box = {"x": 700, "y": 900, "w": 57, "h": 67}
    reference_box = {"x": 17, "y": 1008, "w": 57, "h": 67}
    initial = {"box": reference_box, "matches": [{"box": box, "crop_similarity": 79, "crop_score": 0.79}]}
    observed_tolerances = []

    def fake_run_match(_ctx, _image, probe_shape, _frame, **_kwargs):
        tolerance = probe_shape["pixelTolerance"]
        observed_tolerances.append(tolerance)
        similarity = 86 if tolerance == 25 else 79
        return {"matches": [{"box": box, "crop_similarity": similarity, "crop_score": similarity / 100}]}

    monkeypatch.setattr(runner, "_run_match", fake_run_match)
    result = runner._resolve_unique_floating_image_match(
        {}, image, shape, "fresh-frame", initial, match_strategy="auto"
    )

    assert result["unique_match"] is True
    assert result["pixel_tolerance"] == 25
    assert result["box"] == reference_box
    assert result["resolved_box"] == box
    assert observed_tolerances == [25]


def test_floating_image_match_tightens_score_until_duplicate_is_unique():
    runner = create_behavior_tree_runtime_runner()
    image = _image("气泡", "0421.png")
    shape = {"title": "气泡", "floating": True, "imageMatchRole": "required", "pixelTolerance": 20}
    best_box = {"x": 700, "y": 900, "w": 57, "h": 67}
    initial = {
        "matches": [
            {"box": best_box, "crop_similarity": 91, "crop_score": 0.91},
            {"box": {"x": 100, "y": 200, "w": 57, "h": 67}, "crop_similarity": 88, "crop_score": 0.88},
        ]
    }

    result = runner._resolve_unique_floating_image_match(
        {}, image, shape, "fresh-frame", initial, match_strategy="auto"
    )

    assert result["unique_match"] is True
    assert result["selection_threshold"] == 89
    assert result["resolved_box"] == best_box


def test_floating_ocr_shape_rejects_ambiguous_full_frame_matches():
    runner = create_behavior_tree_runtime_runner()
    image = _image("气泡菜单", "0590.png")
    shape = {
        "id": "account",
        "title": "账户",
        "floating": True,
        "imageMatchRole": "off",
        "ocrText": "账户",
        "ocrMatchRole": "required",
        "ocrMatchMode": "contains",
        "x": 0.1,
        "y": 0.1,
        "w": 0.1,
        "h": 0.04,
    }
    ctx = {
        "_ocr_tokens_cache": {
            "frame": "fresh-frame",
            "tokens": [
                {"text": "账户", "x": 100.0, "y": 200.0, "w": 60.0, "h": 30.0, "parent_line_id": "first"},
                {"text": "账户", "x": 700.0, "y": 900.0, "w": 60.0, "h": 30.0, "parent_line_id": "second"},
            ],
        },
    }

    result = runner._shape_cached_frame_ocr_match(ctx, image, shape, "fresh-frame")

    assert result["matched"] is False
    assert result["reason"] == "floating_ocr_ambiguous"
    assert len(result["candidate_boxes"]) == 2


def test_floating_scene_alignment_requires_one_common_popup_translation(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("气泡菜单", "0590.png")
    image["floatingAlignmentRequired"] = True
    image["floatingAlignmentTolerance"] = 20
    image["shapes"] = [
        {
            "id": "account",
            "title": "账户",
            "isSceneIdentity": True,
            "sceneIdentityRole": "required",
            "floating": True,
            "ocrText": "账户",
            "ocrMatchRole": "required",
            "x": 0.10,
            "y": 0.20,
            "w": 0.10,
            "h": 0.04,
        },
        {
            "id": "voucher",
            "title": "代金券",
            "isSceneIdentity": True,
            "sceneIdentityRole": "required",
            "floating": True,
            "ocrText": "代金券",
            "ocrMatchRole": "required",
            "x": 0.30,
            "y": 0.40,
            "w": 0.10,
            "h": 0.04,
        },
    ]
    live_boxes = {
        "账户": {"x": 190.0, "y": 420.0, "w": 90.0, "h": 64.0},
        "代金券": {"x": 370.0, "y": 740.0, "w": 90.0, "h": 64.0},
    }

    monkeypatch.setattr(
        runner,
        "_match_shape",
        lambda _ctx, _image, shape, _frame, **_kwargs: {
            "matched": True,
            "resolved_box": live_boxes[shape["title"]],
        },
    )
    ctx: dict = {}

    assert runner._floating_scene_alignment_score(ctx, image, "fresh-frame") == 100.0
    assert ctx["_floating_scene_alignment"]["image_id"] == runner._image_number(image)
    assert ctx["_floating_scene_alignment"]["maximum_error"] == pytest.approx(0.0)

    live_boxes["代金券"] = {"x": 520.0, "y": 740.0, "w": 90.0, "h": 64.0}
    assert runner._floating_scene_alignment_score({}, image, "fresh-frame") == 0.0


def test_click_floating_optional_ocr_shape_still_requires_a_match(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("世界下方菜单", "0035.png")
    shape = {
        "id": "mail",
        "kind": "rect",
        "title": "邮件",
        "floating": True,
        "imageMatchRole": "off",
        "ocrText": "邮件",
        "ocrMatchRole": "optional",
        "x": 0.5,
        "y": 0.9,
        "w": 0.1,
        "h": 0.06,
    }
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}
    clicked: list[dict] = []

    monkeypatch.setattr(
        runner,
        "_run_match",
        lambda *_args, **_kwargs: {"similarity": 0, "matches": [], "fixed_box": {"x": 455, "y": 1460, "w": 83, "h": 105}},
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.behavior_tree_runtime._click_game_window2_service",
        lambda payload: clicked.append(payload) or {"ok": True},
    )

    try:
        runner._click_shape(ctx, image, shape, "frame")
    except RuntimeError as exc:
        assert "未能定位浮动按钮" in str(exc)
    else:
        raise AssertionError("expected optional OCR miss on floating action to abort click")
    assert clicked == []


def test_click_shape_respecting_conditions_waits_when_shape_has_condition(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("双修痴情咒详情", "0216.png")
    shape = {
        "id": "invite",
        "kind": "rect",
        "title": "邀请道友",
        "imageMatchRole": "required",
        "x": 0.45,
        "y": 0.76,
        "w": 0.1,
        "h": 0.05,
    }
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}
    waited: list[str] = []
    clicked: list[tuple[str, dict]] = []

    def fake_wait_shape_match(_ctx, _stop_event, _image, _shape, **kwargs):
        waited.append(kwargs["label"])
        if False:
            yield None
        return "matched-frame", {"matched": True, "similarity": 100, "fixed_box": {"x": 100, "y": 200, "w": 80, "h": 40}}

    monkeypatch.setattr(runner, "_wait_shape_match", fake_wait_shape_match)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, _shape, frame, match_result=None: clicked.append((frame, match_result)))
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not raw click")))

    result = runner._run_direct_runtime_action(
        lambda: runner._click_shape_respecting_conditions(
            ctx,
            threading.Event(),
            image,
            shape,
            {},
            label="等待邀请道友",
        ),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result is None
    assert waited == ["等待邀请道友"]
    assert clicked == [("matched-frame", {"matched": True, "similarity": 100, "fixed_box": {"x": 100, "y": 200, "w": 80, "h": 40}})]


def test_daily_shuangxiu_can_resume_from_detail_scene(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    ctx = {
        "asset_tree_path": Path("asset-tree.json"),
        "images": {216: _image("双修痴情咒详情", "0216.png")},
    }
    stop_event = threading.Event()
    actions: list[tuple] = []

    class FakeRuntime:
        def current_scene(self, candidates, *, update: bool = False):
            actions.append(("current_scene", candidates, update))
            return 216, 100.0, "frame"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            return "痴情咒 双人神通"

    def fake_invite(*_args, **_kwargs):
        actions.append(("invite",))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_click_daily_shuangxiu_invite", fake_invite)

    result = _drain_generator(runner._execute_daily_shuangxiu_task(ctx, stop_event, {}))

    assert result == "success"
    assert actions == [
        ("current_scene", [221, 219, 218, 217, 216, 215, 69, 34], True),
        ("ocr_text", "frame"),
        ("invite",),
    ]


def test_daily_shuangxiu_remaining_zero_goes_to_finish(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    ctx = {"asset_tree_path": Path("asset-tree.json"), "images": {}}
    stop_event = threading.Event()
    actions: list[tuple] = []

    class FakeRuntime:
        def current_scene(self, candidates, *, update: bool = False):
            actions.append(("current_scene", candidates, update))
            return 219, 100.0, "frame"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            return "双人修炼 今日剩余修炼次数：0+ 前往修炼"

    def fake_finish(*_args, **_kwargs):
        actions.append(("finish",))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_finish_daily_shuangxiu_after_continue", fake_finish)
    monkeypatch.setattr(runner, "_click_daily_shuangxiu_start_training", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not start")))

    result = _drain_generator(runner._execute_daily_shuangxiu_task(ctx, stop_event, {}))

    assert result == "success"
    assert actions == [
        ("current_scene", [221, 219, 218, 217, 216, 215, 69, 34], True),
        ("ocr_text", "frame"),
        ("finish",),
    ]


def test_click_shape_respecting_conditions_raw_clicks_without_condition(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("双修秘术", "0215.png")
    shape = {
        "id": "book",
        "kind": "rect",
        "title": "痴情咒",
        "imageMatchRole": "off",
        "ocrMatchRole": "off",
        "x": 0.166667,
        "y": 0.260417,
        "w": 0.207407,
        "h": 0.052083,
    }
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}
    clicked: list[tuple[float, float]] = []

    monkeypatch.setattr(runner, "_wait_shape_match", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not wait")))
    monkeypatch.setattr(runner, "_click_shape", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not matched click")))
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1))))

    result = runner._run_direct_runtime_action(
        lambda: runner._click_shape_respecting_conditions(
            ctx,
            threading.Event(),
            image,
            shape,
            {},
            label="点击痴情咒",
            y_ratio=0.35,
        ),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result is None
    assert clicked == [(243.3, 445.8)]


def test_runtime_shape_payload_matches_frontend_protocol_for_floating_ocr():
    runner = create_behavior_tree_runtime_runner()
    image = _image("世界下方菜单", "0035.png")
    shape = {
        "id": "mail",
        "kind": "rect",
        "title": "邮件",
        "floating": True,
        "imageMatchRole": "off",
        "ocrText": "邮件",
        "ocrMatchRole": "optional",
        "x": 0.5,
        "y": 0.9,
        "w": 0.1,
        "h": 0.06,
    }

    flags = runner._shape_runtime_match_payload_flags(shape)
    payload = runner._build_shape_match_payload(
        image,
        shape,
        "frame",
        scan=bool(flags["scan"]),
        match_strategy=str(flags["match_strategy"]),
        ocr_enabled=bool(flags["ocr_enabled"]),
    )

    assert payload["scan"] is False
    assert payload["match_strategy"] == "anchor_pixel"
    assert payload["ocr_enabled"] is True
    assert payload["ocr_text"] == "邮件"
    assert payload["ocr_mask_mode"] == "inherit-envelope"
    assert payload["ocr_mask_data_url"] is None


def test_runtime_shape_payload_scans_floating_image_without_ocr():
    runner = create_behavior_tree_runtime_runner()
    image = _image("世界下方动态", "0068.png")
    shape = {
        "id": "mail-icon",
        "kind": "rect",
        "title": "邮件",
        "floating": True,
        "imageMatchRole": "required",
        "ocrMatchRole": "off",
        "x": 0.38,
        "y": 0.86,
        "w": 0.07,
        "h": 0.04,
    }

    flags = runner._shape_runtime_match_payload_flags(shape)
    payload = runner._build_shape_match_payload(
        image,
        shape,
        "frame",
        scan=bool(flags["scan"]),
        match_strategy=str(flags["match_strategy"]),
        ocr_enabled=bool(flags["ocr_enabled"]),
    )

    assert payload["scan"] is True
    assert payload["match_strategy"] == "auto"
    assert payload["ocr_enabled"] is False


def test_runtime_shape_match_flags_follow_explicit_image_and_ocr_roles():
    runner = create_behavior_tree_runtime_runner()

    assert runner._shape_runtime_match_payload_flags({"id": "legacy", "title": "旧shape"}) == {
        "image_role": "off",
        "ocr_role": "off",
        "ocr_enabled": False,
        "scan": False,
        "match_strategy": "anchor_pixel",
    }

    image_shape = {"id": "image", "imageMatchRole": "required", "ocrMatchRole": "off"}
    assert runner._shape_match_conditions(image_shape) == ["image"]

    ocr_shape = {"id": "ocr", "imageMatchRole": "off", "ocrMatchRole": "required", "ocrText": "邮件"}
    assert runner._shape_match_conditions(ocr_shape) == ["ocr"]
    assert runner._shape_runtime_match_payload_flags(ocr_shape)["ocr_enabled"] is True

    missing_text_shape = {"id": "ocr-title-only", "title": "邮件", "imageMatchRole": "off", "ocrMatchRole": "required"}
    assert runner._shape_match_conditions(missing_text_shape) == []
    assert runner._shape_runtime_match_payload_flags(missing_text_shape)["ocr_enabled"] is False


def test_wait_click_shape_does_not_infer_ocr_text_from_title():
    runner = create_behavior_tree_runtime_runner()
    image = _image("邮件", "0121.png", [])
    view = behavior_tree_runtime_core.View(image)
    raw = {
        "id": "mail",
        "kind": "rect",
        "title": "邮件",
        "imageMatchRole": "off",
        "ocrMatchRole": "required",
    }
    shape = behavior_tree_runtime_core.Shape(raw, parent_view=view)
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"entry": object()},
        asset_tree_path=Path("asset-tree.json"),
        stop_event=threading.Event(),
    )

    search_shape = runtime._shape_match_search_shape(shape)

    assert search_shape.get("ocrText") in (None, "")
    assert runner._shape_match_conditions(search_shape) == []


def test_auto_close_guard_images_flatten_popup_domain_and_require_scene_identity():
    runner = create_behavior_tree_runtime_runner()
    top_level = _image("所有提示窗口", "0047.jpg", [
        {"id": "identity-47", "isSceneIdentity": True},
        {"id": "close-47", "title": "空白"},
    ])
    nested = _image("拍卖", "0028.jpg", [
        {"id": "identity-28", "sceneIdentityRole": "required"},
        {"id": "close-28", "title": "关闭"},
    ])
    no_identity = _image("普通素材", "0355.jpg", [{"id": "close", "title": "关闭"}])
    no_action = _image("退出道场", "0054.jpg", [{"id": "identity-54", "sceneIdentityRole": "required"}])
    login = _image("修炼", "0019.jpg", [{"id": "identity-19", "isSceneIdentity": True}])

    candidates = runner._auto_close_guard_images([
        {"type": "folder", "title": "遮挡", "children": [_image("公告遮挡", "0026.jpg")]},
        {"type": "folder", "title": "登录弹窗", "children": [login]},
        {
            "type": "folder",
            "title": "弹窗",
            "children": [
                top_level,
                    {"type": "folder", "title": "所有提示窗口", "children": [nested, no_identity, no_action]},
            ],
        },
    ])

    assert [item["image"]["title"] for item in candidates] == ["所有提示窗口", "拍卖"]
    assert [item["folder_path"] for item in candidates] == ["弹窗", "弹窗/所有提示窗口"]


def test_auto_close_guard_indexes_confirmation_action_spelling():
    runner = create_behavior_tree_runtime_runner()
    popup = _image("服用丹药", "0507.png", [
        {
            "id": "identity",
            "title": "服用丹药标识",
            "sceneIdentityRole": "required",
            "ocrMatchRole": "required",
            "ocrText": "本次服用丹药增加属性",
        },
        {"id": "confirm", "title": "确认", "ocrMatchRole": "required", "ocrText": "确认"},
    ])

    candidates = runner._auto_close_guard_images(_popup_domain(popup))

    assert len(candidates) == 1
    assert candidates[0]["image"] is popup
    assert candidates[0]["action_shape"]["title"] == "确认"


def test_generic_popup_handler_clicks_the_indexed_confirmation_action(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    popup = _image("服用丹药", "0507.png", [
        {"id": "identity", "title": "服用丹药标识", "sceneIdentityRole": "required"},
        {"id": "confirm", "title": "确认"},
    ])
    candidate = runner._auto_close_guard_images(_popup_domain(popup))[0]
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, {"entry": object()}, frame_data_url="popup-frame")
    clicked: list[str] = []
    monkeypatch.setattr(
        runtime,
        "click_shape",
        lambda _view, shape, **_kwargs: clicked.append(shape.title),
    )

    assert runner._handle_recognized_popup_candidate(runtime, candidate, score=100.0) is True
    assert clicked == ["确认"]
    assert runner.status()["last_guard_event"]["action"] == "click:确认"


def test_login_promotion_popup_indexes_close_and_never_participate(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    popup = _image("缘定三生登录推广", "0608.png", [
        {
            "id": "title",
            "title": "缘定三生",
            "sceneIdentityRole": "required",
            "ocrMatchRole": "required",
            "ocrText": "缘定三生",
        },
        {
            "id": "participate",
            "title": "立即参与",
            "sceneIdentityRole": "required",
            "ocrMatchRole": "required",
            "ocrText": "立即参与",
        },
        {"id": "close", "title": "关闭"},
    ])

    candidate = runner._auto_close_guard_images(_popup_domain(popup))[0]

    assert candidate["action_shape"]["title"] == "关闭"
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"entry": object()},
        frame_data_url="promotion-frame",
    )
    clicked: list[str] = []
    monkeypatch.setattr(
        runtime,
        "click_shape",
        lambda _view, shape, **_kwargs: clicked.append(shape.title),
    )
    assert runner._handle_recognized_popup_candidate(runtime, candidate, score=100.0)
    assert clicked == ["关闭"]


def test_offline_cultivation_popup_indexes_acknowledgement_only():
    runner = create_behavior_tree_runtime_runner()
    popup = _image("离线修炼结算", "0609.png", [
        {
            "id": "duration",
            "title": "修炼时间",
            "sceneIdentityRole": "required",
            "ocrMatchRole": "required",
            "ocrText": "修炼时间",
        },
        {
            "id": "skill",
            "title": "修炼功法",
            "sceneIdentityRole": "required",
            "ocrMatchRole": "required",
            "ocrText": "修炼功法",
        },
        {"id": "confirm", "title": "确定"},
    ])

    candidate = runner._auto_close_guard_images(_popup_domain(popup))[0]

    assert candidate["action_shape"]["title"] == "确定"


def test_runtime_current_scene_repeats_same_layer0_after_popup_interruption(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    business = _scene_image("世界", "0034.png")
    popup = _image("服用丹药", "0507.png", [
        {"id": "identity", "title": "服用丹药标识", "sceneIdentityRole": "required"},
        {"id": "confirm", "title": "确认"},
    ])
    candidate = runner._auto_close_guard_images(_popup_domain(popup))[0]
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(
        runner,
        {"images": {34: business}, "asset_tree": []},
        frame_data_url="popup-frame",
    )
    runtime.candidates = [candidate]
    identified: list[tuple[str, tuple[int, ...]]] = []
    handled: list[int] = []

    monkeypatch.setattr(runner, "_handle_disconnect_reconnect_popup", lambda _runtime: False)
    monkeypatch.setattr(runner, "_skip_popup_guard_on_login_or_maintenance", lambda _runtime: False)

    def identify(_ctx, frame, preferred_scene_ids):
        identified.append((frame, tuple(preferred_scene_ids)))
        return (507, 100.0) if frame == "popup-frame" else (34, 100.0)

    monkeypatch.setattr(runner, "_identify_scene_number", identify)
    monkeypatch.setattr(
        runner,
        "_handle_recognized_popup_candidate",
        lambda _runtime, item, **_kwargs: handled.append(runner._image_number(item["image"])) or True,
    )
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "business-frame")
    monkeypatch.setattr(behavior_tree_runtime_core.time, "sleep", lambda _seconds: None)

    assert runtime.current_scene([34]) == (34, 100.0, "business-frame")
    assert handled == [507]
    assert identified == [
        ("popup-frame", (34, 507)),
        ("business-frame", (34, 507)),
    ]


def test_scene_score_requires_explicit_scene_identity_shape(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    normal_shape = {"id": "signup", "kind": "rect", "title": "报名", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}
    image = _image("报名", "0023.jpg", [normal_shape])
    calls: list[str] = []

    monkeypatch.setattr(runner, "_shape_score", lambda _ctx, _image, shape, _frame: calls.append(shape["title"]) or 90)

    assert runner._scene_score({"entry": object()}, image, "frame") == 0
    assert calls == []


def test_scene_score_rejects_layer3_without_identity_as_scene(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    layer3_image = _image("素材模板", "0999.jpg")
    layer3_image["layer"] = 3
    layer2_image = _image("普通页面", "0100.jpg")
    layer2_image["layer"] = 2
    identified_image = _image("明确页面", "0101.jpg", [
        {"id": "identity", "kind": "rect", "title": "身份", "sceneIdentityRole": "required"},
    ])
    identified_image["layer"] = 3

    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: 91.0)

    assert runner._scene_score({"entry": object()}, layer3_image, "frame") == 0.0
    assert runner._scene_score({"entry": object()}, layer2_image, "frame") == 0.0
    assert runner._scene_score({"entry": object()}, identified_image, "frame") == 91.0


def test_identify_scene_number_keeps_layer3_similarity_as_unknown_evidence(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    weak_root = _image("隐藏浮动窗", "0058.png")
    weak_root["layer"] = 3
    ctx = {"asset_tree": [weak_root], "images": {58: weak_root}}

    monkeypatch.setattr(runner, "_scene_reference_similarity", lambda *_args: 92.0)

    assert runner._identify_scene_number(ctx, "frame") == (None, 92.0)
    assert ctx["_last_scene_recognition_status"] == "no_match"
    assert ctx["_last_layer3_auxiliary"] == {
        "reference_id": 58,
        "score": 92.0,
        "threshold": 90.0,
        "above_threshold": True,
    }


def test_navigation_scene_id_rejects_identity_free_layer3_reference():
    runner = create_behavior_tree_runtime_runner()
    parent = _image("世界", "0034.jpg", [
        {"id": "world-id", "kind": "rect", "title": "大地图", "sceneIdentityRole": "required"},
    ])
    parent["layer"] = 1
    weak_child = _image("世界-下方动态", "0068.jpg")
    weak_child["layer"] = 3
    identified_child = _image("明确子场景", "0100.jpg", [
        {"id": "child-id", "kind": "rect", "title": "明确", "sceneIdentityRole": "required"},
    ])
    identified_child["layer"] = 3
    identified_layer2 = _image("可靠场景", "0101.jpg", [
        {"id": "layer2-id", "kind": "rect", "title": "可靠", "sceneIdentityRole": "required"},
    ])
    identified_layer2["layer"] = 2
    parent["children"] = [weak_child, identified_child]
    ctx = {
        "asset_tree": [parent, identified_layer2],
        "images": {34: parent, 68: weak_child, 100: identified_child, 101: identified_layer2},
    }

    assert runner._navigation_scene_id(ctx, 68) is None
    # View classifies an identity-bearing asset as Layer 2 even if stale raw
    # metadata still says 3; its required identity makes it a real scene.
    assert runner._navigation_scene_id(ctx, 100) == 100
    assert runner._navigation_scene_id(ctx, 101) == 101


def test_navigation_scene_id_rejects_top_level_identity_free_layer3_reference():
    runner = create_behavior_tree_runtime_runner()
    weak_root = _image("弱兜底素材", "0999.jpg")
    weak_root["layer"] = 3
    ctx = {"asset_tree": [weak_root], "images": {999: weak_root}}

    assert runner._navigation_scene_id(ctx, 999) is None


def test_scene_route_candidates_exclude_identity_free_layer3_child():
    runner = create_behavior_tree_runtime_runner()
    entry_shape = {"id": "entry", "title": "进入", "sceneJumpTarget": "20"}
    world = _image("世界", "0034.jpg", [
        {"id": "world-id", "title": "世界身份", "sceneIdentityRole": "required"},
        entry_shape,
    ])
    world["layer"] = 1
    auxiliary_child = _image("世界动态参考", "0068.jpg")
    auxiliary_child["layer"] = 3
    world["children"] = [auxiliary_child]
    target = _image("目标", "0020.jpg", [
        {"id": "target-id", "title": "目标身份", "sceneIdentityRole": "required"},
    ])
    target["layer"] = 2
    tree = [world, target]

    candidates = runner._scene_route_candidate_ids(tree, 20)

    assert 34 in candidates
    assert 68 not in candidates


def test_navigation_scene_id_does_not_inherit_parent_identity_for_layer3(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    parent = _image("世界", "0034.jpg", [
        {"id": "world-id", "kind": "rect", "title": "大地图", "sceneIdentityRole": "required"},
    ])
    parent["layer"] = 1
    weak_child = _image("世界-下方动态", "0068.jpg")
    weak_child["layer"] = 3
    parent["children"] = [weak_child]
    ctx = {"asset_tree": [parent], "images": {34: parent, 68: weak_child}}

    monkeypatch.setattr(runner, "_scene_score", lambda _ctx, _image, _frame: 0.0)

    assert runner._navigation_scene_id(ctx, 68, "not-world-frame") is None


def test_wait_scene_jump_does_not_record_layer3_as_real_landing(monkeypatch, tmp_path):
    runner = create_behavior_tree_runtime_runner()
    daily_shape = {"id": "daily", "kind": "rect", "title": "日常", "x": 0.05, "y": 0.2, "w": 0.1, "h": 0.1, "sceneJumpTarget": "69"}
    parent = _image("世界", "0034.jpg", [
        {"id": "world-id", "kind": "rect", "title": "大地图", "sceneIdentityRole": "required"},
        daily_shape,
    ])
    parent["layer"] = 1
    weak_child = _image("世界-下方动态", "0068.jpg")
    weak_child["layer"] = 3
    daily = _image("日常", "0069.jpg")
    daily["layer"] = 1
    parent["children"] = [weak_child]
    tree = [parent, daily]
    path = tmp_path / "asset-tree.json"
    path.write_text(json.dumps(tree), encoding="utf-8")
    ctx = {"asset_tree": tree, "images": {34: parent, 68: weak_child, 69: daily}}

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, _frame, preferred_scene_ids=None: (
            (None, 0.0) if preferred_scene_ids else (68, 86.0)
        ),
    )
    monkeypatch.setattr(runner, "_scene_score", lambda _ctx, image, _frame: 100.0 if image is parent else 86.0)
    monkeypatch.setattr(runner, "_scene_jump_source_stall_timeout", lambda **_kwargs: 0.0)

    assert runner._navigation_scene_id(ctx, 68, "frame") is None
    assert json.loads(path.read_text(encoding="utf-8"))[0]["shapes"][1]["sceneJumpTarget"] == "69"


def test_go_scene_next_edge_treats_self_landing_frequency_as_soft_non_progress_evidence():
    runner = create_behavior_tree_runtime_runner()
    mostly_self_shape = {
        "id": "mostly-self",
        "kind": "rect",
        "title": "返回",
        "sceneJumpTarget": "1(90),2(10)",
    }
    reliable_shape = {
        "id": "reliable",
        "kind": "rect",
        "title": "返回",
        "sceneJumpTarget": "4(5)",
    }
    tree = [
        _scene_image("起点", "0001.png", [mostly_self_shape, reliable_shape]),
        _scene_image("落点甲", "0002.png", [{"id": "to-target-a", "kind": "rect", "title": "返回", "sceneJumpTarget": "3"}]),
        _scene_image("目标", "0003.png", []),
        _scene_image("落点乙", "0004.png", [{"id": "to-target-b", "kind": "rect", "title": "返回", "sceneJumpTarget": "3"}]),
    ]

    decision = runner._select_scene_next_edge(tree, 1, 3)

    assert decision is not None
    assert decision["edge"]["shape"]["id"] == reliable_shape["id"]
    assert "自身落点" not in decision["reason"]


def _ocr_tokens(text: str, *, x: float, y: float, w: float, h: float) -> list[dict]:
    token_width = w / max(1, len(text))
    return [
        {"text": char, "x": x + index * token_width, "y": y, "w": token_width, "h": h}
        for index, char in enumerate(text)
    ]


def _ocr_tokens_from_fragments(fragments: list[dict]) -> list[dict]:
    return [
        token
        for fragment in fragments
        for token in _ocr_tokens(
            str(fragment.get("text") or ""),
            x=float(fragment.get("x") or 0),
            y=float(fragment.get("y") or 0),
            w=float(fragment.get("w") or max(1, len(str(fragment.get("text") or "")))),
            h=float(fragment.get("h") or 1),
        )
    ]


def test_identify_scene_number_for_route_uses_ordered_detect_candidates(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    tree: list[dict] = []
    ctx = {"images": {1: _image("先匹配", "0001.jpg"), 2: _image("后匹配", "0002.jpg")}}
    seen: list[list[int]] = []

    def fake_identify(_ctx, _frame, candidates=None):
        seen.append(list(candidates or []))
        return 1, 81.0

    monkeypatch.setattr(runner, "_identify_scene_number", fake_identify)

    assert runner._identify_scene_number_for_route(ctx, "frame", tree, 34, [1, 2]) == (1, 81.0)
    assert seen == [[1, 2]]


def test_popup_score_can_fallback_to_plain_shapes(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    blank_shape = {"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}
    image = _image("所有提示窗口", "0047.jpg", [blank_shape])

    monkeypatch.setattr(runner, "_shape_score", lambda _ctx, _image, _shape, _frame, **_kwargs: 80)

    assert runner._popup_score({"entry": object()}, image, "frame") == 80


def test_scene_jump_edges_do_not_infer_nested_leave_target():
    runner = create_behavior_tree_runtime_runner()
    leave_shape = {"id": "leave", "kind": "rect", "title": "离开", "x": 0.8, "y": 0.5, "w": 0.1, "h": 0.1}
    tree = [
        _scene_image("世界", "0034.jpg", []),
        {
            "id": "folder-world",
            "type": "folder",
            "title": "世界",
            "children": [
                _scene_image("某区域内部", "0085.png", [leave_shape]),
            ],
        },
    ]

    edges = runner._scene_jump_edges(tree)
    assert 85 not in edges
    assert runner._find_scene_route(tree, 85, 34) is None


def test_scene_jump_edges_do_not_infer_folder_named_close_target():
    runner = create_behavior_tree_runtime_runner()
    close_shape = {"id": "close-menu", "kind": "rect", "title": "关闭下方菜单", "x": 0.8, "y": 0.9, "w": 0.1, "h": 0.05}
    tree = [
        {
            "id": "folder-world",
            "type": "folder",
            "title": "世界",
            "children": [
                _scene_image("世界", "0034.jpg", []),
                _scene_image("世界下方菜单", "0035.png", [close_shape]),
            ],
        },
    ]

    edges = runner._scene_jump_edges(tree)

    assert 35 not in edges
    assert runner._find_scene_route(tree, 35, 34) is None


def test_go_scene_next_edge_prefers_observed_reachable_action_over_first_bfs_edge():
    runner = create_behavior_tree_runtime_runner()
    low_confidence_shape = {
        "id": "low",
        "kind": "rect",
        "title": "低频入口",
        "x": 0.1,
        "y": 0.1,
        "w": 0.2,
        "h": 0.1,
        "sceneJumpTarget": "2",
    }
    high_confidence_shape = {
        "id": "high",
        "kind": "rect",
        "title": "高频入口",
        "x": 0.4,
        "y": 0.1,
        "w": 0.2,
        "h": 0.1,
        "sceneJumpTarget": "4(7)",
    }
    tree = [
        _scene_image("起点", "0001.png", [low_confidence_shape, high_confidence_shape]),
        _scene_image("低频落点", "0002.png", [{"id": "to-target-a", "kind": "rect", "title": "去目标", "sceneJumpTarget": "3"}]),
        _scene_image("目标", "0003.png", []),
        _scene_image("高频落点", "0004.png", [{"id": "to-target-b", "kind": "rect", "title": "去目标", "sceneJumpTarget": "3"}]),
    ]

    route = runner._find_scene_route(tree, 1, 3)
    assert route is not None
    assert route[0]["shape"]["id"] == low_confidence_shape["id"]

    decision = runner._select_scene_next_edge(tree, 1, 3)

    assert decision is not None
    assert decision["edge"]["shape"]["id"] == high_confidence_shape["id"]


def test_go_scene_next_edge_rejects_frequent_route_that_returns_to_source_before_target():
    runner = create_behavior_tree_runtime_runner()
    daily_shape = {
        "id": "daily",
        "kind": "rect",
        "title": "日常",
        "sceneJumpTarget": "69(1997),34(5),20(1)",
    }
    xianfu_shape = {
        "id": "xianfu",
        "kind": "rect",
        "title": "仙府",
        "sceneJumpTarget": "171(92),34(25)",
    }
    daily_exit_shape = {
        "id": "daily-exit",
        "kind": "rect",
        "title": "退出",
        "sceneJumpTarget": "34(515),20(1),85(1)",
    }
    tree = [
        _scene_image("世界", "0034.png", [daily_shape, xianfu_shape]),
        _scene_image("日常", "0069.png", [daily_exit_shape]),
        _scene_image("仙府", "0171.png", []),
        _scene_image("绿瓶", "0020.png", []),
        _scene_image("区域内部", "0085.png", []),
    ]

    decision = runner._select_scene_next_edge(tree, 34, 171)

    assert decision is not None
    assert decision["edge"]["shape"]["id"] == xianfu_shape["id"]
    assert decision["reason"].startswith("下一步主要直达目标")


def test_go_scene_next_edge_weights_current_step_by_posterior_progress_probability():
    runner = create_behavior_tree_runtime_runner()
    low_probability_shape = {
        "id": "low",
        "kind": "rect",
        "title": "低概率入口",
        "sceneJumpTarget": "3(3),1(7)",
    }
    high_probability_shape = {
        "id": "high",
        "kind": "rect",
        "title": "高概率入口",
        "sceneJumpTarget": "3(5),1(5)",
    }
    tree = [
        _scene_image("起点", "0001.png", [low_probability_shape, high_probability_shape]),
        _scene_image("目标", "0003.png", []),
    ]

    decision = runner._select_scene_next_edge(tree, 1, 3)

    assert decision is not None
    assert decision["edge"]["shape"]["id"] == high_probability_shape["id"]
    assert "单步进展权重" in decision["reason"]


def test_go_scene_next_edge_uses_progress_probabilities_as_random_choice_weights():
    runner = _create_behavior_tree_runtime_runner()
    captured: dict[str, object] = {}

    class FakeRandom:
        def choices(self, population, *, weights, k):
            captured["ids"] = [item["edge"]["shape"]["id"] for item in population]
            captured["weights"] = list(weights)
            captured["k"] = k
            return [population[0]]

    runner._navigation_random = FakeRandom()
    low_probability_shape = {
        "id": "low",
        "kind": "rect",
        "title": "低概率入口",
        "sceneJumpTarget": "3(10),1(90)",
    }
    high_probability_shape = {
        "id": "high",
        "kind": "rect",
        "title": "高概率入口",
        "sceneJumpTarget": "3(50),1(50)",
    }
    tree = [
        _scene_image("起点", "0001.png", [low_probability_shape, high_probability_shape]),
        _scene_image("目标", "0003.png", []),
    ]

    decision = runner._select_scene_next_edge(tree, 1, 3)

    assert decision is not None
    assert captured["ids"] == ["high", "low"]
    assert captured["k"] == 1
    high_weight, low_weight = captured["weights"]
    assert high_weight / low_weight == pytest.approx(51 / 11)


def test_go_scene_next_edge_green_bottle_dirty_landings_do_not_beat_canonical_entry():
    runner = create_behavior_tree_runtime_runner()
    daily_shape = {
        "id": "daily",
        "kind": "rect",
        "title": "日常",
        "sceneJumpTarget": "69(2247),34(5),20(1)",
    }
    green_bottle_shape = {
        "id": "green-bottle",
        "kind": "rect",
        "title": "进入绿瓶",
        "sceneJumpTarget": "20(52)",
    }
    daily_exit_shape = {
        "id": "daily-exit",
        "kind": "rect",
        "title": "退出",
        "sceneJumpTarget": "34(711),20(1),85(1)",
    }
    tree = [
        _scene_image("世界", "0034.png", [daily_shape, green_bottle_shape]),
        _scene_image("日常", "0069.png", [daily_exit_shape]),
        _scene_image("绿瓶", "0020.png", []),
        _scene_image("区域内部", "0085.png", []),
    ]

    daily = runner._rank_scene_next_edge(tree, runner._scene_jump_edges(tree)[34][0], 20, order=0)
    green_bottle = runner._rank_scene_next_edge(tree, runner._scene_jump_edges(tree)[34][1], 20, order=1)
    decision = runner._select_scene_next_edge(tree, 34, 20)

    assert daily is not None
    assert green_bottle is not None
    assert daily["score"][0] < 1_000
    assert green_bottle["score"][0] > 980_000
    assert decision is not None
    assert decision["edge"]["shape"]["id"] == green_bottle_shape["id"]


def test_go_scene_to_world_never_uses_xuanhuang_forward_as_a_return_route():
    runner = create_behavior_tree_runtime_runner()
    forward = {
        "id": "forward",
        "kind": "rect",
        "title": "前往",
        "sceneJumpTarget": "420(4)",
    }
    leave = {
        "id": "leave",
        "kind": "rect",
        "title": "离开",
        "sceneJumpTarget": "34(4)",
    }
    tree = [
        _scene_image("世界", "0034.png", []),
        _scene_image("玄荒挑战", "0418.png", [forward]),
        _scene_image("玄荒战斗结束", "0420.png", [leave]),
    ]

    assert runner._find_scene_route(tree, 418, 34) is not None
    assert runner._select_scene_next_edge(tree, 418, 34) is None
    assert runner._scene_navigation_exploration_priority(forward) == 0


def test_go_scene_to_world_allows_explicitly_verified_forward_transit():
    runner = create_behavior_tree_runtime_runner()
    forward = {
        "id": "verified-forward",
        "kind": "rect",
        "title": "前往",
        "sceneJumpTarget": "400(4)",
        "allowReturnViaForward": True,
    }
    leave = {
        "id": "leave",
        "kind": "rect",
        "title": "返回",
        "sceneJumpTarget": "34(4)",
    }
    tree = [
        _scene_image("世界", "0034.png", []),
        _scene_image("已验证前往过渡页", "0528.png", [forward]),
        _scene_image("活动主页", "0400.png", [leave]),
    ]

    decision = runner._select_scene_next_edge(tree, 528, 34)

    assert decision is not None
    assert decision["edge"]["shape"]["id"] == "verified-forward"


def test_scene_navigation_only_allows_exact_claim_reward_action():
    runner = create_behavior_tree_runtime_runner()

    def edge(title: str) -> dict:
        return {
            "source_id": 437,
            "target_ids": [438],
            "image": {"title": "历练奖励"},
            "shape": {"id": title, "title": title, "sceneJumpTarget": "438"},
        }

    assert runner._scene_navigation_edge_risk(edge("领取奖励"), 34) == 0
    assert runner._scene_navigation_edge_risk(edge("领取"), 34) is None
    assert runner._scene_navigation_edge_risk(edge("一键领取"), 34) is None


def test_scene_navigation_closes_lilian_reward_route_from_437_to_world():
    runner = create_behavior_tree_runtime_runner()
    tree = [
        _scene_image(
            "历练事件结果",
            "0437.png",
            [{
                "id": "claim-reward",
                "kind": "rect",
                "title": "领取奖励",
                "sceneJumpTarget": "438",
            }],
        ),
        _scene_image(
            "历练奖励",
            "0438.png",
            [{
                "id": "close-reward",
                "kind": "rect",
                "title": "关闭",
                "sceneJumpTarget": "425",
            }],
        ),
        _scene_image(
            "历练地图",
            "0425.png",
            [{
                "id": "return-world",
                "kind": "rect",
                "title": "返回",
                "sceneJumpTarget": "34",
            }],
        ),
        _scene_image("世界", "0034.png", []),
    ]

    route = runner._find_scene_route(tree, 437, 34)
    assert route is not None
    assert [edge["shape"]["title"] for edge in route] == [
        "领取奖励",
        "关闭",
        "返回",
    ]
    assert runner._select_scene_next_edge(tree, 437, 34)["edge"]["shape"]["title"] == "领取奖励"
    assert runner._select_scene_next_edge(tree, 438, 34)["edge"]["shape"]["title"] == "关闭"
    assert runner._select_scene_next_edge(tree, 425, 34)["edge"]["shape"]["title"] == "返回"


def test_go_scene_next_edge_skips_failed_candidate_within_same_goto_round():
    runner = create_behavior_tree_runtime_runner()
    first_shape = {
        "id": "first",
        "kind": "rect",
        "title": "第一入口",
        "x": 0.1,
        "y": 0.1,
        "w": 0.2,
        "h": 0.1,
        "sceneJumpTarget": "2(9)",
    }
    second_shape = {
        "id": "second",
        "kind": "rect",
        "title": "备用入口",
        "x": 0.4,
        "y": 0.1,
        "w": 0.2,
        "h": 0.1,
        "sceneJumpTarget": "4(1)",
    }
    tree = [
        _scene_image("起点", "0001.png", [first_shape, second_shape]),
        _scene_image("第一落点", "0002.png", [{"id": "to-target-a", "kind": "rect", "title": "去目标", "sceneJumpTarget": "3"}]),
        _scene_image("目标", "0003.png", []),
        _scene_image("备用落点", "0004.png", [{"id": "to-target-b", "kind": "rect", "title": "去目标", "sceneJumpTarget": "3"}]),
    ]

    first_decision = runner._select_scene_next_edge(tree, 1, 3)
    assert first_decision is not None
    failed = {runner._scene_jump_edge_key(first_decision["edge"])}

    second_decision = runner._select_scene_next_edge(tree, 1, 3, failed_edge_keys=failed)

    assert second_decision is not None
    assert second_decision["edge"]["shape"]["id"] == second_shape["id"]


def test_go_scene_semantic_edge_key_ignores_mutable_landing_counts():
    runner = create_behavior_tree_runtime_runner()
    first = {
        "source_id": 301,
        "target_ids": [301, 20],
        "shape": {
            "id": "return",
            "title": "返回",
            "sceneJumpTarget": "301(3),20(1)",
        },
    }
    learned = {
        "source_id": 301,
        "target_ids": [301, 20],
        "shape": {
            "id": "return",
            "title": "返回",
            "sceneJumpTarget": "301(337),20(3)",
        },
    }

    assert runner._scene_jump_edge_key(first) != runner._scene_jump_edge_key(learned)
    assert runner._scene_jump_edge_semantic_key(first) == runner._scene_jump_edge_semantic_key(learned)


def test_go_scene_next_edge_skips_semantically_failed_action_after_landing_counts_change():
    runner = create_behavior_tree_runtime_runner()
    primary = {
        "id": "continue-primary",
        "kind": "rect",
        "title": "继续",
        "sceneJumpTarget": "413(3),482(1)",
    }
    alternate = {
        "id": "continue-alternate",
        "kind": "rect",
        "title": "继续备用区",
        "sceneJumpTarget": "482(1)",
    }
    tree = [
        _scene_image("修炼结算", "0413.png", [primary, alternate]),
        _scene_image("修炼后继", "0482.png", [{"id": "leave", "kind": "rect", "title": "离开", "sceneJumpTarget": "34"}]),
        _scene_image("世界", "0034.png", []),
    ]

    failed_primary_edge = {
        "source_id": 413,
        "image": tree[0],
        "shape": primary,
        "target_ids": [413, 482],
    }
    failed = {runner._scene_jump_edge_semantic_key(failed_primary_edge)}

    # A real self-loop increments the persisted frequency before replanning.
    primary["sceneJumpTarget"] = "413(4),482(1)"
    second = runner._select_scene_next_edge(tree, 413, 34, failed_edge_keys=failed)

    assert second is not None
    assert second["edge"]["shape"]["id"] == alternate["id"]


def test_shape_score_uses_ocr_fallback_when_image_score_is_below_scene_threshold(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    shape = {
        "id": "leave",
        "kind": "rect",
        "title": "离开",
        "ocrText": "离开",
        "ocrMatchRole": "optional",
        "x": 0.8,
        "y": 0.5,
        "w": 0.1,
        "h": 0.1,
    }
    image = _image("某区域内部", "0085.png", [shape])
    calls: list[bool] = []

    def fake_run_match(_ctx, _image, _shape, _frame, **kwargs):
        ocr_enabled = bool(kwargs.get("ocr_enabled"))
        calls.append(ocr_enabled)
        return {"similarity": 57}

    monkeypatch.setattr(runner, "_run_match", fake_run_match)
    monkeypatch.setattr(
        runner,
        "_ocr_frame",
        lambda _frame, **_kwargs: {"tokens": _ocr_tokens("离开", x=720, y=800, w=90, h=80)},
    )

    assert runner._shape_score({"entry": object()}, image, shape, "frame") == 100
    assert calls == []


def test_shape_score_caches_missing_reference_image(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    shape = {
        "id": "confirm",
        "kind": "rect",
        "title": "确认",
        "imageMatchRole": "required",
        "x": 0.1,
        "y": 0.1,
        "w": 0.1,
        "h": 0.1,
    }
    image = _image("仙缘挑战提示", "0201.png", [shape])
    calls: list[str] = []

    def fake_run_match(_ctx, _image, _shape, _frame, **_kwargs):
        calls.append("match")
        raise RuntimeError("400: 截图不存在：0201.png")

    monkeypatch.setattr(runner, "_run_match", fake_run_match)

    assert runner._shape_score({"entry": object()}, image, shape, "frame") == 0
    assert runner._shape_score({"entry": object()}, image, shape, "frame") == 0
    assert calls == ["match"]


def test_popup_score_does_not_scan_fallback_for_every_candidate(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    shape = {
        "id": "blank",
        "kind": "rect",
        "title": "空白",
        "imageMatchRole": "required",
        "x": 0,
        "y": 0,
        "w": 1,
        "h": 1,
    }
    image = _image("弹窗", "0047.png", [shape])
    calls: list[bool] = []

    def fake_run_match(_ctx, _image, _shape, _frame, **kwargs):
        calls.append(bool(kwargs.get("scan")))
        return {"similarity": 0, "matches": []}

    monkeypatch.setattr(runner, "_run_match", fake_run_match)

    assert runner._popup_score({"entry": object()}, image, "frame") == 0
    assert calls == [False]


def test_scene_score_requires_all_scene_identity_shapes(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    first_shape = {"id": "closed-menu", "kind": "rect", "title": "打开下方菜单", "isSceneIdentity": True}
    second_shape = {"id": "map", "kind": "rect", "title": "大地图", "isSceneIdentity": True}
    image = _image("世界", "0034.jpg", [first_shape, second_shape])

    def fake_shape_score(_ctx, _image, shape, _frame, **_kwargs):
        return 1 if shape["id"] == "closed-menu" else 100

    monkeypatch.setattr(runner, "_shape_score", fake_shape_score)

    assert runner._scene_score({"entry": object()}, image, "frame") == 0

    def fake_passing_shape_score(_ctx, _image, shape, _frame, **_kwargs):
        return 90 if shape["id"] == "closed-menu" else 100

    monkeypatch.setattr(runner, "_shape_score", fake_passing_shape_score)

    assert runner._scene_score({"entry": object()}, image, "frame") == 90


def test_scene_score_enforces_required_ocr_role(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    shape = {
        "id": "leave",
        "kind": "rect",
        "title": "离开",
        "isSceneIdentity": True,
        "imageMatchRole": "optional",
        "ocrEnabled": True,
        "ocrText": "离开",
        "ocrMatchRole": "required",
    }
    image = _image("某区域内部", "0085.png", [shape])
    calls: list[bool] = []

    def fake_run_match(_ctx, _image, _shape, _frame, **kwargs):
        ocr_enabled = bool(kwargs.get("ocr_enabled"))
        calls.append(ocr_enabled)
        return {"similarity": 0 if ocr_enabled else 99}

    monkeypatch.setattr(runner, "_run_match", fake_run_match)
    monkeypatch.setattr(runner, "_ocr_frame", lambda _frame, **_kwargs: {"tokens": []})

    assert runner._scene_score({"entry": object()}, image, "frame") == 0
    assert calls == []


def test_scene_jump_intermediate_confirm_shape_is_limited_to_leave_popup():
    runner = create_behavior_tree_runtime_runner()
    confirm = {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.6, "y": 0.6, "w": 0.1, "h": 0.05}
    leave_popup = _image("离开场景", "0086.png", [confirm])

    assert runner._scene_jump_intermediate_confirm_shape(leave_popup, {"title": "离开"}) is confirm
    assert runner._scene_jump_intermediate_confirm_shape(leave_popup, {"title": "退出"}) is confirm
    assert runner._scene_jump_intermediate_confirm_shape(leave_popup, {"title": "领取"}) is None
    assert runner._scene_jump_intermediate_confirm_shape(_image("奖励提示", "0099.png", [confirm]), {"title": "离开"}) is None


def test_scene_route_candidates_include_leave_confirmation_popup():
    runner = create_behavior_tree_runtime_runner()
    confirm = {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.6, "y": 0.6, "w": 0.1, "h": 0.05}
    tree = [
        _scene_image("世界", "0034.jpg", []),
        {
            "id": "popup",
            "type": "folder",
            "title": "弹窗",
            "children": [_scene_image("离开场景", "0086.png", [confirm])],
        },
    ]

    assert 86 in runner._scene_route_candidate_ids(tree, 34)


def test_scene_jump_confirms_leave_popup_before_replanning(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    leave_shape = {"id": "leave", "kind": "rect", "title": "离开", "x": 0.8, "y": 0.45, "w": 0.1, "h": 0.08, "sceneJumpTarget": "34"}
    confirm_shape = {
        "id": "confirm",
        "kind": "rect",
        "title": "确认",
        "isSceneIdentity": True,
        "x": 0.6,
        "y": 0.6,
        "w": 0.1,
        "h": 0.05,
    }
    image180 = _scene_image("挑战中", "0180.png", [leave_shape])
    image86 = _scene_image("离开场景", "0086.png", [confirm_shape])
    image34 = _scene_image("世界", "0034.png", [])
    tree = [
        image180,
        {"type": "folder", "title": "弹窗", "children": [image86]},
        image34,
    ]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": runner._index_images(tree)}
    frames = iter(["popup", *["world"] * 10])
    clicked: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def identify(_ctx, frame, preferred_scene_ids=None):
        if frame == "popup" and (preferred_scene_ids is None or 86 in preferred_scene_ids):
            return 86, 100.0
        if frame == "world" and (preferred_scene_ids is None or 34 in preferred_scene_ids):
            return 34, 100.0
        return None, 0.0

    def click_shape(_ctx, _image, shape, _frame=None, **_kwargs):
        clicked.append(str(shape.get("title") or ""))
        runner._clear_tick_frame(_ctx)

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(runner, "_identify_scene_number", identify)
    monkeypatch.setattr(runner, "_click_shape", click_shape)

    result = _drain_generator(runner._wait_scene_jump_result(
        ctx,
        path,
        tree,
        source_scene_id=180,
        target_scene_id=69,
        edge={"source_id": 180, "image": image180, "shape": leave_shape, "target_ids": [34]},
        stop_event=FakeStopEvent(),
    ))

    assert result == 34
    assert clicked == ["确认"]
    assert "observedLanding" not in leave_shape


def test_scene_jump_prioritizes_leave_confirmation_over_generic_popup_parent(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    leave_shape = {
        "id": "leave",
        "kind": "rect",
        "title": "离开",
        "x": 0.8,
        "y": 0.45,
        "w": 0.1,
        "h": 0.08,
        "sceneJumpTarget": "34",
    }
    generic_identity = {
        "id": "line",
        "kind": "rect",
        "title": "分割线",
        "isSceneIdentity": True,
        "x": 0.15,
        "y": 0.52,
        "w": 0.7,
        "h": 0.03,
    }
    generic_blank = {
        "id": "blank",
        "kind": "rect",
        "title": "空白",
        "x": 0.2,
        "y": 0.1,
        "w": 0.1,
        "h": 0.04,
    }
    confirm_shape = {
        "id": "confirm",
        "kind": "rect",
        "title": "确认",
        "x": 0.6,
        "y": 0.6,
        "w": 0.1,
        "h": 0.05,
    }
    confirm_identity = {
        "id": "confirm-identity",
        "kind": "rect",
        "title": "离开场景标识",
        "isSceneIdentity": True,
        "x": 0.35,
        "y": 0.44,
        "w": 0.35,
        "h": 0.04,
    }
    image180 = _scene_image("挑战中", "0180.png", [leave_shape])
    image47 = _scene_image("所有提示窗口", "0047.png", [generic_identity, generic_blank])
    image86 = _scene_image("离开场景", "0086.png", [confirm_shape, confirm_identity])
    image34 = _scene_image("世界", "0034.png", [])
    tree = [
        image180,
        {
            "type": "folder",
            "title": "弹窗",
            "children": [image47, image86],
        },
        image34,
    ]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {
        "entry": object(),
        "asset_tree": tree,
        "asset_tree_path": path,
        "images": runner._index_images(tree),
    }
    frames = iter(["popup", *["world"] * 10])
    clicked: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def identify(_ctx, frame, preferred_scene_ids=None):
        preferred = set(preferred_scene_ids or [])
        if frame == "popup":
            if 86 in preferred and 47 not in preferred:
                return 86, 100.0
            if 47 in preferred:
                return 47, 100.0
        if frame == "world" and (preferred_scene_ids is None or 34 in preferred):
            return 34, 100.0
        return None, 0.0

    def click_shape(_ctx, _image, shape, _frame=None, **_kwargs):
        clicked.append(str(shape.get("title") or ""))
        runner._clear_tick_frame(_ctx)

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(runner, "_identify_scene_number", identify)
    monkeypatch.setattr(runner, "_click_shape", click_shape)

    result = _drain_generator(runner._wait_scene_jump_result(
        ctx,
        path,
        tree,
        source_scene_id=180,
        target_scene_id=34,
        edge={"source_id": 180, "image": image180, "shape": leave_shape, "target_ids": [34]},
        stop_event=FakeStopEvent(),
        layer0_wait_seconds=0,
    ))

    assert result == 34
    assert clicked == ["确认"]


def test_scene_jump_20_to_34_unknown_does_not_borrow_target_scene_coordinates(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    back_shape = {"id": "back", "kind": "rect", "title": "回到世界", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1}
    blank_shape = {"id": "blank", "kind": "rect", "title": "空白", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1}
    image20 = _image("绿瓶", "0020.png", [back_shape])
    image34 = _image("世界", "0034.png", [blank_shape])
    tree = [image20, image34]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": {20: image20, 34: image34}}
    frames = iter(["unknown-popup", *["world"] * 10])
    clicked: list[tuple[str, float, float]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def identify(_ctx, frame, preferred_scene_ids=None):
        if frame == "world" and (preferred_scene_ids is None or 34 in preferred_scene_ids):
            return 34, 100.0
        return None, 0.0

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(runner, "_identify_scene_number", identify)
    monkeypatch.setattr(runner, "_save_action_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, image, x, y: clicked.append((image["title"], x, y)))

    result = _drain_generator(runner._wait_scene_jump_result(
        ctx,
        path,
        tree,
        source_scene_id=20,
        target_scene_id=34,
        edge={"source_id": 20, "image": image20, "shape": back_shape, "target_ids": [34]},
        stop_event=FakeStopEvent(),
        layer0_wait_seconds=0,
    ))

    assert result == 34
    assert clicked == []


def test_go_scene_replans_after_direct_entry_stalls_before_failing(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    entry_shape = {"id": "green", "kind": "rect", "title": "进入绿瓶", "x": 0.1, "y": 0.9, "w": 0.2, "h": 0.08, "sceneJumpTarget": "20"}
    blank_shape = {"id": "blank", "kind": "rect", "title": "空白", "x": 0.2, "y": 0.03, "w": 0.4, "h": 0.05}
    image34 = _scene_image("世界", "0034.png", [entry_shape, blank_shape])
    image20 = _scene_image("绿瓶", "0020.png", [])
    tree = [image34, image20]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": {34: image34, 20: image20}}
    clicked: list[str] = []
    saved: dict[str, object] = {}

    class FakeStopEvent:
        def is_set(self):
            return False

    def wait_stays_source(*_args, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return 34

    def save_unknown(_ctx, _path, _tree, _frame, **kwargs):
        saved.update(kwargs)
        raise RuntimeError("direct entry stalled")

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "world")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, preferred_scene_ids=None, trace=None: (34, 100.0))
    monkeypatch.setattr(runner, "_wait_scene_jump_result", wait_stays_source)
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(
        runner,
        "_click_scene_route_shape",
        lambda _ctx, _image, shape, _frame, *, jitter_radius=0: clicked.append(shape["title"]),
    )
    monkeypatch.setattr(runner, "_save_unknown_scene_frame", save_unknown)

    with pytest.raises(RuntimeError, match="direct entry stalled"):
        _drain_generator(runner._go_scene_task(ctx, path, 20, FakeStopEvent()))

    assert clicked == ["进入绿瓶"] * 10 + ["空白"]
    assert saved["current_scene_id"] == 34
    assert saved["action_shape"] is blank_shape
    assert "候选" in saved["history"][0]


def test_go_scene_bounds_same_stalled_edge_across_animated_visual_states(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    entry_shape = {
        "id": "green",
        "kind": "rect",
        "title": "进入绿瓶",
        "x": 0.1,
        "y": 0.9,
        "w": 0.2,
        "h": 0.08,
        "sceneJumpTarget": "20",
    }
    blank_shape = {
        "id": "blank",
        "kind": "rect",
        "title": "空白",
        "x": 0.2,
        "y": 0.03,
        "w": 0.4,
        "h": 0.05,
    }
    image34 = _scene_image("世界", "0034.png", [entry_shape, blank_shape])
    image20 = _scene_image("绿瓶", "0020.png", [])
    tree = [image34, image20]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {
        "entry": object(),
        "asset_tree": tree,
        "asset_tree_path": path,
        "images": {34: image34, 20: image20},
    }
    frame_index = {"value": 0}
    clicked: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

    def screencap(_ctx):
        frame_index["value"] += 1
        return f"animated-world-{frame_index['value']}"

    def wait_stays_source(*_args, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return 34

    jitter_radii: list[int] = []

    def click_shape(_ctx, _image, shape, _frame, *, jitter_radius=0):
        clicked.append(shape["title"])
        jitter_radii.append(jitter_radius)
        if shape["title"] == "进入绿瓶" and clicked.count("进入绿瓶") > 10:
            raise AssertionError("同一语义导航边不应因背景动画被无限重试")
        if shape["title"] == "空白":
            raise RuntimeError("已切换到其他候选")

    monkeypatch.setattr(runner, "_screencap", screencap)
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, _frame, preferred_scene_ids=None, trace=None: (34, 100.0),
    )
    monkeypatch.setattr(runner, "_navigation_scene_id", lambda _ctx, current, _frame: current)
    monkeypatch.setattr(runner, "_wait_scene_jump_result", wait_stays_source)
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_click_scene_route_shape", click_shape)

    with pytest.raises(RuntimeError, match="已切换到其他候选"):
        _drain_generator(runner._go_scene_task(ctx, path, 20, FakeStopEvent()))

    assert clicked == ["进入绿瓶"] * 10 + ["空白"]
    assert jitter_radii == [0, 1, 2, 4, 8, 16, 32, 64, 114, 164, 0]


def test_go_scene_marks_stable_repeated_self_loop_for_human_review(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    entry_shape = {
        "id": "green",
        "kind": "rect",
        "title": "进入绿瓶",
        "sceneJumpTarget": "20",
    }
    blank_shape = {"id": "blank", "kind": "rect", "title": "空白"}
    image34 = _scene_image("世界", "0034.png", [entry_shape, blank_shape])
    image20 = _scene_image("绿瓶", "0020.png", [])
    tree = [image34, image20]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {
        "entry": object(),
        "entry_id": "entry-test",
        "asset_tree": tree,
        "asset_tree_path": path,
        "images": {34: image34, 20: image20},
    }
    triggers: list[dict[str, object]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRecorder:
        active = False
        fallback_used = False

        def __init__(self, *_args, **_kwargs):
            pass

        def elapsed_seconds(self):
            return 0.0

        def record_action(self, **_kwargs):
            pass

        def trigger(self, **kwargs):
            triggers.append(kwargs)
            self.active = True

        def mark_fallback_used(self):
            self.fallback_used = True

        def finalize(self, **_kwargs):
            pass

    def wait_stays_source(*_args, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        ctx["_last_scene_jump_evidence"] = {
            "scene_id": 34,
            "score": 100.0,
            "frame_data_url": "stable-world",
        }
        return 34

    def click_shape(_ctx, _image, shape, _frame, *, jitter_radius=0):
        if shape["title"] == "空白":
            raise RuntimeError("stop after stall")

    monkeypatch.setattr(behavior_tree_runtime_core, "NavigationIncidentRecorder", FakeRecorder)
    monkeypatch.setattr(behavior_tree_runtime_core, "_image_similarity_percent", lambda *_args, **_kwargs: 100.0)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "stable-world")
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, _frame, preferred_scene_ids=None, trace=None: (34, 100.0),
    )
    monkeypatch.setattr(runner, "_wait_scene_jump_result", wait_stays_source)
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_click_scene_route_shape", click_shape)

    with pytest.raises(RuntimeError, match="stop after stall"):
        _drain_generator(runner._go_scene_task(ctx, path, 20, FakeStopEvent()))

    stable_trigger = next(item for item in triggers if item["trigger_type"] == "stable_self_loop")
    assert stable_trigger["threshold"]["state_attempts"] == 2
    assert stable_trigger["threshold"]["frame_similarity"] == 100.0


def test_go_scene_known_navigation_dead_end_never_projects_424(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    forward_shape = {
        "id": "forward",
        "kind": "rect",
        "title": "前往",
        "sceneJumpTarget": "420(9),327(6),186(1),418(1)",
        "x": 0.4,
        "y": 0.7,
        "w": 0.2,
        "h": 0.1,
    }
    fallback_return = {"id": "fallback-return", "kind": "rect", "title": "返回", "x": 0.05, "y": 0.94, "w": 0.08, "h": 0.04}
    image418 = _scene_image("怪物预览", "0418.png", [forward_shape])
    image34 = _scene_image("世界", "0034.png", [])
    image424 = _scene_image("通用动作素材", "0424.png", [fallback_return])
    tree = [image418, image34, image424]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": runner._index_images(tree)}
    state = {"frame": "scene-418"}
    clicked: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

    def identify(_ctx, frame, preferred_scene_ids=None, trace=None):
        return {"scene-418": (418, 100.0), "scene-34": (34, 100.0)}[frame]

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: state["frame"])
    monkeypatch.setattr(runner, "_identify_scene_number", identify)
    monkeypatch.setattr(runner, "_navigation_scene_id", lambda _ctx, current, _frame: current)
    monkeypatch.setattr(runner, "_save_action_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))

    def click_fallback(*_args, **_kwargs):
        clicked.append("fallback:返回")
        state["frame"] = "scene-34"

    monkeypatch.setattr(runner, "_click_frame_point", click_fallback)

    with pytest.raises(RuntimeError, match="无法从当前#418找到可达#34的路径"):
        _drain_generator(runner._go_scene_task(ctx, path, 34, FakeStopEvent()))
    assert clicked == []


def test_go_scene_unknown_transition_guard_waits_instead_of_clicking_424(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _scene_image("世界", "0034.png", [])
    image314 = _scene_image("魔道过场参考", "0314.png", [])
    tree = [image34, image314]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {
        "entry": object(),
        "asset_tree": tree,
        "asset_tree_path": path,
        "images": runner._index_images(tree),
        "_go_scene_unknown_transition_guard": {
            "reference_scene_id": 314,
            "similarity_threshold": 94.0,
            "wait_seconds": 120.0,
            "phase": "daily_boss_wait_mozu_world_transition",
            "label": "魔道入侵回城动画",
        },
    }
    recognition_calls: list[dict] = []

    def guarded_recognition(*_args, **kwargs):
        recognition_calls.append(dict(kwargs))
        if False:
            yield None
        return 34, 100.0, "world", "matched"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "transition")
    monkeypatch.setattr(runner, "_scene_reference_similarity", lambda *_args, **_kwargs: 96.0)
    monkeypatch.setattr(runner, "_wait_for_go_scene_recognition", guarded_recognition)
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda *_args, **_kwargs: (34, 100.0),
    )

    assert _drain_generator(runner._go_scene_task(ctx, path, 34, threading.Event())) == "success"
    assert recognition_calls == [{
        "wait_seconds": 120.0,
        "max_wait_seconds": 120.0,
        "immediate_unknown_fallback": False,
    }]
    assert ctx["_go_scene_unknown_transition_guard"]["announced"] is True


def test_go_scene_468_uses_its_formal_return_then_formal_467_return(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    detail_return = {
        "id": "zhenwuge-detail-return",
        "kind": "rect",
        "title": "返回",
        "sceneJumpTarget": "467",
        "x": 0.05,
        "y": 0.94,
        "w": 0.08,
        "h": 0.04,
    }
    formal_return = {
        "id": "zhenwuge-return",
        "kind": "rect",
        "title": "返回",
        "sceneJumpTarget": "34",
        "x": 0.05,
        "y": 0.94,
        "w": 0.08,
        "h": 0.04,
    }
    image34 = _scene_image("世界", "0034.png", [])
    image467 = _scene_image("珍宝阁", "0467.png", [formal_return])
    image468 = _scene_image("珍宝阁详情", "0468.png", [detail_return])
    tree = [image34, image467, image468]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {
        "entry": object(),
        "asset_tree": tree,
        "asset_tree_path": path,
        "images": runner._index_images(tree),
    }
    state = {"scene": 468}
    clicked = []

    class FakeStopEvent:
        def is_set(self):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: f"scene-{state['scene']}")
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, frame, preferred_scene_ids=None, trace=None: (int(frame.split("-")[1]), 100.0),
    )
    monkeypatch.setattr(runner, "_navigation_scene_id", lambda _ctx, current, _frame: current)
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_save_action_trace", lambda *_args, **_kwargs: None)

    def click_shape(_ctx, image, shape, _frame, *, jitter_radius=0):
        clicked.append((image["filename"], shape["title"]))

    def wait_landing(_ctx, _path, _tree, *, source_scene_id, **_kwargs):
        state["scene"] = 467 if source_scene_id == 468 else 34
        _ctx["_last_scene_jump_evidence"] = {
            "scene_id": state["scene"],
            "score": 100.0,
            "frame_data_url": f"scene-{state['scene']}",
        }
        if False:
            yield None
        return state["scene"]

    monkeypatch.setattr(runner, "_click_scene_route_shape", click_shape)
    monkeypatch.setattr(runner, "_wait_scene_jump_result", wait_landing)

    assert _drain_generator(runner._go_scene_task(ctx, path, 34, FakeStopEvent())) == "success"
    assert clicked == [("0468.png", "返回"), ("0467.png", "返回")]


def test_go_scene_layer1_missing_route_refuses_blank_and_424(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    blank_shape = {"id": "blank", "kind": "rect", "title": "空白", "x": 0.2, "y": 0.03, "w": 0.4, "h": 0.05}
    fallback_return = {"id": "fallback-return", "kind": "rect", "title": "返回", "x": 0.05, "y": 0.94, "w": 0.08, "h": 0.04}
    image34 = _scene_image("世界", "0034.png", [blank_shape], layer=1)
    image247 = _scene_image("仙市入口", "0247.png", [], layer=2)
    image424 = _scene_image("通用动作素材", "0424.png", [fallback_return])
    tree = [image34, image247, image424]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": runner._index_images(tree)}

    class FakeStopEvent:
        def is_set(self):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "scene-34")
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, _frame, preferred_scene_ids=None, trace=None: (34, 100.0),
    )
    monkeypatch.setattr(
        runner,
        "_click_frame_point",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Layer 1 缺路径不得点击")),
    )
    monkeypatch.setattr(
        runner,
        "_click_scene_route_shape",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Layer 1 缺路径不得探索空白")),
    )

    with pytest.raises(RuntimeError, match="Layer 1 枢纽"):
        _drain_generator(runner._go_scene_task(ctx, path, 247, FakeStopEvent()))


def test_go_scene_non_world_target_retries_recognition_before_424_fallback(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    fallback_return = {"id": "fallback-return", "kind": "rect", "title": "返回", "x": 0.05, "y": 0.94, "w": 0.08, "h": 0.04}
    image171 = _scene_image("寻仙台", "0171.png", [])
    image424 = _scene_image("通用动作素材", "0424.png", [fallback_return])
    tree = [image171, image424]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": {171: image171, 424: image424}}
    state = {"frame": "transition", "now": 100.0}

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, seconds):
            state["now"] += float(seconds)
            state["frame"] = "scene-171"
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: state["frame"])
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, frame, *_args, **_kwargs: (171, 100.0) if frame == "scene-171" else (None, 0.0),
    )
    monkeypatch.setattr(runner, "_identify_scene_number_for_route", lambda *_args, **_kwargs: (None, 0.0))
    monkeypatch.setattr(
        runner,
        "_click_frame_point",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("过渡帧恢复后不得点击 #424")),
    )
    monkeypatch.setattr(behavior_tree_runtime_core.time, "monotonic", lambda: state["now"])

    assert _drain_generator(runner._go_scene_task(ctx, path, 171, FakeStopEvent())) == "success"
    assert state["now"] == pytest.approx(102.0)


def test_go_scene_ambiguous_without_scene_id_remains_unknown_for_424(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    fallback_return = {"id": "fallback-return", "kind": "rect", "title": "返回", "x": 0.05, "y": 0.94, "w": 0.08, "h": 0.04}
    image34 = _scene_image("世界", "0034.png", [])
    image424 = _scene_image("通用动作素材", "0424.png", [fallback_return])
    tree = [image34, image424]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": {34: image34, 424: image424}}
    state = {"now": 100.0}
    clicked: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, seconds):
            state["now"] += float(seconds)
            return False

    def identify(context, _frame, *_args, **_kwargs):
        if state.get("frame") == "scene-34":
            context["_last_scene_recognition_status"] = "graph_nearest"
            return 34, 100.0
        context["_last_scene_recognition_status"] = "ambiguous"
        return None, 94.0

    def click_fallback(*_args, **_kwargs):
        clicked.append("返回")
        state["frame"] = "scene-34"

    state["frame"] = "ambiguous-frame"
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: state["frame"])
    monkeypatch.setattr(runner, "_identify_scene_number", identify)
    monkeypatch.setattr(runner, "_identify_scene_number_for_route", lambda *_args, **_kwargs: (None, 0.0))
    monkeypatch.setattr(
        runner,
        "_match_shape",
        lambda _ctx, image, shape, *_args, **_kwargs: {
            "matched": True,
            "similarity": 100.0,
            "box": runner._box(shape, image),
            "resolved_box": runner._box(shape, image),
            "matches": [{}],
        },
    )
    monkeypatch.setattr(runner, "_click_frame_point", click_fallback)
    monkeypatch.setattr(runner, "_save_action_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_runtime_core.time, "monotonic", lambda: state["now"])

    assert _drain_generator(runner._go_scene_task(ctx, path, 34, FakeStopEvent())) == "success"
    assert state["now"] >= 160.0
    assert clicked == ["返回"]


def test_go_scene_unknown_to_world_uses_424_return_immediately(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    fallback_return = {"id": "fallback-return", "kind": "rect", "title": "返回", "x": 0.05, "y": 0.94, "w": 0.08, "h": 0.04}
    image34 = _scene_image("世界", "0034.png", [])
    image424 = _scene_image("通用动作素材", "0424.png", [fallback_return])
    tree = [image34, image424]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": {34: image34, 424: image424}}
    state = {"frame": "unknown-a", "now": 100.0}
    clicked: list[str] = []
    clicked_at: list[float] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, seconds):
            state["now"] += float(seconds)
            return False

    def click_fallback(_ctx, image, _x, _y, **_kwargs):
        clicked.append(f"fallback:{image['filename']}:返回")
        clicked_at.append(state["now"])
        if len(clicked) >= 2:
            state["frame"] = "scene-34"

    def no_side_leave(*_args, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: state["frame"])
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, frame, *_args, **_kwargs: (34, 100.0) if frame == "scene-34" else (None, 0.0),
    )
    monkeypatch.setattr(runner, "_identify_scene_number_for_route", lambda *_args, **_kwargs: (None, 0.0))
    monkeypatch.setattr(
        runner,
        "_match_shape",
        lambda _ctx, image, shape, *_args, **_kwargs: {
            "matched": True,
            "similarity": 100.0,
            "box": runner._box(shape, image),
            "resolved_box": runner._box(shape, image),
            "matches": [{}],
        },
    )
    monkeypatch.setattr(
        runner,
        "_leave_world_side_scene_if_present",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("#424 仍可推进时不应先做 unknown 业务分型")
        ),
    )
    monkeypatch.setattr(runner, "_click_frame_point", click_fallback)
    monkeypatch.setattr(runner, "_save_action_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_runtime_core.time, "monotonic", lambda: state["now"])

    assert _drain_generator(runner._go_scene_task(ctx, path, 34, FakeStopEvent())) == "success"
    assert clicked == ["fallback:0424.png:返回", "fallback:0424.png:返回"]
    assert clicked_at[0] == pytest.approx(100.0)
    assert clicked_at[1] - clicked_at[0] < 5.0


def test_unknown_424_return_consumes_qualified_attempt_without_recapturing(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    fallback_return = {"id": "fallback-return", "kind": "rect", "title": "返回", "x": 0.05, "y": 0.94, "w": 0.08, "h": 0.04}
    image424 = _scene_image("通用动作素材", "0424.png", [fallback_return])
    ctx = {"images": {424: image424}}
    attempts: dict[tuple[str, str], dict[str, float | int]] = {}
    clicked: list[str] = []

    traced_frames: list[str] = []

    def click(_ctx, _image, _x, _y, *, save_action_trace=True):
        assert save_action_trace is False
        clicked.append("返回")

    monkeypatch.setattr(runner, "_click_frame_point", click)
    monkeypatch.setattr(
        runner,
        "_match_shape",
        lambda _ctx, image, shape, *_args, **_kwargs: {
            "matched": True,
            "similarity": 100.0,
            "box": runner._box(shape, image),
            "resolved_box": {**runner._box(shape, image), "y": runner._box(shape, image)["y"] - 48},
            "matches": [{}],
        },
    )
    monkeypatch.setattr(
        runner,
        "_save_action_trace",
        lambda *_args, frame_data_url=None, **_kwargs: traced_frames.append(frame_data_url),
    )

    first = runner._try_navigation_fallback_return(
        ctx,
        "unknown-frame",
        navigation_state_key="unknown:state:1",
        target_scene_id=34,
        attempted_actions=attempts,
    )

    assert first.status == "clicked"
    assert clicked == ["返回"]
    assert traced_frames == ["unknown-frame"]
    assert first.point is not None
    assert first.point[1] < 0.96 * 1600


def test_unknown_424_return_without_unique_visual_match_does_not_click(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    fallback_return = {"id": "fallback-return", "kind": "rect", "title": "返回", "x": 0.05, "y": 0.94, "w": 0.08, "h": 0.04}
    image424 = _scene_image("通用动作素材", "0424.png", [fallback_return])
    clicked: list[str] = []
    attempts: dict[tuple[str, str], dict[str, float | int]] = {}
    monkeypatch.setattr(
        runner,
        "_match_shape",
        lambda *_args, **_kwargs: {
            "matched": False,
            "similarity": 86.0,
            "matches": [{}, {}],
            "reason": "floating_image_not_unique",
        },
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: clicked.append("返回"))

    decision = runner._try_navigation_fallback_return(
        {"images": {424: image424}},
        "unknown-frame-without-return",
        navigation_state_key="unknown:state:1",
        target_scene_id=34,
        attempted_actions=attempts,
    )

    assert decision.status == "unavailable"
    assert decision.attempt == 0
    assert clicked == []
    assert attempts[("__continuous_unknown__", "fallback_return")]["count"] == 0


def test_unknown_424_return_rejects_any_known_scene(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    fallback_return = {"id": "fallback-return", "kind": "rect", "title": "返回", "x": 0.05, "y": 0.94, "w": 0.08, "h": 0.04}
    image424 = _scene_image("通用动作素材", "0424.png", [fallback_return])
    ctx = {"images": {424: image424}}
    attempts: dict[tuple[str, str], dict[str, float | int]] = {}
    clicked: list[str] = []
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: clicked.append("返回"))
    monkeypatch.setattr(runner, "_save_action_trace", lambda *_args, **_kwargs: None)

    decision = runner._try_navigation_fallback_return(
        ctx,
        "scene-185",
        navigation_state_key="unknown:state:1",
        target_scene_id=171,
        attempted_actions=attempts,
        current_scene_id=185,
        current_score=100.0,
    )

    assert decision.status == "unavailable"
    assert clicked == []


def test_424_return_allows_611_promotion_overlay_only_when_returning_world(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    fallback_return = {
        "id": "fallback-return",
        "kind": "rect",
        "title": "返回",
        "x": 0.05,
        "y": 0.94,
        "w": 0.08,
        "h": 0.04,
    }
    image424 = _scene_image("通用动作素材", "0424.png", [fallback_return])
    ctx = {"images": {424: image424}}
    clicked: list[str] = []
    monkeypatch.setattr(
        runner,
        "_click_frame_point",
        lambda *_args, **_kwargs: clicked.append("返回"),
    )
    monkeypatch.setattr(
        runner,
        "_match_shape",
        lambda _ctx, image, shape, *_args, **_kwargs: {
            "matched": True,
            "similarity": 100.0,
            "box": runner._box(shape, image),
            "resolved_box": runner._box(shape, image),
            "matches": [{}],
        },
    )
    monkeypatch.setattr(runner, "_save_action_trace", lambda *_args, **_kwargs: None)

    allowed = runner._try_navigation_fallback_return(
        ctx,
        "scene-611",
        navigation_state_key="611:state:1",
        target_scene_id=34,
        attempted_actions={},
        current_scene_id=611,
        current_score=100.0,
    )
    blocked = runner._try_navigation_fallback_return(
        ctx,
        "scene-611",
        navigation_state_key="611:state:2",
        target_scene_id=614,
        attempted_actions={},
        current_scene_id=611,
        current_score=100.0,
    )

    assert allowed.status == "clicked"
    assert blocked.status == "unavailable"
    assert clicked == ["返回"]


def test_unknown_424_return_stops_after_bounded_navigation_attempts(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    fallback_return = {"id": "fallback-return", "kind": "rect", "title": "返回", "x": 0.05, "y": 0.94, "w": 0.08, "h": 0.04}
    image424 = _scene_image("通用动作素材", "0424.png", [fallback_return])
    ctx = {"images": {424: image424}}
    attempts: dict[tuple[str, str], dict[str, float | int]] = {}
    clicked: list[str] = []
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: clicked.append("返回"))
    monkeypatch.setattr(
        runner,
        "_match_shape",
        lambda _ctx, image, shape, *_args, **_kwargs: {
            "matched": True,
            "similarity": 100.0,
            "box": runner._box(shape, image),
            "resolved_box": runner._box(shape, image),
            "matches": [{}],
        },
    )
    monkeypatch.setattr(runner, "_save_action_trace", lambda *_args, **_kwargs: None)

    decisions = [
        runner._try_navigation_fallback_return(
            ctx,
            "unknown-frame",
            navigation_state_key="unknown:state:1",
            target_scene_id=34,
            attempted_actions=attempts,
        )
        for index in range(5)
    ]

    assert [decision.status for decision in decisions] == [
        "clicked",
        "clicked",
        "clicked",
        "clicked",
        "exhausted",
    ]
    assert clicked == ["返回"] * 4


def test_lingmai_clear_treats_return_to_313_after_transient_as_one_round_complete(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    scheduled = []
    monkeypatch.setattr(runner, "_persist_scheduler_task_next_time", lambda task_id, next_time: scheduled.append((task_id, next_time)))

    class FakeRuntime:
        def wait_click_then_view(self, *_args, **_kwargs):
            if False:
                yield BehaviorTreeStatus.RUNNING
            return 313

    result = _drain_generator(runner._continue_daily_lingmai_clear_from_transient(
        FakeRuntime(),
        {},
        task_label="灵脉_清体力",
    ))

    assert result == "success"
    assert scheduled and scheduled[0][0] == "legacy-daily-lingmai-clear"
    assert any("固定一轮探索" in item["message"] for item in runner.status()["logs"])


def test_lingmai_clear_treats_direct_return_to_313_as_one_round_complete(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    actions: list[str] = []
    monkeypatch.setattr(runner, "_persist_scheduler_task_next_time", lambda task_id, next_time: actions.append(f"next:{task_id}:{next_time}"))

    class FakeRuntime:
        def drag_shape_to_frame_edge(self, *_args, **_kwargs):
            actions.append("drag")

        def wait_action_settle(self, *_args, **_kwargs):
            if False:
                yield BehaviorTreeStatus.RUNNING

        def wait_click_then_view(self, *_args, **_kwargs):
            actions.append("confirm")
            if False:
                yield BehaviorTreeStatus.RUNNING
            return 313

    result = _drain_generator(runner._continue_daily_lingmai_clear_from_amount(
        FakeRuntime(),
        {},
        task_label="灵脉_清体力",
    ))

    assert result == "success"
    assert actions[:2] == ["drag", "confirm"]
    assert actions[2].startswith("next:legacy-daily-lingmai-clear:")


def test_lingmai_clear_skips_when_available_stamina_is_below_single_run_cost(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    actions: list[str] = []
    monkeypatch.setattr(runner, "_persist_scheduler_task_next_time", lambda task_id, next_time: actions.append(f"next:{task_id}:{next_time}"))

    class FakeRuntime:
        def cur_frame(self, *, update=False):
            return "scene-313"

        def ocr_text_in_shapes(self, view, shapes, *, frame_data_url=None):
            assert view == 313
            assert shapes == ("体力",)
            assert frame_data_url == "scene-313"
            return "30/11"

        def shape_score(self, *_args, **_kwargs):
            raise AssertionError("体力不足时不应继续检查或点击一键探索")

        def goto_view(self, target):
            actions.append(f"goto:{target}")
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "success"

    result = _drain_generator(runner._continue_daily_lingmai_clear_from_explore(
        FakeRuntime(),
        {},
        task_label="灵脉_清体力",
    ))

    assert result == "success"
    assert actions[0] == "goto:34"
    assert actions[1].startswith("next:legacy-daily-lingmai-clear:")
    assert any("现有体力 11 小于单次消耗 30" in item["message"] for item in runner.status()["logs"])


def test_lingmai_clear_stamina_parser_uses_cost_then_available_order():
    runner = create_behavior_tree_runtime_runner()

    assert runner._parse_daily_lingmai_clear_stamina("30/1113") == (30, 1113)
    assert runner._parse_daily_lingmai_clear_stamina("30／11") == (30, 11)
    assert runner._parse_daily_lingmai_clear_stamina("识别失败") is None


def test_lingmai_clear_guiyuan_skips_upgrade_when_resource_is_insufficient():
    runner = create_behavior_tree_runtime_runner()
    actions: list[tuple[int, str, object]] = []

    class FakeRuntime:
        def wait_click_then_view(self, scene, shape, target, **_kwargs):
            actions.append((scene, shape, target))
            if False:
                yield BehaviorTreeStatus.RUNNING
            return target

        def cur_frame(self, *, update=False):
            return "scene-589"

        def ocr_text_in_shapes(self, scene, shapes, *, frame_data_url=None):
            assert (scene, shapes, frame_data_url) == (589, ("凝神资源",), "scene-589")
            return "8542/15000"

    result = _drain_generator(runner._check_daily_lingmai_guiyuan_upgrade(
        FakeRuntime(),
        {},
        task_label="灵脉_清体力",
    ))

    assert result == "not_upgradable"
    assert actions == [(285, "归元凝神", 589), (589, "返回", 285)]


def test_lingmai_clear_guiyuan_can_resume_from_already_open_page():
    runner = create_behavior_tree_runtime_runner()
    actions = []

    class Runtime:
        def wait_click_then_view(self, scene, shape, target, **kwargs):
            actions.append((scene, shape, target))
            if False:
                yield None

        def cur_frame(self, *, update=False):
            assert update is True
            return "scene-589"

        def ocr_text_in_shapes(self, scene, shapes, *, frame_data_url):
            assert (scene, shapes, frame_data_url) == (589, ("凝神资源",), "scene-589")
            return "17095/45000"

    result = _drain_generator(runner._check_daily_lingmai_guiyuan_upgrade(
        Runtime(),
        {},
        task_label="灵脉_清体力",
        already_open=True,
    ))

    assert result == "not_upgradable"
    assert actions == [(589, "返回", 285)]


def test_lingmai_clear_guiyuan_upgrades_once_and_verifies_resource_delta():
    runner = create_behavior_tree_runtime_runner()
    actions: list[tuple[int, str, object]] = []
    resource_texts = iter(("23542/15000", "8542/15000"))

    class FakeRuntime:
        def wait_click_then_view(self, scene, shape, target, **_kwargs):
            actions.append((scene, shape, target))
            if False:
                yield BehaviorTreeStatus.RUNNING
            return target

        def cur_frame(self, *, update=False):
            return "scene-589"

        def ocr_text_in_shapes(self, scene, shapes, *, frame_data_url=None):
            assert (scene, shapes, frame_data_url) == (589, ("凝神资源",), "scene-589")
            return next(resource_texts)

    result = _drain_generator(runner._check_daily_lingmai_guiyuan_upgrade(
        FakeRuntime(),
        {},
        task_label="灵脉_清体力",
    ))

    assert result == "upgraded"
    assert actions == [
        (285, "归元凝神", 589),
        (589, "凝神", 346),
        (346, "继续", 589),
        (589, "返回", 285),
    ]


def test_safe_daily_done_cleanup_restores_non_error_runtime_status():
    runner = create_behavior_tree_runtime_runner()
    with runner._lock:
        runner._set_status_locked(
            "running",
            "日常_首领：本轮已经结算",
            phase="daily_boss_done_by_runtime_delta",
            current_scene=186,
        )

    def failed_cleanup():
        with runner._lock:
            runner._set_status_locked(
                "error",
                "cleanup navigation failed",
                phase="error",
                current_scene=None,
            )
        raise RuntimeError("transient transition frame")
        yield  # pragma: no cover

    result = _drain_generator(
        runner._safe_daily_done_cleanup(
            failed_cleanup,
            label="日常_首领",
            repeat_risk="重复挑战",
        )
    )

    assert result == "success"
    assert runner._status["status"] == "running"
    assert runner._status["phase"] == "daily_boss_done_by_runtime_delta"
    assert runner._status["message"] == "日常_首领：本轮已经结算"


def test_go_scene_fails_fast_when_world_target_is_below_scene_threshold(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _scene_image("世界", "0034.png", [
        {"id": "world-id", "kind": "rect", "title": "大地图", "sceneIdentityRole": "required"},
    ], layer=1)
    tree = [image34]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": {34: image34}}
    saved: dict[str, object] = {}
    state = {"now": 100.0}

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, seconds):
            state["now"] += float(seconds)
            return False

    def save_unknown(_ctx, _path, _tree, _frame, **kwargs):
        saved.update(kwargs)
        raise RuntimeError("below threshold")

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "big-map")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, preferred_scene_ids=None, trace=None: (None, 0.0))
    monkeypatch.setattr(runner, "_identify_scene_number_for_route", lambda *_args, **_kwargs: (34, 77.0))
    monkeypatch.setattr(runner, "_save_unknown_scene_frame", save_unknown)
    monkeypatch.setattr(behavior_tree_runtime_core.time, "monotonic", lambda: state["now"])

    with pytest.raises(RuntimeError, match="below threshold"):
        _drain_generator(runner._go_scene_task(ctx, path, 34, FakeStopEvent()))

    assert saved["target_scene_id"] == 34
    assert saved["current_scene_id"] is None
    # An unguarded return-to-world must not hold the Runtime for a full minute
    # on a weak route fallback.  It records the unknown frame immediately and
    # still refuses to claim target success.
    assert state["now"] == pytest.approx(100.0)


def test_auto_close_guard_candidates_are_cached_by_asset_tree_signature(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
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


def test_runtime_wait_view_timeout_uses_bounded_unknown_evidence(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image("邮件", "0121.png", [])
    ctx = {"entry": object(), "images": {121: image121}}
    runtime = runner._fanxiu_runtime(ctx, frame_data_url="frame")
    evidence_calls: list[dict[str, Any]] = []

    class Evidence:
        classification = "hard_unknown"
        suggestion = "bounded"
        frame_path = None
        report_path = None

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (None, 0.0))
    monkeypatch.setattr(
        behavior_tree_runtime_core,
        "build_unknown_evidence",
        lambda *_args, **kwargs: evidence_calls.append(dict(kwargs)) or Evidence(),
    )

    try:
        runner._run_direct_runtime_action(
            lambda: runtime.wait_view(121, timeout=0.01, label="测试等待"),
            stop_event=threading.Event(),
            tick_seconds=0.02,
            max_runtime_seconds=1,
        )
    except TimeoutError as exc:
        assert "测试等待 超时" in str(exc)
    else:
        raise AssertionError("wait_view should timeout even when popup guard keeps handling")

    assert evidence_calls
    assert evidence_calls[-1]["candidate_scene_ids"] == [121]


def test_popup_guard_parallel_scoring_checks_all_candidates_in_one_tick(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    candidates = [{"image": _image(f"图{index}", f"{index:04d}.jpg")} for index in range(12)]
    seen: list[str] = []

    def score(_ctx, image, _frame):
        seen.append(image["title"])
        return 90 if image["title"] == "图1" else 0

    monkeypatch.setattr(runner, "_scene_score", score)

    candidate, score_value = runner._auto_close_popup_graph_match({"entry": object()}, candidates, "frame")

    assert candidate is not None
    assert candidate["image"]["title"] == "图1"
    assert score_value == 90
    assert set(seen) == {f"图{index}" for index in range(12)}


def test_popup_guard_parallel_scoring_uses_one_worker_per_candidate_up_to_cap(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    candidates = [{"image": _image(f"图{index}", f"{index:04d}.jpg")} for index in range(12)]
    created_workers: list[int] = []
    real_executor = popup_guard_core.ThreadPoolExecutor

    class SpyExecutor(real_executor):
        def __init__(self, *args, **kwargs):
            created_workers.append(int(kwargs.get("max_workers") or args[0]))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(popup_guard_core, "ThreadPoolExecutor", SpyExecutor)
    monkeypatch.setattr(runner, "_scene_score", lambda _ctx, image, _frame: 90 if image["title"] == "图11" else 0)

    scores = runner._auto_close_popup_candidate_scores_parallel({"entry": object()}, candidates, "frame")

    assert created_workers == [len(candidates)]
    assert scores == [0] * 11 + [90]


def test_popup_guard_parallel_scoring_caps_worker_count(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    candidates = [{"image": _image(f"图{index}", f"{index:04d}.jpg")} for index in range(40)]
    created_workers: list[int] = []
    real_executor = popup_guard_core.ThreadPoolExecutor

    class SpyExecutor(real_executor):
        def __init__(self, *args, **kwargs):
            created_workers.append(int(kwargs.get("max_workers") or args[0]))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(popup_guard_core, "ThreadPoolExecutor", SpyExecutor)
    monkeypatch.setattr(runner, "_scene_score", lambda _ctx, image, _frame: 90 if image["title"] == "图39" else 0)

    scores = runner._auto_close_popup_candidate_scores_parallel({"entry": object()}, candidates, "frame")

    assert created_workers == [32]
    assert scores == [0] * 39 + [90]


def test_popup_guard_parallel_scoring_reuses_one_full_frame_ocr(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    candidates = [
        {
            "image": _image(
                f"图{index}",
                f"{index:04d}.jpg",
                [
                    {
                        "id": f"title-{index}",
                        "kind": "rect",
                        "title": f"标题{index}",
                        "isSceneIdentity": True,
                        "imageMatchRole": "off",
                        "ocrEnabled": True,
                        "ocrText": "目标弹窗" if index == 2 else f"其他{index}",
                        "ocrMatchRole": "required",
                        "x": 0.1,
                        "y": 0.1,
                        "w": 0.2,
                        "h": 0.05,
                    }
                ],
            )
        }
        for index in range(3)
    ]
    ocr_calls: list[str] = []
    run_match_calls: list[tuple[str, bool]] = []

    def fake_ocr_frame(frame, **_kwargs):
        ocr_calls.append(frame)
        return {"tokens": _ocr_tokens("目标弹窗", x=100, y=170, w=120, h=30)}

    monkeypatch.setattr(runner, "_ocr_frame", fake_ocr_frame)
    def fake_run_match(_ctx, image, _shape, _frame, **kwargs):
        ocr_enabled = bool(kwargs.get("ocr_enabled"))
        run_match_calls.append((image["title"], ocr_enabled))
        if image["title"] == "图2" and ocr_enabled:
            return {
                "similarity": 100,
                "matches": [{"text": "目标弹窗", "x": 100, "y": 170, "w": 120, "h": 30}],
                "fixed_box": {"x": 100, "y": 170, "w": 120, "h": 30},
            }
        return {"similarity": 0, "matches": []}

    monkeypatch.setattr(runner, "_run_match", fake_run_match)

    scores = runner._auto_close_popup_candidate_scores_parallel({"entry": object()}, candidates, "frame")

    assert scores == [0.0, 0.0, 100.0]
    assert ocr_calls == ["frame"]
    assert run_match_calls == []


def test_popup_guard_graph_match_scores_candidates_once(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    candidates = [{"image": _image(f"图{index}", f"{index:04d}.jpg")} for index in range(3)]
    calls: list[int] = []

    def fake_scores(_ctx, score_candidates, _frame):
        calls.append(len(score_candidates))
        return [0.0, 100.0, 90.0]

    monkeypatch.setattr(runner, "_auto_close_popup_candidate_scores_parallel", fake_scores)
    monkeypatch.setattr(
        runner,
        "_scene_reference_similarity",
        lambda _ctx, image, _frame: {"图1": 96.0, "图2": 88.0}.get(image["title"]),
    )

    candidate, score = runner._auto_close_popup_graph_match({"entry": object()}, candidates, "frame")

    assert candidate is candidates[1]
    assert score == 100
    assert calls == [3]


def test_scene_number_reuses_one_full_frame_ocr_across_candidates(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image(
        "世界",
        "0034.jpg",
        [
                {
                    "id": "world-title",
                    "kind": "rect",
                    "title": "大地图",
                    "isSceneIdentity": True,
                    "imageMatchRole": "optional",
                    "ocrEnabled": True,
                    "ocrText": "大地图",
                "ocrMatchRole": "required",
                "x": 0.1,
                "y": 0.1,
                "w": 0.2,
                "h": 0.05,
            }
            ],
        )
    image34["layer"] = 1
    image35 = _image(
        "菜单",
        "0035.jpg",
        [
                {
                    "id": "menu-title",
                    "kind": "rect",
                    "title": "菜单",
                    "isSceneIdentity": True,
                    "imageMatchRole": "off",
                    "ocrEnabled": True,
                    "ocrText": "菜单",
                "ocrMatchRole": "required",
                "x": 0.1,
                "y": 0.1,
                "w": 0.2,
                "h": 0.05,
            }
            ],
        )
    image35["layer"] = 1
    ctx = {"entry": object(), "images": {34: image34, 35: image35}}
    ocr_calls: list[str] = []
    run_match_calls: list[tuple[str, bool]] = []

    def fake_ocr_frame(frame, **_kwargs):
        ocr_calls.append(frame)
        return {"tokens": _ocr_tokens("大地图", x=100, y=170, w=120, h=30)}

    monkeypatch.setattr(runner, "_ocr_frame", fake_ocr_frame)
    def fake_run_match(_ctx, image, _shape, _frame, **kwargs):
        ocr_enabled = bool(kwargs.get("ocr_enabled"))
        run_match_calls.append((image["title"], ocr_enabled))
        if image["title"] == "世界" and ocr_enabled:
            return {
                "similarity": 100,
                "matches": [{"text": "大地图", "x": 100, "y": 170, "w": 120, "h": 30}],
                "fixed_box": {"x": 100, "y": 170, "w": 120, "h": 30},
            }
        return {"similarity": 0, "matches": []}

    monkeypatch.setattr(runner, "_run_match", fake_run_match)

    scene_id, score = runner._identify_scene_number(ctx, "frame")

    assert (scene_id, score) == (34, 100)
    assert ocr_calls == ["frame"]
    assert run_match_calls == []


def test_popup_guard_parallel_scoring_is_faster_than_serial_for_independent_candidates(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    candidates = [{"image": _image(f"图{index}", f"{index:04d}.jpg")} for index in range(12)]
    ctx = {"entry": object()}
    score_delay = 0.01
    rounds = 2

    def score(_ctx, image, _frame):
        time.sleep(score_delay)
        return 0 if image["title"] != "图11" else 90

    monkeypatch.setattr(runner, "_scene_score", score)

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
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")

    with runner._lock:
        runner._guard_enabled = True

    container = _BehaviorTreeRuntimeContainer(
        runner,
        runtime_ctx={"entry": object()},
        asset_tree_path=path,
        stop_event=threading.Event(),
    )

    specs = {spec.node_id: spec.enabled for spec in container.guard_specs()}
    assert specs == {"device_health": True}


def test_runtime_guard_defaults_enable_device_health_only(tmp_path):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")

    with runner._lock:
        runner._sync_guard_status_locked()

    status = runner.status()
    assert status["guard_group_enabled"] is True
    assert set(status["guard_items"]) == {"device_health"}
    assert status["guard_items"]["device_health"]["enabled"] is True


def test_runtime_guard_group_switch_disables_execution_without_clearing_items(tmp_path):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")

    with runner._lock:
        runner._guard_group_enabled = False
        runner._guard_enabled = True
        runner._sync_guard_status_locked()

    status = runner.status()
    assert status["guard_group_enabled"] is False
    assert status["guard_items"]["device_health"]["enabled"] is True
    assert runner._runtime_guard_enabled("device_health") is False

    container = _BehaviorTreeRuntimeContainer(
        runner,
        runtime_ctx={"entry": object()},
        asset_tree_path=path,
        stop_event=threading.Event(),
    )
    specs = {spec.node_id: spec.enabled for spec in container.guard_specs()}
    assert specs == {"device_health": False}


def test_runtime_container_guard_override_controls_this_job_only(tmp_path):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")

    with runner._lock:
        runner._guard_group_enabled = False
        runner._guard_enabled = True

    disabled_container = _BehaviorTreeRuntimeContainer(
        runner,
        runtime_ctx={"entry": object()},
        asset_tree_path=path,
        stop_event=threading.Event(),
        guard_override=False,
    )
    assert {spec.node_id: spec.enabled for spec in disabled_container.guard_specs()} == {
        "device_health": False,
    }

    forced_container = _BehaviorTreeRuntimeContainer(
        runner,
        runtime_ctx={"entry": object()},
        asset_tree_path=path,
        stop_event=threading.Event(),
        guard_override=True,
    )
    assert {spec.node_id: spec.enabled for spec in forced_container.guard_specs()} == {
        "device_health": True,
    }


def test_runtime_guard_override_payload_parser():
    runner = create_behavior_tree_runtime_runner()

    assert runner._runtime_guard_override_from_payload({}) is None
    assert runner._runtime_guard_override_from_payload({"guard": True}) is True
    assert runner._runtime_guard_override_from_payload({"guard": "false"}) is False
    assert runner._runtime_guard_override_from_payload({"guard": "关闭"}) is False
    assert runner._runtime_guard_override_from_payload({"guard": "开启"}) is True
    assert runner._runtime_guard_override_from_payload({"guard": "unknown"}) is None








def test_debug_eval_act_cell_injects_bound_runtime(monkeypatch, tmp_path):
    from backend.core.fanxiu.data_annotation.debug_eval import run_data_annotation_debug_eval

    runner = create_behavior_tree_runtime_runner()
    runtime = SimpleNamespace(marker="bound")
    calls: list[tuple] = []

    def fake_runtime(raw_ctx, asset_tree_path=None, *, stop_event=None):
        calls.append((raw_ctx, asset_tree_path, stop_event))
        return runtime

    monkeypatch.setattr(runner, "_fanxiu_runtime", fake_runtime)
    stop_event = threading.Event()
    raw_ctx = {
        "entry_id": "cell-entry",
        "entry": object(),
        "images": {},
        "asset_tree_path": tmp_path / "asset-tree.json",
    }

    result = run_data_annotation_debug_eval(
        runner,
        raw_ctx,
        {"code": "result = runtime.marker", "mode": "act"},
        stop_event,
    )

    assert result == "success"
    assert calls == [(raw_ctx, raw_ctx["asset_tree_path"], stop_event)]


def test_debug_eval_readonly_cell_does_not_inherit_runtime(monkeypatch, tmp_path):
    from backend.core.fanxiu.data_annotation.debug_eval import run_data_annotation_debug_eval

    runner = create_behavior_tree_runtime_runner()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: SimpleNamespace(marker="bound"))
    raw_ctx = {
        "entry_id": "cell-entry",
        "entry": object(),
        "images": {},
        "asset_tree_path": tmp_path / "asset-tree.json",
    }

    run_data_annotation_debug_eval(
        runner,
        raw_ctx,
        {"code": "act_marker = runtime.marker", "mode": "act"},
        threading.Event(),
    )

    with pytest.raises(NameError):
        run_data_annotation_debug_eval(
            runner,
            raw_ctx,
            {"code": "result = runtime.marker", "mode": "readonly"},
            threading.Event(),
        )


def test_debug_eval_context_shape_score_is_readonly_probe():
    from backend.core.fanxiu.data_annotation.debug_eval import BehaviorTreeRuntimeDebugContext

    image = _image("法则之主", "0265.png", [{"title": "返回", "x": 0.1, "y": 0.8, "w": 0.1, "h": 0.1}])

    class FakeRunner:
        def _raise_if_stopped(self, _stop_event):
            return None

        def _find_shape(self, image, title, *, contains=False):
            return next(shape for shape in image["shapes"] if shape["title"] == title)

        def _shape_score(self, ctx, image, shape, frame):
            return 79.0

    ctx = BehaviorTreeRuntimeDebugContext(FakeRunner(), {"images": {265: image}}, threading.Event())

    assert ctx.shape_score(265, "返回", frame=_png_data_url()) == 79.0


def test_debug_eval_context_exposes_match_relation_probe():
    from backend.core.fanxiu.data_annotation.debug_eval import BehaviorTreeRuntimeDebugContext

    calls: list[tuple] = []

    class FakeRunner:
        def _raise_if_stopped(self, _stop_event):
            return None

        def match_scene_frame(self, ctx, s, x, *, threshold=None, frame_data_url=None):
            calls.append((ctx, s, x, threshold, frame_data_url))
            return {"s": s, "x": x, "score": 91.0, "threshold": threshold or 80.0, "matched": True}

        def match_scene_matrix(self, ctx, scene_ids=None, *, layer=2, threshold=None, use_cache=True):
            calls.append((ctx, scene_ids, layer, threshold, use_cache))
            return {"scene_ids": scene_ids or [], "matches": [], "cache_hit": False}

    raw_ctx = {"images": {}}
    ctx = BehaviorTreeRuntimeDebugContext(FakeRunner(), raw_ctx, threading.Event())

    assert ctx.match(101, 102, threshold=80.0, frame="frame")["matched"] is True
    assert ctx.match_matrix([101, 102], layer=2)["scene_ids"] == [101, 102]
    assert calls == [
        (raw_ctx, 101, 102, 80.0, "frame"),
        (raw_ctx, [101, 102], 2, None, True),
    ]


def test_runtime_match_scene_matrix_uses_cache(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    images = {
        1: _scene_image("a", "0001.png", layer=2),
        2: _scene_image("b", "0002.png", layer=2),
    }
    ctx = {"images": images}
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(runner, "_scene_match_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(runner, "_scene_frame_data_url_from_reference", lambda _ctx, image: f"frame:{image['filename']}")

    def fake_scene_score(_ctx, reference_image, frame, **_kwargs):
        calls.append((frame, reference_image["filename"]))
        if frame == "frame:0001.png" and reference_image["filename"] == "0002.png":
            return 91.0
        return 40.0

    monkeypatch.setattr(runner, "_scene_score", fake_scene_score)

    result = runner.match_scene_matrix(ctx, [1, 2], threshold=80.0)

    assert result["cache_hit"] is False
    assert result["match_count"] == 1
    assert result["matches"] == [{"s": 2, "x": 1, "score": 91.0, "threshold": 80.0, "matched": True}]
    assert len(calls) == 2

    monkeypatch.setattr(
        runner,
        "_scene_score",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache was not used")),
    )
    cached = runner.match_scene_matrix(ctx, [1, 2], threshold=80.0)

    assert cached["cache_hit"] is True
    assert cached["matches"] == result["matches"]


def test_scene_score_does_not_infer_ocr_from_shape_title(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    shape = {
        "id": "map",
        "kind": "rect",
        "title": "大地图",
        "isSceneIdentity": True,
        "imageMatchRole": "required",
        "ocrMatchRole": "off",
    }
    image = _image("世界", "0034.png", [shape])

    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(runner, "_cached_ocr_fragments", lambda *_args, **_kwargs: [{"text": "大地图"}])

    assert runner._scene_score({"entry": object()}, image, "frame") == 0.0


def test_runtime_match_scene_matrix_uses_scene_score_without_inferred_title_ocr(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    images = {
        1: _scene_image("a", "0001.png", layer=2),
        2: _scene_image("b", "0002.png", layer=2),
    }
    ctx = {"images": images}
    calls = 0

    monkeypatch.setattr(runner, "_scene_match_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(runner, "_scene_frame_data_url_from_reference", lambda _ctx, image: f"frame:{image['filename']}")

    def fake_scene_score(_ctx, _reference_image, _frame):
        nonlocal calls
        calls += 1
        return 0.0

    monkeypatch.setattr(runner, "_scene_score", fake_scene_score)

    result = runner.match_scene_matrix(ctx, [1, 2], threshold=80.0)

    assert calls == 2
    assert result["matches"] == []


def test_runtime_match_scene_matrix_groups_rules_by_fact_frame_for_shared_ocr(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    images = {
        1: _scene_image("a", "0001.png", layer=2),
        2: _scene_image("b", "0002.png", layer=2),
        3: _scene_image("c", "0003.png", layer=2),
    }
    ctx = {"images": images}
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(runner, "_scene_match_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(runner, "_scene_frame_data_url_from_reference", lambda _ctx, image: f"frame:{image['filename']}")

    def fake_scene_score(_ctx, reference_image, frame):
        calls.append((frame, reference_image["filename"]))
        return 0.0

    monkeypatch.setattr(runner, "_scene_score", fake_scene_score)

    runner.match_scene_matrix(ctx, [1, 2, 3], threshold=80.0, use_cache=False)

    assert calls == [
        ("frame:0001.png", "0002.png"),
        ("frame:0001.png", "0003.png"),
        ("frame:0002.png", "0001.png"),
        ("frame:0002.png", "0003.png"),
        ("frame:0003.png", "0001.png"),
        ("frame:0003.png", "0002.png"),
    ]


def test_runtime_scene_identification_prefers_graph_nearest_candidate(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    images = {
        34: _scene_image("世界", "0034.png", [{"id": "world", "isSceneIdentity": True}], layer=2),
        266: _scene_image("法则详情", "0266.png", [{"id": "detail", "isSceneIdentity": True}], layer=2),
    }
    ctx = {"asset_tree": [images[34], images[266]], "images": images}

    def fake_scene_score(_ctx, image, _frame):
        if image["filename"] == "0034.png":
            return 99.0
        if image["filename"] == "0266.png":
            return 88.0
        return 0.0

    monkeypatch.setattr(runner, "_scene_score", fake_scene_score)
    monkeypatch.setattr(runner, "_scene_match_edges_for_candidates", lambda _ctx, _ids, trace=None: [{"s": 34, "x": 266, "matched": True}])

    scene_id, score = runner._identify_scene_number(ctx, _png_data_url())

    assert scene_id == 266
    assert score == 88.0


def test_debug_eval_context_exposes_ocr_tokens_in_shapes():
    from backend.core.fanxiu.data_annotation.debug_eval import BehaviorTreeRuntimeDebugContext

    calls: list[tuple] = []

    class FakeRuntime:
        def ocr_tokens_in_shapes(self, scene, shape_titles, *, frame_data_url=None, padding=0, options=None):
            calls.append((scene, shape_titles, frame_data_url, padding, options))
            return [{"text": "魔道", "x": 700.0, "y": 760.0, "w": 60.0, "h": 32.0}]

    class FakeRunner:
        def _raise_if_stopped(self, _stop_event):
            return None

        def _fanxiu_runtime(self, _ctx, *, stop_event=None):
            return FakeRuntime()

    ctx = BehaviorTreeRuntimeDebugContext(FakeRunner(), {}, threading.Event())
    frame = _png_data_url()
    result = ctx.ocr_tokens_in_shapes(
        265,
        ["识别区"],
        frame=frame,
        padding=4,
        options={"return_word_box": True},
    )

    assert result == [{"text": "魔道", "x": 700.0, "y": 760.0, "w": 60.0, "h": 32.0}]
    assert calls == [(265, ("识别区",), frame, 4, {"return_word_box": True})]


def test_debug_eval_context_shape_probe_reports_score_and_shape_metadata():
    from backend.core.fanxiu.data_annotation.debug_eval import BehaviorTreeRuntimeDebugContext

    calls: list[int] = []
    image = _image("法则之主", "0265.png", [{
        "title": "返回",
        "x": 0.1,
        "y": 0.8,
        "w": 0.1,
        "h": 0.1,
        "sceneJumpTarget": "",
        "imageMatchRole": "required",
        "ocrMatchRole": "off",
        "pixelTolerance": 5,
    }])

    class FakeRunner:
        overlay_threshold = 80.0
        scene_threshold = 80.0

        def _raise_if_stopped(self, _stop_event):
            return None

        def _find_shape(self, image, title, *, contains=False):
            return next(shape for shape in image["shapes"] if shape["title"] == title)

        def _shape_match_conditions(self, shape):
            return ["image"]

        def _match_shape(self, ctx, image, shape, frame, *, condition="auto"):
            calls.append(int(shape.get("pixelTolerance") or 0))
            similarity = 81.0 if int(shape.get("pixelTolerance") or 0) >= 10 else 79.0
            return {"similarity": similarity, "matched": similarity >= self.scene_threshold}

        def _shape_score(self, ctx, image, shape, frame):
            return 79.0

    ctx = BehaviorTreeRuntimeDebugContext(FakeRunner(), {"images": {265: image}}, threading.Event())

    result = ctx.shape_probe(265, "返回", frame=_png_data_url())
    override_result = ctx.shape_probe(265, "返回", frame=_png_data_url(), overrides={"pixelTolerance": 10})

    assert result["score"] == 79.0
    assert result["scene_threshold"] == 80.0
    assert result["overlay_threshold"] == 80.0
    assert result["matched"] is False
    assert result["conditions"] == [{"condition": "image", "similarity": 79.0, "matched": False, "ocr_text": ""}]
    assert result["shape"]["imageMatchRole"] == "required"
    assert result["shape"]["pixelTolerance"] == 5
    assert override_result["score"] == 81.0
    assert override_result["matched"] is True
    assert override_result["shape"]["pixelTolerance"] == 10
    assert image["shapes"][0]["pixelTolerance"] == 5
    assert calls == [5, 10]


def test_debug_eval_context_exposes_wait_click_then_view():
    from backend.core.fanxiu.data_annotation.debug_eval import BehaviorTreeRuntimeDebugContext

    calls: list[tuple[tuple, dict]] = []

    class FakeRuntime:
        def wait_click_then_view(self, *args, **kwargs):
            calls.append((args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return "ok"

    class FakeRunner:
        def _raise_if_stopped(self, _stop_event):
            return None

        def _fanxiu_runtime(self, _ctx, *, stop_event=None):
            return FakeRuntime()

    ctx = BehaviorTreeRuntimeDebugContext(FakeRunner(), {}, threading.Event(), readonly=False)
    result = _drain_generator(ctx.wait_click_then_view(265, "返回", 264, timeout=20.0, label="probe"))

    assert result == "ok"
    assert calls == [((265, "返回", 264), {"timeout": 20.0, "label": "probe"})]


def test_debug_eval_context_exposes_wait_scene():
    from backend.core.fanxiu.data_annotation.debug_eval import BehaviorTreeRuntimeDebugContext

    calls: list[tuple[tuple, dict]] = []

    class FakeRuntime:
        def wait_scene(self, *args, **kwargs):
            calls.append((args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return "ok"

    class FakeRunner:
        def _raise_if_stopped(self, _stop_event):
            return None

        def _fanxiu_runtime(self, _ctx, *, stop_event=None):
            return FakeRuntime()

    ctx = BehaviorTreeRuntimeDebugContext(FakeRunner(), {}, threading.Event(), readonly=False)
    result = _drain_generator(ctx.wait_scene(121, 34, timeout=12.0, label="probe"))

    assert result == "ok"
    assert calls == [((121, 34), {"timeout": 12.0, "label": "probe"})]


def test_debug_eval_context_exposes_go_scene():
    from backend.core.fanxiu.data_annotation.debug_eval import BehaviorTreeRuntimeDebugContext

    calls: list[tuple] = []

    class FakeRuntime:
        def go_scene(self, *args, **kwargs):
            calls.append((args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return "ok"

    class FakeRunner:
        def _raise_if_stopped(self, _stop_event):
            return None

        def _fanxiu_runtime(self, raw_ctx, asset_tree_path=None, *, stop_event=None):
            calls.append((raw_ctx, asset_tree_path, stop_event))
            return FakeRuntime()

    stop_event = threading.Event()
    raw_ctx = {"asset_tree_path": Path("asset-tree.json")}
    ctx = BehaviorTreeRuntimeDebugContext(FakeRunner(), raw_ctx, stop_event, readonly=False)
    result = _drain_generator(ctx.go_scene(34, layer0_wait_seconds=30.0))

    assert result == "ok"
    assert calls == [
        (raw_ctx, raw_ctx["asset_tree_path"], stop_event),
        ((34,), {"layer0_wait_seconds": 30.0}),
    ]


def test_runtime_scene_terms_are_documented():
    docs_root = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "domains"
        / "fanxiu"
        / "architecture"
    )
    text = (
        (docs_root / "凡修GUI场景地图与图模型约定.md").read_text(encoding="utf-8")
        + "\n"
        + (docs_root / "凡修行为树运行框架约定.md").read_text(encoding="utf-8")
        + "\n"
        + (docs_root / "凡修data-annotation自动化Runtime与Scheduler.md").read_text(encoding="utf-8")
        + "\n"
        + (docs_root / "凡修data-annotation命名约定.md").read_text(encoding="utf-8")
    )

    assert "场景帧 scene/frame" in text
    assert "业务代码、公开 API 和日志优先使用 `scene`" in text
    assert "view 是旧版兼容" in text
    assert "go_scene()" in text
    assert "wait_scene()" in text
    assert "wait_view()" in text








def test_runtime_scene_candidates_use_all_default_graph_nodes_without_context():
    runner = create_behavior_tree_runtime_runner()
    image20 = _image("绿瓶", "0020.png", [
        {"id": "green-bottle-id", "kind": "rect", "title": "绿瓶", "sceneIdentityRole": "required"},
    ])
    image20["layer"] = 1
    image34 = _image("世界", "0034.png", [
        {"id": "world-id", "kind": "rect", "title": "世界标识", "sceneIdentityRole": "required"},
    ])
    image34["layer"] = 1
    image35 = _image("世界下方菜单", "0035.png", [
        {"id": "menu-id", "kind": "rect", "title": "下方菜单标识", "sceneIdentityRole": "required"},
    ])
    image35["layer"] = 2
    image47 = _image("提示", "0047.png")
    image47["layer"] = 1
    image47["shapes"] = [
        {"id": "popup-id", "kind": "rect", "title": "提示", "sceneIdentityRole": "required"},
        {"id": "popup-close", "kind": "rect", "title": "空白"},
    ]
    image86 = _image("离开提示", "0086.png", [
        {"id": "leave-id", "kind": "rect", "title": "离开提示", "sceneIdentityRole": "required"},
    ])
    image86["layer"] = 1
    image278 = _image("邮件删除提示", "0278.png")
    image278["layer"] = 3
    image198 = _image("仙缘人物详情", "0198.png", [
        {"id": "identity-id", "kind": "rect", "title": "身份", "sceneIdentityRole": "required"},
    ])
    image198["layer"] = 2
    image199 = _image("素材模板", "0199.png", [
        {"id": "template-id", "kind": "rect", "title": "素材", "sceneIdentityRole": "required"},
    ])
    image199["layer"] = 3
    image204 = _image("小助手清单", "0204.png", [
        {"id": "assistant-id", "kind": "rect", "title": "小助手清单标识", "sceneIdentityRole": "required"},
    ])
    image204["layer"] = 1
    image34["children"] = [image35]
    tree = [
        {"type": "folder", "title": "绿瓶", "children": [image20]},
        {"type": "folder", "title": "世界", "children": [image34]},
        {"type": "folder", "title": "弹窗", "children": [image47, image86]},
        {"type": "folder", "title": "日常", "children": [image198, image204, image199]},
    ]
    image47["children"] = [image278]
    ctx = {"asset_tree": tree, "images": {20: image20, 34: image34, 35: image35, 47: image47, 86: image86, 198: image198, 199: image199, 204: image204, 278: image278}}

    assert runner._runtime_scene_candidate_ids(ctx) == [20, 34, 35, 47, 86, 198, 204, 199]
    assert runner._runtime_popup_scene_candidate_ids(ctx) == [47, 86]


def test_identify_scene_number_without_context_checks_all_default_graph_candidates(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [
        {"id": "world-id", "kind": "rect", "title": "世界标识", "sceneIdentityRole": "required"},
    ])
    image34["layer"] = 1
    image47 = _image("提示", "0047.png", [
        {"id": "popup-id", "kind": "rect", "title": "提示标识", "sceneIdentityRole": "required"},
    ])
    image47["layer"] = 1
    image204 = _image("小助手清单", "0204.png", [
        {"id": "assistant-id", "kind": "rect", "title": "小助手清单标识", "sceneIdentityRole": "required"},
    ])
    image204["layer"] = 1
    tree = [
        {"type": "folder", "title": "世界", "children": [image34]},
        {"type": "folder", "title": "弹窗", "children": [image47]},
        {"type": "folder", "title": "日常", "children": [image204]},
    ]
    ctx = {"asset_tree": tree, "images": {34: image34, 47: image47, 204: image204}}
    scanned: list[int] = []

    def score(_ctx, image, _frame):
        scene_id = runner._image_number(image)
        scanned.append(scene_id)
        return 92.0 if scene_id == 47 else 0.0

    monkeypatch.setattr(runner, "_scene_score", score)

    assert runner._identify_scene_number(ctx, "frame") == (47, 92.0)
    assert scanned == [34, 47, 204]
    scanned.clear()
    assert runner._identify_scene_number(ctx, "frame", [47]) == (47, 92.0)
    assert scanned == [47]


def test_identify_scene_number_directly_checks_explicit_nested_candidate(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image47 = _image("所有提示窗口", "0047.png", [
        {"id": "title", "kind": "rect", "title": "提示", "sceneIdentityRole": "required"},
    ])
    image278 = _image("邮件删除提示", "0278.png", [
        {"id": "mail", "kind": "rect", "title": "邮件", "sceneIdentityRole": "required", "ocrMatchRole": "required"},
    ])
    image47["children"] = [image278]
    tree = [{"type": "folder", "title": "弹窗", "children": [image47]}]
    ctx = {"asset_tree": tree, "images": {47: image47, 278: image278}}

    monkeypatch.setattr(
        runner,
        "_scene_score",
        lambda _ctx, image, _frame: 100.0 if runner._image_number(image) == 278 else 40.0,
    )

    assert runner._identify_scene_number(ctx, "frame") == (278, 100.0)
    assert runner._identify_scene_number(ctx, "frame", [278]) == (278, 100.0)


def test_identify_scene_number_treats_layer0_as_exact_candidate_list(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image299 = _image("论道", "0299.png", [
        {"id": "title", "kind": "rect", "title": "论道", "sceneIdentityRole": "required"},
    ])
    image297 = _image("三清道场", "0297.png", [
        {"id": "seat", "kind": "rect", "title": "请他让座", "sceneIdentityRole": "required"},
    ])
    image298 = _image("空位", "0298.png", [
        {"id": "empty", "kind": "rect", "title": "空位", "sceneIdentityRole": "required"},
    ])
    image299["children"] = [image297, image298]
    image299["layer"] = 1
    tree = [{"type": "folder", "title": "日常", "children": [image299]}]
    ctx = {"asset_tree": tree, "images": {297: image297, 298: image298, 299: image299}}

    def fake_scene_score(_ctx, image, _frame):
        return {299: 96.0, 297: 92.0, 298: 20.0}.get(runner._image_number(image), 0.0)

    monkeypatch.setattr(runner, "_scene_score", fake_scene_score)

    assert runner._identify_scene_number(ctx, "frame", [299]) == (299, 96.0)


def test_identify_scene_number_does_not_use_asset_parentage_to_resolve_ambiguity(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [
        {"id": "map", "kind": "rect", "title": "大地图", "sceneIdentityRole": "required"},
    ])
    image299 = _image("论道", "0299.png", [
        {"id": "title", "kind": "rect", "title": "论道", "sceneIdentityRole": "required"},
    ])
    image297 = _image("三清道场", "0297.png", [
        {"id": "seat", "kind": "rect", "title": "请他让座", "sceneIdentityRole": "required"},
    ])
    image34["layer"] = 1
    image299["layer"] = 1
    image299["children"] = [image297]
    tree = [{"type": "folder", "title": "世界", "children": [image34]}, {"type": "folder", "title": "日常", "children": [image299]}]
    ctx = {"asset_tree": tree, "images": {34: image34, 297: image297, 299: image299}}

    def fake_scene_score(_ctx, image, _frame):
        return {34: 97.0, 299: 96.0, 297: 92.0}.get(runner._image_number(image), 0.0)

    monkeypatch.setattr(runner, "_scene_score", fake_scene_score)
    monkeypatch.setattr(
        runner,
        "_cached_ocr_fragments",
        lambda _ctx, _frame: [{"text": "三清道场 论道 闻道感悟 剩余座位 请他让座"}],
    )

    assert runner._identify_scene_number(ctx, "frame") == (None, 97.0)


def test_direct_runtime_action_times_out_generator_and_sets_stop_event(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    stop_event = threading.Event()
    monotonic_values = iter([0.0, 0.0, 31.0])
    monkeypatch.setattr(fanxiu_api.time, "monotonic", lambda: next(monotonic_values))

    def never_finishes():
        while True:
            yield BehaviorTreeStatus.RUNNING

    try:
        runner._run_direct_runtime_action(
            never_finishes,
            stop_event=stop_event,
            tick_seconds=0.01,
            max_runtime_seconds=30,
        )
    except RuntimeError as exc:
        assert "行为树任务超时" in str(exc)
    else:
        raise AssertionError("runtime action should time out")

    assert stop_event.is_set()


def test_go_scene_moves_by_scene_jump_and_records_declared_target_frequency(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    jump_shape = {"id": "jump", "kind": "rect", "title": "去二", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "sceneJumpTarget": "2"}
    tree = [
        _scene_image("一", "0001.jpg", [
            jump_shape,
            {"id": "id1", "kind": "rect", "title": "一标识", "isSceneIdentity": True, "x": 0.4, "y": 0.1, "w": 0.2, "h": 0.1},
        ]),
        _scene_image("二", "0002.jpg", [{"id": "id2", "kind": "rect", "title": "二标识", "isSceneIdentity": True, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}], layer=1),
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

    assert result == "success"
    assert json.loads(path.read_text(encoding="utf-8"))[0]["shapes"][0]["sceneJumpTarget"] == "2(1)"


def test_go_scene_waits_layer0_declared_target_through_transition(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    jump_shape = {"id": "jump13", "kind": "rect", "title": "去三", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "sceneJumpTarget": "3"}
    tree = [
        _scene_image("一", "0001.jpg", [
            jump_shape,
            {"id": "id1", "kind": "rect", "title": "一标识", "isSceneIdentity": True, "x": 0.4, "y": 0.1, "w": 0.2, "h": 0.1},
        ], layer=1),
        _scene_image("二过渡", "0002.jpg", [{"id": "id2", "kind": "rect", "title": "二标识", "isSceneIdentity": True, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}], layer=1),
        _scene_image("三", "0003.jpg", [{"id": "id3", "kind": "rect", "title": "三标识", "isSceneIdentity": True, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}], layer=1),
    ]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree), encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": runner._index_images(tree)}
    scene_state = {"value": 1, "transition_frames": 0}

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def capture_frame(_ctx):
        if scene_state["value"] == 2 and scene_state["transition_frames"] > 0:
            scene_state["transition_frames"] -= 1
            return "scene2"
        if scene_state["value"] == 2:
            scene_state["value"] = 3
        return f"scene{scene_state['value']}"

    def click_shape(_ctx, image, _shape, _frame=None):
        if runner._image_number(image) == 1:
            scene_state.update({"value": 2, "transition_frames": 2})
        runner._clear_tick_frame(_ctx)

    monkeypatch.setattr(runner, "_capture_frame", capture_frame)
    monkeypatch.setattr(runner, "_scene_score", lambda _ctx, image, frame: 80 if str(runner._image_number(image)) in str(frame) else 0)
    monkeypatch.setattr(runner, "_click_shape", click_shape)

    result = runner._run_runtime_behavior_tree(
        runtime_ctx=ctx,
        asset_tree_path=path,
        stop_event=FakeStopEvent(),
        action=lambda: runner._execute_runtime_task(ctx, "go_scene", {"target_scene_id": 3}, FakeStopEvent()),
        label="到场景",
    )

    assert result == "success"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert scene_state["value"] == 3
    assert saved[0]["shapes"][0]["sceneJumpTarget"] == "3(1)"
    assert "observedLanding" not in saved[0]["shapes"][0]


def test_go_scene_replans_through_annotated_reward_interruption(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    jump_shape = {"id": "jump13", "kind": "rect", "title": "日常", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "sceneJumpTarget": "3"}
    claim_shape = {"id": "claim", "kind": "rect", "title": "领取奖励", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1, "sceneJumpTarget": "1"}
    tree = [
        _scene_image("一", "0001.jpg", [
            jump_shape,
            {"id": "id1", "kind": "rect", "title": "一标识", "isSceneIdentity": True, "x": 0.4, "y": 0.1, "w": 0.2, "h": 0.1},
        ], layer=1),
        _scene_image("报名领取灵石奖励", "0002.jpg", [
            claim_shape,
            {"id": "id2", "kind": "rect", "title": "奖励标识", "isSceneIdentity": True, "x": 0.1, "y": 0.2, "w": 0.2, "h": 0.1},
        ], layer=1),
        _scene_image("三", "0003.jpg", [{"id": "id3", "kind": "rect", "title": "三标识", "isSceneIdentity": True, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}], layer=1),
    ]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree), encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": runner._index_images(tree)}
    scene_state = {"value": 1}
    first_jump_interrupted = {"value": False}

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def click_shape(_ctx, image, _shape, _frame=None):
        image_id = runner._image_number(image)
        if image_id == 1 and not first_jump_interrupted["value"]:
            first_jump_interrupted["value"] = True
            scene_state["value"] = 2
        elif image_id == 1:
            scene_state["value"] = 3
        elif image_id == 2:
            scene_state["value"] = 1
        runner._clear_tick_frame(_ctx)

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

    assert result == "success"
    assert first_jump_interrupted["value"] is True
    assert scene_state["value"] == 3


def test_go_scene_records_new_landing_and_replans_to_target(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    first_shape = {"id": "jump12", "kind": "rect", "title": "随机跳", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "sceneJumpTarget": "3"}
    tree = [
        _scene_image("一", "0001.jpg", [first_shape, {"id": "id1", "kind": "rect", "title": "一标识", "isSceneIdentity": True, "x": 0.4, "y": 0.1, "w": 0.2, "h": 0.1}], layer=1),
        _scene_image("二", "0002.jpg", [{"id": "jump23", "kind": "rect", "title": "继续", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "sceneJumpTarget": "3"}, {"id": "id2", "kind": "rect", "title": "二标识", "isSceneIdentity": True, "x": 0.4, "y": 0.1, "w": 0.2, "h": 0.1}], layer=1),
        _scene_image("三", "0003.jpg", [{"id": "id3", "kind": "rect", "title": "三标识", "isSceneIdentity": True, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}], layer=1),
    ]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree), encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": runner._index_images(tree)}
    scene_state = {"value": 1}
    monotonic_value = {"value": 0.0}

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def click_shape(_ctx, image, _shape, _frame=None):
        scene_state["value"] = 2 if runner._image_number(image) == 1 else 3
        runner._clear_tick_frame(_ctx)

    def fake_monotonic():
        monotonic_value["value"] += 61.0
        return monotonic_value["value"]

    monkeypatch.setattr("backend.api.fanxiu.time.monotonic", fake_monotonic)
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
    assert "observedLanding" not in saved[0]["shapes"][0]
    assert saved[1]["shapes"][0]["sceneJumpTarget"] == "3(1)"


def test_go_scene_replans_through_historical_reachable_landing(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    first_shape = {
        "id": "jump12",
        "kind": "rect",
        "title": "入口",
        "x": 0.1,
        "y": 0.1,
        "w": 0.2,
        "h": 0.1,
        "sceneJumpTarget": "2(1),3",
    }
    second_shape = {"id": "jump23", "kind": "rect", "title": "继续", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "sceneJumpTarget": "3"}
    tree = [
        _scene_image("一", "0001.jpg", [first_shape, {"id": "id1", "kind": "rect", "title": "一标识", "isSceneIdentity": True, "x": 0.4, "y": 0.1, "w": 0.2, "h": 0.1}], layer=1),
        _scene_image("二", "0002.jpg", [second_shape, {"id": "id2", "kind": "rect", "title": "二标识", "isSceneIdentity": True, "x": 0.4, "y": 0.1, "w": 0.2, "h": 0.1}], layer=1),
        _scene_image("三", "0003.jpg", [{"id": "id3", "kind": "rect", "title": "三标识", "isSceneIdentity": True, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}], layer=1),
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

    def click_shape(_ctx, image, _shape, _frame=None):
        if runner._image_number(image) == 1:
            scene_state["value"] = 2
        elif runner._image_number(image) == 2:
            scene_state["value"] = 3
        runner._clear_tick_frame(_ctx)

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

    assert result == "success"
    assert scene_state["value"] == 3
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved[0]["shapes"][0]["sceneJumpTarget"] == "2(2),3"
    assert "observedLanding" not in saved[0]["shapes"][0]
    assert saved[1]["shapes"][0]["sceneJumpTarget"] == "3(1)"


def test_daily_signup_scheduler_task_is_runtime_daily_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-signup")

    assert task["task_type"] == "daily_signup"
    assert task["label"] == "日常_报名"
    assert task["trigger_description"] == "每日"
    assert task["next_time"]
    assert task["error_retry_delay_seconds"] == 600
    assert _data_annotation_task_supported(task)


def test_mail_selective_claim_is_daily_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "mail-selective-claim")

    assert task["task_type"] == "mail_selective_claim"
    assert task["label"] == "邮件_选择性领取"
    assert task["trigger_description"] == "每日"
    assert task["next_time"]
    assert task["error_retry_delay_seconds"] == 600
    assert task["payload"]["max_runtime_seconds"] == 10800
    assert _data_annotation_task_supported(task)


def test_xianfu_visit_partner_is_dynamic_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "xianfu-visit-partner")

    assert task["task_type"] == "xianfu_visit_partner"
    assert task["label"] == "仙府_寻访仙侣"
    assert task["source"] == "data_annotation_runtime"
    assert task["trigger_description"] == "动态"
    assert task["next_time"]
    assert task["error_retry_delay_seconds"] == 600
    assert _data_annotation_task_supported(task)


def test_daily_boss_is_daily_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "daily-boss")

    assert task["task_type"] == "daily_boss"
    assert task["label"] == "日常_首领"
    assert task["source"] == "data_annotation_runtime"
    assert task["trigger_description"] == "每日"
    assert task["next_time"]
    assert task["error_retry_delay_seconds"] == 600
    assert _data_annotation_task_supported(task)


def test_daily_dongtian_is_daily_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-dongtian")

    assert task["task_type"] == "daily_dongtian"
    assert task["label"] == "洞天_领取"
    assert task["source"] == "data_annotation_runtime"
    assert task["trigger_description"] == "每日"
    assert task["next_time"]
    assert "window" not in task
    assert task["error_retry_delay_seconds"] == 600
    assert _data_annotation_task_supported(task)


@pytest.mark.parametrize(
    ("start_scene_id", "expected_clicks"),
    [
        (279, [(279, "收益", ("claim_page", "no_reward")), (284, "领取", (279,)), (279, "返回", (34, 20))]),
        (284, [(284, "领取", (279,)), (279, "返回", (34, 20))]),
    ],
)
def test_daily_dongtian_claim_uses_actual_entry_landing(monkeypatch, start_scene_id, expected_clicks):
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.clicks = []
            self.scene_reads = 0

        def wait_click_then_view(self, scene_id, shape_title, *target_scene_ids, **_kwargs):
            self.clicks.append((scene_id, shape_title, target_scene_ids))
            yield BehaviorTreeStatus.RUNNING
            return SimpleNamespace(id=target_scene_ids[0])

        def view_visible(self, scene_id):
            return scene_id

        def wait_click_then_any(self, scene_id, shape_title, conditions, **_kwargs):
            self.clicks.append((scene_id, shape_title, tuple(conditions)))
            yield BehaviorTreeStatus.RUNNING
            return "claim_page"

        def go_scene(self, _target_scene_id):
            raise AssertionError("direct #34 landing must not invoke go_scene")

        def current_scene(self, *_args, **_kwargs):
            self.scene_reads += 1
            if self.scene_reads == 1:
                return 279, 100.0, "dongtian-home"
            return 34, 100.0, "world"

        def ocr_text(self, _frame):
            return "洞天福地 我的编队 收益"

    runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)
    ctx = {
        "asset_tree_path": Path("asset-tree.json"),
        "images": {
            279: {"shapes": [{"title": "收益"}, {"title": "返回"}]},
            284: {"shapes": [{"title": "领取"}]},
        },
    }

    result = _drain_generator(
        runner._claim_daily_dongtian_profit(
            ctx,
            threading.Event(),
            {},
            task_label="洞天_领取",
            start_scene_id=start_scene_id,
        )
    )

    assert result is True
    assert runtime.clicks == expected_clicks


def test_daily_dongtian_inert_profit_button_is_no_reward_success(monkeypatch):
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.clicks = []

        def view_visible(self, scene_id):
            return scene_id

        def wait_click_then_any(self, scene_id, shape_title, conditions, **_kwargs):
            self.clicks.append((scene_id, shape_title, tuple(conditions)))
            yield BehaviorTreeStatus.RUNNING
            return "no_reward"

        def wait_click_then_view(self, scene_id, shape_title, *target_scene_ids, **_kwargs):
            self.clicks.append((scene_id, shape_title, target_scene_ids))
            yield BehaviorTreeStatus.RUNNING
            return SimpleNamespace(id=34)

        def current_scene(self, *_args, **_kwargs):
            return 34, 100.0, "world"

        def ocr_text(self, _frame):
            return "世界"

    runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)
    ctx = {
        "asset_tree_path": Path("asset-tree.json"),
        "images": {
            279: {"shapes": [{"title": "收益"}, {"title": "返回"}]},
            284: {"shapes": [{"title": "领取"}]},
        },
    }

    result = _drain_generator(
        runner._claim_daily_dongtian_profit(
            ctx,
            threading.Event(),
            {},
            task_label="洞天_领取",
            start_scene_id=279,
        )
    )

    assert result is False
    assert runtime.clicks == [
        (279, "收益", ("claim_page", "no_reward")),
        (279, "返回", (34, 20)),
    ]


def test_daily_dongtian_claim_routes_known_scene20_landing_back_to_world(monkeypatch):
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.go_targets = []

        def view_visible(self, scene_id):
            return scene_id

        def wait_click_then_any(self, *_args, **_kwargs):
            yield BehaviorTreeStatus.RUNNING
            return "claim_page"

        def wait_click_then_view(self, scene_id, shape_title, *target_scene_ids, **_kwargs):
            yield BehaviorTreeStatus.RUNNING
            if scene_id == 279 and shape_title == "返回":
                return SimpleNamespace(id=20)
            return SimpleNamespace(id=target_scene_ids[0])

        def go_scene(self, target_scene_id):
            self.go_targets.append(target_scene_id)
            yield BehaviorTreeStatus.RUNNING
            return SimpleNamespace(id=target_scene_id)

        def current_scene(self, *_args, **_kwargs):
            return 34, 100.0, "world"

        def ocr_text(self, _frame):
            return "世界"

    runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)
    ctx = {
        "asset_tree_path": Path("asset-tree.json"),
        "images": {
            279: {"shapes": [{"title": "收益"}, {"title": "返回"}]},
            284: {"shapes": [{"title": "领取"}]},
        },
    }

    _drain_generator(
        runner._claim_daily_dongtian_profit(
            ctx,
            threading.Event(),
            {},
            task_label="洞天_领取",
            start_scene_id=279,
        )
    )

    assert runtime.go_targets == [34]


def test_daily_lingmai_is_registered_with_dynamic_scheduler_task():
    from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs

    register_fanxiu_data_annotation_default_runtime_jobs()
    tasks = {item["id"]: item for item in _default_data_annotation_scheduler_tasks()}
    definition = fanxiu_api._data_annotation_task_cell_definition("daily_lingmai")

    task = tasks["daily-lingmai-seat"]
    assert task["task_type"] == "daily_lingmai"
    assert task["label"] == "灵脉_座位"
    assert task["trigger_description"] == "动态"
    assert task["next_time"]
    assert task["error_retry_delay_seconds"] == 600
    assert task["payload"] == {
        "daily_start_time": "17:30",
        "daily_end_time": "22:00",
        "lingmai_no_target_retry_seconds": 1800,
    }
    assert definition is not None
    assert definition.label == "灵脉_座位"
    assert definition.scheduler_supported is True


def test_daily_lingmai_world_guard_skips_gui_when_runtime_is_seated(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    actions = []
    ctx: dict[str, Any] = {}

    class FakeRuntime:
        def goto_view(self, scene_id):
            actions.append(("goto", scene_id))
            yield BehaviorTreeStatus.RUNNING

    monkeypatch.setattr(
        runner,
        "_schedule_daily_lingmai_next_check",
        lambda _payload, **kwargs: actions.append(
            ("next", kwargs)
        ),
    )
    status = {
        "available": True,
        "complete": True,
        "completed": False,
        "remaining_milliseconds": 10_800_000,
        "self_seat_facts": {
            "seated": True,
            "seat_id": 3798,
        },
    }

    result = _drain_generator(
        runner._daily_lingmai_world_runtime_guard(
            FakeRuntime(),
            ctx,
            {"__lingmai_runtime_snapshot_override": status},
            scene_id=69,
        )
    )

    assert result == "skipped"
    assert ctx["_daily_lingmai_status"] == status
    assert actions == [
        ("goto", 34),
        (
            "next",
                {
                    "message": (
                        "Runtime 已确认仍在灵脉聚灵中，"
                        "体力事实不足时不尝试驱离升级"
                    ),
                "seconds": 1800,
            },
        ),
    ]


def test_daily_lingmai_world_guard_marks_only_zero_remaining_success():
    runner = create_behavior_tree_runtime_runner()
    ctx: dict[str, Any] = {}
    status = {
        "available": True,
        "complete": True,
        "completed": True,
        "remaining_milliseconds": 0,
        "self_seat_facts": {"seated": False},
    }

    result = _drain_generator(
        runner._daily_lingmai_world_runtime_guard(
            object(),
            ctx,
            {"__lingmai_runtime_snapshot_override": status},
            scene_id=34,
        )
    )

    assert result == "success"
    assert ctx["_daily_lingmai_status"] == status


def test_daily_lingmai_world_guard_opens_upgrade_flow_only_at_300_strength():
    runner = create_behavior_tree_runtime_runner()
    status = {
        "available": True,
        "complete": True,
        "completed": False,
        "remaining_milliseconds": 10_800_000,
        "strength": 300,
        "own_room_id": 17,
        "self_seat_facts": {
            "available": True,
            "seated": True,
            "room_id": 17,
            "seat_id": 3798,
        },
    }

    result = _drain_generator(
        runner._daily_lingmai_world_runtime_guard(
            object(),
            {},
            {"__lingmai_runtime_snapshot_override": status},
            scene_id=34,
        )
    )

    assert result is None


def test_daily_lingmai_world_guard_keeps_shenmai_below_300_strength(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    scheduled = []
    monkeypatch.setattr(
        runner,
        "_schedule_daily_lingmai_next_check",
        lambda _payload, **kwargs: scheduled.append(kwargs),
    )
    status = {
        "available": True,
        "complete": True,
        "completed": False,
        "remaining_milliseconds": 10_800_000,
        "strength": 299,
        "own_room_id": 17,
        "self_seat_facts": {
            "available": True,
            "seated": True,
            "room_id": 17,
            "seat_id": 3798,
        },
    }

    result = _drain_generator(
        runner._daily_lingmai_world_runtime_guard(
            object(),
            {},
            {"__lingmai_runtime_snapshot_override": status},
            scene_id=34,
        )
    )

    assert result == "skipped"
    assert scheduled == [{
        "message": "当前体力不足 300，保留至少一次重新落座资源并继续坐神脉",
        "seconds": 1800,
    }]


def test_daily_lingmai_world_guard_caches_unseated_runtime_for_gui_flow():
    runner = create_behavior_tree_runtime_runner()
    ctx: dict[str, Any] = {}
    status = {
        "available": True,
        "complete": True,
        "completed": False,
        "remaining_milliseconds": 10_800_000,
        "self_seat_facts": {"seated": False},
    }

    result = _drain_generator(
        runner._daily_lingmai_world_runtime_guard(
            object(),
            ctx,
            {"__lingmai_runtime_snapshot_override": status},
            scene_id=34,
        )
    )

    assert result is None
    assert ctx["_daily_lingmai_status"] == status


def test_daily_lingmai_already_seated_returns_world_without_occupy_or_kick(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    scheduled = []
    monkeypatch.setattr(
        runner,
        "_schedule_daily_lingmai_next_check",
        lambda _payload, **kwargs: (
            scheduled.append(kwargs)
            or "2026-07-30 18:00:00"
        ),
    )
    monkeypatch.setattr(
        daily_foundation,
        "refresh_lingmai_daily_status",
        lambda **_kwargs: {"ok": True, "available": False},
    )
    monkeypatch.setattr(
        daily_foundation,
        "refresh_and_select_lingmai_seat_action",
        lambda **_kwargs: {
            "ok": True,
            "status": "already_seated",
            "action": "already_seated",
            "self_seat": {"seat_id": 3798, "owner": {"role_id": 42, "name": "自己"}},
        },
    )

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def goto_view(self, scene_id):
            self.actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING
            return "success"

    runtime = FakeRuntime()
    result = _drain_generator(runner._continue_daily_lingmai_from_select_slot(
        {"images": {286: {}}},
        threading.Event(),
        {},
        runtime,
        "frame",
        task_label="灵脉_座位",
    ))

    assert result == "skipped"
    assert runtime.actions == [("goto_view", 34)]
    assert scheduled == [
        {
                "message": (
                    "Runtime 已确认仍在「仙煌神脉」聚灵中，"
                    "30 分钟后复查是否被驱离"
                ),
            "seconds": 1800,
        }
    ]


def test_daily_lingmai_transient_runtime_gap_returns_world_and_rechecks(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    scheduled = []
    monkeypatch.setattr(
        runner,
        "_schedule_daily_lingmai_next_check",
        lambda _payload, **kwargs: (
            scheduled.append(kwargs) or "2026-08-11 17:55:00"
        ),
    )
    monkeypatch.setattr(
        daily_foundation,
        "refresh_lingmai_daily_status",
        lambda **_kwargs: {"available": False},
    )
    monkeypatch.setattr(
        daily_foundation,
        "refresh_and_select_lingmai_seat_action",
        lambda **_kwargs: {
            "ok": False,
            "status": "runtime_unavailable",
            "reason": "联盟灵脉 Runtime 模型尚未初始化",
            "action": None,
        },
    )

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def goto_view(self, scene_id):
            self.actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING
            return "success"

    runtime = FakeRuntime()
    result = _drain_generator(runner._continue_daily_lingmai_from_select_slot(
        {"images": {286: {}}},
        threading.Event(),
        {},
        runtime,
        "frame",
        task_label="灵脉_座位",
    ))

    assert result == "skipped"
    assert runtime.actions == [("goto_view", 34)]
    assert scheduled == [{
        "message": (
            "「仙煌神脉」运行态事实暂不完整 "
            "(runtime_unavailable/联盟灵脉 Runtime 模型尚未初始化)"
        ),
        "seconds": 1800,
    }]


def test_daily_lingmai_scene_285_empty_uses_bounded_click_transition(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    monkeypatch.setattr(
        daily_foundation,
        "refresh_lingmai_daily_status",
        lambda **_kwargs: {"ok": True, "available": False},
    )

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def shape_score(self, scene_id, shape, *, frame_data_url):
            self.actions.append(("shape_score", scene_id, shape, frame_data_url))
            return 0.0

        def wait_click_ocr_text(self, *args, **kwargs):
            self.actions.append(("wait_click_ocr_text", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return OcrTextMatch(
                target="仙煌神脉",
                text="仙煌神脉",
                x=350.0,
                y=620.0,
                w=200.0,
                h=50.0,
                tokens=(),
            )

        def wait_view(self, *args, **kwargs):
            self.actions.append(("wait_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 286

        def current_scene(self, scenes, *, update):
            self.actions.append(("current_scene", tuple(scenes), update))
            return 286, 100.0, "frame286"

        @staticmethod
        def ocr_text(_frame):
            return "选择空位"

    runtime = FakeRuntime()

    def fake_continue_from_select_slot(*_args, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    runner._continue_daily_lingmai_from_select_slot = fake_continue_from_select_slot
    result = _drain_generator(runner._continue_daily_lingmai_from_zaohua(
        {
            "images": {
                285: {
                    "shapes": [
                        {"title": "空位"},
                        {"title": "聚灵中"},
                        {"title": "窗口", "loadDirection": "down"},
                    ],
                },
            },
        },
        threading.Event(),
        {},
        runtime,
        "frame285",
        task_label="灵脉_座位",
    ))

    assert result == "success"
    assert (
        "wait_click_ocr_text",
        (285, "仙煌神脉"),
        {
            "in_shapes": ("窗口",),
            "occurrence": 0,
            "anchor": "top_left",
            "offset": (0.0, 2.0),
            "offset_unit": "height",
                "timeout_seconds": 30.0,
                "max_scrolls_per_direction": 8,
                "search_direction": None,
                "frame_data_url": "frame285",
            },
        ) in runtime.actions
    assert (
        "wait_view",
        (286,),
        {"timeout": 12.0, "label": "灵脉_座位：点击「仙煌神脉」后等待 #286 座位页"},
    ) in runtime.actions


def test_daily_lingmai_scene_285_runtime_zero_finishes_today(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    monkeypatch.setattr(
        daily_foundation,
        "refresh_lingmai_daily_status",
        lambda **_kwargs: {
            "ok": True,
            "available": True,
            "completed": True,
            "remaining_milliseconds": 0,
            "protocol": "SM_SyncUnionVeinsRoleInfo",
        },
    )

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def goto_view(self, scene_id):
            self.actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING
            return "success"

    runtime = FakeRuntime()
    result = _drain_generator(runner._continue_daily_lingmai_from_zaohua(
        {"images": {285: {"shapes": [{"title": "空位"}]}}},
        threading.Event(),
        {},
        runtime,
        "frame285",
        task_label="灵脉_座位",
    ))

    assert result == "success"
    assert runtime.actions == [("goto_view", 34)]


def test_daily_lingmai_scene_286_rechecks_runtime_zero_when_285_was_unavailable(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    monkeypatch.setattr(
        daily_foundation,
        "refresh_lingmai_daily_status",
        lambda **_kwargs: {
            "ok": True,
            "available": True,
            "completed": True,
            "remaining_milliseconds": 0,
            "protocol": "SM_SyncUnionVeinsRoleInfo",
        },
    )

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def goto_view(self, scene_id):
            self.actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING
            return "success"

    runtime = FakeRuntime()
    result = _drain_generator(runner._continue_daily_lingmai_from_select_slot(
        {
            "images": {286: {}},
            "_daily_lingmai_status": {"ok": False, "available": False},
        },
        threading.Event(),
        {},
        runtime,
        "frame286",
        task_label="灵脉_座位",
    ))

    assert result == "success"
    assert runtime.actions == [("goto_view", 34)]


def test_daily_lingmai_kick_click_uses_name_alignment_and_completes_battle_tail(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    scheduled = []
    monkeypatch.setattr(
        runner,
        "_schedule_daily_lingmai_next_check",
        lambda _payload, **kwargs: (
            scheduled.append(kwargs)
            or "2026-07-30 18:00:00"
        ),
    )
    monkeypatch.setattr(
        runner,
        "_shared_spatial_ocr_result",
        lambda _ctx, frame, **_kwargs: {"tokens": (
            [{"text": "目标玩家", "x": 200, "y": 700, "w": 100, "h": 20}]
            if frame == "frame"
            else [{"text": "确认", "x": 398.0, "y": 1042.0, "w": 110.0, "h": 54.0}]
        )},
    )

    class FakeGeometry:
        @staticmethod
        def _box(raw, _view):
            return dict(raw)

        @staticmethod
        def _frame_size(_view):
            return 900.0, 1600.0

    class FakeRuntime:
        def __init__(self):
            self.ctx = {}
            self.runner = FakeGeometry()
            self.clicks = []
            self.actions = []
            # The click is authorized on #380.  Its first post-click sample
            # still sees the underlying #588; that background must transfer
            # ownership to an explicit wait for #381 instead of authorizing a
            # second wait_click on the vanished #380 button.
            self.kick_confirm_states = [380, 588]
            self.dialogue_sequences = {
                (318, 303, 374, 382, 375, 588): [318, 303, 374],
                (318, 303, 443, 289, 305, 306, 85, 186, 285, 588): [318, 303, 306],
            }

        @staticmethod
        def shape(_scene, name):
            raw = (
                {"x": 200, "y": 480, "w": 200, "h": 40}
                if name == "姓名"
                else {"x": 770, "y": 500, "w": 60, "h": 40}
            )
            return SimpleNamespace(raw=raw)

        @staticmethod
        def view(_scene):
            if _scene == 306:
                return behavior_tree_runtime_core.View(_image("灵脉收益确认", "0306.png", [{"title": "确认", "x": 0.4, "y": 0.65, "w": 0.2, "h": 0.08}]))
            return SimpleNamespace(raw={"width": 900, "height": 1600})

        @staticmethod
        def cur_frame(*, update):
            assert update is True
            return "frame"

        def click_frame_point(self, scene, x, y):
            self.clicks.append((scene, x, y))

        def click_shape_center(self, scene, shape):
            self.actions.append(("click_shape_center", scene, shape))

        def click_shape(self, scene, shape):
            self.actions.append(("click_shape", scene, shape))

        def wait_click(self, *args, **kwargs):
            self.actions.append(("wait_click", args, kwargs))
            yield BehaviorTreeStatus.RUNNING

        def wait_view(self, *args, **kwargs):
            self.actions.append(("wait_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return args[0]

        def wait_scene(self, *scenes, **kwargs):
            self.actions.append(("wait_scene", scenes, kwargs))
            yield BehaviorTreeStatus.RUNNING
            if scenes == (380,):
                return 380
            if scenes == (381, 318, 443):
                return 381
            if scenes == (318, 443):
                return 318
            if scenes in self.dialogue_sequences:
                return self.dialogue_sequences[scenes].pop(0)
            if scenes == (382, 375, 588):
                return 375
            if scenes == (306,):
                return 306
            if scenes == (312, 285, 85, 186, 34):
                return 85
            raise AssertionError(scenes)

        def current_scene(self, scenes, *, update):
            assert update is True
            if list(scenes) == [381, 318, 443, 380, 588]:
                scene_id = self.kick_confirm_states.pop(0)
                return scene_id, 100.0, f"frame{scene_id}"
            if list(scenes) == [380]:
                return 380, 99.0, "frame380"
            if list(scenes) == [306]:
                return 306, 100.0, "summary"
            raise AssertionError(scenes)

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return args[2] if len(args) > 2 else True

        def advance_dialogue(self, *args, **kwargs):
            self.actions.append(("advance_dialogue", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 1

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def goto_view(self, scene_id):
            self.actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING
            return "success"

        @staticmethod
        def ocr_text(_frame):
            return "驱离确认"

    runtime = FakeRuntime()
    result = _drain_generator(runner._click_daily_lingmai_kick_target(
        {},
        threading.Event(),
        {
            "__lingmai_post_action_runtime_snapshot_override": {
                "available": True,
                "complete": True,
                "completed": False,
                "remaining_milliseconds": 10_800_000,
                "self_seat_facts": {"seated": True},
            },
        },
        runtime,
        target_player={"seat_id": 7, "name": "目标玩家", "battle_score": 100},
        task_label="灵脉_座位",
    ))

    assert result == "skipped"
    assert scheduled[0]["seconds"] == 1800
    assert runtime.clicks == [(286, 800.0, 730.0)]
    assert any(
        action[0] == "click_shape_center"
        and getattr(action[1], "id", None) == 306
        and getattr(action[2], "title", None) == "确认"
        for action in runtime.actions
    )
    assert ("wait_click", (380, "驱离"), {}) in runtime.actions
    assert runtime.actions.count(("wait_click", (380, "驱离"), {})) == 1
    assert (
        "wait_scene",
        (381, 318, 443),
        {
            "timeout": 20.0,
            "label": "灵脉_座位：#380「驱离」点击后等待业务确认层/战前对白",
        },
    ) in runtime.actions
    assert (
        "wait_click",
        (381, "确定"),
        {"timeout": 20.0},
    ) in runtime.actions
    assert (
        "wait_scene",
        (318, 443),
        {"timeout": 45.0, "label": "灵脉_座位：点击 #381「确定」后等待更换确认或战前对白"},
    ) in runtime.actions
    assert (
        "wait_scene",
        (318, 303, 443, 289, 305, 306, 85, 186, 285, 588),
        {
            "timeout": 45.0,
            "label": "灵脉_座位：推进战后对白直到换座/入座确认或稳定灵脉场景",
        },
    ) in runtime.actions
    assert ("wait_click", (375, "关闭"), {}) in runtime.actions
    assert ("click_shape_center", 318, "确认") in runtime.actions
    assert ("click_shape_center", 303, "对话") in runtime.actions
    assert runtime.actions[-1] == ("goto_view", 34)


def test_daily_lingmai_kick_never_reclicks_380_from_background_588():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []
            self.states = iter([380, 588, 588])

        def current_scene(self, scenes, *, update):
            assert list(scenes) == [381, 318, 443, 380, 588]
            assert update is True
            scene_id = next(self.states)
            self.actions.append(("current_scene", scene_id))
            return scene_id, 100.0, f"frame{scene_id}"

        def wait_click(self, *args, **kwargs):
            self.actions.append(("wait_click", args, kwargs))
            yield BehaviorTreeStatus.RUNNING

        def wait_scene(self, *scenes, **kwargs):
            self.actions.append(("wait_scene", scenes, kwargs))
            yield BehaviorTreeStatus.RUNNING
            raise TimeoutError("confirmation not identified")

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

    runtime = FakeRuntime()
    with pytest.raises(RuntimeError, match="未重复点击背景/unknown"):
        _drain_generator(
            runner._complete_daily_lingmai_kick(
                runtime,
                {},
                task_label="灵脉_座位",
            )
        )

    assert runtime.actions.count(("wait_click", (380, "驱离"), {})) == 1
    assert (
        "wait_scene",
        (381, 318, 443),
        {
            "timeout": 20.0,
            "label": "灵脉_座位：#380「驱离」点击后等待业务确认层/战前对白",
        },
    ) in runtime.actions


def test_daily_lingmai_kick_handles_switch_before_battle_and_289_after_win(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    switch_confirms = []

    def fake_switch_confirm(_runtime, _payload, **kwargs):
        switch_confirms.append(kwargs)
        yield BehaviorTreeStatus.RUNNING

    monkeypatch.setattr(runner, "_click_daily_lingmai_switch_confirm", fake_switch_confirm)
    monkeypatch.setattr(
        runner,
        "_schedule_daily_lingmai_next_check",
        lambda _payload, **_kwargs: "2026-08-03 20:00:00",
    )

    class FakeRuntime:
        def __init__(self):
            self.actions = []
            self.dialogue_sequences = {
                (318, 303, 374, 382, 375, 588): [318, 382],
                (318, 303, 443, 289, 305, 306, 85, 186, 285, 588): [318, 289],
            }

        def wait_click(self, *args, **kwargs):
            self.actions.append(("wait_click", args, kwargs))
            yield BehaviorTreeStatus.RUNNING

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def click_shape(self, scene, shape):
            self.actions.append(("click_shape", scene, shape))

        def click_shape_center(self, scene, shape):
            self.actions.append(("click_shape_center", scene, shape))

        def wait_scene(self, *scenes, **kwargs):
            self.actions.append(("wait_scene", scenes, kwargs))
            yield BehaviorTreeStatus.RUNNING
            if scenes == (318, 443):
                return 443
            if scenes == (382, 318, 303, 443, 289, 305, 306, 85, 186, 285, 588):
                return 318
            if scenes in self.dialogue_sequences:
                return self.dialogue_sequences[scenes].pop(0)
            raise AssertionError(scenes)

        def wait_leave_view(self, *args, **kwargs):
            self.actions.append(("wait_leave_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING

        def wait_view(self, *args, **kwargs):
            self.actions.append(("wait_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return args[0]

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return args[2]

        def current_scene(self, scenes, *, update):
            assert update is True
            if list(scenes) == [381, 318, 443, 380, 588]:
                return 381, 100.0, "frame381"
            assert list(scenes) == [186, 85, 34, 285]
            return 85, 100.0, "lingmai-room"

        def goto_view(self, scene_id):
            self.actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING
            return "success"

        @staticmethod
        def ocr_text(_frame):
            return ""

    runtime = FakeRuntime()
    result = _drain_generator(runner._complete_daily_lingmai_kick(
        runtime,
        {
            "__lingmai_post_action_runtime_snapshot_override": {
                "available": True,
                "complete": True,
                "completed": False,
                "remaining_milliseconds": 10_800_000,
                "self_seat_facts": {"seated": True},
            },
        },
        task_label="灵脉_座位",
    ))

    assert result == "skipped"
    assert switch_confirms == [{"task_label": "灵脉_座位", "scene_id": 443}]
    assert (
        "wait_view",
        (318,),
        {"timeout": 45.0, "label": "灵脉_座位：确认更换灵脉后等待 #318 战前对白"},
    ) in runtime.actions
    assert (
        "wait_click_then_view",
        (289, "确认", [186, 85, 34, 285]),
        {"timeout": 30.0},
    ) in runtime.actions
    assert runtime.actions[-1] == ("goto_view", 34)


def test_daily_lingmai_victory_closes_each_fresh_382_layer():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.landings = [382, 588]
            self.actions = []

        def wait_click(self, *args, **kwargs):
            self.actions.append(("click", args, kwargs))
            yield BehaviorTreeStatus.RUNNING

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def wait_scene(self, *args, **kwargs):
            self.actions.append(("scene", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return self.landings.pop(0)

    runtime = FakeRuntime()
    result = _drain_generator(runner._close_daily_lingmai_victory_layers(
        runtime,
        {},
        task_label="灵脉_座位",
    ))

    assert result == 588
    assert [action[0] for action in runtime.actions] == [
        "click", "settle", "scene", "click", "settle", "scene",
    ]
    assert all(
        action[1] == (382, "关闭")
        for action in runtime.actions
        if action[0] == "click"
    )


def test_daily_lingmai_588_uses_icon_then_business_leave_confirmation():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        ctx = {}

        def __init__(self):
            self.actions = []

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append((args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 289 if args[0] == 588 else 34

        def current_scene(self, scenes, *, update):
            assert list(scenes) == [186, 85, 34, 285]
            assert update is True
            return 34, 100.0, "world"

        @staticmethod
        def ocr_text(_frame=None, **_kwargs):
            return ""

    runtime = FakeRuntime()
    result = _drain_generator(runner._finish_daily_lingmai_to_world(
        runtime,
        {
            "__lingmai_post_action_runtime_snapshot_override": {
                "available": True,
                "complete": True,
                "completed": True,
                "remaining_milliseconds": 0,
            },
        },
        task_label="灵脉_座位",
        scene_id=588,
        frame="occupied",
    ))

    assert result == "success"
    assert runtime.actions == [
        ((588, "离开", [289, 186, 85, 34, 285]), {"timeout": 30.0}),
        ((289, "确认", [186, 85, 34, 285]), {"timeout": 30.0}),
    ]


def test_daily_lingmai_old_trigger_fields_are_discarded():
    repaired, changed = repair_data_annotation_scheduler_tasks(
        [{
            "id": "daily-lingmai-seat",
            "task_type": "daily_lingmai",
            "label": "灵脉_座位",
            "source": "data_annotation_runtime",
            "schedule_kind": "manual",
            "schedule_times": [],
            "enabled": False,
            "payload": {},
        }],
        _default_data_annotation_scheduler_tasks(),
        {},
        task_supported=lambda _task: True,
        now=datetime(2026, 7, 16, 16, 0, 0),
    )

    task = next(item for item in repaired if item["id"] == "daily-lingmai-seat")
    assert changed is True
    assert "schedule_kind" not in task
    assert "schedule_times" not in task
    assert "enabled" not in task
    assert task["next_time"]
    assert task["error_retry_delay_seconds"] == 600
    assert task["trigger_description"] == "动态"
    assert task["template_label"] == "灵脉_座位"
    assert task["payload"]["lingmai_no_target_retry_seconds"] == 1800
    assert task["payload"]["daily_start_time"] == "17:30"


def test_daily_lingmai_skipped_recheck_does_not_schedule_next_day():
    runner = create_behavior_tree_runtime_runner()
    completed = []

    def fake_run(*_args, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "skipped"

    runner._run_daily_lingmai_task = fake_run
    runner._record_daily_lingmai_done = lambda *_args, **_kwargs: completed.append(True)

    result = _drain_generator(runner._execute_daily_lingmai_task({}, threading.Event(), {}))

    assert result == {
        "result": "skipped",
        "message": "灵脉_座位：本次已安全结束并写入后续检查时间",
    }
    assert completed == []


def test_daily_lingmai_gather_confirm_uses_scene_305():
    runner = create_behavior_tree_runtime_runner()
    scheduled = []
    runner._schedule_daily_lingmai_next_check = (
        lambda _payload, **kwargs: (
            scheduled.append(kwargs)
            or "2026-07-30 17:40:00"
        )
    )

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return True

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def current_scene(self, *_args, **_kwargs):
            return 303, 100.0, "after"

        def ocr_text(self, _frame):
            return "此处灵脉绵延悠远"

        def cur_frame(self, **_kwargs):
            return "after"

        def goto_view(self, scene_id):
            self.actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING
            return "success"

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._confirm_daily_lingmai_gather(
            runtime,
            {
                "__lingmai_post_action_runtime_snapshot_override": {
                    "available": False,
                    "complete": False,
                },
            },
            task_label="灵脉_座位",
        )
    )

    assert result == "skipped"
    assert runtime.actions[0] == (
        "wait_click_then_view",
        (305, "确定"),
        {"wait_leave": True, "timeout": 20.0},
    )
    assert scheduled[0]["seconds"] == 600


def test_daily_lingmai_reward_confirm_uses_scene_318():
    runner = create_behavior_tree_runtime_runner()
    scheduled = []
    runner._schedule_daily_lingmai_next_check = (
        lambda _payload, **kwargs: (
            scheduled.append(kwargs)
            or "2026-07-30 17:40:00"
        )
    )

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return True

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def current_scene(self, *_args, **_kwargs):
            return 303, 100.0, "after"

        def ocr_text(self, _frame):
            return "此处灵脉绵延悠远"

        def cur_frame(self, **_kwargs):
            return "after"

        def goto_view(self, scene_id):
            self.actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING
            return "success"

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._confirm_daily_lingmai_reward(
            runtime,
            {
                "__lingmai_post_action_runtime_snapshot_override": {
                    "available": False,
                    "complete": False,
                },
            },
            task_label="灵脉_座位",
        )
    )

    assert result == "skipped"
    assert runtime.actions[0] == (
        "wait_click_then_view",
        (318, "确认"),
        {"wait_leave": True, "timeout": 20.0},
    )
    assert runtime.actions[-1] == ("goto_view", 34)
    assert scheduled[0]["seconds"] == 600


def test_daily_lingmai_reward_exit_keeps_scene_186_in_layer0_and_clicks_leave():
    runner = create_behavior_tree_runtime_runner()
    scheduled = []
    runner._schedule_daily_lingmai_next_check = (
        lambda _payload, **kwargs: (
            scheduled.append(kwargs)
            or "2026-07-30 18:00:00"
        )
    )

    class FakeRuntime:
        def __init__(self):
            self.ctx = {"images": {443: _image("灵脉更换确认", "0443.png", [{"title": "确定", "x": 0.4, "y": 0.58, "w": 0.2, "h": 0.08}])}}
            self.actions = []

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            if args and args[0] in {186, 85}:
                return 34
            return True

        def expect_views(self, *_scene_ids):
            return nullcontext(self)

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def current_scene(self, scene_ids, **_kwargs):
            self.actions.append(("current_scene", tuple(scene_ids)))
            return 186, 100.0, "lingmai-exit"

        def wait_scene(self, *scene_ids, **kwargs):
            self.actions.append(("wait_scene", scene_ids, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 34

        def ocr_text(self, _frame=None, **_kwargs):
            return "仙煌神脉 本次聚灵剩余 02:55:01 探索 离开"

        def wait_click(self, scene_id, shape_title, **_kwargs):
            self.actions.append(("wait_click", scene_id, shape_title))
            yield BehaviorTreeStatus.RUNNING

        def wait_view(self, *scene_ids, **kwargs):
            self.actions.append(("wait_view", scene_ids, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 34

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._confirm_daily_lingmai_reward(
            runtime,
            {
                "__lingmai_post_action_runtime_snapshot_override": {
                    "available": True,
                    "complete": True,
                    "completed": False,
                    "remaining_milliseconds": 10_501_000,
                    "self_seat_facts": {"seated": True},
                },
            },
            task_label="灵脉_座位",
        )
    )

    assert result == "skipped"
    assert scheduled[0]["seconds"] == 1800
    assert ("current_scene", (306, 303, 285, 186, 34, 318, 59)) in runtime.actions
    assert runtime.actions[-1] == (
        "wait_click_then_view",
        (186, "离开", [47, 34, 69, 289, 86, 85, 386, 375, 295]),
        {
            "settle_seconds": 1.5,
            "timeout": 12.0,
            "max_clicks": 1,
        },
    )


def test_shared_scene_186_leave_retries_stable_self_loop_then_returns_world():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []
            self.attempt = 0

        def expect_views(self, *_scene_ids):
            return nullcontext(self)

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            self.attempt += 1
            if self.attempt == 1:
                raise TimeoutError("source remained")
            return 34

        def current_scene(self, *_args, **_kwargs):
            return 186, 100.0, "same"

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._leave_shared_scene_186_to_world(runtime, label="论道_座位")
    )

    assert result == "success"
    assert len(runtime.actions) == 2
    assert all(action[0] == "wait_click_then_view" for action in runtime.actions)


def test_shared_scene_186_leave_accepts_view_result():
    from pyxllib.autogui import View

    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def expect_views(self, *_scene_ids):
            return nullcontext(self)

        def wait_click_then_view(self, *_args, **_kwargs):
            yield BehaviorTreeStatus.RUNNING
            return View({"id": 34, "title": "世界"})

    result = _drain_generator(
        runner._leave_shared_scene_186_to_world(FakeRuntime(), label="论道_座位")
    )

    assert result == "success"


def test_shared_scene_186_leave_consumes_confirmation_from_final_budget_step():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []
            self.leave_count = 0

        def expect_views(self, *_scene_ids):
            return nullcontext(self)

        def wait_click_then_view(self, scene_id, shape_title, *args, **kwargs):
            self.actions.append((scene_id, shape_title, args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            if scene_id in {186, 85}:
                self.leave_count += 1
                return 289 if self.leave_count == 4 else 85
            assert scene_id == 289
            assert shape_title == "确认"
            return 34

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._leave_shared_scene_186_to_world(runtime, label="灵脉_座位")
    )

    assert result == "success"
    assert runtime.leave_count == 4
    assert runtime.actions[-1][0:2] == (289, "确认")


def test_shared_scene_186_leave_routes_known_activity_overlay_to_world():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def expect_views(self, *_scene_ids):
            return nullcontext(self)

        def wait_click_then_view(self, *_args, **_kwargs):
            yield BehaviorTreeStatus.RUNNING
            return 386

        def goto_view(self, scene_id):
            self.actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING
            return scene_id

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._leave_shared_scene_186_to_world(runtime, label="论道_座位")
    )

    assert result == "success"
    assert runtime.actions == [("goto_view", 34)]


def test_shared_scene_186_leave_claims_parent_popup_until_specific_confirmation():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []
            self.landings = iter([47, 34])

        def expect_views(self, *scene_ids):
            self.actions.append(("expect_views", scene_ids))
            return nullcontext(self)

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return next(self.landings)

        def click_shape_center(self, *args):
            self.actions.append(("click_shape_center", args))

        def wait_action_settle(self, seconds):
            self.actions.append(("wait_action_settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def wait_view(self, *scene_ids, **kwargs):
            self.actions.append(("wait_view", scene_ids, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 34

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._leave_shared_scene_186_to_world(runtime, label="论道_座位")
    )

    assert result == "success"
    assert ("click_shape_center", (86, "确认")) in runtime.actions
    assert ("wait_view", (34, 69, 186, 85, 53, 386, 375, 295), {
        "timeout": 15.0,
        "label": "论道_座位：确认离开后等待落点",
    }) in runtime.actions


def test_world_side_exit_context_accepts_scene_186_and_clicks_its_own_leave(monkeypatch):
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []
            self.scenes = iter([
                (186, 100.0, "inside"),
                (34, 100.0, "world"),
            ])

        def current_scene(self, scene_ids, **_kwargs):
            self.actions.append(("current_scene", tuple(scene_ids)))
            return next(self.scenes)

        def wait_click(self, scene_id, shape_title, **_kwargs):
            self.actions.append(("wait_click", scene_id, shape_title))
            yield BehaviorTreeStatus.RUNNING

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def wait_view(self, *scene_ids, **_kwargs):
            self.actions.append(("wait_view", scene_ids))
            yield BehaviorTreeStatus.RUNNING
            return 34

    runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)
    ctx = {}
    result = _drain_generator(runner._leave_world_side_scene_if_present(
        ctx,
        threading.Event(),
        "inside",
        "离开",
        label="测试退场",
    ))

    assert result is True
    assert runtime.actions[0] == (
        "current_scene",
        (477, 66, 326, 325, 266, 265, 264, 233, 225, 85, 186, 289, 86, 69, 34),
    )
    assert ("wait_click", 186, "离开") in runtime.actions
    assert ctx["_go_scene_known_scene_id"] == 34


def test_daily_lingmai_summary_popup_clicks_ocr_confirm_before_returning_world(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    scheduled = []
    monkeypatch.setattr(
        runner,
        "_schedule_daily_lingmai_next_check",
        lambda _payload, **kwargs: (
            scheduled.append(kwargs)
            or "2026-07-30 18:00:00"
        ),
    )

    class FakeRuntime:
        def __init__(self):
            self.ctx = {}
            self.actions = []
            self.scenes = iter([(59, 100.0, "inside")])

        def ocr_text(self, _frame=None, **_kwargs):
            return "灵脉聚灵收益:960/分 今日聚灵剩余：03:00:00 基础保护时间 确认"

        def view(self, scene_id):
            return behavior_tree_runtime_core.View(_image("灵脉收益确认", f"{scene_id:04d}.png", [{"title": "确认", "x": 0.4, "y": 0.65, "w": 0.2, "h": 0.08}]))

        def click_shape_center(self, view, shape):
            self.actions.append(("click_shape_center", view.id, shape.title))

        def click_frame_point(self, *args):
            self.actions.append(("click_frame_point", args))

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def current_scene(self, *_args, **_kwargs):
            return next(self.scenes)

        def cur_frame(self, **_kwargs):
            return "inside"

        def wait_scene(self, *scenes, **kwargs):
            self.actions.append(("wait_scene", scenes, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 85

        def goto_view(self, scene_id):
            self.actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING
            return "success"

    confirm_tokens = _ocr_tokens("确认", x=407.0, y=1042.0, w=100.0, h=54.0)
    for index, token in enumerate(confirm_tokens):
        token.update(parent_line_id="line-0", line_order=0, order=index)
    monkeypatch.setattr(
        runner,
        "_cached_ocr_tokens",
        lambda *_args, **_kwargs: confirm_tokens,
    )

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._finish_daily_lingmai_to_world(
            runtime,
            {
                "__lingmai_post_action_runtime_snapshot_override": {
                    "available": True,
                    "complete": True,
                    "completed": False,
                    "remaining_milliseconds": 10_800_000,
                    "self_seat_facts": {"seated": True},
                },
            },
            task_label="灵脉_座位",
            scene_id=306,
            frame="summary",
        )
    )

    assert result == "skipped"
    assert runtime.actions[0] == ("click_shape_center", 306, "确认")
    assert runtime.actions[-1] == ("goto_view", 34)
    assert scheduled[0]["seconds"] == 1800


def test_daily_lingmai_switch_popup_verifies_semantics_and_clicks_ocr_confirm(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    finishes = []

    def fake_finish(_runtime, _payload, **kwargs):
        finishes.append(kwargs)
        yield BehaviorTreeStatus.RUNNING
        return "skipped"

    monkeypatch.setattr(runner, "_finish_daily_lingmai_to_world", fake_finish)
    confirm_tokens = _ocr_tokens("确定", x=465.0, y=930.0, w=90.0, h=48.0)
    for index, token in enumerate(confirm_tokens):
        token.update(parent_line_id="line-confirm", line_order=0, order=index)
    monkeypatch.setattr(runner, "_cached_ocr_tokens", lambda *_args, **_kwargs: confirm_tokens)

    class FakeRuntime:
        def __init__(self):
            self.ctx = {"images": {443: _image("灵脉更换确认", "0443.png", [{"title": "确定", "x": 0.4, "y": 0.58, "w": 0.2, "h": 0.08}])}}
            self.actions = []

        def ocr_text(self, _frame=None, **_kwargs):
            return "提示 你已在仙煌神脉聚灵，是否更换到该灵脉？当前灵脉：天罡圣脉 取消 确定"

        def ocr_text_in_shapes(self, scene_id, shapes, *, frame_data_url):
            assert scene_id == 443
            assert shapes == ("是否更换",)
            assert frame_data_url == "switch-popup"
            return "你已在仙煌神脉聚灵，是否更换到该灵脉？"

        def click_frame_point(self, *args):
            self.actions.append(("click_frame_point", args))

        def click_shape_center(self, scene_id, shape):
            self.actions.append(("click_shape_center", (scene_id, shape)))

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def wait_scene(self, *scene_ids, **kwargs):
            self.actions.append(("wait_scene", scene_ids, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 289

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 34

        def current_scene(self, *_args, **_kwargs):
            return 34, 100.0, "after-switch"

    runtime = FakeRuntime()
    result = _drain_generator(runner._confirm_daily_lingmai_switch_popup(
        runtime,
        {},
        task_label="灵脉_座位",
        frame="switch-popup",
    ))

    assert result == "skipped"
    assert runtime.actions[0] == ("click_shape_center", (443, "确定"))
    assert ("wait_click_then_view", (289, "确认", [186, 85, 34, 285]), {"timeout": 30.0}) in runtime.actions
    assert finishes == [{"task_label": "灵脉_座位", "scene_id": 34, "frame": "after-switch"}]


def test_daily_lingmai_switch_rejects_generic_popup_owner():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def cur_frame(self, **_kwargs):
            return "generic-popup"

    with pytest.raises(RuntimeError, match="通用提示 #47 不拥有灵脉更换确认权限"):
        _drain_generator(runner._click_daily_lingmai_switch_confirm(
            FakeRuntime(),
            {},
            task_label="灵脉_座位",
            scene_id=47,
        ))


def test_daily_lingmai_zero_daily_remaining_finishes_without_reentering_seat_flow(monkeypatch):
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.ctx = {}
            self.actions = []

        def ocr_text(self, frame=None, **_kwargs):
            if frame == "summary":
                return "上次聚灵：03:00:00 聚灵收益：33.19万 今日聚灵剩余：00:00:00 确认"
            return "联盟灵脉"

        def view(self, scene_id):
            return behavior_tree_runtime_core.View(_image("灵脉收益确认", f"{scene_id:04d}.png", [{"title": "确认", "x": 0.4, "y": 0.65, "w": 0.2, "h": 0.08}]))

        def click_shape_center(self, view, shape):
            self.actions.append(("click_shape_center", view.id, shape.title))

        def click_frame_point(self, *args):
            self.actions.append(("click_frame_point", args))

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def wait_scene(self, *scenes, **kwargs):
            self.actions.append(("wait_scene", scenes, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 312

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 285

        def current_scene(self, *_args, **_kwargs):
            return 285, 100.0, "zaohua"

        def goto_view(self, scene_id):
            self.actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING
            return "success"

    confirm_tokens = _ocr_tokens("确认", x=407.0, y=1042.0, w=100.0, h=54.0)
    for index, token in enumerate(confirm_tokens):
        token.update(parent_line_id="line-0", line_order=0, order=index)
    monkeypatch.setattr(runner, "_cached_ocr_tokens", lambda *_args, **_kwargs: confirm_tokens)

    runtime = FakeRuntime()
    result = _drain_generator(runner._finish_daily_lingmai_to_world(
        runtime,
        {},
        task_label="灵脉_座位",
        scene_id=306,
        frame="summary",
    ))

    assert result == "success"
    assert ("wait_click_then_view", (312, "确认", [285, 85, 186, 34]), {"timeout": 20.0}) in runtime.actions
    assert runtime.actions[-1] == ("goto_view", 34)


def test_daily_lingmai_frame_306_clicks_ocr_confirm_before_returning_world(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    scheduled = []
    monkeypatch.setattr(
        runner,
        "_schedule_daily_lingmai_next_check",
        lambda _payload, **kwargs: (
            scheduled.append(kwargs)
            or "2026-07-30 17:40:00"
        ),
    )

    class FakeRuntime:
        def __init__(self):
            self.ctx = {}
            self.actions = []
            self.scenes = iter([(59, 100.0, "inside")])

        def ocr_text(self, _frame=None, **_kwargs):
            return "灵脉聚灵收益 今日聚灵剩余 确定"

        def view(self, scene_id):
            return behavior_tree_runtime_core.View(_image("灵脉收益确认", f"{scene_id:04d}.png", [{"title": "确定", "x": 0.4, "y": 0.65, "w": 0.2, "h": 0.08}]))

        def click_shape_center(self, view, shape):
            self.actions.append(("click_shape_center", view.id, shape.title))

        def click_frame_point(self, *args):
            self.actions.append(("click_frame_point", args))

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def current_scene(self, *_args, **_kwargs):
            return next(self.scenes)

        def cur_frame(self, **_kwargs):
            return "inside"

        def wait_scene(self, *scenes, **kwargs):
            self.actions.append(("wait_scene", scenes, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 85

        def goto_view(self, scene_id):
            self.actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING
            return "success"

    confirm_tokens = _ocr_tokens("确定", x=398.0, y=1042.0, w=110.0, h=54.0)
    for index, token in enumerate(confirm_tokens):
        token.update(parent_line_id="line-0", line_order=0, order=index)
    monkeypatch.setattr(
        runner,
        "_cached_ocr_tokens",
        lambda *_args, **_kwargs: confirm_tokens,
    )

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._finish_daily_lingmai_to_world(
            runtime,
            {
                "__lingmai_post_action_runtime_snapshot_override": {
                    "available": False,
                    "complete": False,
                    "reason": "not initialized",
                },
            },
            task_label="灵脉_座位",
            scene_id=306,
            frame="summary",
        )
    )

    assert result == "skipped"
    assert runtime.actions[0] == ("click_shape_center", 306, "确定")
    assert runtime.actions[-1] == ("goto_view", 34)
    assert scheduled[0]["seconds"] == 600


def test_daily_lingmai_uses_scene_graph_for_full_multi_step_exit():
    runner = create_behavior_tree_runtime_runner()
    scheduled = []
    runner._schedule_daily_lingmai_next_check = (
        lambda _payload, **kwargs: (
            scheduled.append(kwargs)
            or "2026-07-30 18:00:00"
        )
    )

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def ocr_text(self, frame=None, **_kwargs):
            return "信息 仙煌神脉 剩余空位0/10 本次聚灵剩余02:57:16 保护时间剩余00:02:16 离开"

        def goto_view(self, scene_id):
            self.actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING
            return "success"

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._finish_daily_lingmai_to_world(
            runtime,
            {
                "__lingmai_post_action_runtime_snapshot_override": {
                    "available": True,
                    "complete": True,
                    "completed": False,
                    "remaining_milliseconds": 10_636_000,
                    "self_seat_facts": {"seated": True},
                },
            },
            task_label="灵脉_座位",
            scene_id=285,
            frame="stale-frame",
        )
    )

    assert result == "skipped"
    assert runtime.actions == [("goto_view", 34)]
    assert scheduled[0]["seconds"] == 1800


def test_daily_lingmai_final_occupy_waits_for_switch_popup(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    calls = []

    def confirm_switch(runtime, payload, *, task_label, scene_id, frame):
        calls.append((runtime, payload, task_label, scene_id, frame))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_confirm_daily_lingmai_switch_popup", confirm_switch)

    class FakeRuntime:
        def wait_click_then_view(self, scene, shape, targets, **kwargs):
            assert (scene, shape) == (288, "占领")
            assert 288 not in targets
            assert 443 in targets
            assert kwargs == {"settle_seconds": 2.0, "timeout": 20.0, "max_clicks": 2}
            if False:
                yield None
            return 443

        def current_scene(self, targets, *, update):
            assert 288 not in targets
            assert update is True
            return 443, 100.0, "switch-popup"

        def ocr_text(self, frame):
            assert frame == "switch-popup"
            return "你已在仙煌神脉聚灵，是否更换到该灵脉？当前灵脉：天罡圣脉 确定"

    runtime = FakeRuntime()
    result = _drain_generator(runner._continue_daily_lingmai_from_final_occupy(
        {"images": {288: {"shapes": [{"title": "占领"}]}}},
        threading.Event(),
        {},
        runtime,
        task_label="灵脉_座位",
    ))

    assert result == "success"
    assert calls == [(runtime, {}, "灵脉_座位", 443, "switch-popup")]


def test_daily_lingmai_zero_stamina_token_closes_today_without_slot_click(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    scheduled = []
    monkeypatch.setattr(
        daily_foundation._behavior_tree_runtime,
        "_now",
        lambda: datetime(2026, 8, 12, 21, 48, 38),
    )
    monkeypatch.setattr(runner, "_persist_scheduler_task_next_time", lambda task_id, value: scheduled.append((task_id, value)))

    class FakeRuntime:
        def wait_shape(self, scene, shape, **kwargs):
            assert (scene, shape) == (286, "选择空位")
            assert kwargs["threshold"] == 55.0
            if False:
                yield None
            return "select-frame"

        def shape_score(self, *_args, **_kwargs):
            return 100.0

        def wait_click(self, *_args, **_kwargs):
            if False:
                yield None

        def wait_action_settle(self, *_args, **_kwargs):
            if False:
                yield None

        def current_scene(self, candidates, **_kwargs):
            assert 287 in candidates
            return None, 0.0, "stamina-popup"

        def ocr_text(self, frame):
            assert frame == "stamina-popup"
            return "聚灵体力符持有数量：0"

    monkeypatch.setattr(
        daily_foundation,
        "refresh_lingmai_daily_status",
        lambda: {"available": False},
    )
    monkeypatch.setattr(
        daily_foundation,
        "refresh_and_select_lingmai_seat_action",
        lambda **_kwargs: {"ok": True, "action": "occupy_empty", "room_id": 17},
    )

    result = _drain_generator(runner._continue_daily_lingmai_from_select_slot(
        {
            "images": {
                286: {"shapes": [{"title": "选择空位"}, {"title": "占领"}, {"title": "返回"}]},
                287: {"shapes": [{"title": "前往灵脉"}, {"title": "确认"}]},
                288: {"shapes": [{"title": "占领"}]},
            }
        },
        threading.Event(),
        {"__scheduler_task_id": "daily-lingmai-seat", "daily_start_time": "17:30"},
        FakeRuntime(),
        "select-frame",
        task_label="灵脉_座位",
    ))

    assert result["ok"] is False
    assert result["outcome"] == "resource_insufficient"
    assert "next_time" not in result
    assert scheduled == [("daily-lingmai-seat", "2026-08-13 17:30:00")]
    assert scheduled == [("daily-lingmai-seat", "2026-08-13 17:30:00")]


def test_daily_lingmai_reopens_entry_once_when_popup_returns_to_daily():
    runner = create_behavior_tree_runtime_runner()
    waits = []
    actions = []

    class FakeRuntime:
        def wait_view(self, *scene_ids, **options):
            waits.append((scene_ids, options))
            if len(waits) == 1:
                raise TimeoutError("transient popup returned to daily")
            if False:
                yield None
            return 285

        def current_scene(self, scene_ids, **options):
            actions.append(("current_scene", scene_ids, options))
            return 69, 98.0, "daily-frame"

        def open_daily_entry(self, **options):
            actions.append(("open_daily_entry", options))
            if False:
                yield None
            return "open"

        def view(self, scene_id):
            return scene_id

    result = _drain_generator(
        runner._wait_daily_lingmai_zaohua_after_entry(
            FakeRuntime(),
            {"lingmai_entry_timeout": 25},
            task_label="灵脉_座位",
        )
    )

    assert result == 285
    assert [call[0] for call in actions] == ["current_scene", "open_daily_entry"]
    reopen_options = actions[1][1]
    assert reopen_options["max_scrolls"] == 0
    assert reopen_options["initial_checks"] == 1
    assert waits[0][1]["timeout"] <= 8.0
    assert waits[1][1]["timeout"] > 8.0


def test_daily_lundao_empty_seat_does_not_map_ocr_prompt_to_another_scene(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    actions = []
    monotonic_values = iter([0.0, 1.0, 46.0])
    monkeypatch.setattr(daily_foundation.time, "monotonic", lambda: next(monotonic_values))

    class FakeRuntime:
        def wait_click(self, scene_id, shape):
            actions.append(("click", scene_id, shape))
            if False:
                yield None

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield None

        def cur_frame(self, update=False):
            assert update is True
            return "seat-choice"

        def ocr_text(self, frame):
            assert frame == "seat-choice"
            return "听道座位  灵气飘渺，此处座位甚佳！  入座  再看看别的座位"

        def current_scene(self, candidates, **_kwargs):
            assert candidates == [300, 329, 301, 302, 303]
            return 30, 100.0, "seat-choice"

    with pytest.raises(TimeoutError, match="#300/#329/#301/#302/#303"):
        _drain_generator(runner._run_daily_lundao_empty_seat_strategy(FakeRuntime()))
    assert actions == [("click", 298, "入座"), ("settle", 1.5), ("settle", 1.0)]


def test_daily_lundao_empty_seat_accepts_delayed_confirm_prompt(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    monotonic_values = iter([0.0, 1.0, 26.0])
    monkeypatch.setattr(daily_foundation.time, "monotonic", lambda: next(monotonic_values))
    scenes = iter(
        [
            (30, 100.0, "seat-choice"),
            (300, 100.0, "dojo-confirm"),
        ]
    )

    class FakeRuntime:
        def wait_click(self, _scene_id, _shape):
            if False:
                yield None

        def wait_action_settle(self, _seconds):
            if False:
                yield None

        def cur_frame(self, update=False):
            assert update is True
            return "frame"

        def ocr_text(self, _frame):
            return "是否要前往该道场 取消 确认"

        def current_scene(self, candidates, **_kwargs):
            assert candidates == [300, 329, 301, 302, 303]
            return next(scenes)

    assert _drain_generator(
        runner._run_daily_lundao_empty_seat_strategy(FakeRuntime())
    ) == (300, 100.0)


def test_daily_lundao_empty_seat_accepts_exact_travel_prompt_ocr_fallback(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    monotonic_values = iter([0.0, 1.0])
    monkeypatch.setattr(daily_foundation.time, "monotonic", lambda: next(monotonic_values))

    class FakeRuntime:
        def wait_click(self, _scene_id, _shape):
            if False:
                yield None

        def wait_action_settle(self, _seconds):
            if False:
                yield None

        def cur_frame(self, update=False):
            assert update is True
            return "dojo-confirm"

        def ocr_text(self, _frame):
            return "提宗 是否要前往该道场？ 取消 确认"

        def current_scene(self, candidates, **_kwargs):
            assert candidates == [300, 329, 301, 302, 303]
            return None, 0.0, "dojo-confirm"

    assert _drain_generator(
        runner._run_daily_lundao_empty_seat_strategy(FakeRuntime())
    ) == (300, 0.0)


def test_daily_lundao_travel_confirmation_treats_300_and_329_as_equivalent():
    runner = create_behavior_tree_runtime_runner()
    actions = []

    class FakeRuntime:
        def current_scene(self, candidates, **kwargs):
            assert candidates == [300, 329, 301, 302, 303, 52, 53, 34, 69]
            assert kwargs == {"update": True}
            return 329, 100.0, "dojo-confirm"

        def ocr_text(self, _frame):
            return "是否要前往该道场 取消 确认"

        def click_shape_center(self, scene_id, shape_title):
            actions.append(("click", scene_id, shape_title))

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield None

        def wait_scene(self, *scene_ids, **kwargs):
            actions.append(("wait_scene", scene_ids, kwargs))
            if False:
                yield None
            return 301

    assert _drain_generator(
        runner._advance_daily_lundao_dojo_travel_confirmation(FakeRuntime(), 300)
    ) == (301, 100.0)
    assert actions == [
        ("click", 329, "确认"),
        ("settle", 1.5),
        (
            "wait_scene",
            (300, 329, 301, 302, 303, 52, 53, 34, 69),
            {"timeout": 20.0, "label": "论道_座位：确认前往道场后等待入座链"},
        ),
    ]


def test_daily_lundao_travel_confirmation_waits_through_scene53_auto_navigation(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    scenes = iter(
        (
            (300, 100.0, "travel-confirm"),
            (53, 100.0, "navigating"),
            (53, 100.0, "stable-unconfirmed"),
            (53, 100.0, "stable-confirmed"),
        )
    )
    runtime_checks = []
    runtime_results = iter((False, True))
    actions = []

    class FakeRuntime:
        def current_scene(self, candidates, **kwargs):
            assert candidates == [300, 329, 301, 302, 303, 52, 53, 34, 69]
            assert kwargs == {"update": True}
            return next(scenes)

        def ocr_text(self, frame):
            return {
                "travel-confirm": "是否要前往该道场 取消 确认",
                "navigating": "闻道感悟 剩余座位 离开 自动寻路中",
                "stable-unconfirmed": "闻道感悟 剩余座位 离开",
                "stable-confirmed": "闻道感悟 剩余座位 离开",
            }[frame]

        def click_shape_center(self, scene_id, shape_title):
            actions.append(("click", scene_id, shape_title))

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield None

        def wait_scene(self, *scene_ids, **kwargs):
            assert scene_ids == (300, 329, 301, 302, 303, 52, 53, 34, 69)
            assert kwargs["timeout"] <= 20.0
            if False:
                yield None
            return 53

    monkeypatch.setattr(
        runner,
        "_daily_lundao_runtime_confirms_seated",
        lambda: runtime_checks.append("checked") or next(runtime_results),
    )

    result = _drain_generator(
        runner._advance_daily_lundao_dojo_travel_confirmation(
            FakeRuntime(),
            300,
        )
    )

    assert result == (53, 100.0)
    assert actions[0] == ("click", 300, "确认")
    assert actions.count(("click", 300, "确认")) == 1
    assert runtime_checks == ["checked", "checked"]


def test_daily_lundao_seated_text_rejects_scene53_auto_navigation_transition():
    runner = create_behavior_tree_runtime_runner()

    assert runner._daily_lundao_text_is_seated(
        "闻道感悟 剩余座位 离开 自动寻路中"
    ) is False
    assert runner._daily_lundao_text_is_seated(
        "闻道感悟 剩余座位 离开"
    ) is True


def test_daily_lundao_broad_scene53_prefers_official_seat_choice_shape():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def cur_frame(self, update=False):
            assert update is True
            return "real-seat-choice-frame"

        def shape_score(self, scene_id, shape_title, **kwargs):
            assert (scene_id, shape_title) == (301, "入座")
            assert kwargs == {"frame_data_url": "real-seat-choice-frame"}
            return 100.0

    assert runner._prefer_daily_lundao_seat_choice_scene(
        FakeRuntime(),
        53,
        100.0,
    ) == (301, 100.0)


def test_daily_lundao_genuine_scene53_remains_seated_terminal():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def cur_frame(self, update=False):
            assert update is True
            return "real-seated-frame"

        def shape_score(self, scene_id, shape_title, **kwargs):
            assert (scene_id, shape_title) == (301, "入座")
            assert kwargs == {"frame_data_url": "real-seated-frame"}
            return 0.0

    assert runner._prefer_daily_lundao_seat_choice_scene(
        FakeRuntime(),
        53,
        96.0,
    ) == (53, 96.0)


def test_daily_lundao_resume_from_broad_scene53_runs_seat_confirmation():
    runner = create_behavior_tree_runtime_runner()
    actions = []

    class FakeRuntime:
        def cur_frame(self, update=False):
            assert update is True
            return "real-seat-choice-frame"

        def shape_score(self, scene_id, shape_title, **kwargs):
            assert (scene_id, shape_title) == (301, "入座")
            assert kwargs == {"frame_data_url": "real-seat-choice-frame"}
            return 100.0

    def fake_seat_confirmation(runtime, stop_event, scene_id):
        del runtime, stop_event
        actions.append(("seat_confirmation", scene_id))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return 34, 100.0

    runner._advance_daily_lundao_seat_confirmation = fake_seat_confirmation

    assert _drain_generator(
        runner._complete_daily_lundao_seat_and_leave(
            FakeRuntime(),
            threading.Event(),
            53,
            100.0,
        )
    ) == "success"
    assert actions == [("seat_confirmation", 301)]


def test_daily_lundao_seat_confirmation_rearbitrates_broad_scene53():
    runner = create_behavior_tree_runtime_runner()
    scenes = iter([
        (53, 100.0, "seat-choice-after-transition"),
        (303, 100.0, "seat-result"),
    ])

    class FakeRuntime:
        def wait_click(self, scene_id, shape_title):
            assert (scene_id, shape_title) in {(301, "入座"), (302, "确定")}
            if False:
                yield BehaviorTreeStatus.RUNNING

        def wait_action_settle(self, _seconds):
            if False:
                yield BehaviorTreeStatus.RUNNING

        def current_scene(self, _candidates, **_kwargs):
            return next(scenes)

        def ocr_text(self, _frame):
            return ""

        def shape_score(self, scene_id, shape_title, **kwargs):
            assert (scene_id, shape_title) == (301, "入座")
            assert kwargs == {"frame_data_url": "seat-choice-after-transition"}
            return 100.0

    assert _drain_generator(
        runner._advance_daily_lundao_seat_confirmation(
            FakeRuntime(),
            threading.Event(),
            302,
        )
    ) == (303, 100.0)


def test_daily_daofa_is_registered_as_one_dynamic_scheduler_job():
    from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs

    register_fanxiu_data_annotation_default_runtime_jobs()
    tasks = {item["id"]: item for item in _default_data_annotation_scheduler_tasks()}
    definition = fanxiu_api._data_annotation_task_cell_definition("daily_daofa")

    task = tasks["daily-daofa"]
    assert task["task_type"] == "daily_daofa"
    assert task["label"] == "道法争锋"
    assert task["trigger_description"] == "动态"
    assert task["next_time"]
    assert task["error_retry_delay_seconds"] == 60
    assert task["payload"]["retry_seconds"] == 60
    assert task["payload"]["no_target_retry_seconds"] == 3600
    assert "sunday-daofa" not in tasks
    assert definition is not None
    assert definition.label == "道法争锋"
    assert definition.scheduler_supported is True
    assert callable(definition.admission)
    assert not hasattr(definition, "lifecycle")


def test_daily_lundao_entry_stable_state_mapping_keeps_only_confirmed_scene_ids():
    from backend.core.fanxiu.data_annotation.tasks.daily_foundation import _DAILY_LUNDAO_STABLE_STATE_SCENE_IDS

    assert _DAILY_LUNDAO_STABLE_STATE_SCENE_IDS == {
        "ready": (),
        "in_progress": (304,),
        "kicked": (391,),
        "completed": (),
    }


def test_enter_daily_lundao_waits_for_stable_scene_then_uses_fresh_layer0_frame(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks.daily_foundation import _DAILY_LUNDAO_ENTRY_LAYER0_SCENE_IDS

    runner = create_behavior_tree_runtime_runner()
    actions = []

    def fake_open_daily_entry(*_args, **_kwargs):
        actions.append(("click_entry", 69, "论道"))
        yield BehaviorTreeStatus.RUNNING
        return "open"

    class FakeRuntime:
        def wait_scene(self, *scenes, **kwargs):
            actions.append(("wait_scene", scenes, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 296

        def current_scene(self, candidates, *, update=False):
            actions.append(("layer0", tuple(candidates), update))
            return 296, 100.0, "fresh-after-settle"

    monkeypatch.setattr(runner, "_open_daily_entry_from_daily", fake_open_daily_entry)
    result = _drain_generator(runner._enter_daily_lundao_and_route_state({}, threading.Event(), {}, FakeRuntime()))

    assert result == {"status": "dojo_selection", "scene_id": 296, "score": 100.0}
    assert actions == [
        ("click_entry", 69, "论道"),
        (
            "wait_scene",
            _DAILY_LUNDAO_ENTRY_LAYER0_SCENE_IDS,
            {"timeout": 20.0, "label": "论道_座位：等待道场选择/闻道中/被踢状态"},
        ),
        ("layer0", _DAILY_LUNDAO_ENTRY_LAYER0_SCENE_IDS, True),
    ]


def test_daily_lundao_does_not_reuse_xianmeng_294_as_ready_state():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def click_shape_center(self, *args):
            self.actions.append(("click_shape_center", args))

    runtime = FakeRuntime()
    route = runner._route_daily_lundao_entry_scene(294, 100.0, "ready")
    assert route == {"status": "unimplemented", "scene_id": 294, "score": 100.0}
    assert runtime.actions == []


def test_daily_lundao_kicked_confirms_popup_then_routes_the_fresh_stable_state():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return True

        def current_scene(self, candidates, *, update=False):
            self.actions.append(("current_scene", tuple(candidates), update))
            return 296, 100.0, "fresh-after-kicked-confirm"

    runtime = FakeRuntime()
    route = runner._route_daily_lundao_entry_scene(391, 100.0, "kicked")
    next_route = _drain_generator(runner._dismiss_daily_lundao_kicked(runtime))

    assert route == {"status": "kicked", "stable_state": "kicked", "scene_id": 391, "score": 100.0}
    assert next_route == {"status": "dojo_selection", "scene_id": 296, "score": 100.0}
    assert runtime.actions == [
        (
            "wait_click_then_view",
            (391, "确认", [296, 304]),
            {"settle_seconds": 1.5, "timeout": 20.0},
        ),
        ("current_scene", (296, 304), True),
    ]


def test_daily_lundao_dojo_selection_real_boundary_is_296_to_297_after_five_seconds():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def click_shape_center(self, *args):
            self.actions.append(("click_shape_center", args))

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def current_scene(self, candidates, *, update=False):
            self.actions.append(("layer0", tuple(candidates), update))
            return 297, 85.0, "execution-count-13-fresh-frame"

    runtime = FakeRuntime()
    result = _drain_generator(runner._select_daily_lundao_dojo_level(runtime, 296))

    assert result == {"status": "selected", "source_scene_id": 296, "scene_id": 297, "score": 85.0}
    assert runtime.actions == [
        ("click_shape_center", (296, "大罗道场")),
        ("settle", 5.0),
        ("layer0", (297,), True),
    ]


def test_daily_lundao_dynamic_dojo_slot_uses_only_first_visible_title():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self, title):
            self.title = title
            self.actions = []

        def cur_frame(self, *, update=False):
            return "frame"

        def ocr_tokens_in_shapes(self, view, shapes, **_kwargs):
            assert (view, shapes) == (296, ["至尊道场"])
            return [{"text": self.title, "x": 300, "y": 180, "w": 80, "h": 30}]

        def click_shape_center(self, *args):
            self.actions.append(args)

    shifted = FakeRuntime("大罗")
    runner._click_daily_lundao_dojo(shifted, "三清")
    assert shifted.actions == [(296, "大罗道场")]

    standard = FakeRuntime("至尊")
    runner._click_daily_lundao_dojo(standard, "大罗")
    assert standard.actions == [(296, "大罗道场")]


def test_daily_lundao_ingestion_busy_keeps_original_trigger_for_immediate_retry(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    actions = []

    class FakeRuntime:
        def wait_scene(self, *scenes, **_kwargs):
            actions.append(("wait_scene", scenes))
            yield BehaviorTreeStatus.RUNNING
            return 297

        def goto_view(self, scene_id):
            actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING

        def wait_action_settle(self, seconds):
            actions.append(("wait_action_settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def current_scene(self, scenes, *, update):
            actions.append(("current_scene", scenes, update))
            return 296, 100.0, ""

    def fake_return_to_selection(_runtime, scene_id):
        actions.append(("return_to_selection", scene_id))
        yield BehaviorTreeStatus.RUNNING

    monkeypatch.setattr(runner, "_click_daily_lundao_dojo", lambda _runtime, target: actions.append(("click_dojo", target)))
    monkeypatch.setattr(
        runner,
        "_refresh_daily_lundao_runtime_facts",
        lambda **_kwargs: {
            "status": {"available": True},
            "roster": {"evidence": {"order_key": [1]}},
            "runtime_catch_up": {"ok": False, "status": "ingestion_busy"},
        },
    )
    monkeypatch.setattr(runner, "_return_daily_lundao_to_selection", fake_return_to_selection)
    monkeypatch.setattr(
        runner,
        "_record_daily_lundao_next_time",
        lambda *_args, **_kwargs: pytest.fail("temporary ingestion contention must not publish a future next_time"),
    )

    with pytest.raises(RuntimeError, match="保留原触发时间立即整单重试.*ingestion_busy"):
        _drain_generator(runner._run_daily_lundao_dynamic_strategy(
            FakeRuntime(),
            threading.Event(),
            {"__lundao_runtime_snapshot_override": {"available": False, "complete": False}},
        ))

    assert actions == [
        ("click_dojo", "大罗"),
        ("wait_scene", (297, 298)),
        ("return_to_selection", 297),
        ("wait_action_settle", 2.0),
        ("current_scene", [296, 304], True),
        ("wait_action_settle", 2.0),
        ("current_scene", [296, 304], True),
        ("wait_action_settle", 2.0),
        ("current_scene", [296, 304], True),
        ("goto_view", 34),
    ]


def test_daily_lundao_ingestion_busy_while_visibly_seated_uses_normal_recheck(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    actions = []
    scheduler_times: list[tuple[str, str]] = []
    monkeypatch.setattr(daily_foundation._behavior_tree_runtime, "_now", lambda: datetime(2026, 7, 21, 18, 20, 0))

    class FakeRuntime:
        def wait_scene(self, *scenes, **_kwargs):
            actions.append(("wait_scene", scenes))
            yield BehaviorTreeStatus.RUNNING
            return 297

        def wait_action_settle(self, seconds):
            actions.append(("wait_action_settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def current_scene(self, scenes, *, update):
            actions.append(("current_scene", scenes, update))
            return 304, 100.0, ""

        def goto_view(self, scene_id):
            actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING

    def fake_return_to_selection(_runtime, scene_id):
        actions.append(("return_to_selection", scene_id))
        yield BehaviorTreeStatus.RUNNING
        return 296

    monkeypatch.setattr(runner, "_click_daily_lundao_dojo", lambda _runtime, target: actions.append(("click_dojo", target)))
    monkeypatch.setattr(
        runner,
        "_refresh_daily_lundao_runtime_facts",
        lambda **_kwargs: {
            "status": {"available": True, "seated": True},
            "roster": {"evidence": {"order_key": [1]}},
            "runtime_catch_up": {
                "ok": False,
                "status": "completed",
                "result": {"result": {"status": "ingestion_busy"}},
            },
        },
    )
    monkeypatch.setattr(runner, "_return_daily_lundao_to_selection", fake_return_to_selection)
    monkeypatch.setattr(
        runner,
        "_record_daily_lundao_next_time",
        lambda _payload, next_time, *, reason: scheduler_times.append((next_time, reason)),
    )

    result = _drain_generator(runner._run_daily_lundao_dynamic_strategy(
        FakeRuntime(),
        threading.Event(),
        {"__lundao_runtime_snapshot_override": {"available": False, "complete": False}},
    ))

    assert result == "success"
    assert scheduler_times == [
        ("2026-07-21 18:50:00", "已在闻道中，Runtime暂未追平（ingestion_busy），按正常半小时复查")
    ]
    assert actions == [
        ("click_dojo", "大罗"),
        ("wait_scene", (297, 298)),
        ("return_to_selection", 297),
        ("wait_action_settle", 2.0),
        ("current_scene", [296, 304], True),
        ("goto_view", 34),
    ]


def test_daily_lundao_visible_sanqing_empty_seat_skips_runtime_refresh(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    actions = []
    wait_results = iter((297, 298))
    refresh_count = 0
    scheduler_times: list[str] = []
    now_calls = 0

    def fake_now():
        nonlocal now_calls
        now_calls += 1
        return datetime(2026, 7, 21, 16, 0, 0) if now_calls == 1 else datetime(2026, 7, 21, 16, 10, 0)

    monkeypatch.setattr(daily_foundation._behavior_tree_runtime, "_now", fake_now)

    class FakeRuntime:
        def wait_scene(self, *scenes, **_kwargs):
            result = next(wait_results)
            actions.append(("wait_scene", scenes, result))
            yield BehaviorTreeStatus.RUNNING
            return result

    def fake_refresh(**_kwargs):
        nonlocal refresh_count
        refresh_count += 1
        return {
            "status": (
                {
                    "available": True,
                    "complete": True,
                    "seated": True,
                    "room_id": daily_foundation.LUNDAO_SANQING_ROOM_ID,
                }
                if refresh_count >= 3
                else {"available": True, "complete": True, "seated": False, "room_id": None}
            ),
            "roster": {
                "available": True,
                "complete": True,
                "room_id": daily_foundation.LUNDAO_DALUO_ROOM_ID,
                "evidence": {"order_key": [refresh_count]},
            },
            "runtime_catch_up": {"ok": True, "status": "completed"},
        }

    def fake_return_to_selection(_runtime, scene_id):
        actions.append(("return_to_selection", scene_id))
        yield BehaviorTreeStatus.RUNNING

    def fake_room_action(_runtime, _stop_event, *, opportunity):
        actions.append(("room_action", opportunity))
        yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(daily_foundation, "current_lundao_player_profile", lambda: {"available": True})
    monkeypatch.setattr(
        daily_foundation,
        "evaluate_lundao_room_opportunity",
        lambda *_args, **_kwargs: {"ok": True, "actionable": False},
    )
    monkeypatch.setattr(
        daily_foundation,
        "plan_lundao_strategy",
        lambda _status, *, daluo_opportunity, at: (
            {"action": "retry"} if daluo_opportunity is None else {"action": "seat_sanqing"}
        ),
    )
    monkeypatch.setattr(runner, "_click_daily_lundao_dojo", lambda _runtime, target: actions.append(("click_dojo", target)))
    monkeypatch.setattr(runner, "_refresh_daily_lundao_runtime_facts", fake_refresh)
    monkeypatch.setattr(runner, "_return_daily_lundao_to_selection", fake_return_to_selection)
    monkeypatch.setattr(runner, "_daily_lundao_remaining_attempts", lambda _runtime: 1)
    monkeypatch.setattr(runner, "_run_daily_lundao_room_action", fake_room_action)
    monkeypatch.setattr(
        runner,
        "_record_daily_lundao_next_time",
        lambda _payload, next_time, *, reason: (
            scheduler_times.append(next_time),
            actions.append(("record_next", reason)),
        ),
    )

    result = _drain_generator(runner._run_daily_lundao_dynamic_strategy(
        FakeRuntime(),
        threading.Event(),
        {"__lundao_runtime_snapshot_override": {"available": False, "complete": False}},
    ))

    assert result == "success"
    assert refresh_count == 4
    assert ("click_dojo", "三清") in actions
    assert ("room_action", {"action": "empty"}) in actions
    assert ("record_next", "三清画面确认有空位，已直接入座") in actions
    assert scheduler_times == ["2026-07-21 16:40:00"]


def test_daily_lundao_changed_daluo_target_falls_back_to_visible_sanqing_empty_seat(
    monkeypatch,
):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    actions: list[tuple] = []
    now = datetime(2026, 8, 3, 20, 19, 35)
    waited_scenes = iter((297, 298))
    refresh_count = 0
    runtime_status = {
        "available": True,
        "complete": True,
        "seated": False,
        "room_id": None,
        "seat_id": None,
        "current_left_listen_time": 6_180_000,
        "daluo_roster": {
            "available": True,
            "complete": True,
            "room_id": daily_foundation.LUNDAO_DALUO_ROOM_ID,
            "evidence": {"order_key": [2]},
        },
    }

    class FakeRuntime:
        def wait_scene(self, *_scenes, **_kwargs):
            yield BehaviorTreeStatus.RUNNING
            return next(waited_scenes)

        def goto_view(self, scene_id):
            actions.append(("goto", scene_id))
            yield BehaviorTreeStatus.RUNNING

    def fake_return_to_selection(_runtime, scene_id):
        actions.append(("return_to_selection", scene_id))
        yield BehaviorTreeStatus.RUNNING
        return 296

    def fake_room_action(_runtime, _stop_event, *, opportunity):
        actions.append(("room_action", opportunity))
        yield BehaviorTreeStatus.RUNNING
        return "target_changed" if opportunity.get("action") == "kick" else "success"

    def fake_refresh(**_kwargs):
        nonlocal refresh_count
        refresh_count += 1
        return {
            "status": (
                {
                    "available": True,
                    "complete": True,
                    "seated": True,
                    "room_id": daily_foundation.LUNDAO_SANQING_ROOM_ID,
                }
                if refresh_count >= 3
                else {"available": True, "complete": True, "seated": False, "room_id": None}
            ),
            "roster": {
                "available": True,
                "complete": True,
                "room_id": daily_foundation.LUNDAO_DALUO_ROOM_ID,
                "evidence": {"order_key": [refresh_count]},
            },
            "runtime_catch_up": {"ok": True, "status": "completed"},
        }

    monkeypatch.setattr(daily_foundation._behavior_tree_runtime, "_now", lambda: now)
    monkeypatch.setattr(
        daily_foundation,
        "current_lundao_player_profile",
        lambda: {"available": True},
    )
    monkeypatch.setattr(
        daily_foundation,
        "evaluate_lundao_room_opportunity",
        lambda *_args, **_kwargs: {
            "ok": True,
            "actionable": True,
            "action": "kick",
            "target": {"name": "旧目标"},
            "safety_score": 2,
            "threshold": 2,
            "available_count": 0,
            "eligible_count": 1,
        },
    )
    monkeypatch.setattr(
        daily_foundation,
        "plan_lundao_strategy",
        lambda _status, *, daluo_opportunity, at: (
            {"action": "retry"}
            if daluo_opportunity is None
            else {"action": "seat_daluo"}
        ),
    )
    monkeypatch.setattr(
        runner,
        "_click_daily_lundao_dojo",
        lambda _runtime, target: actions.append(("click_dojo", target)),
    )
    monkeypatch.setattr(
        runner,
        "_return_daily_lundao_to_selection",
        fake_return_to_selection,
    )
    monkeypatch.setattr(runner, "_run_daily_lundao_room_action", fake_room_action)
    monkeypatch.setattr(runner, "_refresh_daily_lundao_runtime_facts", fake_refresh)
    monkeypatch.setattr(
        runner,
        "_record_daily_lundao_next_time",
        lambda _payload, next_time, *, reason: actions.append(
            ("record_next", next_time, reason)
        ),
    )

    result = _drain_generator(
        runner._run_daily_lundao_dynamic_strategy(
            FakeRuntime(),
            threading.Event(),
            {"__lundao_runtime_snapshot_override": runtime_status},
            attempt_ready=True,
        )
    )

    assert result == "success"
    assert ("click_dojo", "三清") in actions
    assert ("room_action", {"action": "empty"}) in actions
    assert (
        "record_next",
        "2026-08-03 20:49:35",
        "三清画面确认有空位，已直接入座",
    ) in actions


def test_daily_lundao_after_21_with_zero_free_attempts_finishes_until_next_day(
    monkeypatch,
):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    actions: list[tuple] = []
    now = datetime(2026, 8, 3, 21, 5, 0)
    runtime_status = {
        "available": True,
        "complete": True,
        "seated": False,
        "room_id": None,
        "seat_id": None,
        "current_left_listen_time": 3_600_000,
        "daluo_roster": {
            "available": True,
            "complete": True,
            "room_id": daily_foundation.LUNDAO_DALUO_ROOM_ID,
            "evidence": {"order_key": [2]},
        },
    }

    class FakeRuntime:
        def wait_scene(self, *_scenes, **_kwargs):
            yield BehaviorTreeStatus.RUNNING
            return 297

        def goto_view(self, scene_id):
            actions.append(("goto", scene_id))
            yield BehaviorTreeStatus.RUNNING

    def fake_return_to_selection(_runtime, scene_id):
        actions.append(("return_to_selection", scene_id))
        yield BehaviorTreeStatus.RUNNING
        return 296

    monkeypatch.setattr(daily_foundation._behavior_tree_runtime, "_now", lambda: now)
    monkeypatch.setattr(
        daily_foundation,
        "current_lundao_player_profile",
        lambda: {"available": True},
    )
    monkeypatch.setattr(
        daily_foundation,
        "evaluate_lundao_room_opportunity",
        lambda *_args, **_kwargs: {
            "ok": True,
            "actionable": True,
            "action": "kick",
            "target": {"name": "可挑战目标"},
            "safety_score": 2,
            "threshold": 1,
            "available_count": 0,
            "eligible_count": 1,
        },
    )
    monkeypatch.setattr(
        daily_foundation,
        "plan_lundao_strategy",
        lambda _status, *, daluo_opportunity, at: (
            {"action": "retry"}
            if daluo_opportunity is None
            else {"action": "seat_daluo"}
        ),
    )
    monkeypatch.setattr(
        runner,
        "_click_daily_lundao_dojo",
        lambda _runtime, target: actions.append(("click_dojo", target)),
    )
    monkeypatch.setattr(
        runner,
        "_return_daily_lundao_to_selection",
        fake_return_to_selection,
    )
    monkeypatch.setattr(
        runner,
        "_refresh_daily_lundao_runtime_facts",
        lambda **_kwargs: {
            "status": {"available": True, "complete": True, "seated": False, "room_id": None},
            "roster": {
                "available": True,
                "complete": True,
                "room_id": daily_foundation.LUNDAO_DALUO_ROOM_ID,
                "evidence": {"order_key": [1]},
            },
            "runtime_catch_up": {"ok": True, "status": "completed"},
        },
    )
    monkeypatch.setattr(runner, "_daily_lundao_remaining_attempts", lambda _runtime: 0)
    monkeypatch.setattr(
        runner,
        "_run_daily_lundao_room_action",
        lambda *_args, **_kwargs: pytest.fail("零免费次数时不得挑战或尝试三清"),
    )
    monkeypatch.setattr(
        runner,
        "_record_daily_lundao_next_time",
        lambda _payload, next_time, *, reason: actions.append(
            ("record_next", next_time, reason)
        ),
    )

    result = _drain_generator(
        runner._run_daily_lundao_dynamic_strategy(
            FakeRuntime(),
            threading.Event(),
            {"__lundao_runtime_snapshot_override": runtime_status},
        )
    )

    assert result == "success"
    assert ("click_dojo", "三清") not in actions
    assert ("goto", 34) in actions
    assert (
        "record_next",
        "2026-08-04 15:30:00",
        "21点后免费次数为0，不再购买或争取最后一小时收益",
    ) in actions


def test_daily_lundao_post_seat_runtime_skips_runtime_catch_up(monkeypatch):
    runner = create_behavior_tree_runtime_runner()

    monkeypatch.setattr(
        runner,
        "_refresh_daily_lundao_runtime_facts",
        lambda **_kwargs: pytest.fail(
            "complete seated Runtime must bypass runtime catch-up"
        ),
    )

    result = runner._daily_lundao_post_seat_status(
        {
            "__lundao_post_seat_runtime_snapshot_override": {
                "available": True,
                "complete": True,
                "seated": True,
                "room_id": 15,
                "seat_id": 2,
                "current_left_listen_time": 21_600_000,
            },
        },
        reason="test-after-seat",
    )

    assert result["room_id"] == 15
    assert result["seat_id"] == 2
    assert result["current_left_listen_time"] == 21_600_000


def test_daily_lundao_post_seat_incomplete_runtime_falls_back_to_runtimes(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    calls = []
    monkeypatch.setattr(
        runner,
        "_refresh_daily_lundao_runtime_facts",
        lambda **kwargs: (
            calls.append(kwargs)
            or {
                "status": {
                    "available": True,
                    "current_left_listen_time": 18_000_000,
                },
            }
        ),
    )

    result = runner._daily_lundao_post_seat_status(
        {
            "__lundao_post_seat_runtime_snapshot_override": {
                "available": False,
                "complete": False,
                "reason": "not initialized",
            },
        },
        reason="test-fallback",
    )

    assert result["current_left_listen_time"] == 18_000_000
    assert calls == [{"reason": "test-fallback"}]


def test_daily_lundao_world_guard_skips_missing_entry_when_runtime_is_seated(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    actions = []

    class FakeRuntime:
        def goto_view(self, scene_id):
            actions.append(("goto", scene_id))
            yield BehaviorTreeStatus.RUNNING

    monkeypatch.setattr(
        runner,
        "_record_daily_lundao_next_time",
        lambda _payload, next_time, *, reason: actions.append(
            ("next", next_time, reason)
        ),
    )

    result = _drain_generator(runner._daily_lundao_world_runtime_guard(
        FakeRuntime(),
        {
            "__lundao_runtime_snapshot_override": {
                "available": True,
                "complete": True,
                "seated": True,
                "room_id": 15,
                "current_left_listen_time": 21_000_000,
            },
        },
        scene_id=69,
    ))

    assert result == "skipped"
    assert actions[0] == ("goto", 34)
    assert actions[1][0] == "next"
    assert actions[1][2] == "Runtime 已确认仍在闻道中"


def test_daily_lundao_world_guard_keeps_checking_upgrade_when_seated_in_sanqing(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    actions = []
    monkeypatch.setattr(
        runner,
        "_record_daily_lundao_next_time",
        lambda _payload, next_time, *, reason: actions.append(
            (next_time, reason)
        ),
    )

    result = _drain_generator(runner._daily_lundao_world_runtime_guard(
        object(),
        {
            "__lundao_runtime_snapshot_override": {
                "available": True,
                "complete": True,
                "seated": True,
                "room_id": 14,
                "current_left_listen_time": 21_000_000,
            },
        },
        scene_id=34,
    ))

    assert result is None
    assert actions == []


def test_daily_lundao_world_guard_marks_true_completion_without_gui(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    actions = []
    monkeypatch.setattr(
        runner,
        "_record_daily_lundao_next_time",
        lambda _payload, next_time, *, reason: actions.append(
            (next_time, reason)
        ),
    )

    result = _drain_generator(runner._daily_lundao_world_runtime_guard(
        object(),
        {
            "__lundao_runtime_snapshot_override": {
                "available": True,
                "complete": True,
                "seated": False,
                "current_left_listen_time": 0,
            },
        },
        scene_id=34,
    ))

    assert result == "success"
    assert actions[0][1] == "Runtime 已确认今日闻道时间归零"


def test_daily_lundao_room_facts_waits_past_stale_cross_room_roster(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    actions = []
    stale = {
        "status": {"available": True},
        "roster": {
            "available": True,
            "complete": True,
            "room_id": daily_foundation.LUNDAO_DALUO_ROOM_ID,
            "evidence": {"order_key": [2]},
        },
        "runtime_catch_up": {"ok": True, "status": "completed"},
    }
    fresh = {
        "status": {"available": True},
        "roster": {
            "available": True,
            "complete": True,
            "room_id": daily_foundation.LUNDAO_SANQING_ROOM_ID,
            "evidence": {"order_key": [3]},
        },
    }

    class FakeRuntime:
        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

    snapshots = iter((stale, fresh))
    monkeypatch.setattr(
        runner,
        "_refresh_daily_lundao_runtime_facts",
        lambda **_kwargs: next(snapshots),
    )

    result = _drain_generator(
        runner._wait_daily_lundao_room_facts(
            FakeRuntime(),
            reason="test-sanqing",
            room_id=daily_foundation.LUNDAO_SANQING_ROOM_ID,
            baseline_key=(1,),
        )
    )

    assert result["roster"]["room_id"] == daily_foundation.LUNDAO_SANQING_ROOM_ID
    assert result["roster"]["evidence"]["order_key"] == [3]
    assert actions == [("settle", 1.0)]


def test_daily_lundao_dynamic_dojo_slot_stops_on_ambiguous_first_title():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def cur_frame(self, *, update=False):
            return "frame"

        def ocr_tokens_in_shapes(self, *_args, **_kwargs):
            return [
                {"text": "至尊", "x": 300, "y": 180, "w": 80, "h": 30},
                {"text": "大罗", "x": 400, "y": 180, "w": 80, "h": 30},
            ]

        def click_shape_center(self, *_args):
            raise AssertionError("ambiguous OCR must not click")

    with pytest.raises(RuntimeError, match="第一行道场 OCR 不唯一"):
        runner._click_daily_lundao_dojo(FakeRuntime(), "大罗")


@pytest.mark.parametrize(("text", "expected"), [("剩余次数 1 次", 1), ("0／2", 0), ("次数：１２", 12)])
def test_daily_lundao_remaining_attempts_uses_first_number_without_requiring_slash(text, expected):
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def ocr_text_in_shapes(self, view, shapes, **kwargs):
            assert (view, shapes, kwargs) == (296, ["次数"], {"padding": 8})
            return text

    assert runner._daily_lundao_remaining_attempts(FakeRuntime()) == expected


def test_daily_lundao_buys_exactly_one_attempt_and_verifies_counter_increment():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []
            self.ocr_values = ["1/2"]

        def click_shape_center(self, scene, shape):
            self.actions.append(("click", scene, shape))

        def wait_scene(self, *scenes, **kwargs):
            self.actions.append(("wait_scene", scenes, kwargs.get("timeout")))
            yield BehaviorTreeStatus.RUNNING
            return scenes[0]

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def current_scene(self, candidates, *, update=False):
            self.actions.append(("current_scene", tuple(candidates), update))
            return behavior_tree_runtime_core.View({"id": 392, "title": "0392.png", "shapes": []}), 100.0, "frame"

        def ocr_text_in_shapes(self, view, shapes, **kwargs):
            assert (view, shapes, kwargs) == (296, ["次数"], {"padding": 8})
            return self.ocr_values.pop(0)

    runtime = FakeRuntime()
    result = _drain_generator(runner._buy_one_daily_lundao_attempt(
        runtime,
        before=0,
        at=datetime(2026, 7, 20, 20, 0),
    ))

    assert result == {"ready": True, "purchased": True, "before": 0, "after": 1}
    assert runtime.actions == [
        ("click", 296, "购买"),
        ("wait_scene", (392,), 15.0),
        ("click", 392, "购买"),
        ("settle", 1.5),
        ("current_scene", (392,), True),
        ("click", 392, "返回"),
        ("wait_scene", (296,), 15.0),
    ]


def test_daily_lundao_purchase_does_not_repeat_when_counter_does_not_increase():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.purchase_clicks = 0

        def click_shape_center(self, scene, shape):
            if (scene, shape) == (392, "购买"):
                self.purchase_clicks += 1

        def wait_scene(self, *scenes, **_kwargs):
            yield BehaviorTreeStatus.RUNNING
            return scenes[0]

        def wait_action_settle(self, _seconds):
            yield BehaviorTreeStatus.RUNNING

        def current_scene(self, _candidates, *, update=False):
            return 392, 100.0, "frame"

        def ocr_text_in_shapes(self, _view, _shapes, **_kwargs):
            return "0/2"

    runtime = FakeRuntime()
    result = _drain_generator(runner._buy_one_daily_lundao_attempt(
        runtime,
        before=0,
        at=datetime(2026, 7, 20, 20, 0),
    ))

    assert result == {
        "ready": False,
        "purchased": False,
        "before": 0,
        "after": 0,
        "reason": "purchase_unavailable",
    }
    assert runtime.purchase_clicks == 1


def test_daily_lundao_does_not_open_purchase_after_21_when_free_attempts_are_zero():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def click_shape_center(self, *_args):
            raise AssertionError("21点后免费次数为0时不得点击购买")

    result = _drain_generator(runner._buy_one_daily_lundao_attempt(
        FakeRuntime(),
        before=0,
        at=datetime(2026, 7, 20, 21, 0),
    ))

    assert result == {
        "ready": False,
        "purchased": False,
        "before": 0,
        "after": 0,
        "reason": "purchase_cutoff",
    }


def test_repeated_template_geometry_uses_live_annotation_centers_not_fixed_pixel_delta():
    from backend.core.fanxiu.data_annotation.behavior_tree_runtime import repeated_template_item_box_from_anchor

    resolved_name = {"x": 120, "y": 610, "w": 80, "h": 20}
    item = repeated_template_item_box_from_anchor(
        {"x": 40, "y": 200, "w": 700, "h": 120},
        {"x": 90, "y": 230, "w": 100, "h": 20},
        resolved_name,
        load_direction="down",
    )
    first_button_center_y = item["y"] + (290 - 200) + 20 / 2
    assert first_button_center_y == 620 + ((290 + 10) - (230 + 10))

    moved_item = repeated_template_item_box_from_anchor(
        {"x": 40, "y": 320, "w": 700, "h": 150},
        {"x": 90, "y": 355, "w": 100, "h": 30},
        resolved_name,
        load_direction="down",
    )
    changed_button_center_y = moved_item["y"] + (430 - 320) + 24 / 2
    assert changed_button_center_y == 620 + ((430 + 12) - (355 + 15))
    assert changed_button_center_y != first_button_center_y


def test_daily_lundao_in_progress_routes_then_returns_through_formal_shape():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def click_shape_center(self, *args):
            self.actions.append(("click_shape_center", args))

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

    runtime = FakeRuntime()
    route = runner._route_daily_lundao_entry_scene(304, 99.0, "in-progress")
    result = _drain_generator(runner._finish_daily_lundao_in_progress(runtime))

    assert route == {"status": "in_progress", "stable_state": "in_progress", "scene_id": 304, "score": 99.0}
    assert result == "success"
    assert runtime.actions == [
        ("click_shape_center", (304, "返回")),
        ("settle", 1.5),
    ]


def test_daily_lundao_task_checks_daluo_upgrade_from_existing_sanqing_seat(monkeypatch, tmp_path):
    runner = create_behavior_tree_runtime_runner()
    actions = []

    class FakeRuntime:
        def current_scene(self, *_args, **_kwargs):
            return 304, 100.0, "in-progress"

        def ocr_text(self, _frame):
            return "闻道中"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    def dynamic(_runtime, _stop_event, _payload, **kwargs):
        actions.append(("dynamic", kwargs))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "upgraded"

    monkeypatch.setattr(runner, "_run_daily_lundao_dynamic_strategy", dynamic)

    result = _drain_generator(
        runner._execute_daily_lundao_task(
            {"asset_tree_path": tmp_path / "asset-tree.json"},
            threading.Event(),
            {"__scheduler_task_id": "daily-lundao-seat"},
        )
    )

    assert result == "upgraded"
    assert actions == [("dynamic", {"daluo_source_scene_id": 304})]


def test_daily_lundao_attempt_check_rebuild_preserves_scene304_source(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    captured = []

    def dynamic(_runtime, _stop_event, _payload, **kwargs):
        captured.append(kwargs)
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "resumed"

    monkeypatch.setattr(runner, "_run_daily_lundao_dynamic_strategy", dynamic)

    result = _drain_generator(
        runner._rebuild_daily_lundao_strategy_after_attempt_check(
            object(),
            threading.Event(),
            {},
            source_scene_id=304,
            purchase_used=False,
        )
    )

    assert result == "resumed"
    assert captured == [{
        "attempt_ready": True,
        "purchase_used": False,
        "daluo_source_scene_id": 304,
    }]


def test_daily_lundao_clicks_unique_daluo_title_from_in_progress_page():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def cur_frame(self, *, update=False):
            assert update is True
            return "frame"

        def ocr_lines(self, *, frame_data_url=None):
            assert frame_data_url == "frame"
            return [
                {"text": "大罗道场", "x": 370, "y": 160, "w": 160, "h": 48},
                {"text": "三清道场", "x": 370, "y": 470, "w": 160, "h": 48},
            ]

        def click_frame_point(self, *args):
            self.actions.append(args)

    runtime = FakeRuntime()
    runner._click_daily_lundao_dojo(
        runtime,
        "大罗道场",
        source_scene_id=304,
    )

    assert runtime.actions == [(304, 450.0, 184.0)]


def test_daily_lundao_clicks_unique_stable_daluo_fragment_from_in_progress_page():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def cur_frame(self, *, update=False):
            assert update is True
            return "frame"

        def ocr_lines(self, *, frame_data_url=None):
            assert frame_data_url == "frame"
            return [
                {"text": "大罗", "x": 370, "y": 160, "w": 80, "h": 48},
                {"text": "三清道场", "x": 370, "y": 470, "w": 160, "h": 48},
            ]

        def click_frame_point(self, *args):
            self.actions.append(args)

    runtime = FakeRuntime()
    runner._click_daily_lundao_dojo(runtime, "大罗道场", source_scene_id=304)

    assert runtime.actions == [(304, 410.0, 184.0)]


def test_daily_lundao_reconstructs_daluo_from_real_ocr_tokens_on_in_progress_page():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def cur_frame(self, *, update=False):
            assert update is True
            return "frame"

        def ocr_tokens(self, frame_data_url):
            assert frame_data_url == "frame"
            return [
                {"text": "大", "x": 370, "y": 160, "w": 38, "h": 48, "parent_line_id": "dojo"},
                {"text": "罗", "x": 410, "y": 160, "w": 38, "h": 48, "parent_line_id": "dojo"},
                {"text": "三清", "x": 370, "y": 470, "w": 80, "h": 48, "parent_line_id": "other"},
            ]

        def ocr_lines(self, *, frame_data_url=None):
            raise AssertionError("production token path must not fall back to whole OCR lines")

        def click_frame_point(self, *args):
            self.actions.append(args)

    runtime = FakeRuntime()
    runner._click_daily_lundao_dojo(runtime, "大罗道场", source_scene_id=304)

    assert runtime.actions == [(304, 409.0, 184.0)]


def test_daily_lundao_uses_full_frame_tokens_for_non_identity_dojo_control():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def cur_frame(self, *, update=False):
            assert update is True
            return "frame"

        def full_frame_ocr_tokens(self, frame_data_url):
            assert frame_data_url == "frame"
            return [{"text": "大罗", "x": 370, "y": 160, "w": 80, "h": 48}]

        def ocr_tokens(self, _frame_data_url):
            raise AssertionError("scene-shape OCR excludes the dojo control")

        def click_frame_point(self, *args):
            self.actions.append(args)

    runtime = FakeRuntime()
    runner._click_daily_lundao_dojo(runtime, "大罗道场", source_scene_id=304)

    assert runtime.actions == [(304, 410.0, 184.0)]


@pytest.mark.parametrize(
    "lines, expected_count",
    [
        ([], 0),
        (
            [
                {"text": "大罗", "x": 370, "y": 160, "w": 80, "h": 48},
                {"text": "大罗道场", "x": 370, "y": 470, "w": 160, "h": 48},
            ],
            2,
        ),
    ],
)
def test_daily_lundao_in_progress_daluo_fragment_fails_closed_without_one_candidate(lines, expected_count):
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def cur_frame(self, *, update=False):
            assert update is True
            return "frame"

        def ocr_lines(self, *, frame_data_url=None):
            assert frame_data_url == "frame"
            return lines

        def click_frame_point(self, *args):
            self.actions.append(args)

    runtime = FakeRuntime()
    with pytest.raises(RuntimeError, match=rf"候选={expected_count}，已停止且未点击"):
        runner._click_daily_lundao_dojo(runtime, "大罗道场", source_scene_id=304)

    assert runtime.actions == []


def test_daily_lundao_scene304_retries_zero_candidate_in_place_then_clicks():
    runner = create_behavior_tree_runtime_runner()

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def __init__(self):
            self.frames = iter(["empty-1", "empty-2", "ready"])
            self.actions = []

        def cur_frame(self, *, update=False):
            assert update is True
            return next(self.frames)

        def ocr_tokens(self, frame_data_url):
            if frame_data_url != "ready":
                return []
            return [{"text": "大罗", "x": 370, "y": 160, "w": 80, "h": 48}]

        def click_frame_point(self, *args):
            self.actions.append(("click", args))

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._click_daily_lundao_dojo_with_stable_ocr(
            runtime,
            FakeStopEvent(),
            "大罗道场",
            source_scene_id=304,
        )
    )

    assert result is None
    assert runtime.actions == [
        ("settle", 1.0),
        ("settle", 1.0),
        ("click", (304, 410.0, 184.0)),
    ]


def test_daily_lundao_scene304_stable_retry_exhaustion_stays_fail_closed():
    runner = create_behavior_tree_runtime_runner()

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def __init__(self):
            self.settles = []

        def cur_frame(self, *, update=False):
            assert update is True
            return "empty"

        def ocr_tokens(self, frame_data_url):
            assert frame_data_url == "empty"
            return []

        def click_frame_point(self, *_args):
            raise AssertionError("zero-candidate frames must never click")

        def wait_action_settle(self, seconds):
            self.settles.append(seconds)
            yield BehaviorTreeStatus.RUNNING

    runtime = FakeRuntime()
    with pytest.raises(RuntimeError, match="候选=0，已停止且未点击"):
        _drain_generator(
            runner._click_daily_lundao_dojo_with_stable_ocr(
                runtime,
                FakeStopEvent(),
                "大罗道场",
                source_scene_id=304,
                attempts=4,
            )
        )

    assert runtime.settles == [1.0, 1.0, 1.0]


def test_daily_lundao_in_progress_accepts_wait_scene_view_result():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def click_shape_center(self, *args):
            self.actions.append(("click_shape_center", args))

        def wait_scene(self, *args, **kwargs):
            self.actions.append(("wait_scene", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return behavior_tree_runtime_core.View({"id": 34, "title": "0034.png", "shapes": []})

    runtime = FakeRuntime()
    result = _drain_generator(runner._finish_daily_lundao_in_progress(runtime, continue_to_selection=True))

    assert result == 34
    assert runtime.actions[0] == ("click_shape_center", (304, "返回"))
    assert runtime.actions[1][0] == "wait_scene"


def test_daily_lundao_seat_node_starts_with_only_supported_layer0_scenes():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.calls = []

        def current_scene(self, candidates, *, update=False):
            self.calls.append((tuple(candidates), update))
            return None, 0.0, "unknown"

    runtime = FakeRuntime()
    with pytest.raises(RuntimeError, match="只接受 #297/#298/#371/#372/#373/#375"):
        _drain_generator(runner._run_daily_lundao_seat_and_leave(runtime, threading.Event()))

    assert runtime.calls == [((297, 298, 371, 372, 373, 375), True)]


def test_daily_lundao_rule_block_closes_notice_and_returns_world():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.scene_reads = iter([
                (564, 100.0, "rule-block"),
                (34, 100.0, "world"),
            ])
            self.actions = []

        def current_scene(self, candidates, *, update=False):
            self.actions.append(("current_scene", tuple(candidates), update))
            return next(self.scene_reads)

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return behavior_tree_runtime_core.View({"id": 297, "title": "0297.png", "shapes": []})

        def goto_view(self, scene_id):
            self.actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING
            return "success"

    runtime = FakeRuntime()
    result = _drain_generator(runner._leave_daily_lundao_rule_block_to_world(runtime))

    assert result == {
        "status": "prerequisite_required",
        "prerequisite": "law_mail",
        "source_scene_id": 564,
        "scene_id": 34,
        "score": 100.0,
    }
    assert runtime.actions == [
        ("current_scene", (564,), True),
        (
            "wait_click_then_view",
            (564, "确认", 297),
            {
                "timeout": 12.0,
                "label": "论道_座位：关闭法则提示后等待座位列表 #297",
            },
        ),
        ("goto_view", 34),
        ("current_scene", (34,), True),
    ]


def test_daily_lundao_row_button_wait_keeps_564_out_of_popup_guard_domain():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.calls = []

        def wait_scene(self, *candidates, **kwargs):
            self.calls.append((tuple(candidates), kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 564

    runtime = FakeRuntime()
    result = _drain_generator(runner._wait_daily_lundao_kick_request_result(runtime))

    assert result == 564
    assert runtime.calls == [
        (
            (371, 564),
            {
                "timeout": 180.0,
                "label": "论道_座位：等待 #297 动态条目按钮的局部结果 #371/#564",
            },
        )
    ]


def test_daily_xianyuan_duel_is_registered_as_one_dynamic_scheduler_job():
    from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs

    register_fanxiu_data_annotation_default_runtime_jobs()
    tasks = {item["id"]: item for item in _default_data_annotation_scheduler_tasks()}
    definition = fanxiu_api._data_annotation_task_cell_definition("daily_xianyuan_duel")

    task = tasks["daily-xianyuan-duel"]
    assert task["task_type"] == "daily_xianyuan_duel"
    assert task["label"] == "仙缘斗法"
    assert task["trigger_description"] == "动态"
    assert task["next_time"]
    assert task["error_retry_delay_seconds"] == 60
    assert task["payload"]["retry_seconds"] == 60
    assert task["payload"]["purchase_max_price"] == 300
    assert task["payload"]["max_runtime_seconds"] == 1800
    assert "sunday-xianyuan-duel" not in tasks
    assert definition is not None
    assert definition.label == "仙缘斗法"
    assert definition.scheduler_supported is True
    assert callable(definition.admission)


def test_daily_lundao_seat_confirmation_retries_by_local_shape_chain():
    runner = create_behavior_tree_runtime_runner()

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def __init__(self):
            self.scenes = iter([(301, 80.0, "seat"), (303, 100.0, "dialog")])
            self.actions = []

        def wait_click(self, *args, **kwargs):
            self.actions.append(("wait_click", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return True

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def current_scene(self, *_args, **_kwargs):
            return next(self.scenes)

        def ocr_text(self, frame):
            return ""

    runtime = FakeRuntime()
    result = _drain_generator(runner._advance_daily_lundao_seat_confirmation(runtime, FakeStopEvent(), 302))

    assert result == (303, 100.0)
    assert [action[0] for action in runtime.actions] == [
        "wait_click",
        "settle",
        "wait_click",
        "settle",
        "wait_click",
        "settle",
    ]
    assert runtime.actions[0] == ("wait_click", (302, "确定"), {})
    assert runtime.actions[2] == ("wait_click", (301, "入座"), {})


def test_daily_lundao_seat_confirmation_uses_target_shape_instead_of_full_ocr():
    runner = create_behavior_tree_runtime_runner()

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def wait_click(self, *args, **kwargs):
            self.actions.append(("wait_click", args, kwargs))
            if False:
                yield None

        def current_scene(self, candidates, **kwargs):
            assert 47 not in candidates
            assert kwargs["handle_interruptions"] is False
            return 303, 100.0, "done"

        def ocr_text(self, _frame):
            return ""

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._advance_daily_lundao_seat_confirmation(runtime, FakeStopEvent(), 301)
    )

    assert result == (303, 100.0)
    assert [action[0] for action in runtime.actions] == [
        "wait_click", "settle", "wait_click", "settle"
    ]


def test_daily_lundao_identified_301_accepts_minimal_seat_action_ocr():
    runner = create_behavior_tree_runtime_runner()

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def wait_click(self, *args, **kwargs):
            self.actions.append(("wait_click", args, kwargs))
            if False:
                yield None

        def current_scene(self, candidates, **kwargs):
            assert 47 not in candidates
            assert kwargs["handle_interruptions"] is False
            return 303, 100.0, "done"

        def ocr_text(self, _frame):
            return ""

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._advance_daily_lundao_seat_confirmation(
            runtime,
            FakeStopEvent(),
            301,
        )
    )

    assert result == (303, 100.0)
    assert [action[0] for action in runtime.actions] == [
        "wait_click", "settle", "wait_click", "settle"
    ]


def test_daily_lundao_unknown_scene_rejects_minimal_seat_action_ocr():
    runner = create_behavior_tree_runtime_runner()
    assert runner._daily_lundao_text_is_seat_choice_prompt("入座") is False


def test_daily_lundao_accepts_real_compact_empty_seat_confirmation():
    runner = create_behavior_tree_runtime_runner()

    assert runner._daily_lundao_text_is_seat_confirm_prompt(
        "是否在该空位入座？确定"
    ) is True


def test_daily_lundao_empty_seat_confirms_go_to_dojo_popup_before_continuing():
    runner = create_behavior_tree_runtime_runner()

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def __init__(self):
            self.scenes = iter([
                (298, 100.0, "empty"),
                (300, 100.0, "go-dojo"),
                (300, 100.0, "go-dojo"),
                (303, 100.0, "dialog"),
                (303, 100.0, "dialog"),
                (53, 100.0, "leave"),
                (34, 100.0, "world"),
            ])
            self.actions = []

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return True

        def wait_click(self, *args, **kwargs):
            self.actions.append(("wait_click", args, kwargs))
            yield BehaviorTreeStatus.RUNNING

        def wait_scene(self, *args, **kwargs):
            self.actions.append(("wait_scene", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            if args == (54, 34, 69, 186):
                return 34
            if args == (303, 373, 52, 53, 186, 329, 301, 302):
                return 53
            return 301

        def advance_dialogue(self, *args, **kwargs):
            self.actions.append(("advance_dialogue", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 1

        def cur_frame(self, update=False):
            assert update is True
            return "frame"

        def current_scene(self, *_args, **_kwargs):
            return next(self.scenes)

        def ocr_text(self, _frame):
            if _frame == "seat-choice":
                return "听道座位 此处座位甚佳 再看看别的座位 入座"
            if _frame == "seat-confirm":
                return "是否在该空位入座 当前道场 三清道场 论道收益 剩余时间"
            return ""

        def click_shape_center(self, *args, **kwargs):
            self.actions.append(("click_shape_center", args, kwargs))

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

    runtime = FakeRuntime()
    result = _drain_generator(runner._run_daily_lundao_seat_and_leave(runtime, FakeStopEvent()))

    assert result == "success"
    assert runtime.actions[0] == (
        "wait_click",
        (298, "入座"),
        {},
    )
    assert runtime.actions[1] == ("settle", 1.5)
    assert runtime.actions[2] == ("click_shape_center", (300, "确认"), {})
    assert runtime.actions[3] == ("settle", 1.5)
    assert runtime.actions[4] == (
        "wait_scene",
        (300, 329, 301, 302, 303, 52, 53, 34, 69),
        {"timeout": 20.0, "label": "论道_座位：确认前往道场后等待入座链"},
    )
    assert runtime.actions[5] == ("wait_click", (301, "入座"), {})
    assert runtime.actions[7] == ("wait_click", (302, "确定"), {})
    assert runtime.actions[9] == (
        "advance_dialogue",
        (303, "对话"),
        {"label": "论道_座位：推进 #303 连续人物对话（第 1/8 段）"},
    )
    assert runtime.actions[10] == (
        "wait_scene",
        (303, 373, 52, 53, 186, 329, 301, 302),
        {"timeout": 30.0, "label": "论道_座位：人物对话后等待入座/下一段对话"},
    )
    assert runtime.actions[11] == ("click_shape_center", (53, "离开"), {})
    assert runtime.actions[12] == ("settle", 1.5)
    assert runtime.actions[13] == (
        "wait_scene",
        (54, 34, 69, 186),
        {"timeout": 30.0, "label": "论道_座位：点击 #53「离开」后等待退出确认/世界"},
    )


def test_daily_lundao_kick_dialogue_accepts_direct_seated_scene186():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def advance_dialogue(self, *_args, **_kwargs):
            yield BehaviorTreeStatus.RUNNING
            return 2

        def wait_scene(self, *scenes, **kwargs):
            assert scenes == (375, 295, 52, 186, 303)
            assert kwargs["timeout"] == 30.0
            yield BehaviorTreeStatus.RUNNING
            return 186

    result = _drain_generator(
        runner._advance_daily_lundao_kick_dialogue(
            FakeRuntime(),
            start_scene=373,
        )
    )

    assert result == {
        "status": "dialogue_finished",
        "clicks": 2,
        "pre_battle_clicks": 2,
        "post_battle_clicks": 0,
        "scene_id": 186,
        "score": 100.0,
    }


def test_daily_lundao_kick_dialogue_routes_scene303_to_shared_completion():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def advance_dialogue(self, *_args, **_kwargs):
            yield BehaviorTreeStatus.RUNNING
            return 1

        def wait_scene(self, *scenes, **kwargs):
            assert scenes == (375, 295, 52, 186, 303)
            assert kwargs["timeout"] == 30.0
            yield BehaviorTreeStatus.RUNNING
            return 303

    result = _drain_generator(
        runner._advance_daily_lundao_kick_dialogue(
            FakeRuntime(),
            start_scene=373,
        )
    )

    assert result["status"] == "dialogue_finished"
    assert result["scene_id"] == 303
    assert result["pre_battle_clicks"] == 1


def test_daily_lundao_current_scene_action_records_scheduler_intent(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    recorded = []
    monkeypatch.setattr(
        daily_foundation._behavior_tree_runtime,
        "_now",
        lambda: datetime(2026, 8, 11, 17, 0, 0),
    )
    monkeypatch.setattr(
        runner,
        "_read_daily_lundao_runtime_status",
        lambda _payload: {
            "available": True,
            "complete": True,
            "seated": True,
            "room_id": daily_foundation.LUNDAO_DALUO_ROOM_ID,
            "current_left_listen_time": 1_000,
        },
    )
    monkeypatch.setattr(
        runner,
        "_record_daily_lundao_next_time",
        lambda _payload, next_time, *, reason: recorded.append((next_time, reason)),
    )

    assert runner._finish_daily_lundao_current_scene_action(
        {"__scheduler_task_id": "daily-lundao-seat"},
        "success",
        reason="当前事实收口完成",
    ) == "success"
    assert recorded == [("2026-08-12 15:30:00", "当前事实收口完成，当前道场 room_id=15")]


def test_daily_lundao_daluo_completion_requires_runtime_room_15():
    runner = create_behavior_tree_runtime_runner()

    with pytest.raises(RuntimeError, match="expected_room_id=15.*actual_room_id=14"):
        runner._require_daily_lundao_expected_room(
            {
                "available": True,
                "complete": True,
                "seated": True,
                "room_id": 14,
            },
            15,
            label="大罗入座",
        )


def test_daily_lundao_kick_strategy_without_target_stops_before_any_click():
    runner = create_behavior_tree_runtime_runner()

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def __init__(self):
            self.scenes = iter([
                (297, 100.0, "request"),
                (297, 100.0, "request"),
                (329, 100.0, "confirm"),
                (186, 100.0, "seated"),
                (34, 100.0, "world"),
            ])
            self.actions = []

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return True

        def current_scene(self, *_args, **_kwargs):
            return next(self.scenes)

        def cur_frame(self, *_args, **_kwargs):
            return "seated"

        def ocr_text(self, frame):
            if frame == "seated":
                return "闻道感悟 剩余座位 离开"
            return "三清道场 前往道场 剩余座位：0/30"

        def ocr_fragments(self, _frame):
            return [{"text": "前往道场", "x": 650, "y": 520, "w": 120, "h": 40}]

        def click_frame_point(self, *args, **kwargs):
            self.actions.append(("click_frame_point", args, kwargs))

        def click_shape_center(self, *args, **kwargs):
            self.actions.append(("click_shape_center", args, kwargs))

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

    runtime = FakeRuntime()
    with pytest.raises(RuntimeError, match="缺少上游确定的目标玩家"):
        _drain_generator(runner._run_daily_lundao_kick_for_seat_strategy(runtime, FakeStopEvent()))

    assert runtime.actions == []


def test_daily_lundao_kick_strategy_rejects_best_ocr_name_below_threshold():
    runner = create_behavior_tree_runtime_runner()
    low_confidence_item = SimpleNamespace(
        name_similarity=0.17,
        text="[落宝八方]断桥残荷伴孤舟",
    )

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def shape(self, view, shape):
            assert (view, shape) == (297, "窗口")
            return SimpleNamespace(raw={"loadDirection": "down"})

        def cur_frame(self, *, update=False):
            assert update is True
            return "real-list-frame"

        def find_floating_items_by_anchor_text(self, *args, **kwargs):
            assert kwargs["frame_data_url"] == "real-list-frame"
            return [low_confidence_item]

        def scroll_shape_content(self, view, shape, **kwargs):
            self.actions.append(("scroll", view, shape, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return False

        def click_floating_item_field(self, item, field):
            self.actions.append(("click", item, field))

    runtime = FakeRuntime()
    with pytest.raises(RuntimeError, match=r"仅 17%.*低于姓名可信阈值.*已停止且未点击"):
        _drain_generator(
            runner._run_daily_lundao_kick_for_seat_strategy(
                runtime,
                FakeStopEvent(),
                target_player={"name": "真实目标玩家", "seat_id": 17},
            )
        )

    assert runtime.actions == [("scroll", 297, "窗口", {})]


def test_daily_lundao_room_action_treats_changed_target_as_no_position(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def current_scene(self, candidates, *, update=False):
            assert tuple(candidates) == (297, 298)
            assert update is True
            return 297, 100.0, "full"

    def fake_kick(_runtime, _stop_event, *, target_player, room_id):
        assert target_player == {"seat_id": 17, "name": "临时目标"}
        assert room_id == 14
        yield BehaviorTreeStatus.RUNNING
        return {"status": "target_changed", "scene_id": 297, "score": 0.0}

    monkeypatch.setattr(
        runner,
        "_run_daily_lundao_kick_for_seat_strategy",
        fake_kick,
    )
    monkeypatch.setattr(
        runner,
        "_complete_daily_lundao_seat_and_leave",
        lambda *_args, **_kwargs: pytest.fail("changed target must not enter seat completion"),
    )

    result = _drain_generator(runner._run_daily_lundao_room_action(
        FakeRuntime(),
        threading.Event(),
        opportunity={
            "action": "kick",
            "room_id": 14,
            "target": {"seat_id": 17, "name": "临时目标"},
        },
    ))

    assert result == "target_changed"


def test_daily_lundao_room_action_redecides_stale_kick_as_fresh_empty_seat(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    actions = []

    class FakeRuntime:
        def current_scene(self, candidates, *, update=False):
            assert tuple(candidates) == (297, 298)
            assert update is True
            return 298, 100.0, "fresh-empty-list"

    def fake_empty(_runtime):
        actions.append("empty")
        yield BehaviorTreeStatus.RUNNING
        return 301, 99.0

    def fake_complete(_runtime, _stop_event, scene_id, score):
        actions.append(("complete", scene_id, score))
        yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_run_daily_lundao_empty_seat_strategy", fake_empty)
    monkeypatch.setattr(
        runner,
        "_run_daily_lundao_kick_for_seat_strategy",
        lambda *_args, **_kwargs: pytest.fail("stale kick target must not be used on #298"),
    )
    monkeypatch.setattr(runner, "_complete_daily_lundao_seat_and_leave", fake_complete)

    result = _drain_generator(runner._run_daily_lundao_room_action(
        FakeRuntime(),
        threading.Event(),
        opportunity={
            "action": "kick",
            "room_id": 14,
            "target": {"seat_id": 17, "name": "旧目标"},
        },
    ))

    assert result == "success"
    assert actions == ["empty", ("complete", 301, 99.0)]


def test_daily_lundao_room_action_does_not_invent_kick_when_empty_seat_disappears(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def current_scene(self, candidates, *, update=False):
            assert tuple(candidates) == (297, 298)
            assert update is True
            return 297, 100.0, "fresh-full-list"

    monkeypatch.setattr(
        runner,
        "_run_daily_lundao_empty_seat_strategy",
        lambda *_args, **_kwargs: pytest.fail("missing empty seat must not be clicked"),
    )
    monkeypatch.setattr(
        runner,
        "_run_daily_lundao_kick_for_seat_strategy",
        lambda *_args, **_kwargs: pytest.fail("no fresh kick target was authorized"),
    )
    monkeypatch.setattr(
        runner,
        "_complete_daily_lundao_seat_and_leave",
        lambda *_args, **_kwargs: pytest.fail("changed decision must not complete a seat transaction"),
    )

    result = _drain_generator(runner._run_daily_lundao_room_action(
        FakeRuntime(),
        threading.Event(),
        opportunity={"action": "empty", "room_id": 14, "target": None},
    ))

    assert result == "target_changed"


def test_daily_lundao_sanqing_target_recheck_reads_sanqing_roster(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    observed_rooms = []
    item = SimpleNamespace(name_similarity=1.0, text="三清目标")

    class FakeRuntime:
        def shape(self, view, shape):
            assert (view, shape) == (297, "窗口")
            return SimpleNamespace(raw={"loadDirection": "down"})

        def cur_frame(self, *, update=False):
            assert update is True
            return "sanqing-list"

        def find_floating_items_by_anchor_text(self, *_args, **_kwargs):
            return [item]

        def floating_item_is_fully_inside(self, _item, container):
            return container == "窗口"

        def floating_item_field_is_inside(self, _item, field, container):
            return (field, container) == ("按钮", "窗口")

        def click_floating_item_field(self, _item, field):
            assert field == "按钮"

    def refresh(*, reason, room_id):
        assert reason == "daily-lundao-before-kick"
        observed_rooms.append(room_id)
        return {
            "roster": {
                "seats": [{
                    "seat_id": 17,
                    "owner": {"role_id": 23},
                }],
            },
        }

    def confirm(_runtime):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return {"scene_id": 52, "score": 100.0}

    monkeypatch.setattr(runner, "_refresh_daily_lundao_runtime_facts", refresh)
    monkeypatch.setattr(runner, "_confirm_daily_lundao_kick_request", confirm)

    result = _drain_generator(runner._run_daily_lundao_kick_for_seat_strategy(
        FakeRuntime(),
        threading.Event(),
        target_player={
            "name": "三清目标",
            "seat_id": 17,
            "role_id": 23,
        },
        room_id=14,
    ))

    assert result["status"] == "request_sent"
    assert observed_rooms == [14]


def test_daily_lundao_no_beatable_target_returns_success_and_retries_in_ten_minutes(
    monkeypatch,
):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    actions = []
    monkeypatch.setattr(
        daily_foundation._behavior_tree_runtime,
        "_now",
        lambda: datetime(2026, 7, 19, 15, 40, 0),
    )

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def current_scene(self, candidates, *, update=False):
            assert tuple(candidates) == (297, 298, 371, 372, 373, 375)
            assert update is True
            return 297, 100.0, "full"

        def goto_view(self, scene_id):
            actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING
            return "success"

    monkeypatch.setattr(
        runner,
        "_select_daily_lundao_kick_target",
        lambda: {
            "ok": True,
            "status": "no_target",
            "target": None,
            "rejected": {"friendly": 2, "stronger_law": 6},
        },
    )
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: actions.append(
            ("next_time", task_id, next_time)
        ),
    )

    result = _drain_generator(
        runner._run_daily_lundao_seat_and_leave(
            FakeRuntime(),
            FakeStopEvent(),
            payload={"__scheduler_task_id": "daily-lundao-seat"},
        )
    )

    assert result == "success"
    assert actions == [
        ("goto_view", 34),
        (
                "next_time",
                "daily-lundao-seat",
                "2026-07-19 15:50:00",
            ),
        ]


def test_daily_lundao_unseated_after_sixteen_retries_in_ten_minutes(
    monkeypatch,
):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    scheduled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        daily_foundation._behavior_tree_runtime,
        "_now",
        lambda: datetime(2026, 7, 19, 16, 10, 0),
    )
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: scheduled.append(
            (task_id, next_time)
        ),
    )

    result = runner._schedule_daily_lundao_next_check(
        {"__scheduler_task_id": "daily-lundao-seat"},
        message="测试",
    )

    assert result == "2026-07-19 16:20:00"
    assert scheduled == [
        ("daily-lundao-seat", "2026-07-19 16:20:00")
    ]


def test_daily_lundao_unseated_finish_sets_now_and_returns_success(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    scheduled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        daily_foundation._behavior_tree_runtime,
        "_now",
        lambda: datetime(2026, 7, 19, 17, 5, 23),
    )
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: scheduled.append((task_id, next_time)),
    )

    result = runner._finish_daily_lundao_unseated_retry(
        {"__scheduler_task_id": "daily-lundao-seat"},
        reason="测试未落座",
    )

    assert result == "success"
    assert scheduled == [
        ("daily-lundao-seat", "2026-07-19 17:15:23")
    ]


def test_daily_lundao_kick_strategy_scrolls_then_relocates_and_clicks_target_item_button():
    runner = create_behavior_tree_runtime_runner()

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def __init__(self):
            self.scenes = iter([
                (297, 100.0, "request"),
                (297, 100.0, "request"),
                (297, 100.0, "request"),
                (303, 100.0, "dialog"),
                (53, 100.0, "leave"),
                (34, 100.0, "world"),
            ])
            self.actions = []
            self.frames = iter(["before-scroll", "after-scroll"])
            self.item = SimpleNamespace(name_similarity=1.0, text="雪花")
            self.pre_battle_checks = iter([(None, 0.0, "dialog"), (375, 100.0, "battle")])
            self.post_battle_checks = iter([(373, 100.0, "dialog"), (52, 100.0, "done")])

        def shape(self, view, shape):
            assert (view, shape) == (297, "窗口")
            return SimpleNamespace(raw={"loadDirection": "down"})

        def cur_frame(self, *, update=False):
            frame = next(self.frames, "confirm")
            self.actions.append(("fresh_frame", frame, update))
            return frame

        def ocr_tokens_in_shapes(self, *_args, **_kwargs):
            return [{"text": "请他让座", "x": 600, "y": 800, "w": 120, "h": 40}]

        def find_floating_items_by_anchor_text(self, *args, **kwargs):
            self.actions.append(("find", args, kwargs))
            return [] if kwargs["frame_data_url"] == "before-scroll" else [self.item]

        def floating_item_is_fully_inside(self, item, container):
            return item is self.item and container == "窗口"

        def floating_item_field_is_inside(self, item, field, container):
            return item is self.item and (field, container) == ("按钮", "窗口")

        def click_floating_item_field(self, item, field):
            self.actions.append(("click_floating_item_field", item, field))

        def scroll_shape_content(self, view, shape):
            self.actions.append(("scroll", view, shape))
            yield BehaviorTreeStatus.RUNNING
            return True

        def wait_scene(self, *scenes, **kwargs):
            self.actions.append(("wait_scene", scenes, kwargs))
            yield BehaviorTreeStatus.RUNNING
            if scenes == (371, 564):
                return 371
            if scenes == (371, 372, 373, 375, 295, 52):
                return 372
            if scenes == (373, 375, 295, 52):
                return 373
            if scenes == (375, 295, 52, 186, 303):
                return 375
            if scenes == (373, 52, 329, 301, 302, 303):
                return 373
            if scenes == (52, 329, 301, 302, 303):
                return 52
            raise AssertionError(scenes)

        def advance_dialogue(self, *args, **kwargs):
            self.actions.append(("advance_dialogue", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 1

        def current_scene(self, candidates, **_kwargs):
            if list(candidates) == [52, 375]:
                return next(self.pre_battle_checks)
            if list(candidates) == [52, 373]:
                return next(self.post_battle_checks)
            return next(self.scenes)

        def ocr_text(self, frame):
            return "三清道场 前往道场 请他让座 剩余座位：0/30" if frame == "request" else ""

        def ocr_fragments(self, _frame):
            return [{"text": "前往道场", "x": 650, "y": 520, "w": 120, "h": 40}]

        def click_frame_point(self, *args, **kwargs):
            self.actions.append(("click_frame_point", args, kwargs))

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return True

        def click_shape_center(self, *args, **kwargs):
            self.actions.append(("click_shape_center", args, kwargs))

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._run_daily_lundao_kick_for_seat_strategy(
            runtime,
            FakeStopEvent(),
            target_player={"name": "雪花", "seat_id": "seat-17"},
        )
    )

    assert result == {
        "status": "request_sent",
        "target": {"id": "seat-17", "name": "雪花"},
        "scene_id": 52,
        "score": 100.0,
    }
    assert [action[0] for action in runtime.actions] == [
        "fresh_frame", "find", "scroll", "fresh_frame", "find", "click_floating_item_field",
        "wait_scene", "fresh_frame", "click_frame_point", "settle", "wait_scene", "click_shape_center", "settle",
        "wait_scene", "advance_dialogue", "wait_scene", "click_shape_center", "settle",
        "wait_scene", "advance_dialogue", "wait_scene",
    ]
    assert runtime.actions[1][2]["frame_data_url"] == "before-scroll"
    assert runtime.actions[4][2]["frame_data_url"] == "after-scroll"
    assert runtime.actions[6] == (
        "wait_scene",
        (371, 564),
        {
            "timeout": 180.0,
            "label": "论道_座位：等待 #297 动态条目按钮的局部结果 #371/#564",
        },
    )
    assert runtime.actions[7] == ("fresh_frame", "confirm", True)
    assert runtime.actions[8] == ("click_frame_point", (371, 660.0, 820.0), {})
    assert runtime.actions[9] == ("settle", 1.5)
    assert runtime.actions[10] == (
        "wait_scene",
        (371, 372, 373, 375, 295, 52),
        {"timeout": 8.0, "label": "论道_座位：第 1/3 次点击请他让座后确认来源或落点"},
    )
    assert runtime.actions[11] == ("click_shape_center", (372, "确定"), {})
    assert runtime.actions[12] == ("settle", 1.5)
    assert runtime.actions[14] == (
        "advance_dialogue",
        (373, "聊天按钮"),
        {"label": "论道_座位：推进战前对话"},
    )
    assert runtime.actions[16] == (
        "click_shape_center",
        (375, "关闭"),
        {},
    )
    assert runtime.actions[18] == (
        "wait_scene",
        (373, 52, 329, 301, 302, 303),
        {"timeout": 30.0, "label": "论道_座位：关闭胜利浮层后等待战后对话/入座"},
    )
    assert runtime.actions[19] == (
        "advance_dialogue",
        (373, "聊天按钮"),
        {"label": "论道_座位：推进战后对话"},
    )


def test_daily_lundao_kick_confirmation_consumes_current_scene_371():
    runner = create_behavior_tree_runtime_runner()

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def __init__(self):
            self.actions = []
            self.pre_battle_checks = iter([(375, 100.0, "battle")])
            self.post_battle_checks = iter([(373, 100.0, "dialog"), (52, 100.0, "done")])

        def current_scene(self, candidates, **_kwargs):
            if list(candidates) == [52, 375]:
                return next(self.pre_battle_checks)
            if list(candidates) == [52, 373]:
                return next(self.post_battle_checks)
            if list(candidates) == [53, 69, 34, 85, 186, 52]:
                return 34, 100.0, "world"
            return 371, 100.0, "confirm"

        def cur_frame(self, *, update=False):
            return "confirm"

        def ocr_tokens_in_shapes(self, *_args, **_kwargs):
            return [{"text": "请他让座", "x": 600, "y": 800, "w": 120, "h": 40}]

        def click_frame_point(self, *args, **kwargs):
            self.actions.append(("click_frame_point", args, kwargs))

        def wait_scene(self, *scenes, **kwargs):
            self.actions.append(("wait_scene", scenes, kwargs))
            yield BehaviorTreeStatus.RUNNING
            if scenes == (371, 372, 373, 375, 295, 52):
                return 372
            if scenes == (373, 375, 295, 52):
                return 373
            if scenes == (375, 295, 52, 186, 303):
                return 375
            if scenes == (373, 52, 329, 301, 302, 303):
                return 373
            if scenes == (52, 329, 301, 302, 303):
                return 52
            raise AssertionError(scenes)

        def advance_dialogue(self, *args, **kwargs):
            self.actions.append(("advance_dialogue", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 1

        def click_shape_center(self, *args, **kwargs):
            self.actions.append(("click_shape_center", args, kwargs))

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING

        def ocr_text(self, _frame):
            return ""

    runtime = FakeRuntime()
    assert _drain_generator(runner._run_daily_lundao_seat_and_leave(runtime, FakeStopEvent())) == "success"

    assert [action[0] for action in runtime.actions] == [
        "click_frame_point", "settle", "wait_scene", "click_shape_center", "settle", "wait_scene",
        "advance_dialogue", "wait_scene", "click_shape_center", "settle", "wait_scene",
        "advance_dialogue", "wait_scene", "wait_click_then_view",
    ]


def test_daily_lundao_kick_confirmation_consumes_current_scene_372():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []
            self.pre_battle_checks = iter([(375, 100.0, "battle")])
            self.post_battle_checks = iter([(373, 100.0, "dialog"), (52, 100.0, "done")])

        def current_scene(self, candidates, **_kwargs):
            if list(candidates) == [52, 375]:
                return next(self.pre_battle_checks)
            if list(candidates) == [52, 373]:
                return next(self.post_battle_checks)
            if list(candidates) == [53, 69, 34, 85, 186, 52]:
                return 34, 100.0, "world"
            return 372, 100.0, "confirm"

        def click_shape_center(self, *args, **kwargs):
            self.actions.append(("click_shape_center", args, kwargs))

        def wait_scene(self, *scenes, **kwargs):
            self.actions.append(("wait_scene", scenes, kwargs))
            yield BehaviorTreeStatus.RUNNING
            if scenes == (373, 375, 295, 52):
                return 373
            if scenes == (375, 295, 52, 186, 303):
                return 375
            if scenes == (373, 52, 329, 301, 302, 303):
                return 373
            if scenes == (52, 329, 301, 302, 303):
                return 52
            raise AssertionError(scenes)

        def advance_dialogue(self, *args, **kwargs):
            self.actions.append(("advance_dialogue", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 1

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING

        def ocr_text(self, _frame):
            return ""

    runtime = FakeRuntime()
    assert _drain_generator(runner._run_daily_lundao_seat_and_leave(runtime, threading.Event())) == "success"

    assert [action[0] for action in runtime.actions] == [
        "click_shape_center", "settle", "wait_scene", "advance_dialogue", "wait_scene",
        "click_shape_center", "settle", "wait_scene", "advance_dialogue", "wait_scene",
        "wait_click_then_view",
    ]


def test_daily_lundao_consumes_scene_373_and_clicks_until_375_without_rematching_373():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []
            self.pre_battle_checks = iter([(None, 0.0, "transition"), (375, 100.0, "battle")])
            self.post_battle_checks = iter([(373, 100.0, "dialog"), (52, 100.0, "done")])

        def current_scene(self, candidates, **_kwargs):
            if list(candidates) == [52, 375]:
                return next(self.pre_battle_checks)
            if list(candidates) == [52, 373]:
                return next(self.post_battle_checks)
            if list(candidates) == [53, 69, 34, 85, 186, 52]:
                return 34, 100.0, "world"
            return 373, 100.0, "dialog"

        def click_shape_center(self, *args, **kwargs):
            self.actions.append(("click_shape_center", args, kwargs))

        def wait_scene(self, *scenes, **kwargs):
            self.actions.append(("wait_scene", scenes, kwargs))
            yield BehaviorTreeStatus.RUNNING
            if scenes == (375, 295, 52, 186, 303):
                return 375
            if scenes == (373, 52, 329, 301, 302, 303):
                return 373
            if scenes == (52, 329, 301, 302, 303):
                return 52
            raise AssertionError(scenes)

        def advance_dialogue(self, *args, **kwargs):
            self.actions.append(("advance_dialogue", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 1

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING

        def ocr_text(self, _frame):
            return ""

    runtime = FakeRuntime()
    assert _drain_generator(runner._run_daily_lundao_seat_and_leave(runtime, threading.Event())) == "success"

    assert [action[0] for action in runtime.actions] == [
        "advance_dialogue", "wait_scene", "click_shape_center", "settle", "wait_scene",
        "advance_dialogue", "wait_scene", "wait_click_then_view",
    ]


@pytest.mark.parametrize("victory_scene_id", [375, 295])
def test_daily_lundao_closes_victory_overlay_before_continuing_to_seat_confirmation(victory_scene_id):
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def click_shape_center(self, *args, **kwargs):
            self.actions.append(("click_shape_center", args, kwargs))

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def wait_scene(self, *scenes, **kwargs):
            self.actions.append(("wait_scene", scenes, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 329

        def current_scene(self, candidates, **_kwargs):
            assert list(candidates) == [329, 301, 302, 303]
            return 329, 100.0, "seat-confirm"

    runtime = FakeRuntime()
    result = _drain_generator(runner._advance_daily_lundao_kick_dialogue(runtime, start_scene=victory_scene_id))

    assert result["status"] == "battle_won"
    assert result["scene_id"] == 329
    assert runtime.actions == [
        ("click_shape_center", (victory_scene_id, "关闭"), {}),
        ("settle", 1.5),
        (
            "wait_scene",
            (373, 52, 329, 301, 302, 303),
            {"timeout": 30.0, "label": "论道_座位：关闭胜利浮层后等待战后对话/入座"},
        ),
    ]


def test_daily_lundao_dialogue_accepts_wait_scene_view_result():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def advance_dialogue(self, *args, **_kwargs):
            yield BehaviorTreeStatus.RUNNING
            return 1

        def wait_scene(self, *scenes, **_kwargs):
            yield BehaviorTreeStatus.RUNNING
            return behavior_tree_runtime_core.View({"id": 52, "title": "0052.png", "shapes": []})

    result = _drain_generator(runner._advance_daily_lundao_kick_dialogue(FakeRuntime(), start_scene=373))

    assert result["status"] == "dialogue_finished"
    assert result["scene_id"] == 52


def test_daily_lundao_scene_303_routes_through_post_battle_dialogue_without_long_retry():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []
            self.scene_checks = iter([
                (303, 100.0, "character-dialogue"),
                (373, 100.0, "post-dialogue"),
                (34, 100.0, "world"),
            ])

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return True

        def current_scene(self, _candidates, **_kwargs):
            return next(self.scene_checks)

        def advance_dialogue(self, *args, **kwargs):
            self.actions.append(("advance_dialogue", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 1

        def wait_scene(self, *scenes, **kwargs):
            self.actions.append(("wait_scene", scenes, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 373 if len([action for action in self.actions if action[0] == "wait_scene"]) == 1 else 52

        def ocr_text(self, _frame):
            return ""

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._complete_daily_lundao_seat_and_leave(runtime, threading.Event(), 303)
    )

    assert result == "success"
    assert runtime.actions == [
        (
            "advance_dialogue",
            (303, "对话"),
            {"label": "论道_座位：推进 #303 连续人物对话（第 1/8 段）"},
        ),
        (
            "wait_scene",
            (303, 373, 52, 53, 186, 329, 301, 302),
            {"timeout": 30.0, "label": "论道_座位：人物对话后等待入座/下一段对话"},
        ),
        (
            "advance_dialogue",
            (373, "聊天按钮"),
            {"label": "论道_座位：推进 #373 连续人物对话（第 2/8 段）"},
        ),
        (
            "wait_scene",
            (303, 373, 52, 53, 186, 329, 301, 302),
            {"timeout": 30.0, "label": "论道_座位：人物对话后等待入座/下一段对话"},
        ),
        ("wait_click_then_view", (52, "确认"), {"wait_leave": True}),
    ]


def test_daily_lundao_post_seat_dialogue_allows_scene_303_self_loop_then_known_terminal():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.scenes = iter([
                (303, 100.0, "first"),
                (303, 100.0, "second"),
                (303, 100.0, "second"),
                (52, 100.0, "reward"),
            ])
            self.dialogues = []

        def current_scene(self, _candidates, **_kwargs):
            return next(self.scenes)

        def advance_dialogue(self, *args, **kwargs):
            self.dialogues.append((args, kwargs))
            yield BehaviorTreeStatus.RUNNING

        def wait_scene(self, *_scenes, **_kwargs):
            yield BehaviorTreeStatus.RUNNING
            return 303 if len(self.dialogues) == 1 else 52

    runtime = FakeRuntime()
    result = _drain_generator(runner._advance_daily_lundao_post_seat_dialogue(runtime, 303))

    assert result == (52, 100.0)
    assert [args for args, _kwargs in runtime.dialogues] == [(303, "对话"), (303, "对话")]


def test_daily_lundao_post_seat_dialogue_unknown_successor_fails_closed():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def current_scene(self, _candidates, **_kwargs):
            return None, 0.0, "unknown"

    with pytest.raises(RuntimeError, match="未声明落点"):
        _drain_generator(runner._advance_daily_lundao_post_seat_dialogue(FakeRuntime(), 303))


def test_daily_lundao_scene53_leave_waits_for_destination_instead_of_rereading_source():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def click_shape_center(self, *args):
            self.actions.append(("click", args))

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def wait_scene(self, *scenes, **kwargs):
            self.actions.append(("wait_scene", scenes, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 34

    runtime = FakeRuntime()
    result = _drain_generator(runner._leave_daily_lundao_seated_for_daily_entry(runtime, 53))

    assert result == "success"
    assert runtime.actions == [
        ("click", (53, "离开")),
        ("settle", 1.5),
        (
            "wait_scene",
            (54, 34, 69, 186),
            {
                "timeout": 30.0,
                "label": "论道_座位：点击 #53「离开」后等待退出确认/世界",
            },
        ),
    ]


def test_daily_lundao_cleanup_accepts_shared_scene_186_leave_action():
    runner = create_behavior_tree_runtime_runner()

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def expect_views(self, *_scene_ids):
            return nullcontext(self)

        def cur_frame(self, *_args, **_kwargs):
            return "seated"

        def ocr_text(self, frame):
            return "闻道感悟+20/分 剩余座位：224/240 离开" if frame == "seated" else ""

        def click_shape_center(self, *args, **kwargs):
            self.actions.append(("click_shape_center", args, kwargs))

        def wait_click(self, scene_id, shape_title, **_kwargs):
            self.actions.append(("wait_click", scene_id, shape_title))
            yield BehaviorTreeStatus.RUNNING

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            yield BehaviorTreeStatus.RUNNING

        def current_scene(self, *_args, **_kwargs):
            return 34, 100.0, "world"

        def wait_scene(self, *scene_ids, **kwargs):
            self.actions.append(("wait_scene", scene_ids, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 34

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 34

        def wait_view(self, *scene_ids, **kwargs):
            self.actions.append(("wait_view", scene_ids, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 34

    runtime = FakeRuntime()
    result = _drain_generator(runner._complete_daily_lundao_seat_and_leave(runtime, FakeStopEvent(), 186))

    assert result == "success"
    assert (
        "wait_click_then_view",
        (186, "离开", [47, 34, 69, 289, 86, 85, 386, 375, 295]),
        {"settle_seconds": 1.5, "timeout": 12.0, "max_clicks": 1},
    ) in runtime.actions


def test_daily_lundao_cleanup_recovers_known_victory_overlay_after_scene_186_leave():
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.actions = []

        def expect_views(self, *_scene_ids):
            return nullcontext(self)

        def wait_click_then_view(self, *args, **kwargs):
            self.actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return 375

        def goto_view(self, scene_id):
            self.actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING

    runtime = FakeRuntime()
    result = _drain_generator(
        runner._leave_shared_scene_186_to_world(runtime, label="论道_座位")
    )

    assert result == "success"
    assert runtime.actions == [
        (
            "wait_click_then_view",
            (186, "离开", [47, 34, 69, 289, 86, 85, 386, 375, 295]),
            {"settle_seconds": 1.5, "timeout": 12.0, "max_clicks": 1},
        ),
        ("goto_view", 34),
    ]


def test_daily_lundao_reward_confirm_accepts_shared_186_when_runtime_is_seated(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    actions = []

    class FakeRuntime:
        def wait_click_then_view(self, *args, **kwargs):
            actions.append(("wait_click_then_view", args, kwargs))
            yield BehaviorTreeStatus.RUNNING

        def current_scene(self, *_args, **_kwargs):
            return 186, 100.0, "seated"

        def ocr_text(self, _frame):
            return "闻道感悟+35/分 剩余座位：1/30 离开"

    def leave_shared(_runtime, *, label):
        actions.append(("leave_shared_186", label))
        yield BehaviorTreeStatus.RUNNING

    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.lundao.read_lundao_snapshot",
        lambda: {"available": True, "complete": True, "seated": True},
    )
    monkeypatch.setattr(runner, "_leave_shared_scene_186_to_world", leave_shared)

    result = _drain_generator(
        runner._complete_daily_lundao_seat_and_leave(
            FakeRuntime(),
            threading.Event(),
            52,
        )
    )

    assert result == "success"
    assert actions == [
        ("wait_click_then_view", (52, "确认"), {"wait_leave": True}),
        ("leave_shared_186", "论道_座位"),
    ]


def test_daily_lundao_kick_strategy_scrolls_past_clipped_target_before_clicking(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    clipped = SimpleNamespace(name_similarity=1.0, text="逍遥门窗哥")
    visible = SimpleNamespace(name_similarity=1.0, text="逍遥门窗哥")

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def __init__(self):
            self.frames = iter(["clipped", "visible"])
            self.actions = []

        def shape(self, view, shape):
            assert (view, shape) == (297, "窗口")
            return SimpleNamespace(raw={"loadDirection": "down"})

        def cur_frame(self, *, update=False):
            return next(self.frames)

        def find_floating_items_by_anchor_text(self, *args, **kwargs):
            return [clipped] if kwargs["frame_data_url"] == "clipped" else [visible]

        def floating_item_is_fully_inside(self, item, container):
            assert container == "窗口"
            return item is visible

        def floating_item_field_is_inside(self, item, field, container):
            assert (field, container) == ("按钮", "窗口")
            return item is visible

        def scroll_shape_content(self, view, shape, **kwargs):
            self.actions.append(("scroll", view, shape, kwargs))
            yield BehaviorTreeStatus.RUNNING
            return True

        def click_floating_item_field(self, item, field):
            self.actions.append(("click", item, field))

    def fake_confirm(_runtime):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return {"scene_id": 52, "score": 100.0}

    monkeypatch.setattr(runner, "_confirm_daily_lundao_kick_request", fake_confirm)
    runtime = FakeRuntime()

    result = _drain_generator(
        runner._run_daily_lundao_kick_for_seat_strategy(
            runtime,
            FakeStopEvent(),
            target_player={"name": "逍遥门窗哥", "seat_id": "seat-2"},
        )
    )

    assert result["status"] == "request_sent"
    assert runtime.actions == [
        ("scroll", 297, "窗口", {}),
        ("click", visible, "按钮"),
    ]


def test_daily_lundao_reward_confirm_rejects_shared_186_without_runtime_seat(monkeypatch):
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def wait_click_then_view(self, *_args, **_kwargs):
            yield BehaviorTreeStatus.RUNNING

        def current_scene(self, *_args, **_kwargs):
            return 186, 100.0, "seated"

        def ocr_text(self, _frame):
            return "闻道感悟+35/分 剩余座位：1/30 离开"

    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.lundao.read_lundao_snapshot",
        lambda: {"available": True, "complete": True, "seated": False},
    )

    with pytest.raises(RuntimeError, match="Runtime 未确认 seated=true"):
        _drain_generator(
            runner._complete_daily_lundao_seat_and_leave(
                FakeRuntime(),
                threading.Event(),
                52,
            )
        )


def test_daily_lundao_seat_confirmation_rejects_unrelated_301_false_positive():
    runner = create_behavior_tree_runtime_runner()

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def wait_click(self, scene, shape, **_kwargs):
            assert (scene, shape) == (301, "入座")
            raise RuntimeError("wait_click #301 入座 超时，局部 Shape 未命中")
            yield  # pragma: no cover

    with pytest.raises(RuntimeError, match="局部 Shape 未命中"):
        _drain_generator(runner._advance_daily_lundao_seat_confirmation(FakeRuntime(), FakeStopEvent(), 301))


def test_daily_lundao_301_to_302_uses_action_shapes_without_generic_47():
    runner = create_behavior_tree_runtime_runner()
    calls: list[tuple] = []

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def wait_click(self, scene, shape, **kwargs):
            calls.append(("wait_click", scene, shape, kwargs))
            if False:
                yield None

        def wait_action_settle(self, seconds):
            calls.append(("settle", seconds))
            if False:
                yield None

        def current_scene(self, candidates, **kwargs):
            calls.append(("current_scene", tuple(candidates), kwargs))
            assert 47 not in candidates
            assert kwargs.get("handle_interruptions") is False
            return 303, 100.0, "result"

        def ocr_text(self, _frame):
            return ""

    result = _drain_generator(
        runner._advance_daily_lundao_seat_confirmation(
            FakeRuntime(),
            FakeStopEvent(),
            301,
        )
    )

    assert result == (303, 100.0)
    assert calls == [
        ("wait_click", 301, "入座", {}),
        ("settle", 2.0),
        ("wait_click", 302, "确定", {}),
        ("settle", 2.0),
        (
            "current_scene",
            (303, 301, 302, 329, 52, 53, 186, 237, 18, 14, 69, 34),
            {"update": True, "handle_interruptions": False},
        ),
    ]


def test_daily_lingzu_is_not_independent_scheduler_task():
    tasks = {item["id"]: item for item in _default_data_annotation_scheduler_tasks()}

    assert "legacy-daily-lingzu" not in tasks
    assert _data_annotation_task_supported({"task_type": "daily_lingzu"}) is False


def test_daily_jianling_is_not_independent_scheduler_task():
    tasks = {item["id"]: item for item in _default_data_annotation_scheduler_tasks()}

    assert "legacy-daily-jianling" not in tasks
    assert _data_annotation_task_supported({"task_type": "daily_jianling"}) is False


def test_daily_lingta_is_not_independent_scheduler_task():
    tasks = {item["id"]: item for item in _default_data_annotation_scheduler_tasks()}

    assert "legacy-daily-lingta" not in tasks
    assert _data_annotation_task_supported({"task_type": "daily_lingta"}) is False


def test_daily_green_bottle_baiye_is_daily_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-green-bottle-baiye")

    assert task["task_type"] == "daily_green_bottle_baiye"
    assert task["label"] == "日常_绿瓶拜谒"
    assert task["source"] == "data_annotation_runtime"
    assert task["trigger_description"] == "每日"
    assert task["next_time"]
    assert _data_annotation_task_supported(task)


def test_daily_green_bottle_baiye_treats_peak_bottle_detail_as_done(monkeypatch, tmp_path):
    runner = create_behavior_tree_runtime_runner()
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {
            20: _image("绿瓶", "0020.png", []),
            282: _image("0282.png", "0282.png", []),
            283: _image("0283.png", "0283.png", []),
        },
    }
    calls: list[tuple[str, object]] = []
    records: list[dict[str, object]] = []

    class FakeRuntime:
        def goto_view(self, scene_id):
            calls.append(("goto_view", scene_id))
            if False:
                yield None

        def current_scene(self, preferred=None, update=False):
            calls.append(("current_scene", tuple(preferred or ())))
            if preferred == [20]:
                return 20, 91.0, "frame20"
            if preferred == [282, 301, 20]:
                return 301, 85.0, "frame301"
            if preferred == [34]:
                return 34, 100.0, "frame34"
            return None, 0.0, None

        def wait_click(self, scene_id, shape_title):
            calls.append(("wait_click", (scene_id, shape_title)))
            if False:
                yield None

        def wait_action_settle(self, seconds):
            calls.append(("settle", seconds))
            if False:
                yield None

        def ocr_text(self, frame=None, update=False):
            calls.append(("ocr_text", frame))
            return "掌天瓶 桎梏境界 金仙前期 已达巅峰"

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_record_daily_entry_done", lambda payload, **kwargs: records.append(kwargs))

    result = _drain_generator(runner._execute_daily_green_bottle_baiye_task(ctx, FakeStopEvent(), {}))

    assert result == "success"
    assert ("wait_click", (301, "返回")) in calls
    assert ("goto_view", 34) in calls
    assert records[0]["task_id"] == "legacy-daily-green-bottle-baiye"
    assert records[0]["message"] == "掌天瓶已达巅峰，今日绿瓶状态已确认"


def test_daily_green_bottle_baiye_zero_remaining_stays_done_when_cleanup_scene_is_unknown(monkeypatch, tmp_path):
    runner = create_behavior_tree_runtime_runner()
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {
            20: _image("绿瓶", "0020.png", []),
            282: _image("0282.png", "0282.png", []),
            283: _image("0283.png", "0283.png", []),
        },
    }
    calls: list[tuple[str, object]] = []
    records: list[dict[str, object]] = []

    class FakeRuntime:
        def goto_view(self, scene_id):
            calls.append(("goto_view", scene_id))
            if False:
                yield None

        def current_scene(self, preferred=None, update=False):
            calls.append(("current_scene", tuple(preferred or ())))
            if preferred == [20]:
                return 20, 100.0, "frame20"
            if preferred == [282, 301, 20]:
                return 282, 100.0, "frame282"
            if preferred == [283]:
                return 283, 100.0, "frame283"
            if preferred == [34]:
                return None, 0.0, "transition"
            return None, 0.0, None

        def wait_click(self, scene_id, shape_title):
            calls.append(("wait_click", (scene_id, shape_title)))
            if False:
                yield None

        def wait_action_settle(self, seconds):
            calls.append(("settle", seconds))
            if False:
                yield None

        def ocr_text_in_shapes(self, scene_id, shape_titles, **kwargs):
            calls.append(("ocr_text_in_shapes", (scene_id, tuple(shape_titles))))
            return "0/1"

        def click_shape_center(self, *_args, **_kwargs):
            raise AssertionError("0/1 不应再次点击拜谒")

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_record_daily_entry_done", lambda payload, **kwargs: records.append(kwargs))

    result = _drain_generator(runner._execute_daily_green_bottle_baiye_task(ctx, FakeStopEvent(), {}))

    assert result == "success"
    assert records[0]["message"] == "今日拜谒已确认完成"
    assert ("goto_view", 34) in calls
    assert ("ocr_text_in_shapes", (283, ("剩余次数",))) in calls


def test_daily_lingta_green_bottle_returns_by_left_bottom_world_without_back(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image20 = _image("绿瓶", "0020.png", [
        {"id": "back-world", "kind": "rect", "title": "回到世界", "x": 0.07, "y": 0.90, "w": 0.18, "h": 0.08},
    ])
    ctx = {"entry": object(), "images": {20: image20}}
    frames = iter(["green_outer", "world"])
    clicked: list[tuple[float, float]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def fake_ocr_fragments(frame):
        if frame == "green_outer":
            return [{"text": "炼丹绿瓶世界"}]
        return [{"text": "储物袋角色装备功法书"}]

    def fake_identify_scene_number(_ctx, frame, _preferred):
        if frame == "world":
            return 34, 100.0
        return None, 0.0

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(runner, "_ocr_fragments", fake_ocr_fragments)
    monkeypatch.setattr(runner, "_identify_scene_number", fake_identify_scene_number)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1))))
    monkeypatch.setattr(runner, "_keyevents", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not use keyevent/back")))

    result = runner._run_direct_runtime_action(
        lambda: runner._leave_daily_lingta_green_bottle(ctx, FakeStopEvent()),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(144.0, 1504.0)]


def test_daily_xianyuan_is_daily_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-xianyuan")

    assert task["task_type"] == "daily_xianyuan"
    assert task["label"] == "日常_挑战仙缘"
    assert task["source"] == "data_annotation_runtime"
    assert task["trigger_description"] == "每日"
    assert task["next_time"]
    assert task["error_retry_delay_seconds"] == 600
    assert _data_annotation_task_supported(task)


def test_daily_xianyuan_uses_business_layer0_candidates_instead_of_forcing_world_start():
    from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
    from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition

    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_xianyuan")

    assert definition is not None
    assert not hasattr(definition, "lifecycle")


def test_daily_assistant_is_daily_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-assistant")

    assert task["task_type"] == "daily_assistant"
    assert task["label"] == "日常_助手"
    assert task["source"] == "data_annotation_runtime"
    assert task["trigger_description"] == "每日"
    assert task["next_time"]
    assert task["error_retry_delay_seconds"] == 600
    assert _data_annotation_task_supported(task)


def test_daily_assistant_uses_standard_popup_guard_by_default():
    from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
    from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition

    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_assistant")

    assert definition is not None
    assert definition.normalize_payload is None


def test_daily_assistant_old_trigger_fields_are_discarded():
    defaults = _default_data_annotation_scheduler_tasks()
    raw = [
        {
            "id": "legacy-daily-assistant",
            "task_type": "daily_assistant",
            "label": "日常_助手",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "schedule_times": ["05:00", "12:00", "18:00", "00:00"],
            "enabled": False,
            "next_time": "2026-06-12 05:00:00",
            "payload": {"__scheduler_definition_task_type": "daily_assistant"},
        }
    ]

    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        defaults,
        {},
        task_supported=_data_annotation_task_supported,
        now=datetime(2026, 6, 12, 12, 0, 0),
    )
    task = next(item for item in tasks if item["id"] == "legacy-daily-assistant")

    assert changed is True
    assert "enabled" not in task
    assert "schedule_kind" not in task
    assert "schedule_times" not in task
    assert task["next_time"] == "2026-06-12 05:00:00"
    assert task["last_result"] != "unsupported"


def test_daily_assistant_success_advances_to_next_configured_time(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    written = []
    facts = {"discoveries": {}}

    class Runtime:
        def current_scene(self, *_args, **_kwargs):
            return 69, 100.0, "frame"

        def ocr_text(self, _frame):
            return ""

    def completed(value):
        if False:
            yield None
        return value

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: Runtime())
    monkeypatch.setattr(runner, "_open_daily_assistant_from_daily", lambda *_args, **_kwargs: completed("opened"))
    monkeypatch.setattr(runner, "_wait_daily_assistant_after_entry", lambda *_args, **_kwargs: completed((204, 100.0)))
    monkeypatch.setattr(runner, "_run_daily_assistant_from_list", lambda *_args, **_kwargs: completed("success"))
    monkeypatch.setattr(daily_challenge_module, "next_business_time", lambda *_args, **_kwargs: "2026-07-24 00:00:00")
    monkeypatch.setattr(daily_challenge_module._behavior_tree_runtime, "_now", lambda: datetime(2026, 7, 23, 5, 12, 30))
    monkeypatch.setattr(daily_challenge_module._behavior_tree_runtime, "_read_data_annotation_world_facts", lambda: facts)
    monkeypatch.setattr(daily_challenge_module._behavior_tree_runtime, "_write_data_annotation_world_facts", lambda value: facts.update(value))
    monkeypatch.setattr(runner, "_persist_scheduler_task_next_time", lambda task_id, next_time: written.append((task_id, next_time)))
    monkeypatch.setattr(runner, "_log", lambda *_args, **_kwargs: None)

    result = _drain_generator(runner._execute_daily_assistant_task(
        {"asset_tree_path": tmp_path / "asset-tree.json"},
        threading.Event(),
        {"__scheduler_task_id": "legacy-daily-assistant"},
    ))

    assert result == "success"
    assert written == [
        ("lilian-event", "2026-07-23 05:42:30"),
        ("legacy-daily-assistant", "2026-07-24 00:00:00"),
    ]
    assert facts["discoveries"]["daily_assistant_lilian_event_trigger"]["business_date"] == "2026-07-23"

    _drain_generator(runner._execute_daily_assistant_task(
        {"asset_tree_path": tmp_path / "asset-tree.json"},
        threading.Event(),
        {"__scheduler_task_id": "legacy-daily-assistant"},
    ))

    assert written == [
        ("lilian-event", "2026-07-23 05:42:30"),
        ("legacy-daily-assistant", "2026-07-24 00:00:00"),
        ("legacy-daily-assistant", "2026-07-24 00:00:00"),
    ]


def test_daily_assistant_success_before_five_does_not_schedule_lilian(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    written = []
    monkeypatch.setattr(daily_challenge_module._behavior_tree_runtime, "_read_data_annotation_world_facts", lambda: {"discoveries": {}})
    monkeypatch.setattr(runner, "_persist_scheduler_task_next_time", lambda task_id, next_time: written.append((task_id, next_time)))

    result = runner._schedule_lilian_event_after_daily_assistant_success(datetime(2026, 7, 23, 4, 59, 59))

    assert result is None
    assert written == []


def test_daily_yaowang_and_yaozu_are_not_independent_scheduler_tasks():
    tasks = {item["id"]: item for item in _default_data_annotation_scheduler_tasks()}

    assert "legacy-daily-yaowang" not in tasks
    assert "legacy-daily-yaozu" not in tasks
    assert _data_annotation_task_supported({"task_type": "daily_yaowang"}) is False
    assert _data_annotation_task_supported({"task_type": "daily_yaozu"}) is False


def test_daily_entry_matches_yaowang_and_yaozu_in_scroll_window():
    runner = create_behavior_tree_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.20, "w": 0.90, "h": 0.62},
    ])
    lines = [
        {"text": "抵御妖王来袭", "x": 370.0, "y": 760.0, "w": 260.0, "h": 42.0},
        {"text": "活10/次0/2", "x": 420.0, "y": 830.0, "w": 180.0, "h": 38.0},
        {"text": "抵御妖族袭城", "x": 370.0, "y": 1180.0, "w": 260.0, "h": 42.0},
    ]

    yaowang = runner._daily_entry_matches(lines, image69, title_pattern=r"妖王\s*来袭|妖王")
    yaozu = runner._daily_entry_matches(lines, image69, title_pattern=r"妖族\s*袭城|妖族")

    assert yaowang and yaowang[0][2] == "抵御妖王来袭"
    assert yaozu and yaozu[0][2] == "抵御妖族袭城"
    assert runner._daily_task_row_progress(lines, yaowang[0][1]) == (0, 2)


def test_daily_entry_matches_shuangxiu_in_scroll_window():
    runner = create_behavior_tree_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.20, "w": 0.90, "h": 0.62},
    ])
    lines = [
        {"text": "完成双人修炼1次", "x": 365.0, "y": 300.0, "w": 285.0, "h": 45.0},
        {"text": "活10/次", "x": 425.0, "y": 380.0, "w": 140.0, "h": 38.0},
        {"text": "0/3", "x": 425.0, "y": 420.0, "w": 80.0, "h": 38.0},
    ]

    matches = runner._daily_entry_matches(
        lines,
        image69,
        title_pattern=r"完成\s*双\s*人\s*修\s*炼\s*1\s*次|双\s*人\s*修\s*炼|双\s*修",
    )

    assert matches and matches[0][2] == "完成双人修炼1次"
    assert runner._daily_task_row_progress(lines, matches[0][1]) == (0, 3)


def test_daily_dungeon_entry_does_not_match_wudao_weekly():
    runner = create_behavior_tree_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.20, "w": 0.90, "h": 0.62},
    ])
    lines = [
        {"text": "完成悟道试炼周本", "x": 365.0, "y": 300.0, "w": 285.0, "h": 45.0},
        {"text": "活10/次", "x": 425.0, "y": 380.0, "w": 140.0, "h": 38.0},
        {"text": "0/1", "x": 425.0, "y": 420.0, "w": 80.0, "h": 38.0},
        {"text": "通关每日副本", "x": 365.0, "y": 700.0, "w": 285.0, "h": 45.0},
        {"text": "0/6", "x": 425.0, "y": 820.0, "w": 80.0, "h": 38.0},
    ]

    matches = runner._daily_entry_matches(
        lines,
        image69,
        title_pattern=r"通\s*关\s*每\s*日\s*副\s*本|每\s*日\s*副\s*本|副\s*本\s*探\s*险",
        exclude_pattern=r"悟\s*道|试\s*炼|周\s*本",
    )

    assert matches and matches[0][2] == "通关每日副本"


def _fake_daily_shuangxiu_finish(runner):
    def finish(*_args, **_kwargs):
        if False:
            yield None
        return runner._complete_daily_shuangxiu_after_continue(current_scene=34)

    return finish


def _patch_real_fanxiu_runtime(monkeypatch, runner):
    monkeypatch.setattr(
        runner,
        "_fanxiu_runtime",
        lambda runtime_ctx, asset_tree_path=None, frame_data_url=None, stop_event=None, **_kwargs: behavior_tree_runtime_core.BehaviorTreeRuntime(
            runner,
            runtime_ctx,
            asset_tree_path=asset_tree_path,
            frame_data_url=frame_data_url,
            stop_event=stop_event,
        ),
    )


def test_daily_shuangxiu_clicks_first_book_when_already_on_215(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.20, "w": 0.90, "h": 0.62},
    ])
    image215 = _image("双修秘术", "0215.png", [
        {"id": "secret", "kind": "rect", "title": "秘术标识", "x": 0.137037, "y": 0.071875, "w": 0.144444, "h": 0.054167},
        {"id": "book", "kind": "rect", "title": "痴情咒", "x": 0.166667, "y": 0.260417, "w": 0.207407, "h": 0.052083},
    ])
    image216 = _image("双修痴情咒详情", "0216.png", [
        {"id": "invite", "kind": "rect", "title": "邀请道友", "x": 0.451852, "y": 0.763542, "w": 0.103704, "h": 0.054167},
    ])
    image217 = _image("双修邀请", "0217.png", [
        {"id": "xianyuan-tab", "kind": "rect", "title": "仙缘页签", "x": 0.285185, "y": 0.207292, "w": 0.138889, "h": 0.041667},
    ])
    image218 = _image("双修仙缘邀请列表", "0218.png", [
        {"id": "partner-invite", "kind": "rect", "title": "邀请按钮", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.724074, "y": 0.284375, "w": 0.133333, "h": 0.055208},
    ])
    image219 = _image("双修修炼准备", "0219.png", [
        {"id": "start-training", "kind": "rect", "title": "前往修炼", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.385185, "y": 0.86875, "w": 0.22963, "h": 0.033333},
    ])
    image221 = _image("双修修炼完成", "0221.png", [
        {"id": "continue", "kind": "rect", "title": "点击屏幕继续", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.361111, "y": 0.875, "w": 0.275926, "h": 0.044792},
    ])
    ctx = {
        "entry": object(),
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {34: image34, 69: image69, 215: image215, 216: image216, 217: image217, 218: image218, 219: image219, 221: image221},
    }
    clicked: list[tuple[float, float]] = []

    _patch_real_fanxiu_runtime(monkeypatch, runner)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "secret-page")
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, _frame, ids: (218, 100.0) if ids is not None and list(ids) == [218] else (215, 100.0),
    )
    monkeypatch.setattr(runner, "_ocr_fragments", lambda _frame: [{"text": "秘术", "x": 120.0, "y": 120.0, "w": 100.0, "h": 40.0}])
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1))))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_detail", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_invite", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_training_ready", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_complete", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_finish_daily_shuangxiu_after_continue", _fake_daily_shuangxiu_finish(runner))

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_shuangxiu_task(ctx, threading.Event(), {}),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(243.3, 445.8), (453.3, 1265.0), (319.2, 365.0), (711.7, 499.2), (450.0, 1416.7), (449.2, 1435.8)]


def test_daily_shuangxiu_clicks_invite_when_already_on_detail(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.20, "w": 0.90, "h": 0.62},
    ])
    image215 = _image("双修秘术", "0215.png", [
        {"id": "secret", "kind": "rect", "title": "秘术标识", "x": 0.137037, "y": 0.071875, "w": 0.144444, "h": 0.054167},
        {"id": "book", "kind": "rect", "title": "痴情咒", "x": 0.166667, "y": 0.260417, "w": 0.207407, "h": 0.052083},
    ])
    image216 = _image("双修痴情咒详情", "0216.png", [
        {"id": "invite", "kind": "rect", "title": "邀请道友", "x": 0.451852, "y": 0.763542, "w": 0.103704, "h": 0.054167},
    ])
    image217 = _image("双修邀请", "0217.png", [
        {"id": "xianyuan-tab", "kind": "rect", "title": "仙缘页签", "x": 0.285185, "y": 0.207292, "w": 0.138889, "h": 0.041667},
    ])
    image218 = _image("双修仙缘邀请列表", "0218.png", [
        {"id": "partner-invite", "kind": "rect", "title": "邀请按钮", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.724074, "y": 0.284375, "w": 0.133333, "h": 0.055208},
    ])
    image219 = _image("双修修炼准备", "0219.png", [
        {"id": "start-training", "kind": "rect", "title": "前往修炼", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.385185, "y": 0.86875, "w": 0.22963, "h": 0.033333},
    ])
    image221 = _image("双修修炼完成", "0221.png", [
        {"id": "continue", "kind": "rect", "title": "点击屏幕继续", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.361111, "y": 0.875, "w": 0.275926, "h": 0.044792},
    ])
    ctx = {
        "entry": object(),
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {34: image34, 69: image69, 215: image215, 216: image216, 217: image217, 218: image218, 219: image219, 221: image221},
    }
    clicked: list[tuple[float, float]] = []

    _patch_real_fanxiu_runtime(monkeypatch, runner)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "detail-page")
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, _frame, ids: (218, 100.0) if ids is not None and list(ids) == [218] else (216, 100.0),
    )
    monkeypatch.setattr(
        runner,
        "_ocr_fragments",
        lambda _frame: [
            {"text": "痴情咒", "x": 300.0, "y": 500.0, "w": 100.0, "h": 35.0},
            {"text": "双人神通", "x": 120.0, "y": 800.0, "w": 160.0, "h": 35.0},
            {"text": "邀请道友", "x": 390.0, "y": 1320.0, "w": 150.0, "h": 40.0},
        ],
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1))))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_invite", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_training_ready", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_complete", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_finish_daily_shuangxiu_after_continue", _fake_daily_shuangxiu_finish(runner))

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_shuangxiu_task(ctx, threading.Event(), {}),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(453.3, 1265.0), (319.2, 365.0), (711.7, 499.2), (450.0, 1416.7), (449.2, 1435.8)]


def test_daily_shuangxiu_clicks_xianyuan_when_already_on_invite(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.20, "w": 0.90, "h": 0.62},
    ])
    image215 = _image("双修秘术", "0215.png", [
        {"id": "secret", "kind": "rect", "title": "秘术标识", "x": 0.137037, "y": 0.071875, "w": 0.144444, "h": 0.054167},
        {"id": "book", "kind": "rect", "title": "痴情咒", "x": 0.166667, "y": 0.260417, "w": 0.207407, "h": 0.052083},
    ])
    image216 = _image("双修痴情咒详情", "0216.png", [
        {"id": "invite", "kind": "rect", "title": "邀请道友", "x": 0.451852, "y": 0.763542, "w": 0.103704, "h": 0.054167},
    ])
    image217 = _image("双修邀请", "0217.png", [
        {"id": "xianyuan-tab", "kind": "rect", "title": "仙缘页签", "x": 0.285185, "y": 0.207292, "w": 0.138889, "h": 0.041667},
    ])
    image218 = _image("双修仙缘邀请列表", "0218.png", [
        {"id": "partner-invite", "kind": "rect", "title": "邀请按钮", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.724074, "y": 0.284375, "w": 0.133333, "h": 0.055208},
    ])
    image219 = _image("双修修炼准备", "0219.png", [
        {"id": "start-training", "kind": "rect", "title": "前往修炼", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.385185, "y": 0.86875, "w": 0.22963, "h": 0.033333},
    ])
    image221 = _image("双修修炼完成", "0221.png", [
        {"id": "continue", "kind": "rect", "title": "点击屏幕继续", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.361111, "y": 0.875, "w": 0.275926, "h": 0.044792},
    ])
    ctx = {
        "entry": object(),
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {34: image34, 69: image69, 215: image215, 216: image216, 217: image217, 218: image218, 219: image219, 221: image221},
    }
    clicked: list[tuple[float, float]] = []

    _patch_real_fanxiu_runtime(monkeypatch, runner)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "invite-page")
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, _frame, ids: (
            (218, 100.0)
            if ids is not None and list(ids) == [218]
            else (217, 100.0)
        ),
    )
    monkeypatch.setattr(
        runner,
        "_ocr_fragments",
        lambda _frame: [
            {"text": "双人邀请", "x": 300.0, "y": 60.0, "w": 160.0, "h": 40.0},
            {"text": "好友 仙缘 灵界", "x": 110.0, "y": 340.0, "w": 260.0, "h": 40.0},
            {"text": "已顿悟《痴情咒》次数不足", "x": 220.0, "y": 560.0, "w": 310.0, "h": 40.0},
        ],
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1))))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_training_ready", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_complete", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_finish_daily_shuangxiu_after_continue", _fake_daily_shuangxiu_finish(runner))

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_shuangxiu_task(ctx, threading.Event(), {}),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(319.2, 365.0), (711.7, 499.2), (450.0, 1416.7), (449.2, 1435.8)]


def test_daily_shuangxiu_clicks_partner_when_already_on_xianyuan_list(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.20, "w": 0.90, "h": 0.62},
    ])
    image215 = _image("双修秘术", "0215.png", [
        {"id": "secret", "kind": "rect", "title": "秘术标识", "x": 0.137037, "y": 0.071875, "w": 0.144444, "h": 0.054167},
        {"id": "book", "kind": "rect", "title": "痴情咒", "x": 0.166667, "y": 0.260417, "w": 0.207407, "h": 0.052083},
    ])
    image216 = _image("双修痴情咒详情", "0216.png", [
        {"id": "invite", "kind": "rect", "title": "邀请道友", "x": 0.451852, "y": 0.763542, "w": 0.103704, "h": 0.054167},
    ])
    image217 = _image("双修邀请", "0217.png", [
        {"id": "xianyuan-tab", "kind": "rect", "title": "仙缘页签", "x": 0.285185, "y": 0.207292, "w": 0.138889, "h": 0.041667},
    ])
    image218 = _image("双修仙缘邀请列表", "0218.png", [
        {"id": "partner-invite", "kind": "rect", "title": "邀请按钮", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.724074, "y": 0.284375, "w": 0.133333, "h": 0.055208},
    ])
    image219 = _image("双修修炼准备", "0219.png", [
        {"id": "start-training", "kind": "rect", "title": "前往修炼", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.385185, "y": 0.86875, "w": 0.22963, "h": 0.033333},
    ])
    image221 = _image("双修修炼完成", "0221.png", [
        {"id": "continue", "kind": "rect", "title": "点击屏幕继续", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.361111, "y": 0.875, "w": 0.275926, "h": 0.044792},
    ])
    ctx = {
        "entry": object(),
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {34: image34, 69: image69, 215: image215, 216: image216, 217: image217, 218: image218, 219: image219, 221: image221},
    }
    clicked: list[tuple[float, float]] = []

    _patch_real_fanxiu_runtime(monkeypatch, runner)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "xianyuan-list")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _ids: (218, 100.0))
    monkeypatch.setattr(
        runner,
        "_ocr_fragments",
        lambda _frame: [
            {"text": "邀请", "x": 300.0, "y": 60.0, "w": 120.0, "h": 40.0},
            {"text": "好友 仙缘 灵界", "x": 110.0, "y": 330.0, "w": 260.0, "h": 40.0},
            {"text": "好感度：不离不弃", "x": 220.0, "y": 560.0, "w": 310.0, "h": 40.0},
        ],
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1))))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_training_ready", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_complete", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_finish_daily_shuangxiu_after_continue", _fake_daily_shuangxiu_finish(runner))

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_shuangxiu_task(ctx, threading.Event(), {}),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(711.7, 499.2), (450.0, 1416.7), (449.2, 1435.8)]
    assert runner.status()["phase"] == "daily_shuangxiu_complete_continued"


def test_daily_shuangxiu_clicks_start_when_already_on_training_ready(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.20, "w": 0.90, "h": 0.62},
    ])
    image215 = _image("双修秘术", "0215.png", [
        {"id": "secret", "kind": "rect", "title": "秘术标识", "x": 0.137037, "y": 0.071875, "w": 0.144444, "h": 0.054167},
        {"id": "book", "kind": "rect", "title": "痴情咒", "x": 0.166667, "y": 0.260417, "w": 0.207407, "h": 0.052083},
    ])
    image216 = _image("双修痴情咒详情", "0216.png", [
        {"id": "invite", "kind": "rect", "title": "邀请道友", "x": 0.451852, "y": 0.763542, "w": 0.103704, "h": 0.054167},
    ])
    image217 = _image("双修邀请", "0217.png", [
        {"id": "xianyuan-tab", "kind": "rect", "title": "仙缘页签", "x": 0.285185, "y": 0.207292, "w": 0.138889, "h": 0.041667},
    ])
    image218 = _image("双修仙缘邀请列表", "0218.png", [
        {"id": "partner-invite", "kind": "rect", "title": "邀请按钮", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.724074, "y": 0.284375, "w": 0.133333, "h": 0.055208},
    ])
    image219 = _image("双修修炼准备", "0219.png", [
        {"id": "start-training", "kind": "rect", "title": "前往修炼", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.385185, "y": 0.86875, "w": 0.22963, "h": 0.033333},
    ])
    image221 = _image("双修修炼完成", "0221.png", [
        {"id": "continue", "kind": "rect", "title": "点击屏幕继续", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.361111, "y": 0.875, "w": 0.275926, "h": 0.044792},
    ])
    ctx = {
        "entry": object(),
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {34: image34, 69: image69, 215: image215, 216: image216, 217: image217, 218: image218, 219: image219, 221: image221},
    }
    clicked: list[tuple[float, float]] = []

    _patch_real_fanxiu_runtime(monkeypatch, runner)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "training-ready")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _ids: (219, 100.0))
    monkeypatch.setattr(
        runner,
        "_ocr_fragments",
        lambda _frame: [
            {"text": "双人修炼规则", "x": 340.0, "y": 80.0, "w": 180.0, "h": 40.0},
            {"text": "今日剩余修炼次数：3", "x": 260.0, "y": 1260.0, "w": 280.0, "h": 40.0},
            {"text": "前往修炼", "x": 350.0, "y": 1400.0, "w": 180.0, "h": 45.0},
        ],
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1))))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_complete", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_finish_daily_shuangxiu_after_continue", _fake_daily_shuangxiu_finish(runner))

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_shuangxiu_task(ctx, threading.Event(), {}),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(450.0, 1416.7), (449.2, 1435.8)]
    assert runner.status()["phase"] == "daily_shuangxiu_complete_continued"


def test_daily_shuangxiu_clicks_continue_when_already_on_complete(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.20, "w": 0.90, "h": 0.62},
    ])
    image215 = _image("双修秘术", "0215.png", [
        {"id": "secret", "kind": "rect", "title": "秘术标识", "x": 0.137037, "y": 0.071875, "w": 0.144444, "h": 0.054167},
        {"id": "book", "kind": "rect", "title": "痴情咒", "x": 0.166667, "y": 0.260417, "w": 0.207407, "h": 0.052083},
    ])
    image216 = _image("双修痴情咒详情", "0216.png", [
        {"id": "invite", "kind": "rect", "title": "邀请道友", "x": 0.451852, "y": 0.763542, "w": 0.103704, "h": 0.054167},
    ])
    image217 = _image("双修邀请", "0217.png", [
        {"id": "xianyuan-tab", "kind": "rect", "title": "仙缘页签", "x": 0.285185, "y": 0.207292, "w": 0.138889, "h": 0.041667},
    ])
    image218 = _image("双修仙缘邀请列表", "0218.png", [
        {"id": "partner-invite", "kind": "rect", "title": "邀请按钮", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.724074, "y": 0.284375, "w": 0.133333, "h": 0.055208},
    ])
    image219 = _image("双修修炼准备", "0219.png", [
        {"id": "start-training", "kind": "rect", "title": "前往修炼", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.385185, "y": 0.86875, "w": 0.22963, "h": 0.033333},
    ])
    image221 = _image("双修修炼完成", "0221.png", [
        {"id": "continue", "kind": "rect", "title": "点击屏幕继续", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.361111, "y": 0.875, "w": 0.275926, "h": 0.044792},
    ])
    ctx = {
        "entry": object(),
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {34: image34, 69: image69, 215: image215, 216: image216, 217: image217, 218: image218, 219: image219, 221: image221},
    }
    clicked: list[tuple[float, float]] = []

    _patch_real_fanxiu_runtime(monkeypatch, runner)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "complete-page")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _ids: (221, 100.0))
    monkeypatch.setattr(
        runner,
        "_ocr_fragments",
        lambda _frame: [
            {"text": "修炼完成", "x": 320.0, "y": 260.0, "w": 220.0, "h": 120.0},
            {"text": "已进行3次修炼获得修为：19.31万", "x": 220.0, "y": 960.0, "w": 430.0, "h": 45.0},
            {"text": "点击屏幕继续", "x": 300.0, "y": 1420.0, "w": 260.0, "h": 45.0},
        ],
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1))))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_finish_daily_shuangxiu_after_continue", _fake_daily_shuangxiu_finish(runner))

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_shuangxiu_task(ctx, threading.Event(), {}),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(449.2, 1435.8)]
    assert runner.status()["phase"] == "daily_shuangxiu_complete_continued"


def test_daily_shuangxiu_leaves_219_after_complete_continue(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image219 = _image("双修修炼准备", "0219.png", [
        {"id": "start-training", "kind": "rect", "title": "前往修炼", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.385185, "y": 0.86875, "w": 0.22963, "h": 0.033333},
        {"id": "leave", "kind": "rect", "title": "离开", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.038889, "y": 0.485417, "w": 0.109259, "h": 0.084375},
    ])
    image86 = _image("离开场景", "0086.png", [
        {"id": "confirm", "kind": "rect", "title": "确认", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.624074, "y": 0.64375, "w": 0.12037, "h": 0.041667},
    ])
    ctx = {"entry": object(), "asset_tree_path": tmp_path / "asset_tree.json", "images": {86: image86, 219: image219}}
    frames = iter(["training-ready", "leave-confirm", "world"])
    clicked: list[tuple[str, float, float]] = []
    waited: list[int] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, frame, _ids: (
            (86, 100.0)
            if frame == "leave-confirm"
            else ((34, 100.0) if frame == "world" else (219, 100.0))
        ),
    )
    monkeypatch.setattr(
        runner,
        "_ocr_fragments",
        lambda frame: (
            [{"text": "双人修炼规则 今日剩余修炼次数：3 前往修炼", "x": 100, "y": 100, "w": 600, "h": 80}]
            if frame == "training-ready"
            else [{"text": "是否离开当前场景 取消 确认", "x": 200, "y": 720, "w": 500, "h": 80}]
        ),
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, image, x, y: clicked.append((image["title"], round(x, 1), round(y, 1))))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))

    def fake_wait_scene_id(_ctx, _stop_event, scene_id, **_kwargs):
        waited.append(scene_id)
        if False:
            yield None
        return scene_id, 100.0

    monkeypatch.setattr(runner, "_wait_scene_id", fake_wait_scene_id)

    result = runner._run_direct_runtime_action(
        lambda: runner._finish_daily_shuangxiu_after_continue(ctx, threading.Event(), {}),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [("双修修炼准备", 84.2, 844.2), ("离开场景", 615.8, 1063.3)]
    assert waited == []
    assert runner.status()["phase"] == "daily_shuangxiu_complete_continued"


def test_daily_boss_entry_match_does_not_click_boss_marquee():
    runner = create_behavior_tree_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.20, "w": 0.90, "h": 0.62},
    ])
    lines = [
        {"text": "喜[海浪无声]成功击败积麟秘境首领，获得了[龙魂精魄]", "x": 95.0, "y": 700.0, "w": 730.0, "h": 42.0},
        {"text": "抵御妖王来袭", "x": 370.0, "y": 760.0, "w": 260.0, "h": 42.0},
    ]

    matches = runner._daily_entry_matches(lines, image69, title_pattern=r"击\s*败\s*首\s*领")

    assert matches == []


def test_daily_audit_boss_identity_ignores_boss_marquee():
    runner = create_behavior_tree_runtime_runner()

    assert runner._daily_audit_task_identity("喜[海浪无声]成功击败积麟秘境首领，获得了[龙魂精魄] 0/3") is None
    assert runner._daily_audit_task_identity("日常 击败首领 0/3") == {
        "task_type": "daily_boss",
        "task_id": "daily-boss",
    }


def test_daily_audit_xianyuan_identity_ignores_xianyuan_duel():
    runner = create_behavior_tree_runtime_runner()

    assert runner._daily_audit_task_identity("参与仙缘斗法活5/次80/3") is None
    assert runner._daily_audit_task_identity("日常 挑战仙缘 0/3") == {
        "task_type": "daily_xianyuan",
        "task_id": "legacy-daily-xianyuan",
    }


def test_daily_gongfeng_count_fraction_zero_keeps_zero_first_number():
    runner = create_behavior_tree_runtime_runner()

    assert runner._daily_gongfeng_numbers("次数：0/1+")[:1] == [0]
    assert runner._daily_gongfeng_numbers("次数：2/1+")[:1] == [2]
    assert runner._daily_gongfeng_remaining("今日接受供奉次数：0") == 0


def test_generic_world_reward_tip_detection_api_is_removed():
    runner = create_behavior_tree_runtime_runner()

    assert not hasattr(runner, "_world_reward_tip_text_matches")
    assert not hasattr(runner, "_world_reward_tip_detected")


def test_readonly_mail_inventory_comparison_does_not_accept_title_only(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    seen_allow_title_only: list[bool] = []
    historical = SimpleNamespace(
        mail_key="old-mail",
        create_time_text="2026年07月15日22:44",
        title="功法新的敬赠",
        normalized_title="功法新的敬赠",
    )

    def fake_find(_title, _time_text, *, allow_title_only=True):
        seen_allow_title_only.append(bool(allow_title_only))
        return [historical]

    monkeypatch.setattr(runner, "_find_runtime_mail_records_for_visible_row", fake_find)
    monkeypatch.setattr(runner, "_mail_row_runtime_missing_reason", lambda *_args: "same_title_without_time")

    result = runner._compare_visible_mail_row_with_runtime_store(
        {"title": "功法新的敬赠", "time_text": "2026年07月16日22:44"}
    )

    assert seen_allow_title_only == [False]
    assert result["runtime_match"] == "missing"
    assert result["mail_key"] == ""


def test_daily_entry_world_text_with_fengmosha_does_not_click_popup(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [
        {"id": "daily", "kind": "rect", "title": "日常", "x": 0.04, "y": 0.20, "w": 0.08, "h": 0.07},
    ])
    ctx = {
        "entry": object(),
        "asset_tree_path": Path("asset-tree.json"),
        "images": {34: image34},
    }
    wait_clicks: list[tuple[int, str]] = []
    goto_targets: list[int] = []

    class FakeRuntime:
        def __init__(self):
            self.after_goto = False

        def current_scene(self, *_args, **_kwargs):
            if self.after_goto:
                return 69, 100.0, "daily-frame"
            return 34, 100.0, "world-frame"

        def ocr_text(self, frame=None, *_args, **_kwargs):
            if frame == "daily-frame":
                return "日常 击败首领 0/3"
            return "世界 储物袋 角色 装备 功法书 封魔杀"

        def wait_click(self, scene_id, shape_title, **_kwargs):
            wait_clicks.append((scene_id, shape_title))
            if False:
                yield None
            return "clicked"

        def goto_view(self, target_scene_id):
            goto_targets.append(target_scene_id)
            self.after_goto = True
            if False:
                yield None
            return target_scene_id

    fake_runtime = FakeRuntime()

    def false_generator():
        if False:
            yield None
        return False

    monkeypatch.setattr(runner, "_leave_world_side_scene_if_present", lambda *_args, **_kwargs: false_generator())

    result = runner._run_direct_runtime_action(
        lambda: runner._enter_daily_from_world_like(
            ctx,
            fake_runtime,
            threading.Event(),
            "world-frame",
            34,
            "世界 储物袋 角色 装备 功法书 封魔杀",
            label="日常_首领",
        ),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == 69
    assert wait_clicks == []
    assert goto_targets == [69]


def test_enter_daily_retries_when_promo_interrupt_returns_world(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [
        {"id": "daily", "kind": "rect", "title": "日常", "x": 0.04, "y": 0.20, "w": 0.08, "h": 0.07},
    ])
    ctx = {
        "entry": object(),
        "asset_tree_path": Path("asset-tree.json"),
        "images": {34: image34},
    }

    class FakeRuntime:
        def __init__(self):
            self.goto_count = 0
            self.last_frame = "world-frame"
            self.settles = 0

        def goto_view(self, target_scene_id):
            self.goto_count += 1
            self.last_frame = "world-after-promo-frame" if self.goto_count == 1 else "daily-frame"
            if False:
                yield None
            return target_scene_id

        def current_scene(self, *_args, **_kwargs):
            if self.last_frame == "daily-frame":
                return 69, 100.0, "daily-frame"
            return 34, 100.0, self.last_frame

        def ocr_text(self, frame=None, *_args, **_kwargs):
            if frame == "daily-frame":
                return "日常 活跃度 击败首领 0/3"
            return "世界 储物袋 角色 装备 功法书 大地图"

        def wait_action_settle(self, *_args, **_kwargs):
            self.settles += 1
            if False:
                yield None
            return None

    fake_runtime = FakeRuntime()

    def false_generator():
        if False:
            yield None
        return False

    monkeypatch.setattr(runner, "_leave_world_side_scene_if_present", lambda *_args, **_kwargs: false_generator())

    result = runner._run_direct_runtime_action(
        lambda: runner._enter_daily_from_world_like(
            ctx,
            fake_runtime,
            threading.Event(),
            "world-frame",
            34,
            "世界 储物袋 角色 装备 功法书 大地图",
            label="日常_首领",
        ),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == 69
    assert fake_runtime.goto_count == 2
    assert fake_runtime.settles >= 1


def test_enter_daily_unknown_direct_failure_recovers_via_world_anchor(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [
        {"id": "daily", "kind": "rect", "title": "日常", "x": 0.04, "y": 0.20, "w": 0.08, "h": 0.07},
    ])
    ctx = {
        "entry": object(),
        "asset_tree_path": Path("asset-tree.json"),
        "images": {34: image34},
    }

    class FakeRuntime:
        def __init__(self):
            self.scene_id = None
            self.goto_targets: list[int] = []

        def current_scene(self, *_args, **_kwargs):
            if self.scene_id == 34:
                return 34, 100.0, "world-frame"
            if self.scene_id == 69:
                return 69, 100.0, "daily-frame"
            return None, 0.0, "unknown-frame"

        def ocr_text(self, frame=None, *_args, **_kwargs):
            if frame == "world-frame":
                return "世界 储物袋 角色 装备 功法书 大地图"
            if frame == "daily-frame":
                return "日常 活跃度"
            return "仙喻 神农大帝 前往铸魂"

        def goto_view(self, target_scene_id):
            self.goto_targets.append(target_scene_id)
            if target_scene_id == 69 and self.scene_id is None:
                raise RuntimeError("unknown 过渡帧尚未完成返回")
            self.scene_id = target_scene_id
            if False:
                yield None
            return target_scene_id

    fake_runtime = FakeRuntime()

    def false_generator():
        if False:
            yield None
        return False

    def no_youli_recovery(*_args, **_kwargs):
        if False:
            yield None
        return False, None, "unknown-frame", "仙喻 神农大帝"

    monkeypatch.setattr(runner, "_leave_world_side_scene_if_present", lambda *_args, **_kwargs: false_generator())
    monkeypatch.setattr(runner, "_recover_daily_youli_result_before_daily_entry", no_youli_recovery)

    result = runner._run_direct_runtime_action(
        lambda: runner._enter_daily_from_world_like(
            ctx,
            fake_runtime,
            threading.Event(),
            "unknown-frame",
            None,
            "仙喻 神农大帝 前往铸魂",
            label="洞天_行动力",
        ),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == 69
    assert fake_runtime.goto_targets == [69, 34, 69]


def test_daily_task_row_progress_repairs_ocr_prefix_glued_to_fraction():
    runner = create_behavior_tree_runtime_runner()
    zero_lines = [
        {"text": "抵御妖王来袭", "x": 430.0, "y": 1028.0, "w": 242.0, "h": 44.0},
        {"text": "80/2可购买", "x": 421.0, "y": 1141.0, "w": 393.0, "h": 59.0},
    ]
    done_lines = [
        {"text": "抵御妖王来袭", "x": 430.0, "y": 1028.0, "w": 242.0, "h": 44.0},
        {"text": "82/2可购买", "x": 421.0, "y": 1141.0, "w": 393.0, "h": 59.0},
    ]

    assert runner._daily_task_row_progress(zero_lines, 1050.0) == (0, 2)
    assert runner._daily_task_row_progress(done_lines, 1050.0) == (2, 2)


def test_daily_xianyuan_detail_and_dialogue_text_fallbacks():
    runner = create_behavior_tree_runtime_runner()

    assert runner._daily_xianyuan_text_is_detail("身份 魔界三大始祖之一 功法主修 剑修 出没地点 落潮城 前往")
    assert not runner._daily_xianyuan_text_is_detail("查探 送礼 教他做人")
    assert runner._daily_xianyuan_text_is_dialogue("查探 送礼 教他做人 我可是魔界始祖")
    assert not runner._daily_xianyuan_text_is_dialogue("出没地点 落潮城 前往")


def test_daily_assistant_requires_assistant_asset_after_list_detected(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "exit", "kind": "rect", "title": "退出", "x": 0.02, "y": 0.93, "w": 0.1, "h": 0.05},
    ])
    ctx = {"entry": object(), "images": {69: image69}}
    clicked: list[tuple[float, float]] = []
    runner._click_frame_point = lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1)))  # type: ignore[method-assign]
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))

    try:
        runner._run_direct_runtime_action(
            lambda: runner._run_daily_assistant_from_list(ctx, threading.Event(), {}),
            stop_event=threading.Event(),
            tick_seconds=0.01,
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("daily_assistant must not continue without #204 assistant list assets")

    assert clicked == [(63.0, 1528.0)]
    assert "缺少新版 #204「小助手总览」标注" in message


def test_daily_assistant_entry_matches_bottom_tab_merged_ocr_line():
    runner = create_behavior_tree_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.1, "y": 0.15, "w": 0.8, "h": 0.6},
    ])
    lines = [{"text": "活动报名小助手奖励找回新", "x": 131.0, "y": 1385.0, "w": 600.0, "h": 45.0}]

    matches = runner._daily_assistant_entry_matches(lines, image69)

    assert matches
    x, y, text = matches[0]
    assert text == "活动报名小助手奖励找回新"
    assert 350 <= x <= 370
    assert y == 1407.5


def test_daily_assistant_entry_matches_bottom_tab_real_ocr_width():
    runner = create_behavior_tree_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.1, "y": 0.15, "w": 0.8, "h": 0.6},
    ])
    lines = [{"text": "活动报名小助手奖励找回", "x": 129.0, "y": 1384.0, "w": 425.0, "h": 33.0}]

    matches = runner._daily_assistant_entry_matches(lines, image69)

    assert matches
    x, y, text = matches[0]
    assert text == "活动报名小助手奖励找回"
    assert 335 <= x <= 350
    assert y == 1400.5


def test_daily_assistant_after_entry_keeps_waiting_while_source_scene_is_still_visible(monkeypatch):
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __init__(self):
            self.scenes = iter([(69, 100.0, "daily-frame"), (204, 100.0, "assistant-frame")])
            self.waits: list[float] = []

        def current_scene(self, *_scene_ids, **_kwargs):
            return next(self.scenes)

        def ocr_text(self, frame):
            return "日常" if frame == "daily-frame" else "一键执行"

        def wait_action_settle(self, seconds):
            self.waits.append(seconds)
            if False:
                yield None

    runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)

    result = _drain_generator(runner._wait_daily_assistant_after_entry(
        {"images": {}},
        threading.Event(),
        {"assistant_post_click_timeout": 5.0},
    ))

    assert result == (204, 100.0)
    assert runtime.waits == [0.5]


def test_world_identity_has_no_business_ocr_shortcut():
    runner = create_behavior_tree_runtime_runner()

    assert not hasattr(runner, "_daily_assistant_text_is_world_like")
    assert not hasattr(runner, "_world_scene_ocr_confirmed_text")


def test_daily_assistant_one_key_rejects_direct_world_without_result(monkeypatch):
    runner = create_behavior_tree_runtime_runner()

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        @contextmanager
        def expect_views(self, *_views):
            yield self

        def wait_click(self, *_args, **_kwargs):
            if False:
                yield None

        def wait_action_settle(self, *_args, **_kwargs):
            if False:
                yield None

        def current_scene(self, *_args, **_kwargs):
            return 34, 100.0, "world"

        def ocr_text(self, _frame):
            return "世界 大地图 角色 装备"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    with pytest.raises(RuntimeError) as exc_info:
        _drain_generator(runner._run_daily_assistant_one_key_from_overview(
            {"images": {}},
            FakeStopEvent(),
            {},
            _image("小助手总览", "0204.png", [{"title": "一键执行"}]),
        ))

    _assert_daily_assistant_unverified_error(exc_info, "直接回到 #34")


def test_daily_assistant_one_key_claims_business_foreground_for_whole_transaction(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, {"images": {}, "asset_tree": []})
    observed: list[tuple[int, ...]] = []

    def claimed(*_args, **_kwargs):
        observed.append(runtime.active_business_view_ids())
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(runner, "_run_daily_assistant_one_key_claimed", claimed)

    result = _drain_generator(runner._run_daily_assistant_one_key_from_overview(
        {"images": {}},
        threading.Event(),
        {},
        _image("小助手总览", "0204.png", [{"title": "一键执行"}]),
    ))

    assert result == "success"
    assert observed == [(276, 277, 275, 237)]
    assert runtime.active_business_view_ids() == ()


def test_daily_assistant_one_key_rejects_overview_without_result(monkeypatch):
    runner = create_behavior_tree_runtime_runner()

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        @contextmanager
        def expect_views(self, *_views):
            yield self

        def wait_click(self, *_args, **_kwargs):
            if False:
                yield None

        def wait_action_settle(self, *_args, **_kwargs):
            if False:
                yield None

        def current_scene(self, *_args, **_kwargs):
            return 204, 100.0, "overview"

        def ocr_text(self, _frame):
            return "小助手 一键执行 游历 妖王来袭"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    with pytest.raises(RuntimeError) as exc_info:
        _drain_generator(runner._run_daily_assistant_one_key_from_overview(
            {"images": {}},
            FakeStopEvent(),
            {"assistant_one_key_confirm_timeout": 0.001},
            _image("小助手总览", "0204.png", [{"title": "一键执行"}]),
        ))

    _assert_daily_assistant_unverified_error(exc_info, "仍在小助手总览")


def test_daily_assistant_one_key_progress_times_out_when_ocr_time_never_parses(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    settle_seconds: list[float] = []

    class FakeRuntime:
        def current_scene(self, *_args, **_kwargs):
            return 277, 100.0, "progress"

        def ocr_text(self, _frame):
            return "执行进度 剩余时间 无法识别"

        def wait_action_settle(self, seconds):
            settle_seconds.append(seconds)
            if False:
                yield None

    monotonic_values = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(daily_challenge_module.time, "monotonic", lambda: next(monotonic_values))

    with pytest.raises(TimeoutError, match=r"等待 #277 进度或 #275 结果超时"):
        _drain_generator(runner._wait_daily_assistant_one_key_progress(
            {},
            threading.Event(),
            {"assistant_one_key_progress_timeout": 1.0},
            FakeRuntime(),
        ))

    assert settle_seconds == [10.0]


def test_daily_assistant_one_key_default_progress_window_covers_long_transaction(monkeypatch):
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def current_scene(self, *_args, **_kwargs):
            return 275, 100.0, "result"

        def ocr_text(self, _frame):
            return "执行结果 退出"

    monotonic_values = iter((0.0, 181.0))
    monkeypatch.setattr(daily_challenge_module.time, "monotonic", lambda: next(monotonic_values))

    result = _drain_generator(
        runner._wait_daily_assistant_one_key_progress(
            {},
            threading.Event(),
            {},
            FakeRuntime(),
        )
    )

    assert result is None


def test_daily_assistant_one_key_progress_keeps_parsed_wait_and_result_path(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    scenes = iter(((277, 100.0, "progress"), (275, 100.0, "result")))
    settle_seconds: list[float] = []

    class FakeRuntime:
        def current_scene(self, *_args, **_kwargs):
            return next(scenes)

        def ocr_text(self, frame):
            if frame == "progress":
                return "执行进度 剩余时间 00:03"
            return "执行结果 退出"

        def wait_action_settle(self, seconds):
            settle_seconds.append(seconds)
            if False:
                yield None

    monotonic_values = iter((0.0, 0.0, 0.5))
    monkeypatch.setattr(daily_challenge_module.time, "monotonic", lambda: next(monotonic_values))

    result = _drain_generator(runner._wait_daily_assistant_one_key_progress(
        {},
        threading.Event(),
        {"assistant_one_key_progress_timeout": 10.0},
        FakeRuntime(),
    ))

    assert result is None
    assert settle_seconds == [3.0]


def test_world_side_leave_uses_formal_scene_and_shape_actions(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    actions: list[tuple] = []

    class FakeRuntime:
        def current_scene(self, candidates, **kwargs):
            actions.append(("current_scene", tuple(candidates), kwargs))
            if 85 in candidates:
                return 85, 96.0, "frame"
            return 86, 98.0, "confirm"

        def wait_click(self, scene_id, shape_title, **kwargs):
            actions.append(("wait_click", scene_id, shape_title, kwargs))
            if False:
                yield None

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield None

        def wait_view(self, *scene_ids, **kwargs):
            actions.append(("wait_view", scene_ids, kwargs))
            if False:
                yield None
            return 34

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    ctx: dict[str, Any] = {}

    recovered = _drain_generator(
        runner._leave_world_side_scene_if_present(
            ctx,
            threading.Event(),
            "frame",
            "离 开",
            label="测试",
        )
    )

    assert recovered is True
    assert ("wait_click", 85, "离开", {}) in actions
    assert ("wait_click", 86, "确认", {}) in actions
    assert any(item[0] == "wait_view" and item[1] == (34,) for item in actions)
    assert ctx["_go_scene_known_scene_id"] == 34


def test_scene_number_does_not_prefetch_unrelated_ocr_identity(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [
        {"id": "world-id", "kind": "rect", "title": "世界标识", "sceneIdentityRole": "required"},
    ])
    image999 = _image("其它OCR场景", "0999.png", [
        {
            "id": "ocr-id",
            "kind": "rect",
            "title": "OCR身份",
            "sceneIdentityRole": "required",
            "ocrEnabled": True,
            "ocrMatchRole": "required",
            "ocrText": "其它",
        },
    ])
    ctx = {"images": {34: image34, 999: image999}}
    ocr_calls: list[str] = []

    monkeypatch.setattr(runner, "_runtime_scene_candidate_ids", lambda _ctx: [34])
    monkeypatch.setattr(runner, "_cached_ocr_fragments", lambda _ctx, _frame: ocr_calls.append("ocr") or [])
    monkeypatch.setattr(runner, "_scene_score", lambda _ctx, image, _frame: 100.0 if image is image34 else 0.0)

    assert runner._identify_scene_number(ctx, "frame") == (34, 100.0)
    assert ocr_calls == []


def test_mail_selective_claim_does_not_reclassify_world_side_scene_from_business_ocr(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import mail as mail_tasks

    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    image86 = _image("离开场景", "0086.png", [
        {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.6240740740740741, "y": 0.64375, "w": 0.12037037037037035, "h": 0.04166666666666663},
    ])
    image121 = _image("邮件", "0121.png", [
        {"id": "list", "kind": "rect", "title": "邮件清单2", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6},
    ])
    ctx = {"entry": object(), "asset_tree_path": tmp_path / "asset_tree.json", "images": {34: image34, 86: image86, 121: image121}}
    frames = iter(["scene-inside", "leave-confirm", "world-after-leave", "mail-list"])
    clicked: list[tuple[float, float]] = []
    opened_mail: list[bool] = []

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        attrs: dict[str, object] = {}

        def cur_frame(self, update: bool = False):
            return next(frames)

        def current_scene(self, _candidates=None, *, frame_data_url=None, update=False, **_kwargs):
            del update
            current_frame = frame_data_url if frame_data_url is not None else next(frames)
            scene_id, score = fake_identify(ctx, current_frame, _candidates)
            return scene_id, score, current_frame

        def wait_view(self, *_args, **_kwargs):
            if False:
                yield BehaviorTreeStatus.RUNNING

        def scroll_shape_content(self, *_args, **_kwargs):
            if False:
                yield BehaviorTreeStatus.RUNNING
            return False

    def fake_identify(_ctx, frame, _preferred=None):
        if frame == "scene-inside":
            return 69, 100.0
        if frame == "leave-confirm":
            return 86, 100.0
        if frame == "world-after-leave":
            return 34, 100.0
        return 121, 100.0

    def fake_ocr_fragments(frame):
        if frame == "leave-confirm":
            return [{"text": "是否离开当前场景?", "x": 323.0, "y": 722.0, "w": 281.0, "h": 34.0}]
        return [
            {"text": "离开", "x": 790.0, "y": 840.0, "w": 70.0, "h": 50.0},
            {"text": "角色 装备 储物袋 功法书", "x": 420.0, "y": 1450.0, "w": 300.0, "h": 50.0},
        ]

    def fake_open_mail(_runtime):
        opened_mail.append(True)
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_refresh_runtime_mail_snapshot", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        mail_tasks,
        "current_runtime_mail_sequence_snapshot",
        lambda _engine: {"available": True, "complete": True, "items": []},
    )
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(runner, "_identify_scene_number", fake_identify)
    monkeypatch.setattr(runner, "_ocr_fragments", fake_ocr_fragments)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_open_mail_selective_claim_entry", fake_open_mail)
    monkeypatch.setattr(runner, "_runtime_mail_rows_from_frame", lambda *_args, **_kwargs: [])

    with pytest.raises(RuntimeError, match="#69"):
        runner._run_direct_runtime_action(
            lambda: runner._execute_mail_selective_claim_task(ctx, FakeStopEvent(), {"max_actions": 1, "max_scrolls": 1}),
            stop_event=FakeStopEvent(),
            tick_seconds=0.01,
        )

    assert clicked == []
    assert opened_mail == [True]


def test_mail_selective_claim_default_scroll_limit_supports_large_mailbox(tmp_path, monkeypatch):
    source = (Path(__file__).parents[1] / "core/fanxiu/data_annotation/tasks/mail.py").read_text(encoding="utf-8")

    assert 'payload.get("max_scrolls") or 150' in source


def test_mail_visible_row_keys_ignore_dynamic_title_overlay():
    runner = create_behavior_tree_runtime_runner()
    view = behavior_tree_runtime_core.View(_image("邮件", "0121.png"))
    shape = behavior_tree_runtime_core.Shape(
        {"id": "row", "kind": "rect", "title": "邮件", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.05},
        parent_view=view,
    )
    before = [behavior_tree_runtime_core._RuntimeMailRow({"title": "未取之宝", "time_text": "2026年07月06日 12:00"}, shape)]
    covered = [behavior_tree_runtime_core._RuntimeMailRow({"title": "击败幻瑶谷首领获得", "time_text": "2026年07月06日 12:00"}, shape)]

    assert runner._mail_visible_row_keys(before) == runner._mail_visible_row_keys(covered)


def test_xianfu_learn_skill_is_dynamic_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "xianfu-learn-skill")

    assert task["task_type"] == "xianfu_learn_skill"
    assert task["label"] == "仙府_领悟绝技"
    assert task["source"] == "data_annotation_runtime"
    assert task["trigger_description"] == "动态"
    assert task["next_time"]
    assert task["error_retry_delay_seconds"] == 600
    assert _data_annotation_task_supported(task)


def test_unknown_old_mail_instance_is_not_interpreted_or_migrated():
    defaults = _default_data_annotation_scheduler_tasks()
    raw = [
        {
            "id": "mail-claim-check",
            "task_type": "mail_claim_check",
            "label": "邮件_领取检查",
            "source": "manual",
            "schedule_kind": "manual",
            "enabled": False,
            "payload": {"__scheduler_definition_task_type": "mail_claim_check"},
        },
        next(item for item in defaults if item["id"] == "mail-selective-claim"),
    ]

    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        defaults,
        {},
        task_supported=_data_annotation_task_supported,
        now=datetime(2026, 6, 10, 12, 0, 0),
    )
    mail_tasks = [item for item in tasks if str(item.get("task_type") or "").startswith("mail_")]

    assert changed is True
    mail_tuples = [(item["id"], item["task_type"], item["label"]) for item in mail_tasks]
    assert ("mail-claim-check", "mail_claim_check", "邮件_领取检查") in mail_tuples
    assert sum(item["id"] == "mail-claim-check" for item in mail_tasks) == 1
    assert {
        item["id"]
        for item in defaults
        if str(item.get("task_type") or "").startswith("mail_")
    }.issubset({item["id"] for item in mail_tasks})
    selective = next(item for item in mail_tasks if item["id"] == "mail-selective-claim")
    assert selective["payload"]["max_runtime_seconds"] == 10800


def test_mail_full_scan_is_not_a_default_scheduler_task():
    task_ids = {str(item.get("id") or "") for item in _default_data_annotation_scheduler_tasks()}

    assert "mail-full-scan" not in task_ids


def test_mail_full_scan_observe_only_ignores_claim_and_delete_policy(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image121 = _image("邮件", "0121.png", [])
    ctx = {"asset_tree_path": path, "images": {121: image121}}
    received: dict[str, bool] = {}

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def fake_scan(_ctx, _stop_event, *, action_enabled=True, **_kwargs):
        received["action_enabled"] = action_enabled
        return "success"

    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: (121, 100, "frame"))
    monkeypatch.setattr(runner, "_scan_mail_scene", fake_scan)

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_mail_legacy_scan_task(ctx, FakeStopEvent(), {"observe_only": True}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert received == {"action_enabled": False}


def test_legacy_mail_cleanup_alias_resolves_to_selective_claim_observe_only_job():
    from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
    from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition

    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("mail_cleanup")
    assert definition is not None
    assert definition.task_type == "mail_selective_claim"
    calls: list[str] = []

    class FakeRuntime:
        def goto_view(self, _scene_id):
            if False:
                yield BehaviorTreeStatus.RUNNING

    class FakeRunner:
        def _fanxiu_runtime(self, _ctx, *, stop_event):
            return FakeRuntime()

        def _execute_mail_legacy_scan_task(self, _ctx, _stop_event, _payload):
            calls.append("legacy")
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "legacy-result"

        def _execute_mail_selective_claim_task(self, _ctx, _stop_event, _payload):
            calls.append("cleanup")
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "cleanup-result"

    result = _drain_generator(
        definition.handler(FakeRunner(), {}, {"observe_only": True}, threading.Event())
    )

    assert result == "legacy-result"
    assert calls == ["legacy"]


def test_mail_selective_claim_returns_from_detail_when_claim_does_not_auto_close(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image("邮件", "0121.png", [])
    image122 = _image("邮件内容", "0122.png", [
        {"id": "claim", "kind": "rect", "title": "领取", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.05},
        {"id": "back", "kind": "rect", "title": "空白-返回", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05},
    ])
    ctx = {"entry": object(), "images": {121: image121, 122: image122}}
    runtime = runner._fanxiu_runtime(ctx, tmp_path / "asset_tree.json")
    title_shape = behavior_tree_runtime_core.Shape(
        {"id": "row", "kind": "rect", "title": "测试邮件", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.05},
        parent_view=behavior_tree_runtime_core.View(image121),
    )
    mail = behavior_tree_runtime_core._RuntimeMailRow({"title": "测试邮件"}, title_shape)
    clicked: list[str] = []
    wait_calls: list[tuple[int, ...]] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")

    def fake_wait_view(*views, **_kwargs):
        view_ids = tuple(int(view) for view in views)
        wait_calls.append(view_ids)
        if view_ids == (122, 123):
            if False:
                yield BehaviorTreeStatus.RUNNING
            return behavior_tree_runtime_core.View(image122)
        if view_ids == (121,):
            if False:
                yield BehaviorTreeStatus.RUNNING
            return behavior_tree_runtime_core.View(image121)
        raise AssertionError(view_ids)

    def fake_wait_mail_list_or_world(*_args, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "timeout"

    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)
    monkeypatch.setattr(runner, "_wait_mail_list_or_reopen_from_world_after_action", fake_wait_mail_list_or_world)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, *_args, **_kwargs: clicked.append(shape["title"]))

    result = runner._run_direct_runtime_action(
        lambda: runner._claim_runtime_mail_row(runtime, mail),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result.policy == "claim"
    assert result.wait_result == "timeout"
    assert result.visual_confirmed is False
    assert clicked == ["测试邮件", "领取", "空白-返回"]
    assert wait_calls == [(122, 123), (121,)]


def test_mail_selective_claim_runs_one_key_delete_after_reward_and_reopen(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image(
        "邮件",
        "0121.png",
        [{"id": "marker", "kind": "rect", "title": "邮件标识", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.06}],
    )
    image122 = _image("邮件内容", "0122.png", [
        {"id": "claim", "kind": "rect", "title": "领取", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.05},
        {"id": "back", "kind": "rect", "title": "空白-返回", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05},
    ])
    ctx = {"entry": object(), "images": {121: image121, 122: image122}}
    runtime = runner._fanxiu_runtime(ctx, tmp_path / "asset_tree.json", stop_event=threading.Event())
    title_shape = behavior_tree_runtime_core.Shape(
        {"id": "row", "kind": "rect", "title": "测试邮件", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.05},
        parent_view=behavior_tree_runtime_core.View(image121),
    )
    mail = behavior_tree_runtime_core._RuntimeMailRow({"title": "测试邮件"}, title_shape)
    clicked: list[str] = []
    reopened: list[bool] = []
    deleted: list[str] = []

    def fake_wait_view(*views, **_kwargs):
        view_ids = tuple(int(view) for view in views)
        if view_ids == (122, 123):
            if False:
                yield BehaviorTreeStatus.RUNNING
            return behavior_tree_runtime_core.View(image122)
        raise AssertionError(view_ids)

    def fake_wait_mail_list_or_world(*_args, **_kwargs):
        reopened.append(True)
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "reopened_after_reward"

    def fake_delete_read(_runtime, _view, *, reason):
        deleted.append(reason)
        if False:
            yield BehaviorTreeStatus.RUNNING
        return 121

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)
    monkeypatch.setattr(runner, "_wait_mail_list_or_reopen_from_world_after_action", fake_wait_mail_list_or_world)
    monkeypatch.setattr(runner, "_delete_read_mail_once", fake_delete_read)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, *_args, **_kwargs: clicked.append(shape["title"]))

    result = runner._run_direct_runtime_action(
        lambda: runner._claim_runtime_mail_row(runtime, mail),
        stop_event=runtime.stop_event,
        tick_seconds=0.01,
    )

    assert result.policy == "claim"
    assert result.wait_result == "reopened_after_reward"
    assert result.visual_confirmed is True
    assert clicked == ["测试邮件", "领取"]
    assert reopened == [True]
    assert deleted == ["领取返回 #121 后"]


def test_mail_selective_claim_accepts_direct_return_to_mail_list_without_reward_overlay(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image(
        "邮件",
        "0121.png",
        [{"id": "marker", "kind": "rect", "title": "邮件标识", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.06}],
    )
    image122 = _image("邮件内容", "0122.png", [
        {"id": "claim", "kind": "rect", "title": "领取", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.05},
    ])
    ctx = {"entry": object(), "images": {121: image121, 122: image122}}
    runtime = runner._fanxiu_runtime(ctx, tmp_path / "asset_tree.json", stop_event=threading.Event())
    title_shape = behavior_tree_runtime_core.Shape(
        {"id": "row", "kind": "rect", "title": "测试邮件", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.05},
        parent_view=behavior_tree_runtime_core.View(image121),
    )
    mail = behavior_tree_runtime_core._RuntimeMailRow({"title": "测试邮件"}, title_shape)
    deleted: list[str] = []

    def fake_wait_view(*views, **_kwargs):
        assert tuple(int(view) for view in views) == (122, 123)
        if False:
            yield BehaviorTreeStatus.RUNNING
        return behavior_tree_runtime_core.View(image122)

    def fake_wait_mail_list_or_world(*_args, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "list"

    def fake_delete_read(_runtime, _view, *, reason):
        deleted.append(reason)
        if False:
            yield BehaviorTreeStatus.RUNNING
        return 121

    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)
    monkeypatch.setattr(runner, "_wait_mail_list_or_reopen_from_world_after_action", fake_wait_mail_list_or_world)
    monkeypatch.setattr(runner, "_delete_read_mail_once", fake_delete_read)
    monkeypatch.setattr(runner, "_click_shape", lambda *_args, **_kwargs: None)

    result = runner._run_direct_runtime_action(
        lambda: runner._claim_runtime_mail_row(runtime, mail),
        stop_event=runtime.stop_event,
        tick_seconds=0.01,
    )

    assert result.policy == "claim"
    assert result.wait_result == "list"
    assert result.visual_confirmed is True
    assert deleted == ["领取返回 #121 后"]


def test_mail_selective_claim_wait_does_not_reclassify_world_from_business_ocr(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image(
        "邮件",
        "0121.png",
        [{"id": "marker", "kind": "rect", "title": "邮件标识", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.06}],
    )
    image122 = _image("邮件内容", "0122.png", [])
    ctx = {"images": {121: image121, 122: image122}}
    runtime = runner._fanxiu_runtime(ctx, tmp_path / "asset_tree.json", stop_event=threading.Event())
    events: list[str] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _targets=None: (None, 0.0))
    monkeypatch.setattr(runner, "_cached_ocr_fragments", lambda _ctx, _frame: [{"text": "角色 装备 星海 功法书 储物袋"}])
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: events.append("settle") or iter(()))

    def fake_reopen(_runtime):
        events.append("reopen")
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_reopen_mail_from_current_world_like", fake_reopen)

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_mail_list_or_reopen_from_world_after_action(
            runtime,
            behavior_tree_runtime_core.View(image122),
            timeout=1.0,
            label="邮件_选择性领取：返回邮件 #121",
        ),
        stop_event=runtime.stop_event,
        tick_seconds=0.01,
    )

    assert result == "timeout"
    assert "reopen" not in events


def test_mail_selective_claim_wait_treats_reward_overlay_as_transition(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image(
        "邮件",
        "0121.png",
        [{"id": "marker", "kind": "rect", "title": "邮件标识", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.06}],
    )
    image122 = _image("邮件内容", "0122.png", [])
    image347 = _image("恭喜获得", "0347.png", [])
    ctx = {"images": {121: image121, 122: image122, 347: image347}}
    runtime = runner._fanxiu_runtime(ctx, tmp_path / "asset_tree.json", stop_event=threading.Event())
    scenes = [
        (347, 100.0, "reward-frame", "恭喜获得 点击屏幕继续 2秒后自动关闭"),
        (121, 100.0, "list-frame", "邮件 资源领取通知"),
    ]

    def fake_scene_text(*_args, **_kwargs):
        return scenes.pop(0)

    monkeypatch.setattr(runner, "_fanxiu_runtime_scene_text", fake_scene_text)
    monkeypatch.setattr(runner, "_match_shape", lambda *_args, **_kwargs: {"matched": True, "similarity": 96.0})

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_mail_list_or_reopen_from_world_after_action(
            runtime,
            behavior_tree_runtime_core.View(image122),
            timeout=18.0,
            label="邮件_选择性领取：返回邮件 #121",
        ),
        stop_event=runtime.stop_event,
        tick_seconds=0.01,
    )

    assert result == "list_after_reward"
    assert scenes == []


def test_mail_selective_claim_wait_clicks_explicit_continue_hint_and_closes_item_detail(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image(
        "邮件",
        "0121.png",
        [{"id": "marker", "kind": "rect", "title": "邮件标识", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.06}],
    )
    image122 = _image("邮件内容", "0122.png", [])
    ctx = {"images": {121: image121, 122: image122}}
    runtime = runner._fanxiu_runtime(ctx, tmp_path / "asset_tree.json", stop_event=threading.Event())
    scenes = [
        (None, 0.0, "reward-frame", "造型·秦妍 每周天功法经验效率提升 点击屏幕继续"),
        (250, 100.0, "item-frame", "灵石 境界要求 描述"),
        (121, 100.0, "list-frame", "邮件 资源领取通知"),
    ]
    clicks: list[tuple[float, float]] = []
    wait_clicks: list[tuple[int, str]] = []

    monkeypatch.setattr(runner, "_fanxiu_runtime_scene_text", lambda *_args, **_kwargs: scenes.pop(0))
    monkeypatch.setattr(runner, "_match_shape", lambda *_args, **_kwargs: {"matched": True, "similarity": 96.0})
    monkeypatch.setattr(runtime, "click_frame_point", lambda _view, x, y: clicks.append((x, y)))
    monkeypatch.setattr(
        runtime,
        "ocr_fragments",
        lambda frame, **_kwargs: (
            [{"text": "点击屏幕继续", "x": 300.0, "y": 1420.0, "w": 260.0, "h": 45.0}]
            if frame == "reward-frame"
            else []
        ),
    )

    def fake_wait_click(view_id, shape, **_kwargs):
        wait_clicks.append((view_id, shape))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runtime, "wait_click", fake_wait_click)

    def fake_settle(_seconds=1.0):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runtime, "wait_action_settle", fake_settle)

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_mail_list_or_reopen_from_world_after_action(
            runtime,
            behavior_tree_runtime_core.View(image122),
            timeout=18.0,
            label="邮件_选择性领取：返回邮件 #121",
        ),
        stop_event=runtime.stop_event,
        tick_seconds=0.01,
    )

    assert result == "list_after_reward"
    assert clicks == [(430.0, 1442.5)]
    assert wait_clicks == [(250, "返回")]
    assert scenes == []


def test_mail_list_restore_preserves_unknown_when_scene_route_fails(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    ctx = {"images": {34: _image("世界", "0034.png", [])}}
    stop_event = threading.Event()

    def fail_wait(*_args, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        raise RuntimeError("等待邮件 #121 超时")

    class FakeRuntime:
        def goto_view(self, _view_id):
            if False:
                yield BehaviorTreeStatus.RUNNING
            raise RuntimeError("当前 unknown 没有到 #34 的可靠路径")

        def click_frame_point(self, *_args, **_kwargs):
            raise AssertionError("unknown 场景不得执行猜测坐标")

    monkeypatch.setattr(runner, "_wait_mail_list_ready", fail_wait)
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    with pytest.raises(RuntimeError, match="等待邮件 #121 超时"):
        runner._run_direct_runtime_action(
            lambda: runner._wait_mail_list_ready_or_restore_world(
                ctx,
                stop_event,
                timeout=1.0,
                label="邮件_历史扫描：等待邮件 #121",
            ),
            stop_event=stop_event,
            tick_seconds=0.01,
        )


def test_mail_selective_claim_deletes_read_and_confirms_348_after_reward(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image(
        "邮件",
        "0121.png",
        [{"id": "delete", "kind": "rect", "title": "一键删除", "x": 0.2, "y": 0.8, "w": 0.2, "h": 0.05}],
    )
    image348 = _image(
        "一键删除确认",
        "0348.png",
        [{"id": "confirm", "kind": "rect", "title": "确认", "x": 0.6, "y": 0.65, "w": 0.15, "h": 0.05}],
    )
    ctx = {"images": {121: image121, 348: image348}}
    runtime = runner._fanxiu_runtime(ctx, tmp_path / "asset_tree.json", stop_event=threading.Event())
    actions: list[tuple] = []
    waits = iter([behavior_tree_runtime_core.View(image348), behavior_tree_runtime_core.View(image121)])

    def fake_wait_view(*view_ids, **_kwargs):
        actions.append(("wait", tuple(view_ids)))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return next(waits)

    def fake_runtime_click_shape(target, shape, **_kwargs):
        if isinstance(target, int):
            actions.append(("confirm", target, shape))
        else:
            actions.append(("click", shape.title))

    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)
    monkeypatch.setattr(runtime, "click_shape", fake_runtime_click_shape)

    result = runner._run_direct_runtime_action(
        lambda: runner._delete_read_mail_once(runtime, behavior_tree_runtime_core.View(image121), reason="#347 奖励后"),
        stop_event=runtime.stop_event,
        tick_seconds=0.01,
    )

    assert result == 121
    assert actions == [
        ("click", "一键删除"),
        ("wait", (348, 210, 278)),
        ("confirm", 348, "确认"),
        ("wait", (121,)),
    ]


def test_mail_precise_click_uses_observed_title_after_fractional_scroll():
    runner = create_behavior_tree_runtime_runner()
    point = runner._precise_mail_observed_title_point(
        [
            {"text": "宗门镇邪活动奖励", "x": 250, "y": 450, "w": 300, "h": 50},
            {"text": "2026年08月09日21:04", "x": 250, "y": 515, "w": 330, "h": 40},
        ],
        title="宗门镇邪活动奖励",
        fallback_y=599,
        geometry=SimpleNamespace(title_center_offset=-35),
    )

    assert point == (400.0, 475.0)


def test_mail_precise_click_rejects_same_title_from_adjacent_slot():
    runner = create_behavior_tree_runtime_runner()
    point = runner._precise_mail_observed_title_point(
        [
            {"text": "奖励请查收", "x": 250, "y": 850, "w": 188, "h": 72},
        ],
        title="奖励请查收",
        fallback_y=1080,
        geometry=SimpleNamespace(title_center_offset=-35, row_pitch=194),
    )

    assert point is None


def test_mail_precise_click_uses_matching_duplicate_in_expected_slot():
    runner = create_behavior_tree_runtime_runner()
    point = runner._precise_mail_observed_title_point(
        [
            {"text": "奖励请查收", "x": 250, "y": 850, "w": 188, "h": 72},
            {"text": "奖励请查收", "x": 260, "y": 1009, "w": 188, "h": 72},
        ],
        title="奖励请查收",
        fallback_y=1080,
        geometry=SimpleNamespace(title_center_offset=-35, row_pitch=194),
    )

    assert point == (354.0, 1045.0)


def test_mail_selective_claim_wait_returns_detail_still_open_after_short_delay(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image("邮件", "0121.png", [])
    image122 = _image("邮件内容", "0122.png", [])
    ctx = {"images": {121: image121, 122: image122}}
    runtime = runner._fanxiu_runtime(ctx, tmp_path / "asset_tree.json", stop_event=threading.Event())
    times = [0.0, 6.0]

    class FakeTime:
        @staticmethod
        def monotonic():
            return times.pop(0) if times else 6.0

        @staticmethod
        def time():
            return 0.0

    monkeypatch.setitem(runner._wait_mail_list_or_reopen_from_world_after_action.__globals__, "time", FakeTime)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _targets: (122, 100.0))
    monkeypatch.setattr(runner, "_ocr_fragments", lambda _frame: [])

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_mail_list_or_reopen_from_world_after_action(
            runtime,
            behavior_tree_runtime_core.View(image122),
            timeout=18.0,
            label="邮件_选择性领取：返回邮件 #121",
        ),
        stop_event=runtime.stop_event,
        tick_seconds=0.01,
    )

    assert result == "detail_still_open"


def test_mail_world_menu_ocr_click_uses_mail_shape_center_when_available():
    runner = create_behavior_tree_runtime_runner()
    image35 = _image(
        "世界下方菜单",
        "0035.png",
        [
            {"id": "mail", "kind": "rect", "title": "邮件", "x": 0.505, "y": 0.912, "w": 0.093, "h": 0.066},
            {"id": "menu", "kind": "rect", "title": "菜单", "x": 0.36, "y": 0.705, "w": 0.513, "h": 0.278},
        ],
    )

    x, y = runner._mail_world_menu_icon_click_point(image35, 360.0, 1548.0)

    assert 495 <= x <= 500
    assert 1510 <= y <= 1514


def test_mail_world_menu_shape_wait_does_not_full_frame_ocr_when_mail_shape_exists(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image35 = _image(
        "世界下方菜单",
        "0035.png",
        [
            {"id": "mail", "kind": "rect", "title": "邮件", "x": 0.505, "y": 0.912, "w": 0.093, "h": 0.066},
            {"id": "menu", "kind": "rect", "title": "菜单", "x": 0.36, "y": 0.705, "w": 0.513, "h": 0.278},
        ],
    )
    ctx = {"images": {35: image35}}

    class FakeRuntime:
        def cur_frame(self, *, update: bool = False):
            return "frame"

        def ocr_fragments(self, _frame):
            raise AssertionError("existing mail shape should not trigger full-frame OCR fallback")

    class FakeTime:
        calls = 0

        @classmethod
        def time(cls):
            cls.calls += 1
            if cls.calls <= 2:
                return 0.0
            return 9.0

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setitem(runner._open_mail_from_world_menu_shape.__globals__, "time", FakeTime)

    with pytest.raises(RuntimeError, match="等待 #35 邮件入口超时"):
        runner._run_direct_runtime_action(
            lambda: runner._open_mail_from_world_menu_shape(ctx, threading.Event()),
            stop_event=threading.Event(),
            tick_seconds=0.01,
        )


def test_mail_stable_entry_uses_visible_menu_before_toggling_open_button(monkeypatch, tmp_path):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image(
        "世界",
        "0034.png",
        [{"id": "open", "kind": "rect", "title": "打开下方菜单", "x": 0.85, "y": 0.9, "w": 0.08, "h": 0.08}],
    )
    ctx = {"images": {34: image34}}
    clicked_open: list[str] = []
    visible_calls: list[str] = []

    class FakeRuntime:
        def cur_frame(self, *, update: bool = False):
            return "frame"

        def click_shape(self, *_args, **_kwargs):
            raise AssertionError("stable entry should not image-match the open menu button")

        def click_frame_point(self, _image, x, y):
            clicked_open.append(f"{round(float(x), 1)},{round(float(y), 1)}")

        def wait_action_settle(self, _seconds):
            if False:
                yield None

    def fake_visible(_ctx, _stop_event, **_kwargs):
        visible_calls.append("visible")
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_click_mail_from_visible_world_menu_once", fake_visible)

    result = runner._run_direct_runtime_action(
        lambda: runner._open_mail_stable_entry(ctx, threading.Event(), tmp_path / "asset_tree.json"),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert visible_calls == ["visible"]
    assert clicked_open == []


def test_mail_visible_menu_once_can_skip_scene_identification(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image35 = _image(
        "世界下方菜单",
        "0035.png",
        [
            {"id": "mail", "kind": "rect", "title": "邮件", "x": 0.45, "y": 0.9, "w": 0.1, "h": 0.05},
            {"id": "menu", "kind": "rect", "title": "菜单", "x": 0.36, "y": 0.705, "w": 0.513, "h": 0.278},
        ],
    )
    ctx = {"images": {35: image35}}
    clicked: list[tuple[float, float]] = []
    waited: list[str] = []

    class FakeRuntime:
        def cur_frame(self, *, update: bool = False):
            return "frame"

        def ocr_centers_in_shape(self, *_args, **_kwargs):
            return [(500.0, 1475.0, "仙缘邮件修为设置8638")]

        def current_scene(self, **_kwargs):
            raise AssertionError("stable menu probe should be able to skip scene identification")

        def click_frame_point(self, _image, x, y):
            clicked.append((round(float(x), 1), round(float(y), 1)))

    def fake_wait_ready(_ctx, _stop_event, **_kwargs):
        waited.append("ready")
        if False:
            yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fast menu probe should use current-frame OCR before image matching")))
    monkeypatch.setattr(runner, "_ocr_fragments_in_shapes", lambda *_args, **_kwargs: [{"text": "邮件", "x": 420, "y": 1460, "w": 50, "h": 30}])
    monkeypatch.setattr(runner, "_wait_mail_list_ready_or_restore_world", fake_wait_ready)

    result = runner._run_direct_runtime_action(
        lambda: runner._click_mail_from_visible_world_menu_once(ctx, threading.Event(), require_world_scene=False),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(450.0, 1480.0)]
    assert waited == ["ready"]


def test_mail_visible_menu_once_rejects_mail_list_status_text(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image35 = _image(
        "世界下方菜单",
        "0035.png",
        [
            {"id": "mail", "kind": "rect", "title": "邮件", "x": 0.45, "y": 0.9, "w": 0.1, "h": 0.05},
            {"id": "menu", "kind": "rect", "title": "菜单", "x": 0.36, "y": 0.705, "w": 0.513, "h": 0.278},
        ],
    )
    ctx = {"images": {35: image35}}
    clicked: list[tuple[float, float]] = []

    class FakeRuntime:
        def cur_frame(self, *, update: bool = False):
            return "frame"

        def ocr_centers_in_shape(self, *_args, **_kwargs):
            return []

        def current_scene(self, **_kwargs):
            raise AssertionError("stable menu probe should be able to skip scene identification")

        def click_frame_point(self, _image, x, y):
            clicked.append((float(x), float(y)))

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(
        runner,
        "_shape_score",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("mail-list status text must not fall through to image matching")),
    )
    monkeypatch.setattr(runner, "_ocr_fragments_in_shapes", lambda *_args, **_kwargs: [{"text": "已锁定4/10封邮件", "x": 340, "y": 1240, "w": 180, "h": 40}])

    result = runner._run_direct_runtime_action(
        lambda: runner._click_mail_from_visible_world_menu_once(ctx, threading.Event(), require_world_scene=False),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "missing"
    assert clicked == []


def test_mail_visible_menu_once_accepts_compound_world_menu_ocr(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image35 = _image(
        "世界下方菜单",
        "0035.png",
        [
            {"id": "mail", "kind": "rect", "title": "邮件", "x": 0.45, "y": 0.9, "w": 0.1, "h": 0.05},
            {"id": "menu", "kind": "rect", "title": "菜单", "x": 0.36, "y": 0.705, "w": 0.513, "h": 0.278},
        ],
    )
    ctx = {"images": {35: image35}}
    clicked: list[tuple[float, float]] = []

    class FakeRuntime:
        def cur_frame(self, *, update: bool = False):
            return "frame"

        def ocr_centers_in_shape(self, *_args, **_kwargs):
            return [(500.0, 1475.0, "仙缘邮件修为设置8638")]

        def current_scene(self, **_kwargs):
            raise AssertionError("stable menu probe should be able to skip scene identification")

        def click_frame_point(self, _image, x, y):
            clicked.append((round(float(x), 1), round(float(y), 1)))

    def fake_wait_ready(_ctx, _stop_event, **_kwargs):
        if False:
            yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fast menu probe should use current-frame OCR before image matching")))
    monkeypatch.setattr(runner, "_ocr_fragments_in_shapes", lambda *_args, **_kwargs: [{"text": "仙缘邮件修为设置8638", "x": 420, "y": 1460, "w": 220, "h": 30}])
    monkeypatch.setattr(runner, "_wait_mail_list_ready_or_restore_world", fake_wait_ready)

    result = runner._run_direct_runtime_action(
        lambda: runner._click_mail_from_visible_world_menu_once(ctx, threading.Event(), require_world_scene=False),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(450.0, 1480.0)]


def test_mail_visible_menu_once_ignores_click_use_text(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image35 = _image(
        "世界下方菜单",
        "0035.png",
        [
            {"id": "mail", "kind": "rect", "title": "邮件", "x": 0.45, "y": 0.9, "w": 0.1, "h": 0.05},
            {"id": "menu", "kind": "rect", "title": "菜单", "x": 0.36, "y": 0.705, "w": 0.513, "h": 0.278},
        ],
    )
    ctx = {"images": {35: image35}}
    clicked: list[tuple[float, float]] = []

    class FakeRuntime:
        def cur_frame(self, *, update: bool = False):
            return "frame"

        def ocr_centers_in_shape(self, *_args, **_kwargs):
            return []

        def current_scene(self, **_kwargs):
            raise AssertionError("stable menu probe should be able to skip scene identification")

        def click_frame_point(self, _image, x, y):
            clicked.append((float(x), float(y)))

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_ocr_fragments_in_shapes", lambda *_args, **_kwargs: [{"text": "点击使用", "x": 420, "y": 1220, "w": 80, "h": 30}])

    result = runner._run_direct_runtime_action(
        lambda: runner._click_mail_from_visible_world_menu_once(ctx, threading.Event(), require_world_scene=False),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "missing"
    assert clicked == []


def test_go_scene_ignores_world_click_use_text_and_uses_id_bound_route(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [
        {"id": "xianfu", "kind": "rect", "title": "仙府", "sceneJumpTarget": "171", "x": 0.88, "y": 0.68, "w": 0.08, "h": 0.08},
    ])
    image171 = _image("仙府主页", "0171.png", [])
    tree = [image34, image171]
    ctx = {"entry": object(), "asset_tree": tree, "images": {34: image34, 171: image171}}
    clicked: list[str] = []
    first_probe = True

    def identify_scene(_ctx, _frame, preferred=None):
        nonlocal first_probe
        if first_probe:
            first_probe = False
            return 34, 100.0
        if preferred and 171 in preferred:
            return 171, 100.0
        return 171, 100.0

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", identify_scene)
    monkeypatch.setattr(
        runner,
        "_click_scene_route_shape",
        lambda _ctx, _image, shape, _frame, *, jitter_radius=0: clicked.append(str(shape["title"])),
    )

    result = runner._run_direct_runtime_action(
        lambda: runner._go_scene_task(ctx, tmp_path / "asset_tree.json", 171, threading.Event()),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == ["仙府"]


def test_mail_stable_entry_clicks_open_shape_center_after_visible_miss(monkeypatch, tmp_path):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image(
        "世界",
        "0034.png",
        [{"id": "open", "kind": "rect", "title": "打开下方菜单", "x": 0.85, "y": 0.9, "w": 0.08, "h": 0.08}],
    )
    ctx = {"images": {34: image34}}
    clicked_open: list[str] = []
    visible_results = iter(["missing", "success"])

    class FakeRuntime:
        def click_shape(self, *_args, **_kwargs):
            raise AssertionError("stable entry should not image-match the open menu button")

        def click_frame_point(self, _image, x, y):
            clicked_open.append(f"{round(float(x), 1)},{round(float(y), 1)}")

        def wait_action_settle(self, _seconds):
            if False:
                yield None

    def fake_visible(_ctx, _stop_event, **_kwargs):
        return next(visible_results)

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_click_mail_from_visible_world_menu_once", fake_visible)

    result = runner._run_direct_runtime_action(
        lambda: runner._open_mail_stable_entry(ctx, threading.Event(), tmp_path / "asset_tree.json"),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked_open == ["801.0,1504.0"]


def test_mail_selective_claim_entry_does_not_reopen_after_stable_success(monkeypatch, tmp_path):
    runner = create_behavior_tree_runtime_runner()
    ctx = {"images": {34: _image("世界", "0034.png", [])}}
    stable_results = iter(["success"])
    reopened: list[str] = []

    class FakeRuntime:
        def __init__(self):
            self.ctx = ctx
            self.stop_event = threading.Event()
            self.asset_tree_path = tmp_path / "asset_tree.json"

    def fake_green(*_args, **_kwargs):
        if False:
            yield None
        return False

    def fake_stable(*_args, **_kwargs):
        return next(stable_results)

    def fake_reopen(_runtime):
        reopened.append("reopen")
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_leave_green_bottle_to_world_if_present", fake_green)
    monkeypatch.setattr(runner, "_fanxiu_runtime_scene_text", lambda *_args, **_kwargs: (34, 100.0, "frame", "世界"))
    monkeypatch.setattr(runner, "_open_mail_stable_entry", fake_stable)
    monkeypatch.setattr(runner, "_reopen_mail_from_current_world_like", fake_reopen)

    result = runner._run_direct_runtime_action(
        lambda: runner._open_mail_selective_claim_entry(FakeRuntime()),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert reopened == []


def test_mail_selective_claim_green_bottle_recovery_ignores_mail_page_danyao_text(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    clicked: list[tuple[int, float, float]] = []

    class FakeRuntime:
        def click_frame_point(self, scene_id, x, y):
            clicked.append((int(scene_id), float(x), float(y)))

        def wait_view(self, *_args, **_kwargs):
            if False:
                yield None
            return None

    monkeypatch.setattr(
        runner,
        "_fanxiu_runtime_scene_text",
        lambda *_args, **_kwargs: (121, 96.0, "frame", "灵鳄丹尊奖励 服用丹药获得属性翻倍"),
    )

    result = runner._run_direct_runtime_action(
        lambda: runner._leave_green_bottle_to_world_if_present({}, threading.Event(), FakeRuntime(), label="邮件_选择性领取"),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result is False
    assert clicked == []


def test_mail_reopen_uses_stable_entry_without_reward_cleanup(monkeypatch, tmp_path):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image(
        "世界",
        "0034.png",
        [{"id": "bottom-menu", "kind": "rect", "title": "下方菜单", "x": 0.36, "y": 0.91, "w": 0.52, "h": 0.07}],
    )
    ctx = {"images": {34: image34}}
    stable_results = iter(["success"])

    class FakeRuntime:
        def __init__(self):
            self.ctx = ctx
            self.stop_event = threading.Event()
            self.asset_tree_path = tmp_path / "asset_tree.json"

    def fake_stable(_ctx, _stop_event, _asset_tree_path, **_kwargs):
        return next(stable_results)

    monkeypatch.setattr(runner, "_open_mail_stable_entry", fake_stable)

    result = runner._run_direct_runtime_action(
        lambda: runner._reopen_mail_from_current_world_like(FakeRuntime()),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"


def test_generic_mail_reward_tip_cleanup_api_is_removed():
    runner = create_behavior_tree_runtime_runner()

    assert not hasattr(runner, "_mail_world_reward_tip_text_matches")
    assert not hasattr(runner, "_mail_world_reward_tip_detected")
    assert not hasattr(runner, "_close_mail_world_reward_tip_if_present")
    assert not hasattr(runner, "_close_mail_world_reward_tip_stack_if_present")


def test_visible_mail_menu_probe_missing_does_not_stamp_scene_35(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image35 = _image(
        "世界下方菜单",
        "0035.png",
        [{"id": "mail", "kind": "rect", "title": "邮件", "x": 0.5, "y": 0.9, "w": 0.1, "h": 0.05}],
    )
    ctx = {"images": {35: image35}}

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: (69, 100.0, "frame"))
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(runner, "_ocr_fragments", lambda _frame: [])

    stop_event = FakeStopEvent()
    result = runner._run_direct_runtime_action(
        lambda: runner._try_open_mail_from_visible_world_menu(ctx, stop_event, timeout=0.02),
        stop_event=stop_event,
        tick_seconds=0.01,
    )

    assert result == "missing"
    assert runner.status().get("current_scene") != 35
    assert runner.status()["phase"] == "mail_claim_probe_world_menu_mail"


def test_visible_mail_menu_probe_clicks_shape_in_world_context(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image35 = _image(
        "世界下方菜单",
        "0035.png",
        [{"id": "mail", "kind": "rect", "title": "邮件", "x": 0.45, "y": 0.9, "w": 0.1, "h": 0.05}],
    )
    ctx = {"images": {35: image35}}
    clicked: list[tuple[float, float]] = []
    waited: list[str] = []

    class FakeRuntime:
        def cur_frame(self, *, update: bool = False):
            return "world-frame"

        def current_scene(self):
            return 34, 100.0, "world-frame"

        def click_frame_point(self, _image, x, y):
            clicked.append((round(float(x), 1), round(float(y), 1)))

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: 100.0)

    def fake_wait_ready(_ctx, _stop_event, **_kwargs):
        waited.append("ready")
        if False:
            yield None

    monkeypatch.setattr(runner, "_wait_mail_list_ready_or_restore_world", fake_wait_ready)

    result = runner._run_direct_runtime_action(
        lambda: runner._try_open_mail_from_visible_world_menu(ctx, threading.Event(), timeout=0.01),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(450.0, 1480.0)]
    assert waited == ["ready"]


def test_visible_mail_menu_probe_requires_scene_35_before_ocr_click(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image35 = _image(
        "世界下方菜单",
        "0035.png",
        [{"id": "menu", "kind": "rect", "title": "菜单", "x": 0.36, "y": 0.705, "w": 0.513, "h": 0.278}],
    )
    ctx = {"images": {35: image35}}
    clicked: list[tuple[float, float]] = []

    class FakeRuntime:
        def cur_frame(self, *, update: bool = False):
            return "mail-frame"

        def current_scene(self):
            return 121, 100.0, "mail-frame"

        def ocr_fragments(self, _frame):
            return [{"text": "已锁定4/10封邮件", "x": 340, "y": 1240, "w": 180, "h": 40}]

        def click_frame_point(self, _view, x, y):
            clicked.append((x, y))

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = runner._run_direct_runtime_action(
        lambda: runner._try_open_mail_from_visible_world_menu(ctx, threading.Event(), timeout=0.01),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "missing"
    assert clicked == []


def test_mail_dynamic_entry_clicks_after_first_match_without_wait_click(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image68 = _image(
        "动态栏",
        "0068.png",
        [{"id": "mail", "kind": "rect", "title": "邮件", "x": 0.42, "y": 0.30, "w": 0.12, "h": 0.08}],
    )
    ctx = {"images": {68: image68}}
    clicked: list[tuple[float, float]] = []
    waited: list[str] = []

    class FakeRuntime:
        def cur_frame(self, *, update: bool = False):
            return "frame"

        def click_frame_point(self, _view, x, y):
            clicked.append((round(float(x), 1), round(float(y), 1)))

        def wait_click(self, *_args, **_kwargs):
            raise AssertionError("dynamic entry should reuse first match instead of waiting again")

    def fake_wait_ready(_ctx, _stop_event, **_kwargs):
        waited.append("ready")
        if False:
            yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_match_shape", lambda *_args, **_kwargs: {"matched": True, "similarity": 82})
    monkeypatch.setattr(runner, "_wait_mail_list_ready_or_restore_world", fake_wait_ready)

    result = runner._run_direct_runtime_action(
        lambda: runner._try_open_mail_dynamic_entry(ctx, threading.Event()),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(432.0, 544.0)]
    assert waited == ["ready"]


def test_mail_dynamic_entry_falls_back_when_click_does_not_open_list(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image68 = _image(
        "动态栏",
        "0068.png",
        [{"id": "mail", "kind": "rect", "title": "邮件", "x": 0.42, "y": 0.30, "w": 0.12, "h": 0.08}],
    )
    ctx = {"images": {68: image68}}
    clicked: list[tuple[float, float]] = []

    class FakeRuntime:
        def cur_frame(self, *, update: bool = False):
            return "frame"

        def click_frame_point(self, _view, x, y):
            clicked.append((round(float(x), 1), round(float(y), 1)))

    def fake_wait_ready(_ctx, _stop_event, **_kwargs):
        if False:
            yield None
        raise RuntimeError("未进入 #121")

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_match_shape", lambda *_args, **_kwargs: {"matched": True, "similarity": 84})
    monkeypatch.setattr(runner, "_wait_mail_list_ready_or_restore_world", fake_wait_ready)

    result = runner._run_direct_runtime_action(
        lambda: runner._try_open_mail_dynamic_entry(ctx, threading.Event()),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "missing"
    assert clicked == [(432.0, 544.0)]


def test_mail_world_menu_shape_click_uses_mail_shape_center(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image35 = _image(
        "世界下方菜单",
        "0035.png",
        [
            {"id": "mail", "kind": "rect", "title": "邮件", "x": 0.511, "y": 0.958, "w": 0.072, "h": 0.021},
            {"id": "menu", "kind": "rect", "title": "菜单", "x": 0.36, "y": 0.705, "w": 0.513, "h": 0.278},
        ],
    )
    ctx = {"images": {35: image35}}
    clicked = []
    waited = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _timeout):
            return False

    def fake_wait(_ctx, _stop_event, scene_id, **_kwargs):
        waited.append(scene_id)
        return "success"

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: 100.0)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))
    monkeypatch.setattr(runner, "_wait_scene_id", fake_wait)

    result = runner._run_direct_runtime_action(
        lambda: runner._open_mail_from_world_menu_shape(ctx, FakeStopEvent()),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert waited == [121]
    assert len(clicked) == 1
    x, y = clicked[0]
    assert 490 <= x <= 495
    assert 1548 <= y <= 1552


def test_mail_world_menu_does_not_click_unmatched_shape(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image35 = _image(
        "世界下方菜单",
        "0035.png",
        [
            {"id": "mail", "kind": "rect", "title": "邮件", "x": 0.511, "y": 0.958, "w": 0.072, "h": 0.021},
            {"id": "menu", "kind": "rect", "title": "菜单", "x": 0.36, "y": 0.705, "w": 0.513, "h": 0.278},
        ],
    )
    ctx = {"images": {35: image35}}
    clicked: list[tuple[float, float]] = []
    times = [0.0, 0.0, 9.0]

    class FakeRuntime:
        def cur_frame(self, *, update: bool = False):
            return "frame"

        def ocr_fragments(self, _frame):
            return []

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))
    monkeypatch.setattr(runner._open_mail_from_world_menu_shape.__globals__["time"], "time", lambda: times.pop(0) if times else 9.0)

    with pytest.raises(RuntimeError, match="等待 #35 邮件入口超时"):
        runner._run_direct_runtime_action(
            lambda: runner._open_mail_from_world_menu_shape(ctx, threading.Event()),
            stop_event=threading.Event(),
            tick_seconds=0.01,
        )

    assert clicked == []


def test_mail_rows_normalize_time_and_mark_read():
    runner = create_behavior_tree_runtime_runner()
    image121 = _image(
        "邮件",
        "0121.png",
        [{"id": "list", "kind": "rect", "title": "邮件清单2", "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.5}],
    )
    lines = [
        {"text": "香车馈赠", "x": 250, "y": 200, "w": 120, "h": 30},
        {"text": "O2026年06月02日16:17已阅", "x": 250, "y": 250, "w": 220, "h": 30},
    ]

    rows = runner._mail_rows_in_shape(lines, image121, "邮件清单2")

    assert rows == [
        {
            "title": "香车馈赠",
            "x": 310.0,
            "y": 215.0,
            "raw_text": "香车馈赠",
            "time_text": "2026年06月02日16:17",
            "raw_time_text": "O2026年06月02日16:17已阅",
            "is_read": True,
            "status": "已阅",
        }
    ]


def test_mail_rows_use_template_title_box_and_time_anchor():
    runner = create_behavior_tree_runtime_runner()
    image121 = _image(
        "邮件",
        "0121.png",
        [
            {"id": "list", "kind": "rect", "title": "邮件清单2", "x": 0.1074, "y": 0.3364, "w": 0.8037, "h": 0.4104},
            {
                "id": "tpl",
                "kind": "rect",
                "title": "邮件模板",
                "x": 0.2685,
                "y": 0.449,
                "w": 0.5722,
                "h": 0.0906,
                "children": [
                    {"id": "title", "kind": "rect", "title": "标题", "x": 0.2796, "y": 0.4531, "w": 0.413, "h": 0.0396},
                    {"id": "time", "kind": "rect", "title": "时间", "x": 0.2759, "y": 0.499, "w": 0.4148, "h": 0.0323},
                    {"id": "status", "kind": "rect", "title": "状态", "x": 0.704, "y": 0.4531, "w": 0.11, "h": 0.0396},
                ],
            },
        ],
    )
    lines = [
        {"text": "活动奖励未领取", "x": 300, "y": 815, "w": 220, "h": 30},
        {"text": "已间", "x": 690, "y": 815, "w": 36, "h": 30},
        {"text": "60F06月03", "x": 300, "y": 885, "w": 120, "h": 30},
        {"text": "2026年06月03日21:30已阅", "x": 300, "y": 885, "w": 250, "h": 30},
        {"text": "单bll业控]业", "x": 330, "y": 1140, "w": 140, "h": 30},
    ]

    rows = runner._mail_rows_in_shape(lines, image121, "邮件清单2")

    assert rows == [
        {
            "title": "活动奖励未领取",
            "x": 410.0,
            "y": 830.0,
            "raw_text": "活动奖励未领取",
            "time_text": "2026年06月03日21:30",
            "raw_time_text": "2026年06月03日21:30已阅",
            "is_read": True,
            "status": "已阅",
            "raw_status_text": "已间",
        }
    ]


def test_mail_rows_keep_duplicate_same_title_and_time_with_template():
    runner = create_behavior_tree_runtime_runner()
    image121 = _image(
        "邮件",
        "0121.png",
        [
            {"id": "list", "kind": "rect", "title": "邮件清单2", "x": 0.1074, "y": 0.3364, "w": 0.8037, "h": 0.55},
            {
                "id": "tpl",
                "kind": "rect",
                "title": "邮件模板",
                "x": 0.2685,
                "y": 0.449,
                "w": 0.5722,
                "h": 0.0906,
                "children": [
                    {"id": "title", "kind": "rect", "title": "标题", "x": 0.2796, "y": 0.4531, "w": 0.413, "h": 0.0396},
                    {"id": "time", "kind": "rect", "title": "时间", "x": 0.2759, "y": 0.499, "w": 0.4148, "h": 0.0323},
                ],
            },
        ],
    )
    lines = [
        {"text": "联盟天地弈局奖励", "x": 300, "y": 760, "w": 240, "h": 34},
        {"text": "2026年06月06日23:59", "x": 300, "y": 830, "w": 260, "h": 30},
        {"text": "联盟天地弈局奖励", "x": 300, "y": 955, "w": 240, "h": 34},
        {"text": "2026年06月06日23:59", "x": 300, "y": 1025, "w": 260, "h": 30},
    ]

    rows = runner._mail_rows_in_shape(lines, image121, "邮件清单2")

    assert [(row["title"], row["time_text"]) for row in rows] == [
        ("联盟天地弈局奖励", "2026年06月06日23:59"),
        ("联盟天地弈局奖励", "2026年06月06日23:59"),
    ]
    assert rows[0]["y"] != rows[1]["y"]


def test_runtime_no_attachment_policy_skips_read_mail(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    row = {"title": "香车馈赠", "time_text": "2026年06月02日16:17", "is_read": True}
    record = FanxiuMailRecord(
        mail_key="id:runtime-test",
        mail_id="runtime-test",
        title="香车馈赠",
        normalized_title="香车馈赠",
        create_time_text="2026年06月02日16:17",
                source="runtime_memory",
                present_in_runtime=True,
        status="seen",
        payload={"mail_rewards": []},
    )
    monkeypatch.setattr(runner, "_find_runtime_mail_record", lambda _title, _time_text, **_kwargs: record)
    monkeypatch.setattr(runner, "_find_runtime_mail_records_for_visible_row", lambda _title, _time_text: [record])

    runner._prepare_mail_row_policy(row)

    assert row["mail_key"] == ""
    assert row["policy"] == ""
    assert row["runtime_match"] == "ui_skipped"
    assert row["status"] == "已阅"
    assert row["time_text"] == "2026年06月02日16:17"


def test_runtime_claim_policy_does_not_rescue_read_mail_by_title(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    row = {"title": "灵脉收益", "time_text": "2026年06月09日16:11", "is_read": True}
    record = FanxiuMailRecord(
        mail_key="id:lingmai-history",
        mail_id="lingmai-history",
        title="灵脉收益",
        normalized_title="灵脉收益",
        create_time_text="2026年06月07日19:43",
                source="runtime_memory",
                present_in_runtime=True,
        status="claimed",
        payload={"mail_rewards": [{"item_name": "玄神灵液", "item_type": "道具", "amount": 287232}]},
    )
    monkeypatch.setattr(runner, "_find_runtime_mail_records_for_visible_row", lambda _title, _time_text: [record])

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["status"] == "已阅"
    assert row["runtime_match"] == "ui_skipped"
    assert row["mail_key"] == ""
    assert row["policy"] == ""


def test_runtime_claim_policy_claims_locked_visible_row_when_runtime_is_safe(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    row = {"title": "香车馈赠", "time_text": "2026年06月09日13:48", "list_has_lock": True}
    record = FanxiuMailRecord(
        mail_key="id:xiangche-latest",
        mail_id="xiangche-latest",
        title="香车馈赠",
        normalized_title="香车馈赠",
        create_time_text="2026年06月09日13:48",
                source="runtime_memory",
                present_in_runtime=True,
        status="可领",
        action_policy="claim",
        payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币", "amount": 8888}]},
    )
    monkeypatch.setattr(runner, "_find_runtime_mail_records_for_visible_row", lambda _title, _time_text: [record])

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["status"] == "锁定"
    assert row["mail_key"] == "id:xiangche-latest"
    assert row["policy"] == "claim"
    assert row["runtime_match"] == "matched"


def test_runtime_claim_policy_keeps_locked_visible_row_when_runtime_is_protected(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    row = {"title": "珍贵馈赠", "time_text": "2026年06月09日13:48", "list_has_lock": True}
    record = FanxiuMailRecord(
        mail_key="id:protected-latest",
        mail_id="protected-latest",
        title="珍贵馈赠",
        normalized_title="珍贵馈赠",
        create_time_text="2026年06月09日13:48",
                source="runtime_memory",
                present_in_runtime=True,
        status="seen",
        payload={"mail_rewards": [{"item_name": "洗灵奇石", "item_type": "资源", "amount": 1}]},
    )
    monkeypatch.setattr(runner, "_find_runtime_mail_records_for_visible_row", lambda _title, _time_text: [record])

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["status"] == "锁定"
    assert row["mail_key"] == "id:protected-latest"
    assert row["policy"] == ""
    assert row["runtime_match"] == "matched"


def test_mail_rows_use_template_status_lock_text():
    runner = create_behavior_tree_runtime_runner()
    image121 = _image(
        "邮件",
        "0121.png",
        [
            {"id": "list", "kind": "rect", "title": "邮件清单2", "x": 0.1074, "y": 0.3364, "w": 0.8037, "h": 0.4104},
            {
                "id": "tpl",
                "kind": "rect",
                "title": "邮件模板",
                "x": 0.2685,
                "y": 0.449,
                "w": 0.5722,
                "h": 0.0906,
                "children": [
                    {"id": "title", "kind": "rect", "title": "标题", "x": 0.2796, "y": 0.4531, "w": 0.413, "h": 0.0396},
                    {"id": "time", "kind": "rect", "title": "时间", "x": 0.2759, "y": 0.499, "w": 0.4148, "h": 0.0323},
                    {"id": "status", "kind": "rect", "title": "状态", "x": 0.704, "y": 0.4531, "w": 0.11, "h": 0.0396},
                ],
            },
        ],
    )
    lines = [
        {"text": "珍贵馈赠", "x": 300, "y": 815, "w": 120, "h": 30},
        {"text": "2026年06月07日12:00", "x": 300, "y": 885, "w": 220, "h": 30},
        {"text": "锁走", "x": 690, "y": 815, "w": 60, "h": 30},
    ]

    rows = runner._mail_rows_in_shape(lines, image121, "邮件清单2")

    assert rows[0]["status"] == "锁定"
    assert rows[0]["raw_status_text"] == "锁走"


def test_mail_status_single_lock_character_is_not_locked():
    runner = create_behavior_tree_runtime_runner()

    assert runner._normalize_mail_row_status("锁") == ""
    assert runner._normalize_mail_row_status("锁走") == "锁定"
    assert runner._normalize_mail_row_status("锁定") == "锁定"


def test_visible_mail_adjacency_uses_visual_slots_not_filtered_rows():
    runner = create_behavior_tree_runtime_runner()
    rows = [
        {"title": "a", "time_text": "2026年06月21日23:59", "y": 100, "visual_scope": "邮件清单2", "visual_slot_index": 0},
        # visual slot 1 exists on screen but OCR reconstruction failed, so it is not in rows.
        {"title": "c", "time_text": "2026年06月19日23:59", "y": 420, "visual_scope": "邮件清单2", "visual_slot_index": 2},
        {"title": "d", "time_text": "2026年06月18日23:59", "y": 580, "visual_scope": "邮件清单2", "visual_slot_index": 3},
    ]

    intervals = runner._visible_mail_adjacency_intervals(rows)

    assert intervals == [
        {
            "newer_time_text": "2026年06月19日23:59",
            "older_time_text": "2026年06月18日23:59",
        }
    ]


def test_visible_mail_adjacency_skips_same_time_and_cross_scope_rows():
    runner = create_behavior_tree_runtime_runner()
    rows = [
        {"title": "a", "time_text": "2026年06月21日23:59", "y": 100, "visual_scope": "第1封", "visual_slot_index": 0},
        {"title": "b", "time_text": "2026年06月21日23:59", "y": 260, "visual_scope": "邮件清单2", "visual_slot_index": 1},
        {"title": "c", "time_text": "2026年06月21日23:59", "y": 420, "visual_scope": "邮件清单2", "visual_slot_index": 2},
        {"title": "d", "time_text": "2026年06月20日23:59", "y": 580, "visual_scope": "邮件清单2", "visual_slot_index": 3},
    ]

    intervals = runner._visible_mail_adjacency_intervals(rows)

    assert intervals == [
        {
            "newer_time_text": "2026年06月21日23:59",
            "older_time_text": "2026年06月20日23:59",
        }
    ]


def test_runtime_mail_record_matches_noisy_ocr_title_by_time(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(behavior_tree_runtime_core, "_default_engine", engine)
    with Session(engine) as session:
        session.add(
            FanxiuMailRecord(
                mail_key="id:no-attachment",
                mail_id="no-attachment",
                title="仙缘夺魁个人榜奖励",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("仙缘夺魁个人榜奖励"),
                create_time_text="2026年06月04日23:59",
                source="runtime_memory",
                present_in_runtime=True,
                status="seen",
                payload={"mail_rewards": []},
            )
        )
        session.commit()
    runner = create_behavior_tree_runtime_runner()

    record = runner._find_runtime_mail_record("仙缘夺魅个人榜奖励", "2026年06月04日23:59")

    assert record is not None
    assert record.mail_key == "id:no-attachment"


def test_runtime_mail_record_does_not_match_title_without_same_time(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(behavior_tree_runtime_core, "_default_engine", engine)
    with Session(engine) as session:
        session.add(
            FanxiuMailRecord(
                mail_key="id:latest-same-title",
                mail_id="latest-same-title",
                title="太乙馈赠",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("太乙馈赠"),
                create_time_text="2026年06月07日19:17",
                source="runtime_memory",
                present_in_runtime=True,
                status="seen",
                payload={"mail_rewards": []},
            )
        )
        session.commit()
    runner = create_behavior_tree_runtime_runner()

    record = runner._find_runtime_mail_record("太乙馈赠", "2026年06月07日19:43")

    assert record is None


def test_visible_mail_row_falls_back_to_title_when_time_mismatches(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(behavior_tree_runtime_core, "_default_engine", engine)
    with Session(engine) as session:
        session.add(
            FanxiuMailRecord(
                mail_key="id:known-same-title",
                mail_id="known-same-title",
                title="分红发放",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("分红发放"),
                create_time_text="2026年06月05日13:07",
                source="runtime_memory",
                present_in_runtime=True,
                status="seen",
                payload={"mail_rewards": [{"item_name": "灵石", "amount": 200}]},
            )
        )
        session.commit()
    runner = create_behavior_tree_runtime_runner()
    row = {"title": "分红发放", "time_text": "2026年06月06日21:35"}

    runner._prepare_mail_row_policy(row, action_enabled=False)

    assert row["policy"] == ""
    assert row["mail_key"] == "id:known-same-title"
    assert row["runtime_match"] == "title_only"
    assert row["runtime_missing_reason"] == ""


def test_visible_mail_row_falls_back_to_title_only_runtime_group(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(behavior_tree_runtime_core, "_default_engine", engine)
    with Session(engine) as session:
        session.add(
            FanxiuMailRecord(
                mail_key="id:title-only-safe",
                mail_id="title-only-safe",
                title="节日馈赠",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("节日馈赠"),
                create_time_text="2026年06月07日12:00",
                source="runtime_memory",
                present_in_runtime=True,
                status="seen",
                payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币"}]},
            )
        )
        session.add(
            FanxiuMailRecord(
                mail_key="id:title-only-protected",
                mail_id="title-only-protected",
                title="节日馈赠",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("节日馈赠"),
                create_time_text="2026年06月06日12:00",
                source="runtime_memory",
                present_in_runtime=True,
                status="seen",
                payload={"mail_rewards": [{"item_name": "未知道具 #999999", "item_type": "资源"}]},
            )
        )
        session.commit()
    runner = create_behavior_tree_runtime_runner()
    row = {"title": "节日馈赠", "time_text": "2026年06月08日12:00"}

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["runtime_match"] == "title_only"
    assert row["mail_key"] in {"id:title-only-safe", "id:title-only-protected"}
    assert row["policy"] == ""


def test_visible_mail_row_title_only_claims_when_all_candidates_safe(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(behavior_tree_runtime_core, "_default_engine", engine)
    with Session(engine) as session:
        session.add(
            FanxiuMailRecord(
                mail_key="id:title-only-safe",
                mail_id="title-only-safe",
                title="节日馈赠",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("节日馈赠"),
                create_time_text="2026年06月07日12:00",
                source="runtime_memory",
                present_in_runtime=True,
                status="seen",
                payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币"}]},
            )
        )
        session.commit()
    runner = create_behavior_tree_runtime_runner()
    row = {"title": "节日馈赠", "time_text": "2026年06月08日12:00"}

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["runtime_match"] == "title_only"
    assert row["mail_key"] == "id:title-only-safe"
    assert row["policy"] == "claim"


def test_visible_mail_row_title_only_uses_initial_reward_rule_not_user_status(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(behavior_tree_runtime_core, "_default_engine", engine)
    with Session(engine) as session:
        session.add(
            FanxiuMailRecord(
                mail_key="id:title-only-user-locked",
                mail_id="title-only-user-locked",
                title="节日馈赠",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("节日馈赠"),
                create_time_text="2026年06月07日12:00",
                source="runtime_memory",
                present_in_runtime=True,
                status="锁定",
                payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币"}]},
            )
        )
        session.commit()
    runner = create_behavior_tree_runtime_runner()
    row = {"title": "节日馈赠", "time_text": "2026年06月08日12:00"}

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["runtime_match"] == "title_only"
    assert row["mail_key"] == "id:title-only-user-locked"
    assert row["policy"] == "claim"


def test_runtime_mail_fuzzy_title_does_not_cross_policy_conflict(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(behavior_tree_runtime_core, "_default_engine", engine)
    with Session(engine) as session:
        session.add(
            FanxiuMailRecord(
                mail_key="id:protected",
                mail_id="protected",
                title="未取之宝",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("未取之宝"),
                create_time_text="2026年06月06日22:00",
                source="runtime_memory",
                present_in_runtime=True,
                status="seen",
                payload={"mail_rewards": [{"item_name": "洗灵奇石"}]},
            )
        )
        session.add(
            FanxiuMailRecord(
                mail_key="id:delete",
                mail_id="delete",
                title="未取至宝",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("未取至宝"),
                create_time_text="2026年06月06日22:00",
                source="runtime_memory",
                present_in_runtime=True,
                status="seen",
                payload={"mail_rewards": []},
            )
        )
        session.commit()
    runner = create_behavior_tree_runtime_runner()

    record = runner._find_runtime_mail_record("未取之宝", "2026年06月06日22:00")

    assert record is not None
    assert fanxiu_api.fanxiu_mail_action_policy_for_record(record) == ""


def test_visible_read_mail_skips_even_when_runtime_is_missing_from_list(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    row = {"title": "资源领取通知", "time_text": "2026年06月05日22:00", "is_read": True}
    record = FanxiuMailRecord(
        mail_key="id:missing-visible",
        mail_id="missing-visible",
        title="资源领取通知",
        normalized_title="资源领取通知",
        create_time_text="2026年06月05日22:00",
        source="runtime",
        status="missing_from_list",
        payload={"mail_rewards": []},
    )
    monkeypatch.setattr(runner, "_find_runtime_mail_record", lambda _title, _time_text, **_kwargs: record)

    runner._prepare_mail_row_policy(row, action_policies={"delete"})

    assert row["mail_key"] == ""
    assert row["policy"] == ""
    assert row["runtime_match"] == "ui_skipped"


def test_mail_ui_delete_probe_deletes_only_delete_detail(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image("邮件", "0121.png", [])
    image123 = _image("邮件内容", "0123.png", [{"id": "delete", "kind": "rect", "title": "删除", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.08}])
    ctx = {"images": {121: image121, 123: image123}}
    row = {"title": "无附件旧邮件", "time_text": "2026年06月06日20:00", "x": 320, "y": 420}
    clicked_points: list[tuple[float, float]] = []
    clicked_shapes: list[str] = []
    updated: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def fake_wait_detail(*_args, **_kwargs):
        yield BehaviorTreeStatus.RUNNING
        return 123, 100.0

    def fake_wait_scene(*_args, **_kwargs):
        yield BehaviorTreeStatus.RUNNING
        return 121, 100.0

    def fake_wait_after_action(*_args, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "list"

    monkeypatch.setattr(runner, "_wait_mail_detail_or_list_scene", fake_wait_detail)
    monkeypatch.setattr(runner, "_wait_scene_id", fake_wait_scene)
    monkeypatch.setattr(runner, "_wait_mail_list_after_detail_action", fake_wait_after_action)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked_points.append((x, y)))
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, _frame=None, **_kwargs: clicked_shapes.append(shape["title"]))
    monkeypatch.setattr(runner, "_update_runtime_mail_action_for_row", lambda _row, **_kwargs: updated.append(_row["title"]))

    result = runner._run_direct_runtime_action(
        lambda: runner._probe_and_maybe_delete_mail_row(ctx, FakeStopEvent(), image121, row),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "processed"
    assert clicked_points[0] == (320, 420)
    assert clicked_points[1] == pytest.approx((450, 1344))
    assert clicked_shapes == []
    assert updated == ["无附件旧邮件"]


def test_runtime_claim_policy_clicks_claim_detail(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image("邮件", "0121.png", [])
    image122 = _image("邮件内容", "0122.png", [{"id": "claim", "kind": "rect", "title": "领取", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.08}])
    ctx = {"images": {121: image121, 122: image122}}
    row = {
        "title": "仙财福礼",
        "time_text": "2026年06月07日12:00",
        "x": 320,
        "y": 420,
        "policy": "claim",
        "mail_key": "id:claimable",
    }
    clicked_points: list[tuple[float, float]] = []
    clicked_shapes: list[str] = []
    updates: list[tuple[str, str]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def fake_wait_detail(*_args, **_kwargs):
        yield BehaviorTreeStatus.RUNNING
        return 122, 100.0

    def fake_wait_scene(*_args, **_kwargs):
        yield BehaviorTreeStatus.RUNNING
        return 121, 100.0

    def fake_wait_shape_match(*_args, **_kwargs):
        yield BehaviorTreeStatus.RUNNING
        return "frame", {"matched": True, "similarity": 100}

    def fake_wait_after_action(*_args, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "list"

    monkeypatch.setattr(runner, "_wait_mail_detail_or_list_scene", fake_wait_detail)
    monkeypatch.setattr(runner, "_wait_scene_id", fake_wait_scene)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked_points.append((x, y)))
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, _frame=None, **_kwargs: clicked_shapes.append(shape["title"]))
    monkeypatch.setattr(runner, "_wait_shape_match", fake_wait_shape_match)
    monkeypatch.setattr(runner, "_wait_mail_list_after_detail_action", fake_wait_after_action)
    monkeypatch.setattr(
        runner,
        "_update_runtime_mail_action_for_row",
        lambda _row, **kwargs: updates.append((_row["title"], kwargs["status"])),
    )

    result = runner._run_direct_runtime_action(
        lambda: runner._process_mail_row(ctx, FakeStopEvent(), image121, row),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "processed"
    assert clicked_points[0] == (320, 420)
    assert clicked_points[1] == pytest.approx((450, 1344))
    assert clicked_shapes == []
    assert updates == [("仙财福礼", "claim_requested")]


def test_runtime_mail_action_update_tolerates_sqlite_lock(monkeypatch):
    from sqlalchemy.exc import OperationalError

    from backend.core.fanxiu.data_annotation.tasks import mail as mail_tasks

    runner = create_behavior_tree_runtime_runner()
    warnings: list[str] = []

    def fake_update(*_args, **_kwargs):
        raise OperationalError("UPDATE fanxiumailrecord", {}, Exception("database is locked"))

    monkeypatch.setattr(mail_tasks, "update_runtime_mail_action", fake_update)
    monkeypatch.setattr(runner, "_log", lambda level, message: warnings.append(f"{level}:{message}"))

    runner._update_runtime_mail_action_for_row(
        {"mail_key": "id:locked"},
        status="claim_requested",
        evidence={"runtime_requested_action": "claim"},
    )

    assert warnings
    assert "database is locked" in warnings[0] or "数据库锁" in warnings[0]


def test_runtime_mail_action_update_reraises_other_db_errors(monkeypatch):
    from sqlalchemy.exc import OperationalError

    from backend.core.fanxiu.data_annotation.tasks import mail as mail_tasks

    runner = create_behavior_tree_runtime_runner()

    def fake_update(*_args, **_kwargs):
        raise OperationalError("UPDATE fanxiumailrecord", {}, Exception("no such table"))

    monkeypatch.setattr(mail_tasks, "update_runtime_mail_action", fake_update)

    with pytest.raises(OperationalError):
        runner._update_runtime_mail_action_for_row(
            {"mail_key": "id:broken"},
            status="claim_requested",
            evidence={"runtime_requested_action": "claim"},
        )


def test_mark_pending_runtime_mail_actions_skips_sqlite_lock(monkeypatch):
    from sqlalchemy.exc import OperationalError

    from backend.core.fanxiu.data_annotation.tasks import mail as mail_tasks

    runner = create_behavior_tree_runtime_runner()
    locked = FanxiuMailRecord(
        mail_key="id:locked",
        mail_id="locked",
        title="锁住的邮件",
        normalized_title="锁住的邮件",
        create_time_text="2026年07月01日00:00",
        source="runtime",
        status="claim_pending",
        payload={"mail_rewards": [{"item_name": "灵石", "amount": 1}]},
    )
    ok = FanxiuMailRecord(
        mail_key="id:ok",
        mail_id="ok",
        title="正常邮件",
        normalized_title="正常邮件",
        create_time_text="2026年07月01日00:01",
        source="runtime",
        status="claim_pending",
        payload={"mail_rewards": [{"item_name": "灵石", "amount": 2}]},
    )
    marked: list[str] = []

    monkeypatch.setattr(mail_tasks, "pending_runtime_mail_action_candidates", lambda *_args, **_kwargs: [locked, ok])
    monkeypatch.setattr(mail_tasks, "fanxiu_mail_action_policy_for_record", lambda _record: "claim")

    def fake_mark(_engine, record, **_kwargs):
        if record.mail_key == "id:locked":
            raise OperationalError("UPDATE fanxiumailrecord", {}, Exception("database is locked"))
        marked.append(record.mail_key)

    monkeypatch.setattr(mail_tasks, "mark_runtime_mail_record_missing_from_list", fake_mark)

    assert runner._mark_pending_runtime_mail_actions_not_visible(reason="full_scan") == 1
    assert marked == ["id:ok"]


def test_align_mail_records_from_visible_adjacency_skips_sqlite_lock(monkeypatch):
    from sqlalchemy.exc import OperationalError

    from backend.core.fanxiu.data_annotation.tasks import mail as mail_tasks

    runner = create_behavior_tree_runtime_runner()
    rows = [
        {"visual_slot_index": 0, "time_text": "2026年07月01日05:00"},
        {"visual_slot_index": 1, "time_text": "2026年06月30日23:59"},
    ]

    def fake_align(*_args, **_kwargs):
        raise OperationalError("UPDATE fanxiumailrecord", {}, Exception("database is locked"))

    monkeypatch.setattr(mail_tasks, "align_runtime_mail_records_claimable_between_visible_neighbors", fake_align)

    result = runner._align_mail_records_from_visible_adjacency(rows, source="mail_selective_claim")

    assert result["ok"] is True
    assert result["interval_count"] == 1
    assert result["updated"] == 0
    assert result["matched"] == 0


def test_find_runtime_mail_record_skips_schema_changed(monkeypatch):
    from sqlalchemy.exc import OperationalError

    from backend.core.fanxiu.data_annotation.tasks import mail as mail_tasks

    runner = create_behavior_tree_runtime_runner()

    def fake_find(*_args, **_kwargs):
        raise OperationalError("SELECT fanxiumailrecord", {}, Exception("database schema has changed"))

    monkeypatch.setattr(mail_tasks, "find_runtime_mail_record_exact", fake_find)

    record = runner._find_runtime_mail_record("鬼道八便个八仿天厕", "2026年06月21日23:59")

    assert record is None


def test_find_visible_runtime_mail_records_skips_schema_changed(monkeypatch):
    from sqlalchemy.exc import OperationalError

    from backend.core.fanxiu.data_annotation.tasks import mail as mail_tasks

    runner = create_behavior_tree_runtime_runner()

    def fake_find(*_args, **_kwargs):
        raise OperationalError("SELECT fanxiumailrecord", {}, Exception("database schema has changed"))

    monkeypatch.setattr(mail_tasks, "runtime_mail_records_for_visible_row_exact", fake_find)

    records = runner._find_runtime_mail_records_for_visible_row("鬼道八便个八仿天厕", "2026年06月21日23:59")

    assert records == []


def test_runtime_claim_requested_retries_after_cooldown(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    requested_at = datetime.fromtimestamp(time.time() - 120).strftime("%Y-%m-%d %H:%M:%S")
    record = FanxiuMailRecord(
        mail_key="id:claim-requested",
        mail_id="claim-requested",
        title="仙财福礼",
        normalized_title="仙财福礼",
        create_time_text="2026年06月07日12:00",
        source="runtime",
        status="claim_requested",
        payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币", "amount": 1688}]},
        evidence={"runtime_requested_action": "claim", "runtime_action_requested_at": requested_at},
    )

    assert runner._visible_runtime_mail_action_policy(record) == "claim"


def test_runtime_claim_requested_waits_during_cooldown():
    runner = create_behavior_tree_runtime_runner()
    requested_at = datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")
    record = FanxiuMailRecord(
        mail_key="id:claim-requested",
        mail_id="claim-requested",
        title="仙财福礼",
        normalized_title="仙财福礼",
        create_time_text="2026年06月07日12:00",
        source="runtime",
        status="claim_requested",
        payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币", "amount": 1688}]},
        evidence={"runtime_requested_action": "claim", "runtime_action_requested_at": requested_at},
    )

    assert runner._visible_runtime_mail_action_policy(record) == ""


def test_visible_mail_row_does_not_replay_terminal_runtime(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    row = {"title": "仙财福礼", "time_text": "2026年06月07日12:00"}
    record = FanxiuMailRecord(
        mail_key="id:already-marked",
        mail_id="already-marked",
        title="仙财福礼",
        normalized_title="仙财福礼",
        create_time_text="2026年06月07日12:00",
        source="runtime",
        status="deleted",
        payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币", "amount": 1688}]},
    )
    monkeypatch.setattr(runner, "_find_runtime_mail_records_for_visible_row", lambda _title, _time_text: [record])
    monkeypatch.setattr(runner, "_find_runtime_mail_record", lambda _title, _time_text, **_kwargs: record)

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["mail_key"] == "id:already-marked"
    assert row["policy"] == ""


def test_visible_mail_row_uses_next_active_duplicate_after_terminal_runtime(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    row = {"title": "香车馈赠", "time_text": "2026年07月27日18:19"}
    deleted = FanxiuMailRecord(
        mail_key="id:deleted-duplicate",
        mail_id="deleted-duplicate",
        title="香车馈赠",
        normalized_title="香车馈赠",
        create_time_text="2026年07月27日18:19",
        source="runtime",
        status="deleted",
        payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币"}]},
    )
    active = FanxiuMailRecord(
        mail_key="id:active-duplicate",
        mail_id="active-duplicate",
        title="香车馈赠",
        normalized_title="香车馈赠",
        create_time_text="2026年07月27日18:19",
        source="runtime",
        status="可领",
        payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币"}]},
    )
    monkeypatch.setattr(
        runner,
        "_find_runtime_mail_records_for_visible_row",
        lambda _title, _time_text: [deleted, active],
    )

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["mail_key"] == "id:active-duplicate"
    assert row["policy"] == "claim"


def test_visible_mail_row_skips_when_any_same_title_time_runtime_is_protected(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    row = {"title": "活动奖励未领取", "time_text": "2026年06月04日23:59"}
    safe = FanxiuMailRecord(
        mail_key="id:safe",
        mail_id="safe",
        title="活动奖励未领取",
        normalized_title="活动奖励未领取",
        create_time_text="2026年06月04日23:59",
        source="runtime",
        status="seen",
        payload={"mail_rewards": [{"item_name": "天资丹", "item_type": "道具", "amount": 30}]},
    )
    protected = FanxiuMailRecord(
        mail_key="id:protected",
        mail_id="protected",
        title="活动奖励未领取",
        normalized_title="活动奖励未领取",
        create_time_text="2026年06月04日23:59",
        source="runtime",
        status="seen",
        payload={"mail_rewards": [{"item_name": "炼丹灵草匣", "item_type": "资源", "amount": 25}]},
    )
    monkeypatch.setattr(runner, "_find_runtime_mail_records_for_visible_row", lambda _title, _time_text: [safe, protected])
    monkeypatch.setattr(runner, "_find_runtime_mail_record", lambda _title, _time_text, **_kwargs: safe)

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["mail_key"] == "id:safe"
    assert row["policy"] == ""


def test_scroll_shape_content_uses_half_page_slow_drag(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("邮件", "0121.png", [
        {"title": "邮件清单2", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6},
    ])
    ctx = {"images": {121: image}, "asset_tree": [image]}
    drags: list[tuple[float, float, float, float, int]] = []
    waits: list[float] = []
    monkeypatch.setattr(
        runner,
        "_drag_frame_point",
        lambda _ctx, _image, sx, sy, ex, ey, *, duration_ms=300: drags.append((sx, sy, ex, ey, duration_ms)),
    )
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: _png_data_url())
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, seconds=2.0, **_kwargs: waits.append(seconds) or iter(()))

    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, stop_event=threading.Event())
    changed = runner._run_direct_runtime_action(
        lambda: runtime.scroll_shape_content(121, "邮件清单2"),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert changed is False
    assert len(drags) == 1
    sx, sy, ex, ey, duration_ms = drags[0]
    assert sx == ex == 450
    assert sy == 1040
    assert ey == 560
    assert duration_ms == 1500
    assert waits == [1.5, 0.35]


def test_scroll_shape_content_can_limit_signature_to_recognition_shape(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("滚动页", "0121.png", [
        {"title": "滚动窗口", "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8},
        {"title": "识别区", "x": 0.15, "y": 0.3, "w": 0.7, "h": 0.4},
    ])
    ctx = {"images": {121: image}, "asset_tree": [image]}
    drags: list[tuple[float, float, float, float, int]] = []
    signatures: list[str] = []
    waits: list[float] = []

    monkeypatch.setattr(
        runner,
        "_drag_frame_point",
        lambda _ctx, _image, sx, sy, ex, ey, *, duration_ms=300: drags.append((sx, sy, ex, ey, duration_ms)),
    )
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: _png_data_url())
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, seconds=2.0, **_kwargs: waits.append(seconds) or iter(()))

    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, stop_event=threading.Event())

    def fake_signature(view_or_shape, shape=None, **_kwargs):
        target = view_or_shape if shape is None else runtime.shape(view_or_shape, shape)
        signatures.append(str(getattr(target, "raw", {}).get("title") or ""))
        return bytes([0 if len(signatures) == 1 else 255]) * 1024

    monkeypatch.setattr(runtime, "image_signature_bytes_in_shape", fake_signature)
    changed = runner._run_direct_runtime_action(
        lambda: runtime.scroll_shape_content(121, "滚动窗口", recognition_shape="识别区"),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert changed is True
    assert signatures == ["识别区", "识别区", "识别区"]
    assert len(drags) == 1
    sx, sy, ex, ey, duration_ms = drags[0]
    assert sx == ex == 450
    assert sy == 1120
    assert ey == 480
    assert duration_ms == 1500
    assert waits == [1.5, 0.35]


def test_daily_list_scene_identity_is_not_rejected_by_visible_row_ocr(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image69 = _image("日常", "0069.png")
    ctx = {"images": {69: image69}, "asset_tree": [image69]}

    class FakeRuntime:
        def current_scene(self, *_args, **_kwargs):
            return 69, 98.0, "frame"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    runner._ensure_daily_list_frame(
        ctx,
        "frame",
        [{"text": "丝竹管弦之盛，列坐其次"}],
        task_label="论道_座位",
    )


def test_runtime_daily_list_scene_identity_is_not_rejected_by_visible_row_ocr(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image69 = _image("日常", "0069.png")
    ctx = {"images": {69: image69}, "asset_tree": [image69]}
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, stop_event=threading.Event())
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (69, 98.0))

    runtime._ensure_daily_list_frame(
        "frame",
        [{"text": "丝竹管弦之盛，列坐其次"}],
        label="论道_座位",
    )


def test_wait_ocr_text_uses_annotated_load_direction(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("玄荒古域", "0417.png", [
        {
            "title": "窗口",
            "x": 0.02,
            "y": 0.08,
            "w": 0.97,
            "h": 0.77,
            "loadDirection": "right",
        },
    ])
    ctx = {"images": {417: image}, "asset_tree": [image]}
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, stop_event=threading.Event())
    directions: list[str] = []
    attempts = {"count": 0}
    expected = object()
    monkeypatch.setattr(runtime, "cur_frame", lambda update=False: f"frame-{attempts['count']}")

    def fake_find(*_args, **_kwargs):
        attempts["count"] += 1
        return expected if attempts["count"] == 3 else None

    def fake_scroll(*_args, direction=None, **_kwargs):
        directions.append(str(direction))
        if False:
            yield None
        return True

    monkeypatch.setattr(runtime, "find_ocr_text", fake_find)
    monkeypatch.setattr(runtime, "scroll_shape_content", fake_scroll)

    result = runner._run_direct_runtime_action(
        lambda: runtime.wait_ocr_text(
            417,
            "推",
            in_shapes=["窗口"],
            timeout_seconds=60,
        ),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result is expected
    assert directions == ["right", "right"]


def test_wait_click_ocr_text_can_reverse_search_and_use_first_relative_match(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("造化灵脉", "0285.png", [
        {
            "title": "窗口",
            "x": 0.01,
            "y": 0.10,
            "w": 0.97,
            "h": 0.60,
            "loadDirection": "down",
        },
    ])
    ctx = {"images": {285: image}, "asset_tree": [image]}
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, stop_event=threading.Event())
    match = OcrTextMatch(
        target="天罡圣脉",
        text="天罡圣脉",
        x=320.0,
        y=240.0,
        w=160.0,
        h=40.0,
        tokens=(),
    )
    waited: list[dict] = []
    clicked: list[tuple[float, float]] = []

    def fake_wait(*_args, **kwargs):
        waited.append(kwargs)
        if False:
            yield None
        return match

    monkeypatch.setattr(runtime, "wait_ocr_text", fake_wait)
    monkeypatch.setattr(runtime, "click_frame_point", lambda _view, x, y: clicked.append((x, y)))

    result = runner._run_direct_runtime_action(
        lambda: runtime.wait_click_ocr_text(
            285,
            "天罡圣脉",
            in_shapes=("窗口",),
            occurrence=0,
            anchor="top_left",
            offset=(0.0, 2.0),
            offset_unit="height",
            search_direction="up",
        ),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result is match
    assert waited == [{
        "in_shapes": ("窗口",),
        "occurrence": 0,
        "padding": 0,
        "timeout_seconds": 30.0,
        "poll_seconds": 1.0,
        "max_scrolls_per_direction": 30,
        "search_direction": "up",
        "max_gap_height_ratio": behavior_tree_runtime_core.DEFAULT_TEXT_TOKEN_GAP_HEIGHT_RATIO,
    }]
    assert clicked == [(320.0, 320.0)]


def test_wait_ocr_text_reuses_supplied_frame_before_refresh(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("造化灵脉", "0285.png", [
        {
            "title": "窗口",
            "x": 0.01,
            "y": 0.10,
            "w": 0.97,
            "h": 0.60,
            "loadDirection": "down",
        },
    ])
    ctx = {"images": {285: image}, "asset_tree": [image]}
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, stop_event=threading.Event())
    match = OcrTextMatch(
        target="天罡圣脉",
        text="天罡圣脉",
        x=320.0,
        y=240.0,
        w=160.0,
        h=40.0,
        tokens=(),
    )
    frames: list[str | None] = []

    def fake_find(_view, _target, **kwargs):
        frames.append(kwargs.get("frame_data_url"))
        return match if kwargs.get("frame_data_url") == "cached-frame" else None

    monkeypatch.setattr(runtime, "find_ocr_text", fake_find)
    monkeypatch.setattr(runtime, "cur_frame", lambda update=True: pytest.fail("supplied frame should be checked first"))
    monkeypatch.setattr(runtime, "scroll_shape_content", lambda *_args, **_kwargs: pytest.fail("cache hit should not scroll"))

    result = runner._run_direct_runtime_action(
        lambda: runtime.wait_ocr_text(
            285,
            "天罡圣脉",
            in_shapes=("窗口",),
            frame_data_url="cached-frame",
            max_scrolls_per_direction=8,
        ),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result is match
    assert frames == ["cached-frame"]


def test_wait_ocr_text_scans_opposite_after_primary_end(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("玄荒古域", "0417.png", [
        {
            "title": "窗口",
            "x": 0.02,
            "y": 0.08,
            "w": 0.97,
            "h": 0.77,
            "loadDirection": "right",
        },
    ])
    ctx = {"images": {417: image}, "asset_tree": [image]}
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, stop_event=threading.Event())
    directions: list[str] = []
    attempts = {"count": 0}
    expected = object()
    monkeypatch.setattr(runtime, "cur_frame", lambda update=False: f"frame-{attempts['count']}")

    def fake_find(*_args, **_kwargs):
        attempts["count"] += 1
        return expected if attempts["count"] == 3 else None

    def fake_scroll(*_args, direction=None, **_kwargs):
        directions.append(str(direction))
        if False:
            yield None
        return direction == "left"

    monkeypatch.setattr(runtime, "find_ocr_text", fake_find)
    monkeypatch.setattr(runtime, "scroll_shape_content", fake_scroll)

    result = runner._run_direct_runtime_action(
        lambda: runtime.wait_ocr_text(
            417,
            "推",
            in_shapes=["窗口"],
            timeout_seconds=60,
        ),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result is expected
    assert directions == ["right", "left"]


def test_wait_ocr_any_text_accepts_either_target_in_the_same_frame(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("玄荒古域", "0417.png", [
        {
            "title": "窗口",
            "x": 0.02,
            "y": 0.08,
            "w": 0.97,
            "h": 0.77,
            "loadDirection": "right",
        },
    ])
    ctx = {"images": {417: image}, "asset_tree": [image]}
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, stop_event=threading.Event())
    checked: list[str] = []
    expected = object()
    monkeypatch.setattr(runtime, "cur_frame", lambda update=False: "same-frame")

    def fake_find(_view, target, **_kwargs):
        checked.append(str(target))
        return expected if target == "荐" else None

    monkeypatch.setattr(runtime, "find_ocr_text", fake_find)

    result = runner._run_direct_runtime_action(
        lambda: runtime.wait_ocr_any_text(
            417,
            ("推", "荐"),
            in_shapes=["窗口"],
            timeout_seconds=60,
        ),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result is expected
    assert checked == ["推", "荐"]


def test_wait_ocr_any_text_can_repeat_bidirectional_cycles(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("玄荒古域", "0417.png", [
        {
            "title": "窗口",
            "x": 0.02,
            "y": 0.08,
            "w": 0.97,
            "h": 0.77,
            "loadDirection": "right",
        },
    ])
    ctx = {"images": {417: image}, "asset_tree": [image]}
    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, stop_event=threading.Event())
    directions: list[str] = []
    monkeypatch.setattr(runtime, "cur_frame", lambda update=False: "frame")
    monkeypatch.setattr(runtime, "find_ocr_text", lambda *_args, **_kwargs: None)

    def fake_scroll(*_args, direction=None, **_kwargs):
        directions.append(str(direction))
        if False:
            yield None
        return False

    monkeypatch.setattr(runtime, "scroll_shape_content", fake_scroll)

    result = runner._run_direct_runtime_action(
        lambda: runtime.wait_ocr_any_text(
            417,
            ("推", "荐"),
            in_shapes=["窗口"],
            timeout_seconds=60,
            direction_cycles=3,
        ),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result is None
    assert directions == ["right", "left"] * 3


def test_nudge_shape_content_for_box_only_drags_when_candidate_near_edge(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image = _image("邮件", "0121.png", [
        {"title": "邮件清单2", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6},
    ])
    ctx = {"images": {121: image}, "asset_tree": [image]}
    drags: list[tuple[float, float, float, float, int]] = []
    waits: list[float] = []
    monkeypatch.setattr(
        runner,
        "_drag_frame_point",
        lambda _ctx, _image, sx, sy, ex, ey, *, duration_ms=300: drags.append((sx, sy, ex, ey, duration_ms)),
    )
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, seconds=2.0, **_kwargs: waits.append(seconds) or iter(()))

    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, stop_event=threading.Event())
    direction = runner._run_direct_runtime_action(
        lambda: runtime.nudge_shape_content_for_box(121, "邮件清单2", {"x": 120, "y": 1180, "w": 80, "h": 40}),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert direction == "down"
    assert len(drags) == 1
    sx, sy, ex, ey, duration_ms = drags[0]
    assert sx == ex == 450
    assert sy == 872
    assert ey == 728
    assert duration_ms == 1500
    assert waits == [1.5]

    center_direction = runner._run_direct_runtime_action(
        lambda: runtime.nudge_shape_content_for_box(121, "邮件清单2", {"x": 120, "y": 760, "w": 80, "h": 40}),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert center_direction is None
    assert len(drags) == 1
    assert waits == [1.5]


def test_baiye_target_box_uses_character_tokens():
    runner = create_behavior_tree_runtime_runner()

    box = runner._baiye_target_box_from_tokens(
        _ocr_tokens("魔道仙弈", x=700.0, y=760.0, w=120.0, h=32.0),
        "魔道",
    )

    assert box == {"x": 700.0, "y": 760.0, "w": 60.0, "h": 32.0}


def test_baiye_target_box_preserves_real_variable_spacing():
    runner = create_behavior_tree_runtime_runner()

    box = runner._baiye_target_box_from_tokens(
        [
            {"text": "剑", "x": 309.3, "y": 742.0, "w": 100.0, "h": 46.0},
            {"text": "魔", "x": 771.2, "y": 742.0, "w": 64.4, "h": 46.0},
            {"text": "道", "x": 835.6, "y": 742.0, "w": 64.4, "h": 46.0},
        ],
        "魔道",
    )

    assert box == pytest.approx({"x": 771.2, "y": 742.0, "w": 128.8, "h": 46.0})


def test_baiye_target_box_ignores_rule_text():
    runner = create_behavior_tree_runtime_runner()

    rule_tokens = _ocr_tokens("魔道法则", x=340.0, y=512.0, w=160.0, h=32.0)
    target_tokens = _ocr_tokens("魔道", x=840.0, y=746.0, w=56.0, h=39.0)
    for index, token in enumerate(rule_tokens):
        token.update(parent_line_id="line-0", line_order=0, order=index)
    for index, token in enumerate(target_tokens):
        token.update(parent_line_id="line-1", line_order=1, order=index)
    box = runner._baiye_target_box_from_tokens(
        rule_tokens + target_tokens,
        "魔道",
        lines=[
            {"line_id": "line-0", "text": "魔道法则", "x": 340.0, "y": 512.0, "w": 160.0, "h": 32.0},
            {"line_id": "line-1", "text": "魔道", "x": 840.0, "y": 746.0, "w": 56.0, "h": 39.0},
        ],
    )

    assert box == {"x": 840.0, "y": 746.0, "w": 56.0, "h": 39.0}


def test_baiye_lord_click_point_uses_icon_center_above_text():
    runner = create_behavior_tree_runtime_runner()

    click_x, click_y = runner._baiye_lord_click_point_from_box(
        {"x": 700.0, "y": 760.0, "w": 60.0, "h": 32.0},
        {},
    )

    assert click_x == pytest.approx(730.0)
    assert click_y == pytest.approx(716.8)


def test_baiye_lord_probe_only_does_not_click_target(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    actions: list[tuple] = []

    class FakeRuntime:
        def current_scene(self, *_args, **_kwargs):
            actions.append(("current_scene", _args, _kwargs))
            return 265, 100.0, "frame"

        def cur_frame(self, *, update: bool = False):
            actions.append(("cur_frame", update))
            return "frame"

        def ocr_text(self, *_args, **_kwargs):
            return ""

        def ocr_tokens_in_shapes(self, *_args, **_kwargs):
            return _ocr_tokens("魔道", x=700.0, y=760.0, w=60.0, h=32.0)

        def click_frame_point(self, *_args):
            actions.append(("click_frame_point", _args))

        def click_shape_center(self, *_args):
            actions.append(("click_shape_center", _args))

        def wait_any(self, *_args, **_kwargs):
            actions.append(("wait_any", _kwargs.get("label")))
            if False:
                yield None
            return "returned"

        def view_visible(self, *_args):
            return lambda *_a, **_k: True

        def ocr_matches(self, *_args, **_kwargs):
            return lambda *_a, **_k: True

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = _drain_generator(
        runner._select_baiye_law_lord(
            {"asset_tree_path": Path("asset.json")},
            threading.Event(),
            {"baiye_lord_probe_only": True},
            target="魔道",
        )
    )

    assert result == "skipped"
    assert not any(action[0] == "click_frame_point" for action in actions)
    assert ("click_shape_center", (265, "返回")) in actions
    assert ("wait_any", "日常_拜谒：probe 等待返回 #264") in actions


def test_baiye_completed_text_wins_over_lord_map_text():
    runner = create_behavior_tree_runtime_runner()
    text = "历史记录 魔道法则之主 法则效果 虚位以待 剩余次数：0/1 已拜谒 本次拜谒可获得：天雷竹*1100"

    assert runner._baiye_text_is_completed(text) is True
    assert runner._baiye_text_is_lord_map(text) is True


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("魔道法则之主 剩余次数：1/1 拜谒", "actionable"),
        ("魔道法则之主 剩余次数：0/1 已拜谒", "completed"),
        ("魔道法则之主 剩余次数识别不清", "unknown"),
    ],
)
def test_baiye_detail_state_distinguishes_incomplete_completed_and_uncertain(text, expected):
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def current_scene(self, *_args, **_kwargs):
            return 266, 100.0, "frame"

        def ocr_text_in_shapes(self, scene_id, shape_titles, **_kwargs):
            assert scene_id == 266
            assert shape_titles == ["法则之主名称", "法则详情", "拜谒"]
            return text

    state, observed, _frame = runner._baiye_detail_state(FakeRuntime())

    assert state == expected
    assert observed == text


def test_baiye_worship_unknown_postcondition_never_returns_success():
    runner = create_behavior_tree_runtime_runner()
    actions: list[tuple] = []

    class FakeRuntime:
        def click_shape_center(self, scene_id, title):
            actions.append(("click_shape_center", scene_id, title))

        def wait_action_settle(self, seconds):
            actions.append(("wait_action_settle", seconds))
            if False:
                yield None

        def current_scene(self, *_args, **_kwargs):
            return None, 0.0, "uncertain-frame"

    with pytest.raises(RuntimeError, match="禁止按未知状态返回 success"):
        _drain_generator(
            runner._click_baiye_worship_button(
                FakeRuntime(),
                {"baiye_worship_confirm_timeout": 2, "baiye_worship_poll_seconds": 1},
                reason="回归不确定态",
            )
        )

    assert actions[0] == ("click_shape_center", 266, "拜谒")
    assert not any(action[:2] == ("click_shape_center", 266) and action[2] == "返回" for action in actions)


def test_baiye_worship_waits_through_transient_absent_frame(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    actions: list[tuple] = []
    states = iter([
        ("absent", "", "transition-frame"),
        ("completed", "魔道法则之主 剩余次数：0/1 已拜谒", "completed-frame"),
    ])

    class FakeRuntime:
        def click_shape_center(self, scene_id, title):
            actions.append(("click_shape_center", scene_id, title))

        def wait_action_settle(self, seconds):
            actions.append(("wait_action_settle", seconds))
            if False:
                yield None

    def returned_to_world(*_args, **_kwargs):
        actions.append(("return_to_world",))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_baiye_detail_state", lambda *_args, **_kwargs: next(states))
    monkeypatch.setattr(runner, "_return_baiye_to_world", returned_to_world)

    result = _drain_generator(
        runner._click_baiye_worship_button(
            FakeRuntime(),
            {"baiye_worship_confirm_timeout": 4, "baiye_worship_poll_seconds": 1},
            reason="真实刷新帧回归",
        )
    )

    assert result == "success"
    assert actions.count(("click_shape_center", 266, "拜谒")) == 1
    assert actions.count(("wait_action_settle", 1.0)) == 2
    assert ("return_to_world",) in actions


@pytest.mark.parametrize(
    ("business_result", "expected_message"),
    [
        ("success", "日常_拜谒：今日拜谒已确认完成"),
        ("skipped", "日常_拜谒：本轮未确认完成，已按业务规则安排复查"),
    ],
)
def test_daily_baiye_job_preserves_business_result_message(
    business_result,
    expected_message,
):
    from backend.core.fanxiu.data_annotation.default_jobs import (
        register_fanxiu_data_annotation_default_runtime_jobs,
    )
    from backend.core.fanxiu.data_annotation.jobs import (
        get_fanxiu_data_annotation_task_cell_definition,
    )

    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_baiye")
    assert definition is not None
    goto_calls: list[int] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def goto_view(self, scene_id):
            goto_calls.append(scene_id)
            return done("success")

    class FakeRunner:
        def _fanxiu_runtime(self, *_args, **_kwargs):
            raise AssertionError("daily baiye wrapper must not repeat scene navigation")

        def _execute_daily_baiye_task(self, *_args, **_kwargs):
            return done(business_result)

    result = _drain_generator(
        definition.handler(FakeRunner(), {}, {}, threading.Event())
    )

    assert result == {"result": "success", "message": expected_message}
    assert goto_calls == []


def test_daily_baiye_persists_completion_before_cleanup_failure(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    events: list[tuple[str, object]] = []

    def record_done(_payload, **kwargs):
        events.append(("record", kwargs["message"]))

    def failed_cleanup(*_args, **_kwargs):
        events.append(("cleanup", "start"))
        if False:
            yield None
        raise RuntimeError("transient unknown")

    monkeypatch.setattr(runner, "_record_daily_entry_done", record_done)
    monkeypatch.setattr(runner, "_return_baiye_to_world", failed_cleanup)

    payload = {"__scheduler_task_id": "legacy-daily-baiye"}
    result = _drain_generator(
        runner._finish_baiye_completed(object(), payload, reason="完成后收尾")
    )

    assert result == "success"
    assert events == [("record", "今日拜谒已确认完成"), ("cleanup", "start")]
    assert payload["__baiye_completion_persisted"] is True


def test_daily_baiye_cleanup_does_not_swallow_interruption(monkeypatch):
    runner = create_behavior_tree_runtime_runner()

    def interrupted_cleanup(*_args, **_kwargs):
        if False:
            yield None
        raise InterruptedError("stop")

    monkeypatch.setattr(runner, "_record_daily_entry_done", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_return_baiye_to_world", interrupted_cleanup)

    with pytest.raises(InterruptedError, match="stop"):
        _drain_generator(
            runner._finish_baiye_completed(
                object(),
                {"__scheduler_task_id": "legacy-daily-baiye"},
                reason="完成后收尾",
            )
        )


class _BaiyeReturnRuntimeMixin:
    def _baiye_return_ocr_text(self) -> str | None:
        scene_id = getattr(self, "_scene_id", 266)
        if scene_id == 265:
            return "可旋转并选择法则之主进行拜谒"
        if scene_id == 264:
            return "三千大道 拜谒排行 16跨法则"
        if scene_id == 34:
            return "世界 日常 仙府 邮件"
        return None

    def current_scene(self, *_args, **_kwargs):
        self._actions.append(("current_scene", _args, _kwargs))
        scene_id = getattr(self, "_scene_id", 266)
        requested = _args[0] if _args else None
        if requested is not None and scene_id not in requested:
            return None, 0.0, "frame"
        return scene_id, 100.0, "frame"

    def ocr_text_in_shapes(self, *_args, **_kwargs):
        self._actions.append(("ocr_text_in_shapes", _args, _kwargs))
        return self.ocr_text()

    def click_shape_center(self, *_args):
        self._actions.append(("click_shape_center", _args))
        scene_id = _args[0] if _args else None
        title = _args[1] if len(_args) > 1 else None
        if title == "返回":
            if scene_id == 266:
                self._scene_id = 265
            elif scene_id == 265:
                self._scene_id = 264
            elif scene_id == 264:
                self._scene_id = 34

    def wait_any(self, *_args, **_kwargs):
        self._actions.append(("wait_any", _kwargs.get("label")))
        if False:
            yield None
        return "returned"

    def wait_view(self, *_args, **_kwargs):
        self._actions.append(("wait_view", _args, _kwargs.get("label")))
        if False:
            yield None
        return getattr(self, "_scene_id", 266)

    def view_visible(self, *_args):
        return lambda *_a, **_k: True

    def ocr_matches(self, *_args, **_kwargs):
        return lambda *_a, **_k: True

    def wait_action_settle(self, seconds: float):
        self._actions.append(("wait_action_settle", seconds))
        if False:
            yield None
        return "settled"

    def goto_view(self, scene_id: int):
        self._actions.append(("goto_view", scene_id))
        self._scene_id = scene_id
        if False:
            yield None
        return "success"


def test_baiye_lord_selection_short_circuits_when_already_completed(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    actions: list[tuple] = []

    class FakeRuntime(_BaiyeReturnRuntimeMixin):
        def __init__(self):
            self._actions = actions

        def cur_frame(self, *, update: bool = False):
            actions.append(("cur_frame", update))
            return "frame"

        def ocr_text(self, *_args, **_kwargs):
            if text := self._baiye_return_ocr_text():
                return text
            return "历史记录 魔道法则之主 剩余次数：0/1 已拜谒"

        def ocr_tokens_in_shapes(self, *_args, **_kwargs):
            actions.append(("ocr_tokens_in_shapes",))
            return []

        def click_frame_point(self, *_args):
            actions.append(("click_frame_point", _args))

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = _drain_generator(
        runner._select_baiye_law_lord(
            {"asset_tree_path": Path("asset.json")},
            threading.Event(),
            {},
            target="魔道",
        )
    )

    assert result == "success"
    assert ("cur_frame", True) in actions
    assert not any(action[0] == "ocr_tokens_in_shapes" for action in actions)
    assert not any(action[0] == "click_frame_point" for action in actions)
    assert ("click_shape_center", (266, "返回")) in actions
    assert ("click_shape_center", (265, "返回")) in actions
    assert ("click_shape_center", (264, "返回")) in actions
    assert ("goto_view", 34) not in actions
    assert not any(action == ("click_shape_center", (69, "退出")) for action in actions)


def test_baiye_lord_selection_clicks_worship_button_after_target_selected(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    actions: list[tuple] = []
    ocr_texts = iter([
        "法则之主 魔道 剩余次数：1/1 拜谒 本次拜谒可获得：天雷竹*1100",
        "法则之主 魔道 剩余次数：0/1 已拜谒",
    ])

    class FakeRuntime(_BaiyeReturnRuntimeMixin):
        def __init__(self):
            self._actions = actions
            self._scene_id = 265

        def cur_frame(self, *, update: bool = False):
            actions.append(("cur_frame", update))
            return "frame"

        def ocr_text(self, *_args, **_kwargs):
            if text := self._baiye_return_ocr_text():
                return text
            return next(ocr_texts, "法则之主 魔道 剩余次数：0/1 已拜谒")

        def ocr_tokens_in_shapes(self, *_args, **_kwargs):
            return _ocr_tokens("魔道", x=700.0, y=760.0, w=60.0, h=32.0)

        def click_frame_point(self, *_args):
            actions.append(("click_frame_point", _args))
            self._scene_id = 266

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = _drain_generator(
        runner._select_baiye_law_lord(
            {"asset_tree_path": Path("asset.json")},
            threading.Event(),
            {},
            target="魔道",
        )
    )

    assert result == "success"
    assert any(action[0] == "click_frame_point" for action in actions)
    assert any(action[0] == "wait_view" and action[1] == (266,) for action in actions)
    assert ("click_shape_center", (266, "拜谒")) in actions
    assert ("click_shape_center", (266, "返回")) in actions
    assert ("click_shape_center", (265, "返回")) in actions
    assert ("click_shape_center", (264, "返回")) in actions
    assert ("goto_view", 34) not in actions
    assert not any(action == ("click_shape_center", (69, "退出")) for action in actions)


def test_baiye_lord_selection_waits_through_transient_unrecognized_scene(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    actions: list[tuple] = []

    class FakeRuntime(_BaiyeReturnRuntimeMixin):
        def __init__(self):
            self._actions = actions
            self._scene_id = 265
            self._selection_scene_reads = 0

        def cur_frame(self, *, update: bool = False):
            actions.append(("cur_frame", update))
            return "frame"

        def current_scene(self, *_args, **_kwargs):
            requested = _args[0] if _args else None
            if requested == [265] and self._selection_scene_reads == 0:
                self._selection_scene_reads += 1
                actions.append(("transient_current_scene", None))
                return None, 0.0, "transition-frame"
            return super().current_scene(*_args, **_kwargs)

        def ocr_text(self, *_args, **_kwargs):
            if text := self._baiye_return_ocr_text():
                return text
            return "法则之主 魔道 剩余次数：0/1 已拜谒"

        def ocr_tokens_in_shapes(self, *_args, **_kwargs):
            return _ocr_tokens("魔道", x=700.0, y=760.0, w=60.0, h=32.0)

        def click_frame_point(self, *_args):
            actions.append(("click_frame_point", _args))
            self._scene_id = 266

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = _drain_generator(
        runner._select_baiye_law_lord(
            {"asset_tree_path": Path("asset.json")},
            threading.Event(),
            {"baiye_lord_poll_seconds": 0.01},
            target="魔道",
        )
    )

    assert result == "success"
    assert ("transient_current_scene", None) in actions
    assert ("wait_action_settle", 0.01) in actions
    assert sum(action[0] == "click_frame_point" for action in actions) == 1
    assert ("click_shape_center", (266, "返回")) in actions
    assert ("click_shape_center", (265, "返回")) in actions
    assert ("click_shape_center", (264, "返回")) in actions


def test_baiye_lord_selection_clicks_worship_when_already_on_target_page(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    actions: list[tuple] = []
    ocr_texts = iter([
        "法则之主 魔道法则之主 剩余次数：1/1 拜谒 本次拜谒可获得：天雷竹*1100",
        "法则之主 魔道法则之主 剩余次数：0/1 已拜谒",
    ])

    class FakeRuntime(_BaiyeReturnRuntimeMixin):
        def __init__(self):
            self._actions = actions

        def cur_frame(self, *, update: bool = False):
            actions.append(("cur_frame", update))
            return "frame"

        def ocr_text(self, *_args, **_kwargs):
            if text := self._baiye_return_ocr_text():
                return text
            return next(ocr_texts, "法则之主 魔道法则之主 剩余次数：0/1 已拜谒")

        def ocr_tokens_in_shapes(self, *_args, **_kwargs):
            actions.append(("ocr_tokens_in_shapes",))
            return []

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = _drain_generator(
        runner._select_baiye_law_lord(
            {"asset_tree_path": Path("asset.json")},
            threading.Event(),
            {},
            target="魔道",
        )
    )

    assert result == "success"
    assert ("click_shape_center", (266, "拜谒")) in actions
    assert ("click_shape_center", (266, "返回")) in actions
    assert ("click_shape_center", (265, "返回")) in actions
    assert ("click_shape_center", (264, "返回")) in actions
    assert ("goto_view", 34) not in actions
    assert not any(action == ("click_shape_center", (69, "退出")) for action in actions)
    assert not any(action[0] == "ocr_tokens_in_shapes" for action in actions)


def test_baiye_lord_selection_timeout_owns_retry_next_time(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    actions: list[tuple] = []
    scheduled: list[tuple[str, str]] = []
    monotonic_values = iter([10.0, 131.0])

    class FakeRuntime:
        def click_shape_center(self, scene_id, title):
            actions.append(("click_shape_center", (scene_id, title)))

        def wait_any(self, *_args, **kwargs):
            actions.append(("wait_any", kwargs.get("label")))
            if False:
                yield None
            return "scene"

        def view_visible(self, _scene_id):
            return object()

        def ocr_matches(self, *_args, **_kwargs):
            return object()

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        behavior_tree_runtime_core,
        "_now",
        lambda: datetime(2026, 8, 13, 6, 40, 0),
    )
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: scheduled.append((task_id, next_time)),
    )

    result = _drain_generator(
        runner._select_baiye_law_lord(
            {"asset_tree_path": Path("asset.json")},
            threading.Event(),
            {"__scheduler_task_id": "legacy-daily-baiye"},
            target="魔道",
        )
    )

    assert result == "skipped"
    assert scheduled == [("legacy-daily-baiye", "2026-08-13 06:45:00")]
    assert ("click_shape_center", (265, "返回")) in actions


def test_runtime_open_daily_entry_clicks_ocr_matched_row(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.08, "y": 0.18, "w": 0.84, "h": 0.66},
    ])
    ctx = {"images": {69: image69}, "entry": type("Entry", (), {"mode": "local"})()}
    clicked: list[tuple[float, float]] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _views=None: (69, 100.0))
    monkeypatch.setattr(
        runner,
        "_ocr_fragments_in_scene_shapes",
        lambda _ctx, _frame, _image: [
            {"text": "日常 活跃度 活动报名", "x": 80, "y": 150, "w": 300, "h": 40},
            {"text": "挑战或扫荡淬剑试炼", "x": 180, "y": 540, "w": 280, "h": 44},
            {"text": "0/1", "x": 650, "y": 540, "w": 80, "h": 44},
        ],
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))
    waits: list[float] = []
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, seconds=2.0, **_kwargs: waits.append(seconds) or iter(()))

    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, stop_event=threading.Event())
    result = runner._run_direct_runtime_action(
        lambda: runtime.open_daily_entry(label="日常_剑灵", title_pattern=r"淬剑试炼"),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "open"
    assert clicked == [(320.0, 562.0)]
    assert waits == [1.0]


def test_runtime_open_daily_entry_returns_done_when_row_progress_is_full(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.08, "y": 0.18, "w": 0.84, "h": 0.66},
    ])
    ctx = {"images": {69: image69}, "entry": type("Entry", (), {"mode": "local"})()}
    clicked: list[tuple[float, float]] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _views=None: (69, 100.0))
    monkeypatch.setattr(
        runner,
        "_ocr_fragments_in_scene_shapes",
        lambda _ctx, _frame, _image: [
            {"text": "日常 活跃度 活动报名", "x": 80, "y": 150, "w": 300, "h": 40},
            {"text": "挑战或扫荡混沌灵塔", "x": 180, "y": 620, "w": 280, "h": 44},
            {"text": "1/1", "x": 650, "y": 620, "w": 80, "h": 44},
        ],
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))

    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, stop_event=threading.Event())
    result = runner._run_direct_runtime_action(
        lambda: runtime.open_daily_entry(label="日常_灵塔", title_pattern=r"混沌灵塔"),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "done"
    assert clicked == []


def test_runtime_open_daily_entry_ignores_bottom_navigation_text(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.08, "y": 0.25, "w": 0.88, "h": 0.65},
    ])
    ctx = {"images": {69: image69}, "entry": type("Entry", (), {"mode": "local"})()}

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _views=None: (69, 100.0))
    monkeypatch.setattr(
        runner,
        "_cached_ocr_fragments",
        lambda _ctx, _frame: [
            {"text": "日常 活跃度100", "x": 80, "y": 250, "w": 740, "h": 70},
            {"text": "活动报名小助手奖励找回", "x": 130, "y": 1386, "w": 590, "h": 43},
        ],
    )
    monkeypatch.setattr(runner, "_drag_frame_point", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(behavior_tree_runtime_core.BehaviorTreeRuntime, "image_signature_bytes_in_shape", lambda *_args, **_kwargs: b"same")

    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, stop_event=threading.Event())
    result = runner._run_direct_runtime_action(
        lambda: runtime.open_daily_entry(label="日常_报名", title_pattern="报名", max_scrolls=0),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "not_found"


def test_mail_ui_delete_probe_returns_from_claim_detail_without_claiming(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image("邮件", "0121.png", [])
    image122 = _image("邮件内容", "0122.png", [
        {"id": "claim", "kind": "rect", "title": "领取", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.08},
        {"id": "back", "kind": "rect", "title": "空白-返回", "x": 0.05, "y": 0.88, "w": 0.15, "h": 0.08},
    ])
    ctx = {"images": {121: image121, 122: image122}}
    row = {"title": "有附件邮件", "time_text": "2026年06月06日20:00", "x": 320, "y": 420}
    clicked_shapes: list[str] = []
    clicked_points: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def fake_wait_detail(*_args, **_kwargs):
        yield BehaviorTreeStatus.RUNNING
        return 122, 100.0

    def fake_wait_scene(*_args, **_kwargs):
        yield BehaviorTreeStatus.RUNNING
        return 121, 100.0

    monkeypatch.setattr(runner, "_wait_mail_detail_or_list_scene", fake_wait_detail)
    monkeypatch.setattr(runner, "_wait_scene_id", fake_wait_scene)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    def fake_click_frame_point(_ctx, image, x, y):
        for shape in image.get("shapes") or []:
            box = runner._box(shape, image)
            left = float(box.get("x") or 0)
            top = float(box.get("y") or 0)
            right = left + float(box.get("w") or 0)
            bottom = top + float(box.get("h") or 0)
            if left <= x <= right and top <= y <= bottom:
                clicked_points.append(shape.get("title"))
                return

    monkeypatch.setattr(runner, "_click_frame_point", fake_click_frame_point)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, _frame=None, **_kwargs: clicked_shapes.append(shape["title"]))

    result = runner._run_direct_runtime_action(
        lambda: runner._probe_and_maybe_delete_mail_row(ctx, FakeStopEvent(), image121, row),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "seen"
    assert clicked_shapes == []
    assert clicked_points == ["空白-返回"]


def test_daily_assistant_leaves_mail_detail_via_reward_and_mail_list():
    runner = create_behavior_tree_runtime_runner()
    image122 = _image("邮件内容", "0122.png", [
        {"id": "back", "kind": "rect", "title": "空白-返回", "x": 0.05, "y": 0.88, "w": 0.15, "h": 0.08},
    ])
    ctx = {
        "images": {122: image122},
        "asset_tree_path": Path("assets.json"),
    }
    runtime_calls: list[tuple] = []
    list_checks = iter([(121, 100.0, "frame", "邮件 已阅 一键删除"), (34, 100.0, "frame", "世界 大地图")])

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    class FakeRuntime:
        def wait_click(self, view_id, shape, **kwargs):
            runtime_calls.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

        def wait_view(self, *view_ids, **kwargs):
            runtime_calls.append(("wait_view", view_ids, kwargs))
            if False:
                yield None
            if view_ids == (34, 121, 227):
                return fanxiu_api.View({"id": 227})
            if view_ids == (34, 121):
                return fanxiu_api.View({"id": 121})
            return fanxiu_api.View({"id": view_ids[0]})

        def click_frame_point(self, view_id, x, y):
            runtime_calls.append(("click_frame_point", view_id, x, y))

        def click_shape_center(self, view_id, shape):
            runtime_calls.append(("click_shape_center", view_id, shape))

        def wait_click_then_view(self, view_id, shape, target_view_id, **kwargs):
            runtime_calls.append(("wait_click_then_view", view_id, shape, target_view_id, kwargs))
            if False:
                yield None
            return fanxiu_api.View({"id": target_view_id})

        def wait_action_settle(self, seconds):
            runtime_calls.append(("settle", seconds))
            if False:
                yield None
            return "success"

    def fake_scene_text(*_args, **_kwargs):
        return next(list_checks)

    runner._fanxiu_runtime_scene_text = fake_scene_text
    result = runner._run_direct_runtime_action(
        lambda: runner._leave_mail_scene_to_world(ctx, FakeStopEvent(), FakeRuntime(), 122, label="日常_助手"),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result is None
    assert ("wait_click", 122, "空白-返回", {}) in runtime_calls
    assert ("wait_click", 227, "继续", {"timeout": 8.0}) in runtime_calls
    assert (
        "wait_click_then_view",
        121,
        "空白-返回",
        34,
        {"timeout": 25.0, "label": "日常_助手：关闭邮件列表并等待世界 #34"},
    ) in runtime_calls


def test_mail_list_lock_hint_skips_delete_probe(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image("邮件", "0121.png", [])
    ctx = {"images": {121: image121}}
    row = {"title": "锁定邮件", "time_text": "2026年06月06日20:00", "x": 320, "y": 420, "list_has_lock": True}
    clicked: list[tuple[float, float]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))

    result = runner._run_direct_runtime_action(
        lambda: runner._probe_and_maybe_delete_mail_row(ctx, FakeStopEvent(), image121, row),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "seen"
    assert clicked == []


def test_mail_list_lock_icon_does_not_mark_row_locked(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image(
        "邮件",
        "0121.png",
        [
            {
                "id": "tpl",
                "kind": "rect",
                "title": "邮件模板",
                "x": 0.2685,
                "y": 0.449,
                "w": 0.6056,
                "h": 0.0917,
                "children": [
                    {"id": "title", "kind": "rect", "title": "标题", "x": 0.2796, "y": 0.4531, "w": 0.413, "h": 0.0396},
                    {"id": "time", "kind": "rect", "title": "时间", "x": 0.2759, "y": 0.499, "w": 0.4148, "h": 0.0323},
                    {"id": "lock", "kind": "rect", "title": "锁定", "x": 0.7407, "y": 0.4719, "w": 0.087, "h": 0.0479},
                ],
            },
        ],
    )
    row = {"title": "有附件", "time_text": "2026年06月06日20:00", "x": 360.0, "y": 820.0}

    monkeypatch.setattr(
        runner,
        "_shape_score",
        lambda *_args, **_kwargs: 88.0,
    )

    runner._annotate_mail_rows_list_state({"entry": object()}, image121, "frame", [row])

    assert row["list_has_lock"] is False
    assert row["has_attachment_hint"] is False
    assert "list_lock_score" not in row




def test_mail_scan_keeps_scrolling_through_old_overlap_pages(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image(
        "邮件",
        "0121.png",
        [
            {"id": "first", "kind": "rect", "title": "第1封", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.1},
            {"id": "list", "kind": "rect", "title": "邮件清单2", "x": 0.1, "y": 0.3, "w": 0.8, "h": 0.5},
        ],
    )
    ctx = {"images": {121: image121}}
    old_row = {"title": "旧邮件", "time_text": "2026年06月02日16:17"}
    new_row = {"title": "后续加载邮件", "time_text": "2026年06月05日09:30"}
    pages = [[old_row], [old_row], [old_row], [new_row], [new_row], [new_row], [new_row], [new_row], [new_row]]
    page_index = -1
    seen_titles: list[str] = []
    dragged: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def fake_screencap(_ctx):
        nonlocal page_index
        page_index += 1
        return page_index

    def fake_mail_rows(_lines, _image, shape_title):
        if shape_title == "第1封":
            return []
        return pages[min(page_index, len(pages) - 1)]

    def fake_prepare(row, *, action_enabled=True, action_policies=None):
        row["policy"] = ""
        row["mail_key"] = ""
        title = str(row["title"])
        seen_titles.append(title)

    monkeypatch.setattr(runner, "_screencap", fake_screencap)
    monkeypatch.setattr(runner, "_ocr_fragments", lambda _frame: [])
    monkeypatch.setattr(runner, "_mail_rows_in_shape", fake_mail_rows)
    monkeypatch.setattr(runner, "_prepare_mail_row_policy", fake_prepare)

    def fake_scroll_changed(*_args, **_kwargs):
        dragged.append("scroll")
        if False:
            yield BehaviorTreeStatus.RUNNING
        return len(dragged) < 4

    monkeypatch.setattr(runner, "_scroll_shape_content_changed", fake_scroll_changed)

    result = runner._run_direct_runtime_action(
        lambda: runner._scan_mail_scene(ctx, FakeStopEvent(), action_enabled=False, scan_mode="full"),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert "后续加载邮件" in seen_titles
    assert len(dragged) >= 3


def test_mail_full_action_scan_continues_when_no_pending_runtime_actions(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image121 = _image(
        "邮件",
        "0121.png",
        [
            {"id": "first", "kind": "rect", "title": "第1封", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.1},
            {"id": "list", "kind": "rect", "title": "邮件清单2", "x": 0.1, "y": 0.3, "w": 0.8, "h": 0.5},
        ],
    )
    ctx = {"images": {121: image121}}
    pages = [
        [{"title": "首页邮件", "time_text": "2026年06月07日12:00"}],
        [{"title": "后续邮件", "time_text": "2026年06月06日20:00"}],
        [{"title": "后续邮件", "time_text": "2026年06月06日20:00"}],
        [{"title": "后续邮件", "time_text": "2026年06月06日20:00"}],
        [{"title": "后续邮件", "time_text": "2026年06月06日20:00"}],
        [{"title": "后续邮件", "time_text": "2026年06月06日20:00"}],
    ]
    page_index = -1
    seen_titles: list[str] = []
    dragged: list[str] = []
    probed_titles: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def fake_screencap(_ctx):
        nonlocal page_index
        page_index += 1
        return page_index

    def fake_mail_rows(_lines, _image, shape_title):
        if shape_title == "第1封":
            return []
        return pages[min(page_index, len(pages) - 1)]

    def fake_prepare(row, *, action_enabled=True, action_policies=None):
        row["policy"] = ""
        row["mail_key"] = ""
        seen_titles.append(str(row["title"]))

    monkeypatch.setattr(runner, "_screencap", fake_screencap)
    monkeypatch.setattr(runner, "_ocr_fragments", lambda _frame: [])
    monkeypatch.setattr(runner, "_mail_rows_in_shape", fake_mail_rows)
    monkeypatch.setattr(runner, "_prepare_mail_row_policy", fake_prepare)
    monkeypatch.setattr(runner, "_pending_runtime_mail_action_count", lambda **_kwargs: 0)

    def fake_scroll_changed(*_args, **_kwargs):
        dragged.append("scroll")
        if False:
            yield BehaviorTreeStatus.RUNNING
        return len(dragged) < 2

    monkeypatch.setattr(runner, "_scroll_shape_content_changed", fake_scroll_changed)
    monkeypatch.setattr(runner, "_probe_and_maybe_delete_mail_row", lambda _ctx, _stop, _image, row: probed_titles.append(str(row["title"])) or "seen")

    result = runner._run_direct_runtime_action(
        lambda: runner._scan_mail_scene(ctx, FakeStopEvent(), action_enabled=True, scan_mode="full"),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert "后续邮件" in seen_titles
    assert probed_titles == []
    assert dragged


class _FakeSignupRuntime:
    def __init__(
        self,
        *,
        claim_available: bool = True,
        row_batches: list[list[tuple[float, float, str]]] | None = None,
        scroll_results: list[bool] | None = None,
        fail_first_reward_view: bool = False,
        claim_return_states: list[str] | None = None,
        initial_scene_id: int | None = None,
        activity_page: bool = False,
    ):
        self.claim_available = claim_available
        self.row_batches = list(row_batches or [])
        self.scroll_results = list(scroll_results or [])
        self.fail_first_reward_view = fail_first_reward_view
        self.claim_return_states = list(claim_return_states or [])
        self.scene_id = initial_scene_id
        self.activity_page = activity_page
        self.actions: list[tuple[Any, ...]] = []
        self.next_times: list[str] = []

    def set_next_time(self, value: str):
        self.next_times.append(value)

    def current_scene(self, view_ids=None, **_kwargs):
        return self.scene_id, 0.0, "frame"

    def cur_frame(self, **_kwargs):
        return "frame"

    def ocr_text(self, _frame):
        if self.activity_page:
            return "道法争锋 活动将于每周日22:00结算 当前排名:27 剩余挑战次数:5/5"
        if self.scene_id == 23:
            return "报名 活动时间 待报名"
        if self.scene_id == 69:
            return "日常 活跃度 活动报名 小助手"
        if self.scene_id == 34:
            return "世界 储物袋 角色"
        return ""

    def goto_view(self, view_id: int):
        self.actions.append(("goto", view_id))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    def shape_visible(self, view_id: int, shape: str, **_kwargs):
        return ("shape", view_id, shape)

    def ocr_contains(self, *, all_of=(), any_of=(), normalize=True, label="OCR 文本"):
        return ("ocr", {"all_of": all_of, "any_of": any_of, "normalize": normalize, "label": label})

    def ocr_matches(self, matcher, **kwargs):
        return ("ocr_matches", matcher, kwargs)

    def view_visible(self, view_id: int, **_kwargs):
        return ("view", view_id)

    def all_of(self, *conditions, **_kwargs):
        return ("all", conditions)

    def wait_any(self, conditions, **_kwargs):
        if self.claim_available and "可领取" in conditions:
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "可领取"
        if "scene" in conditions:
            scene_condition = conditions["scene"]
            if isinstance(scene_condition, tuple) and len(scene_condition) >= 2 and scene_condition[0] == "view":
                self.actions.append(("wait_view", scene_condition[1]))
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "scene"
        if "报名页" in conditions:
            state = self.claim_return_states.pop(0) if self.claim_return_states else "报名页"
            scene_by_state = {"报名页": 23, "日常页": 69, "世界": 34, "绿瓶": 20}
            self.scene_id = scene_by_state.get(state, self.scene_id)
            self.actions.append(("wait_any", tuple(conditions.keys()), state))
            if False:
                yield BehaviorTreeStatus.RUNNING
            return state
        if "已完成" in conditions:
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "已完成"
        raise AssertionError(f"no fake condition matched: {conditions}")

    def wait_click(self, view_id: int, shape: str, **_kwargs):
        self.actions.append(("wait_click", view_id, shape))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    def wait_view(self, view_id: int, **_kwargs):
        self.actions.append(("wait_view", view_id))
        if self.fail_first_reward_view and view_id == 24:
            self.fail_first_reward_view = False
            raise TimeoutError("未进入领取页")
        self.scene_id = view_id
        if False:
            yield BehaviorTreeStatus.RUNNING
        return view_id

    def wait_click_then_view(self, view_id: int, shape: str, *target_views: int, **_kwargs):
        target_view = int(target_views[0]) if target_views else 69
        self.actions.append(("wait_click_then_view", view_id, shape, target_view))
        self.scene_id = target_view
        if False:
            yield BehaviorTreeStatus.RUNNING
        return target_view

    def ocr_row_clicks_in_shape(self, view_id: int, shape: str, **kwargs):
        self.actions.append(("ocr_rows", view_id, shape))
        if kwargs.get("include") == ("已报名",):
            return []
        if self.row_batches:
            return self.row_batches.pop(0)
        return []

    def click_frame_point(self, view_id: int, x: float, y: float):
        self.actions.append(("point", view_id, x, y))
        if self.activity_page and x <= 120 and y >= 1400:
            self.activity_page = False
            self.scene_id = 34

    def scroll_shape_content(self, view_id: int, shape: str, **_kwargs):
        self.actions.append(("scroll", view_id, shape))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return self.scroll_results.pop(0) if self.scroll_results else False


def test_daily_signup_flow_reads_like_business_steps():
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeSignupRuntime(
        claim_available=True,
        initial_scene_id=34,
        claim_return_states=["报名页", "报名页"],
        row_batches=[
            [(720.0, 415.0, "丹道问鼎报名")],
            [(720.0, 637.5, "仙缘夺魁报名")],
            [],
        ],
        scroll_results=[False],
    )

    result = _drain_generator(runner.日常报名流程(runtime))

    assert result == {"result": "success", "claimed": 2, "signup_page_opened": True, "evidence": "claimed_rewards"}
    assert runtime.actions == [
        ("wait_click_then_view", 34, "日常", 69),
        ("wait_click_then_view", 75, "活动报名", 23),
        ("wait_view", 23),
        ("ocr_rows", 23, "报名列"),
        ("ocr_rows", 23, "报名列"),
        ("point", 23, 720.0, 415.0),
        ("wait_view", 24),
        ("wait_click", 24, "领取"),
        ("wait_any", ("报名页", "日常页", "世界", "绿瓶", "报名文本"), "报名页"),
        ("ocr_rows", 23, "报名列"),
        ("ocr_rows", 23, "报名列"),
        ("point", 23, 720.0, 637.5),
        ("wait_view", 24),
        ("wait_click", 24, "领取"),
        ("wait_any", ("报名页", "日常页", "世界", "绿瓶", "报名文本"), "报名页"),
        ("ocr_rows", 23, "报名列"),
        ("ocr_rows", 23, "报名列"),
        ("scroll", 23, "报名列"),
        ("ocr_rows", 23, "报名列"),
        ("ocr_rows", 23, "报名列"),
        ("scroll", 23, "报名列"),
        ("wait_click", 23, "返回"),
        ("goto", 34),
    ]


def test_daily_signup_stops_scanning_when_claim_returns_daily_page():
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeSignupRuntime(
        initial_scene_id=23,
        row_batches=[[(720.0, 415.0, "丹道问鼎报名")], [(720.0, 637.5, "不应继续扫描")]],
        claim_return_states=["日常页"],
    )

    result = _drain_generator(runner.日常报名流程(runtime))

    assert result == {"result": "success", "claimed": 1, "signup_page_opened": True, "evidence": "claimed_rewards"}
    assert ("point", 23, 720.0, 637.5) not in runtime.actions


def test_daily_signup_flow_returns_world_when_already_done():
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeSignupRuntime(claim_available=False)

    result = _drain_generator(runner.日常报名流程(runtime))

    assert result["result"] == "success"
    assert result["claimed"] == 0
    assert "未领取任何报名项" in result["message"]
    assert runtime.actions == [
        ("goto", 69),
        ("wait_click_then_view", 75, "活动报名", 23),
        ("wait_view", 23),
        ("ocr_rows", 23, "报名列"),
        ("ocr_rows", 23, "报名列"),
        ("scroll", 23, "报名列"),
        ("ocr_rows", 23, "报名列"),
        ("ocr_rows", 23, "报名列"),
        ("scroll", 23, "报名列"),
        ("wait_click", 23, "返回"),
        ("goto", 34),
    ]


def test_daily_signup_activity_page_is_successful_idempotent_tail():
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeSignupRuntime(activity_page=True)

    result = _drain_generator(runner.日常报名流程(runtime))

    assert result == {"result": "success", "claimed": 1, "activity_opened": True, "evidence": "activity_page"}
    assert runtime.actions == [("point", 23, 80.0, 1482.0), ("wait_view", 34)]


def test_daily_signup_list_scrolls_after_click_without_reward_page():
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeSignupRuntime(
        row_batches=[
            [(720.0, 415.0, "疑似报名")],
            [(720.0, 415.0, "疑似报名")],
            [],
        ],
        scroll_results=[True, False],
        fail_first_reward_view=True,
    )

    result = _drain_generator(runner._日常报名处理报名列(runtime))

    assert result == {"claimed": 1, "saw_signed_item": False, "bottom_confirmed": True}
    assert runtime.actions.count(("point", 23, 720.0, 415.0)) == 2
    assert runtime.actions.count(("wait_view", 24)) == 2
    assert runtime.actions.count(("wait_click", 24, "领取")) == 1
    assert runtime.actions.count(("scroll", 23, "报名列")) == 3


def test_daily_signup_flow_without_claims_returns_success_with_business_message():
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeSignupRuntime(
        claim_available=True,
        row_batches=[[], []],
        scroll_results=[False, False],
    )

    result = _drain_generator(runner.日常报名流程(runtime))

    assert result["result"] == "success"
    assert result["claimed"] == 0
    assert "未领取任何报名项" in result["message"]


def test_runtime_image_signature_excludes_occlusion_marker_regions(tmp_path):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image23 = _image("报名", "0023.png", [
        {"id": "column", "kind": "rect", "title": "报名列", "x": 0.2, "y": 0.2, "w": 0.7, "h": 0.6},
    ])
    occlusion_image = _image("通知遮挡", "0033.jpg", [
        {"id": "notice", "kind": "rect", "title": "通知遮挡", "x": 0.0, "y": 0.24, "w": 1.0, "h": 0.04},
    ])
    ctx = {
        "entry": object(),
        "asset_tree": [image23, {"type": "folder", "title": "遮挡", "children": [occlusion_image]}],
        "asset_tree_path": path,
        "images": {23: image23},
    }
    from PIL import Image, ImageDraw

    def frame(notice_color: str, row_color: str) -> str:
        buffer = io.BytesIO()
        image = Image.new("RGB", (900, 1600), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 384, 900, 448), fill=notice_color)
        draw.rectangle((300, 560, 600, 620), fill=row_color)
        image.save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, asset_tree_path=path)
    baseline = runtime.image_signature_in_shape(23, "报名列", frame_data_url=frame("red", "blue"))
    occlusion_changed = runtime.image_signature_in_shape(23, "报名列", frame_data_url=frame("green", "blue"))
    row_changed = runtime.image_signature_in_shape(23, "报名列", frame_data_url=frame("red", "black"))

    assert occlusion_changed == baseline
    assert row_changed != baseline


def test_runtime_image_signature_keeps_legacy_occlusion_marker_group_compatible(tmp_path):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image23 = _image("报名", "0023.png", [
        {"id": "column", "kind": "rect", "title": "报名列", "x": 0.2, "y": 0.2, "w": 0.7, "h": 0.6},
    ])
    occlusion_image = _image("通知遮挡", "0033.jpg", [
        {"id": "notice", "kind": "rect", "title": "通知遮挡", "x": 0.0, "y": 0.24, "w": 1.0, "h": 0.04},
    ])
    ctx = {
        "entry": object(),
        "asset_tree": [image23, {"type": "folder", "title": "遮挡标记", "children": [occlusion_image]}],
        "asset_tree_path": path,
        "images": {23: image23},
    }
    boxes = runner._occlusion_marker_boxes(ctx, image23)

    assert boxes == [{"x": 0.0, "y": 384.0, "w": 900.0, "h": 64.0}]


def test_runtime_image_signature_similarity_allows_small_pixel_noise(tmp_path):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image23 = _image("报名", "0023.png", [
        {"id": "column", "kind": "rect", "title": "报名列", "x": 0.2, "y": 0.2, "w": 0.7, "h": 0.6},
    ])
    ctx = {"entry": object(), "asset_tree": [image23], "asset_tree_path": path, "images": {23: image23}}
    from PIL import Image, ImageDraw

    def frame(noise: bool) -> str:
        buffer = io.BytesIO()
        image = Image.new("RGB", (900, 1600), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((300, 560, 600, 620), fill="blue")
        if noise:
            draw.rectangle((300, 560, 310, 570), fill=(0, 0, 220))
        image.save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    runtime = behavior_tree_runtime_core.BehaviorTreeRuntime(runner, ctx, asset_tree_path=path)
    baseline = runtime.image_signature_bytes_in_shape(23, "报名列", frame_data_url=frame(False))
    noisy = runtime.image_signature_bytes_in_shape(23, "报名列", frame_data_url=frame(True))

    assert runtime.image_signature_similarity(baseline, noisy) >= 95.0


def test_daily_scroll_window_unchanged_signature_ignores_occlusion_markers(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.20, "w": 0.90, "h": 0.62},
    ])
    occlusion_image = _image("通知遮挡", "0033.jpg", [
        {"id": "notice", "kind": "rect", "title": "通知遮挡", "x": 0.0, "y": 0.24, "w": 1.0, "h": 0.08},
    ])
    ctx = {
        "entry": object(),
        "asset_tree": [image69, {"type": "folder", "title": "遮挡", "children": [occlusion_image]}],
        "asset_tree_path": path,
        "images": {69: image69},
    }
    from PIL import Image, ImageDraw

    def frame(notice_color: str) -> str:
        buffer = io.BytesIO()
        image = Image.new("RGB", (900, 1600), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 384, 900, 512), fill=notice_color)
        draw.rectangle((330, 650, 550, 700), fill="blue")
        draw.rectangle((330, 820, 550, 870), fill="green")
        image.save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    frames = iter([frame("red"), frame("orange")])
    dragged: list[tuple[float, float, float, float]] = []
    waits: list[float] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(runner, "_drag_frame_point", lambda _ctx, _image, sx, sy, ex, ey, **_kwargs: dragged.append((sx, sy, ex, ey)))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, seconds=2.0, **_kwargs: waits.append(seconds) or iter(()))

    changed = runner._run_direct_runtime_action(
        lambda: runner._scroll_daily_xianyuan_list(ctx, threading.Event(), image69, direction="down"),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert changed is False
    assert len(dragged) == 1
    assert waits == [1.5]
    assert any("签名未变化" in log["message"] for log in runner.status()["logs"])


def test_daily_xianyuan_not_found_does_not_mark_success(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.2, "w": 0.9, "h": 0.65},
    ])
    ctx = {"entry": object(), "asset_tree": [], "asset_tree_path": path, "images": {69: image69}}
    dragged: list[tuple[float, float, float, float]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    frame_data_url = _png_data_url()
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: frame_data_url)
    monkeypatch.setattr(runner, "_ocr_fragments", lambda _frame: [{"x": 300, "y": 500, "w": 200, "h": 30, "text": "仙缘斗法"}])
    monkeypatch.setattr(runner, "_drag_frame_point", lambda _ctx, _image, sx, sy, ex, ey, **_kwargs: dragged.append((sx, sy, ex, ey)))

    result = runner._run_direct_runtime_action(
        lambda: runner._open_daily_xianyuan_from_daily(
            ctx,
            FakeStopEvent(),
            {"max_scrolls": 1},
        ),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "not_found"
    assert len(dragged) == 1


def test_daily_xianyuan_runtime_done_skips_daily_list_scan(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    actions: list[object] = []

    class FakeRuntime:
        def goto_view(self, scene_id):
            actions.append(("goto", scene_id))
            if False:
                yield None

        def current_scene(self, *_args, **_kwargs):
            raise AssertionError("Runtime 完成事实成立时不应再扫描 GUI")

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(
        runner,
        "_daily_xianyuan_runtime_snapshot",
        lambda _payload: {
            "complete": True,
            "task_id": 1008,
            "status": 5,
            "turn": 3,
            "target_turn": 3,
            "done": True,
            "elapsed_seconds": 0.2,
        },
    )
    monkeypatch.setattr(
        runner,
        "_record_daily_xianyuan_done",
        lambda _payload, *, message: actions.append(("done", message)) or "2026-08-14 05:00:00",
    )

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_xianyuan_task(ctx, threading.Event(), {}),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert actions == [
        ("done", "Runtime 已证明挑战仙缘任务完成"),
        ("goto", 34),
    ]


def test_daily_xianyuan_duel_waits_formation_before_optimizing(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation as daily_foundation_module

    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        scene_calls = 0

        def current_scene(self, _preferred, *, update=False):
            calls.append(("current_scene", tuple(_preferred), update))
            self.scene_calls += 1
            return (69, 100.0, "frame") if self.scene_calls == 1 else (None, 0.0, "frame326")

        def ocr_text(self, _frame):
            calls.append(("ocr_text",))
            return "玉霄天宫 本周剩余奖励次数:3/3 挑战" if _frame == "frame326" else "日常"

        def open_daily_entry(self, **kwargs):
            calls.append(("open_daily_entry", kwargs.get("title_pattern")))
            return done("open")

        def wait_click_then_view(self, scene, shape, *targets, **_options):
            calls.append(("wait_click_then_view", scene, shape, targets))
            if scene == 309 and shape == "开始挑战":
                return done(SimpleNamespace(id=310))
            target = targets[0]
            if isinstance(target, list):
                target = target[0]
            return done(SimpleNamespace(id=int(target)))

    fake_runtime = FakeRuntime()

    def prepare(_runtime, _payload):
        calls.append(("prepare_purchase",))
        return done(None)

    def optimize(_runtime, _payload):
        calls.append(("optimize_formation",))
        return done(None)

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: fake_runtime)
    monkeypatch.setattr(runner, "_prepare_daily_xianyuan_duel_purchases", prepare)
    monkeypatch.setattr(runner, "_optimize_daily_xianyuan_duel_formation", optimize)
    facts = iter(
        [
            {"remaining_challenges": 1, "remaining_refreshes": 1, "self_power": 1000, "targets": [{}, {}, {}]},
            {"remaining_challenges": 0, "remaining_refreshes": 1, "self_power": 1000, "targets": [{}, {}, {}]},
        ]
    )
    remaining_counts = iter([1, 0])
    monkeypatch.setattr(
        runner,
        "_read_daily_xianyuan_duel_remaining",
        lambda *_args, **_kwargs: done(next(remaining_counts)),
    )
    monkeypatch.setattr(runner, "_read_current_daily_xianyuan_duel_facts", lambda *_args, **_kwargs: next(facts))
    monkeypatch.setattr(
        runner,
        "_map_daily_xianyuan_duel_targets",
        lambda *_args, **_kwargs: {
            "method": "fuzzy_name_assignment",
            "targets": [
                {
                    "name": "目标",
                    "score": 3000,
                    "team_power": 100,
                    "camp": "non_friendly",
                    "relation_label": "其他区服",
                    "challenge_shape": "挑战1",
                }
            ],
        },
    )
    result = _drain_generator(
        runner._execute_daily_xianyuan_duel_task(ctx, threading.Event(), {"max_runs": 1})
    )

    assert result == "success"
    assert calls == [
        ("current_scene", (308, 69, 34), True),
        ("ocr_text",),
        ("open_daily_entry", r"斗\s*法"),
        ("prepare_purchase",),
        ("wait_click_then_view", 308, "挑战1", (309,)),
        ("optimize_formation",),
        ("wait_click_then_view", 309, "开始挑战", ([345, 310],)),
        ("wait_click_then_view", 310, "点击继续", (308,)),
    ]


def test_daily_xianyuan_duel_skips_battle_animation_before_continuing(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation as daily_foundation_module

    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, _preferred, *, update=False):
            return 308, 100.0, "frame"

        def ocr_text(self, _frame):
            return "仙缘斗法"

        def wait_click_then_view(self, scene, shape, target, **_options):
            calls.append(("wait_click_then_view", scene, shape, target))
            landing = {
                (308, "挑战1"): 309,
                (309, "开始挑战"): 345,
                (345, "跳过"): 310,
                (310, "点击继续"): 308,
            }[(scene, shape)]
            return done(SimpleNamespace(id=landing))

    fake_runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: fake_runtime)
    monkeypatch.setattr(runner, "_persist_scheduler_task_next_time", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_prepare_daily_xianyuan_duel_purchases", lambda *_args, **_kwargs: done(None))
    monkeypatch.setattr(runner, "_optimize_daily_xianyuan_duel_formation", lambda *_args, **_kwargs: done(None))
    monkeypatch.setattr(
        runner,
        "_read_daily_xianyuan_duel_remaining",
        lambda *_args, **_kwargs: done(next(remaining_counts)),
    )
    facts = iter([
        {
            "remaining_challenges": 1,
            "remaining_refreshes": 0,
            "self_power": 1000,
            "targets": [{}, {}, {}],
        },
        {
            "remaining_challenges": 0,
            "remaining_refreshes": 0,
            "self_power": 1000,
            "targets": [{}, {}, {}],
        },
    ])
    monkeypatch.setattr(
        runner,
        "_read_current_daily_xianyuan_duel_facts",
        lambda *_args, **_kwargs: next(facts),
    )
    monkeypatch.setattr(
        runner,
        "_map_daily_xianyuan_duel_targets",
        lambda *_args, **_kwargs: {
            "method": "fuzzy_name_assignment",
            "targets": [{
                "name": "目标",
                "score": 3000,
                "team_power": 100,
                "camp": "non_friendly",
                "relation_label": "其他区服",
                "challenge_shape": "挑战1",
            }],
        },
    )
    monkeypatch.setattr(daily_foundation_module, "next_xianyuan_duel_cycle_trigger_at", lambda *_args: SimpleNamespace(strftime=lambda _fmt: "2026-08-17 23:00:00"))
    remaining_counts = iter([1, 0])

    result = _drain_generator(
        runner._execute_daily_xianyuan_duel_task(
            ctx,
            threading.Event(),
            {"max_runs": 1, "skip_purchase": True},
        )
    )

    assert result == "success"
    assert calls == [
        ("wait_click_then_view", 308, "挑战1", 309),
        ("wait_click_then_view", 309, "开始挑战", [345, 310]),
        ("wait_click_then_view", 345, "跳过", 310),
        ("wait_click_then_view", 310, "点击继续", 308),
    ]


def test_daily_xianyuan_duel_purchase_stops_before_300():
    runner = create_behavior_tree_runtime_runner()
    calls: list[tuple] = []
    prices = iter([100, 200, 300, 400])

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def wait_click_then_view(self, scene, shape, target, **kwargs):
            calls.append(("wait_click_then_view", scene, shape, target, kwargs))
            return done(SimpleNamespace(id=target))

        def ocr_numbers_in_shapes(self, scene, shapes, *, padding):
            price = next(prices)
            calls.append(("ocr_numbers_in_shapes", scene, shapes, padding, price))
            return [price], f"价格：{price}"

        def wait_click(self, scene, shape):
            calls.append(("wait_click", scene, shape))
            return done(None)

        def wait_action_settle(self, seconds):
            calls.append(("wait_action_settle", seconds))
            return done(None)

        def current_scene(self, preferred, *, update=False):
            calls.append(("current_scene", tuple(preferred), update))
            return 311, 100.0, "purchase-frame"

    _drain_generator(runner._prepare_daily_xianyuan_duel_purchases(FakeRuntime(), {}))

    assert calls.count(("wait_click", 311, "购买")) == 2
    assert ("wait_click_then_view", 311, "返回", 308, {}) in calls


def test_daily_xianyuan_duel_remaining_retries_and_reads_308_count():
    runner = create_behavior_tree_runtime_runner()
    calls: list[tuple] = []
    results = iter([([], "次数"), ([2], "次数：2")])

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def ocr_numbers_in_shapes(self, scene, shapes, *, padding, crop=False):
            calls.append(("ocr_numbers_in_shapes", scene, shapes, padding, crop))
            return next(results)

        def wait_action_settle(self, seconds):
            calls.append(("wait_action_settle", seconds))
            return done(None)

    remaining = _drain_generator(runner._read_daily_xianyuan_duel_remaining(FakeRuntime(), {}))

    assert remaining == 2
    assert calls == [
        ("ocr_numbers_in_shapes", 308, ("次数",), 8, False),
        ("ocr_numbers_in_shapes", 308, ("次数",), 8, True),
    ]


def test_daily_xianyuan_duel_remaining_corrects_plus_as_duplicated_digit():
    runner = create_behavior_tree_runtime_runner()
    logs: list[tuple[str, str]] = []

    class FakeRuntime:
        def ocr_numbers_in_shapes(self, scene, shapes, *, padding, crop=False):
            assert (scene, shapes, padding, crop) == (308, ("次数",), 8, False)
            return [33], "剩余挑战次数：33+"

    runner._log = lambda kind, message, **_kwargs: logs.append((kind, message))

    remaining = _drain_generator(runner._read_daily_xianyuan_duel_remaining(FakeRuntime(), {}))

    assert remaining == 3
    assert any(kind == "warning" and "33→3" in message for kind, message in logs)


def test_daily_xianyuan_duel_purchase_open_failure_cannot_claim_idempotent_completion():
    runner = create_behavior_tree_runtime_runner()
    logs: list[tuple[str, str]] = []
    open_calls = 0

    def failed_open():
        if False:
            yield None
        raise TimeoutError("still on #308")

    class FakeRuntime:
        def wait_click_then_view(self, scene, shape, target, **kwargs):
            nonlocal open_calls
            open_calls += 1
            assert (scene, shape, target) == (308, "购买", 311)
            assert kwargs == {"timeout": 12.0, "max_clicks": 1}
            return failed_open()

        def wait_action_settle(self, seconds):
            assert seconds == 1.0
            if False:
                yield None

        def current_scene(self, preferred, *, update=False):
            assert (tuple(preferred), update) == ((311, 308), True)
            return 308, 100.0, "duel-frame"

        def ocr_text(self, frame):
            assert frame == "duel-frame"
            return "仙缘斗法 剩余挑战次数：7"

    runner._log = lambda kind, message, **_kwargs: logs.append((kind, message))

    with pytest.raises(RuntimeError, match="必须进入 #311.*300 灵石"):
        _drain_generator(runner._prepare_daily_xianyuan_duel_purchases(FakeRuntime(), {}))

    assert open_calls == 2
    assert not any("幂等继续挑战" in message for _kind, message in logs)


def test_daily_xianyuan_duel_purchase_open_retries_only_after_reconfirming_308():
    runner = create_behavior_tree_runtime_runner()
    calls: list[tuple] = []
    opens = iter(["timeout", "success"])

    def done(value=None):
        if False:
            yield None
        return value

    def failed_open():
        if False:
            yield None
        raise TimeoutError("still on #308")

    class FakeRuntime:
        def wait_click_then_view(self, scene, shape, target, **kwargs):
            calls.append(("wait_click_then_view", scene, shape, target, kwargs))
            if next(opens) == "timeout":
                return failed_open()
            return done(SimpleNamespace(id=target))

        def current_scene(self, preferred, *, update=False):
            calls.append(("current_scene", tuple(preferred), update))
            return 308, 100.0, "duel-frame"

        def ocr_text(self, frame):
            assert frame == "duel-frame"
            return "仙缘斗法 剩余挑战次数：7"

        def wait_action_settle(self, seconds):
            calls.append(("wait_action_settle", seconds))
            return done(None)

    _drain_generator(
        runner._open_daily_xianyuan_duel_purchase(
            FakeRuntime(),
            {},
            reason="测试",
        )
    )

    assert calls == [
        ("wait_click_then_view", 308, "购买", 311, {"timeout": 12.0, "max_clicks": 1}),
        ("current_scene", (311, 308), True),
        ("wait_action_settle", 1.0),
        ("wait_click_then_view", 308, "购买", 311, {"timeout": 12.0, "max_clicks": 1}),
    ]


def test_daily_xianyuan_duel_purchase_reopens_after_return_to_308_and_confirms_300():
    runner = create_behavior_tree_runtime_runner()
    calls: list[tuple] = []
    prices = iter([200, 300])

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def wait_click_then_view(self, scene, shape, target, **kwargs):
            calls.append(("wait_click_then_view", scene, shape, target, kwargs))
            return done(SimpleNamespace(id=target))

        def ocr_numbers_in_shapes(self, scene, shapes, *, padding):
            calls.append(("ocr_numbers_in_shapes", scene, shapes, padding))
            price = next(prices)
            return [price], f"价格：{price}"

        def wait_click(self, scene, shape):
            calls.append(("wait_click", scene, shape))
            return done(None)

        def wait_action_settle(self, seconds):
            calls.append(("wait_action_settle", seconds))
            return done(None)

        def current_scene(self, preferred, *, update=False):
            calls.append(("current_scene", tuple(preferred), update))
            return 308, 100.0, "duel-frame"

        def ocr_text(self, frame):
            assert frame == "duel-frame"
            return "仙缘斗法 剩余挑战次数：7"

    _drain_generator(runner._prepare_daily_xianyuan_duel_purchases(FakeRuntime(), {}))

    assert calls.count(("wait_click", 311, "购买")) == 1
    assert sum(
        call[:4] == ("wait_click_then_view", 308, "购买", 311)
        for call in calls
    ) == 2
    assert any(call[:4] == ("wait_click_then_view", 311, "返回", 308) for call in calls)


def test_daily_xianyuan_duel_purchase_does_not_replay_when_price_did_not_advance():
    runner = create_behavior_tree_runtime_runner()
    clicks: list[tuple[int, str]] = []
    prices = iter([100, 100])

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def wait_click_then_view(self, _scene, _shape, target, **_kwargs):
            return done(SimpleNamespace(id=target))

        def ocr_numbers_in_shapes(self, _scene, _shapes, *, padding):
            assert padding == 16
            price = next(prices)
            return [price], f"价格：{price}"

        def wait_click(self, scene, shape):
            clicks.append((scene, shape))
            return done(None)

        def wait_action_settle(self, _seconds):
            return done(None)

        def current_scene(self, _preferred, *, update=False):
            assert update is True
            return 311, 100.0, "purchase-frame"

    with pytest.raises(RuntimeError, match="价格未向下一档推进.*拒绝重放"):
        _drain_generator(runner._prepare_daily_xianyuan_duel_purchases(FakeRuntime(), {}))

    assert clicks == [(311, "购买")]


def test_daily_xianyuan_duel_requires_result_before_return_to_list(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation as daily_foundation_module

    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, _preferred, *, update=False):
            calls.append(("current_scene", tuple(_preferred), update))
            return 308, 100.0, "frame"

        def ocr_text(self, _frame):
            calls.append(("ocr_text",))
            return "仙缘斗法"

        def wait_click_then_view(self, scene, shape, *targets, **_options):
            calls.append(("wait_click_then_view", scene, shape, targets))
            if scene == 309 and shape == "开始挑战":
                assert targets == ([345, 310],)
                return done(SimpleNamespace(id=310))
            target = targets[0]
            if isinstance(target, list):
                target = target[0]
            return done(SimpleNamespace(id=int(target)))

    fake_runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: fake_runtime)
    monkeypatch.setattr(runner, "_prepare_daily_xianyuan_duel_purchases", lambda *_args, **_kwargs: done(None))
    monkeypatch.setattr(runner, "_optimize_daily_xianyuan_duel_formation", lambda *_args, **_kwargs: done(None))
    facts = iter(
        [
            {"remaining_challenges": 1, "remaining_refreshes": 1, "self_power": 1000, "targets": [{}, {}, {}]},
            {"remaining_challenges": 0, "remaining_refreshes": 1, "self_power": 1000, "targets": [{}, {}, {}]},
        ]
    )
    remaining_counts = iter([1, 0])
    monkeypatch.setattr(
        runner,
        "_read_daily_xianyuan_duel_remaining",
        lambda *_args, **_kwargs: done(next(remaining_counts)),
    )
    monkeypatch.setattr(runner, "_read_current_daily_xianyuan_duel_facts", lambda *_args, **_kwargs: next(facts))
    monkeypatch.setattr(
        runner,
        "_map_daily_xianyuan_duel_targets",
        lambda *_args, **_kwargs: {
            "method": "score_order_fallback",
            "targets": [
                {
                    "name": "目标",
                    "score": 3000,
                    "team_power": 100,
                    "camp": "non_friendly",
                    "relation_label": "其他区服",
                    "challenge_shape": "挑战1",
                }
            ],
        },
    )
    result = _drain_generator(
        runner._execute_daily_xianyuan_duel_task(ctx, threading.Event(), {"max_runs": 1})
    )

    assert result == "success"
    assert ("wait_click_then_view", 309, "开始挑战", ([345, 310],)) in calls
    assert ("wait_click_then_view", 310, "点击继续", (308,)) in calls


def test_daily_xianyuan_duel_never_accepts_308_as_start_battle_landing(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, _preferred, *, update=False):
            return 308, 100.0, "frame"

        def ocr_text(self, _frame):
            return "仙缘斗法"

        def wait_click_then_view(self, scene, shape, *targets, **_options):
            calls.append(("wait_click_then_view", scene, shape, targets))
            landing = {
                (308, "挑战1"): 309,
                (309, "开始挑战"): 310,
                (310, "点击继续"): 308,
            }[(scene, shape)]
            return done(SimpleNamespace(id=landing))

    fake_runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: fake_runtime)
    monkeypatch.setattr(runner, "_prepare_daily_xianyuan_duel_purchases", lambda *_args, **_kwargs: done(None))
    monkeypatch.setattr(runner, "_optimize_daily_xianyuan_duel_formation", lambda *_args, **_kwargs: done(None))
    remaining_counts = iter([1, 0])
    monkeypatch.setattr(
        runner,
        "_read_daily_xianyuan_duel_remaining",
        lambda *_args, **_kwargs: done(next(remaining_counts)),
    )
    facts = iter([
        {"remaining_challenges": 1, "remaining_refreshes": 0, "self_power": 1000, "targets": [{}, {}, {}]},
        {"remaining_challenges": 0, "remaining_refreshes": 0, "self_power": 1000, "targets": [{}, {}, {}]},
    ])
    monkeypatch.setattr(runner, "_read_current_daily_xianyuan_duel_facts", lambda *_args, **_kwargs: next(facts))
    monkeypatch.setattr(
        runner,
        "_map_daily_xianyuan_duel_targets",
        lambda *_args, **_kwargs: {
            "method": "score_order_fallback",
            "targets": [{
                "name": "目标",
                "score": 3000,
                "team_power": 100,
                "camp": "non_friendly",
                "relation_label": "其他区服",
                "challenge_shape": "挑战1",
            }],
        },
    )

    result = _drain_generator(
        runner._execute_daily_xianyuan_duel_task(
            ctx,
            threading.Event(),
            {"max_runs": 1, "skip_purchase": True},
        )
    )

    assert result == "success"
    assert ("wait_click_then_view", 309, "开始挑战", ([345, 310],)) in calls
    assert ("wait_click_then_view", 310, "点击继续", (308,)) in calls


def test_daily_xianyuan_duel_retries_when_308_count_does_not_change(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    start_attempts = 0
    logs: list[tuple[str, str]] = []

    def done(value=None):
        if False:
            yield None
        return value

    def timeout():
        if False:
            yield None
        raise TimeoutError("still on #308")

    class FakeRuntime:
        def current_scene(self, _preferred, *, update=False):
            return 308, 100.0, "frame"

        def ocr_text(self, _frame):
            return "仙缘斗法"

        def wait_click_then_view(self, scene, shape, *targets, **_options):
            nonlocal start_attempts
            if scene == 309 and shape == "开始挑战":
                start_attempts += 1
                if start_attempts == 1:
                    return timeout()
                return done(SimpleNamespace(id=310))
            landing = {
                (308, "挑战1"): 309,
                (310, "点击继续"): 308,
            }[(scene, shape)]
            return done(SimpleNamespace(id=landing))

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_prepare_daily_xianyuan_duel_purchases", lambda *_args, **_kwargs: done(None))
    monkeypatch.setattr(runner, "_optimize_daily_xianyuan_duel_formation", lambda *_args, **_kwargs: done(None))
    monkeypatch.setattr(runner, "_persist_scheduler_task_next_time", lambda *_args, **_kwargs: None)
    runner._log = lambda kind, message, **_kwargs: logs.append((kind, message))
    remaining_counts = iter([1, 1, 1, 0])
    monkeypatch.setattr(
        runner,
        "_read_daily_xianyuan_duel_remaining",
        lambda *_args, **_kwargs: done(next(remaining_counts)),
    )
    facts = iter([
        {"remaining_challenges": 1, "remaining_refreshes": 0, "self_power": 1000, "targets": [{}, {}, {}]},
        {"remaining_challenges": 1, "remaining_refreshes": 0, "self_power": 1000, "targets": [{}, {}, {}]},
        {"remaining_challenges": 0, "remaining_refreshes": 0, "self_power": 1000, "targets": [{}, {}, {}]},
    ])
    monkeypatch.setattr(runner, "_read_current_daily_xianyuan_duel_facts", lambda *_args, **_kwargs: next(facts))
    monkeypatch.setattr(
        runner,
        "_map_daily_xianyuan_duel_targets",
        lambda *_args, **_kwargs: {
            "method": "score_order_fallback",
            "targets": [{
                "name": "目标",
                "score": 3000,
                "team_power": 100,
                "camp": "non_friendly",
                "relation_label": "其他区服",
                "challenge_shape": "挑战1",
            }],
        },
    )

    result = _drain_generator(
        runner._execute_daily_xianyuan_duel_task(
            ctx,
            threading.Event(),
            {"max_runs": 1, "skip_purchase": True},
        )
    )

    assert result == "success"
    assert start_attempts == 2
    assert any(kind == "warning" and "点击未生效并重试" in message for kind, message in logs)


def test_daily_xianyuan_duel_refreshes_once_when_all_targets_are_stronger(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation as daily_foundation_module

    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, _preferred, *, update=False):
            return 308, 100.0, "frame"

        def ocr_text(self, _frame):
            return "仙缘斗法"

        def wait_click(self, scene, shape):
            calls.append(("wait_click", scene, shape))
            return done(None)

        def wait_action_settle(self, seconds):
            calls.append(("wait_action_settle", seconds))
            return done(None)

        def wait_click_then_view(self, scene, shape, *targets, **_options):
            calls.append(("wait_click_then_view", scene, shape, targets))
            target = targets[0]
            if isinstance(target, list):
                target = target[0]
            return done(SimpleNamespace(id=int(target)))

    fake_runtime = FakeRuntime()
    facts = iter(
        [
            {
                "remaining_challenges": 1,
                "remaining_refreshes": 1,
                "self_power": 500,
                "targets": [
                    {"name": "甲", "score": 3300, "team_power": 600},
                    {"name": "乙", "score": 3200, "team_power": 700},
                    {"name": "丙", "score": 3100, "team_power": 800},
                ],
                "evidence": {"order_key": [1]},
            },
            {
                "remaining_challenges": 1,
                "remaining_refreshes": 0,
                "self_power": 500,
                "targets": [
                    {"name": "丁", "score": 3400, "team_power": 900},
                    {"name": "戊", "score": 3350, "team_power": 700},
                    {"name": "己", "score": 3250, "team_power": 600},
                ],
                "evidence": {"order_key": [2]},
            },
            {
                "remaining_challenges": 0,
                "remaining_refreshes": 0,
                "self_power": 500,
                "targets": [{}, {}, {}],
                "evidence": {"order_key": [3]},
            },
        ]
    )

    def map_targets(_runtime, current_facts, _payload):
        targets = []
        for slot, item in enumerate(current_facts["targets"], start=1):
            row = dict(item)
            row.update(
                {
                    "camp": "non_friendly",
                    "relation_label": "其他区服",
                    "challenge_shape": f"挑战{slot}",
                }
            )
            targets.append(row)
        return {"method": "fuzzy_name_assignment", "targets": targets}

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: fake_runtime)
    monkeypatch.setattr(runner, "_prepare_daily_xianyuan_duel_purchases", lambda *_args, **_kwargs: done(None))
    monkeypatch.setattr(runner, "_optimize_daily_xianyuan_duel_formation", lambda *_args, **_kwargs: done(None))
    remaining_counts = iter([1, 1, 0])
    monkeypatch.setattr(
        runner,
        "_read_daily_xianyuan_duel_remaining",
        lambda *_args, **_kwargs: done(next(remaining_counts)),
    )
    monkeypatch.setattr(runner, "_read_current_daily_xianyuan_duel_facts", lambda *_args, **_kwargs: next(facts))
    monkeypatch.setattr(runner, "_map_daily_xianyuan_duel_targets", map_targets)

    result = _drain_generator(
        runner._execute_daily_xianyuan_duel_task(ctx, threading.Event(), {"skip_purchase": True, "max_runs": 1})
    )

    assert result == "success"
    assert calls.count(("wait_click", 308, "刷新")) == 1
    assert ("wait_click_then_view", 308, "挑战3", (309,)) in calls


def test_daily_xianyuan_duel_zero_remaining_writes_next_schedule(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation as daily_foundation_module

    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    next_times: list[tuple[str, str]] = []

    class FakeRuntime:
        def current_scene(self, _preferred, *, update=False):
            return 308, 100.0, "frame"

        def ocr_text(self, _frame):
            return "仙缘斗法"

    def remaining_zero(*_args, **_kwargs):
        if False:
            yield None
        return 0

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(
        runner,
        "_read_daily_xianyuan_duel_remaining",
        remaining_zero,
    )
    monkeypatch.setattr(
        runner,
        "_read_current_daily_xianyuan_duel_facts",
        lambda *_args, **_kwargs: {
            "remaining_challenges": 0,
            "remaining_refreshes": 0,
            "self_power": 500,
            "targets": [],
        },
    )
    monkeypatch.setattr(
        daily_foundation_module,
        "next_xianyuan_duel_cycle_trigger_at",
        lambda *_args, **_kwargs: datetime(2026, 7, 26, 19, 0, 0),
    )
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: next_times.append((task_id, next_time)),
    )

    result = _drain_generator(
        runner._execute_daily_xianyuan_duel_task(
            ctx,
            threading.Event(),
            {"skip_purchase": True, "__scheduler_task_id": "daily-xianyuan-duel"},
        )
    )

    assert result == "success"
    assert next_times == [("daily-xianyuan-duel", "2026-07-26 19:00:00")]


def test_daily_xianyuan_duel_forces_lowest_power_when_refresh_is_exhausted(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation as daily_foundation_module

    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    next_times: list[tuple[str, str]] = []
    calls: list[tuple] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, _preferred, *, update=False):
            return 308, 100.0, "frame"

        def ocr_text(self, _frame):
            return "仙缘斗法"

        def wait_click_then_view(self, scene, shape, *targets, **_options):
            calls.append(("wait_click_then_view", scene, shape, targets))
            target = targets[0]
            if isinstance(target, list):
                target = target[0]
            return done(SimpleNamespace(id=int(target)))

    targets = [
        {
            "name": name,
            "score": score,
            "team_power": power,
            "camp": "non_friendly",
            "relation_label": "其他区服",
            "challenge_shape": f"挑战{slot}",
        }
        for slot, (name, score, power) in enumerate(
            [("甲", 3300, 600), ("乙", 3200, 700), ("丙", 3100, 800)],
            start=1,
        )
    ]
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    remaining_counts = iter([1, 0])
    monkeypatch.setattr(
        runner,
        "_read_daily_xianyuan_duel_remaining",
        lambda *_args, **_kwargs: done(next(remaining_counts)),
    )
    facts = iter(
        [
            {
            "remaining_challenges": 1,
            "remaining_refreshes": 0,
            "self_power": 500,
            "targets": targets,
            },
            {
                "remaining_challenges": 0,
                "remaining_refreshes": 0,
                "self_power": 500,
                "targets": targets,
            },
        ]
    )
    monkeypatch.setattr(
        runner,
        "_read_current_daily_xianyuan_duel_facts",
        lambda *_args, **_kwargs: next(facts),
    )
    monkeypatch.setattr(
        runner,
        "_map_daily_xianyuan_duel_targets",
        lambda *_args, **_kwargs: {"method": "fuzzy_name_assignment", "targets": targets},
    )
    monkeypatch.setattr(runner, "_optimize_daily_xianyuan_duel_formation", lambda *_args, **_kwargs: done(None))
    monkeypatch.setattr(
        daily_foundation_module,
        "next_xianyuan_duel_cycle_trigger_at",
        lambda *_args, **_kwargs: datetime(2026, 7, 26, 19, 0, 0),
    )
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: next_times.append((task_id, next_time)),
    )

    result = _drain_generator(
        runner._execute_daily_xianyuan_duel_task(
            ctx,
            threading.Event(),
            {"skip_purchase": True, "__scheduler_task_id": "daily-xianyuan-duel"},
        )
    )

    assert result == "success"
    assert ("wait_click_then_view", 308, "挑战1", (309,)) in calls
    assert next_times == [("daily-xianyuan-duel", "2026-07-26 19:00:00")]


def test_runtime_click_translates_child_shape_by_matched_parent_box():
    runner = create_behavior_tree_runtime_runner()
    image = {"width": 900, "height": 1600}
    child_shape = {
        "title": "修罗",
        "x": 341 / 900,
        "y": 158 / 1600,
        "w": 79 / 900,
        "h": 80 / 1600,
    }
    match_result = {
        "box": {"name": "检索区域", "x": 48, "y": 71, "w": 757, "h": 266},
        "fixed_box": {"name": "检索区域", "x": 141, "y": 26, "w": 757, "h": 266},
    }

    click_x, click_y = runner._shape_match_resolved_click_point(image, child_shape, match_result)

    assert click_x == pytest.approx(473.5)
    assert click_y == pytest.approx(153.0)


def test_runtime_click_applies_relative_click_ratio_to_dynamic_match():
    runner = create_behavior_tree_runtime_runner()
    image = {"width": 900, "height": 1600}
    shape = {
        "title": "修罗",
        "x": 439 / 900,
        "y": 205 / 1600,
        "w": 32 / 900,
        "h": 24 / 1600,
    }
    match_result = {
        "box": {"x": 439, "y": 205, "w": 32, "h": 24},
        "resolved_box": {"x": 439, "y": 111, "w": 32, "h": 24},
    }

    click_x, click_y = runner._shape_match_resolved_click_point(
        image,
        shape,
        match_result,
        x_ratio=1.21875,
        y_ratio=5.0 / 3.0,
    )

    assert click_x == pytest.approx(478.0)
    assert click_y == pytest.approx(151.0)


def test_runtime_click_keeps_raw_center_for_ocr_only_navigation_shape():
    runner = create_behavior_tree_runtime_runner()
    nav_shape = {
        "title": "离开",
        "sceneJumpTarget": "34",
        "imageMatchRole": "off",
        "ocrMatchRole": "required",
    }
    match_result = {
        "box": {"x": 770, "y": 760, "w": 100, "h": 80},
        "fixed_box": {"x": 789, "y": 803, "w": 70, "h": 44},
    }

    assert runner._shape_should_keep_raw_click_for_ocr_navigation(nav_shape, match_result) is True


def test_runtime_click_resolves_dynamic_ocr_non_navigation_shape():
    runner = create_behavior_tree_runtime_runner()
    dynamic_shape = {
        "title": "注视中",
        "imageMatchRole": "off",
        "ocrMatchRole": "required",
    }
    match_result = {
        "box": {"x": 400, "y": 900, "w": 120, "h": 50},
        "fixed_box": {"x": 410, "y": 920, "w": 110, "h": 44},
    }

    assert runner._shape_should_keep_raw_click_for_ocr_navigation(dynamic_shape, match_result) is False


def test_runtime_click_shape_center_then_view_clicks_fixed_shape_before_wait(monkeypatch):
    from pyxllib.autogui import View

    from backend.core.fanxiu.data_annotation.behavior_tree_runtime import BehaviorTreeRuntime

    calls: list[tuple] = []

    class FakeSession:
        default_wait_condition_timeout = 30.0

        def view(self, view):
            raw = {"title": "0321.png", "width": 900, "height": 1600}
            return View(raw | {"id": int(view), "shapes": [{"title": "返回", "x": 0.05, "y": 0.9, "w": 0.1, "h": 0.05}]})

        def click_shape_center(self, view, shape):
            calls.append(("click_shape_center", view.id, shape))

        def wait_action_settle(self, seconds=1.0):
            calls.append(("wait_action_settle", seconds))
            if False:
                yield None
            return None

        def wait_view(self, *target_ids, **kwargs):
            calls.append(("wait_view", target_ids, kwargs))
            if False:
                yield None
            return View({"id": target_ids[0], "title": f"{target_ids[0]}.png", "shapes": []})

    session = FakeSession()
    result = _drain_generator(BehaviorTreeRuntime.click_shape_center_then_view(session, 321, "返回", 320))

    assert result.id == 320
    assert calls == [
        ("click_shape_center", 321, "返回"),
        ("wait_action_settle", 1.0),
        ("wait_view", (320,), {"timeout": 30.0, "label": "固定点击后等待目标场景 #320"}),
    ]


def test_daily_mojie_raid_opens_daily_entry_by_mojie(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs

    register_fanxiu_data_annotation_default_runtime_jobs()
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []
    trigger_calls: list[tuple[dict, str]] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        scene_calls = 0

        def current_scene(self, _preferred, *, update=False):
            calls.append(("current_scene", tuple(_preferred), update))
            self.scene_calls += 1
            return (69, 100.0, "frame") if self.scene_calls == 1 else (None, 0.0, "frame326")

        def ocr_text(self, _frame):
            calls.append(("ocr_text",))
            return "玉霄天宫 本周剩余奖励次数:3/3 挑战" if _frame == "frame326" else "日常"

        def open_daily_entry(self, **kwargs):
            calls.append((
                "open_daily_entry",
                kwargs.get("label"),
                kwargs.get("title_pattern"),
                kwargs.get("progress_can_mark_done"),
            ))
            return done("open")

        def ocr_numbers_in_shapes(self, scene_id, shape_titles, **kwargs):
            calls.append(("ocr_numbers_in_shapes", scene_id, tuple(shape_titles), kwargs))
            if scene_id == 322:
                return [2, 3], "2/3"
            return [7], "本周剩余进攻次数：7"

        def ocr_text_in_shapes(self, scene_id, shape_titles, **kwargs):
            calls.append(("ocr_text_in_shapes", scene_id, tuple(shape_titles), kwargs))
            return "进攻倒计时 -00:00:01"

        def shape(self, scene_id, title):
            calls.append(("shape", scene_id, title))
            return (scene_id, title)

        def match_shape(self, shape):
            calls.append(("match_shape", shape))
            return False

        def wait_click(self, scene_id, shape, **kwargs):
            calls.append(("wait_click", scene_id, shape, kwargs))
            return done(None)

        def wait_view(self, *scene_ids, **kwargs):
            calls.append(("wait_view", scene_ids, kwargs))
            return done(scene_ids[0])

        def wait_action_settle(self, seconds=1.0):
            calls.append(("wait_action_settle", seconds))
            return done(None)

        def click_shape_center(self, scene_id, shape):
            calls.append(("click_shape_center", scene_id, shape))
            return None

        def wait_action_settle(self, seconds=1.0):
            calls.append(("wait_action_settle", seconds))
            return done(None)

        def click_shape_center(self, scene_id, shape):
            calls.append(("click_shape_center", scene_id, shape))
            return None

        def wait_click_then_view(self, scene_id, shape, *target_scene_ids, **kwargs):
            calls.append(("wait_click_then_view", scene_id, shape, target_scene_ids, kwargs))
            return done(target_scene_ids[0] if target_scene_ids else scene_id)

        def click_shape_center_then_view(self, scene_id, shape, *target_scene_ids, **kwargs):
            calls.append(("click_shape_center_then_view", scene_id, shape, target_scene_ids, kwargs))
            return done(target_scene_ids[0] if target_scene_ids else scene_id)

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(
        runner,
        "_schedule_next_mojie_raid_trigger",
        lambda payload, *, reason: trigger_calls.append((payload, reason)) or "2026-07-27 21:32:00",
    )

    result = _drain_generator(
        runner._execute_daily_mojie_raid_task(
            ctx,
            threading.Event(),
            {"mojie_raid_target_shape": "修罗"},
        )
    )
    definition = fanxiu_api._data_annotation_task_cell_definition("daily_mojie_raid")

    assert result == "success"
    assert definition is not None
    assert definition.label == "日常_奇袭魔界"
    assert trigger_calls == [
        ({"mojie_raid_target_shape": "修罗"}, "已确认建队成功并进入 #324"),
    ]
    assert calls == [
        ("current_scene", (331, 330, 319, 320, 321, 322, 323, 324, 69, 34, 20), True),
        ("ocr_text",),
        ("open_daily_entry", "日常_奇袭魔界", r"参与.{0,4}奇|奇.{0,4}魔|魔界", False),
        ("wait_view", (319, 330), {"label": "日常_奇袭魔界：等待奇袭魔界 #319"}),
        ("ocr_numbers_in_shapes", 319, ("剩余次数",), {"padding": 16}),
        ("wait_click_then_view", 319, "参与进攻", (320,), {}),
        ("ocr_text_in_shapes", 320, ("进攻倒计时标识",), {"padding": 12}),
        (
            "wait_click",
            320,
            "修罗",
            {"timeout": 12.0, "x_ratio": 1.21875, "y_ratio": 5.0 / 3.0},
        ),
        ("wait_action_settle", 1.5),
        ("wait_view", (321, 331), {"timeout": 12.0, "label": "日常_奇袭魔界：点击 #320 修罗据点后等待 #321/#331"}),
        ("wait_click_then_view", 321, "创建队伍", (322,), {"max_clicks": 1}),
        ("ocr_numbers_in_shapes", 322, ("队伍额度",), {"padding": 12}),
        ("wait_click_then_view", 322, "下拉选项", (323,), {}),
        ("wait_click_then_view", 323, "开启", (322,), {}),
        ("wait_click_then_view", 322, "确定", (324,), {"timeout": 8.0, "max_clicks": 6}),
        ("click_shape_center_then_view", 324, "返回", (331, 34), {}),
        ("click_shape_center_then_view", 331, "返回", (320,), {}),
        ("wait_click", 320, "返回", {}),
        ("wait_click_then_view", 319, "返回", (34,), {}),
    ]


def test_daily_mojie_raid_clicks_annotated_xiuluo_target_by_default():
    runner = create_behavior_tree_runtime_runner()
    calls: list[tuple] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        default_wait_click_timeout = 12.0

        def wait_click(self, scene_id, shape, **kwargs):
            calls.append(("wait_click", scene_id, shape, kwargs))
            return done(None)

        def wait_action_settle(self, seconds=1.0):
            calls.append(("wait_action_settle", seconds))
            return done(None)

        def wait_view(self, *scene_ids, **kwargs):
            calls.append(("wait_view", scene_ids, kwargs))
            return done(scene_ids[0])

    result = _drain_generator(
        runner._click_daily_mojie_raid_top_attack_target(
            FakeRuntime(),
            {},
        )
    )

    assert result == 321
    assert calls == [
        (
            "wait_click",
            320,
            "检索区域/修罗",
            {"timeout": 12.0, "x_ratio": 1.21875, "y_ratio": 5.0 / 3.0},
        ),
        ("wait_action_settle", 1.5),
        (
            "wait_view",
            (321, 331),
            {"timeout": 12.0, "label": "日常_奇袭魔界：点击 #320 修罗据点后等待 #321/#331"},
        ),
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("进攻倒计时：13:57:48", 13 * 3600 + 57 * 60 + 48),
        ("进攻倒计时：００:００:００", 0),
        ("进攻倒计时：-12：33:50", 0),
        ("进攻倒计时：13:72:00", None),
        ("进攻倒计时", None),
        ("进攻倒计时：00:00:00 进攻倒计时：13:00:00", None),
    ],
)
def test_daily_mojie_raid_parses_attack_countdown_strictly(text, expected):
    runner = create_behavior_tree_runtime_runner()

    assert runner._daily_mojie_raid_attack_countdown_seconds(text) == expected


def test_daily_mojie_raid_defers_positive_attack_countdown_without_clicking(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    calls: list[tuple] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, views=None, *, update=False):
            calls.append(("current_scene", tuple(views or ()), update))
            return 320, 100.0, "frame"

        def ocr_text(self, frame):
            calls.append(("ocr_text", frame))
            return "奇袭魔界"

        def ocr_text_in_shapes(self, scene_id, shapes, **kwargs):
            calls.append(("ocr_text_in_shapes", scene_id, tuple(shapes), kwargs))
            return "进攻倒计时：13:57:48"

        def wait_click(self, scene_id, shape, **kwargs):
            calls.append(("wait_click", scene_id, shape, kwargs))
            return done(None)

        def wait_click_then_view(self, scene_id, shape, *targets, **kwargs):
            calls.append(("wait_click_then_view", scene_id, shape, targets, kwargs))
            return done(targets[0])

    scheduled: list[tuple] = []
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(
        runner,
        "_schedule_next_mojie_raid_countdown",
        lambda payload, *, countdown_seconds, reason: scheduled.append(
            (payload, countdown_seconds, reason)
        ) or "2026-08-26 13:01:00",
    )

    result = _drain_generator(
        runner._execute_daily_mojie_raid_task(
            {"asset_tree_path": path},
            threading.Event(),
            {"__scheduler_task_id": "legacy-daily-mojie-raid"},
        )
    )

    assert result == "skipped"
    assert scheduled == [
        (
            {"__scheduler_task_id": "legacy-daily-mojie-raid"},
            13 * 3600 + 57 * 60 + 48,
            "#320 仍处于进攻开放倒计时 进攻倒计时：13:57:48，据点当前不可交互",
        )
    ]
    assert calls[-2:] == [
        ("wait_click", 320, "返回", {}),
        ("wait_click_then_view", 319, "返回", (34,), {}),
    ]
    assert not any(call[0] == "wait_click" and call[2] == "检索区域/修罗" for call in calls)


def test_daily_mojie_raid_fails_closed_when_attack_countdown_is_unreadable(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def current_scene(self, views=None, *, update=False):
            return 320, 100.0, "frame"

        def ocr_text(self, _frame):
            return "奇袭魔界"

        def ocr_text_in_shapes(self, *_args, **_kwargs):
            return "进攻倒计时"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    with pytest.raises(RuntimeError, match="未能唯一解析"):
        _drain_generator(
            runner._execute_daily_mojie_raid_task(
                {"asset_tree_path": path},
                threading.Event(),
                {},
            )
        )


def test_daily_mojie_raid_schedules_from_authoritative_countdown(monkeypatch):
    from backend.core.fanxiu.data_annotation import behavior_tree_runtime as runtime_module

    runner = create_behavior_tree_runtime_runner()
    writes: list[tuple[str, str]] = []
    monkeypatch.setattr(runtime_module, "_now", lambda: datetime(2026, 8, 25, 23, 2, 12))
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: writes.append((task_id, next_time)),
    )

    next_time = runner._schedule_next_mojie_raid_countdown(
        {"__scheduler_task_id": "legacy-daily-mojie-raid"},
        countdown_seconds=13 * 3600 + 57 * 60 + 48,
        reason="test",
    )

    assert next_time == "2026-08-26 13:01:00"
    assert writes == [("legacy-daily-mojie-raid", "2026-08-26 13:01:00")]


def test_daily_mojie_raid_does_not_replay_when_countdown_appears_after_first_click(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    calls: list[tuple] = []
    countdown_texts = iter(("进攻倒计时：00:00:00", "进攻倒计时：13:57:48"))

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        default_wait_click_timeout = 12.0

        def current_scene(self, views=None, *, update=False, handle_interruptions=True):
            calls.append(("current_scene", tuple(views or ()), update, handle_interruptions))
            return 320, 100.0, "frame"

        def ocr_text(self, _frame):
            return "奇袭魔界"

        def ocr_text_in_shapes(self, scene_id, shapes, **kwargs):
            calls.append(("ocr_text_in_shapes", scene_id, tuple(shapes), kwargs))
            return next(countdown_texts)

        def wait_click(self, scene_id, shape, **kwargs):
            calls.append(("wait_click", scene_id, shape, kwargs))
            return done(None)

        def wait_action_settle(self, _seconds=1.0):
            return done(None)

        def wait_view(self, *_scene_ids, **_kwargs):
            def fail():
                if False:
                    yield None
                raise TimeoutError("still on #320")
            return fail()

        def wait_click_then_view(self, scene_id, shape, *targets, **kwargs):
            calls.append(("wait_click_then_view", scene_id, shape, targets, kwargs))
            return done(targets[0])

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_schedule_next_mojie_raid_countdown", lambda *_args, **_kwargs: "next")

    result = _drain_generator(
        runner._execute_daily_mojie_raid_task(
            {"asset_tree_path": path},
            threading.Event(),
            {},
        )
    )

    target_clicks = [
        call for call in calls
        if call[0] == "wait_click" and call[2] == "检索区域/修罗"
    ]
    assert result == "skipped"
    assert len(target_clicks) == 1
    assert calls[-2:] == [
        ("wait_click", 320, "返回", {}),
        ("wait_click_then_view", 319, "返回", (34,), {}),
    ]


def test_daily_mojie_raid_accepts_direct_already_joined_scene_after_target_click():
    runner = create_behavior_tree_runtime_runner()

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        default_wait_click_timeout = 12.0

        def wait_click(self, _scene_id, _shape, **_kwargs):
            return done(None)

        def wait_action_settle(self, _seconds=1.0):
            return done(None)

        def wait_view(self, *scene_ids, **_kwargs):
            assert scene_ids == (321, 331)
            return done(331)

    result = _drain_generator(
        runner._click_daily_mojie_raid_top_attack_target(FakeRuntime(), {})
    )

    assert result == 331


def test_daily_mojie_raid_marks_starting_already_joined_scene_success(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    trigger_reasons: list[str] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, _preferred, *, update=False):
            return 331, 100.0, "frame331"

        def ocr_text(self, _frame):
            return "已加入 5/5"

        def goto_view(self, scene_id):
            assert scene_id == 34
            return done(34)

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(
        runner,
        "_schedule_next_mojie_raid_trigger",
        lambda _payload, *, reason: trigger_reasons.append(reason) or "2026-08-18 13:00:00",
    )

    result = _drain_generator(
        runner._execute_daily_mojie_raid_task(ctx, threading.Event(), {})
    )

    assert result == "success"
    assert trigger_reasons == ["起点已处于 #331「已加入」状态，本轮业务已完成"]


def test_daily_mojie_raid_commits_direct_320_to_already_joined_scene(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []
    trigger_reasons: list[str] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, _preferred, *, update=False):
            return 320, 100.0, "frame320"

        def ocr_text(self, _frame):
            return "修罗"

        def ocr_text_in_shapes(self, *_args, **_kwargs):
            return "进攻倒计时：00:00:00"

        def click_shape_center_then_view(self, scene_id, shape, *targets):
            calls.append(("click_shape_center_then_view", scene_id, shape, targets))
            return done(targets[0])

        def wait_click(self, scene_id, shape):
            calls.append(("wait_click", scene_id, shape))
            return done(None)

        def wait_click_then_view(self, scene_id, shape, target):
            calls.append(("wait_click_then_view", scene_id, shape, target))
            return done(target)

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(
        runner,
        "_click_daily_mojie_raid_top_attack_target",
        lambda *_args, **_kwargs: done(331),
    )
    monkeypatch.setattr(
        runner,
        "_schedule_next_mojie_raid_trigger",
        lambda _payload, *, reason: trigger_reasons.append(reason) or "2026-08-18 13:00:00",
    )

    result = _drain_generator(
        runner._execute_daily_mojie_raid_task(ctx, threading.Event(), {})
    )

    assert result == "success"
    assert trigger_reasons == ["点击据点后已处于 #331「已加入」状态，本轮业务已完成"]
    assert calls == [
        ("click_shape_center_then_view", 331, "返回", (320,)),
        ("wait_click", 320, "返回"),
        ("wait_click_then_view", 319, "返回", 34),
    ]


def test_daily_mojie_raid_relocates_xiuluo_when_first_click_leaves_scene_unchanged():
    runner = create_behavior_tree_runtime_runner()
    calls: list[tuple] = []
    wait_results = iter((TimeoutError("still on #320"), 321))

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        default_wait_click_timeout = 12.0

        def wait_click(self, scene_id, shape, **kwargs):
            calls.append(("wait_click", scene_id, shape, kwargs))
            return done(None)

        def wait_action_settle(self, seconds=1.0):
            calls.append(("wait_action_settle", seconds))
            return done(None)

        def wait_view(self, *scene_ids, **kwargs):
            calls.append(("wait_view", scene_ids, kwargs))
            result = next(wait_results)
            if isinstance(result, Exception):
                def fail():
                    if False:
                        yield None
                    raise result
                return fail()
            return done(result)

        def current_scene(self, views=None, *, update=False, handle_interruptions=True):
            calls.append(("current_scene", tuple(views or ()), update, handle_interruptions))
            return 320, 100.0, "frame"

        def ocr_text_in_shapes(self, scene_id, shapes, **kwargs):
            calls.append(("ocr_text_in_shapes", scene_id, tuple(shapes), kwargs))
            return "进攻倒计时：00:00:00"

    result = _drain_generator(
        runner._click_daily_mojie_raid_top_attack_target(FakeRuntime(), {})
    )

    assert result == 321
    assert [item for item in calls if item[0] == "wait_click"] == [
        (
            "wait_click",
            320,
            "检索区域/修罗",
            {"timeout": 12.0, "x_ratio": 1.21875, "y_ratio": 5.0 / 3.0},
        ),
        (
            "wait_click",
            320,
            "检索区域/修罗",
            {"timeout": 12.0, "x_ratio": 1.21875, "y_ratio": 5.0 / 3.0},
        ),
    ]
    assert ("current_scene", (320, 321, 331), True, False) in calls


@pytest.mark.parametrize(
    "payload",
    [
        {"stop_after_daily_entry": True},
        {"debug": {"stop_after_daily_entry": True}},
    ],
)
def test_daily_mojie_raid_can_pause_after_daily_entry(tmp_path, monkeypatch, payload):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        scene_calls = 0

        def current_scene(self, preferred=None, *, update=False):
            calls.append(("current_scene", tuple(preferred or ()), update))
            self.scene_calls += 1
            if self.scene_calls == 1:
                return 69, 100.0, "daily-frame"
            return 47, 96.0, "popup-frame"

        def ocr_text(self, frame):
            calls.append(("ocr_text", frame))
            return "日常" if frame == "daily-frame" else "弹窗"

        def open_daily_entry(self, **kwargs):
            calls.append((
                "open_daily_entry",
                kwargs.get("label"),
                kwargs.get("title_pattern"),
                kwargs.get("progress_can_mark_done"),
            ))
            return done("open")

        def wait_action_settle(self, seconds=1.0):
            calls.append(("wait_action_settle", seconds))
            return done(None)

        def wait_view(self, *_args, **_kwargs):
            raise AssertionError("pause branch must not wait for #319")

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = _drain_generator(runner._execute_daily_mojie_raid_task(ctx, threading.Event(), payload))

    assert result == "skipped"
    assert calls == [
        ("current_scene", (331, 330, 319, 320, 321, 322, 323, 324, 69, 34, 20), True),
        ("ocr_text", "daily-frame"),
        ("open_daily_entry", "日常_奇袭魔界", r"参与.{0,4}奇|奇.{0,4}魔|魔界", False),
        ("wait_action_settle", 1.5),
        ("current_scene", (), True),
        ("ocr_text", "popup-frame"),
    ]


def test_daily_mojie_raid_rejects_daily_entry_done_as_weekly_success(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, preferred, *, update=False):
            calls.append(("current_scene", tuple(preferred), update))
            return 69, 100.0, "daily-frame"

        def ocr_text(self, frame):
            calls.append(("ocr_text", frame))
            return "日常"

        def open_daily_entry(self, **kwargs):
            calls.append((
                "open_daily_entry",
                kwargs.get("label"),
                kwargs.get("title_pattern"),
                kwargs.get("progress_can_mark_done"),
            ))
            return done("done")

        def wait_view(self, *_args, **_kwargs):
            raise AssertionError("completed daily entry must not enter raid scene")

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    with pytest.raises(RuntimeError, match="入口行完成态不能作为奇袭魔界完成判据"):
        _drain_generator(
            runner._execute_daily_mojie_raid_task(
                ctx,
                threading.Event(),
                {"__scheduler_task_id": "legacy-daily-mojie-raid"},
            )
        )

    assert calls == [
        ("current_scene", (331, 330, 319, 320, 321, 322, 323, 324, 69, 34, 20), True),
        ("ocr_text", "daily-frame"),
        ("open_daily_entry", "日常_奇袭魔界", r"参与.{0,4}奇|奇.{0,4}魔|魔界", False),
    ]


def test_daily_mojie_raid_returns_when_remaining_empty(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []
    schedule_calls: list[tuple[dict, str]] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, _preferred, *, update=False):
            return 69, 100.0, "frame"

        def ocr_text(self, _frame):
            return "日常"

        def open_daily_entry(self, **_kwargs):
            return done("open")

        def wait_view(self, *scene_ids, **_kwargs):
            return done(scene_ids[0])

        def ocr_numbers_in_shapes(self, scene_id, shape_titles, **kwargs):
            calls.append(("ocr_numbers_in_shapes", scene_id, tuple(shape_titles), kwargs))
            return [0], "本周剩余进攻次数：0"

        def wait_action_settle(self, seconds=1.0):
            calls.append(("wait_action_settle", seconds))
            return done(None)

        def shape(self, scene_id, title):
            calls.append(("shape", scene_id, title))
            return (scene_id, title)

        def match_shape(self, shape):
            calls.append(("match_shape", shape))
            return False

        def wait_click(self, scene_id, shape, **kwargs):
            calls.append(("wait_click", scene_id, shape, kwargs))
            return done(None)

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(behavior_tree_runtime_core, "_now", lambda: datetime(2026, 7, 30, 13, 0, 0))
    monkeypatch.setattr(
        runner,
        "_schedule_next_mojie_raid_week",
        lambda payload, *, reason: schedule_calls.append((payload, reason)) or "2026-07-27 13:00:00",
    )

    result = _drain_generator(
        runner._execute_daily_mojie_raid_task(ctx, threading.Event(), {})
    )

    assert result == "success"
    assert schedule_calls == [({}, "连续两帧确认剩余次数为 0，本周已完成")]
    assert calls == [
        ("ocr_numbers_in_shapes", 319, ("剩余次数",), {"padding": 16}),
        ("wait_action_settle", 2.0),
        ("ocr_numbers_in_shapes", 319, ("剩余次数",), {"padding": 16}),
        ("wait_click", 319, "返回", {}),
    ]


def test_daily_mojie_raid_zero_always_completes_the_week(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, _preferred, *, update=False):
            return 319, 100.0, "frame319"

        def ocr_text(self, _frame):
            return "奇袭魔界"

        def ocr_numbers_in_shapes(self, scene_id, shape_titles, **kwargs):
            calls.append(("ocr_numbers_in_shapes", scene_id, tuple(shape_titles), kwargs))
            return [0], "本周剩余进攻次数：0"

        def wait_action_settle(self, seconds=1.0):
            calls.append(("wait_action_settle", seconds))
            return done(None)

        def wait_click(self, scene_id, shape, **kwargs):
            calls.append(("wait_click", scene_id, shape, kwargs))
            if False:
                yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(behavior_tree_runtime_core, "_now", lambda: datetime(2026, 7, 28, 13, 0, 0))
    scheduled: list[str] = []
    monkeypatch.setattr(
        runner,
        "_schedule_next_mojie_raid_week",
        lambda *_args, **_kwargs: scheduled.append("next-week") or "2026-08-03 10:00:00",
    )

    payload = {"__scheduler_task_id": "legacy-daily-mojie-raid"}
    result = _drain_generator(runner._execute_daily_mojie_raid_task(ctx, threading.Event(), payload))

    assert result == "success"
    assert scheduled == ["next-week"]
    assert calls == [
        ("ocr_numbers_in_shapes", 319, ("剩余次数",), {"padding": 16}),
        ("wait_action_settle", 2.0),
        ("ocr_numbers_in_shapes", 319, ("剩余次数",), {"padding": 16}),
        ("wait_click", 319, "返回", {}),
    ]


def test_daily_mojie_raid_single_false_zero_keeps_current_week(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    remaining_reads = iter([(0, "本周剩余进攻次数：0"), (1, "本周剩余进攻次数：1")])
    trigger_calls: list[tuple[dict, str]] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, _preferred, *, update=False):
            return 319, 100.0, "frame319"

        def ocr_text(self, _frame):
            return "奇袭魔界"

        def ocr_numbers_in_shapes(self, scene_id, _shape_titles, **_kwargs):
            if scene_id == 319:
                remaining, text = next(remaining_reads)
                return [remaining], text
            assert scene_id == 322
            return [3, 3], "队伍数 3/3"

        def ocr_text_in_shapes(self, _scene_id, _shape_titles, **_kwargs):
            return "进攻倒计时 -00:00:01"

        def wait_action_settle(self, _seconds=1.0):
            return done(None)

        def wait_click_then_view(self, _scene_id, _shape, target, **_kwargs):
            return done(target)

        def click_shape_center_then_view(self, _scene_id, _shape, *_targets, **_kwargs):
            return done(34)

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(
        runner,
        "_click_daily_mojie_raid_top_attack_target",
        lambda *_args, **_kwargs: done(321),
    )
    monkeypatch.setattr(
        runner,
        "_schedule_next_mojie_raid_week",
        lambda *_args, **_kwargs: pytest.fail("单帧误读 0 不应推进到下周"),
    )
    monkeypatch.setattr(
        runner,
        "_schedule_next_mojie_raid_trigger",
        lambda payload, *, reason: trigger_calls.append((payload, reason)) or "2026-08-02 13:00:00",
    )
    monkeypatch.setattr(
        runner,
        "_join_daily_mojie_raid_friendly_team",
        lambda *_args, **_kwargs: done(324),
    )

    payload = {"__scheduler_task_id": "legacy-daily-mojie-raid"}
    result = _drain_generator(runner._execute_daily_mojie_raid_task(ctx, threading.Event(), payload))

    assert result == "success"
    assert trigger_calls == [(payload, "已确认加入友方队伍并进入 #324")]


def test_daily_mojie_raid_join_friendly_team_uses_template_delta_and_horizontal_load():
    runner = create_behavior_tree_runtime_runner()
    calls: list[tuple] = []

    def done(value=None):
        if False:
            yield None
        return value

    shapes = {
        "友方加入锚点": {"x": 157 / 900, "y": 1236 / 1600, "w": 92 / 900, "h": 33 / 1600},
        "友方人物点击点": {"x": 193 / 900, "y": 1156 / 1600, "w": 20 / 900, "h": 20 / 1600},
    }

    class FakeRuntime:
        def __init__(self):
            self.state = "first-page"
            self.runner = SimpleNamespace(_frame_size=lambda _raw: (900, 1600))

        def shape(self, scene_id, title):
            assert scene_id == 321
            return SimpleNamespace(raw=shapes[title])

        def view(self, scene_id):
            assert scene_id == 321
            return SimpleNamespace(raw={"width": 900, "height": 1600})

        def cur_frame(self, *, update=False):
            return self.state

        def ocr_fragments(self, frame):
            if frame == "first-page":
                return [{"text": "5/5", "x": 700, "y": 1380, "w": 70, "h": 30}]
            if frame == "second-page":
                return [
                    {"text": "加入", "x": 157, "y": 1236, "w": 92, "h": 33},
                    {"text": "3/5", "x": 175, "y": 1380, "w": 70, "h": 30},
                ]
            return []

        def ocr_fragments_in_shapes(self, scene_id, titles, *, frame_data_url, padding):
            assert (scene_id, titles, padding) == (321, ("队伍窗口",), 0)
            return self.ocr_fragments(frame_data_url)

        def scroll_shape_content(self, scene_id, title, **kwargs):
            calls.append(("scroll", scene_id, title, kwargs))
            self.state = "second-page"
            return done(True)

        def click_frame_point(self, scene_id, x, y):
            calls.append(("click-card", scene_id, round(x, 1), round(y, 1)))
            self.state = "popup"

        def wait_action_settle(self, seconds=1.0):
            calls.append(("settle", seconds))
            return done(None)

        def wait_view(self, *scene_ids, **kwargs):
            calls.append(("wait_view", scene_ids, kwargs))
            if scene_ids == (508,) and self.state == "popup":
                return done(SimpleNamespace(id=508))
            if scene_ids == (324,) and self.state == "team-page":
                return done(SimpleNamespace(id=324))
            raise RuntimeError("unexpected scene")

        def ocr_text(self, frame):
            if frame == "popup":
                return "是否加入该队伍 队长名：测试 队伍人数：3/5 取消 确认"
            if frame == "team-page":
                return "队伍成员 点击邀请加入队伍 鼓舞输出+40% 队伍频道"
            return ""

        def click_shape_center(self, scene_id, title):
            calls.append(("click-shape", scene_id, title))
            self.state = "team-page"

    result = _drain_generator(runner._join_daily_mojie_raid_friendly_team(FakeRuntime(), {}))

    assert result == 324
    assert ("click-card", 321, 203.0, 1166.0) in calls
    assert (
        "wait_view",
        (508,),
        {"timeout": 8.0, "label": "日常_奇袭魔界：等待加入队伍确认 #508"},
    ) in calls
    assert ("click-shape", 508, "确认") in calls
    assert (
        "wait_view",
        (324,),
        {"timeout": 12.0, "label": "日常_奇袭魔界：确认加入后等待队伍页 #324"},
    ) in calls
    scroll = next(call for call in calls if call[0] == "scroll")
    assert scroll[1:3] == (321, "队伍窗口")
    assert scroll[3]["direction"] == "right"
    assert scroll[3]["settle_seconds"] == 2.0
    assert scroll[3]["stable_sample_count"] == 1


def test_daily_mojie_raid_accepts_direct_world_landing_after_team_return(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []
    trigger_calls: list[tuple[dict, str]] = []

    class FakeRuntime:
        def current_scene(self, preferred, *, update=False):
            calls.append(("current_scene", tuple(preferred), update))
            return 324, 100.0, "frame324"

        def ocr_text(self, frame):
            calls.append(("ocr_text", frame))
            return "队伍 鼓舞"

        def click_shape_center_then_view(self, scene_id, shape, *targets, **kwargs):
            calls.append(("click_shape_center_then_view", scene_id, shape, targets, kwargs))
            if False:
                yield None
            return 34

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(
        runner,
        "_schedule_next_mojie_raid_trigger",
        lambda payload, *, reason: trigger_calls.append((payload, reason)) or "2026-07-27 21:32:00",
    )

    result = _drain_generator(runner._execute_daily_mojie_raid_task(ctx, threading.Event(), {}))

    assert result == "success"
    assert trigger_calls == [({}, "已确认建队成功并进入 #324")]
    assert calls == [
        ("current_scene", (331, 330, 319, 320, 321, 322, 323, 324, 69, 34, 20), True),
        ("ocr_text", "frame324"),
        ("click_shape_center_then_view", 324, "返回", (331, 34), {}),
    ]


def test_daily_mojie_raid_persists_commit_before_team_page_cleanup(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []

    class FakeRuntime:
        def current_scene(self, _preferred, *, update=False):
            return 324, 100.0, "frame324"

        def ocr_text(self, _frame):
            return "队伍成员 4/5 队伍频道"

        def click_shape_center_then_view(self, *_args, **_kwargs):
            calls.append(("cleanup",))
            raise TimeoutError("team-page cleanup failed")
            yield  # pragma: no cover

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(
        runner,
        "_schedule_next_mojie_raid_trigger",
        lambda payload, *, reason: calls.append(("commit", payload, reason)) or "2026-08-17 13:00:00",
    )

    with pytest.raises(TimeoutError, match="cleanup failed"):
        _drain_generator(
            runner._execute_daily_mojie_raid_task(
                ctx,
                threading.Event(),
                {"__scheduler_task_id": "legacy-daily-mojie-raid"},
            )
        )

    assert calls == [
        (
            "commit",
            {"__scheduler_task_id": "legacy-daily-mojie-raid"},
            "已确认建队成功并进入 #324",
        ),
        ("cleanup",),
    ]


def test_daily_mojie_raid_existing_team_marks_current_trigger_success(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []
    trigger_calls: list[tuple[dict, str]] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, preferred, *, update=False):
            calls.append(("current_scene", tuple(preferred), update))
            return 319, 100.0, "frame319"

        def ocr_text(self, frame):
            calls.append(("ocr_text", frame))
            return "我的队伍"

        def shape_matches(self, scene_id, title):
            calls.append(("shape_matches", scene_id, title))
            return {"matched": True, "similarity": 100}

        def ocr_numbers_in_shapes(self, scene_id, titles, *, padding):
            raise AssertionError("已有我的队伍时不能再读取次数并继续参与进攻")

        def wait_click_then_view(self, scene_id, shape_title, target_scene_id):
            calls.append(("wait_click_then_view", scene_id, shape_title, target_scene_id))
            return done(target_scene_id)

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(behavior_tree_runtime_core, "_now", lambda: datetime(2026, 7, 30, 13, 0, 0))
    monkeypatch.setattr(
        runner,
        "_schedule_next_mojie_raid_trigger",
        lambda payload, *, reason: trigger_calls.append((payload, reason)) or "2026-07-30 13:00:00",
    )

    payload = {"__scheduler_task_id": "legacy-daily-mojie-raid"}
    result = _drain_generator(runner._execute_daily_mojie_raid_task(ctx, threading.Event(), payload))

    assert result == "success"
    assert trigger_calls == [(payload, '#319 已显示「我的队伍」，本轮业务已完成')]
    assert calls == [
        ("current_scene", (331, 330, 319, 320, 321, 322, 323, 324, 69, 34, 20), True),
        ("ocr_text", "frame319"),
        ("shape_matches", 319, "队伍"),
        ("wait_click_then_view", 319, "返回", 34),
    ]


def test_daily_mojie_raid_week_completion_persists_next_monday(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    trigger_calls: list[tuple[str, str]] = []
    log_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(behavior_tree_runtime_core, "_now", lambda: datetime(2026, 7, 25, 22, 0, 0))
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: trigger_calls.append((task_id, next_time)),
    )
    monkeypatch.setattr(runner, "_log", lambda kind, message: log_calls.append((kind, message)))

    next_time = runner._schedule_next_mojie_raid_week(
        {"__scheduler_task_id": "legacy-daily-mojie-raid"},
        reason="剩余次数为 0，本周已完成",
    )

    assert next_time == "2026-07-27 10:00:00"
    assert trigger_calls == [("legacy-daily-mojie-raid", next_time)]
    assert log_calls == [
        ("success", "日常_奇袭魔界：剩余次数为 0，本周已完成，下次 2026-07-27 10:00:00"),
    ]


def test_daily_mojie_raid_single_success_keeps_next_daily_trigger(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    trigger_calls: list[tuple[str, str]] = []
    log_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(runner, "_next_mojie_raid_followup_time_text", lambda *_args, **_kwargs: "2026-07-28 21:32:00")
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: trigger_calls.append((task_id, next_time)),
    )
    monkeypatch.setattr(runner, "_log", lambda kind, message: log_calls.append((kind, message)))

    next_time = runner._schedule_next_mojie_raid_trigger(
        {"__scheduler_task_id": "legacy-daily-mojie-raid"},
        reason="本次建队成功并退出奇袭魔界",
    )

    assert next_time == "2026-07-28 21:32:00"
    assert trigger_calls == [("legacy-daily-mojie-raid", next_time)]
    assert log_calls == [
        ("success", "日常_奇袭魔界：本次建队成功并退出奇袭魔界，本周仍需继续，下次 2026-07-28 21:32:00"),
    ]


def test_daily_mojie_raid_confirms_optional_reward_popup_before_main(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        wait_view_calls = 0

        def current_scene(self, preferred, *, update=False):
            calls.append(("current_scene", tuple(preferred), update))
            return 69, 100.0, "frame"

        def ocr_text(self, _frame):
            return "日常"

        def open_daily_entry(self, **_kwargs):
            return done("open")

        def wait_view(self, *scene_ids, **kwargs):
            calls.append(("wait_view", scene_ids, kwargs))
            self.wait_view_calls += 1
            return done(330 if self.wait_view_calls == 1 else 319)

        def wait_click(self, scene_id, shape, **kwargs):
            calls.append(("wait_click", scene_id, shape, kwargs))
            return done(None)

        def wait_click_then_view(self, scene_id, shape, candidates, **kwargs):
            calls.append(("wait_click_then_view", scene_id, shape, tuple(candidates), kwargs))
            return done(319)

        def wait_action_settle(self, seconds=1.0):
            calls.append(("wait_action_settle", seconds))
            return done(None)

        def shape(self, scene_id, title):
            calls.append(("shape", scene_id, title))
            return (scene_id, title)

        def match_shape(self, shape):
            calls.append(("match_shape", shape))
            return False

        def ocr_numbers_in_shapes(self, scene_id, shape_titles, **kwargs):
            calls.append(("ocr_numbers_in_shapes", scene_id, tuple(shape_titles), kwargs))
            return [0], "本周剩余进攻次数：0"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(behavior_tree_runtime_core, "_now", lambda: datetime(2026, 7, 30, 13, 0, 0))

    result = _drain_generator(
        runner._execute_daily_mojie_raid_task(ctx, threading.Event(), {})
    )

    assert result == "success"
    assert ("wait_view", (319, 330), {"label": "日常_奇袭魔界：等待奇袭魔界 #319"}) in calls
    assert ("wait_click_then_view", 330, "确定", (319,), {"settle_seconds": 1.5, "timeout": 20.0}) in calls
    assert ("ocr_numbers_in_shapes", 319, ("剩余次数",), {"padding": 16}) in calls


def test_daily_mojie_raid_starts_from_preconfirm_cover_330_and_confirms_it(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, preferred, *, update=False):
            calls.append(("current_scene", tuple(preferred), update))
            return 330, 100.0, "frame"

        def ocr_text(self, _frame):
            return "前置奖励确认"

        def wait_click_then_view(self, scene_id, shape, candidates, **kwargs):
            calls.append(("wait_click_then_view", scene_id, shape, tuple(candidates), kwargs))
            return done(319)

        def wait_action_settle(self, seconds=1.0):
            calls.append(("wait_action_settle", seconds))
            return done(None)

        def shape(self, scene_id, title):
            calls.append(("shape", scene_id, title))
            return (scene_id, title)

        def match_shape(self, shape):
            calls.append(("match_shape", shape))
            return False

        def open_daily_entry(self, **_kwargs):
            raise AssertionError("preconfirm cover should be handled before opening the list")

        def wait_view(self, *scene_ids, **kwargs):
            calls.append(("wait_view", scene_ids, kwargs))
            return done(319)

        def wait_click(self, scene_id, shape, **kwargs):
            calls.append(("wait_click", scene_id, shape, kwargs))
            return done(None)

        def ocr_numbers_in_shapes(self, scene_id, shape_titles, **kwargs):
            calls.append(("ocr_numbers_in_shapes", scene_id, tuple(shape_titles), kwargs))
            return [0], "本周剩余进攻次数：0"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(behavior_tree_runtime_core, "_now", lambda: datetime(2026, 7, 30, 13, 0, 0))

    result = _drain_generator(
        runner._execute_daily_mojie_raid_task(ctx, threading.Event(), {})
    )

    assert result == "success"
    assert ("current_scene", (331, 330, 319, 320, 321, 322, 323, 324, 69, 34, 20), True) in calls
    assert ("wait_click_then_view", 330, "确定", (319,), {"settle_seconds": 1.5, "timeout": 20.0}) in calls
    assert ("wait_view", (319,), {"label": "日常_奇袭魔界：等待 #330 后的奇袭魔界 #319"}) not in calls


def test_daily_weekly_dungeon_clicks_challenge_when_already_on_second_challenge_scene(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import daily_foundation

    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": path, "asset_tree": [], "images": {}}
    calls: list[tuple] = []
    scheduled: list[tuple[str, str]] = []

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, _preferred, *, update=False):
            calls.append(("current_scene", tuple(_preferred), update))
            return 327, 100.0, "frame"

        def ocr_text(self, _frame):
            calls.append(("ocr_text",))
            return "挑战"

        def wait_click(self, scene_id, shape):
            calls.append(("wait_click", scene_id, shape))
            return done(None)

        def wait_view(self, scene_id, **kwargs):
            calls.append(("wait_view", scene_id, kwargs))
            return done(scene_id)

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_persist_scheduler_task_next_time", lambda task_id, next_time: scheduled.append((task_id, next_time)))
    monkeypatch.setattr(daily_foundation, "_now", lambda: datetime(2026, 8, 3, 5, 30, 0))

    result = _drain_generator(
        runner._execute_daily_weekly_dungeon_task(ctx, threading.Event(), {})
    )

    assert result == "success"
    assert calls == [
        ("current_scene", (327, 326, 325, 69, 34), True),
        ("ocr_text",),
        ("wait_click", 327, "挑战"),
        ("wait_view", 34, {"timeout": 600.0, "label": "日常_周本：等待战斗结束回到世界 #34"}),
    ]
    assert scheduled == [("daily-weekly-dungeon", "2026-08-10 05:00:00")]


def test_daily_weekly_dungeon_tiangong_wait_covers_long_transition():
    runner = behavior_tree_runtime_core.BehaviorTreeRuntimeRunner.__new__(
        behavior_tree_runtime_core.BehaviorTreeRuntimeRunner
    )

    class RuntimeStub:
        def __init__(self):
            self.options = None

        def wait_click_then_view(self, *_args, **options):
            self.options = options
            if False:
                yield None

    runtime = RuntimeStub()
    result = runner._open_daily_weekly_dungeon_tiangong_view(runtime, {})
    with pytest.raises(StopIteration) as finished:
        next(result)

    assert finished.value.value == 326
    assert runtime.options is not None
    assert runtime.options["timeout"] == 60.0


def test_daily_xianyuan_people_list_target_candidates():
    runner = create_behavior_tree_runtime_runner()
    image197 = {
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "title": "人物列表",
                "x": 0.07,
                "y": 0.19,
                "w": 0.88,
                "h": 0.66,
            }
        ],
    }
    lines = [
        {"text": "不离不弃不离不弃不离不弃", "x": 97, "y": 1174, "w": 665, "h": 50},
        {"text": "隐藏已无物品的仙缘", "x": 554, "y": 1367, "w": 322, "h": 36},
    ]

    candidates = runner._daily_xianyuan_list_target_candidates(lines, image197, {"target_pattern": "不离不弃"})

    assert len(candidates) == 3
    assert 150 < candidates[0][0] < 280
    assert 1020 < candidates[0][1] < 1100


def test_daily_signup_does_not_log_ready_when_scene_confirm_fails(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": [], "asset_tree_path": path, "images": {}}

    class FakeStopEvent:
        def is_set(self):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")

    def fail_daily_overview(_ctx, _path, target, _stop_event):
        return "error" if int(target) == 69 else "success"

    monkeypatch.setattr(runner, "_go_scene_task", fail_daily_overview)

    stop_event = FakeStopEvent()
    with pytest.raises(RuntimeError, match="前往 #69 失败"):
        runner._run_direct_runtime_action(
            lambda: runner._execute_runtime_task(ctx, "daily_signup", {}, stop_event),
            stop_event=stop_event,
            tick_seconds=0.01,
        )

    assert all("已到达日常 #69" not in log["message"] for log in runner.status()["logs"])


def test_runtime_service_task_cell_requires_fanxiu_runtime_scope(monkeypatch):
    session = _build_service_session()
    entry = UserDevice(entry_id="entry-1", user_id=1, device_id="local-device", name="MuMu", mode="local", token="token")
    session.add(entry)
    session.commit()
    ocr_token = create_service_access_token(session, label="ocr-only", scopes=["services.ocr:predict"])
    runtime_token = create_service_access_token(
        session,
        label="fanxiu-runtime",
        scopes=[SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL],
    )
    calls: list[tuple[str, str]] = []

    def fake_submit_task_cell(entry_arg, entry_id, task_type, payload, *, timeout_seconds=None, source=""):
        calls.append((entry_arg.entry_id, entry_id, task_type, payload, source))
        return {
            "ok": True,
            "status": "done",
            "entry_id": entry_arg.entry_id,
            "task_type": task_type,
            "message": "done",
        }

    monkeypatch.setattr(fanxiu_api, "_submit_data_annotation_task_cell", fake_submit_task_cell)
    client = _build_service_client(session)
    payload = {"entry_id": "entry-1", "task_type": "daily_signup", "payload": {}}

    forbidden = client.post(
        "/api/fanxiu/data-annotation/runtime/service/cells/task",
        headers={"Authorization": f"Bearer {ocr_token['plaintext_value']}"},
        json=payload,
    )
    ok = client.post(
        "/api/fanxiu/data-annotation/runtime/service/cells/task",
        headers={"Authorization": f"Bearer {runtime_token['plaintext_value']}"},
        json=payload,
    )

    assert forbidden.status_code == 403, forbidden.text
    assert ok.status_code == 200
    assert ok.json()["task_type"] == "daily_signup"
    assert calls == [("entry-1", "entry-1", "daily_signup", {}, "service")]


def test_xianfu_visit_partner_cd_parser():
    assert behavior_tree_runtime_core._parse_xianfu_visit_cd_seconds("06:33:27后可免费抽取") == 23607
    assert behavior_tree_runtime_core._parse_xianfu_visit_cd_seconds("12分05秒后可免费抽取") == 725
    assert behavior_tree_runtime_core._parse_xianfu_visit_cd_seconds("免费抽取") == 0
    assert behavior_tree_runtime_core._parse_xianfu_visit_cd_seconds("无法识别") is None


def test_daily_boss_reward_and_cd_parsers():
    assert behavior_tree_runtime_core._parse_daily_boss_reward_remaining("剩余奖励次数：3/3+") == 3
    assert behavior_tree_runtime_core._parse_daily_boss_reward_remaining("剩余奖励次数：0") == 0
    assert behavior_tree_runtime_core._parse_daily_boss_reward_remaining("前往挑战") is None
    assert behavior_tree_runtime_core._parse_daily_boss_cd_seconds("12:20后刷新") == 740
    assert behavior_tree_runtime_core._parse_daily_boss_cd_seconds("12小时后消失") is None
    assert behavior_tree_runtime_core._parse_daily_boss_cd_seconds("06:33:27后刷新") is None
    assert behavior_tree_runtime_core._parse_daily_boss_cd_seconds_from_six_digits("刷新时间 001220") == 740
    assert behavior_tree_runtime_core._parse_daily_boss_cd_seconds_from_six_digits("刷新时间 120020") is None
    assert behavior_tree_runtime_core._parse_daily_boss_hp_percent("首领 命20% 自动战斗中") == 20


def test_daily_boss_runtime_zero_skips_daily_list_before_gui(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    returned: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(
        runner,
        "_daily_boss_runtime_snapshot",
        lambda _payload: {
            "complete": True,
            "reward_remaining": 0,
            "resolver": "lua_global",
        },
    )
    monkeypatch.setattr(
        runner,
        "_record_daily_boss_done_for_today",
        lambda _payload: "2026-08-14 05:00:00",
    )

    def fake_return(_ctx, _stop_event, **_kwargs):
        returned.append("world")
        if False:
            yield BehaviorTreeStatus.RUNNING

    monkeypatch.setattr(runner, "_return_daily_boss_to_world", fake_return)
    monkeypatch.setattr(
        runner,
        "_fanxiu_runtime",
        lambda *_args, **_kwargs: pytest.fail("Runtime zero preflight must skip GUI construction"),
    )

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_boss_task_flow(
            {"asset_tree_path": path, "images": {}},
            FakeStopEvent(),
            {},
        ),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert returned == ["world"]
    assert runner.status()["phase"] == "daily_boss_done_runtime_preflight"


def test_daily_boss_list_consumes_preflight_snapshot_once(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    cached = {
        "complete": True,
        "list_loaded": True,
        "reward_remaining": 2,
    }
    payload = {"_daily_boss_preflight_snapshot": cached}
    monkeypatch.setattr(
        runner,
        "_daily_boss_runtime_snapshot",
        lambda _payload: pytest.fail("list stage must reuse the preflight snapshot"),
    )

    result = runner._daily_boss_runtime_snapshot_for_list(payload)

    assert result == cached
    assert result is not cached
    assert "_daily_boss_preflight_snapshot" not in payload


def test_daily_boss_detail_clicks_challenge_when_available(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image179 = _image("首领详情", "0179.png", [
        {"id": "watch", "kind": "rect", "title": "神识注视", "x": 0.35, "y": 0.5, "w": 0.5, "h": 0.06},
        {"id": "reward", "kind": "rect", "title": "剩余奖励次数", "x": 0.3, "y": 0.78, "w": 0.4, "h": 0.06},
        {"id": "action", "kind": "rect", "title": "挑战状态", "x": 0.25, "y": 0.82, "w": 0.5, "h": 0.07},
        {"id": "challenge", "kind": "rect", "title": "前往挑战", "x": 0.25, "y": 0.82, "w": 0.5, "h": 0.07},
    ])
    ctx = {"asset_tree_path": path, "images": {179: image179}}
    clicked: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_ocr_fragments_in_shapes", lambda *_args, **_kwargs: [{"text": "神识注视剩余奖励次数：3/3前往挑战"}])
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, image, x, y: clicked.append((image["title"], round(x, 1), round(y, 1))))

    def fake_wait(_ctx, _stop_event, _payload):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_wait_daily_boss_after_challenge", fake_wait)

    result = runner._run_direct_runtime_action(
        lambda: runner._handle_daily_boss_detail(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [("首领详情", 450.0, 1368.0)]


def test_daily_boss_detail_clicks_challenge_when_reward_remains_even_if_button_ocr_misses(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image179 = _image("首领详情", "0179.png", [
        {"id": "watch", "kind": "rect", "title": "神识注视", "x": 0.35, "y": 0.5, "w": 0.5, "h": 0.06},
        {"id": "reward", "kind": "rect", "title": "剩余奖励次数", "x": 0.3, "y": 0.78, "w": 0.4, "h": 0.06},
        {"id": "action", "kind": "rect", "title": "挑战状态", "x": 0.25, "y": 0.82, "w": 0.5, "h": 0.07},
        {"id": "challenge", "kind": "rect", "title": "前往挑战", "x": 0.25, "y": 0.82, "w": 0.5, "h": 0.07},
    ])
    ctx = {"asset_tree_path": path, "images": {179: image179}}
    clicked: list[tuple[str, float, float]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_ocr_fragments_in_shapes", lambda *_args, **_kwargs: [{"text": "神识注视剩余奖励次数：3/3"}])
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, image, x, y: clicked.append((image["title"], round(x, 1), round(y, 1))))

    def fake_wait(_ctx, _stop_event, payload):
        assert payload["_daily_boss_challenge_remaining"] == 3
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_wait_daily_boss_after_challenge", fake_wait)

    result = runner._run_direct_runtime_action(
        lambda: runner._handle_daily_boss_detail(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [("首领详情", 450.0, 1368.0)]


def test_daily_boss_after_challenge_treats_refresh_countdown_as_round_done(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(behavior_tree_runtime_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(behavior_tree_runtime_core, "_now", lambda: datetime(2026, 6, 11, 11, 0, 0))
    runner = create_behavior_tree_runtime_runner()
    ctx = {"images": {}, "asset_tree_path": tmp_path / "asset_tree.json"}
    phases: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: (180, 100.0, "fight"))

    monkeypatch.setattr(runner, "_daily_boss_status_text_from_frame", lambda _ctx, frame=None: "首领自动战斗中 00:07:17后刷新首领伤害数据统计")
    finished: list[dict[str, object]] = []

    def fake_finish(_ctx, _runtime, _stop_event, payload):
        finished.append(dict(payload))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "skipped"

    monkeypatch.setattr(runner, "_finish_daily_boss_round_after_done", fake_finish)
    original_set_status = runner._set_status_locked

    def record_status(status, message="", **extra):
        if extra.get("phase"):
            phases.append(extra["phase"])
        original_set_status(status, message, **extra)

    monkeypatch.setattr(runner, "_set_status_locked", record_status)

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_daily_boss_after_challenge(ctx, FakeStopEvent(), {"post_challenge_wait_seconds": 0.01}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "skipped"
    assert "daily_boss_wait_boss_done" not in phases
    assert finished == [{"post_challenge_wait_seconds": 0.01}]


def test_daily_boss_after_challenge_accepts_runtime_reward_delta_without_waiting_for_scene_181(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(behavior_tree_runtime_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    runner = create_behavior_tree_runtime_runner()
    ctx = {"images": {}, "asset_tree_path": tmp_path / "asset_tree.json"}

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: (186, 100.0, "result"))
    monkeypatch.setattr(runner, "_daily_boss_status_text_from_frame", lambda _ctx, frame=None: "")
    monkeypatch.setattr(
        runner,
        "_daily_boss_runtime_snapshot",
        lambda _payload=None: {"complete": True, "reward_remaining": 2},
    )
    retries: list[int] = []
    returned: list[bool] = []
    monkeypatch.setattr(
        runner,
        "_record_daily_boss_recheck_time",
        lambda _payload, *, seconds: retries.append(seconds) or "2026-06-11 11:01:00",
    )

    def fake_return(_ctx, _stop_event, **_kwargs):
        returned.append(True)
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_return_daily_boss_to_world", fake_return)

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_daily_boss_after_challenge(
            ctx,
            FakeStopEvent(),
            {
                "_daily_boss_challenge_remaining": 3,
                "post_challenge_wait_seconds": 30,
            },
        ),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "skipped"
    assert retries == [60]
    assert returned == [True]
    assert runner.status()["phase"] == "daily_boss_done_by_runtime_delta"


def test_daily_boss_runtime_delta_keeps_business_result_when_world_cleanup_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(behavior_tree_runtime_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    runner = create_behavior_tree_runtime_runner()
    ctx = {"images": {}, "asset_tree_path": tmp_path / "asset_tree.json"}

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: (186, 100.0, "result"))
    monkeypatch.setattr(runner, "_daily_boss_status_text_from_frame", lambda _ctx, frame=None: "")
    monkeypatch.setattr(
        runner,
        "_daily_boss_runtime_snapshot",
        lambda _payload=None: {"complete": True, "reward_remaining": 2},
    )
    monkeypatch.setattr(
        runner,
        "_record_daily_boss_recheck_time",
        lambda _payload, *, seconds: "2026-06-11 11:01:00",
    )

    def failing_return(_ctx, _stop_event):
        if False:
            yield BehaviorTreeStatus.RUNNING
        raise RuntimeError("战斗转场尚未稳定")

    monkeypatch.setattr(runner, "_return_daily_boss_to_world", failing_return)

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_daily_boss_after_challenge(
            ctx,
            FakeStopEvent(),
            {"_daily_boss_challenge_remaining": 3, "post_challenge_wait_seconds": 30},
        ),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "skipped"
    assert runner.status()["phase"] == "daily_boss_done_by_runtime_delta"
    assert any("业务已完成" in item["message"] for item in runner.status()["logs"])


def test_daily_boss_stuck_at_twenty_percent_leaves_and_rechecks_rewards(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(behavior_tree_runtime_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(behavior_tree_runtime_core, "_now", lambda: datetime(2026, 6, 11, 11, 0, 0))
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image180 = _image("挑战中", "0180.png", [
        {"id": "hp", "kind": "rect", "title": "BOSS生命值", "x": 0.18, "y": 0.11, "w": 0.62, "h": 0.26},
        {"id": "leave", "kind": "rect", "title": "离开", "x": 0.83, "y": 0.45, "w": 0.12, "h": 0.09},
    ])
    image178 = _image("首领列表", "0178.png", [
        {"id": "reward", "kind": "rect", "title": "剩余奖励次数", "x": 0.3, "y": 0.8, "w": 0.4, "h": 0.06},
    ])
    ctx = {"asset_tree_path": path, "images": {178: image178, 180: image180}}
    clicked: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: (180, 100.0, "fight"))
    monkeypatch.setattr(runner, "_daily_boss_status_text_from_frame", lambda _ctx, frame=None: "首领 命20% 自动战斗中")
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, *_args, **_kwargs: clicked.append(shape["title"]))
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "list-frame")
    monkeypatch.setattr(runner, "_ocr_fragments_in_shapes", lambda *_args, **_kwargs: [{"text": "剩余奖励次数：0"}])
    retries: list[int] = []
    monkeypatch.setattr(runner, "_record_daily_boss_recheck_time", lambda _payload, *, seconds: retries.append(seconds) or "2026-06-11 11:30:00")
    def fake_return_world(_ctx, _stop_event):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_return_daily_boss_to_world", fake_return_world)

    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_daily_boss_after_challenge(ctx, FakeStopEvent(), {"post_challenge_wait_seconds": 0.01}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "skipped"
    assert clicked == []
    assert retries == [1800]


def test_daily_boss_stuck_on_boss_map_leaves_and_rechecks_rewards(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(behavior_tree_runtime_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(behavior_tree_runtime_core, "_now", lambda: datetime(2026, 6, 11, 11, 0, 0))
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image180 = _image("挑战中", "0180.png", [
        {"id": "hp", "kind": "rect", "title": "BOSS生命值", "x": 0.18, "y": 0.11, "w": 0.62, "h": 0.26},
        {"id": "leave", "kind": "rect", "title": "离开", "x": 0.83, "y": 0.45, "w": 0.12, "h": 0.09},
    ])
    image178 = _image("首领列表", "0178.png", [
        {"id": "reward", "kind": "rect", "title": "剩余奖励次数", "x": 0.3, "y": 0.8, "w": 0.4, "h": 0.06},
    ])
    ctx = {"asset_tree_path": path, "images": {178: image178, 180: image180}}
    clicked: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: (180, 100.0, "map"))
    monkeypatch.setattr(runner, "_daily_boss_status_text_from_frame", lambda _ctx, frame=None: "首领·泷尊剑主 100% 数据统计 离开")
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, *_args, **_kwargs: clicked.append(shape["title"]))
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "list-frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, view_ids=None: (178, 100.0))
    monkeypatch.setattr(runner, "_ocr_fragments_in_shapes", lambda *_args, **_kwargs: [{"text": "剩余奖励次数：0"}])
    retries: list[int] = []
    monkeypatch.setattr(runner, "_record_daily_boss_recheck_time", lambda _payload, *, seconds: retries.append(seconds) or "2026-06-11 11:30:00")
    def fake_return_world(_ctx, _stop_event):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_return_daily_boss_to_world", fake_return_world)

    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_daily_boss_after_challenge(ctx, FakeStopEvent(), {"post_challenge_wait_seconds": 0.01}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "skipped"
    assert clicked == []
    assert retries == [1800]


def test_daily_boss_reopens_list_after_leave_lands_on_world(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    calls: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def wait_view_id(self, *_args, **_kwargs):
            if False:
                yield BehaviorTreeStatus.RUNNING
            raise RuntimeError("not list")

        def goto_view(self, scene_id):
            calls.append(f"goto:{scene_id}")
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "success"

    def fake_open_list(_ctx, _stop_event):
        calls.append("open-list")
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: (34, 100.0, "world"))
    monkeypatch.setattr(runner, "_open_daily_boss_list_from_daily", fake_open_list)

    result = runner._run_direct_runtime_action(
        lambda: runner._open_daily_boss_list_after_leaving_fight(ctx, FakeRuntime(), FakeStopEvent()),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result is True
    assert calls == ["goto:69", "open-list"]


def test_daily_boss_leave_recheck_failure_on_world_becomes_retry(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    calls: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def wait_view_id(self, *_args, **_kwargs):
            if False:
                yield BehaviorTreeStatus.RUNNING
            raise RuntimeError("not list")

        def goto_view(self, scene_id):
            calls.append(f"goto:{scene_id}")
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "success"

    def fake_open_list(_ctx, _stop_event):
        calls.append("open-list")
        if False:
            yield BehaviorTreeStatus.RUNNING
        raise RuntimeError("日常_首领：等待首领列表 #178 超时，最后 #34 100%")

    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: (34, 100.0, "world"))
    monkeypatch.setattr(runner, "_open_daily_boss_list_from_daily", fake_open_list)

    result = runner._run_direct_runtime_action(
        lambda: runner._open_daily_boss_list_after_leaving_fight(ctx, FakeRuntime(), FakeStopEvent()),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result is False
    assert calls == ["goto:69", "open-list"]


def test_daily_boss_leave_closes_reward_result_before_rechecking_list(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    ctx = {
        "entry": object(),
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {177: _image("领悟绝技", "0177.png", [])},
    }
    calls: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        wait_count = 0

        def wait_view_id(self, *_args, **_kwargs):
            self.wait_count += 1
            if False:
                yield BehaviorTreeStatus.RUNNING
            if self.wait_count == 1:
                raise RuntimeError("reward page")
            return "success"

        def current_scene(self, *_args, **_kwargs):
            return 177, 100.0, "reward-frame"

        def ocr_text(self, _frame=None, **_kwargs):
            return "恭喜获得 点击屏幕继续 1秒后自动关闭"

        def click_frame_point(self, _image, x, y):
            calls.append(f"click:{round(x)}:{round(y)}")

        def wait_action_settle(self, seconds=1.0):
            calls.append(f"settle:{seconds}")
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "success"

    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = runner._run_direct_runtime_action(
        lambda: runner._open_daily_boss_list_after_leaving_fight(ctx, FakeRuntime(), FakeStopEvent()),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result is True
    assert calls == ["click:450:1376", "settle:2.0"]


def test_daily_boss_cleanup_closes_item_detail_with_frame_250_return(tmp_path):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image250 = _image("道具详情", "0250.png", [
        {"id": "back", "kind": "rect", "title": "返回", "x": 0.05, "y": 0.92, "w": 0.08, "h": 0.04},
    ])
    ctx = {"asset_tree_path": path, "images": {250: image250}}
    calls: list[tuple[str, float, float] | tuple[str, float]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def click_frame_point(self, image, x, y):
            assert image is image250
            calls.append(("click", round(x, 3), round(y, 3)))

        def wait_action_settle(self, seconds=1.0):
            calls.append(("settle", seconds))
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "success"

    result = runner._run_direct_runtime_action(
        lambda: runner._close_daily_boss_item_detail_if_present(
            ctx,
            FakeRuntime(),
            FakeStopEvent(),
            "frame",
            "灵月天缘镜碎片 境界要求：炼虚前期壹层 描述 合成 获取途径 使用",
        ),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result is True
    assert calls == [("click", 81.0, 1504.0), ("settle", 2.0)]


def test_daily_boss_cleanup_closes_storage_bag_with_frame_249_return(tmp_path):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image249 = _image("储物袋", "0249.png", [
        {"id": "back", "kind": "rect", "title": "返回", "x": 0.05, "y": 0.932, "w": 0.08, "h": 0.047},
    ])
    ctx = {"asset_tree_path": path, "images": {249: image249}}
    calls: list[tuple[str, float, float] | tuple[str, float]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def click_frame_point(self, image, x, y):
            assert image is image249
            calls.append(("click", round(x, 3), round(y, 3)))

        def wait_action_settle(self, seconds=1.0):
            calls.append(("settle", seconds))
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "success"

    result = runner._run_direct_runtime_action(
        lambda: runner._close_daily_boss_storage_bag_if_present(
            ctx,
            FakeRuntime(),
            FakeStopEvent(),
            "frame",
            "储物袋 全部 书籍 丹药 礼物 日程 快捷操作",
        ),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result is True
    assert calls == [("click", 81.0, 1528.8), ("settle", 2.0)]


def test_daily_boss_returns_to_world_after_list_completion(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image178 = _image("首领列表", "0178.png", [
        {"id": "xianjie", "kind": "rect", "title": "仙界", "x": 0.6, "y": 0.08, "w": 0.2, "h": 0.05},
        {"id": "list", "kind": "rect", "title": "首领列表", "x": 0.05, "y": 0.15, "w": 0.9, "h": 0.65},
        {"id": "reward", "kind": "rect", "title": "剩余奖励次数", "x": 0.3, "y": 0.8, "w": 0.4, "h": 0.06},
    ])
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.08, "y": 0.25, "w": 0.88, "h": 0.55},
    ])
    image179 = _image("首领详情", "0179.png", [])
    image180 = _image("挑战中", "0180.png", [])
    image181 = _image("挑战完", "0181.png", [])
    ctx = {"asset_tree_path": path, "images": {69: image69, 178: image178, 179: image179, 180: image180, 181: image181}}
    returned: list[bool] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: (178, 100.0, "list"))
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_click_shape", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _preferred=None: (178, 100.0))
    def fake_wait_scene(*_args, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_wait_scene_id", fake_wait_scene)
    monkeypatch.setattr(runner, "_ocr_fragments_in_shapes", lambda *_args, **_kwargs: [{"text": "剩余奖励次数：0"}])

    def fake_return(_ctx, _stop_event):
        returned.append(True)
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_return_daily_boss_to_world", fake_return)

    payload = {
        "__daily_boss_runtime_snapshot_override": {
            "complete": True,
            "list_loaded": True,
            "reward_remaining": 0,
        }
    }
    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_boss_task(ctx, FakeStopEvent(), payload),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert returned == [True]


def test_xianfu_visit_partner_max_continue_zero_closes_popup(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image174 = _image("绝品仙侣", "0174.png", [])
    image175 = _image("继续寻访", "0175.png", [
        {"id": "half", "kind": "rect", "title": "半价", "x": 0.58, "y": 0.75, "w": 0.3, "h": 0.06},
        {"id": "continue", "kind": "rect", "title": "继续", "x": 0.56, "y": 0.82, "w": 0.3, "h": 0.07},
        {"id": "close", "kind": "rect", "title": "关闭", "x": 0.16, "y": 0.82, "w": 0.29, "h": 0.07},
    ])
    ctx = {"entry": object(), "images": {174: image174, 175: image175}}
    runtime = runner._fanxiu_runtime(ctx, tmp_path / "asset_tree.json")
    clicked: list[str] = []
    wait_calls: list[tuple[int, ...]] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_ocr_fragments_in_shapes", lambda *_args, **_kwargs: [{"text": "50（半价）"}])

    def fake_wait_view(*views, **_kwargs):
        view_ids = tuple(int(view) for view in views)
        wait_calls.append(view_ids)
        if view_ids == (175,):
            if False:
                yield BehaviorTreeStatus.RUNNING
            return behavior_tree_runtime_core.View(image175)
        if view_ids == (174,):
            if False:
                yield BehaviorTreeStatus.RUNNING
            return behavior_tree_runtime_core.View(image174)
        raise AssertionError(view_ids)

    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)
    monkeypatch.setattr(runtime, "wait_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runtime, "current_scene", lambda *_args, **_kwargs: (174, 100.0, "frame"))
    monkeypatch.setattr(runtime, "ocr_text", lambda _frame: "绝品仙侣 免费抽取")
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, *_args, **_kwargs: clicked.append(shape["title"]))

    result = runner._run_direct_runtime_action(
        lambda: runner._handle_xianfu_continue_visit_popup(runtime, max_continue=0),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == ["关闭"]
    assert wait_calls == [(175,)]


def test_xianfu_return_confirms_leave_popup_even_when_base_scene_is_world(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    image86 = _image("离开场景", "0086.png", [
        {"id": "confirm", "kind": "rect", "title": "确认", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.6, "y": 0.6, "w": 0.1, "h": 0.05},
    ])
    image171 = _image("仙府主页", "0171.png", [
        {"id": "leave", "kind": "rect", "title": "离开", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.8, "y": 0.5, "w": 0.1, "h": 0.08},
    ])
    ctx = {"entry": object(), "images": {34: image34, 86: image86, 171: image171}}
    runtime = runner._fanxiu_runtime(ctx, tmp_path / "asset_tree.json")
    clicked: list[str] = []
    scenes = iter([
        (171, 100.0, "home"),
        (34, 100.0, "leave-confirm"),
        (34, 100.0, "world"),
    ])

    monkeypatch.setattr(runtime, "current_scene", lambda *_args, **_kwargs: next(scenes))
    monkeypatch.setattr(
        runtime,
        "ocr_text",
        lambda frame: "是否离开当前场景 取消 确认" if frame == "leave-confirm" else "世界 大地图 仙府",
    )
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, *_args, **_kwargs: clicked.append(shape["title"]))

    def fake_wait_view(*views, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        for view_id in views:
            if int(view_id) == 34:
                return behavior_tree_runtime_core.View(image34)
        return behavior_tree_runtime_core.View(image171)

    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)

    result = runner._run_direct_runtime_action(
        lambda: runner._return_xianfu_pages_to_world(runtime, task_label="仙府_寻访仙侣", current_candidates=(171, 86, 34)),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == ["离开", "确认"]


def test_xianfu_scene_185_fails_closed_instead_of_clicking_lingzu_skip(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image185 = _image("灵祖挑战过场", "0185.png", [
        {"id": "skip", "kind": "rect", "title": "跳过", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.88, "y": 0.07, "w": 0.1, "h": 0.06},
    ])
    ctx = {
        "entry": object(),
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {185: image185},
    }
    runtime = runner._fanxiu_runtime(ctx, tmp_path / "asset_tree.json")
    clicked: list[str] = []
    monkeypatch.setattr(runtime, "current_scene", lambda *_args, **_kwargs: (185, 100.0, "lingzu"))
    monkeypatch.setattr(runtime, "ocr_text", lambda *_args, **_kwargs: pytest.fail("#185 must fail before OCR fallback"))
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, *_args, **_kwargs: clicked.append(shape["title"]))

    with pytest.raises(RuntimeError, match="#185.*日常_灵祖.*不属于仙府"):
        runner._run_direct_runtime_action(
            lambda: runner._execute_xianfu_visit_partner_task(ctx, threading.Event(), {}),
            stop_event=threading.Event(),
            tick_seconds=0.01,
        )

    assert 185 not in runner._XIANFU_INTERNAL_SCENES
    assert clicked == []


def test_xianfu_learn_skill_rejects_scene_185_before_any_gui_action(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image176 = _image("绝技", "0176.png", [])
    image185 = _image("灵祖挑战过场", "0185.png", [
        {"id": "skip", "kind": "rect", "title": "跳过", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.88, "y": 0.07, "w": 0.1, "h": 0.06},
    ])
    ctx = {
        "entry": object(),
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {176: image176, 185: image185},
    }
    runtime = runner._fanxiu_runtime(ctx, tmp_path / "asset_tree.json")
    clicked: list[str] = []
    monkeypatch.setattr(runtime, "current_scene", lambda *_args, **_kwargs: (185, 100.0, "lingzu"))
    monkeypatch.setattr(runtime, "ocr_text", lambda *_args, **_kwargs: pytest.fail("#185 must fail before OCR fallback"))
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, *_args, **_kwargs: clicked.append(shape["title"]))

    with pytest.raises(RuntimeError, match="#185.*日常_灵祖.*不属于仙府"):
        runner._run_direct_runtime_action(
            lambda: runner._execute_xianfu_learn_skill_task(
                ctx,
                threading.Event(),
                {"__xianfu_skill_runtime_snapshot_override": {"complete": False}},
            ),
            stop_event=threading.Event(),
            tick_seconds=0.01,
        )

    assert clicked == []


def test_xianfu_learn_skill_return_uses_explicit_xianfu_chain(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    image86 = _image("离开场景", "0086.png", [
        {"id": "confirm", "kind": "rect", "title": "确认", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.6, "y": 0.6, "w": 0.1, "h": 0.05},
    ])
    image171 = _image("仙府主页", "0171.png", [
        {"id": "leave", "kind": "rect", "title": "离开", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.8, "y": 0.5, "w": 0.1, "h": 0.08},
    ])
    image176 = _image("绝技", "0176.png", [
        {"id": "exit", "kind": "rect", "title": "退出", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.08, "y": 0.48, "w": 0.1, "h": 0.08},
    ])
    ctx = {"entry": object(), "images": {34: image34, 86: image86, 171: image171, 176: image176}}
    runtime = runner._fanxiu_runtime(ctx, tmp_path / "asset_tree.json")
    clicked: list[str] = []
    goto_targets: list[int] = []
    scenes = iter([
        (176, 100.0, "skill"),
        (171, 100.0, "home"),
        (34, 100.0, "leave-confirm"),
        (34, 100.0, "world"),
    ])

    monkeypatch.setattr(runtime, "current_scene", lambda *_args, **_kwargs: next(scenes))
    monkeypatch.setattr(
        runtime,
        "ocr_text",
        lambda frame: "是否离开当前场景 取消 确认" if frame == "leave-confirm" else "世界 大地图 仙府",
    )
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, *_args, **_kwargs: clicked.append(shape["title"]))

    def fake_wait_view(*views, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        for view_id in views:
            if int(view_id) == 34:
                return behavior_tree_runtime_core.View(image34)
            if int(view_id) == 171:
                return behavior_tree_runtime_core.View(image171)
            if int(view_id) == 176:
                return behavior_tree_runtime_core.View(image176)
        return None

    def fake_goto_view(target, *_args, **_kwargs):
        goto_targets.append(int(target))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return behavior_tree_runtime_core.View(image34)

    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)
    monkeypatch.setattr(runtime, "goto_view", fake_goto_view)

    result = runner._run_direct_runtime_action(
        lambda: runner._return_xianfu_learn_skill_to_world(runtime),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == ["退出", "离开", "确认"]
    assert goto_targets == []


@pytest.mark.parametrize("landing_scene", [171, 176])
def test_xianfu_reward_scene_347_waits_for_safe_auto_landing(tmp_path, monkeypatch, landing_scene):
    runner = create_behavior_tree_runtime_runner()
    landing = _image("仙府落点", f"{landing_scene:04d}.png", [])
    runtime = runner._fanxiu_runtime(
        {"entry": object(), "images": {landing_scene: landing}},
        tmp_path / "asset_tree.json",
    )
    waits: list[tuple] = []

    def fake_wait_view(*views, **kwargs):
        waits.append((tuple(int(view) for view in views), kwargs))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return behavior_tree_runtime_core.View(landing)

    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)

    result = runner._run_direct_runtime_action(
        lambda: runner._recover_xianfu_reward_transition(runtime, 347, ""),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == landing_scene
    assert waits == [
        (
            (177, 176, 172, 171, 34),
            {"timeout": 18.0, "label": "仙府_领悟绝技：等待 #347 自动关闭"},
        )
    ]


def test_xianfu_reward_scene_347_timeout_fails_closed(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = runner._fanxiu_runtime({"entry": object(), "images": {}}, tmp_path / "asset_tree.json")

    def fake_wait_view(*_views, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        raise TimeoutError("reward overlay did not close")

    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)

    with pytest.raises(TimeoutError, match="did not close"):
        runner._run_direct_runtime_action(
            lambda: runner._recover_xianfu_reward_transition(runtime, 347, ""),
            stop_event=threading.Event(),
            tick_seconds=0.01,
        )


def test_xianfu_reward_text_requires_auto_close_before_waiting(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    runtime = runner._fanxiu_runtime({"entry": object(), "images": {}}, tmp_path / "asset_tree.json")
    monkeypatch.setattr(
        runtime,
        "wait_view",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not wait")),
    )

    result = runner._run_direct_runtime_action(
        lambda: runner._recover_xianfu_reward_transition(runtime, None, "恭喜获得 点击屏幕继续"),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result is None


def test_xianfu_scene_177_reference_refresh_preserves_asset_metadata_and_backs_up_old_frame(tmp_path):
    from PIL import Image

    runner = create_behavior_tree_runtime_runner()
    entry_dir = tmp_path / "entry"
    image_dir = entry_dir / "images"
    image_dir.mkdir(parents=True)
    asset_tree_path = entry_dir / "asset-tree.json"
    asset_tree_path.write_text("[]", encoding="utf-8")
    image177 = _image("领悟绝技", "0177.jpg", [{"title": "继续", "sceneJumpTarget": "176(16)"}])
    image177.update({"width": 12, "height": 10})
    with Image.new("RGB", (12, 10), "blue") as old_image:
        old_image.save(image_dir / "0177.jpg", format="JPEG")
    runtime = runner._fanxiu_runtime(
        {"asset_tree_path": asset_tree_path, "images": {177: image177}},
        asset_tree_path,
    )

    evidence_dir = runner._refresh_scene_reference_frame(runtime, 177, _png_data_url(12, 10))

    assert image177["filename"] == "0177.jpg"
    assert image177["width"] == 12
    assert image177["height"] == 10
    assert image177["shapes"] == [{"title": "继续", "sceneJumpTarget": "176(16)"}]
    assert (evidence_dir / "before-0177.jpg").is_file()
    summary = json.loads((evidence_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["scene_id"] == 177
    assert summary["shape_count"] == 1
    with Image.open(image_dir / "0177.jpg") as refreshed:
        assert refreshed.size == (12, 10)


def test_xianfu_learn_skill_cd_parser_reuses_free_draw_cd_text():
    assert behavior_tree_runtime_core._parse_xianfu_skill_cd_seconds("06:33:27后可免费抽取") == 23607
    assert behavior_tree_runtime_core._parse_xianfu_skill_cd_seconds("12分05秒后可免费抽取") == 725
    assert behavior_tree_runtime_core._parse_xianfu_skill_cd_seconds("免费抽取") == 0
    assert behavior_tree_runtime_core._parse_xianfu_skill_cd_seconds("攻击+18.9兆御+7282.42万免费抽取后重新倒计时") == 0
    assert behavior_tree_runtime_core._parse_xianfu_skill_cd_seconds("无法识别") is None


def test_xianfu_learn_skill_skips_when_skill_page_annotation_missing(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    scheduled = []
    monkeypatch.setattr(behavior_tree_runtime_core, "_now", lambda: datetime(2026, 8, 3, 5, 0, 0))
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: scheduled.append((task_id, next_time)),
    )

    class FakeStopEvent:
        def is_set(self):
            return False

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_xianfu_learn_skill_task({"asset_tree_path": path, "images": {}}, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "skipped"
    assert any("缺少 #176" in log["message"] for log in runner.status()["logs"])
    assert scheduled == [("xianfu-learn-skill", "2026-08-03 05:30:00")]


def test_unknown_scene_frame_reports_missing_annotation_without_writing_asset_tree(tmp_path, monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    tree: list[dict] = []
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")

    class Entry:
        mode = "local"

    try:
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
    except RuntimeError as exc:
        assert "请人工补标/修标后重试" in str(exc)
    else:
        raise AssertionError("unknown scene should stop instead of writing placeholder assets")

    assert json.loads(path.read_text(encoding="utf-8")) == []


def test_behavior_tree_logs_include_current_scope_and_item_id():
    runner = create_behavior_tree_runtime_runner()

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








def test_restore_persisted_guard_config_prunes_retired_guard_items_when_logs_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_behavior_tree_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(behavior_tree_runtime_core, "_behavior_tree_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    runtime_state = tmp_path / "runtime_state.json"
    runtime_state.write_text(
        json.dumps(
            {
                "guard_enabled": True,
                "guard_running": False,
                "guard_entry_id": "codepc_mf",
                "guard_interval_seconds": 2.0,
                "guard_items": {
                    "close_popups": {"enabled": True, "entry_id": "codepc_mf", "updated_at": 123.0},
                    "wanling_invite": {"enabled": False, "entry_id": "", "updated_at": 0.0},
                },
                "logs": [{"kind": "info", "message": "persisted"}],
                "cell_logs": [{"id": "cell-persisted", "title": "持久化 cell", "entries": []}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    runner = create_behavior_tree_runtime_runner()

    with runner._lock:
        runner._guard_enabled = False
        runner._status["logs"] = [{"kind": "info", "message": "local"}]
        runner._restore_persisted_config_locked()
        runner._sync_guard_status_locked()
        status = json.loads(json.dumps(runner._status, ensure_ascii=False))

    assert status["guard_enabled"] is False
    assert status["guard_entry_id"] == "codepc_mf"
    assert set(status["guard_items"]) == {"device_health"}
    assert status["guard_items"]["device_health"]["enabled"] is True
    assert status["logs"] == [{"kind": "info", "message": "local"}]
    assert status["cell_logs"] == [{"id": "cell-persisted", "title": "持久化 cell", "entries": []}]






def test_runtime_status_normalizes_guard_items_from_backend_definitions():
    status = {
        "guard_enabled": False,
        "guard_running": False,
        "guard_entry_id": "",
        "guard_items": {},
    }

    _normalize_behavior_tree_runtime_guard_items(status)

    assert set(status["guard_items"]) == {"device_health"}
    assert status["guard_items"]["device_health"]["label"] == "设备健康"
    assert status["guard_items"]["device_health"]["enabled"] is True


def test_runtime_status_migrates_unedited_device_health_default():
    status = {
        "guard_enabled": True,
        "guard_running": False,
        "guard_entry_id": "",
        "guard_items": {
            "device_health": {"enabled": False, "updated_at": 0.0},
        },
    }

    _normalize_behavior_tree_runtime_guard_items(status)

    assert status["guard_items"]["device_health"]["enabled"] is True


def test_runtime_status_preserves_user_disabled_device_health():
    status = {
        "guard_enabled": True,
        "guard_running": False,
        "guard_entry_id": "",
        "guard_items": {
            "device_health": {"enabled": False, "updated_at": 1710000000.0},
        },
    }

    _normalize_behavior_tree_runtime_guard_items(status)

    assert status["guard_items"]["device_health"]["enabled"] is False






def test_runtime_guard_service_tick_runs_device_health_guard(monkeypatch, tmp_path):
    runner = create_behavior_tree_runtime_runner()
    calls: list[str] = []
    monkeypatch.setattr(runner, "_run_device_health_guard_tick", lambda entry_id: calls.append(entry_id) or True)

    with runner._lock:
        runner._guard_group_enabled = True
        runner._guard_items["device_health"] = {"enabled": True, "updated_at": 1710000001.0}

    result = runner._runtime_guard_service_tick(
        "device_health",
        {"entry_id": "mf-entry"},
        tmp_path / "asset-tree.json",
        threading.Event(),
    )

    assert result == BehaviorTreeStatus.RUNNING
    assert calls == ["mf-entry"]






















def test_device_health_guard_tick_respects_item_switch(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        behavior_tree_runtime_core,
        "ensure_mumu_device_healthy",
        lambda **kwargs: calls.append(kwargs) or {"status": "healthy"},
    )

    with runner._lock:
        runner._guard_group_enabled = True
        runner._guard_items["device_health"] = {"enabled": False, "updated_at": 1710000000.0}

    runner._run_device_health_guard_tick("entry")
    assert calls == []

    with runner._lock:
        runner._guard_items["device_health"] = {"enabled": True, "updated_at": 1710000001.0}

    runner._run_device_health_guard_tick("entry")
    assert calls == [{"recover": True, "reason": "resident_heartbeat"}]

def test_device_health_guard_tick_reports_recovered_and_throttles(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(behavior_tree_runtime_core.time, "time", lambda: 100.0)
    monkeypatch.setattr(
        behavior_tree_runtime_core,
        "ensure_mumu_device_healthy",
        lambda **kwargs: calls.append(kwargs) or {"status": "healthy", "recovered": True},
    )

    with runner._lock:
        runner._guard_group_enabled = True
        runner._guard_items["device_health"] = {"enabled": True, "updated_at": 1710000001.0}

    assert runner._run_device_health_guard_tick("entry") is True
    assert runner._run_device_health_guard_tick("entry") is False
    assert calls == [{"recover": True, "reason": "resident_heartbeat"}]


def test_unknown_fallback_return_is_an_explicit_navigation_primitive():
    runner = behavior_tree_runtime_core.BehaviorTreeRuntimeRunner()

    assert hasattr(runner, "_try_navigation_fallback_return")


def test_manual_device_restart_interrupts_running_cell_and_keeps_runtime(monkeypatch):
    status_calls = iter(
        [
            {"ok": True, "running": True, "status": "running", "entry_id": "codepc_mf"},
            {"ok": True, "running": False, "status": "idle", "entry_id": "codepc_mf"},
        ]
    )
    interrupts: list[tuple[str, dict[str, object]]] = []
    recoveries: list[dict[str, object]] = []
    monkeypatch.setattr(fanxiu_api, "_sync_behavior_tree_runtime_runner_to_core", lambda: None)
    monkeypatch.setattr(
        fanxiu_api,
        "_behavior_tree_runtime_status",
        lambda **_kwargs: next(status_calls),
    )
    monkeypatch.setattr(
        fanxiu_api._behavior_tree_framework,
        "interrupt_current_cell",
        lambda entry_id, **kwargs: interrupts.append((entry_id, kwargs)) or {"status": "stopped"},
    )
    monkeypatch.setattr(
        fanxiu_api,
        "recover_mumu_device",
        lambda **kwargs: recoveries.append(kwargs) or {"status": "healthy", "recovered": True},
    )

    result = fanxiu_api._restart_fanxiu_behavior_tree_runtime_device("codepc_mf")

    assert result.ok is True
    assert result.message == "模拟器已重启，游戏画面可用"
    assert result.runtime.status == "idle"
    assert interrupts[0][0] == "codepc_mf"
    assert recoveries == [
        {
            "vmindex": "1",
            "reason": "manual_runtime_page_request",
            "force_restart": True,
        }
    ]


def test_manual_device_restart_reports_recovery_failure(monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_sync_behavior_tree_runtime_runner_to_core", lambda: None)
    monkeypatch.setattr(
        fanxiu_api,
        "_behavior_tree_runtime_status",
        lambda **_kwargs: {"ok": True, "running": False, "status": "idle"},
    )
    monkeypatch.setattr(
        fanxiu_api,
        "recover_mumu_device",
        lambda **_kwargs: {"status": "broken", "recovered": False, "last_error": "launch failed"},
    )

    with pytest.raises(fanxiu_api.HTTPException, match="模拟器重启失败：launch failed") as exc_info:
        fanxiu_api._restart_fanxiu_behavior_tree_runtime_device("codepc_mf")

    assert exc_info.value.status_code == 503


def test_known_announcement_overlay_ignores_scene_49_ocr_tokens(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    frame = "frame-49"
    ctx = {"asset_tree": [], "images": {}}
    identify_calls = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: frame)
    monkeypatch.setattr(runner, "_ocr_fragments", lambda _frame: "ocr-fragments")
    monkeypatch.setattr(runner, "_ocr_text", lambda _fragments: "游戏公告 更新公告 风险提醒")

    def identify(_ctx, observed_frame, preferred_scene_ids):
        identify_calls.append((observed_frame, preferred_scene_ids))
        # Model _identify_scene_number's legacy OCR fallback on a real #49
        # frame: the numeric result may be 14, but it is not a formal match.
        _ctx["_last_scene_recognition_status"] = "startup_ocr"
        return 14, 100.0

    monkeypatch.setattr(runner, "_identify_scene_number", identify)

    assert runner._known_blocking_overlay_info(ctx) is None
    assert identify_calls == [(frame, [14])]


def test_known_announcement_overlay_requires_formal_scene_14_match(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    frame = "frame-14"
    announcement = _image(
        "游戏公告",
        "0014.png",
        [{"id": "close", "kind": "rect", "title": "关闭公告", "x": 0.8, "y": 0.1, "w": 0.1, "h": 0.1}],
    )
    ctx = {"asset_tree": [announcement], "images": {14: announcement}}

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: frame)
    monkeypatch.setattr(runner, "_ocr_fragments", lambda _frame: "ocr-fragments")
    monkeypatch.setattr(runner, "_ocr_text", lambda _fragments: "ordinary text")

    def identify(_ctx, observed_frame, preferred_scene_ids):
        assert observed_frame == frame
        assert preferred_scene_ids == [14]
        _ctx["_last_scene_recognition_status"] = "graph_match"
        return 14, 96.0

    monkeypatch.setattr(runner, "_identify_scene_number", identify)

    assert runner._known_blocking_overlay_info(ctx) == {
        "scene_id": 14,
        "title": "游戏公告",
        "blocking": False,
        "all_shapes": ["关闭公告"],
        "action_shapes": ["关闭公告"],
        "message": "检测到游戏公告遮挡，已有安全关闭动作标注",
    }
