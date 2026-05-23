from __future__ import annotations

import json
import select
import socket
import socketserver
import threading
import time
from datetime import datetime
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from backend.core.settings import get_settings

HEADER_LIMIT = 64 * 1024
BODY_PREVIEW_LIMIT = 4096
EVENT_LIMIT = 500
BUFFER_SIZE = 16384

TEXT_CONTENT_HINTS = (
    "json",
    "text/",
    "xml",
    "javascript",
    "x-www-form-urlencoded",
)
BINARY_CONTENT_HINTS = (
    "image/",
    "audio/",
    "video/",
    "font/",
    "octet-stream",
    "zip",
    "gzip",
    "protobuf",
    "msgpack",
)
STATIC_PATH_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".ico",
    ".mp4",
    ".mp3",
    ".wav",
    ".ogg",
    ".apk",
    ".obb",
    ".zip",
    ".gz",
    ".br",
    ".7z",
    ".unity3d",
    ".assetbundle",
    ".bundle",
    ".ab",
    ".bytes",
    ".bin",
    ".dat",
    ".so",
    ".dll",
)
API_PATH_HINTS = (
    "/api",
    "/gateway",
    "/rpc",
    "/login",
    "/auth",
    "/account",
    "/user",
    "/role",
    "/player",
    "/game",
    "/server",
    "/notice",
    "/event",
    "/pay",
    "/order",
)


def _now_label() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _now_file_label() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _today_label() -> str:
    return time.strftime("%Y%m%d")


def _decode_text(data: bytes) -> str:
    if not data:
        return ""
    text = data[:BODY_PREVIEW_LIMIT].decode("utf-8", errors="replace")
    return "".join(ch if ch.isprintable() or ch in "\r\n\t" else "." for ch in text)


def _hex_preview(data: bytes) -> str:
    return data[:BODY_PREVIEW_LIMIT].hex(" ")


def _packet_log_dir():
    path = get_settings().data_dir / "fanxiu" / "packet-capture"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_state_path() -> Path:
    return _packet_log_dir() / "session.json"


def _read_session_state() -> dict[str, Any]:
    path = _session_state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_session_state(payload: dict[str, Any]) -> None:
    path = _session_state_path()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _log_time_label(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def _public_event_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if not str(key).startswith("_")}


def _count_log_events(path: Path) -> int:
    try:
        if path.suffix == ".jsonl":
            with path.open("r", encoding="utf-8") as file:
                return sum(1 for line in file if line.strip())
        if path.suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            items = payload.get("items") if isinstance(payload, dict) else []
            return len(items) if isinstance(items, list) else 0
    except Exception:
        return 0
    return 0


def _read_log_events(path: Path, limit: int) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 500), 2000))
    items: list[dict[str, Any]] = []
    if path.suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    items.append(payload)
        return list(reversed(items[-limit:]))
    if path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_items = payload.get("items") if isinstance(payload, dict) else []
        if isinstance(raw_items, list):
            items = [item for item in raw_items if isinstance(item, dict)]
        return list(reversed(items[-limit:]))
    return []


def _event_time_value(event: dict[str, Any]) -> float:
    for key in ("started_at", "finished_at", "logged_at"):
        value = str(event.get(key) or "").strip()
        if not value:
            continue
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            continue
    return 0.0


