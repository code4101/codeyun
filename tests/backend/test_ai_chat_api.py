import json
from unittest.mock import patch

from backend.app import app
from backend.core.ai import chat as ai_chat
from backend.core.ai.chat import OllamaClientError, chat_with_provider, get_ai_provider_status, stream_chat_with_provider
from backend.core import settings as settings_module
from backend.core.ai.chat_user_config import build_ai_chat_provider_config_key, save_user_ai_chat_provider_config
from backend.core.access.auth import get_current_active_superuser
from backend.core.ai.ollama_access_keys import create_ollama_access_key
from backend.models import AppSetting, User


def _create_test_ollama_access_key(session) -> str:
    created = create_ollama_access_key(session, created_by_user_id=1, label="测试访问 Key")
    return created["plaintext_value"]


def _override_superuser():
    admin_user = User(
        id=1,
        username="admin",
        hashed_password="pw",
        is_active=True,
        is_superuser=True,
    )
    app.dependency_overrides[get_current_active_superuser] = lambda: admin_user
    return admin_user


def _clear_settings_cache():
    settings_module.get_settings.cache_clear()


def test_ai_chat_status_success(client, session):
    access_key = _create_test_ollama_access_key(session)
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
        response = client.post(
            "/api/ai-chat/status",
            json={
                "provider": "ollama",
                "api_key": access_key,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["provider"] == "ollama"
    assert payload["default_model"] == "qwen3-vl:4b"
    assert payload["models"] == ["qwen3-vl:4b", "llama3.2:latest"]
    assert payload["requires_api_key"] is True
    assert payload["is_custom"] is False


def test_ai_chat_status_unavailable(client, session):
    access_key = _create_test_ollama_access_key(session)
    with patch("backend.api.ai_chat.get_ai_provider_status", side_effect=OllamaClientError("连接 Ollama 失败")):
        response = client.post(
            "/api/ai-chat/status",
            json={
                "provider": "ollama",
                "api_key": access_key,
            },
        )

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
    assert payload["items"][0]["requires_api_key"] is True
    assert payload["items"][1]["requires_api_key"] is True


def test_ai_chat_providers_anonymous_hide_server_managed_cloud_config(client, monkeypatch):
    monkeypatch.setenv("CODEYUN_LOAD_DOTENV", "0")
    monkeypatch.setenv("CODEYUN_AI_DEFAULT_PROVIDER", "deepseek")
    monkeypatch.setenv("CODEYUN_DEEPSEEK_API_KEY", "server-deepseek-key")
    _clear_settings_cache()
    try:
        response = client.get("/api/ai-chat/providers")
    finally:
        _clear_settings_cache()

    assert response.status_code == 200
    payload = response.json()
    provider_map = {item["id"]: item for item in payload["items"]}
    assert payload["default_provider"] == "ollama"
    assert provider_map["ollama"]["requires_api_key"] is True
    assert provider_map["deepseek"]["configured"] is False


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


def test_ai_chat_status_anonymous_does_not_inherit_server_managed_cloud_api_key(client, monkeypatch):
    monkeypatch.setenv("CODEYUN_LOAD_DOTENV", "0")
    monkeypatch.setenv("CODEYUN_DEEPSEEK_API_KEY", "server-deepseek-key")
    _clear_settings_cache()
    try:
        response = client.post(
            "/api/ai-chat/status",
            json={
                "provider": "deepseek",
            },
        )
    finally:
        _clear_settings_cache()

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "deepseek"
    assert payload["available"] is False
    assert payload["configured"] is False
    assert "未填写 API Key" in payload["error"]


def test_ai_chat_anonymous_chat_does_not_inherit_server_managed_cloud_api_key(client, monkeypatch):
    monkeypatch.setenv("CODEYUN_LOAD_DOTENV", "0")
    monkeypatch.setenv("CODEYUN_DEEPSEEK_API_KEY", "server-deepseek-key")
    _clear_settings_cache()
    try:
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
    finally:
        _clear_settings_cache()

    assert response.status_code == 502
    assert "未填写 API Key" in response.json()["detail"]


def test_ai_chat_admin_can_manage_ollama_access_keys(client):
    _override_superuser()
    try:
        create_response = client.post(
            "/api/ai-chat/ollama-access-keys",
            json={"label": "分发给测试同学"},
        )
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["label"] == "分发给测试同学"
    assert created["plaintext_value"].startswith("oky-")
    assert created["masked_value"]

    _override_superuser()
    try:
        list_response = client.get("/api/ai-chat/ollama-access-keys")
        reveal_response = client.get(f"/api/ai-chat/ollama-access-keys/{created['id']}")
        delete_response = client.delete(f"/api/ai-chat/ollama-access-keys/{created['id']}")
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["id"] == created["id"]
    assert reveal_response.status_code == 200
    assert reveal_response.json()["plaintext_value"] == created["plaintext_value"]
    assert delete_response.status_code == 200


def test_ai_chat_ollama_requires_valid_access_key(client, auth_user):
    response = client.post(
        "/api/ai-chat/chat",
        json={
            "provider": "ollama",
            "messages": [
                {
                    "role": "user",
                    "content": "你好",
                }
            ],
        },
    )

    assert response.status_code == 502
    assert "访问 Key" in response.json()["detail"]


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


def test_ai_chat_sessions_anonymous_returns_empty(client):
    response = client.get("/api/ai-chat/sessions")

    assert response.status_code == 200
    assert response.json() == {
        "signed_in": False,
        "active_session_id": None,
        "items": [],
    }


def test_ai_chat_sessions_can_save_restore_and_reorder_by_update_time(client, auth_user):
    response = client.put(
        "/api/ai-chat/sessions",
        json={
            "active_session_id": "session-new",
            "items": [
                {
                    "id": "session-old",
                    "title": "旧会话",
                    "preview": "更早的内容",
                    "provider_id": "",
                    "model": "",
                    "selected_model_option_ids": [],
                    "selected_assistant_message_id": None,
                    "draft": "",
                    "updated_at": 10,
                    "messages": [
                        {
                            "id": "user-old",
                            "role": "user",
                            "content": "旧内容",
                            "images": [],
                            "target_model_option_ids": [],
                            "provider_id": "",
                            "model_option_id": "",
                            "model": "",
                            "display_model": "",
                            "created_at": "2026-04-03T09:00:00Z",
                            "total_duration": None,
                            "error": False,
                        }
                    ],
                },
                {
                    "id": "session-new",
                    "title": "新会话",
                    "preview": "继续追问",
                    "provider_id": "ollama",
                    "model": "qwen3.5:4b-instruct",
                    "selected_model_option_ids": ["ollama::qwen3.5:4b-instruct"],
                    "selected_assistant_message_id": "assistant-1",
                    "draft": "继续追问",
                    "updated_at": 20,
                    "messages": [
                        {
                            "id": "user-1",
                            "role": "user",
                            "content": "你好",
                            "images": [],
                            "target_model_option_ids": ["ollama::qwen3.5:4b-instruct"],
                            "provider_id": "",
                            "model_option_id": "",
                            "model": "",
                            "display_model": "",
                            "created_at": "2026-04-03T09:00:00Z",
                            "total_duration": None,
                            "error": False,
                        },
                        {
                            "id": "assistant-1",
                            "role": "assistant",
                            "content": "你好，我在。",
                            "images": [],
                            "target_model_option_ids": [],
                            "provider_id": "ollama",
                            "model_option_id": "ollama::qwen3.5:4b-instruct",
                            "model": "qwen3.5:4b-instruct",
                            "display_model": "Ollama / qwen3.5:4b-instruct",
                            "created_at": "2026-04-03T09:00:02Z",
                            "total_duration": 1200000,
                            "error": False,
                        },
                    ],
                },
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["signed_in"] is True
    assert payload["active_session_id"] == "session-new"
    assert [item["id"] for item in payload["items"]] == ["session-new", "session-old"]
    assert payload["items"][0]["model"] == "qwen3.5:4b-instruct"
    assert payload["items"][0]["draft"] == "继续追问"
    assert payload["items"][0]["updated_at"] is not None
    assert payload["items"][1]["updated_at"] == 10

    listed = client.get("/api/ai-chat/sessions").json()
    assert listed == payload

    cleared = client.put(
        "/api/ai-chat/sessions",
        json={
            "active_session_id": None,
            "items": [],
        },
    )

    assert cleared.status_code == 200
    assert cleared.json() == {
        "signed_in": True,
        "active_session_id": None,
        "items": [],
    }


def test_ai_chat_builtin_provider_list_includes_new_openai_compatible_sources(client):
    response = client.get("/api/ai-chat/providers")

    assert response.status_code == 200
    provider_ids = {item["id"] for item in response.json()["items"]}
    assert {"ollama", "deepseek", "302ai", "aihubmix", "openrouter"} <= provider_ids


def test_ai_chat_codex_cmd_config_resolves_without_cmd_wrapper(monkeypatch, tmp_path):
    repo_tools = tmp_path / "tools" / "node"
    repo_tools.mkdir(parents=True)
    native = (
        repo_tools
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
    native.parent.mkdir(parents=True)
    native.write_text("", encoding="utf-8")

    configured_cmd = tmp_path / "AppData" / "Local" / "OpenAI" / "Codex" / "bin" / "codex.cmd"
    configured_cmd.parent.mkdir(parents=True)
    configured_cmd.write_text("@echo off\n", encoding="utf-8")

    monkeypatch.setattr(ai_chat.os, "name", "nt")
    monkeypatch.setattr(ai_chat, "_codex_tools_node_dir", lambda: repo_tools)

    resolved = ai_chat._resolve_command_path([str(configured_cmd), "exec", "--json"])

    assert resolved == [str(native), "exec", "--json"]


def test_ai_chat_status_adds_qwen35_instruct_alias_for_ollama_models(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "models": [
                    {"model": "qwen3.5:4b"},
                    {"model": "qwen3-vl:8b-instruct"},
                ]
            }

    monkeypatch.setattr("backend.core.ai.chat.requests.get", lambda *args, **kwargs: FakeResponse())

    status = get_ai_provider_status(
        "ollama",
        base_url="http://127.0.0.1:11434",
    )

    assert status["available"] is True
    assert "qwen3.5:4b-instruct" in status["models"]
    assert status["models"].index("qwen3.5:4b-instruct") < status["models"].index("qwen3.5:4b")


def test_ai_chat_ollama_alias_runs_with_think_false(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "model": "qwen3.5:4b",
                "message": {
                    "role": "assistant",
                    "content": "OK",
                },
            }

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("backend.core.ai.chat.requests.post", fake_post)

    response = chat_with_provider(
        provider_id="ollama",
        base_url="http://127.0.0.1:11434",
        messages=[{"role": "user", "content": "只回复OK"}],
        model="qwen3.5:4b-instruct",
    )

    assert captured["json"]["model"] == "qwen3.5:4b"
    assert captured["json"]["think"] is False
    assert response["model"] == "qwen3.5:4b-instruct"
    assert response["content"] == "OK"


def test_ai_chat_ollama_structured_sync_requests_use_streaming(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_lines(self, decode_unicode=False):
            assert decode_unicode is True
            return iter(
                [
                    json.dumps(
                        {
                            "model": "qwen3.5:4b",
                            "message": {
                                "role": "assistant",
                                "content": '{"topic":"Git 分层"}',
                            },
                            "done": True,
                        },
                        ensure_ascii=False,
                    )
                ]
            )

    def fake_post(url, json=None, timeout=None, stream=False):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("backend.core.ai.chat.requests.post", fake_post)

    response = chat_with_provider(
        provider_id="ollama",
        base_url="http://127.0.0.1:11434",
        messages=[{"role": "user", "content": "只输出 JSON"}],
        model="qwen3.5:4b-instruct",
        response_format={"type": "object"},
        timeout_seconds=321,
    )

    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["stream"] is True
    assert captured["timeout"] == 321
    assert captured["json"]["model"] == "qwen3.5:4b"
    assert captured["json"]["format"] == {"type": "object"}
    assert response["model"] == "qwen3.5:4b-instruct"
    assert response["content"] == '{"topic":"Git 分层"}'


def test_ai_chat_openrouter_stream_ignores_sse_comments_and_event_lines(monkeypatch):
    class FakeResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_lines(self, decode_unicode=False):
            assert decode_unicode is False
            return iter(
                [
                    ": OPENROUTER PROCESSING",
                    "",
                    "event: message",
                    "data: {\"model\":\"anthropic/claude-opus-4.6\",\"created\":1712620800,\"choices\":[{\"delta\":{\"content\":\"你\"}}]}",
                    "",
                    "data: {\"choices\":[{\"delta\":{\"content\":\"好\"}}]}",
                    "",
                    "event: message",
                    "data: {\"choices\":[{\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":3,\"completion_tokens\":2}}",
                    "",
                    "data: [DONE]",
                    "",
                ]
            )

    monkeypatch.setattr("backend.core.ai.chat.requests.post", lambda *args, **kwargs: FakeResponse())

    events = list(
        stream_chat_with_provider(
            provider_id="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-test",
            model="anthropic/claude-opus-4.6",
            messages=[{"role": "user", "content": "你好"}],
        )
    )

    assert events[0]["type"] == "delta"
    assert events[0]["delta"] == "你"
    assert events[1]["type"] == "delta"
    assert events[1]["delta"] == "好"
    assert events[2] == {
        "type": "done",
        "model": "anthropic/claude-opus-4.6",
        "content": "你好",
        "created_at": "2024-04-09T00:00:00+00:00",
        "done_reason": "stop",
        "prompt_eval_count": 3,
        "eval_count": 2,
        "total_duration": None,
    }


def test_ai_chat_openrouter_stream_decodes_utf8_bytes_without_mojibake(monkeypatch):
    class FakeResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_lines(self, decode_unicode=False):
            assert decode_unicode is False
            return iter(
                [
                    b": OPENROUTER PROCESSING",
                    b"",
                    'data: {"model":"anthropic/claude-opus-4.6","created":1712620800,"choices":[{"delta":{"content":"仅"}}]}'.encode("utf-8"),
                    b"",
                    'data: {"choices":[{"delta":{"content":"好"}}]}'.encode("utf-8"),
                    b"",
                    b'data: {"choices":[{"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2}}',
                    b"",
                    b"data: [DONE]",
                    b"",
                ]
            )

    monkeypatch.setattr("backend.core.ai.chat.requests.post", lambda *args, **kwargs: FakeResponse())

    events = list(
        stream_chat_with_provider(
            provider_id="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-test",
            model="anthropic/claude-opus-4.6",
            messages=[{"role": "user", "content": "只回复‘仅好’"}],
        )
    )

    assert events[0]["type"] == "delta"
    assert events[0]["delta"] == "仅"
    assert events[1]["type"] == "delta"
    assert events[1]["delta"] == "好"
    assert events[2]["content"] == "仅好"


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


def test_ai_chat_uses_saved_ollama_access_key_for_runtime_requests(client, session, auth_user):
    access_key = _create_test_ollama_access_key(session)
    save_user_ai_chat_provider_config(
        session,
        auth_user.id,
        "ollama",
        api_key=access_key,
    )

    with patch(
        "backend.api.ai_chat.chat_with_provider",
        return_value={
            "model": "qwen3-vl:4b",
            "content": "你好",
        },
    ) as mock_chat:
        response = client.post(
            "/api/ai-chat/chat",
            json={
                "provider": "ollama",
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
    assert kwargs["provider_id"] == "ollama"
    assert kwargs["api_key"] == access_key


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


def test_ai_chat_post_success_with_images(client, session):
    access_key = _create_test_ollama_access_key(session)
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
                "api_key": access_key,
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
    assert kwargs["api_key"] == access_key
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


def test_ai_chat_surfaces_ollama_errors(client, session):
    access_key = _create_test_ollama_access_key(session)
    with patch("backend.api.ai_chat.chat_with_provider", side_effect=OllamaClientError("请求 Ollama 失败")):
        response = client.post(
            "/api/ai-chat/chat",
            json={
                "provider": "ollama",
                "api_key": access_key,
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


def test_ai_chat_stream_success(client, session):
    access_key = _create_test_ollama_access_key(session)
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
                "api_key": access_key,
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


def test_ai_chat_stream_surfaces_errors(client, session):
    access_key = _create_test_ollama_access_key(session)
    with patch("backend.api.ai_chat.stream_chat_with_provider", side_effect=OllamaClientError("流式请求失败")):
        response = client.post(
            "/api/ai-chat/chat-stream",
            json={
                "provider": "ollama",
                "api_key": access_key,
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
