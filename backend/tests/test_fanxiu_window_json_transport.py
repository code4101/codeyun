import json

import pytest
import requests
from fastapi import HTTPException

from backend.core.fanxiu.game import window_remote
from backend.models import UserDevice


@pytest.mark.parametrize(
    ("status", "body", "error", "detail"),
    [
        (200, {"ok": True}, None, None),
        (409, {"detail": "设备忙碌"}, 409, "设备忙碌"),
        (200, [1], 502, "响应格式不支持"),
        (200, "invalid-json", 502, "响应不是 JSON"),
    ],
)
def test_remote_json_contract_and_connection_release(monkeypatch, status, body, error, detail):
    response = requests.Response()
    response.status_code = status
    response.encoding = "utf-8"
    response._content = (body if isinstance(body, str) else json.dumps(body)).encode("utf-8")
    calls = []
    closed = []
    monkeypatch.setattr(response, "close", lambda: closed.append(True))

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return response

    monkeypatch.setattr(window_remote.requests, "request", request)
    entry = UserDevice(mode="remote", server_url="http://example.invalid/", token="test-token")
    if error:
        with pytest.raises(HTTPException) as caught:
            window_remote.request_remote_game_window2_json(
                entry, "/service-test", {"value": 1}, "测试", method="put", read_timeout=20,
            )
        assert caught.value.status_code == error
        assert detail in caught.value.detail
    else:
        assert window_remote.request_remote_game_window2_json(
            entry, "/service-test", {"value": 1}, "测试", method="put", read_timeout=20,
        ) == {"ok": True}
    assert closed == [True]
    method, url, kwargs = calls[0]
    assert method == "put" and url.endswith("/game-window2/service-test")
    assert kwargs["timeout"] == (5.0, 20)
    assert kwargs["json"] == {"value": 1}
    assert kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert kwargs["proxies"] == window_remote.REMOTE_DEVICE_DIRECT_PROXIES


def test_transport_failure_keeps_action_context(monkeypatch):
    def unavailable(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(window_remote.requests, "request", unavailable)
    entry = UserDevice(mode="remote", server_url="http://example.invalid", token="test-token")
    with pytest.raises(HTTPException) as caught:
        window_remote.request_remote_game_window2_json(entry, "service-test", {}, "匹配")
    assert caught.value.status_code == 502
    assert "匹配服务不可达" in caught.value.detail


def test_missing_remote_activation_keeps_upgrade_guidance(monkeypatch):
    from backend.api.fanxiu import _activate_remote_game_window2

    response = requests.Response()
    response.status_code = 404
    response._content = b'{"detail":"Not Found"}'
    response._content_consumed = True
    monkeypatch.setattr(window_remote.requests, "request", lambda *args, **kwargs: response)
    entry = UserDevice(mode="remote", server_url="http://example.invalid", token="test-token")
    with pytest.raises(HTTPException) as caught:
        _activate_remote_game_window2(entry, {})
    assert caught.value.status_code == 502
    assert "缺少激活窗口接口" in caught.value.detail
    assert "更新并重启" in caught.value.detail
