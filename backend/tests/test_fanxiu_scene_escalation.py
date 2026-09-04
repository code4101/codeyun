from __future__ import annotations

from pathlib import Path

from backend.core.codex import CodexDispatch
from backend.core.fanxiu.data_annotation import scene_escalation


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


def test_persistent_scene_unknown_requires_a_real_evidence_file(tmp_path) -> None:
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
