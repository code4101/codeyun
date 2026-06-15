from __future__ import annotations

import ipaddress
import random
import socket
import struct
import time
from typing import Any

import psutil

DEFAULT_DNS_HOSTS = ("cdn-frxxz.akbing.com",)
DEFAULT_DNS_SERVER = "127.0.0.1"
DEFAULT_DNS_PORT = 1053
PROCESS_KEYWORDS = (
    "mumu",
    "nemu",
    "android",
    "adb",
    "clash",
    "verge",
    "mihomo",
)
FAKE_IP_NETWORKS = (
    ipaddress.ip_network("198.18.0.0/15"),
)
COMMON_HTTP_PORTS = {80, 443, 8080, 8443}
LOW_VALUE_HOST_HINTS = (
    "cdn",
    "beian",
    "report",
    "log",
    "upload",
    "analytics",
    "stat",
)


def _normalize_dns_hosts(hosts: list[str] | None) -> list[str]:
    values = hosts or list(DEFAULT_DNS_HOSTS)
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        host = str(value or "").strip().lower()
        if not host or host in seen:
            continue
        normalized.append(host)
        seen.add(host)
        if len(normalized) >= 50:
            break
    return normalized


def _is_fake_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(ip in network for network in FAKE_IP_NETWORKS)


def _ip_scope(value: str) -> str:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return "unknown"
    if any(ip in network for network in FAKE_IP_NETWORKS):
        return "fake_ip"
    if ip.is_loopback:
        return "loopback"
    if ip.is_private:
        return "private"
    if ip.is_link_local:
        return "link_local"
    if ip.is_global:
        return "public"
    return "reserved"


def _address_to_dict(addr: Any) -> dict[str, Any] | None:
    if not addr:
        return None
    ip = getattr(addr, "ip", None)
    port = getattr(addr, "port", None)
    if ip is None or port is None:
        return None
    return {
        "ip": str(ip),
        "port": int(port),
        "label": f"{ip}:{port}",
    }


def _connection_protocol(conn: Any) -> str:
    if conn.type == socket.SOCK_STREAM:
        return "tcp"
    if conn.type == socket.SOCK_DGRAM:
        return "udp"
    return str(conn.type)


def _connection_signal(
    *,
    process_group: str,
    protocol: str,
    status: str,
    remote: dict[str, Any] | None,
    mapped_hosts: list[str],
    is_fake_ip: bool,
) -> dict[str, Any]:
    if not remote:
        return {
            "remote_scope": "",
            "signal_score": 0,
            "signal_label": "本地监听",
            "signal_reason": "没有远端地址",
        }

    remote_ip = str(remote.get("ip") or "")
    remote_port = int(remote.get("port") or 0)
    remote_scope = _ip_scope(remote_ip)
    score = 0
    reasons: list[str] = []

    if process_group == "mumu":
        score += 30
        reasons.append("MuMu 进程")
    elif process_group == "android":
        score += 16
        reasons.append("安卓相关进程")
    elif process_group == "proxy":
        score -= 28
        reasons.append("代理工具自身连接")

    if protocol == "udp":
        score += 18
        reasons.append("UDP")
    elif protocol == "tcp":
        score += 10
        reasons.append("TCP")

    normalized_status = str(status or "").upper()
    if normalized_status == "ESTABLISHED":
        score += 12
        reasons.append("已建立")
    elif protocol == "udp":
        score += 6

    if is_fake_ip or remote_scope == "fake_ip":
        score += 26
        reasons.append("Fake IP")
    elif remote_scope == "public":
        score += 22
        reasons.append("公网远端")
    elif remote_scope in {"private", "loopback", "link_local"}:
        score -= 35
        reasons.append("本地/内网")
    else:
        score -= 8

    if remote_port in COMMON_HTTP_PORTS:
        score -= 8
        reasons.append("常见 HTTP 端口")
    elif remote_port:
        score += 18
        reasons.append("非 HTTP 常用端口")

    lowered_hosts = " ".join(mapped_hosts).lower()
    if mapped_hosts:
        if any(hint in lowered_hosts for hint in LOW_VALUE_HOST_HINTS):
            score -= 18
            reasons.append("域名像 CDN/上报")
        else:
            score += 8
            reasons.append("已映射域名")

    score = max(0, min(100, score))
    if score >= 70:
        label = "疑似业务连接"
    elif score >= 45:
        label = "可能业务连接"
    elif score >= 25:
        label = "观察连接"
    else:
        label = "低价值"

    return {
        "remote_scope": remote_scope,
        "signal_score": score,
        "signal_label": label,
        "signal_reason": "；".join(reasons) or "-",
    }


def _process_group(info: dict[str, Any]) -> str:
    text = " ".join(
        [
            str(info.get("name") or ""),
            str(info.get("exe") or ""),
            str(info.get("command_line") or ""),
        ]
    ).lower()
    if "mihomo" in text or "clash" in text or "verge" in text:
        return "proxy"
    if "mumu" in text or "nemu" in text or "android_emulator" in text:
        return "mumu"
    if "adb" in text or "android" in text:
        return "android"
    return "other"


def _safe_processes() -> dict[int, dict[str, Any]]:
    processes: dict[int, dict[str, Any]] = {}
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            info = proc.info
            command_line = " ".join(info.get("cmdline") or [])
            searchable = " ".join(
                [
                    str(info.get("name") or ""),
                    str(info.get("exe") or ""),
                    command_line,
                ]
            ).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
        if not any(keyword in searchable for keyword in PROCESS_KEYWORDS):
            continue
        item = {
            "pid": int(info["pid"]),
            "name": str(info.get("name") or ""),
            "exe": info.get("exe"),
            "command_line": command_line,
        }
        item["group"] = _process_group(item)
        processes[int(info["pid"])] = item
    return processes


