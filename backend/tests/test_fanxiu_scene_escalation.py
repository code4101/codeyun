from __future__ import annotations

from pathlib import Path
import pytest

from backend.core.codex import CodexDispatch
from backend.core.fanxiu.data_annotation import scene_escalation


@pytest.fixture(autouse=True)
def isolated_scheduler(monkeypatch):
    state = {"job_group_enabled": True}
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.kernel_scheduler_control.read_scheduler_settings",
        lambda: dict(state),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.kernel_scheduler_control.set_scheduler_job_group_enabled",
        lambda enabled: state.update(job_group_enabled=enabled) or dict(state),
    )
    return state


def test_persistent_scene_unknown_stops_dispatch_before_starting_codex(monkeypatch, tmp_path) -> None:
    evidence_path = tmp_path / "unknown.png"
    evidence_path.write_bytes(b"png")
    evidence_path.with_suffix(".json").write_text("{}", encoding="utf-8")
    asset_tree_path = tmp_path / "tree.json"
    asset_tree_path.write_text("[]", encoding="utf-8")
    events: list[tuple[str, object]] = []
    captured = {}

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.kernel_scheduler_control.set_scheduler_job_group_enabled",
        lambda enabled: events.append(("scheduler", enabled)) or {"job_group_enabled": enabled},
    )

    def dispatch(request):
        events.append(("codex", request.title))
        captured["request"] = request
        return CodexDispatch(
            dispatch_id="dispatch-1",
            pid=4101,
            workspace_dir=str(tmp_path),
            request_path=str(tmp_path / "request.json"),
            prompt_path=str(tmp_path / "prompt.md"),
            stdout_path=str(tmp_path / "stdout.jsonl"),
            stderr_path=str(tmp_path / "stderr.log"),
            created_at="2026-09-04T00:00:00+00:00",
        )

    monkeypatch.setattr(scene_escalation, "escalate_to_codex", dispatch)

    result = scene_escalation.escalate_persistent_scene_unknown(
        task_id="daily-test",
        task_label="等待目标场景",
        entry_id="entry-1",
        expected_scene_ids=[301, 301, 302],
        evidence_frame_path=str(evidence_path),
        asset_tree_path=asset_tree_path,
        layer0_wait_seconds=5.0,
        unmatched_guard_seconds=120.0,
    )

    assert result.dispatch_id == "dispatch-1"
    assert events[0] == ("scheduler", False)
    assert events[1][0] == "codex"
    request = captured["request"]
    assert "#301, #302" in request.evidence[1]
    assert "恢复凡修稳定工程运行" in request.objective
    assert "整个 CodeYun" in request.objective
    assert any("render_unknown_scene_overview" in item for item in request.suggested_focus)
    assert "不得恢复旧 Cell" in request.recovery_instructions
    assert any("重新启用工程 Job 派发" in item for item in request.completion_criteria)


def test_persistent_scene_unknown_requires_a_real_evidence_file(tmp_path, isolated_scheduler) -> None:
    try:
        scene_escalation.escalate_persistent_scene_unknown(
            task_id="daily-test",
            task_label="等待目标场景",
            entry_id="entry-1",
            expected_scene_ids=[301],
            evidence_frame_path=str(tmp_path / "missing.png"),
            asset_tree_path=None,
            layer0_wait_seconds=5.0,
            unmatched_guard_seconds=120.0,
        )
    except ValueError as exc:
        assert "原始帧" in str(exc)
    else:
        raise AssertionError("Codex escalation must not start without evidence")
    assert isolated_scheduler["job_group_enabled"] is False


@pytest.mark.parametrize("engineering", [True, False])
def test_known_scene_repairs_once_without_recursive_ai_dispatch(
    monkeypatch, tmp_path, isolated_scheduler, engineering,
):
    isolated_scheduler["job_group_enabled"] = engineering
    evidence = tmp_path / "scene63.png"
    evidence.write_bytes(b"png")
    requests = []

    def dispatch(request):
        assert not isolated_scheduler["job_group_enabled"]
        requests.append(request)
        return object()

    monkeypatch.setattr(scene_escalation, "escalate_to_codex", dispatch)
    for _ in range(2):
        scene_escalation.escalate_scene_repair_required(
            task_id="daily-lundao-seat", task_label="返回世界", entry_id="entry",
            scene_id=63, expected_scene_ids=[34], evidence_frame_path=str(evidence),
            asset_tree_path=None, reason="#63 缺少返回世界的安全路径",
        )
    assert len(requests) == int(engineering)
    if requests:
        request = requests[0]
        assert any("render_scene_comparison" in item for item in request.suggested_focus)
        assert any("无需再次请示" in item for item in request.constraints)
        assert "attempt_id 已清空" in request.recovery_instructions


def test_dispatch_failure_keeps_queue_stopped(monkeypatch, tmp_path, isolated_scheduler):
    evidence = tmp_path / "scene63.png"
    evidence.write_bytes(b"png")

    def fail(request):
        raise OSError("CLI unavailable")

    monkeypatch.setattr(scene_escalation, "escalate_to_codex", fail)
    with pytest.raises(OSError, match="CLI unavailable"):
        scene_escalation.escalate_scene_repair_required(
            task_id="daily-lundao-seat", task_label="返回世界", entry_id="entry",
            scene_id=63, expected_scene_ids=[34], evidence_frame_path=str(evidence),
            asset_tree_path=None, reason="缺少返回",
        )
    assert not isolated_scheduler["job_group_enabled"]
