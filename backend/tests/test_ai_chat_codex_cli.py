from __future__ import annotations

import base64
from pathlib import Path
import subprocess

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from backend.api.ai_chat import _get_extra_providers, _resolve_runtime_provider_config
from backend.core.ai.chat import (
    AiProviderConfig,
    CODEX_CLI_DEFAULT_MODEL,
    CODEX_CLI_MODELS,
    CODEX_CLI_WORKSPACE_DIRNAME,
    _resolve_command_path,
    _summarize_process_output,
    chat_with_provider,
    get_ai_provider_status,
)
from backend.core.access.codex_access_keys import create_codex_access_key
from backend.core.ai.chat_user_config import (
    AiChatUserConfigError,
    list_public_ai_chat_custom_provider_configs,
    list_public_ai_chat_custom_providers,
    save_user_ai_chat_custom_provider,
)
from backend.models import AppSetting, User


def _build_codex_provider(
    provider_id: str = "custom-codex",
    *,
    workspace_dir: str = "",
    session_id: str = "",
) -> AiProviderConfig:
    return AiProviderConfig(
        id=provider_id,
        label="My Codex",
        kind="codex_cli",
        base_url="codex",
        default_model=CODEX_CLI_DEFAULT_MODEL,
        timeout_seconds=600.0,
        api_key="",
        supports_stream=False,
        supports_vision=True,
        requires_api_key=False,
        configured=False,
        models=CODEX_CLI_MODELS,
        is_custom=True,
        sharing_mode="private",
        can_manage=True,
        workspace_dir=workspace_dir,
        session_id=session_id,
    )


def test_codex_cli_status_reports_available_when_version_succeeds(monkeypatch):
    def fake_run(command, **kwargs):
        assert command[-1] == "--version"
        return subprocess.CompletedProcess(command, 0, stdout="OpenAI Codex v0.121.0\n", stderr="")

    monkeypatch.setattr("backend.core.ai.chat.subprocess.run", fake_run)

    status = get_ai_provider_status(
        provider_id="custom-codex",
        base_url="codex",
        extra_providers=(_build_codex_provider(),),
    )

    assert status["id"] == "custom-codex"
    assert status["kind"] == "codex_cli"
    assert status["available"] is True
    assert status["error"] is None


def test_codex_cli_command_resolution_skips_incomplete_repo_node_shim(monkeypatch, tmp_path):
    tools_node_dir = tmp_path / "tools" / "node"
    tools_node_dir.mkdir(parents=True)
    broken_shim = tools_node_dir / "codex.cmd"
    broken_shim.write_text("@echo off\nnode missing-codex.js %*\n", encoding="utf-8")
    global_shim = tmp_path / "global" / "codex.cmd"
    global_shim.parent.mkdir()
    global_shim.write_text("@echo off\necho codex\n", encoding="utf-8")

    monkeypatch.setattr("backend.core.ai.chat._codex_tools_node_dir", lambda: tools_node_dir)
    monkeypatch.setattr("backend.core.ai.chat._get_preferred_codex_command_candidates", lambda: [broken_shim])
    monkeypatch.setattr("backend.core.ai.chat.shutil.which", lambda executable: str(global_shim))

    command = _resolve_command_path(["codex", "--version"])

    assert command == [str(global_shim), "--version"]


def test_codex_cli_command_resolution_uses_node_script_for_cmd(monkeypatch, tmp_path):
    shim = tmp_path / "node-bin" / "codex.cmd"
    script = shim.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    node = shim.parent / "node.exe"
    script.parent.mkdir(parents=True)
    shim.write_text("@echo off\nnode codex.js %*\n", encoding="utf-8")
    script.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    node.write_text("", encoding="utf-8")

    monkeypatch.setattr("backend.core.ai.chat._get_preferred_codex_command_candidates", lambda: [shim])

    command = _resolve_command_path(["codex", "-p", "myprofile", "--version"])

    assert command == [str(node), str(script), "-p", "myprofile", "--version"]


