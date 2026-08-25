from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import time
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only module.
    winreg = None

from Crypto.Cipher import AES

try:
    import psutil
except Exception:  # pragma: no cover - psutil is a backend dependency on Windows.
    psutil = None

from pyxllib.autogui.wechat_db import (
    MAX_PAGE_SIZE,
    SQLITE_HEADER,
    WeChatDbError,
    _decode_text_value,
    _image_type_from_header,
    _parse_appmsg,
    _safe_like,
    _table_exists,
    normalize_message_type,
)


WX3_PAGE_SIZE = 4096
WX3_KEY_SIZE = 32
WX3_RESERVE_SIZE = 48
WX3_IV_OFFSET = WX3_PAGE_SIZE - WX3_RESERVE_SIZE
WX3_HMAC_OFFSET = WX3_IV_OFFSET + 16
WX3_HMAC_SIZE = 20
WX3_MARKERS = (b"iphone\x00", b"android\x00", b"ipad\x00")


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _column_or_null(columns: set[str], name: str) -> str:
    return name if name in columns else f"NULL AS {name}"


def _jsonable_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"<blob:{len(value)}>"
    return value


def _jsonable_row(row: sqlite3.Row) -> dict[str, Any]:
    return {key: _jsonable_value(row[key]) for key in row.keys()}


def _is_sqlite_db(path: Path) -> bool:
    try:
        return path.exists() and path.read_bytes()[: len(SQLITE_HEADER)] == SQLITE_HEADER
    except OSError:
        return False


def _display_name(username: str, contacts: dict[str, dict[str, Any]], sessions: dict[str, dict[str, Any]] | None = None) -> str:
    contact = contacts.get(username) or {}
    session = (sessions or {}).get(username) or {}
    value = contact.get("remark") or contact.get("nick_name") or contact.get("alias") or session.get("nick_name")
    return html.unescape(str(value or username))


_BYTES_EXTRA_USERNAME_RE = re.compile(rb"(?<![A-Za-z0-9_@.-])(?:wxid_[A-Za-z0-9_-]{4,}|[A-Za-z][A-Za-z0-9_-]{3,31})(?![A-Za-z0-9_@.-])")
_BYTES_EXTRA_FILE_RE = re.compile(
    r"(?:[A-Za-z]:\\|wxid_[^\\/:*?\"<>|\s\x00]+\\|FileStorage\\)"
    r"[^\x00\r\n<>\"|]{1,360}?\.(?:dat|jpg|jpeg|png|gif|webp|bmp|mp4)",
    re.IGNORECASE,
)
_LEGACY_MEDIA_URL_MAX_BYTES = 16 * 1024 * 1024
_BYTES_EXTRA_USERNAME_SKIP = {
    "aeskey",
    "alnode",
    "bizflag",
    "cdnthumbmd5",
    "cdnthumburl",
    "chatroom",
    "emoji",
    "fromusername",
    "membercount",
    "msgsource",
    "newmsgid",
    "publisher-id",
    "pua",
    "sec_msg_node",
    "session",
    "signature",
    "silence",
    "template",
    "tmp_node",
    "tousername",
}


def _read_varint(data: bytes, offset: int) -> tuple[int, int] | None:
    value = 0
    shift = 0
    while offset < len(data) and shift <= 63:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    return None


def _iter_protobuf_length_payloads(data: bytes, depth: int = 0):
    if depth > 3:
        return
    offset = 0
    while offset < len(data):
        key_result = _read_varint(data, offset)
        if key_result is None:
            return
        key, offset = key_result
        wire_type = key & 0x07
        if wire_type == 0:
            value_result = _read_varint(data, offset)
            if value_result is None:
                return
            _, offset = value_result
        elif wire_type == 1:
            offset += 8
        elif wire_type == 2:
            length_result = _read_varint(data, offset)
            if length_result is None:
                return
            length, offset = length_result
            if length < 0 or offset + length > len(data):
                return
            payload = data[offset : offset + length]
            yield payload
            yield from _iter_protobuf_length_payloads(payload, depth + 1)
            offset += length
        elif wire_type == 5:
            offset += 4
        else:
            return
        if offset > len(data):
            return


def _looks_like_wechat_username(value: str) -> bool:
    if not (3 < len(value) <= 160):
        return False
    if any(char.isspace() for char in value) or "<" in value or ">" in value:
        return False
    lowered = value.lower()
    if lowered in _BYTES_EXTRA_USERNAME_SKIP or re.fullmatch(r"[0-9a-f]{32}", lowered):
        return False
    return bool(
        value.startswith("wxid_")
        or value.endswith("@chatroom")
        or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{3,31}", value)
    )


def _bytes_extra_sender_username(value: Any, known_usernames: set[str] | None = None) -> str | None:
    if isinstance(value, memoryview):
        data = value.tobytes()
    elif isinstance(value, bytearray):
        data = bytes(value)
    elif isinstance(value, bytes):
        data = value
    else:
        return None
    if not data:
        return None

    known_usernames = known_usernames or set()
    candidates: list[str] = []
    for payload in _iter_protobuf_length_payloads(data):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if _looks_like_wechat_username(text):
            candidates.append(text)

    if not candidates:
        for match in _BYTES_EXTRA_USERNAME_RE.finditer(data):
            try:
                text = match.group(0).decode("utf-8")
            except UnicodeDecodeError:
                continue
            if _looks_like_wechat_username(text):
                candidates.append(text)

    for candidate in candidates:
        if candidate in known_usernames:
            return candidate
    for candidate in candidates:
        if candidate.startswith("wxid_") or candidate.endswith("@chatroom"):
            return candidate
    return candidates[0] if candidates else None


def _xml_attrs(text: str, tag: str | None = None) -> dict[str, str]:
    target = text
    if tag:
        match = re.search(rf"<{tag}\b[^>]*>", text, flags=re.IGNORECASE)
        target = match.group(0) if match else ""
    return {
        key.lower(): html.unescape(value)
        for key, value in re.findall(r"([\w:-]+)\s*=\s*['\"]([^'\"]*)['\"]", target)
    }


def _bytes_extra_file_paths(value: Any) -> list[str]:
    if isinstance(value, memoryview):
        data = value.tobytes()
    elif isinstance(value, bytearray):
        data = bytes(value)
    elif isinstance(value, bytes):
        data = value
    else:
        return []
    if not data:
        return []
    text = data.decode("utf-8", errors="ignore")
    paths: list[str] = []
    seen: set[str] = set()
    for match in _BYTES_EXTRA_FILE_RE.finditer(text):
        candidate = match.group(0).strip("\x00\r\n\t ")
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            paths.append(candidate)
    return paths


def _safe_media_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return stem[:140] or "media"


def _decode_legacy_xor_image_dat(source: Path, target_dir: Path, stem: str) -> Path | None:
    try:
        data = source.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    image_type = _image_type_from_header(data[:16])
    plain_data = data
    if not image_type:
        signatures = [
            b"\xff\xd8\xff",
            b"\x89PNG\r\n\x1a\n",
            b"GIF87a",
            b"GIF89a",
            b"BM",
            b"RIFF",
        ]
        for signature in signatures:
            key = data[0] ^ signature[0]
            header = bytes(byte ^ key for byte in data[: max(16, len(signature))])
            if signature == b"RIFF":
                matched = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
            else:
                matched = header.startswith(signature)
            if matched:
                plain_data = bytes(byte ^ key for byte in data)
                image_type = _image_type_from_header(plain_data[:16])
                break
    if not image_type:
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{stem}.{image_type}"
    if not target.exists() or target.stat().st_size != len(plain_data):
        target.write_bytes(plain_data)
    return target


