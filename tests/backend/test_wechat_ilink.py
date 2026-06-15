from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from backend.models import NoteNode
from backend.core.messaging import wechat_ilink
from backend.core.ai.app_config import AI_APP_CODECLAW, save_user_ai_app_config


@pytest.fixture()
def isolated_wechat_store(monkeypatch, tmp_path):
    monkeypatch.setattr(
        wechat_ilink,
        "get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path, secret_key="wechat-test-secret"),
    )
    wechat_ilink._get_fernet.cache_clear()
    wechat_ilink._login_sessions.clear()
    wechat_ilink.shutdown_codex_bridges(join_timeout=0.1)
    yield tmp_path
    wechat_ilink._get_fernet.cache_clear()
    wechat_ilink._login_sessions.clear()
    wechat_ilink.shutdown_codex_bridges(join_timeout=0.1)


class FakeResponse:
    def __init__(self, payload, *, ok=True, status_code=200, content=b"", headers=None):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = "{}" if payload is None else str(payload)
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self._payload


def test_wechat_ilink_saves_token_encrypted(isolated_wechat_store):
    summary = wechat_ilink.save_account(
        account_id="bot@example",
        token="plain-token",
        user_id="user-1",
        base_url="https://ilink.example.com",
    )

    assert summary["account_id"] == "bot@example"
    assert summary["token_masked"] == "plain...token"

    store_text = (isolated_wechat_store / "wechat-ilink" / "accounts.json").read_text(encoding="utf-8")
    assert "plain-token" not in store_text
    assert wechat_ilink.list_accounts()[0]["user_id"] == "user-1"


def test_wechat_ilink_updates_cursor_and_send_uses_context_token(monkeypatch, isolated_wechat_store):
    wechat_ilink.save_account(
        account_id="bot@example",
        token="plain-token",
        user_id="bot-user",
        base_url="https://ilink.example.com",
    )
    posted_payloads = []

    def fake_post(url, headers=None, data=None, timeout=None):
        posted_payloads.append((url, headers, data, timeout))
        if url.endswith("/ilink/bot/getupdates"):
            return FakeResponse(
                {
                    "ret": 0,
                    "get_updates_buf": "cursor-2",
                    "msgs": [
                        {
                            "seq": 12,
                            "from_user_id": "friend-1",
                            "to_user_id": "bot-user",
                            "create_time_ms": 1710000000000,
                            "message_type": 1,
                            "message_state": 2,
                            "context_token": "context-abc",
                            "item_list": [
                                {
                                    "type": 1,
                                    "text_item": {"text": "你好"},
                                }
                            ],
                        }
                    ],
                }
            )
        return FakeResponse({})

    monkeypatch.setattr(wechat_ilink.requests, "post", fake_post)

    updates = wechat_ilink.get_updates("bot@example", timeout_seconds=1)
    assert updates["messages"][0]["text"] == "你好"

    sent = wechat_ilink.send_text_message("bot@example", to_user_id="friend-1", text="收到")
    assert sent["used_context_token"] is True
    assert posted_payloads[0][0] == "https://ilink.example.com/ilink/bot/getupdates"
    assert posted_payloads[1][0] == "https://ilink.example.com/ilink/bot/sendmessage"
    assert b"context-abc" in posted_payloads[1][2]