def test_codex_cli_command_resolution_prefers_native_windows_exe(monkeypatch, tmp_path):
    shim = tmp_path / "node-bin" / "codex.cmd"
    script = shim.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    native = (
        shim.parent
        / "node_modules"
        / "@openai"
        / "codex"
        / "node_modules"
        / "@openai"
        / "codex-win32-x64"
        / "vendor"
        / "x86_64-pc-windows-msvc"
        / "codex"
        / "codex.exe"
    )
    script.parent.mkdir(parents=True)
    native.parent.mkdir(parents=True)
    shim.write_text("@echo off\nnode codex.js %*\n", encoding="utf-8")
    script.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    native.write_text("", encoding="utf-8")

    monkeypatch.setattr("backend.core.ai.chat._get_preferred_codex_command_candidates", lambda: [shim])
    monkeypatch.setattr("backend.core.ai.chat.os.name", "nt")

    command = _resolve_command_path(["codex", "--version"])

    assert command == [str(native), "--version"]


def test_summarize_process_output_skips_trailing_node_version():
    detail = _summarize_process_output(
        "Error: Cannot find module 'missing-codex.js'\nNode.js v24.14.0\n"
    )

    assert detail == "Error: Cannot find module 'missing-codex.js'"


def test_codex_cli_chat_uses_isolated_exec_wrapper(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    workspace_dir = tmp_path / "codeyun"
    workspace_dir.mkdir()

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input")
        captured["cwd"] = kwargs.get("cwd")

        output_flag_index = command.index("--output-last-message")
        output_path = Path(command[output_flag_index + 1])
        output_path.write_text("wrapped reply", encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"type":"thread.started","thread_id":"019debcf-8ede-7591-9e59-6ccc85992f8d"}\n',
            stderr="",
        )

    monkeypatch.setattr("backend.core.ai.chat.subprocess.run", fake_run)

    response = chat_with_provider(
        provider_id="custom-codex",
        base_url="codex -p myprofile",
        model="gpt-5.4-mini",
        messages=[{"role": "user", "content": "hello from CodeYun"}],
        system_prompt="Reply briefly.",
        extra_providers=(_build_codex_provider(workspace_dir=str(workspace_dir)),),
    )

    command = captured["command"]

    assert response["content"] == "wrapped reply"
    assert response["model"] == "gpt-5.4-mini"
    exec_index = command.index("exec")
    assert command[exec_index - 2:exec_index] == ["-p", "myprofile"]
    assert "--ignore-user-config" in command
    disable_values = [
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "--disable"
    ]
    assert "image_generation" in disable_values
    assert "plugins" in disable_values
    assert "--dangerously-bypass-approvals-and-sandbox" in command
    assert "--skip-git-repo-check" in command
    assert "--json" in command
    assert "shell_environment_policy.inherit=none" not in command
    assert "--model" in command
    assert "gpt-5.4-mini" in command
    assert "--cd" in command
    assert workspace_dir == Path(command[command.index("--cd") + 1])
    assert captured["cwd"] == str(workspace_dir)
    assert "hello from CodeYun" in str(captured["input"])
    assert "Reply briefly." in str(captured["input"])
    assert "当前工作目录由 CodeYun 通过 --cd 指定" in str(captured["input"])
    assert response["session_id"] == "019debcf-8ede-7591-9e59-6ccc85992f8d"


