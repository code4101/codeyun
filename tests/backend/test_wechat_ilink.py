from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import wechat_ilink


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

    def fake_chat_with_provider(*, provider_id=None, messages, model=None, system_prompt=None, extra_providers=(), **kwargs):
        assert provider_id == "wechat-codex-cli"
        assert extra_providers[0].kind == "codex_cli"
        assert model == "gpt-test"
        assert "微信发送方：friend-1" in messages[0]["content"]
        assert "高风险操作" in system_prompt
        return {"content": "Codex 已处理"}

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
            "message_type": 1,
            "context_token": "ctx-1",
        },
        model="gpt-test",
        command="codex",
    )

    assert result["message_id"] == "reply-1"
    assert sent_messages == [
        {
            "account_id": "bot@example",
            "to_user_id": "friend-1",
            "text": "Codex 已处理",
            "context_token": "ctx-1",
        }
    ]


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

    assert summary["codex_bridge"]["model"] == "gpt-5.5"


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
