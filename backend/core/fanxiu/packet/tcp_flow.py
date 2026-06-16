from __future__ import annotations

import csv
import json
import hashlib
import os
import re
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Any
from threading import Lock
from types import MethodType

from backend.core.runtime.process_launcher import run_quiet

_TTL_CACHE: dict[str, tuple[float, Any]] = {}
_TTL_CACHE_LOCK = Lock()
_TTL_CACHE_SECONDS: float = 60.0


def _ttl_cache_get(key: str) -> Any | None:
    with _TTL_CACHE_LOCK:
        entry = _TTL_CACHE.get(key)
        if entry and time.monotonic() - entry[0] < _TTL_CACHE_SECONDS:
            return entry[1]
        if entry:
            del _TTL_CACHE[key]
    return None


def _ttl_cache_set(key: str, value: Any) -> None:
    with _TTL_CACHE_LOCK:
        _TTL_CACHE[key] = (time.monotonic(), value)
        if len(_TTL_CACHE) > 64:
            oldest = min(_TTL_CACHE.items(), key=lambda item: item[1][0])
            del _TTL_CACHE[oldest[0]]

from pyxllib.file.packetstream import (
    LuaPacketSchemaIndex,
    PacketDecodeError,
    VarintBinaryReader,
    summarize_decoded_frames,
)

from backend.core.settings import get_settings
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root


DEFAULT_FANXIU_SERVER_HOST = "1.12.44.63"
DEFAULT_TCP_CAPTURE_DIR = Path("tcp_captures")
DEFAULT_TCP_STORE_DIR = Path("fanxiu") / "tcp-flow"
DEFAULT_TCP_RETENTION_MAX_RECORDS = 0
DEFAULT_TCP_RETENTION_MAX_RECORD_BYTES = 5 * 1024 * 1024 * 1024
DEFAULT_TCP_RETENTION_MAX_LIVE_CAPTURES = 0
DEFAULT_TCP_RETENTION_MAX_LIVE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_TCP_RETENTION_MIN_KEEP = 20
DEFAULT_TSHARK_TIMEOUT_SECONDS = 8.0
DEFAULT_TEXT_ASSETS = Path(
    "by_source/lscripts/gamesystem/game/message_bf46a8de9ccefb33ec3f4d0545cc766e/text_assets"
)
DEFAULT_SYSTEM_MESSAGE_ASSETS = Path(
    "by_source/lscripts/generate/cfg/systemmessage_4f1639aa9e08562a84eb8e1111656050/text_assets"
)
DEFAULT_LANG_ASSETS = Path(
    "by_source/lscripts/generate/localization/chinese/lang_8b7a93eb4d5f06d47ea5377f764549a0/text_assets"
)
DEFAULT_PARSED_CONFIGS = Path("parsed_configs")
DEFAULT_LUA_PACKET_INDEX = DEFAULT_PARSED_CONFIGS / "lua_packet_index"
_SAFE_NAME_CHARS = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ._-")
_SYSTEM_MESSAGE_TEXT_REF_RE = re.compile(r"\[(\d+)\]=setmetatable\(\{.*?\[3\]=_C\[(\d+)\]", re.S)
_LUA_INT_VALUE_RE = re.compile(r"\[(\d+)\]=(\d+),")
_LUA_LANG_VALUE_RE = re.compile(r"\[(\d+)\]='((?:\\'|[^'])*)',")
_POST_PARAM_RE = re.compile(r"\$([A-Z0-9_]+)\$")
FANXIU_TCP_PROTOCOL_CATEGORY_RULES = [
    (
        "奖励/消耗/道具",
        "背包道具使用、资源消耗、奖励发放和奖励结果回执。",
        ("Reward", "Cost", "ItemUse", "ItemOneKeyOperate", "QuanFDraw"),
    ),
    (
        "公告/广播",
        "系统公告、跑马灯、玩家行为广播和跨服提示。",
        ("Notice",),
    ),
    (
        "修炼收益",
        "挂机/修炼/功法经验收取及周期收益。",
        ("PracticeCollect",),
    ),
    (
        "灵地争夺",
        "灵地争夺活动状态、报名、积分、阵柱和角色信息。",
        ("LandContend",),
    ),
    (
        "宗门/联盟",
        "宗门、联盟、成员、公告、排行、目标和红点状态同步。",
        ("Club", "Union", "CrossUnion"),
    ),
    (
        "活动",
        "限时活动、抽取、收益记录、排行和活动基础状态。",
        ("Activity", "RevenueRecord", "MemberDraw"),
    ),
    (
        "玩法场景/席位",
        "矿脉、论道、座位、阵营旗帜等场景玩法状态。",
        ("Veins", "Lundao", "Seat", "CampFlag"),
    ),
    (
        "战斗/地图同步",
        "地图切换、单位同步、Buff、限制状态、Boss 和跨服战斗场景。",
        ("SyncUnit", "LoadMap", "ChangeMap", "AllBuff", "RestrictStatus", "PeaceState", "Boss", "IsCross", "FazeShow", "PartnerArena", "Blld"),
    ),
    (
        "社交/查看他人",
        "好友列表、查看他人、队伍或目标角色信息。",
        ("Friend", "Other", "Team", "ShowOther"),
    ),
    (
        "客户端状态",
        "客户端本地数据、心跳、同步时间和轻量状态回写。",
        ("SetClientData", "SyncTime"),
    ),
]


