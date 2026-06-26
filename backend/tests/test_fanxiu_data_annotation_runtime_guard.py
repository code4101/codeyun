import os
import tempfile

os.environ["CODEYUN_DATA_DIR"] = os.path.join(tempfile.gettempdir(), "codeyun", "pytest_fanxiu_runtime_guard")

import json
import threading
import time
import base64
import io
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from backend.api import fanxiu as fanxiu_api
from backend.api.fanxiu import (
    BehaviorTreeStatus,
    _DataAnnotationRuntimeContainer,
    _default_data_annotation_scheduler_tasks,
    _enqueue_data_annotation_manual_job,
    _data_annotation_task_supported,
    _normalize_data_annotation_runtime_guard_items,
    _pop_next_data_annotation_manual_job,
    _record_data_annotation_scheduler_task_fact,
    _read_data_annotation_world_facts,
    _requeue_running_data_annotation_manual_jobs,
    _remove_data_annotation_manual_job,
    _write_data_annotation_world_facts,
)
from backend.core.fanxiu.runtime.behavior_tree import create_fanxiu_runtime_runner
from backend.core.fanxiu.runtime import behavior_tree as fanxiu_behavior_tree_core
from backend.core.fanxiu.data_annotation import runtime_control as runtime_control_core
from backend.core.fanxiu.data_annotation import runtime_runner as runtime_runner_core
from backend.core.fanxiu.data_annotation import popup_guard as popup_guard_core
from backend.core.fanxiu.data_annotation.scheduler import (
    data_annotation_scheduler_time_order_key,
    repair_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.state import normalize_data_annotation_runtime_display
from backend.core.access.service_tokens import SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL, create_service_access_token
from backend.core.fanxiu.runtime.mumu_control import _compare_frame_crops
from backend.models import FanxiuMailRecord, User, UserDevice
from backend.core.fanxiu.runtime import mumu_control as mumu_control


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
    asset_tree_dir = tmp_path / "fanxiu" / "data-annotation" / "asset-trees"

    def runtime_file(name: str) -> Path:
        return runtime_dir / name

    def asset_tree_path(entry_id: str) -> Path:
        return asset_tree_dir / f"{entry_id}.json"

    monkeypatch.setattr(fanxiu_api, "_DATA_ANNOTATION_RUNTIME_RUNNER", create_fanxiu_runtime_runner())
    monkeypatch.setattr(fanxiu_api, "_data_annotation_runtime_state_path", lambda: runtime_file("runtime_state.json"))
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: runtime_file("world_facts.json"))
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_state_path", lambda: runtime_file("scheduler_tasks.json"))
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_settings_path", lambda: runtime_file("scheduler_settings.json"))
    monkeypatch.setattr(fanxiu_api, "_data_annotation_manual_job_state_path", lambda: runtime_file("manual_jobs.json"))
    monkeypatch.setattr(fanxiu_api, "_data_annotation_job_group_isolation_path", lambda: runtime_file("job_group_isolation.json"))
    monkeypatch.setattr(fanxiu_api, "_data_annotation_mail_scan_state_path", lambda: runtime_file("mail_scan_state.json"))
    monkeypatch.setattr(fanxiu_api, "_data_annotation_asset_tree_path", asset_tree_path)

    monkeypatch.setattr(runtime_runner_core, "_data_annotation_runtime_state_path", lambda: runtime_file("runtime_state.json"))
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: runtime_file("world_facts.json"))
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: runtime_file("scheduler_tasks.json"))
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_settings_path", lambda: runtime_file("scheduler_settings.json"))
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_manual_job_state_path", lambda: runtime_file("manual_jobs.json"))
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_job_group_isolation_path", lambda: runtime_file("job_group_isolation.json"))
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_mail_scan_state_path", lambda: runtime_file("mail_scan_state.json"))
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_asset_tree_path", asset_tree_path)
    monkeypatch.setattr(runtime_runner_core, "_behavior_tree_control_path", lambda: runtime_file("behavior_tree_control.json"))

    monkeypatch.setattr(runtime_control_core, "fanxiu_data_annotation_runtime_state_path", lambda: runtime_file("runtime_state.json"))
    monkeypatch.setattr(runtime_control_core, "fanxiu_data_annotation_world_facts_path", lambda: runtime_file("world_facts.json"))
    monkeypatch.setattr(runtime_control_core, "fanxiu_data_annotation_scheduler_state_path", lambda: runtime_file("scheduler_tasks.json"))
    monkeypatch.setattr(runtime_control_core, "fanxiu_data_annotation_scheduler_settings_path", lambda: runtime_file("scheduler_settings.json"))
    monkeypatch.setattr(runtime_control_core, "fanxiu_data_annotation_manual_job_state_path", lambda: runtime_file("manual_jobs.json"))

    monkeypatch.setattr(fanxiu_behavior_tree_core, "_RUNTIME_RUNNER", None)
    monkeypatch.setattr(fanxiu_behavior_tree_core, "fanxiu_data_annotation_runtime_state_path", lambda: runtime_file("runtime_state.json"))
    monkeypatch.setattr(fanxiu_behavior_tree_core, "fanxiu_data_annotation_world_facts_path", lambda: runtime_file("world_facts.json"))
    monkeypatch.setattr(fanxiu_behavior_tree_core, "fanxiu_data_annotation_scheduler_state_path", lambda: runtime_file("scheduler_tasks.json"))
    monkeypatch.setattr(fanxiu_behavior_tree_core, "fanxiu_data_annotation_scheduler_settings_path", lambda: runtime_file("scheduler_settings.json"))
    monkeypatch.setattr(fanxiu_behavior_tree_core, "fanxiu_data_annotation_manual_job_state_path", lambda: runtime_file("manual_jobs.json"))
    monkeypatch.setattr(fanxiu_behavior_tree_core, "fanxiu_data_annotation_mail_scan_state_path", lambda: runtime_file("mail_scan_state.json"))
    monkeypatch.setattr(fanxiu_behavior_tree_core, "data_annotation_asset_tree_path", asset_tree_path)
    monkeypatch.setattr(fanxiu_behavior_tree_core, "fanxiu_job_group_isolation_path", lambda: runtime_file("job_group_isolation.json"))
    monkeypatch.setattr(fanxiu_behavior_tree_core, "fanxiu_behavior_tree_service_owner_path", lambda: runtime_file("behavior_tree_service_owner.json"))
    monkeypatch.setattr(fanxiu_behavior_tree_core, "fanxiu_behavior_tree_control_path", lambda: runtime_file("behavior_tree_control.json"))