def _event_id_value(event: dict[str, Any]) -> int:
    try:
        return int(event.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _event_dedupe_key(event: dict[str, Any]) -> str:
    fields = (
        "started_at",
        "event_type",
        "method",
        "target",
        "url",
        "request_headers",
        "request_body_text",
        "response_status",
        "response_headers",
        "response_body_text",
        "error",
    )
    text = "\x1f".join(str(event.get(field) or "") for field in fields)
    return sha1(text.encode("utf-8", errors="replace")).hexdigest()


def _event_matches_filter(event: dict[str, Any], event_filter: str) -> bool:
    if event_filter == "candidate":
        return event.get("semantic_role") == "api_candidate"
    if event_filter == "readable":
        return event.get("semantic_role") in {"api_candidate", "readable_http"}
    if event_filter == "encrypted_or_resource":
        return event.get("event_type") == "tls_tunnel" or event.get("semantic_role") == "static_resource"
    return True


def _event_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "event_count": len(items),
        "candidate_count": sum(1 for item in items if item.get("semantic_role") == "api_candidate"),
        "readable_count": sum(1 for item in items if item.get("semantic_role") in {"api_candidate", "readable_http"}),
        "plain_http_count": sum(1 for item in items if item.get("event_type") == "plain_http"),
        "resource_count": sum(1 for item in items if item.get("semantic_role") == "static_resource"),
        "tunnel_count": sum(1 for item in items if item.get("event_type") == "tls_tunnel"),
    }


def _content_type_kind(value: str) -> str:
    lowered = (value or "").lower()
    if any(hint in lowered for hint in TEXT_CONTENT_HINTS):
        return "text"
    if any(hint in lowered for hint in BINARY_CONTENT_HINTS):
        return "binary"
    return ""


def _text_ratio(data: bytes) -> float:
    if not data:
        return 0.0
    text = data[:BODY_PREVIEW_LIMIT].decode("utf-8", errors="replace")
    if not text:
        return 0.0
    useful = 0
    for ch in text:
        if ch == "\ufffd":
            continue
        if ch.isprintable() or ch in "\r\n\t":
            useful += 1
    return useful / max(len(text), 1)


def _looks_like_structured_text(text: str) -> bool:
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return True
    lowered = text[:1024].lower()
    return any(token in lowered for token in ('"code"', '"msg"', '"data"', "code=", "msg=", "data="))


def _host_is_asset_like(host: str) -> bool:
    lowered = host.lower()
    return any(part in lowered for part in ("cdn", "static", "asset", "res", "download", "patch"))


def _path_is_static(path: str) -> bool:
    lowered = path.lower().split("?", 1)[0]
    return any(lowered.endswith(suffix) for suffix in STATIC_PATH_SUFFIXES)


def _classify_plain_http_event(
    *,
    method: str,
    host: str,
    path: str,
    request_headers: dict[str, str],
    response_headers: dict[str, str],
    request_preview: bytes,
    response_preview: bytes,
    bytes_up: int,
    bytes_down: int,
    error: str = "",
) -> dict[str, Any]:
    if error:
        return {
            "plaintext_state": "error",
            "semantic_role": "error",
            "signal_score": 0,
            "signal_label": "错误",
            "signal_reason": error[:240],
        }

    method_upper = method.upper()
    request_content_type = request_headers.get("content-type", "")
    response_content_type = response_headers.get("content-type", "")
    request_kind = _content_type_kind(request_content_type)
    response_kind = _content_type_kind(response_content_type)
    request_text = _decode_text(request_preview)
    response_text = _decode_text(response_preview)
    request_ratio = _text_ratio(request_preview)
    response_ratio = _text_ratio(response_preview)
    static_path = _path_is_static(path)
    asset_host = _host_is_asset_like(host)
    binary_response = response_kind == "binary"
    text_response = response_kind == "text" or response_ratio >= 0.82
    structured_text = _looks_like_structured_text(request_text) or _looks_like_structured_text(response_text)
    has_upload_body = bool(request_preview) or bytes_up > len(request_headers)

    score = 20
    reasons: list[str] = ["明文 HTTP"]
    if method_upper in {"POST", "PUT", "PATCH", "DELETE"}:
        score += 20
        reasons.append(method_upper)
    if has_upload_body:
        score += 14
        reasons.append("有上行 body")
    if request_kind == "text":
        score += 16
        reasons.append(f"请求 {request_content_type}")
    if response_kind == "text":
        score += 18
        reasons.append(f"响应 {response_content_type}")
    if structured_text:
        score += 16
        reasons.append("结构化文本")
    if any(hint in path.lower() for hint in API_PATH_HINTS):
        score += 18
        reasons.append("路径像接口")
    if static_path:
        score -= 34
        reasons.append("路径像资源文件")
    if binary_response:
        score -= 32
        reasons.append(f"响应 {response_content_type}")
    if asset_host:
        score -= 10
        reasons.append("域名像 CDN/资源")
    if bytes_down > 1024 * 1024 and bytes_up < 4096:
        score -= 14
        reasons.append("下载量明显大于上行")
    if not request_preview and not response_preview and not text_response:
        score -= 18
        reasons.append("无内容片段")

    score = max(0, min(100, score))

    if static_path or binary_response or (asset_host and bytes_down > 512 * 1024 and method_upper == "GET"):
        semantic_role = "static_resource"
        signal_label = "资源/补丁"
    elif score >= 65:
        semantic_role = "api_candidate"
        signal_label = "接口候选"
    elif text_response or request_ratio >= 0.82 or response_ratio >= 0.82:
        semantic_role = "readable_http"
        signal_label = "明文可读"
    else:
        semantic_role = "low_value_http"
        signal_label = "低价值明文"

    return {
        "plaintext_state": "visible",
        "semantic_role": semantic_role,
        "signal_score": score,
        "signal_label": signal_label,
        "signal_reason": "；".join(reasons[:8]),
    }