def _download_legacy_media_url(url: str, target_dir: Path, stem: str) -> Path | None:
    if not url.lower().startswith(("http://", "https://")):
        return None
    try:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=8) as response:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _LEGACY_MEDIA_URL_MAX_BYTES:
                    return None
                chunks.append(chunk)
        data = b"".join(chunks)
    except Exception:
        return None
    image_type = _image_type_from_header(data[:16])
    if not image_type:
        path_suffix = Path(unquote(urlparse(url).path)).suffix.lower().lstrip(".")
        image_type = path_suffix if path_suffix in {"jpg", "jpeg", "png", "gif", "webp", "bmp"} else ""
    if not image_type:
        return None
    if image_type == "jpeg":
        image_type = "jpg"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{stem}.{image_type}"
    if not target.exists() or target.stat().st_size != len(data):
        target.write_bytes(data)
    return target


def _pe_pointer_size(path: str | None) -> int:
    if not path:
        return 8
    try:
        with open(path, "rb") as handle:
            if handle.read(2) != b"MZ":
                return 8
            handle.seek(0x3C)
            pe_offset = struct.unpack("<I", handle.read(4))[0]
            handle.seek(pe_offset + 4)
            machine = struct.unpack("<H", handle.read(2))[0]
    except OSError:
        return 8
    return 4 if machine == 0x14C else 8


def _default_wechat_files_roots() -> list[Path]:
    roots: list[Path] = []
    env_path = (os.environ.get("CODEYUN_WECHAT_LEGACY_FILES") or "").strip()
    if env_path:
        roots.append(Path(env_path).expanduser())
    if winreg is not None:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat", 0, winreg.KEY_READ)
            value, _ = winreg.QueryValueEx(key, "FileSavePath")
            winreg.CloseKey(key)
            if value and value != "MyDocument:":
                roots.append(Path(value).expanduser() / "WeChat Files")
        except OSError:
            pass
    profile = os.environ.get("USERPROFILE")
    if profile:
        roots.append(Path(profile) / "Documents" / "WeChat Files")
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = os.fspath(root).lower()
        if key not in seen:
            seen.add(key)
            deduped.append(root)
    return deduped


def _candidate_legacy_account_roots() -> list[Path]:
    env_account = (os.environ.get("CODEYUN_WECHAT_LEGACY_ACCOUNT_ROOT") or "").strip()
    roots = [Path(env_account).expanduser()] if env_account else []
    for files_root in _default_wechat_files_roots():
        if not files_root.exists():
            continue
        for child in files_root.iterdir():
            if child.is_dir() and (child / "Msg" / "MicroMsg.db").exists():
                roots.append(child)
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in sorted(roots, key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
        key = os.fspath(root.resolve() if root.exists() else root).lower()
        if key not in seen:
            seen.add(key)
            deduped.append(root)
    return deduped


def _wechat_processes() -> list[dict[str, Any]]:
    if os.name != "nt" or psutil is None:
        return []
    processes: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = proc.info.get("name") or ""
            if name.lower() != "wechat.exe":
                continue
            exe = proc.info.get("exe")
            processes.append({"pid": int(proc.info["pid"]), "exe": exe, "pointer_size": _pe_pointer_size(exe)})
        except (psutil.Error, OSError, TypeError, ValueError):
            continue
    return processes


class _MemoryBasicInformation64(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", ctypes.c_ulong),
        ("__alignment1", ctypes.c_ulong),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", ctypes.c_ulong),
        ("Protect", ctypes.c_ulong),
        ("Type", ctypes.c_ulong),
        ("__alignment2", ctypes.c_ulong),
    ]


def _read_process_bytes(handle: int, address: int, size: int) -> bytes | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    read_process_memory = kernel32.ReadProcessMemory
    read_process_memory.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    read_process_memory.restype = ctypes.c_int

    buffer = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t()
    ok = read_process_memory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(read))
    if not ok or read.value != size:
        return None
    return bytes(buffer)


def _read_pointer(handle: int, address: int, pointer_size: int) -> int | None:
    data = _read_process_bytes(handle, address, pointer_size)
    if not data:
        return None
    value = int.from_bytes(data, byteorder="little", signed=False)
    return value or None


def _verify_wx3_key(key: bytes, db_path: Path) -> bool:
    if len(key) != WX3_KEY_SIZE:
        return False
    try:
        with db_path.open("rb") as handle:
            first_page = handle.read(WX3_PAGE_SIZE)
    except OSError:
        return False
    if len(first_page) != WX3_PAGE_SIZE:
        return False
    salt = first_page[:16]
    if first_page[: len(SQLITE_HEADER)] == SQLITE_HEADER:
        return False
    derived_key = hashlib.pbkdf2_hmac("sha1", key, salt, 64000, WX3_KEY_SIZE)
    mac_salt = bytes(part ^ 0x3A for part in salt)
    mac_key = hashlib.pbkdf2_hmac("sha1", derived_key, mac_salt, 2, WX3_KEY_SIZE)
    digest = hmac.new(mac_key, first_page[16:WX3_HMAC_OFFSET], hashlib.sha1)
    digest.update(b"\x01\x00\x00\x00")
    return hmac.compare_digest(digest.digest(), first_page[WX3_HMAC_OFFSET : WX3_HMAC_OFFSET + WX3_HMAC_SIZE])


