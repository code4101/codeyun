from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from backend.api import fanxiu as fanxiu_api
from backend.core.fanxiu.runtime import behavior_tree as bt
from backend.core.fanxiu.runtime import jupyter_kernel as jupyter_kernel_core
from backend.core.fanxiu.runtime.kernel import FanxiuKernel
from backend.core.fanxiu.data_annotation.runtime import DataAnnotationRuntimeContainer
from backend.core.fanxiu.data_annotation import runtime_control as runtime_control
from backend.core.fanxiu.data_annotation import runtime_framework as runtime_framework
from backend.core.fanxiu.data_annotation import runtime_runner as runtime_runner_core
from backend.core.fanxiu.data_annotation import storage as storage_core
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
    list_fanxiu_data_annotation_task_cell_definitions,
    normalize_data_annotation_debug_eval_payload,
    normalize_data_annotation_go_scene_payload,
    parse_data_annotation_scene_id,
)
from backend.core.fanxiu.data_annotation.debug_eval import register_fanxiu_data_annotation_debug_eval_job
from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu.data_annotation import runner as runner_core
from backend.models import UserDevice


def test_core_behavior_tree_facade_does_not_import_codeyun_db_at_module_top():
    source = Path("backend/core/fanxiu/runtime/behavior_tree.py").read_text(encoding="utf-8")
    header = source.split("DEFAULT_FANXIU_ENTRY_ID", 1)[0]

    assert "from backend.db import" not in header
    assert "from backend.models import" not in header
    assert "from sqlmodel import" not in header
    assert "def resolve_fanxiu_entry" in source


def test_core_runtime_control_does_not_import_codeyun_model_at_module_top():
    source = Path("backend/core/fanxiu/data_annotation/runtime_control.py").read_text(encoding="utf-8")
    header = source.split("def read_world_facts", 1)[0]

    assert "from backend.models import" not in header
    assert "from backend.db import" not in header
    assert "from sqlmodel import" not in header


def test_core_asset_tree_path_sanitizes_entry_id(monkeypatch, tmp_path):
    class Settings:
        data_dir = tmp_path

    monkeypatch.setattr(storage_core, "get_settings", lambda: Settings())

    path = bt.data_annotation_asset_tree_path(" bad/id ")

    assert path == tmp_path / "fanxiu" / "data-annotation" / "entries" / "bad_id" / "asset-tree.json"
























def test_fanxiu_kernel_cell_is_canonical_code_cell_facade(monkeypatch):
    calls: list[dict] = []

    def fake_submit(code, **kwargs):
        calls.append({"code": code, **kwargs})
        return {"status": "success", "output": "ok"}

    monkeypatch.setattr(bt, "resolve_fanxiu_entry", lambda _entry_id: object())
    monkeypatch.setattr(bt, "ensure_fanxiu_behavior_tree_service", lambda *_args: {})
    monkeypatch.setattr(jupyter_kernel_core, "execute_fanxiu_jupyter_cell", fake_submit)

    status = FanxiuKernel(entry_id="entry-1").cell("result = 1").run(timeout_seconds=9)

    assert status["status"] == "success"
    assert calls == [{
        "code": "result = 1",
        "timeout_seconds": 9.0,
        "max_output_chars": 4000,
    }]






def test_jupyter_binding_keeps_ctx_identity_and_caches_assets(tmp_path):
    asset_path = tmp_path / "asset-tree.json"
    asset_path.write_text("[]", encoding="utf-8")

    class FakeRunner:
        def __init__(self):
            self.loads = 0

        def _load_asset_tree(self, _path):
            self.loads += 1
            return []

        def _index_images(self, _tree):
            return {}

        def _require_assets(self, _ctx):
            return None

        def _fanxiu_runtime(self, ctx, _path, *, stop_event):
            return {"ctx": ctx, "stop_event": stop_event}

    runner = FakeRunner()
    binding = jupyter_kernel_core.FanxiuJupyterBinding(runner, object(), "entry-1", asset_path)
    original_ctx = binding.ctx
    original_runtime_ctx = binding.runtime_ctx

    binding.refresh()

    assert binding.ctx is original_ctx
    assert binding.runtime_ctx is original_runtime_ctx
    assert runner.loads == 1


def test_runtime_long_press_shape_owns_shape_coordinate_conversion():
    image = {
        "id": "349",
        "type": "image",
        "title": "0349.png",
        "width": 900,
        "height": 1600,
        "shapes": [{"id": "cuiling", "title": "淬灵", "x": 0.4, "y": 0.7, "w": 0.2, "h": 0.1}],
    }
    view = runtime_runner_core.View(image)
    shape = view.get_shapes()[0]
    calls: list[dict] = []

    class FakeRunner:
        def _drag_frame_point(self, ctx, source_image, start_x, start_y, end_x, end_y, *, duration_ms):
            calls.append({
                "ctx": ctx,
                "image": source_image,
                "start": (start_x, start_y),
                "end": (end_x, end_y),
                "duration_ms": duration_ms,
            })
            return {"ok": True}

    runtime = runtime_runner_core.FanxiuRuntime(FakeRunner(), {"images": {349: image}})
    runtime.view = lambda _selector: view
    runtime.resolve_shape_selector = lambda _view, _selector: shape
    runtime.match_shape = lambda _shape: True
    runtime._emit_runtime_action = lambda *_args, **_kwargs: None
    runtime.clear_frame = lambda: None

    result = runtime.long_press_shape(349, "淬灵", duration=5)

    assert result == {"ok": True}
    assert calls == [{
        "ctx": runtime.ctx,
        "image": image,
        "start": (450.0, 1200.0),
        "end": (450.0, 1200.0),
        "duration_ms": 3000,
    }]






