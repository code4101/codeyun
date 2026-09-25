import asyncio

import pytest
import requests
from fastapi import HTTPException
from starlette.requests import ClientDisconnect

from backend.core.fanxiu.game.window_streaming import stream_response_from_requests


@pytest.mark.parametrize("outcome", ["complete", "upstream_error", "client_disconnect", "cancelled"])
def test_stream_releases_connection_and_cleanup_on_every_exit(monkeypatch, outcome):
    upstream = requests.Response()
    upstream.status_code = 200
    events = []

    def chunks(**kwargs):
        yield b"frame"
        if outcome == "upstream_error":
            raise requests.exceptions.ChunkedEncodingError("broken stream")

    monkeypatch.setattr(upstream, "iter_content", chunks)
    monkeypatch.setattr(upstream, "close", lambda: events.append("close"))
    stream = stream_response_from_requests(upstream, cleanup=lambda: events.append("cleanup"))

    async def run():
        async def receive():
            raise AssertionError("ASGI 2.4 handles disconnect through send")

        async def send(message):
            if message["type"] == "http.response.body":
                if outcome == "client_disconnect":
                    raise OSError("client left")
                if outcome == "cancelled":
                    asyncio.current_task().cancel()
                    await asyncio.sleep(0)

        await stream({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    expected = {
        # Starlette 将 OSError（包括 requests 传输错误）包装为 ClientDisconnect。
        "upstream_error": ClientDisconnect,
        "client_disconnect": ClientDisconnect,
        "cancelled": asyncio.CancelledError,
    }.get(outcome)
    if expected:
        with pytest.raises(expected) as caught:
            asyncio.run(run())
        if outcome == "upstream_error":
            assert isinstance(caught.value.__context__, requests.exceptions.ChunkedEncodingError)
    else:
        asyncio.run(run())
    assert events == ["close", "cleanup"]
    assert stream.headers["Cache-Control"] == "no-store"


def test_upstream_http_error_also_runs_associated_cleanup(monkeypatch):
    upstream = requests.Response()
    upstream.status_code = 503
    upstream._content = b'{"detail":"stream unavailable"}'
    events = []
    monkeypatch.setattr(upstream, "close", lambda: events.append("close"))
    with pytest.raises(HTTPException) as caught:
        stream_response_from_requests(upstream, cleanup=lambda: events.append("cleanup"))
    assert caught.value.status_code == 503
    assert caught.value.detail == "stream unavailable"
    assert events == ["close", "cleanup"]


def test_legacy_asgi_disconnect_cleans_up(monkeypatch):
    upstream = requests.Response()
    upstream.status_code = 200
    events = []

    def chunks(**kwargs):
        while True:
            yield b"frame"

    monkeypatch.setattr(upstream, "iter_content", chunks)
    monkeypatch.setattr(upstream, "close", lambda: events.append("close"))
    stream = stream_response_from_requests(upstream, cleanup=lambda: events.append("cleanup"))

    async def run():
        sent = asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.body":
                sent.set()

        async def receive():
            await sent.wait()
            return {"type": "http.disconnect"}

        await asyncio.wait_for(stream({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send), 5)

    asyncio.run(run())
    assert events == ["close", "cleanup"]
