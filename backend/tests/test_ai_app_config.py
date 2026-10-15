from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine
from backend.core.ai import app_config as config_module

from backend.core.ai.app_config import (
    AI_APP_CODEX_DIARY,
    AI_APP_CODEX_DIARY_DEFAULT_MODEL,
    AI_APP_CODEX_DIARY_DEFAULT_PROVIDER,
    AI_APP_CODEX_DAILY_SUMMARY,
    AI_APP_GIT_COMMIT,
    AI_APP_GIT_COMMIT_DEFAULT_MODEL,
    AI_APP_GIT_COMMIT_DEFAULT_PROVIDER,
    AI_APP_NOTE_SHEET_EXCEL_IMPORT,
    AI_APP_NOTE_TAXONOMY,
    build_legacy_ai_git_commit_config_setting_key,
    get_user_ai_app_config,
    resolve_ai_app_runtime_config,
    save_user_ai_app_config,
)
from backend.core.ai.chat_user_config import save_user_ai_chat_provider_config
from backend.core.ai.git_repos import get_user_ai_git_commit_config, save_user_ai_git_commit_config
from backend.models import AppSetting, User


@pytest.fixture(autouse=True)
def local_go_key(monkeypatch):
    monkeypatch.setattr(config_module, "read_opencode_go_key", lambda: "go-test-key")


def _build_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(engine, checkfirst=True)
    AppSetting.__table__.create(engine, checkfirst=True)
    return engine


def _create_user(session: Session, username: str = "alice") -> User:
    user = User(username=username, nickname=username, hashed_password="x", is_active=True)
    session.add(user)
    session.commit()
    session.refresh(user)
    # Tests resolving Ollama defaults use a provisioned test account, matching
    # the public runtime permission contract rather than bypassing it.
    from backend.core.ai.ollama_access_keys import create_ollama_access_key
    key = create_ollama_access_key(session, created_by_user_id=user.id)
    save_user_ai_chat_provider_config(session, user.id, "ollama", api_key=key["plaintext_value"])
    return user


def test_ai_git_commit_config_reads_legacy_setting_when_app_config_missing():
    engine = _build_engine()
    with Session(engine) as session:
        user = _create_user(session)
        session.add(
            AppSetting(
                key=build_legacy_ai_git_commit_config_setting_key(user.id),
                value={"provider_id": "codex-cli", "model": "gpt-5.3-codex-spark"},
                updated_at=123.0,
            )
        )
        session.commit()

        app_config = get_user_ai_app_config(session, user.id, AI_APP_GIT_COMMIT)
        compat_config = get_user_ai_git_commit_config(session, user.id)

        assert app_config["provider"] == "codex-cli"
        assert app_config["model"] == ""
        assert compat_config == {
            "provider_id": "codex-cli",
            "model": "",
        }


def test_ai_git_commit_default_uses_ollama_qwen35():
    engine = _build_engine()
    with Session(engine) as session:
        user = _create_user(session)

        app_config = get_user_ai_app_config(session, user.id, AI_APP_GIT_COMMIT)
        runtime = resolve_ai_app_runtime_config(
            session=session,
            current_user=user,
            app_id=AI_APP_GIT_COMMIT,
        )

        assert app_config["enabled"] is True
        assert app_config["provider"] == AI_APP_GIT_COMMIT_DEFAULT_PROVIDER
        assert app_config["model"] == AI_APP_GIT_COMMIT_DEFAULT_MODEL
        assert runtime["provider"] == AI_APP_GIT_COMMIT_DEFAULT_PROVIDER
        assert runtime["model"] == AI_APP_GIT_COMMIT_DEFAULT_MODEL


def test_ai_git_commit_codex_spark_saved_config_is_coerced_to_ollama_qwen35():
    engine = _build_engine()
    with Session(engine) as session:
        user = _create_user(session)
        save_user_ai_app_config(
            session,
            user.id,
            AI_APP_GIT_COMMIT,
            provider="codex-cli",
            model="gpt-5.3-codex-spark",
        )

        app_config = get_user_ai_app_config(session, user.id, AI_APP_GIT_COMMIT)
        runtime = resolve_ai_app_runtime_config(
            session=session,
            current_user=user,
            app_id=AI_APP_GIT_COMMIT,
        )

        assert app_config["provider"] == AI_APP_GIT_COMMIT_DEFAULT_PROVIDER
        assert app_config["model"] == AI_APP_GIT_COMMIT_DEFAULT_MODEL
        assert runtime["provider"] == AI_APP_GIT_COMMIT_DEFAULT_PROVIDER
        assert runtime["model"] == AI_APP_GIT_COMMIT_DEFAULT_MODEL