def test_runtime_control_reads_doctor_watch_latest_snapshot(monkeypatch, tmp_path):
    latest_path = tmp_path / "fanxiu-watch" / "latest.json"
    heartbeat_path = tmp_path / "fanxiu-watch" / "heartbeat.json"
    monkeypatch.setattr(runtime_control, "doctor_watch_heartbeat_path", lambda: heartbeat_path)

    missing = runtime_control.read_doctor_watch_latest(latest_path)

    assert missing["ok"] is False
    assert missing["exists"] is False
    assert missing["snapshot"] == {}
    assert missing["heartbeat"]["active"] is False

    latest_path.parent.mkdir(parents=True)
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "stable_latest_path": str(runtime_control.doctor_watch_latest_path()),
                "severity": "blocked",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    latest_path.write_text(
        json.dumps(
            {
                "severity": "blocked",
                "summary": "检测到游戏公告遮挡",
                "due_task_count": 13,
                "stale_due_count": 13,
                "stale_due_success_count": 0,
                "blocked_due_count": 13,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    loaded = runtime_control.read_doctor_watch_latest(latest_path)

    assert loaded["ok"] is True
    assert loaded["exists"] is True
    assert loaded["path"] == str(latest_path)
    assert loaded["message"] == "检测到游戏公告遮挡"
    assert loaded["snapshot"]["severity"] == "blocked"
    assert loaded["heartbeat"]["pid"] == 123


def test_runtime_control_reads_stable_or_fallback_doctor_watch_latest(monkeypatch, tmp_path):
    watch_dir = tmp_path / "fanxiu-watch"
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *parts: tmp_path.joinpath(*parts))

    fallback_path = watch_dir / "doctor_watch_20260615_050000.latest.json"
    fallback_path.parent.mkdir(parents=True)
    fallback_path.write_text(json.dumps({"severity": "blocked", "summary": "旧快照"}, ensure_ascii=False), encoding="utf-8")

    loaded_fallback = runtime_control.read_doctor_watch_latest()

    assert loaded_fallback["ok"] is True
    assert loaded_fallback["path"] == str(fallback_path)
    assert loaded_fallback["message"] == "旧快照"

    stable_path = watch_dir / "doctor_watch_latest.json"
    stable_path.write_text(json.dumps({"severity": "ok", "summary": "稳定快照"}, ensure_ascii=False), encoding="utf-8")

    loaded_stable = runtime_control.read_doctor_watch_latest()

    assert loaded_stable["ok"] is True
    assert loaded_stable["path"] == str(stable_path)
    assert loaded_stable["message"] == "稳定快照"


def test_runtime_control_reads_doctor_watch_heartbeat(monkeypatch, tmp_path):
    heartbeat_path = tmp_path / "fanxiu-watch" / "doctor_watch_heartbeat.json"
    stable_path = tmp_path / "fanxiu-watch" / "doctor_watch_latest.json"
    heartbeat_path.parent.mkdir(parents=True)
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(runtime_control.time, "time", lambda: 150.0)
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "updated_at_text": "2026-06-15 05:30:00",
                "stable_latest_path": str(stable_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    active = runtime_control.read_doctor_watch_heartbeat(stale_after_seconds=180.0)

    assert active["ok"] is True
    assert active["active"] is True
    assert active["runtime_consistent"] is True
    assert active["age_seconds"] == 50.0

    heartbeat_path.write_text(
        json.dumps({"pid": 123, "updated_at": 100.0, "stable_latest_path": str(tmp_path / "old.json")}, ensure_ascii=False),
        encoding="utf-8",
    )

    inconsistent = runtime_control.read_doctor_watch_heartbeat(stale_after_seconds=180.0)

    assert inconsistent["active"] is False
    assert inconsistent["runtime_consistent"] is False


def test_runtime_control_prefers_active_heartbeat_latest_sidecar(monkeypatch, tmp_path):
    watch_dir = tmp_path / "fanxiu-watch"
    stable_path = watch_dir / "doctor_watch_latest.json"
    active_sidecar_path = watch_dir / "doctor_watch_background.latest.json"
    heartbeat_path = watch_dir / "doctor_watch_heartbeat.json"
    watch_dir.mkdir(parents=True)
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(runtime_control.time, "time", lambda: 150.0)
    stable_path.write_text(json.dumps({"severity": "ok", "summary": "旧稳定快照"}, ensure_ascii=False), encoding="utf-8")
    active_sidecar_path.write_text(json.dumps({"severity": "blocked", "summary": "活跃巡检快照"}, ensure_ascii=False), encoding="utf-8")
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "stable_latest_path": str(stable_path),
                "latest_path": str(active_sidecar_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = runtime_control.read_doctor_watch_latest()

    assert loaded["path"] == str(active_sidecar_path)
    assert loaded["snapshot"]["severity"] == "blocked"
    assert loaded["heartbeat"]["active"] is True


def test_doctor_watch_latest_payload_for_frontend_omits_large_auto_run_due(monkeypatch):
    monkeypatch.setattr(
        fanxiu_api._runtime_control,
        "read_doctor_watch_latest",
        lambda: {
            "ok": True,
            "exists": True,
            "path": "doctor-watch.latest.json",
            "message": "巡检摘要",
            "heartbeat": {"active": True},
            "snapshot": {
                "summary": "巡检摘要",
                "severity": "attention",
                "action_required": ["查看人工复核"],
                "auto_run_due": {
                    "runs": [{"message": "x" * 4096}],
                    "blocking_overlays": [{"message": "y" * 4096}],
                },
            },
        },
    )

    payload = fanxiu_api._doctor_watch_latest_payload_for_frontend()

    assert payload["snapshot"]["summary"] == "巡检摘要"
    assert payload["snapshot"]["severity"] == "attention"
    assert payload["snapshot"]["action_required"] == ["查看人工复核"]
    assert payload["snapshot"]["auto_run_due"] is None


def test_runtime_control_ensure_doctor_watch_skips_recent_observe_heartbeat(monkeypatch, tmp_path):
    class ActiveProcess:
        def __init__(self, _pid):
            pass

        def cmdline(self):
            return ["pythonw.exe", "fanxiu_bt.py", "watch-doctor"]

        def is_running(self):
            return True

    heartbeat_path = tmp_path / "fanxiu-watch" / "doctor_watch_heartbeat.json"
    stable_path = tmp_path / "fanxiu-watch" / "doctor_watch_latest.json"
    heartbeat_path.parent.mkdir(parents=True)
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(runtime_control.time, "time", lambda: 120.0)
    monkeypatch.setattr(runtime_control.psutil, "Process", ActiveProcess)
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "stable_latest_path": str(stable_path),
                "latest_path": str(stable_path),
                "auto_run_due_enabled": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stable_path.write_text(json.dumps({"severity": "blocked", "summary": "活跃"}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(runtime_control.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not start")))

    result = runtime_control.ensure_doctor_watch_background(stale_after_seconds=180.0, auto_run_due=False)

    assert result["started"] is False
    assert result["reason"] == "heartbeat_recent"
    assert result["heartbeat"]["active"] is True
    assert result["heartbeat"]["auto_run_due_enabled"] is False
    assert result["latest"]["snapshot"]["severity"] == "blocked"


def test_runtime_control_ensure_doctor_watch_can_request_auto_run_due(monkeypatch, tmp_path):
    class FakeProcess:
        pid = 789

    popen_calls = []

    def fake_popen(command, **kwargs):
        popen_calls.append({"command": command, "kwargs": kwargs})
        return FakeProcess()

    heartbeat_path = tmp_path / "fanxiu-watch" / "doctor_watch_heartbeat.json"
    stable_path = tmp_path / "fanxiu-watch" / "doctor_watch_latest.json"
    heartbeat_path.parent.mkdir(parents=True)
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(runtime_control.time, "time", lambda: 120.0)
    monkeypatch.setattr(runtime_control.subprocess, "Popen", fake_popen)
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "stable_latest_path": str(stable_path),
                "latest_path": str(stable_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stable_path.write_text(json.dumps({"severity": "blocked", "summary": "旧 watcher"}, ensure_ascii=False), encoding="utf-8")

    result = runtime_control.ensure_doctor_watch_background(stale_after_seconds=180.0, auto_run_due=True)

    assert result["started"] is True
    assert result["pid"] == 789
    assert result["previous_heartbeat"]["active"] is True
    assert result["previous_heartbeat"]["auto_run_due_enabled"] is False
    assert "--auto-run-due" in popen_calls[0]["command"]


def test_runtime_control_ensure_doctor_watch_allows_observe_only_recent_heartbeat(monkeypatch, tmp_path):
    class ActiveProcess:
        def __init__(self, _pid):
            pass

        def cmdline(self):
            return ["pythonw.exe", "fanxiu_bt.py", "watch-doctor"]

        def is_running(self):
            return True

    heartbeat_path = tmp_path / "fanxiu-watch" / "doctor_watch_heartbeat.json"
    stable_path = tmp_path / "fanxiu-watch" / "doctor_watch_latest.json"
    heartbeat_path.parent.mkdir(parents=True)
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(runtime_control.time, "time", lambda: 120.0)
    monkeypatch.setattr(runtime_control.psutil, "Process", ActiveProcess)
    monkeypatch.setattr(runtime_control.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not start")))
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "stable_latest_path": str(stable_path),
                "latest_path": str(stable_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stable_path.write_text(json.dumps({"severity": "blocked", "summary": "observe only"}, ensure_ascii=False), encoding="utf-8")

    result = runtime_control.ensure_doctor_watch_background(stale_after_seconds=180.0, auto_run_due=False)

    assert result["started"] is False
    assert result["reason"] == "heartbeat_recent"
    assert result["heartbeat"]["auto_run_due_enabled"] is False


def test_runtime_control_ensure_doctor_watch_starts_when_heartbeat_stale(monkeypatch, tmp_path):
    class FakeProcess:
        pid = 456

    popen_calls = []

    def fake_popen(command, **kwargs):
        popen_calls.append({"command": command, "kwargs": kwargs})
        return FakeProcess()

    heartbeat_path = tmp_path / "fanxiu-watch" / "doctor_watch_heartbeat.json"
    stable_path = tmp_path / "fanxiu-watch" / "doctor_watch_latest.json"
    heartbeat_path.parent.mkdir(parents=True)
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(runtime_control.time, "time", lambda: 400.0)
    monkeypatch.setattr(runtime_control.subprocess, "Popen", fake_popen)
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "stable_latest_path": str(stable_path),
                "latest_path": str(stable_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runtime_control.ensure_doctor_watch_background(
        interval_seconds=30,
        duration_seconds=60,
        stale_after_seconds=180.0,
        include_screenshot=False,
    )

    assert result["started"] is True
    assert result["pid"] == 456
    assert result["reason"] == "heartbeat_missing_or_stale"
    assert len(popen_calls) == 1
    command = popen_calls[0]["command"]
    assert "watch-doctor" in command
    assert "--interval-seconds" in command
    assert "30.0" in command
    assert "--duration-seconds" in command
    assert "60.0" in command
    assert "--screenshot" not in command
    assert "--auto-run-due" in command
    assert result["output_path"].endswith(".ndjson")
















def test_runtime_runner_default_close_popups_guard_is_on():
    runner = bt.create_fanxiu_runtime_runner()

    status = runner.status()

    assert status["guard_enabled"] is True
    assert status["guard_running"] is False
    assert status["guard_items"]["close_popups"]["enabled"] is True


def test_runtime_scene_does_not_fallback_when_graph_returns_unknown(monkeypatch):
    runner = bt.create_fanxiu_runtime_runner()
    monkeypatch.setattr(runner, "_identify_scene_number_by_graph", lambda *_args, **_kwargs: (None, 37.0, "unknown"))

    scene_id, score = runner._identify_scene_number({}, "frame", [327, 326])

    assert scene_id is None
    assert score == 37.0




def test_runtime_status_migrates_legacy_close_popups_guard_off_to_on():
    status = {
        "guard_group_enabled": True,
        "guard_enabled": False,
        "guard_running": False,
        "guard_items": {"close_popups": {"enabled": False}},
    }

    runtime_control.normalize_runtime_guard_items(status)

    assert status["guard_enabled"] is True
    assert status["guard_items"]["close_popups"]["enabled"] is True
    assert status["close_popups_guard_config_version"] >= 2


def test_runtime_status_preserves_versioned_close_popups_guard_off():
    status = {
        "guard_group_enabled": True,
        "guard_enabled": False,
        "close_popups_guard_config_version": 2,
        "guard_running": False,
        "guard_items": {"close_popups": {"enabled": False}},
    }

    runtime_control.normalize_runtime_guard_items(status)

    assert status["guard_enabled"] is False
    assert status["guard_items"]["close_popups"]["enabled"] is False




def test_core_debug_eval_payload_requires_code():
    try:
        normalize_data_annotation_debug_eval_payload({})
    except ValueError as exc:
        assert "payload.code" in str(exc)
    else:
        raise AssertionError("expected ValueError")












































def test_core_runner_factory_creates_and_registers_runner(monkeypatch):
    class FakeRunner:
        pass

    monkeypatch.setattr(bt, "_RUNTIME_RUNNER", None)
    monkeypatch.setattr(runner_core, "_RUNTIME_RUNNER_CLASS", None)

    bt.register_fanxiu_runtime_runner_class(FakeRunner)
    runner = bt.create_and_register_fanxiu_runtime_runner()

    assert isinstance(runner, FakeRunner)
    assert bt.get_fanxiu_runtime_runner() is runner


def test_core_runner_factory_can_create_unregistered_runner(monkeypatch):
    class FakeRunner:
        pass

    monkeypatch.setattr(runner_core, "_RUNTIME_RUNNER_CLASS", None)
    bt.register_fanxiu_runtime_runner_class(FakeRunner)

    runner = bt.create_fanxiu_runtime_runner()

    assert isinstance(runner, FakeRunner)




















def _patch_fanxiu_bt_kernel(monkeypatch, fanxiu_bt, calls):
    class FakeCell:
        def __init__(self, kind, value, options):
            self.kind = kind
            self.value = value
            self.options = options

        def run(self, *, timeout_seconds=None):
            calls.append((self.kind, self.value, {**self.options, "run_timeout_seconds": float(timeout_seconds or 0)}))
            return {"status": "success", "message": "ok"}

    class FakeKernel:
        def __init__(self, entry_id):
            self.entry_id = entry_id

        def task(self, task_type, payload):
            return FakeCell("task", (task_type, dict(payload or {})), {"entry_id": self.entry_id})

        def cell(self, code, **kwargs):
            return FakeCell("cell", code, {"entry_id": self.entry_id, **kwargs})

    monkeypatch.setattr(fanxiu_bt, "FanxiuKernel", FakeKernel)


def test_fanxiu_bt_task_compiles_to_kernel_cell(monkeypatch):
    import scripts.fanxiu_bt as fanxiu_bt

    calls = []
    _patch_fanxiu_bt_kernel(monkeypatch, fanxiu_bt, calls)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        ["fanxiu_bt.py", "--entry-id", "entry", "task", "xianfu_visit_partner"],
    )

    assert fanxiu_bt.main() == 0
    assert calls == [(
        "task",
        ("xianfu_visit_partner", {}),
        {"entry_id": "entry", "run_timeout_seconds": 300.0},
    )]


def test_fanxiu_bt_go_scene_accepts_hash_scene_id(monkeypatch):
    import scripts.fanxiu_bt as fanxiu_bt

    calls = []
    _patch_fanxiu_bt_kernel(monkeypatch, fanxiu_bt, calls)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        ["fanxiu_bt.py", "--entry-id", "entry", "go-scene", "#121"],
    )

    assert fanxiu_bt.main() == 0
    assert calls[0][0] == "task"
    assert calls[0][1][0] == "go_scene"
    assert calls[0][1][1]["target_scene_id"] == 121
    assert calls[0][2]["entry_id"] == "entry"












def test_fanxiu_bt_cell_is_canonical_and_waits_by_default(monkeypatch):
    import scripts.fanxiu_bt as fanxiu_bt

    calls = []
    _patch_fanxiu_bt_kernel(monkeypatch, fanxiu_bt, calls)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "--entry-id",
            "entry",
            "cell",
            "result = ctx.scene()",
            "--wait-timeout-seconds",
            "88",
        ],
    )

    assert fanxiu_bt.main() == 0
    assert calls == [(
        "cell",
        "result = ctx.scene()",
        {
            "entry_id": "entry",
            "timeout_seconds": 120.0,
            "max_output_chars": 4000,
            "run_timeout_seconds": 88.0,
        },
    )]




def test_fanxiu_bt_idle_runtime_annotation_error_does_not_block_other_due_tasks():
    import scripts.fanxiu_bt as fanxiu_bt

    report = {
        "runtime": {
            "running": False,
            "status": "idle",
            "phase": "idle_tick",
            "current_task": "",
            "error": "场景跳转缺少可靠标注，已中断，请人工补标/修标后重试：目标场景=#34；当前/点击前场景=#237；动作 shape=确定",
        },
        "scheduler": {
            "next_action": "job_group_disabled",
            "message": "作业组已关闭，到期作业暂不自动执行",
            "due_tasks": [
                {"id": "legacy-daily-jianling", "label": "日常_剑灵"},
                {"id": "xianfu-visit-partner", "label": "仙府_寻访仙侣"},
            ],
            "enabled_tasks": [
                {
                    "id": "legacy-daily-jianling",
                    "label": "日常_剑灵",
                    "enabled": True,
                    "next_time": "2026-06-21 05:00:00",
                    "last_result": "",
                },
                {
                    "id": "xianfu-visit-partner",
                    "label": "仙府_寻访仙侣",
                    "enabled": True,
                    "next_time": "2026-06-21 06:30:00",
                    "last_result": "",
                },
            ],
        },
    }

    maintenance = fanxiu_bt._build_maintenance_summary(report)

    assert maintenance["severity"] == "attention"
    assert maintenance["automation_safe"] is True
    assert maintenance["needs_human_annotation"] is False
    assert maintenance["blocked_due_count"] == 0
    assert "等待 AI 显式提交 cell" in maintenance["action_required"][0]


def test_fanxiu_bt_doctor_summary_reports_blocked_action_and_exit_code(monkeypatch, capsys):
    import scripts.fanxiu_bt as fanxiu_bt

    report = {
        "checked_at": "2026-06-15 05:10:00",
        "runtime": {"status": "idle", "phase": "scheduler_blocked", "current_scene": None},
        "scheduler": {"next_action": "blocked"},
        "screenshot": {"path": "C:/Temp/codeyun/fanxiu-evidence/doctor.png"},
        "maintenance": {
            "severity": "blocked",
            "summary": "检测到游戏公告遮挡；资产树「游戏公告」缺少「关闭公告」动作标注，自动作业无法安全进入游戏",
            "automation_safe": False,
            "needs_human_annotation": True,
            "blocked_by": [
                {
                    "title": "游戏公告",
                    "message": "检测到游戏公告遮挡；资产树「游戏公告」缺少安全推进动作标注",
                }
            ],
            "due_task_count": 13,
            "stale_due_count": 13,
            "stale_due_success_count": 0,
            "blocked_due_count": 13,
            "action_required": ["在资产树「游戏公告」补充「关闭公告」动作标注"],
            "annotation_targets": [
                {
                    "title": "游戏公告",
                    "url": "/fanxiu/data-annotation?entry_id=30b82d72-8a76-4a74-be4b-4fc1591c6ce2&focus_image_title=%E6%B8%B8%E6%88%8F%E5%85%AC%E5%91%8A",
                    "acceptable_shapes": ["关闭公告"],
                    "existing_shapes": [],
                    "all_shapes": ["公告"],
                    "missing_shapes": ["关闭公告"],
                    "required_shapes": ["关闭公告"],
                }
            ],
            "retry_condition": "阻断浮层消失且对应资产树已有安全推进动作标注",
        },
    }
    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: report)
    monkeypatch.setattr(fanxiu_bt.sys, "argv", ["fanxiu_bt.py", "doctor", "--summary", "--exit-code"])

    assert fanxiu_bt.main() == 2
    output = capsys.readouterr().out

    assert "severity: blocked" in output
    assert "due_task_count=13" in output
    assert "stale_due_count=13" in output
    assert "blocked_due_count=13" in output
    assert "stale_due_success_count=0" in output
    assert "needs_human_annotation: True" in output
    assert "blocked_by: 游戏公告" in output
    assert "action_required: 在资产树「游戏公告」补充「关闭公告」动作标注" in output
    assert "annotation_target: 游戏公告" in output
    assert "focus_image_title=%E6%B8%B8%E6%88%8F%E5%85%AC%E5%91%8A" in output
    assert "all_shapes=公告" in output
    assert "existing_safe_shapes=" in output
    assert "acceptable_shapes=关闭公告" in output
    assert "missing_shapes=关闭公告" in output
    assert "screenshot: C:/Temp/codeyun/fanxiu-evidence/doctor.png" in output




def test_fanxiu_bt_watch_doctor_writes_ndjson_and_returns_blocked(monkeypatch, tmp_path, capsys):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch.ndjson"
    latest_path = output_path.with_suffix(".latest.json")
    stable_latest_path = tmp_path / "doctor_watch_latest.json"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    report = {
        "checked_at": "2026-06-15 05:20:00",
        "runtime": {"status": "idle", "phase": "scheduler_blocked", "current_scene": None},
        "scheduler": {"next_action": "blocked"},
        "maintenance": {
            "severity": "blocked",
            "summary": "检测到游戏公告遮挡",
            "automation_safe": False,
            "needs_human_annotation": True,
            "blocked_by": [{"title": "游戏公告", "blocking": True}],
            "due_task_count": 13,
            "due_task_ids": ["legacy-daily-youli"],
            "stale_due_count": 13,
            "stale_due_success_count": 0,
            "blocked_due_count": 13,
            "blocked_due_ids": ["legacy-daily-youli"],
            "action_required": ["在资产树「游戏公告」补充「关闭公告」动作标注"],
            "annotation_targets": [
                {
                    "title": "游戏公告",
                    "url": "/fanxiu/data-annotation?entry_id=30b82d72-8a76-4a74-be4b-4fc1591c6ce2&focus_image_title=%E6%B8%B8%E6%88%8F%E5%85%AC%E5%91%8A",
                    "acceptable_shapes": ["关闭公告"],
                    "existing_shapes": [],
                    "all_shapes": ["公告"],
                    "missing_shapes": ["关闭公告"],
                    "required_shapes": ["关闭公告"],
                }
            ],
            "retry_condition": "阻断浮层消失且对应资产树已有安全推进动作标注",
        },
    }
    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: report)
    monkeypatch.setattr(fanxiu_bt, "_stable_doctor_watch_latest_path", lambda: stable_latest_path)
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "3",
            "--stop-on-blocked",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 2
    lines = output_path.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    stable_latest = json.loads(stable_latest_path.read_text(encoding="utf-8"))
    heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))

    assert len(lines) == 1
    assert event["severity"] == "blocked"
    assert latest["severity"] == "blocked"
    assert stable_latest["severity"] == "blocked"
    assert heartbeat["severity"] == "blocked"
    assert heartbeat["output_path"] == str(output_path)
    assert heartbeat["auto_run_due_enabled"] is False
    assert heartbeat["stale_due_count"] == 13
    assert heartbeat["stale_due_success_count"] == 0
    assert heartbeat["blocked_due_count"] == 13
    assert latest["blocked_by"][0]["title"] == "游戏公告"
    assert latest["annotation_targets"][0]["title"] == "游戏公告"
    assert latest["annotation_targets"][0]["url"].startswith("/fanxiu/data-annotation?")
    assert event["due_task_count"] == 13
    assert event["due_task_ids"] == ["legacy-daily-youli"]
    assert event["stale_due_count"] == 13
    assert event["stale_due_success_count"] == 0
    assert event["blocked_due_count"] == 13
    assert event["blocked_due_ids"] == ["legacy-daily-youli"]
    assert event["needs_human_annotation"] is True
    assert event["annotation_targets"][0]["acceptable_shapes"] == ["关闭公告"]
    assert event["annotation_targets"][0]["all_shapes"] == ["公告"]
    assert event["annotation_targets"][0]["missing_shapes"] == ["关闭公告"]
    assert event["annotation_targets"][0]["required_shapes"] == ["关闭公告"]
    output = capsys.readouterr().out
    assert "path=" in output
    assert "latest=" in output
    assert "stable_latest=" in output


def test_fanxiu_bt_watch_doctor_stops_when_ok_no_due(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch-ok.ndjson"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    report = {
        "checked_at": "2026-06-15 06:00:00",
        "runtime": {"status": "idle", "phase": "idle", "current_scene": 34},
        "scheduler": {"next_action": "idle"},
        "maintenance": {
            "severity": "ok",
            "summary": "巡检未发现阻断",
            "automation_safe": True,
            "needs_human_annotation": False,
            "due_task_count": 0,
            "due_task_ids": [],
            "stale_due_count": 0,
            "stale_due_success_count": 0,
            "blocked_due_count": 0,
            "action_required": ["当前没有到期任务"],
            "retry_condition": "无需特殊条件",
        },
    }
    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: report)
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--stop-on-ok-no-due",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 0
    event = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])

    assert event["severity"] == "ok"
    assert event["due_task_count"] == 0


