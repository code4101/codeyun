from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from backend.core.ai.chat_user_config import (
    AiChatUserConfigError,
    activate_user_ai_chat_provider_api_key,
    activate_user_ai_chat_provider_base_url,
    delete_user_ai_chat_provider_base_url,
    get_user_ai_chat_provider_runtime_config,
    list_user_ai_chat_provider_configs,
    reveal_user_ai_chat_provider_api_key,
    save_user_ai_chat_provider_config,
    update_user_ai_chat_provider_api_key,
    update_user_ai_chat_provider_base_url,
)
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


def test_saved_provider_api_key_can_be_revealed_by_owner():
    engine = _build_engine()

    with Session(engine) as session:
        session.add(User(username="alice", nickname="Alice", hashed_password="x", is_active=True))
        session.add(User(username="bob", nickname="Bob", hashed_password="x", is_active=True))
        session.commit()

        alice = session.get(User, 1)
        bob = session.get(User, 2)
        assert alice is not None
        assert bob is not None

        saved = save_user_ai_chat_provider_config(
            session,
            alice.id,
            "deepseek",
            base_url="https://api.deepseek.com/v1",
            preferred_models=["deepseek-v4-flash"],
            api_key="sk-deepseek-plaintext-value",
        )
        key_id = saved["keys"][0]["id"]

        second_saved = save_user_ai_chat_provider_config(
            session,
            alice.id,
            "deepseek",
            api_key="sk-second-plaintext-value",
        )
        second_key_id = second_saved["keys"][-1]["id"]
        key_order_before_activate = [item["id"] for item in second_saved["keys"]]
        assert second_saved["active_key_id"] == key_id
        assert second_saved["keys"][0]["is_active"] is True
        assert second_saved["keys"][-1]["is_active"] is False

        activated_saved = activate_user_ai_chat_provider_api_key(session, alice.id, "deepseek", second_key_id)

        listed_keys = list_user_ai_chat_provider_configs(session, alice.id)[0]["keys"]
        listed = next(item for item in listed_keys if item["id"] == key_id)
        revealed = reveal_user_ai_chat_provider_api_key(session, alice.id, "deepseek", key_id)

        assert key_order_before_activate == [key_id, second_key_id]
        assert [item["id"] for item in activated_saved["keys"]] == key_order_before_activate
        assert [item["id"] for item in listed_keys] == key_order_before_activate
        assert activated_saved["active_key_id"] == second_key_id
        assert activated_saved["keys"][-1]["is_active"] is True
        assert listed["masked_value"] == "sk-d...alue"
        assert "plaintext_value" not in listed
        assert revealed["id"] == key_id
        assert revealed["label"] == ""
        assert revealed["masked_value"] == "sk-d...alue"
        assert revealed["plaintext_value"] == "sk-deepseek-plaintext-value"

        updated_saved = update_user_ai_chat_provider_api_key(
            session,
            alice.id,
            "deepseek",
            key_id,
            api_key="sk-updated-plaintext-value",
        )
        updated_revealed = reveal_user_ai_chat_provider_api_key(session, alice.id, "deepseek", key_id)
        assert [item["id"] for item in updated_saved["keys"]] == key_order_before_activate
        assert updated_revealed["plaintext_value"] == "sk-updated-plaintext-value"

        with pytest.raises(AiChatUserConfigError, match="当前来源还没有账号保存的连接配置"):
            reveal_user_ai_chat_provider_api_key(session, bob.id, "deepseek", key_id)


def test_saved_provider_api_key_label_is_empty():
    engine = _build_engine()

    with Session(engine) as session:
        session.add(User(username="alice", nickname="Alice", hashed_password="x", is_active=True))
        session.commit()

        alice = session.get(User, 1)
        assert alice is not None

        saved = save_user_ai_chat_provider_config(
            session,
            alice.id,
            "deepseek",
            api_key="sk-unlabeled-plaintext-value",
        )

        assert saved["keys"][0]["label"] == ""


def test_saved_provider_base_urls_can_be_activated_and_deleted():
    engine = _build_engine()

    with Session(engine) as session:
        session.add(User(username="alice", nickname="Alice", hashed_password="x", is_active=True))
        session.commit()

        alice = session.get(User, 1)
        assert alice is not None

        first = save_user_ai_chat_provider_config(
            session,
            alice.id,
            "deepseek",
            base_url="https://api.deepseek.com/v1",
            preferred_models=["deepseek-v4-flash"],
        )
        first_url_id = first["base_urls"][0]["id"]
        assert first["base_url"] == "https://api.deepseek.com/v1"
        assert first["active_base_url_id"] == first_url_id
        assert first["base_urls"][0]["is_active"] is True

        second = save_user_ai_chat_provider_config(
            session,
            alice.id,
            "deepseek",
            base_url="https://deepseek.example/v1",
        )
        second_url_id = second["base_urls"][-1]["id"]
        url_order_before_activate = [item["id"] for item in second["base_urls"]]
        assert second_url_id != first_url_id
        assert second["base_url"] == "https://api.deepseek.com/v1"
        assert second["active_base_url_id"] == first_url_id
        assert second["base_url_count"] == 2
        assert second["base_urls"][0]["is_active"] is True
        assert second["base_urls"][-1]["is_active"] is False

        deduped = save_user_ai_chat_provider_config(
            session,
            alice.id,
            "deepseek",
            base_url="https://deepseek.example/v1",
        )
        assert deduped["base_url_count"] == 2
        assert deduped["base_url"] == "https://api.deepseek.com/v1"

        activated = activate_user_ai_chat_provider_base_url(session, alice.id, "deepseek", second_url_id)
        runtime = get_user_ai_chat_provider_runtime_config(session, alice.id, "deepseek")
        assert activated["base_url"] == "https://deepseek.example/v1"
        assert activated["active_base_url_id"] == second_url_id
        assert [item["id"] for item in activated["base_urls"]] == url_order_before_activate
        assert runtime["base_url"] == "https://deepseek.example/v1"

        updated = update_user_ai_chat_provider_base_url(
            session,
            alice.id,
            "deepseek",
            first_url_id,
            base_url="https://api.deepseek.com/v2",
        )
        updated_runtime = get_user_ai_chat_provider_runtime_config(session, alice.id, "deepseek")
        assert [item["id"] for item in updated["base_urls"]] == url_order_before_activate
        assert updated["base_url"] == "https://deepseek.example/v1"
        assert updated_runtime["base_url"] == "https://deepseek.example/v1"

        deleted = delete_user_ai_chat_provider_base_url(session, alice.id, "deepseek", first_url_id)
        assert deleted["base_url"] == "https://deepseek.example/v1"
        assert deleted["active_base_url_id"] == second_url_id
        assert deleted["base_url_count"] == 1