def test_ai_git_commit_compat_save_writes_app_config():
    engine = _build_engine()
    with Session(engine) as session:
        user = _create_user(session)

        saved = save_user_ai_git_commit_config(session, user.id, "openrouter", "anthropic/claude-opus-4.6")
        app_config = get_user_ai_app_config(session, user.id, AI_APP_GIT_COMMIT)

        assert saved["provider_id"] == "openrouter"
        assert saved["model"] == "anthropic/claude-opus-4.6"
        assert app_config["provider"] == "openrouter"
        assert app_config["model"] == "anthropic/claude-opus-4.6"


def test_legacy_deepseek_app_ignores_official_account_secret_and_model():
    engine = _build_engine()
    with Session(engine) as session:
        user = _create_user(session)
        save_user_ai_chat_provider_config(
            session,
            user.id,
            "deepseek",
            base_url="https://api.deepseek.com/v1",
            preferred_models=["deepseek-default"],
            api_key="sk-deepseek-plaintext-value",
        )
        save_user_ai_app_config(
            session,
            user.id,
            AI_APP_NOTE_TAXONOMY,
            provider="deepseek",
            model="deepseek-app-model",
        )

        runtime = resolve_ai_app_runtime_config(
            session=session,
            current_user=user,
            app_id=AI_APP_NOTE_TAXONOMY,
        )

        assert runtime["provider"] == "ds"
        assert runtime["base_url"] == "https://opencode.ai/zen/go/v1"
        assert runtime["api_key"] == "go-test-key"
        assert runtime["model"] == "deepseek-v4.1-flash"


def test_excel_import_uses_cc_without_system_api_credentials():
    engine = _build_engine()
    with Session(engine) as session:
        system_account = _create_user(session, "code4101")
        user = _create_user(session, "attendance-editor")
        save_user_ai_chat_provider_config(
            session,
            system_account.id,
            "deepseek",
            base_url="https://system.deepseek.example/v1",
            api_key="sk-system-deepseek",
        )

        runtime = resolve_ai_app_runtime_config(
            session=session,
            current_user=user,
            app_id=AI_APP_NOTE_SHEET_EXCEL_IMPORT,
        )

        assert runtime["provider"] == 'cc'
        assert runtime["base_url"] is None
        assert runtime["api_key"] is None
        assert runtime["resource_scope"] == "local"
        assert runtime["system_resource_id"] is None
        assert "resource_owner_user_id" not in runtime


def test_excel_import_ignores_both_user_and_system_official_deepseek_keys():
    engine = _build_engine()
    with Session(engine) as session:
        system_account = _create_user(session, "code4101")
        user = _create_user(session, "attendance-editor")
        save_user_ai_chat_provider_config(
            session,
            system_account.id,
            "deepseek",
            api_key="sk-system-deepseek",
        )
        save_user_ai_chat_provider_config(
            session,
            user.id,
            "deepseek",
            api_key="sk-user-deepseek",
        )

        runtime = resolve_ai_app_runtime_config(
            session=session,
            current_user=user,
            app_id=AI_APP_NOTE_SHEET_EXCEL_IMPORT,
        )

        assert runtime["api_key"] is None
        assert runtime["resource_scope"] == "local"
        assert runtime["system_resource_id"] is None


def test_legacy_app_uses_local_go_without_system_official_fallback():
    engine = _build_engine()
    with Session(engine) as session:
        system_account = _create_user(session, "code4101")
        user = _create_user(session, "ordinary-user")
        save_user_ai_chat_provider_config(
            session,
            system_account.id,
            "deepseek",
            api_key="sk-system-deepseek",
        )
        save_user_ai_app_config(
            session,
            user.id,
            AI_APP_NOTE_TAXONOMY,
            provider="deepseek",
            model="deepseek-v4-pro",
        )

        runtime = resolve_ai_app_runtime_config(
            session=session,
            current_user=user,
            app_id=AI_APP_NOTE_TAXONOMY,
        )

        assert runtime["api_key"] == "go-test-key"
        assert runtime["resource_scope"] == "local"
        assert runtime["system_resource_id"] is None