def test_fanxiu_bt_watch_doctor_dispatches_run_due_as_one_external_cell(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch-auto.ndjson"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    reports = [
        {
            "checked_at": "2026-06-15 06:20:00",
            "runtime": {"status": "idle", "phase": "idle", "current_scene": 34},
            "scheduler": {"next_action": "run_due", "due_tasks": [{"id": "legacy-daily-youli"}]},
            "maintenance": {
                "severity": "attention",
                "summary": "1 个任务已到期，等待自动执行",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 1,
                "due_task_ids": ["legacy-daily-youli"],
                "stale_due_count": 1,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": ["当前有到期任务且未发现阻断"],
                "retry_condition": "无需特殊条件",
            },
        },
        {
            "checked_at": "2026-06-15 06:20:01",
            "runtime": {"status": "idle", "phase": "idle", "current_scene": 34},
            "scheduler": {"next_action": "idle", "due_tasks": []},
            "maintenance": {
                "severity": "ok",
                "summary": "巡检未发现阻断",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 0,
                "due_task_ids": [],
                "stale_due_count": 0,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": ["当前没有到期任务"],
                "retry_condition": "无需特殊条件",
            },
        },
    ]
    run_due_calls = []
    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: reports.pop(0))
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt, "resolve_fanxiu_entry", lambda entry_id: {"id": entry_id})
    monkeypatch.setattr(fanxiu_bt, "data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(
        fanxiu_bt,
        "run_due_scheduler_tasks",
        lambda **kwargs: run_due_calls.append(kwargs) or {"status": "idle", "phase": "scheduler_run_due", "message": "submitted"},
    )
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "1",
            "--auto-run-due",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 0
    event = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])

    assert len(run_due_calls) == 1
    assert event["severity"] == "ok"
    assert event["auto_run_due_enabled"] is True
    assert event["auto_run_due"].get("triggered") is True












