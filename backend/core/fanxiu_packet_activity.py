from __future__ import annotations

import socket
import struct
import threading
import time
import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from backend.core.fanxiu_packet_capture import FAKE_IP_NETWORKS, build_fanxiu_packet_capture_snapshot
from backend.core.fanxiu_tcp_flow import prune_fanxiu_tcp_storage, resolve_fanxiu_tcp_live_capture_dir

MAX_PAYLOAD_SAMPLE_BYTES = 2048
MAX_PAYLOAD_HISTORY_ITEMS = 5000
DEFAULT_PAYLOAD_STREAM_BYTES = 32768
MAX_PAYLOAD_STREAM_BYTES = 65536
PCAP_LINKTYPE_RAW = 101


@dataclass
class PacketFlowStats:
    key: str
    protocol: str
    remote_ip: str
    remote_port: int
    packets_up: int = 0
    packets_down: int = 0
    bytes_up: int = 0
    bytes_down: int = 0
    payload_bytes_up: int = 0
    payload_bytes_down: int = 0
    sample_payload_up: bytes = b""
    sample_payload_down: bytes = b""
    first_seen: str = ""
    last_seen: str = ""

    @staticmethod
    def _payload_preview(payload: bytes) -> dict[str, Any]:
        if not payload:
            return {
                "length": 0,
                "hex": "",
                "ascii": "",
                "text": "",
                "printable_ratio": 0.0,
                "guess": "无负载",
            }

        printable = 0
        chars: list[str] = []
        text_chars: list[str] = []
        for value in payload:
            if value in (9, 10, 13):
                printable += 1
                chars.append(" ")
                text_chars.append("\n" if value == 10 else "\t" if value == 9 else "")
            elif 32 <= value <= 126:
                printable += 1
                char = chr(value)
                chars.append(char)
                text_chars.append(char)
            else:
                chars.append(".")
                text_chars.append(".")

        ratio = printable / len(payload)
        stripped = payload.lstrip()
        upper = stripped[:16].upper()
        if upper.startswith((b"GET ", b"POST ", b"PUT ", b"HEAD ", b"HTTP/")):
            guess = "HTTP 明文"
        elif stripped.startswith((b"{", b"[")) and ratio >= 0.75:
            guess = "JSON/文本"
        elif ratio >= 0.85:
            guess = "可读文本"
        elif ratio >= 0.45:
            guess = "混合/二进制"
        else:
            guess = "二进制或密文"

        return {
            "length": len(payload),
            "hex": payload.hex(" "),
            "ascii": "".join(chars).strip(),
            "text": "".join(text_chars).strip(),
            "printable_ratio": round(ratio, 3),
            "guess": guess,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "protocol": self.protocol,
            "remote": {
                "ip": self.remote_ip,
                "port": self.remote_port,
                "label": f"{self.remote_ip}:{self.remote_port}",
            },
            "packets_up": self.packets_up,
            "packets_down": self.packets_down,
            "bytes_up": self.bytes_up,
            "bytes_down": self.bytes_down,
            "payload_bytes_up": self.payload_bytes_up,
            "payload_bytes_down": self.payload_bytes_down,
            "payload_preview": {
                "up": self._payload_preview(self.sample_payload_up),
                "down": self._payload_preview(self.sample_payload_down),
            },
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass
class PacketPayloadHistoryItem:
    id: int
    captured_at: str
    key: str
    protocol: str
    remote_ip: str
    remote_port: int
    direction: str
    packet_bytes: int
    payload_bytes: int
    sample_payload: bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "captured_at": self.captured_at,
            "key": self.key,
            "protocol": self.protocol,
            "remote": {
                "ip": self.remote_ip,
                "port": self.remote_port,
                "label": f"{self.remote_ip}:{self.remote_port}",
            },
            "direction": self.direction,
            "packet_bytes": self.packet_bytes,
            "payload_bytes": self.payload_bytes,
            "payload_preview": PacketFlowStats._payload_preview(self.sample_payload),
        }


def _now_label() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _now_file_label() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _is_fake_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(ip in network for network in FAKE_IP_NETWORKS)


