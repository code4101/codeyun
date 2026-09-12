"""Ephemeral, single-device broker; Kernel calls cross-process loopback HTTP.

Only the authenticated superuser API can register workers. No device fallback is
allowed. Delivery is at most once: a lost receipt is uncertain, never replayed.
This broker requires one API process (the current CodeYun deployment).
"""
from __future__ import annotations

import copy
from collections import deque
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .sessions import RemoteError

LEASE_SECONDS = 45
# 200k commands cover 12 hours at 4.6 commands/second. Completed records keep
# two 32-byte digests and flags, never screenshots, memory or shell payloads.
MAX_COMMANDS = 200_000
PACKAGE = "com.frxxcrjpwssc3.ggws"
OPERATIONS = frozenset({"shell_text", "shell_bytes", "input", "capture", "health", "root", "recover", "manager_input", "launch"})


@dataclass(slots=True)
class CommandRecord:
    fingerprint: bytes
    expires_at: float
    command: dict | None
    delivered: bool = False
    receipt: dict | None = None
    receipt_hash: bytes | None = None
    retired: bool = False

    def retire(self):
        self.command = None
        self.receipt = None
        self.retired = True


@dataclass
class Worker:
    user_id: int
    device_id: str
    instance_id: str
    metadata: dict
    worker_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    expires_at: float = 0
    capability: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    commands: dict = field(default_factory=dict)
    pending: deque = field(default_factory=deque)
    closed: bool = False


class DeviceBridge:
    def __init__(self, *, clock=time.time):
        self.clock = clock
        self.condition = threading.Condition()
        self.workers: dict[str, Worker] = {}

    def _worker(self, worker_id, user_id=None):
        worker = self.workers.get(worker_id)
        if worker is None or (user_id is not None and worker.user_id != user_id):
            raise RemoteError(404, "设备会话不存在")
        if worker.closed or worker.expires_at <= self.clock():
            raise RemoteError(409, "客户端设备租约已失效；禁止回退本机设备")
        return worker

    def connect(self, user_id, device_id, client_instance_id, metadata):
        with self.condition:
            for worker in self.workers.values():
                if not worker.closed and worker.expires_at > self.clock():
                    if (worker.user_id, worker.device_id, worker.instance_id) == (user_id, device_id, client_instance_id):
                        worker.expires_at = self.clock() + LEASE_SECONDS
                        return {"worker_id": worker.worker_id, "lease_seconds": LEASE_SECONDS}
                    raise RemoteError(409, "唯一 Kernel 已绑定一个客户端设备，请先断开原会话")
            self.workers.clear()
            worker = Worker(user_id, device_id, client_instance_id, copy.deepcopy(metadata), expires_at=self.clock() + LEASE_SECONDS)
            self.workers[worker.worker_id] = worker
            return {"worker_id": worker.worker_id, "lease_seconds": LEASE_SECONDS}

    def bind(self, user_id, worker_id):
        """API-process only: return scoped capability to the trusted Kernel binder.

        Never include this secret in worker responses, status, logs or artifacts.
        """
        with self.condition:
            return self._worker(worker_id, user_id).capability

    def describe(self, user_id, worker_id):
        """Validate authenticated dispatch ownership; excludes all credentials."""
        with self.condition:
            worker = self._worker(worker_id, user_id)
            return {"worker_id": worker.worker_id, "device_id": worker.device_id,
                    "metadata": copy.deepcopy(worker.metadata), "expires_at": worker.expires_at}

    def signed_rpc(self, token, **payload):
        if not verify_kernel_signature(payload["worker_id"], token):
            raise RemoteError(403, "设备 RPC 凭证无效")
        with self.condition:
            capability = self._worker(payload["worker_id"]).capability
        return self.rpc(capability=capability, **payload)

    def poll(self, user_id, worker_id, wait_seconds=20):
        deadline = time.monotonic() + min(25, max(0, wait_seconds))
        with self.condition:
            worker = self._worker(worker_id, user_id)
            worker.expires_at = self.clock() + LEASE_SECONDS
            while True:
                self._worker(worker_id, user_id)
                while worker.pending:
                    item = worker.commands[worker.pending.popleft()]
                    if item.retired or item.expires_at <= self.clock():
                        item.retire()
                        continue
                    item.delivered = True
                    command = item.command
                    item.command = None
                    return {"command": command}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"command": None}
                self.condition.wait(min(remaining, 1))

    def result(self, user_id, worker_id, request_id, status, result, error=""):
        with self.condition:
            worker = self._worker(worker_id, user_id)
            worker.expires_at = self.clock() + LEASE_SECONDS
            item = worker.commands.get(request_id)
            if item is None or not item.delivered:
                raise RemoteError(409, "回执没有对应的已投递命令")
            receipt = {"status": status, "result": copy.deepcopy(result), "error": error}
            receipt_hash = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).digest()
            if item.receipt_hash is not None:
                if item.receipt_hash != receipt_hash:
                    raise RemoteError(409, "命令回执冲突")
            else:
                if not item.retired:
                    item.receipt = receipt
                item.receipt_hash = receipt_hash
            self.condition.notify_all()
            return {"accepted": True}

    def close(self, user_id, worker_id):
        with self.condition:
            worker = self._worker(worker_id, user_id)
            worker.closed = True
            self.condition.notify_all()
            return {"closed": True}

    def rpc(self, worker_id, capability, request_id, operation, args, timeout_s=30):
        if operation not in OPERATIONS or not isinstance(args, dict):
            raise RemoteError(422, "不支持的设备操作")
        if args.get("package", PACKAGE) != PACKAGE:
            raise RemoteError(422, "设备桥仅用于凡修进程")
        fingerprint = hashlib.sha256(json.dumps([operation, args], sort_keys=True).encode()).digest()
        deadline = time.monotonic() + min(120, max(1, timeout_s))
        with self.condition:
            worker = self._worker(worker_id)
            if not hmac.compare_digest(worker.capability, capability):
                raise RemoteError(403, "设备桥凭证无效")
            item = worker.commands.get(request_id)
            if item is not None and item.fingerprint != fingerprint:
                raise RemoteError(409, "命令编号已用于其他操作")
            if item is None:
                # Receipts stay for the lease lifetime to prevent replay. Bound
                # growth by refusing new work, never evicting deduplication keys.
                if len(worker.commands) >= MAX_COMMANDS:
                    raise RemoteError(409, "设备会话容量已满，请空闲时重新连接")
                expires_at = self.clock() + min(120, max(1, timeout_s))
                item = CommandRecord(fingerprint, expires_at,
                    {"request_id": request_id, "operation": operation,
                     "args": copy.deepcopy(args), "expires_at": expires_at})
                worker.commands[request_id] = item
                worker.pending.append(request_id)
                self.condition.notify_all()
            while item.receipt is None:
                self._worker(worker_id)
                if item.retired:
                    return {"status": "uncertain", "result": {}, "error": "命令结果已消费或过期；禁止重放动作，新观察需使用新请求"}
                remaining = deadline - time.monotonic()
                if remaining <= 0 or item.expires_at <= self.clock():
                    item.retire()
                    raise RemoteError(504, "设备命令结果不确定；禁止重放动作")
                self.condition.wait(min(remaining, 1))
            response = item.receipt
            item.retire()
            return response