def _wechatwin_ranges(handle: int) -> list[tuple[int, int]]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    virtual_query_ex = kernel32.VirtualQueryEx
    virtual_query_ex.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_MemoryBasicInformation64),
        ctypes.c_size_t,
    ]
    virtual_query_ex.restype = ctypes.c_size_t
    get_mapped_file_name = psapi.GetMappedFileNameW
    get_mapped_file_name.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
    get_mapped_file_name.restype = ctypes.c_ulong

    ranges: list[tuple[int, int]] = []
    address = 0
    mbi = _MemoryBasicInformation64()
    while address < 0x7FFFFFFFFFFF:
        if not virtual_query_ex(handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        region_base = int(mbi.BaseAddress or address)
        region_size = int(mbi.RegionSize or 0)
        name_buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if get_mapped_file_name(handle, ctypes.c_void_p(region_base), name_buffer, wintypes.MAX_PATH):
            if "wechatwin.dll" in name_buffer.value.lower():
                ranges.append((region_base, region_base + region_size))
        address = region_base + max(region_size, 1)
    if not ranges:
        return []
    ranges.sort()
    merged: list[tuple[int, int]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _search_process_markers(
    handle: int,
    *,
    limit: int = 64,
    start_address: int = 0,
    end_address: int = 0x7FFFFFFFFFFF,
) -> list[int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    virtual_query_ex = kernel32.VirtualQueryEx
    virtual_query_ex.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_MemoryBasicInformation64),
        ctypes.c_size_t,
    ]
    virtual_query_ex.restype = ctypes.c_size_t

    mem_commit = 0x1000
    page_noaccess = 0x01
    page_guard = 0x100
    readable_pages = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
    max_region_size = 64 * 1024 * 1024
    chunk_size = 2 * 1024 * 1024
    addresses: list[int] = []
    address = start_address
    mbi = _MemoryBasicInformation64()
    while address < end_address and len(addresses) < limit:
        if not virtual_query_ex(handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        region_base = int(mbi.BaseAddress or address)
        region_size = int(mbi.RegionSize or 0)
        protect = int(mbi.Protect or 0)
        base_protect = protect & ~(page_guard | 0x200 | 0x400)
        if mbi.State == mem_commit and not (protect & page_guard) and base_protect != page_noaccess and base_protect in readable_pages:
            region_offset = max(0, start_address - region_base)
            region_end = min(region_size, end_address - region_base)
            read_size = min(max(0, region_end - region_offset), max_region_size)
            offset = 0
            overlap = max(len(marker) for marker in WX3_MARKERS) - 1
            tail = b""
            while offset < read_size and len(addresses) < limit:
                size = min(chunk_size, read_size - offset)
                data = _read_process_bytes(handle, region_base + region_offset + offset, size)
                if data:
                    search_block = tail + data
                    tail_len = len(tail)
                    for marker in WX3_MARKERS:
                        start = 0
                        while len(addresses) < limit:
                            pos = search_block.find(marker, start)
                            if pos < 0:
                                break
                            absolute = region_base + offset + pos - tail_len
                            absolute = region_base + region_offset + offset + pos - tail_len
                            if absolute >= region_base:
                                addresses.append(absolute)
                            start = pos + 1
                    tail = search_block[-overlap:] if overlap else b""
                offset += size
        address = region_base + max(region_size, 1)
    return sorted(set(addresses))


def _scan_wx3_key(pid: int, db_path: Path, pointer_size: int) -> str | None:
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    open_process.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    process_query_information = 0x0400
    process_vm_read = 0x0010

    handle = open_process(process_query_information | process_vm_read, 0, pid)
    if not handle:
        return None
    try:
        marker_addresses: list[int] = []
        for start, end in _wechatwin_ranges(handle):
            marker_addresses.extend(_search_process_markers(handle, limit=128, start_address=start, end_address=end))
        if not marker_addresses:
            marker_addresses = _search_process_markers(handle, limit=256)
        seen: set[int] = set()
        for marker in sorted(marker_addresses, reverse=True):
            stop = max(0, marker - 2000)
            address = marker
            while address >= stop:
                pointer = _read_pointer(handle, address, pointer_size)
                if pointer and pointer not in seen:
                    seen.add(pointer)
                    key = _read_process_bytes(handle, pointer, WX3_KEY_SIZE)
                    if key and _verify_wx3_key(key, db_path):
                        return key.hex()
                address -= pointer_size
    finally:
        close_handle(handle)
    return None


@dataclass(frozen=True)
class LegacyWeChatLiveInfo:
    account_root: Path
    key_hex: str
    pid: int | None = None
    wxid: str | None = None
    exe: str | None = None


def find_live_legacy_wechat_account() -> LegacyWeChatLiveInfo | None:
    key_hex = (os.environ.get("CODEYUN_WECHAT_LEGACY_DB_KEY") or "").strip()
    processes = _wechat_processes()
    for account_root in _candidate_legacy_account_roots():
        micro_msg = account_root / "Msg" / "MicroMsg.db"
        if key_hex:
            try:
                if _verify_wx3_key(bytes.fromhex(key_hex), micro_msg):
                    return LegacyWeChatLiveInfo(account_root=account_root, key_hex=key_hex, wxid=account_root.name)
            except ValueError:
                pass
        for process in processes:
            found = _scan_wx3_key(process["pid"], micro_msg, int(process["pointer_size"] or 8))
            if found:
                return LegacyWeChatLiveInfo(
                    account_root=account_root,
                    key_hex=found,
                    pid=process["pid"],
                    wxid=account_root.name,
                    exe=process.get("exe"),
                )
    return None


def has_legacy_wechat_live_source() -> bool:
    return bool(_candidate_legacy_account_roots() and (_wechat_processes() or os.environ.get("CODEYUN_WECHAT_LEGACY_DB_KEY")))


def decrypt_wechat_v3_db(in_path: Path, out_path: Path, key_hex: str) -> bool:
    if _is_sqlite_db(in_path):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(in_path, out_path)
        return True
    key = bytes.fromhex(key_hex)
    if not _verify_wx3_key(key, in_path):
        return False
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with in_path.open("rb") as source:
        data = source.read()
    salt = data[:16]
    derived_key = hashlib.pbkdf2_hmac("sha1", key, salt, 64000, WX3_KEY_SIZE)
    with tmp_path.open("wb") as target:
        target.write(SQLITE_HEADER)
        for page_start in range(0, len(data), WX3_PAGE_SIZE):
            page = data[page_start : page_start + WX3_PAGE_SIZE]
            if len(page) != WX3_PAGE_SIZE:
                return False
            payload = page[16 : WX3_PAGE_SIZE] if page_start == 0 else page
            plain = AES.new(derived_key, AES.MODE_CBC, payload[-WX3_RESERVE_SIZE:-32]).decrypt(
                payload[:-WX3_RESERVE_SIZE]
            )
            target.write(plain)
            target.write(payload[-WX3_RESERVE_SIZE:])
    tmp_path.replace(out_path)
    return True


def _iter_decrypted_wx3_wal_frames(db_path: Path, wal_path: Path, key_hex: str):
    try:
        with db_path.open("rb") as handle:
            first_page = handle.read(WX3_PAGE_SIZE)
        wal_data = wal_path.read_bytes()
    except OSError:
        return
    if len(first_page) < WX3_PAGE_SIZE or len(wal_data) < 32:
        return
    try:
        page_size = struct.unpack(">I", wal_data[8:12])[0]
    except struct.error:
        return
    if page_size <= 0 or page_size > 65536:
        return
    key = bytes.fromhex(key_hex)
    derived_key = hashlib.pbkdf2_hmac("sha1", key, first_page[:16], 64000, WX3_KEY_SIZE)
    pos = 32
    frame_index = 0
    while pos + 24 + page_size <= len(wal_data):
        frame_header = wal_data[pos : pos + 24]
        try:
            page_no, db_size = struct.unpack(">II", frame_header[:8])
        except struct.error:
            break
        page = wal_data[pos + 24 : pos + 24 + page_size]
        try:
            payload = page[16:] if page_no == 1 else page
            plain = AES.new(derived_key, AES.MODE_CBC, payload[-WX3_RESERVE_SIZE:-32]).decrypt(
                payload[:-WX3_RESERVE_SIZE]
            )
            data = (SQLITE_HEADER + plain if page_no == 1 else plain) + payload[-WX3_RESERVE_SIZE:]
        except Exception:
            data = b""
        if data:
            yield {"frame_index": frame_index, "page_no": page_no, "db_size": db_size, "data": data}
        frame_index += 1
        pos += 24 + page_size


_XML_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_XML_CONTENT_RE = re.compile(r"<content>(.*?)</content>", re.DOTALL | re.IGNORECASE)
_LOOSE_XML_TITLE_RE = re.compile(r"<title>(.*?)(?:</|/ ype>| appattach|<type>)", re.DOTALL | re.IGNORECASE)
_LOOSE_XML_CONTENT_RE = re.compile(
    r"(?:<content>|content>)(.*?)(?:</content>|/ msgsource| wxid_[A-Za-z0-9_-]{4,}|<msgsource>|$)",
    re.DOTALL | re.IGNORECASE,
)
_MEDIA_PATH_HINT_RE = re.compile(
    r"FileStorage\\MsgAttach\\[A-Za-z0-9]+\\(?:Thumb|Image)\\20\d{2}-\d{2}\\[^\\\x00\r\n<>\"|]+\.(?:dat|jpg|jpeg|png|gif|webp|bmp)",
    re.IGNORECASE,
)


def _clean_wal_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return html.unescape(html.unescape(value)).strip()


def _extract_xml_field(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return _clean_wal_text(match.group(1)) if match else ""


def _extract_chat_wal_fragments_from_text(
    text: str,
    *,
    chat_username: str,
    q: str | None,
    source_db: str,
    frame_index: int,
    page_no: int,
    contacts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    needle = q.strip() if q else ""
    for match in re.finditer(re.escape(chat_username), text):
        context = text[max(0, match.start() - 500) : match.start() + 3200]
        cleaned_context = _clean_wal_text(context)
        media_match = _MEDIA_PATH_HINT_RE.search(context)
        title = _extract_xml_field(_XML_TITLE_RE, cleaned_context) or _extract_xml_field(
            _LOOSE_XML_TITLE_RE, cleaned_context
        )
        content = _extract_xml_field(_XML_CONTENT_RE, cleaned_context) or _extract_xml_field(
            _LOOSE_XML_CONTENT_RE, cleaned_context
        )
        message_text = title or content
        if title and content and content not in title:
            message_text = f"{title}\n引用：{content}"
        if not message_text:
            body_match = re.search(
                re.escape(chat_username) + r"(?P<body>.*?)(?:<msgsource>|wxid_[A-Za-z0-9_-]{4,}|\Z)",
                cleaned_context,
                flags=re.DOTALL,
            )
            message_text = _clean_wal_text(body_match.group("body")) if body_match else cleaned_context
        if needle and needle not in message_text and needle not in cleaned_context:
            continue
        sender_username = None
        sender_candidates = re.findall(r"(wxid_[A-Za-z0-9_-]{4,}|[A-Za-z][A-Za-z0-9_-]{3,31})", cleaned_context)
        for candidate in sender_candidates:
            if candidate in contacts:
                sender_username = candidate
                break
        if sender_username is None and sender_candidates:
            sender_username = sender_candidates[0]
        fragments.append(
            {
                "source_db": source_db,
                "frame_index": frame_index,
                "page_no": page_no,
                "source": chat_username,
                "sender_username_hint": sender_username,
                "sender_name_hint": _display_name(sender_username, contacts) if sender_username else None,
                "message_text": message_text[:2000],
                "context_text": cleaned_context[:3000],
                "media_path_hint": media_match.group(0) if media_match else None,
            }
        )
    return fragments


class WeChatLegacyDbStorage:
    """Query decrypted WeChat 3.x databases while exposing the 4.x page schema."""

    def __init__(self, root: os.PathLike[str] | str):
        self.root = Path(root)
        self._chat_index_cache: list[dict[str, Any]] | None = None

    @property
    def micro_msg_path(self) -> Path:
        return self.root / "Msg" / "MicroMsg.db"

    @property
    def multi_path(self) -> Path:
        return self.root / "Msg" / "Multi"

    @property
    def multi_search_chat_msg_path(self) -> Path:
        return self.root / "Msg" / "MultiSearchChatMsg.db"

    def _message_db_paths(self) -> list[Path]:
        if not self.multi_path.exists():
            return []
        return sorted(self.multi_path.glob("MSG*.db"), key=self._message_db_index)

    def _message_db_index(self, path: Path) -> int:
        match = re.search(r"MSG(\d+)\.db$", path.name, flags=re.IGNORECASE)
        return int(match.group(1)) if match else 0

    def _live_db_path_for_decrypted_message_db(self, live: LegacyWeChatLiveInfo, db_path: Path) -> Path:
        return live.account_root / "Msg" / "Multi" / db_path.name

    def _self_username(self) -> str | None:
        value = (os.environ.get("CODEYUN_WECHAT_LEGACY_SELF_USERNAME") or "").strip()
        if value:
            return value
        raw_snapshot = self.root.parent / "raw_snapshot" / "wechat_files"
        if raw_snapshot.exists():
            candidates = [path.name for path in raw_snapshot.iterdir() if path.is_dir() and path.name.startswith("wxid_")]
            if candidates:
                return sorted(candidates)[-1]
        return None

    def status(self) -> dict[str, Any]:
        message_dbs = self._message_db_paths()
        databases = {
            "micro_msg": _is_sqlite_db(self.micro_msg_path),
            "message": bool(message_dbs),
            "multi_search_chat_msg": _is_sqlite_db(self.multi_search_chat_msg_path),
        }
        for path in message_dbs:
            databases[f"message_{self._message_db_index(path)}"] = _is_sqlite_db(path)
        return {
            "db_storage_path": os.fspath(self.root),
            "source_format": "wechat_3",
            "exists": self.root.exists(),
            "ready": databases["micro_msg"] and bool(message_dbs),
            "databases": databases,
            "self_username": self._self_username(),
        }

    def _raw_snapshot_root(self, account_root: Path) -> Path:
        return self.root.parent / "raw_snapshot" / "wechat_files" / account_root.name

    def _snapshot_account_name(self) -> str | None:
        raw_snapshot = self.root.parent / "raw_snapshot" / "wechat_files"
        if not raw_snapshot.exists():
            return None
        candidates = [path.name for path in raw_snapshot.iterdir() if path.is_dir() and path.name.startswith("wxid_")]
        return sorted(candidates)[-1] if candidates else None

    def _media_account_root(self) -> Path | None:
        env_account = (os.environ.get("CODEYUN_WECHAT_LEGACY_ACCOUNT_ROOT") or "").strip()
        if env_account:
            path = Path(env_account).expanduser()
            if path.exists():
                return path
        snapshot_account = self._snapshot_account_name()
        for files_root in _default_wechat_files_roots():
            if snapshot_account:
                candidate = files_root / snapshot_account
                if candidate.exists():
                    return candidate
            if files_root.exists():
                for child in files_root.iterdir():
                    if child.is_dir() and child.name.startswith("wxid_") and (child / "FileStorage").exists():
                        return child
        return None

    def _resource_export_root(self) -> Path:
        return self.root.parent / "exported_media"

    def _resolve_media_source_path(self, raw_path: str, account_root: Path) -> Path | None:
        normalized = raw_path.replace("/", "\\").strip("\x00\r\n\t ")
        if not normalized:
            return None
        path = Path(normalized)
        candidates: list[Path] = []
        if path.is_absolute():
            candidates.append(path)
        if normalized.lower().startswith((account_root.name + "\\").lower()):
            candidates.append(account_root.parent / normalized)
        if normalized.lower().startswith("filestorage\\"):
            candidates.append(account_root / normalized)
        candidates.append(account_root.parent / normalized)
        candidates.append(account_root / normalized)
        for candidate in candidates:
            try:
                if candidate.exists() and candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    def _export_legacy_media_file(
        self,
        source: Path,
        *,
        kind: str,
        stem: str,
        md5_text: str = "",
        original_file_name: str | None = None,
    ) -> dict[str, Any] | None:
        export_dir = self._resource_export_root() / kind
        safe_stem = _safe_media_stem(stem)
        target: Path | None = None
        decoded_from_dat = False
        if kind == "image" and source.suffix.lower() == ".dat":
            target = _decode_legacy_xor_image_dat(source, export_dir, safe_stem)
            decoded_from_dat = target is not None
        if target is None:
            suffix = source.suffix.lower().lstrip(".")
            if not suffix:
                suffix = "bin"
            target = export_dir / f"{safe_stem}.{suffix}"
            export_dir.mkdir(parents=True, exist_ok=True)
            try:
                if not target.exists() or target.stat().st_size != source.stat().st_size:
                    shutil.copy2(source, target)
            except OSError:
                return None
        try:
            size = int(target.stat().st_size)
        except OSError:
            return None
        return {
            "kind": kind,
            "file_name": target.name,
            "original_file_name": original_file_name or source.name,
            "size": size,
            "source_path": os.fspath(source),
            "stored_path": os.fspath(target),
            "download_name": f"{kind}/{target.name}",
            "md5": md5_text,
            "decoded_from_dat": decoded_from_dat,
        }

    def _download_legacy_media_export(self, url: str, *, kind: str, stem: str, md5_text: str = "") -> dict[str, Any] | None:
        export_dir = self._resource_export_root() / kind
        safe_stem = _safe_media_stem(stem)
        target = next(
            (
                candidate
                for ext in ("jpg", "png", "gif", "webp", "bmp")
                for candidate in [export_dir / f"{safe_stem}.{ext}"]
                if candidate.exists()
            ),
            None,
        )
        if target is None:
            target = _download_legacy_media_url(url, export_dir, safe_stem)
        if not target:
            return None
        try:
            size = int(target.stat().st_size)
        except OSError:
            return None
        return {
            "kind": kind,
            "file_name": target.name,
            "original_file_name": target.name,
            "size": size,
            "source_path": url,
            "stored_path": os.fspath(target),
            "download_name": f"{kind}/{target.name}",
            "md5": md5_text,
            "decoded_from_dat": False,
        }

    def _iter_live_db_files(self, account_root: Path) -> list[Path]:
        msg_root = account_root / "Msg"
        paths = [msg_root / "MicroMsg.db"]
        paths.append(msg_root / "MultiSearchChatMsg.db")
        paths.extend(sorted((msg_root / "Multi").glob("MSG*.db")))
        paths.extend(sorted((msg_root / "Multi").glob("MediaMSG*.db")))
        return [path for path in paths if path.exists()]

    def _copy_live_dbs(self, live: LegacyWeChatLiveInfo) -> dict[str, Any]:
        target_root = self._raw_snapshot_root(live.account_root)
        copied = 0
        unchanged = 0
        errors: list[str] = []
        for source in self._iter_live_db_files(live.account_root):
            rel = source.relative_to(live.account_root)
            target = target_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                source_stat = source.stat()
                target_stat = target.stat() if target.exists() else None
                if (
                    target_stat
                    and target_stat.st_size == source_stat.st_size
                    and int(target_stat.st_mtime) == int(source_stat.st_mtime)
                ):
                    unchanged += 1
                    continue
                shutil.copy2(source, target)
                copied += 1
            except Exception as exc:
                errors.append(f"{rel}: {type(exc).__name__}: {exc}")
        return {
            "source": os.fspath(live.account_root),
            "target": os.fspath(target_root),
            "copied": copied,
            "unchanged": unchanged,
            "errors": errors[:20],
            "error_count": len(errors),
        }

    def _decrypt_snapshot_dbs(self, live: LegacyWeChatLiveInfo) -> dict[str, Any]:
        source_root = self._raw_snapshot_root(live.account_root)
        manifest_path = self.root.parent / "legacy_decrypt_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        except Exception:
            manifest = {}
        decrypted = 0
        skipped = 0
        failed: list[str] = []
        next_manifest: dict[str, dict[str, Any]] = {}
        for source in sorted(source_root.rglob("*.db")):
            rel = source.relative_to(source_root)
            rel_key = rel.as_posix()
            target = self.root / rel
            source_stat = source.stat()
            fingerprint = {"size": source_stat.st_size, "mtime": int(source_stat.st_mtime)}
            if manifest.get(rel_key) == fingerprint and _is_sqlite_db(target):
                skipped += 1
                next_manifest[rel_key] = fingerprint
                continue
            try:
                if decrypt_wechat_v3_db(source, target, live.key_hex):
                    decrypted += 1
                    next_manifest[rel_key] = fingerprint
                else:
                    failed.append(f"{rel}: decrypt-failed")
            except Exception as exc:
                failed.append(f"{rel}: {type(exc).__name__}: {exc}")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(next_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "source": os.fspath(source_root),
            "target": os.fspath(self.root),
            "decrypted": decrypted,
            "skipped": skipped,
            "failed": failed[:20],
            "failed_count": len(failed),
        }

    def sync_from_live(self, *, export_media: bool = True) -> dict[str, Any]:
        started_at = time.time()
        live = find_live_legacy_wechat_account()
        if not live:
            raise WeChatDbError("No running WeChat 3.x account or valid legacy key was found")
        copy_result = self._copy_live_dbs(live)
        decrypt_result = self._decrypt_snapshot_dbs(live)
        media_result = {"scanned_chats": 0, "exported_files": 0, "new_files": 0, "errors": [], "error_count": 0}
        return {
            "live_account_root": os.fspath(live.account_root),
            "elapsed_seconds": round(time.time() - started_at, 3),
            "copy": copy_result,
            "decrypt": decrypt_result,
            "media": media_result if export_media else None,
        }

    def _contact_map(self, *, include_avatar: bool = False) -> dict[str, dict[str, Any]]:
        if not self.micro_msg_path.exists():
            return {}
        conn = _connect_readonly(self.micro_msg_path)
        try:
            if not _table_exists(conn, "Contact"):
                return {}
            columns = _table_columns(conn, "Contact")
            contacts: dict[str, dict[str, Any]] = {}
            rows = conn.execute(
                f"""
                SELECT UserName, Alias, Remark, NickName, Type, VerifyFlag, ChatRoomType,
                       {_column_or_null(columns, "SmallHeadImgUrl")},
                       {_column_or_null(columns, "BigHeadImgUrl")},
                       {_column_or_null(columns, "HeadImgMd5")}
                FROM Contact
                """
            ).fetchall()
            for row in rows:
                username = row["UserName"]
                if username:
                    contacts[username] = {
                        "username": username,
                        "alias": row["Alias"],
                        "remark": row["Remark"],
                        "nick_name": row["NickName"],
                        "type": row["Type"],
                        "verify_flag": row["VerifyFlag"],
                        "chat_room_type": row["ChatRoomType"],
                        "small_head_img_url": row["SmallHeadImgUrl"],
                        "big_head_img_url": row["BigHeadImgUrl"],
                        "head_img_md5": row["HeadImgMd5"],
                        "avatar_data_url": None,
                    }
            if include_avatar:
                avatar_map = self._avatar_data_urls(set(contacts))
                for username, contact in contacts.items():
                    contact["avatar_data_url"] = avatar_map.get(username)
            return contacts
        finally:
            conn.close()

    def _avatar_data_urls(self, usernames: set[str] | None = None) -> dict[str, str]:
        if not self.micro_msg_path.exists():
            return {}
        conn = _connect_readonly(self.micro_msg_path)
        try:
            avatars: dict[str, str] = {}
            params: list[Any] = []
            where_sql = ""
            if usernames:
                placeholders = ",".join("?" for _ in usernames)
                where_sql = f" WHERE UserName IN ({placeholders})"
                params = sorted(usernames)
            if _table_exists(conn, "Contact"):
                columns = _table_columns(conn, "Contact")
                if {"SmallHeadImgUrl", "BigHeadImgUrl"} & columns:
                    rows = conn.execute(
                        f"""
                        SELECT UserName,
                               {_column_or_null(columns, "SmallHeadImgUrl")},
                               {_column_or_null(columns, "BigHeadImgUrl")}
                        FROM Contact
                        {where_sql}
                        """,
                        params,
                    ).fetchall()
                    for row in rows:
                        url = row["SmallHeadImgUrl"] or row["BigHeadImgUrl"]
                        if row["UserName"] and url:
                            avatars[row["UserName"]] = url

            if _table_exists(conn, "ContactHeadImgUrl"):
                columns = _table_columns(conn, "ContactHeadImgUrl")
                if "usrName" in columns:
                    params = []
                    where_sql = ""
                    if usernames:
                        placeholders = ",".join("?" for _ in usernames)
                        where_sql = f" WHERE usrName IN ({placeholders})"
                        params = sorted(usernames)
                    rows = conn.execute(
                        f"""
                        SELECT usrName,
                               {_column_or_null(columns, "smallHeadImgUrl")},
                               {_column_or_null(columns, "bigHeadImgUrl")}
                        FROM ContactHeadImgUrl
                        {where_sql}
                        """,
                        params,
                    ).fetchall()
                    for row in rows:
                        url = row["smallHeadImgUrl"] or row["bigHeadImgUrl"]
                        if row["usrName"] and url:
                            avatars[row["usrName"]] = url
            return avatars
        finally:
            conn.close()

    def _chatroom_member_map(self) -> dict[str, list[str]]:
        if not self.micro_msg_path.exists():
            return {}
        conn = _connect_readonly(self.micro_msg_path)
        try:
            if not _table_exists(conn, "ChatRoom"):
                return {}
            columns = _table_columns(conn, "ChatRoom")
            if not {"ChatRoomName", "UserNameList"}.issubset(columns):
                return {}
            rows = conn.execute("SELECT ChatRoomName, UserNameList FROM ChatRoom").fetchall()
            rooms: dict[str, list[str]] = {}
            for row in rows:
                username = row["ChatRoomName"]
                raw_members = row["UserNameList"] or ""
                members = [item for item in re.split(r"\^G|\x07", raw_members) if item]
                if username and members:
                    rooms[username] = members
            return rooms
        finally:
            conn.close()

    def _session_map(self) -> dict[str, dict[str, Any]]:
        if not self.micro_msg_path.exists():
            return {}
        conn = _connect_readonly(self.micro_msg_path)
        try:
            if not _table_exists(conn, "Session"):
                return {}
            sessions: dict[str, dict[str, Any]] = {}
            rows = conn.execute(
                """
                SELECT strUsrName, strNickName, nUnReadCount, strContent, nMsgType, nTime, nIsSend
                FROM Session
                """
            ).fetchall()
            for row in rows:
                username = row["strUsrName"]
                if username:
                    sessions[username] = {
                        "username": username,
                        "nick_name": row["strNickName"],
                        "unread_count": row["nUnReadCount"],
                        "summary": _decode_text_value(row["strContent"]),
                        "last_msg_type": row["nMsgType"],
                        "last_time": row["nTime"],
                        "is_send": row["nIsSend"],
                    }
            return sessions
        finally:
            conn.close()

    def _chat_stats(self) -> dict[str, dict[str, Any]]:
        stats: dict[str, dict[str, Any]] = {}
        for db_path in self._message_db_paths():
            conn = _connect_readonly(db_path)
            try:
                if not (_table_exists(conn, "MSG") and _table_exists(conn, "Name2ID")):
                    continue
                rows = conn.execute(
                    """
                    SELECT
                        COALESCE(NULLIF(m.StrTalker, ''), n.UsrName) AS username,
                        COUNT(*) AS n,
                        MIN(m.CreateTime) AS first_time,
                        MAX(m.CreateTime) AS last_time
                    FROM MSG m
                    LEFT JOIN Name2ID n ON n.rowid = m.TalkerId
                    GROUP BY COALESCE(NULLIF(m.StrTalker, ''), n.UsrName)
                    """
                ).fetchall()
                for row in rows:
                    username = row["username"]
                    if not username:
                        continue
                    item = stats.setdefault(
                        username,
                        {"message_count": 0, "first_time": None, "last_time": None},
                    )
                    item["message_count"] += int(row["n"] or 0)
                    first_time = row["first_time"]
                    last_time = row["last_time"]
                    if first_time is not None:
                        item["first_time"] = first_time if item["first_time"] is None else min(item["first_time"], first_time)
                    if last_time is not None:
                        item["last_time"] = last_time if item["last_time"] is None else max(item["last_time"], last_time)
            finally:
                conn.close()
        return stats

    def list_chats(
        self,
        limit: int = 500,
        q: str | None = None,
        offset: int = 0,
        folded: bool | None = None,
        include_folded_entry: bool = False,
    ) -> list[dict[str, Any]]:
        chats = list(self._all_chats())
        needle = q.strip().lower() if q else ""
        if needle:
            chats = [
                item
                for item in chats
                if needle
                in " ".join(str(part or "") for part in [item.get("username"), item.get("name"), item.get("summary")]).lower()
            ]
        if folded is not None:
            chats = [item for item in chats if bool(item.get("is_folded")) == folded]
        return chats[offset : offset + limit]

    def _all_chats(self) -> list[dict[str, Any]]:
        if self._chat_index_cache is not None:
            return self._chat_index_cache
        contacts = self._contact_map(include_avatar=True)
        chatroom_members = self._chatroom_member_map()
        sessions = self._session_map()
        stats = self._chat_stats()
        chats: list[dict[str, Any]] = []
        for username, stat in stats.items():
            session = sessions.get(username) or {}
            name = _display_name(username, contacts, sessions)
            last_type = session.get("last_msg_type")
            avatar_data_url = (contacts.get(username) or {}).get("avatar_data_url")
            if not avatar_data_url and username.endswith("@chatroom"):
                avatar_data_url = next(
                    (
                        (contacts.get(member) or {}).get("avatar_data_url")
                        for member in chatroom_members.get(username, [])
                        if (contacts.get(member) or {}).get("avatar_data_url")
                    ),
                    None,
                )
            chats.append(
                {
                    "username": username,
                    "name": name,
                    "table_name": "MSG",
                    "chat_type": "chatroom" if username.endswith("@chatroom") else "contact",
                    "is_folded": False,
                    "is_folded_entry": False,
                    "message_count": stat["message_count"],
                    "first_time": stat["first_time"],
                    "last_time": stat["last_time"] or session.get("last_time"),
                    "summary": session.get("summary"),
                    "unread_count": session.get("unread_count"),
                    "last_msg_type": last_type,
                    "last_msg_type_normalized": normalize_message_type(last_type),
                    "last_msg_sender": None,
                    "last_msg_sender_name": None,
                    "avatar_data_url": avatar_data_url,
                }
            )
        chats.sort(key=lambda item: (item["last_time"] or 0, item["message_count"]), reverse=True)
        self._chat_index_cache = chats
        return chats

    def count_chats(self, q: str | None = None, folded: bool | None = None, include_folded_entry: bool = False) -> int:
        return len(self.list_chats(limit=100000, q=q, offset=0, folded=folded, include_folded_entry=include_folded_entry))

    def _message_where(self, q: str | None, message_type: str | None) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if q:
            needle = f"%{_safe_like(q.strip())}%"
            clauses.append("(m.StrContent LIKE ? ESCAPE '\\' OR m.DisplayContent LIKE ? ESCAPE '\\' OR m.StrTalker LIKE ? ESCAPE '\\')")
            params.extend([needle, needle, needle])
        if message_type:
            normalized_type = int(message_type)
            clauses.append("m.Type = ?")
            params.append(normalized_type)
        return clauses, params

    def _talker_where(self, conn: sqlite3.Connection, chat_username: str, *, alias: str | None = None) -> tuple[str, list[Any]]:
        name2id_table = f"{alias}.Name2ID" if alias else "Name2ID"
        talker_ids: list[int] = []
        try:
            rows = conn.execute(f"SELECT rowid FROM {name2id_table} WHERE UsrName = ?", (chat_username,)).fetchall()
            talker_ids = [int(row[0]) for row in rows if row[0] is not None]
        except sqlite3.Error:
            talker_ids = []
        if talker_ids:
            placeholders = ",".join("?" for _ in talker_ids)
            return f"m.TalkerId IN ({placeholders})", [*talker_ids]
        return "m.StrTalker = ?", [chat_username]

    def count_messages(self, chat_username: str, q: str | None = None, message_type: str | None = None) -> dict[str, Any]:
        total = 0
        base_clauses, base_params = self._message_where(q, message_type)
        for db_path in self._message_db_paths():
            conn = _connect_readonly(db_path)
            try:
                if not (_table_exists(conn, "MSG") and _table_exists(conn, "Name2ID")):
                    continue
                talker_clause, talker_params = self._talker_where(conn, chat_username)
                clauses = [talker_clause]
                params: list[Any] = [*talker_params]
                clauses.extend(base_clauses)
                params.extend(base_params)
                where_sql = " WHERE " + " AND ".join(clauses)
                total += int(
                    conn.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM MSG m
                        {where_sql}
                        """,
                        params,
                    ).fetchone()[0]
                    or 0
                )
            finally:
                conn.close()
        return {"total": total, "table_name": "MSG"}

    def _infer_sender_username(
        self,
        row: sqlite3.Row,
        chat_username: str,
        self_username: str | None,
        chatroom_members: dict[str, list[str]] | None = None,
        contacts: dict[str, dict[str, Any]] | None = None,
    ) -> str | None:
        if int(row["IsSender"] or 0) == 1:
            return self_username
        if chat_username.endswith("@chatroom"):
            known_usernames = set((chatroom_members or {}).get(chat_username, []))
            known_usernames.update((contacts or {}).keys())
            if "BytesExtra" in row.keys():
                sender = _bytes_extra_sender_username(row["BytesExtra"], known_usernames)
                if sender:
                    return sender
            text = _decode_text_value(row["StrContent"])
            match = re.match(r"([^:\r\n]{1,160}):\r?\n", text)
            if match:
                return match.group(1)
        return chat_username

    def _legacy_resource_payload(self, row: sqlite3.Row, db_index: int, raw_local_id: int, message_text: str) -> dict[str, Any] | None:
        local_type = normalize_message_type(row["Type"])
        if local_type not in {3, 47}:
            return None
        account_root = self._media_account_root()
        attrs = _xml_attrs(message_text, "img" if local_type == 3 else "emoji")
        exports: list[dict[str, Any]] = []
        md5_text = attrs.get("md5") or attrs.get("androidmd5") or ""
        size_value = attrs.get("length") or attrs.get("len") or attrs.get("androidlen") or "0"
        try:
            declared_size = int(size_value or 0)
        except ValueError:
            declared_size = 0

        if local_type == 3 and account_root:
            paths = _bytes_extra_file_paths(row["BytesExtra"] if "BytesExtra" in row.keys() else None)
            ranked_paths = sorted(
                paths,
                key=lambda value: (
                    0 if "\\Image\\" in value or "/Image/" in value else 1,
                    0 if "\\Thumb\\" not in value and "/Thumb/" not in value else 1,
                ),
            )
            for raw_path in ranked_paths:
                source = self._resolve_media_source_path(raw_path, account_root)
                if not source:
                    continue
                export = self._export_legacy_media_file(
                    source,
                    kind="image",
                    stem=f"wx3_{db_index}_{raw_local_id}_{source.stem}",
                    md5_text=md5_text,
                    original_file_name=source.name,
                )
                if export:
                    exports.append(export)
                    break

        if local_type == 47:
            if account_root and md5_text:
                emotion_source = account_root / "FileStorage" / "CustomEmotion" / md5_text[:2].upper() / md5_text.upper()
                if emotion_source.exists():
                    export = self._export_legacy_media_file(
                        emotion_source,
                        kind="image",
                        stem=f"wx3_emoji_{md5_text.lower()}",
                        md5_text=md5_text,
                        original_file_name=emotion_source.name,
                    )
                    if export and _image_type_from_header(Path(export["stored_path"]).read_bytes()[:16]):
                        exports.append(export)
            if not exports:
                url = attrs.get("cdnurl") or attrs.get("thumburl") or attrs.get("externurl")
                if url:
                    export = self._download_legacy_media_export(
                        url,
                        kind="image",
                        stem=f"wx3_emoji_{(md5_text or str(raw_local_id)).lower()}",
                        md5_text=md5_text,
                    )
                    if export:
                        exports.append(export)

        if not exports:
            return None
        items = [
            {
                "resource_id": None,
                "type": local_type,
                "size": int(export.get("size") or declared_size or 0),
                "data_index": None,
                "packed_text": message_text,
                "export": export,
            }
            for export in exports
        ]
        return {
            "resource_count": len(items),
            "total_size": sum(int(item.get("size") or 0) for item in items),
            "resource_types": "emoji" if local_type == 47 else "image",
            "data_indexes": "",
            "items": items,
        }

    def list_messages(
        self,
        chat_username: str,
        q: str | None = None,
        message_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
        order: str = "desc",
        include_resources: bool = True,
        known_total: int | None = None,
    ) -> dict[str, Any]:
        limit = min(max(1, int(limit)), MAX_PAGE_SIZE)
        offset = max(0, int(offset))
        order_desc = order != "asc"
        total = int(known_total) if known_total is not None else self.count_messages(chat_username, q=q, message_type=message_type)["total"]
        if offset >= total:
            return {"total": total, "items": [], "table_name": "MSG"}
        contacts = self._contact_map(include_avatar=True)
        chatroom_members = self._chatroom_member_map() if chat_username.endswith("@chatroom") else {}
        sessions = self._session_map()
        self_username = self._self_username()
        base_clauses, base_params = self._message_where(q, message_type)
        query_limit = limit
        query_offset = offset
        query_order_desc = order_desc
        reverse_page = False
        if not order_desc and total > 0 and offset + limit >= total:
            query_limit = max(0, min(limit, total - offset))
            query_offset = max(0, total - offset - query_limit)
            query_order_desc = True
            reverse_page = True
        page_rows = self._query_message_page(
            chat_username=chat_username,
            base_clauses=base_clauses,
            base_params=base_params,
            limit=query_limit,
            offset=query_offset,
            order_desc=query_order_desc,
        )
        if reverse_page:
            page_rows = list(reversed(page_rows))
        items: list[dict[str, Any]] = []
        for db_index, row in page_rows:
            raw_local_id = int(row["localId"] or 0)
            local_type = row["Type"]
            sender_username = self._infer_sender_username(
                row,
                chat_username,
                self_username,
                chatroom_members=chatroom_members,
                contacts=contacts,
            )
            message_text = _decode_text_value(row["StrContent"])
            display_text = _decode_text_value(row["DisplayContent"])
            resource = self._legacy_resource_payload(row, db_index, raw_local_id, message_text) if include_resources else None
            item = {
                "local_id": db_index * 10_000_000_000 + raw_local_id,
                "raw_local_id": raw_local_id,
                "source_db": f"MSG{db_index}.db",
                "server_id": row["MsgSvrID"],
                "local_type": local_type,
                "local_type_normalized": normalize_message_type(local_type),
                "sort_seq": row["Sequence"],
                "sender_username": sender_username,
                "sender_name": _display_name(str(sender_username or ""), contacts, sessions) if sender_username else None,
                "sender_avatar_data_url": (contacts.get(str(sender_username or "")) or {}).get("avatar_data_url")
                if sender_username
                else None,
                "create_time": row["CreateTime"],
                "create_time_text": _format_epoch(row["CreateTime"]),
                "status": row["Status"],
                "upload_status": row["StatusEx"],
                "download_status": None,
                "server_seq": row["MsgServerSeq"],
                "origin_source": row["SubType"],
                "source": row["StrTalker"] or row["TalkerName"],
                "message_content": message_text,
                "message_text": message_text,
                "compress_content": None,
                "source_text": display_text,
                "appmsg": _parse_appmsg(message_text) or _parse_appmsg(display_text),
                "packed_info_size": row["CompressContentSize"],
                "resource": resource,
            }
            items.append(item)
        payload = {"total": total, "items": items, "table_name": "MSG"}
        if offset == 0 and order_desc:
            fragments = self.live_wal_fragments(chat_username=chat_username, q=q, limit=min(80, max(limit * 3, 20)))
            if fragments:
                payload["live_wal_fragments"] = fragments
        return payload

    def live_wal_fragments(self, chat_username: str, q: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        live = find_live_legacy_wechat_account()
        if not live:
            return []
        if self._self_username() and live.wxid != self._self_username():
            return []
        contacts = self._contact_map()
        fragments: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for db_path in self._message_db_paths():
            live_db = self._live_db_path_for_decrypted_message_db(live, db_path)
            wal_path = Path(os.fspath(live_db) + "-wal")
            if not live_db.exists() or not wal_path.exists():
                continue
            for frame in _iter_decrypted_wx3_wal_frames(live_db, wal_path, live.key_hex):
                text = frame["data"].decode("utf-8", errors="ignore")
                if chat_username not in text:
                    continue
                for fragment in _extract_chat_wal_fragments_from_text(
                    text,
                    chat_username=chat_username,
                    q=q,
                    source_db=f"MSG{self._message_db_index(db_path)}.db-wal",
                    frame_index=frame["frame_index"],
                    page_no=frame["page_no"],
                    contacts=contacts,
                ):
                    dedupe_key = (fragment.get("message_text") or "", fragment.get("media_path_hint") or "")
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    fragment["resource"] = self._wal_fragment_resource_payload(fragment)
                    fragments.append(fragment)
                    if len(fragments) >= limit:
                        return fragments
        return fragments

    def _wal_fragment_resource_payload(self, fragment: dict[str, Any]) -> dict[str, Any] | None:
        raw_path = str(fragment.get("media_path_hint") or "")
        if not raw_path:
            return None
        account_root = self._media_account_root()
        if not account_root:
            return None
        source = self._resolve_media_source_path(raw_path, account_root)
        if not source:
            return None
        kind = "image" if source.suffix.lower() in {".dat", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"} else "file"
        export = self._export_legacy_media_file(
            source,
            kind=kind,
            stem=f"wx3_wal_{fragment.get('frame_index')}_{source.stem}",
            original_file_name=source.name,
        )
        if not export:
            return None
        return {
            "resource_count": 1,
            "total_size": int(export.get("size") or 0),
            "resource_types": kind,
            "data_indexes": "",
            "items": [
                {
                    "resource_id": None,
                    "type": 3 if kind == "image" else 49,
                    "size": int(export.get("size") or 0),
                    "data_index": None,
                    "packed_text": fragment.get("message_text") or "",
                    "export": export,
                }
            ],
        }

    def _query_message_page(
        self,
        *,
        chat_username: str,
        base_clauses: list[str],
        base_params: list[Any],
        limit: int,
        offset: int,
        order_desc: bool,
    ) -> list[tuple[int, sqlite3.Row]]:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        selects: list[str] = []
        params: list[Any] = []
        try:
            for position, db_path in enumerate(self._message_db_paths()):
                db_index = self._message_db_index(db_path)
                alias = f"db{position}"
                conn.execute(f"ATTACH DATABASE ? AS {alias}", (os.fspath(db_path),))
                if not self._attached_table_exists(conn, alias, "MSG"):
                    continue
                columns = {str(row["name"]) for row in conn.execute(f"PRAGMA {alias}.table_info(MSG)").fetchall()}
                bytes_extra_sql = "m.BytesExtra" if "BytesExtra" in columns else "NULL"
                talker_clause, talker_params = self._talker_where(conn, chat_username, alias=alias)
                clauses = [talker_clause]
                clauses.extend(base_clauses)
                where_sql = " WHERE " + " AND ".join(clauses)
                params.extend([*talker_params, *base_params])
                selects.append(
                    f"""
                    SELECT
                        {db_index} AS source_db_index,
                        m.localId AS localId,
                        m.TalkerId AS TalkerId,
                        m.MsgSvrID AS MsgSvrID,
                        m.Type AS Type,
                        m.SubType AS SubType,
                        m.IsSender AS IsSender,
                        m.CreateTime AS CreateTime,
                        m.Sequence AS Sequence,
                        m.StatusEx AS StatusEx,
                        m.FlagEx AS FlagEx,
                        m.Status AS Status,
                        m.MsgServerSeq AS MsgServerSeq,
                        m.MsgSequence AS MsgSequence,
                        m.StrTalker AS StrTalker,
                        m.StrContent AS StrContent,
                        m.DisplayContent AS DisplayContent,
                        {bytes_extra_sql} AS BytesExtra,
                        length(m.CompressContent) AS CompressContentSize,
                        n.UsrName AS TalkerName
                    FROM {alias}.MSG m
                    LEFT JOIN {alias}.Name2ID n ON n.rowid = m.TalkerId
                    {where_sql}
                    """
                )
            if not selects:
                return []
            direction = "DESC" if order_desc else "ASC"
            sql = (
                " UNION ALL ".join(selects)
                + f"""
                ORDER BY Sequence {direction}, CreateTime {direction}, source_db_index {direction}, localId {direction}
                LIMIT ? OFFSET ?
                """
            )
            rows = conn.execute(sql, [*params, limit, offset]).fetchall()
            return [(int(row["source_db_index"] or 0), row) for row in rows]
        finally:
            conn.close()

    def _attached_table_exists(self, conn: sqlite3.Connection, alias: str, table: str) -> bool:
        row = conn.execute(
            f"SELECT name FROM {alias}.sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

    def message_types(self, chat_username: str | None = None) -> list[dict[str, Any]]:
        counts: dict[int, int] = {}
        for db_path in self._message_db_paths():
            conn = _connect_readonly(db_path)
            try:
                if not (_table_exists(conn, "MSG") and _table_exists(conn, "Name2ID")):
                    continue
                if chat_username:
                    talker_clause, talker_params = self._talker_where(conn, chat_username)
                    rows = conn.execute(
                        """
                        SELECT m.Type AS local_type, COUNT(*) AS n
                        FROM MSG m
                        WHERE {talker_clause}
                        GROUP BY m.Type
                        """.format(talker_clause=talker_clause),
                        talker_params,
                    ).fetchall()
                else:
                    rows = conn.execute("SELECT Type AS local_type, COUNT(*) AS n FROM MSG GROUP BY Type").fetchall()
                for row in rows:
                    key = normalize_message_type(row["local_type"])
                    counts[key] = counts.get(key, 0) + int(row["n"] or 0)
            finally:
                conn.close()
        return [{"local_type": key, "count": value} for key, value in sorted(counts.items(), key=lambda item: item[1], reverse=True)]

    def _database_path(self, database: str) -> Path:
        mapping = {
            "micro_msg": self.micro_msg_path,
            "multi_search_chat_msg": self.multi_search_chat_msg_path,
        }
        for path in self._message_db_paths():
            mapping[f"message_{self._message_db_index(path)}"] = path
        if database in mapping:
            return mapping[database]
        raise WeChatDbError(f"Unknown legacy database: {database}")

    def schema_overview(self) -> list[dict[str, Any]]:
        items = []
        db_paths = {
            "micro_msg": self.micro_msg_path,
            "multi_search_chat_msg": self.multi_search_chat_msg_path,
            **{f"message_{self._message_db_index(p)}": p for p in self._message_db_paths()},
        }
        for name, path in db_paths.items():
            item = {"name": name, "path": os.fspath(path), "exists": path.exists(), "objects": 0, "tables": []}
            if _is_sqlite_db(path):
                conn = _connect_readonly(path)
                try:
                    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
                    item["objects"] = len(rows)
                    item["tables"] = [row["name"] for row in rows]
                finally:
                    conn.close()
            items.append(item)
        return items

    def list_tables(self, database: str) -> list[dict[str, Any]]:
        path = self._database_path(database)
        conn = _connect_readonly(path)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            items = []
            for row in rows:
                table = row["name"]
                count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                columns = [col["name"] for col in conn.execute(f'PRAGMA table_info("{table}")')]
                items.append({"name": table, "count": int(count), "columns": columns})
            return items
        finally:
            conn.close()

    def browse_table(
        self,
        database: str,
        table: str,
        q: str | None = None,
        limit: int = 80,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit = min(max(1, int(limit)), MAX_PAGE_SIZE)
        offset = max(0, int(offset))
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            raise WeChatDbError(f"Invalid table name: {table}")
        path = self._database_path(database)
        conn = _connect_readonly(path)
        try:
            if not _table_exists(conn, table):
                raise WeChatDbError(f"Table not found: {database}.{table}")
            columns = [row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            clauses: list[str] = []
            params: list[Any] = []
            if q:
                needle = f"%{_safe_like(q.strip())}%"
                clauses = [f'CAST("{column}" AS TEXT) LIKE ? ESCAPE \'\\\'' for column in columns]
                params = [needle] * len(columns)
            where_sql = f" WHERE {' OR '.join(clauses)}" if clauses else ""
            total = conn.execute(f'SELECT COUNT(*) FROM "{table}"{where_sql}', params).fetchone()[0]
            rows = conn.execute(
                f'SELECT * FROM "{table}"{where_sql} LIMIT ? OFFSET ?',
                [*params, limit, offset],
            ).fetchall()
            return {
                "database": database,
                "table": table,
                "columns": columns,
                "total": int(total),
                "items": [_jsonable_row(row) for row in rows],
            }
        finally:
            conn.close()


def _format_epoch(value: Any) -> str | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
