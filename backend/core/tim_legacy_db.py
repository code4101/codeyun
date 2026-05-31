from __future__ import annotations

import hashlib
import html
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import time
from typing import Any

try:
    import psutil
except Exception:  # pragma: no cover - psutil is a backend dependency on Windows.
    psutil = None

from pyxllib.autogui.wechat_db import MAX_PAGE_SIZE, WeChatDbError, _safe_like, _table_exists, normalize_message_type

from backend.core.wechat_legacy_db import _connect_readonly, _format_epoch, _is_sqlite_db, _jsonable_row


TIM_SOURCE_FORMAT = "tim_legacy"
TIM_MESSAGE_DB_NAMES = ("Msg3.0.db", "Msg2.0.db")
TIM_TEXT_EXPORT_SUFFIXES = {".txt", ".htm", ".html", ".mht", ".mhtml"}
TIM_BAK_EXPORT_SUFFIXES = {".bak"}
TIM_MEMORY_SCAN_LIMIT_BYTES = 1024 * 1024 * 1024
_TIM_COMMON_TEXT_CHARS = set(
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产"
    "种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业本去"
    "把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与关各重新线内数正心反你明看原又么利比或但质"
    "气第向道命此变条只没结解问意建月公无系军很情者最立代想已通并提直题党程展五果料象员革位入常文总次品式活设"
    "及管特件长求老头基资边流路级少图山统接知较将组见计别她手角期根论运农指几九区强放决西被干做必战先回则任取"
    "据处队南给色光门即保治北造百规热领七海口东导器压志世金增争济阶油思术极交受联什么吗呢啊吧呀哦哈"
)


def _sqlite_varint(data: bytes, pos: int, limit: int) -> tuple[int, int]:
    value = 0
    for i in range(9):
        if pos + i >= limit:
            raise ValueError("truncated SQLite varint")
        byte = data[pos + i]
        if i == 8:
            return (value << 8) | byte, pos + i + 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos + i + 1
    raise ValueError("invalid SQLite varint")


def _sqlite_serial_size(serial_type: int) -> int:
    fixed = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 8, 7: 8, 8: 0, 9: 0}
    if serial_type in fixed:
        return fixed[serial_type]
    if serial_type >= 12:
        return (serial_type - 12) // 2
    raise ValueError(f"unsupported SQLite serial type: {serial_type}")


def _sqlite_serial_value(serial_type: int, raw: bytes) -> Any:
    if serial_type == 0:
        return None
    if serial_type in {1, 2, 3, 4, 5, 6}:
        return int.from_bytes(raw, "big", signed=True)
    if serial_type == 8:
        return 0
    if serial_type == 9:
        return 1
    if serial_type >= 13 and serial_type % 2 == 1:
        return raw.decode("utf-8", errors="replace")
    if serial_type >= 12 and serial_type % 2 == 0:
        return raw
    return raw


def _parse_sqlite_record(data: bytes, pos: int, limit: int) -> tuple[list[Any], list[int]]:
    header_size, cursor = _sqlite_varint(data, pos, limit)
    header_end = pos + header_size
    if header_size < 1 or header_end > limit:
        raise ValueError("invalid SQLite record header")
    serial_types: list[int] = []
    while cursor < header_end:
        serial_type, cursor = _sqlite_varint(data, cursor, header_end)
        _sqlite_serial_size(serial_type)
        serial_types.append(serial_type)
    values: list[Any] = []
    payload_cursor = header_end
    for serial_type in serial_types:
        size = _sqlite_serial_size(serial_type)
        if payload_cursor + size > limit:
            raise ValueError("truncated SQLite record payload")
        raw = data[payload_cursor : payload_cursor + size]
        payload_cursor += size
        values.append(_sqlite_serial_value(serial_type, raw))
    return values, serial_types


def extract_msgdbrandkeys_from_memory(data: bytes, base_address: int = 0) -> list[dict[str, Any]]:
    """Extract TIM MsgIndexVersion.msgdbrandkey rows from SQLite b-tree pages in memory."""

    keys: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for page_size in (1024, 2048, 4096, 8192):
        if len(data) < page_size:
            continue
        for offset in range(0, len(data) - page_size + 1, 16):
            if data[offset] != 0x0D:  # SQLite table b-tree leaf page
                continue
            try:
                cell_count = struct.unpack(">H", data[offset + 3 : offset + 5])[0]
                content_start = struct.unpack(">H", data[offset + 5 : offset + 7])[0]
            except struct.error:
                continue
            if not 1 <= cell_count <= 50 or not 8 <= content_start <= page_size:
                continue
            page = data[offset : offset + page_size]
            for i in range(cell_count):
                try:
                    cell_pointer = struct.unpack(">H", page[8 + 2 * i : 10 + 2 * i])[0]
                    if not 8 <= cell_pointer < page_size:
                        continue
                    payload_size, cursor = _sqlite_varint(page, cell_pointer, page_size)
                    rowid, cursor = _sqlite_varint(page, cursor, page_size)
                    if payload_size <= 0 or payload_size > page_size:
                        continue
                    values, serial_types = _parse_sqlite_record(page, cursor, min(page_size, cursor + payload_size))
                except (ValueError, struct.error):
                    continue
                if len(values) != 2 or not isinstance(values[0], str) or not isinstance(values[1], bytes):
                    continue
                version = values[0].strip("\x00")
                key = values[1]
                if not re.fullmatch(r"\d+(?:\.\d+)*", version) or not 16 <= len(key) <= 128:
                    continue
                dedupe_key = (version, key.hex())
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                keys.append(
                    {
                        "version": version,
                        "msgdbrandkey_hex": key.hex(),
                        "msgdbrandkey_length": len(key),
                        "rowid": rowid,
                        "address": hex(base_address + offset),
                        "page_size": page_size,
                        "cell_count": cell_count,
                        "serial_types": serial_types,
                    }
                )
    return keys


def _decode_tim_msg_content(blob: bytes) -> str:
    if not blob:
        return ""
    candidates: list[tuple[int, int, str]] = []
    pattern = r"[\u4e00-\u9fffA-Za-z0-9_\-，。！？、：；（）《》“”‘’,.!?@#%&+=/\\]{2,}"
    for byte_offset in range(0, min(16, len(blob))):
        decoded = blob[byte_offset:].decode("utf-16-le", errors="ignore")
        for match in re.finditer(pattern, decoded):
            text = match.group().strip("._- ")
            for font_name in ("微软雅黑", "宋体"):
                if text.startswith(font_name) and len(text) > len(font_name):
                    text = text[len(font_name) :]
            if text.startswith("体") and len(text) > 1 and text[1] in _TIM_COMMON_TEXT_CHARS:
                text = text[1:]
            if len(text) < 2 or text in {"宋体", "微软雅黑", "latex"}:
                continue
            common_count = sum(char in _TIM_COMMON_TEXT_CHARS for char in text)
            cjk_count = sum("\u4e00" <= char <= "\u9fff" for char in text)
            ascii_count = sum(char.isascii() and char.isalnum() for char in text)
            if cjk_count == 0 and ascii_count < 4:
                continue
            # Real TIM text runs tend to contain common CJK words. Rich-text metadata often
            # decodes to rare CJK glyphs, so common-character density is the useful signal.
            score = common_count * 12 + min(len(text), 80) + ascii_count
            if cjk_count and common_count == 0:
                score -= cjk_count * 2
            candidates.append((score, byte_offset * 10000 + match.start(), text))
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[-1][2]
    for encoding in ("utf-8", "gb18030"):
        text = blob.decode(encoding, errors="ignore").strip("\x00")
        if text:
            return text[:500]
    return ""