def _list_local_ipv4_addresses() -> list[str]:
    addresses: list[str] = []
    seen: set[str] = set()
    for items in psutil.net_if_addrs().values():
        for item in items:
            if item.family != socket.AF_INET:
                continue
            ip = str(item.address or "").strip()
            if not ip or ip == "127.0.0.1" or ip in seen:
                continue
            addresses.append(ip)
            seen.add(ip)
    return addresses


def _choose_bind_ip() -> str:
    counts: dict[str, int] = {}
    try:
        snapshot = build_fanxiu_packet_capture_snapshot(resolve_dns=False)
    except Exception:
        snapshot = {}
    for item in snapshot.get("connections") or []:
        if item.get("process_group") != "mumu":
            continue
        local = item.get("local") or {}
        ip = str(local.get("ip") or "").strip()
        if not ip or ip == "127.0.0.1":
            continue
        counts[ip] = counts.get(ip, 0) + 1
    if counts:
        return sorted(counts, key=lambda value: (_is_fake_ip(value), counts[value]), reverse=True)[0]

    addresses = _list_local_ipv4_addresses()
    fake_ip = next((ip for ip in addresses if _is_fake_ip(ip)), "")
    return fake_ip or (addresses[0] if addresses else "")


def _parse_ipv4_packet(data: bytes, bind_ip: str) -> tuple[str, str, int, str, int, int, bytes] | None:
    if len(data) < 20:
        return None
    version = data[0] >> 4
    if version != 4:
        return None
    header_length = (data[0] & 0x0F) * 4
    if len(data) < header_length + 4:
        return None

    total_length = struct.unpack("!H", data[2:4])[0]
    if total_length < header_length:
        return None
    packet = data[: min(len(data), total_length)]
    if len(packet) < header_length + 4:
        return None

    protocol_number = packet[9]
    if protocol_number == 6:
        protocol = "tcp"
    elif protocol_number == 17:
        protocol = "udp"
    else:
        return None

    src_ip = socket.inet_ntoa(packet[12:16])
    dst_ip = socket.inet_ntoa(packet[16:20])
    src_port, dst_port = struct.unpack("!HH", packet[header_length:header_length + 4])

    if protocol == "tcp":
        if len(packet) < header_length + 20:
            return None
        tcp_header_length = (packet[header_length + 12] >> 4) * 4
        payload_start = header_length + tcp_header_length
    else:
        if len(packet) < header_length + 8:
            return None
        udp_length = struct.unpack("!H", packet[header_length + 4:header_length + 6])[0]
        payload_start = header_length + 8
        packet = packet[: min(len(packet), header_length + udp_length)]

    payload = packet[payload_start:] if payload_start <= len(packet) else b""

    if src_ip == bind_ip:
        return protocol, dst_ip, dst_port, "up", src_port, len(packet), payload
    if dst_ip == bind_ip:
        return protocol, src_ip, src_port, "down", dst_port, len(packet), payload
    return None