def test_wechat_ilink_downloads_inbound_image(monkeypatch, isolated_wechat_store):
    wechat_ilink.save_account(
        account_id="bot@example",
        token="plain-token",
        user_id="bot-user",
        base_url="https://ilink.example.com",
    )
    image_bytes = b"\x89PNG\r\n\x1a\nfake-png"
    aes_key = b"0123456789abcdef"
    encrypted = wechat_ilink._aes_ecb_encrypt(image_bytes, aes_key)

    def fake_post(url, headers=None, data=None, timeout=None):
        return FakeResponse(
            {
                "ret": 0,
                "get_updates_buf": "cursor-2",
                "msgs": [
                    {
                        "seq": 12,
                        "from_user_id": "friend-1",
                        "message_type": 1,
                        "context_token": "context-abc",
                        "item_list": [
                            {
                                "type": 2,
                                "image_item": {
                                    "media": {
                                        "full_url": "https://cdn.example.com/image",
                                        "aes_key": base64.b64encode(aes_key).decode("ascii"),
                                    }
                                },
                            }
                        ],
                    }
                ],
            }
        )

    def fake_get(url, timeout=None):
        assert url == "https://cdn.example.com/image"
        return FakeResponse(None, content=encrypted)

    monkeypatch.setattr(wechat_ilink.requests, "post", fake_post)
    monkeypatch.setattr(wechat_ilink.requests, "get", fake_get)

    updates = wechat_ilink.get_updates("bot@example", timeout_seconds=1)

    image = updates["messages"][0]["images"][0]
    assert image["mime_type"] == "image/png"
    assert image["size"] == len(image_bytes)
    assert image["data_url"].startswith("data:image/png;base64,")
    assert Path(image["path"]).read_bytes() == image_bytes


def test_wechat_ilink_login_confirmation_stores_account(monkeypatch, isolated_wechat_store):
    responses = [
        FakeResponse({"qrcode": "qr-1", "qrcode_img_content": "https://qr.example.com/1.png"}),
        FakeResponse(
            {
                "status": "confirmed",
                "ilink_bot_id": "bot@example",
                "ilink_user_id": "user-1",
                "bot_token": "login-token",
                "baseurl": "https://ilink.example.com",
            }
        ),
    ]

    def fake_get(url, headers=None, timeout=None):
        return responses.pop(0)

    monkeypatch.setattr(wechat_ilink.requests, "get", fake_get)

    login = wechat_ilink.start_login()
    result = wechat_ilink.wait_login(session_key=login["session_key"], timeout_seconds=1)

    assert login["qrcode_url"].startswith("data:image/png;base64,")
    assert result["connected"] is True
    assert result["account"]["account_id"] == "bot@example"
    assert wechat_ilink.list_accounts()[0]["base_url"] == "https://ilink.example.com"


def test_wechat_codex_bridge_replies_to_user_text_message(monkeypatch, isolated_wechat_store):
    wechat_ilink.save_account(
        account_id="bot@example",
        token="plain-token",
        user_id="bot-user",
        base_url="https://ilink.example.com",
    )
    sent_messages = []
    session_id = "019debcf-8ede-7591-9e59-6ccc85992f8d"

    def fake_chat_with_provider(*, provider_id=None, messages, model=None, system_prompt=None, extra_providers=(), **kwargs):
        assert provider_id == "wechat-codex-cli"
        assert extra_providers[0].kind == "codex_cli"
        assert extra_providers[0].workspace_dir == str(wechat_ilink.ROOT_DIR)
        assert extra_providers[0].session_id == ""
        assert model == "gpt-test"
        assert "微信发送方：friend-1" in messages[0]["content"]
        assert "时间上下文（Asia/Shanghai）" in messages[0]["content"]
        assert "消息时间戳=1710000000000" in messages[0]["content"]
        assert "消息时间=2024-03-10 00:00:00" in messages[0]["content"]
        assert "系统收到" not in messages[0]["content"]
        assert "昨天=2024-03-09" in messages[0]["content"]
        assert "上周=2024-02-26~2024-03-03" in messages[0]["content"]
        assert "CodeClaw" in system_prompt
        assert "高风险操作" not in system_prompt
        return {"content": "Codex 已处理", "session_id": session_id}

    def fake_send_text_message(account_id, *, to_user_id, text, context_token=None, timeout_seconds=15):
        sent_messages.append(
            {
                "account_id": account_id,
                "to_user_id": to_user_id,
                "text": text,
                "context_token": context_token,
            }
        )
        return {"message_id": "reply-1", "to_user_id": to_user_id, "used_context_token": bool(context_token)}

    monkeypatch.setattr(wechat_ilink, "chat_with_provider", fake_chat_with_provider)
    monkeypatch.setattr(wechat_ilink, "send_text_message", fake_send_text_message)

    result = wechat_ilink.handle_codex_bridge_message(
        "bot@example",
        {
            "from_user_id": "friend-1",
            "text": "帮我看一下项目状态",
            "create_time_ms": 1710000000000,
            "message_type": 1,
            "context_token": "ctx-1",
        },
        model="gpt-test",
        command="codex",
    )

    assert result["message_id"] == "reply-1"
    assert wechat_ilink._load_codex_bridge_session_id("bot@example", "friend-1") == session_id
    assert sent_messages == [
        {
            "account_id": "bot@example",
            "to_user_id": "friend-1",
            "text": "Codex 已处理",
            "context_token": "ctx-1",
        }
    ]