def extract_tim_message_rows_from_memory(data: bytes, base_address: int = 0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int, str]] = set()
    for page_size in (1024, 2048, 4096, 8192):
        if len(data) < page_size:
            continue
        for offset in range(0, len(data) - page_size + 1, 16):
            if data[offset] != 0x0D:
                continue
            try:
                cell_count = struct.unpack(">H", data[offset + 3 : offset + 5])[0]
                content_start = struct.unpack(">H", data[offset + 5 : offset + 7])[0]
            except struct.error:
                continue
            if not 1 <= cell_count <= 200 or not 8 <= content_start <= page_size:
                continue
            page = data[offset : offset + page_size]
            for i in range(min(cell_count, 120)):
                try:
                    cell_pointer = struct.unpack(">H", page[8 + 2 * i : 10 + 2 * i])[0]
                    if not 8 <= cell_pointer < page_size:
                        continue
                    payload_size, cursor = _sqlite_varint(page, cell_pointer, page_size)
                    rowid, cursor = _sqlite_varint(page, cursor, page_size)
                    if payload_size <= 5 or payload_size > page_size:
                        continue
                    values, serial_types = _parse_sqlite_record(page, cursor, min(page_size, cursor + payload_size))
                except (ValueError, struct.error):
                    continue
                if (
                    len(values) != 5
                    or not all(isinstance(values[index], int) for index in range(3))
                    or not isinstance(values[3], bytes)
                    or not isinstance(values[4], bytes)
                ):
                    continue
                msg_time, rand, sender = int(values[0]), int(values[1]), int(values[2])
                msg_content = values[3]
                info = values[4]
                if not 946684800 <= msg_time <= 1893456000 or not msg_content.startswith(b"MSG"):
                    continue
                dedupe_key = (msg_time, rand, sender, msg_content[:64].hex())
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                rows.append(
                    {
                        "rowid": rowid,
                        "address": hex(base_address + offset),
                        "page_size": page_size,
                        "cell_count": cell_count,
                        "serial_types": serial_types,
                        "time": msg_time,
                        "rand": rand,
                        "sender": str(sender),
                        "message_text": _decode_tim_msg_content(msg_content),
                        "msg_content_hex": msg_content.hex(),
                        "info_hex": info.hex(),
                    }
                )
    return rows


def _candidate_tim_account_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = (os.environ.get("CODEYUN_TIM_ACCOUNT_ROOT") or "").strip()
    if env_root:
        roots.append(Path(env_root).expanduser())
    documents = Path.home() / "Documents" / "Tencent Files"
    if documents.exists():
        for child in documents.iterdir():
            if child.is_dir() and child.name.isdigit() and any((child / name).exists() for name in TIM_MESSAGE_DB_NAMES):
                roots.append(child)
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = os.fspath(root.resolve() if root.exists() else root).lower()
        if key not in seen:
            seen.add(key)
            deduped.append(root)
    return deduped


def tim_account_roots() -> list[Path]:
    return _candidate_tim_account_roots()


def _is_tim_sqlite_variant(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        with path.open("rb") as file:
            header = file.read(16)
    except OSError:
        return False
    return header in {b"SQLite format 3\x00", b"SQLite header 3\x00"}


def tim_processes() -> list[dict[str, Any]]:
    if os.name != "nt" or psutil is None:
        return []
    processes: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = str(proc.info.get("name") or "")
            if name.lower() != "tim.exe":
                continue
            processes.append({"pid": int(proc.info["pid"]), "name": name, "exe": proc.info.get("exe")})
        except (psutil.Error, OSError, TypeError, ValueError):
            continue
    return processes


def tim_runtime_msgdbrandkeys(max_bytes_per_process: int = TIM_MEMORY_SCAN_LIMIT_BYTES) -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    try:
        import ctypes
        import ctypes.wintypes as wt
    except Exception:
        return []

    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    MEM_COMMIT = 0x1000
    PAGE_NOACCESS = 0x01
    PAGE_GUARD = 0x100

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p),
            ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", wt.DWORD),
            ("RegionSize", ctypes.c_size_t),
            ("State", wt.DWORD),
            ("Protect", wt.DWORD),
            ("Type", wt.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.restype = wt.HANDLE
    open_process.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    read_process_memory = kernel32.ReadProcessMemory
    read_process_memory.restype = wt.BOOL
    read_process_memory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    virtual_query_ex = kernel32.VirtualQueryEx
    virtual_query_ex.restype = ctypes.c_size_t
    virtual_query_ex.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
    close_handle = kernel32.CloseHandle

    results: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for proc in tim_processes():
        pid = int(proc["pid"])
        handle = open_process(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            continue
        scanned = 0
        address = 0
        mbi = MEMORY_BASIC_INFORMATION()
        try:
            while address < 0x80000000 and scanned < max_bytes_per_process:
                if not virtual_query_ex(handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)):
                    break
                base = int(mbi.BaseAddress or 0)
                size = int(mbi.RegionSize or 0)
                if (
                    size > 0
                    and mbi.State == MEM_COMMIT
                    and not (mbi.Protect & PAGE_NOACCESS)
                    and not (mbi.Protect & PAGE_GUARD)
                ):
                    read_size = min(size, max_bytes_per_process - scanned)
                    buffer = ctypes.create_string_buffer(read_size)
                    bytes_read = ctypes.c_size_t()
                    if read_process_memory(handle, ctypes.c_void_p(base), buffer, read_size, ctypes.byref(bytes_read)) and bytes_read.value:
                        scanned += bytes_read.value
                        chunk = buffer.raw[: bytes_read.value]
                        if b"MsgIndexVersion" in chunk or b"msgdbrandkey" in chunk:
                            for item in extract_msgdbrandkeys_from_memory(chunk, base):
                                dedupe_key = (pid, item["version"], item["msgdbrandkey_hex"])
                                if dedupe_key in seen:
                                    continue
                                seen.add(dedupe_key)
                                results.append({**item, "pid": pid, "process": proc})
                address = base + size if size else address + 0x1000
        finally:
            close_handle(handle)
    return results


def tim_live_message_rows(max_bytes_per_process: int = TIM_MEMORY_SCAN_LIMIT_BYTES) -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    try:
        import ctypes
        import ctypes.wintypes as wt
    except Exception:
        return []

    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    MEM_COMMIT = 0x1000
    PAGE_NOACCESS = 0x01
    PAGE_GUARD = 0x100

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p),
            ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", wt.DWORD),
            ("RegionSize", ctypes.c_size_t),
            ("State", wt.DWORD),
            ("Protect", wt.DWORD),
            ("Type", wt.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.restype = wt.HANDLE
    open_process.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    read_process_memory = kernel32.ReadProcessMemory
    read_process_memory.restype = wt.BOOL
    read_process_memory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    virtual_query_ex = kernel32.VirtualQueryEx
    virtual_query_ex.restype = ctypes.c_size_t
    virtual_query_ex.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
    close_handle = kernel32.CloseHandle

    results: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str, str]] = set()
    for proc in tim_processes():
        pid = int(proc["pid"])
        handle = open_process(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            continue
        scanned = 0
        address = 0
        mbi = MEMORY_BASIC_INFORMATION()
        try:
            while address < 0x80000000 and scanned < max_bytes_per_process:
                if not virtual_query_ex(handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)):
                    break
                base = int(mbi.BaseAddress or 0)
                size = int(mbi.RegionSize or 0)
                if (
                    size > 0
                    and mbi.State == MEM_COMMIT
                    and not (mbi.Protect & PAGE_NOACCESS)
                    and not (mbi.Protect & PAGE_GUARD)
                ):
                    read_size = min(size, max_bytes_per_process - scanned)
                    buffer = ctypes.create_string_buffer(read_size)
                    bytes_read = ctypes.c_size_t()
                    if read_process_memory(handle, ctypes.c_void_p(base), buffer, read_size, ctypes.byref(bytes_read)) and bytes_read.value:
                        scanned += bytes_read.value
                        for row in extract_tim_message_rows_from_memory(buffer.raw[: bytes_read.value], base):
                            dedupe_key = (row["time"], row["rand"], row["sender"], row["msg_content_hex"][:128])
                            if dedupe_key in seen:
                                continue
                            seen.add(dedupe_key)
                            results.append({**row, "pid": pid, "process": proc, "source_db": "tim_live_memory"})
                address = base + size if size else address + 0x1000
        finally:
            close_handle(handle)
    results.sort(key=lambda item: (int(item["time"]), int(item["rand"])))
    return results


