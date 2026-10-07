"""按需远端浏览器诊断：默认关闭、限时、内存环形记录，无后台线程或轮询。

请求 UA 是启发式识别；品牌隐藏时，专用链接可发送显式 qianxin 标记。
只记录路径/状态/耗时，不读取请求正文、认证头或业务响应内容。
"""
from collections import deque
from threading import RLock
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders


HEADER = 'X-CodeYun-Browser-Diagnostics'


class BrowserDiagnostics:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = RLock()
        self.run_id = ''
        self.expires_at = 0.0
        self.markers = ['qianxin', 'qi-an-xin', 'qaxbrowser', '奇安信']
        self.events = deque(maxlen=500)
        self.report_count = 0

    def active(self):
        return bool(self.run_id) and self.clock() < self.expires_at

    def configure(self, enabled, ttl_seconds=600, markers=None):
        with self.lock:
            if enabled:
                self.run_id = uuid.uuid4().hex
                self.expires_at = self.clock() + ttl_seconds
                self.markers = markers or ['qianxin', 'qi-an-xin', 'qaxbrowser', '奇安信']
                self.events.clear()
                self.report_count = 0
            else:
                self.expires_at = 0
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            return {'enabled': self.active(), 'run_id': self.run_id,
                    'expires_at': self.expires_at, 'user_agent_contains': self.markers,
                    'events': list(self.events)}

    def target(self, user_agent, explicit=''):
        if not self.active():
            return False
        return explicit == 'qianxin' or any(marker.lower() in user_agent.lower() for marker in self.markers)

    def record(self, run_id, event):
        with self.lock:
            if not self.active() or run_id != self.run_id:
                return False
            self.events.append({'at': self.clock(), **event})
            return True

    def report(self, run_id, user_id, events):
        with self.lock:
            # 调试期间也限制总上传量；关闭后拒绝迟到的批次。
            if not self.active() or run_id != self.run_id or self.report_count >= 100:
                return False
            self.report_count += 1
            for event in events:
                self.record(run_id, {'source': 'browser', 'user_id': user_id, **event})
            return True


diagnostics = BrowserDiagnostics()


class BrowserDiagnosticsMiddleware:
    """关闭时直接透传 ASGI；开启后才解析 UA、计时和附加诊断响应头。"""
    def __init__(self, app, store=diagnostics):
        self.app, self.store = app, store

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] != 'http' or not self.store.active() or not path.startswith('/api/') or path.startswith('/api/browser-diagnostics'):
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        ua = headers.get('user-agent', '')
        if not self.store.target(ua, headers.get('x-codeyun-debug-browser', '')):
            return await self.app(scope, receive, send)
        run_id, expires_at = self.store.run_id, self.store.expires_at
        started = time.perf_counter()
        status_code, size = 500, 0

        async def capture(message):
            nonlocal status_code, size
            if message['type'] == 'http.response.start':
                status_code = message['status']
                MutableHeaders(scope=message)[HEADER] = f'{run_id};{int(expires_at)}'
            elif message['type'] == 'http.response.body':
                size += len(message.get('body', b''))
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            self.store.record(run_id, {'source': 'server', 'method': scope['method'],
                'path': path[:500], 'status': status_code, 'bytes': size,
                'duration_ms': round((time.perf_counter() - started) * 1000, 1),
                'user_agent': ua[:1000]})
