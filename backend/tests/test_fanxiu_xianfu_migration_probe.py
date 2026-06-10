import json
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from scripts.fanxiu_xianfu_migration_probe import audit_xianfu_assets, build_candidates, install_continue_visit_image
from scripts.fanxiu_xianfu_capture_continue import (
    _compact_text,
    _parse_scheduler_next_time,
    _preflight_report,
    _run_runtime_after_install,
    _scheduler_wait_plan,
    _wait_for_free_status,
)


def _candidate(label: str, x: float, y: float) -> dict:
    return {
        "label": label,
        "source": "ocr_snap",
        "normalized": {"x": x, "y": y, "w": 0.1, "h": 0.03},
    }


def _shape(title: str, *, jump: str = "", identity: bool = False, ocr: str = "") -> dict:
    return {
        "title": title,
        "sceneJumpTarget": jump,
        "isSceneIdentity": identity,
        "ocrText": ocr,
        "x": 0.1,
        "y": 0.1,
        "w": 0.1,
        "h": 0.05,
    }


def _xianfu_image(number: int, title: str, shapes: list[dict]) -> dict:
    return {
        "type": "image",
        "title": title,
        "filename": f"{number:04d}.png",
        "width": 900,
        "height": 1600,
        "shapes": shapes,
        "children": [],
    }


def _write_minimal_xianfu_tree(tmp_path: Path, *, bad_jump: bool = False) -> tuple[Path, Path]:
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir()
    for number in (171, 172, 173, 174):
        Image.new("RGB", (900, 1600), "black").save(screenshot_dir / f"{number:04d}.png")
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text(json.dumps([
        {
            "type": "folder",
            "title": "仙府",
            "children": [
                _xianfu_image(171, "仙府主页", [
                    _shape("仙府功能区", identity=True, ocr="仙侣居"),
                    _shape("寻仙台", jump="999" if bad_jump else "172"),
                    _shape("离开", jump="34"),
                ]),
                _xianfu_image(172, "寻仙台", [
                    _shape("寻仙台", identity=True, ocr="寻仙台"),
                    _shape("寻访", jump="173"),
                    _shape("领悟绝技"),
                ]),
                _xianfu_image(173, "仙侣寻访", [
                    _shape("切换心愿", identity=True, ocr="切换心愿"),
                    _shape("绝品仙侣", jump="174", ocr="绝品仙侣"),
                    _shape("寻访一次", ocr="寻访一次"),
                    _shape("返回", jump="172"),
                ]),
                _xianfu_image(174, "绝品仙侣", [
                    _shape("绝品仙侣标识", identity=True, ocr="绝品仙侣"),
                    _shape("状态", ocr="免费"),
                    _shape("价格"),
                    _shape("免费提示", ocr="免费"),
                    _shape("寻访", ocr="寻访一次"),
                    _shape("大奖记录", ocr="大奖记录"),
                    _shape("菜单", ocr="绝品仙侣"),
                    _shape("退出", jump="172"),
                ]),
            ],
        }
    ], ensure_ascii=False), encoding="utf-8")
    return asset_tree, screenshot_dir


def test_install_continue_visit_refuses_unverified_candidates(tmp_path: Path):
    frame = tmp_path / "frame.png"
    Image.new("RGB", (900, 1600), "black").save(frame)
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text(json.dumps([{"type": "folder", "title": "仙府", "children": []}], ensure_ascii=False), encoding="utf-8")

    with pytest.raises(RuntimeError, match="OCR 未全部复核"):
        install_continue_visit_image(
            result={
                "page": "继续寻访",
                "frame_path": str(frame),
                "ocr_verified": False,
                "unverified_labels": ["半价"],
                "candidates": [_candidate("半价", 0.5, 0.7), _candidate("继续", 0.6, 0.8), _candidate("关闭", 0.2, 0.8)],
            },
            asset_tree_path=asset_tree,
            screenshot_dir=tmp_path / "screenshots",
        )

    assert "0175.png" not in asset_tree.read_text(encoding="utf-8")


