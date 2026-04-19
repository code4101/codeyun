from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from backend.api.ai_chat import _get_extra_providers, _resolve_runtime_provider_config
from backend.core.ai_chat import (
    AiProviderConfig,
    CODEX_CLI_DEFAULT_MODEL,
    CODEX_CLI_WORKSPACE_DIRNAME,
    chat_with_provider,
    get_ai_provider_status,
)
from backend.core.codex_access_keys import create_codex_access_key
from backend.core.ai_chat_user_config import (
    AiChatUserConfigError,
    list_public_ai_chat_custom_provider_configs,
    list_public_ai_chat_custom_providers,
    save_user_ai_chat_custom_provider,
)
from backend.models import AppSetting, User


def _build_codex_provider(provider_id: str = "custom-codex") -> AiProviderConfig:
    return AiProviderConfig(
        id=provider_id,
        label="My Codex",
        kind="codex_cli",
        base_url="codex",
        default_model=CODEX_CLI_DEFAULT_MODEL,
        timeout_seconds=600.0,
        api_key="",
        supports_stream=False,
        supports_vision=False,
        requires_api_key=False,
        configured=False,
        models=(CODEX_CLI_DEFAULT_MODEL,),
        is_custom=True,
        sharing_mode="private",
        can_manage=True,
    )


def test_codex_cli_status_reports_available_when_version_succeeds(monkeypatch):
    def fake_run(command, **kwargs):
        assert command[-1] == "--version"
        return subprocess.CompletedProcess(command, 0, stdout="OpenAI Codex v0.121.0\n", stderr="")

    monkeypatch.setattr("backend.core.ai_chat.subprocess.run", fake_run)

    status = get_ai_provider_status(
        provider_id="custom-codex",
        base_url="codex",
        extra_providers=(_build_codex_provider(),),
    )

    assert status["id"] == "custom-codex"
    assert status["kind"] == "codex_cli"
    assert status["available"] is True
    assert status["error"] is None


def test_codex_cli_chat_uses_isolated_exec_wrapper(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input")
        captured["cwd"] = kwargs.get("cwd")

        output_flag_index = command.index("--output-last-message")
        output_path = Path(command[output_flag_index + 1])
        output_path.write_text("wrapped reply", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("backend.core.ai_chat.subprocess.run", fake_run)
    monkeypatch.setattr(
        "backend.core.ai_chat._build_codex_workspace_dir",
        lambda: tmp_path / CODEX_CLI_WORKSPACE_DIRNAME,
    )

    response = chat_with_provider(
        provider_id="custom-codex",
        base_url="codex -p myprofile",
        model="gpt-5.4-mini",
        messages=[{"role": "user", "content": "hello from CodeYun"}],
        system_prompt="Reply briefly.",
        extra_providers=(_build_codex_provider(),),
    )

    workspace_dir = tmp_path / CODEX_CLI_WORKSPACE_DIRNAME
    command = captured["command"]

    assert response["content"] == "wrapped reply"
    assert response["model"] == "gpt-5.4-mini"
    assert Path(command[0]).name.lower() in {"codex", "codex.cmd", "codex.exe", "codex.ps1"}
    assert command[1:4] == ["-p", "myprofile", "exec"]
    assert "--dangerously-bypass-approvals-and-sandbox" in command
    assert "--skip-git-repo-check" in command
    assert "--model" in command
    assert "gpt-5.4-mini" in command
    assert "--cd" in command
    assert workspace_dir == Path(command[command.index("--cd") + 1])
    assert captured["cwd"] == str(workspace_dir)
    assert "hello from CodeYun" in str(captured["input"])
    assert "Reply briefly." in str(captured["input"])
    assert "完整的命令执行与文件系统访问权限" in str(captured["input"])


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
