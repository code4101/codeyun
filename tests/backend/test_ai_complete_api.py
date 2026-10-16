"""External lightweight inference uses distributable keys, never user credentials."""
from unittest.mock import patch

import pytest

from backend.core.ai.chat import OllamaClientError
from backend.core.ai.ollama_access_keys import create_ollama_access_key, delete_ollama_access_key


def test_complete_uses_fixed_local_model(client, session):
    key = create_ollama_access_key(session, label="external")
    with patch("backend.api.ai_chat.chat_with_provider", return_value={
        "model": "qwen3.5:4b-instruct", "content": "接口", "eval_count": 2,
    }) as inference:
        result = client.post("/api/ai-chat/complete", headers={
            "Authorization": f"Bearer {key['plaintext_value']}",
        }, json={"prompt": "解释 API"})
    assert result.status_code == 200
    assert result.json()["content"] == "接口"
    assert result.json()["eval_count"] == 2
    inference.assert_called_once_with(
        provider_id="ollama", model="qwen3.5:4b-instruct",
        messages=[{"role": "user", "content": "解释 API"}],
        system_prompt=None, temperature=0, response_format=None, timeout_seconds=60,
    )


@pytest.mark.parametrize("authorization", [None, "Bearer invalid", "Basic invalid"])
def test_complete_rejects_unauthorized_without_inference(client, session, authorization):
    create_ollama_access_key(session)
    with patch("backend.api.ai_chat.chat_with_provider") as inference:
        result = client.post("/api/ai-chat/complete", json={"prompt": "hello"},
                             headers={"Authorization": authorization} if authorization else {})
    assert result.status_code == 401
    assert result.headers["www-authenticate"] == "Bearer"
    inference.assert_not_called()


def test_complete_revoked_key(client, session):
    key = create_ollama_access_key(session)
    delete_ollama_access_key(session, key["id"])
    with patch("backend.api.ai_chat.chat_with_provider") as inference:
        result = client.post("/api/ai-chat/complete", json={"prompt": "hello"},
                             headers={"Authorization": f"Bearer {key['plaintext_value']}"})
    assert result.status_code == 401
    inference.assert_not_called()


def test_complete_json_and_provider_failure(client, session):
    key = create_ollama_access_key(session)
    headers = {"Authorization": f"Bearer {key['plaintext_value']}"}
    with patch("backend.api.ai_chat.chat_with_provider", return_value={
        "model": "qwen3.5:4b-instruct", "content": '{"kind":"question"}',
    }) as inference:
        result = client.post("/api/ai-chat/complete", headers=headers, json={
            "prompt": "你好", "system_prompt": "返回 kind 字段", "response_format": "json",
        })
    assert result.status_code == 200
    assert inference.call_args.kwargs["response_format"] == "json"
    assert inference.call_args.kwargs["system_prompt"] == "返回 kind 字段"
    with patch("backend.api.ai_chat.chat_with_provider", side_effect=OllamaClientError("private URL")):
        result = client.post("/api/ai-chat/complete", headers=headers, json={"prompt": "你好"})
    assert result.status_code == 502
    assert "private URL" not in result.text


@pytest.mark.parametrize("payload", [
    {"prompt": " "}, {"prompt": "x" * 16001}, {"prompt": "x", "temperature": 3},
    {"prompt": "x", "model": "other"}, {"prompt": "x", "base_url": "http://other"},
    {"prompt": "x", "response_format": "xml"},
])
def test_complete_validates_input(client, payload):
    with patch("backend.api.ai_chat.chat_with_provider") as inference:
        result = client.post("/api/ai-chat/complete", json=payload)
    assert result.status_code == 422
    inference.assert_not_called()