def test_fanxiu_bt_watch_doctor_wakes_early_when_blocked_annotation_changes(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch-annotation-change.ndjson"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    reports = [
        {
            "checked_at": "2026-06-15 06:24:00",
            "runtime": {"status": "idle", "phase": "scheduler_blocked", "current_scene": None},
            "scheduler": {"next_action": "blocked", "due_tasks": [{"id": "legacy-daily-youli"}]},
            "maintenance": {
                "severity": "blocked",
                "summary": "检测到游戏公告遮挡",
                "automation_safe": False,
                "needs_human_annotation": True,
                "blocked_by": [{"title": "游戏公告", "blocking": True, "action_shapes": []}],
                "due_task_count": 1,
                "due_task_ids": ["legacy-daily-youli"],
                "stale_due_count": 1,
                "stale_due_success_count": 0,
                "blocked_due_count": 1,
                "blocked_due_ids": ["legacy-daily-youli"],
                "action_required": ["补标"],
                "annotation_targets": [{"title": "游戏公告"}],
                "retry_condition": "补标后重试",
            },
        },
        {
            "checked_at": "2026-06-15 06:24:02",
            "runtime": {"status": "idle", "phase": "idle", "current_scene": 34},
            "scheduler": {"next_action": "run_due", "due_tasks": [{"id": "legacy-daily-youli"}]},
            "maintenance": {
                "severity": "attention",
                "summary": "1 个任务已到期，等待自动执行",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 1,
                "due_task_ids": ["legacy-daily-youli"],
                "stale_due_count": 1,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": ["当前有到期任务且未发现阻断"],
                "retry_condition": "无需特殊条件",
            },
        },
        {
            "checked_at": "2026-06-15 06:24:03",
            "runtime": {"status": "idle", "phase": "idle", "current_scene": 34},
            "scheduler": {"next_action": "idle", "due_tasks": []},
            "maintenance": {
                "severity": "ok",
                "summary": "巡检未发现阻断",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 0,
                "due_task_ids": [],
                "stale_due_count": 0,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": ["当前没有到期任务"],
                "retry_condition": "无需特殊条件",
            },
        },
    ]
    signatures = iter([(1, 10), (2, 20)])
    sleep_calls = []
    run_due_calls = []

    class FakeTime:
        current = 100.0

        @staticmethod
        def time():
            return FakeTime.current

        @staticmethod
        def monotonic():
            return FakeTime.current

        @staticmethod
        def sleep(seconds):
            sleep_calls.append(seconds)
            FakeTime.current += float(seconds)

    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: reports.pop(0))
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt, "_asset_tree_signature_for_entry", lambda entry_id: next(signatures))
    monkeypatch.setattr(fanxiu_bt, "time", FakeTime)
    monkeypatch.setattr(fanxiu_bt, "resolve_fanxiu_entry", lambda entry_id: {"id": entry_id})
    monkeypatch.setattr(fanxiu_bt, "data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(
        fanxiu_bt,
        "run_due_scheduler_tasks",
        lambda **kwargs: run_due_calls.append(kwargs) or {"status": "idle", "phase": "scheduler_run_due", "message": "submitted"},
    )
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "2",
            "--interval-seconds",
            "60",
            "--auto-run-due",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 2
    events = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]

    assert len(events) == 2
    assert events[0]["severity"] == "blocked"
    assert events[1]["severity"] == "ok"
    assert events[1]["scheduler_next_action"] == "idle"
    assert events[1]["auto_run_due"].get("triggered") is True
    assert sleep_calls == [2.0]
    assert len(run_due_calls) == 1


