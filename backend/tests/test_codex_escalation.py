from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core.codex import escalation


class _Process:
    pid = 4101


def _fake_temp_root(tmp_path: Path):
    def resolve(*parts: str, create: bool = True) -> Path:
        path = tmp_path.joinpath(*parts)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    return resolve


def test_structured_prompt_requires_full_agent_owned_closure():
    prompt = escalation.build_codex_escalation_prompt(
        escalation.CodexEscalationRequest(
            title="场景识别异常",
            problem="wait_scene 持续无法识别当前场景",
            objective="修复后恢复原作业",
            suggested_focus=("优先检查场景识别证据，但可调查整个 CodeYun",),
            evidence=("截图：evidence.png",),
            attempted_actions=("全量场景检索仍无可靠结果",),
            recovery_instructions="从 Scheduler 正式入口重新提交完整 attempt",
            completion_criteria=("Job 形成合法业务终态",),
        )
    )

    assert prompt.startswith("# 场景识别异常")
    assert "请求已发出不代表运行权已移交" in prompt
    assert "只是参考方向，不限制你的行动范围" in prompt
    assert "恢复工程稳定运行是唯一核心目标" in prompt
    assert "最小充分干预只是调查起点" in prompt
    assert "证据要求时扩大到整个 CodeYun" in prompt
    assert "可调查整个 CodeYun" in prompt
    assert "evidence.png" in prompt
    assert "从稳定入口重新提交完整、幂等的新 attempt" in prompt
    assert "运行权已经归还" in prompt
    assert "首先调用 create_goal" in prompt
    assert "update_goal(status='complete')" in prompt
    assert "再完成 Goal" in prompt


def test_escalate_to_codex_dispatches_without_waiting(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _Process()

    monkeypatch.setattr(escalation, "_resolve_codex_executable", lambda: "codex.exe")
    monkeypatch.setattr(escalation, "codeyun_temp_root", _fake_temp_root(tmp_path))
    monkeypatch.setattr(escalation, "popen_service", fake_popen)

    result = escalation.escalate_to_codex(
        "reply with hello world",
        workspace_dir=tmp_path,
        model="gpt-test",
        reasoning_effort="high",
    )

    command = captured["command"]
    kwargs = captured["kwargs"]
    assert result.pid == 4101
    assert result.dispatch_id
    assert result.workspace_dir == str(tmp_path.resolve())
    assert command[0:2] == ["codex.exe", "exec"]
    assert command[-1] == "-"
    assert ["--model", "gpt-test"] == command[command.index("--model") : command.index("--model") + 2]
    assert 'model_reasoning_effort="high"' in command
    assert "--approve-for-me" in command
    assert "--sandbox" not in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert command[command.index("--cd") + 1] == str(tmp_path.resolve())
    assert kwargs["cwd"] == str(tmp_path.resolve())
    assert Path(result.request_path).is_file()
    assert Path(result.prompt_path).read_text(encoding="utf-8") == "reply with hello world"
    assert Path(result.stdout_path).name == "stdout.jsonl"
    assert Path(result.stderr_path).name == "stderr.log"


def test_inspect_codex_dispatch_returns_visible_thread(monkeypatch, tmp_path):
    monkeypatch.setattr(escalation, "codeyun_temp_root", _fake_temp_root(tmp_path))
    dispatch_id = "abc123"
    dispatch_dir = escalation._dispatch_root(dispatch_id, create=True)
    stdout_path = dispatch_dir / "stdout.jsonl"
    stderr_path = dispatch_dir / "stderr.log"
    (dispatch_dir / "dispatch.json").write_text(
        json.dumps(
            {
                "dispatch_id": dispatch_id,
                "pid": 4101,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            }
        ),
        encoding="utf-8",
    )
    stdout_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                json.dumps({"type": "turn.started"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "done"},
                    }
                ),
                json.dumps({"type": "turn.completed"}),
            ]
        ),
        encoding="utf-8",
    )

    status = escalation.inspect_codex_dispatch(dispatch_id)

    assert status.status == "completed"
    assert status.thread_id == "thread-1"
    assert status.codex_url == "codex://threads/thread-1"
    assert status.last_message == "done"


@pytest.mark.parametrize("prompt", ["", "   "])
def test_escalate_to_codex_rejects_empty_prompt(prompt):
    with pytest.raises(ValueError, match="不能为空"):
        escalation.escalate_to_codex(prompt)


