from __future__ import annotations

import subprocess
import json
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from backend.core.codex_saver.service import (
    build_default_codex_saver_config,
    execute_codex_saver_task,
    get_codex_saver_config,
    preview_codex_saver_route,
    save_codex_saver_config,
    _build_worker_system_prompt,
    get_codex_saver_mcp_bearer_config,
    get_codex_saver_runtime_status,
)
from backend.models import AppSetting


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_default_routes_text_to_deepseek() -> None:
    with _session() as session:
        result = preview_codex_saver_route(session, {"task": "write docs", "input_kinds": ["text"]})

    assert result["decision"] == "deepseek"


def test_default_routes_multimodal_to_deny() -> None:
    with _session() as session:
        result = preview_codex_saver_route(session, {"task": "inspect screenshot", "input_kinds": ["image"]})

    assert result["decision"] == "deny"


def test_worker_prompts_cover_all_user_request_types() -> None:
    flash_prompt = _build_worker_system_prompt(tier="flash")
    pro_prompt = _build_worker_system_prompt(tier="pro")

    assert "text and code tasks only" in flash_prompt
    assert "images, screenshots, audio, video, documents, file attachments" in flash_prompt
    assert "text and code CodeYun request types only" in pro_prompt
    assert "multimodal input" in pro_prompt


def test_mcp_bearer_config_reveals_token_only_when_requested(monkeypatch) -> None:
    monkeypatch.setenv("CODEYUN_DEVICE_TOKEN", "device-token")
    from backend.core.settings import get_settings

    get_settings.cache_clear()
    try:
        hidden = get_codex_saver_mcp_bearer_config(reveal=False)
        revealed = get_codex_saver_mcp_bearer_config(reveal=True)
    finally:
        get_settings.cache_clear()

    assert hidden["environment_variable"] == "MCP_BEARER_TOKEN"
    assert hidden["configured"] is True
    assert hidden["token"] == ""
    assert revealed["token"] == "device-token"


def test_legacy_multimodal_codex_decision_migrates_to_deny() -> None:
    with _session() as session:
        config = get_codex_saver_config(session)
        config["multimodal_decision"] = "codex"
        save_codex_saver_config(session, config)
        result = preview_codex_saver_route(
            session,
            {"task": "inspect screenshot", "input_kinds": ["text", "image"]},
        )

    assert result["decision"] == "deny"
    assert result["hit_rules"] == []


def test_multimodal_policy_denies_to_outer_codex(monkeypatch, tmp_path: Path) -> None:
    def fake_chat_with_provider(**_kwargs):
        raise AssertionError("multimodal deny must not call DeepSeek")

    monkeypatch.setattr("backend.core.codex_saver.service.chat_with_provider", fake_chat_with_provider)
    with _session() as session:
        result = execute_codex_saver_task(
            session,
            {"task": "inspect screenshot", "cwd": str(tmp_path), "input_kinds": ["image"]},
        )

    assert result["status"] == "codex_required"
    assert "多模态" in result["reason"]


def test_rule_order_can_override_default() -> None:
    config = build_default_codex_saver_config()
    config["rules"].insert(
        0,
        {
            "id": "force-deny",
            "label": "force deny",
            "enabled": True,
            "order": 1,
            "match": {
                "prompt_includes": ["README"],
                "path_includes": [],
                "file_extensions": [],
                "input_kinds": [],
            },
            "decision": "deny",
            "reason": "test override",
        },
    )
    with _session() as session:
        save_codex_saver_config(session, config)
        result = preview_codex_saver_route(session, {"task": "update README", "input_kinds": ["text"]})

    assert result["decision"] == "deny"
    assert result["reason"] == "test override"


def test_invalid_deepseek_json_returns_failed(monkeypatch, tmp_path: Path) -> None:
    def fake_chat_with_provider(**_kwargs):
        return {"content": "not json"}

    monkeypatch.setattr("backend.core.codex_saver.service.chat_with_provider", fake_chat_with_provider)
    with _session() as session:
        result = execute_codex_saver_task(
            session,
            {"task": "write docs", "cwd": str(tmp_path), "input_kinds": ["text"]},
        )

    assert result["status"] == "failed"
    assert result["fallback"] == "codex_required"