def test_install_continue_visit_writes_verified_asset(tmp_path: Path):
    frame = tmp_path / "frame.png"
    Image.new("RGB", (900, 1600), "black").save(frame)
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text(json.dumps([{"type": "folder", "title": "仙府", "children": []}], ensure_ascii=False), encoding="utf-8")

    result = install_continue_visit_image(
        result={
            "page": "继续寻访",
            "frame_path": str(frame),
            "ocr_verified": True,
            "unverified_labels": [],
            "candidates": [_candidate("半价", 0.5, 0.7), _candidate("继续", 0.6, 0.8), _candidate("关闭", 0.2, 0.8)],
        },
        asset_tree_path=asset_tree,
        screenshot_dir=tmp_path / "screenshots",
    )

    tree = json.loads(asset_tree.read_text(encoding="utf-8"))
    image = tree[0]["children"][0]
    assert result["image"] == {"title": "继续寻访", "filename": "0175.png", "shape_count": 3}
    assert Path(result["backup_path"]).is_file()
    assert Path(result["screenshot_path"]).is_file()
    assert image["filename"] == "0175.png"
    assert [shape["title"] for shape in image["shapes"]] == ["关闭", "半价", "继续"]
    assert image["shapes"][0]["isSceneIdentity"] is True
    assert image["shapes"][0]["sceneJumpTarget"] == "174"
    assert image["shapes"][2]["sceneJumpTarget"] == "175"


def test_audit_xianfu_assets_accepts_171_to_174_and_reports_missing_optional_175(tmp_path: Path):
    asset_tree, screenshot_dir = _write_minimal_xianfu_tree(tmp_path)

    result = audit_xianfu_assets(
        asset_tree_path=asset_tree,
        screenshot_dir=screenshot_dir,
        output_dir=tmp_path / "audit",
    )

    assert result["ok"] is True
    required = [row for row in result["rows"] if row["required"]]
    optional = [row for row in result["rows"] if not row["required"]]
    assert [row["number"] for row in required] == [171, 172, 173, 174]
    assert all(row["ok"] for row in required)
    assert optional == [{
        "number": 175,
        "title": "继续寻访",
        "filename": "0175.png",
        "required": False,
        "present": False,
        "ok": True,
        "issues": [],
        "warnings": [],
        "shape_count": 0,
        "ocr": {},
    }]
    assert Path(result["output_json"]).is_file()


def test_audit_xianfu_assets_rejects_wrong_required_jump(tmp_path: Path):
    asset_tree, screenshot_dir = _write_minimal_xianfu_tree(tmp_path, bad_jump=True)

    result = audit_xianfu_assets(
        asset_tree_path=asset_tree,
        screenshot_dir=screenshot_dir,
        output_dir=tmp_path / "audit",
    )

    assert result["ok"] is False
    row171 = next(row for row in result["rows"] if row["number"] == 171)
    assert row171["ok"] is False
    assert any("寻仙台.sceneJumpTarget" in issue for issue in row171["issues"])


def test_build_candidates_records_ocr_lines_and_overlay(monkeypatch, tmp_path: Path):
    old_root = tmp_path / "old"
    old_root.mkdir()
    (old_root / "继续寻访.json").write_text(json.dumps({
        "shapes": [
            {"label": "关闭", "points": [[18, 18], [82, 46]]},
        ]
    }, ensure_ascii=False), encoding="utf-8")
    frame = tmp_path / "frame.png"
    Image.new("RGB", (900, 1600), "black").save(frame)
    monkeypatch.setattr(
        "scripts.fanxiu_xianfu_migration_probe._ocr_lines",
        lambda _frame_path: [{"text": "关闭", "x": 20, "y": 20, "w": 60, "h": 24}],
    )

    result = build_candidates(
        page="继续寻访",
        old_root=old_root,
        frame_path=frame,
        old_crop=(0, 0, 900, 1600),
        output_dir=tmp_path / "out",
    )

    assert result["ocr_lines"] == [{"text": "关闭", "x": 20, "y": 20, "w": 60, "h": 24}]
    assert result["candidates"][0]["source"] == "ocr_snap"
    assert Path(result["annotated_path"]).is_file()
    assert Path(tmp_path / "out" / "继续寻访_candidates.json").is_file()


def test_capture_wait_for_free_returns_not_free_without_sleep():
    sleeps: list[float] = []

    status, payload, exit_code = _wait_for_free_status(
        lambda: {"scene_id": 174, "score": 100.0, "status_text": "05:00:00后可免费抽取", "cd_seconds": 18000, "frame": "frame"},
        wait_until_free=False,
        wait_timeout_seconds=0,
        poll_seconds=60,
        sleep=sleeps.append,
        monotonic=lambda: 0.0,
    )

    assert status["cd_seconds"] == 18000
    assert payload["reason"] == "not_free"
    assert exit_code == 0
    assert sleeps == []