class FanxiuPacketActivityService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._flows: dict[str, PacketFlowStats] = {}
        self._running = False
        self._bind_ip = ""
        self._started_at = ""
        self._last_error = ""
        self._total_packets = 0
        self._total_bytes = 0
        self._history: list[PacketPayloadHistoryItem] = []
        self._next_history_id = 1
        self._pcap_path: Path | None = None
        self._pcap_file = None
        self._pcap_size = 0

    def start(self, bind_ip: str = "") -> dict[str, Any]:
        selected_ip = (bind_ip or _choose_bind_ip()).strip()
        if not selected_ip:
            with self._lock:
                self._last_error = "没有可监听的本机 IPv4 地址"
            return self.status()

        with self._lock:
            if self._running and self._bind_ip == selected_ip:
                return self.status()
            self._stop_locked()
            self._flows = {}
            self._bind_ip = selected_ip
            self._started_at = _now_label()
            self._last_error = ""
            self._total_packets = 0
            self._total_bytes = 0
            self._history = []
            self._next_history_id = 1
            self._open_pcap_locked(selected_ip)
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(target=self._run, args=(selected_ip,), daemon=True)
            self._thread.start()
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._stop_locked()
            return self.status()

    def clear(self) -> dict[str, Any]:
        with self._lock:
            self._flows = {}
            self._total_packets = 0
            self._total_bytes = 0
            self._history = []
            self._next_history_id = 1
            return self.status()

    def status(self, limit: int = 200) -> dict[str, Any]:
        with self._lock:
            items = sorted(
                (flow.to_dict() for flow in self._flows.values()),
                key=lambda item: (
                    int(item["bytes_up"]) + int(item["bytes_down"]),
                    int(item["packets_up"]) + int(item["packets_down"]),
                    str(item["last_seen"]),
                ),
                reverse=True,
            )[: max(1, min(int(limit or 200), 1000))]
            return {
                "running": self._running,
                "bind_ip": self._bind_ip,
                "interfaces": _list_local_ipv4_addresses(),
                "started_at": self._started_at,
                "last_error": self._last_error,
                "total_packets": self._total_packets,
                "total_bytes": self._total_bytes,
                "history_total": len(self._history),
                "history_capacity": MAX_PAYLOAD_HISTORY_ITEMS,
                "pcap_path": str(self._pcap_path) if self._pcap_path else "",
                "pcap_size": self._pcap_size,
                "items": items,
            }

    def history(self, offset: int = 0, limit: int = 50, key: str = "") -> dict[str, Any]:
        offset = max(0, int(offset or 0))
        limit = max(1, min(int(limit or 50), 200))
        normalized_key = str(key or "").strip()
        with self._lock:
            candidates = [
                item for item in reversed(self._history)
                if not normalized_key or item.key == normalized_key
            ]
            total = len(candidates)
            page = candidates[offset: offset + limit]
            return {
                "items": [item.to_dict() for item in page],
                "total": total,
                "offset": offset,
                "limit": limit,
                "history_capacity": MAX_PAYLOAD_HISTORY_ITEMS,
            }

    def stream(self, key: str = "", max_bytes: int = DEFAULT_PAYLOAD_STREAM_BYTES) -> dict[str, Any]:
        normalized_key = str(key or "").strip()
        limit_bytes = max(1024, min(int(max_bytes or DEFAULT_PAYLOAD_STREAM_BYTES), MAX_PAYLOAD_STREAM_BYTES))

        def empty_side() -> dict[str, Any]:
            return {
                "packet_count": 0,
                "payload_bytes": 0,
                "sampled_bytes": 0,
                "dropped_bytes": 0,
                "truncated_packets": 0,
                "first_seen": "",
                "last_seen": "",
                "buffer": bytearray(),
            }

        sides = {"up": empty_side(), "down": empty_side()}
        with self._lock:
            for item in self._history:
                if normalized_key and item.key != normalized_key:
                    continue
                if item.direction not in sides:
                    continue
                side = sides[item.direction]
                sample = item.sample_payload
                side["packet_count"] += 1
                side["payload_bytes"] += item.payload_bytes
                side["sampled_bytes"] += len(sample)
                if len(sample) < item.payload_bytes:
                    side["truncated_packets"] += 1
                if not side["first_seen"]:
                    side["first_seen"] = item.captured_at
                side["last_seen"] = item.captured_at
                side["buffer"].extend(sample)
                if len(side["buffer"]) > limit_bytes:
                    del side["buffer"][:-limit_bytes]

        result: dict[str, Any] = {
            "key": normalized_key,
            "max_bytes": limit_bytes,
        }
        for direction, side in sides.items():
            buffer = bytes(side.pop("buffer"))
            side["dropped_bytes"] = max(0, int(side["sampled_bytes"]) - len(buffer))
            side["preview"] = PacketFlowStats._payload_preview(buffer)
            result[direction] = side
        return result

    def _stop_locked(self) -> None:
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        self._running = False
        if thread and thread.is_alive():
            thread.join(timeout=1.5)
        self._close_pcap_locked()

    def _open_pcap_locked(self, bind_ip: str) -> None:
        self._close_pcap_locked()
        prune_fanxiu_tcp_storage()
        capture_dir = resolve_fanxiu_tcp_live_capture_dir()
        path = capture_dir / f"fanxiu_live_{_now_file_label()}_{bind_ip.replace('.', '-')}.pcap"
        handle = path.open("wb")
        handle.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, PCAP_LINKTYPE_RAW))
        handle.flush()
        self._pcap_path = path
        self._pcap_file = handle
        self._pcap_size = 24

    def _close_pcap_locked(self) -> None:
        handle = self._pcap_file
        self._pcap_file = None
        if handle:
            try:
                handle.flush()
                handle.close()
            except Exception:
                pass

    def _write_pcap_packet_locked(self, data: bytes) -> None:
        if not self._pcap_file:
            return
        timestamp = time.time()
        ts_sec = int(timestamp)
        ts_usec = int((timestamp - ts_sec) * 1_000_000)
        packet = data[:65535]
        header = struct.pack("<IIII", ts_sec, ts_usec, len(packet), len(data))
        try:
            self._pcap_file.write(header)
            self._pcap_file.write(packet)
            self._pcap_size += len(header) + len(packet)
        except Exception as exc:
            self._last_error = f"写入 pcap 失败：{exc}"

    def _run(self, bind_ip: str) -> None:
        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
            sock.bind((bind_ip, 0))
            sock.settimeout(0.5)
            sock.ioctl(socket.SIO_RCVALL, socket.RCVALL_ON)
            while not self._stop_event.is_set():
                try:
                    data, _addr = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                parsed = _parse_ipv4_packet(data, bind_ip)
                if not parsed:
                    continue
                protocol, remote_ip, remote_port, direction, _local_port, byte_count, payload = parsed
                self._record_packet(protocol, remote_ip, remote_port, direction, byte_count, payload, raw_packet=data)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
                self._running = False
        finally:
            if sock:
                try:
                    sock.ioctl(socket.SIO_RCVALL, socket.RCVALL_OFF)
                except Exception:
                    pass
                sock.close()

    def _record_packet(
        self,
        protocol: str,
        remote_ip: str,
        remote_port: int,
        direction: str,
        byte_count: int,
        payload: bytes,
        raw_packet: bytes = b"",
    ) -> None:
        key = f"{protocol}|{remote_ip}|{remote_port}"
        now = _now_label()
        with self._lock:
            flow = self._flows.get(key)
            if not flow:
                flow = PacketFlowStats(
                    key=key,
                    protocol=protocol,
                    remote_ip=remote_ip,
                    remote_port=remote_port,
                    first_seen=now,
                )
                self._flows[key] = flow
            flow.last_seen = now
            if direction == "up":
                flow.packets_up += 1
                flow.bytes_up += byte_count
                flow.payload_bytes_up += len(payload)
                if payload:
                    flow.sample_payload_up = payload[:MAX_PAYLOAD_SAMPLE_BYTES]
            else:
                flow.packets_down += 1
                flow.bytes_down += byte_count
                flow.payload_bytes_down += len(payload)
                if payload:
                    flow.sample_payload_down = payload[:MAX_PAYLOAD_SAMPLE_BYTES]
            self._total_packets += 1
            self._total_bytes += byte_count
            if raw_packet:
                self._write_pcap_packet_locked(raw_packet)
            if payload:
                self._history.append(
                    PacketPayloadHistoryItem(
                        id=self._next_history_id,
                        captured_at=now,
                        key=key,
                        protocol=protocol,
                        remote_ip=remote_ip,
                        remote_port=remote_port,
                        direction=direction,
                        packet_bytes=byte_count,
                        payload_bytes=len(payload),
                        sample_payload=payload[:MAX_PAYLOAD_SAMPLE_BYTES],
                    )
                )
                self._next_history_id += 1
                overflow = len(self._history) - MAX_PAYLOAD_HISTORY_ITEMS
                if overflow > 0:
                    del self._history[:overflow]


fanxiu_packet_activity_service = FanxiuPacketActivityService()