def test_fanxiu_bt_watch_doctor_does_not_auto_run_due_when_blocked(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch-blocked.ndjson"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    report = {
        "checked_at": "2026-06-15 06:25:00",
        "runtime": {"status": "idle", "phase": "scheduler_blocked", "current_scene": None},
        "scheduler": {"next_action": "blocked", "due_tasks": [{"id": "legacy-daily-youli"}]},
        "maintenance": {
            "severity": "blocked",
            "summary": "检测到游戏公告遮挡",
            "automation_safe": False,
            "needs_human_annotation": True,
            "blocked_by": [{"title": "游戏公告", "blocking": True}],
            "due_task_count": 1,
            "due_task_ids": ["legacy-daily-youli"],
            "stale_due_count": 1,
            "stale_due_success_count": 0,
            "blocked_due_count": 1,
            "blocked_due_ids": ["legacy-daily-youli"],
            "action_required": ["补标"],
            "retry_condition": "补标后重试",
        },
    }
    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: report)
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(
        fanxiu_bt,
        "run_due_scheduler_tasks",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("blocked watcher must not run due")),
    )
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "1",
            "--auto-run-due",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 2
    event = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])

    assert event["severity"] == "blocked"
    assert event["auto_run_due_enabled"] is True
    assert event["auto_run_due"] == {}




def test_fanxiu_bt_watch_doctor_forces_screenshot_when_blocked(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch-blocked-screenshot.ndjson"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    stable_latest_path = tmp_path / "doctor_watch_latest.json"
    include_screenshot_values = []

    def build_report(*, log_limit, include_screenshot):
        include_screenshot_values.append(bool(include_screenshot))
        report = {
            "checked_at": "2026-06-15 06:35:00",
            "runtime": {"status": "idle", "phase": "scheduler_blocked", "current_scene": None},
            "scheduler": {"next_action": "blocked"},
            "maintenance": {
                "severity": "blocked",
                "summary": "检测到游戏公告遮挡",
                "automation_safe": False,
                "needs_human_annotation": True,
                "blocked_by": [{"title": "游戏公告", "blocking": True}],
                "due_task_count": 1,
                "due_task_ids": ["legacy-daily-youli"],
                "stale_due_count": 1,
                "stale_due_success_count": 0,
                "blocked_due_count": 1,
                "blocked_due_ids": ["legacy-daily-youli"],
                "action_required": ["补标"],
                "retry_condition": "补标后重试",
            },
        }
        if include_screenshot:
            report["screenshot"] = {"path": "C:/Temp/codeyun/fanxiu-evidence/blocked.png"}
        return report

    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", build_report)
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt, "_stable_doctor_watch_latest_path", lambda: stable_latest_path)
    monkeypatch.setattr(fanxiu_bt, "time", type("FakeTime", (), {
        "time": staticmethod(lambda: 100.0),
        "monotonic": staticmethod(lambda: 100.0),
        "sleep": staticmethod(lambda _seconds: None),
    }))
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "2",
            "--screenshot",
            "--screenshot-every",
            "10",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 2
    lines = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]

    assert include_screenshot_values == [True, False, True]
    assert lines[0]["screenshot_path"] == "C:/Temp/codeyun/fanxiu-evidence/blocked.png"
    assert lines[1]["screenshot_path"] == "C:/Temp/codeyun/fanxiu-evidence/blocked.png"


