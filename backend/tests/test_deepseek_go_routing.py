"""DS aliases, backing credential separation and quota-controlled fallback."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import requests
from sqlmodel import Session, create_engine

from backend.core.ai import app_config, chat
from backend.models import AppSetting


@pytest.fixture(autouse=True)
def go_credentials(monkeypatch):
    # Never read real credentials, even when an assertion fails.
    monkeypatch.setattr(chat, "read_opencode_go_key", lambda: "go-test-key")
    monkeypatch.setattr(app_config, "read_opencode_go_key", lambda: "go-test-key")
    monkeypatch.setattr(chat, "select_ds_route", lambda: (
        chat.load_agent_config()["resources"]["ds"]["primary"], {"reason": "primary"}))
    monkeypatch.setattr(chat, "read_official_ds_key", lambda: "official-test-key")


def reply(payload):
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(payload).encode()
    response._content_consumed = True
    return response


@pytest.mark.parametrize("provider", ["ds", "deepseek", "opencode-go"])
def test_legacy_and_go_requests_use_only_go_credentials_and_flash(monkeypatch, provider):
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs)
        assert url == "https://opencode.ai/zen/go/v1/chat/completions"
        assert kwargs["headers"]["Authorization"] == "Bearer go-test-key"
        assert kwargs["headers"]["x-opencode-session"]
        assert kwargs["headers"]["User-Agent"] == "codeyun-ai/1.0"
        assert kwargs["json"]["model"] == "deepseek-v4.1-flash"
        return reply({"model": "deepseek-v4.1-flash", "choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call-test", "type": "function", "function": {
                "name": "echo", "arguments": "{}"}}],
        }}]})

    monkeypatch.setattr(requests, "post", post)
    tools = [{"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}}]
    result = chat.chat_with_provider(provider_id=provider, base_url="https://api.deepseek.com/v1",
        api_key="official-test-key", model="deepseek-v4-pro", messages=[], tools=tools, tool_choice="auto")
    assert calls[0]["json"]["tools"] == tools
    assert result["tool_calls"][0]["function"]["name"] == "echo"


@pytest.mark.parametrize("route", ["primary", "fallback"])
@pytest.mark.parametrize("choice", ["auto", "required"])
@pytest.mark.parametrize("thinking", [None, False, True])
def test_ds_forced_tools_disable_thinking_on_both_routes(monkeypatch, route, choice, thinking):
    monkeypatch.setattr(chat, "select_ds_route", lambda: (
        chat.load_agent_config()["resources"]["ds"][route], {"reason": "test"}))

    def post(url, **kwargs):
        payload = kwargs["json"]
        assert payload["tool_choice"] == choice
        if choice == "required":
            assert payload["thinking"] == {"type": "disabled"}
        elif thinking is not None:
            assert payload["thinking"] == {"type": "enabled" if thinking else "disabled"}
        else:
            assert "thinking" not in payload
        return reply({"choices": [{"message": {"content": None, "tool_calls": [{
            "type": "function", "function": {"name": "echo", "arguments": "{}"},
        }]}}]})

    monkeypatch.setattr(requests, "post", post)
    result = chat.chat_with_provider(
        provider_id="ds", messages=[], tool_choice=choice, thinking=thinking,
        tools=[{"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}}],
    )
    assert result["tool_calls"][0]["function"]["name"] == "echo"


def test_stream_routes_go_and_preserves_events(monkeypatch):
    def post(url, **kwargs):
        assert url == "https://opencode.ai/zen/go/v1/chat/completions"
        assert kwargs["headers"]["Authorization"] == "Bearer go-test-key"
        assert kwargs["headers"]["x-opencode-session"]
        assert kwargs["json"]["model"] == "deepseek-v4.1-flash"
        response = reply({})
        response.iter_lines = lambda **_: iter([
            'data: {"model":"deepseek-v4.1-flash","choices":[{"delta":{"content":"OK"}}]}',
            "", "data: [DONE]", "",
        ])
        return response

    monkeypatch.setattr(requests, "post", post)
    result = list(chat.stream_chat_with_provider(provider_id="deepseek", api_key="official-test-key",
        base_url="https://api.deepseek.com/v1", model="deepseek-v4-pro", messages=[]))
    assert any(event.get("content") == "OK" for event in result)


@pytest.mark.parametrize("stream", [False, True])
def test_missing_go_key_cannot_fall_back_to_official(monkeypatch, stream):
    monkeypatch.setattr(chat, "read_opencode_go_key", lambda: "")
    monkeypatch.setattr(requests, "post", lambda *a, **kw: pytest.fail("Network must not be reached"))
    with pytest.raises(chat.OllamaClientError):
        if stream:
            list(chat.stream_chat_with_provider(provider_id="deepseek", api_key="official-test-key", messages=[]))
        else:
            chat.chat_with_provider(provider_id="deepseek", api_key="official-test-key", messages=[])


@pytest.mark.parametrize("url", ["https://api.deepseek.com/v1", "https://API.DEEPSEEK.COM:443/v1", "https://api.deepseek.com./v1"])
@pytest.mark.parametrize("stream", [False, True])
def test_custom_official_endpoint_is_blocked_before_network(monkeypatch, url, stream):
    provider = replace(chat.get_ai_provider("302ai", api_key="official-test-key"),
        id="custom-official", base_url=url, configured=True)
    monkeypatch.setattr(requests, "post", lambda *a, **kw: pytest.fail("Official endpoint must not be called"))
    with pytest.raises(chat.OllamaClientError, match="额度降级入口"):
        if stream:
            list(chat.stream_chat_with_provider(provider_id=provider.id, extra_providers=(provider,), messages=[]))
        else:
            chat.chat_with_provider(provider_id=provider.id, extra_providers=(provider,), messages=[])


def test_other_provider_settings_remain_unchanged():
    other = chat.get_ai_provider("302ai", base_url="https://other.example/v1", api_key="other-test-key")
    assert (other.id, other.base_url, other.api_key) == ("302ai", "https://other.example/v1", "other-test-key")
    summaries = chat.list_ai_provider_summaries()
    assert any(p["id"] == "ds" and p["default_model"] == "deepseek-v4.1-flash" for p in summaries)
    assert not any(p["id"] == "deepseek" for p in summaries)


def test_legacy_app_config_preserves_disabled_state_and_ignores_official_secret(monkeypatch):
    engine = create_engine("sqlite://")
    AppSetting.__table__.create(engine)
    monkeypatch.setattr(app_config, "list_user_ai_chat_custom_provider_configs", lambda *a: [])
    with Session(engine) as session:
        saved = app_config.save_user_ai_app_config(session, 42, "codex-diary", enabled=False,
            provider="deepseek", model="deepseek-v4-pro")
        assert saved["enabled"] is False
        assert (saved["provider"], saved["model"]) == ("ds", "deepseek-v4.1-flash")
        runtime = app_config.resolve_ai_app_runtime_config(session=session,
            current_user=SimpleNamespace(id=42), app_id="codex-diary", provider="deepseek",
            base_url="https://api.deepseek.com/v1", api_key="official-test-key", model="deepseek-v4-pro")
        assert runtime["provider"] == "ds"
        assert runtime["api_key"] == "go-test-key"
        assert runtime["base_url"] == "https://opencode.ai/zen/go/v1"
        assert runtime["model"] == "deepseek-v4.1-flash"
        assert app_config.get_user_ai_app_config(session, 42, "codex-diary")["enabled"] is False
    engine.dispose()


@pytest.mark.parametrize("stream", [False, True])
def test_exhausted_ds_uses_only_official_key_and_flash(monkeypatch, stream):
    monkeypatch.setattr(chat, "select_ds_route", lambda: (
        chat.load_agent_config()["resources"]["ds"]["fallback"], {"reason": "quota_exhausted"}))
    def post(url, **kwargs):
        assert url == "https://api.deepseek.com/v1/chat/completions"
        assert kwargs["headers"]["Authorization"] == "Bearer official-test-key"
        assert "x-opencode-session" not in kwargs["headers"]
        assert kwargs["json"]["model"] == "deepseek-flash"
        response = reply({"model": "deepseek-flash", "choices": [{"message": {"content": "OK"}}]})
        response.iter_lines = lambda **_: iter([
            'data: {"model":"deepseek-flash","choices":[{"delta":{"content":"OK"}}]}',
            "", "data: [DONE]", "",
        ])
        return response
    monkeypatch.setattr(requests, "post", post)
    if stream:
        assert any(e.get("content") == "OK" for e in chat.stream_chat_with_provider(provider_id="ds", messages=[]))
    else:
        result = chat.chat_with_provider(provider_id="ds", messages=[])
        assert result["provider"] == "deepseek-official"
        assert result["resource"] == "ds"