def has_tim_live_source() -> bool:
    return bool(tim_processes() and _candidate_tim_account_roots())


def _decode_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for encoding in ("utf-8", "gb18030", "utf-16-le"):
            try:
                return value.decode(encoding).strip("\x00")
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="ignore").strip("\x00")
    return str(value)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _first_column(columns: set[str], *names: str) -> str | None:
    lowered = {column.lower(): column for column in columns}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _looks_like_message_table(columns: set[str]) -> bool:
    return bool(
        _first_column(columns, "content", "msg", "msgcontent", "message_content", "msgdata")
        and _first_column(columns, "time", "msgtime", "create_time", "sendtime", "timestamp")
    )


def _chat_username_from_table(table: str) -> str:
    for prefix in ("mr_troop_", "mr_discuss_", "mr_friend_", "msg_", "message_"):
        if table.lower().startswith(prefix):
            return table[len(prefix) :]
    return table


def _chat_type_from_table(table: str) -> str:
    lowered = table.lower()
    if "troop" in lowered or "group" in lowered:
        return "chatroom"
    if "discuss" in lowered:
        return "discuss"
    return "friend"


def _normalize_epoch(value: Any) -> int | None:
    try:
        timestamp = int(float(value))
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    return timestamp if timestamp > 0 else None


def _candidate_tim_export_files(account_root: Path) -> list[Path]:
    roots: list[Path] = []
    env_value = (os.environ.get("CODEYUN_QQ_CHAT_EXPORTS") or "").strip()
    if env_value:
        for part in re.split(r"[;\n]", env_value):
            if part.strip():
                roots.append(Path(part.strip()).expanduser())
    for name in ("MsgExport", "QQExport", "Export", "消息导出", "聊天记录"):
        roots.append(account_root / name)

    files: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        candidates = [root]
        if root.is_dir():
            try:
                candidates = [path for path in root.rglob("*") if path.is_file()]
            except OSError:
                candidates = []
        for path in candidates:
            if not path.exists() or not path.is_file() or path.suffix.lower() not in TIM_TEXT_EXPORT_SUFFIXES | TIM_BAK_EXPORT_SUFFIXES:
                continue
            key = os.fspath(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            files.append(path)
    files.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    return files


def classify_tim_export_file(path: Path) -> dict[str, Any]:
    """Identify TIM MsgMgr export flavor before parsing it."""

    suffix = path.suffix.lower()
    result: dict[str, Any] = {
        "path": os.fspath(path),
        "suffix": suffix,
        "kind": "unknown",
        "parseable": False,
        "size": path.stat().st_size if path.exists() else 0,
    }
    if suffix in TIM_TEXT_EXPORT_SUFFIXES:
        result.update({"kind": "msgmgr_text", "parseable": True})
        return result
    if suffix in TIM_BAK_EXPORT_SUFFIXES:
        header = path.read_bytes()[:32] if path.exists() else b""
        result["header_hex"] = header.hex()
        if header.startswith(b"SQLite header 3\x00"):
            result.update(
                {
                    "kind": "tim_encrypted_sqlite_bak",
                    "parseable": False,
                    "requires_helper": "tim_kernelutil",
                    "note": "TIM .bak stores encrypted CMultiSQLite3DB data; it needs the TIM SvrSeal/DataStorage path rather than text parsing.",
                }
            )
        elif header.startswith(b"\xef\xbb\xbf") or header.startswith(b"\xff\xfe") or header.startswith(b"\xfe\xff"):
            result.update({"kind": "msgmgr_text_like_bak", "parseable": True})
        else:
            result.update(
                {
                    "kind": "tim_encrypted_bak",
                    "parseable": False,
                    "requires_helper": "tim_kernelutil",
                    "note": "TIM .bak is an encrypted export container and cannot be decoded by the text exporter parser.",
                }
            )
        return result
    return result


def _read_tim_export_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="ignore")
    for encoding in ("utf-8-sig", "gb18030", "utf-16-le"):
        text = raw.decode(encoding, errors="ignore")
        if text.count("\ufffd") < 5:
            return text
    return raw.decode("utf-8", errors="ignore")


def _parse_tim_export_time(value: str) -> int | None:
    value = value.strip()
    patterns = (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
    )
    for pattern in patterns:
        try:
            return int(time.mktime(time.strptime(value, pattern)))
        except ValueError:
            continue
    return None


