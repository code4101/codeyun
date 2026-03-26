import json
from unittest.mock import patch

from backend.core.ai_chat import OllamaClientError
from backend.core.ai_chat_user_config import build_ai_chat_provider_config_key
from backend.models import AppSetting


def test_ai_chat_status_success(client):
    with patch(
        "backend.api.ai_chat.get_ai_provider_status",
        return_value={
            "id": "ollama",
            "label": "Ollama",
            "kind": "ollama",
            "is_custom": False,
            "available": True,
            "configured": True,
            "supports_stream": True,
            "supports_vision": True,
            "requires_api_key": False,
            "base_url": "http://127.0.0.1:11434",
            "default_model": "qwen3-vl:4b",
            "models": ["qwen3-vl:4b", "llama3.2:latest"],
            "error": None,
        },
    ):
        response = client.get("/api/ai-chat/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["provider"] == "ollama"
    assert payload["default_model"] == "qwen3-vl:4b"
    assert payload["models"] == ["qwen3-vl:4b", "llama3.2:latest"]
    assert payload["requires_api_key"] is False
    assert payload["is_custom"] is False


def test_ai_chat_status_unavailable(client):
    with patch("backend.api.ai_chat.get_ai_provider_status", side_effect=OllamaClientError("连接 Ollama 失败")):
        response = client.get("/api/ai-chat/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["error"] == "连接 Ollama 失败"


def test_ai_chat_providers(client):
    with patch(
        "backend.api.ai_chat.list_ai_provider_summaries",
        return_value=[
            {
                "id": "ollama",
                "label": "Ollama",
                "kind": "ollama",
                "is_custom": False,
                "configured": True,
                "requires_api_key": False,
                "base_url": "http://127.0.0.1:11434",
                "default_model": "qwen3-vl:4b",
                "models": ["qwen3-vl:4b"],
                "supports_stream": True,
                "supports_vision": True,
            },
            {
                "id": "deepseek",
                "label": "DeepSeek",
                "kind": "openai_compatible",
                "is_custom": False,
                "configured": True,
                "requires_api_key": True,
                "base_url": "https://api.deepseek.com/v1",
                "default_model": "deepseek-chat",
                "models": ["deepseek-chat", "deepseek-reasoner"],
                "supports_stream": True,
                "supports_vision": False,
            },
        ],
    ), patch("backend.api.ai_chat.get_default_ai_provider_id", return_value="ollama"):
        response = client.get("/api/ai-chat/providers")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_provider"] == "ollama"
    assert [item["id"] for item in payload["items"]] == ["ollama", "deepseek"]
    assert payload["items"][1]["requires_api_key"] is True


def test_ai_chat_can_create_custom_provider(client, auth_user):
    response = client.post(
        "/api/ai-chat/custom-providers",
        json={
            "label": "我的代理源",
            "base_url": "https://example.com/v1",
            "default_model": "gpt-4o-mini",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"].startswith("custom-")
    assert payload["label"] == "我的代理源"
    assert payload["base_url"] == "https://example.com/v1"
    assert payload["default_model"] == "gpt-4o-mini"
    assert payload["is_custom"] is True

    provider_response = client.get("/api/ai-chat/providers")
    provider_ids = [item["id"] for item in provider_response.json()["items"]]
    assert payload["id"] in provider_ids


def test_ai_chat_saved_configs_anonymous_returns_empty(client):
    response = client.get("/api/ai-chat/saved-configs")

    assert response.status_code == 200
    assert response.json() == {
        "signed_in": False,
        "items": [],
    }


def test_ai_chat_prompt_cards_anonymous_returns_empty(client):
    response = client.get("/api/ai-chat/prompt-cards")

    assert response.status_code == 200
    assert response.json() == {
        "signed_in": False,
        "selected_id": None,
        "items": [],
    }


def test_ai_chat_prompt_cards_can_save_to_user_asset(client, auth_user):
    response = client.put(
        "/api/ai-chat/prompt-cards",
        json={
            "selected_id": "prompt-1",
            "items": [
                {
                    "id": "prompt-1",
                    "title": "代码助手",
                    "content": "先给结论，再补关键步骤。",
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["signed_in"] is True
    assert payload["selected_id"] == "prompt-1"
    assert payload["items"] == [
        {
            "id": "prompt-1",
            "title": "代码助手",
            "content": "先给结论，再补关键步骤。",
            "updated_at": None,
        }
    ]

    listed = client.get("/api/ai-chat/prompt-cards").json()
    assert listed == payload


def test_ai_chat_builtin_provider_list_includes_new_openai_compatible_sources(client):
    response = client.get("/api/ai-chat/providers")

    assert response.status_code == 200
    provider_ids = {item["id"] for item in response.json()["items"]}
    assert {"ollama", "deepseek", "302ai", "aihubmix", "openrouter"} <= provider_ids


def test_ai_chat_can_save_provider_config_to_user_asset(client, session, auth_user):
    response = client.put(
        "/api/ai-chat/saved-configs/deepseek",
        json={
            "base_url": "https://api.deepseek.com/v1",
            "preferred_model": "deepseek-chat",
            "api_key": "sk-secret",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "deepseek"
    assert payload["base_url"] == "https://api.deepseek.com/v1"
    assert payload["preferred_model"] == "deepseek-chat"
    assert payload["preferred_models"] == ["deepseek-chat"]
    assert payload["has_api_key"] is True

    row = session.get(AppSetting, build_ai_chat_provider_config_key(auth_user.id))
    assert row is not None
    provider_row = row.value["providers"]["deepseek"]
    assert provider_row["preferred_models"] == ["deepseek-chat"]
    active_key_id = provider_row["active_key_id"]
    encrypted = provider_row["api_keys"][active_key_id]["api_key_encrypted"]
    assert encrypted
    assert encrypted != "sk-secret"

    list_response = client.get("/api/ai-chat/saved-configs")
    assert list_response.status_code == 200
    listed = list_response.json()
    assert listed["signed_in"] is True
    assert len(listed["items"]) == 1
    saved_item = listed["items"][0]
    assert saved_item["provider"] == "deepseek"
    assert saved_item["base_url"] == "https://api.deepseek.com/v1"
    assert saved_item["preferred_model"] == "deepseek-chat"
    assert saved_item["preferred_models"] == ["deepseek-chat"]
    assert saved_item["has_api_key"] is True
    assert saved_item["active_key_id"] == payload["active_key_id"]
    assert saved_item["key_count"] == 1
    assert len(saved_item["keys"]) == 1
    assert saved_item["keys"][0]["label"] == "Key 1"
    assert saved_item["keys"][0]["is_active"] is True
    assert saved_item["updated_at"] == payload["updated_at"]


def test_ai_chat_uses_saved_user_provider_config_for_runtime_requests(client, auth_user):
    client.put(
        "/api/ai-chat/saved-configs/deepseek",
        json={
            "base_url": "https://api.deepseek.com/v1",
            "preferred_model": "deepseek-reasoner",
            "api_key": "sk-saved",
        },
    )

    with patch(
        "backend.api.ai_chat.chat_with_provider",
        return_value={
            "model": "deepseek-chat",
            "content": "你好",
        },
    ) as mock_chat:
        response = client.post(
            "/api/ai-chat/chat",
            json={
                "provider": "deepseek",
                "messages": [
                    {
                        "role": "user",
                        "content": "你好",
                    }
                ],
            },
        )

    assert response.status_code == 200
    kwargs = mock_chat.call_args.kwargs
    assert kwargs["provider_id"] == "deepseek"
    assert kwargs["base_url"] == "https://api.deepseek.com/v1"
    assert kwargs["api_key"] == "sk-saved"
    assert kwargs["model"] == "deepseek-reasoner"


def test_ai_chat_can_switch_active_saved_provider_key(client, auth_user):
    first = client.put(
        "/api/ai-chat/saved-configs/deepseek",
        json={
            "base_url": "https://api.deepseek.com/v1",
            "preferred_model": "deepseek-chat",
            "api_key": "sk-first",
        },
    ).json()
    second = client.put(
        "/api/ai-chat/saved-configs/deepseek",
        json={
            "base_url": "https://api.deepseek.com/v1",
            "preferred_model": "deepseek-chat",
            "api_key": "sk-second",
        },
    ).json()

    assert first["key_count"] == 1
    assert second["key_count"] == 2
    assert second["active_key_id"] != first["active_key_id"]

    activate_response = client.post(
        f"/api/ai-chat/saved-configs/deepseek/keys/{first['active_key_id']}/activate",
    )

    assert activate_response.status_code == 200
    activated = activate_response.json()
    assert activated["active_key_id"] == first["active_key_id"]
    active_keys = [item for item in activated["keys"] if item["is_active"]]
    assert len(active_keys) == 1
    assert active_keys[0]["id"] == first["active_key_id"]

    with patch(
        "backend.api.ai_chat.chat_with_provider",
        return_value={
            "model": "deepseek-chat",
            "content": "你好",
        },
    ) as mock_chat:
        response = client.post(
            "/api/ai-chat/chat",
            json={
                "provider": "deepseek",
                "messages": [
                    {
                        "role": "user",
                        "content": "你好",
                    }
                ],
            },
        )

    assert response.status_code == 200
    kwargs = mock_chat.call_args.kwargs
    assert kwargs["api_key"] == "sk-first"


def test_ai_chat_can_save_provider_key_with_custom_label(client, auth_user):
    response = client.put(
        "/api/ai-chat/saved-configs/deepseek",
        json={
            "base_url": "https://api.deepseek.com/v1",
            "preferred_model": "deepseek-chat",
            "api_key": "sk-labeled",
            "api_key_label": "工作号",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["key_count"] == 1
    assert payload["keys"][0]["label"] == "工作号"
    assert payload["keys"][0]["is_active"] is True


def test_ai_chat_delete_saved_provider_key_keeps_remaining_active(client, auth_user):
    first = client.put(
        "/api/ai-chat/saved-configs/deepseek",
        json={
            "base_url": "https://api.deepseek.com/v1",
            "preferred_model": "deepseek-chat",
            "api_key": "sk-first",
        },
    ).json()
    second = client.put(
        "/api/ai-chat/saved-configs/deepseek",
        json={
            "base_url": "https://api.deepseek.com/v1",
            "preferred_model": "deepseek-chat",
            "api_key": "sk-second",
        },
    ).json()

    response = client.delete(
        f"/api/ai-chat/saved-configs/deepseek/keys/{second['active_key_id']}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["key_count"] == 1
    assert payload["active_key_id"] == first["active_key_id"]
    assert payload["keys"][0]["id"] == first["active_key_id"]
    assert payload["keys"][0]["is_active"] is True


def test_ai_chat_status_supports_runtime_connection_overrides(client):
    with patch(
        "backend.api.ai_chat.get_ai_provider_status",
        return_value={
            "id": "deepseek",
            "label": "DeepSeek",
            "kind": "openai_compatible",
            "is_custom": False,
            "available": True,
            "configured": True,
            "supports_stream": True,
            "supports_vision": False,
            "requires_api_key": True,
            "base_url": "https://api.deepseek.com/v1",
            "default_model": "deepseek-chat",
            "models": ["deepseek-chat"],
            "error": None,
        },
    ) as mock_status:
        response = client.post(
            "/api/ai-chat/status",
            json={
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-test",
            },
        )

    assert response.status_code == 200
    kwargs = mock_status.call_args.kwargs
    assert kwargs["provider_id"] == "deepseek"
    assert kwargs["base_url"] == "https://api.deepseek.com/v1"
    assert kwargs["api_key"] == "sk-test"


def test_ai_chat_status_prefers_saved_provider_model(client, auth_user):
    client.put(
        "/api/ai-chat/saved-configs/deepseek",
        json={
            "preferred_models": ["deepseek-reasoner", "deepseek-chat"],
        },
    )

    with patch(
        "backend.api.ai_chat.get_ai_provider_status",
        return_value={
            "id": "deepseek",
            "label": "DeepSeek",
            "kind": "openai_compatible",
            "is_custom": False,
            "available": True,
            "configured": True,
            "supports_stream": True,
            "supports_vision": False,
            "requires_api_key": True,
            "base_url": "https://api.deepseek.com/v1",
            "default_model": "deepseek-chat",
            "models": ["deepseek-chat"],
            "error": None,
        },
    ):
        response = client.post(
            "/api/ai-chat/status",
            json={
                "provider": "deepseek",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_model"] == "deepseek-reasoner"
    assert payload["models"][0] == "deepseek-reasoner"
    assert payload["models"][1] == "deepseek-chat"


def test_ai_chat_can_save_preferred_model_without_connection_fields(client, auth_user):
    response = client.put(
        "/api/ai-chat/saved-configs/openrouter",
        json={
            "preferred_models": ["anthropic/claude-sonnet-4"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openrouter"
    assert payload["base_url"] == ""
    assert payload["preferred_model"] == "anthropic/claude-sonnet-4"
    assert payload["preferred_models"] == ["anthropic/claude-sonnet-4"]
    assert payload["has_api_key"] is False

    listed = client.get("/api/ai-chat/saved-configs").json()["items"]
    assert listed == [payload]


def test_ai_chat_model_list_order_controls_runtime_default(client, auth_user):
    client.put(
        "/api/ai-chat/saved-configs/deepseek",
        json={
            "base_url": "https://api.deepseek.com/v1",
            "preferred_models": ["deepseek-reasoner", "deepseek-chat"],
            "api_key": "sk-saved",
        },
    )

    with patch(
        "backend.api.ai_chat.chat_with_provider",
        return_value={
            "model": "deepseek-reasoner",
            "content": "你好",
        },
    ) as mock_chat:
        response = client.post(
            "/api/ai-chat/chat",
            json={
                "provider": "deepseek",
                "messages": [
                    {
                        "role": "user",
                        "content": "你好",
                    }
                ],
            },
        )

    assert response.status_code == 200
    kwargs = mock_chat.call_args.kwargs
    assert kwargs["model"] == "deepseek-reasoner"


def test_ai_chat_saved_provider_config_can_be_deleted(client, auth_user):
    client.put(
        "/api/ai-chat/saved-configs/deepseek",
        json={
            "base_url": "https://api.deepseek.com/v1",
            "preferred_model": "deepseek-chat",
            "api_key": "sk-test",
        },
    )

    response = client.delete("/api/ai-chat/saved-configs/deepseek")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    listed = client.get("/api/ai-chat/saved-configs").json()
    assert listed["items"] == []


def test_ai_chat_custom_provider_delete_removes_provider_and_saved_config(client, auth_user):
    created = client.post(
        "/api/ai-chat/custom-providers",
        json={
            "label": "我的代理源",
            "base_url": "https://example.com/v1",
        },
    ).json()
    client.put(
        f"/api/ai-chat/saved-configs/{created['id']}",
        json={
            "base_url": "https://example.com/v1",
            "preferred_model": "gpt-4o-mini",
            "api_key": "sk-test",
        },
    )

    response = client.delete(f"/api/ai-chat/custom-providers/{created['id']}")

    assert response.status_code == 200
    provider_ids = [item["id"] for item in client.get("/api/ai-chat/providers").json()["items"]]
    assert created["id"] not in provider_ids
    saved_items = client.get("/api/ai-chat/saved-configs").json()["items"]
    assert saved_items == []


def test_ai_chat_post_success_with_images(client):
    with patch(
        "backend.api.ai_chat.chat_with_provider",
        return_value={
            "model": "qwen3-vl:4b",
            "content": "你好，这是一条测试回复。",
            "created_at": "2026-03-24T12:00:00Z",
            "done_reason": "stop",
            "prompt_eval_count": 10,
            "eval_count": 20,
            "total_duration": 123456789,
        },
    ) as mock_chat:
        response = client.post(
            "/api/ai-chat/chat",
            json={
                "provider": "ollama",
                "model": "qwen3-vl:4b",
                "system_prompt": "你是一个测试助手",
                "temperature": 0.4,
                "messages": [
                    {
                        "role": "user",
                        "content": "帮我看一下这张图",
                        "images": [
                            {
                                "name": "demo.png",
                                "mime_type": "image/png",
                                "data_base64": "ZmFrZS1pbWFnZS1kYXRh",
                            }
                        ],
                    }
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["model"] == "qwen3-vl:4b"
    assert payload["content"] == "你好，这是一条测试回复。"

    kwargs = mock_chat.call_args.kwargs
    assert kwargs["provider_id"] == "ollama"
    assert kwargs["base_url"] is None
    assert kwargs["api_key"] is None
    assert kwargs["model"] == "qwen3-vl:4b"
    assert kwargs["system_prompt"] == "你是一个测试助手"
    assert kwargs["temperature"] == 0.4
    assert kwargs["messages"] == [
        {
            "role": "user",
            "content": "帮我看一下这张图",
            "images": ["ZmFrZS1pbWFnZS1kYXRh"],
        }
    ]


def test_ai_chat_rejects_images_on_assistant_message(client):
    response = client.post(
        "/api/ai-chat/chat",
        json={
            "messages": [
                {
                    "role": "assistant",
                    "content": "",
                    "images": [
                        {
                            "data_base64": "ZmFrZQ==",
                        }
                    ],
                }
            ]
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "只有用户消息可以附带图片"


def test_ai_chat_surfaces_ollama_errors(client):
    with patch("backend.api.ai_chat.chat_with_provider", side_effect=OllamaClientError("请求 Ollama 失败")):
        response = client.post(
            "/api/ai-chat/chat",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "你好",
                    }
                ]
            },
        )

    assert response.status_code == 502
    assert response.json()["detail"] == "请求 Ollama 失败"


def test_ai_chat_stream_success(client):
    with patch(
        "backend.api.ai_chat.stream_chat_with_provider",
        return_value=iter(
            [
                {
                    "type": "delta",
                    "delta": "你好，",
                    "model": "qwen3-vl:4b",
                    "created_at": "2026-03-24T12:00:00Z",
                },
                {
                    "type": "delta",
                    "delta": "世界",
                    "model": "qwen3-vl:4b",
                },
                {
                    "type": "done",
                    "model": "qwen3-vl:4b",
                    "content": "你好，世界",
                    "created_at": "2026-03-24T12:00:00Z",
                    "done_reason": "stop",
                    "prompt_eval_count": 3,
                    "eval_count": 5,
                    "total_duration": 123,
                },
            ]
        ),
    ):
        response = client.post(
            "/api/ai-chat/chat-stream",
            json={
                "provider": "ollama",
                "messages": [
                    {
                        "role": "user",
                        "content": "你好",
                    }
                ]
            },
        )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.strip().splitlines()]
    assert events[0]["type"] == "delta"
    assert events[0]["delta"] == "你好，"
    assert events[1]["delta"] == "世界"
    assert events[2]["type"] == "done"
    assert events[2]["content"] == "你好，世界"


def test_ai_chat_stream_passes_runtime_provider_config(client):
    with patch(
        "backend.api.ai_chat.stream_chat_with_provider",
        return_value=iter(
            [
                {
                    "type": "done",
                    "model": "deepseek-chat",
                    "content": "你好",
                    "created_at": "2026-03-24T12:00:00Z",
                    "done_reason": "stop",
                }
            ]
        ),
    ) as mock_stream:
        response = client.post(
            "/api/ai-chat/chat-stream",
            json={
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-test",
                "messages": [
                    {
                        "role": "user",
                        "content": "你好",
                    }
                ],
            },
        )

    assert response.status_code == 200
    kwargs = mock_stream.call_args.kwargs
    assert kwargs["provider_id"] == "deepseek"
    assert kwargs["base_url"] == "https://api.deepseek.com/v1"
    assert kwargs["api_key"] == "sk-test"


def test_ai_chat_stream_surfaces_errors(client):
    with patch("backend.api.ai_chat.stream_chat_with_provider", side_effect=OllamaClientError("流式请求失败")):
        response = client.post(
            "/api/ai-chat/chat-stream",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "你好",
                    }
                ]
            },
        )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.strip().splitlines()]
    assert events == [
        {
            "type": "error",
            "detail": "流式请求失败",
        }
    ]