def _classify_tls_tunnel() -> dict[str, Any]:
    return {
        "plaintext_state": "encrypted",
        "semantic_role": "tls_tunnel",
        "signal_score": 0,
        "signal_label": "TLS 隧道",
        "signal_reason": "HTTPS CONNECT 只可见域名端口和字节数，当前代理不解密内容",
    }


def _read_headers(sock: socket.socket) -> tuple[bytes, bytes]:
    data = b""
    while b"\r\n\r\n" not in data and len(data) < HEADER_LIMIT:
        chunk = sock.recv(BUFFER_SIZE)
        if not chunk:
            break
        data += chunk
    if b"\r\n\r\n" not in data:
        return data, b""
    head, rest = data.split(b"\r\n\r\n", 1)
    return head + b"\r\n\r\n", rest


def _parse_header_lines(header_bytes: bytes) -> tuple[str, dict[str, str], list[tuple[str, str]]]:
    text = header_bytes.decode("iso-8859-1", errors="replace")
    lines = text.split("\r\n")
    first_line = lines[0] if lines else ""
    headers: dict[str, str] = {}
    ordered: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        name, value = line.split(":", 1)
        key = name.strip().lower()
        normalized_value = value.strip()
        headers[key] = normalized_value
        ordered.append((name.strip(), normalized_value))
    return first_line, headers, ordered


def _split_host_port(value: str, default_port: int) -> tuple[str, int]:
    value = value.strip()
    if value.startswith("["):
        host, _, remainder = value[1:].partition("]")
        if remainder.startswith(":"):
            return host, int(remainder[1:] or default_port)
        return host, default_port
    if ":" in value:
        host, port_text = value.rsplit(":", 1)
        return host, int(port_text or default_port)
    return value, default_port


def _local_ipv4_candidates() -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        if value and value not in seen and not value.startswith("127."):
            candidates.append(value)
            seen.add(value)

    try:
        hostname = socket.gethostname()
        for item in socket.getaddrinfo(hostname, None, socket.AF_INET):
            add(str(item[4][0]))
    except OSError:
        pass

    try:
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp.connect(("8.8.8.8", 80))
            add(str(udp.getsockname()[0]))
        finally:
            udp.close()
    except OSError:
        pass
    return candidates


@dataclass
class _ProxyRuntime:
    server: socketserver.ThreadingTCPServer
    thread: threading.Thread
    host: str
    port: int