def test_wechat_codex_bridge_uses_codeclaw_ai_app_config(
    monkeypatch,
    isolated_wechat_store,
    session,
    engine,
    auth_user,
):
    wechat_ilink.save_account(
        account_id="bot@example",
        token="plain-token",
        user_id="bot-user",
        base_url="https://ilink.example.com",
        owner_user_id=auth_user.id,
    )
    save_user_ai_app_config(
        session,
        auth_user.id,
        AI_APP_CODECLAW,
        provider="codex-cli",
        model="gpt-5.5",
    )
    captured = {}

    def fake_chat_with_provider(*, provider_id=None, model=None, extra_providers=(), **kwargs):
        captured["provider_id"] = provider_id
        captured["model"] = model
        captured["provider"] = extra_providers[0]
        return {"content": "CodeClaw 已处理", "session_id": ""}

    def fake_send_text_message(account_id, *, to_user_id, text, context_token=None, timeout_seconds=15):
        return {"message_id": "reply-1", "to_user_id": to_user_id, "text": text}

    monkeypatch.setattr(wechat_ilink, "_get_database_engine", lambda: engine)
    monkeypatch.setattr(wechat_ilink, "chat_with_provider", fake_chat_with_provider)
    monkeypatch.setattr(wechat_ilink, "send_text_message", fake_send_text_message)

    result = wechat_ilink.handle_codex_bridge_message(
        "bot@example",
        {
            "from_user_id": "friend-1",
            "text": "帮我看一下项目状态",
            "create_time_ms": 1710000000000,
            "message_type": 1,
            "context_token": "ctx-1",
        },
        model="legacy-model",
        command="codex",
    )

    assert result["message_id"] == "reply-1"
    assert captured["provider_id"] == "codex-cli"
    assert captured["model"] == "gpt-5.5"
    assert captured["provider"].id == "codex-cli"
    assert captured["provider"].workspace_dir == str(wechat_ilink.ROOT_DIR)


def test_wechat_codex_bridge_dash_message_creates_private_note_without_ai(
    monkeypatch,
    isolated_wechat_store,
    session,
    engine,
    auth_user,
):
    wechat_ilink.save_account(
        account_id="bot@example",
        token="plain-token",
        user_id="bot-user",
        base_url="https://ilink.example.com",
        owner_user_id=auth_user.id,
    )
    sent_messages = []

    def fake_chat_with_provider(**kwargs):
        raise AssertionError("dash quick note should not call AI")

    def fake_send_text_message(account_id, *, to_user_id, text, context_token=None, timeout_seconds=15):
        sent_messages.append(
            {
                "account_id": account_id,
                "to_user_id": to_user_id,
                "text": text,
                "context_token": context_token,
            }
        )
        return {"message_id": "quick-note-reply", "to_user_id": to_user_id, "used_context_token": bool(context_token)}

    monkeypatch.setattr(wechat_ilink, "_get_database_engine", lambda: engine)
    monkeypatch.setattr(wechat_ilink, "chat_with_provider", fake_chat_with_provider)
    monkeypatch.setattr(wechat_ilink, "send_text_message", fake_send_text_message)

    result = wechat_ilink.handle_codex_bridge_message(
        "bot@example",
        {
            "from_user_id": "friend-1",
            "text": " - ",
            "create_time_ms": 1710000000000,
            "message_type": 1,
            "context_token": "ctx-1",
        },
        model="gpt-test",
        command="codex",
    )

    session.expire_all()
    note = session.exec(select(NoteNode).where(NoteNode.user_id == auth_user.id)).one()
    assert note.title == "-"
    assert note.content == ""
    assert note.private_level == 1
    assert note.note_form == "document"
    assert note.start_at == note.created_at == note.updated_at
    assert wechat_ilink._load_codex_bridge_session_id("bot@example", "friend-1") == ""
    assert result["message_id"] == "quick-note-reply"
    assert result["handled_without_ai"] is True
    assert result["note"]["id"] == note.id
    assert sent_messages == [
        {
            "account_id": "bot@example",
            "to_user_id": "friend-1",
            "text": sent_messages[0]["text"],
            "context_token": "ctx-1",
        }
    ]
    assert sent_messages[0]["text"].startswith("已记录星图笔记：-（")


