"""公共聊天契约的工具调用往返，防止被纯文本适配吞掉。"""
from backend.core.ai import chat


def test_openai_tool_only_response_and_followup_preserve_protocol(monkeypatch):
    monkeypatch.setattr(chat, "read_opencode_go_key", lambda: "go-test-key")
    calls = [{"id": "call1", "type": "function", "function": {"name": "web_search", "arguments": '{"query":"fact"}'}}]
    payloads = []
    def post(url, **kwargs):
        payloads.append(kwargs["json"])
        message = {"role": "assistant", "content": None, "tool_calls": calls, "reasoning_content": "check"} if len(payloads) == 1 else {"role": "assistant", "content": "事实"}
        class Response:
            status_code = 200
            def json(self):
                return {"model": "test", "choices": [{"message": message, "finish_reason": "tool_calls" if len(payloads) == 1 else "stop"}]}
        return Response()
    monkeypatch.setattr(chat.requests, "post", post)
    params = dict(provider_id="deepseek", api_key="test-key", model="test", tools=[{"type": "function", "function": {"name": "web_search"}}])
    first = chat.chat_with_provider(messages=[{"role": "user", "content": "问题"}], **params)
    assert first["content"] == ""
    assert first["tool_calls"] == calls
    second = chat.chat_with_provider(messages=[first["message"], {"role": "tool", "tool_call_id": "call1", "content": "证据"}], tool_choice="none", **params)
    assert second["content"] == "事实"
    assert payloads[0]["tools"] == params["tools"]
    assert payloads[1]["tool_choice"] == "none"
    assert payloads[1]["messages"][0]["reasoning_content"] == "check"
    assert payloads[1]["messages"][1]["tool_call_id"] == "call1"
