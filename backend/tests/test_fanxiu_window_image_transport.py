import pytest
import requests
from fastapi import HTTPException

from backend.api.fanxiu import (
    _remote_game_window2_match_image,
    _remote_game_window2_screenshot_image,
)
from backend.core.fanxiu.game import window_remote
from backend.models import UserDevice


@pytest.mark.parametrize(
    ("kind", "media_type", "cache", "timeout", "path"),
    [
        ("screencap", "image/png", "no-store", 20, "service-screencap"),
        ("screenshot", "image/jpeg", "private, no-cache", 30, "service-screenshot/image"),
        ("match", "image/jpeg", "no-store", 30, "service-match/image"),
    ],
)
def test_image_adapters_preserve_response_contract(monkeypatch, kind, media_type, cache, timeout, path):
    upstream = requests.Response()
    upstream.status_code = 200
    upstream._content = b"image-content"
    closed = []
    calls = []
    monkeypatch.setattr(upstream, "close", lambda: closed.append(True))

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return upstream

    monkeypatch.setattr(window_remote.requests, "get", get)
    entry = UserDevice(mode="remote", server_url="http://example.invalid", token="test-token")
    if kind == "screencap":
        result = window_remote.remote_game_window2_screencap(entry)
    elif kind == "screenshot":
        result = _remote_game_window2_screenshot_image(entry, "frame.png")
    else:
        result = _remote_game_window2_match_image(entry, "frame.png")
    assert result.body == b"image-content"
    assert result.media_type == media_type
    assert result.headers["Cache-Control"] == cache
    assert closed == [True]
    assert calls[0][0].endswith("/" + path)
    assert calls[0][1]["timeout"] == (5.0, timeout)
    if kind != "screencap":
        assert calls[0][1]["params"] == {"filename": "frame.png"}


@pytest.mark.parametrize("status", [200, 404])
def test_image_content_type_and_error_release(monkeypatch, status):
    upstream = requests.Response()
    upstream.status_code = status
    upstream.headers["content-type"] = "image/webp"
    upstream._content = b'{"detail":"frame missing"}'
    closed = []
    monkeypatch.setattr(upstream, "close", lambda: closed.append(True))
    monkeypatch.setattr(window_remote.requests, "get", lambda *args, **kwargs: upstream)
    entry = UserDevice(mode="remote", server_url="http://example.invalid", token="test-token")
    if status == 200:
        assert window_remote.remote_game_window2_screencap(entry).media_type == "image/webp"
    else:
        with pytest.raises(HTTPException) as caught:
            window_remote.remote_game_window2_screencap(entry)
        assert caught.value.status_code == 404
        assert caught.value.detail == "frame missing"
    assert closed == [True]