def list_tcp_streams_with_tshark(
    pcap: str | Path,
    *,
    host: str = "",
    tshark: str | Path = r"C:\Program Files\Wireshark\tshark.exe",
    timeout_seconds: float = DEFAULT_TSHARK_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Return TCP stream ids with a timeout so one bad pcap cannot stall ingestion."""
    display_filter = "tcp.len > 0"
    if host:
        display_filter = f"ip.addr == {host} && {display_filter}"
    cmd = [
        str(tshark),
        "-r",
        str(pcap),
        "-Y",
        display_filter,
        "-T",
        "fields",
        "-e",
        "tcp.stream",
        "-e",
        "tcp.len",
    ]
    completed = _run_tshark_allow_partial_stdout(
        cmd,
        timeout_seconds=timeout_seconds,
    )
    streams: dict[int, dict[str, Any]] = {}
    for line in completed.stdout.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        try:
            stream = int(parts[0])
            length = int(parts[1] or 0) if len(parts) > 1 else 0
        except ValueError:
            continue
        item = streams.setdefault(stream, {"stream": stream, "packets": 0, "payload_bytes": 0})
        item["packets"] += 1
        item["payload_bytes"] += length
    return sorted(streams.values(), key=lambda item: (item["payload_bytes"], item["packets"]), reverse=True)


def extract_tcp_stream_payloads_with_tshark(
    pcap: str | Path,
    stream: int,
    *,
    server_host: str,
    tshark: str | Path = r"C:\Program Files\Wireshark\tshark.exe",
    timeout_seconds: float = DEFAULT_TSHARK_TIMEOUT_SECONDS,
) -> tuple[bytes, bytes]:
    """Return client/server TCP payload bytes with bounded tshark runtime."""
    cmd = [
        str(tshark),
        "-r",
        str(pcap),
        "-Y",
        f"tcp.stream == {int(stream)} && tcp.len > 0",
        "-T",
        "fields",
        "-e",
        "ip.src",
        "-e",
        "ip.dst",
        "-e",
        "tcp.payload",
    ]
    completed = _run_tshark_allow_partial_stdout(
        cmd,
        timeout_seconds=timeout_seconds,
    )
    client = bytearray()
    server = bytearray()
    for line in completed.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[2]:
            continue
        payload = bytes.fromhex(parts[2].replace(":", ""))
        if parts[1] == server_host:
            client.extend(payload)
        elif parts[0] == server_host:
            server.extend(payload)
    return bytes(client), bytes(server)


def _run_tshark_allow_partial_stdout(cmd: list[str], *, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    completed = run_quiet(
        cmd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=max(1.0, float(timeout_seconds)),
        check=False,
    )
    if completed.returncode == 0 or completed.stdout.strip():
        return completed
    raise subprocess.CalledProcessError(
        completed.returncode,
        cmd,
        output=completed.stdout,
        stderr=completed.stderr,
    )


def _resolve_export_child(export_root: str | Path | None, path: str | Path) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    raw = Path(path)
    return raw.expanduser().resolve() if raw.is_absolute() else (root / raw).resolve()


def _resolve_export_child_with_glob_fallback(export_root: str | Path | None, path: str | Path, fallback_glob: str) -> Path:
    resolved = _resolve_export_child(export_root, path)
    if resolved.exists() or Path(path).is_absolute():
        return resolved
    root = resolve_fanxiu_export_root(export_root)
    candidates = sorted((item for item in root.glob(fallback_glob) if item.is_dir()), key=lambda item: (item.stat().st_mtime, str(item)))
    return candidates[-1].resolve() if candidates else resolved


def _resolve_fanxiu_message_text_assets(export_root: str | Path | None, path: str | Path = DEFAULT_TEXT_ASSETS) -> Path:
    resolved = _resolve_export_child_with_glob_fallback(
        export_root,
        path,
        "by_source/lscripts/gamesystem/game/message_*/text_assets",
    )
    if resolved.is_dir():
        return resolved
    try:
        from backend.core.fanxiu.catalog.resources import export_fanxiu_unity_text_assets, resolve_fanxiu_resource_root

        resource_root = resolve_fanxiu_resource_root(None)
        bundles = sorted(
            (item for item in (resource_root / "lscripts" / "gamesystem" / "game").glob("message*.bytes") if item.is_file()),
            key=lambda item: (item.stat().st_mtime, str(item)),
        )
        if bundles:
            result = export_fanxiu_unity_text_assets(
                bundles[-1].relative_to(resource_root),
                resource_root=resource_root,
                export_root=resolve_fanxiu_export_root(export_root),
            )
            output_dir = Path(str(result.get("output_dir") or ""))
            if output_dir.is_dir():
                return output_dir.resolve()
    except Exception:
        pass
    return resolved


def _resolve_fanxiu_system_message_text_assets(export_root: str | Path | None) -> Path:
    return _resolve_export_child_with_glob_fallback(
        export_root,
        DEFAULT_SYSTEM_MESSAGE_ASSETS,
        "by_source/lscripts/generate/cfg/systemmessage_*/text_assets",
    )


def _resolve_fanxiu_lang_text_assets(export_root: str | Path | None) -> Path:
    return _resolve_export_child_with_glob_fallback(
        export_root,
        DEFAULT_LANG_ASSETS,
        "by_source/lscripts/generate/localization/chinese/lang_*/text_assets",
    )


def resolve_fanxiu_tcp_store_root(data_dir: str | Path | None = None) -> Path:
    base = Path(data_dir).expanduser().resolve() if data_dir else get_settings().data_dir
    if base.name == DEFAULT_TCP_STORE_DIR.name and base.parent.name == DEFAULT_TCP_STORE_DIR.parent.name:
        return base
    return (base / DEFAULT_TCP_STORE_DIR).resolve()


def resolve_fanxiu_tcp_live_capture_dir(data_dir: str | Path | None = None) -> Path:
    path = resolve_fanxiu_tcp_store_root(data_dir) / "live-captures"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(value: str, fallback: str = "capture") -> str:
    text = "".join(ch if ch in _SAFE_NAME_CHARS else "_" for ch in str(value or "").strip()).strip("._")
    return text[:80] if text else fallback


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_id_for(pcap_path: Path, digest: str, stream: int) -> str:
    return f"{_safe_name(pcap_path.stem)}_{digest[:12]}_stream{stream}"


def _record_dir_for(pcap_path: Path, digest: str, stream: int, *, data_dir: str | Path | None = None) -> Path:
    return resolve_fanxiu_tcp_store_root(data_dir) / _record_id_for(pcap_path, digest, stream)


def _load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                pass
    return total


def _remove_storage_path(path: Path, *, root: Path) -> bool:
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
        if resolved == root_resolved or not resolved.is_relative_to(root_resolved):
            return False
        if resolved.is_dir():
            shutil.rmtree(resolved)
        elif resolved.is_file():
            resolved.unlink()
        return True
    except Exception:
        return False


def _prune_storage_entries(
    entries: list[dict[str, Any]],
    *,
    root: Path,
    max_count: int,
    max_bytes: int,
    min_keep: int,
    preserve_paths: set[Path],
) -> dict[str, Any]:
    preserved = {path.resolve() for path in preserve_paths}
    candidates = sorted(entries, key=lambda item: float(item.get("mtime") or 0), reverse=True)
    total_bytes = sum(int(item.get("size") or 0) for item in candidates)
    kept_count = len(candidates)
    deleted: list[dict[str, Any]] = []

    for item in reversed(candidates):
        if kept_count <= min_keep:
            break
        count_ok = max_count <= 0 or kept_count <= max_count
        bytes_ok = max_bytes <= 0 or total_bytes <= max_bytes
        if count_ok and bytes_ok:
            break
        paths = [Path(str(path)).resolve() for path in item.get("paths", [])]
        if any(path in preserved for path in paths):
            continue
        removed_any = False
        for path in paths:
            removed_any = _remove_storage_path(path, root=root) or removed_any
        if removed_any:
            kept_count -= 1
            total_bytes = max(0, total_bytes - int(item.get("size") or 0))
            deleted.append({"name": item.get("name") or "", "size": int(item.get("size") or 0)})

    return {
        "kept_count": kept_count,
        "total_bytes": total_bytes,
        "deleted_count": len(deleted),
        "deleted_bytes": sum(int(item.get("size") or 0) for item in deleted),
        "deleted": deleted[:20],
    }


def _live_capture_key(path: Path) -> str:
    name = path.name
    for suffix in (".codeyun_decoded.json", ".decoded.json"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def prune_fanxiu_tcp_storage(
    *,
    data_dir: str | Path | None = None,
    max_records: int = DEFAULT_TCP_RETENTION_MAX_RECORDS,
    max_record_bytes: int = DEFAULT_TCP_RETENTION_MAX_RECORD_BYTES,
    max_live_captures: int = DEFAULT_TCP_RETENTION_MAX_LIVE_CAPTURES,
    max_live_bytes: int = DEFAULT_TCP_RETENTION_MAX_LIVE_BYTES,
    min_keep: int = DEFAULT_TCP_RETENTION_MIN_KEEP,
    preserve_paths: set[Path] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_tcp_store_root(data_dir)
    live_dir = resolve_fanxiu_tcp_live_capture_dir(data_dir)
    preserve = {path.resolve() for path in (preserve_paths or set())}

    record_entries: list[dict[str, Any]] = []
    if root.is_dir():
        for meta_path in root.glob("*/meta.json"):
            record_dir = meta_path.parent
            if record_dir == live_dir:
                continue
            record_entries.append(
                {
                    "name": record_dir.name,
                    "paths": [record_dir],
                    "mtime": meta_path.stat().st_mtime,
                    "size": _path_size(record_dir),
                }
            )

    live_groups: dict[str, dict[str, Any]] = {}
    if live_dir.is_dir():
        for path in list(live_dir.glob("*.pcap")) + list(live_dir.glob("*.pcapng")) + list(live_dir.glob("*.decoded.json")) + list(live_dir.glob("*.codeyun_decoded.json")):
            key = _live_capture_key(path)
            group = live_groups.setdefault(key, {"name": key, "paths": [], "mtime": 0.0, "size": 0})
            group["paths"].append(path)
            group["mtime"] = max(float(group["mtime"]), path.stat().st_mtime)
            group["size"] = int(group["size"]) + path.stat().st_size

    return {
        "record_policy": {
            "max_records": max_records,
            "max_bytes": max_record_bytes,
            "min_keep": min_keep,
        },
        "live_policy": {
            "max_captures": max_live_captures,
            "max_bytes": max_live_bytes,
            "min_keep": min_keep,
        },
        "records": _prune_storage_entries(
            record_entries,
            root=root,
            max_count=max_records,
            max_bytes=max_record_bytes,
            min_keep=min_keep,
            preserve_paths=preserve,
        ),
        "live_captures": _prune_storage_entries(
            list(live_groups.values()),
            root=root,
            max_count=max_live_captures,
            max_bytes=max_live_bytes,
            min_keep=min_keep,
            preserve_paths=preserve,
        ),
    }


@lru_cache(maxsize=4)
def _load_fanxiu_system_message_templates(export_root_text: str) -> dict[int, str]:
    system_message_path = _resolve_fanxiu_system_message_text_assets(export_root_text or None) / "SystemMessage.lua"
    lang_path = _resolve_fanxiu_lang_text_assets(export_root_text or None) / "lang.lua"
    if not system_message_path.is_file() or not lang_path.is_file():
        return {}

    system_text = system_message_path.read_text(encoding="utf-8", errors="ignore")
    lang_text = lang_path.read_text(encoding="utf-8", errors="ignore")
    c_values = {int(key): int(value) for key, value in _LUA_INT_VALUE_RE.findall(system_text)}
    lang_values = {
        int(key): value.replace("\\'", "'").replace("\\n", "\n")
        for key, value in _LUA_LANG_VALUE_RE.findall(lang_text)
    }

    templates: dict[int, str] = {}
    for message_id, c_index in _SYSTEM_MESSAGE_TEXT_REF_RE.findall(system_text):
        lang_id = c_values.get(int(c_index))
        template = lang_values.get(lang_id or -1)
        if template:
            templates[int(message_id)] = template
    return templates


def _strip_lua_rich_text(value: str) -> str:
    text = re.sub(r"<color=#[0-9A-Fa-f]+>(.*?)</color>", r"\1", value)
    text = re.sub(r"</?[^>]+>", "", text)
    return text


@lru_cache(maxsize=1)
def _load_fanxiu_level_realm_map(export_root_text: str) -> dict[int, str]:
    from backend.core.fanxiu.catalog.lua_config import parse_fanxiu_generated_lua_config, load_fanxiu_lang_map

    export_root = resolve_fanxiu_export_root(export_root_text or None)
    realm_config = export_root / "by_source" / "lscripts" / "generate" / "cfg"
    lang_dir = export_root / "by_source" / "lscripts" / "generate" / "localization" / "chinese"

    realm_candidates = sorted(realm_config.glob("realm_*/text_assets/RealmResource.lua"), key=lambda p: p.parts[-3])
    lang_candidates = sorted(lang_dir.glob("lang_*/text_assets/lang__*.lua"), key=lambda p: p.parts[-3])

    if not realm_candidates or not lang_candidates:
        return {}

    lang_map = load_fanxiu_lang_map(lang_candidates[-1]) if lang_candidates[-1].is_file() else {}
    output: dict[int, str] = {}
    for realm_path in realm_candidates:
        data = parse_fanxiu_generated_lua_config(realm_path, lang_map=lang_map)
        for row in data.get("rows") or []:
            row_id = row.get("_row_key")
            if not isinstance(row_id, int):
                continue
            realm = row.get("nameRealm_plain") or row.get("nameRealm") or ""
            stage = row.get("nameStage_plain") or row.get("nameStage") or ""
            lev = row.get("nameLevel_plain") or row.get("nameLevel") or ""
            name = f"{realm}{stage}{lev}"
            if isinstance(name, str) and name.strip():
                output[row_id] = name
    return output


@lru_cache(maxsize=24)
def _load_fanxiu_config_name_map(export_root_text: str, config_name: str) -> dict[int, str]:
    path = resolve_fanxiu_export_root(export_root_text or None) / DEFAULT_PARSED_CONFIGS / config_name / "rows.json"
    if not path.is_file():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(rows, list):
        return {}

    output: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("id", row.get("_row_key"))
        try:
            row_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        name = row.get("name_plain") or row.get("name") or row.get("title_plain") or row.get("title")
        if isinstance(name, str) and name.strip():
            output[row_id] = _strip_lua_rich_text(name.strip())
    return output


def _fanxiu_config_name(export_root: str | Path | None, config_name: str, value: Any) -> str:
    try:
        item_id = int(value)
    except (TypeError, ValueError):
        return ""
    names = _load_fanxiu_config_name_map(str(resolve_fanxiu_export_root(export_root)), config_name)
    return names.get(item_id, "")


def _format_fanxiu_named_id(export_root: str | Path | None, config_name: str, value: Any, *, fallback_prefix: str = "") -> str:
    name = _fanxiu_config_name(export_root, config_name, value)
    if name:
        return f"{name}({value})"
    return f"{fallback_prefix}{value}" if fallback_prefix else str(value)


def _format_fanxiu_ms(value: Any) -> str:
    try:
        seconds = int(value) / 1000
    except (TypeError, ValueError):
        return str(value)
    if seconds <= 0:
        return str(value)
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(seconds))


def _format_fanxiu_bool(value: Any) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return str(value)


def _clean_fanxiu_text(value: Any) -> str:
    text = str(value)
    return "".join(ch if ch in "\n\t" or ord(ch) >= 32 else "" for ch in text).replace("\ufffd", "").strip()


def _fanxiu_message_result(parsed: dict[str, Any]) -> str:
    super_value = parsed.get("_super")
    if not isinstance(super_value, dict):
        return ""
    code = super_value.get("code")
    if code in (None, 0):
        return ""
    return f"失败 code={code}"


def describe_fanxiu_tcp_protocol_category(name: str) -> dict[str, str]:
    packet_name = str(name or "")
    for category, meaning, needles in FANXIU_TCP_PROTOCOL_CATEGORY_RULES:
        if any(needle in packet_name for needle in needles):
            return {"category": category, "meaning": meaning}
    return {
        "category": "未归类",
        "meaning": "已有结构字段但业务域还没沉淀规则，需要继续结合样本和逆向逻辑标注。",
    }


FANXIU_VALUE_LABELS: dict[str, dict[int | str, str]] = {
    "sex": {1: "男", 2: "女"},
    "leaderSex": {1: "男", 2: "女"},
    "post": {
        1: "掌门",
        2: "副掌门",
        3: "长老",
        4: "护法",
        5: "精英",
        6: "成员",
    },
    "vipLevel": {
        0: "普通",
        1: "VIP1",
        2: "VIP2",
        3: "VIP3",
        4: "VIP4",
        5: "VIP5",
        6: "VIP6",
        7: "VIP7",
        8: "VIP8",
        9: "VIP9",
        10: "VIP10",
        11: "VIP11",
        12: "VIP12",
        13: "VIP13",
        14: "VIP14",
        15: "VIP15",
    },
    "leaderVipLevel": {
        0: "普通",
        1: "VIP1",
        2: "VIP2",
        3: "VIP3",
        4: "VIP4",
        5: "VIP5",
        6: "VIP6",
        7: "VIP7",
        8: "VIP8",
        9: "VIP9",
        10: "VIP10",
        11: "VIP11",
        12: "VIP12",
        13: "VIP13",
        14: "VIP14",
        15: "VIP15",
    },
    "direction": {0: "下", 1: "上", 2: "左", 3: "右"},
    "belongAlliance": {0: "否", 1: "是"},
    "canGetReward": {0: "否", 1: "是"},
    "canSignUp": {0: "否", 1: "是"},
    "finishNewRoleTask": {0: "未完成", 1: "已完成"},
    "inner": {0: "否", 1: "是"},
    "isCross": {0: "本服", 1: "跨服"},
    "cross": {0: "本服", 1: "跨服"},
}

FANXIU_CONFIG_ID_FIELDS: dict[str, str] = {
    "activityId": "Activity",
    "baseId": "Item",
    "battleField": "BattleField",
    "clubId": "Club",
    "code": "Item",
    "dungeonId": "Dungeon",
    "fazeResId": "Faze",
    "gongfa": "Gongfa",
    "item": "Item",
    "itemId": "Item",
    "landId": "Land",
    "mapId": "Map",
    "npcId": "Npc",
    "sceneId": "Scene",
}


def _build_fanxiu_field_value_labels(parsed: dict[str, Any], *, export_root: str | Path | None = None, packet_name: str = "") -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    realm_map: dict[int, str] = {}
    config_field_map: dict[str, str] = {}

    def _get_realm_map() -> dict[int, str]:
        nonlocal realm_map
        if not realm_map:
            realm_map = _load_fanxiu_level_realm_map(str(resolve_fanxiu_export_root(export_root)))
        return realm_map

    def _get_config_field_map() -> dict[str, str]:
        nonlocal config_field_map
        if not config_field_map and packet_name:
            meta = _fanxiu_protocol_meta(packet_name, export_root)
            for field in meta.get("fields") or []:
                fname = str(field.get("name") or "")
                ftype = str(field.get("type") or "")
                if fname and ftype and ftype.lower() not in _PRIMITIVE_FIELD_TYPES:
                    config_field_map[fname] = ftype
        return config_field_map

    _config_name_hints: dict[str, str] = {}

    def _guess_config_names() -> list[str]:
        stem = re.sub(r"^(CM|SM)_", "", str(packet_name or ""))
        tokens = re.findall(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|\d+", stem)
        candidates: list[str] = []
        for i in range(len(tokens)):
            for j in range(i + 1, min(i + 4, len(tokens) + 1)):
                candidate = "".join(tokens[i:j])
                if candidate.lower() not in _PRIMITIVE_FIELD_TYPES:
                    candidates.append(candidate)
        return candidates

    def _try_config_name(key: str, val: int) -> str | None:
        config_name = _get_config_field_map().get(key)
        if config_name:
            name = _fanxiu_config_name(export_root, config_name, val)
            if name:
                return name
        config_name = FANXIU_CONFIG_ID_FIELDS.get(key)
        if config_name:
            name = _fanxiu_config_name(export_root, config_name, val)
            if name:
                return name
        cache_key = f"{packet_name}:{key}"
        cached = _config_name_hints.get(cache_key)
        if cached:
            return _fanxiu_config_name(export_root, cached, val) or None
        for candidate in _guess_config_names():
            name = _fanxiu_config_name(export_root, candidate, val)
            if name:
                _config_name_hints[cache_key] = candidate
                return name
        return None

    def collect(obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        items = obj.get("items")
        if isinstance(items, list):
            for row in items:
                if not isinstance(row, dict):
                    continue
                row_super = row.get("_super")
                source = {**row_super, **row} if isinstance(row_super, dict) else row
                for key, labels in FANXIU_VALUE_LABELS.items():
                    if key in source:
                        val = source[key]
                        if isinstance(val, (int, float)) and not isinstance(val, bool):
                            label = labels.get(int(val))
                            if label:
                                result.setdefault(key, {})[str(int(val))] = label
                if "level" in source:
                    val = source["level"]
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        level_str = str(int(val))
                        if level_str not in result.get("level", {}):
                            realm_name = _get_realm_map().get(int(val))
                            if realm_name:
                                result.setdefault("level", {})[level_str] = realm_name
                for key in source:
                    if key in FANXIU_VALUE_LABELS or key == "level":
                        continue
                    val = source[key]
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        val_str = str(int(val))
                        existing = result.get(key, {})
                        if val_str not in existing:
                            label = _try_config_name(key, int(val))
                            if label:
                                result.setdefault(key, {})[val_str] = label
                collect(row)
        for value in obj.values():
            collect(value)

    collect(parsed)
    return result


_PRIMITIVE_FIELD_TYPES = {
    "int", "integer", "long", "float", "double", "number",
    "string", "str", "text",
    "bool", "boolean",
    "bytes", "byte",
    "list", "array", "map", "object", "dict",
    "any", "unknown", "void", "null",
}


FANXIU_FIELD_LABELS = {
    "account": "账号",
    "accountId": "账号ID",
    "activityId": "活动ID",
    "allRewards": "全部奖励",
    "amount": "数量",
    "attributes": "属性",
    "avgWorldLevel": "平均世界等级",
    "baseId": "基础道具ID",
    "battleField": "战场",
    "battleScore": "战斗力",
    "bean_id": "配置ID",
    "bundleId": "包名",
    "bundleVersion": "包版本",
    "canGetReward": "可领取",
    "canSignUp": "可报名",
    "channelPackage": "渠道包",
    "clubId": "宗门/联盟ID",
    "clubName": "宗门名称",
    "clubScore": "宗门/联盟积分",
    "clubVO": "宗门/联盟信息",
    "code": "道具/资源",
    "conditionLevels": "条件等级",
    "content": "内容",
    "costAndReward": "消耗和奖励",
    "createStep": "创建步骤",
    "createTime": "创建时间",
    "cross": "跨服",
    "crossGroup": "跨服分组",
    "crossUnionVO": "跨服联盟信息",
    "crossVO": "跨服信息",
    "devId": "设备ID",
    "device": "设备",
    "dungeonId": "副本ID",
    "endIndex": "结束序号",
    "endTime": "结束时间",
    "face": "头像",
    "finishedNum": "完成数量",
    "finishNewRoleTask": "新手任务",
    "functionOpen": "功能开放",
    "gongfa": "功法ID",
    "gongfaExp": "功法经验",
    "gotRewardPlayerNum": "已领奖人数",
    "gotRewards": "已领取奖励",
    "hangPoint": "挂机点",
    "hideMap": "隐藏地图",
    "i18nId": "公告模板ID",
    "infos": "参数列表",
    "inner": "内部标志",
    "interval": "间隔",
    "item": "道具",
    "itemId": "道具ID",
    "items": "列表",
    "itemUseVO": "道具使用",
    "key": "配置键",
    "landId": "灵地ID",
    "location": "位置",
    "mapId": "地图ID",
    "mapInfo": "地图信息",
    "member": "成员",
    "members": "成员列表",
    "multiCareers": "多职业",
    "myClubRank": "宗门排名",
    "name": "名称",
    "num": "数量",
    "openServer": "开服时间",
    "param": "参数",
    "pillarCurHp": "阵柱当前血量",
    "pillarMaxHp": "阵柱最大血量",
    "position": "坐标",
    "progress": "进度",
    "rank": "排行",
    "reason": "原因",
    "revenueRecordVO": "收益记录",
    "role": "角色信息",
    "roleExp": "角色经验",
    "roleId": "角色ID",
    "sceneId": "场景ID",
    "score": "积分",
    "serverId": "服务器ID",
    "serverName": "服务器",
    "sign": "签名",
    "signTime": "签名时间",
    "signUpBattleField": "报名战场",
    "skill": "技能",
    "stage": "阶段",
    "startIndex": "起始序号",
    "startTime": "开始时间",
    "storey": "层数",
    "time": "时间",
    "timestamp": "时间戳",
    "timeZone": "时区",
    "token": "令牌",
    "total": "总量",
    "totalOnlineSecends": "总在线秒数",
    "totalTimes": "总次数",
    "type": "类型",
    "vipLevel": "VIP等级",
    "wallet": "钱包",
    "worldLevel": "世界等级",
    "x": "X坐标",
    "z": "Z坐标",
}


def _fanxiu_field_label(key: str) -> str:
    if key in FANXIU_FIELD_LABELS:
        return FANXIU_FIELD_LABELS[key]
    token_text = "".join(FANXIU_PACKET_TOKEN_LABELS.get(token, token) for token in _packet_camel_tokens(key))
    return token_text or key


@lru_cache(maxsize=8)
def _load_fanxiu_protocol_catalog(export_root_text: str) -> dict[str, dict[str, Any]]:
    path = Path(export_root_text) / DEFAULT_LUA_PACKET_INDEX / "protocol_catalog_canonical.tsv"
    if not path.exists():
        return {}
    catalog: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file, delimiter="\t"):
            name = str(row.get("name") or "")
            if not name:
                continue
            fields = []
            for part in str(row.get("read_fields") or "").split(","):
                field = part.strip()
                if not field:
                    continue
                field_name, _, field_type = field.partition(":")
                fields.append({"name": field_name.strip(), "type": field_type.strip()})
            catalog[name] = {
                "id": row.get("id") or "",
                "name": name,
                "direction": row.get("direction") or "",
                "module": row.get("module") or "",
                "fields": fields,
                "handler_names": row.get("handler_names") or "",
                "logic_names": row.get("logic_names") or "",
            }
    return catalog


def _fanxiu_protocol_meta(packet_name: str, export_root: str | Path | None = None) -> dict[str, Any]:
    root = str(resolve_fanxiu_export_root(export_root))
    return _load_fanxiu_protocol_catalog(root).get(str(packet_name or ""), {})


FANXIU_PACKET_TOKEN_LABELS = {
    "Act": "活动",
    "Activity": "活动",
    "Add": "增加",
    "All": "全部",
    "Arena": "竞技场",
    "Award": "奖励",
    "Bag": "包",
    "Base": "基础",
    "Battle": "战斗",
    "Blld": "灵地争夺",
    "Blue": "蓝",
    "Boss": "Boss",
    "Buff": "Buff",
    "Camp": "阵营",
    "Change": "切换",
    "Charge": "充值",
    "Club": "宗门/联盟",
    "Collect": "收取",
    "Contend": "争夺",
    "Cost": "消耗",
    "Cross": "跨服",
    "Data": "数据",
    "Dot": "点",
    "Doupo": "斗破",
    "Draw": "抽取",
    "Energy": "能量",
    "Enter": "进入",
    "Event": "事件",
    "Exchange": "兑换",
    "Faze": "法宝",
    "Find": "寻找",
    "Finish": "完成",
    "Friend": "好友",
    "Fun": "",
    "Game": "游戏",
    "Get": "领取",
    "Goal": "目标",
    "Gongfa": "功法",
    "Grid": "格子",
    "Hp": "血量",
    "Info": "信息",
    "Is": "是否",
    "Item": "道具",
    "Land": "灵地",
    "Learn": "学习",
    "Level": "等级",
    "Light": "点亮",
    "List": "列表",
    "Load": "加载",
    "Make": "制作",
    "Map": "地图",
    "Member": "成员",
    "Mp": "法力",
    "Notice": "公告",
    "One": "一",
    "Operate": "操作",
    "Other": "他人",
    "Page": "页面",
    "Partner": "伙伴",
    "Peace": "和平",
    "Plan": "方案",
    "Player": "玩家",
    "Pool": "奖池",
    "Practice": "修炼",
    "Purify": "净化",
    "Put": "放置",
    "Rank": "排行",
    "Record": "记录",
    "Red": "红",
    "Refresh": "刷新",
    "Remove": "移除",
    "Rename": "改名",
    "Replace": "替换",
    "Restrict": "限制",
    "Result": "结果",
    "Reward": "奖励",
    "Revenue": "收益",
    "Role": "角色",
    "Save": "保存",
    "Sea": "海",
    "Search": "搜索",
    "Seat": "席位",
    "Self": "自己",
    "Send": "发送",
    "Server": "服务器",
    "Set": "设置",
    "Share": "分享",
    "Show": "查看",
    "Sign": "报名",
    "Skill": "技能",
    "Star": "星",
    "State": "状态",
    "Status": "状态",
    "Sync": "同步",
    "TD": "塔防",
    "Team": "队伍",
    "Template": "模板",
    "Time": "时间",
    "Union": "联盟",
    "Up": "提升",
    "Update": "更新",
    "Use": "使用",
    "VO": "信息",
    "View": "查看",
    "Wake": "唤醒",
    "Wave": "波次",
    "World": "世界",
    "Worship": "供奉",
    "Youli": "游历",
}


def _packet_camel_tokens(name: str) -> list[str]:
    stem = re.sub(r"^(CM|SM)_", "", str(name or "").strip())
    return re.findall(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|\d+", stem)


def describe_fanxiu_tcp_protocol_meaning(name: str, *, category: str = "", parsed: dict[str, Any] | None = None) -> str:
    packet_name = str(name or "").strip()
    if not packet_name:
        return ""
    direction = "客户端请求" if packet_name.startswith("CM_") else "服务端下发" if packet_name.startswith("SM_") else "业务包"
    phrase = "".join(FANXIU_PACKET_TOKEN_LABELS.get(token, token) for token in _packet_camel_tokens(packet_name))
    return f"{direction}{phrase}"


def _iter_fanxiu_message_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        items = value.get("items")
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _is_fanxiu_amount_item(value: dict[str, Any]) -> bool:
    return value.get("_class") in {"RewardResult", "NoticeRewardVO", "CostResult"} or (
        "type" in value and "code" in value and "amount" in value
    )


def _format_fanxiu_reward_item(reward: dict[str, Any], export_root: str | Path | None) -> str:
    amount = reward.get("amount")
    content = reward.get("content")
    if isinstance(content, dict):
        content_class = content.get("_class")
        if content_class == "GongFaExpVo":
            total = content.get("total")
            return f"功法经验 +{amount}（总 {total}）" if total is not None else f"功法经验 +{amount}"
        if content_class == "PartnerGongFaExpVo":
            total = content.get("total")
            return f"伙伴功法经验 +{amount}（总 {total}）" if total is not None else f"伙伴功法经验 +{amount}"

    code = reward.get("code")
    if code not in (None, 0):
        item = _format_fanxiu_named_id(export_root, "Item", code, fallback_prefix="道具")
        return f"{item} x{amount}" if amount is not None else item

    reward_type = reward.get("type")
    if amount is not None:
        return f"奖励 type={reward_type} +{amount}"
    return f"奖励 type={reward_type}"


def _format_fanxiu_reward_list(value: Any, export_root: str | Path | None) -> str:
    items = _iter_fanxiu_message_items(value)
    if not all(_is_fanxiu_amount_item(item) for item in items):
        names = [str(item.get("name") or (item.get("_super") or {}).get("name") or item.get("_class") or item) for item in items[:3]]
        return "，".join(names) if names else str(value)
    rewards = [_format_fanxiu_reward_item(item, export_root) for item in items]
    return "，".join(text for text in rewards if text) or str(value)


def _format_fanxiu_cost_item(cost: dict[str, Any], export_root: str | Path | None) -> str:
    code = cost.get("code")
    amount = cost.get("amount")
    if code not in (None, 0):
        item = _format_fanxiu_named_id(export_root, "Item", code, fallback_prefix="道具")
        return f"{item} x{amount}" if amount is not None else item
    cost_type = cost.get("type")
    return f"消耗 type={cost_type} x{amount}" if amount is not None else f"消耗 type={cost_type}"


def _format_fanxiu_cost_list(value: Any, export_root: str | Path | None) -> str:
    costs = [_format_fanxiu_cost_item(item, export_root) for item in _iter_fanxiu_message_items(value)]
    return "，".join(text for text in costs if text) or str(value)


def _format_fanxiu_notice_param_value(key: str, value: Any, export_root: str | Path | None) -> str:
    if isinstance(value, dict):
        value_class = value.get("_class")
        if value_class == "NoticeRoleVO":
            return str(value.get("name") or value.get("roleId") or value)
        if value_class in {"NoticeRewardVO", "RewardResult"}:
            return _format_fanxiu_reward_item(value, export_root)
        if isinstance(value.get("items"), list):
            return _format_fanxiu_reward_list(value, export_root)
        if value.get("name") not in (None, ""):
            return str(value.get("name"))
        super_value = value.get("_super")
        if isinstance(super_value, dict) and super_value.get("name") not in (None, ""):
            return str(super_value.get("name"))
        return json.dumps(_trim_value(value, max_items=3), ensure_ascii=False, separators=(",", ":"))
    if key == "MONSTER":
        return _format_fanxiu_named_id(export_root, "Monster", value, fallback_prefix="怪物")
    if key == "GONGFA":
        return _format_fanxiu_named_id(export_root, "Gongfa", value, fallback_prefix="功法")
    if key == "ITEM":
        return _format_fanxiu_named_id(export_root, "Item", value, fallback_prefix="道具")
    if key == "MAP":
        map_name = _fanxiu_config_name(export_root, "ScenePosition", value) or _fanxiu_config_name(export_root, "MapInfo", value)
        if map_name:
            return f"{map_name}({value})"
        return f"地图(ID={value})"
    return str(value)


def _fanxiu_notice_param_items(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    infos = parsed.get("infos")
    if not isinstance(infos, dict):
        return []
    items = infos.get("items")
    return items if isinstance(items, list) else []


def _fanxiu_notice_params_by_key(parsed: dict[str, Any], export_root: str | Path | None = None) -> dict[str, list[str]]:
    items = _fanxiu_notice_param_items(parsed)
    by_key: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = ""
        super_value = item.get("_super")
        if isinstance(super_value, dict):
            key = str(super_value.get("key") or "")
        if not key:
            key = str(item.get("key") or "")
        value = item.get("value")
        if value is None:
            continue
        by_key.setdefault(key, []).append(_format_fanxiu_notice_param_value(key, value, export_root))
    return by_key


def _render_fanxiu_post_template_segments(template: str, parsed: dict[str, Any], *, export_root: str | Path | None = None) -> list[dict[str, Any]]:
    by_key = _fanxiu_notice_params_by_key(parsed, export_root)
    used: dict[str, int] = {}
    segments: list[dict[str, Any]] = []
    cursor = 0

    for match in _POST_PARAM_RE.finditer(template):
        prefix = _strip_lua_rich_text(template[cursor:match.start()])
        if prefix:
            segments.append({"text": prefix, "kind": "text"})

        key = match.group(1)
        values = by_key.get(key) or []
        if not values and key.startswith("L_"):
            values = by_key.get(key[2:]) or []
        index = used.get(key, 0)
        used[key] = index + 1
        value = values[index] if index < len(values) else match.group(0)
        segments.append({"text": _strip_lua_rich_text(value), "kind": "param", "key": key})
        cursor = match.end()

    suffix = _strip_lua_rich_text(template[cursor:])
    if suffix:
        segments.append({"text": suffix, "kind": "text"})
    return segments


def _render_fanxiu_post_template(template: str, parsed: dict[str, Any], *, export_root: str | Path | None = None) -> str:
    return "".join(str(segment.get("text") or "") for segment in _render_fanxiu_post_template_segments(template, parsed, export_root=export_root))


def _describe_fanxiu_practice_collect(parsed: dict[str, Any], *, export_root: str | Path | None = None) -> str:
    gongfa = _format_fanxiu_named_id(export_root, "Gongfa", parsed.get("gongfa"), fallback_prefix="功法")
    parts = [f"修炼收取 {gongfa}"]
    if parsed.get("gongfaExp") is not None:
        parts.append(f"功法经验 +{parsed.get('gongfaExp')}")
    if parsed.get("roleExp") not in (None, 0):
        parts.append(f"角色经验 +{parsed.get('roleExp')}")
    if parsed.get("interval") is not None:
        parts.append(f"间隔 {parsed.get('interval')}秒")
    if parsed.get("collectTime") is not None:
        parts.append(f"时间 {_format_fanxiu_ms(parsed.get('collectTime'))}")
    result = _fanxiu_message_result(parsed)
    if result:
        parts.append(result)
    return "，".join(parts)


def _describe_fanxiu_reward_result(parsed: dict[str, Any], *, export_root: str | Path | None = None) -> str:
    rewards = _format_fanxiu_reward_list(parsed.get("rewards"), export_root)
    reason = _clean_fanxiu_text(parsed.get("reason", ""))
    return f"获得奖励：{rewards}" + (f"，原因 {reason}" if reason not in (None, "") else "")


def _describe_fanxiu_cost_result(parsed: dict[str, Any], *, export_root: str | Path | None = None) -> str:
    costs = _format_fanxiu_cost_list(parsed.get("costs"), export_root)
    reason = _clean_fanxiu_text(parsed.get("reason", ""))
    return f"消耗：{costs}" + (f"，原因 {reason}" if reason else "")


def _describe_fanxiu_item_use(parsed: dict[str, Any], *, export_root: str | Path | None = None) -> str:
    if parsed.get("_class") == "CM_ItemUse":
        item_use = parsed.get("itemUseVO")
        if isinstance(item_use, dict):
            item = _format_fanxiu_named_id(export_root, "Item", item_use.get("baseId"), fallback_prefix="道具")
            num = item_use.get("num")
            return f"请求使用道具：{item}" + (f" x{num}" if num is not None else "")
    cost_and_reward = parsed.get("costAndReward")
    if not isinstance(cost_and_reward, dict):
        return ""
    costs = _format_fanxiu_cost_list(cost_and_reward.get("costs"), export_root)
    rewards = _format_fanxiu_reward_list(cost_and_reward.get("rewards"), export_root)
    parts = []
    if costs:
        parts.append(f"消耗 {costs}")
    if rewards:
        parts.append(f"获得 {rewards}")
    result = _fanxiu_message_result(parsed)
    if result:
        parts.append(result)
    return "使用道具：" + "，".join(parts) if parts else "使用道具"


def _describe_fanxiu_item_onekey(parsed: dict[str, Any], *, export_root: str | Path | None = None) -> str:
    packet_class = str(parsed.get("_class") or "")
    if packet_class == "CM_ItemOneKeyOperate":
        parts: list[str] = []
        for field_key, label in [("decomposeItemIdList", "分解"), ("mergeItemIdList", "合并"), ("useItemIdList", "使用"), ("keepIdList", "保留")]:
            data = parsed.get(field_key)
            if not isinstance(data, dict):
                continue
            items = data.get("items")
            if not isinstance(items, list) or not items:
                continue
            total = data.get("_count") or len(items)
            named = [_format_fanxiu_named_id(export_root, "Item", item_id, fallback_prefix="道具") for item_id in items[:5]]
            more = f"等{total}个" if total > 5 else ""
            parts.append(f"{label}{'，'.join(named)}{more}")
        prefix = "请求道具一键操作"
        return f"{prefix}：" + "；".join(parts) if parts else prefix
    # SM_ItemOneKeyOperate - reward/cost results
    parts = []
    for field_key, label in [("decomposeRewardResults", "分解获得"), ("mergeRewardResults", "合并获得"), ("useRewardResults", "使用获得"), ("danYaoCostResults", "丹药消耗")]:
        data = parsed.get(field_key)
        if not isinstance(data, dict):
            continue
        items = data.get("items")
        if isinstance(items, list) and items:
            rewards = _format_fanxiu_reward_list(data, export_root)
            if rewards:
                parts.append(f"{label} {rewards}")
    prefix = "道具一键操作结果"
    return f"{prefix}：" + "；".join(parts) if parts else prefix


def _describe_fanxiu_land_contend_info(parsed: dict[str, Any]) -> str:
    cls = parsed.get("_class")
    stage = parsed.get("stage")
    if cls == "CM_LandContendInfo":
        return f"请求灵地争夺信息：阶段 {stage}"
    parts = [f"灵地争夺：阶段 {stage}"]
    if parsed.get("canSignUp") is not None:
        parts.append(f"可报名 {_format_fanxiu_bool(parsed.get('canSignUp'))}")
    if parsed.get("signUpBattleField") is not None:
        parts.append(f"报名战场 {parsed.get('signUpBattleField')}")
    if parsed.get("myClubRank") is not None:
        parts.append(f"社团排名 {parsed.get('myClubRank')}")
    if parsed.get("score") is not None:
        parts.append(f"个人积分 {parsed.get('score')}")
    cur_hp = parsed.get("pillarCurHp")
    max_hp = parsed.get("pillarMaxHp")
    if cur_hp is not None or max_hp is not None:
        parts.append(f"阵柱 {cur_hp}/{max_hp}")
    result = _fanxiu_message_result(parsed)
    if result:
        parts.append(result)
    return "，".join(parts)


def _describe_fanxiu_revenue_record(parsed: dict[str, Any], *, export_root: str | Path | None = None) -> str:
    record = parsed.get("revenueRecordVO")
    if not isinstance(record, dict):
        return ""
    base = record.get("_super") if isinstance(record.get("_super"), dict) else record
    name = base.get("name") or "未知玩家"
    item = _format_fanxiu_named_id(export_root, "Item", base.get("item"), fallback_prefix="道具")
    amount = base.get("amount")
    parts = [f"收益记录：{name} 获得 {item}" + (f" x{amount}" if amount is not None else "")]
    if record.get("_class") == "MiningRecordVO":
        if record.get("storey") is not None:
            parts.append(f"{record.get('storey')}层")
        if record.get("club"):
            parts.append(str(record.get("club")))
    if base.get("serverName"):
        parts.append(str(base.get("serverName")))
    if base.get("time") is not None:
        parts.append(_format_fanxiu_ms(base.get("time")))
    return "，".join(parts)


def _describe_fanxiu_member_draw(parsed: dict[str, Any]) -> str:
    parts = [f"成员抽取：{parsed.get('member') or '未知成员'}"]
    if parsed.get("storey") is not None:
        parts.append(f"{parsed.get('storey')}层")
    if parsed.get("progress") is not None:
        parts.append(f"进度 {parsed.get('progress')}")
    if parsed.get("clubScore") is not None:
        parts.append(f"社团积分 {parsed.get('clubScore')}")
    if parsed.get("activityId") is not None:
        parts.append(f"活动 {parsed.get('activityId')}")
    return "，".join(parts)


def _compact_fanxiu_scalar(value: Any, *, export_root: str | Path | None = None, key: str = "") -> str:
    if isinstance(value, bool):
        return _format_fanxiu_bool(value)
    if isinstance(value, (int, float)):
        if key.lower().endswith("time") and int(value) > 1_000_000_000_000:
            return _format_fanxiu_ms(value)
        if key.lower() in {"item", "itemid", "code"}:
            return _format_fanxiu_named_id(export_root, "Item", value, fallback_prefix="道具")
        int_val = int(value)
        enum_map = FANXIU_VALUE_LABELS.get(key)
        if enum_map:
            label = enum_map.get(int_val)
            if label:
                return f"{int_val}（{label}）"
        if key.lower() == "level":
            realm_map = _load_fanxiu_level_realm_map(str(resolve_fanxiu_export_root(export_root)))
            realm_name = realm_map.get(int_val)
            if realm_name:
                return f"{int_val}（{realm_name}）"
        config_name = FANXIU_CONFIG_ID_FIELDS.get(key)
        if config_name:
            return _format_fanxiu_named_id(export_root, config_name, value)
        return str(value)
    if isinstance(value, str):
        return _clean_fanxiu_text(value)
    if isinstance(value, dict):
        if value.get("_class") in {"RewardResult", "NoticeRewardVO"}:
            return _format_fanxiu_reward_item(value, export_root)
        if value.get("_class") == "CostResult":
            return _format_fanxiu_cost_item(value, export_root)
        if isinstance(value.get("items"), list) and (not value.get("_class") or value.get("_class") in {"RewardResult", "NoticeRewardVO", "CostResult"}):
            return _format_fanxiu_reward_list(value, export_root)
        if value.get("name") not in (None, ""):
            return str(value.get("name"))
        return json.dumps(_trim_value(value, max_items=2), ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        if not value:
            return "[]"
        return json.dumps(_trim_value(value, max_items=2), ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _fanxiu_count_items(value: Any) -> int | None:
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return len(value["items"])
    if isinstance(value, list):
        return len(value)
    return None


def _fanxiu_named_fields(parsed: dict[str, Any], keys: list[str], *, export_root: str | Path | None = None) -> list[str]:
    parts: list[str] = []
    for key in keys:
        value = parsed.get(key)
        if value in (None, "", [], {}):
            continue
        parts.append(f"{_fanxiu_field_label(key)}={_compact_fanxiu_scalar(value, export_root=export_root, key=key)}")
    return parts


def _describe_fanxiu_club_packet(parsed: dict[str, Any], *, export_root: str | Path | None = None) -> str:
    packet_class = str(parsed.get("_class") or "")
    if packet_class == "CM_ClubMemberList":
        club_id = parsed.get("clubId")
        start = parsed.get("startIndex")
        end = parsed.get("endIndex")
        parts = ["请求宗门/联盟成员列表"]
        if club_id is not None:
            parts.append(f"宗门/联盟ID {club_id}")
        if start is not None and end is not None:
            parts.append(f"分页第 {start}-{end} 条")
        return "：".join([parts[0], "，".join(parts[1:])]) if len(parts) > 1 else parts[0]
    if packet_class == "SM_ClubMemberList":
        count = _fanxiu_count_items(parsed.get("members"))
        parts = ["下发宗门/联盟成员列表"]
        if parsed.get("clubId") is not None:
            parts.append(f"宗门/联盟ID {parsed.get('clubId')}")
        if count is not None:
            parts.append(f"{count} 名成员")
        return "：".join([parts[0], "，".join(parts[1:])]) if len(parts) > 1 else parts[0]
    if packet_class == "CM_SelfClubInfo":
        return "请求自己的宗门/联盟信息"
    if packet_class == "SM_SelfClubInfo":
        club = parsed.get("clubVO")
        if isinstance(club, dict):
            name = club.get("name") or club.get("clubName")
            club_id = club.get("clubId") or club.get("id")
            parts = ["下发自己的宗门/联盟信息"]
            if name:
                parts.append(f"名称 {name}")
            if club_id is not None:
                parts.append(f"ID {club_id}")
            return "：".join([parts[0], "，".join(parts[1:])]) if len(parts) > 1 else parts[0]
        return "下发自己的宗门/联盟信息"
    if packet_class == "CM_ClubChargeActInfo":
        return "请求宗门/联盟充值活动信息"
    if packet_class == "SM_ClubChargeActInfo":
        parts = ["下发宗门/联盟充值活动信息"]
        detail = _fanxiu_named_fields(
            parsed,
            ["activityId", "stage", "finishedNum", "gotRewardPlayerNum", "canGetReward", "startTime", "endTime"],
            export_root=export_root,
        )
        return "：".join([parts[0], "，".join(detail)]) if detail else parts[0]
    if packet_class == "CM_SelfCrossUnionInfo":
        return "请求自己跨服联盟信息"
    if packet_class == "SM_SelfCrossUnionInfo":
        vo = parsed.get("crossUnionVO")
        if isinstance(vo, dict):
            super_vo = vo.get("_super") if isinstance(vo.get("_super"), dict) else vo
            name = super_vo.get("name") or ""
            purpose = vo.get("purpose") or ""
            parts = ["下发自己跨服联盟信息"]
            if name:
                parts.append(f"联盟 {name}")
            if purpose:
                parts.append(f"宗旨 {purpose}")
            count = _fanxiu_count_items(super_vo.get("members"))
            if count is not None:
                parts.append(f"{count} 个成员宗门")
            return "：".join([parts[0], "，".join(parts[1:])]) if len(parts) > 1 else parts[0]
        return "下发自己跨服联盟信息"
    if packet_class == "SM_CrossUnionRedDotInfo":
        count = _fanxiu_count_items(parsed.get("items"))
        return f"下发跨服联盟红点信息：{count} 条" if count else "下发跨服联盟红点信息"
    return ""


def _summarize_fanxiu_packet(parsed: dict[str, Any], *, export_root: str | Path | None = None) -> str:
    cls = str(parsed.get("_class") or "业务包")
    parts: list[str] = []
    meta = _fanxiu_protocol_meta(cls, export_root)
    field_names = [str(field.get("name") or "") for field in meta.get("fields", []) if field.get("name")]
    keys = [key for key in field_names if key in parsed]
    keys.extend([key for key in parsed if key not in keys])
    for key in keys:
        value = parsed.get(key)
        if key in {"_class", "_super"} or value in (None, "", [], {}):
            continue
        parts.append(f"{_fanxiu_field_label(key)}={_compact_fanxiu_scalar(value, export_root=export_root, key=key)}")
        if len(parts) >= 5:
            break
    result = _fanxiu_message_result(parsed)
    if result:
        parts.append(result)
    if parts:
        return f"{describe_fanxiu_tcp_protocol_meaning(cls)}：" + "，".join(parts)
    return describe_fanxiu_tcp_protocol_meaning(cls)


def _describe_fanxiu_tcp_business_entry(parsed: dict[str, Any], *, export_root: str | Path | None = None) -> str:
    packet_class = parsed.get("_class")
    if isinstance(packet_class, str) and ("Club" in packet_class or "Union" in packet_class):
        club_text = _describe_fanxiu_club_packet(parsed, export_root=export_root)
        if club_text:
            return club_text
    if packet_class == "SM_PracticeCollect":
        return _describe_fanxiu_practice_collect(parsed, export_root=export_root)
    if packet_class == "SM_RewardResult":
        return _describe_fanxiu_reward_result(parsed, export_root=export_root)
    if packet_class == "SM_CostResult":
        return _describe_fanxiu_cost_result(parsed, export_root=export_root)
    if packet_class in {"SM_ItemUse", "CM_ItemUse"}:
        return _describe_fanxiu_item_use(parsed, export_root=export_root)
    if packet_class in {"SM_LandContendInfo", "CM_LandContendInfo"}:
        return _describe_fanxiu_land_contend_info(parsed)
    if packet_class == "SM_RevenueRecord":
        return _describe_fanxiu_revenue_record(parsed, export_root=export_root)
    if packet_class == "SM_MemberDraw":
        return _describe_fanxiu_member_draw(parsed)
    if packet_class in {"CM_ItemOneKeyOperate", "SM_ItemOneKeyOperate"}:
        return _describe_fanxiu_item_onekey(parsed, export_root=export_root)
    if packet_class != "SM_Notice":
        return _summarize_fanxiu_packet(parsed, export_root=export_root)
    i18n_id = parsed.get("i18nId")
    if not isinstance(i18n_id, int):
        return _summarize_fanxiu_packet(parsed, export_root=export_root)
    templates = _load_fanxiu_system_message_templates(str(resolve_fanxiu_export_root(export_root)))
    template = templates.get(i18n_id)
    notice_keys = {
        str((item.get("_super") or {}).get("key") or item.get("key") or "")
        for item in _fanxiu_notice_param_items(parsed)
        if isinstance(item, dict)
    }
    _BOSS_KEYS = {"MONSTER", "MAP", "BOSS", "MONSTER_LEVEL", "MONSTER_NUM"}
    category = "Boss" if (notice_keys & _BOSS_KEYS) else ""
    if template:
        rendered = _render_fanxiu_post_template(template, parsed, export_root=export_root)
        return f"[{category}] {rendered}" if category else rendered
    values = [
        _format_fanxiu_notice_param_value(str((item.get("_super") or {}).get("key") or item.get("key") or ""), item.get("value"), export_root)
        for item in _fanxiu_notice_param_items(parsed)
        if isinstance(item, dict) and item.get("value") is not None
    ]
    base = f"[{category}] 系统公告 {i18n_id}" if category else f"系统公告 {i18n_id}"
    return base + ": " + "，".join(values) if values else base


def _describe_fanxiu_tcp_business_entry_segments(parsed: dict[str, Any], *, export_root: str | Path | None = None) -> list[dict[str, Any]]:
    if parsed.get("_class") != "SM_Notice":
        return []
    i18n_id = parsed.get("i18nId")
    if not isinstance(i18n_id, int):
        return []
    notice_keys = {
        str((item.get("_super") or {}).get("key") or item.get("key") or "")
        for item in _fanxiu_notice_param_items(parsed)
        if isinstance(item, dict)
    }
    _BOSS_KEYS = {"MONSTER", "MAP", "BOSS", "MONSTER_LEVEL", "MONSTER_NUM"}
    category = "Boss" if (notice_keys & _BOSS_KEYS) else ""
    templates = _load_fanxiu_system_message_templates(str(resolve_fanxiu_export_root(export_root)))
    template = templates.get(i18n_id)
    if template:
        segments = _render_fanxiu_post_template_segments(template, parsed, export_root=export_root)
        if category:
            segments.insert(0, {"text": f"[{category}] ", "kind": "text"})
        return segments
    values = [
        _format_fanxiu_notice_param_value(str((item.get("_super") or {}).get("key") or item.get("key") or ""), item.get("value"), export_root)
        for item in _fanxiu_notice_param_items(parsed)
        if isinstance(item, dict) and item.get("value") is not None
    ]
    text = f"[{category}] 系统公告 {i18n_id}" if category else f"系统公告 {i18n_id}"
    segments = [{"text": text, "kind": "text"}]
    if values:
        segments.append({"text": ": ", "kind": "text"})
        for index, value in enumerate(values):
            if index:
                segments.append({"text": "，", "kind": "text"})
            segments.append({"text": value, "kind": "param"})
    return segments


FANXIU_PACKET_STEM_ORDER = {
    "SelfClubInfo": 10,
    "SelfClubRoleInfo": 12,
    "SelfClubUnionInfo": 14,
    "ClubInfo": 16,
    "ClubMemberList": 20,
    "ClubNoticeList": 30,
    "ClubGoalActInfo": 40,
    "ClubChargeActInfo": 42,
    "ClubRenameActInfo": 44,
    "ClubTemplateSync": 50,
    "SearchClub": 60,
    "CrossUnionRedDotInfo": 70,
}

FANXIU_PACKET_TOKEN_ORDER = [
    ("Self", 10),
    ("Base", 12),
    ("Info", 14),
    ("List", 20),
    ("Sync", 30),
    ("Search", 35),
    ("Show", 40),
    ("Get", 45),
    ("Set", 50),
    ("Update", 55),
    ("Use", 60),
    ("Collect", 62),
    ("Cost", 64),
    ("Reward", 66),
    ("Result", 68),
    ("Notice", 80),
]


def _fanxiu_packet_stem(name: str) -> str:
    return re.sub(r"^(CM|SM)_", "", str(name or "").strip())


def _fanxiu_packet_direction_order(name: str) -> int:
    if str(name or "").startswith("CM_"):
        return 0
    if str(name or "").startswith("SM_"):
        return 1
    return 2


def _fanxiu_protocol_business_order(name: str, category: str = "") -> tuple[Any, ...]:
    packet_name = str(name or "")
    stem = _fanxiu_packet_stem(packet_name)
    if stem in FANXIU_PACKET_STEM_ORDER:
        order = FANXIU_PACKET_STEM_ORDER[stem]
    elif category == "宗门/联盟" and "CrossUnion" in stem:
        order = 110
    elif category == "宗门/联盟" and "UnionVeins" in stem:
        order = 120
    else:
        order = 100
        for token, token_order in FANXIU_PACKET_TOKEN_ORDER:
            if token in stem:
                order = min(order, token_order)
    return (
        order,
        stem,
        _fanxiu_packet_direction_order(packet_name),
        packet_name,
    )


def _trim_value(value: Any, *, max_items: int = 8, preserve_item_types: set[str] | None = None) -> Any:
    preserve_item_types = preserve_item_types or {"MailVo", "RewardItem"}
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if key == "items" and isinstance(item, list) and len(item) > max_items:
                if str(value.get("_type") or "") in preserve_item_types:
                    output[key] = [_trim_value(x, max_items=max_items, preserve_item_types=preserve_item_types) for x in item]
                else:
                    output[key] = [_trim_value(x, max_items=max_items, preserve_item_types=preserve_item_types) for x in item[:max_items]]
                    output["_truncated_items"] = len(item) - max_items
            else:
                output[key] = _trim_value(item, max_items=max_items, preserve_item_types=preserve_item_types)
        return output
    if isinstance(value, list):
        return [_trim_value(x, max_items=max_items, preserve_item_types=preserve_item_types) for x in value[:max_items]]
    if isinstance(value, float):
        return round(value, 4)
    return value


def _decode_lusuo_frames_tolerant(data: bytes, schema: LuaPacketSchemaIndex) -> tuple[list[dict[str, Any]], list[str]]:
    frames: list[dict[str, Any]] = []
    warnings: list[str] = []
    pos = 0
    total = len(data)
    while pos + 4 <= total:
        offset = pos
        length = int.from_bytes(data[pos : pos + 4], "big")
        pos += 4
        if length < 0:
            warnings.append(f"offset {offset}: invalid frame length {length}")
            break
        if pos + length > total:
            warnings.append(f"offset {offset}: incomplete frame length={length}, left={total - pos}")
            break
        body = data[pos : pos + length]
        pos += length
        try:
            reader = VarintBinaryReader(body)
            sn = reader.read_int()
            packet_id = reader.read_int()
            payload = body[reader.pos :]
            item: dict[str, Any] = {
                "offset": offset,
                "frame_len": length,
                "sn": sn,
                "pro_id": packet_id,
                "name": schema.protocol_names.get(packet_id),
                "payload_len": len(payload),
            }
            try:
                item.update(schema.decode_packet_payload(packet_id, payload))
            except Exception as exc:
                item["decode_error"] = str(exc)
                warnings.append(
                    f"offset {offset}: payload decode failed for {packet_id} "
                    f"{item.get('name') or ''}: {exc}"
                )
            frames.append(item)
        except PacketDecodeError as exc:
            warnings.append(f"offset {offset}: frame header decode failed: {exc}")
        except Exception as exc:
            warnings.append(f"offset {offset}: frame decode failed: {exc}")
    if pos < total and total - pos < 4:
        warnings.append(f"offset {pos}: trailing {total - pos} byte(s) ignored")
    return frames, warnings


def _patch_fanxiu_schema_long_list(schema: LuaPacketSchemaIndex) -> LuaPacketSchemaIndex:
    i18n_num = schema.by_name.get("I18nParam2Num")
    if i18n_num is not None:
        i18n_num.ops = [
            (kind, field, "Double" if kind == "primitive" and field == "value" and arg == "Int" else arg)
            for kind, field, arg in i18n_num.ops
        ]
    original_read_list = schema._read_list

    def read_list_with_raw_long_fallback(self: LuaPacketSchemaIndex, reader: VarintBinaryReader, write_method: str | None = None, depth: int = 0) -> Any:
        if write_method != "LongList":
            return original_read_list(reader, write_method=write_method, depth=depth)
        start = reader.pos
        try:
            return original_read_list(reader, write_method=write_method, depth=depth)
        except Exception:
            reader.pos = start
            count = reader.read_int()
            if count < -1:
                count = reader.read_int()
            if count <= 0:
                return []
            values = [reader.read_long() for _ in range(count)]
            return {"_count": count, "_type_id": -15, "_wire": "rawLongList", "items": values}

    schema._read_list = MethodType(read_list_with_raw_long_fallback, schema)
    return schema


def decode_fanxiu_tcp_pcap(
    pcap: str | Path,
    *,
    stream: int = 34,
    server_host: str = DEFAULT_FANXIU_SERVER_HOST,
    export_root: str | Path | None = None,
    text_assets: str | Path = DEFAULT_TEXT_ASSETS,
    output_path: str | Path | None = None,
    persist: bool = True,
    data_dir: str | Path | None = None,
    sync_after_decode: bool = True,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    pcap_path = _resolve_export_child(export_root, pcap)
    text_assets_path = _resolve_fanxiu_message_text_assets(export_root, text_assets)
    if not text_assets_path.is_dir():
        raise FileNotFoundError(f"凡修协议 text_assets 不存在，无法解码抓包：{text_assets_path}")
    pcap_digest = _sha256_file(pcap_path)
    stream_candidates = list_tcp_streams_with_tshark(pcap_path, host=server_host)
    if stream < 0:
        stream = int(stream_candidates[0]["stream"]) if stream_candidates else 0
    schema = _patch_fanxiu_schema_long_list(LuaPacketSchemaIndex(text_assets_path))
    if not schema.protocol_names:
        raise RuntimeError(f"凡修协议表为空，无法解码抓包：{text_assets_path}")
    c2s_payload, s2c_payload = extract_tcp_stream_payloads_with_tshark(
        pcap_path,
        stream,
        server_host=server_host,
    )

    c2s_frames, c2s_decode_warnings = _decode_lusuo_frames_tolerant(c2s_payload, schema)
    s2c_frames, s2c_decode_warnings = _decode_lusuo_frames_tolerant(s2c_payload, schema)
    for item in c2s_frames:
        item["direction"] = "c2s"
    for item in s2c_frames:
        item["direction"] = "s2c"
    frames = [_trim_value(item) for item in c2s_frames + s2c_frames]

    result = {
        "export_root": str(root),
        "pcap": str(pcap_path),
        "stream": stream,
        "server_host": server_host,
        "text_assets": str(text_assets_path),
        "capture_sha256": pcap_digest,
        "stream_candidates": stream_candidates[:20],
        "summary": {
            "c2s_bytes": len(c2s_payload),
            "s2c_bytes": len(s2c_payload),
            "c2s_frames": len(c2s_frames),
            "s2c_frames": len(s2c_frames),
            "c2s_protocols": summarize_decoded_frames(c2s_frames),
            "s2c_protocols": summarize_decoded_frames(s2c_frames),
            "decode_warnings": {
                "c2s": c2s_decode_warnings[:20],
                "s2c": s2c_decode_warnings[:20],
            },
        },
        "frames": frames,
    }

    if output_path is None:
        if persist:
            output = _record_dir_for(pcap_path, pcap_digest, stream, data_dir=data_dir) / "decoded.json"
        else:
            output = pcap_path.with_suffix(".decoded.json")
    else:
        output = _resolve_export_child(export_root, output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["output_path"] = str(output)

    if persist:
        record_dir = output.parent
        stored_pcap = record_dir / pcap_path.name
        if not stored_pcap.is_file() or stored_pcap.stat().st_size != pcap_path.stat().st_size:
            shutil.copy2(pcap_path, stored_pcap)
        meta = {
            "record_id": record_dir.name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source_pcap": str(pcap_path),
            "stored_pcap": str(stored_pcap),
            "decoded_path": str(output),
            "pcap_name": pcap_path.name,
            "pcap_size": pcap_path.stat().st_size,
            "pcap_modified_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(pcap_path.stat().st_mtime)),
            "capture_sha256": pcap_digest,
            "stream": stream,
            "server_host": server_host,
            "text_assets": str(text_assets_path),
            "summary": result["summary"],
        }
        (record_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        result.update(
            {
                "record_id": record_dir.name,
                "record_dir": str(record_dir),
                "created_at": meta["created_at"],
                "pcap_name": meta["pcap_name"],
                "pcap_modified_at": meta["pcap_modified_at"],
                "stored_pcap": str(stored_pcap),
                "stored_decoded_path": str(output),
                "meta_path": str(record_dir / "meta.json"),
            }
        )
        try:
            from backend.core.fanxiu.packet.decoded_store import persist_fanxiu_packet_decoded_result

            result["decoded_db_sync"] = persist_fanxiu_packet_decoded_result(result)
        except Exception as exc:
            result["decoded_db_sync"] = {
                "created": 0,
                "updated": 0,
                "skipped_invalid": 0,
                "skipped_duplicate": 0,
                "error": str(exc),
            }
        result["retention"] = prune_fanxiu_tcp_storage(
            data_dir=data_dir,
            preserve_paths={pcap_path, record_dir, stored_pcap, output},
        )
        if sync_after_decode:
            _sync_fanxiu_packet_runtime_insights_after_decode(result, data_dir=data_dir, export_root=export_root)
    return result


def _sync_fanxiu_packet_runtime_insights_after_decode(
    result: dict[str, Any],
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> None:
    try:
        from backend.core.fanxiu.packet.insights import sync_fanxiu_packet_business_for_decode_result

        sync_fanxiu_packet_business_for_decode_result(
            result,
            data_dir=data_dir,
            export_root=export_root,
        )
    except Exception:
        return


def list_fanxiu_tcp_records(
    *,
    data_dir: str | Path | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    root = resolve_fanxiu_tcp_store_root(data_dir)
    items: list[dict[str, Any]] = []
    if root.is_dir():
        meta_paths = sorted(root.glob("*/meta.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        for meta_path in meta_paths[: max(1, min(limit, 500))]:
            meta = _load_json_file(meta_path) or {}
            record_dir = meta_path.parent
            decoded_path = Path(str(meta.get("decoded_path") or record_dir / "decoded.json"))
            stored_pcap_text = str(meta.get("stored_pcap") or "")
            stored_pcap = Path(stored_pcap_text) if stored_pcap_text else None
            items.append(
                {
                    "record_id": meta.get("record_id") or record_dir.name,
                    "record_dir": str(record_dir),
                    "pcap_name": meta.get("pcap_name") or (stored_pcap.name if stored_pcap else ""),
                    "source_pcap": meta.get("source_pcap") or "",
                    "stored_pcap": str(stored_pcap) if stored_pcap else "",
                    "decoded_path": str(decoded_path),
                    "decoded": decoded_path.is_file(),
                    "stream": int(meta.get("stream") or 0),
                    "server_host": meta.get("server_host") or "",
                    "capture_sha256": meta.get("capture_sha256") or "",
                    "created_at": meta.get("created_at") or "",
                    "summary": meta.get("summary") or {},
                }
            )
    return {
        "store_root": str(root),
        "items": items,
    }


def _normalize_fanxiu_worldline_activity_item(item: dict[str, Any], *, export_root: str | Path | None = None) -> dict[str, Any] | None:
    source = item.get("_super") if isinstance(item.get("_super"), dict) else item
    if not isinstance(source, dict):
        return None
    activity_id = source.get("activityId")
    activity_name = str(item.get("name") or "").strip() or _fanxiu_config_name(export_root, "Activity", activity_id)

    def _server_ids(value: Any) -> list[int]:
        if isinstance(value, dict):
            raw_items = value.get("items") or []
        elif isinstance(value, list):
            raw_items = value
        else:
            raw_items = []
        rows: list[int] = []
        for row in raw_items:
            try:
                rows.append(int(row))
            except (TypeError, ValueError):
                pass
        return rows

    row_id = source.get("id")
    start_time = source.get("startTime")
    end_time = source.get("endTime")
    close_panel_time = source.get("closePanelTime")
    unique_key = "|".join(str(value or "") for value in (row_id, activity_id, start_time, end_time, close_panel_time))
    server_ids = _server_ids(source.get("serverIds"))
    return {
        "key": unique_key,
        "class": item.get("class") or item.get("_class") or "",
        "bean_id": item.get("bean_id") or item.get("_bean_id") or 0,
        "id": row_id,
        "activityId": activity_id,
        "name": activity_name,
        "activityType": source.get("activityType"),
        "state": source.get("state"),
        "prepareEndTime": source.get("prepareEndTime"),
        "prepareEndTimeText": _format_fanxiu_ms(source.get("prepareEndTime")),
        "startTime": start_time,
        "startTimeText": _format_fanxiu_ms(start_time),
        "endTime": end_time,
        "endTimeText": _format_fanxiu_ms(end_time),
        "closePanelTime": close_panel_time,
        "closePanelTimeText": _format_fanxiu_ms(close_panel_time),
        "daoNian": source.get("daoNian"),
        "scheduleId": source.get("scheduleId"),
        "row": source.get("row"),
        "loopDay": source.get("loopDay"),
        "avgWorldLevel": source.get("avgWorldLevel"),
        "crossGroup": source.get("crossGroup"),
        "serverIds": server_ids,
        "serverCount": len(server_ids),
    }


def _fanxiu_worldline_items_from_decoded(data: dict[str, Any], *, export_root: str | Path | None = None) -> list[dict[str, Any]]:
    frames = data.get("frames") or []
    for frame in frames:
        if not isinstance(frame, dict) or int(frame.get("pro_id") or 0) != 51006:
            continue
        parsed = frame.get("parsed") or {}
        if not isinstance(parsed, dict):
            continue
        activity_vos = parsed.get("activityVOS") or {}
        raw_items = activity_vos.get("items") if isinstance(activity_vos, dict) else []
        rows = [
            normalized
            for raw in raw_items or []
            if isinstance(raw, dict)
            for normalized in [_normalize_fanxiu_worldline_activity_item(raw, export_root=export_root)]
            if normalized
        ]
        if rows:
            return rows
    return []


def list_fanxiu_worldline_activity_schedule_snapshots(
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    live_dir = resolve_fanxiu_tcp_live_capture_dir(data_dir)
    candidates: list[dict[str, Any]] = []

    if live_dir.is_dir():
        for path in sorted(live_dir.glob("*_worldline_activity.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            data = _load_json_file(path) or {}
            raw_items = data.get("items") or []
            items = [
                normalized
                for raw in raw_items
                if isinstance(raw, dict)
                for normalized in [_normalize_fanxiu_worldline_activity_item(raw, export_root=export_root)]
                if normalized
            ]
            if items:
                candidates.append(
                    {
                        "source_kind": "worldline_activity_json",
                        "source_path": str(path),
                        "source_mtime": path.stat().st_mtime,
                        "created_at": data.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime)),
                        "pcap": data.get("pcap") or "",
                        "stream": int(data.get("stream") or 0),
                        "server_host": data.get("server_host") or "",
                        "protocol": data.get("protocol") or "SM_WorldLineActivitySync",
                        "pro_id": int(data.get("pro_id") or 51006),
                        "openServerTime": data.get("openServerTime"),
                        "openServerTimeText": data.get("openServerTimeText") or _format_fanxiu_ms(data.get("openServerTime")),
                        "decode_warnings": data.get("decode_warnings") or [],
                        "items": items,
                    }
                )

    for source in _iter_fanxiu_tcp_decoded_sources(data_dir):
        decoded_path = Path(str(source.get("decoded_path") or ""))
        data = _load_json_file(decoded_path) or {}
        items = _fanxiu_worldline_items_from_decoded(data, export_root=export_root)
        if not items:
            continue
        stat = decoded_path.stat()
        candidates.append(
            {
                "source_kind": source.get("source_kind") or "decoded",
                "source_path": str(decoded_path),
                "source_mtime": stat.st_mtime,
                "created_at": source.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                "pcap": data.get("pcap") or "",
                "stream": int(data.get("stream") or 0),
                "server_host": data.get("server_host") or "",
                "protocol": "SM_WorldLineActivitySync",
                "pro_id": 51006,
                "openServerTime": "",
                "openServerTimeText": "",
                "decode_warnings": ((data.get("summary") or {}).get("decode_warnings") or {}).get("s2c") or [],
                "items": items,
            }
        )

    candidates.sort(key=lambda item: float(item.get("source_mtime") or 0), reverse=True)
    for candidate in candidates:
        candidate["count"] = len(candidate.get("items") or [])
    return candidates


def get_latest_fanxiu_worldline_activity_schedule(
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    candidates = list_fanxiu_worldline_activity_schedule_snapshots(data_dir=data_dir, export_root=export_root)
    if not candidates:
        return {
            "available": False,
            "source_kind": "",
            "source_path": "",
            "created_at": "",
            "pcap": "",
            "stream": 0,
            "server_host": "",
            "protocol": "SM_WorldLineActivitySync",
            "pro_id": 51006,
            "openServerTime": "",
            "openServerTimeText": "",
            "count": 0,
            "decode_warnings": [],
            "items": [],
        }
    latest = candidates[0]
    latest["available"] = True
    latest.pop("source_mtime", None)
    return latest


def _iter_fanxiu_tcp_decoded_sources(data_dir: str | Path | None = None) -> list[dict[str, Any]]:
    root = resolve_fanxiu_tcp_store_root(data_dir)
    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()

    record_rows: list[dict[str, Any]] = []
    if root.is_dir():
        for meta_path in sorted(root.glob("*/meta.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            meta = _load_json_file(meta_path) or {}
            record_dir = meta_path.parent
            decoded_path = Path(str(meta.get("decoded_path") or record_dir / "decoded.json"))
            stored_pcap_text = str(meta.get("stored_pcap") or "")
            stored_pcap = Path(stored_pcap_text) if stored_pcap_text else None
            record_rows.append(
                {
                    "record_id": meta.get("record_id") or record_dir.name,
                    "decoded_path": str(decoded_path),
                    "source_pcap": meta.get("source_pcap") or "",
                    "stored_pcap": str(stored_pcap) if stored_pcap else "",
                    "stream": int(meta.get("stream") or 0),
                    "capture_sha256": meta.get("capture_sha256") or "",
                    "pcap_name": meta.get("pcap_name") or (stored_pcap.name if stored_pcap else ""),
                    "created_at": meta.get("created_at") or "",
                }
            )

    for record in record_rows:
        decoded_path = Path(str(record.get("decoded_path") or ""))
        if not decoded_path.is_file():
            continue
        key = (
            str(record.get("source_pcap") or record.get("stored_pcap") or ""),
            int(record.get("stream") or 0),
            str(record.get("capture_sha256") or ""),
        )
        seen.add(key)
        sources.append(
            {
                "decoded_path": decoded_path,
                "record_id": record.get("record_id") or "",
                "pcap_name": record.get("pcap_name") or "",
                "created_at": record.get("created_at") or "",
                "source_kind": "record",
                "source_pcap": record.get("source_pcap") or "",
                "stored_pcap": record.get("stored_pcap") or "",
                "stream": int(record.get("stream") or 0),
            }
        )

    live_dir = resolve_fanxiu_tcp_live_capture_dir(data_dir)
    if live_dir.is_dir():
        for decoded_path in sorted(live_dir.glob("*.decoded.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            data = _load_json_file(decoded_path) or {}
            key = (
                str(data.get("pcap") or ""),
                int(data.get("stream") or 0),
                str(data.get("capture_sha256") or ""),
            )
            if key in seen:
                continue
            stat = decoded_path.stat()
            sources.append(
                {
                    "decoded_path": decoded_path,
                    "record_id": data.get("record_id") or "",
                    "pcap_name": Path(str(data.get("pcap") or decoded_path.name)).name,
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                    "source_kind": "live",
                    "source_pcap": str(data.get("pcap") or ""),
                    "stored_pcap": "",
                    "stream": int(data.get("stream") or 0),
                }
            )
    return sources


def _build_fanxiu_tcp_entries(data_dir: str, export_root: str | Path | None = None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    skip_names = {"CM_SyncTime", "SM_SyncTime"}
    for source in _iter_fanxiu_tcp_decoded_sources(data_dir):
        decoded_path = source["decoded_path"]
        data = _load_json_file(decoded_path) or {}
        frames = data.get("frames") or []
        decoded_at = source.get("created_at") or time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(decoded_path.stat().st_mtime),
        )
        record_id = source.get("record_id") or data.get("record_id") or ""
        pcap_name = source.get("pcap_name") or Path(str(data.get("pcap") or decoded_path.name)).name
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict):
                continue
            parsed = frame.get("parsed")
            name = str(frame.get("name") or frame.get("pro_id") or "")
            if name in skip_names:
                continue
            if not isinstance(parsed, dict):
                if not name:
                    continue
                parsed = {"_class": name, "_parse_error": frame.get("parse_error") or frame.get("decode_error") or ""}
            protocol_category = describe_fanxiu_tcp_protocol_category(name)
            protocol_meaning = describe_fanxiu_tcp_protocol_meaning(
                name,
                category=protocol_category["category"],
                parsed=parsed if isinstance(parsed, dict) else None,
            )
            entry_id = "|".join(
                [
                    str(record_id or decoded_path),
                    str(frame.get("direction") or ""),
                    str(frame.get("offset") or index),
                    str(frame.get("pro_id") or ""),
                    str(frame.get("sn") or ""),
                ]
            )
            entries.append(
                {
                    "id": entry_id,
                    "decoded_at": decoded_at,
                    "record_id": record_id,
                    "pcap_name": pcap_name,
                    "source_kind": source.get("source_kind") or "",
                    "source_path": str(decoded_path),
                    "source_pcap": source.get("source_pcap") or "",
                    "stored_pcap": source.get("stored_pcap") or "",
                    "stream": int(source.get("stream") or 0),
                    "direction": frame.get("direction") or "",
                    "name": name,
                    "category": protocol_category["category"],
                    "meaning": protocol_category["meaning"],
                    "protocol_meaning": protocol_meaning,
                    "pro_id": int(frame.get("pro_id") or 0),
                    "sn": int(frame.get("sn") or 0),
                    "frame_index": index,
                    "display_text": _describe_fanxiu_tcp_business_entry(parsed, export_root=export_root),
                    "display_segments": _describe_fanxiu_tcp_business_entry_segments(parsed, export_root=export_root),
                    "content": parsed,
                }
            )
    return entries


def list_fanxiu_tcp_business_entries(
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
    category: str = "",
    protocol: str = "",
    hidden_protocols: list[str] | set[str] | tuple[str, ...] | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    requested_category = str(category or "").strip()
    requested_protocol = str(protocol or "").strip()
    hidden_protocol_set = {str(item).strip() for item in (hidden_protocols or []) if str(item).strip()}

    resolved_data = str(data_dir or resolve_fanxiu_tcp_store_root(data_dir))
    resolved_export = str(resolve_fanxiu_export_root(export_root))
    cache_key = f"entries:{resolved_data}:{resolved_export}"
    cached_entries = _ttl_cache_get(cache_key)
    if cached_entries is not None:
        entries = cached_entries
    else:
        entries = _build_fanxiu_tcp_entries(resolved_data, export_root)
        _ttl_cache_set(cache_key, entries)

    entries.sort(key=lambda item: (str(item.get("decoded_at") or ""), int(item.get("frame_index") or 0)), reverse=True)
    category_map: dict[str, dict[str, Any]] = {}
    for item in entries:
        category = str(item.get("category") or "未归类")
        summary = category_map.setdefault(
            category,
            {
                "category": category,
                "meaning": item.get("meaning") or "",
                "count": 0,
                "protocols": set(),
            },
        )
        summary["count"] += 1
        if item.get("name"):
            summary["protocols"].add(str(item["name"]))
    category_summary = [
        {
            "category": item["category"],
            "meaning": item["meaning"],
            "count": item["count"],
            "protocols": sorted(item["protocols"]),
        }
        for item in category_map.values()
    ]
    category_summary.sort(key=lambda item: (-int(item["count"]), str(item["category"])))

    filtered_entries = [
        item for item in entries
        if not requested_category or str(item.get("category") or "") == requested_category
    ]
    if hidden_protocol_set:
        filtered_entries = [
            item for item in filtered_entries
            if str(item.get("name") or "") not in hidden_protocol_set
        ]
    if requested_protocol:
        filtered_entries = [
            item for item in filtered_entries
            if str(item.get("name") or "") == requested_protocol
        ]
    protocol_map: dict[str, dict[str, Any]] = {}
    for item in filtered_entries:
        name = str(item.get("name") or "")
        if not name:
            continue
        summary = protocol_map.setdefault(
            name,
            {
                "name": name,
                "category": item.get("category") or "",
                "meaning": item.get("protocol_meaning") or "",
                "count": 0,
                "samples": [],
            },
        )
        summary["count"] += 1
        if len(summary["samples"]) < 3:
            sample_content = item.get("content") or {}
            summary["samples"].append(
                {
                    "id": item.get("id") or "",
                    "decoded_at": item.get("decoded_at") or "",
                    "direction": item.get("direction") or "",
                    "display_text": item.get("display_text") or "",
                    "display_segments": item.get("display_segments") or [],
                    "content": sample_content,
                    "field_labels": _build_fanxiu_field_value_labels(sample_content, export_root=export_root, packet_name=name) if isinstance(sample_content, dict) else {},
                }
            )
    protocol_summary = sorted(
        protocol_map.values(),
        key=lambda item: _fanxiu_protocol_business_order(str(item.get("name") or ""), str(item.get("category") or "")),
    )

    total = len(filtered_entries)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "category_summary": category_summary,
        "protocol_summary": protocol_summary,
        "items": filtered_entries[start:end],
    }


def list_fanxiu_tcp_captures(
    *,
    capture_dir: str | Path = DEFAULT_TCP_CAPTURE_DIR,
    export_root: str | Path | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    directories = [
        _resolve_export_child(export_root, capture_dir),
        resolve_fanxiu_tcp_live_capture_dir(),
    ]
    records_by_digest: dict[str, dict[str, Any]] = {}
    for record in list_fanxiu_tcp_records(limit=500)["items"]:
        digest = record.get("capture_sha256")
        if digest and digest not in records_by_digest:
            records_by_digest[digest] = record
    items: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for directory in directories:
        if not directory.is_dir():
            continue
        candidates = list(directory.glob("*.pcapng")) + list(directory.glob("*.pcap"))
        for path in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True):
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            stat = path.stat()
            decoded_candidates = [
                path.with_suffix(".decoded.json"),
                path.with_name(f"{path.stem}.codeyun_decoded.json"),
            ]
            decoded_path = next((item for item in decoded_candidates if item.is_file()), None)
            digest = _sha256_file(path)
            record = records_by_digest.get(digest) or {}
            items.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "relative_path": path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path),
                    "size": stat.st_size,
                    "modified_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                    "decoded_path": str(decoded_path) if decoded_path else "",
                    "decoded": decoded_path is not None,
                    "capture_sha256": digest,
                    "record_id": record.get("record_id", ""),
                    "record_dir": record.get("record_dir", ""),
                    "stored_pcap": record.get("stored_pcap", ""),
                    "stored_decoded_path": record.get("decoded_path", ""),
                    "stored": bool(record),
                }
            )
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break
    return {
        "export_root": str(root),
        "capture_dir": str(directories[0]),
        "store_capture_dir": str(directories[1]),
        "items": items,
    }


def decode_latest_fanxiu_tcp_capture(
    *,
    capture_dir: str | Path = DEFAULT_TCP_CAPTURE_DIR,
    stream: int = 34,
    server_host: str = DEFAULT_FANXIU_SERVER_HOST,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    directory = _resolve_export_child(export_root, capture_dir)
    pcaps = sorted(directory.glob("*.pcapng"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not pcaps:
        raise FileNotFoundError(f"未找到 pcapng：{directory}")
    return decode_fanxiu_tcp_pcap(
        pcaps[0],
        stream=stream,
        server_host=server_host,
        export_root=export_root,
    )
