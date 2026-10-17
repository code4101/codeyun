"""Privacy boundaries: official ingress, stable references, local execution and safe errors."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.ai import privacy


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "_root", lambda: tmp_path / "ai" / "privacy")
    return tmp_path


def extractor(monkeypatch, entities):
    def chat(**kwargs):
        assert kwargs["provider_id"] == "ds"
        assert kwargs["thinking"] is False
        text = kwargs["messages"][0]["content"]
        found = [e for e in entities if e["value"] in text]
        return {"provider": "deepseek-official", "content": json.dumps({"entities": found})}
    monkeypatch.setattr(privacy, "chat_with_provider", chat)


def test_prepare_handoff_and_stable_followups(vault, monkeypatch):
    extractor(monkeypatch, [{"value": "陈某某", "kind": "PERSON"}, {"value": "s3cret!", "kind": "PASSWORD"}])
    first = privacy.prepare("为陈某某登录，密码s3cret!")
    safe = json.dumps(first, ensure_ascii=False)
    assert "陈某某" not in safe and "s3cret!" not in safe
    assert first["text"] == "为{{PERSON_0001}}登录，密码{{PASSWORD_0001}}"
    second = privacy.prepare("继续为陈某某操作", session_id=first["session_id"])
    assert second["text"] == "继续为{{PERSON_0001}}操作"
    assert len(second["variables"]) == 2
    assert privacy.handoff(first["session_id"]) == second
    directory = vault / "ai" / "privacy" / first["session_id"]
    assert "s3cret!" in (directory / ".env").read_text(encoding="utf-8")
    assert "s3cret!" not in (directory / "manifest.json").read_text(encoding="utf-8")


def test_values_roundtrip_quotes_newlines_and_no_env_interpolation(vault, monkeypatch):
    secret = 'abc\n"quote"\\path=${HOME}\tend' + "single'quote\\\\tail"
    extractor(monkeypatch, [{"value": secret, "kind": "PASSWORD"}])
    packet = privacy.prepare("密码是" + secret)
    from dotenv import dotenv_values
    values = dotenv_values(packet["env_file"], interpolate=False)
    assert values["PASSWORD_0001"] == secret
    assert privacy.redact(packet["session_id"], json.dumps(secret)) == '\"{{PASSWORD_0001}}\"'


def test_overlapping_spans_replaced_longest_first(vault, monkeypatch):
    extractor(monkeypatch, [{"value": "alice", "kind": "ACCOUNT"}, {"value": "alice@example.test", "kind": "EMAIL"}])
    packet = privacy.prepare("alice / alice@example.test")
    assert packet["text"] == "{{ACCOUNT_0001}} / {{EMAIL_0001}}"


@pytest.mark.parametrize("entities", [None, {}, [{"value": "invented", "kind": "PERSON"}],
                                          [{"value": "real", "kind": "INVALID"}],
                                          [{"value": "", "kind": "PASSWORD"}]])
def test_invalid_extraction_does_not_publish_mapping(vault, monkeypatch, entities):
    monkeypatch.setattr(privacy, "chat_with_provider", lambda **_: {
        "provider": "deepseek-official", "content": json.dumps({"entities": entities})})
    with pytest.raises(privacy.PrivacyError, match="privacy_translation_failed"):
        privacy.prepare("real")
    assert not list(vault.rglob(".env"))


def test_wrong_provider_and_provider_errors_fail_closed(vault, monkeypatch):
    monkeypatch.setattr(privacy, "chat_with_provider", lambda **_: {
        "provider": "opencode-go", "content": '{"entities": []}'})
    with pytest.raises(privacy.PrivacyError, match="privacy_translation_failed"):
        privacy.prepare("secret")
    def fail(**_):
        raise RuntimeError("private password leaked by upstream error")
    monkeypatch.setattr(privacy, "chat_with_provider", fail)
    with pytest.raises(privacy.PrivacyError) as exc:
        privacy.prepare("secret")
    assert str(exc.value) == "privacy_translation_failed"


@pytest.mark.parametrize("session_id", ["../escape", "", "a" * 33, "A" * 32])
def test_handoff_rejects_invalid_ids(vault, session_id):
    with pytest.raises(privacy.PrivacyError):
        privacy.handoff(session_id)


def test_unknown_reference_does_not_call_model(vault, monkeypatch):
    monkeypatch.setattr(privacy, "chat_with_provider", lambda **_: pytest.fail("Must validate references first"))
    with pytest.raises(privacy.PrivacyError, match="unknown_privacy_variable"):
        privacy.prepare("use {{PASSWORD_0001}}")


def test_no_sensitive_data_and_forget(vault, monkeypatch):
    extractor(monkeypatch, [])
    packet = privacy.prepare("计算 1+1")
    assert packet["variables"] == [] and packet["text"] == "计算 1+1"
    privacy.forget(packet["session_id"])
    with pytest.raises(privacy.PrivacyError):
        privacy.handoff(packet["session_id"])


def test_standard_dotenv_runtime_uses_real_values(vault, monkeypatch, tmp_path):
    import os, sys
    from backend.core.agents.cli import run_process
    extractor(monkeypatch, [{"value": "fake-secret-123", "kind": "PASSWORD"}])
    packet = privacy.prepare("password fake-secret-123")
    output_file = tmp_path / "effect.txt"
    script = tmp_path / "operation.py"
    script.write_text(
        "from dotenv import dotenv_values\nfrom pathlib import Path\n"
        f"values=dotenv_values({packet['env_file']!r}, interpolate=False)\n"
        f"Path({str(output_file)!r}).write_text(values['PASSWORD_0001'])\n"
        "print('operation completed')\n", encoding="utf-8")
    result = run_process([sys.executable, str(script)], env=os.environ.copy(), directory=tmp_path, timeout=15)
    assert result.returncode == 0
    assert output_file.read_text() == "fake-secret-123"
    assert "fake-secret-123" not in result.stdout


def test_redact_result_preserves_json_even_for_quote_secret(vault, monkeypatch):
    extractor(monkeypatch, [{"value": '"', "kind": "PASSWORD"}])
    packet = privacy.prepare('quote "')
    result = privacy.redact_result(packet["session_id"], {"text": ['secret="'], "exit_code": 0})
    assert result == {"text": ["secret={{PASSWORD_0001}}"], "exit_code": 0}


def test_agent_privacy_option_sanitizes_handoff_and_return(vault, monkeypatch, tmp_path):
    from backend.core.agents import cli
    extractor(monkeypatch, [{"value": "fake-secret-123", "kind": "PASSWORD"}])
    monkeypatch.setattr(cli, "bootstrap", lambda _: None)
    monkeypatch.setattr(cli, "cli_command", lambda *args: (["fake"], {}))
    def run(*args, **kwargs):
        assert "fake-secret-123" not in kwargs["input_text"]
        assert "{{PASSWORD_0001}}" in kwargs["input_text"]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": "fake-secret-123", "session_id": "test"}))
    monkeypatch.setattr(cli, "run_process", run)
    result = cli.run_agent(resource="cc", prompt="use fake-secret-123", directory=tmp_path, privacy=True)
    assert result["ok"] and result["privacy_session"]
    assert result["text"] == "{{PASSWORD_0001}}"


def test_http_owner_boundary_and_safe_payload(vault, monkeypatch):
    from backend.api.ai_privacy import router
    from backend.core.access.auth import get_current_active_superuser
    app = FastAPI()
    app.include_router(router, prefix="/privacy")
    client = TestClient(app)
    assert client.post("/privacy/prepare", json={"text": "hello"}).status_code in (401, 403)
    app.dependency_overrides[get_current_active_superuser] = lambda: SimpleNamespace(id=1)
    invalid = client.post("/privacy/prepare", json={"text": {"password": "fake-secret-123"}})
    assert invalid.status_code == 422 and "fake-secret-123" not in invalid.text
    extractor(monkeypatch, [{"value": "fake-secret-123", "kind": "PASSWORD"}])
    response = client.post("/privacy/prepare", json={"text": "password fake-secret-123"})
    assert response.status_code == 200
    assert "fake-secret-123" not in response.text
    session_id = response.json()["session_id"]
    assert "fake-secret-123" not in client.get(f"/privacy/{session_id}").text
    assert client.delete(f"/privacy/{session_id}").status_code == 200


def test_codex_receives_safe_handoff_without_starting_cli(vault, monkeypatch, tmp_path):
    from backend.core.agents import cli
    extractor(monkeypatch, [{"value": "fake-secret-123", "kind": "PASSWORD"}])
    monkeypatch.setattr(cli, "run_process", lambda *a, **kw: pytest.fail("Codex uses current session"))
    result = cli.run_agent(resource="codex", prompt="use fake-secret-123", directory=tmp_path, privacy=True)
    assert result["status"] == "current_session_required"
    assert result["handoff"]["env_file"].endswith(".env")
    assert "fake-secret-123" not in json.dumps(result)