def test_escalate_to_codex_rejects_missing_workspace(tmp_path):
    with pytest.raises(NotADirectoryError, match="工作目录"):
        escalation.escalate_to_codex("hello", workspace_dir=tmp_path / "missing")


def test_escalate_to_codex_rejects_invalid_reasoning_effort(tmp_path):
    with pytest.raises(ValueError, match="reasoning_effort"):
        escalation.escalate_to_codex("hello", workspace_dir=tmp_path, reasoning_effort='high"')


def test_incident_dispatch_starts_fresh_thread_with_native_goals(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(escalation, '_resolve_codex_executable', lambda: 'codex.exe')
    monkeypatch.setattr(escalation, 'codeyun_temp_root', _fake_temp_root(tmp_path))
    monkeypatch.setattr(escalation, 'popen_service',
                        lambda command, **kwargs: commands.append(command) or _Process())
    request = escalation.CodexEscalationRequest('fault', 'error', 'recover')
    first = escalation.escalate_to_codex(request, workspace_dir=tmp_path)
    second = escalation.escalate_to_codex(request, workspace_dir=tmp_path)
    assert first.dispatch_id != second.dispatch_id
    for worker_command in commands:
        assert worker_command[1:3] == ['-m', 'backend.core.codex.goal_worker']
        command = json.loads(Path(worker_command[-1]).read_text(encoding='utf-8'))['command']
        assert command[:2] == ['codex.exe', 'exec']
        assert command[command.index('--enable') + 1] == 'goals'
        assert 'resume' not in command


@pytest.mark.parametrize(('goal_status', 'alive', 'expected'), [
    ('active', True, 'running'), ('active', False, 'failed'),
    ('complete', False, 'completed'), (None, False, 'failed'), ('blocked', False, 'failed'),
])
def test_turn_completion_is_not_goal_completion(monkeypatch, tmp_path, goal_status, alive, expected):
    from backend.core.codex import app_server
    monkeypatch.setattr(escalation, 'codeyun_temp_root', _fake_temp_root(tmp_path))
    monkeypatch.setattr(escalation.psutil, 'pid_exists', lambda pid: alive)
    monkeypatch.setattr(app_server, 'read_codex_thread_goal', lambda thread: {'goal': {'status': goal_status}})
    root = tmp_path / 'codex-escalations' / 'goaltest'
    root.mkdir(parents=True)
    request = root / 'request.json'
    request.write_text(json.dumps({'objective': 'repair'}), encoding='utf-8')
    stdout = root / 'stdout.jsonl'
    stdout.write_text('\n'.join(json.dumps(row) for row in [
        {'type': 'thread.started', 'thread_id': 'new-thread'}, {'type': 'turn.completed'},
    ]), encoding='utf-8')
    (root / 'dispatch.json').write_text(json.dumps(dict(
        dispatch_id='goaltest', pid=123, request_path=str(request), stdout_path=str(stdout),
        stderr_path=str(root / 'stderr.log'),
    )), encoding='utf-8')
    result = escalation.inspect_codex_dispatch('goaltest')
    assert result.status == expected
    assert result.goal_status == goal_status


def test_inspect_codex_dispatch_detects_exit_without_terminal(monkeypatch, tmp_path):
    import os
    import subprocess
    import sys

    monkeypatch.setattr(escalation, "codeyun_temp_root", _fake_temp_root(tmp_path))
    dispatch_id = "dead123"
    dispatch_dir = escalation._dispatch_root(dispatch_id, create=True)
    stdout_path = dispatch_dir / "stdout.jsonl"
    stdout_path.write_text(json.dumps({"type": "turn.started"}), encoding="utf-8")
    payload = {"dispatch_id": dispatch_id, "pid": os.getpid(),
               "stdout_path": str(stdout_path), "stderr_path": str(dispatch_dir / "stderr.log")}
    path = dispatch_dir / "dispatch.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert escalation.inspect_codex_dispatch(dispatch_id).status == "running"
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=10)
    payload["pid"] = process.pid
    path.write_text(json.dumps(payload), encoding="utf-8")
    status = escalation.inspect_codex_dispatch(dispatch_id)
    assert status.status == "failed"
    assert "未记录完成终态" in status.error
