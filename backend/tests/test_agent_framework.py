"""Stable role, routing and CLI-result contracts; no model calls or secrets."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.core.agents import cli as runner


@pytest.mark.parametrize("business,aux", [("data-manager", "cc"), ("pgraph", "cc"), ("fanxiu", None), ('attendance', None)])
def test_business_and_explicit_override(business, aux):
    config = runner.load_config()
    assert runner.resolve(config, business=business)["aux"] == aux
    assert runner.resolve(config, business=business, pair="ds")["main"] == "ds"
    assert runner.resolve(config, business=business, pair="ds")["aux"] is None
    assert config["businesses"][business]["aux"] == aux


@pytest.mark.parametrize("pair", ["", "ds+cc+codex", "ds+", "unknown"])
def test_invalid_explicit_pairs(pair):
    if pair:
        with pytest.raises(ValueError):
            runner.resolve(runner.load_config(), pair=pair)
    else:
        assert runner.resolve(runner.load_config())["aux"] is None


@pytest.mark.parametrize("usage", [
    {"available": True, "windows": [{"remaining_percent": 0}]},
    {"available": True, "windows": [{"remaining_percent": 50}]},
    {"available": False},
])
@pytest.mark.parametrize("previous", ["", "opencode-go", "deepseek-official"])
def test_ds_is_always_official(usage, previous):
    route, reason = runner.ds_route(runner.load_config(), usage, previous)
    assert route["provider"] == "deepseek-official"
    assert route["base_url"] == "https://api.deepseek.com/v1"
    assert reason == "official_only"


def test_select_ds_route_does_not_probe_go(monkeypatch):
    monkeypatch.setattr("backend.core.opencode_usage.read_opencode_go_usage",
                        lambda: pytest.fail("Must not contact Go"))
    assert runner.select_ds_route()[0]["provider"] == "deepseek-official"
    with pytest.raises(ValueError):
        runner.ds_route(runner.load_config(), {}, "claude")


def test_provider_override_is_isolated_and_never_serializes_secret():
    original = {"OPENCODE_CONFIG_CONTENT": json.dumps({"permission": {"edit": "deny"}})}
    result = runner.official_environment(runner.load_config(), original, "synthetic-secret")
    assert "synthetic-secret" not in result["OPENCODE_CONFIG_CONTENT"]
    assert json.loads(result["OPENCODE_CONFIG_CONTENT"])["permission"]["edit"] == "deny"
    assert "DEEPSEEK_API_KEY" not in original


@pytest.mark.parametrize("payload,status", [
    ({"is_error": False, "session_id": "s", "result": "done"}, "completed"),
    ({"is_error": True, "result": "failed"}, "agent_error"),
    ({"permission_denials": [{"tool_name": "Edit"}], "result": "done"}, "permission_denied"),
    ({}, "missing_final_text"),
])
def test_cc_semantics_override_success_exit(payload, status):
    assert runner.parse_result("claude", json.dumps(payload), 0)["status"] == status


def test_opencode_error_preserves_session_even_with_text():
    events = [{"type": "text", "sessionID": "ses_example", "part": {"type": "text", "text": "partial"}},
              {"type": "error", "error": {"name": "APIError"}}]
    result = runner.parse_result("opencode", "\n".join(map(json.dumps, events)), 0)
    assert not result["ok"]
    assert result["session_id"] == "ses_example"


def test_windows_arguments_are_data_not_shell_source():
    prompt = '中文\n$HOME `literal` "quotes" $(never execute)'
    with patch.object(runner.shutil, "which", return_value="C:/tools/opencode.cmd"), patch.object(runner.os, "name", "nt"):
        command, env = runner.cli_command("opencode", ["run", prompt], {})
    assert "-EncodedCommand" in command
    assert prompt not in " ".join(command)
    assert json.loads(env["CODEYUN_AGENT_ARGS"])[1] == prompt


@pytest.mark.skipif(os.name != "nt", reason="Windows shim contract")
def test_windows_shim_roundtrip_preserves_each_argument(tmp_path):
    script = tmp_path / "echo-args.ps1"
    script.write_text('ConvertTo-Json -InputObject $args -Compress', encoding="utf-8-sig")
    expected = ["-p", '中文\n$HOME `literal` "quotes" $(never execute)', "--output-format", "json"]
    with patch.object(runner.shutil, "which", return_value=str(script)):
        command, env = runner.cli_command(str(script), expected, os.environ.copy())
    result = runner.run_process(command, env=env, directory=tmp_path, timeout=15)
    assert result.returncode == 0
    assert json.loads(result.stdout) == expected


def test_background_request_carries_business_profile_to_selected_transport(monkeypatch):
    from backend.core.agents.contracts import AgentTaskRequest
    from backend.core.agents.dispatch import dispatch_agent_task
    from backend.core.codex import escalation
    import backend.core.codex as codex
    from backend.core.agents import background
    request = AgentTaskRequest(title="incident", problem="failure", objective="repair", business="fanxiu")
    captured = []
    monkeypatch.setattr(background, 'dispatch_claude', lambda r, **kw: captured.append(r) or 'dispatch')
    monkeypatch.setattr(background, 'dispatch_codex', lambda r, **kw: captured.append(r) or 'dispatch')
    assert dispatch_agent_task(request) == "dispatch"
    assert captured[0].business == 'fanxiu'
    assert captured[0].acceptance_checks == ('fanxiu.engineering_control_returned',)
    assert escalation.CodexEscalationRequest is AgentTaskRequest
    override = AgentTaskRequest(title="incident", problem="failure", objective="repair",
                                business="fanxiu", pair="codex")
    assert dispatch_agent_task(override) == 'dispatch'
    override = AgentTaskRequest(title='incident', problem='failure', objective='repair',
                               business='fanxiu', pair='cc')
    assert dispatch_agent_task(override) == 'dispatch'
    monkeypatch.setattr(codex, 'escalate_to_codex', lambda r, **kw: 'legacy_codex')
    assert dispatch_agent_task(AgentTaskRequest(title='other', problem='x', objective='y')) == 'legacy_codex'


def test_builtin_codex_descriptor_inherits_model():
    from backend.core.ai.chat import get_ai_provider
    resource = get_ai_provider("codex")
    assert resource.resource == "codex"
    assert resource.default_model == ""
    assert resource.models == ()


def _py(code: str) -> list[str]:
    import sys
    return [sys.executable, "-c", code]


def test_run_process_returns_when_grandchild_keeps_capture_handles(tmp_path):
    import subprocess, sys, time
    marker = tmp_path / "grandchild.pid"
    grand = "import time; time.sleep(20)"
    child = ("import subprocess, sys; "
             f"p = subprocess.Popen([sys.executable, '-c', {grand!r}]); "
             f"open({str(marker)!r}, 'w').write(str(p.pid)); "
             "print('输出✓'); print('警告', file=sys.stderr)")
    started = time.monotonic()
    try:
        result = runner.run_process(_py(child), env={**os.environ, "PYTHONIOENCODING": "utf-8"}, directory=tmp_path, timeout=60)
        elapsed = time.monotonic() - started
    finally:
        if marker.exists():
            pid = int(marker.read_text())
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            else:
                import signal
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
    assert elapsed < 15
    assert result.returncode == 0
    assert result.stdout == "输出✓\n"
    assert result.stderr == "警告\n"


def test_run_process_stdin_preserves_complete_utf8_prompt(tmp_path):
    prompt = '中文\n{"instruction":"修改 poll_interval_ms 为 250", "path":"C:/path with spaces/file"}\n$HOME `literal`'
    result = runner.run_process(_py("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
        env=os.environ.copy(), directory=tmp_path, timeout=15, input_text=prompt)
    assert result.returncode == 0
    assert result.stdout == prompt


def test_run_process_nonzero_exit_keeps_streams(tmp_path):
    result = runner.run_process(_py("import sys; print('o'); print('e', file=sys.stderr); sys.exit(3)"),
                                env=os.environ.copy(), directory=tmp_path, timeout=30)
    assert (result.returncode, result.stdout, result.stderr) == (3, "o\n", "e\n")


def test_run_process_timeout_is_bounded_and_kills_only_own_tree(tmp_path):
    import subprocess, sys, time
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            runner.run_process(_py("import time; time.sleep(60)"), env=os.environ.copy(),
                               directory=tmp_path, timeout=2)
        assert time.monotonic() - started < 20
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait(timeout=10)


@pytest.mark.parametrize("failure", [OSError("no taskkill"), "timeout"])
def test_run_process_timeout_still_kills_root_when_tree_kill_fails(tmp_path, failure):
    import subprocess, sys, time
    if os.name != "nt":
        pytest.skip("taskkill path is Windows-only")
    roots = []
    real_popen = subprocess.Popen

    def spy(*a, **kw):
        roots.append(real_popen(*a, **kw))
        return roots[-1]

    def broken_run(cmd, *a, **kw):
        raise (subprocess.TimeoutExpired(cmd, 30) if failure == "timeout" else failure)

    bystander = real_popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with patch.object(runner.subprocess, "Popen", spy), patch.object(runner.subprocess, "run", broken_run):
            started = time.monotonic()
            with pytest.raises(subprocess.TimeoutExpired) as raised:
                runner.run_process(_py("import time; time.sleep(60)"), env=os.environ.copy(),
                                   directory=tmp_path, timeout=2)
        assert time.monotonic() - started < 20
        assert raised.value.timeout == 2
        assert roots[0].poll() is not None
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait(timeout=10)