def test_fanxiu_bt_watch_doctor_accepts_explicit_latest_json(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch.ndjson"
    latest_path = tmp_path / "nested" / "latest.json"
    stable_latest_path = tmp_path / "doctor_watch_latest.json"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    report = {
        "checked_at": "2026-06-15 06:10:00",
        "runtime": {"status": "idle", "phase": "idle", "current_scene": 34},
        "scheduler": {"next_action": "idle"},
        "maintenance": {
            "severity": "ok",
            "summary": "巡检未发现阻断",
            "automation_safe": True,
            "needs_human_annotation": False,
            "due_task_count": 0,
            "due_task_ids": [],
            "stale_due_count": 0,
            "stale_due_success_count": 0,
            "blocked_due_count": 0,
            "action_required": ["当前没有到期任务"],
            "retry_condition": "无需特殊条件",
        },
    }
    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: report)
    monkeypatch.setattr(fanxiu_bt, "_stable_doctor_watch_latest_path", lambda: stable_latest_path)
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "1",
            "--output",
            str(output_path),
            "--latest-json",
            str(latest_path),
        ],
    )

    assert fanxiu_bt.main() == 0

    assert json.loads(latest_path.read_text(encoding="utf-8"))["severity"] == "ok"
    assert json.loads(stable_latest_path.read_text(encoding="utf-8"))["severity"] == "ok"
    assert output_path.with_suffix(".latest.json").exists() is False


def test_fanxiu_bt_ensure_watch_doctor_skips_when_heartbeat_recent(monkeypatch, tmp_path, capsys):
    import scripts.fanxiu_bt as fanxiu_bt

    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    stable_latest_path = tmp_path / "doctor_watch_latest.json"
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "stable_latest_path": str(stable_latest_path),
                "severity": "blocked",
                "auto_run_due_enabled": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt, "_stable_doctor_watch_latest_path", lambda: stable_latest_path)
    monkeypatch.setattr(fanxiu_bt.time, "time", lambda: 120.0)
    monkeypatch.setattr(fanxiu_bt, "read_doctor_watch_latest", lambda: {"ok": True, "snapshot": {"severity": "blocked"}})
    monkeypatch.setattr(fanxiu_bt.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not start")))
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        ["fanxiu_bt.py", "ensure-watch-doctor", "--stale-after-seconds", "180"],
    )

    assert fanxiu_bt.main() == 0
    result = json.loads(capsys.readouterr().out)

    assert result["started"] is False
    assert result["reason"] == "heartbeat_recent"
    assert result["heartbeat"]["pid"] == 123
    assert result["heartbeat"]["auto_run_due_enabled"] is True