def test_codex_cli_chat_resumes_explicit_session(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    workspace_dir = tmp_path / "codeyun"
    workspace_dir.mkdir()
    session_id = "019debcf-8ede-7591-9e59-6ccc85992f8d"

    def fake_run(command, **kwargs):
        captured["command"] = command

        output_flag_index = command.index("--output-last-message")
        output_path = Path(command[output_flag_index + 1])
        output_path.write_text("resumed reply", encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f'{{"type":"thread.started","thread_id":"{session_id}"}}\n',
            stderr="",
        )

    monkeypatch.setattr("backend.core.ai.chat.subprocess.run", fake_run)

    response = chat_with_provider(
        provider_id="custom-codex",
        base_url="codex",
        messages=[{"role": "user", "content": "continue"}],
        extra_providers=(
            _build_codex_provider(workspace_dir=str(workspace_dir), session_id=session_id),
        ),
    )

    command = captured["command"]

    assert response["content"] == "resumed reply"
    assert response["session_id"] == session_id
    assert command[-3:] == ["resume", session_id, "-"]


def test_codex_cli_chat_attaches_images(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    image_bytes = b"\x89PNG\r\n\x1a\nfake-png"
    image_payload = f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input")

        image_flag_index = command.index("--image")
        image_path = Path(command[image_flag_index + 1])
        assert image_path.exists()
        assert image_path.read_bytes() == image_bytes

        output_flag_index = command.index("--output-last-message")
        output_path = Path(command[output_flag_index + 1])
        output_path.write_text("image reply", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("backend.core.ai.chat.subprocess.run", fake_run)
    monkeypatch.setattr(
        "backend.core.ai.chat._build_codex_workspace_dir",
        lambda workspace_dir=None: tmp_path / CODEX_CLI_WORKSPACE_DIRNAME,
    )

    response = chat_with_provider(
        provider_id="custom-codex",
        base_url="codex",
        messages=[{"role": "user", "content": "看图", "images": [image_payload]}],
        extra_providers=(_build_codex_provider(),),
    )

    assert response["content"] == "image reply"
    assert "--image" in captured["command"]
    assert "附带 1 张图片" in str(captured["input"])


def test_public_custom_codex_provider_is_visible_to_other_users():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(engine, checkfirst=True)
    AppSetting.__table__.create(engine, checkfirst=True)

    with Session(engine) as session:
        session.add(User(username="alice", nickname="Alice", hashed_password="x", is_active=True, is_superuser=True))
        session.add(User(username="bob", nickname="Bob", hashed_password="x", is_active=True, is_superuser=False))
        session.commit()

        alice = session.get(User, 1)
        bob = session.get(User, 2)
        assert alice is not None
        assert bob is not None

        save_user_ai_chat_custom_provider(
            session,
            alice.id,
            label="Alice Codex",
            kind="codex_cli",
            visibility="public",
            base_url="codex",
            default_model="gpt-5.4-mini",
            models=[],
        )
        save_user_ai_chat_custom_provider(
            session,
            alice.id,
            label="Alice Private",
            kind="codex_cli",
            visibility="private",
            base_url="codex",
            default_model="gpt-5.4-mini",
            models=[],
        )

        visible_items = list_public_ai_chat_custom_providers(session, bob.id)
        visible_configs = list_public_ai_chat_custom_provider_configs(session, bob.id)

    assert len(visible_items) == 1
    assert visible_items[0]["kind"] == "codex_cli"
    assert visible_items[0]["sharing_mode"] == "public"
    assert visible_items[0]["can_manage"] is False
    assert visible_items[0]["id"].startswith("shared-u1-custom-")
    assert visible_items[0]["label"].startswith("Alice Codex @ ")
    assert len(visible_configs) == 1
    assert visible_configs[0].id == visible_items[0]["id"]
    assert visible_configs[0].kind == "codex_cli"
    assert visible_configs[0].can_manage is False


def test_shared_codex_provider_requires_token_for_regular_user():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(engine, checkfirst=True)
    AppSetting.__table__.create(engine, checkfirst=True)

    with Session(engine) as session:
        session.add(User(username="alice", nickname="Alice", hashed_password="x", is_active=True, is_superuser=True))
        session.add(User(username="bob", nickname="Bob", hashed_password="x", is_active=True, is_superuser=False))
        session.commit()

        alice = session.get(User, 1)
        bob = session.get(User, 2)
        assert alice is not None
        assert bob is not None

        created_provider = save_user_ai_chat_custom_provider(
            session,
            alice.id,
            label="Alice Codex",
            kind="codex_cli",
            visibility="public",
            base_url="codex -p admin",
            default_model="gpt-5.4-mini",
            models=[],
        )
        created_token = create_codex_access_key(session, created_by_user_id=alice.id, label="Bob")

        admin_providers = _get_extra_providers(alice, session)
        user_providers = _get_extra_providers(bob, session)

        admin_codex = next(provider for provider in admin_providers if provider.id == created_provider["id"])
        shared_codex = next(provider for provider in user_providers if provider.kind == "codex_cli")

        assert admin_codex.requires_api_key is False
        assert shared_codex.requires_api_key is True
        assert shared_codex.can_manage is False

        with pytest.raises(AiChatUserConfigError, match="Codex 需要访问 Token"):
            _resolve_runtime_provider_config(
                provider=shared_codex.id,
                base_url="codex hacked",
                api_key=None,
                current_user=bob,
                session=session,
                extra_providers=user_providers,
            )

        resolved_provider, resolved_base_url, resolved_api_key = _resolve_runtime_provider_config(
            provider=shared_codex.id,
            base_url="codex hacked",
            api_key=created_token["plaintext_value"],
            current_user=bob,
            session=session,
            extra_providers=user_providers,
        )

    assert resolved_provider == shared_codex.id
    assert resolved_base_url == "codex -p admin"
    assert resolved_api_key == created_token["plaintext_value"]