def test_wechat_codex_bridge_uses_new_default_for_legacy_config(isolated_wechat_store):
    wechat_ilink.save_account(
        account_id="bot@example",
        token="plain-token",
        user_id="bot-user",
        base_url="https://ilink.example.com",
    )
    summary = wechat_ilink._save_codex_bridge_config(
        "bot@example",
        {
            "enabled": True,
            "model": "gpt-5.4",
            "command": "codex",
        },
    )

    assert summary["codex_bridge"]["model"] == "gpt-5.3-codex-spark"


def test_start_enabled_codex_bridges_restores_persisted_bridge(monkeypatch, isolated_wechat_store):
    wechat_ilink.save_account(
        account_id="enabled@example",
        token="plain-token",
        user_id="bot-user",
        base_url="https://ilink.example.com",
    )
    wechat_ilink.save_account(
        account_id="disabled@example",
        token="plain-token",
        user_id="bot-user",
        base_url="https://ilink.example.com",
    )
    wechat_ilink._save_codex_bridge_config(
        "enabled@example",
        {
            "enabled": True,
            "model": "gpt-5.4",
            "command": "codex",
            "system_prompt": "extra rules",
        },
    )
    wechat_ilink._save_codex_bridge_config(
        "disabled@example",
        {
            "enabled": False,
            "model": "gpt-5.5",
            "command": "codex",
        },
    )
    started = []

    def fake_start_codex_bridge(account_id, *, model=None, command=None, system_prompt=None):
        started.append(
            {
                "account_id": account_id,
                "model": model,
                "command": command,
                "system_prompt": system_prompt,
            }
        )
        return {"account_id": account_id}

    monkeypatch.setattr(wechat_ilink, "start_codex_bridge", fake_start_codex_bridge)

    result = wechat_ilink.start_enabled_codex_bridges()

    assert result == [{"account_id": "enabled@example"}]
    assert started == [
        {
            "account_id": "enabled@example",
            "model": "gpt-5.4",
            "command": "codex",
            "system_prompt": "extra rules",
        }
    ]


def test_start_enabled_codex_bridges_ignores_unreadable_store(isolated_wechat_store):
    store_path = isolated_wechat_store / "wechat-ilink" / "accounts.json"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text("{", encoding="utf-8")

    assert wechat_ilink.start_enabled_codex_bridges() == []


def test_wechat_codex_bridge_passes_images_to_codex(monkeypatch, isolated_wechat_store, tmp_path):
    image_path = tmp_path / "inbound.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-png")
    captured = {}

    def fake_chat_with_provider(*, provider_id=None, messages, model=None, extra_providers=(), **kwargs):
        captured["provider"] = extra_providers[0]
        captured["messages"] = messages
        return {"content": "这是一张图片"}

    def fake_send_text_message(account_id, *, to_user_id, text, context_token=None, timeout_seconds=15):
        return {"message_id": "reply-1", "to_user_id": to_user_id, "used_context_token": bool(context_token)}

    monkeypatch.setattr(wechat_ilink, "chat_with_provider", fake_chat_with_provider)
    monkeypatch.setattr(wechat_ilink, "send_text_message", fake_send_text_message)

    result = wechat_ilink.handle_codex_bridge_message(
        "bot@example",
        {
            "from_user_id": "friend-1",
            "text": "",
            "images": [{"path": str(image_path), "mime_type": "image/png"}],
            "message_type": 1,
            "context_token": "ctx-1",
        },
        model="gpt-test",
        command="codex",
    )

    assert result["message_id"] == "reply-1"
    assert captured["provider"].supports_vision is True
    assert captured["messages"][0]["images"][0].startswith("data:image/png;base64,")