def test_excel_import_whitelist_does_not_grant_other_system_provider_resources():
    engine = _build_engine()
    with Session(engine) as session:
        system_account = _create_user(session, "code4101")
        user = _create_user(session, "attendance-editor")
        save_user_ai_chat_provider_config(
            session,
            system_account.id,
            "openrouter",
            api_key="sk-system-openrouter",
        )
        save_user_ai_app_config(
            session,
            user.id,
            AI_APP_NOTE_SHEET_EXCEL_IMPORT,
            provider="openrouter",
            model="some-model",
        )

        runtime = resolve_ai_app_runtime_config(
            session=session,
            current_user=user,
            app_id=AI_APP_NOTE_SHEET_EXCEL_IMPORT,
        )

        assert runtime["api_key"] is None
        assert runtime["resource_scope"] == 'local'
        assert runtime['provider'] == 'cc'
        assert runtime["system_resource_id"] is None


def test_legacy_deepseek_preferred_model_is_pinned_to_go_flash():
    engine = _build_engine()
    with Session(engine) as session:
        user = _create_user(session)
        save_user_ai_chat_provider_config(
            session,
            user.id,
            "deepseek",
            base_url="https://api.deepseek.com/v1",
            preferred_models=["deepseek-preferred"],
            api_key="sk-deepseek-plaintext-value",
        )
        save_user_ai_app_config(session, user.id, AI_APP_CODEX_DAILY_SUMMARY, provider="deepseek", model="")

        runtime = resolve_ai_app_runtime_config(
            session=session,
            current_user=user,
            app_id=AI_APP_CODEX_DAILY_SUMMARY,
        )

        assert runtime["provider"] == "ds"
        assert runtime["model"] == "deepseek-v4.1-flash"


def test_codex_diary_default_uses_deepseek_flash():
    engine = _build_engine()
    with Session(engine) as session:
        user = _create_user(session)

        app_config = get_user_ai_app_config(session, user.id, AI_APP_CODEX_DIARY)
        runtime = resolve_ai_app_runtime_config(
            session=session,
            current_user=user,
            app_id=AI_APP_CODEX_DIARY,
        )

        assert app_config["enabled"] is True
        assert app_config["provider"] == AI_APP_CODEX_DIARY_DEFAULT_PROVIDER
        assert app_config["model"] == AI_APP_CODEX_DIARY_DEFAULT_MODEL
        assert runtime["provider"] == AI_APP_CODEX_DIARY_DEFAULT_PROVIDER
        assert runtime["model"] == AI_APP_CODEX_DIARY_DEFAULT_MODEL


def test_diary_legacy_pro_is_read_as_flash_and_flash_survives_save():
    from backend.core.ai.app_config import build_ai_app_config_setting_key

    engine = _build_engine()
    with Session(engine) as session:
        user = _create_user(session)
        session.add(AppSetting(
            key=build_ai_app_config_setting_key(user.id),
            value={"apps": {AI_APP_CODEX_DIARY: {
                "provider": "deepseek", "model": "deepseek-v4-pro",
            }}},
        ))
        session.commit()
        assert get_user_ai_app_config(session, user.id, AI_APP_CODEX_DIARY)["model"] == "deepseek-v4.1-flash"
        saved = save_user_ai_app_config(
            session, user.id, AI_APP_CODEX_DIARY,
            provider="deepseek", model="deepseek-v4-flash",
        )
        assert saved["provider"] == "ds"
        assert saved["model"] == "deepseek-v4.1-flash"
        runtime = resolve_ai_app_runtime_config(
            session=session, current_user=user, app_id=AI_APP_CODEX_DIARY,
            model="deepseek-v4-pro",
        )
        assert runtime["model"] == "deepseek-v4.1-flash"


def test_codex_diary_manual_provider_selection_is_preserved():
    engine = _build_engine()
    with Session(engine) as session:
        user = _create_user(session)
        save_user_ai_app_config(
            session,
            user.id,
            AI_APP_CODEX_DIARY,
            provider="custom-codex-cli",
            model="gpt-5.5",
        )

        app_config = get_user_ai_app_config(session, user.id, AI_APP_CODEX_DIARY)
        runtime = resolve_ai_app_runtime_config(
            session=session,
            current_user=user,
            app_id=AI_APP_CODEX_DIARY,
        )

        assert app_config["provider"] == "custom-codex-cli"
        assert app_config["model"] == "gpt-5.5"
        assert runtime["provider"] == "custom-codex-cli"
        assert runtime["model"] == "gpt-5.5"