def _dns_query_a(
    host: str,
    *,
    server: str = DEFAULT_DNS_SERVER,
    port: int = DEFAULT_DNS_PORT,
    timeout: float = 1.0,
) -> list[str]:
    query_id = random.randrange(0, 65536)
    header = struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
    qname = b"".join(bytes([len(part)]) + part.encode("ascii") for part in host.split(".")) + b"\0"
    packet = header + qname + struct.pack("!HH", 1, 1)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (server, port))
        data, _addr = sock.recvfrom(4096)
    finally:
        sock.close()

    response_id, _flags, question_count, answer_count, _ns_count, _ar_count = struct.unpack("!HHHHHH", data[:12])
    if response_id != query_id:
        return []

    offset = 12
    for _index in range(question_count):
        while offset < len(data) and data[offset] != 0:
            offset += data[offset] + 1
        offset += 1 + 4

    ips: list[str] = []
    for _index in range(answer_count):
        if offset >= len(data):
            break
        if data[offset] & 0xC0 == 0xC0:
            offset += 2
        else:
            while offset < len(data) and data[offset] != 0:
                offset += data[offset] + 1
            offset += 1
        if offset + 10 > len(data):
            break
        answer_type, answer_class, _ttl, data_length = struct.unpack("!HHIH", data[offset:offset + 10])
        offset += 10
        answer_data = data[offset:offset + data_length]
        offset += data_length
        if answer_type == 1 and answer_class == 1 and data_length == 4:
            ips.append(socket.inet_ntoa(answer_data))
    return ips


def _resolve_dns_hosts(hosts: list[str]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    mappings: list[dict[str, Any]] = []
    ip_to_hosts: dict[str, list[str]] = {}
    for host in hosts:
        item: dict[str, Any] = {"host": host, "ips": [], "error": None}
        try:
            ips = _dns_query_a(host)
        except Exception as exc:  # pragma: no cover - depends on local Clash state.
            item["error"] = str(exc)
            ips = []
        item["ips"] = ips
        mappings.append(item)
        for ip in ips:
            ip_to_hosts.setdefault(ip, []).append(host)
    return mappings, ip_to_hosts


def build_fanxiu_packet_capture_snapshot(
    dns_hosts: list[str] | None = None,
    *,
    resolve_dns: bool = True,
) -> dict[str, Any]:
    hosts = _normalize_dns_hosts(dns_hosts) if resolve_dns else []
    dns_mappings, ip_to_hosts = _resolve_dns_hosts(hosts) if resolve_dns else ([], {})
    processes = _safe_processes()
    connections: list[dict[str, Any]] = []
    listeners: list[dict[str, Any]] = []
    warnings: list[str] = []

    try:
        raw_connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError) as exc:
        raw_connections = []
        warnings.append(f"读取本机连接失败：{exc}")

    for conn in raw_connections:
        if conn.pid not in processes:
            continue
        local = _address_to_dict(conn.laddr)
        remote = _address_to_dict(conn.raddr)
        process = processes[conn.pid]
        mapped_hosts = ip_to_hosts.get(remote["ip"], []) if remote else []
        is_fake_ip = _is_fake_ip(remote["ip"]) if remote else False
        protocol = _connection_protocol(conn)
        item = {
            "pid": conn.pid,
            "process_name": process["name"],
            "process_group": process["group"],
            "protocol": protocol,
            "status": conn.status,
            "local": local,
            "remote": remote,
            "mapped_hosts": mapped_hosts,
            "is_fake_ip": is_fake_ip,
        }
        item.update(
            _connection_signal(
                process_group=process["group"],
                protocol=protocol,
                status=conn.status,
                remote=remote,
                mapped_hosts=mapped_hosts,
                is_fake_ip=is_fake_ip,
            )
        )
        if remote:
            connections.append(item)
        else:
            listeners.append(item)

    connections.sort(
        key=lambda item: (
            {"mumu": 0, "proxy": 1, "android": 2}.get(str(item["process_group"]), 9),
            str(item["process_name"]),
            str((item["remote"] or {}).get("ip") or ""),
            int((item["remote"] or {}).get("port") or 0),
        )
    )
    listeners.sort(key=lambda item: (str(item["process_name"]), str((item["local"] or {}).get("port") or "")))

    summary = {
        "process_count": len(processes),
        "connection_count": len(connections),
        "listener_count": len(listeners),
        "tcp_connection_count": sum(1 for item in connections if item["protocol"] == "tcp"),
        "udp_connection_count": sum(1 for item in connections if item["protocol"] == "udp"),
        "mumu_connection_count": sum(1 for item in connections if item["process_group"] == "mumu"),
        "mumu_tcp_connection_count": sum(1 for item in connections if item["process_group"] == "mumu" and item["protocol"] == "tcp"),
        "mumu_udp_connection_count": sum(1 for item in connections if item["process_group"] == "mumu" and item["protocol"] == "udp"),
        "proxy_connection_count": sum(1 for item in connections if item["process_group"] == "proxy"),
        "fake_ip_connection_count": sum(1 for item in connections if item["is_fake_ip"]),
        "mapped_connection_count": sum(1 for item in connections if item["mapped_hosts"]),
        "candidate_connection_count": sum(1 for item in connections if int(item.get("signal_score") or 0) >= 45),
    }
    return {
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dns_server": f"{DEFAULT_DNS_SERVER}:{DEFAULT_DNS_PORT}",
        "dns_mappings": dns_mappings,
        "processes": sorted(processes.values(), key=lambda item: (str(item["group"]), str(item["name"]), int(item["pid"]))),
        "connections": connections,
        "listeners": listeners,
        "warnings": warnings,
        "summary": summary,
    }
