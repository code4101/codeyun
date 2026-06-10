import json
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
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
from backend.core.fanxiu_behavior_tree import create_fanxiu_runtime_runner
from backend.core import fanxiu_data_annotation_runtime_runner as runtime_runner_core
from backend.core.service_tokens import SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL, create_service_access_token
from backend.core.fanxiu_mumu_control import _compare_frame_crops
from backend.models import FanxiuMailRecord, UserDevice
from backend.core import fanxiu_mumu_control as mumu_control


def _image(title: str, filename: str, shapes: list[dict] | None = None) -> dict:
    return {
        "type": "image",
        "title": title,
        "filename": filename,
        "width": 900,
        "height": 1600,
        "shapes": shapes or [],
    }


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
    image34 = _image("世界", "0034.jpg", [
        {"id": "open", "kind": "rect", "title": "打开下方菜单", "sceneJumpTarget": "35"}
    ])
    image35 = _image("世界下方菜单", "0035.png", [
        {"id": "mail", "kind": "rect", "title": "邮件", "sceneJumpTarget": "121"}
    ])
    image121 = _image("邮件", "0121.png", [])
    tree = [image34, image35, image121]
    ctx = {"entry": object(), "asset_tree": tree, "asset_tree_path": path, "images": {34: image34, 35: image35, 121: image121}}

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(
        runner,
        "_scene_score",
        lambda _ctx, image, _frame: 100.0 if image.get("filename") in {"0034.jpg", "0035.png"} else 0.0,
    )

    candidates = runner._scene_route_candidate_ids(tree, 121)

    assert candidates[:3] == [121, 35, 34]
    assert runner._identify_scene_number(ctx, "frame", candidates) == (35, 100.0)


def test_compare_frame_crops_resizes_current_crop_without_mask():
    reference = np.full((45, 48, 3), 120, dtype=np.uint8)
    current = np.full((36, 38, 3), 120, dtype=np.uint8)

    similarity, score = _compare_frame_crops(reference, current, pixel_tolerance=0)

    assert similarity == 100
    assert score == 1.0


def test_mumu_adb_serial_candidates_use_local_ports_before_proxy_devices(monkeypatch):
    for key in mumu_control.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    mumu_control._MUMU_ADB_SESSION.clear()
    monkeypatch.setattr(mumu_control.fanxiu_android_proxy_service, "devices", lambda: ["192.168.31.181:5555"])

    assert mumu_control._mumu_adb_serial_candidates() == [
        "127.0.0.1:7555",
        "127.0.0.1:16416",
        "127.0.0.1:5555",
        "192.168.31.181:5555",
    ]


def test_mumu_adb_serial_candidates_keep_default_ports_only_without_devices(monkeypatch):
    for key in mumu_control.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    mumu_control._MUMU_ADB_SESSION.clear()
    monkeypatch.setattr(mumu_control.fanxiu_android_proxy_service, "devices", lambda: [])

    assert mumu_control._mumu_adb_serial_candidates() == [
        "127.0.0.1:7555",
        "127.0.0.1:16416",
        "127.0.0.1:5555",
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
        "backend.core.fanxiu_data_annotation_runtime_runner._click_game_window2_service",
        lambda payload: clicked.append(payload) or {"ok": True},
    )

    runner._click_shape(ctx, image, shape, "frame")

    assert calls == [{"scan": False, "match_strategy": "anchor_pixel", "ocr_enabled": True}]
    assert clicked[0]["x"] == 140
    assert clicked[0]["y"] == 220


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
        "backend.core.fanxiu_data_annotation_runtime_runner._click_game_window2_service",
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
        "backend.core.fanxiu_data_annotation_runtime_runner._click_game_window2_service",
        lambda payload: clicked.append(payload) or {"ok": True},
    )

    try:
        runner._click_shape(ctx, image, shape, "frame")
    except RuntimeError as exc:
        assert "未能定位浮动按钮" in str(exc)
    else:
        raise AssertionError("expected optional OCR miss on floating action to abort click")
    assert clicked == []


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


