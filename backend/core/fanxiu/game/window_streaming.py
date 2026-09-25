"""窗口画面流的 HTTP 适配与上游连接所有权。"""

from collections.abc import Callable

import anyio
import requests
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from .window_remote import extract_stream_error


class _UpstreamStreamResponse(StreamingResponse):
    def __init__(self, upstream: requests.Response, cleanup: Callable[[], None] | None):
        self._upstream = upstream
        self._cleanup = cleanup
        self._closed = False
        super().__init__(
            upstream.iter_content(chunk_size=64 * 1024),
            media_type=upstream.headers.get("content-type") or "multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store", "Pragma": "no-cache", "X-Accel-Buffering": "no"},
        )

    def close_upstream(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._upstream.close()
        finally:
            if self._cleanup is not None:
                self._cleanup()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # 传输异常不会运行 Starlette 的 background；外层取消也不能跳过释放。
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(self.close_upstream)


def stream_response_from_requests(
    response: requests.Response,
    *,
    cleanup: Callable[[], None] | None = None,
) -> StreamingResponse:
    """接管上游响应；拒绝错误响应或结束流时，关闭连接并执行一次关联清理。"""
    stream = _UpstreamStreamResponse(response, cleanup)
    if response.status_code >= 400:
        try:
            detail = extract_stream_error(response)
            raise HTTPException(status_code=response.status_code, detail=detail)
        finally:
            stream.close_upstream()
    return stream
