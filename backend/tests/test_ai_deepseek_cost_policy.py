"""Verify the actual outbound model when callers retain an old Pro setting."""
import json

import pytest

from backend.core.ai.chat import chat_with_provider, stream_chat_with_provider


@pytest.mark.parametrize("stream", [False, True])
def test_legacy_pro_request_is_sent_as_flash(monkeypatch, stream):
    sent = []

    class Response:
        status_code = 200
        encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def json(self):
            return {"model": "deepseek-v4-flash", "choices": [{"message": {"content": "ok"}}]}

        def iter_lines(self, **kwargs):
            yield 'data: ' + json.dumps({"model": "deepseek-v4-flash", "choices": [{"delta": {"content": "ok"}}]})
            yield ''
            yield 'data: [DONE]'
            yield ''

    def post(url, **kwargs):
        sent.append(kwargs["json"])
        return Response()

    monkeypatch.setattr("backend.core.ai.chat.requests.post", post)
    args = dict(provider_id="deepseek", api_key="test-only", model="deepseek-v4-pro",
                messages=[{"role": "user", "content": "hello"}])
    if stream:
        list(stream_chat_with_provider(**args))
    else:
        chat_with_provider(**args)
    assert len(sent) == 1
    assert sent[0]["model"] == "deepseek-v4-flash"