def test_runtime_wait_click_then_shape_closes_click_with_target_probe(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    runtime = runtime_runner_core.FanxiuRuntime(runner, {"images": {}}, stop_event=threading.Event())
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


def test_runtime_shape_lookup_inherits_parent_frame_shapes():
    runner = create_fanxiu_runtime_runner()
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
    parent["children"] = [child]
    tree = [parent]
    ctx = {"asset_tree": tree, "images": runner._index_images(tree), "attrs": {}}
    runtime = runtime_runner_core.FanxiuRuntime(runner, ctx)

    inherited = runtime.shape(75, "退出")

    assert inherited.raw["id"] == "daily-exit"
    assert inherited.parent_view.id == 69


def test_runtime_child_shape_overrides_parent_shape_with_same_title():
    runner = create_fanxiu_runtime_runner()
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
    runtime = runtime_runner_core.FanxiuRuntime(runner, ctx)

    found = runtime.shape(75, "日常")

    assert found.raw["id"] == "child-daily"
    assert found.parent_view.id == 75


def test_runtime_click_inherited_shape_uses_parent_shape_source(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    parent = _scene_image(
        "日常",
        "0069.png",
        [{"id": "daily-exit", "title": "退出", "x": 0.1, "y": 0.2, "w": 0.1, "h": 0.1}],
        layer=1,
    )
    child = _scene_image("活动报名", "0075.png", [], layer=1)
    parent["children"] = [child]
    tree = [parent]
    ctx = {"asset_tree": tree, "images": runner._index_images(tree), "attrs": {}}
    runtime = runtime_runner_core.FanxiuRuntime(runner, ctx)
    clicked: list[tuple[int | None, str]] = []

    monkeypatch.setattr(runner, "_shape_click_needs_frame", lambda _shape: False)
    monkeypatch.setattr(
        runner,
        "_click_shape",
        lambda _ctx, image, shape, *_args, **_kwargs: clicked.append((runner._image_number(image), shape["id"])),
    )

    runtime.click_shape(75, "退出")

    assert clicked == [(69, "daily-exit")]


def test_scheduler_running_manual_job_orphaned_after_runtime_stop_keeps_due_time(tmp_path, monkeypatch):
    now_ts = datetime(2026, 6, 21, 7, 2, 0).timestamp()
    manual_job_path = tmp_path / "manual_jobs.json"
    tasks = [
        {
            "id": "legacy-daily-shuangxiu",
            "task_type": "daily_shuangxiu",
            "label": "日常_双修",
            "enabled": True,
            "schedule_kind": "daily",
            "next_time": "2026-06-21 05:00:00",
            "retry_after": None,
            "last_run_at": "2026-06-21 07:00:00",
            "last_result": "queued",
            "cooldown_seconds": 600,
        }
    ]
    runtime_control_core.write_manual_jobs(
        [
            {
                "id": "manual-stale",
                "task_type": "daily_shuangxiu",
                "label": "AI保底接管到期任务：日常_双修",
                "group": "manual_job",
                "status": "running",
                "payload": {"__scheduler_task_id": "legacy-daily-shuangxiu"},
                "created_at": now_ts - 120,
                "updated_at": now_ts - 120,
            }
        ],
        manual_job_path,
    )
    monkeypatch.setattr(
        runtime_control_core,
        "read_runtime_status",
        lambda: {"running": False, "status": "stopped", "updated_at": now_ts - 120},
    )

    changed = runtime_control_core.repair_orphaned_scheduler_runs(
        tasks,
        manual_job_path=manual_job_path,
        now_ts=now_ts,
        running=False,
    )

    assert changed is True
    assert runtime_control_core.read_manual_jobs(manual_job_path) == []
    assert tasks[0]["last_result"] == "queued"
    assert tasks[0]["next_time"] == "2026-06-21 05:00:00"
    assert tasks[0]["retry_after"] is None


def test_runtime_wait_click_then_any_closes_click_with_branch_probe(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    runtime = runtime_runner_core.FanxiuRuntime(runner, {"images": {}}, stop_event=threading.Event())
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
    runner = create_fanxiu_runtime_runner()
    image69 = _image("日常", "0069.png", [{"title": "拜谒", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1, "sceneJumpTarget": "264"}])
    image264 = _image("拜谒", "0264.png", [])
    runtime = runtime_runner_core.FanxiuRuntime(
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


def test_runtime_wait_click_then_view_requires_target_or_jump_target():
    runner = create_fanxiu_runtime_runner()
    image69 = _image("日常", "0069.png", [{"title": "无目标", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1}])
    runtime = runtime_runner_core.FanxiuRuntime(
        runner,
        {"images": {69: image69}, "asset_tree": [image69]},
        stop_event=threading.Event(),
    )

    with pytest.raises(RuntimeError, match="缺少目标场景"):
        _drain_generator(runtime.wait_click_then_view(69, "无目标"))


def test_runtime_wait_click_then_view_timeout_reports_source_and_declared_target(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image69 = _image("日常", "0069.png", [{"title": "拜谒", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1, "sceneJumpTarget": "264"}])
    runtime = runtime_runner_core.FanxiuRuntime(
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
        _drain_generator(runtime.wait_click_then_view(69, "拜谒", timeout=3.0))

    message = str(exc_info.value)
    assert "源场景=#69" in message
    assert "shape=[拜谒]" in message
    assert "期望目标=#264" in message
    assert "sceneJumpTarget=264" in message
    assert "unknown 0%" in message


def test_runtime_ocr_matches_wraps_predicate(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    runtime = runtime_runner_core.FanxiuRuntime(runner, {"images": {}}, stop_event=threading.Event())
    monkeypatch.setattr(runner, "_cached_ocr_lines", lambda _ctx, _frame: [{"text": "修仙传 游历 人界"}])
    monkeypatch.setattr(runner, "_ocr_text", lambda lines: " ".join(str(line.get("text") or "") for line in lines))

    condition = runtime.ocr_matches(lambda text: "修仙传" in text and "游历" in text, label="游历首页 OCR")
    result = condition.check(runtime, "frame")

    assert result.matched is True
    assert "游历首页 OCR 命中" in result.detail


def test_runtime_wait_view_or_ocr_returns_branch_and_scene(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    runtime = runtime_runner_core.FanxiuRuntime(runner, {"images": {}}, stop_event=threading.Event())
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
    app.include_router(fanxiu_api.router, prefix="/api/fanxiu")

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


def test_runtime_cell_logs_prefers_persisted_cell_and_falls_back_to_runtime_logs(monkeypatch):
    session = _build_service_session()
    app = FastAPI()
    app.include_router(fanxiu_api.router, prefix="/api/fanxiu")
    current_user = User(id=1, username="alice", hashed_password="x", is_active=True)

    def override_get_session():
        yield session

    app.dependency_overrides[fanxiu_api.get_session] = override_get_session
    app.dependency_overrides[fanxiu_api.get_current_active_user] = lambda: current_user
    monkeypatch.setattr(fanxiu_api, "ensure_feature_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fanxiu_api, "_sync_data_annotation_runtime_runner_to_core", lambda: None)
    monkeypatch.setattr(fanxiu_api, "_data_annotation_runtime_state_path", lambda: Path("runtime-state.json"))
    monkeypatch.setattr(
        fanxiu_api,
        "_read_data_annotation_runtime_status",
        lambda: {
            "cell_logs": [
                {
                    "id": "cell-new",
                    "title": "调度器提交 tick：tick_once",
                    "source_kind": "command",
                    "source": json.dumps({"cmd": "framework.tick", "entry_id": "mumu"}, ensure_ascii=False),
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
        "_core_data_annotation_runtime_logs",
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
    assert payload["cells"][0]["title"] == "调度器提交 tick：tick_once"
    assert "行为树.tick(" in payload["cells"][0]["source"]
    assert "entry_id" not in payload["cells"][0]["source"]
    assert payload["cells"][0]["entries"][0]["message"] == "提交 cell"
    assert payload["cells"][1]["title"] == "守护 cell"
    assert payload["cells"][1]["source"].startswith("# 历史运行日志回放")


def test_ocr_row_clicks_in_shape_uses_shape_center_x_and_filters_text():
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()

    assert runner._daily_dungeon_purchase_remaining_count("剩余限购次数：3") == 3
    assert runner._daily_dungeon_purchase_remaining_count("破界符 拥有：0/1 剩余限购次数: １") == 1
    assert runner._daily_dungeon_purchase_remaining_count("剩余限购次数：O 购买并使用") == 0
    assert runner._daily_dungeon_purchase_remaining_count("购买并使用 价格：100") is None


def test_daily_dungeon_recommend_completed_state_skips_purchase(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()

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
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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


def test_scene_route_click_falls_back_for_jump_target_shape(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}
    clicked: list[dict] = []

    def fake_temp_root(*parts, create=True):
        path = tmp_path.joinpath(*parts)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(runtime_runner_core, "codeyun_temp_root", fake_temp_root)
    monkeypatch.setattr(runner, "_capture_frame", lambda _ctx: _png_data_url())
    monkeypatch.setenv("CODEYUN_FANXIU_ACTION_TRACE_MAX_FILES", "10000")
    monkeypatch.setattr(runtime_runner_core, "_click_game_window2_service", lambda payload: clicked.append(payload) or {"ok": True})

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
    runner = create_fanxiu_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}
    clicked: list[dict] = []

    class FakeActionPlanner:
        def click_point_payload(self, _image, x, y):
            return {"x": x, "y": y, "input_backend": "desktop"}

    monkeypatch.setattr(runtime_runner_core, "ActionPlanner", FakeActionPlanner)
    monkeypatch.setattr(runner, "_save_action_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime_runner_core, "_click_game_window2_service", lambda payload: clicked.append(payload) or {"ok": True})

    runner._click_frame_point(ctx, image34, 123.0, 456.0)

    assert clicked and clicked[0]["input_backend"] == "adb"


def test_runtime_local_shape_click_overrides_action_planner_input_backend(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    shape = {"id": "main", "kind": "rect", "title": "主线", "x": 0.4, "y": 0.5, "w": 0.2, "h": 0.1}
    image34 = _image("世界", "0034.png", [shape])
    ctx = {"entry": type("Entry", (), {"mode": "local"})()}
    clicked: list[dict] = []

    class FakeActionPlanner:
        def shape_center(self, _image, _shape):
            return (450.0, 880.0)

        def click_shape_payload(self, _image, _shape):
            return {"x": 450.0, "y": 880.0, "input_backend": "desktop"}

    monkeypatch.setattr(runtime_runner_core, "ActionPlanner", FakeActionPlanner)
    monkeypatch.setattr(runner, "_save_action_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime_runner_core, "_click_game_window2_service", lambda payload: clicked.append(payload) or {"ok": True})

    runner._click_shape(ctx, image34, shape)

    assert clicked and clicked[0]["input_backend"] == "adb"


def test_runtime_shape_click_uses_parent_shape_center_when_ocr_resolves_sub_box(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runtime_runner_core, "_click_game_window2_service", lambda payload: clicked.append(payload) or {"ok": True})

    runner._click_shape(ctx, image34, shape, frame_data_url="frame", match_result=match_result)

    assert clicked and clicked[0]["x"] == 450.0
    assert clicked[0]["y"] == 400.0
    assert any("ocr=日常" in log["message"] and "click=(450.0,400.0)" in log["message"] for log in runner.status()["logs"])


def test_runtime_local_drag_overrides_action_planner_input_backend(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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

    monkeypatch.setattr(runtime_runner_core, "ActionPlanner", FakeActionPlanner)
    monkeypatch.setattr(runner, "_save_action_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime_runner_core, "_drag_game_window2_service", lambda payload: dragged.append(payload) or {"ok": True})

    runner._drag_frame_point(ctx, image34, 100.0, 900.0, 100.0, 400.0, duration_ms=600)

    assert dragged and dragged[0]["input_backend"] == "adb"
    assert dragged[0]["duration_ms"] == 600


def test_runtime_drag_shape_to_shape_uses_runtime_drag(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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

    runtime = runtime_runner_core.FanxiuRuntime(runner, ctx)
    runtime.drag_shape_to_shape(58, "图标", "隐藏区", duration=0.35, frame_data_url=_png_data_url())

    assert dragged == [(135.0, 400.0, 765.0, 1200.0, 350)]


def test_runtime_remote_click_uses_remote_service(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    entry = type("Entry", (), {"mode": "remote"})()
    ctx = {"entry": entry}
    remote_clicks: list[dict] = []

    monkeypatch.setattr(runtime_runner_core, "_click_remote_game_window2", lambda _entry, payload: remote_clicks.append(payload) or {"ok": True})
    monkeypatch.setattr(runtime_runner_core, "_click_game_window2_service", lambda _payload: pytest.fail("remote click should not call local window service"))

    runner._click_frame_point(ctx, image34, 123.0, 456.0)

    assert remote_clicks and remote_clicks[0]["x"] == 123.0


def test_scene_route_click_falls_back_for_daily_exit(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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


def test_scene_route_candidates_prefer_nearer_scene_when_scores_tie(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image34 = _scene_image("世界", "0034.jpg", [
        {"id": "open", "kind": "rect", "title": "打开下方菜单", "sceneJumpTarget": "35"}
    ])
    image35 = _scene_image("世界下方菜单", "0035.png", [
        {"id": "mail", "kind": "rect", "title": "邮件", "sceneJumpTarget": "121"}
    ])
    image121 = _scene_image("邮件", "0121.png", [])
    tree = [image34, image35, image121]
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": {34: image34, 35: image35, 121: image121}}

    frame_data_url = _png_data_url()
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: frame_data_url)
    monkeypatch.setattr(
        runner,
        "_scene_score",
        lambda _ctx, image, _frame: 100.0 if image.get("filename") in {"0034.jpg", "0035.png"} else 0.0,
    )

    candidates = runner._scene_route_candidate_ids(tree, 121)

    assert candidates[:3] == [121, 35, 34]
    assert runner._identify_scene_number(ctx, "frame", candidates) == (35, 100.0)


def test_route_candidate_prefers_direct_world_over_false_high_score_selection(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image34 = _scene_image("世界", "0034.png", [
        {"id": "daily", "kind": "rect", "title": "日常", "sceneJumpTarget": "69"},
    ], layer=1)
    image69 = _scene_image("日常", "0069.png", [], layer=1)
    image18 = _scene_image("游戏封面", "0018.png", [
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


def test_default_scene_identification_prefers_strong_world_ocr_over_false_activity_text(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image34 = _scene_image("世界", "0034.png", [
        {"id": "map", "kind": "rect", "title": "大地图", "isSceneIdentity": True},
    ], layer=1)
    image18 = _scene_image("游戏封面", "0018.png", [
        {"id": "title", "kind": "rect", "title": "游戏封面", "isSceneIdentity": True},
    ], layer=1)
    ctx = {"asset_tree": [image34, image18], "images": {18: image18, 34: image34}}
    monkeypatch.setattr(runner, "_cached_ocr_lines", lambda _ctx, _frame: [
        {"text": "日常活动场景内寻得)(1/2)"},
        {"text": "大地图 仙市 仙府 天机阁 角色 装备 功法书"},
    ])
    monkeypatch.setattr(runner, "_scene_score", lambda _ctx, image, _frame: 100.0 if runner._image_number(image) == 18 else 80.0)

    scene_id, score = runner._identify_scene_number(ctx, "frame")

    assert (scene_id, score) == (34, runner.scene_threshold)


def test_go_scene_prefers_world_ocr_over_local_route_candidate(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    image34 = _scene_image("世界", "0034.jpg", [
        {"id": "daily", "kind": "rect", "title": "日常", "sceneJumpTarget": "69"}
    ])
    image69 = _scene_image("日常", "0069.jpg", [])
    image187 = _scene_image("战灵长老", "0187.jpg", [
        {"id": "blank", "kind": "rect", "title": "空白", "sceneJumpTarget": "183"}
    ])
    tree = [image34, image69, image187]
    ctx = {
        "entry": object(),
        "asset_tree": tree,
        "asset_tree_path": path,
        "images": {34: image34, 69: image69, 187: image187},
    }
    frame_data_url = _png_data_url()
    clicked: list[tuple[int | None, str]] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: frame_data_url)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, preferred_scene_ids=None: (None, 0.0))
    monkeypatch.setattr(
        runner,
        "_cached_ocr_lines",
        lambda _ctx, _frame: [{"text": "储物袋 角色 装备 功法书", "x": 0, "y": 0, "w": 100, "h": 20}],
    )
    monkeypatch.setattr(
        runner,
        "_identify_scene_number_for_route",
        lambda *_args, **_kwargs: pytest.fail("world OCR should bypass local route candidate matching"),
    )

    def click_route(_ctx, image, shape, _frame):
        clicked.append((runner._image_number(image), str(shape.get("title") or "")))

    def wait_result(*_args, **_kwargs):
        yield BehaviorTreeStatus.RUNNING
        return 69

    monkeypatch.setattr(runner, "_click_scene_route_shape", click_route)
    monkeypatch.setattr(runner, "_wait_scene_jump_result", wait_result)

    result = _drain_generator(runner._go_scene_task(ctx, path, 69, threading.Event()))

    assert result == "success"
    assert clicked == [(34, "日常")]
    assert any("场景强 OCR 命中 #34" in log["message"] for log in runner.status()["logs"])


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
    monkeypatch.setattr(mumu_control.fanxiu_android_proxy_service, "devices", lambda: ["192.168.31.181:5555"])
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
    monkeypatch.setattr(mumu_control.fanxiu_android_proxy_service, "devices", lambda: ["192.168.31.181:5555"])
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
    monkeypatch.setattr(mumu_control.fanxiu_android_proxy_service, "devices", lambda: [])
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
    monkeypatch.setattr(mumu_control.fanxiu_android_proxy_service, "devices", lambda: [])
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
    monkeypatch.setattr(mumu_control.fanxiu_android_proxy_service, "devices", lambda: [])
    monkeypatch.setattr(mumu_control.fanxiu_android_proxy_service, "adb_path", lambda: Path("D:/adb.exe"))

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
    monkeypatch.setattr(mumu_control.fanxiu_android_proxy_service, "adb_path", lambda: Path("D:/adb.exe"))

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
    runner = create_fanxiu_runtime_runner()
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
    calls: list[dict] = []
    clicked: list[dict] = []

    def fake_match(*_args, **kwargs):
        calls.append(kwargs)
        return {"similarity": 100, "matches": [{"ocr_text": "邮件"}], "fixed_box": {"x": 100, "y": 200, "w": 80, "h": 40}}

    monkeypatch.setattr(runner, "_run_match", fake_match)
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.runtime_runner._click_game_window2_service",
        lambda payload: clicked.append(payload) or {"ok": True},
    )

    runner._click_shape(ctx, image, shape, "frame")

    assert calls == [{"scan": False, "match_strategy": "anchor_pixel", "ocr_enabled": True}]
    assert clicked[0]["x"] == 495.0
    assert clicked[0]["y"] == 1488.0
    assert clicked[0]["input_backend"] == "adb"


def test_shape_ocr_reuses_existing_full_frame_cache(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
        "_ocr_lines_cache": {
            "frame": "frame",
            "lines": [{"text": "邮件", "x": 100, "y": 170, "w": 80, "h": 30}],
        },
    }

    monkeypatch.setattr(runner, "_run_match", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should reuse cached OCR")))
    monkeypatch.setattr(runner, "_ocr_lines", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not run full-frame OCR")))

    result = runner._match_shape(ctx, image, shape, "frame", condition="ocr")

    assert result["matched"] is True
    assert result["similarity"] == 100
    assert result["ocr_text"] == "邮件"
    assert result["reason"] == "cached_frame_ocr"


def test_shape_ocr_cache_miss_does_not_repeat_ocr(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
        "_ocr_lines_cache": {
            "frame": "frame",
            "lines": [{"text": "邮件", "x": 700, "y": 1400, "w": 80, "h": 30}],
        },
    }

    monkeypatch.setattr(runner, "_run_match", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache is decisive for this frame")))
    monkeypatch.setattr(runner, "_ocr_lines", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not run full-frame OCR")))

    result = runner._match_shape(ctx, image, shape, "frame", condition="ocr")

    assert result["matched"] is False
    assert result["similarity"] == 0
    assert result["matches"] == []
    assert result["reason"] == "cached_frame_ocr"


def test_click_floating_required_ocr_shape_does_not_fallback_to_raw_box(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
        "backend.core.fanxiu.data_annotation.runtime_runner._click_game_window2_service",
        lambda payload: clicked.append(payload) or {"ok": True},
    )

    try:
        runner._click_shape(ctx, image, shape, "frame")
    except RuntimeError as exc:
        assert "未能按 OCR 定位浮动按钮" in str(exc)
    else:
        raise AssertionError("expected required OCR miss to abort click")
    assert clicked == []


def test_click_floating_optional_ocr_shape_still_requires_a_match(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
        "backend.core.fanxiu.data_annotation.runtime_runner._click_game_window2_service",
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
    runner = create_fanxiu_runtime_runner()
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


def test_daily_shuangxiu_detail_allows_invite_button_ocr_miss():
    runner = create_fanxiu_runtime_runner()

    assert runner._daily_shuangxiu_text_is_detail(
        "修炼 凤舞九天诀 激活功法 痴情咒 每次修炼可得体魄：1800 气劲：1800 双人神通 激活本功法即可获得被动技能：痴情神通"
    ) is True


def test_daily_shuangxiu_can_resume_from_detail_scene(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
        ("current_scene", [216, 215, 69, 34], True),
        ("ocr_text", "frame"),
        ("invite",),
    ]


def test_daily_shuangxiu_remaining_zero_goes_to_finish(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {"asset_tree_path": Path("asset-tree.json"), "images": {}}
    stop_event = threading.Event()
    actions: list[tuple] = []

    class FakeRuntime:
        def current_scene(self, candidates, *, update: bool = False):
            actions.append(("current_scene", candidates, update))
            return None, 0.0, "frame"

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
        ("current_scene", [216, 215, 69, 34], True),
        ("ocr_text", "frame"),
        ("finish",),
    ]


def test_click_shape_respecting_conditions_raw_clicks_without_condition(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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


def test_runtime_shape_payload_scans_floating_image_without_ocr():
    runner = create_fanxiu_runtime_runner()
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


def test_auto_close_guard_images_use_only_top_level_popup_entries():
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
    normal_shape = {"id": "signup", "kind": "rect", "title": "报名", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}
    image = _image("报名", "0023.jpg", [normal_shape])
    calls: list[str] = []

    monkeypatch.setattr(runner, "_shape_score", lambda _ctx, _image, shape, _frame: calls.append(shape["title"]) or 90)

    assert runner._scene_score({"entry": object()}, image, "frame") == 0
    assert calls == []


def test_scene_score_uses_full_frame_similarity_only_for_layer3_without_identity(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    layer3_image = _image("素材模板", "0999.jpg")
    layer3_image["layer"] = 3
    layer2_image = _image("普通页面", "0100.jpg")
    layer2_image["layer"] = 2
    identified_image = _image("明确页面", "0101.jpg", [
        {"id": "identity", "kind": "rect", "title": "身份", "sceneIdentityRole": "required"},
    ])
    identified_image["layer"] = 3

    monkeypatch.setattr(runtime_runner_core, "_reference_frame_similarity", lambda *_args: 93.0)
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: 91.0)

    assert runner._scene_score({"entry": object()}, layer3_image, "frame") == 93.0
    assert runner._scene_score({"entry": object()}, layer2_image, "frame") == 0.0
    assert runner._scene_score({"entry": object()}, identified_image, "frame") == 91.0


def test_navigation_scene_id_maps_layer3_weak_child_to_parent_scene():
    runner = create_fanxiu_runtime_runner()
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
    parent["children"] = [weak_child, identified_child]
    ctx = {"asset_tree": [parent], "images": {34: parent, 68: weak_child, 100: identified_child}}

    assert runner._navigation_scene_id(ctx, 68) == 34
    assert runner._navigation_scene_id(ctx, 100) == 100


def test_navigation_scene_id_rejects_top_level_layer3_weak_scene():
    runner = create_fanxiu_runtime_runner()
    weak_root = _image("弱兜底素材", "0999.jpg")
    weak_root["layer"] = 3
    ctx = {"asset_tree": [weak_root], "images": {999: weak_root}}

    assert runner._navigation_scene_id(ctx, 999) is None


def test_identify_scene_number_for_route_uses_ordered_detect_candidates(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
    blank_shape = {"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}
    image = _image("所有提示窗口", "0047.jpg", [blank_shape])

    monkeypatch.setattr(runner, "_shape_score", lambda _ctx, _image, _shape, _frame, **_kwargs: 80)

    assert runner._popup_score({"entry": object()}, image, "frame") == 80


def test_scene_jump_edges_infer_nested_leave_returns_to_parent_scene():
    runner = create_fanxiu_runtime_runner()
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
    assert 85 in edges
    assert edges[85][0]["shape"] is leave_shape
    assert edges[85][0]["target_ids"] == [34]
    assert runner._find_scene_route(tree, 85, 34) == [edges[85][0]]


def test_scene_jump_edges_infer_world_menu_close_returns_to_world():
    runner = create_fanxiu_runtime_runner()
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

    assert 35 in edges
    assert edges[35][0]["shape"] is close_shape
    assert edges[35][0]["target_ids"] == [34]
    assert runner._find_scene_route(tree, 35, 34) == [edges[35][0]]


def test_go_scene_next_edge_prefers_observed_reachable_action_over_first_bfs_edge():
    runner = create_fanxiu_runtime_runner()
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
    assert route[0]["shape"] is low_confidence_shape

    decision = runner._select_scene_next_edge(tree, 1, 3)

    assert decision is not None
    assert decision["edge"]["shape"] is high_confidence_shape


def test_go_scene_next_edge_skips_failed_candidate_within_same_goto_round():
    runner = create_fanxiu_runtime_runner()
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
    assert second_decision["edge"]["shape"] is second_shape


def test_shape_score_uses_ocr_fallback_when_image_score_is_below_scene_threshold(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
        return {"similarity": 100 if ocr_enabled else 57}

    monkeypatch.setattr(runner, "_run_match", fake_run_match)

    assert runner._shape_score({"entry": object()}, image, shape, "frame") == 100
    assert calls == [False, True]


def test_shape_score_caches_missing_reference_image(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    shape = {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}
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
    runner = create_fanxiu_runtime_runner()
    shape = {"id": "blank", "kind": "rect", "title": "空白", "x": 0, "y": 0, "w": 1, "h": 1}
    image = _image("弹窗", "0047.png", [shape])
    calls: list[bool] = []

    def fake_run_match(_ctx, _image, _shape, _frame, **kwargs):
        calls.append(bool(kwargs.get("scan")))
        return {"similarity": 0, "matches": []}

    monkeypatch.setattr(runner, "_run_match", fake_run_match)

    assert runner._popup_score({"entry": object()}, image, "frame") == 0
    assert calls == [False]


def test_scene_score_requires_all_scene_identity_shapes(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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

    assert runner._scene_score({"entry": object()}, image, "frame") == 0
    assert calls == [True]


def test_scene_jump_intermediate_confirm_shape_is_limited_to_leave_popup():
    runner = create_fanxiu_runtime_runner()
    confirm = {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.6, "y": 0.6, "w": 0.1, "h": 0.05}
    leave_popup = _image("离开场景", "0086.png", [confirm])

    assert runner._scene_jump_intermediate_confirm_shape(leave_popup, {"title": "离开"}) is confirm
    assert runner._scene_jump_intermediate_confirm_shape(leave_popup, {"title": "退出"}) is confirm
    assert runner._scene_jump_intermediate_confirm_shape(leave_popup, {"title": "领取"}) is None
    assert runner._scene_jump_intermediate_confirm_shape(_image("奖励提示", "0099.png", [confirm]), {"title": "离开"}) is None


def test_scene_route_candidates_include_leave_confirmation_popup():
    runner = create_fanxiu_runtime_runner()
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


def test_scene_jump_20_to_34_unknown_clicks_world_blank_once(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    back_shape = {"id": "back", "kind": "rect", "title": "回到世界", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1}
    blank_shape = {"id": "blank", "kind": "rect", "title": "空白", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.1}
    image20 = _image("绿瓶", "0020.png", [back_shape])
    image34 = _image("世界", "0034.png", [blank_shape])
    tree = [image20, image34]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": {20: image20, 34: image34}}
    frames = iter(["unknown-popup", "world"])
    clicked: list[tuple[str, float, float]] = []

    class FakeStopEvent:
        def is_set(self):
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
    ))

    assert result == 34
    assert clicked == [("世界", 270.0, 560.0)]
    assert any("#34「空白」" in log["message"] for log in runner.status()["logs"])


def test_auto_close_guard_tick_clicks_first_matching_blank_shape(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    blank_shape = {"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1}
    nested_shape = {"id": "nested", "kind": "rect", "title": "空白", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.1, "sceneJumpTarget": "-1"}
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
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, _frame=None, **_kwargs: clicked.append((image["title"], shape["title"])))

    assert runner._auto_close_popup_guard_step(runner._fanxiu_runtime({"entry": object()}, path, "data:image/png;base64,frame"))
    assert clicked == [("所有提示窗口", "空白")]


def test_auto_close_guard_requires_high_confidence_during_task(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    blank_shape = {"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1}
    tree = [{
        "type": "folder",
        "title": "弹窗",
        "children": [_image("所有提示窗口", "0047.jpg", [blank_shape])],
    }]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree), encoding="utf-8")

    clicked: list[tuple[str, str]] = []
    monkeypatch.setattr(runner, "_popup_score", lambda _ctx, image, _frame: 70 if image["title"] == "所有提示窗口" else 0)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, _frame=None, **_kwargs: clicked.append((image["title"], shape["title"])))

    with runner._lock:
        runner._status.update({"running": True, "phase": "wait_shape_match"})

    assert not runner._auto_close_popup_guard_step(runner._fanxiu_runtime({"entry": object()}, path, "data:image/png;base64,frame"))
    assert clicked == []


def test_auto_close_guard_clicks_popup_blank_without_jump_target(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    tree = [{
        "type": "folder",
        "title": "弹窗",
        "children": [
            _image("封魔杀", "0059.png", [{"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}]),
        ],
    }]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree), encoding="utf-8")

    clicked: list[str] = []
    monkeypatch.setattr(runner, "_popup_score", lambda _ctx, image, _frame: 100 if image["title"] == "封魔杀" else 0)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, _frame=None, **_kwargs: clicked.append(shape["title"]))

    assert runner._auto_close_popup_guard_step(runner._fanxiu_runtime({"entry": object()}, path, "data:image/png;base64,frame"))
    assert clicked == ["空白"]
    assert runner.status()["last_guard_event"]["action"] == "click:空白"


def test_auto_close_guard_handles_disconnect_reconnect_before_generic_popup(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps([{
        "type": "folder",
        "title": "弹窗",
        "children": [_image("所有提示窗口", "0047.jpg", [{"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1}])],
    }]), encoding="utf-8")

    lines = [
        {"text": "断线重连", "x": 120, "y": 320, "w": 180, "h": 48},
        {"text": "当前网络已断开，请重新登录", "x": 180, "y": 560, "w": 540, "h": 44},
        {"text": "重新登录", "x": 170, "y": 1120, "w": 160, "h": 60},
        {"text": "重连", "x": 560, "y": 1120, "w": 110, "h": 60},
    ]
    clicked: list[tuple[float, float]] = []

    monkeypatch.setattr(runner, "_cached_ocr_lines", lambda _ctx, _frame: lines)
    monkeypatch.setattr(runner, "_popup_score", lambda *_args, **_kwargs: pytest.fail("断线弹窗应先于通用弹窗匹配处理"))
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))

    assert runner._auto_close_popup_guard_step(runner._fanxiu_runtime({"entry": object()}, path, _png_data_url()))

    assert clicked == [(615.0, 1150.0)]
    event = runner.status()["last_guard_event"]
    assert event["kind"] == "network_disconnect"
    assert event["action"] == "click:重连"


def test_auto_close_guard_disconnect_reconnect_uses_layout_fallback_when_button_ocr_missing(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    lines = [
        {"text": "断线重连", "x": 120, "y": 320, "w": 180, "h": 48},
        {"text": "当前网络已断开，请重新登录", "x": 180, "y": 560, "w": 540, "h": 44},
    ]
    clicked: list[tuple[float, float]] = []

    monkeypatch.setattr(runner, "_cached_ocr_lines", lambda _ctx, _frame: lines)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))

    assert runner._auto_close_popup_guard_step(runner._fanxiu_runtime({"entry": object()}, path, _png_data_url()))

    assert clicked == [(594.0, 1056.0)]
    assert runner.status()["last_guard_event"]["button_source"] == "layout_fallback"


def test_auto_close_guard_disconnect_reconnect_stops_after_reconnect_limit(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps([{
        "type": "folder",
        "title": "弹窗",
        "children": [_image("所有提示窗口", "0047.jpg", [{"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1}])],
    }]), encoding="utf-8")
    lines = [
        {"text": "断线重连", "x": 120, "y": 320, "w": 180, "h": 48},
        {"text": "当前网络已断开，请重新登录", "x": 180, "y": 560, "w": 540, "h": 44},
        {"text": "重连", "x": 560, "y": 1120, "w": 110, "h": 60},
    ]
    clicked: list[tuple[float, float]] = []

    monkeypatch.setattr(runner, "_cached_ocr_lines", lambda _ctx, _frame: lines)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))
    monkeypatch.setattr(runner, "_popup_score", lambda *_args, **_kwargs: pytest.fail("断线重连达到上限后不应继续通用弹窗匹配"))

    def run_guard_tick() -> bool:
        runtime = runner._fanxiu_runtime({"entry": object()}, path, _png_data_url())
        return runner._auto_close_popup_guard_step(runtime)

    assert run_guard_tick()
    assert run_guard_tick()
    assert run_guard_tick()
    assert not run_guard_tick()

    assert clicked == [(615.0, 1150.0), (615.0, 1150.0), (615.0, 1150.0)]
    event = runner.status()["last_guard_event"]
    assert event["action"] == "device_recovery_required"
    assert event["attempts"] == 3
    assert runner.status()["network_disconnect_reconnect_blocked"] is True


def test_auto_close_guard_candidates_are_cached_by_asset_tree_signature(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
    blank_shape = {"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1, "sceneJumpTarget": "-1"}
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
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, _frame=None, **_kwargs: clicked.append((image["title"], shape["title"])))

    assert runner._auto_close_popup_guard_step(runner._fanxiu_runtime({"entry": object()}, path, "data:image/png;base64,frame"))
    assert clicked == [("自动切磋", "不再提示")]


def test_auto_close_guard_popup_47_child_84_checked_clicks_confirm(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    no_more_prompt_shape = {"id": "no-more", "kind": "rect", "title": "不再提示", "x": 0.3, "y": 0.5, "w": 0.1, "h": 0.1}
    confirm_shape = {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1}
    child_84 = _image("自动切磋", "0084.png", [
        {"id": "identity", "kind": "rect", "title": "切磋已满30次", "isSceneIdentity": True, "x": 0.2, "y": 0.4, "w": 0.5, "h": 0.1},
        no_more_prompt_shape,
        confirm_shape,
    ])
    popup_47 = _image("所有提示窗口", "0047.jpg", [{"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1, "sceneJumpTarget": "-1"}])
    popup_47["children"] = [child_84]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps([{"type": "folder", "title": "弹窗", "children": [popup_47]}]), encoding="utf-8")

    clicked: list[tuple[str, str]] = []
    monkeypatch.setattr(runner, "_popup_score", lambda _ctx, image, _frame: 70 if image["title"] in {"所有提示窗口", "自动切磋"} else 0)
    monkeypatch.setattr(runner, "_shape_score", lambda _ctx, _image, _shape, _frame: 70)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, _frame=None, **_kwargs: clicked.append((image["title"], shape["title"])))
    monkeypatch.setattr("backend.api.fanxiu.time.sleep", lambda _seconds: None)

    assert runner._auto_close_popup_guard_step(runner._fanxiu_runtime({"entry": object()}, path, "data:image/png;base64,frame"))
    assert clicked == [("自动切磋", "确认")]


def test_auto_close_guard_popup_47_child_84_can_avoid_confirm_for_mail_wait(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    no_more_prompt_shape = {"id": "no-more", "kind": "rect", "title": "不再提示", "x": 0.3, "y": 0.5, "w": 0.1, "h": 0.1}
    confirm_shape = {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1}
    child_84 = _image("自动切磋", "0084.png", [
        {"id": "identity", "kind": "rect", "title": "切磋已满30次", "isSceneIdentity": True, "x": 0.2, "y": 0.4, "w": 0.5, "h": 0.1},
        no_more_prompt_shape,
        confirm_shape,
    ])
    popup_47 = _image("所有提示窗口", "0047.jpg", [{"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1, "sceneJumpTarget": "-1"}])
    popup_47["children"] = [child_84]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps([{"type": "folder", "title": "弹窗", "children": [popup_47]}]), encoding="utf-8")

    clicked: list[tuple[str, str]] = []
    monkeypatch.setattr(runner, "_popup_score", lambda _ctx, image, _frame: 70 if image["title"] in {"所有提示窗口", "自动切磋"} else 0)
    monkeypatch.setattr(runner, "_shape_score", lambda _ctx, _image, _shape, _frame: 70)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, _frame=None, **_kwargs: clicked.append((image["title"], shape["title"])))

    assert runner._auto_close_popup_guard_step(
        runner._fanxiu_runtime({"entry": object()}, path, "data:image/png;base64,frame"),
        allow_confirm_actions=False,
    )
    assert clicked == [("所有提示窗口", "空白")]


def test_auto_close_guard_popup_47_child_86_clicks_confirm(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    confirm_shape = {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1}
    child_86 = _image("离开场景", "0086.png", [
        {"id": "identity", "kind": "rect", "title": "离开场景", "isSceneIdentity": True, "x": 0.2, "y": 0.4, "w": 0.5, "h": 0.1},
        confirm_shape,
    ])
    popup_47 = _image("所有提示窗口", "0047.jpg", [{"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1, "sceneJumpTarget": "-1"}])
    popup_47["children"] = [child_86]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps([{"type": "folder", "title": "弹窗", "children": [popup_47]}]), encoding="utf-8")

    clicked: list[tuple[str, str]] = []
    monkeypatch.setattr(runner, "_popup_score", lambda _ctx, image, _frame: 70 if image["title"] in {"所有提示窗口", "离开场景"} else 0)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, _frame=None, **_kwargs: clicked.append((image["title"], shape["title"])))

    assert runner._auto_close_popup_guard_step(runner._fanxiu_runtime({"entry": object()}, path, "data:image/png;base64,frame"))
    assert clicked == [("离开场景", "确认")]


def test_auto_close_guard_popup_47_child_skips_business_only_confirm(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    business_confirm = {
        "id": "confirm",
        "kind": "rect",
        "title": "确认",
        "description": "确认一键同游。只允许同游传道助手闭环内点击，不作为通用弹窗守护动作。",
        "x": 0.54,
        "y": 0.638,
        "w": 0.28,
        "h": 0.055,
    }
    child_210 = _image("同游传道确认提示", "0210.png", [
        {"id": "identity", "kind": "rect", "title": "同游传道确认标识", "isSceneIdentity": True, "x": 0.18, "y": 0.43, "w": 0.67, "h": 0.075},
        business_confirm,
    ])
    popup_47 = _image("所有提示窗口", "0047.jpg", [{"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1, "sceneJumpTarget": "-1"}])
    popup_47["children"] = [child_210]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps([{"type": "folder", "title": "弹窗", "children": [popup_47]}]), encoding="utf-8")

    clicked: list[tuple[str, str]] = []
    monkeypatch.setattr(runner, "_popup_score", lambda _ctx, image, _frame: 100 if image["title"] in {"所有提示窗口", "同游传道确认提示"} else 0)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, _frame=None, **_kwargs: clicked.append((image["title"], shape["title"])))

    assert runner._auto_close_popup_guard_step(runner._fanxiu_runtime({"entry": object()}, path, "data:image/png;base64,frame"))
    assert clicked == []


def test_auto_close_guard_popup_47_uses_best_child_before_special_confirm(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    event_blank = {"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}
    event_popup = _image("洗灵活动提示", "0032.png", [
        {"id": "identity", "kind": "rect", "title": "洗灵证武", "isSceneIdentity": True, "x": 0.2, "y": 0.4, "w": 0.5, "h": 0.1},
        event_blank,
    ])
    leave_popup = _image("离开场景", "0086.png", [
        {"id": "identity", "kind": "rect", "title": "离开场景", "isSceneIdentity": True, "x": 0.2, "y": 0.4, "w": 0.5, "h": 0.1},
        {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1},
    ])
    popup_47 = _image("所有提示窗口", "0047.jpg", [{"id": "parent-blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1}])
    popup_47["children"] = [event_popup, leave_popup]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps([{"type": "folder", "title": "弹窗", "children": [popup_47]}]), encoding="utf-8")

    clicked: list[tuple[str, str]] = []

    def popup_score(_ctx, image, _frame):
        return {"所有提示窗口": 100, "洗灵活动提示": 90, "离开场景": 70}.get(image["title"], 0)

    monkeypatch.setattr(runner, "_popup_score", popup_score)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, shape, _frame=None, **_kwargs: clicked.append((image["title"], shape["title"])))

    assert runner._auto_close_popup_guard_step(runner._fanxiu_runtime({"entry": object()}, path, "data:image/png;base64,frame"))
    assert clicked == [("洗灵活动提示", "空白")]


def test_wait_mail_detail_closes_popup_and_keeps_waiting(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {
        "entry": object(),
        "asset_tree_path": path,
        "images": {
            121: _image("邮件", "0121.png"),
            122: _image("邮件内容", "0122.png"),
            123: _image("邮件内容", "0123.png"),
        },
    }
    scene_results = iter([(None, 0.0), (122, 100.0)])
    closed: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

        def wait(self, _seconds):
            return False

        def wait(self, _seconds):
            return False

        def wait(self, _seconds):
            return False

        def wait(self, _seconds):
            return False

        def wait(self, _seconds):
            return False

        def wait(self, _seconds):
            return False

    frame_data_url = _png_data_url()
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: frame_data_url)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: next(scene_results))
    monkeypatch.setattr(runner, "_close_mail_wait_popup_once", lambda _ctx, _frame: closed.append("popup") or True if not closed else False)

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_mail_detail_or_list_scene(ctx, FakeStopEvent(), timeout=5.0, label="等待详情"),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == (122, 100.0)
    assert closed == ["popup"]


def test_runtime_behavior_tree_popup_84_uses_separate_ticks_for_checkbox_and_confirm(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    no_more_prompt_shape = {"id": "no-more", "kind": "rect", "title": "不再提示", "x": 0.3, "y": 0.5, "w": 0.1, "h": 0.1}
    confirm_shape = {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1}
    child_84 = _image("自动切磋", "0084.png", [
        {"id": "identity", "kind": "rect", "title": "切磋已满30次", "isSceneIdentity": True, "x": 0.2, "y": 0.4, "w": 0.5, "h": 0.1},
        no_more_prompt_shape,
        confirm_shape,
    ])
    popup_47 = _image("所有提示窗口", "0047.jpg", [{"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1, "sceneJumpTarget": "-1"}])
    popup_47["children"] = [child_84]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps([{"type": "folder", "title": "弹窗", "children": [popup_47]}]), encoding="utf-8")
    ctx = {"entry": object()}
    captured: list[str] = []
    clicked: list[tuple[str, str, str]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _timeout):
            return False

        def wait(self, _timeout):
            return False

        def wait(self, _seconds):
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

    def click_shape(_ctx, image, shape, frame=None, **_kwargs):
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
        ("自动切磋", "确认", "frame1"),
    ]
    assert captured == ["frame0", "frame1", "frame2"]


def test_runtime_behavior_tree_reuses_guard_frame_for_job_when_guard_skips(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", lambda _runtime: False)

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


def test_runtime_guard_service_skips_manual_jobs(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    with runner._lock:
        runner._guard_enabled = True
        runner._status["phase"] = "manual_job"
    calls: list[str] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: calls.append("screencap") or "frame")
    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", lambda *_args: calls.append("guard") or True)

    status = runner._runtime_guard_service_tick(
        "close_popups",
        {"entry": object()},
        tmp_path / "entry.json",
        threading.Event(),
    )

    assert status == BehaviorTreeStatus.SKIP
    assert calls == []


def test_runtime_wait_view_timeout_is_not_deferred_by_popup_guard(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image121 = _image("邮件", "0121.png", [])
    ctx = {"entry": object(), "images": {121: image121}}
    runtime = runner._fanxiu_runtime(ctx, frame_data_url="frame")
    guard_calls: list[str] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (None, 0.0))
    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", lambda _runtime: guard_calls.append("guard") or True)

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

    assert guard_calls == []


def test_runtime_behavior_tree_runs_guard_before_job_and_skips_job_when_handled(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
        runner._status["running"] = True
        runner._status["phase"] = "manual_job"

    def capture_frame(_ctx):
        frame = f"data:image/png;base64,frame{len(captured)}"
        captured.append(frame)
        return frame

    def guard_tick(runtime):
        events.append(("guard", runtime.cur_frame()))
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
    runner = create_fanxiu_runtime_runner()
    shapes = {
        title: {"id": title, "kind": "rect", "title": "空白", "x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1, "sceneJumpTarget": "-1"}
        for title in ("图1", "图2", "图3", "图4", "图5")
    }
    tree = [{"type": "folder", "title": "弹窗", "children": [_image(title, f"{index:04d}.jpg", [shapes[title]]) for index, title in enumerate(shapes, start=1)]}]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree), encoding="utf-8")
    ctx = {"entry": object()}
    actual_order = ["图3", "图2", "图4", "图1"]
    clicked: list[tuple[str, str | None]] = []
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
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, _shape, _frame=None, **_kwargs: clicked.append(image["title"]))
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
    runner = create_fanxiu_runtime_runner()
    tree = [{
        "type": "folder",
        "title": "弹窗",
        "children": [
            _image("图1", "0001.jpg", [{"id": "s1", "kind": "rect", "title": "空白", "x": 0, "y": 0, "w": 1, "h": 1, "sceneJumpTarget": "-1"}]),
            _image("图2", "0002.jpg", [{"id": "s2", "kind": "rect", "title": "空白", "x": 0, "y": 0, "w": 1, "h": 1, "sceneJumpTarget": "-1"}]),
            _image("图3", "0003.jpg", [{"id": "s3", "kind": "rect", "title": "空白", "x": 0, "y": 0, "w": 1, "h": 1, "sceneJumpTarget": "-1"}]),
        ],
    }]
    path = tmp_path / "asset_tree.json"
    path.write_text(json.dumps(tree), encoding="utf-8")
    clicked: list[tuple[str, str | None]] = []

    def score(_ctx, image, _frame):
        return {"图1": 10, "图2": 75, "图3": 90}[image["title"]]

    monkeypatch.setattr(runner, "_popup_score", score)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, image, _shape, _frame=None, **_kwargs: clicked.append(image["title"]))

    assert runner._auto_close_popup_guard_step(runner._fanxiu_runtime({"entry": object()}, path, "frame"))
    assert clicked == ["图2"]


def test_popup_guard_parallel_scoring_checks_all_candidates_in_one_tick(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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


def test_popup_guard_parallel_scoring_uses_one_worker_per_candidate_up_to_cap(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    candidates = [{"image": _image(f"图{index}", f"{index:04d}.jpg")} for index in range(12)]
    created_workers: list[int] = []
    real_executor = popup_guard_core.ThreadPoolExecutor

    class SpyExecutor(real_executor):
        def __init__(self, *args, **kwargs):
            created_workers.append(int(kwargs.get("max_workers") or args[0]))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(popup_guard_core, "ThreadPoolExecutor", SpyExecutor)
    monkeypatch.setattr(runner, "_popup_score", lambda _ctx, image, _frame: 70 if image["title"] == "图11" else 0)

    scores = runner._auto_close_popup_candidate_scores_parallel({"entry": object()}, candidates, "frame")

    assert created_workers == [len(candidates)]
    assert scores == [0] * 11 + [70]


def test_popup_guard_parallel_scoring_caps_worker_count(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    candidates = [{"image": _image(f"图{index}", f"{index:04d}.jpg")} for index in range(40)]
    created_workers: list[int] = []
    real_executor = popup_guard_core.ThreadPoolExecutor

    class SpyExecutor(real_executor):
        def __init__(self, *args, **kwargs):
            created_workers.append(int(kwargs.get("max_workers") or args[0]))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(popup_guard_core, "ThreadPoolExecutor", SpyExecutor)
    monkeypatch.setattr(runner, "_popup_score", lambda _ctx, image, _frame: 70 if image["title"] == "图39" else 0)

    scores = runner._auto_close_popup_candidate_scores_parallel({"entry": object()}, candidates, "frame")

    assert created_workers == [32]
    assert scores == [0] * 39 + [70]


def test_popup_guard_parallel_scoring_uses_shape_ocr_without_full_frame_prefetch(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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

    def fake_ocr_lines(frame):
        ocr_calls.append(frame)
        return [{"text": "目标弹窗", "x": 100, "y": 170, "w": 120, "h": 30}]

    monkeypatch.setattr(runner, "_ocr_lines", fake_ocr_lines)
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
    assert ocr_calls == []
    assert sorted(run_match_calls) == [("图0", True), ("图1", True), ("图2", True)]


def test_popup_guard_first_match_scores_candidates_once(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    candidates = [{"image": _image(f"图{index}", f"{index:04d}.jpg")} for index in range(3)]
    calls: list[int] = []

    def fake_scores(_ctx, score_candidates, _frame):
        calls.append(len(score_candidates))
        return [0.0, 100.0, 100.0]

    monkeypatch.setattr(runner, "_auto_close_popup_candidate_scores_parallel", fake_scores)

    candidate, score = runner._auto_close_popup_first_match({"entry": object()}, candidates, "frame")

    assert candidate is candidates[1]
    assert score == 100
    assert calls == [3]


def test_scene_number_uses_shape_ocr_without_full_frame_prefetch(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image34 = _image(
        "世界",
        "0034.jpg",
        [
                {
                    "id": "world-title",
                    "kind": "rect",
                    "title": "大地图",
                    "isSceneIdentity": True,
                    "sceneIdentityScope": "global",
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
                    "sceneIdentityScope": "global",
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

    def fake_ocr_lines(frame):
        ocr_calls.append(frame)
        return [{"text": "大地图", "x": 100, "y": 170, "w": 120, "h": 30}]

    monkeypatch.setattr(runner, "_ocr_lines", fake_ocr_lines)
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
    assert ocr_calls == []
    assert run_match_calls == [("世界", True), ("菜单", True)]


def test_popup_guard_parallel_scoring_is_faster_than_serial_for_independent_candidates(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    candidates = [{"image": _image(f"图{index}", f"{index:04d}.jpg")} for index in range(12)]
    ctx = {"entry": object()}
    score_delay = 0.01
    rounds = 2

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
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")

    with runner._lock:
        runner._guard_enabled = True
        runner._guard_items["wanling_invite"] = {"enabled": False}

    container = _DataAnnotationRuntimeContainer(
        runner,
        runtime_ctx={"entry": object()},
        asset_tree_path=path,
        stop_event=threading.Event(),
    )

    specs = {spec.node_id: spec.enabled for spec in container.guard_specs()}
    assert specs == {"device_health": True, "close_popups": True, "wanling_invite": False}


def test_runtime_guard_defaults_enable_close_popups_only(tmp_path):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")

    with runner._lock:
        runner._sync_guard_status_locked()

    status = runner.status()
    assert status["guard_enabled"] is True
    assert status["guard_items"]["device_health"]["enabled"] is True
    assert status["guard_items"]["close_popups"]["enabled"] is True
    assert status["guard_items"]["wanling_invite"]["enabled"] is False


def test_runtime_guard_group_switch_disables_execution_without_clearing_items(tmp_path):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")

    with runner._lock:
        runner._guard_group_enabled = False
        runner._guard_enabled = True
        runner._guard_items["wanling_invite"] = {"enabled": True}
        runner._sync_guard_status_locked()

    status = runner.status()
    assert status["guard_group_enabled"] is False
    assert status["guard_items"]["close_popups"]["enabled"] is True
    assert status["guard_items"]["wanling_invite"]["enabled"] is True
    assert runner._runtime_guard_enabled("close_popups") is False
    assert runner._runtime_guard_enabled("wanling_invite") is False

    container = _DataAnnotationRuntimeContainer(
        runner,
        runtime_ctx={"entry": object()},
        asset_tree_path=path,
        stop_event=threading.Event(),
    )
    specs = {spec.node_id: spec.enabled for spec in container.guard_specs()}
    assert specs == {"device_health": False, "close_popups": False, "wanling_invite": False}


def test_runtime_container_groups_are_prioritized_and_non_preemptive(tmp_path):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")

    container = _DataAnnotationRuntimeContainer(
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


def test_manual_jobs_are_persisted_as_manual_group(tmp_path, monkeypatch):
    path = tmp_path / "manual_jobs.json"
    monkeypatch.setattr("backend.api.fanxiu._data_annotation_manual_job_state_path", lambda: path)

    job = _enqueue_data_annotation_manual_job("detect_scene", {"x": 1}, label="单步识别")
    popped = _pop_next_data_annotation_manual_job()

    assert job["group"] == "manual_job"
    assert "priority" not in job
    assert popped is not None
    assert popped["id"] == job["id"]
    assert popped["task_type"] == "detect_scene"
    assert popped["payload"] == {"x": 1}
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted[0]["id"] == job["id"]
    assert persisted[0]["status"] == "running"

    _remove_data_annotation_manual_job(job["id"])

    assert _pop_next_data_annotation_manual_job() is None


def test_debug_eval_manual_job_is_registered_as_manual_group(tmp_path, monkeypatch):
    path = tmp_path / "manual_jobs.json"
    monkeypatch.setattr("backend.api.fanxiu._data_annotation_manual_job_state_path", lambda: path)

    job = _enqueue_data_annotation_manual_job("debug_eval", {"code": "result = 'ok'"})

    assert job["group"] == "manual_job"
    assert job["task_type"] == "debug_eval"
    assert job["label"] == "调试代码"
    assert job["payload"]["mode"] == "readonly"
    assert job["payload"]["timeout_seconds"] == 120


def test_debug_eval_handler_runs_simple_context_code():
    definition = fanxiu_api._data_annotation_manual_job_definition("debug_eval")
    runner = create_fanxiu_runtime_runner()

    result = definition.handler(
        runner,
        {"images": {}, "entry": object()},
        {"code": "ctx.log({'ok': True}); result = 'done'"},
        threading.Event(),
    )

    assert result == "success"
    assert any("debug_eval result" in str(item.get("message") or "") for item in runner.status().get("logs") or [])


def test_debug_eval_readonly_blocks_actions():
    definition = fanxiu_api._data_annotation_manual_job_definition("debug_eval")
    runner = create_fanxiu_runtime_runner()

    try:
        definition.handler(
            runner,
            {"images": {}, "entry": object()},
            {"code": "ctx.tap(34, 1, 1)"},
            threading.Event(),
        )
    except RuntimeError as exc:
        assert "readonly" in str(exc)
    else:
        raise AssertionError("debug_eval readonly action should fail")


def test_debug_eval_context_shape_score_is_readonly_probe():
    from backend.core.fanxiu.data_annotation.debug_eval import DataAnnotationRuntimeDebugContext

    image = _image("法则之主", "0265.png", [{"title": "返回", "x": 0.1, "y": 0.8, "w": 0.1, "h": 0.1}])

    class FakeRunner:
        def _raise_if_stopped(self, _stop_event):
            return None

        def _find_shape(self, image, title, *, contains=False):
            return next(shape for shape in image["shapes"] if shape["title"] == title)

        def _shape_score(self, ctx, image, shape, frame):
            return 79.0

    ctx = DataAnnotationRuntimeDebugContext(FakeRunner(), {"images": {265: image}}, threading.Event())

    assert ctx.shape_score(265, "返回", frame=_png_data_url()) == 79.0


def test_debug_eval_context_exposes_ocr_words_in_shapes():
    from backend.core.fanxiu.data_annotation.debug_eval import DataAnnotationRuntimeDebugContext

    calls: list[tuple] = []

    class FakeRuntime:
        def ocr_words_in_shapes(self, scene, shape_titles, *, frame_data_url=None, padding=0, options=None):
            calls.append((scene, shape_titles, frame_data_url, padding, options))
            return [{"text": "魔道", "x": 700.0, "y": 760.0, "w": 60.0, "h": 32.0}]

    class FakeRunner:
        def _raise_if_stopped(self, _stop_event):
            return None

        def _fanxiu_runtime(self, _ctx, *, stop_event=None):
            return FakeRuntime()

    ctx = DataAnnotationRuntimeDebugContext(FakeRunner(), {}, threading.Event())
    frame = _png_data_url()
    result = ctx.ocr_words_in_shapes(
        265,
        ["识别区"],
        frame=frame,
        padding=4,
        options={"return_word_box": True},
    )

    assert result == [{"text": "魔道", "x": 700.0, "y": 760.0, "w": 60.0, "h": 32.0}]
    assert calls == [(265, ("识别区",), frame, 4, {"return_word_box": True})]


def test_debug_eval_context_shape_probe_reports_score_and_shape_metadata():
    from backend.core.fanxiu.data_annotation.debug_eval import DataAnnotationRuntimeDebugContext

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

    ctx = DataAnnotationRuntimeDebugContext(FakeRunner(), {"images": {265: image}}, threading.Event())

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
    from backend.core.fanxiu.data_annotation.debug_eval import DataAnnotationRuntimeDebugContext

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

    ctx = DataAnnotationRuntimeDebugContext(FakeRunner(), {}, threading.Event(), readonly=False)
    result = _drain_generator(ctx.wait_click_then_view(265, "返回", 264, timeout=20.0, label="probe"))

    assert result == "ok"
    assert calls == [((265, "返回", 264), {"timeout": 20.0, "label": "probe"})]


def test_manual_job_pop_ignores_stale_running_until_requeued(tmp_path, monkeypatch):
    path = tmp_path / "manual_jobs.json"
    monkeypatch.setattr("backend.api.fanxiu._data_annotation_manual_job_state_path", lambda: path)
    path.write_text(json.dumps([
        {
            "id": "running-job",
            "task_type": "detect_scene",
            "label": "旧运行作业",
            "status": "running",
            "priority": 1,
            "created_at": 10.0,
            "updated_at": 10.0,
        },
        {
            "id": "pending-job",
            "task_type": "detect_scene",
            "label": "新排队作业",
            "status": "pending",
            "priority": 50,
            "created_at": 20.0,
            "updated_at": 20.0,
        },
    ], ensure_ascii=False), encoding="utf-8")

    popped = _pop_next_data_annotation_manual_job()
    assert popped is not None
    assert popped["id"] == "pending-job"

    jobs = json.loads(path.read_text(encoding="utf-8"))
    assert [job["status"] for job in jobs] == ["running", "running"]


def test_manual_job_requeue_running_resets_stale_jobs_to_fifo_queue(tmp_path, monkeypatch):
    path = tmp_path / "manual_jobs.json"
    monkeypatch.setattr("backend.api.fanxiu._data_annotation_manual_job_state_path", lambda: path)
    path.write_text(json.dumps([
        {
            "id": "running-old",
            "task_type": "detect_scene",
            "label": "旧运行作业",
            "status": "running",
            "priority": 1,
            "created_at": 10.0,
            "updated_at": 10.0,
        },
        {
            "id": "pending-new",
            "task_type": "detect_scene",
            "label": "新排队作业",
            "status": "pending",
            "priority": 50,
            "created_at": 20.0,
            "updated_at": 20.0,
        },
    ], ensure_ascii=False), encoding="utf-8")

    assert _requeue_running_data_annotation_manual_jobs() == 1
    popped = _pop_next_data_annotation_manual_job()

    assert popped is not None
    assert popped["id"] == "running-old"
    jobs = json.loads(path.read_text(encoding="utf-8"))
    assert jobs[0]["status"] == "running"
    assert jobs[1]["status"] == "pending"


def test_manual_job_queue_orders_by_group_then_created_at(tmp_path, monkeypatch):
    path = tmp_path / "manual_jobs.json"
    monkeypatch.setattr("backend.api.fanxiu._data_annotation_manual_job_state_path", lambda: path)
    path.write_text(json.dumps([
        {
            "id": "manual-newer-high-priority",
            "task_type": "detect_scene",
            "label": "高优先级但后提交",
            "group": "manual_job",
            "status": "pending",
            "priority": 1,
            "created_at": 20.0,
            "updated_at": 20.0,
        },
        {
            "id": "manual-older-low-priority",
            "task_type": "detect_scene",
            "label": "低优先级但先提交",
            "group": "manual_job",
            "status": "pending",
            "priority": 99,
            "created_at": 10.0,
            "updated_at": 10.0,
        },
    ], ensure_ascii=False), encoding="utf-8")

    popped = _pop_next_data_annotation_manual_job()

    assert popped is not None
    assert popped["id"] == "manual-older-low-priority"


def test_runtime_scene_candidates_use_layer_queue_roots_without_context():
    runner = create_fanxiu_runtime_runner()
    image20 = _image("绿瓶", "0020.png", [
        {"id": "green-bottle-id", "kind": "rect", "title": "绿瓶", "sceneIdentityRole": "required", "sceneIdentityScope": "global"},
    ])
    image20["layer"] = 1
    image34 = _image("世界", "0034.png", [
        {"id": "world-id", "kind": "rect", "title": "世界标识", "sceneIdentityRole": "required", "sceneIdentityScope": "global"},
    ])
    image34["layer"] = 1
    image35 = _image("世界下方菜单", "0035.png", [
        {"id": "menu-id", "kind": "rect", "title": "下方菜单标识", "sceneIdentityRole": "required", "sceneIdentityScope": "global"},
    ])
    image35["layer"] = 2
    image47 = _image("提示", "0047.png")
    image47["layer"] = 1
    image86 = _image("离开提示", "0086.png")
    image86["layer"] = 1
    image278 = _image("邮件删除提示", "0278.png")
    image278["layer"] = 3
    image198 = _image("仙缘人物详情", "0198.png", [
        {"id": "local-id", "kind": "rect", "title": "身份", "sceneIdentityRole": "required", "sceneIdentityScope": "local"},
    ])
    image198["layer"] = 2
    image199 = _image("素材模板", "0199.png", [
        {"id": "template-id", "kind": "rect", "title": "素材", "sceneIdentityRole": "required", "sceneIdentityScope": "local"},
    ])
    image199["layer"] = 3
    image204 = _image("小助手清单", "0204.png", [
        {"id": "assistant-id", "kind": "rect", "title": "小助手清单标识", "sceneIdentityRole": "required", "sceneIdentityScope": "global"},
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

    assert runner._runtime_scene_candidate_ids(ctx) == [20, 34, 47, 86, 204, 198, 199]
    assert runner._runtime_popup_scene_candidate_ids(ctx) == [47, 86]


def test_identify_scene_number_without_context_checks_popup_root_but_not_nested_children(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image34 = _image("世界", "0034.png", [
        {"id": "world-id", "kind": "rect", "title": "世界标识", "sceneIdentityRole": "required", "sceneIdentityScope": "global"},
    ])
    image34["layer"] = 1
    image47 = _image("提示", "0047.png")
    image47["layer"] = 1
    image204 = _image("小助手清单", "0204.png", [
        {"id": "assistant-id", "kind": "rect", "title": "小助手清单标识", "sceneIdentityRole": "required", "sceneIdentityScope": "global"},
    ])
    image204["layer"] = 1
    tree = [
        {"type": "folder", "title": "世界", "children": [image34]},
        {"type": "folder", "title": "弹窗", "children": [image47]},
        {"type": "folder", "title": "日常", "children": [image204]},
    ]
    ctx = {"asset_tree": tree, "images": {34: image34, 47: image47, 204: image204}}
    scanned: list[list[int] | None] = []

    class FakeRecognizer:
        def identify_scene_number(self, _ctx, _frame, *, preferred_scene_ids=None):
            scanned.append(list(preferred_scene_ids) if preferred_scene_ids is not None else None)
            if preferred_scene_ids == [47]:
                return 47, 92.0
            return None, 0.0

        def identify_scene_tree_number(self, _ctx, _frame, *, preferred_scene_ids=None):
            scanned.append(list(preferred_scene_ids) if preferred_scene_ids is not None else None)
            if preferred_scene_ids and 47 in preferred_scene_ids:
                return 47, 92.0
            return None, 0.0

    monkeypatch.setattr(runner, "_scene_recognizer", lambda: FakeRecognizer())

    assert runner._identify_scene_number(ctx, "frame") == (47, 92.0)
    assert scanned == [[34, 47, 204]]
    assert runner._identify_scene_number(ctx, "frame", [47]) == (47, 92.0)
    assert scanned == [[34, 47, 204], [47]]


def test_identify_scene_number_directly_checks_explicit_nested_candidate(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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

    assert runner._identify_scene_number(ctx, "frame") == (None, 40.0)
    assert runner._identify_scene_number(ctx, "frame", [278]) == (278, 100.0)


def test_runtime_container_skips_disabled_guards(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", lambda _runtime: events.append("guard") or False)

    result = runner._run_runtime_behavior_tree(
        runtime_ctx=ctx,
        asset_tree_path=path,
        stop_event=FakeStopEvent(),
        action=lambda: events.append("job") or "done",
        label="测试作业",
    )

    assert result == "done"
    assert events == ["job"]


def test_direct_runtime_action_times_out_generator_and_sets_stop_event(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
    jump_shape = {"id": "jump", "kind": "rect", "title": "去二", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "sceneJumpTarget": "2"}
    tree = [
        _scene_image("一", "0001.jpg", [jump_shape]),
        _scene_image("二", "0002.jpg", [{"id": "id2", "kind": "rect", "title": "二标识", "isSceneIdentity": True, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}]),
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


def test_go_scene_replans_through_annotated_reward_interruption(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    jump_shape = {"id": "jump13", "kind": "rect", "title": "日常", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "sceneJumpTarget": "3"}
    claim_shape = {"id": "claim", "kind": "rect", "title": "领取", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1, "sceneJumpTarget": "1"}
    tree = [
        _scene_image("一", "0001.jpg", [jump_shape]),
        _scene_image("报名领取灵石奖励", "0002.jpg", [claim_shape]),
        _scene_image("三", "0003.jpg", [{"id": "id3", "kind": "rect", "title": "三标识", "isSceneIdentity": True, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}]),
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
    assert any("奖励/提示页打断" in log["message"] for log in runner.status()["logs"])


def test_go_scene_stops_on_unannotated_result_after_timeout(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    first_shape = {"id": "jump12", "kind": "rect", "title": "随机跳", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "sceneJumpTarget": "3"}
    second_shape = {"id": "jump23", "kind": "rect", "title": "去三", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "sceneJumpTarget": "3"}
    tree = [
        _scene_image("一", "0001.jpg", [first_shape]),
        _scene_image("二", "0002.jpg", [second_shape]),
        _scene_image("三", "0003.jpg", [{"id": "id3", "kind": "rect", "title": "三标识", "isSceneIdentity": True, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}]),
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

    try:
        runner._run_runtime_behavior_tree(
            runtime_ctx=ctx,
            asset_tree_path=path,
            stop_event=FakeStopEvent(),
            action=lambda: runner._execute_runtime_task(ctx, "go_scene", {"target_scene_id": 3}, FakeStopEvent()),
            label="到场景",
        )
    except RuntimeError as exc:
        assert "未声明落点" in str(exc) or "不在" in str(exc)
    else:
        raise AssertionError("go_scene should stop when actual result is not declared in sceneJumpTarget")

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved[0]["shapes"][0]["sceneJumpTarget"] == "3"
    assert saved[1]["shapes"][0]["sceneJumpTarget"] == "3"


def test_daily_signup_scheduler_task_is_runtime_daily_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-signup")

    assert task["task_type"] == "daily_signup"
    assert task["label"] == "日常_报名"
    assert task["schedule_kind"] == "daily"
    assert task["schedule_times"] == ["05:00"]
    assert task["enabled"] is True
    assert task["cooldown_seconds"] == 600
    assert _data_annotation_task_supported(task)


def test_mail_cleanup_is_daily_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "mail-cleanup")

    assert task["task_type"] == "mail_cleanup"
    assert task["label"] == "邮件_清理"
    assert task["schedule_kind"] == "daily"
    assert task["schedule_times"] == ["00:05"]
    assert task["enabled"] is True
    assert task["cooldown_seconds"] == 600
    assert task["payload"]["max_runtime_seconds"] == 3600
    assert _data_annotation_task_supported(task)


def test_mail_cleanup_retry_near_daily_trigger_defers_to_next_daily_run():
    defaults = _default_data_annotation_scheduler_tasks()
    raw = [
        {
            "id": "mail-cleanup",
            "task_type": "mail_cleanup",
            "label": "邮件_清理",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["00:05"],
            "last_result": "retry_later",
            "retry_after": "2026-06-14 23:30:00",
            "next_time": None,
            "payload": {"__scheduler_definition_task_type": "mail_cleanup"},
            "checkpoint": {"manual_inspection_note": "邮件清理重试延后"},
        }
    ]

    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        defaults,
        {},
        task_supported=_data_annotation_task_supported,
        now=datetime(2026, 6, 14, 15, 30, 0),
    )
    task = next(item for item in tasks if item["id"] == "mail-cleanup")

    assert changed is True
    assert task["retry_after"] is None
    assert task["next_time"] == "2026-06-15 00:05:00"
    assert task["checkpoint"] is None


def test_xianfu_visit_partner_is_dynamic_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "xianfu-visit-partner")

    assert task["task_type"] == "xianfu_visit_partner"
    assert task["label"] == "仙府_寻访仙侣"
    assert task["source"] == "data_annotation_runtime"
    assert task["schedule_kind"] == "dynamic"
    assert task["schedule_times"] == []
    assert task["enabled"] is True
    assert task["cooldown_seconds"] == 600
    assert _data_annotation_task_supported(task)


def test_daily_boss_is_daily_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "daily-boss")

    assert task["task_type"] == "daily_boss"
    assert task["label"] == "日常_首领"
    assert task["source"] == "data_annotation_runtime"
    assert task["schedule_kind"] == "daily"
    assert task["schedule_times"] == ["05:00"]
    assert task["enabled"] is False
    assert task["cooldown_seconds"] == 600
    assert _data_annotation_task_supported(task)


def test_daily_lingzu_is_not_independent_scheduler_task():
    tasks = {item["id"]: item for item in _default_data_annotation_scheduler_tasks()}

    assert "legacy-daily-lingzu" not in tasks
    assert _data_annotation_task_supported({"task_type": "daily_lingzu"}) is False


def test_daily_jianling_is_daily_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-jianling")

    assert task["task_type"] == "daily_jianling"
    assert task["label"] == "日常_剑灵"
    assert task["source"] == "data_annotation_runtime"
    assert task["schedule_kind"] == "daily"
    assert task["schedule_times"] == ["05:00"]
    assert task["enabled"] is False
    assert task["cooldown_seconds"] == 600
    assert _data_annotation_task_supported(task)


def test_daily_lingta_is_not_independent_scheduler_task():
    tasks = {item["id"]: item for item in _default_data_annotation_scheduler_tasks()}

    assert "legacy-daily-lingta" not in tasks
    assert _data_annotation_task_supported({"task_type": "daily_lingta"}) is False


def test_daily_lingta_green_bottle_returns_by_left_bottom_world_without_back(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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

    def fake_ocr_lines(frame):
        if frame == "green_outer":
            return [{"text": "炼丹绿瓶世界"}]
        return [{"text": "储物袋角色装备功法书"}]

    def fake_identify_scene_number(_ctx, frame, _preferred):
        if frame == "world":
            return 34, 100.0
        return None, 0.0

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(runner, "_ocr_lines", fake_ocr_lines)
    monkeypatch.setattr(runner, "_identify_scene_number", fake_identify_scene_number)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1))))
    monkeypatch.setattr(runner, "_keyevents", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not use keyevent/back")))

    result = runner._run_direct_runtime_action(
        lambda: runner._leave_daily_lingta_green_bottle(ctx, FakeStopEvent()),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(144.0, 1504.0), (94.5, 1456.0)]


def test_daily_xianyuan_is_daily_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-xianyuan")

    assert task["task_type"] == "daily_xianyuan"
    assert task["label"] == "日常_挑战仙缘"
    assert task["source"] == "data_annotation_runtime"
    assert task["schedule_kind"] == "daily"
    assert task["schedule_times"] == ["05:00"]
    assert task["enabled"] is False
    assert task["cooldown_seconds"] == 600
    assert _data_annotation_task_supported(task)


def test_daily_assistant_is_daily_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-assistant")

    assert task["task_type"] == "daily_assistant"
    assert task["label"] == "日常_助手"
    assert task["source"] == "data_annotation_runtime"
    assert task["schedule_kind"] == "daily"
    assert task["schedule_times"] == ["00:00", "06:00", "12:00", "18:00"]
    assert task["enabled"] is True
    assert task["cooldown_seconds"] == 600
    assert _data_annotation_task_supported(task)


def test_daily_assistant_scheduler_task_enables_with_standard_times():
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
    assert task["enabled"] is True
    assert task["schedule_times"] == ["00:00", "06:00", "12:00", "18:00"]
    assert task["last_result"] != "unsupported"


def test_scheduler_time_order_sorts_dynamic_before_later_daily_and_disabled_last():
    tasks = [
        {
            "id": "daily-0500",
            "enabled": True,
            "schedule_kind": "daily",
            "next_time": "2026-06-13 05:00:00",
        },
        {
            "id": "dynamic-0115",
            "enabled": True,
            "schedule_kind": "dynamic",
            "next_time": "2026-06-13 01:15:00",
        },
        {
            "id": "manual-disabled",
            "enabled": False,
            "schedule_kind": "manual",
        },
        {
            "id": "retry-0050",
            "enabled": True,
            "schedule_kind": "daily",
            "retry_after": "2026-06-13 00:50:00",
            "next_time": "2026-06-13 18:00:00",
        },
    ]

    ordered = [task["id"] for task in sorted(tasks, key=data_annotation_scheduler_time_order_key)]

    assert ordered == ["retry-0050", "dynamic-0115", "daily-0500", "manual-disabled"]


def test_daily_yaowang_and_yaozu_are_not_independent_scheduler_tasks():
    tasks = {item["id"]: item for item in _default_data_annotation_scheduler_tasks()}

    assert "legacy-daily-yaowang" not in tasks
    assert "legacy-daily-yaozu" not in tasks
    assert _data_annotation_task_supported({"task_type": "daily_yaowang"}) is False
    assert _data_annotation_task_supported({"task_type": "daily_yaozu"}) is False


def test_daily_entry_matches_yaowang_and_yaozu_in_scroll_window():
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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
        lambda runtime_ctx, asset_tree_path=None, frame_data_url=None, stop_event=None, **_kwargs: runtime_runner_core.FanxiuRuntime(
            runner,
            runtime_ctx,
            asset_tree_path=asset_tree_path,
            frame_data_url=frame_data_url,
            stop_event=stop_event,
        ),
    )


def test_daily_shuangxiu_opens_daily_entry_from_69(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    frames = iter(["initial-daily", "daily-list", "secret"])
    clicked: list[tuple[float, float]] = []

    monkeypatch.setattr(
        runner,
        "_fanxiu_runtime",
        lambda runtime_ctx, asset_tree_path=None, frame_data_url=None, stop_event=None, **_kwargs: runtime_runner_core.FanxiuRuntime(
            runner,
            runtime_ctx,
            asset_tree_path=asset_tree_path,
            frame_data_url=frame_data_url,
            stop_event=stop_event,
        ),
    )
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, frame, _ids=None: (215, 100.0) if frame == "secret" else (69, 100.0))
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
        lambda frame: (
            []
            if frame == "initial-daily"
            else [
                {"text": "完成双人修炼1次", "x": 365.0, "y": 300.0, "w": 285.0, "h": 45.0},
                {"text": "活10/次", "x": 425.0, "y": 380.0, "w": 140.0, "h": 38.0},
                {"text": "0/3", "x": 425.0, "y": 420.0, "w": 80.0, "h": 38.0},
            ]
        ),
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1))))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_detail", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_invite", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_training_ready", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_wait_daily_shuangxiu_complete", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_finish_daily_shuangxiu_after_continue", _fake_daily_shuangxiu_finish(runner))

    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_shuangxiu_task(ctx, threading.Event(), {}),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(507.5, 322.5), (243.3, 445.8), (453.3, 1265.0), (319.2, 365.0), (711.7, 499.2), (450.0, 1416.7), (449.2, 1435.8)]
    assert runner.status()["phase"] == "daily_shuangxiu_complete_continued"


def test_daily_shuangxiu_clicks_first_book_when_already_on_215(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _ids: (215, 100.0))
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: [{"text": "秘术", "x": 120.0, "y": 120.0, "w": 100.0, "h": 40.0}])
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
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _ids: (None, 0.0))
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
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
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _ids: (None, 0.0))
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
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
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _ids: (None, 0.0))
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
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
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _ids: (None, 0.0))
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
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
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _ids: (None, 0.0))
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
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
    runner = create_fanxiu_runtime_runner()
    image219 = _image("双修修炼准备", "0219.png", [
        {"id": "start-training", "kind": "rect", "title": "前往修炼", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.385185, "y": 0.86875, "w": 0.22963, "h": 0.033333},
        {"id": "leave", "kind": "rect", "title": "离开", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.038889, "y": 0.485417, "w": 0.109259, "h": 0.084375},
    ])
    image86 = _image("离开场景", "0086.png", [
        {"id": "confirm", "kind": "rect", "title": "确认", "imageMatchRole": "off", "ocrMatchRole": "off", "x": 0.624074, "y": 0.64375, "w": 0.12037, "h": 0.041667},
    ])
    ctx = {"entry": object(), "asset_tree_path": tmp_path / "asset_tree.json", "images": {86: image86, 219: image219}}
    frames = iter(["training-ready", "leave-confirm"])
    clicked: list[tuple[str, float, float]] = []
    waited: list[int] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, frame, _ids: (86, 100.0) if frame == "leave-confirm" else (None, 0.0),
    )
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
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
    assert waited == [34]
    assert runner.status()["phase"] == "daily_shuangxiu_complete_continued"


def test_daily_boss_entry_match_does_not_click_boss_marquee():
    runner = create_fanxiu_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.20, "w": 0.90, "h": 0.62},
    ])
    lines = [
        {"text": "喜[海浪无声]成功击败积麟秘境首领，获得了[龙魂精魄]", "x": 95.0, "y": 700.0, "w": 730.0, "h": 42.0},
        {"text": "抵御妖王来袭", "x": 370.0, "y": 760.0, "w": 260.0, "h": 42.0},
    ]

    matches = runner._daily_entry_matches(lines, image69, title_pattern=r"击\s*败\s*首\s*领")

    assert matches == []


def test_daily_entry_world_text_with_fengmosha_does_not_click_popup(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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


def test_daily_task_row_progress_repairs_ocr_prefix_glued_to_fraction():
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()

    assert runner._daily_xianyuan_text_is_detail("身份 魔界三大始祖之一 功法主修 剑修 出没地点 落潮城 前往")
    assert not runner._daily_xianyuan_text_is_detail("查探 送礼 教他做人")
    assert runner._daily_xianyuan_text_is_dialogue("查探 送礼 教他做人 我可是魔界始祖")
    assert not runner._daily_xianyuan_text_is_dialogue("出没地点 落潮城 前往")


def test_daily_yaowang_uses_exterminate_not_unchecked_fast_sweep(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.20, "w": 0.90, "h": 0.62},
    ])
    ctx = {
        "entry": object(),
        "asset_tree_path": Path("asset-tree.json"),
        "images": {69: image69},
    }
    frames = iter(["selection", "detail", "combat", "world"])
    clicked: list[tuple[float, float]] = []
    waits: list[float] = []

    class ImmediateStopEvent:
        def is_set(self):
            return False

        def wait(self, seconds):
            waits.append(seconds)
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, frame, _ids: (
            (34, 100.0) if frame == "world" and 34 in _ids else
            (None, 0.0)
        ),
    )
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
        lambda frame: [
            {"text": "妖王来袭", "x": 80.0, "y": 80.0, "w": 180.0, "h": 40.0},
            {"text": "推荐剿灭", "x": 300.0, "y": 500.0, "w": 180.0, "h": 42.0},
        ] if frame == "selection" else [
            {"text": "剩余奖励次数:1/2", "x": 330.0, "y": 1260.0, "w": 260.0, "h": 42.0},
            {"text": "前往剿灭", "x": 300.0, "y": 1350.0, "w": 300.0, "h": 58.0},
            {"text": "快速扫荡 跳过动画", "x": 170.0, "y": 1450.0, "w": 520.0, "h": 42.0},
        ] if frame == "detail" else [
            {"text": "用时：00:31 副本", "x": 500.0, "y": 80.0, "w": 260.0, "h": 42.0},
            {"text": "妖兽波数4/8波", "x": 330.0, "y": 120.0, "w": 260.0, "h": 42.0},
        ] if frame == "combat" else [
            {"text": "世界 储物袋 角色 装备 功法书", "x": 100.0, "y": 1400.0, "w": 700.0, "h": 42.0},
        ],
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1))))
    stop_event = ImmediateStopEvent()

    result = runner._run_direct_runtime_action(
        lambda: runner._run_daily_free_challenge_from_scene(
            ctx,
            stop_event,
            {"free_challenge_timeout": 1.0},
            task_id="legacy-daily-yaowang",
            task_type="daily_yaowang",
            task_label="日常_妖王来袭",
        ),
        stop_event=stop_event,
        tick_seconds=0.01,
    )

    assert result == "reenter"
    assert clicked == [(390.0, 521.0), (450.0, 1379.0)]
    assert waits == [2.0, 0.1, 4.0, 0.1, 0.1]


def test_daily_yaowang_stops_on_purchase_modal(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.20, "w": 0.90, "h": 0.62},
    ])
    ctx = {"entry": object(), "images": {69: image69}}

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "purchase")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _ids: (None, 0.0))
    purchase_lines = [
        {"text": "碧霄引兽草", "x": 200.0, "y": 260.0, "w": 320.0, "h": 50.0},
        {"text": "价格：100 拥有：125005", "x": 300.0, "y": 1120.0, "w": 360.0, "h": 50.0},
        {"text": "购买并使用", "x": 330.0, "y": 1240.0, "w": 240.0, "h": 58.0},
    ]
    monkeypatch.setattr(runner, "_ocr_frame", lambda _frame, **_kwargs: {"lines": purchase_lines})
    clicked: list[tuple[float, float]] = []
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))

    with pytest.raises(RuntimeError, match="购买并使用"):
        runner._run_direct_runtime_action(
            lambda: runner._run_daily_free_challenge_from_scene(
                ctx,
                threading.Event(),
                {"free_challenge_timeout": 1.0},
                task_id="legacy-daily-yaowang",
                task_type="daily_yaowang",
                task_label="日常_妖王来袭",
            ),
            stop_event=threading.Event(),
            tick_seconds=0.01,
        )

    assert clicked == []


def test_daily_yaozu_purchase_modal_marks_free_attempts_done(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.20, "w": 0.90, "h": 0.62},
    ])
    ctx = {"entry": object(), "images": {69: image69}}

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "purchase")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _ids: (None, 0.0))
    purchase_lines = [
        {"text": "碧霄引兽草", "x": 200.0, "y": 260.0, "w": 320.0, "h": 50.0},
        {"text": "价格：100 拥有：125005", "x": 300.0, "y": 1120.0, "w": 360.0, "h": 50.0},
        {"text": "购买并使用", "x": 330.0, "y": 1240.0, "w": 240.0, "h": 58.0},
    ]
    monkeypatch.setattr(runner, "_ocr_frame", lambda _frame, **_kwargs: {"lines": purchase_lines})
    clicked: list[tuple[float, float]] = []
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))
    recorded: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        runner,
        "_record_scheduler_task_discovered_next_time",
        lambda task_id, next_time, *, task_type, label: recorded.append((task_id, task_type, label)),
    )

    def fake_cleanup(_factory, **_kwargs):
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_safe_daily_done_cleanup", fake_cleanup)

    result = runner._run_direct_runtime_action(
        lambda: runner._run_daily_free_challenge_from_scene(
            ctx,
            threading.Event(),
            {"free_challenge_timeout": 1.0},
            task_id="legacy-daily-yaozu",
            task_type="daily_yaozu",
            task_label="日常_妖族袭城",
        ),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == []
    assert recorded == [("legacy-daily-yaozu", "daily_yaozu", "日常_妖族袭城")]


def test_daily_assistant_requires_assistant_asset_after_list_detected(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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


def test_daily_assistant_accepts_world_like_text_without_storage_bag_word():
    runner = create_fanxiu_runtime_runner()

    assert runner._daily_assistant_text_is_world_like("止清羊驼角色装备星海功法书 修为：14.5亿")


def test_world_scene_leave_matches_only_right_side_leave_text():
    runner = create_fanxiu_runtime_runner()
    lines = [
        {"text": "离开", "x": 790.0, "y": 840.0, "w": 70.0, "h": 50.0},
        {"text": "离开", "x": 100.0, "y": 840.0, "w": 70.0, "h": 50.0},
        {"text": "是否离开", "x": 720.0, "y": 840.0, "w": 120.0, "h": 50.0},
    ]

    matches = runner._world_scene_leave_matches(lines, width=900.0, height=1600.0)

    assert matches == [(809.0, 777.5, "离开")]


def test_leave_scene_confirm_ocr_prefers_86_over_false_24(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {"images": {86: _image("离开场景", "0086.png", []), 24: _image("报名领取灵石奖励", "0024.png", [])}}
    monkeypatch.setattr(
        runner,
        "_cached_ocr_lines",
        lambda _ctx, _frame: [
            {"text": "是否离开当前场景?", "x": 320.0, "y": 720.0, "w": 280.0, "h": 34.0},
            {"text": "取消确认", "x": 260.0, "y": 1036.0, "w": 393.0, "h": 47.0},
        ],
    )
    scene_id, score = runner._identify_scene_number(ctx, "frame", [24, 86])

    assert (scene_id, score) == (86, 100.0)


def test_scene_number_does_not_prefetch_unrelated_ocr_identity(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image34 = _image("世界", "0034.png", [
        {"id": "world-id", "kind": "rect", "title": "世界标识", "sceneIdentityRole": "required", "sceneIdentityScope": "global"},
    ])
    image999 = _image("其它OCR场景", "0999.png", [
        {
            "id": "ocr-id",
            "kind": "rect",
            "title": "OCR身份",
            "sceneIdentityRole": "required",
            "sceneIdentityScope": "global",
            "ocrEnabled": True,
            "ocrMatchRole": "required",
            "ocrText": "其它",
        },
    ])
    ctx = {"images": {34: image34, 999: image999}}
    ocr_calls: list[str] = []

    monkeypatch.setattr(runner, "_runtime_scene_candidate_ids", lambda _ctx: [34])
    monkeypatch.setattr(runner, "_cached_ocr_lines", lambda _ctx, _frame: ocr_calls.append("ocr") or [])
    monkeypatch.setattr(runner, "_scene_score", lambda _ctx, image, _frame: 100.0 if image is image34 else 0.0)

    assert runner._identify_scene_number(ctx, "frame") == (34, 100.0)
    assert ocr_calls == []


def test_mail_cleanup_leaves_world_side_scene_before_opening_mail(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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

        def wait_view(self, *_args, **_kwargs):
            if False:
                yield BehaviorTreeStatus.RUNNING

        def scroll_shape_content(self, *_args, **_kwargs):
            if False:
                yield BehaviorTreeStatus.RUNNING
            return False

    def fake_identify(_ctx, frame, _preferred):
        if frame == "scene-inside":
            return 69, 100.0
        if frame == "leave-confirm":
            return 86, 100.0
        if frame == "world-after-leave":
            return 34, 100.0
        return 121, 100.0

    def fake_ocr_lines(frame):
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
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(runner, "_identify_scene_number", fake_identify)
    monkeypatch.setattr(runner, "_ocr_lines", fake_ocr_lines)
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(runner, "_open_mail_cleanup_entry", fake_open_mail)
    monkeypatch.setattr(runner, "_runtime_mail_rows_from_frame", lambda *_args, **_kwargs: [])

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_mail_cleanup_task(ctx, FakeStopEvent(), {"max_actions": 1, "max_scrolls": 1}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(809.0, 777.5), (615.8333333333334, 1063.3333333333333)]
    assert opened_mail == [True]


def test_xianfu_learn_skill_is_dynamic_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "xianfu-learn-skill")

    assert task["task_type"] == "xianfu_learn_skill"
    assert task["label"] == "仙府_领悟绝技"
    assert task["source"] == "data_annotation_runtime"
    assert task["schedule_kind"] == "dynamic"
    assert task["schedule_times"] == []
    assert task["enabled"] is True
    assert task["cooldown_seconds"] == 600
    assert _data_annotation_task_supported(task)


def test_legacy_mail_claim_check_scheduler_task_is_merged_into_mail_cleanup():
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
        next(item for item in defaults if item["id"] == "mail-cleanup"),
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
    assert [(item["id"], item["task_type"], item["label"]) for item in mail_tasks] == [
        ("mail-cleanup", "mail_cleanup", "邮件_清理")
    ]
    assert mail_tasks[0]["payload"]["max_runtime_seconds"] == 3600


def test_enabled_xianfu_dynamic_tasks_get_static_initial_check_time():
    defaults = _default_data_annotation_scheduler_tasks()
    raw = [
        {
            **next(item for item in defaults if item["id"] == "xianfu-visit-partner"),
            "enabled": True,
            "next_time": None,
            "retry_after": None,
            "last_result": "",
        },
        {
            **next(item for item in defaults if item["id"] == "xianfu-learn-skill"),
            "enabled": True,
            "next_time": None,
            "retry_after": None,
            "last_result": "",
        },
    ]

    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        defaults,
        {},
        task_supported=_data_annotation_task_supported,
        now=datetime(2026, 6, 21, 7, 0, 0),
    )

    assert changed is True
    assert next(item for item in tasks if item["id"] == "xianfu-visit-partner")["next_time"] == "2026-06-21 06:30:00"
    assert next(item for item in tasks if item["id"] == "xianfu-learn-skill")["next_time"] == "2026-06-21 06:30:00"


def test_legacy_xianfu_visit_scheduler_task_is_migrated_to_runtime_task():
    defaults = _default_data_annotation_scheduler_tasks()
    raw = [
        {
            "id": "legacy-dynamic-xianfu-visit",
            "task_type": "legacy_dynamic_task",
            "label": "仙府 寻访仙侣",
            "source": "legacy_behavior_tree",
            "schedule_kind": "dynamic",
            "legacy_name": "仙府_寻访仙侣",
            "enabled": False,
            "cooldown_seconds": 0,
            "payload": {"legacy_name": "仙府_寻访仙侣"},
        }
    ]

    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        defaults,
        {"discoveries": {"task": {"xianfu-visit-partner": {"discovered_next_time": "2026-06-11 04:16:13"}}}},
        task_supported=_data_annotation_task_supported,
        now=datetime(2026, 6, 10, 12, 0, 0),
    )
    task = next(item for item in tasks if item["id"] == "xianfu-visit-partner")

    assert changed is True
    assert task["task_type"] == "xianfu_visit_partner"
    assert task["source"] == "data_annotation_runtime"
    assert task["schedule_kind"] == "dynamic"
    assert task["label"] == "仙府_寻访仙侣"
    assert task["cooldown_seconds"] == 600
    assert task["next_time"] == "2026-06-11 04:16:13"


def test_legacy_daily_boss_scheduler_task_is_migrated_to_runtime_task():
    defaults = _default_data_annotation_scheduler_tasks()
    raw = [
        {
            "id": "legacy-dynamic-daily-boss",
            "task_type": "legacy_dynamic_task",
            "label": "日常 首领",
            "source": "legacy_behavior_tree",
            "schedule_kind": "dynamic",
            "legacy_name": "日常_首领",
            "enabled": False,
            "cooldown_seconds": 0,
            "payload": {"legacy_name": "日常_首领"},
        }
    ]

    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        defaults,
        {"discoveries": {"task": {"daily-boss": {"discovered_next_time": "2026-06-11 05:00:00"}}}},
        task_supported=_data_annotation_task_supported,
        now=datetime(2026, 6, 10, 12, 0, 0),
    )
    task = next(item for item in tasks if item["id"] == "daily-boss")

    assert changed is True
    assert task["task_type"] == "daily_boss"
    assert task["source"] == "data_annotation_runtime"
    assert task["schedule_kind"] == "daily"
    assert task["label"] == "日常_首领"
    assert task["schedule_times"] == ["05:00"]
    assert task["cooldown_seconds"] == 600
    assert task["next_time"] == "2026-06-10 05:00:00"


def test_scheduler_world_fact_sync_ignores_fact_older_than_task_run():
    defaults = _default_data_annotation_scheduler_tasks()
    raw = [
        {
            "id": "daily-boss",
            "task_type": "daily_boss",
            "label": "日常_首领",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "schedule_times": ["05:00"],
            "enabled": True,
            "last_run_at": "2026-06-12 09:36:30",
            "last_result": "error",
            "retry_after": "2026-06-12 09:46:44",
            "next_time": "2026-06-12 05:25:50",
        }
    ]

    tasks, _changed = repair_data_annotation_scheduler_tasks(
        raw,
        defaults,
        {
            "discoveries": {
                "task": {
                    "daily-boss": {
                        "discovered_next_time": "2026-06-12 05:25:50",
                        "last_result": "success",
                        "updated_at": datetime(2026, 6, 12, 5, 20, 0).timestamp(),
                    }
                }
            }
        },
        task_supported=_data_annotation_task_supported,
        now=datetime(2026, 6, 12, 9, 40, 0),
    )
    task = next(item for item in tasks if item["id"] == "daily-boss")

    assert task["last_result"] == "error"
    assert task["retry_after"] == "2026-06-12 09:46:44"


def test_legacy_daily_lingzu_scheduler_task_is_removed_by_assistant_coverage():
    defaults = _default_data_annotation_scheduler_tasks()
    raw = [
        {
            "id": "legacy-daily-lingzu",
            "task_type": "legacy_daily_task",
            "label": "日常 灵祖",
            "source": "legacy_behavior_tree",
            "schedule_kind": "daily",
            "legacy_name": "日常_灵祖",
            "enabled": False,
            "cooldown_seconds": 0,
            "payload": {"legacy_name": "日常_灵祖"},
        }
    ]

    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        defaults,
        {"discoveries": {"task": {"legacy-daily-lingzu": {"discovered_next_time": "2026-06-11 05:00:00"}}}},
        task_supported=_data_annotation_task_supported,
        now=datetime(2026, 6, 10, 12, 0, 0),
    )

    assert changed is True
    assert not any(item["id"] == "legacy-daily-lingzu" for item in tasks)


def test_legacy_daily_jianling_scheduler_task_is_migrated_to_runtime_task():
    defaults = _default_data_annotation_scheduler_tasks()
    raw = [
        {
            "id": "legacy-daily-jianling",
            "task_type": "legacy_daily_task",
            "label": "日常 剑灵",
            "source": "legacy_behavior_tree",
            "schedule_kind": "daily",
            "legacy_name": "日常_剑灵",
            "enabled": False,
            "cooldown_seconds": 0,
            "payload": {"legacy_name": "日常_剑灵"},
        }
    ]

    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        defaults,
        {"discoveries": {"task": {"legacy-daily-jianling": {"discovered_next_time": "2026-06-11 05:00:00"}}}},
        task_supported=_data_annotation_task_supported,
        now=datetime(2026, 6, 10, 12, 0, 0),
    )
    task = next(item for item in tasks if item["id"] == "legacy-daily-jianling")

    assert changed is True
    assert task["task_type"] == "daily_jianling"
    assert task["source"] == "data_annotation_runtime"
    assert task["schedule_kind"] == "daily"
    assert task["label"] == "日常_剑灵"
    assert task["schedule_times"] == ["05:00"]
    assert task["cooldown_seconds"] == 600
    assert task["next_time"] == "2026-06-10 05:00:00"


def test_legacy_daily_lingta_scheduler_task_is_removed_by_assistant_coverage():
    defaults = _default_data_annotation_scheduler_tasks()
    raw = [
        {
            "id": "legacy-daily-lingta",
            "task_type": "legacy_daily_task",
            "label": "日常 灵塔",
            "source": "legacy_behavior_tree",
            "schedule_kind": "daily",
            "legacy_name": "日常_灵塔",
            "enabled": False,
            "cooldown_seconds": 0,
            "payload": {"legacy_name": "日常_灵塔"},
        }
    ]

    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        defaults,
        {"discoveries": {"task": {"legacy-daily-lingta": {"discovered_next_time": "2026-06-11 05:00:00"}}}},
        task_supported=_data_annotation_task_supported,
        now=datetime(2026, 6, 10, 12, 0, 0),
    )

    assert changed is True
    assert not any(item["id"] == "legacy-daily-lingta" for item in tasks)


def test_legacy_daily_xianyuan_scheduler_task_is_migrated_to_runtime_task():
    defaults = _default_data_annotation_scheduler_tasks()
    raw = [
        {
            "id": "legacy-daily-xianyuan",
            "task_type": "legacy_daily_task",
            "label": "日常 挑战仙缘",
            "source": "legacy_behavior_tree",
            "schedule_kind": "daily",
            "legacy_name": "日常_挑战仙缘",
            "enabled": False,
            "cooldown_seconds": 0,
            "payload": {"legacy_name": "日常_挑战仙缘"},
        }
    ]

    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        defaults,
        {"discoveries": {"task": {"legacy-daily-xianyuan": {"discovered_next_time": "2026-06-11 05:00:00"}}}},
        task_supported=_data_annotation_task_supported,
        now=datetime(2026, 6, 10, 12, 0, 0),
    )
    task = next(item for item in tasks if item["id"] == "legacy-daily-xianyuan")

    assert changed is True
    assert task["task_type"] == "daily_xianyuan"
    assert task["source"] == "data_annotation_runtime"
    assert task["schedule_kind"] == "daily"
    assert task["label"] == "日常_挑战仙缘"
    assert task["schedule_times"] == ["05:00"]
    assert task["cooldown_seconds"] == 600
    assert task["next_time"] == "2026-06-10 00:00:00"


def test_legacy_daily_assistant_scheduler_task_is_migrated_to_runtime_task():
    defaults = _default_data_annotation_scheduler_tasks()
    raw = [
        {
            "id": "legacy-daily-assistant",
            "task_type": "legacy_daily_task",
            "label": "日常 助手",
            "source": "legacy_behavior_tree",
            "schedule_kind": "daily",
            "legacy_name": "日常_助手",
            "enabled": False,
            "cooldown_seconds": 0,
            "payload": {"legacy_name": "日常_助手"},
        }
    ]

    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        defaults,
        {"discoveries": {"task": {"legacy-daily-assistant": {"discovered_next_time": "2026-06-11 05:00:00"}}}},
        task_supported=_data_annotation_task_supported,
        now=datetime(2026, 6, 10, 12, 0, 0),
    )
    task = next(item for item in tasks if item["id"] == "legacy-daily-assistant")

    assert changed is True
    assert task["task_type"] == "daily_assistant"
    assert task["source"] == "data_annotation_runtime"
    assert task["schedule_kind"] == "daily"
    assert task["label"] == "日常_助手"
    assert task["schedule_times"] == ["00:00", "06:00", "12:00", "18:00"]
    assert task["enabled"] is True
    assert task["cooldown_seconds"] == 600
    assert task["next_time"] == "2026-06-10 00:00:00"


def test_legacy_daily_yaowang_scheduler_task_is_removed_by_assistant_coverage():
    defaults = _default_data_annotation_scheduler_tasks()
    raw = [
        {
            "id": "legacy-daily-yaowang",
            "task_type": "legacy_daily_task",
            "label": "日常 妖王来袭",
            "source": "legacy_behavior_tree",
            "schedule_kind": "daily",
            "legacy_name": "日常_妖王来袭",
            "enabled": False,
            "cooldown_seconds": 0,
            "payload": {"legacy_name": "日常_妖王来袭"},
        }
    ]

    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        defaults,
        {"discoveries": {"task": {"legacy-daily-yaowang": {"discovered_next_time": "2026-06-11 05:00:00"}}}},
        task_supported=_data_annotation_task_supported,
        now=datetime(2026, 6, 10, 12, 0, 0),
    )

    assert changed is True
    assert not any(item["id"] == "legacy-daily-yaowang" for item in tasks)


def test_legacy_daily_yaozu_scheduler_task_is_removed_by_assistant_coverage():
    defaults = _default_data_annotation_scheduler_tasks()
    raw = [
        {
            "id": "legacy-daily-yaozu",
            "task_type": "legacy_daily_task",
            "label": "日常 妖族袭城",
            "source": "legacy_behavior_tree",
            "schedule_kind": "daily",
            "legacy_name": "日常_妖族袭城",
            "enabled": False,
            "cooldown_seconds": 0,
            "payload": {"legacy_name": "日常_妖族袭城", "args": [2, 0]},
        }
    ]

    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        defaults,
        {"discoveries": {"task": {"legacy-daily-yaozu": {"discovered_next_time": "2026-06-11 05:00:00"}}}},
        task_supported=_data_annotation_task_supported,
        now=datetime(2026, 6, 10, 12, 0, 0),
    )

    assert changed is True
    assert not any(item["id"] == "legacy-daily-yaozu" for item in tasks)


def test_legacy_xianfu_skill_scheduler_task_is_migrated_to_runtime_task():
    defaults = _default_data_annotation_scheduler_tasks()
    raw = [
        {
            "id": "legacy-dynamic-xianfu-skill",
            "task_type": "legacy_dynamic_task",
            "label": "仙府 领悟绝技",
            "source": "legacy_behavior_tree",
            "schedule_kind": "dynamic",
            "legacy_name": "仙府_领悟绝技",
            "enabled": False,
            "cooldown_seconds": 0,
            "payload": {"legacy_name": "仙府_领悟绝技"},
        }
    ]

    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        defaults,
        {"discoveries": {"task": {"xianfu-learn-skill": {"discovered_next_time": "2026-06-11 04:16:13"}}}},
        task_supported=_data_annotation_task_supported,
        now=datetime(2026, 6, 10, 12, 0, 0),
    )
    task = next(item for item in tasks if item["id"] == "xianfu-learn-skill")

    assert changed is True
    assert task["task_type"] == "xianfu_learn_skill"
    assert task["source"] == "data_annotation_runtime"
    assert task["schedule_kind"] == "dynamic"
    assert task["label"] == "仙府_领悟绝技"
    assert task["cooldown_seconds"] == 600
    assert task["next_time"] == "2026-06-11 04:16:13"


def test_mail_full_scan_is_not_a_default_scheduler_task():
    task_ids = {str(item.get("id") or "") for item in _default_data_annotation_scheduler_tasks()}

    assert "mail-full-scan" not in task_ids


def test_mail_full_scan_observe_only_ignores_claim_and_delete_policy(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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


def test_mail_cleanup_returns_from_detail_when_claim_does_not_auto_close(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image121 = _image("邮件", "0121.png", [])
    image122 = _image("邮件内容", "0122.png", [
        {"id": "claim", "kind": "rect", "title": "领取", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.05},
        {"id": "back", "kind": "rect", "title": "空白-返回", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05},
    ])
    ctx = {"entry": object(), "images": {121: image121, 122: image122}}
    runtime = runner._fanxiu_runtime(ctx, tmp_path / "asset_tree.json")
    title_shape = runtime_runner_core.Shape(
        {"id": "row", "kind": "rect", "title": "测试邮件", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.05},
        parent_view=runtime_runner_core.View(image121),
    )
    mail = runtime_runner_core._RuntimeMailRow({"title": "测试邮件"}, title_shape)
    clicked: list[str] = []
    wait_calls: list[tuple[int, ...]] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")

    def fake_wait_view(*views, **_kwargs):
        view_ids = tuple(int(view) for view in views)
        wait_calls.append(view_ids)
        if view_ids == (122, 123):
            if False:
                yield BehaviorTreeStatus.RUNNING
            return runtime_runner_core.View(image122)
        if view_ids == (121,):
            if False:
                yield BehaviorTreeStatus.RUNNING
            return runtime_runner_core.View(image121)
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

    assert result == "claim"
    assert clicked == ["测试邮件", "领取", "空白-返回"]
    assert wait_calls == [(122, 123), (121,)]


def test_mail_cleanup_reopens_mail_when_claim_returns_to_world(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    title_shape = runtime_runner_core.Shape(
        {"id": "row", "kind": "rect", "title": "测试邮件", "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.05},
        parent_view=runtime_runner_core.View(image121),
    )
    mail = runtime_runner_core._RuntimeMailRow({"title": "测试邮件"}, title_shape)
    clicked: list[str] = []
    reopened: list[bool] = []

    def fake_wait_view(*views, **_kwargs):
        view_ids = tuple(int(view) for view in views)
        if view_ids == (122, 123):
            if False:
                yield BehaviorTreeStatus.RUNNING
            return runtime_runner_core.View(image122)
        raise AssertionError(view_ids)

    def fake_wait_mail_list_or_world(*_args, **_kwargs):
        reopened.append(True)
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "reopened"

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runtime, "wait_view", fake_wait_view)
    monkeypatch.setattr(runner, "_wait_mail_list_or_reopen_from_world_after_action", fake_wait_mail_list_or_world)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, *_args, **_kwargs: clicked.append(shape["title"]))

    result = runner._run_direct_runtime_action(
        lambda: runner._claim_runtime_mail_row(runtime, mail),
        stop_event=runtime.stop_event,
        tick_seconds=0.01,
    )

    assert result == "claim"
    assert clicked == ["测试邮件", "领取"]
    assert reopened == [True]


def test_mail_cleanup_wait_reopens_from_world_like_text(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _targets: (None, 0.0))
    monkeypatch.setattr(runner, "_cached_ocr_lines", lambda _ctx, _frame: [{"text": "角色 装备 星海 功法书 储物袋"}])
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
            runtime_runner_core.View(image122),
            timeout=1.0,
            label="邮件_清理：返回邮件 #121",
        ),
        stop_event=runtime.stop_event,
        tick_seconds=0.01,
    )

    assert result == "reopened"
    assert events == ["settle", "reopen"]


def test_mail_cleanup_wait_treats_reward_overlay_as_transition(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image121 = _image(
        "邮件",
        "0121.png",
        [{"id": "marker", "kind": "rect", "title": "邮件标识", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.06}],
    )
    image122 = _image("邮件内容", "0122.png", [])
    ctx = {"images": {121: image121, 122: image122}}
    runtime = runner._fanxiu_runtime(ctx, tmp_path / "asset_tree.json", stop_event=threading.Event())
    scenes = [
        (122, 100.0, "reward-frame", "恭喜获得 点击屏幕继续 2秒后自动关闭"),
        (121, 100.0, "list-frame", "邮件 资源领取通知"),
    ]

    def fake_scene_text(*_args, **_kwargs):
        return scenes.pop(0)

    monkeypatch.setattr(runner, "_fanxiu_runtime_scene_text", fake_scene_text)
    monkeypatch.setattr(runner, "_match_shape", lambda *_args, **_kwargs: {"matched": True, "similarity": 96.0})

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_mail_list_or_reopen_from_world_after_action(
            runtime,
            runtime_runner_core.View(image122),
            timeout=18.0,
            label="邮件_清理：返回邮件 #121",
        ),
        stop_event=runtime.stop_event,
        tick_seconds=0.01,
    )

    assert result == "list"
    assert scenes == []


def test_mail_cleanup_wait_returns_detail_still_open_after_short_delay(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: [])

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_mail_list_or_reopen_from_world_after_action(
            runtime,
            runtime_runner_core.View(image122),
            timeout=18.0,
            label="邮件_清理：返回邮件 #121",
        ),
        stop_event=runtime.stop_event,
        tick_seconds=0.01,
    )

    assert result == "detail_still_open"


def test_mail_world_menu_ocr_click_uses_mail_shape_center_when_available():
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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

        def ocr_lines(self, _frame):
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
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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

        def current_scene(self, **_kwargs):
            raise AssertionError("stable menu probe should be able to skip scene identification")

        def click_frame_point(self, _image, x, y):
            clicked.append((round(float(x), 1), round(float(y), 1)))

    def fake_wait_ready(_ctx, _stop_event, **_kwargs):
        waited.append("ready")
        if False:
            yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fast menu probe should use local OCR before image matching")))
    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", lambda *_args, **_kwargs: [{"text": "邮件", "x": 420, "y": 1460, "w": 50, "h": 30}])
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
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", lambda *_args, **_kwargs: [{"text": "已锁定4/10封邮件", "x": 340, "y": 1240, "w": 180, "h": 40}])

    result = runner._run_direct_runtime_action(
        lambda: runner._click_mail_from_visible_world_menu_once(ctx, threading.Event(), require_world_scene=False),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "missing"
    assert clicked == []


def test_mail_visible_menu_once_accepts_compound_world_menu_ocr(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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

        def current_scene(self, **_kwargs):
            raise AssertionError("stable menu probe should be able to skip scene identification")

        def click_frame_point(self, _image, x, y):
            clicked.append((round(float(x), 1), round(float(y), 1)))

    def fake_wait_ready(_ctx, _stop_event, **_kwargs):
        if False:
            yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fast menu probe should use local OCR before image matching")))
    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", lambda *_args, **_kwargs: [{"text": "仙缘邮件修为设置8638", "x": 420, "y": 1460, "w": 220, "h": 30}])
    monkeypatch.setattr(runner, "_wait_mail_list_ready_or_restore_world", fake_wait_ready)

    result = runner._run_direct_runtime_action(
        lambda: runner._click_mail_from_visible_world_menu_once(ctx, threading.Event(), require_world_scene=False),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(450.0, 1480.0)]


def test_mail_visible_menu_once_reports_reward_tip_blocker(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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

        def current_scene(self, **_kwargs):
            raise AssertionError("stable menu probe should be able to skip scene identification")

        def click_frame_point(self, _image, x, y):
            clicked.append((float(x), float(y)))

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(
        runner,
        "_shape_score",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("blocked reward tip must not fall through to image matching")),
    )
    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", lambda *_args, **_kwargs: [{"text": "点击使用", "x": 420, "y": 1220, "w": 80, "h": 30}])

    result = runner._run_direct_runtime_action(
        lambda: runner._click_mail_from_visible_world_menu_once(ctx, threading.Event(), require_world_scene=False),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "blocked_reward_tip"
    assert clicked == []


def test_mail_stable_entry_clicks_open_shape_center_after_visible_miss(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
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


def test_mail_stable_entry_returns_reward_tip_blocker_without_retry(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    image34 = _image(
        "世界",
        "0034.png",
        [{"id": "open", "kind": "rect", "title": "打开下方菜单", "x": 0.85, "y": 0.9, "w": 0.08, "h": 0.08}],
    )
    ctx = {"images": {34: image34}}
    visible_calls: list[str] = []

    class FakeRuntime:
        def click_frame_point(self, *_args, **_kwargs):
            raise AssertionError("blocked reward tip should stop before toggling menu")

        def wait_action_settle(self, _seconds):
            raise AssertionError("blocked reward tip should not wait through menu retries")

    def fake_visible(_ctx, _stop_event, **_kwargs):
        visible_calls.append("visible")
        return "blocked_reward_tip"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_click_mail_from_visible_world_menu_once", fake_visible)

    result = runner._run_direct_runtime_action(
        lambda: runner._open_mail_stable_entry(ctx, threading.Event(), tmp_path / "asset_tree.json"),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "blocked_reward_tip"
    assert visible_calls == ["visible"]


def test_mail_reopen_closes_reward_tip_blocker_before_retry(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    image34 = _image(
        "世界",
        "0034.png",
        [{"id": "bottom-menu", "kind": "rect", "title": "下方菜单", "x": 0.36, "y": 0.91, "w": 0.52, "h": 0.07}],
    )
    ctx = {"images": {34: image34}}
    stable_results = iter(["blocked_reward_tip", "success"])
    clicked_bottom_menu: list[str] = []
    settled: list[float] = []

    class FakeRuntime:
        def __init__(self):
            self.ctx = ctx
            self.stop_event = threading.Event()
            self.asset_tree_path = tmp_path / "asset_tree.json"

        def cur_frame(self, *, update: bool = False):
            return "frame"

        def click_shape_center(self, _image, title):
            clicked_bottom_menu.append(str(title))

        def wait_action_settle(self, seconds):
            settled.append(float(seconds))
            if False:
                yield None

    def fake_close(_ctx, runtime, _frame, _text):
        runtime.click_shape_center(image34, "下方菜单")
        return True

    def fake_stable(_ctx, _stop_event, _asset_tree_path, **_kwargs):
        return next(stable_results)

    monkeypatch.setattr(runner, "_close_mail_world_reward_tip_if_present", fake_close)
    monkeypatch.setattr(runner, "_open_mail_stable_entry", fake_stable)

    result = runner._run_direct_runtime_action(
        lambda: runner._reopen_mail_from_current_world_like(FakeRuntime()),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked_bottom_menu == ["下方菜单", "下方菜单"]
    assert settled == [0.3, 0.3]


def test_mail_reward_tip_close_clicks_world_bottom_menu_shape(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image34 = _image(
        "世界",
        "0034.png",
        [
            {"id": "bottom-menu", "kind": "rect", "title": "下方菜单", "x": 0.36, "y": 0.91, "w": 0.52, "h": 0.07},
            {"id": "storage", "kind": "rect", "title": "储物袋", "x": 0.7, "y": 0.92, "w": 0.12, "h": 0.06},
        ],
    )
    image35 = _image(
        "世界下方菜单",
        "0035.png",
        [{"id": "menu", "kind": "rect", "title": "菜单", "x": 0.36, "y": 0.705, "w": 0.513, "h": 0.278}],
    )
    ctx = {"images": {34: image34, 35: image35}}
    clicked: list[str] = []

    class FakeRuntime:
        def click_shape_center(self, _image, title):
            clicked.append(str(title))

    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", lambda *_args, **_kwargs: [{"text": "点击使用", "x": 420, "y": 1220, "w": 80, "h": 30}])

    assert runner._close_mail_world_reward_tip_if_present(ctx, FakeRuntime(), "frame", "") is True
    assert clicked == ["下方菜单"]


def test_visible_mail_menu_probe_missing_does_not_stamp_scene_35(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: [])

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
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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

        def ocr_lines(self, _frame):
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
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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

        def ocr_lines(self, _frame):
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
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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


def test_packet_no_attachment_policy_skips_read_mail(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    row = {"title": "香车馈赠", "time_text": "2026年06月02日16:17", "is_read": True}
    record = FanxiuMailRecord(
        mail_key="id:packet-test",
        mail_id="packet-test",
        title="香车馈赠",
        normalized_title="香车馈赠",
        create_time_text="2026年06月02日16:17",
        source="packet",
        status="seen",
        payload={"mail_rewards": []},
    )
    monkeypatch.setattr(runner, "_find_packet_mail_record", lambda _title, _time_text, **_kwargs: record)
    monkeypatch.setattr(runner, "_find_packet_mail_records_for_visible_row", lambda _title, _time_text: [record])

    runner._prepare_mail_row_policy(row)

    assert row["mail_key"] == ""
    assert row["policy"] == ""
    assert row["packet_match"] == "ui_skipped"
    assert row["status"] == "已阅"
    assert row["time_text"] == "2026年06月02日16:17"


def test_packet_claim_policy_does_not_rescue_read_mail_by_title(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    row = {"title": "灵脉收益", "time_text": "2026年06月09日16:11", "is_read": True}
    record = FanxiuMailRecord(
        mail_key="id:lingmai-history",
        mail_id="lingmai-history",
        title="灵脉收益",
        normalized_title="灵脉收益",
        create_time_text="2026年06月07日19:43",
        source="packet",
        status="claimed",
        payload={"mail_rewards": [{"item_name": "玄神灵液", "item_type": "道具", "amount": 287232}]},
    )
    monkeypatch.setattr(runner, "_find_packet_mail_records_for_visible_row", lambda _title, _time_text: [record])

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["status"] == "已阅"
    assert row["packet_match"] == "ui_skipped"
    assert row["mail_key"] == ""
    assert row["policy"] == ""


def test_packet_claim_policy_claims_locked_visible_row_when_packet_is_safe(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    row = {"title": "香车馈赠", "time_text": "2026年06月09日13:48", "list_has_lock": True}
    record = FanxiuMailRecord(
        mail_key="id:xiangche-latest",
        mail_id="xiangche-latest",
        title="香车馈赠",
        normalized_title="香车馈赠",
        create_time_text="2026年06月09日13:48",
        source="packet",
        status="可领",
        action_policy="claim",
        payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币", "amount": 8888}]},
    )
    monkeypatch.setattr(runner, "_find_packet_mail_records_for_visible_row", lambda _title, _time_text: [record])

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["status"] == "锁定"
    assert row["mail_key"] == "id:xiangche-latest"
    assert row["policy"] == "claim"
    assert row["packet_match"] == "matched"


def test_packet_claim_policy_keeps_locked_visible_row_when_packet_is_protected(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    row = {"title": "珍贵馈赠", "time_text": "2026年06月09日13:48", "list_has_lock": True}
    record = FanxiuMailRecord(
        mail_key="id:protected-latest",
        mail_id="protected-latest",
        title="珍贵馈赠",
        normalized_title="珍贵馈赠",
        create_time_text="2026年06月09日13:48",
        source="packet",
        status="seen",
        payload={"mail_rewards": [{"item_name": "洗灵奇石", "item_type": "资源", "amount": 1}]},
    )
    monkeypatch.setattr(runner, "_find_packet_mail_records_for_visible_row", lambda _title, _time_text: [record])

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["status"] == "锁定"
    assert row["mail_key"] == "id:protected-latest"
    assert row["policy"] == ""
    assert row["packet_match"] == "matched"


def test_mail_rows_use_template_status_lock_text():
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()

    assert runner._normalize_mail_row_status("锁") == ""
    assert runner._normalize_mail_row_status("锁走") == "锁定"
    assert runner._normalize_mail_row_status("锁定") == "锁定"


def test_visible_mail_adjacency_uses_visual_slots_not_filtered_rows():
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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


def test_packet_mail_record_matches_noisy_ocr_title_by_time(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(runtime_runner_core, "_default_engine", engine)
    with Session(engine) as session:
        session.add(
            FanxiuMailRecord(
                mail_key="id:no-attachment",
                mail_id="no-attachment",
                title="仙缘夺魁个人榜奖励",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("仙缘夺魁个人榜奖励"),
                create_time_text="2026年06月04日23:59",
                source="packet",
                status="seen",
                payload={"mail_rewards": []},
            )
        )
        session.commit()
    runner = create_fanxiu_runtime_runner()

    record = runner._find_packet_mail_record("仙缘夺魅个人榜奖励", "2026年06月04日23:59")

    assert record is not None
    assert record.mail_key == "id:no-attachment"


def test_packet_mail_record_does_not_match_title_without_same_time(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(runtime_runner_core, "_default_engine", engine)
    with Session(engine) as session:
        session.add(
            FanxiuMailRecord(
                mail_key="id:latest-same-title",
                mail_id="latest-same-title",
                title="太乙馈赠",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("太乙馈赠"),
                create_time_text="2026年06月07日19:17",
                source="packet",
                status="seen",
                payload={"mail_rewards": []},
            )
        )
        session.commit()
    runner = create_fanxiu_runtime_runner()

    record = runner._find_packet_mail_record("太乙馈赠", "2026年06月07日19:43")

    assert record is None


def test_visible_mail_row_falls_back_to_title_when_time_mismatches(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(runtime_runner_core, "_default_engine", engine)
    with Session(engine) as session:
        session.add(
            FanxiuMailRecord(
                mail_key="id:known-same-title",
                mail_id="known-same-title",
                title="分红发放",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("分红发放"),
                create_time_text="2026年06月05日13:07",
                source="packet",
                status="seen",
                payload={"mail_rewards": [{"item_name": "灵石", "amount": 200}]},
            )
        )
        session.commit()
    runner = create_fanxiu_runtime_runner()
    row = {"title": "分红发放", "time_text": "2026年06月06日21:35"}

    runner._prepare_mail_row_policy(row, action_enabled=False)

    assert row["policy"] == ""
    assert row["mail_key"] == "id:known-same-title"
    assert row["packet_match"] == "title_only"
    assert row["packet_missing_reason"] == ""


def test_visible_mail_row_falls_back_to_title_only_packet_group(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(runtime_runner_core, "_default_engine", engine)
    with Session(engine) as session:
        session.add(
            FanxiuMailRecord(
                mail_key="id:title-only-safe",
                mail_id="title-only-safe",
                title="节日馈赠",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("节日馈赠"),
                create_time_text="2026年06月07日12:00",
                source="packet",
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
                source="packet",
                status="seen",
                payload={"mail_rewards": [{"item_name": "洗灵奇石", "item_type": "资源"}]},
            )
        )
        session.commit()
    runner = create_fanxiu_runtime_runner()
    row = {"title": "节日馈赠", "time_text": "2026年06月08日12:00"}

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["packet_match"] == "title_only"
    assert row["mail_key"] in {"id:title-only-safe", "id:title-only-protected"}
    assert row["policy"] == ""


def test_visible_mail_row_title_only_claims_when_all_candidates_safe(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(runtime_runner_core, "_default_engine", engine)
    with Session(engine) as session:
        session.add(
            FanxiuMailRecord(
                mail_key="id:title-only-safe",
                mail_id="title-only-safe",
                title="节日馈赠",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("节日馈赠"),
                create_time_text="2026年06月07日12:00",
                source="packet",
                status="seen",
                payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币"}]},
            )
        )
        session.commit()
    runner = create_fanxiu_runtime_runner()
    row = {"title": "节日馈赠", "time_text": "2026年06月08日12:00"}

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["packet_match"] == "title_only"
    assert row["mail_key"] == "id:title-only-safe"
    assert row["policy"] == "claim"


def test_visible_mail_row_title_only_uses_initial_reward_rule_not_user_status(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(runtime_runner_core, "_default_engine", engine)
    with Session(engine) as session:
        session.add(
            FanxiuMailRecord(
                mail_key="id:title-only-user-locked",
                mail_id="title-only-user-locked",
                title="节日馈赠",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("节日馈赠"),
                create_time_text="2026年06月07日12:00",
                source="packet",
                status="锁定",
                payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币"}]},
            )
        )
        session.commit()
    runner = create_fanxiu_runtime_runner()
    row = {"title": "节日馈赠", "time_text": "2026年06月08日12:00"}

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["packet_match"] == "title_only"
    assert row["mail_key"] == "id:title-only-user-locked"
    assert row["policy"] == "claim"


def test_packet_mail_fuzzy_title_does_not_cross_policy_conflict(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(runtime_runner_core, "_default_engine", engine)
    with Session(engine) as session:
        session.add(
            FanxiuMailRecord(
                mail_key="id:protected",
                mail_id="protected",
                title="未取之宝",
                normalized_title=fanxiu_api.normalize_fanxiu_mail_title("未取之宝"),
                create_time_text="2026年06月06日22:00",
                source="packet",
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
                source="packet",
                status="seen",
                payload={"mail_rewards": []},
            )
        )
        session.commit()
    runner = create_fanxiu_runtime_runner()

    record = runner._find_packet_mail_record("未取之宝", "2026年06月06日22:00")

    assert record is not None
    assert fanxiu_api.fanxiu_mail_action_policy_for_record(record) == ""


def test_visible_read_mail_skips_even_when_packet_is_missing_from_list(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    row = {"title": "资源领取通知", "time_text": "2026年06月05日22:00", "is_read": True}
    record = FanxiuMailRecord(
        mail_key="id:missing-visible",
        mail_id="missing-visible",
        title="资源领取通知",
        normalized_title="资源领取通知",
        create_time_text="2026年06月05日22:00",
        source="packet",
        status="missing_from_list",
        payload={"mail_rewards": []},
    )
    monkeypatch.setattr(runner, "_find_packet_mail_record", lambda _title, _time_text, **_kwargs: record)

    runner._prepare_mail_row_policy(row, action_policies={"delete"})

    assert row["mail_key"] == ""
    assert row["policy"] == ""
    assert row["packet_match"] == "ui_skipped"


def test_mail_ui_delete_probe_deletes_only_delete_detail(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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

    monkeypatch.setattr(runner, "_wait_mail_detail_or_list_scene", fake_wait_detail)
    monkeypatch.setattr(runner, "_wait_scene_id", fake_wait_scene)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked_points.append((x, y)))
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, _frame=None, **_kwargs: clicked_shapes.append(shape["title"]))
    monkeypatch.setattr(runner, "_update_packet_mail_action_for_row", lambda _row, **_kwargs: updated.append(_row["title"]))

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


def test_packet_claim_policy_clicks_claim_detail(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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

    monkeypatch.setattr(runner, "_wait_mail_detail_or_list_scene", fake_wait_detail)
    monkeypatch.setattr(runner, "_wait_scene_id", fake_wait_scene)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked_points.append((x, y)))
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, _frame=None, **_kwargs: clicked_shapes.append(shape["title"]))
    monkeypatch.setattr(runner, "_wait_shape_match", fake_wait_shape_match)
    monkeypatch.setattr(
        runner,
        "_update_packet_mail_action_for_row",
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


def test_packet_claim_requested_retries_after_cooldown(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    requested_at = datetime.fromtimestamp(time.time() - 120).strftime("%Y-%m-%d %H:%M:%S")
    record = FanxiuMailRecord(
        mail_key="id:claim-requested",
        mail_id="claim-requested",
        title="仙财福礼",
        normalized_title="仙财福礼",
        create_time_text="2026年06月07日12:00",
        source="packet",
        status="claim_requested",
        payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币", "amount": 1688}]},
        evidence={"runtime_requested_action": "claim", "runtime_action_requested_at": requested_at},
    )

    assert runner._visible_packet_mail_action_policy(record) == "claim"


def test_packet_claim_requested_waits_during_cooldown():
    runner = create_fanxiu_runtime_runner()
    requested_at = datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")
    record = FanxiuMailRecord(
        mail_key="id:claim-requested",
        mail_id="claim-requested",
        title="仙财福礼",
        normalized_title="仙财福礼",
        create_time_text="2026年06月07日12:00",
        source="packet",
        status="claim_requested",
        payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币", "amount": 1688}]},
        evidence={"runtime_requested_action": "claim", "runtime_action_requested_at": requested_at},
    )

    assert runner._visible_packet_mail_action_policy(record) == ""


def test_visible_mail_row_ignores_db_terminal_status_for_safe_packet(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    row = {"title": "仙财福礼", "time_text": "2026年06月07日12:00"}
    record = FanxiuMailRecord(
        mail_key="id:already-marked",
        mail_id="already-marked",
        title="仙财福礼",
        normalized_title="仙财福礼",
        create_time_text="2026年06月07日12:00",
        source="packet",
        status="deleted",
        payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币", "amount": 1688}]},
    )
    monkeypatch.setattr(runner, "_find_packet_mail_records_for_visible_row", lambda _title, _time_text: [record])
    monkeypatch.setattr(runner, "_find_packet_mail_record", lambda _title, _time_text, **_kwargs: record)

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["mail_key"] == "id:already-marked"
    assert row["policy"] == "claim"


def test_visible_mail_row_skips_when_any_same_title_time_packet_is_protected(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    row = {"title": "活动奖励未领取", "time_text": "2026年06月04日23:59"}
    safe = FanxiuMailRecord(
        mail_key="id:safe",
        mail_id="safe",
        title="活动奖励未领取",
        normalized_title="活动奖励未领取",
        create_time_text="2026年06月04日23:59",
        source="packet",
        status="seen",
        payload={"mail_rewards": [{"item_name": "天资丹", "item_type": "道具", "amount": 30}]},
    )
    protected = FanxiuMailRecord(
        mail_key="id:protected",
        mail_id="protected",
        title="活动奖励未领取",
        normalized_title="活动奖励未领取",
        create_time_text="2026年06月04日23:59",
        source="packet",
        status="seen",
        payload={"mail_rewards": [{"item_name": "炼丹灵草匣", "item_type": "资源", "amount": 25}]},
    )
    monkeypatch.setattr(runner, "_find_packet_mail_records_for_visible_row", lambda _title, _time_text: [safe, protected])
    monkeypatch.setattr(runner, "_find_packet_mail_record", lambda _title, _time_text, **_kwargs: safe)

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["mail_key"] == "id:safe"
    assert row["policy"] == ""


def test_scroll_shape_content_uses_half_page_slow_drag(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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

    runtime = runtime_runner_core.FanxiuRuntime(runner, ctx, stop_event=threading.Event())
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
    assert waits == [1.0]


def test_scroll_shape_content_can_limit_signature_to_recognition_shape(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, seconds=2.0, **_kwargs: waits.append(seconds) or iter(()))

    runtime = runtime_runner_core.FanxiuRuntime(runner, ctx, stop_event=threading.Event())

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
    assert signatures == ["识别区", "识别区"]
    assert len(drags) == 1
    sx, sy, ex, ey, duration_ms = drags[0]
    assert sx == ex == 450
    assert sy == 1120
    assert ey == 480
    assert duration_ms == 1500
    assert waits == [1.0]


def test_nudge_shape_content_for_box_only_drags_when_candidate_near_edge(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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

    runtime = runtime_runner_core.FanxiuRuntime(runner, ctx, stop_event=threading.Event())
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
    assert waits == [1.0]

    center_direction = runner._run_direct_runtime_action(
        lambda: runtime.nudge_shape_content_for_box(121, "邮件清单2", {"x": 120, "y": 760, "w": 80, "h": 40}),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert center_direction is None
    assert len(drags) == 1
    assert waits == [1.0]


def test_baiye_target_box_splits_merged_lord_text():
    runner = create_fanxiu_runtime_runner()

    box = runner._baiye_target_box_from_words(
        [
            {"text": "魔道仙弈", "x": 700.0, "y": 760.0, "w": 120.0, "h": 32.0, "line_index": 0},
        ],
        "魔道",
    )

    assert box == {"x": 700.0, "y": 760.0, "w": 60.0, "h": 32.0}


def test_baiye_target_box_right_anchors_wide_merged_lord_text():
    runner = create_fanxiu_runtime_runner()

    box = runner._baiye_target_box_from_words(
        [
            {"text": "剑魔道", "x": 309.3, "y": 742.0, "w": 590.7, "h": 46.0, "line_index": 0},
        ],
        "魔道",
    )

    assert box == pytest.approx({"x": 771.2, "y": 742.0, "w": 128.8, "h": 46.0})


def test_baiye_target_box_ignores_rule_text():
    runner = create_fanxiu_runtime_runner()

    box = runner._baiye_target_box_from_words(
        [
            {"text": "魔道法则", "x": 340.0, "y": 512.0, "w": 79.5, "h": 132.0, "line_index": 0},
            {"text": "魔道", "x": 840.0, "y": 746.0, "w": 56.0, "h": 39.0, "line_index": 1},
        ],
        "魔道",
    )
    line_box = runner._baiye_target_box_from_lines(
        [{"text": "魔道跨法则仙魁", "x": 300.0, "y": 510.0, "w": 250.0, "h": 60.0}],
        "魔道",
    )

    assert box == {"x": 840.0, "y": 746.0, "w": 56.0, "h": 39.0}
    assert line_box is None


def test_baiye_lord_click_point_uses_icon_center_above_text():
    runner = create_fanxiu_runtime_runner()

    click_x, click_y = runner._baiye_lord_click_point_from_box(
        {"x": 700.0, "y": 760.0, "w": 60.0, "h": 32.0},
        {},
    )

    assert click_x == pytest.approx(730.0)
    assert click_y == pytest.approx(716.8)


def test_baiye_lord_probe_only_does_not_click_target(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []

    class FakeRuntime:
        def cur_frame(self, *, update: bool = False):
            actions.append(("cur_frame", update))
            return "frame"

        def ocr_text(self, *_args, **_kwargs):
            return ""

        def ocr_words_in_shapes(self, *_args, **_kwargs):
            return [{"text": "魔道", "x": 700.0, "y": 760.0, "w": 60.0, "h": 32.0, "line_index": 0}]

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
    runner = create_fanxiu_runtime_runner()
    text = "历史记录 魔道法则之主 法则效果 虚位以待 剩余次数：0/1 已拜谒 本次拜谒可获得：天雷竹*1100"

    assert runner._baiye_text_is_completed(text) is True
    assert runner._baiye_text_is_lord_map(text) is True


class _BaiyeReturnRuntimeMixin:
    def current_scene(self, *_args, **_kwargs):
        self._actions.append(("current_scene", _args, _kwargs))
        if getattr(self, "_goto_world", False):
            return 34, 100.0, "frame"
        return 266, 100.0, "frame"

    def click_shape_center(self, *_args):
        self._actions.append(("click_shape_center", _args))

    def wait_any(self, *_args, **_kwargs):
        self._actions.append(("wait_any", _kwargs.get("label")))
        if False:
            yield None
        return "returned"

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
        self._goto_world = scene_id == 34
        if False:
            yield None
        return "success"


def test_baiye_lord_selection_short_circuits_when_already_completed(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []

    class FakeRuntime(_BaiyeReturnRuntimeMixin):
        def __init__(self):
            self._actions = actions

        def cur_frame(self, *, update: bool = False):
            actions.append(("cur_frame", update))
            return "frame"

        def ocr_text(self, *_args, **_kwargs):
            return "历史记录 魔道法则之主 剩余次数：0/1 已拜谒"

        def ocr_words_in_shapes(self, *_args, **_kwargs):
            actions.append(("ocr_words_in_shapes",))
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
    assert not any(action[0] == "ocr_words_in_shapes" for action in actions)
    assert not any(action[0] == "click_frame_point" for action in actions)
    assert ("goto_view", 34) in actions
    assert not any(action == ("click_shape_center", (266, "返回")) for action in actions)
    assert not any(action == ("click_shape_center", (265, "返回")) for action in actions)
    assert not any(action == ("click_shape_center", (264, "返回")) for action in actions)
    assert not any(action == ("click_shape_center", (69, "退出")) for action in actions)


def test_baiye_lord_selection_clicks_worship_button_after_target_selected(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ocr_texts = iter([
        "",
        "法则之主 魔道 剩余次数：1/1 拜谒 本次拜谒可获得：天雷竹*1100",
        "法则之主 魔道 剩余次数：0/1 已拜谒",
    ])

    class FakeRuntime(_BaiyeReturnRuntimeMixin):
        def __init__(self):
            self._actions = actions

        def cur_frame(self, *, update: bool = False):
            actions.append(("cur_frame", update))
            return "frame"

        def ocr_text(self, *_args, **_kwargs):
            return next(ocr_texts, "法则之主 魔道 剩余次数：0/1 已拜谒")

        def ocr_words_in_shapes(self, *_args, **_kwargs):
            return [{"text": "魔道", "x": 700.0, "y": 760.0, "w": 60.0, "h": 32.0, "line_index": 0}]

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
    assert any(action[0] == "click_frame_point" for action in actions)
    assert ("click_shape_center", (266, "拜谒")) in actions
    assert ("goto_view", 34) in actions
    assert not any(action == ("click_shape_center", (266, "返回")) for action in actions)
    assert not any(action == ("click_shape_center", (265, "返回")) for action in actions)
    assert not any(action == ("click_shape_center", (264, "返回")) for action in actions)
    assert not any(action == ("click_shape_center", (69, "退出")) for action in actions)


def test_baiye_lord_selection_clicks_worship_when_already_on_target_page(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
            return next(ocr_texts, "法则之主 魔道法则之主 剩余次数：0/1 已拜谒")

        def ocr_words_in_shapes(self, *_args, **_kwargs):
            actions.append(("ocr_words_in_shapes",))
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
    assert ("goto_view", 34) in actions
    assert not any(action == ("click_shape_center", (266, "返回")) for action in actions)
    assert not any(action == ("click_shape_center", (265, "返回")) for action in actions)
    assert not any(action == ("click_shape_center", (264, "返回")) for action in actions)
    assert not any(action == ("click_shape_center", (69, "退出")) for action in actions)
    assert not any(action[0] == "ocr_words_in_shapes" for action in actions)


def test_runtime_open_daily_entry_clicks_ocr_matched_row(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.08, "y": 0.18, "w": 0.84, "h": 0.66},
    ])
    ctx = {"images": {69: image69}, "entry": type("Entry", (), {"mode": "local"})()}
    clicked: list[tuple[float, float]] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _views=None: (69, 100.0))
    monkeypatch.setattr(
        runner,
        "_cached_ocr_lines",
        lambda _ctx, _frame: [
            {"text": "日常 活跃度 活动报名", "x": 80, "y": 150, "w": 300, "h": 40},
            {"text": "挑战或扫荡淬剑试炼", "x": 180, "y": 540, "w": 280, "h": 44},
            {"text": "0/1", "x": 650, "y": 540, "w": 80, "h": 44},
        ],
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))
    waits: list[float] = []
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, seconds=2.0, **_kwargs: waits.append(seconds) or iter(()))

    runtime = runtime_runner_core.FanxiuRuntime(runner, ctx, stop_event=threading.Event())
    result = runner._run_direct_runtime_action(
        lambda: runtime.open_daily_entry(label="日常_剑灵", title_pattern=r"淬剑试炼"),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "open"
    assert clicked == [(320.0, 562.0)]
    assert waits == [1.0]


def test_runtime_open_daily_entry_returns_done_when_row_progress_is_full(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.08, "y": 0.18, "w": 0.84, "h": 0.66},
    ])
    ctx = {"images": {69: image69}, "entry": type("Entry", (), {"mode": "local"})()}
    clicked: list[tuple[float, float]] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _views=None: (69, 100.0))
    monkeypatch.setattr(
        runner,
        "_cached_ocr_lines",
        lambda _ctx, _frame: [
            {"text": "日常 活跃度 活动报名", "x": 80, "y": 150, "w": 300, "h": 40},
            {"text": "挑战或扫荡混沌灵塔", "x": 180, "y": 620, "w": 280, "h": 44},
            {"text": "1/1", "x": 650, "y": 620, "w": 80, "h": 44},
        ],
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))

    runtime = runtime_runner_core.FanxiuRuntime(runner, ctx, stop_event=threading.Event())
    result = runner._run_direct_runtime_action(
        lambda: runtime.open_daily_entry(label="日常_灵塔", title_pattern=r"混沌灵塔"),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "done"
    assert clicked == []


def test_mail_ui_delete_probe_returns_from_claim_detail_without_claiming(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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


def test_daily_assistant_leaves_mail_scene_before_start(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image34 = _image("世界", "0034.png", [])
    image69 = _image("日常", "0069.png", [])
    image121 = _image("邮件", "0121.png", [
        {"id": "back", "kind": "rect", "title": "空白-返回", "x": 0.05, "y": 0.88, "w": 0.15, "h": 0.08},
    ])
    ctx = {
        "images": {34: image34, 69: image69, 121: image121},
        "asset_tree_path": Path("assets.json"),
    }
    runtime_calls: list[tuple] = []
    identify_calls = iter([(121, 100.0), (34, 100.0)])

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def fake_enter_daily(*_args, **_kwargs):
        yield BehaviorTreeStatus.RUNNING
        return 69

    def fake_open_daily_assistant(*_args, **_kwargs):
        yield BehaviorTreeStatus.RUNNING
        return "not_found"

    class FakeRuntime:
        def current_scene(self, view_ids=None, **kwargs):
            scene_id, score = next(identify_calls)
            runtime_calls.append(("current_scene", tuple(view_ids or ()), kwargs))
            return scene_id, score, "frame"

        def ocr_text(self, frame):
            runtime_calls.append(("ocr_text", frame))
            return ""

        def wait_click(self, view_id, shape, **kwargs):
            runtime_calls.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

        def wait_view(self, *view_ids, **kwargs):
            runtime_calls.append(("wait_view", view_ids, kwargs))
            if False:
                yield None
            return view_ids[0]

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: [])
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: next(identify_calls))
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_enter_daily_from_world_like", fake_enter_daily)
    monkeypatch.setattr(runner, "_open_daily_assistant_from_daily", fake_open_daily_assistant)
    monkeypatch.setattr(runner, "_record_scheduler_task_discovered_next_time", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="未找到小助手入口"):
        runner._run_direct_runtime_action(
            lambda: runner._execute_daily_assistant_task(ctx, FakeStopEvent(), {}),
            stop_event=FakeStopEvent(),
            tick_seconds=0.01,
        )

    action_calls = [call for call in runtime_calls if call[0] in {"wait_click", "wait_view"}]
    assert action_calls == [
        ("wait_click", 121, "空白-返回", {}),
        ("wait_view", (34,), {"label": "日常_助手：等待返回世界 #34"}),
    ]


def test_mail_list_lock_hint_skips_delete_probe(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    runner = create_fanxiu_runtime_runner()
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


def test_running_mail_manual_job_requeues_after_backend_reload():
    jobs = [
        {
            "id": "manual-mail",
            "task_type": "mail_cleanup",
            "label": "邮件_清理",
            "group": "manual_job",
            "status": "running",
            "interruptible": True,
            "payload": {"scan_mode": "full", "action_policies": ["delete"]},
            "created_at": 100.0,
            "updated_at": 110.0,
        },
        {
            "id": "manual-code",
            "task_type": "gift_code_redeem",
            "label": "兑换礼包码",
            "group": "manual_job",
            "status": "running",
            "interruptible": True,
            "payload": {"codes": ["abc"]},
            "created_at": 100.0,
            "updated_at": 110.0,
        },
    ]

    updated, changed_count = fanxiu_api.requeue_running_data_annotation_manual_jobs(jobs, now=200.0)

    assert changed_count == 2
    assert len(updated) == 1
    assert updated[0]["id"] == "manual-mail"
    assert updated[0]["status"] == "queued"
    assert updated[0]["last_requeue_reason"] == "backend_reload"


def test_mail_scan_keeps_scrolling_through_old_overlap_pages(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: [])
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


def test_mail_full_action_scan_continues_when_no_pending_packet_actions(monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: [])
    monkeypatch.setattr(runner, "_mail_rows_in_shape", fake_mail_rows)
    monkeypatch.setattr(runner, "_prepare_mail_row_policy", fake_prepare)
    monkeypatch.setattr(runner, "_pending_packet_mail_action_count", lambda **_kwargs: 0)

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
        initial_scene_id: int | None = None,
    ):
        self.claim_available = claim_available
        self.row_batches = list(row_batches or [])
        self.scroll_results = list(scroll_results or [])
        self.fail_first_reward_view = fail_first_reward_view
        self.scene_id = initial_scene_id
        self.actions: list[tuple[Any, ...]] = []

    def current_scene(self, view_ids=None, **_kwargs):
        return self.scene_id, 0.0, "frame"

    def cur_frame(self, **_kwargs):
        return "frame"

    def ocr_text(self, _frame):
        return ""

    def goto_view(self, view_id: int):
        self.actions.append(("goto", view_id))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    def shape_visible(self, view_id: int, shape: str, **_kwargs):
        return ("shape", view_id, shape)

    def ocr_contains(self, **kwargs):
        return ("ocr", kwargs)

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

    def ocr_row_clicks_in_shape(self, view_id: int, shape: str, **_kwargs):
        self.actions.append(("ocr_rows", view_id, shape))
        if self.row_batches:
            return self.row_batches.pop(0)
        return []

    def click_frame_point(self, view_id: int, x: float, y: float):
        self.actions.append(("point", view_id, x, y))

    def scroll_shape_content(self, view_id: int, shape: str, **_kwargs):
        self.actions.append(("scroll", view_id, shape))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return self.scroll_results.pop(0) if self.scroll_results else False


def test_daily_signup_flow_reads_like_business_steps():
    runner = create_fanxiu_runtime_runner()
    runtime = _FakeSignupRuntime(
        claim_available=True,
        initial_scene_id=34,
        row_batches=[
            [(720.0, 415.0, "丹道问鼎报名")],
            [(720.0, 637.5, "仙缘夺魁报名")],
            [],
        ],
        scroll_results=[False],
    )

    result = _drain_generator(runner.日常报名流程(runtime))

    assert result == {"result": "success", "claimed": 2}
    assert runtime.actions == [
        ("wait_click_then_view", 34, "日常", 69),
        ("wait_click", 75, "活动报名"),
        ("wait_view", 23),
        ("ocr_rows", 23, "报名列"),
        ("point", 23, 720.0, 415.0),
        ("wait_view", 24),
        ("wait_click", 24, "领取"),
        ("wait_view", 23),
        ("ocr_rows", 23, "报名列"),
        ("point", 23, 720.0, 637.5),
        ("wait_view", 24),
        ("wait_click", 24, "领取"),
        ("wait_view", 23),
        ("ocr_rows", 23, "报名列"),
        ("scroll", 23, "报名列"),
        ("ocr_rows", 23, "报名列"),
        ("scroll", 23, "报名列"),
        ("wait_click", 23, "返回"),
        ("goto", 34),
    ]


def test_daily_signup_flow_returns_world_when_already_done():
    runner = create_fanxiu_runtime_runner()
    runtime = _FakeSignupRuntime(claim_available=False)

    result = _drain_generator(runner.日常报名流程(runtime))

    assert result == {"result": "success", "claimed": 0, "already_done": True}
    assert runtime.actions == [("goto", 69), ("goto", 34)]


def test_daily_signup_list_scrolls_after_click_without_reward_page():
    runner = create_fanxiu_runtime_runner()
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

    assert result == 1
    assert runtime.actions == [
        ("ocr_rows", 23, "报名列"),
        ("point", 23, 720.0, 415.0),
        ("wait_view", 24),
        ("ocr_rows", 23, "报名列"),
        ("point", 23, 720.0, 415.0),
        ("wait_view", 24),
        ("wait_click", 24, "领取"),
        ("wait_view", 23),
        ("ocr_rows", 23, "报名列"),
        ("scroll", 23, "报名列"),
        ("ocr_rows", 23, "报名列"),
        ("scroll", 23, "报名列"),
        ("ocr_rows", 23, "报名列"),
        ("scroll", 23, "报名列"),
    ]


def test_daily_signup_flow_without_claims_returns_skipped():
    runner = create_fanxiu_runtime_runner()
    runtime = _FakeSignupRuntime(
        claim_available=True,
        row_batches=[[], []],
        scroll_results=[False, False],
    )

    result = _drain_generator(runner.日常报名流程(runtime))

    assert result["result"] == "skipped"
    assert result["claimed"] == 0
    assert "未领取任何报名项" in result["message"]


def test_runtime_image_signature_excludes_occlusion_marker_regions(tmp_path):
    runner = create_fanxiu_runtime_runner()
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
    from PIL import Image, ImageDraw

    def frame(notice_color: str, row_color: str) -> str:
        buffer = io.BytesIO()
        image = Image.new("RGB", (900, 1600), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 384, 900, 448), fill=notice_color)
        draw.rectangle((300, 560, 600, 620), fill=row_color)
        image.save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    runtime = runtime_runner_core.FanxiuRuntime(runner, ctx, asset_tree_path=path)
    baseline = runtime.image_signature_in_shape(23, "报名列", frame_data_url=frame("red", "blue"))
    occlusion_changed = runtime.image_signature_in_shape(23, "报名列", frame_data_url=frame("green", "blue"))
    row_changed = runtime.image_signature_in_shape(23, "报名列", frame_data_url=frame("red", "black"))

    assert occlusion_changed == baseline
    assert row_changed != baseline


def test_runtime_image_signature_similarity_allows_small_pixel_noise(tmp_path):
    runner = create_fanxiu_runtime_runner()
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

    runtime = runtime_runner_core.FanxiuRuntime(runner, ctx, asset_tree_path=path)
    baseline = runtime.image_signature_bytes_in_shape(23, "报名列", frame_data_url=frame(False))
    noisy = runtime.image_signature_bytes_in_shape(23, "报名列", frame_data_url=frame(True))

    assert runtime.image_signature_similarity(baseline, noisy) >= 95.0


def test_daily_scroll_window_unchanged_signature_ignores_occlusion_markers(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
        "asset_tree": [image69, {"type": "folder", "title": "遮挡标记", "children": [occlusion_image]}],
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
    assert waits == [1.0]
    assert any("签名未变化" in log["message"] for log in runner.status()["logs"])


def test_daily_xianyuan_excludes_xianyuan_duel_and_clicks_real_entry(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image69 = _image("日常", "0069.png", [
        {"id": "identity", "kind": "rect", "title": "日常", "x": 0.02, "y": 0.03, "w": 0.12, "h": 0.06},
        {"id": "list", "kind": "rect", "title": "滚动窗口", "x": 0.05, "y": 0.2, "w": 0.9, "h": 0.65},
        {"id": "exit", "kind": "rect", "title": "退出", "x": 0.04, "y": 0.9, "w": 0.1, "h": 0.06},
    ])
    ctx = {"entry": object(), "asset_tree": [], "asset_tree_path": path, "images": {69: image69}}
    ocr_calls = iter([
        [
            {"x": 330, "y": 330, "w": 180, "h": 35, "text": "仙缘斗法"},
            {"x": 330, "y": 560, "w": 180, "h": 35, "text": "挑战仙缘"},
        ],
    ])
    clicked: list[tuple[float, float]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: next(ocr_calls))
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))

    result = runner._run_direct_runtime_action(
        lambda: runner._open_daily_xianyuan_from_daily(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "open"
    assert clicked == [(420.0, 577.5)]


def test_daily_xianyuan_not_found_does_not_mark_success(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: [{"x": 300, "y": 500, "w": 200, "h": 30, "text": "仙缘斗法"}])
    monkeypatch.setattr(runner, "_drag_frame_point", lambda _ctx, _image, sx, sy, ex, ey, **_kwargs: dragged.append((sx, sy, ex, ey)))

    result = runner._run_direct_runtime_action(
        lambda: runner._open_daily_xianyuan_from_daily(
            ctx,
            FakeStopEvent(),
            {"max_scrolls": 1, "reverse_scrolls": 1},
        ),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "not_found"
    assert len(dragged) == 2


def test_daily_xianyuan_after_entry_returns_people_list_scene(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    scenes = iter([(None, 0.0), (None, 0.0)])
    stop_event = threading.Event()

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _preferred: next(scenes))
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
        lambda _frame: [
            {"text": "仙缘"},
            {"text": "可送礼雅妃可送礼炎帝萧炎"},
            {"text": "隐藏已无物品的仙缘"},
        ],
    )

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_daily_xianyuan_after_entry(ctx, stop_event, {"post_click_timeout": 1}),
        stop_event=stop_event,
        tick_seconds=0.01,
    )

    assert result == (197, 100.0)


def test_daily_xianyuan_people_list_target_candidates():
    runner = create_fanxiu_runtime_runner()
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


def test_daily_xianyuan_dialogue_runs_challenge_flow(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image199 = _image("仙缘人物对话", "0199.png", [
        {"id": "teach", "kind": "rect", "title": "教他做人", "x": 0.5, "y": 0.56, "w": 0.44, "h": 0.09},
    ])
    ctx = {"entry": object(), "asset_tree": [], "asset_tree_path": path, "images": {199: image199}}
    frame_iter = iter(["f0", "f1", "f2", "f3", "f4", "f5"])
    lines_by_frame = {
        "f0": [{"x": 610, "y": 940, "w": 150, "h": 40, "text": "教他做人"}],
        "f1": [{"x": 610, "y": 940, "w": 120, "h": 40, "text": "看招吧"}],
        "f2": [{"x": 610, "y": 940, "w": 120, "h": 40, "text": "看招吧"}],
        "f3": [{"x": 410, "y": 930, "w": 90, "h": 40, "text": "继续"}],
        "f4": [{"x": 300, "y": 880, "w": 260, "h": 40, "text": "友好度减少"}],
        "f5": [],
    }
    clicked: list[tuple[float, float]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frame_iter))
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: lines_by_frame[frame])
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, frame, _preferred: (34, 100.0) if frame == "f5" else (None, 0.0))
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1))))
    monkeypatch.setattr(runner, "_record_daily_xianyuan_done", lambda _payload, *, message: "2026-06-12 05:00:00")

    result = runner._run_direct_runtime_action(
        lambda: runner._run_daily_xianyuan_from_dialogue(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked[:3] == [(685.0, 960.0), (670.0, 960.0), (455.0, 950.0)]
    assert any(point == (450.0, 992.0) for point in clicked)


def test_daily_xianyuan_dialogue_advances_to_attack_then_world_like(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image199 = _image("仙缘人物对话", "0199.png", [
        {"id": "teach", "kind": "rect", "title": "教他做人", "x": 0.5, "y": 0.65, "w": 0.44, "h": 0.09},
    ])
    ctx = {"entry": object(), "images": {199: image199}}
    frames = iter(["dialogue", "challenge-count", "attack", "battle", "battle", "world"])
    lines_by_frame = {
        "dialogue": [{"x": 560, "y": 1060, "w": 180, "h": 55, "text": "教他做人"}],
        "challenge-count": [{"x": 390, "y": 1190, "w": 360, "h": 40, "text": "今日可挑战次数：3/3"}],
        "attack": [{"x": 520, "y": 960, "w": 260, "h": 55, "text": "看招吧（友好度-2000）"}],
        "battle": [{"x": 785, "y": 800, "w": 80, "h": 50, "text": "离开"}],
        "world": [{"text": "天机阁"}, {"text": "储物袋"}, {"text": "角色"}, {"text": "装备"}, {"text": "功法书"}],
    }
    clicked: list[tuple[float, float]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: lines_by_frame[frame])
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (None, 0.0))
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1))))
    monkeypatch.setattr(runner, "_record_daily_xianyuan_done", lambda _payload, *, message: "2026-06-12 05:00:00")

    result = runner._run_direct_runtime_action(
        lambda: runner._run_daily_xianyuan_from_dialogue(ctx, FakeStopEvent(), {"challenge_continue_timeout": -1}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [
        (650.0, 1087.5),
        (650.0, 987.5),
        (825.0, 825.0),
    ]


def test_daily_xianyuan_can_resume_from_challenge_dialogue(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image200 = _image("仙缘挑战对话", "0200.png")
    ctx = {"entry": object(), "images": {200: image200}}
    frames = iter(["attack-id", "attack", "battle", "battle", "world"])
    lines_by_frame = {
        "attack-id": [{"x": 390, "y": 1190, "w": 360, "h": 40, "text": "今日可挑战次数：3/3"}],
        "attack": [{"x": 520, "y": 960, "w": 260, "h": 55, "text": "看招吧（友好度-2000）"}],
        "battle": [{"x": 785, "y": 800, "w": 80, "h": 50, "text": "离开"}],
        "world": [{"text": "天机阁"}, {"text": "储物袋"}, {"text": "角色"}, {"text": "装备"}, {"text": "功法书"}],
    }
    clicked: list[tuple[float, float]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: lines_by_frame[frame])
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, frame, _preferred: (200, 100.0) if frame == "attack-id" else (None, 0.0))
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1))))
    monkeypatch.setattr(runner, "_record_daily_xianyuan_done", lambda _payload, *, message: "2026-06-12 05:00:00")

    result = runner._run_direct_runtime_action(
        lambda: runner._run_daily_xianyuan_from_challenge_state(ctx, FakeStopEvent(), {"challenge_continue_timeout": -1}, 200),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(650.0, 987.5), (825.0, 825.0)]


def test_daily_xianyuan_count_empty_records_before_returning_world(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image200 = _image("仙缘挑战对话", "0200.png")
    ctx = {"entry": object(), "images": {200: image200}}
    frames = iter(["challenge-id", "count-empty"])
    calls: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, frame, _preferred: (200, 100.0) if frame == "challenge-id" else (None, 0.0))
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "今日可挑战次数：0/3"}] if frame == "count-empty" else [])

    def fake_return_current(_ctx, _stop_event):
        calls.append("return-world")
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    def fake_record(_payload, *, message):
        calls.append(f"record:{message}")
        return "2026-06-12 05:00:00"

    monkeypatch.setattr(runner, "_return_daily_xianyuan_current_to_world", fake_return_current)
    monkeypatch.setattr(runner, "_record_daily_xianyuan_done", fake_record)

    result = runner._run_direct_runtime_action(
        lambda: runner._run_daily_xianyuan_from_challenge_state(ctx, FakeStopEvent(), {}, 200),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert calls == ["record:仙缘对话显示今日可挑战次数已空", "return-world"]


def test_daily_xianyuan_battle_leave_accepts_world_like_text(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ref_image = _image("仙缘人物对话", "0199.png")
    clicked: list[tuple[float, float]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "world-frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (None, 0.0))
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
        lambda _frame: [
            {"text": "天机阁"},
            {"text": "储物袋"},
            {"text": "角色"},
            {"text": "装备"},
            {"text": "功法书"},
        ],
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((x, y)))

    result = runner._run_direct_runtime_action(
        lambda: runner._leave_daily_xianyuan_battle({}, FakeStopEvent(), {}, ref_image),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == []


def test_daily_xianyuan_return_from_daily_accepts_world_like_text(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image69 = _image("日常", "0069.png", [
        {"id": "exit", "kind": "rect", "title": "退出", "x": 0.05, "y": 0.93, "w": 0.08, "h": 0.05},
    ])
    ctx = {"images": {69: image69}}
    frames = iter(["daily", "world-like"])
    clicked: list[tuple[float, float]] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, frame, _preferred: (69, 100.0) if frame == "daily" else (None, 0.0))
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
        lambda frame: [] if frame == "daily" else [
            {"text": "天机阁"},
            {"text": "储物袋"},
            {"text": "角色"},
            {"text": "装备"},
            {"text": "功法书"},
        ],
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked.append((round(x, 1), round(y, 1))))

    result = runner._run_direct_runtime_action(
        lambda: runner._return_daily_xianyuan_to_world(ctx, FakeStopEvent()),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == [(81.0, 1528.0)]


def test_daily_signup_does_not_log_ready_when_scene_confirm_fails(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    ctx = {"entry": object(), "asset_tree": [], "asset_tree_path": path, "images": {}}

    class FakeStopEvent:
        def is_set(self):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_go_scene_task", lambda _ctx, _path, _target, _stop_event: "error")

    stop_event = FakeStopEvent()
    with pytest.raises(RuntimeError, match="前往 #69 失败"):
        runner._run_direct_runtime_action(
            lambda: runner._execute_runtime_task(ctx, "daily_signup", {}, stop_event),
            stop_event=stop_event,
            tick_seconds=0.01,
        )

    assert all("已到达日常 #69" not in log["message"] for log in runner.status()["logs"])


def test_runtime_service_task_start_requires_fanxiu_runtime_scope(monkeypatch):
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

    def fake_start_runtime_task(entry_arg, req):
        calls.append((entry_arg.entry_id, req.task_type))
        return {
            "ok": True,
            "status": "done",
            "entry_id": entry_arg.entry_id,
            "task_type": req.task_type,
            "message": "done",
        }

    monkeypatch.setattr(fanxiu_api, "_start_data_annotation_runtime_task", fake_start_runtime_task)
    client = _build_service_client(session)
    payload = {"entry_id": "entry-1", "task_type": "daily_signup", "payload": {}}

    forbidden = client.post(
        "/api/fanxiu/data-annotation/runtime/service/task/start",
        headers={"Authorization": f"Bearer {ocr_token['plaintext_value']}"},
        json=payload,
    )
    ok = client.post(
        "/api/fanxiu/data-annotation/runtime/service/task/start",
        headers={"Authorization": f"Bearer {runtime_token['plaintext_value']}"},
        json=payload,
    )

    assert forbidden.status_code == 403
    assert ok.status_code == 200
    assert ok.json()["task_type"] == "daily_signup"
    assert calls == [("entry-1", "daily_signup")]


def test_scheduler_success_fact_clears_stale_retry_after(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.api.fanxiu._data_annotation_runtime_dir", lambda: tmp_path)
    _write_data_annotation_world_facts({
        "discoveries": {
            "task": {
                "daily": {
                    "id": "daily",
                    "retry_after": "2026-06-04 19:26:34",
                    "next_time": None,
                }
            }
        },
        "events": [],
    })

    _record_data_annotation_scheduler_task_fact({
        "id": "daily",
        "task_type": "daily_signup",
        "label": "日常_报名",
        "source": "data_annotation_runtime",
        "schedule_kind": "daily",
        "last_run_at": "2026-06-04 19:29:45",
        "next_time": "2026-06-05 05:00:00",
        "retry_after": None,
    }, "success")

    fact = _read_data_annotation_world_facts()["discoveries"]["task"]["daily"]
    assert fact["last_result"] == "success"
    assert fact["next_time"] == "2026-06-05 05:00:00"
    assert fact["retry_after"] is None


def test_xianfu_visit_partner_cd_parser():
    assert runtime_runner_core._parse_xianfu_visit_cd_seconds("06:33:27后可免费抽取") == 23607
    assert runtime_runner_core._parse_xianfu_visit_cd_seconds("12分05秒后可免费抽取") == 725
    assert runtime_runner_core._parse_xianfu_visit_cd_seconds("免费抽取") == 0
    assert runtime_runner_core._parse_xianfu_visit_cd_seconds("无法识别") is None


def test_daily_boss_reward_and_cd_parsers():
    assert runtime_runner_core._parse_daily_boss_reward_remaining("剩余奖励次数：3/3+") == 3
    assert runtime_runner_core._parse_daily_boss_reward_remaining("剩余奖励次数：0") == 0
    assert runtime_runner_core._parse_daily_boss_reward_remaining("前往挑战") is None
    assert runtime_runner_core._parse_daily_boss_cd_seconds("06:33:27后刷新") == 23607
    assert runtime_runner_core._parse_daily_boss_hp_percent("首领 命20% 自动战斗中") == 20


def test_daily_boss_detail_clicks_challenge_when_available(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", lambda *_args, **_kwargs: [{"text": "神识注视剩余奖励次数：3/3前往挑战"}])
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, *_args, **_kwargs: clicked.append(shape["title"]))

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
    assert clicked == ["前往挑战"]


def test_daily_boss_detail_records_next_reset_when_reward_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 11, 10, 0, 0))
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image179 = _image("首领详情", "0179.png", [
        {"id": "watch", "kind": "rect", "title": "神识注视", "x": 0.35, "y": 0.5, "w": 0.5, "h": 0.06},
        {"id": "reward", "kind": "rect", "title": "剩余奖励次数", "x": 0.3, "y": 0.78, "w": 0.4, "h": 0.06},
        {"id": "action", "kind": "rect", "title": "挑战状态", "x": 0.25, "y": 0.82, "w": 0.5, "h": 0.07},
    ])
    ctx = {"asset_tree_path": path, "images": {179: image179}}

    class FakeStopEvent:
        def is_set(self):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", lambda *_args, **_kwargs: [{"text": "神识注视剩余奖励次数：0/3"}])

    def fake_return_world(_ctx, _stop_event):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_return_daily_boss_to_world", fake_return_world)

    result = runner._run_direct_runtime_action(
        lambda: runner._handle_daily_boss_detail(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    fact = fanxiu_api._read_data_annotation_world_facts()["discoveries"]["task"]["daily-boss"]
    assert fact["discovered_next_time"] == "2026-06-12 05:00:00"
    assert fact["task_type"] == "daily_boss"


def test_daily_boss_watched_list_cd_returns_skipped_retry_after(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 11, 21, 56, 0))
    runner = create_fanxiu_runtime_runner()
    image178 = _image("首领列表", "0178.png", [
        {"id": "list", "kind": "rect", "title": "首领列表", "x": 0.05, "y": 0.15, "w": 0.9, "h": 0.65},
    ])
    ctx = {
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {178: image178},
    }

    class FakeRuntime:
        def ocr_text(self, **_kwargs):
            return "剩余奖励次数：2/3"

    class FakeStopEvent:
        def is_set(self):
            return False

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_daily_boss_reward_remaining_from_scene", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(runner, "_daily_boss_refresh_cd_from_list", lambda _ctx: (9, "00:00:09"))

    result = runner._run_direct_runtime_action(
        lambda: runner._open_watched_daily_boss_detail(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "skipped"
    fact = fanxiu_api._read_data_annotation_world_facts()["discoveries"]["task"]["daily-boss"]
    assert fact["discovered_next_time"] is None
    assert fact["next_time"] is None
    assert fact["discovered_retry_after"] == "2026-06-11 21:57:00"
    assert fact["retry_after"] == "2026-06-11 21:57:00"
    assert fact["last_result"] == "skipped"


def test_daily_boss_after_challenge_requires_done_scene_before_success(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 11, 11, 0, 0))
    runner = create_fanxiu_runtime_runner()
    ctx = {"images": {}, "asset_tree_path": tmp_path / "asset_tree.json"}
    scenes = iter([(180, 100.0, "fight"), (None, 0.0, "cd-only"), (181, 100.0, "done")])
    phases: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: next(scenes))

    def fake_text(_ctx, frame=None):
        return "首领自动战斗中" if frame == "fight" else "00:07:17后刷新封印泷尊剑主" if frame == "done" else "00:07:17后刷新首领伤害数据统计"

    monkeypatch.setattr(runner, "_daily_boss_status_text_from_frame", fake_text)
    completed_after_done: list[bool] = []

    def fake_complete(_ctx, _stop_event, _payload):
        completed_after_done.append(True)
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_complete_daily_boss_from_done_frame", fake_complete)
    original_set_status = runner._set_status_locked

    def record_status(status, message="", **extra):
        if extra.get("phase"):
            phases.append(extra["phase"])
        original_set_status(status, message, **extra)

    monkeypatch.setattr(runner, "_set_status_locked", record_status)

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_daily_boss_after_challenge(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert "daily_boss_wait_boss_done" in phases
    assert "daily_boss_wait_done_after_cd" in phases
    assert completed_after_done == [True]


def test_daily_boss_stuck_at_twenty_percent_leaves_and_rechecks_rewards(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 11, 11, 0, 0))
    runner = create_fanxiu_runtime_runner()
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
    scenes = iter([(180, 100.0, "fight-1"), (180, 100.0, "fight-2")])
    clicked: list[str] = []
    waited: list[int] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: next(scenes))
    monkeypatch.setattr(runner, "_daily_boss_status_text_from_frame", lambda _ctx, frame=None: "首领 命20% 自动战斗中")
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, *_args, **_kwargs: clicked.append(shape["title"]))
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "list-frame")
    def fake_identify_scene(_ctx, _frame, view_ids=None):
        waited.extend(list(view_ids or ()))
        return 178, 100.0

    monkeypatch.setattr(runner, "_identify_scene_number", fake_identify_scene)
    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", lambda *_args, **_kwargs: [{"text": "剩余奖励次数：0"}])
    def fake_return_world(_ctx, _stop_event):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_return_daily_boss_to_world", fake_return_world)

    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_daily_boss_after_challenge(ctx, FakeStopEvent(), {"boss_twenty_percent_stuck_count": 2}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == ["离开"]
    assert waited == [178]
    fact = fanxiu_api._read_data_annotation_world_facts()["discoveries"]["task"]["daily-boss"]
    assert fact["discovered_next_time"] == "2026-06-12 05:00:00"


def test_daily_boss_stuck_on_boss_map_leaves_and_rechecks_rewards(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 11, 11, 0, 0))
    runner = create_fanxiu_runtime_runner()
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
    scenes = iter([(180, 100.0, "map-1"), (180, 100.0, "map-2")])
    clicked: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: next(scenes))
    monkeypatch.setattr(runner, "_daily_boss_status_text_from_frame", lambda _ctx, frame=None: "首领·泷尊剑主 100% 数据统计 离开")
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, *_args, **_kwargs: clicked.append(shape["title"]))
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "list-frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, view_ids=None: (178, 100.0))
    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", lambda *_args, **_kwargs: [{"text": "剩余奖励次数：0"}])
    def fake_return_world(_ctx, _stop_event):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_return_daily_boss_to_world", fake_return_world)

    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = runner._run_direct_runtime_action(
        lambda: runner._wait_daily_boss_after_challenge(ctx, FakeStopEvent(), {"boss_map_stuck_count": 2}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == ["离开"]
    fact = fanxiu_api._read_data_annotation_world_facts()["discoveries"]["task"]["daily-boss"]
    assert fact["discovered_next_time"] == "2026-06-12 05:00:00"


def test_daily_boss_reopens_list_after_leave_lands_on_world(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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


def test_daily_boss_returns_to_world_after_list_completion(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    image182 = _image("0182.png", "0182.png", [])
    ctx = {"asset_tree_path": path, "images": {69: image69, 178: image178, 179: image179, 180: image180, 181: image181, 182: image182}}
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
    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", lambda *_args, **_kwargs: [{"text": "剩余奖励次数：0"}])

    def fake_return(_ctx, _stop_event):
        returned.append(True)
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_return_daily_boss_to_world", fake_return)

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_boss_task(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert returned == [True]


def test_daily_boss_done_rechecks_rewards_after_half_hour_even_when_list_cd_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 11, 11, 0, 0))
    runner = create_fanxiu_runtime_runner()
    image178 = _image("首领列表", "0178.png", [
        {"id": "reward", "kind": "rect", "title": "剩余奖励次数", "x": 0.3, "y": 0.8, "w": 0.4, "h": 0.06},
        {"id": "list", "kind": "rect", "title": "首领列表", "x": 0.05, "y": 0.15, "w": 0.9, "h": 0.65},
    ])
    image182 = _image("0182.png", "0182.png", [
        {"id": "refresh", "kind": "rect", "title": "刷新时间", "x": 0.06, "y": 0.49, "w": 0.86, "h": 0.09},
    ])
    ctx = {
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {178: image178, 182: image182},
    }

    class FakeStopEvent:
        def is_set(self):
            return False

    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: (178, 100.0, "list-frame"))
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "list-frame")

    def fake_ocr_lines_in_shapes(_frame, image, names, **_kwargs):
        if image is image178 and "剩余奖励次数" in names:
            return [{"text": "剩余奖励次数：1"}]
        if image is image182 and "刷新时间" in names:
            return [{"text": "刷新时间 00:07:17"}]
        return []

    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", fake_ocr_lines_in_shapes)

    def fake_return_world(_ctx, _stop_event):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_return_daily_boss_to_world", fake_return_world)

    result = runner._run_direct_runtime_action(
        lambda: runner._complete_daily_boss_from_done_frame(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "skipped"
    fact = fanxiu_api._read_data_annotation_world_facts()["discoveries"]["task"]["daily-boss"]
    assert fact["discovered_next_time"] is None
    assert fact["next_time"] is None
    assert fact["discovered_retry_after"] == "2026-06-11 11:30:00"
    assert fact["retry_after"] == "2026-06-11 11:30:00"
    assert fact["last_result"] == "skipped"


def test_daily_boss_done_falls_back_to_half_hour_when_list_cd_unread(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 11, 11, 0, 0))
    runner = create_fanxiu_runtime_runner()
    image178 = _image("首领列表", "0178.png", [
        {"id": "reward", "kind": "rect", "title": "剩余奖励次数", "x": 0.3, "y": 0.8, "w": 0.4, "h": 0.06},
    ])
    image182 = _image("0182.png", "0182.png", [
        {"id": "refresh", "kind": "rect", "title": "刷新时间", "x": 0.06, "y": 0.49, "w": 0.86, "h": 0.09},
    ])
    ctx = {
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {178: image178, 182: image182},
    }

    class FakeStopEvent:
        def is_set(self):
            return False

    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: (178, 100.0, "list-frame"))
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "list-frame")

    def fake_ocr_lines_in_shapes(_frame, image, names, **_kwargs):
        if image is image178 and "剩余奖励次数" in names:
            return [{"text": "剩余奖励次数：1"}]
        return [{"text": ""}]

    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", fake_ocr_lines_in_shapes)

    def fake_return_world(_ctx, _stop_event):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_return_daily_boss_to_world", fake_return_world)

    result = runner._run_direct_runtime_action(
        lambda: runner._complete_daily_boss_from_done_frame(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "skipped"
    fact = fanxiu_api._read_data_annotation_world_facts()["discoveries"]["task"]["daily-boss"]
    assert fact["discovered_next_time"] is None
    assert fact["next_time"] is None
    assert fact["discovered_retry_after"] == "2026-06-11 11:30:00"
    assert fact["retry_after"] == "2026-06-11 11:30:00"
    assert fact["last_result"] == "skipped"


def test_daily_boss_done_marks_success_when_last_reward_was_challenged(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 11, 11, 0, 0))
    runner = create_fanxiu_runtime_runner()
    image178 = _image("首领列表", "0178.png", [
        {"id": "reward", "kind": "rect", "title": "剩余奖励次数", "x": 0.3, "y": 0.8, "w": 0.4, "h": 0.06},
    ])
    ctx = {
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {178: image178},
    }

    class FakeStopEvent:
        def is_set(self):
            return False

    monkeypatch.setattr(runner, "_current_scene_number", lambda _ctx: (178, 100.0, "list-frame"))
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "list-frame")

    def fake_ocr_lines_in_shapes(_frame, _image, _names, **_kwargs):
        return [{"text": ""}]

    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", fake_ocr_lines_in_shapes)

    def fake_return_world(_ctx, _stop_event):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_return_daily_boss_to_world", fake_return_world)

    result = runner._run_direct_runtime_action(
        lambda: runner._complete_daily_boss_from_done_frame(
            ctx,
            FakeStopEvent(),
            {"_daily_boss_challenge_remaining": 1},
        ),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    fact = fanxiu_api._read_data_annotation_world_facts()["discoveries"]["task"]["daily-boss"]
    assert fact["discovered_next_time"] == "2026-06-12 05:00:00"
    assert fact.get("discovered_retry_after") is None
    assert fact["last_result"] == "success"


def test_manual_daily_boss_success_does_not_mark_scheduler_success(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 11, 11, 0, 0))
    task = {
        "id": "daily-boss",
        "task_type": "daily_boss",
        "label": "日常_首领",
        "source": "data_annotation_runtime",
        "schedule_kind": "daily",
        "schedule_times": ["05:00"],
        "enabled": False,
        "last_result": "stopped",
        "retry_after": "2026-06-11 11:30:00",
        "next_time": "2026-06-11 05:00:00",
        "payload": {"__scheduler_definition_task_type": "daily_boss"},
    }
    runtime_runner_core._write_data_annotation_scheduler_tasks([task])
    runner = create_fanxiu_runtime_runner()

    runner._mark_matching_scheduler_tasks_for_manual_success("daily_boss", {})

    saved = next(item for item in runtime_runner_core._read_data_annotation_scheduler_tasks() if item["id"] == "daily-boss")
    assert saved["last_result"] == "stopped"
    assert saved["retry_after"] == "2026-06-11 11:30:00"
    assert saved["next_time"] == "2026-06-11 05:00:00"


def test_scheduler_mark_success_prefers_runtime_discovered_next_time(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 12, 12, 0, 0))
    task = {
        "id": "daily-boss",
        "task_type": "daily_boss",
        "label": "日常_首领",
        "source": "data_annotation_runtime",
        "schedule_kind": "daily",
        "schedule_times": ["05:00"],
        "enabled": True,
        "last_result": "running",
        "retry_after": None,
        "next_time": "2026-06-12 05:00:00",
        "payload": {"__scheduler_definition_task_type": "daily_boss"},
    }
    runtime_runner_core._write_data_annotation_scheduler_tasks([task])
    runner = create_fanxiu_runtime_runner()
    runner._record_scheduler_task_discovered_next_time(
        "daily-boss",
        "2026-06-12 12:36:36",
        task_type="daily_boss",
        label="日常_首领",
    )

    runner._mark_scheduler_task([task], "daily-boss", "success")

    saved = runtime_runner_core._read_data_annotation_scheduler_tasks()[0]
    assert saved["next_time"] == "2026-06-12 12:36:36"
    assert saved["last_result"] == "success"


def test_scheduler_mark_skipped_prefers_runtime_discovered_retry_after(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 12, 12, 0, 0))
    task = {
        "id": "daily-boss",
        "task_type": "daily_boss",
        "label": "日常_首领",
        "source": "data_annotation_runtime",
        "schedule_kind": "daily",
        "schedule_times": ["05:00"],
        "enabled": True,
        "last_result": "running",
        "retry_after": None,
        "next_time": "2026-06-12 05:00:00",
        "payload": {"__scheduler_definition_task_type": "daily_boss"},
    }
    runtime_runner_core._write_data_annotation_scheduler_tasks([task])
    runner = create_fanxiu_runtime_runner()
    runner._record_scheduler_task_discovered_next_time(
        "daily-boss",
        "2026-06-13 05:00:00",
        task_type="daily_boss",
        label="日常_首领",
    )
    runner._record_scheduler_task_discovered_retry_after(
        "daily-boss",
        "2026-06-12 12:30:00",
        task_type="daily_boss",
        label="日常_首领",
    )

    runner._mark_scheduler_task([task], "daily-boss", "skipped")

    saved = runtime_runner_core._read_data_annotation_scheduler_tasks()[0]
    assert saved["next_time"] is None
    assert saved["retry_after"] == "2026-06-12 12:30:00"
    assert saved["last_result"] == "skipped"
    fact = fanxiu_api._read_data_annotation_world_facts()["discoveries"]["task"]["daily-boss"]
    assert "discovered_next_time" not in fact
    assert fact["next_time"] is None
    assert fact["discovered_retry_after"] == "2026-06-12 12:30:00"
    assert fact["retry_after"] == "2026-06-12 12:30:00"


def test_scheduler_success_clears_stale_retry_after_fact(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 12, 12, 0, 0))
    task = {
        "id": "daily-boss",
        "task_type": "daily_boss",
        "label": "日常_首领",
        "source": "data_annotation_runtime",
        "schedule_kind": "dynamic",
        "schedule_times": ["05:00"],
        "enabled": True,
        "last_result": "running",
        "retry_after": None,
        "next_time": None,
        "payload": {"__scheduler_definition_task_type": "daily_boss"},
    }
    runtime_runner_core._write_data_annotation_scheduler_tasks([task])
    runner = create_fanxiu_runtime_runner()
    runner._record_scheduler_task_discovered_retry_after(
        "daily-boss",
        "2026-06-12 12:30:00",
        task_type="daily_boss",
        label="日常_首领",
    )
    runner._record_scheduler_task_discovered_next_time(
        "daily-boss",
        "2026-06-13 05:00:00",
        task_type="daily_boss",
        label="日常_首领",
    )

    runner._mark_scheduler_task([task], "daily-boss", "success")

    saved = runtime_runner_core._read_data_annotation_scheduler_tasks()[0]
    assert saved["next_time"] == "2026-06-13 05:00:00"
    assert saved["retry_after"] is None
    assert saved["last_result"] == "success"
    fact = fanxiu_api._read_data_annotation_world_facts()["discoveries"]["task"]["daily-boss"]
    assert fact["discovered_next_time"] == "2026-06-13 05:00:00"
    assert "discovered_retry_after" not in fact
    assert fact["retry_after"] is None


def test_xianfu_visit_partner_max_continue_zero_closes_popup(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", lambda *_args, **_kwargs: [{"text": "50（半价）"}])

    def fake_wait_view(*views, **_kwargs):
        view_ids = tuple(int(view) for view in views)
        wait_calls.append(view_ids)
        if view_ids == (175,):
            if False:
                yield BehaviorTreeStatus.RUNNING
            return runtime_runner_core.View(image175)
        if view_ids == (174,):
            if False:
                yield BehaviorTreeStatus.RUNNING
            return runtime_runner_core.View(image174)
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


def test_xianfu_learn_skill_cd_parser_reuses_free_draw_cd_text():
    assert runtime_runner_core._parse_xianfu_skill_cd_seconds("06:33:27后可免费抽取") == 23607
    assert runtime_runner_core._parse_xianfu_skill_cd_seconds("12分05秒后可免费抽取") == 725
    assert runtime_runner_core._parse_xianfu_skill_cd_seconds("免费抽取") == 0
    assert runtime_runner_core._parse_xianfu_skill_cd_seconds("攻击+18.9兆御+7282.42万免费抽取后重新倒计时") == 0
    assert runtime_runner_core._parse_xianfu_skill_cd_seconds("无法识别") is None


def test_xianfu_learn_skill_skips_when_skill_page_annotation_missing(tmp_path):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")

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


def test_xianfu_learn_skill_skips_when_price_is_not_free_without_cd(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 11, 1, 0, 0))
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image176 = _image("绝技", "0176.png", [
        {"id": "status", "kind": "rect", "title": "状态", "x": 0.1, "y": 0.78, "w": 0.4, "h": 0.06},
        {"id": "price", "kind": "rect", "title": "价格", "x": 0.2, "y": 0.7, "w": 0.2, "h": 0.05},
        {"id": "xianpin", "kind": "rect", "title": "仙品绝技", "x": 0.72, "y": 0.82, "w": 0.1, "h": 0.16},
    ])
    ctx = {"asset_tree_path": path, "images": {176: image176}}
    clicked: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _preferred=None: (176, 100.0))
    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", lambda *_args, **_kwargs: [{"text": "200领悟一次"}])
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, *_args, **_kwargs: clicked.append(shape["title"]))
    monkeypatch.setattr(runner, "_go_scene_task", lambda *_args, **_kwargs: "success")

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_xianfu_learn_skill_task(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "skipped"
    assert clicked == ["仙品绝技"]
    assert any("未识别到免费领悟或倒计时" in log["message"] for log in runner.status()["logs"])
    fact = fanxiu_api._read_data_annotation_world_facts()["discoveries"]["task"]["xianfu-learn-skill"]
    assert fact["discovered_next_time"] == "2026-06-11 01:30:00"
    assert fact["task_type"] == "xianfu_learn_skill"


def test_xianfu_learn_skill_existing_cd_is_not_marked_success(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 11, 1, 0, 0))
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image176 = _image("绝技", "0176.png", [
        {"id": "status", "kind": "rect", "title": "状态", "x": 0.1, "y": 0.78, "w": 0.4, "h": 0.06},
        {"id": "price", "kind": "rect", "title": "价格", "x": 0.2, "y": 0.7, "w": 0.2, "h": 0.05},
        {"id": "learn", "kind": "rect", "title": "领悟一次", "x": 0.16, "y": 0.742, "w": 0.285, "h": 0.06},
        {"id": "xianpin", "kind": "rect", "title": "仙品绝技", "x": 0.72, "y": 0.82, "w": 0.1, "h": 0.16},
    ])
    ctx = {"asset_tree_path": path, "images": {176: image176}}
    clicked: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _preferred=None: (176, 100.0))
    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", lambda *_args, **_kwargs: [{"text": "23:59:17后可免费抽取"}])
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, *_args, **_kwargs: clicked.append(shape["title"]))
    monkeypatch.setattr(runner, "_go_scene_task", lambda *_args, **_kwargs: "success")

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_xianfu_learn_skill_task(ctx, FakeStopEvent(), {"__scheduler_task_id": "xianfu-learn-skill"}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "skipped"
    assert clicked == []
    assert any("本轮未点击领悟" in log["message"] for log in runner.status()["logs"])
    fact = fanxiu_api._read_data_annotation_world_facts()["discoveries"]["task"]["xianfu-learn-skill"]
    assert fact["discovered_retry_after"] == "2026-06-12 00:59:17"
    assert fact["retry_after"] == "2026-06-12 00:59:17"
    assert fact["task_type"] == "xianfu_learn_skill"


def test_scheduler_success_uses_discovered_dynamic_next_time(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    runner = create_fanxiu_runtime_runner()
    task = {
        "id": "xianfu-visit-partner",
        "task_type": "xianfu_visit_partner",
        "label": "仙府_寻访仙侣",
        "source": "data_annotation_runtime",
        "schedule_kind": "dynamic",
        "enabled": False,
        "interruptible": True,
        "next_time": None,
        "schedule_times": [],
        "window": None,
        "last_run_at": "2026-06-10 21:00:00",
        "last_result": "running",
        "retry_after": None,
        "cooldown_seconds": 600,
        "payload": {"__scheduler_definition_task_type": "xianfu_visit_partner"},
        "checkpoint": None,
    }
    fanxiu_api._write_data_annotation_scheduler_tasks([task])
    runner._record_scheduler_task_discovered_next_time(
        "xianfu-visit-partner",
        "2026-06-11 04:33:27",
        task_type="xianfu_visit_partner",
        label="仙府_寻访仙侣",
    )
    runner._mark_scheduler_task([dict(task)], "xianfu-visit-partner", "success")

    saved = next(item for item in fanxiu_api._read_data_annotation_scheduler_tasks() if item["id"] == "xianfu-visit-partner")
    assert saved["last_result"] == "success"
    assert saved["next_time"] == "2026-06-11 04:33:27"
    fact = fanxiu_api._read_data_annotation_world_facts()["discoveries"]["task"]["xianfu-visit-partner"]
    assert fact["discovered_next_time"] == "2026-06-11 04:33:27"
    assert fact["next_time"] == "2026-06-11 04:33:27"


def test_unknown_scene_frame_reports_missing_annotation_without_writing_asset_tree(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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


def test_runtime_logs_include_current_scope_and_item_id():
    runner = create_fanxiu_runtime_runner()

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
    runner = create_fanxiu_runtime_runner()

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


def test_manual_runtime_task_runs_inside_resident_service_without_worker_thread(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    executed: list[str] = []
    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [])
    monkeypatch.setattr(runner, "_index_images", lambda _tree: {})
    monkeypatch.setattr(runner, "_require_assets", lambda _ctx: None)
    monkeypatch.setattr(runner, "_execute_runtime_task", lambda _ctx, task_type, _payload, _stop_event: executed.append(task_type) or "success")
    monkeypatch.setattr(runner, "_persist_status", lambda: None)
    monkeypatch.setattr(runtime_runner_core, "_remove_data_annotation_manual_job", lambda _task_id: None)
    with runner._lock:
        runner._guard_enabled = True
        runner._guard_entry_id = "entry"
        runner._guard_interval_seconds = 2.0
        runner._guard_items["wanling_invite"] = {"enabled": True, "entry_id": "entry", "updated_at": 123.0}
        runner._sync_guard_status_locked()

    status = runner.start_manual_runtime_task(
        entry=object(),
        entry_id="entry",
        task={"id": "manual-1", "task_type": "detect_scene", "label": "单步识别", "payload": {}},
        asset_tree_path=tmp_path / "entry.json",
    )

    assert executed == ["detect_scene"]
    assert status["running"] is False
    assert status["status"] == "success"
    assert status["task_type"] == ""
    assert status["current_task"] == ""
    assert status["current_task_id"] == ""
    assert "priority" not in status
    assert status["interruptible"] is True
    assert not hasattr(runner, "_thread")
    assert status["guard_enabled"] is True
    assert status["guard_items"]["close_popups"]["enabled"] is True
    assert status["guard_items"]["wanling_invite"]["enabled"] is True


def test_manual_runtime_task_persists_terminal_log_before_removing_queue_item(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    events: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [])
    monkeypatch.setattr(runner, "_index_images", lambda _tree: {})
    monkeypatch.setattr(runner, "_require_assets", lambda _ctx: None)
    monkeypatch.setattr(runner, "_execute_runtime_task", lambda _ctx, _task_type, _payload, _stop_event: "success")

    def fake_persist_status():
        events.append((
            "persist",
            [str(item.get("message") or "") for item in runner.status().get("logs") or []],
        ))

    def fake_remove_job(task_id: str):
        events.append(("remove", [task_id]))

    monkeypatch.setattr(runner, "_persist_status", fake_persist_status)
    monkeypatch.setattr(runtime_runner_core, "_remove_data_annotation_manual_job", fake_remove_job)

    runner.start_manual_runtime_task(
        entry=object(),
        entry_id="entry",
        task={"id": "manual-1", "task_type": "detect_scene", "label": "单步识别", "payload": {}},
        asset_tree_path=tmp_path / "entry.json",
    )

    remove_index = next(index for index, event in enumerate(events) if event[0] == "remove")
    persisted_before_remove = [messages for event, messages in events[:remove_index] if event == "persist"]
    assert any(any("[manual-1] 手动作业已启动：单步识别" in message for message in messages) for messages in persisted_before_remove)
    assert any(any("[manual-1] 手动作业完成：单步识别" in message for message in messages) for messages in persisted_before_remove)


def test_manual_runtime_task_records_task_cell_source(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [])
    monkeypatch.setattr(runner, "_index_images", lambda _tree: {})
    monkeypatch.setattr(runner, "_require_assets", lambda _ctx: None)
    monkeypatch.setattr(runner, "_execute_runtime_task", lambda _ctx, _task_type, _payload, _stop_event: "success")
    monkeypatch.setattr(runner, "_persist_status", lambda: None)
    monkeypatch.setattr(runtime_runner_core, "_remove_data_annotation_manual_job", lambda _task_id: None)

    status = runner.start_manual_runtime_task(
        entry=object(),
        entry_id="entry",
        task={"id": "manual-1", "task_type": "daily_signup", "label": "日常_报名", "payload": {}},
        asset_tree_path=tmp_path / "entry.json",
    )

    assert status["cell_logs"]
    cell = status["cell_logs"][0]
    assert cell["title"] == "手动作业：日常_报名"
    assert "行为树.create_task('daily_signup', {})" in cell["source"]
    assert "行为树.step(task, 守护=True)" in cell["source"]
    assert cell["entries"][0]["message"].startswith("[manual-1] 手动作业已启动")
    assert cell["entries"][-1]["message"].startswith("[manual-1] 手动作业完成")


def test_resident_manual_job_uses_current_runner_instance(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    jobs = [{
        "id": "manual-1",
        "task_type": "detect_scene",
        "label": "单步识别",
        "payload": {},
        "status": "pending",
        "created_at": 1.0,
    }]
    called: list[str] = []

    monkeypatch.setattr(runtime_runner_core, "_read_data_annotation_manual_jobs", lambda: list(jobs))

    def fake_write(updated):
        jobs[:] = list(updated)

    def fake_start_manual_runtime_task(**kwargs):
        called.append(str(kwargs["task"]["id"]))
        return {"status": "success"}

    monkeypatch.setattr(runtime_runner_core, "_write_data_annotation_manual_jobs", fake_write)
    monkeypatch.setattr(runner, "start_manual_runtime_task", fake_start_manual_runtime_task)

    status = runner._start_next_manual_job_if_idle(object(), "entry")

    assert status == {"status": "success"}
    assert called == ["manual-1"]
    assert jobs[0]["status"] == "running"


def test_manual_success_clears_matching_scheduler_retry_without_scheduler_id(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    task = {
        "id": "legacy-daily-signup",
        "task_type": "daily_signup",
        "label": "日常_报名",
        "source": "data_annotation_runtime",
        "schedule_kind": "daily",
        "enabled": False,
        "priority": 120,
        "interruptible": True,
        "next_time": None,
        "schedule_times": ["05:00"],
        "window": None,
        "last_run_at": "2026-06-06 18:51:41",
        "last_result": "stopped",
        "retry_after": "2026-06-06 15:59:04",
        "cooldown_seconds": 600,
        "payload": {"__scheduler_definition_task_type": "daily_signup"},
        "checkpoint": None,
    }
    fanxiu_api._write_data_annotation_scheduler_tasks([task])
    runner = create_fanxiu_runtime_runner()
    executed: list[str] = []
    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [])
    monkeypatch.setattr(runner, "_index_images", lambda _tree: {})
    monkeypatch.setattr(runner, "_require_assets", lambda _ctx: None)
    monkeypatch.setattr(runner, "_execute_runtime_task", lambda _ctx, task_type, _payload, _stop_event: executed.append(task_type) or "success")
    monkeypatch.setattr(runner, "_persist_status", lambda: None)
    monkeypatch.setattr(runtime_runner_core, "_remove_data_annotation_manual_job", lambda _task_id: None)

    runner._run_manual_runtime_task(
        entry=object(),
        entry_id="entry",
        task={"id": "manual-1", "task_type": "daily_signup", "label": "日常_报名", "payload": {}},
        asset_tree_path=tmp_path / "entry.json",
        stop_event=threading.Event(),
    )

    tasks = fanxiu_api._read_data_annotation_scheduler_tasks()
    signup = next(item for item in tasks if item["id"] == "legacy-daily-signup")
    assert executed == ["daily_signup"]
    assert signup["last_result"] == "success"
    assert signup["retry_after"] is None
    assert signup["next_time"]
    fact = fanxiu_api._read_data_annotation_world_facts()["discoveries"]["task"]["legacy-daily-signup"]
    assert fact["last_result"] == "success"
    assert fact["retry_after"] is None


def test_noop_guard_records_enabled_state_and_uses_resident_service(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_manual_job_state_path", lambda: tmp_path / "manual_jobs.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_manual_job_state_path", lambda: tmp_path / "manual_jobs.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    runner = create_fanxiu_runtime_runner()

    status = runner.set_guard(
        entry=object(),
        entry_id="codepc_mf",
        guard_id="wanling_invite",
        enabled=True,
        interval_seconds=2,
        asset_tree_path=tmp_path / "missing.json",
    )

    assert status["guard_items"]["wanling_invite"]["enabled"] is True
    assert status["guard_enabled"] is True
    assert status["guard_items"]["close_popups"]["enabled"] is True
    assert status["service_running"] is True
    assert any(item["scope"] == "guard" and item["item_id"] == "wanling_invite" for item in status["logs"])
    assert runner._service_runtime_state_path == tmp_path / "runtime_state.json"


def test_ensure_service_restarts_stale_loop_when_manual_job_is_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_manual_job_state_path", lambda: tmp_path / "manual_jobs.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_manual_job_state_path", lambda: tmp_path / "manual_jobs.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    _enqueue_data_annotation_manual_job("detect_scene", {}, label="单步识别")
    runner = create_fanxiu_runtime_runner()
    old_stop_event = threading.Event()
    starts: list[dict] = []

    class AliveThread:
        def is_alive(self):
            return True

    class FakeThread:
        def __init__(self, *args, **kwargs):
            starts.append(kwargs)

        def start(self):
            starts[-1]["started"] = True

        def is_alive(self):
            return True

    monkeypatch.setattr(runtime_runner_core.threading, "Thread", FakeThread)
    runner._service_thread = AliveThread()
    runner._service_stop_event = old_stop_event
    runner._service_generation = 7
    runner._service_heartbeat_at = time.time() - 60
    runner._service_step = "idle_guard"

    status = runner.ensure_service(
        entry=object(),
        entry_id="codepc_mf",
        asset_tree_path=tmp_path / "asset-tree.json",
    )

    assert old_stop_event.is_set()
    assert runner._service_generation == 8
    assert starts and starts[-1]["started"] is True
    assert starts[-1]["kwargs"]["generation"] == 8
    assert status["service_running"] is True
    assert any("心跳停滞" in item["message"] for item in status["logs"])


def test_service_control_shutdown_stops_resident_service(tmp_path, monkeypatch):
    control_path = tmp_path / "behavior_tree_control.json"
    monkeypatch.setattr(runtime_runner_core, "_behavior_tree_control_path", lambda: control_path)
    runner = create_fanxiu_runtime_runner()
    stop_event = threading.Event()
    runner._service_stop_event = stop_event
    control_path.write_text(
        json.dumps(
            {
                "id": "shutdown-1",
                "command": "shutdown_service",
                "reason": "test",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    runner._consume_service_control_request()

    assert stop_event.is_set()
    assert not control_path.exists()
    assert any("shutdown_service" in item["message"] for item in runner.status()["logs"])


def test_runtime_display_message_hides_internal_service_step():
    from backend.core.fanxiu.data_annotation.state import (
        data_annotation_runtime_display_message,
        normalize_data_annotation_runtime_logs_for_display,
    )

    assert data_annotation_runtime_display_message("行为树执行器已由后端进程 2664 持有：idle_guard") == "后台服务正在运行（进程 2664），空闲巡检中"
    assert data_annotation_runtime_display_message("行为树常驻服务运行中：进程 2664 idle_guard_done") == "后台服务正在运行（进程 2664）"
    assert data_annotation_runtime_display_message("行为树常驻服务心跳停滞，准备重启：idle_guard") == "行为树常驻服务心跳停滞，准备重启：空闲巡检中"
    assert data_annotation_runtime_display_message("行为树常驻服务未运行，等待恢复：owner 进程不是凡修常驻服务：pid=2664") == "原后台服务已失效（进程 2664）"
    assert normalize_data_annotation_runtime_logs_for_display([
        {"kind": "info", "message": "行为树执行器已由后端进程 2664 持有：scheduler_poll"}
    ]) == []


def test_service_owner_rejects_hidden_uvicorn_runner(monkeypatch):
    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return "pythonw.exe"

        def cmdline(self):
            return [
                r"D:\home\chenkunze\slns\codeyun\.venv\Scripts\pythonw.exe",
                "-m",
                "backend.core.runtime.uvicorn_hidden",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
            ]

    monkeypatch.setattr(fanxiu_behavior_tree_core.psutil, "Process", FakeProcess)

    assert fanxiu_behavior_tree_core._fanxiu_process_matches_service_owner(12345) is False


def test_service_owner_accepts_fanxiu_bt_service(monkeypatch):
    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return "pythonw.exe"

        def cmdline(self):
            return [
                r"D:\home\chenkunze\slns\codeyun\.venv\Scripts\pythonw.exe",
                r"D:\home\chenkunze\slns\codeyun\scripts\fanxiu_bt.py",
                "service",
            ]

    monkeypatch.setattr(fanxiu_behavior_tree_core.psutil, "Process", FakeProcess)

    assert fanxiu_behavior_tree_core._fanxiu_process_matches_service_owner(12345) is True


def test_restore_persisted_guard_config_keeps_close_popups_when_logs_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
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
    runner = create_fanxiu_runtime_runner()

    with runner._lock:
        runner._guard_enabled = False
        runner._status["logs"] = [{"kind": "info", "message": "local"}]
        runner._restore_persisted_config_locked()
        runner._sync_guard_status_locked()
        status = json.loads(json.dumps(runner._status, ensure_ascii=False))

    assert status["guard_enabled"] is True
    assert status["guard_entry_id"] == "codepc_mf"
    assert status["guard_items"]["close_popups"]["enabled"] is True
    assert status["logs"] == [{"kind": "info", "message": "local"}]
    assert status["cell_logs"] == [{"id": "cell-persisted", "title": "持久化 cell", "entries": []}]


def test_scheduler_task_due_soon_detects_near_enabled_supported_task(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 11, 23, 58, 0))
    monkeypatch.setattr(runtime_runner_core.time, "time", lambda: datetime(2026, 6, 11, 23, 58, 0).timestamp())
    monkeypatch.setattr(
        runtime_runner_core,
        "_read_data_annotation_scheduler_tasks",
        lambda: [
            {
                "id": "mail-cleanup",
                "task_type": "mail_cleanup",
                "enabled": True,
                "source": "data_annotation_runtime",
                "schedule_kind": "daily",
                "next_time": "2026-06-12 00:00:00",
                "retry_after": None,
            }
        ],
    )
    runner = create_fanxiu_runtime_runner()

    assert runner._scheduler_task_due_soon(within_seconds=180.0) is True


def test_ensure_service_uses_device_entry_id_over_raw_alias(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu_api, "_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_manual_job_state_path", lambda: tmp_path / "manual_jobs.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(fanxiu_api, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_manual_job_state_path", lambda: tmp_path / "manual_jobs.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    starts: list[dict] = []

    class FakeThread:
        def __init__(self, *args, **kwargs):
            starts.append(kwargs)

        def start(self):
            starts[-1]["started"] = True

        def is_alive(self):
            return True

    monkeypatch.setattr(runtime_runner_core.threading, "Thread", FakeThread)
    runner = create_fanxiu_runtime_runner()
    entry = UserDevice(
        entry_id="uuid-entry",
        user_id=1,
        device_id="device",
        name="codepc_mf",
        mode="local",
        token="token",
    )

    runner.ensure_service(
        entry=entry,
        entry_id="codepc_mf",
        asset_tree_path=tmp_path / "asset-tree.json",
    )

    assert runner._service_entry_id == "uuid-entry"
    assert runner.status()["entry_id"] == "uuid-entry"


def test_runtime_status_normalizes_guard_items_from_backend_definitions():
    status = {
        "guard_enabled": False,
        "guard_running": False,
        "guard_entry_id": "",
        "guard_items": {},
    }

    _normalize_data_annotation_runtime_guard_items(status)

    assert set(status["guard_items"]) == {"device_health", "close_popups", "wanling_invite"}
    assert status["guard_items"]["device_health"]["label"] == "设备健康"
    assert status["guard_items"]["device_health"]["enabled"] is True
    assert status["guard_items"]["close_popups"]["label"] == "关闭弹窗"
    assert status["guard_items"]["wanling_invite"]["label"] == "万灵切磋邀请"


def test_runtime_status_migrates_unedited_device_health_default():
    status = {
        "guard_enabled": True,
        "guard_running": False,
        "guard_entry_id": "",
        "guard_items": {
            "device_health": {"enabled": False, "updated_at": 0.0},
        },
    }

    _normalize_data_annotation_runtime_guard_items(status)

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

    _normalize_data_annotation_runtime_guard_items(status)

    assert status["guard_items"]["device_health"]["enabled"] is False


def test_device_health_guard_tick_respects_item_switch(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        runtime_runner_core,
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


def test_runtime_display_adds_layered_status_projection():
    status = {
        "entry_id": "mf-entry",
        "behavior_tree_enabled": True,
        "service_running": True,
        "running": True,
        "status": "running",
        "phase": "idle_guard",
        "task_type": "daily",
        "current_task": "日常小助手",
        "current_task_id": "daily-helper",
        "current_scene": 34,
        "current_index": 2,
        "total": 5,
        "guard_group_enabled": False,
        "job_group_enabled": False,
        "guard_interval_seconds": 2,
        "guard_items": {
            "device_health": {"enabled": True},
            "close_popups": {"enabled": False},
        },
        "message": "行为树执行器已由后端进程 2664 持有：idle_guard",
        "logs": [
            {"kind": "info", "message": "行为树执行器已由后端进程 2664 持有：idle_guard"},
            {"kind": "info", "message": "local"},
        ],
    }

    normalize_data_annotation_runtime_display(status)

    assert status["message"] == "后台服务正在运行（进程 2664），空闲巡检中"
    assert status["logs"] == [{"kind": "info", "message": "local"}]
    assert status["kernel_status"] == {
        "label": "执行中",
        "enabled": True,
        "running": True,
        "busy": True,
        "current_scene": "#34",
        "message": "后台服务正在运行（进程 2664），空闲巡检中",
        "can_restart": True,
        "can_interrupt": True,
    }
    assert status["engine_status"]["phase"] == "空闲巡检中"
    assert status["engine_status"]["current_task"] == "日常小助手"
    assert status["engine_status"]["progress"] == "2/5"
    assert status["framework_status"]["phase"] == "空闲巡检中"
    assert status["framework_status"]["current_task"] == "日常小助手"
    assert status["framework_status"]["progress"] == "2/5"
    assert status["scheduler_status"]["job_group_enabled"] is False
    assert status["scheduler_status"]["guard_group_enabled"] is False
    assert status["orchestration_status"]["entry_id"] == "mf-entry"
    assert status["orchestration_status"]["guard_count"] == 2
    assert status["orchestration_status"]["guard_enabled_count"] == 1

    owned_status = {
        "behavior_tree_enabled": True,
        "service_running": False,
        "running": False,
        "phase": "service_owned_by_other",
    }
    normalize_data_annotation_runtime_display(owned_status)
    assert owned_status["kernel_status"]["label"] == "后台接管"
    assert owned_status["kernel_status"]["running"] is True


def test_idle_recovery_runs_device_health_and_bounded_popup_ticks(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    events: list[str] = []
    guard_results = iter([BehaviorTreeStatus.RUNNING, BehaviorTreeStatus.RUNNING, BehaviorTreeStatus.SKIP])

    with runner._lock:
        runner._guard_group_enabled = True
        runner._guard_enabled = True
        runner._status["running"] = False

    monkeypatch.setattr(runner, "_run_device_health_guard_tick", lambda entry_id: events.append(f"health:{entry_id}"))
    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [])
    monkeypatch.setattr(runner, "_index_images", lambda _tree: {})
    monkeypatch.setattr(runner, "_require_assets", lambda _ctx: None)
    monkeypatch.setattr(runner, "_runtime_guard_service_tick", lambda *_args: events.append("popup") or next(guard_results))
    monkeypatch.setattr(runner, "_clear_tick_frame", lambda _ctx: events.append("clear"))
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: events.append("screencap") or "frame")
    monkeypatch.setattr(runner, "_identify_scene", lambda _ctx, _frame: ("world", 100.0))
    monkeypatch.setattr(runner, "_scene_matches", lambda _key, _score: True)
    monkeypatch.setattr(runner, "_persist_status", lambda: events.append("persist"))

    runner._run_idle_recovery(
        object(),
        "entry",
        tmp_path / "asset-tree.json",
        stop_event=threading.Event(),
        max_popup_ticks=5,
        settle_seconds=0,
    )

    assert events == [
        "health:entry",
        "popup",
        "clear",
        "popup",
        "clear",
        "popup",
        "screencap",
        "clear",
        "persist",
    ]
    status = runner.status()
    assert status["current_scene"] == 34
    assert status["phase"] == "idle_tick"
    assert status["message"] == "空闲复原识别：#34 world 100%"

def test_runtime_engine_tick_respects_group_flags(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    events: list[str] = []

    with runner._lock:
        runner._service_entry = object()
        runner._service_entry_id = "mf-entry"
        runner._service_asset_tree_path = Path("asset-tree.json")
        runner._guard_group_enabled = True
        runner._guard_enabled = True
        runner._status["running"] = False

    monkeypatch.setattr(runner, "_service_context", lambda: (object(), "mf-entry", Path("asset-tree.json")))
    monkeypatch.setattr(runner, "_service_paths_still_current", lambda: True)
    monkeypatch.setattr(runner, "_persist_status", lambda: None)
    monkeypatch.setattr(runner, "_run_device_health_guard_tick", lambda _entry_id: events.append("device_health"))
    monkeypatch.setattr(runner, "_start_next_manual_job_if_idle", lambda *_args: events.append("manual_job") or None)
    monkeypatch.setattr(runner, "_job_group_isolated", lambda: False)
    monkeypatch.setattr(runner, "_start_due_scheduler_tasks_if_idle", lambda *_args: events.append("scheduled_job") or False)
    monkeypatch.setattr(runner, "_run_idle_guard_tick", lambda *_args: events.append("idle_guard") or False)

    status = runner.run_service_tick_once(guard=True, manual_job=False, scheduled_job=True)

    assert events == ["device_health", "idle_guard", "scheduled_job"]
    assert status["engine_tick"] == {
        "ran": True,
        "action": "guard_checked",
        "guard": True,
        "manual_job": False,
        "scheduled_job": True,
    }


def test_runtime_engine_tick_pauses_lower_groups_when_guard_handles_popup_without_pending_manual_job(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    events: list[str] = []

    with runner._lock:
        runner._service_entry = object()
        runner._service_entry_id = "mf-entry"
        runner._service_asset_tree_path = Path("asset-tree.json")
        runner._guard_group_enabled = True
        runner._guard_enabled = True
        runner._status["running"] = False

    monkeypatch.setattr(runner, "_service_context", lambda: (object(), "mf-entry", Path("asset-tree.json")))
    monkeypatch.setattr(runner, "_service_paths_still_current", lambda: True)
    monkeypatch.setattr(runner, "_persist_status", lambda: None)
    monkeypatch.setattr(runner, "_run_device_health_guard_tick", lambda _entry_id: events.append("device_health"))
    monkeypatch.setattr(runner, "_run_idle_guard_tick", lambda *_args: events.append("idle_guard") or True)
    monkeypatch.setattr(runner, "_pending_manual_job_count", lambda: 0)
    monkeypatch.setattr(runner, "_start_next_manual_job_if_idle", lambda *_args: events.append("manual_job") or None)
    monkeypatch.setattr(runner, "_job_group_isolated", lambda: False)
    monkeypatch.setattr(runner, "_start_due_scheduler_tasks_if_idle", lambda *_args: events.append("scheduled_job") or False)

    status = runner.run_service_tick_once(guard=True, manual_job=True, scheduled_job=True)

    assert events == ["device_health", "idle_guard"]
    assert status["engine_tick"] == {
        "ran": True,
        "action": "guard_checked",
        "guard": True,
        "manual_job": True,
        "scheduled_job": True,
    }


def test_runtime_engine_tick_starts_pending_manual_job_after_guard_handles_popup(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    events: list[str] = []

    with runner._lock:
        runner._service_entry = object()
        runner._service_entry_id = "mf-entry"
        runner._service_asset_tree_path = Path("asset-tree.json")
        runner._guard_group_enabled = True
        runner._guard_enabled = True
        runner._status["running"] = False

    monkeypatch.setattr(runner, "_service_context", lambda: (object(), "mf-entry", Path("asset-tree.json")))
    monkeypatch.setattr(runner, "_service_paths_still_current", lambda: True)
    monkeypatch.setattr(runner, "_persist_status", lambda: None)
    monkeypatch.setattr(runner, "_run_device_health_guard_tick", lambda _entry_id: events.append("device_health"))
    monkeypatch.setattr(runner, "_run_idle_guard_tick", lambda *_args: events.append("idle_guard") or True)
    monkeypatch.setattr(runner, "_pending_manual_job_count", lambda: 1)
    monkeypatch.setattr(runner, "_start_next_manual_job_if_idle", lambda *_args: events.append("manual_job") or {"id": "manual-1"})
    monkeypatch.setattr(runner, "_job_group_isolated", lambda: False)
    monkeypatch.setattr(runner, "_start_due_scheduler_tasks_if_idle", lambda *_args: events.append("scheduled_job") or False)

    status = runner.run_service_tick_once(guard=True, manual_job=True, scheduled_job=True)

    assert events == ["device_health", "idle_guard", "manual_job"]
    assert status["engine_tick"] == {
        "ran": True,
        "action": "manual_job_started",
        "guard": True,
        "manual_job": True,
        "scheduled_job": True,
    }


def test_runtime_engine_tick_request_rejects_ambiguous_run_mode():
    from pydantic import ValidationError

    from backend.core.fanxiu.data_annotation.models import (
        FanxiuDataAnnotationRuntimeEngineTickRequest,
        FanxiuDataAnnotationRuntimeFrameworkTickRequest,
    )

    with pytest.raises(ValidationError):
        FanxiuDataAnnotationRuntimeEngineTickRequest(entry_id="mf-entry", run_mode="run")

    with pytest.raises(ValidationError):
        FanxiuDataAnnotationRuntimeEngineTickRequest(entry_id="mf-entry", max_ticks=0)

    request = FanxiuDataAnnotationRuntimeFrameworkTickRequest(
        entry_id="mf-entry",
        guard=False,
        manual_job=True,
        scheduled_job=False,
        run_mode="tick_once",
    )
    assert request.guard is False
    assert request.manual_job is True
    assert request.scheduled_job is False


def test_runtime_engine_run_until_idle_stops_after_no_progress(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions = iter(["manual_job_started", "scheduler_started", "guard_checked"])
    calls: list[dict[str, bool]] = []

    def fake_tick_once(*, guard: bool, manual_job: bool, scheduled_job: bool):
        calls.append({
            "guard": guard,
            "manual_job": manual_job,
            "scheduled_job": scheduled_job,
        })
        return {
            "running": False,
            "engine_tick": {
                "ran": True,
                "action": next(actions),
            },
        }

    monkeypatch.setattr(runner, "run_service_tick_once", fake_tick_once)
    monkeypatch.setattr(runner, "_persist_status", lambda: None)

    status = runner.run_service_ticks(
        guard=True,
        manual_job=True,
        scheduled_job=False,
        run_mode="until_idle",
        max_ticks=10,
        timeout_seconds=5,
    )

    assert calls == [
        {"guard": True, "manual_job": True, "scheduled_job": False},
        {"guard": True, "manual_job": True, "scheduled_job": False},
        {"guard": True, "manual_job": True, "scheduled_job": False},
    ]
    assert status["engine_tick"]["action"] == "guard_checked"
    assert status["engine_tick"]["run_mode"] == "until_idle"
    assert status["engine_tick"]["ticks"] == 3


def test_runtime_engine_current_job_waits_after_start(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    events: list[str] = []

    def fake_tick_once(*, guard: bool, manual_job: bool, scheduled_job: bool):
        events.append("tick")
        return {
            "running": True,
            "engine_tick": {
                "ran": True,
                "action": "manual_job_started",
            },
        }

    monkeypatch.setattr(runner, "run_service_tick_once", fake_tick_once)
    monkeypatch.setattr(runner, "wait_until_idle", lambda timeout_seconds=5.0: events.append("wait") or True)
    monkeypatch.setattr(runner, "status", lambda: {"running": False, "engine_tick": {}})
    monkeypatch.setattr(runner, "_persist_status", lambda: None)

    status = runner.run_service_ticks(
        guard=False,
        manual_job=True,
        scheduled_job=False,
        run_mode="current_job",
        max_ticks=10,
        timeout_seconds=5,
    )

    assert events == ["tick", "wait"]
    assert status["engine_tick"]["action"] == "manual_job_started"
    assert status["engine_tick"]["reason"] == "current_job_done"
    assert status["engine_tick"]["run_mode"] == "current_job"
    assert status["engine_tick"]["ticks"] == 1


def test_runtime_kernel_restart_stops_service_before_ensure(monkeypatch):
    events: list[str] = []

    class FakeRunner:
        def stop_service(self, *, timeout_seconds: float = 5.0):
            events.append(f"stop:{timeout_seconds}")
            return {"service_running": True}

    def fake_ensure_runtime_service(**kwargs):
        events.append(f"ensure:{kwargs['entry_id']}")
        return {
            "ok": True,
            "behavior_tree_enabled": True,
            "service_running": True,
            "running": False,
            "entry_id": kwargs["entry_id"],
            "status": "idle",
            "message": "行为树常驻服务运行中",
            "logs": [],
        }

    monkeypatch.setattr(runtime_control_core, "get_fanxiu_runtime_runner", lambda: FakeRunner())
    monkeypatch.setattr(runtime_control_core, "ensure_runtime_service", fake_ensure_runtime_service)
    monkeypatch.setattr(runtime_control_core, "persist_runtime_status", lambda *args, **kwargs: None)

    status = runtime_control_core.restart_runtime_kernel(
        entry=object(),
        entry_id="mf-entry",
        timeout_seconds=3,
    )

    assert events == ["stop:3.0", "ensure:mf-entry"]
    assert status["kernel_restart"] == {
        "ran": True,
        "previous_service_running": True,
        "service_running": True,
    }


def test_execute_runtime_tick_returns_layer_status(monkeypatch):
    class FakeRunner:
        def run_service_ticks(self, **kwargs):
            return {
                "ok": True,
                "behavior_tree_enabled": True,
                "service_running": False,
                "running": False,
                "entry_id": "mf-entry",
                "status": "idle",
                "phase": "service_owned_by_other",
                "message": "行为树执行器已由后端进程 2664 持有：idle_guard",
                "engine_tick": {
                    "ran": True,
                    "action": "idle",
                    "guard": kwargs["guard"],
                    "manual_job": kwargs["manual_job"],
                    "scheduled_job": kwargs["scheduled_job"],
                },
                "logs": [],
            }

    monkeypatch.setattr(runtime_control_core, "ensure_runtime_service", lambda **_kwargs: {"behavior_tree_enabled": True})
    monkeypatch.setattr(runtime_control_core, "get_fanxiu_runtime_runner", lambda: FakeRunner())
    monkeypatch.setattr(runtime_control_core, "persist_runtime_status", lambda *args, **kwargs: None)

    status = runtime_control_core.execute_runtime_tick(
        entry=object(),
        entry_id="mf-entry",
        guard=False,
        manual_job=False,
        scheduled_job=False,
        run_mode="tick_once",
        max_ticks=1,
        timeout_seconds=5,
    )

    assert status["message"] == "后台服务正在运行（进程 2664），空闲巡检中"
    assert status["engine_tick"]["guard"] is False
    assert status["kernel_status"]["label"] == "后台接管"
    assert status["kernel_status"]["running"] is True
    assert status["scheduler_status"]["label"] == "运行中"