def test_auto_close_guard_images_use_only_first_level_popup_group_children():
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


def test_popup_score_can_fallback_to_plain_shapes(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    blank_shape = {"id": "blank", "kind": "rect", "title": "空白", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}
    image = _image("所有提示窗口", "0047.jpg", [blank_shape])

    monkeypatch.setattr(runner, "_shape_score", lambda _ctx, _image, _shape, _frame: 80)

    assert runner._popup_score({"entry": object()}, image, "frame") == 80


def test_scene_jump_edges_infer_nested_leave_returns_to_parent_scene():
    runner = create_fanxiu_runtime_runner()
    leave_shape = {"id": "leave", "kind": "rect", "title": "离开", "x": 0.8, "y": 0.5, "w": 0.1, "h": 0.1}
    tree = [
        _image("世界", "0034.jpg", []),
        {
            "id": "folder-world",
            "type": "folder",
            "title": "世界",
            "children": [
                _image("某区域内部", "0085.png", [leave_shape]),
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
                _image("世界", "0034.jpg", []),
                _image("世界下方菜单", "0035.png", [close_shape]),
            ],
        },
    ]

    edges = runner._scene_jump_edges(tree)

    assert 35 in edges
    assert edges[35][0]["shape"] is close_shape
    assert edges[35][0]["target_ids"] == [34]
    assert runner._find_scene_route(tree, 35, 34) == [edges[35][0]]


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


def test_scene_score_uses_best_scene_identity_shape(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    first_shape = {"id": "closed-menu", "kind": "rect", "title": "打开下方菜单", "isSceneIdentity": True}
    second_shape = {"id": "map", "kind": "rect", "title": "大地图", "isSceneIdentity": True}
    image = _image("世界", "0034.jpg", [first_shape, second_shape])

    def fake_shape_score(_ctx, _image, shape, _frame, **_kwargs):
        return 1 if shape["id"] == "closed-menu" else 100

    monkeypatch.setattr(runner, "_shape_score", fake_shape_score)

    assert runner._scene_score({"entry": object()}, image, "frame") == 100


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
    assert calls == [False, True]


def test_scene_jump_intermediate_confirm_shape_is_limited_to_leave_popup():
    runner = create_fanxiu_runtime_runner()
    confirm = {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.6, "y": 0.6, "w": 0.1, "h": 0.05}
    leave_popup = _image("离开场景", "0086.png", [confirm])

    assert runner._scene_jump_intermediate_confirm_shape(leave_popup, {"title": "离开"}) is confirm
    assert runner._scene_jump_intermediate_confirm_shape(leave_popup, {"title": "领取"}) is None
    assert runner._scene_jump_intermediate_confirm_shape(_image("奖励提示", "0099.png", [confirm]), {"title": "离开"}) is None


def test_scene_route_candidates_include_leave_confirmation_popup():
    runner = create_fanxiu_runtime_runner()
    confirm = {"id": "confirm", "kind": "rect", "title": "确认", "x": 0.6, "y": 0.6, "w": 0.1, "h": 0.05}
    tree = [
        _image("世界", "0034.jpg", []),
        {
            "id": "popup",
            "type": "folder",
            "title": "弹窗",
            "children": [_image("离开场景", "0086.png", [confirm])],
        },
    ]

    assert 86 in runner._scene_route_candidate_ids(tree, 34)


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

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
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


def test_popup_guard_parallel_scoring_uses_one_worker_per_candidate(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    candidates = [{"image": _image(f"图{index}", f"{index:04d}.jpg")} for index in range(12)]
    created_workers: list[int] = []
    real_executor = runtime_runner_core.ThreadPoolExecutor

    class SpyExecutor(real_executor):
        def __init__(self, *args, **kwargs):
            created_workers.append(int(kwargs.get("max_workers") or args[0]))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(runtime_runner_core, "ThreadPoolExecutor", SpyExecutor)
    monkeypatch.setattr(runner, "_popup_score", lambda _ctx, image, _frame: 70 if image["title"] == "图11" else 0)

    scores = runner._auto_close_popup_candidate_scores_parallel({"entry": object()}, candidates, "frame")

    assert created_workers == [len(candidates)]
    assert scores == [0] * 11 + [70]


def test_popup_guard_parallel_scoring_reuses_full_frame_ocr(monkeypatch):
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
    run_match_calls: list[str] = []

    def fake_ocr_lines(frame):
        ocr_calls.append(frame)
        return [{"text": "目标弹窗", "x": 100, "y": 170, "w": 120, "h": 30}]

    monkeypatch.setattr(runner, "_ocr_lines", fake_ocr_lines)
    monkeypatch.setattr(
        runner,
        "_run_match",
        lambda _ctx, image, *_args, **_kwargs: run_match_calls.append(image["title"]) or {"similarity": 0, "matches": []},
    )

    scores = runner._auto_close_popup_candidate_scores_parallel({"entry": object()}, candidates, "frame")

    assert scores == [0.0, 0.0, 100.0]
    assert ocr_calls == ["frame"]
    assert run_match_calls == []


def test_scene_number_scan_reuses_full_frame_ocr(monkeypatch):
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
    ctx = {"entry": object(), "images": {34: image34, 35: image35}}
    ocr_calls: list[str] = []
    run_match_calls: list[str] = []

    def fake_ocr_lines(frame):
        ocr_calls.append(frame)
        return [{"text": "大地图", "x": 100, "y": 170, "w": 120, "h": 30}]

    monkeypatch.setattr(runner, "_ocr_lines", fake_ocr_lines)
    monkeypatch.setattr(
        runner,
        "_run_match",
        lambda _ctx, image, *_args, **_kwargs: run_match_calls.append(image["title"]) or {"similarity": 0, "matches": []},
    )

    scene_id, score = runner._identify_scene_number(ctx, "frame")

    assert (scene_id, score) == (34, 100)
    assert ocr_calls == ["frame"]
    assert run_match_calls == []


def test_popup_guard_parallel_scoring_is_faster_than_serial_for_independent_candidates(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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
    assert specs == {"close_popups": True, "wanling_invite": False}


def test_runtime_guard_defaults_enable_close_popups_only(tmp_path):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")

    with runner._lock:
        runner._sync_guard_status_locked()

    status = runner.status()
    assert status["guard_enabled"] is True
    assert status["guard_items"]["close_popups"]["enabled"] is True
    assert status["guard_items"]["wanling_invite"]["enabled"] is False


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

    assert result == "success"
    assert json.loads(path.read_text(encoding="utf-8"))[0]["shapes"][0]["sceneJumpTarget"] == "2(1)"


def test_go_scene_stops_on_unannotated_result_after_timeout(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
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


def test_mail_claim_check_is_manual_runtime_task():
    task = next(item for item in _default_data_annotation_scheduler_tasks() if item["id"] == "mail-claim-check")

    assert task["task_type"] == "mail_claim_check"
    assert task["label"] == "邮件_领取检查"
    assert task["schedule_kind"] == "manual"
    assert task["enabled"] is False
    assert _data_annotation_task_supported(task)


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
        lambda: runner._execute_mail_claim_check_task(ctx, FakeStopEvent(), {"observe_only": True}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert received == {"action_enabled": False}


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
    assert 1510 <= y <= 1515


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


def test_packet_claim_policy_can_rescue_read_mail_by_title(monkeypatch):
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
    assert row["packet_match"] == "title_only"
    assert row["mail_key"] == "id:lingmai-history"
    assert row["policy"] == "claim"


def test_packet_claim_policy_can_rescue_locked_mail_by_title(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    row = {"title": "香车馈赠", "time_text": "2026年06月09日13:48", "list_has_lock": True}
    record = FanxiuMailRecord(
        mail_key="id:xiangche-latest",
        mail_id="xiangche-latest",
        title="香车馈赠",
        normalized_title="香车馈赠",
        create_time_text="2026年06月09日13:48",
        source="packet",
        status="seen",
        payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币", "amount": 8888}]},
    )
    monkeypatch.setattr(runner, "_find_packet_mail_records_for_visible_row", lambda _title, _time_text: [record])

    runner._prepare_mail_row_policy(row, action_policies={"claim"})

    assert row["status"] == "锁定"
    assert row["mail_key"] == "id:xiangche-latest"
    assert row["policy"] == "claim"


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
    assert clicked_points == [(320, 420)]
    assert clicked_shapes == ["删除"]
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
    assert clicked_points == [(320, 420)]
    assert clicked_shapes == ["领取"]
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
    image = _image("邮件", "0121.png", [])
    shape = {"title": "邮件清单2", "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6}
    drags: list[tuple[float, float, float, float, int]] = []
    monkeypatch.setattr(
        runner,
        "_drag_frame_point",
        lambda _ctx, _image, sx, sy, ex, ey, *, duration_ms=300: drags.append((sx, sy, ex, ey, duration_ms)),
    )

    runner._scroll_shape_content({}, image, shape)

    assert len(drags) == 1
    sx, sy, ex, ey, duration_ms = drags[0]
    assert sx == ex == 450
    assert sy == 1040
    assert ey == 560
    assert duration_ms == 1000


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
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args: None)
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, _frame=None: clicked_shapes.append(shape["title"]))

    result = runner._run_direct_runtime_action(
        lambda: runner._probe_and_maybe_delete_mail_row(ctx, FakeStopEvent(), image121, row),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "seen"
    assert clicked_shapes == ["空白-返回"]


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
            "task_type": "mail_claim_check",
            "label": "邮件_领取检查",
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
    monkeypatch.setattr(runner, "_scroll_shape_content", lambda *_args: dragged.append("scroll"))

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
    monkeypatch.setattr(runner, "_scroll_shape_content", lambda *_args: dragged.append("scroll"))
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


def test_daily_signup_first_confirms_daily_scene_69(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    claim_shape = {"id": "claim", "kind": "rect", "title": "活动报名-领取", "ocrText": "领"}
    click_shape = {"id": "signup", "kind": "rect", "title": "活动报名"}
    image75 = _image("活动报名", "0075.png", [claim_shape, click_shape])
    column_shape = {"id": "column", "kind": "rect", "title": "报名列", "x": 0.7, "y": 0.2, "w": 0.2, "h": 0.6, "contentDirection": "down"}
    back_shape = {"id": "back", "kind": "rect", "title": "返回", "x": 0.04, "y": 0.03, "w": 0.1, "h": 0.06}
    image23 = _image("报名", "0023.png", [column_shape, back_shape])
    reward_shape = {"id": "reward", "kind": "rect", "title": "领取", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1}
    image24 = _image("报名领取灵石奖励", "0024.png", [reward_shape])
    ctx = {"entry": object(), "asset_tree": [image75, image23, image24], "asset_tree_path": path, "images": {75: image75, 23: image23, 24: image24}}
    calls: list[int] = []
    clicked: list[str] = []
    clicked_points: list[tuple[float, float]] = []
    dragged: list[str] = []
    waited: list[int] = []
    ocr_calls = iter([
        [{"x": 700, "y": 400, "w": 60, "h": 30, "text": "报名"}],
        [{"x": 700, "y": 400, "w": 60, "h": 30, "text": "已领取"}],
        [{"x": 700, "y": 400, "w": 60, "h": 30, "text": "已领取"}],
    ])

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_go_scene_task", lambda _ctx, _path, target, _stop_event: calls.append(target) or "success")
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_run_match", lambda *_args, **_kwargs: {"matches": [{"text": "领"}], "similarity": 100})
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, _frame=None: clicked.append(shape["title"]))
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked_points.append((x, y)))
    monkeypatch.setattr(runner, "_drag_frame_point", lambda _ctx, _image, *_args, **_kwargs: dragged.append(_image["title"]))
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: next(ocr_calls))

    def fake_wait_scene_id(_ctx, _stop_event, target_scene_id, **_kwargs):
        waited.append(target_scene_id)
        if False:
            yield BehaviorTreeStatus.RUNNING
        return target_scene_id, 100

    monkeypatch.setattr(runner, "_wait_scene_id", fake_wait_scene_id)

    stop_event = FakeStopEvent()
    result = runner._run_direct_runtime_action(
        lambda: runner._execute_runtime_task(ctx, "daily_signup", {}, stop_event),
        stop_event=stop_event,
        tick_seconds=0.01,
    )

    assert result == "success"
    assert calls == [69]
    assert clicked == ["活动报名", "领取", "返回"]
    assert clicked_points == [(720.0, 415.0)]
    assert dragged == ["报名"]
    assert waited == [23, 24, 23, 69]


def test_daily_signup_finishes_when_no_claim_ocr(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    claim_shape = {"id": "claim", "kind": "rect", "title": "活动报名-领取", "ocrText": "领"}
    click_shape = {"id": "signup", "kind": "rect", "title": "活动报名"}
    image75 = _image("活动报名", "0075.png", [claim_shape, click_shape])
    ctx = {"entry": object(), "asset_tree": [image75], "asset_tree_path": path, "images": {75: image75}}
    clicked: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

    monkeypatch.setattr(runner, "_go_scene_task", lambda _ctx, _path, _target, _stop_event: "success")
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_run_match", lambda *_args, **_kwargs: {"matches": [], "similarity": 0})
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, _frame=None: clicked.append(shape["title"]))
    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not wait #23")))

    stop_event = FakeStopEvent()
    result = runner._run_direct_runtime_action(
        lambda: runner._execute_runtime_task(ctx, "daily_signup", {}, stop_event),
        stop_event=stop_event,
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked == []
    assert any("未识别到「领」" in log["message"] for log in runner.status()["logs"])


def test_daily_signup_skips_go_scene_when_already_daily_scene(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    claim_shape = {"id": "claim", "kind": "rect", "title": "活动报名-领取", "ocrText": "领"}
    click_shape = {"id": "signup", "kind": "rect", "title": "活动报名"}
    image75 = _image("活动报名", "0075.png", [claim_shape, click_shape])
    image69 = _image("日常", "0069.png")
    ctx = {"entry": object(), "asset_tree": [image69, image75], "asset_tree_path": path, "images": {69: image69, 75: image75}}

    class FakeStopEvent:
        def is_set(self):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, preferred: (69, 100) if preferred == [23, 24, 69] else (None, 0))
    monkeypatch.setattr(runner, "_go_scene_task", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not go_scene")))
    monkeypatch.setattr(runner, "_run_match", lambda *_args, **_kwargs: {"matches": [], "similarity": 0})

    stop_event = FakeStopEvent()
    result = runner._run_direct_runtime_action(
        lambda: runner._execute_runtime_task(ctx, "daily_signup", {}, stop_event),
        stop_event=stop_event,
        tick_seconds=0.01,
    )

    assert result == "success"
    assert any("当前已在日常 #69" in log["message"] for log in runner.status()["logs"])
    assert any("未识别到「领」" in log["message"] for log in runner.status()["logs"])


def test_daily_signup_scrolls_signup_column_until_all_signup_items_clicked(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    image23 = _image("报名", "0023.png", [
        {"id": "column", "kind": "rect", "title": "报名列", "x": 0.7, "y": 0.2, "w": 0.2, "h": 0.6, "contentDirection": "down"},
        {"id": "back", "kind": "rect", "title": "返回", "x": 0.04, "y": 0.03, "w": 0.1, "h": 0.06},
    ])
    image24 = _image("报名领取灵石奖励", "0024.png", [
        {"id": "claim", "kind": "rect", "title": "领取", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1},
    ])
    ctx = {"entry": object(), "asset_tree": [image23, image24], "asset_tree_path": path, "images": {23: image23, 24: image24}}
    clicked_points: list[tuple[float, float]] = []
    clicked_shapes: list[str] = []
    dragged: list[tuple[float, float, float, float]] = []
    waited: list[int] = []
    ocr_calls = iter([
        [
            {"x": 700, "y": 340, "w": 70, "h": 30, "text": "已报名"},
            {"x": 50, "y": 400, "w": 745, "h": 30, "text": "丹道问鼎报名"},
        ],
        [{"x": 700, "y": 400, "w": 60, "h": 30, "text": "已领取"}],
        [{"x": 52, "y": 600, "w": 745, "h": 75, "text": "攻击+3.7兆仙缘夺魁报名"}],
        [{"x": 700, "y": 600, "w": 60, "h": 30, "text": "已领取"}],
        [{"x": 700, "y": 600, "w": 60, "h": 30, "text": "已领取"}],
    ])

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def fake_wait_scene_id(_ctx, _stop_event, target_scene_id, **_kwargs):
        waited.append(target_scene_id)
        if False:
            yield BehaviorTreeStatus.RUNNING
        return target_scene_id, 100

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: next(ocr_calls))
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked_points.append((x, y)))
    monkeypatch.setattr(runner, "_click_shape", lambda _ctx, _image, shape, _frame=None: clicked_shapes.append(shape["title"]))
    monkeypatch.setattr(runner, "_wait_scene_id", fake_wait_scene_id)
    monkeypatch.setattr(runner, "_drag_frame_point", lambda _ctx, _image, sx, sy, ex, ey, **_kwargs: dragged.append((sx, sy, ex, ey)))

    stop_event = FakeStopEvent()
    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_signup_signup_list(ctx, stop_event),
        stop_event=stop_event,
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked_points == [(720.0, 415.0), (720.0, 637.5)]
    assert clicked_shapes == ["领取", "领取", "返回"]
    assert waited == [24, 23, 24, 23, 69]
    assert len(dragged) == 2


def test_daily_signup_uses_first_row_marker_to_stop_after_scroll_exhausted(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    first_row = {"id": "first-row", "kind": "rect", "title": "第1行", "x": 0.35, "y": 0.2, "w": 0.25, "h": 0.05}
    image25 = _image("已报名状态", "0025.png", [first_row])
    image23 = _image("报名", "0023.png", [
        {"id": "column", "kind": "rect", "title": "报名列", "x": 0.7, "y": 0.2, "w": 0.2, "h": 0.6, "contentDirection": "down"},
        {"id": "back", "kind": "rect", "title": "返回", "x": 0.04, "y": 0.03, "w": 0.1, "h": 0.06},
    ])
    image23["children"] = [image25]
    image24 = _image("报名领取灵石奖励", "0024.png", [
        {"id": "claim", "kind": "rect", "title": "领取", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1},
    ])
    ctx = {"entry": object(), "asset_tree": [image23, image24], "asset_tree_path": path, "images": {23: image23, 24: image24}}
    clicked_points: list[tuple[float, float]] = []
    dragged: list[tuple[float, float, float, float]] = []
    ocr_calls = iter([
        [{"x": 330, "y": 330, "w": 160, "h": 35, "text": "论道"}],
        [
            {"x": 330, "y": 330, "w": 180, "h": 35, "text": "仙缘斗法"},
            {"x": 50, "y": 500, "w": 745, "h": 35, "text": "仙缘斗法报名"},
        ],
        [{"x": 330, "y": 330, "w": 180, "h": 35, "text": "仙缘斗法"}],
        [{"x": 330, "y": 330, "w": 180, "h": 35, "text": "仙缘斗法"}],
    ])

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    def fake_wait_scene_id(_ctx, _stop_event, target_scene_id, **_kwargs):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return target_scene_id, 100

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: next(ocr_calls))
    monkeypatch.setattr(runner, "_click_frame_point", lambda _ctx, _image, x, y: clicked_points.append((x, y)))
    monkeypatch.setattr(runner, "_click_shape", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_wait_scene_id", fake_wait_scene_id)
    monkeypatch.setattr(runner, "_drag_frame_point", lambda _ctx, _image, sx, sy, ex, ey, **_kwargs: dragged.append((sx, sy, ex, ey)))

    stop_event = FakeStopEvent()
    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_signup_signup_list(ctx, stop_event),
        stop_event=stop_event,
        tick_seconds=0.01,
    )

    assert result == "success"
    assert clicked_points == [(720.0, 517.5)]
    assert len(dragged) == 2


def test_daily_signup_stops_when_scroll_signature_stays_unchanged(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    path = tmp_path / "asset_tree.json"
    path.write_text("[]", encoding="utf-8")
    first_row = {"id": "first-row", "kind": "rect", "title": "第1行", "x": 0.35, "y": 0.2, "w": 0.25, "h": 0.05}
    image25 = _image("已报名状态", "0025.png", [first_row])
    image23 = _image("报名", "0023.png", [
        {"id": "column", "kind": "rect", "title": "报名列", "x": 0.7, "y": 0.2, "w": 0.2, "h": 0.6, "contentDirection": "down"},
        {"id": "back", "kind": "rect", "title": "返回", "x": 0.04, "y": 0.03, "w": 0.1, "h": 0.06},
    ])
    image23["children"] = [image25]
    image24 = _image("报名领取灵石奖励", "0024.png", [
        {"id": "claim", "kind": "rect", "title": "领取", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1},
    ])
    ctx = {"entry": object(), "asset_tree": [image23, image24], "asset_tree_path": path, "images": {23: image23, 24: image24}}
    dragged: list[tuple[float, float, float, float]] = []
    ocr_calls = iter([
        [{"x": 330, "y": 330, "w": 160, "h": 35, "text": "第一页"}],
        [{"x": 330, "y": 330, "w": 160, "h": 35, "text": "第二页"}],
        [{"x": 330, "y": 330, "w": 160, "h": 35, "text": "第二页"}],
    ])

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: next(ocr_calls))
    monkeypatch.setattr(runner, "_click_shape", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_drag_frame_point", lambda _ctx, _image, sx, sy, ex, ey, **_kwargs: dragged.append((sx, sy, ex, ey)))
    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: iter(()))

    stop_event = FakeStopEvent()
    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_signup_signup_list(ctx, stop_event),
        stop_event=stop_event,
        tick_seconds=0.01,
    )

    assert result == "success"
    assert len(dragged) == 2
    assert any("签名未变化" in log["message"] for log in runner.status()["logs"])


def test_daily_signup_signature_excludes_occlusion_marker_regions(tmp_path):
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
    lines = [
        {"x": 300, "y": 390, "w": 280, "h": 30, "text": "随机通知内容甲"},
        {"x": 300, "y": 560, "w": 180, "h": 30, "text": "仙缘斗法"},
    ]

    signature = runner._daily_signup_first_row_signature(lines, image23, ctx=ctx)

    assert signature == "仙缘斗法"


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
    result = runner._run_direct_runtime_action(
        lambda: runner._execute_runtime_task(ctx, "daily_signup", {}, stop_event),
        stop_event=stop_event,
        tick_seconds=0.01,
    )

    assert result == "error"
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

    assert set(status["guard_items"]) == {"close_popups", "wanling_invite"}
    assert status["guard_items"]["close_popups"]["label"] == "关闭弹窗"
    assert status["guard_items"]["wanling_invite"]["label"] == "万灵切磋邀请"