def test_wechat_codex_bridge_sends_reply_image_directive(monkeypatch, isolated_wechat_store, tmp_path):
    wechat_ilink.save_account(
        account_id="bot@example",
        token="plain-token",
        user_id="bot-user",
        base_url="https://ilink.example.com",
    )
    image_path = tmp_path / "screen.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-png")
    sent_texts = []
    sent_images = []

    def fake_chat_with_provider(**kwargs):
        return {"content": f"这是当前截图。\nCODECLAW_IMAGE: {image_path}"}

    def fake_send_text_message(account_id, *, to_user_id, text, context_token=None, timeout_seconds=15):
        sent_texts.append(text)
        return {"message_id": "text-1", "to_user_id": to_user_id, "used_context_token": bool(context_token)}

    def fake_send_image_message(
        account_id,
        *,
        to_user_id,
        image_bytes,
        filename="",
        mime_type="",
        text="",
        context_token=None,
        timeout_seconds=15,
    ):
        sent_images.append(
            {
                "to_user_id": to_user_id,
                "image_bytes": image_bytes,
                "filename": filename,
                "mime_type": mime_type,
            }
        )
        return {
            "message_id": "image-1",
            "to_user_id": to_user_id,
            "used_context_token": bool(context_token),
            "image": {"mime_type": mime_type, "size": len(image_bytes)},
        }

    monkeypatch.setattr(wechat_ilink, "chat_with_provider", fake_chat_with_provider)
    monkeypatch.setattr(wechat_ilink, "send_text_message", fake_send_text_message)
    monkeypatch.setattr(wechat_ilink, "send_image_message", fake_send_image_message)

    result = wechat_ilink.handle_codex_bridge_message(
        "bot@example",
        {
            "from_user_id": "friend-1",
            "text": "把当前桌面截图发给我",
            "message_type": 1,
            "context_token": "ctx-1",
        },
        model="gpt-test",
        command="codex",
    )

    assert result["message_id"] == "image-1"
    assert sent_texts == ["这是当前截图。"]
    assert sent_images == [
        {
            "to_user_id": "friend-1",
            "image_bytes": b"\x89PNG\r\n\x1a\nfake-png",
            "filename": "screen.png",
            "mime_type": "image/png",
        }
    ]
    assert result["images"][0]["image"]["mime_type"] == "image/png"


def test_wechat_ilink_send_image_uploads_cdn_and_sends_image(monkeypatch, isolated_wechat_store):
    wechat_ilink.save_account(
        account_id="bot@example",
        token="plain-token",
        user_id="bot-user",
        base_url="https://ilink.example.com",
    )
    posts = []

    def fake_post(url, headers=None, data=None, timeout=None):
        posts.append((url, headers, data))
        if url.endswith("/ilink/bot/getuploadurl"):
            return FakeResponse(
                {
                    "upload_full_url": "https://cdn.example.com/upload",
                }
            )
        if url == "https://cdn.example.com/upload":
            assert len(data) == 16
            return FakeResponse(None, headers={"x-encrypted-param": "download-param"})
        if url.endswith("/ilink/bot/sendmessage"):
            return FakeResponse({})
        raise AssertionError(url)

    monkeypatch.setattr(wechat_ilink.requests, "post", fake_post)

    result = wechat_ilink.send_image_message(
        "bot@example",
        to_user_id="friend-1",
        image_bytes=b"\x89PNG\r\n\x1a\nx",
        filename="x.png",
        context_token="ctx-1",
        timeout_seconds=1,
    )

    assert result["to_user_id"] == "friend-1"
    assert result["image"]["mime_type"] == "image/png"
    assert len(posts) == 3
    assert b'"type":2' in posts[2][2]
    assert b"download-param" in posts[2][2]