def test_capture_wait_for_free_timeout_sleeps_without_reaching_free():
    sleeps: list[float] = []
    emitted: list[dict] = []
    now = {"value": 0.0}

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now["value"] += seconds

    def fake_status() -> dict:
        return {"scene_id": 174, "score": 100.0, "status_text": "05:00:00后可免费抽取", "cd_seconds": 18000, "frame": "frame"}

    status, payload, exit_code = _wait_for_free_status(
        fake_status,
        wait_until_free=True,
        wait_timeout_seconds=2,
        poll_seconds=1,
        sleep=fake_sleep,
        monotonic=lambda: now["value"],
        emit=emitted.append,
    )

    assert status["cd_seconds"] == 18000
    assert payload["reason"] == "free_wait_timeout"
    assert exit_code == 0
    assert sleeps == [1.0, 1.0]
    assert [item["reason"] for item in emitted] == ["waiting_free", "waiting_free"]


def test_capture_wait_for_free_reprepares_when_not_on_174():
    emitted: list[dict] = []
    sleeps: list[float] = []
    statuses = iter([
        {"scene_id": 34, "score": 100.0, "status_text": "", "cd_seconds": None, "frame": "world"},
        {"scene_id": 174, "score": 100.0, "status_text": "免费抽取", "cd_seconds": 0, "frame": "xianfu"},
    ])
    recoveries: list[dict] = []

    status, payload, exit_code = _wait_for_free_status(
        lambda: next(statuses),
        wait_until_free=True,
        wait_timeout_seconds=0,
        poll_seconds=60,
        recover_not_on_174=lambda status: recoveries.append(status) or {"ok": True},
        max_recover_count=1,
        sleep=sleeps.append,
        monotonic=lambda: 0.0,
        emit=emitted.append,
    )

    assert status["scene_id"] == 174
    assert payload is None
    assert exit_code is None
    assert [item["reason"] for item in emitted] == ["reprepare_not_on_174"]
    assert recoveries[0]["scene_id"] == 34
    assert sleeps == [1.0]