def _guess_tim_export_chat(path: Path, text: str) -> str:
    for pattern in (
        r"MSGMGR_MSGOBJ\s*[:=]\s*([^\r\n<>]+)",
        r"MSGMGR_MSGFOLDER\s*[:=]\s*([^\r\n<>]+)",
        r"(?:消息对象|聊天对象|会话)\s*[:：]\s*([^\r\n<>]+)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = html.unescape(match.group(1)).strip()
            if value:
                return value[:80]
    stem = path.stem.strip()
    return stem or "tim_export"


def parse_tim_export_messages(path: Path) -> list[dict[str, Any]]:
    """Parse low-risk TIM MsgMgr text/html exports into structured message rows."""

    flavor = classify_tim_export_file(path)
    if not flavor.get("parseable"):
        raise WeChatDbError(str(flavor.get("note") or f"unsupported TIM export format: {flavor.get('kind')}"))
    text = _read_tim_export_text(path)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    chat = _guess_tim_export_chat(path, text)
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    line_patterns = (
        re.compile(r"^\s*\[?(?P<time>\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\]?\s+(?P<sender>[^:：\(\)<>]{1,80})(?:\((?P<uin>\d{5,12})\))?\s*[:：]\s*(?P<content>.*)$"),
        re.compile(r"^\s*(?P<sender>[^:：\(\)<>]{1,80})(?:\((?P<uin>\d{5,12})\))?\s+(?P<time>\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s*$"),
    )
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("MSGMGR_") or "Save by Tencent MsgMgr" in line:
            continue
        matched = None
        for pattern in line_patterns:
            matched = pattern.match(line)
            if matched:
                break
        if matched:
            if current and current.get("message_text"):
                rows.append(current)
            timestamp = _parse_tim_export_time(matched.group("time"))
            sender = (matched.groupdict().get("uin") or matched.group("sender") or "").strip()
            content = (matched.groupdict().get("content") or "").strip()
            current = {
                "rowid": len(rows) + 1,
                "time": timestamp or 0,
                "rand": len(rows) + 1,
                "sender": sender,
                "chat": chat,
                "message_text": content,
                "msg_content_hex": content.encode("utf-8", errors="ignore").hex(),
                "info_hex": "",
                "address": os.fspath(path),
                "source_db": "tim_msgmgr_export",
            }
            continue
        if current:
            current["message_text"] = (str(current.get("message_text") or "") + "\n" + line).strip()
    if current and current.get("message_text"):
        rows.append(current)
    return [row for row in rows if row.get("time") and row.get("message_text")]


class TimLegacyDbStorage:
    """Query TIM/QQ classic account databases using the WeChat DB page shape."""

    def __init__(self, root: os.PathLike[str] | str):
        self.root = Path(root)
        self._chat_index_cache: list[dict[str, Any]] | None = None
        self._live_rows_cache: list[dict[str, Any]] | None = None

    @property
    def msg_path(self) -> Path:
        for name in TIM_MESSAGE_DB_NAMES:
            path = self.root / name
            if path.exists():
                return path
        return self.root / "Msg3.0.db"

    @property
    def archive_path(self) -> Path:
        env_path = (os.environ.get("CODEYUN_QQ_CHAT_ARCHIVE_DB") or "").strip()
        if env_path:
            return Path(env_path).expanduser()
        from backend.core.settings import get_settings

        account = self.root.name if self.root.name else "default"
        return get_settings().data_dir / "qq_chat" / account / "archive.sqlite"

    def _is_ready(self) -> bool:
        return (_is_sqlite_db(self.msg_path) and self._can_open(self.msg_path)) or self._has_structured_messages()

    def _raw_db_ready(self) -> bool:
        return _is_sqlite_db(self.msg_path) and self._can_open(self.msg_path)

    def _uses_structured_archive(self) -> bool:
        return self._has_structured_messages() and not self._raw_db_ready()

    def _can_open(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            conn = _connect_readonly(path)
            conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            conn.close()
            return True
        except sqlite3.DatabaseError:
            return False

    def _connect_archive(self, readonly: bool = True) -> sqlite3.Connection:
        path = self.archive_path
        if readonly:
            if not path.exists():
                raise FileNotFoundError(path)
            uri = f"{path.resolve().as_uri()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_archive_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                source_root TEXT NOT NULL,
                msgdbrandkey_hex TEXT,
                msgdbrandkey_version TEXT,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chats (
                account_id TEXT NOT NULL,
                username TEXT NOT NULL,
                name TEXT NOT NULL,
                chat_type TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                first_time INTEGER,
                last_time INTEGER,
                summary TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY(account_id, username)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                chat_username TEXT NOT NULL,
                sender_username TEXT NOT NULL,
                create_time INTEGER NOT NULL,
                rand INTEGER NOT NULL,
                message_type INTEGER NOT NULL DEFAULT 1,
                content TEXT NOT NULL,
                msg_content_hex TEXT NOT NULL,
                info_hex TEXT NOT NULL,
                source_address TEXT,
                fingerprint TEXT NOT NULL UNIQUE,
                collected_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tim_messages_chat_time
                ON messages(account_id, chat_username, create_time, rand);
            CREATE INDEX IF NOT EXISTS idx_tim_messages_content
                ON messages(content);
            """
        )

    def _has_structured_messages(self) -> bool:
        if not self.archive_path.exists():
            return False
        try:
            conn = self._connect_archive(readonly=True)
            try:
                row = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
                return bool(row and int(row[0] or 0) > 0)
            finally:
                conn.close()
        except (sqlite3.Error, OSError):
            return False

    def _structured_archive_stats(self) -> dict[str, int]:
        if not self.archive_path.exists():
            return {"messages": 0, "chats": 0}
        account_id = self._account_id()
        try:
            conn = self._connect_archive(readonly=True)
            try:
                messages = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
                chats = conn.execute(
                    "SELECT COUNT(*) FROM chats WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
                return {
                    "messages": int(messages[0] or 0) if messages else 0,
                    "chats": int(chats[0] or 0) if chats else 0,
                }
            finally:
                conn.close()
        except (sqlite3.Error, OSError):
            return {"messages": 0, "chats": 0}

    def _account_id(self) -> str:
        return self.root.name if self.root.name.isdigit() else "tim"

    def status(self) -> dict[str, Any]:
        exists = self.root.exists() or self.msg_path.exists()
        sqlite_header = _is_tim_sqlite_variant(self.msg_path)
        ready = self._is_ready()
        error = None
        runtime_keys: list[dict[str, Any]] = []
        structured_stats = self._structured_archive_stats()
        export_files = _candidate_tim_export_files(self.root)
        export_flavors = [classify_tim_export_file(path) for path in export_files[:20]]
        if sqlite_header and not ready:
            error = "TIM Msg3.0.db uses Tencent's encrypted SQLite variant. CodeYun reads cached structured rows for page display; live memory probing only runs during sync."
        return {
            "db_storage_path": os.fspath(self.root),
            "source_format": TIM_SOURCE_FORMAT,
            "exists": exists,
            "ready": ready,
            "databases": {"message": sqlite_header, self.msg_path.name: sqlite_header},
            "archive_path": os.fspath(self.archive_path),
            "structured_ready": structured_stats["messages"] > 0,
            "structured_total": structured_stats["messages"],
            "structured_chats": structured_stats["chats"],
            "self_username": self.root.name if self.root.name.isdigit() else None,
            "runtime_keys": runtime_keys,
            "export_files": [os.fspath(path) for path in export_files[:20]],
            "export_flavors": export_flavors,
            "export_file_count": len(export_files),
            "requires_manual_export": False,
            "requires_tim_kernelutil_helper": bool(sqlite_header and not self._raw_db_ready()),
            "error": error,
        }

    def sync_from_live(self, *, export_media: bool = True) -> dict[str, Any]:
        started_at = time.time()
        live_roots = _candidate_tim_account_roots()
        if not live_roots:
            raise WeChatDbError("No TIM account root was found")
        source = live_roots[0]
        copy_result = self._copy_live_files(source)
        live_rows = tim_live_message_rows()
        export_result = self._read_export_rows(source)
        structured_result = self._store_live_rows(source, [*live_rows, *export_result["rows"]])
        self._chat_index_cache = None
        self._live_rows_cache = None
        return {
            "live_account_root": os.fspath(source),
            "elapsed_seconds": round(time.time() - started_at, 3),
            "copy": copy_result,
            "decrypt": {
                "source": os.fspath(source),
                "target": os.fspath(self.root),
                "decrypted": 0,
                "skipped": 0,
                "failed": ["TIM encrypted Msg3.0.db decryption is not implemented yet"],
                "failed_count": 1,
            },
            "structured": structured_result,
            "exports": {key: value for key, value in export_result.items() if key != "rows"},
            "media": None,
        }

    def _read_export_rows(self, source: Path) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        files = _candidate_tim_export_files(source)
        for path in files:
            try:
                flavor = classify_tim_export_file(path)
                if flavor.get("parseable"):
                    rows.extend(parse_tim_export_messages(path))
                else:
                    errors.append(f"{path}: {flavor.get('kind')}: {flavor.get('note')}")
            except Exception as exc:
                errors.append(f"{path}: {type(exc).__name__}: {exc}")
        return {
            "files": [os.fspath(path) for path in files],
            "file_count": len(files),
            "rows": rows,
            "parsed": len(rows),
            "errors": errors[:20],
            "error_count": len(errors),
        }

    def _store_live_rows(self, source: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
        account_id = self._account_id()
        now = time.time()
        runtime_keys = tim_runtime_msgdbrandkeys() if tim_processes() else []
        key = runtime_keys[0] if runtime_keys else {}
        conn = self._connect_archive(readonly=False)
        inserted = 0
        skipped = 0
        try:
            self._ensure_archive_schema(conn)
            conn.execute(
                """
                INSERT INTO accounts(account_id, source_root, msgdbrandkey_hex, msgdbrandkey_version, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    source_root = excluded.source_root,
                    msgdbrandkey_hex = COALESCE(excluded.msgdbrandkey_hex, accounts.msgdbrandkey_hex),
                    msgdbrandkey_version = COALESCE(excluded.msgdbrandkey_version, accounts.msgdbrandkey_version),
                    updated_at = excluded.updated_at
                """,
                (
                    account_id,
                    os.fspath(source),
                    key.get("msgdbrandkey_hex"),
                    key.get("version"),
                    now,
                ),
            )
            for row in rows:
                content = str(row.get("message_text") or "")
                if not content:
                    skipped += 1
                    continue
                sender = str(row.get("sender") or "")
                chat_username = str(row.get("chat") or sender or "tim_export")
                create_time = int(row.get("time") or 0)
                rand = int(row.get("rand") or 0)
                msg_hex = str(row.get("msg_content_hex") or "")
                fingerprint = hashlib.sha1(f"{account_id}|{chat_username}|{sender}|{create_time}|{rand}|{msg_hex[:256]}".encode("utf-8")).hexdigest()
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO messages(
                        account_id, chat_username, sender_username, create_time, rand,
                        message_type, content, msg_content_hex, info_hex, source_address,
                        fingerprint, collected_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        chat_username,
                        sender,
                        create_time,
                        rand,
                        1,
                        content,
                        msg_hex,
                        str(row.get("info_hex") or ""),
                        str(row.get("address") or ""),
                        fingerprint,
                        now,
                    ),
                )
                inserted += int(cursor.rowcount or 0)
            conn.execute("DELETE FROM chats WHERE account_id = ?", (account_id,))
            conn.execute(
                """
                INSERT INTO chats(account_id, username, name, chat_type, message_count, first_time, last_time, summary, updated_at)
                SELECT
                    account_id,
                    chat_username,
                    chat_username,
                    'qq',
                    COUNT(*),
                    MIN(create_time),
                    MAX(create_time),
                    (
                        SELECT m2.content
                        FROM messages m2
                        WHERE m2.account_id = messages.account_id AND m2.chat_username = messages.chat_username
                        ORDER BY m2.create_time DESC, m2.rand DESC, m2.id DESC
                        LIMIT 1
                    ),
                    ?
                FROM messages
                WHERE account_id = ?
                GROUP BY account_id, chat_username
                """,
                (now, account_id),
            )
            total = int(conn.execute("SELECT COUNT(*) FROM messages WHERE account_id = ?", (account_id,)).fetchone()[0] or 0)
            chats = int(conn.execute("SELECT COUNT(*) FROM chats WHERE account_id = ?", (account_id,)).fetchone()[0] or 0)
            conn.commit()
            return {
                "archive_path": os.fspath(self.archive_path),
                "source": os.fspath(source),
                "scanned": len(rows),
                "inserted": inserted,
                "skipped": skipped,
                "total": total,
                "chats": chats,
                "runtime_key_found": bool(key),
            }
        finally:
            conn.close()

    def _copy_live_files(self, account_root: Path) -> dict[str, Any]:
        copied = 0
        unchanged = 0
        errors: list[str] = []
        self.root.mkdir(parents=True, exist_ok=True)
        for name in [*TIM_MESSAGE_DB_NAMES, "Msg3.0index.db", "Info.db", "Misc.db"]:
            source = account_root / name
            if not source.exists():
                continue
            target = self.root / name
            try:
                source_stat = source.stat()
                target_stat = target.stat() if target.exists() else None
                if target_stat and target_stat.st_size == source_stat.st_size and int(target_stat.st_mtime) == int(source_stat.st_mtime):
                    unchanged += 1
                    continue
                shutil.copy2(source, target)
                copied += 1
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
        return {
            "source": os.fspath(account_root),
            "target": os.fspath(self.root),
            "copied": copied,
            "unchanged": unchanged,
            "errors": errors[:20],
            "error_count": len(errors),
        }

    def _message_tables(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            table = str(row["name"])
            columns = _table_columns(conn, table)
            if not _looks_like_message_table(columns):
                continue
            items.append(
                {
                    "table_name": table,
                    "username": _chat_username_from_table(table),
                    "chat_type": _chat_type_from_table(table),
                    "columns": columns,
                }
            )
        return items

    def _chat_index(self) -> list[dict[str, Any]]:
        if self._chat_index_cache is not None:
            return self._chat_index_cache
        if self._uses_structured_archive():
            self._chat_index_cache = self._structured_chat_index()
            return self._chat_index_cache
        if not self._is_ready():
            self._chat_index_cache = self._live_chat_index()
            return self._chat_index_cache
        conn = _connect_readonly(self.msg_path)
        try:
            contacts = self._contact_map()
            items: list[dict[str, Any]] = []
            for table in self._message_tables(conn):
                columns = table["columns"]
                time_col = _first_column(columns, "time", "msgtime", "create_time", "sendtime", "timestamp")
                content_col = _first_column(columns, "content", "msg", "msgcontent", "message_content", "msgdata")
                type_col = _first_column(columns, "type", "msgtype", "local_type")
                table_sql = _quote_ident(table["table_name"])
                count = int(conn.execute(f"SELECT COUNT(*) FROM {table_sql}").fetchone()[0] or 0)
                first_time = last_time = None
                summary = ""
                last_msg_type = None
                if count and time_col:
                    row = conn.execute(
                        f"SELECT MIN({_quote_ident(time_col)}), MAX({_quote_ident(time_col)}) FROM {table_sql}"
                    ).fetchone()
                    first_time = _normalize_epoch(row[0])
                    last_time = _normalize_epoch(row[1])
                if count and content_col:
                    select_type = f", {_quote_ident(type_col)}" if type_col else ""
                    row = conn.execute(
                        f"SELECT {_quote_ident(content_col)}{select_type} FROM {table_sql} ORDER BY {_quote_ident(time_col or content_col)} DESC LIMIT 1"
                    ).fetchone()
                    if row:
                        summary = _decode_text(row[0])
                        if type_col:
                            last_msg_type = row[1]
                username = table["username"]
                contact = contacts.get(username, {})
                items.append(
                    {
                        "username": username,
                        "name": html.unescape(str(contact.get("name") or username)),
                        "table_name": table["table_name"],
                        "chat_type": table["chat_type"],
                        "message_count": count,
                        "first_time": first_time,
                        "last_time": last_time,
                        "summary": summary,
                        "unread_count": None,
                        "last_msg_type": last_msg_type,
                        "last_msg_type_normalized": normalize_message_type(last_msg_type) if last_msg_type is not None else None,
                        "last_msg_sender": None,
                        "last_msg_sender_name": None,
                        "avatar_data_url": None,
                    }
                )
            items.sort(key=lambda item: (item.get("last_time") or 0, item.get("message_count") or 0), reverse=True)
            self._chat_index_cache = items
            return items
        finally:
            conn.close()

    def _live_rows(self) -> list[dict[str, Any]]:
        if self._live_rows_cache is None:
            self._live_rows_cache = tim_live_message_rows()
        return self._live_rows_cache

    def _structured_chat_index(self) -> list[dict[str, Any]]:
        return self._query_structured_chats(limit=1000, offset=0)

    def _query_structured_chats(self, limit: int, offset: int = 0, q: str | None = None) -> list[dict[str, Any]]:
        if not self.archive_path.exists():
            return []
        account_id = self._account_id()
        clauses = ["account_id = ?"]
        params: list[Any] = [account_id]
        if q:
            needle = f"%{_safe_like(q.strip())}%"
            clauses.append("(username LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\')")
            params.extend([needle, needle, needle])
        params.extend([min(max(1, int(limit)), 1000), max(0, int(offset))])
        conn = self._connect_archive(readonly=True)
        try:
            rows = conn.execute(
                """
                SELECT username, name, chat_type, message_count, first_time, last_time, summary
                FROM chats
                WHERE {where_sql}
                ORDER BY COALESCE(last_time, 0) DESC, message_count DESC
                LIMIT ? OFFSET ?
                """.format(where_sql=" AND ".join(clauses)),
                params,
            ).fetchall()
            return [
                {
                    "username": row["username"],
                    "name": html.unescape(str(row["name"] or row["username"])),
                    "table_name": f"qq_archive:{row['username']}",
                    "chat_type": row["chat_type"] or "qq",
                    "message_count": int(row["message_count"] or 0),
                    "first_time": row["first_time"],
                    "last_time": row["last_time"],
                    "summary": row["summary"] or "",
                    "unread_count": None,
                    "last_msg_type": 1,
                    "last_msg_type_normalized": normalize_message_type(1),
                    "last_msg_sender": row["username"],
                    "last_msg_sender_name": row["username"],
                    "avatar_data_url": None,
                    "source_db": "qq_archive",
                }
                for row in rows
            ]
        finally:
            conn.close()

    def _live_chat_index(self) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in self._live_rows():
            username = str(row.get("sender") or "")
            if not username:
                continue
            item = grouped.setdefault(
                username,
                {
                    "username": username,
                    "name": username,
                    "table_name": f"tim_live_memory:{username}",
                    "chat_type": "live_memory",
                    "message_count": 0,
                    "first_time": None,
                    "last_time": None,
                    "summary": "",
                    "unread_count": None,
                    "last_msg_type": 1,
                    "last_msg_type_normalized": normalize_message_type(1),
                    "last_msg_sender": username,
                    "last_msg_sender_name": username,
                    "avatar_data_url": None,
                    "source_db": "tim_live_memory",
                },
            )
            msg_time = int(row.get("time") or 0)
            item["message_count"] = int(item["message_count"] or 0) + 1
            item["first_time"] = msg_time if item["first_time"] is None else min(int(item["first_time"]), msg_time)
            if item["last_time"] is None or msg_time >= int(item["last_time"]):
                item["last_time"] = msg_time
                item["summary"] = row.get("message_text") or ""
        items = list(grouped.values())
        items.sort(key=lambda item: (item.get("last_time") or 0, item.get("message_count") or 0), reverse=True)
        return items

    def _contact_map(self) -> dict[str, dict[str, Any]]:
        contacts: dict[str, dict[str, Any]] = {}
        for db_name in ("Info.db", self.msg_path.name):
            path = self.root / db_name
            if not self._can_open(path):
                continue
            conn = _connect_readonly(path)
            try:
                rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
                for row in rows:
                    table = str(row["name"])
                    columns = _table_columns(conn, table)
                    uin_col = _first_column(columns, "uin", "uid", "username", "peeruin")
                    name_col = _first_column(columns, "remark", "nick", "nickname", "name")
                    if not uin_col or not name_col:
                        continue
                    try:
                        for contact_row in conn.execute(
                            f"SELECT {_quote_ident(uin_col)}, {_quote_ident(name_col)} FROM {_quote_ident(table)} LIMIT 20000"
                        ).fetchall():
                            username = _decode_text(contact_row[0]).strip()
                            name = _decode_text(contact_row[1]).strip()
                            if username and name:
                                contacts.setdefault(username, {"name": name})
                    except sqlite3.Error:
                        continue
            finally:
                conn.close()
        return contacts

    def list_chats(
        self,
        limit: int = 500,
        offset: int = 0,
        q: str | None = None,
        folded: bool | None = None,
        include_folded_entry: bool = False,
    ) -> list[dict[str, Any]]:
        limit = min(max(1, int(limit)), 1000)
        offset = max(0, int(offset))
        if self._uses_structured_archive():
            return self._query_structured_chats(limit=limit, offset=offset, q=q)
        items = self._chat_index()
        if q:
            needle = q.strip().lower()
            items = [
                item
                for item in items
                if needle in str(item.get("username") or "").lower()
                or needle in str(item.get("name") or "").lower()
                or needle in str(item.get("summary") or "").lower()
            ]
        return items[offset : offset + limit]

    def count_chats(self, q: str | None = None, folded: bool | None = None, include_folded_entry: bool = False) -> int:
        if self._uses_structured_archive():
            return self._count_structured_chats(q=q)
        return len(self.list_chats(limit=1000, offset=0, q=q, folded=folded, include_folded_entry=include_folded_entry))

    def _chat_table(self, chat_username: str) -> dict[str, Any] | None:
        for item in self._chat_index():
            if item["username"] == chat_username or item["table_name"] == chat_username:
                return item
        return None

    def _message_where(self, columns: set[str], q: str | None, message_type: str | None) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        content_col = _first_column(columns, "content", "msg", "msgcontent", "message_content", "msgdata")
        type_col = _first_column(columns, "type", "msgtype", "local_type")
        if q and content_col:
            clauses.append(f"CAST({_quote_ident(content_col)} AS TEXT) LIKE ? ESCAPE '\\'")
            params.append(f"%{_safe_like(q.strip())}%")
        if message_type and type_col:
            clauses.append(f"{_quote_ident(type_col)} = ?")
            params.append(int(message_type))
        return clauses, params

    def count_messages(self, chat_username: str, q: str | None = None, message_type: str | None = None) -> dict[str, Any]:
        if self._uses_structured_archive():
            query = self._query_structured_rows(chat_username, q=q, message_type=message_type, order="asc", limit=1, offset=0)
            return {"total": int(query["total"]), "table_name": f"qq_archive:{chat_username}"}
        if not self._is_ready():
            rows = self._filter_live_rows(chat_username, q)
            return {"total": len(rows), "table_name": f"tim_live_memory:{chat_username}"}
        table = self._chat_table(chat_username)
        if not table:
            return {"total": 0, "table_name": ""}
        conn = _connect_readonly(self.msg_path)
        try:
            columns = _table_columns(conn, table["table_name"])
            clauses, params = self._message_where(columns, q, message_type)
            where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
            total = conn.execute(f"SELECT COUNT(*) FROM {_quote_ident(table['table_name'])}{where_sql}", params).fetchone()[0]
            return {"total": int(total or 0), "table_name": table["table_name"]}
        finally:
            conn.close()

    def _filter_live_rows(self, chat_username: str, q: str | None = None) -> list[dict[str, Any]]:
        rows = [row for row in self._live_rows() if str(row.get("sender") or "") == chat_username]
        if q:
            needle = q.strip().lower()
            rows = [row for row in rows if needle in str(row.get("message_text") or "").lower()]
        return rows

    def _filter_structured_rows(self, chat_username: str, q: str | None = None) -> list[dict[str, Any]]:
        return self._query_structured_rows(chat_username, q=q, order="asc", limit=None, offset=0)["rows"]

    def _structured_message_clauses(
        self,
        chat_username: str,
        q: str | None = None,
        message_type: str | None = None,
    ) -> tuple[list[str], list[Any]]:
        account_id = self._account_id()
        clauses = ["account_id = ?", "chat_username = ?"]
        params: list[Any] = [account_id, chat_username]
        if q:
            clauses.append("content LIKE ? ESCAPE '\\'")
            params.append(f"%{_safe_like(q.strip())}%")
        if message_type:
            clauses.append("message_type = ?")
            params.append(int(message_type))
        return clauses, params

    def _count_structured_chats(self, q: str | None = None) -> int:
        if not self.archive_path.exists():
            return 0
        account_id = self._account_id()
        clauses = ["account_id = ?"]
        params: list[Any] = [account_id]
        if q:
            needle = f"%{_safe_like(q.strip())}%"
            clauses.append("(username LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\')")
            params.extend([needle, needle, needle])
        conn = self._connect_archive(readonly=True)
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM chats WHERE {' AND '.join(clauses)}", params).fetchone()
            return int(row[0] or 0) if row else 0
        finally:
            conn.close()

    def _query_structured_rows(
        self,
        chat_username: str,
        q: str | None = None,
        message_type: str | None = None,
        order: str = "asc",
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        if not self.archive_path.exists():
            return {"total": 0, "rows": []}
        clauses, params = self._structured_message_clauses(chat_username, q, message_type)
        direction = "ASC" if order == "asc" else "DESC"
        limit_sql = ""
        query_params = list(params)
        if limit is not None:
            limit_sql = " LIMIT ? OFFSET ?"
            query_params.extend([min(max(1, int(limit)), MAX_PAGE_SIZE), max(0, int(offset))])
        conn = self._connect_archive(readonly=True)
        try:
            total_row = conn.execute(f"SELECT COUNT(*) FROM messages WHERE {' AND '.join(clauses)}", params).fetchone()
            rows = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT *
                    FROM messages
                    WHERE {' AND '.join(clauses)}
                    ORDER BY create_time {direction}, rand {direction}, id {direction}
                    {limit_sql}
                    """,
                    query_params,
                ).fetchall()
            ]
            return {"total": int(total_row[0] or 0) if total_row else 0, "rows": rows}
        finally:
            conn.close()

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
        if self._uses_structured_archive():
            query = self._query_structured_rows(
                chat_username,
                q=q,
                message_type=message_type,
                order=order,
                limit=limit,
                offset=offset,
            )
            rows = query["rows"]
            total = int(known_total) if known_total is not None else int(query["total"])
            items = []
            for row in rows:
                create_time = int(row.get("create_time") or 0)
                text = str(row.get("content") or "")
                items.append(
                    {
                        "local_id": int(row.get("id") or 0),
                        "raw_local_id": row.get("rand"),
                        "source_db": "qq_archive",
                        "server_id": None,
                        "local_type": 1,
                        "local_type_normalized": normalize_message_type(1),
                        "sort_seq": create_time,
                        "sender_username": row.get("sender_username"),
                        "sender_name": row.get("sender_username"),
                        "sender_avatar_data_url": None,
                        "create_time": create_time,
                        "create_time_text": _format_epoch(create_time),
                        "status": None,
                        "upload_status": None,
                        "download_status": None,
                        "server_seq": None,
                        "origin_source": row.get("source_address"),
                        "source": chat_username,
                        "message_content": text,
                        "message_text": text,
                        "compress_content": None,
                        "source_text": None,
                        "appmsg": None,
                        "packed_info_size": None,
                        "resource": None,
                    }
                )
            return {"total": total, "items": items, "table_name": f"qq_archive:{chat_username}"}
        if not self._is_ready():
            rows = self._filter_live_rows(chat_username, q)
            rows.sort(key=lambda item: (int(item.get("time") or 0), int(item.get("rand") or 0)), reverse=(order != "asc"))
            total = int(known_total) if known_total is not None else len(rows)
            items = []
            for index, row in enumerate(rows[offset : offset + min(max(1, int(limit)), MAX_PAGE_SIZE)], start=offset + 1):
                create_time = int(row.get("time") or 0)
                text = str(row.get("message_text") or "")
                items.append(
                    {
                        "local_id": index,
                        "raw_local_id": row.get("rowid"),
                        "source_db": "tim_live_memory",
                        "server_id": None,
                        "local_type": 1,
                        "local_type_normalized": normalize_message_type(1),
                        "sort_seq": create_time,
                        "sender_username": row.get("sender"),
                        "sender_name": row.get("sender"),
                        "sender_avatar_data_url": None,
                        "create_time": create_time,
                        "create_time_text": _format_epoch(create_time),
                        "status": None,
                        "upload_status": None,
                        "download_status": None,
                        "server_seq": None,
                        "origin_source": row.get("address"),
                        "source": chat_username,
                        "message_content": text,
                        "message_text": text,
                        "compress_content": None,
                        "source_text": None,
                        "appmsg": None,
                        "packed_info_size": None,
                        "resource": None,
                    }
                )
            return {"total": total, "items": items, "table_name": f"tim_live_memory:{chat_username}"}
        table = self._chat_table(chat_username)
        if not table:
            return {"total": 0, "items": [], "table_name": ""}
        limit = min(max(1, int(limit)), MAX_PAGE_SIZE)
        offset = max(0, int(offset))
        conn = _connect_readonly(self.msg_path)
        try:
            columns = _table_columns(conn, table["table_name"])
            time_col = _first_column(columns, "time", "msgtime", "create_time", "sendtime", "timestamp")
            content_col = _first_column(columns, "content", "msg", "msgcontent", "message_content", "msgdata")
            id_col = _first_column(columns, "id", "msgid", "msg_id", "local_id", "localid")
            type_col = _first_column(columns, "type", "msgtype", "local_type")
            sender_col = _first_column(columns, "sender", "senderuin", "fromuin", "uin")
            is_sender_col = _first_column(columns, "issender", "is_send", "issend", "status")
            clauses, params = self._message_where(columns, q, message_type)
            where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
            total = int(known_total) if known_total is not None else int(
                conn.execute(f"SELECT COUNT(*) FROM {_quote_ident(table['table_name'])}{where_sql}", params).fetchone()[0] or 0
            )
            direction = "ASC" if order == "asc" else "DESC"
            order_col = _quote_ident(time_col or id_col or content_col or "rowid")
            rows = conn.execute(
                f"SELECT rowid AS __rowid__, * FROM {_quote_ident(table['table_name'])}{where_sql} ORDER BY {order_col} {direction}, rowid {direction} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            items = []
            self_username = self.status().get("self_username")
            for row in rows:
                raw_local_id = row[id_col] if id_col else row["__rowid__"]
                create_time = _normalize_epoch(row[time_col]) if time_col else None
                local_type = row[type_col] if type_col else 1
                sender_username = _decode_text(row[sender_col]).strip() if sender_col else table["username"]
                if is_sender_col and int(row[is_sender_col] or 0) in {1, 2}:
                    sender_username = self_username if int(row[is_sender_col] or 0) == 2 else sender_username
                text = _decode_text(row[content_col]) if content_col else ""
                items.append(
                    {
                        "local_id": int(row["__rowid__"]),
                        "raw_local_id": raw_local_id,
                        "source_db": self.msg_path.name,
                        "server_id": None,
                        "local_type": local_type,
                        "local_type_normalized": normalize_message_type(local_type),
                        "sort_seq": create_time,
                        "sender_username": sender_username,
                        "sender_name": sender_username,
                        "sender_avatar_data_url": None,
                        "create_time": create_time,
                        "create_time_text": _format_epoch(create_time),
                        "status": row[is_sender_col] if is_sender_col else None,
                        "upload_status": None,
                        "download_status": None,
                        "server_seq": None,
                        "origin_source": None,
                        "source": table["username"],
                        "message_content": text,
                        "message_text": text,
                        "compress_content": None,
                        "source_text": None,
                        "appmsg": None,
                        "packed_info_size": None,
                        "resource": None,
                    }
                )
            return {"total": total, "items": items, "table_name": table["table_name"]}
        finally:
            conn.close()

    def message_types(self, chat_username: str | None = None) -> list[dict[str, Any]]:
        if self._uses_structured_archive():
            rows = self._filter_structured_rows(chat_username) if chat_username else []
            if chat_username:
                return [{"local_type": 1, "count": len(rows)}] if rows else []
            conn = self._connect_archive(readonly=True)
            try:
                count = int(conn.execute("SELECT COUNT(*) FROM messages WHERE account_id = ?", (self._account_id(),)).fetchone()[0] or 0)
                return [{"local_type": 1, "count": count}] if count else []
            finally:
                conn.close()
        if not self._is_ready():
            rows = self._filter_live_rows(chat_username) if chat_username else self._live_rows()
            return [{"local_type": 1, "count": len(rows)}] if rows else []
        tables = [self._chat_table(chat_username)] if chat_username else self._chat_index()
        counts: dict[int, int] = {}
        conn = _connect_readonly(self.msg_path)
        try:
            for table in [item for item in tables if item]:
                columns = _table_columns(conn, table["table_name"])
                type_col = _first_column(columns, "type", "msgtype", "local_type")
                if not type_col:
                    counts[1] = counts.get(1, 0) + int(table.get("message_count") or 0)
                    continue
                rows = conn.execute(
                    f"SELECT {_quote_ident(type_col)} AS local_type, COUNT(*) AS n FROM {_quote_ident(table['table_name'])} GROUP BY {_quote_ident(type_col)}"
                ).fetchall()
                for row in rows:
                    key = normalize_message_type(row["local_type"])
                    counts[key] = counts.get(key, 0) + int(row["n"] or 0)
            return [{"local_type": key, "count": value} for key, value in sorted(counts.items(), key=lambda item: item[1], reverse=True)]
        finally:
            conn.close()

    def _database_path(self, database: str) -> Path:
        mapping = {"message": self.msg_path, "msg": self.msg_path}
        for name in ("Info.db", "Misc.db", "Msg3.0index.db"):
            path = self.root / name
            if path.exists():
                mapping[Path(name).stem.lower()] = path
        if database in mapping:
            return mapping[database]
        raise WeChatDbError(f"Unknown TIM database: {database}")

    def schema_overview(self) -> list[dict[str, Any]]:
        items = []
        for name, path in {"message": self.msg_path, "info": self.root / "Info.db", "misc": self.root / "Misc.db"}.items():
            item = {"name": name, "path": os.fspath(path), "exists": path.exists(), "objects": 0, "tables": []}
            if self._can_open(path):
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
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
            items = []
            for row in rows:
                table = row["name"]
                count = conn.execute(f"SELECT COUNT(*) FROM {_quote_ident(table)}").fetchone()[0]
                columns = [col["name"] for col in conn.execute(f"PRAGMA table_info({_quote_ident(table)})")]
                items.append({"name": table, "count": int(count), "columns": columns})
            return items
        finally:
            conn.close()

    def browse_table(self, database: str, table: str, q: str | None = None, limit: int = 80, offset: int = 0) -> dict[str, Any]:
        limit = min(max(1, int(limit)), MAX_PAGE_SIZE)
        offset = max(0, int(offset))
        path = self._database_path(database)
        conn = _connect_readonly(path)
        try:
            if not _table_exists(conn, table):
                raise WeChatDbError(f"Table not found: {database}.{table}")
            columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({_quote_ident(table)})")]
            clauses: list[str] = []
            params: list[Any] = []
            if q:
                needle = f"%{_safe_like(q.strip())}%"
                clauses = [f"CAST({_quote_ident(column)} AS TEXT) LIKE ? ESCAPE '\\'" for column in columns]
                params = [needle] * len(columns)
            where_sql = f" WHERE {' OR '.join(clauses)}" if clauses else ""
            total = conn.execute(f"SELECT COUNT(*) FROM {_quote_ident(table)}{where_sql}", params).fetchone()[0]
            rows = conn.execute(f"SELECT * FROM {_quote_ident(table)}{where_sql} LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
            return {
                "database": database,
                "table": table,
                "columns": columns,
                "total": int(total),
                "items": [_jsonable_row(row) for row in rows],
            }
        finally:
            conn.close()
