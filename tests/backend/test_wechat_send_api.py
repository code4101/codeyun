from fastapi import HTTPException
import pytest

from backend.api.wechat_archive import WeChatSendTextRequest, send_wechat_text


def test_send_wechat_text_uses_process_api(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "pyxllib.autogui.weixin4_instrumentation.send_text",
        lambda recipient, text: calls.append((recipient, text)) or {"result": {"result": 1}},
    )

    result = send_wechat_text(WeChatSendTextRequest(recipient="考勤中台", text="日报"))

    assert calls == [("考勤中台", "日报")]
    assert result == {"result": {"result": 1}}


def test_send_wechat_text_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "pyxllib.autogui.weixin4_instrumentation.send_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unsupported version")),
    )

    with pytest.raises(HTTPException, match="禁止 GUI 回退") as exc_info:
        send_wechat_text(WeChatSendTextRequest(recipient="考勤中台", text="日报"))

    assert exc_info.value.status_code == 502