def test_capture_wait_for_free_reports_reprepare_failure():
    status, payload, exit_code = _wait_for_free_status(
        lambda: {"scene_id": 34, "score": 100.0, "status_text": "", "cd_seconds": None, "frame": "world"},
        wait_until_free=True,
        wait_timeout_seconds=0,
        poll_seconds=60,
        recover_not_on_174=lambda _status: {"ok": False, "error": "failed"},
        max_recover_count=1,
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    assert status["scene_id"] == 34
    assert payload["reason"] == "reprepare_failed"
    assert payload["recover_runtime"]["error"] == "failed"
    assert exit_code == 1


def test_capture_wait_for_free_retries_cd_unreadable():
    emitted: list[dict] = []
    sleeps: list[float] = []
    statuses = iter([
        {"scene_id": 174, "score": 100.0, "status_text": "乱码", "cd_seconds": None, "frame": "bad"},
        {"scene_id": 174, "score": 100.0, "status_text": "免费抽取", "cd_seconds": 0, "frame": "ok"},
    ])

    status, payload, exit_code = _wait_for_free_status(
        lambda: next(statuses),
        wait_until_free=True,
        wait_timeout_seconds=0,
        poll_seconds=2,
        max_unreadable_count=1,
        sleep=sleeps.append,
        monotonic=lambda: 0.0,
        emit=emitted.append,
    )

    assert status["cd_seconds"] == 0
    assert payload is None
    assert exit_code is None
    assert [item["reason"] for item in emitted] == ["retry_cd_unreadable"]
    assert sleeps == [2.0]


def test_capture_wait_for_free_fails_after_cd_unreadable_retries():
    status, payload, exit_code = _wait_for_free_status(
        lambda: {"scene_id": 174, "score": 100.0, "status_text": "乱码", "cd_seconds": None, "frame": "bad"},
        wait_until_free=True,
        wait_timeout_seconds=0,
        poll_seconds=2,
        max_unreadable_count=0,
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    assert status["scene_id"] == 174
    assert payload["reason"] == "cd_unreadable"
    assert exit_code == 2


def test_run_runtime_after_install_uses_auto_wait_xianfu_task_by_default(monkeypatch):
    calls: list[dict] = []

    class Result:
        returncode = 0
        stdout = "done"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return Result()

    monkeypatch.setattr("scripts.fanxiu_xianfu_capture_continue.subprocess.run", fake_run)

    result = _run_runtime_after_install(entry_id="entry-1", timeout_seconds=123)

    command = calls[0]["command"]
    assert result["ok"] is True
    assert result["stdout"]["text"] == "done"
    assert result["stdout"]["compacted"] is False
    assert "--entry-id" in command
    assert command[command.index("--entry-id") + 1] == "entry-1"
    assert "--run-mode" in command
    assert command[command.index("--run-mode") + 1] == "auto"
    assert "--timeout-seconds" in command
    assert command[command.index("--timeout-seconds") + 1] == "123.0"
    assert "--wait" in command
    assert "--wait-timeout-seconds" in command
    assert command[command.index("--wait-timeout-seconds") + 1] == "123.0"
    assert command[-2:] == ["task", "xianfu_visit_partner"]
    assert calls[0]["timeout"] == 153.0


def test_compact_text_keeps_tail_for_large_output():
    result = _compact_text("\n".join(f"line-{index}" for index in range(60)), max_lines=5, max_chars=1000)

    assert result["compacted"] is True
    assert result["line_count"] == 60
    assert result["text"].splitlines() == ["line-55", "line-56", "line-57", "line-58", "line-59"]


def test_scheduler_wait_plan_uses_xianfu_next_time():
    plan = _scheduler_wait_plan(
        now=datetime(2026, 6, 10, 22, 0, 0),
        extra_seconds=600,
        tasks=[{"id": "xianfu-visit-partner", "next_time": "2026-06-10 23:00:00"}],
    )

    assert plan["found"] is True
    assert plan["next_time"] == "2026-06-10 23:00:00"
    assert plan["seconds_until"] == 3600.0
    assert plan["timeout_seconds"] == 4200.0


def test_parse_scheduler_next_time_accepts_space_and_iso():
    assert _parse_scheduler_next_time("2026-06-10 23:00:00") == datetime(2026, 6, 10, 23, 0, 0)
    assert _parse_scheduler_next_time("2026-06-10T23:00:00") == datetime(2026, 6, 10, 23, 0, 0)
    assert _parse_scheduler_next_time("bad") is None


def test_preflight_report_combines_audit_queue_and_wait_plan(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "scripts.fanxiu_xianfu_capture_continue.audit_xianfu_assets",
        lambda **_kwargs: {
            "ok": True,
            "rows": [{"number": 175, "present": False}],
            "output_json": "audit.json",
        },
    )
    monkeypatch.setattr(
        "scripts.fanxiu_xianfu_capture_continue.fanxiu_data_annotation_manual_jobs",
        lambda: [],
    )
    monkeypatch.setattr(
        "scripts.fanxiu_xianfu_capture_continue.read_scheduler_tasks",
        lambda: [{"id": "xianfu-visit-partner", "next_time": "2026-06-10 23:00:00"}],
    )

    report = _preflight_report(
        asset_tree=tmp_path / "tree.json",
        screenshot_dir=tmp_path / "screenshots",
        wait_extra_seconds=600,
    )

    assert report["ok"] is True
    assert report["asset_audit_ok"] is True
    assert report["image_175_present"] is False
    assert report["manual_job_count"] == 0
    assert report["wait_plan"]["next_time"] == "2026-06-10 23:00:00"
    assert report["asset_audit_output"] == "audit.json"


def test_preflight_report_not_ok_when_manual_queue_not_empty(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "scripts.fanxiu_xianfu_capture_continue.audit_xianfu_assets",
        lambda **_kwargs: {"ok": True, "rows": [], "output_json": "audit.json"},
    )
    monkeypatch.setattr(
        "scripts.fanxiu_xianfu_capture_continue.fanxiu_data_annotation_manual_jobs",
        lambda: [{"id": "manual-1", "status": "pending", "task_type": "go_scene", "label": "go"}],
    )
    monkeypatch.setattr("scripts.fanxiu_xianfu_capture_continue.read_scheduler_tasks", lambda: [])

    report = _preflight_report(
        asset_tree=tmp_path / "tree.json",
        screenshot_dir=tmp_path / "screenshots",
    )

    assert report["ok"] is False
    assert report["manual_job_count"] == 1
    assert report["manual_jobs"][0]["id"] == "manual-1"


def test_run_runtime_after_install_can_use_direct_without_wait(monkeypatch):
    calls: list[dict] = []

    class Result:
        returncode = 0
        stdout = "done"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return Result()

    monkeypatch.setattr("scripts.fanxiu_xianfu_capture_continue.subprocess.run", fake_run)

    result = _run_runtime_after_install(entry_id="entry-1", timeout_seconds=123, run_mode="direct", wait=False)

    command = calls[0]["command"]
    assert result["ok"] is True
    assert command[command.index("--run-mode") + 1] == "direct"
    assert "--wait" not in command
    assert "--wait-timeout-seconds" not in command
    assert command[-2:] == ["task", "xianfu_visit_partner"]