def test_flash_handles_simple_task(monkeypatch, tmp_path: Path) -> None:
    calls: list[str | None] = []

    def fake_chat_with_provider(**kwargs):
        calls.append(kwargs.get("model"))
        return {"content": '{"status":"handled","summary":"ok","patch":"","changed_files":[],"commands":[]}'}

    monkeypatch.setattr("backend.core.codex_saver.service.chat_with_provider", fake_chat_with_provider)
    with _session() as session:
        result = execute_codex_saver_task(
            session,
            {"task": "explain code", "cwd": str(tmp_path), "input_kinds": ["text"]},
        )

    assert result["status"] == "handled"
    assert result["model"] == "deepseek-v4-flash"
    assert result["model_tier"] == "flash"
    assert calls == ["deepseek-v4-flash"]


def test_runtime_status_tracks_active_and_recent_runs(monkeypatch, tmp_path: Path) -> None:
    observed_active: list[dict] = []

    def fake_chat_with_provider(**_kwargs):
        observed_active.append(get_codex_saver_runtime_status())
        return {"content": '{"status":"handled","summary":"runtime ok","patch":"","changed_files":[],"commands":[]}'}

    monkeypatch.setattr("backend.core.codex_saver.service.chat_with_provider", fake_chat_with_provider)
    with _session() as session:
        result = execute_codex_saver_task(
            session,
            {"task": "check runtime panel", "cwd": str(tmp_path), "input_kinds": ["text"]},
        )

    assert result["status"] == "handled"
    assert observed_active
    assert observed_active[0]["active"]
    status = get_codex_saver_runtime_status()
    assert status["recent"]
    assert status["recent"][0]["summary"] == "runtime ok"
    assert status["recent"][0]["status"] == "handled"


def test_flash_escalates_complex_task_to_pro(monkeypatch, tmp_path: Path) -> None:
    calls: list[str | None] = []

    def fake_chat_with_provider(**kwargs):
        calls.append(kwargs.get("model"))
        if kwargs.get("model") == "deepseek-v4-flash":
            return {
                "content": '{"status":"escalate","reason":"complex implementation",'
                '"summary":"","patch":"","changed_files":[],"commands":[]}'
            }
        return {"content": '{"status":"handled","summary":"pro ok","patch":"","changed_files":[],"commands":[]}'}

    monkeypatch.setattr("backend.core.codex_saver.service.chat_with_provider", fake_chat_with_provider)
    with _session() as session:
        result = execute_codex_saver_task(
            session,
            {"task": "implement a complex feature", "cwd": str(tmp_path), "input_kinds": ["text"]},
        )

    assert result["status"] == "handled"
    assert result["model"] == "deepseek-v4-pro"
    assert result["model_tier"] == "pro"
    assert result["escalated"] is True
    assert calls == ["deepseek-v4-flash", "deepseek-v4-pro"]


def test_auto_apply_patch_and_log_rotation(monkeypatch, tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    target = tmp_path / "README.md"
    target.write_text("old\n", encoding="utf-8")
    patch = (
        "diff --git a/README.md b/README.md\n"
        "index 3367afd..3e75765 100644\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )

    def fake_chat_with_provider(**_kwargs):
        return {
            "content": json.dumps(
                {
                    "status": "handled",
                    "summary": "ok",
                    "patch": patch,
                    "changed_files": ["README.md"],
                    "commands": [],
                }
            )
        }

    monkeypatch.setattr("backend.core.codex_saver.service.chat_with_provider", fake_chat_with_provider)
    with _session() as session:
        config = get_codex_saver_config(session)
        config["log_max_bytes"] = 64
        save_codex_saver_config(session, config)
        result = execute_codex_saver_task(
            session,
            {"task": "update readme", "cwd": str(tmp_path), "input_kinds": ["text"]},
        )
        execute_codex_saver_task(
            session,
            {"task": "update readme", "cwd": str(tmp_path), "input_kinds": ["text"]},
        )

    assert result["status"] == "applied"
    assert target.read_text(encoding="utf-8") == "new\n"
    assert (tmp_path / ".codexsaver.log").exists()
    assert (tmp_path / ".codexsaver.log.backup").exists()