def test_fanxiu_bt_ensure_watch_doctor_skips_when_recent_heartbeat_lacks_auto_run_due(monkeypatch, tmp_path, capsys):
    import scripts.fanxiu_bt as fanxiu_bt

    class FakeProcess:
        pid = 654

        def poll(self):
            return None

    popen_calls = []

    def fake_popen(command, **kwargs):
        popen_calls.append({"command": command, "kwargs": kwargs})
        return FakeProcess()

    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    stable_latest_path = tmp_path / "doctor_watch_latest.json"
    heartbeat_path.write_text(
        json.dumps({"pid": 123, "updated_at": 100.0, "stable_latest_path": str(stable_latest_path), "severity": "blocked"}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt, "_stable_doctor_watch_latest_path", lambda: stable_latest_path)
    monkeypatch.setattr(fanxiu_bt.time, "time", lambda: 120.0)
    monkeypatch.setattr(fanxiu_bt.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        ["fanxiu_bt.py", "ensure-watch-doctor", "--stale-after-seconds", "180"],
    )

    assert fanxiu_bt.main() == 0
    result = json.loads(capsys.readouterr().out)

    assert result["started"] is False
    assert result["reason"] == "heartbeat_recent"
    assert result["heartbeat"].get("auto_run_due_enabled") in {None, False}
    assert popen_calls == []


def test_fanxiu_bt_ensure_watch_doctor_starts_when_heartbeat_stale(monkeypatch, tmp_path, capsys):
    import scripts.fanxiu_bt as fanxiu_bt

    class TempRoot:
        def __call__(self, *parts):
            return tmp_path.joinpath(*parts)

    class FakeProcess:
        pid = 456

        def poll(self):
            return None

    popen_calls = []

    def fake_popen(command, **kwargs):
        popen_calls.append({"command": command, "kwargs": kwargs})
        return FakeProcess()

    heartbeat_path = tmp_path / "fanxiu-watch" / "doctor_watch_heartbeat.json"
    heartbeat_path.parent.mkdir(parents=True)
    heartbeat_path.write_text(
        json.dumps({"pid": 123, "updated_at": 100.0, "stable_latest_path": str(tmp_path / "old.json"), "severity": "blocked"}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(
        fanxiu_bt,
        "_terminate_stale_doctor_watch",
        lambda heartbeat: {"terminated": True, "pid": heartbeat["pid"], "forced": False},
    )
    monkeypatch.setattr(fanxiu_bt.time, "time", lambda: 400.0)
    monkeypatch.setattr(fanxiu_bt.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "ensure-watch-doctor",
            "--interval-seconds",
            "30",
            "--duration-seconds",
            "60",
            "--stale-after-seconds",
            "180",
            "--screenshot",
            "--screenshot-every",
            "2",
        ],
    )
    monkeypatch.setattr("backend.core.temp_paths.codeyun_temp_root", TempRoot())

    assert fanxiu_bt.main() == 0
    result = json.loads(capsys.readouterr().out)

    assert result["started"] is True
    assert result["pid"] == 456
    assert result["reason"] == "heartbeat_missing_or_stale"
    assert result["stale_owner"] == {"terminated": True, "pid": 123, "forced": False}
    watch_commands = [call["command"] for call in popen_calls if "watch-doctor" in call["command"]]
    assert len(watch_commands) == 1
    command = watch_commands[0]
    assert "watch-doctor" in command
    assert "--interval-seconds" in command
    assert "30.0" in command
    assert "--duration-seconds" in command
    assert "60.0" in command
    assert "--screenshot" in command
    assert "--auto-run-due" not in command
    assert result["output_path"].endswith(".ndjson")


def test_fanxiu_bt_doctor_maintenance_reports_blocking_annotation_action():
    import scripts.fanxiu_bt as fanxiu_bt

    report = {
        "runtime": {"status": "idle", "phase": "scheduler_blocked", "message": "检测到游戏公告遮挡"},
        "scheduler": {
            "next_action": "blocked",
            "message": "检测到游戏公告遮挡",
            "due_tasks": [{"id": "legacy-daily-youli"}],
            "enabled_tasks": [
                {
                    "id": "legacy-daily-youli",
                    "label": "日常_游历",
                    "next_time": "2026-06-15 05:00:00",
                    "last_run_at": "2026-06-15 02:51:18",
                    "last_result": "success",
                }
            ],
        },
        "blocking_overlays": [
            {
                "title": "游戏公告",
                "blocking": True,
                "all_shapes": ["公告"],
                "message": "检测到游戏公告遮挡；资产树「游戏公告」缺少安全推进动作标注",
            }
        ],
    }

    summary = fanxiu_bt._build_maintenance_summary(report)

    assert summary["severity"] == "blocked"
    assert summary["needs_human_annotation"] is True
    assert summary["automation_safe"] is False
    assert summary["state_clean"] is True
    assert summary["due_task_ids"] == ["legacy-daily-youli"]
    assert summary["stale_due_count"] == 1
    assert summary["stale_due_success_count"] == 0
    assert summary["blocked_due_count"] == 1
    assert summary["blocked_due_ids"] == ["legacy-daily-youli"]
    assert "资产树「游戏公告」" in summary["action_required"][0]
    assert summary["annotation_targets"][0]["title"] == "游戏公告"
    assert summary["annotation_targets"][0]["query"]["focus_image_title"] == "游戏公告"
    assert summary["annotation_targets"][0]["acceptable_shapes"] == ["关闭公告"]
    assert summary["annotation_targets"][0]["existing_shapes"] == []
    assert summary["annotation_targets"][0]["all_shapes"] == ["公告"]
    assert summary["annotation_targets"][0]["missing_shapes"] == ["关闭公告"]
    assert summary["annotation_targets"][0]["required_shapes"] == ["关闭公告"]
    assert "安全处理动作标注" in summary["retry_condition"]


def test_fanxiu_bt_doctor_maintenance_blocks_runtime_annotation_error():
    import scripts.fanxiu_bt as fanxiu_bt

    error = (
        "日常_灵塔：当前不在可识别的世界或日常页，且无法通过场景图恢复到 #69："
        "场景跳转缺少可靠标注，已中断，请人工补标/修标后重试："
        "目标场景=#69；当前/点击前场景=#237；动作 shape=确定"
    )
    report = {
        "runtime": {"status": "idle", "phase": "idle_guard", "error": error},
        "scheduler": {
            "next_action": "job_group_disabled",
            "due_tasks": [{"id": "legacy-daily-lingta"}],
            "enabled_tasks": [
                {
                    "id": "legacy-daily-lingta",
                    "label": "日常_灵塔",
                    "last_result": "error",
                }
            ],
        },
    }

    summary = fanxiu_bt._build_maintenance_summary(report)

    assert summary["severity"] == "blocked"
    assert summary["automation_safe"] is False
    assert summary["needs_human_annotation"] is True
    assert summary["blocked_by"][0]["title"] == "场景跳转标注缺失"
    assert "请人工补标/修标" in summary["action_required"][0]
    assert summary["blocked_due_ids"] == ["legacy-daily-lingta"]




def test_fanxiu_bt_doctor_maintenance_reports_daily_audit_visual_incomplete():
    import scripts.fanxiu_bt as fanxiu_bt

    report = {
        "runtime": {"status": "idle", "phase": "idle", "message": "idle"},
        "scheduler": {
            "next_action": "idle",
            "message": "当前没有到期任务",
            "due_tasks": [],
            "enabled_tasks": [
                {
                    "id": "legacy-daily-jianling",
                    "task_type": "daily_jianling",
                    "label": "日常_剑灵",
                    "enabled": True,
                    "next_time": "2026-06-21 05:00:00",
                    "last_run_at": "2026-06-20 05:10:00",
                    "last_result": "success",
                }
            ],
            "daily_audit": {
                "mapped_incomplete": [
                    {
                        "task_id": "legacy-daily-jianling",
                        "task_type": "daily_jianling",
                        "title": "淬剑试炼",
                        "progress": {"current": 0, "total": 1},
                    }
                ],
                "unmapped_incomplete": [
                    {
                        "title": "寻道历练1次",
                        "progress": {"current": 0, "total": 4},
                    }
                ],
            },
        },
    }

    summary = fanxiu_bt._build_maintenance_summary(report)

    assert summary["severity"] == "attention"
    assert summary["visual_incomplete_count"] == 1
    assert summary["visual_incomplete_ids"] == ["legacy-daily-jianling"]
    assert summary["visual_unmapped_incomplete_count"] == 1
    assert "日常页复核" in summary["action_required"][0]


def test_fanxiu_bt_doctor_maintenance_reports_failed_mail_selective_claim_without_due_task():
    import scripts.fanxiu_bt as fanxiu_bt

    report = {
        "runtime": {"status": "idle", "phase": "idle", "message": "idle", "running": False},
        "scheduler": {
            "next_action": "idle",
            "message": "当前没有到期任务",
            "due_tasks": [],
            "enabled_tasks": [
                {
                    "id": "mail-selective-claim",
                    "task_type": "mail_selective_claim",
                    "label": "邮件_选择性领取",
                    "enabled": True,
                    "schedule_times": ["00:05"],
                    "last_run_at": "2026-07-06 01:02:12",
                    "last_result": "stopped",
                    "retry_after": "2026-07-06 01:13:14",
                }
            ],
        },
    }

    summary = fanxiu_bt._build_maintenance_summary(report)

    assert summary["severity"] == "attention"
    assert summary["summary"] == "关键作业失败或残留：邮件_选择性领取"
    assert summary["critical_failed_count"] == 1
    assert summary["critical_failed_ids"] == ["mail-selective-claim"]
    assert summary["critical_failed_tasks"][0]["last_result"] == "stopped"
    assert "关键作业今日失败或残留：邮件_选择性领取" in summary["action_required"][0]
    assert "当前没有到期任务" not in summary["action_required"][0]


def test_fanxiu_bt_doctor_maintenance_ignores_stale_daily_audit_visual_incomplete():
    import scripts.fanxiu_bt as fanxiu_bt

    report = {
        "runtime": {"status": "idle", "phase": "idle", "message": "idle"},
        "scheduler": {
            "next_action": "idle",
            "message": "当前没有到期任务",
            "due_tasks": [],
            "enabled_tasks": [
                {
                    "id": "legacy-daily-lingta",
                    "task_type": "daily_lingta",
                    "label": "日常_灵塔",
                    "enabled": True,
                    "next_time": "2026-06-21 05:00:00",
                    "last_run_at": "2026-06-20 20:08:44",
                    "last_result": "success",
                }
            ],
            "daily_audit": {
                "updated_at": datetime(2026, 6, 20, 15, 7, 28).timestamp(),
                "mapped_incomplete": [
                    {
                        "task_id": "legacy-daily-lingta",
                        "task_type": "daily_lingta",
                        "title": "挑战或扫荡混沌灵塔",
                        "progress": {"current": 0, "total": 1},
                    }
                ],
            },
        },
    }

    summary = fanxiu_bt._build_maintenance_summary(report)

    assert summary["visual_incomplete_count"] == 0
    assert summary["visual_incomplete_ids"] == []


def test_fanxiu_bt_doctor_maintenance_distinguishes_blocked_due_from_old_success():
    import scripts.fanxiu_bt as fanxiu_bt

    report = {
        "runtime": {"status": "idle", "phase": "scheduler_blocked", "message": "检测到游戏公告遮挡"},
        "scheduler": {
            "next_action": "blocked",
            "message": "检测到游戏公告遮挡",
            "due_tasks": [{"id": "legacy-daily-youli"}],
            "enabled_tasks": [
                {
                    "id": "legacy-daily-youli",
                    "label": "日常_游历",
                    "next_time": "2026-06-15 05:00:00",
                    "last_run_at": "2026-06-15 02:51:18",
                    "last_result": "blocked",
                }
            ],
        },
        "blocking_overlays": [
            {
                "title": "游戏公告",
                "blocking": True,
                "message": "检测到游戏公告遮挡；资产树「游戏公告」缺少安全推进动作标注",
            }
        ],
    }

    summary = fanxiu_bt._build_maintenance_summary(report)

    assert summary["severity"] == "blocked"
    assert summary["due_task_ids"] == ["legacy-daily-youli"]
    assert summary["stale_due_count"] == 1
    assert summary["stale_due_success_count"] == 0
    assert summary["blocked_due_count"] == 1
    assert summary["blocked_due_ids"] == ["legacy-daily-youli"]


def test_fanxiu_bt_doctor_ignores_reward_popup_words_without_context(tmp_path, monkeypatch):
    import scripts.fanxiu_bt as fanxiu_bt

    class FakeRunner:
        def _ocr_lines(self, frame):
            return [{"text": "百脉宝魄 点击查看"}]

        def _ocr_text(self, lines):
            return "".join(item["text"] for item in lines)

        def _load_asset_tree(self, path):
            return []

        def _index_images(self, tree):
            return {186: {"title": "灵祖奖励浮层", "shapes": [{"title": "奖励浮层标识"}]}}

        def _find_shape(self, image, title):
            for shape in image.get("shapes") or []:
                if shape.get("title") == title:
                    return shape
            return None

    screenshot = tmp_path / "frame.png"
    screenshot.write_bytes(b"fake-png")

    monkeypatch.setattr(fanxiu_bt, "create_fanxiu_runtime_runner", lambda: FakeRunner())

    blockers = fanxiu_bt._doctor_blocking_overlays({"path": str(screenshot)})

    assert blockers == []


def test_fanxiu_bt_doctor_reports_game_announcement_blocker(tmp_path, monkeypatch):
    import scripts.fanxiu_bt as fanxiu_bt

    class FakeRunner:
        def _ocr_lines(self, frame):
            return [{"text": "游戏公告 更新公告 风险提醒"}]

        def _ocr_text(self, lines):
            return "".join(item["text"] for item in lines)

        def _load_asset_tree(self, path):
            return [{"type": "image", "title": "游戏公告", "shapes": [{"title": "公告"}]}]

        def _index_images(self, tree):
            return {}

        def _find_asset_image_by_title(self, ctx, title):
            for item in ctx.get("asset_tree") or []:
                if item.get("title") == title:
                    return item
            return None

        def _find_shape(self, image, title):
            for shape in image.get("shapes") or []:
                if shape.get("title") == title:
                    return shape
            return None

    screenshot = tmp_path / "frame.png"
    screenshot.write_bytes(b"fake-png")

    monkeypatch.setattr(fanxiu_bt, "create_fanxiu_runtime_runner", lambda: FakeRunner())

    blockers = fanxiu_bt._doctor_blocking_overlays({"path": str(screenshot)})

    assert blockers == [{
        "scene_id": None,
        "title": "游戏公告",
        "keywords": ["游戏公告", "更新公告", "风险提醒"],
        "all_shapes": ["公告"],
        "action_shapes": [],
        "blocking": True,
        "message": "检测到游戏公告遮挡；资产树「游戏公告」缺少「关闭公告」动作标注，自动作业无法安全进入游戏",
    }]


def test_fanxiu_bt_doctor_reports_dungeon_purchase_blocker(tmp_path, monkeypatch):
    import scripts.fanxiu_bt as fanxiu_bt

    class FakeRunner:
        def _ocr_lines(self, frame):
            return [{"text": "破界符 剩余限购次数：1 价格：200 购买并使用"}]

        def _ocr_text(self, lines):
            return "".join(item["text"] for item in lines)

        def _load_asset_tree(self, path):
            return []

        def _index_images(self, tree):
            return {
                224: {
                    "title": "购买破界符",
                    "shapes": [
                        {"title": "购买并使用"},
                        {"title": "限购次数标识"},
                    ],
                }
            }

        def _find_asset_image_by_title(self, ctx, title):
            return None

        def _find_shape(self, image, title):
            if not isinstance(image, dict):
                return None
            for shape in image.get("shapes") or []:
                if shape.get("title") == title:
                    return shape
            return None

    screenshot = tmp_path / "frame.png"
    screenshot.write_bytes(b"fake-png")

    monkeypatch.setattr(fanxiu_bt, "create_fanxiu_runtime_runner", lambda: FakeRunner())

    blockers = fanxiu_bt._doctor_blocking_overlays({"path": str(screenshot)})

    assert blockers == [{
        "scene_id": 224,
        "title": "购买破界符",
        "keywords": ["破界符", "购买并使用", "剩余限购次数", "价格"],
        "all_shapes": ["购买并使用", "限购次数标识"],
        "action_shapes": ["购买并使用"],
        "blocking": True,
        "message": "检测到 #224「购买破界符」弹窗；资产树缺少 #225「空白」，自动作业无法按 #224 连续购买到 #225 后回退",
    }]


def test_fanxiu_bt_doctor_does_not_infer_game_announcement_action_from_jump_target(tmp_path, monkeypatch):
    import scripts.fanxiu_bt as fanxiu_bt

    class FakeRunner:
        def _ocr_lines(self, frame):
            return [{"text": "游戏公告 更新公告 风险提醒"}]

        def _ocr_text(self, lines):
            return "".join(item["text"] for item in lines)

        def _load_asset_tree(self, path):
            return [{"type": "image", "title": "游戏公告", "shapes": [{"title": "公告", "sceneJumpTarget": "18"}]}]

        def _index_images(self, tree):
            return {}

        def _find_asset_image_by_title(self, ctx, title):
            for item in ctx.get("asset_tree") or []:
                if item.get("title") == title:
                    return item
            return None

        def _find_shape(self, image, title):
            for shape in image.get("shapes") or []:
                if shape.get("title") == title:
                    return shape
            return None

    screenshot = tmp_path / "frame.png"
    screenshot.write_bytes(b"fake-png")

    monkeypatch.setattr(fanxiu_bt, "create_fanxiu_runtime_runner", lambda: FakeRunner())

    blockers = fanxiu_bt._doctor_blocking_overlays({"path": str(screenshot)})

    assert blockers == [{
        "scene_id": None,
        "title": "游戏公告",
        "keywords": ["游戏公告", "更新公告", "风险提醒"],
        "all_shapes": ["公告"],
        "action_shapes": [],
        "blocking": True,
        "message": "检测到游戏公告遮挡；资产树「游戏公告」缺少「关闭公告」动作标注，自动作业无法安全进入游戏",
    }]


def test_runtime_management_does_not_import_fanxiu_api_directly():
    source = Path("backend/core/runtime/management.py").read_text(encoding="utf-8")

    assert "from backend.api.fanxiu import" not in source
    assert "backend.api.fanxiu" not in source


def test_core_runtime_runner_does_not_import_fanxiu_api_directly():
    source = Path("backend/core/fanxiu/data_annotation/runtime_runner.py").read_text(encoding="utf-8")

    assert "from backend.api import fanxiu" not in source
    assert "backend.api.fanxiu" not in source


def test_core_runtime_runner_db_engine_is_lazy_loaded():
    source = Path("backend/core/fanxiu/data_annotation/runtime_runner.py").read_text(encoding="utf-8")
    header = source.split("FULLWIDTH_DIGIT_TRANSLATION", 1)[0]

    assert "from backend.db import" not in header
    assert "from backend.models import" not in header
    assert "from sqlmodel import" not in header
    assert "from fastapi import" not in header
    assert "UserDevice" not in header
    assert "FanxiuMailRecord" not in header
    assert "_default_engine: Any | None = None" in source
    assert "def _db_engine" in source
    assert "from backend.db import engine" in source


def test_core_runtime_runner_import_does_not_load_codeyun_orm_modules():
    code = "\n".join(
        [
            "import sys",
            "import backend.core.fanxiu.data_annotation.runtime_runner",
            "for name in ('backend.models', 'backend.db', 'sqlmodel', 'fastapi'):",
            "    assert name not in sys.modules, name",
        ]
    )
    subprocess.run([sys.executable, "-c", code], cwd=Path.cwd(), check=True)


def test_core_runtime_runner_factory_does_not_import_runner_at_module_top():
    source = Path("backend/core/fanxiu/data_annotation/runner.py").read_text(encoding="utf-8")
    header = source.split("def _default_fanxiu_runtime_runner_class", 1)[0]

    assert "fanxiu_data_annotation_runtime_runner" not in header
    assert "from backend.core.fanxiu.data_annotation.runtime_runner import DataAnnotationRuntimeRunner" in source






def test_fanxiu_api_import_does_not_load_runtime_runner_module():
    code = "\n".join(
        [
            "import sys",
            "import backend.api.fanxiu as fanxiu",
            "assert 'backend.core.fanxiu.data_annotation.runtime_runner' not in sys.modules",
            "assert type(fanxiu._DATA_ANNOTATION_RUNTIME_RUNNER).__name__ == '_FanxiuRuntimeRunnerProxy'",
        ]
    )
    subprocess.run([sys.executable, "-c", code], cwd=Path.cwd(), check=True)