class _ThreadingProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FanxiuPacketProxyService:
    def __init__(self) -> None:
        self._runtime: _ProxyRuntime | None = None
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._next_event_id = 1
        self._last_error = ""

    def start(self, host: str = "127.0.0.1", port: int = 8899) -> dict[str, Any]:
        host = str(host or "127.0.0.1").strip()
        port = int(port)
        with self._lock:
            if self._runtime is not None:
                current = self._runtime
                if current.host == host and current.port == port:
                    return self.status()
                self.stop()

            service = self

            class Handler(socketserver.BaseRequestHandler):
                def handle(self) -> None:
                    service._handle_client(self.request, self.client_address)

            try:
                server = _ThreadingProxyServer((host, port), Handler)
            except OSError as exc:
                self._last_error = str(exc)
                raise RuntimeError(f"启动代理失败：{exc}") from exc

            actual_host, actual_port = server.server_address[:2]
            thread = threading.Thread(target=server.serve_forever, name="fanxiu-packet-proxy", daemon=True)
            thread.start()
            self._runtime = _ProxyRuntime(server=server, thread=thread, host=str(actual_host), port=int(actual_port))
            self._last_error = ""
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            runtime = self._runtime
            self._runtime = None
        if runtime is not None:
            runtime.server.shutdown()
            runtime.server.server_close()
            runtime.thread.join(timeout=2)
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            runtime = self._runtime
            running = runtime is not None
            host = runtime.host if runtime else ""
            port = runtime.port if runtime else 0
            event_count = len(self._events)
            last_error = self._last_error
        addresses: list[str] = []
        if running:
            if host in {"0.0.0.0", ""}:
                addresses = [f"127.0.0.1:{port}", *(f"{ip}:{port}" for ip in _local_ipv4_candidates())]
            else:
                addresses = [f"{host}:{port}"]
        return {
            "running": running,
            "host": host,
            "port": port,
            "addresses": addresses,
            "event_count": event_count,
            "last_error": last_error,
        }

    def list_events(self, limit: int = 200) -> dict[str, Any]:
        limit = max(1, min(int(limit or 200), EVENT_LIMIT))
        with self._lock:
            items = [_public_event_payload(item) for item in self._events[-limit:]]
        items.reverse()
        return {"items": items, "status": self.status()}

    def clear_events(self) -> dict[str, Any]:
        with self._lock:
            self._events.clear()
        return self.list_events()

    def save_events(self, label: str = "") -> dict[str, Any]:
        safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(label or "").strip())
        safe_label = safe_label.strip("_")[:40]
        suffix = f"-{safe_label}" if safe_label else ""
        path = _packet_log_dir() / f"events-{_now_file_label()}{suffix}.json"
        with self._lock:
            items = [_public_event_payload(item) for item in self._events]
        payload = {
            "saved_at": _now_label(),
            "event_count": len(items),
            "status": self.status(),
            "items": items,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "saved_at": payload["saved_at"],
            "path": str(path),
            "event_count": len(items),
            "status": payload["status"],
        }

    def session_state(self) -> dict[str, Any]:
        return _read_session_state()

    def save_session_state(
        self,
        *,
        active: bool,
        host: str,
        port: int,
        device_id: str = "",
        target_proxy: str = "",
        last_error: str = "",
    ) -> dict[str, Any]:
        payload = {
            "active": bool(active),
            "host": str(host or "127.0.0.1").strip(),
            "port": int(port or 8899),
            "device_id": str(device_id or "").strip(),
            "target_proxy": str(target_proxy or "").strip(),
            "last_error": str(last_error or "").strip(),
            "updated_at": _now_label(),
        }
        _write_session_state(payload)
        return payload

    def list_logs(self, limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(int(limit or 50), 200))
        log_dir = _packet_log_dir()
        files = [
            path
            for path in log_dir.glob("events-*")
            if path.is_file() and path.suffix in {".json", ".jsonl"}
        ]
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        items: list[dict[str, Any]] = []
        for path in files[:limit]:
            stat = path.stat()
            items.append({
                "name": path.name,
                "path": str(path),
                "size": stat.st_size,
                "modified_at": _log_time_label(stat.st_mtime),
                "event_count": _count_log_events(path),
            })
        return {"items": items, "directory": str(log_dir)}

    def load_log(self, name: str, limit: int = 500) -> dict[str, Any]:
        log_dir = _packet_log_dir()
        filename = Path(str(name or "")).name
        if not filename or filename != str(name):
            raise ValueError("日志文件名非法")
        path = log_dir / filename
        if not path.exists() or not path.is_file() or path.suffix not in {".json", ".jsonl"}:
            raise FileNotFoundError("日志文件不存在")
        stat = path.stat()
        log_info = {
            "name": path.name,
            "path": str(path),
            "size": stat.st_size,
            "modified_at": _log_time_label(stat.st_mtime),
            "event_count": _count_log_events(path),
        }
        return {"log": log_info, "items": _read_log_events(path, limit)}

    def list_timeline(self, offset: int = 0, limit: int = 50, event_filter: str = "all") -> dict[str, Any]:
        offset = max(0, int(offset or 0))
        limit = max(1, min(int(limit or 50), 200))
        event_filter = str(event_filter or "all").strip()
        if event_filter not in {"candidate", "readable", "encrypted_or_resource", "all"}:
            event_filter = "all"

        events_by_key: dict[str, dict[str, Any]] = {}

        def add_event(raw_event: dict[str, Any], source: str, source_label: str) -> None:
            event = _public_event_payload(raw_event)
            event["source"] = source
            event["source_label"] = source_label
            key = _event_dedupe_key(event)
            event["timeline_id"] = key
            existing = events_by_key.get(key)
            if existing is None or source == "live":
                events_by_key[key] = event

        log_dir = _packet_log_dir()
        files = [
            path
            for path in log_dir.glob("events-*")
            if path.is_file() and path.suffix in {".json", ".jsonl"}
        ]
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        for path in files[:200]:
            for item in _read_log_events(path, 2000):
                add_event(item, "log", path.name)

        with self._lock:
            live_items = [_public_event_payload(item) for item in self._events]
        for item in live_items:
            add_event(item, "live", "当前")

        all_items = list(events_by_key.values())
        all_items.sort(key=lambda item: (_event_time_value(item), _event_id_value(item)), reverse=True)
        summary = _event_summary(all_items)

        filtered_items = [item for item in all_items if _event_matches_filter(item, event_filter)]
        page_items = filtered_items[offset:offset + limit]
        return {
            "items": page_items,
            "status": self.status(),
            "total": len(filtered_items),
            "offset": offset,
            "limit": limit,
            "summary": summary,
            "log_directory": str(log_dir),
        }

    def _create_event(self, payload: dict[str, Any]) -> int:
        with self._lock:
            event_id = self._next_event_id
            self._next_event_id += 1
            event = {
                "id": event_id,
                "started_at": _now_label(),
                "finished_at": None,
                "active": True,
                "error": "",
                "client": "",
                "event_type": "unknown",
                "method": "",
                "target": "",
                "url": "",
                "request_headers": "",
                "request_body_text": "",
                "request_body_hex": "",
                "response_status": "",
                "response_headers": "",
                "response_body_text": "",
                "response_body_hex": "",
                "bytes_up": 0,
                "bytes_down": 0,
                "plaintext_state": "unknown",
                "semantic_role": "unknown",
                "signal_score": 0,
                "signal_label": "未判断",
                "signal_reason": "",
                "_logged": False,
                **payload,
            }
            should_log = not bool(event.get("active"))
            if should_log:
                event["_logged"] = True
            self._events.append(event)
            del self._events[:-EVENT_LIMIT]
        if should_log:
            self._append_event_log(event)
        return event_id

    def _update_event(self, event_id: int, payload: dict[str, Any]) -> None:
        event_to_log: dict[str, Any] | None = None
        with self._lock:
            for event in reversed(self._events):
                if event.get("id") == event_id:
                    event.update(payload)
                    if not event.get("active") and not event.get("_logged"):
                        event["_logged"] = True
                        event_to_log = dict(event)
                    break
        if event_to_log is not None:
            self._append_event_log(event_to_log)

    def _append_event_log(self, event: dict[str, Any]) -> None:
        try:
            path = _packet_log_dir() / f"events-{_today_label()}.jsonl"
            record = _public_event_payload(event)
            record["logged_at"] = _now_label()
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            self._last_error = f"写入抓包日志失败：{exc}"

    def _handle_client(self, client_sock: socket.socket, client_address: Any) -> None:
        client_label = f"{client_address[0]}:{client_address[1]}" if client_address else ""
        client_sock.settimeout(30)
        try:
            header_bytes, body_buffer = _read_headers(client_sock)
            if not header_bytes:
                return
            request_line, headers, ordered_headers = _parse_header_lines(header_bytes)
            parts = request_line.split()
            if len(parts) < 3:
                self._create_event({
                    "active": False,
                    "finished_at": _now_label(),
                    "client": client_label,
                    "event_type": "error",
                    "error": "invalid request line",
                    "request_headers": request_line,
                })
                return
            method, raw_target, version = parts[0].upper(), parts[1], parts[2]
            if method == "CONNECT":
                self._handle_connect(client_sock, client_label, raw_target, header_bytes)
            else:
                self._handle_plain_http(
                    client_sock,
                    client_label,
                    method,
                    raw_target,
                    version,
                    headers,
                    ordered_headers,
                    body_buffer,
                )
        except Exception as exc:
            self._create_event({
                "active": False,
                "finished_at": _now_label(),
                "client": client_label,
                "event_type": "error",
                "error": str(exc),
            })
        finally:
            try:
                client_sock.close()
            except OSError:
                pass

    def _handle_connect(self, client_sock: socket.socket, client_label: str, raw_target: str, header_bytes: bytes) -> None:
        host, port = _split_host_port(raw_target, 443)
        event_id = self._create_event({
            "client": client_label,
            "event_type": "tls_tunnel",
            "method": "CONNECT",
            "target": f"{host}:{port}",
            "request_headers": header_bytes.decode("iso-8859-1", errors="replace")[:HEADER_LIMIT],
            **_classify_tls_tunnel(),
        })
        remote_sock: socket.socket | None = None
        bytes_up = 0
        bytes_down = 0
        try:
            remote_sock = socket.create_connection((host, port), timeout=10)
            client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            sockets = [client_sock, remote_sock]
            while True:
                readable, _writable, _errors = select.select(sockets, [], sockets, 1)
                if not readable:
                    continue
                for source in readable:
                    chunk = source.recv(BUFFER_SIZE)
                    if not chunk:
                        raise ConnectionAbortedError("tunnel closed")
                    if source is client_sock:
                        remote_sock.sendall(chunk)
                        bytes_up += len(chunk)
                    else:
                        client_sock.sendall(chunk)
                        bytes_down += len(chunk)
        except Exception as exc:
            error = "" if isinstance(exc, ConnectionAbortedError) else str(exc)
            self._update_event(event_id, {
                "active": False,
                "finished_at": _now_label(),
                "bytes_up": bytes_up,
                "bytes_down": bytes_down,
                "error": error,
            })
        finally:
            if remote_sock is not None:
                try:
                    remote_sock.close()
                except OSError:
                    pass

    def _handle_plain_http(
        self,
        client_sock: socket.socket,
        client_label: str,
        method: str,
        raw_target: str,
        version: str,
        headers: dict[str, str],
        ordered_headers: list[tuple[str, str]],
        body_buffer: bytes,
    ) -> None:
        split = urlsplit(raw_target)
        if split.scheme and split.netloc:
            host = split.hostname or ""
            port = split.port or (443 if split.scheme == "https" else 80)
            path = split.path or "/"
            if split.query:
                path += f"?{split.query}"
            url = raw_target
        else:
            host_header = headers.get("host", "")
            host, port = _split_host_port(host_header, 80)
            path = raw_target or "/"
            url = f"http://{host_header}{path}"

        content_length = int(headers.get("content-length") or 0)
        body_preview = body_buffer[:BODY_PREVIEW_LIMIT]
        body_remaining = max(0, content_length - len(body_buffer))
        response_preview = b""
        response_header_map: dict[str, str] = {}
        response_header_text = ""
        response_status = ""
        bytes_up = len(body_buffer)
        bytes_down = 0
        error = ""

        try:
            remote_sock = socket.create_connection((host, port), timeout=10)
        except Exception as exc:
            self._create_event({
                "active": False,
                "finished_at": _now_label(),
                "client": client_label,
                "event_type": "plain_http",
                "method": method,
                "target": f"{host}:{port}",
                "url": url,
                "error": str(exc),
                **_classify_plain_http_event(
                    method=method,
                    host=host,
                    path=path,
                    request_headers=headers,
                    response_headers={},
                    request_preview=body_preview,
                    response_preview=b"",
                    bytes_up=bytes_up,
                    bytes_down=0,
                    error=str(exc),
                ),
            })
            return

        try:
            forward_lines = [f"{method} {path} {version}"]
            for name, value in ordered_headers:
                key = name.lower()
                if key in {"proxy-connection", "connection", "keep-alive", "proxy-authorization"}:
                    continue
                forward_lines.append(f"{name}: {value}")
            forward_lines.append("Connection: close")
            forward_header = ("\r\n".join(forward_lines) + "\r\n\r\n").encode("iso-8859-1", errors="replace")
            remote_sock.sendall(forward_header)
            if body_buffer:
                remote_sock.sendall(body_buffer)
            while body_remaining > 0:
                chunk = client_sock.recv(min(BUFFER_SIZE, body_remaining))
                if not chunk:
                    break
                remote_sock.sendall(chunk)
                if len(body_preview) < BODY_PREVIEW_LIMIT:
                    body_preview += chunk[:BODY_PREVIEW_LIMIT - len(body_preview)]
                bytes_up += len(chunk)
                body_remaining -= len(chunk)

            response_headers, response_rest = _read_headers(remote_sock)
            if response_headers:
                response_status, response_header_map, _ordered = _parse_header_lines(response_headers)
                response_header_text = response_headers.decode("iso-8859-1", errors="replace")[:HEADER_LIMIT]
                client_sock.sendall(response_headers)
            if response_rest:
                client_sock.sendall(response_rest)
                response_preview += response_rest[:BODY_PREVIEW_LIMIT]
                bytes_down += len(response_rest)
            while True:
                chunk = remote_sock.recv(BUFFER_SIZE)
                if not chunk:
                    break
                client_sock.sendall(chunk)
                if len(response_preview) < BODY_PREVIEW_LIMIT:
                    response_preview += chunk[:BODY_PREVIEW_LIMIT - len(response_preview)]
                bytes_down += len(chunk)
        except Exception as exc:
            error = str(exc)
        finally:
            try:
                remote_sock.close()
            except OSError:
                pass

        self._create_event({
            "active": False,
            "finished_at": _now_label(),
            "client": client_label,
            "event_type": "plain_http",
            "method": method,
            "target": f"{host}:{port}",
            "url": url,
            "request_headers": "\r\n".join(
                [f"{method} {raw_target} {version}", *[f"{name}: {value}" for name, value in ordered_headers]]
            )[:HEADER_LIMIT],
            "request_body_text": _decode_text(body_preview),
            "request_body_hex": _hex_preview(body_preview),
            "response_status": response_status,
            "response_headers": response_header_text,
            "response_body_text": _decode_text(response_preview),
            "response_body_hex": _hex_preview(response_preview),
            "bytes_up": bytes_up,
            "bytes_down": bytes_down,
            "error": error,
            **_classify_plain_http_event(
                method=method,
                host=host,
                path=path,
                request_headers=headers,
                response_headers=response_header_map,
                request_preview=body_preview,
                response_preview=response_preview,
                bytes_up=bytes_up,
                bytes_down=bytes_down,
                error=error,
            ),
        })


fanxiu_packet_proxy_service = FanxiuPacketProxyService()