device_bridge = DeviceBridge()


def bind_kernel_worker(user_id: int, worker_id: str) -> str:
    return device_bridge.bind(user_id, worker_id)


def _internal_signature(worker_id: str, expires: int) -> str:
    from backend.core.settings import get_settings
    key = get_settings().secret_key.encode()
    message = f"fanxiu-device-bridge-v1:{worker_id}:{expires}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_kernel_signature(worker_id: str, token: str) -> bool:
    try:
        expires_text, signature = token.split(":", 1)
        expires = int(expires_text)
        return time.time() < expires <= time.time() + 180 and hmac.compare_digest(signature, _internal_signature(worker_id, expires))
    except (ValueError, TypeError):
        return False


def get_kernel_transport(worker_id: str, *, base_url: str = "http://127.0.0.1:8000") -> "BridgeTransport":
    """Get a server-internal transport without putting secrets in Cell source.

    The caller's authenticated dispatch must check worker ownership first. Each
    RPC carries a fresh short-lived signature, scoped to an existing worker.
    """
    return BridgeTransport(base_url, worker_id, "")


class BridgeTransport:
    """Kernel-side RPC client. All failures propagate; there is no local fallback."""
    def __init__(self, base_url: str, worker_id: str, capability: str):
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.password:
            raise ValueError("Kernel broker 地址必须是本机 HTTP")
        self.base_url, self.worker_id = base_url.rstrip("/"), worker_id
        self._capability = capability

    def call(self, operation: str, args: dict, *, timeout_s: float = 30, request_id: str | None = None) -> dict:
        payload = {"worker_id": self.worker_id, "request_id": request_id or uuid.uuid4().hex,
                   "operation": operation, "args": args, "timeout_s": timeout_s}
        expires = int(time.time()) + 150
        capability = self._capability or f"{expires}:{_internal_signature(self.worker_id, expires)}"
        request = urllib.request.Request(self.base_url + "/api/fanxiu/remote/device-rpc",
                    data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "X-Fanxiu-Bridge": capability})
        # Ignore proxy environment, never redirect a scoped capability.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=min(120, timeout_s) + 5) as response:
            receipt = json.load(response)
        if receipt.get("status") != "ok":
            raise RuntimeError("客户端设备操作失败或结果不确定：" + str(receipt.get("error") or receipt.get("status")))
        return receipt["result"]
