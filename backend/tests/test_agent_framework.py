"""Stable role, routing and CLI-result contracts; no model calls or secrets."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.core.agents import cli as runner


@pytest.mark.parametrize("business,aux", [("data-manager", "cc"), ("pgraph", "cc"), ("fanxiu", "ds")])
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


@pytest.mark.parametrize("usage,provider", [
    ({"available": True, "windows": [{"remaining_percent": 0}]}, "deepseek-official"),
    ({"available": True, "windows": [{"remaining_percent": 30}]}, "opencode-go"),
    ({"available": False, "windows": [{"remaining_percent": 0}]}, "opencode-go"),
    ({"available": False}, "opencode-go"),
])
def test_only_proven_exhaustion_falls_back(usage, provider):
    assert runner.ds_route(runner.load_config(), usage)[0]["provider"] == provider


def test_resume_restores_go_priority_and_rejects_cross_driver_session():
    config = runner.load_config()
    usage = {"available": True, "windows": [{"remaining_percent": 50}]}
    assert runner.ds_route(config, usage, "deepseek-official")[0]["provider"] == "opencode-go"
    with pytest.raises(ValueError):
        runner.ds_route(config, {}, "claude")


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


def test_background_request_carries_business_profile_to_codex_transport(monkeypatch):
    from backend.core.agents.contracts import AgentTaskRequest
    from backend.core.agents.dispatch import dispatch_agent_task
    from backend.core.codex import escalation
    import backend.core.codex as codex
    request = AgentTaskRequest(title="incident", problem="failure", objective="repair", business="fanxiu")
    captured = []
    monkeypatch.setattr(codex, "escalate_to_codex", lambda r, **kw: captured.append(r) or "dispatch")
    assert dispatch_agent_task(request) == "dispatch"
    assert captured == [request]
    assert "组合：codex+ds" in escalation.build_codex_escalation_prompt(request)
    assert escalation.CodexEscalationRequest is AgentTaskRequest
    override = AgentTaskRequest(title="incident", problem="failure", objective="repair",
                                business="fanxiu", pair="codex")
    assert "组合：codex。" in escalation.build_codex_escalation_prompt(override)


def test_builtin_codex_descriptor_inherits_model():
    from backend.core.ai.chat import get_ai_provider
    resource = get_ai_provider("codex")
    assert resource.resource == "codex"
    assert resource.default_model == ""
    assert resource.models == ()
