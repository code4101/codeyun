from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from backend.core.ai_app_config import (
    AI_APP_CODEX_DIARY,
    AI_APP_CODEX_DIARY_DEFAULT_MODEL,
    AI_APP_CODEX_DIARY_DEFAULT_PROVIDER,
    AI_APP_CODEX_DAILY_SUMMARY,
    AI_APP_GIT_COMMIT,
    AI_APP_GIT_COMMIT_DEFAULT_MODEL,
    AI_APP_GIT_COMMIT_DEFAULT_PROVIDER,
    AI_APP_NOTE_TAXONOMY,
    build_legacy_ai_git_commit_config_setting_key,
    get_user_ai_app_config,
    resolve_ai_app_runtime_config,
    save_user_ai_app_config,
)
from backend.core.ai_chat_user_config import save_user_ai_chat_provider_config
from backend.core.ai_git_repos import get_user_ai_git_commit_config, save_user_ai_git_commit_config
from backend.models import AppSetting, User


def _build_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(engine, checkfirst=True)
    AppSetting.__table__.create(engine, checkfirst=True)
    return engine


def _create_user(session: Session) -> User:
    user = User(username="alice", nickname="Alice", hashed_password="x", is_active=True)
    session.add(user)
    session.commit()
    session.refresh(user)
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
        assert app_config["model"] == "gpt-5.3-codex-spark"
        assert compat_config == {
            "provider_id": "codex-cli",
            "model": "gpt-5.3-codex-spark",
        }


def test_ai_git_commit_default_uses_codex_spark():
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


def test_ai_git_commit_deepseek_flash_saved_config_is_coerced_to_codex_spark():
    engine = _build_engine()
    with Session(engine) as session:
        user = _create_user(session)
        save_user_ai_app_config(
            session,
            user.id,
            AI_APP_GIT_COMMIT,
            provider="deepseek",
            model="deepseek-v4-flash",
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


def test_resolve_ai_app_runtime_config_uses_app_model_and_provider_account_config():
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

        assert runtime["provider"] == "deepseek"
        assert runtime["base_url"] == "https://api.deepseek.com/v1"
        assert runtime["api_key"] == "sk-deepseek-plaintext-value"
        assert runtime["model"] == "deepseek-app-model"


def test_resolve_ai_app_runtime_config_falls_back_to_provider_preferred_model():
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

        assert runtime["provider"] == "deepseek"
        assert runtime["model"] == "deepseek-preferred"


def test_codex_diary_default_uses_deepseek_pro():
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
